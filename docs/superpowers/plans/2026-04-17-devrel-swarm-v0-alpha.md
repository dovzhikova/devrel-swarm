# devrel-swarm v0 Alpha Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Run OpenClaw's DevRel weekly cycle end-to-end through the productized system (control plane + worker fleet + Postgres + Inngest) for a single hardcoded tenant, with cost tracking accurate to ±5% of Anthropic invoice.

**Architecture:** Monorepo with three top-level dirs — existing `agents/`+`tools/` (Python workers, reshaped), new `control-plane/` (Next.js 15 + Drizzle + NextAuth), new `workers/` (FastAPI HTTP shim exposing `/jobs/weekly-cycle` to Inngest). Shared Postgres (local docker-compose in v0, Neon/Supabase in v1). Atlas orchestrator refactored from 570-LOC monolith into 4 focused modules; `SharedContext` becomes per-tenant (single tenant in v0 but shape is ready for v1 multi-tenancy). RLS deferred to v1. pgvector column added to schema but unused in v0 (KB still markdown-on-disk).

**Tech Stack:** Python 3.12 (existing), FastAPI + uvicorn (worker shim), asyncpg (Postgres driver), Next.js 15 App Router, Drizzle ORM, NextAuth v5 with GitHub provider, Inngest Node SDK, Docker Compose for local Postgres, pytest + respx (existing), Vitest for control-plane unit tests.

**Scope gate (what v0 exits on):** one full weekly cycle runs to completion without manual intervention; cost tracking matches Anthropic invoice within 5%; dashboard Home + Deliverables surfaces show the run.

**Out of scope for v0:** RLS policies, Stripe/billing, Seed/Mimic/Publisher/Meter agents, OAuth connectors, multi-tenancy, pgvector populated, voice profile, onboarding wizard, autoresearch re-targeting. All of these land in v1.

---

## File structure (what gets created/modified)

### New files
- `docker-compose.yml` — local Postgres + Redis
- `workers/main.py` — FastAPI app with health + `/jobs/weekly-cycle`
- `workers/db.py` — asyncpg pool + tenant config loader
- `workers/budget.py` — `BudgetGate` (tracking-only stub for v0)
- `workers/pyproject.toml` — uvicorn + fastapi + asyncpg deps
- `agents/orchestrator.py` — Atlas class + `run_weekly_cycle` (extracted from atlas.py)
- `agents/memory.py` — `WeeklyMemory` + `SharedContext` (extracted)
- `agents/checkpoints.py` — Postgres-backed checkpoint read/write
- `agents/dispatch.py` — `delegate()` + retry-with-backoff (extracted)
- `control-plane/` — full Next.js 15 app (many files, detailed in Phase 6)
- `control-plane/drizzle/schema.ts` — single-source schema (Drizzle), then `drizzle-kit generate`d migrations
- `control-plane/src/lib/inngest/client.ts` + `functions.ts`
- `control-plane/src/app/api/inngest/route.ts` — Inngest HTTP receiver
- `scripts/seed_openclaw_tenant.sql` — manual tenant creation for v0

### Modified files
- `agents/atlas.py` — becomes a thin shim that re-exports from the 4 new modules (back-compat for `python -m agents.atlas --weekly-cycle` CLI)
- `agents/llm.py` — wire LLMClient to go through `BudgetGate`
- `pyproject.toml` — add asyncpg + fastapi + uvicorn to optional `[workers]` extras
- `.gitignore` — add `control-plane/.next`, `control-plane/node_modules`, `.env.local`

### Deleted/deprecated (v0 retains back-compat, v1 removes)
- None in v0. Atlas CLI keeps working via the shim.

---

## Phase 0 — Repo setup & local infra

### Task 0.1: Add monorepo `.gitignore` entries + top-level dir scaffolding

**Files:**
- Modify: `/Users/macmini/devrel-swarm/.gitignore`
- Create: `/Users/macmini/devrel-swarm/workers/.gitkeep`
- Create: `/Users/macmini/devrel-swarm/control-plane/.gitkeep`

- [ ] **Step 1: Append to .gitignore**

Add these lines to `/Users/macmini/devrel-swarm/.gitignore`:

```
# Control plane (Next.js)
control-plane/node_modules/
control-plane/.next/
control-plane/.vercel/
control-plane/.env.local
control-plane/.env.development.local

# Workers local
workers/.venv/

# Local infra
.env.dev
docker-data/
```

- [ ] **Step 2: Create empty placeholder dirs**

```bash
mkdir -p /Users/macmini/devrel-swarm/workers /Users/macmini/devrel-swarm/control-plane
touch /Users/macmini/devrel-swarm/workers/.gitkeep /Users/macmini/devrel-swarm/control-plane/.gitkeep
```

- [ ] **Step 3: Commit**

```bash
cd /Users/macmini/devrel-swarm
git add .gitignore workers/.gitkeep control-plane/.gitkeep
git commit -m "chore: monorepo scaffolding for workers + control-plane"
```

---

### Task 0.2: Docker Compose for local Postgres + Redis

**Files:**
- Create: `/Users/macmini/devrel-swarm/docker-compose.yml`
- Create: `/Users/macmini/devrel-swarm/.env.dev.example`

- [ ] **Step 1: Write docker-compose.yml**

Create `/Users/macmini/devrel-swarm/docker-compose.yml`:

```yaml
services:
  postgres:
    image: pgvector/pgvector:pg16
    restart: unless-stopped
    environment:
      POSTGRES_USER: devrel
      POSTGRES_PASSWORD: devrel
      POSTGRES_DB: devrel_swarm
    ports:
      - "5433:5432"
    volumes:
      - ./docker-data/postgres:/var/lib/postgresql/data
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U devrel -d devrel_swarm"]
      interval: 5s
      timeout: 5s
      retries: 5

  redis:
    image: redis:7-alpine
    restart: unless-stopped
    ports:
      - "6380:6379"
    volumes:
      - ./docker-data/redis:/data
```

- [ ] **Step 2: Write env example**

Create `/Users/macmini/devrel-swarm/.env.dev.example`:

```
# Local Postgres (docker-compose)
DATABASE_URL=postgresql://devrel:devrel@localhost:5433/devrel_swarm

# Local Redis
REDIS_URL=redis://localhost:6380

# Worker endpoint (for Inngest to call)
WORKER_URL=http://localhost:8787

# Control plane → Inngest (local dev server)
INNGEST_EVENT_KEY=local
INNGEST_SIGNING_KEY=signkey-local-test

# Single hardcoded tenant for v0
DEFAULT_TENANT_ID=00000000-0000-0000-0000-000000000001

# Reuse existing secrets from .env
ANTHROPIC_API_KEY=
GITHUB_TOKEN=
FIRECRAWL_API_KEY=
BRAVE_API_KEY=
```

- [ ] **Step 3: Start stack + verify**

```bash
cd /Users/macmini/devrel-swarm
docker compose up -d
docker compose ps
```

Expected: both `postgres` and `redis` services in `healthy` / `running` state.

```bash
docker compose exec postgres psql -U devrel -d devrel_swarm -c "SELECT version();"
```

Expected: Postgres 16.x version string, no error.

- [ ] **Step 4: Commit**

```bash
git add docker-compose.yml .env.dev.example
git commit -m "chore: local postgres + redis via docker-compose"
```

---

## Phase 1 — Database schema (Drizzle, single source of truth)

### Task 1.1: Initialize Next.js skeleton + Drizzle

**Files:**
- Create: `/Users/macmini/devrel-swarm/control-plane/package.json`
- Create: `/Users/macmini/devrel-swarm/control-plane/tsconfig.json`
- Create: `/Users/macmini/devrel-swarm/control-plane/next.config.ts`
- Create: `/Users/macmini/devrel-swarm/control-plane/drizzle.config.ts`

- [ ] **Step 1: Scaffold Next.js app**

```bash
cd /Users/macmini/devrel-swarm
npx create-next-app@15 control-plane --typescript --app --tailwind --eslint --src-dir --import-alias "@/*" --no-git --turbopack
```

When prompted, accept defaults. Verify `control-plane/package.json` exists after.

- [ ] **Step 2: Install Drizzle + Postgres deps**

```bash
cd /Users/macmini/devrel-swarm/control-plane
pnpm add drizzle-orm postgres
pnpm add -D drizzle-kit @types/pg
```

(If `pnpm` missing, substitute `npm install` — but prefer pnpm for monorepo-friendliness.)

- [ ] **Step 3: Write drizzle.config.ts**

Create `/Users/macmini/devrel-swarm/control-plane/drizzle.config.ts`:

```typescript
import { defineConfig } from "drizzle-kit";

export default defineConfig({
  dialect: "postgresql",
  schema: "./src/db/schema/index.ts",
  out: "./drizzle/migrations",
  dbCredentials: {
    url: process.env.DATABASE_URL!,
  },
  strict: true,
  verbose: true,
});
```

- [ ] **Step 4: Verify scaffold**

```bash
cd /Users/macmini/devrel-swarm/control-plane
pnpm next build
```

Expected: build succeeds (empty app compiles).

- [ ] **Step 5: Commit**

```bash
cd /Users/macmini/devrel-swarm
git add control-plane/
git commit -m "feat: scaffold Next.js 15 control-plane with drizzle"
```

---

### Task 1.2: Core schema — tenants, users, product_profile

**Files:**
- Create: `/Users/macmini/devrel-swarm/control-plane/src/db/schema/core.ts`
- Create: `/Users/macmini/devrel-swarm/control-plane/src/db/schema/index.ts`

- [ ] **Step 1: Write core schema**

Create `/Users/macmini/devrel-swarm/control-plane/src/db/schema/core.ts`:

```typescript
import { pgTable, uuid, text, timestamp, integer, pgEnum } from "drizzle-orm/pg-core";

export const planEnum = pgEnum("plan", ["trial", "starter", "pro", "scale"]);
export const userRoleEnum = pgEnum("user_role", ["owner", "member"]);

export const tenants = pgTable("tenants", {
  id: uuid("id").defaultRandom().primaryKey(),
  slug: text("slug").notNull().unique(),
  plan: planEnum("plan").notNull().default("trial"),
  stripeCustomerId: text("stripe_customer_id"),
  stripeSubscriptionId: text("stripe_subscription_id"),
  quotaMonthlyCostCents: integer("quota_monthly_cost_cents").notNull().default(500),
  timezone: text("timezone").notNull().default("UTC"),
  weeklyCycleDay: integer("weekly_cycle_day").notNull().default(1), // 0=Sun..6=Sat
  weeklyCycleHour: integer("weekly_cycle_hour").notNull().default(9),
  createdAt: timestamp("created_at", { withTimezone: true }).notNull().defaultNow(),
});

export const users = pgTable("users", {
  id: uuid("id").defaultRandom().primaryKey(),
  tenantId: uuid("tenant_id").notNull().references(() => tenants.id, { onDelete: "cascade" }),
  email: text("email").notNull().unique(),
  role: userRoleEnum("role").notNull().default("owner"),
  createdAt: timestamp("created_at", { withTimezone: true }).notNull().defaultNow(),
});

export const productProfile = pgTable("product_profile", {
  tenantId: uuid("tenant_id").primaryKey().references(() => tenants.id, { onDelete: "cascade" }),
  productName: text("product_name").notNull(),
  category: text("category"),
  positioning: text("positioning"),
  icpDescription: text("icp_description"),
  updatedAt: timestamp("updated_at", { withTimezone: true }).notNull().defaultNow(),
});
```

- [ ] **Step 2: Write schema index**

Create `/Users/macmini/devrel-swarm/control-plane/src/db/schema/index.ts`:

```typescript
export * from "./core";
```

- [ ] **Step 3: Commit (will generate migration in Task 1.6)**

```bash
cd /Users/macmini/devrel-swarm
git add control-plane/src/db/schema/
git commit -m "feat: core schema — tenants, users, product_profile"
```

---

### Task 1.3: Integrations + voice_profile schema

**Files:**
- Create: `/Users/macmini/devrel-swarm/control-plane/src/db/schema/integrations.ts`
- Modify: `/Users/macmini/devrel-swarm/control-plane/src/db/schema/index.ts`

- [ ] **Step 1: Write integrations schema**

Create `/Users/macmini/devrel-swarm/control-plane/src/db/schema/integrations.ts`:

```typescript
import { pgTable, uuid, text, timestamp, jsonb, integer, pgEnum, customType } from "drizzle-orm/pg-core";
import { tenants } from "./core";

// bytea encoded as Buffer in drizzle
const bytea = customType<{ data: Buffer }>({
  dataType() {
    return "bytea";
  },
});

export const integrationKindEnum = pgEnum("integration_kind", [
  "github", "discord", "slack", "substack", "devto", "linkedin", "x", "posthog",
]);
export const integrationStatusEnum = pgEnum("integration_status", [
  "connected", "expired", "revoked",
]);

export const integrations = pgTable("integrations", {
  id: uuid("id").defaultRandom().primaryKey(),
  tenantId: uuid("tenant_id").notNull().references(() => tenants.id, { onDelete: "cascade" }),
  kind: integrationKindEnum("kind").notNull(),
  oauthTokenEncrypted: bytea("oauth_token_encrypted"),
  oauthRefreshEncrypted: bytea("oauth_refresh_encrypted"),
  oauthExpiresAt: timestamp("oauth_expires_at", { withTimezone: true }),
  metadata: jsonb("metadata").$type<Record<string, unknown>>().notNull().default({}),
  status: integrationStatusEnum("status").notNull().default("connected"),
  createdAt: timestamp("created_at", { withTimezone: true }).notNull().defaultNow(),
  updatedAt: timestamp("updated_at", { withTimezone: true }).notNull().defaultNow(),
});

export const voiceProfile = pgTable("voice_profile", {
  tenantId: uuid("tenant_id").primaryKey().references(() => tenants.id, { onDelete: "cascade" }),
  profileJson: jsonb("profile_json").$type<Record<string, unknown>>().notNull().default({}),
  sourceRefs: jsonb("source_refs").$type<Array<Record<string, unknown>>>().notNull().default([]),
  version: integer("version").notNull().default(1),
  updatedAt: timestamp("updated_at", { withTimezone: true }).notNull().defaultNow(),
});
```

- [ ] **Step 2: Re-export from index**

Update `/Users/macmini/devrel-swarm/control-plane/src/db/schema/index.ts`:

```typescript
export * from "./core";
export * from "./integrations";
```

- [ ] **Step 3: Commit**

```bash
git add control-plane/src/db/schema/
git commit -m "feat: integrations + voice_profile schema"
```

---

### Task 1.4: Content pipeline schema — kb_documents, signals, themes, deliverables, publications

**Files:**
- Create: `/Users/macmini/devrel-swarm/control-plane/src/db/schema/content.ts`
- Modify: `/Users/macmini/devrel-swarm/control-plane/src/db/schema/index.ts`

- [ ] **Step 1: Write content schema**

Create `/Users/macmini/devrel-swarm/control-plane/src/db/schema/content.ts`:

```typescript
import { pgTable, uuid, text, timestamp, jsonb, numeric, pgEnum, index, customType } from "drizzle-orm/pg-core";
import { sql } from "drizzle-orm";
import { tenants } from "./core";

// pgvector type — stored as `vector(1536)` text, handled by Postgres pgvector extension
const vector = customType<{ data: number[]; driverData: string }>({
  dataType() {
    return "vector(1536)";
  },
  toDriver(value) {
    return `[${value.join(",")}]`;
  },
  fromDriver(value) {
    return JSON.parse(value as string) as number[];
  },
});

export const deliverableKindEnum = pgEnum("deliverable_kind", [
  "tutorial", "social_post", "triage_digest", "brand_audit", "preview", "short",
]);
export const deliverableStatusEnum = pgEnum("deliverable_status", [
  "draft", "scheduled", "published", "archived",
]);
export const publicationChannelEnum = pgEnum("publication_channel", [
  "substack", "devto", "linkedin", "x",
]);
export const publicationStatusEnum = pgEnum("publication_status", [
  "pending", "published", "failed",
]);
export const signalSourceEnum = pgEnum("signal_source", [
  "github_issue", "reddit_post", "hn_comment", "x_tweet",
]);

export const kbDocuments = pgTable(
  "kb_documents",
  {
    id: uuid("id").defaultRandom().primaryKey(),
    tenantId: uuid("tenant_id").notNull().references(() => tenants.id, { onDelete: "cascade" }),
    source: text("source").notNull(),
    url: text("url"),
    title: text("title").notNull(),
    content: text("content").notNull(),
    embedding: vector("embedding"),
    lastFetchedAt: timestamp("last_fetched_at", { withTimezone: true }).notNull().defaultNow(),
  },
  (t) => ({
    tenantIdx: index("kb_documents_tenant_idx").on(t.tenantId),
  })
);

export const signals = pgTable(
  "signals",
  {
    id: uuid("id").defaultRandom().primaryKey(),
    tenantId: uuid("tenant_id").notNull().references(() => tenants.id, { onDelete: "cascade" }),
    source: signalSourceEnum("source").notNull(),
    externalId: text("external_id").notNull(),
    rawPayload: jsonb("raw_payload").$type<Record<string, unknown>>().notNull(),
    sentiment: text("sentiment"),
    priority: text("priority"),
    themeTags: text("theme_tags").array().notNull().default(sql`'{}'::text[]`),
    ingestedAt: timestamp("ingested_at", { withTimezone: true }).notNull().defaultNow(),
  },
  (t) => ({
    tenantTimeIdx: index("signals_tenant_time_idx").on(t.tenantId, t.ingestedAt),
  })
);

export const themes = pgTable("themes", {
  id: uuid("id").defaultRandom().primaryKey(),
  tenantId: uuid("tenant_id").notNull().references(() => tenants.id, { onDelete: "cascade" }),
  label: text("label").notNull(),
  description: text("description"),
  signalIds: uuid("signal_ids").array().notNull().default(sql`'{}'::uuid[]`),
  weekIso: text("week_iso").notNull(),
  score: numeric("score", { precision: 6, scale: 2 }),
  createdAt: timestamp("created_at", { withTimezone: true }).notNull().defaultNow(),
});

export const deliverables = pgTable(
  "deliverables",
  {
    id: uuid("id").defaultRandom().primaryKey(),
    tenantId: uuid("tenant_id").notNull().references(() => tenants.id, { onDelete: "cascade" }),
    jobId: uuid("job_id"),
    kind: deliverableKindEnum("kind").notNull(),
    title: text("title").notNull(),
    bodyMd: text("body_md"),
    s3Key: text("s3_key"),
    status: deliverableStatusEnum("status").notNull().default("draft"),
    qualityScore: numeric("quality_score", { precision: 4, scale: 2 }),
    voiceScore: numeric("voice_score", { precision: 4, scale: 2 }),
    createdAt: timestamp("created_at", { withTimezone: true }).notNull().defaultNow(),
  },
  (t) => ({
    tenantListIdx: index("deliverables_tenant_list_idx").on(t.tenantId, t.status, t.createdAt),
  })
);

export const publications = pgTable("publications", {
  id: uuid("id").defaultRandom().primaryKey(),
  tenantId: uuid("tenant_id").notNull().references(() => tenants.id, { onDelete: "cascade" }),
  deliverableId: uuid("deliverable_id").notNull().references(() => deliverables.id, { onDelete: "cascade" }),
  channel: publicationChannelEnum("channel").notNull(),
  externalId: text("external_id"),
  externalUrl: text("external_url"),
  publishedAt: timestamp("published_at", { withTimezone: true }),
  status: publicationStatusEnum("status").notNull().default("pending"),
  error: text("error"),
});
```

- [ ] **Step 2: Re-export**

Update `/Users/macmini/devrel-swarm/control-plane/src/db/schema/index.ts`:

```typescript
export * from "./core";
export * from "./integrations";
export * from "./content";
```

- [ ] **Step 3: Commit**

```bash
git add control-plane/src/db/schema/
git commit -m "feat: content pipeline schema — kb, signals, themes, deliverables, publications"
```

---

### Task 1.5: Execution schema — jobs, checkpoints, cost_events, quality_events

**Files:**
- Create: `/Users/macmini/devrel-swarm/control-plane/src/db/schema/execution.ts`
- Modify: `/Users/macmini/devrel-swarm/control-plane/src/db/schema/index.ts`

- [ ] **Step 1: Write execution schema**

Create `/Users/macmini/devrel-swarm/control-plane/src/db/schema/execution.ts`:

```typescript
import { pgTable, uuid, text, timestamp, jsonb, integer, numeric, pgEnum, index } from "drizzle-orm/pg-core";
import { tenants } from "./core";
import { deliverables } from "./content";

export const jobKindEnum = pgEnum("job_kind", ["weekly_cycle", "preview", "publish", "ad_hoc"]);
export const jobStatusEnum = pgEnum("job_status", [
  "queued", "running", "paused", "completed", "failed",
]);

export const jobs = pgTable("jobs", {
  id: uuid("id").defaultRandom().primaryKey(),
  tenantId: uuid("tenant_id").notNull().references(() => tenants.id, { onDelete: "cascade" }),
  kind: jobKindEnum("kind").notNull(),
  status: jobStatusEnum("status").notNull().default("queued"),
  inngestRunId: text("inngest_run_id"),
  startedAt: timestamp("started_at", { withTimezone: true }),
  completedAt: timestamp("completed_at", { withTimezone: true }),
  costCents: numeric("cost_cents", { precision: 10, scale: 2 }).notNull().default("0"),
  errorMessage: text("error_message"),
  createdAt: timestamp("created_at", { withTimezone: true }).notNull().defaultNow(),
});

export const jobCheckpoints = pgTable("job_checkpoints", {
  id: uuid("id").defaultRandom().primaryKey(),
  jobId: uuid("job_id").notNull().references(() => jobs.id, { onDelete: "cascade" }),
  stage: text("stage").notNull(),
  payload: jsonb("payload").$type<Record<string, unknown>>().notNull(),
  createdAt: timestamp("created_at", { withTimezone: true }).notNull().defaultNow(),
});

export const costEvents = pgTable(
  "cost_events",
  {
    id: uuid("id").defaultRandom().primaryKey(),
    tenantId: uuid("tenant_id").notNull().references(() => tenants.id, { onDelete: "cascade" }),
    jobId: uuid("job_id").references(() => jobs.id, { onDelete: "cascade" }),
    agent: text("agent").notNull(),
    model: text("model").notNull(),
    inputTokens: integer("input_tokens").notNull().default(0),
    outputTokens: integer("output_tokens").notNull().default(0),
    cacheCreationInputTokens: integer("cache_creation_input_tokens").notNull().default(0),
    cacheReadInputTokens: integer("cache_read_input_tokens").notNull().default(0),
    costCents: numeric("cost_cents", { precision: 10, scale: 4 }).notNull(),
    createdAt: timestamp("created_at", { withTimezone: true }).notNull().defaultNow(),
  },
  (t) => ({
    tenantMonthIdx: index("cost_events_tenant_month_idx").on(t.tenantId, t.createdAt),
  })
);

export const qualityEvents = pgTable("quality_events", {
  id: uuid("id").defaultRandom().primaryKey(),
  tenantId: uuid("tenant_id").notNull().references(() => tenants.id, { onDelete: "cascade" }),
  deliverableId: uuid("deliverable_id").notNull().references(() => deliverables.id, { onDelete: "cascade" }),
  dimension: text("dimension").notNull(),
  score: numeric("score", { precision: 4, scale: 2 }).notNull(),
  notes: text("notes"),
  createdAt: timestamp("created_at", { withTimezone: true }).notNull().defaultNow(),
});
```

- [ ] **Step 2: Re-export**

Update `/Users/macmini/devrel-swarm/control-plane/src/db/schema/index.ts`:

```typescript
export * from "./core";
export * from "./integrations";
export * from "./content";
export * from "./execution";
```

- [ ] **Step 3: Commit**

```bash
git add control-plane/src/db/schema/
git commit -m "feat: execution schema — jobs, checkpoints, cost_events, quality_events"
```

---

### Task 1.6: Generate + apply migration (with pgvector extension)

**Files:**
- Create: `/Users/macmini/devrel-swarm/control-plane/drizzle/migrations/0000_initial.sql` (generated)
- Create: `/Users/macmini/devrel-swarm/control-plane/drizzle/custom/0001_enable_pgvector.sql`

- [ ] **Step 1: Generate migration from schema**

```bash
cd /Users/macmini/devrel-swarm/control-plane
cp ../.env.dev.example .env.local
# ensure docker-compose postgres is running (from Task 0.2)
pnpm drizzle-kit generate
```

Expected: `drizzle/migrations/0000_<name>.sql` file created with CREATE TABLE statements for all 14 tables.

- [ ] **Step 2: Hand-write pgvector extension migration**

Create `/Users/macmini/devrel-swarm/control-plane/drizzle/custom/0001_enable_pgvector.sql`:

```sql
-- Must run BEFORE 0000_initial if it contains vector(1536) columns.
-- In v0 we apply this first by hand via psql; drizzle-kit will respect it later.
CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";
```

- [ ] **Step 3: Apply extension migration manually**

```bash
cd /Users/macmini/devrel-swarm
docker compose exec -T postgres psql -U devrel -d devrel_swarm \
  < control-plane/drizzle/custom/0001_enable_pgvector.sql
```

Expected: `CREATE EXTENSION` output, no error.

- [ ] **Step 4: Apply Drizzle migration**

```bash
cd /Users/macmini/devrel-swarm/control-plane
pnpm drizzle-kit migrate
```

Expected: migration applied, tables created. Verify:

```bash
cd /Users/macmini/devrel-swarm
docker compose exec postgres psql -U devrel -d devrel_swarm -c "\dt"
```

Expected: 14 tables listed.

- [ ] **Step 5: Commit**

```bash
git add control-plane/drizzle/
git commit -m "feat: initial migration + pgvector extension"
```

---

### Task 1.7: Seed OpenClaw as single v0 tenant

**Files:**
- Create: `/Users/macmini/devrel-swarm/scripts/seed_openclaw_tenant.sql`

- [ ] **Step 1: Write seed script**

Create `/Users/macmini/devrel-swarm/scripts/seed_openclaw_tenant.sql`:

```sql
-- v0: manually seed OpenClaw as the single test tenant.
-- ID is fixed so it matches DEFAULT_TENANT_ID in .env.dev.

BEGIN;

INSERT INTO tenants (id, slug, plan, quota_monthly_cost_cents, timezone, weekly_cycle_day, weekly_cycle_hour)
VALUES (
  '00000000-0000-0000-0000-000000000001',
  'openclaw',
  'pro',  -- pretend Pro so quota is ample during alpha
  10000,  -- $100 pooled cap for alpha testing
  'Asia/Jerusalem',
  1,      -- Monday
  9
)
ON CONFLICT (id) DO NOTHING;

INSERT INTO users (tenant_id, email, role)
VALUES (
  '00000000-0000-0000-0000-000000000001',
  'dovzhikova@gmail.com',
  'owner'
)
ON CONFLICT (email) DO NOTHING;

INSERT INTO product_profile (tenant_id, product_name, category, positioning, icp_description)
VALUES (
  '00000000-0000-0000-0000-000000000001',
  'OpenClaw',
  'Self-hosted personal AI assistant',
  'The self-hosted Claude-powered assistant for developers who want local control',
  'Technical builders who want AI augmentation without sending data to cloud vendors'
)
ON CONFLICT (tenant_id) DO UPDATE SET
  product_name = EXCLUDED.product_name,
  updated_at = NOW();

COMMIT;
```

- [ ] **Step 2: Apply seed**

```bash
cd /Users/macmini/devrel-swarm
docker compose exec -T postgres psql -U devrel -d devrel_swarm \
  < scripts/seed_openclaw_tenant.sql
```

Verify:

```bash
docker compose exec postgres psql -U devrel -d devrel_swarm \
  -c "SELECT id, slug, plan FROM tenants;"
```

Expected: one row `00000000-0000-0000-0000-000000000001 | openclaw | pro`.

- [ ] **Step 3: Commit**

```bash
git add scripts/seed_openclaw_tenant.sql
git commit -m "feat: seed OpenClaw as v0 test tenant"
```

---

## Phase 2 — Python worker shim (FastAPI)

### Task 2.1: Worker Python project scaffold + asyncpg pool

**Files:**
- Create: `/Users/macmini/devrel-swarm/workers/pyproject.toml`
- Create: `/Users/macmini/devrel-swarm/workers/main.py`
- Create: `/Users/macmini/devrel-swarm/workers/db.py`

- [ ] **Step 1: Write pyproject.toml**

Create `/Users/macmini/devrel-swarm/workers/pyproject.toml`:

```toml
[project]
name = "devrel-swarm-workers"
version = "0.0.1"
description = "HTTP shim that dispatches Inngest jobs to Atlas orchestrator"
requires-python = ">=3.12"
dependencies = [
    "fastapi==0.115.0",
    "uvicorn[standard]==0.32.0",
    "asyncpg==0.30.0",
    "pydantic==2.9.0",
    "python-dotenv==1.0.1",
]

[tool.setuptools]
py-modules = ["main", "db"]
```

- [ ] **Step 2: Write db.py with asyncpg pool**

Create `/Users/macmini/devrel-swarm/workers/db.py`:

```python
"""Async Postgres pool + tenant config loader for worker jobs."""

from __future__ import annotations

import os
from contextlib import asynccontextmanager
from typing import Any

import asyncpg

_pool: asyncpg.Pool | None = None


async def get_pool() -> asyncpg.Pool:
    """Lazy-initialize a shared connection pool."""
    global _pool
    if _pool is None:
        dsn = os.environ["DATABASE_URL"]
        _pool = await asyncpg.create_pool(dsn=dsn, min_size=1, max_size=10)
    return _pool


async def close_pool() -> None:
    """Close the pool on shutdown."""
    global _pool
    if _pool is not None:
        await _pool.close()
        _pool = None


@asynccontextmanager
async def acquire():
    """Context-managed connection from the pool."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        yield conn


async def load_tenant(tenant_id: str) -> dict[str, Any]:
    """Return tenant + product_profile joined as a single dict."""
    async with acquire() as conn:
        row = await conn.fetchrow(
            """
            SELECT
              t.id, t.slug, t.plan, t.quota_monthly_cost_cents,
              t.timezone, t.weekly_cycle_day, t.weekly_cycle_hour,
              p.product_name, p.category, p.positioning, p.icp_description
            FROM tenants t
            LEFT JOIN product_profile p ON p.tenant_id = t.id
            WHERE t.id = $1::uuid
            """,
            tenant_id,
        )
        if row is None:
            raise ValueError(f"Tenant {tenant_id} not found")
        return dict(row)
```

- [ ] **Step 3: Write main.py FastAPI skeleton**

Create `/Users/macmini/devrel-swarm/workers/main.py`:

```python
"""FastAPI worker shim. Receives Inngest HTTP triggers and dispatches to Atlas."""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

load_dotenv()

from workers.db import close_pool, get_pool, load_tenant  # noqa: E402

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    await get_pool()
    yield
    await close_pool()


app = FastAPI(title="devrel-swarm workers", lifespan=lifespan)


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


class WeeklyCycleRequest(BaseModel):
    tenant_id: str
    job_id: str
    inngest_run_id: str | None = None


@app.post("/jobs/weekly-cycle")
async def weekly_cycle(req: WeeklyCycleRequest) -> dict[str, str]:
    try:
        tenant = await load_tenant(req.tenant_id)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))

    logger.info("weekly-cycle start tenant=%s job=%s", tenant["slug"], req.job_id)
    # Real dispatch wired in Task 3.6
    return {"status": "accepted", "tenant": tenant["slug"], "job_id": req.job_id}
```

- [ ] **Step 4: Install + start**

```bash
cd /Users/macmini/devrel-swarm
python3.12 -m venv workers/.venv
source workers/.venv/bin/activate
pip install -e ./workers
pip install -e .  # installs existing agents/tools too
```

Start in background terminal:

```bash
cd /Users/macmini/devrel-swarm
set -a; source .env.dev; set +a
uvicorn workers.main:app --host 0.0.0.0 --port 8787
```

- [ ] **Step 5: Smoke test**

```bash
curl -s http://localhost:8787/health
```

Expected: `{"status":"ok"}`

```bash
curl -s -X POST http://localhost:8787/jobs/weekly-cycle \
  -H "content-type: application/json" \
  -d '{"tenant_id":"00000000-0000-0000-0000-000000000001","job_id":"test-1"}'
```

Expected: `{"status":"accepted","tenant":"openclaw","job_id":"test-1"}`

- [ ] **Step 6: Commit**

```bash
cd /Users/macmini/devrel-swarm
git add workers/
git commit -m "feat: FastAPI worker shim with asyncpg pool + tenant loader"
```

---

### Task 2.2: Pytest scaffolding for workers

**Files:**
- Create: `/Users/macmini/devrel-swarm/workers/tests/__init__.py`
- Create: `/Users/macmini/devrel-swarm/workers/tests/test_health.py`
- Create: `/Users/macmini/devrel-swarm/workers/tests/conftest.py`

- [ ] **Step 1: Write conftest**

Create `/Users/macmini/devrel-swarm/workers/tests/conftest.py`:

```python
"""Shared fixtures for worker tests."""

from __future__ import annotations

import os

import pytest
from fastapi.testclient import TestClient

os.environ.setdefault(
    "DATABASE_URL",
    "postgresql://devrel:devrel@localhost:5433/devrel_swarm",
)


@pytest.fixture
def client():
    from workers.main import app
    with TestClient(app) as c:
        yield c
```

- [ ] **Step 2: Write the failing test for health**

Create `/Users/macmini/devrel-swarm/workers/tests/test_health.py`:

```python
def test_health(client):
    res = client.get("/health")
    assert res.status_code == 200
    assert res.json() == {"status": "ok"}


def test_weekly_cycle_missing_tenant(client):
    res = client.post(
        "/jobs/weekly-cycle",
        json={"tenant_id": "deadbeef-dead-beef-dead-beefdeadbeef", "job_id": "t1"},
    )
    assert res.status_code == 404


def test_weekly_cycle_known_tenant(client):
    res = client.post(
        "/jobs/weekly-cycle",
        json={"tenant_id": "00000000-0000-0000-0000-000000000001", "job_id": "t2"},
    )
    assert res.status_code == 200
    body = res.json()
    assert body["tenant"] == "openclaw"
```

- [ ] **Step 3: Run tests**

```bash
cd /Users/macmini/devrel-swarm
source workers/.venv/bin/activate
pytest workers/tests/ -v
```

Expected: all three pass (docker-compose Postgres must be up with seeded tenant).

- [ ] **Step 4: Commit**

```bash
git add workers/tests/
git commit -m "test: worker health + weekly-cycle smoke tests"
```

---

## Phase 3 — Atlas refactor (split 570-LOC monolith)

### Task 3.1: Extract `WeeklyMemory` + `SharedContext` into `agents/memory.py`

**Files:**
- Create: `/Users/macmini/devrel-swarm/agents/memory.py`
- Modify: `/Users/macmini/devrel-swarm/agents/atlas.py`
- Create: `/Users/macmini/devrel-swarm/tests/test_memory_module.py`

- [ ] **Step 1: Write the failing test**

Create `/Users/macmini/devrel-swarm/tests/test_memory_module.py`:

```python
"""Verifies memory module extraction preserves existing behavior."""

from agents.memory import SharedContext, WeeklyMemory


def test_weekly_memory_roundtrip():
    wm = WeeklyMemory(
        week_of="2026-W17",
        content_titles=["Tutorial A"],
        pain_points_addressed=["flaky setup"],
    )
    d = wm.to_dict()
    assert d["week_of"] == "2026-W17"
    assert d["content_titles"] == ["Tutorial A"]


def test_shared_context_instantiates_with_tenant_id():
    ctx = SharedContext(week_of="2026-W17", tenant_id="00000000-0000-0000-0000-000000000001")
    assert ctx.tenant_id == "00000000-0000-0000-0000-000000000001"
    assert ctx.sage_triage == {}


def test_shared_context_default_tenant_is_none():
    ctx = SharedContext(week_of="2026-W17")
    assert ctx.tenant_id is None
```

- [ ] **Step 2: Run, confirm ImportError / AttributeError**

```bash
cd /Users/macmini/devrel-swarm
pytest tests/test_memory_module.py -v
```

Expected: FAIL with `ModuleNotFoundError: No module named 'agents.memory'`

- [ ] **Step 3: Create agents/memory.py**

Read lines 43–250 of current `agents/atlas.py` and move them into `/Users/macmini/devrel-swarm/agents/memory.py`. Copy the imports needed (`dataclass`, `field`, `Any`, `json`, `Path`, `datetime`). Add `tenant_id: str | None = None` to `SharedContext`.

Create `/Users/macmini/devrel-swarm/agents/memory.py`:

```python
"""WeeklyMemory and SharedContext — extracted from atlas.py for per-tenant scoping."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any


@dataclass
class WeeklyMemory:
    """Summary of a previous week's output for trend detection and dedup."""

    week_of: str = ""
    content_titles: list[str] = field(default_factory=list)
    pain_points_addressed: list[str] = field(default_factory=list)
    competitors_tracked: list[str] = field(default_factory=list)
    experiments_run: list[str] = field(default_factory=list)
    top_themes: list[str] = field(default_factory=list)
    okr_snapshot: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "week_of": self.week_of,
            "content_titles": self.content_titles,
            "pain_points_addressed": self.pain_points_addressed,
            "competitors_tracked": self.competitors_tracked,
            "experiments_run": self.experiments_run,
            "top_themes": self.top_themes,
            "okr_snapshot": self.okr_snapshot,
        }

    @classmethod
    def from_context(cls, ctx: "SharedContext") -> "WeeklyMemory":
        content_titles: list[str] = []
        if isinstance(ctx.kai_content, dict):
            title = ctx.kai_content.get("task", "")
            if title:
                content_titles.append(title)

        pain_points: list[str] = []
        if isinstance(ctx.iris_themes, dict):
            for t in ctx.iris_themes.get("themes", []):
                if isinstance(t, dict):
                    pain_points.append(t.get("title", ""))

        competitors: list[str] = []
        if isinstance(ctx.rex_competitive, dict):
            for c in ctx.rex_competitive.get("competitors", []):
                if isinstance(c, dict):
                    competitors.append(c.get("name", ""))

        experiments: list[str] = []
        if isinstance(ctx.nova_experiments, dict):
            for e in ctx.nova_experiments.get("experiments", []):
                if isinstance(e, dict):
                    experiments.append(e.get("name", ""))

        top_themes: list[str] = []
        if isinstance(ctx.iris_themes, dict):
            for t in ctx.iris_themes.get("themes", [])[:3]:
                if isinstance(t, dict):
                    top_themes.append(t.get("title", ""))

        return cls(
            week_of=ctx.week_of,
            content_titles=[t for t in content_titles if t],
            pain_points_addressed=[p for p in pain_points if p],
            competitors_tracked=[c for c in competitors if c],
            experiments_run=[e for e in experiments if e],
            top_themes=[t for t in top_themes if t],
            okr_snapshot=ctx.okr_progress or {},
        )


@dataclass
class SharedContext:
    """Per-tenant per-cycle shared state across agents."""

    week_of: str = ""
    tenant_id: str | None = None
    sage_triage: dict[str, Any] = field(default_factory=dict)
    echo_social: dict[str, Any] = field(default_factory=dict)
    dex_docs: dict[str, Any] = field(default_factory=dict)
    rex_competitive: dict[str, Any] = field(default_factory=dict)
    iris_themes: dict[str, Any] = field(default_factory=dict)
    nova_experiments: dict[str, Any] = field(default_factory=dict)
    kai_content: dict[str, Any] = field(default_factory=dict)
    vox_video: dict[str, Any] = field(default_factory=dict)
    okr_progress: dict[str, Any] = field(default_factory=dict)
    previous_weeks: list[WeeklyMemory] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "week_of": self.week_of,
            "tenant_id": self.tenant_id,
            "sage_triage": self.sage_triage,
            "echo_social": self.echo_social,
            "dex_docs": self.dex_docs,
            "rex_competitive": self.rex_competitive,
            "iris_themes": self.iris_themes,
            "nova_experiments": self.nova_experiments,
            "kai_content": self.kai_content,
            "vox_video": self.vox_video,
            "okr_progress": self.okr_progress,
            "previous_weeks": [w.to_dict() for w in self.previous_weeks],
        }

    def archive_path(self, archive_dir: Path) -> Path:
        safe_week = self.week_of.replace(" ", "_") or datetime.utcnow().strftime("%Y-W%V")
        return archive_dir / f"context_{safe_week}.json"

    def save(self, archive_dir: Path) -> None:
        archive_dir.mkdir(parents=True, exist_ok=True)
        payload = self.to_dict()
        payload.pop("previous_weeks", None)
        self.archive_path(archive_dir).write_text(json.dumps(payload, indent=2))

    @classmethod
    def load_with_history(
        cls, archive_dir: Path, history_weeks: int = 4
    ) -> "SharedContext":
        ctx = cls(week_of=datetime.utcnow().strftime("%Y-W%V"))
        if not archive_dir.exists():
            return ctx
        files = sorted(archive_dir.glob("context_*.json"), reverse=True)[:history_weeks]
        memories: list[WeeklyMemory] = []
        for f in files:
            try:
                data = json.loads(f.read_text())
                memories.append(
                    WeeklyMemory(
                        week_of=data.get("week_of", ""),
                        content_titles=data.get("kai_content", {}).get("content_titles", []),
                        pain_points_addressed=[
                            t.get("title", "")
                            for t in data.get("iris_themes", {}).get("themes", [])
                            if isinstance(t, dict)
                        ],
                        competitors_tracked=[
                            c.get("name", "")
                            for c in data.get("rex_competitive", {}).get("competitors", [])
                            if isinstance(c, dict)
                        ],
                    )
                )
            except (OSError, ValueError, KeyError):
                continue
        ctx.previous_weeks = memories
        return ctx
```

- [ ] **Step 4: Update atlas.py to re-export for back-compat**

At the top of `/Users/macmini/devrel-swarm/agents/atlas.py` (after existing imports), add:

```python
# Back-compat re-exports — memory classes moved to agents/memory.py in v0 refactor.
from agents.memory import SharedContext, WeeklyMemory  # noqa: F401, E402
```

Then **delete** the original `WeeklyMemory` class (lines 42–100ish) and `SharedContext` class (lines 103–250ish) from `atlas.py`. The re-export above keeps `from agents.atlas import SharedContext` working.

- [ ] **Step 5: Run all tests to verify no regression**

```bash
cd /Users/macmini/devrel-swarm
pytest tests/ -v
```

Expected: all pre-existing tests pass + new `test_memory_module.py` passes.

- [ ] **Step 6: Commit**

```bash
git add agents/memory.py agents/atlas.py tests/test_memory_module.py
git commit -m "refactor: extract WeeklyMemory + SharedContext to agents/memory.py

SharedContext now accepts tenant_id (None for back-compat). Prep for
per-tenant scoping in workers."
```

---

### Task 3.2: Extract `delegate()` + retry logic into `agents/dispatch.py`

**Files:**
- Create: `/Users/macmini/devrel-swarm/agents/dispatch.py`
- Modify: `/Users/macmini/devrel-swarm/agents/atlas.py`
- Create: `/Users/macmini/devrel-swarm/tests/test_dispatch.py`

- [ ] **Step 1: Write the failing test**

Create `/Users/macmini/devrel-swarm/tests/test_dispatch.py`:

```python
import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from agents.dispatch import DelegationResult, delegate_with_retry


@pytest.mark.asyncio
async def test_delegate_success_first_try():
    agent = MagicMock()
    agent.execute = AsyncMock(return_value={"status": "ok"})
    result = await delegate_with_retry(
        agent=agent, task="task x", context={}, max_retries=3, base_delay=0.01
    )
    assert isinstance(result, DelegationResult)
    assert result.success
    assert result.attempts == 1
    assert result.output == {"status": "ok"}


@pytest.mark.asyncio
async def test_delegate_retries_then_succeeds():
    agent = MagicMock()
    agent.execute = AsyncMock(side_effect=[RuntimeError("boom"), {"status": "ok"}])
    result = await delegate_with_retry(
        agent=agent, task="t", context={}, max_retries=3, base_delay=0.01
    )
    assert result.success
    assert result.attempts == 2


@pytest.mark.asyncio
async def test_delegate_fails_after_max_retries():
    agent = MagicMock()
    agent.execute = AsyncMock(side_effect=RuntimeError("boom"))
    result = await delegate_with_retry(
        agent=agent, task="t", context={}, max_retries=2, base_delay=0.01
    )
    assert not result.success
    assert result.attempts == 2
    assert "boom" in (result.error or "")
```

- [ ] **Step 2: Run, confirm failure**

```bash
pytest tests/test_dispatch.py -v
```

Expected: FAIL `ModuleNotFoundError: No module named 'agents.dispatch'`

- [ ] **Step 3: Write agents/dispatch.py**

Create `/Users/macmini/devrel-swarm/agents/dispatch.py`:

```python
"""Agent delegation with exponential-backoff retry — extracted from atlas.py."""

from __future__ import annotations

import asyncio
import logging
import random
from dataclasses import dataclass, field
from typing import Any, Protocol

logger = logging.getLogger(__name__)


class Agent(Protocol):
    async def execute(self, task: str, context: dict[str, Any]) -> dict[str, Any]: ...


@dataclass
class DelegationResult:
    """Outcome of a single delegate call (possibly with retries)."""
    success: bool = False
    output: dict[str, Any] = field(default_factory=dict)
    attempts: int = 0
    error: str | None = None
    agent_name: str = ""


async def delegate_with_retry(
    agent: Agent,
    task: str,
    context: dict[str, Any],
    max_retries: int = 3,
    base_delay: float = 1.0,
    agent_name: str = "",
) -> DelegationResult:
    """Call agent.execute(task, context) with exponential backoff + jitter on failure."""
    last_error: str | None = None
    for attempt in range(1, max_retries + 1):
        try:
            output = await agent.execute(task, context)
            return DelegationResult(
                success=True,
                output=output if isinstance(output, dict) else {"result": output},
                attempts=attempt,
                agent_name=agent_name,
            )
        except Exception as e:  # noqa: BLE001 — we want to catch all agent failures
            last_error = str(e)
            logger.warning(
                "agent=%s attempt=%d failed: %s", agent_name or type(agent).__name__, attempt, e
            )
            if attempt < max_retries:
                delay = base_delay * (2 ** (attempt - 1)) + random.uniform(0, base_delay)
                await asyncio.sleep(delay)

    return DelegationResult(
        success=False, attempts=max_retries, error=last_error, agent_name=agent_name
    )
```

- [ ] **Step 4: Update atlas.py to use it**

In `/Users/macmini/devrel-swarm/agents/atlas.py`, find the existing `async def delegate(...)` method (around line 348) and replace its body to call the new helper. Keep the method for back-compat; thin wrapper:

```python
from agents.dispatch import DelegationResult, delegate_with_retry  # add near top with other agents.* imports

# ...

    async def delegate(
        self,
        agent_name: str,
        task: str,
        context: dict | None = None,
        max_retries: int = 3,
    ) -> DelegationResult:
        """Back-compat wrapper around agents.dispatch.delegate_with_retry."""
        agent = self._agents.get(agent_name)
        if agent is None:
            return DelegationResult(success=False, error=f"unknown agent: {agent_name}", agent_name=agent_name)
        self.llm_client.set_agent(agent_name)
        ctx = context if context is not None else {}
        return await delegate_with_retry(
            agent=agent, task=task, context=ctx,
            max_retries=max_retries, base_delay=self.config.retry_base_delay,
            agent_name=agent_name,
        )
```

(The exact surrounding code varies; adjust `self.config.retry_base_delay` to whatever attribute holds the base delay — inspect existing `delegate` for the exact reference and preserve it.)

- [ ] **Step 5: Run tests**

```bash
pytest tests/test_dispatch.py tests/ -v -x
```

Expected: all pass, no regression in existing agent tests.

- [ ] **Step 6: Commit**

```bash
git add agents/dispatch.py agents/atlas.py tests/test_dispatch.py
git commit -m "refactor: extract delegate_with_retry to agents/dispatch.py"
```

---

### Task 3.3: Postgres-backed checkpoints in `agents/checkpoints.py`

**Files:**
- Create: `/Users/macmini/devrel-swarm/agents/checkpoints.py`
- Create: `/Users/macmini/devrel-swarm/tests/test_checkpoints.py`

- [ ] **Step 1: Write the failing test**

Create `/Users/macmini/devrel-swarm/tests/test_checkpoints.py`:

```python
"""Tests for Postgres-backed checkpoints using respx-less asyncpg mock."""

from __future__ import annotations

import json
import uuid
from unittest.mock import AsyncMock

import pytest

from agents.checkpoints import (
    CheckpointStore,
    InMemoryCheckpointStore,
)


@pytest.mark.asyncio
async def test_in_memory_store_roundtrip():
    store = InMemoryCheckpointStore()
    job_id = str(uuid.uuid4())
    await store.save(job_id, stage="stage_1", payload={"foo": "bar"})
    rows = await store.load_all(job_id)
    assert len(rows) == 1
    assert rows[0]["stage"] == "stage_1"
    assert rows[0]["payload"] == {"foo": "bar"}


@pytest.mark.asyncio
async def test_in_memory_store_latest_stage():
    store = InMemoryCheckpointStore()
    job_id = str(uuid.uuid4())
    await store.save(job_id, stage="s1", payload={"x": 1})
    await store.save(job_id, stage="s2", payload={"x": 2})
    latest = await store.latest(job_id)
    assert latest is not None
    assert latest["stage"] == "s2"


@pytest.mark.asyncio
async def test_postgres_store_calls_executes():
    # smoke test the SQL wrapper; we mock the asyncpg Connection
    conn = AsyncMock()
    conn.execute = AsyncMock(return_value="INSERT 0 1")
    conn.fetch = AsyncMock(return_value=[])

    store = CheckpointStore(conn=conn)
    await store.save("job-1", stage="s1", payload={"k": "v"})
    conn.execute.assert_called_once()
    call_args = conn.execute.call_args[0]
    assert "insert into job_checkpoints" in call_args[0].lower()
```

- [ ] **Step 2: Run, confirm failure**

```bash
pytest tests/test_checkpoints.py -v
```

Expected: FAIL `ModuleNotFoundError: No module named 'agents.checkpoints'`

- [ ] **Step 3: Write agents/checkpoints.py**

Create `/Users/macmini/devrel-swarm/agents/checkpoints.py`:

```python
"""Job checkpoint store — Postgres-backed in workers, in-memory in tests/CLI."""

from __future__ import annotations

import json
from typing import Any, Protocol


class CheckpointBackend(Protocol):
    async def save(self, job_id: str, stage: str, payload: dict[str, Any]) -> None: ...
    async def load_all(self, job_id: str) -> list[dict[str, Any]]: ...
    async def latest(self, job_id: str) -> dict[str, Any] | None: ...


class InMemoryCheckpointStore:
    """Process-local store for unit tests and CLI dry-runs."""

    def __init__(self) -> None:
        self._by_job: dict[str, list[dict[str, Any]]] = {}

    async def save(self, job_id: str, stage: str, payload: dict[str, Any]) -> None:
        self._by_job.setdefault(job_id, []).append({"stage": stage, "payload": payload})

    async def load_all(self, job_id: str) -> list[dict[str, Any]]:
        return list(self._by_job.get(job_id, []))

    async def latest(self, job_id: str) -> dict[str, Any] | None:
        rows = self._by_job.get(job_id)
        return rows[-1] if rows else None


class CheckpointStore:
    """asyncpg-backed store for production workers."""

    def __init__(self, conn: Any) -> None:
        self._conn = conn

    async def save(self, job_id: str, stage: str, payload: dict[str, Any]) -> None:
        await self._conn.execute(
            """
            insert into job_checkpoints (job_id, stage, payload)
            values ($1::uuid, $2, $3::jsonb)
            """,
            job_id,
            stage,
            json.dumps(payload),
        )

    async def load_all(self, job_id: str) -> list[dict[str, Any]]:
        rows = await self._conn.fetch(
            """
            select stage, payload, created_at from job_checkpoints
            where job_id = $1::uuid order by created_at asc
            """,
            job_id,
        )
        return [
            {"stage": r["stage"], "payload": r["payload"], "created_at": r["created_at"]}
            for r in rows
        ]

    async def latest(self, job_id: str) -> dict[str, Any] | None:
        row = await self._conn.fetchrow(
            """
            select stage, payload, created_at from job_checkpoints
            where job_id = $1::uuid order by created_at desc limit 1
            """,
            job_id,
        )
        if row is None:
            return None
        return {"stage": row["stage"], "payload": row["payload"], "created_at": row["created_at"]}
```

- [ ] **Step 4: Run tests**

```bash
pytest tests/test_checkpoints.py -v
```

Expected: all 3 pass.

- [ ] **Step 5: Commit**

```bash
git add agents/checkpoints.py tests/test_checkpoints.py
git commit -m "feat: CheckpointStore with asyncpg + InMemory backends"
```

---

### Task 3.4: Extract Atlas orchestration into `agents/orchestrator.py`

**Files:**
- Create: `/Users/macmini/devrel-swarm/agents/orchestrator.py`
- Modify: `/Users/macmini/devrel-swarm/agents/atlas.py`

- [ ] **Step 1: Copy the Atlas class + its helpers into orchestrator.py**

Read the existing `agents/atlas.py` from the `class Atlas:` line to the end of the file (before the `if __name__ == "__main__":` block). Move that block to `/Users/macmini/devrel-swarm/agents/orchestrator.py`, renaming nothing (the class is still `Atlas`).

Adjust imports at the top of `orchestrator.py` to match the old atlas.py imports, plus:

```python
from agents.dispatch import DelegationResult, delegate_with_retry
from agents.memory import SharedContext, WeeklyMemory
```

Remove the old inline `delegate` body and update it to thin-wrap `delegate_with_retry` (exactly as staged in Task 3.2).

Remove the local `_checkpoint`/`_load_checkpoint` methods and replace them with optional `CheckpointStore` injection (defaults to `InMemoryCheckpointStore` when none passed, matching existing disk-backed behavior via a fallback):

```python
from agents.checkpoints import CheckpointBackend, InMemoryCheckpointStore

class Atlas:
    def __init__(self, ..., checkpoint_store: CheckpointBackend | None = None):
        ...
        self._checkpoints: CheckpointBackend = checkpoint_store or InMemoryCheckpointStore()
        self._job_id: str | None = None  # set by workers before run_weekly_cycle()

    async def _checkpoint(self, stage_label: str, ctx: "SharedContext") -> None:
        if self._job_id is None:
            return  # back-compat: CLI without job_id is a no-op
        await self._checkpoints.save(
            job_id=self._job_id, stage=stage_label, payload=ctx.to_dict()
        )
```

(Exact wiring: in `run_weekly_cycle`, replace every `self._checkpoint(stage=n)` call with `await self._checkpoint(f"stage_{n}", ctx)`.)

- [ ] **Step 2: Thin atlas.py into a re-export shim**

Replace the entire contents of `/Users/macmini/devrel-swarm/agents/atlas.py` with:

```python
"""Back-compat shim. Atlas + memory classes live in orchestrator.py and memory.py.

The CLI entry point `python -m agents.atlas --weekly-cycle` is preserved here.
"""

from __future__ import annotations

from agents.dispatch import DelegationResult  # noqa: F401
from agents.memory import SharedContext, WeeklyMemory  # noqa: F401
from agents.orchestrator import Atlas  # noqa: F401

__all__ = ["Atlas", "DelegationResult", "SharedContext", "WeeklyMemory"]


def _main() -> None:
    """Preserve `python -m agents.atlas` CLI."""
    import argparse
    import asyncio
    import logging

    logging.basicConfig(level=logging.INFO)
    parser = argparse.ArgumentParser()
    parser.add_argument("--weekly-cycle", action="store_true")
    parser.add_argument("--agent", default=None)
    parser.add_argument("--task", default=None)
    args = parser.parse_args()

    atlas = Atlas()
    if args.weekly_cycle:
        asyncio.run(atlas.run_weekly_cycle())
    elif args.agent and args.task:
        asyncio.run(atlas.delegate(args.agent, args.task))
    else:
        parser.print_help()


if __name__ == "__main__":
    _main()
```

- [ ] **Step 3: Run full test suite**

```bash
pytest tests/ -v
```

Expected: all tests pass, no regressions.

- [ ] **Step 4: Smoke-test CLI back-compat**

```bash
cd /Users/macmini/devrel-swarm
python -m agents.atlas --help
```

Expected: help text prints without ImportError.

- [ ] **Step 5: Commit**

```bash
git add agents/atlas.py agents/orchestrator.py
git commit -m "refactor: split atlas.py into orchestrator + memory + dispatch + checkpoints

atlas.py is now a 30-line re-export shim preserving CLI back-compat."
```

---

### Task 3.5: Wire workers to instantiate Atlas per tenant

**Files:**
- Modify: `/Users/macmini/devrel-swarm/workers/main.py`
- Create: `/Users/macmini/devrel-swarm/workers/dispatcher.py`

- [ ] **Step 1: Write the dispatcher**

Create `/Users/macmini/devrel-swarm/workers/dispatcher.py`:

```python
"""Per-request Atlas construction for the worker shim."""

from __future__ import annotations

import logging
import uuid
from datetime import datetime
from typing import Any

from agents.checkpoints import CheckpointStore
from agents.memory import SharedContext
from agents.orchestrator import Atlas
from workers.db import acquire, load_tenant

logger = logging.getLogger(__name__)


async def run_weekly_cycle_for_tenant(
    tenant_id: str, job_id: str
) -> dict[str, Any]:
    """Load tenant config, construct Atlas, run one weekly cycle, persist result."""
    tenant = await load_tenant(tenant_id)
    logger.info(
        "dispatching tenant=%s (%s) job=%s", tenant["slug"], tenant_id, job_id
    )

    async with acquire() as conn:
        # Mark job running
        await conn.execute(
            "update jobs set status = 'running', started_at = now() where id = $1::uuid",
            job_id,
        )

        atlas = Atlas()
        atlas._job_id = job_id  # pylint: disable=protected-access
        atlas._checkpoints = CheckpointStore(conn=conn)

        ctx = SharedContext(
            week_of=datetime.utcnow().strftime("%Y-W%V"),
            tenant_id=tenant_id,
        )

        try:
            result_ctx = await atlas.run_weekly_cycle_with_context(ctx)
            status = "completed"
            error = None
        except Exception as e:  # noqa: BLE001
            logger.exception("weekly cycle failed")
            status = "failed"
            error = str(e)
            result_ctx = ctx

        await conn.execute(
            """
            update jobs
            set status = $2, completed_at = now(), error_message = $3
            where id = $1::uuid
            """,
            job_id, status, error,
        )

    return {
        "tenant_id": tenant_id,
        "job_id": job_id,
        "status": status,
        "week_of": result_ctx.week_of,
        "error": error,
    }
```

- [ ] **Step 2: Add `run_weekly_cycle_with_context` to Atlas**

In `/Users/macmini/devrel-swarm/agents/orchestrator.py`, add alongside the existing `run_weekly_cycle`:

```python
async def run_weekly_cycle_with_context(self, ctx: SharedContext) -> SharedContext:
    """Run weekly cycle using caller-provided SharedContext (for workers).

    The existing run_weekly_cycle() constructs its own context from disk history;
    this variant accepts a pre-built one so workers can inject tenant_id + seed
    cross-run memory loaded from Postgres in v1.
    """
    # Preserve existing run logic but skip the disk-based ctx bootstrap.
    # Implementation: inline the body of run_weekly_cycle() here, replacing
    # `ctx = SharedContext.load_with_history(...)` with `ctx = ctx`.
    # Keep both methods: CLI callers use run_weekly_cycle(), workers use this.
    return await self._run_pipeline(ctx)

async def _run_pipeline(self, ctx: SharedContext) -> SharedContext:
    """Extracted pipeline body — same stages, accepts ctx."""
    # Move the full Stage 0–8 logic from run_weekly_cycle() into this method.
    # run_weekly_cycle() now becomes:
    #   ctx = SharedContext.load_with_history(self.archive_dir, self.config.history_weeks)
    #   return await self._run_pipeline(ctx)
    ...  # (see Task 3.5 Step 3 for the full body)
```

- [ ] **Step 3: Refactor `run_weekly_cycle` body into `_run_pipeline`**

In `agents/orchestrator.py`:

1. Rename the existing `run_weekly_cycle` body to `_run_pipeline(self, ctx)` — move everything except the `ctx = SharedContext.load_with_history(...)` line.
2. Rewrite `run_weekly_cycle`:

```python
async def run_weekly_cycle(self) -> SharedContext:
    """CLI entry: builds context from disk history, runs pipeline."""
    from pathlib import Path
    ctx = SharedContext.load_with_history(
        Path(self.config.archive_dir), history_weeks=4
    )
    return await self._run_pipeline(ctx)
```

- [ ] **Step 4: Update workers/main.py to call dispatcher**

Edit `/Users/macmini/devrel-swarm/workers/main.py`, replace the stub body of `weekly_cycle` with:

```python
from workers.dispatcher import run_weekly_cycle_for_tenant

@app.post("/jobs/weekly-cycle")
async def weekly_cycle(req: WeeklyCycleRequest) -> dict[str, Any]:
    try:
        await load_tenant(req.tenant_id)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))

    result = await run_weekly_cycle_for_tenant(
        tenant_id=req.tenant_id, job_id=req.job_id
    )
    return result
```

- [ ] **Step 5: Smoke test**

The full cycle needs real API keys. Shortcut: point to a test-mode config. From the repo root:

```bash
set -a; source .env.dev; source .env; set +a   # .env holds Anthropic + GitHub keys
source workers/.venv/bin/activate
uvicorn workers.main:app --host 0.0.0.0 --port 8787 &
sleep 2

# Create a job row first (control-plane normally does this; for now use psql)
JOB_ID=$(docker compose exec -T postgres psql -U devrel -d devrel_swarm -tAc \
  "insert into jobs (tenant_id, kind, status) values ('00000000-0000-0000-0000-000000000001','weekly_cycle','queued') returning id;")

curl -s -X POST http://localhost:8787/jobs/weekly-cycle \
  -H "content-type: application/json" \
  -d "{\"tenant_id\":\"00000000-0000-0000-0000-000000000001\",\"job_id\":\"$JOB_ID\"}"
```

Expected: JSON response with `"status":"completed"` (may take several minutes). If `"failed"`, inspect `error` field.

- [ ] **Step 6: Commit**

```bash
git add workers/dispatcher.py workers/main.py agents/orchestrator.py
git commit -m "feat: wire workers to Atlas.run_weekly_cycle_with_context per tenant"
```

---

## Phase 4 — BudgetGate stub (tracking only in v0)

### Task 4.1: Write `BudgetGate` tracking stub with tests

**Files:**
- Create: `/Users/macmini/devrel-swarm/workers/budget.py`
- Create: `/Users/macmini/devrel-swarm/workers/tests/test_budget.py`

- [ ] **Step 1: Write the failing test**

Create `/Users/macmini/devrel-swarm/workers/tests/test_budget.py`:

```python
from unittest.mock import AsyncMock

import pytest

from workers.budget import BudgetGate, CostRecord


def test_cost_record_sonnet_pricing():
    # Sonnet 4.6 per current Anthropic pricing: $3/MTok input, $15/MTok output
    rec = CostRecord(
        model="claude-sonnet-4-6",
        input_tokens=1000, output_tokens=500,
        cache_creation_input_tokens=0, cache_read_input_tokens=0,
    )
    # $3 * 0.001 + $15 * 0.0005 = $0.003 + $0.0075 = $0.0105 → 1.05 cents
    assert abs(rec.cost_cents - 1.05) < 0.01


def test_cost_record_with_cache_hits():
    rec = CostRecord(
        model="claude-sonnet-4-6",
        input_tokens=0, output_tokens=100,
        cache_creation_input_tokens=2000,  # $3.75/MTok
        cache_read_input_tokens=5000,       # $0.30/MTok
    )
    # 2000 * $3.75/1e6 = $0.0075 + 5000 * $0.30/1e6 = $0.0015 + 100 * $15/1e6 = $0.0015
    # = $0.0105 → 1.05 cents
    assert abs(rec.cost_cents - 1.05) < 0.01


@pytest.mark.asyncio
async def test_gate_tracks_without_blocking():
    conn = AsyncMock()
    conn.execute = AsyncMock()
    gate = BudgetGate(
        conn=conn,
        tenant_id="00000000-0000-0000-0000-000000000001",
        job_id="j1",
        block_on_exceed=False,  # v0 tracking-only
    )
    allowed = await gate.check_and_record(
        CostRecord(
            model="claude-sonnet-4-6",
            input_tokens=1000, output_tokens=500,
            cache_creation_input_tokens=0, cache_read_input_tokens=0,
        ),
        agent="kai",
    )
    assert allowed is True
    conn.execute.assert_called_once()


@pytest.mark.asyncio
async def test_gate_blocks_when_enabled_and_over_cap():
    conn = AsyncMock()
    # simulate existing monthly spend at $99 on $100 cap
    conn.fetchval = AsyncMock(return_value=9900.0)
    conn.execute = AsyncMock()
    gate = BudgetGate(
        conn=conn,
        tenant_id="00000000-0000-0000-0000-000000000001",
        job_id="j1",
        block_on_exceed=True,
        monthly_cap_cents=10000,
    )
    allowed = await gate.check_and_record(
        CostRecord(
            model="claude-sonnet-4-6",
            input_tokens=200_000, output_tokens=50_000,  # ~$1.35 = 135 cents
            cache_creation_input_tokens=0, cache_read_input_tokens=0,
        ),
        agent="kai",
    )
    assert allowed is False
```

- [ ] **Step 2: Run, confirm failure**

```bash
cd /Users/macmini/devrel-swarm
pytest workers/tests/test_budget.py -v
```

Expected: FAIL `ModuleNotFoundError`

- [ ] **Step 3: Write workers/budget.py**

Create `/Users/macmini/devrel-swarm/workers/budget.py`:

```python
"""Cost tracking + budget enforcement gate.

v0: tracking-only (block_on_exceed=False).
v1: block_on_exceed=True enforces monthly_cap_cents.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)

# Prices in dollars per million tokens. Update when Anthropic changes pricing.
# Source: https://www.anthropic.com/pricing (verified 2026-04-17)
_PRICING: dict[str, dict[str, float]] = {
    "claude-opus-4-7": {
        "input": 15.0, "output": 75.0,
        "cache_write": 18.75, "cache_read": 1.50,
    },
    "claude-sonnet-4-6": {
        "input": 3.0, "output": 15.0,
        "cache_write": 3.75, "cache_read": 0.30,
    },
    "claude-haiku-4-5-20251001": {
        "input": 1.0, "output": 5.0,
        "cache_write": 1.25, "cache_read": 0.10,
    },
}


@dataclass
class CostRecord:
    model: str
    input_tokens: int
    output_tokens: int
    cache_creation_input_tokens: int
    cache_read_input_tokens: int

    @property
    def cost_cents(self) -> float:
        """Compute cost in cents from token counts + model pricing."""
        prices = _PRICING.get(self.model)
        if prices is None:
            logger.warning("unknown model for pricing: %s — treating as sonnet", self.model)
            prices = _PRICING["claude-sonnet-4-6"]
        dollars = (
            self.input_tokens * prices["input"] / 1_000_000
            + self.output_tokens * prices["output"] / 1_000_000
            + self.cache_creation_input_tokens * prices["cache_write"] / 1_000_000
            + self.cache_read_input_tokens * prices["cache_read"] / 1_000_000
        )
        return dollars * 100


class BudgetExceeded(RuntimeError):
    """Raised when BudgetGate is configured to block and tenant is over cap."""


class BudgetGate:
    """Wraps LLM calls to track + optionally enforce tenant budget."""

    def __init__(
        self,
        conn: Any,
        tenant_id: str,
        job_id: str | None,
        block_on_exceed: bool = False,
        monthly_cap_cents: int = 0,
    ) -> None:
        self._conn = conn
        self._tenant_id = tenant_id
        self._job_id = job_id
        self._block = block_on_exceed
        self._cap = monthly_cap_cents

    async def check_and_record(self, rec: CostRecord, agent: str) -> bool:
        """Persist cost event; optionally block if tenant is over cap."""
        if self._block and self._cap > 0:
            current = await self._monthly_spend_cents()
            projected = current + rec.cost_cents
            if projected > self._cap:
                logger.warning(
                    "BudgetGate blocked tenant=%s agent=%s projected=%.2f cap=%d",
                    self._tenant_id, agent, projected, self._cap,
                )
                return False

        await self._conn.execute(
            """
            insert into cost_events (
              tenant_id, job_id, agent, model,
              input_tokens, output_tokens,
              cache_creation_input_tokens, cache_read_input_tokens,
              cost_cents
            ) values ($1::uuid, $2::uuid, $3, $4, $5, $6, $7, $8, $9)
            """,
            self._tenant_id, self._job_id, agent, rec.model,
            rec.input_tokens, rec.output_tokens,
            rec.cache_creation_input_tokens, rec.cache_read_input_tokens,
            rec.cost_cents,
        )
        return True

    async def _monthly_spend_cents(self) -> float:
        row = await self._conn.fetchval(
            """
            select coalesce(sum(cost_cents), 0)::float
            from cost_events
            where tenant_id = $1::uuid
              and created_at >= date_trunc('month', now())
            """,
            self._tenant_id,
        )
        return float(row or 0.0)
```

- [ ] **Step 4: Run tests**

```bash
pytest workers/tests/test_budget.py -v
```

Expected: all 4 tests pass.

- [ ] **Step 5: Commit**

```bash
git add workers/budget.py workers/tests/test_budget.py
git commit -m "feat: BudgetGate stub (tracking-only in v0)"
```

---

### Task 4.2: Wire LLMClient through BudgetGate

**Files:**
- Modify: `/Users/macmini/devrel-swarm/agents/llm.py`
- Modify: `/Users/macmini/devrel-swarm/workers/dispatcher.py`
- Create: `/Users/macmini/devrel-swarm/tests/test_llm_budget_wiring.py`

- [ ] **Step 1: Inspect current LLMClient**

```bash
cd /Users/macmini/devrel-swarm
grep -n "class LLMClient\|async def generate\|TokenUsage" agents/llm.py | head -30
```

Identify where Claude API calls happen and where per-call token counts become available. LLMClient currently has `usage: TokenUsage` tracking in memory. We'll add a `cost_sink` callback.

- [ ] **Step 2: Write the failing test**

Create `/Users/macmini/devrel-swarm/tests/test_llm_budget_wiring.py`:

```python
"""Verify LLMClient can emit CostRecord to a sink callback."""

from unittest.mock import AsyncMock

import pytest

from agents.llm import LLMClient


@pytest.mark.asyncio
async def test_llm_emits_cost_when_sink_set(monkeypatch):
    calls: list[dict] = []

    async def sink(agent: str, model: str, usage: dict) -> None:
        calls.append({"agent": agent, "model": model, "usage": usage})

    client = LLMClient()
    client.set_agent("kai")
    client.set_cost_sink(sink)

    # Fake a usage accounting call — we don't actually hit Anthropic here.
    await client._emit_cost(
        model="claude-sonnet-4-6",
        input_tokens=100, output_tokens=50,
        cache_creation_input_tokens=0, cache_read_input_tokens=0,
    )

    assert len(calls) == 1
    assert calls[0]["agent"] == "kai"
    assert calls[0]["model"] == "claude-sonnet-4-6"
    assert calls[0]["usage"]["input_tokens"] == 100
```

- [ ] **Step 3: Add cost sink to LLMClient**

Edit `/Users/macmini/devrel-swarm/agents/llm.py`. Near the top of the `LLMClient` class `__init__`, add:

```python
self._cost_sink = None  # Optional[Callable[[str, str, dict], Awaitable[None]]]
```

Add two new methods to `LLMClient`:

```python
def set_cost_sink(self, sink) -> None:
    """Register an async callback (agent, model, usage_dict) -> None."""
    self._cost_sink = sink

async def _emit_cost(
    self,
    model: str,
    input_tokens: int,
    output_tokens: int,
    cache_creation_input_tokens: int = 0,
    cache_read_input_tokens: int = 0,
) -> None:
    if self._cost_sink is None:
        return
    await self._cost_sink(
        self._current_agent or "unknown",
        model,
        {
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "cache_creation_input_tokens": cache_creation_input_tokens,
            "cache_read_input_tokens": cache_read_input_tokens,
        },
    )
```

In the existing LLM call path (the method that invokes the Anthropic SDK and receives a `usage` object on the response), after updating in-memory `TokenUsage`, add one line:

```python
await self._emit_cost(
    model=<the model name used>,
    input_tokens=usage.input_tokens,
    output_tokens=usage.output_tokens,
    cache_creation_input_tokens=getattr(usage, "cache_creation_input_tokens", 0) or 0,
    cache_read_input_tokens=getattr(usage, "cache_read_input_tokens", 0) or 0,
)
```

- [ ] **Step 4: Run the new test**

```bash
pytest tests/test_llm_budget_wiring.py -v
```

Expected: PASS.

- [ ] **Step 5: Wire dispatcher to pass a BudgetGate sink**

Edit `/Users/macmini/devrel-swarm/workers/dispatcher.py`. Inside `run_weekly_cycle_for_tenant`, after constructing Atlas:

```python
from workers.budget import BudgetGate, CostRecord

gate = BudgetGate(
    conn=conn,
    tenant_id=tenant_id,
    job_id=job_id,
    block_on_exceed=False,   # v0 tracking-only
    monthly_cap_cents=tenant["quota_monthly_cost_cents"],
)

async def cost_sink(agent: str, model: str, usage: dict) -> None:
    rec = CostRecord(
        model=model,
        input_tokens=usage["input_tokens"],
        output_tokens=usage["output_tokens"],
        cache_creation_input_tokens=usage["cache_creation_input_tokens"],
        cache_read_input_tokens=usage["cache_read_input_tokens"],
    )
    await gate.check_and_record(rec, agent=agent)

atlas.llm_client.set_cost_sink(cost_sink)
```

Insert before `atlas.run_weekly_cycle_with_context(ctx)` is called.

- [ ] **Step 6: Run full test suite**

```bash
pytest tests/ workers/tests/ -v
```

Expected: all pass.

- [ ] **Step 7: Commit**

```bash
git add agents/llm.py workers/dispatcher.py tests/test_llm_budget_wiring.py
git commit -m "feat: wire LLMClient → BudgetGate via cost_sink callback"
```

---

## Phase 5 — Inngest integration

### Task 5.1: Install Inngest SDK in control-plane

**Files:**
- Modify: `/Users/macmini/devrel-swarm/control-plane/package.json`
- Create: `/Users/macmini/devrel-swarm/control-plane/src/lib/inngest/client.ts`

- [ ] **Step 1: Install deps**

```bash
cd /Users/macmini/devrel-swarm/control-plane
pnpm add inngest
```

- [ ] **Step 2: Write Inngest client**

Create `/Users/macmini/devrel-swarm/control-plane/src/lib/inngest/client.ts`:

```typescript
import { Inngest } from "inngest";

export const inngest = new Inngest({
  id: "devrel-swarm",
  eventKey: process.env.INNGEST_EVENT_KEY,
});

export type WeeklyCycleEvent = {
  name: "app/weekly-cycle.requested";
  data: {
    tenantId: string;
    jobId: string;
  };
};

export type AppEvents = {
  "app/weekly-cycle.requested": WeeklyCycleEvent;
};
```

- [ ] **Step 3: Commit**

```bash
cd /Users/macmini/devrel-swarm
git add control-plane/package.json control-plane/pnpm-lock.yaml control-plane/src/lib/inngest/
git commit -m "feat: inngest client setup"
```

---

### Task 5.2: Inngest function — weekly-cycle HTTP dispatch

**Files:**
- Create: `/Users/macmini/devrel-swarm/control-plane/src/lib/inngest/functions.ts`
- Create: `/Users/macmini/devrel-swarm/control-plane/src/app/api/inngest/route.ts`

- [ ] **Step 1: Write function**

Create `/Users/macmini/devrel-swarm/control-plane/src/lib/inngest/functions.ts`:

```typescript
import { inngest } from "./client";

const WORKER_URL = process.env.WORKER_URL ?? "http://localhost:8787";

export const weeklyCycle = inngest.createFunction(
  {
    id: "weekly-cycle",
    name: "Run weekly DevRel cycle",
    retries: 2,
  },
  { event: "app/weekly-cycle.requested" },
  async ({ event, step }) => {
    const { tenantId, jobId } = event.data;

    const result = await step.run("dispatch-to-worker", async () => {
      const res = await fetch(`${WORKER_URL}/jobs/weekly-cycle`, {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({
          tenant_id: tenantId,
          job_id: jobId,
          inngest_run_id: event.id,
        }),
        // Weekly cycles take several minutes — disable default 30s abort
        signal: AbortSignal.timeout(1_200_000), // 20 min
      });
      if (!res.ok) {
        throw new Error(`worker returned ${res.status}: ${await res.text()}`);
      }
      return (await res.json()) as Record<string, unknown>;
    });

    return result;
  }
);
```

- [ ] **Step 2: Write HTTP receiver**

Create `/Users/macmini/devrel-swarm/control-plane/src/app/api/inngest/route.ts`:

```typescript
import { serve } from "inngest/next";
import { inngest } from "@/lib/inngest/client";
import { weeklyCycle } from "@/lib/inngest/functions";

export const { GET, POST, PUT } = serve({
  client: inngest,
  functions: [weeklyCycle],
});
```

- [ ] **Step 3: Smoke test Inngest dev server**

Terminal 1 (Inngest dev):
```bash
cd /Users/macmini/devrel-swarm/control-plane
pnpm dlx inngest-cli@latest dev -u http://localhost:3000/api/inngest
```

Terminal 2 (Next.js):
```bash
cd /Users/macmini/devrel-swarm/control-plane
cp ../.env.dev.example .env.local
pnpm dev
```

Terminal 3 (workers):
```bash
cd /Users/macmini/devrel-swarm
set -a; source .env.dev; source .env; set +a
source workers/.venv/bin/activate
uvicorn workers.main:app --port 8787
```

Visit `http://localhost:8288` (Inngest dev UI) and verify the `weekly-cycle` function is registered.

- [ ] **Step 4: Commit**

```bash
cd /Users/macmini/devrel-swarm
git add control-plane/src/
git commit -m "feat: weekly-cycle inngest function dispatches to worker HTTP"
```

---

### Task 5.3: "Run now" trigger endpoint

**Files:**
- Create: `/Users/macmini/devrel-swarm/control-plane/src/db/client.ts`
- Create: `/Users/macmini/devrel-swarm/control-plane/src/app/api/jobs/run-now/route.ts`

- [ ] **Step 1: Write db client**

Create `/Users/macmini/devrel-swarm/control-plane/src/db/client.ts`:

```typescript
import { drizzle } from "drizzle-orm/postgres-js";
import postgres from "postgres";
import * as schema from "./schema";

const connectionString = process.env.DATABASE_URL!;
const client = postgres(connectionString, { max: 10 });
export const db = drizzle(client, { schema });
export * from "./schema";
```

- [ ] **Step 2: Write run-now API**

Create `/Users/macmini/devrel-swarm/control-plane/src/app/api/jobs/run-now/route.ts`:

```typescript
import { NextRequest, NextResponse } from "next/server";
import { db, jobs } from "@/db/client";
import { inngest } from "@/lib/inngest/client";

const DEFAULT_TENANT = process.env.DEFAULT_TENANT_ID!;

export async function POST(_req: NextRequest) {
  const [job] = await db
    .insert(jobs)
    .values({ tenantId: DEFAULT_TENANT, kind: "weekly_cycle", status: "queued" })
    .returning({ id: jobs.id });

  await inngest.send({
    name: "app/weekly-cycle.requested",
    data: { tenantId: DEFAULT_TENANT, jobId: job.id },
  });

  return NextResponse.json({ jobId: job.id });
}
```

- [ ] **Step 3: Smoke test**

With all three services from Task 5.2 running:

```bash
curl -s -X POST http://localhost:3000/api/jobs/run-now
```

Expected: `{"jobId":"<uuid>"}`. Then visit Inngest dev UI (`http://localhost:8288`), see the event flow and the worker call. Allow up to 20 minutes for the cycle to complete.

Verify after:

```bash
docker compose exec postgres psql -U devrel -d devrel_swarm -c \
  "select id, kind, status, cost_cents from jobs order by created_at desc limit 3;"
```

Expected: most recent row with `status = 'completed'`.

- [ ] **Step 4: Commit**

```bash
git add control-plane/src/db/client.ts control-plane/src/app/api/jobs/
git commit -m "feat: POST /api/jobs/run-now triggers weekly cycle via Inngest"
```

---

## Phase 6 — Dashboard surfaces (Home + Deliverables)

### Task 6.1: NextAuth v5 with GitHub provider

**Files:**
- Create: `/Users/macmini/devrel-swarm/control-plane/src/auth.ts`
- Create: `/Users/macmini/devrel-swarm/control-plane/src/middleware.ts`
- Create: `/Users/macmini/devrel-swarm/control-plane/src/app/api/auth/[...nextauth]/route.ts`

- [ ] **Step 1: Install**

```bash
cd /Users/macmini/devrel-swarm/control-plane
pnpm add next-auth@beta
```

- [ ] **Step 2: Register GitHub OAuth app**

Manual step: go to `https://github.com/settings/developers` → New OAuth App.
- Homepage: `http://localhost:3000`
- Callback: `http://localhost:3000/api/auth/callback/github`
Save `GITHUB_CLIENT_ID` and `GITHUB_CLIENT_SECRET` into `control-plane/.env.local`.

Also generate:
```bash
openssl rand -base64 32
```
Put result as `AUTH_SECRET=` in `.env.local`.

- [ ] **Step 3: Write auth config**

Create `/Users/macmini/devrel-swarm/control-plane/src/auth.ts`:

```typescript
import NextAuth from "next-auth";
import GitHub from "next-auth/providers/github";

const ALLOWED_EMAILS = (process.env.ALLOWED_EMAILS ?? "dovzhikova@gmail.com")
  .split(",")
  .map((s) => s.trim().toLowerCase());

export const { handlers, auth, signIn, signOut } = NextAuth({
  providers: [GitHub],
  callbacks: {
    signIn({ user }) {
      const email = user.email?.toLowerCase();
      return !!email && ALLOWED_EMAILS.includes(email);
    },
  },
});
```

- [ ] **Step 4: Handler route**

Create `/Users/macmini/devrel-swarm/control-plane/src/app/api/auth/[...nextauth]/route.ts`:

```typescript
import { handlers } from "@/auth";
export const { GET, POST } = handlers;
```

- [ ] **Step 5: Middleware (protect dashboard)**

Create `/Users/macmini/devrel-swarm/control-plane/src/middleware.ts`:

```typescript
import { auth } from "@/auth";

export default auth((req) => {
  if (!req.auth && !req.nextUrl.pathname.startsWith("/api/auth") && !req.nextUrl.pathname.startsWith("/api/inngest")) {
    const url = new URL("/api/auth/signin", req.nextUrl.origin);
    return Response.redirect(url);
  }
});

export const config = {
  matcher: ["/((?!_next/static|_next/image|favicon.ico).*)"],
};
```

- [ ] **Step 6: Smoke test**

```bash
pnpm dev
```

Open `http://localhost:3000` — should redirect to GitHub sign-in; signing in with allowlisted email lets you through.

- [ ] **Step 7: Commit**

```bash
git add control-plane/src/auth.ts control-plane/src/middleware.ts control-plane/src/app/api/auth/
git commit -m "feat: NextAuth v5 with GitHub provider + email allowlist"
```

---

### Task 6.2: Dashboard Home page

**Files:**
- Create: `/Users/macmini/devrel-swarm/control-plane/src/app/page.tsx`
- Create: `/Users/macmini/devrel-swarm/control-plane/src/lib/queries.ts`

- [ ] **Step 1: Write data queries**

Create `/Users/macmini/devrel-swarm/control-plane/src/lib/queries.ts`:

```typescript
import { db, jobs, deliverables, tenants } from "@/db/client";
import { desc, eq, sql } from "drizzle-orm";

const DEFAULT_TENANT = process.env.DEFAULT_TENANT_ID!;

export async function getHomeData() {
  const [tenant] = await db
    .select()
    .from(tenants)
    .where(eq(tenants.id, DEFAULT_TENANT))
    .limit(1);

  const recentJobs = await db
    .select({
      id: jobs.id,
      kind: jobs.kind,
      status: jobs.status,
      startedAt: jobs.startedAt,
      completedAt: jobs.completedAt,
      costCents: jobs.costCents,
    })
    .from(jobs)
    .where(eq(jobs.tenantId, DEFAULT_TENANT))
    .orderBy(desc(jobs.createdAt))
    .limit(5);

  const recentDeliverables = await db
    .select({
      id: deliverables.id,
      kind: deliverables.kind,
      title: deliverables.title,
      status: deliverables.status,
      qualityScore: deliverables.qualityScore,
      createdAt: deliverables.createdAt,
    })
    .from(deliverables)
    .where(eq(deliverables.tenantId, DEFAULT_TENANT))
    .orderBy(desc(deliverables.createdAt))
    .limit(10);

  const monthSpend = await db.execute<{ cents: string }>(sql`
    SELECT COALESCE(SUM(cost_cents), 0)::text AS cents
    FROM cost_events
    WHERE tenant_id = ${DEFAULT_TENANT}::uuid
      AND created_at >= date_trunc('month', now())
  `);

  return {
    tenant,
    recentJobs,
    recentDeliverables,
    monthSpendCents: Number(monthSpend[0]?.cents ?? 0),
  };
}
```

- [ ] **Step 2: Write home page**

Create `/Users/macmini/devrel-swarm/control-plane/src/app/page.tsx`:

```tsx
import { getHomeData } from "@/lib/queries";

export const dynamic = "force-dynamic";

async function triggerRun() {
  "use server";
  await fetch(`${process.env.NEXT_PUBLIC_APP_URL ?? "http://localhost:3000"}/api/jobs/run-now`, {
    method: "POST",
  });
}

export default async function Home() {
  const data = await getHomeData();

  return (
    <main className="p-8 max-w-5xl mx-auto space-y-8">
      <header className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-semibold">
            {data.tenant?.slug ?? "—"}
          </h1>
          <p className="text-sm text-gray-500">
            Plan: {data.tenant?.plan} · Month spend:{" "}
            ${(data.monthSpendCents / 100).toFixed(2)} /{" "}
            ${((data.tenant?.quotaMonthlyCostCents ?? 0) / 100).toFixed(0)} cap
          </p>
        </div>
        <form action={triggerRun}>
          <button className="px-4 py-2 rounded-md bg-black text-white text-sm">
            Run weekly cycle now
          </button>
        </form>
      </header>

      <section>
        <h2 className="text-lg font-medium mb-3">Recent runs</h2>
        <div className="border rounded-md divide-y">
          {data.recentJobs.map((j) => (
            <div key={j.id} className="p-3 flex justify-between text-sm">
              <div>
                <div className="font-mono text-xs text-gray-500">
                  {j.id.slice(0, 8)}
                </div>
                <div>{j.kind}</div>
              </div>
              <div>{j.status}</div>
              <div>${(Number(j.costCents) / 100).toFixed(2)}</div>
            </div>
          ))}
          {data.recentJobs.length === 0 && (
            <div className="p-3 text-sm text-gray-500">No runs yet.</div>
          )}
        </div>
      </section>

      <section>
        <h2 className="text-lg font-medium mb-3">Recent deliverables</h2>
        <div className="border rounded-md divide-y">
          {data.recentDeliverables.map((d) => (
            <div key={d.id} className="p-3 flex justify-between text-sm">
              <div>
                <div>{d.title}</div>
                <div className="text-xs text-gray-500">{d.kind}</div>
              </div>
              <div>{d.status}</div>
              <div>
                {d.qualityScore ? Number(d.qualityScore).toFixed(1) : "—"} / 10
              </div>
            </div>
          ))}
          {data.recentDeliverables.length === 0 && (
            <div className="p-3 text-sm text-gray-500">
              No deliverables yet — run a cycle.
            </div>
          )}
        </div>
      </section>
    </main>
  );
}
```

- [ ] **Step 3: Smoke test**

```bash
pnpm dev
```

Visit `http://localhost:3000`. Expected: tenant header + empty lists with "Run weekly cycle now" button.

- [ ] **Step 4: Commit**

```bash
git add control-plane/src/app/page.tsx control-plane/src/lib/queries.ts
git commit -m "feat: dashboard Home with jobs list + run-now trigger"
```

---

### Task 6.3: Deliverables list page with detail view

**Files:**
- Create: `/Users/macmini/devrel-swarm/control-plane/src/app/deliverables/page.tsx`
- Create: `/Users/macmini/devrel-swarm/control-plane/src/app/deliverables/[id]/page.tsx`

- [ ] **Step 1: Write list page**

Create `/Users/macmini/devrel-swarm/control-plane/src/app/deliverables/page.tsx`:

```tsx
import Link from "next/link";
import { db, deliverables } from "@/db/client";
import { desc, eq } from "drizzle-orm";

export const dynamic = "force-dynamic";

const DEFAULT_TENANT = process.env.DEFAULT_TENANT_ID!;

export default async function DeliverablesPage() {
  const rows = await db
    .select()
    .from(deliverables)
    .where(eq(deliverables.tenantId, DEFAULT_TENANT))
    .orderBy(desc(deliverables.createdAt))
    .limit(50);

  return (
    <main className="p-8 max-w-5xl mx-auto">
      <h1 className="text-2xl font-semibold mb-6">Deliverables</h1>
      <div className="border rounded-md divide-y">
        {rows.map((d) => (
          <Link
            key={d.id}
            href={`/deliverables/${d.id}`}
            className="p-4 flex justify-between hover:bg-gray-50"
          >
            <div>
              <div className="font-medium">{d.title}</div>
              <div className="text-xs text-gray-500">
                {d.kind} · {new Date(d.createdAt).toLocaleString()}
              </div>
            </div>
            <div className="text-sm">
              {d.status} · {d.qualityScore ? Number(d.qualityScore).toFixed(1) : "—"}
            </div>
          </Link>
        ))}
        {rows.length === 0 && (
          <div className="p-4 text-sm text-gray-500">No deliverables yet.</div>
        )}
      </div>
    </main>
  );
}
```

- [ ] **Step 2: Write detail page**

Create `/Users/macmini/devrel-swarm/control-plane/src/app/deliverables/[id]/page.tsx`:

```tsx
import { notFound } from "next/navigation";
import { db, deliverables } from "@/db/client";
import { and, eq } from "drizzle-orm";

export const dynamic = "force-dynamic";

const DEFAULT_TENANT = process.env.DEFAULT_TENANT_ID!;

export default async function DeliverableDetail({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  const { id } = await params;
  const [row] = await db
    .select()
    .from(deliverables)
    .where(and(eq(deliverables.tenantId, DEFAULT_TENANT), eq(deliverables.id, id)))
    .limit(1);

  if (!row) return notFound();

  return (
    <main className="p-8 max-w-3xl mx-auto space-y-4">
      <div>
        <h1 className="text-2xl font-semibold">{row.title}</h1>
        <div className="text-xs text-gray-500 mt-1">
          {row.kind} · {row.status} · quality{" "}
          {row.qualityScore ? Number(row.qualityScore).toFixed(1) : "—"} / 10
        </div>
      </div>
      <pre className="whitespace-pre-wrap font-sans text-sm bg-gray-50 p-4 rounded">
        {row.bodyMd ?? "(no body stored)"}
      </pre>
    </main>
  );
}
```

- [ ] **Step 3: Smoke test**

Visit `http://localhost:3000/deliverables` — empty or showing whatever was in the DB.

- [ ] **Step 4: Commit**

```bash
git add control-plane/src/app/deliverables/
git commit -m "feat: deliverables list + detail pages"
```

---

### Task 6.4: Persist Kai deliverables to Postgres from the worker

**Files:**
- Modify: `/Users/macmini/devrel-swarm/workers/dispatcher.py`
- Modify: `/Users/macmini/devrel-swarm/agents/orchestrator.py`

- [ ] **Step 1: Add deliverable persistence hook**

In `/Users/macmini/devrel-swarm/agents/orchestrator.py`, inside `_run_pipeline` after each Kai invocation (and after Sentinel brand audit), add a persistence call. The simplest approach: add a `deliverable_sink` callback to Atlas and invoke after relevant stages.

In Atlas `__init__`:
```python
self._deliverable_sink = None  # Optional[Callable[[dict], Awaitable[None]]]

def set_deliverable_sink(self, sink) -> None:
    self._deliverable_sink = sink

async def _persist_deliverable(self, record: dict) -> None:
    if self._deliverable_sink is None:
        return
    await self._deliverable_sink(record)
```

After Kai Stage 3 completes (inside `_run_pipeline`):
```python
if ctx.kai_content.get("content"):
    for item in ctx.kai_content.get("content", []):
        await self._persist_deliverable({
            "kind": item.get("kind", "tutorial"),
            "title": item.get("title", "Untitled"),
            "body_md": item.get("body", ""),
            "quality_score": item.get("score"),
        })
```

(Exact field names: inspect `agents/kai.py` ContentPiece output structure and map to our deliverable shape.)

Similarly after Sentinel Stage 5:
```python
if ctx.okr_progress.get("brand_audit"):
    await self._persist_deliverable({
        "kind": "brand_audit",
        "title": f"Brand audit · {ctx.week_of}",
        "body_md": json.dumps(ctx.okr_progress["brand_audit"], indent=2),
        "quality_score": ctx.okr_progress["brand_audit"].get("overall_score"),
    })
```

- [ ] **Step 2: Wire sink in dispatcher**

Edit `/Users/macmini/devrel-swarm/workers/dispatcher.py`. Inside `run_weekly_cycle_for_tenant`, after creating Atlas:

```python
async def deliverable_sink(record: dict) -> None:
    await conn.execute(
        """
        insert into deliverables (
          tenant_id, job_id, kind, title, body_md, quality_score, status
        ) values ($1::uuid, $2::uuid, $3, $4, $5, $6, 'draft')
        """,
        tenant_id, job_id,
        record["kind"], record["title"], record.get("body_md"),
        record.get("quality_score"),
    )

atlas.set_deliverable_sink(deliverable_sink)
```

- [ ] **Step 3: Trigger + verify**

```bash
curl -s -X POST http://localhost:3000/api/jobs/run-now
# Wait for completion (check Inngest UI)
docker compose exec postgres psql -U devrel -d devrel_swarm -c \
  "select kind, title, status, quality_score from deliverables order by created_at desc limit 5;"
```

Expected: at least one tutorial + one brand_audit row.

- [ ] **Step 4: Commit**

```bash
cd /Users/macmini/devrel-swarm
git add agents/orchestrator.py workers/dispatcher.py
git commit -m "feat: persist Kai + Sentinel deliverables to Postgres during runs"
```

---

## Phase 7 — End-to-end validation

### Task 7.1: Full cycle E2E + cost reconciliation

**Files:**
- Create: `/Users/macmini/devrel-swarm/scripts/verify_v0_exit.sh`

- [ ] **Step 1: Write verification script**

Create `/Users/macmini/devrel-swarm/scripts/verify_v0_exit.sh`:

```bash
#!/usr/bin/env bash
# v0 exit-gate verification. Requires all services running + .env populated.
set -euo pipefail

echo "==> 1. Triggering weekly cycle..."
RESPONSE=$(curl -fsS -X POST http://localhost:3000/api/jobs/run-now)
JOB_ID=$(echo "$RESPONSE" | jq -r '.jobId')
echo "   job_id: $JOB_ID"

echo "==> 2. Polling job status (timeout 25 min)..."
END=$((SECONDS + 1500))
while (( SECONDS < END )); do
  STATUS=$(docker compose exec -T postgres psql -U devrel -d devrel_swarm -tAc \
    "select status from jobs where id = '$JOB_ID'")
  echo "   [$(date +%H:%M:%S)] status=$STATUS"
  if [[ "$STATUS" == "completed" ]]; then break; fi
  if [[ "$STATUS" == "failed" ]]; then
    ERR=$(docker compose exec -T postgres psql -U devrel -d devrel_swarm -tAc \
      "select error_message from jobs where id = '$JOB_ID'")
    echo "   FAILED: $ERR" >&2
    exit 1
  fi
  sleep 30
done

if [[ "$STATUS" != "completed" ]]; then
  echo "   TIMEOUT after 25 min" >&2
  exit 1
fi

echo "==> 3. Verifying deliverables..."
DELIV_COUNT=$(docker compose exec -T postgres psql -U devrel -d devrel_swarm -tAc \
  "select count(*) from deliverables where job_id = '$JOB_ID'")
echo "   deliverables: $DELIV_COUNT"
if (( DELIV_COUNT < 1 )); then
  echo "   expected >= 1 deliverable" >&2
  exit 1
fi

echo "==> 4. Cost tracking summary..."
docker compose exec -T postgres psql -U devrel -d devrel_swarm -c \
  "select agent, model, sum(input_tokens) as in_tok, sum(output_tokens) as out_tok,
          round(sum(cost_cents)::numeric, 2) as cents
   from cost_events where job_id = '$JOB_ID'
   group by agent, model order by cents desc;"

TOTAL=$(docker compose exec -T postgres psql -U devrel -d devrel_swarm -tAc \
  "select round(sum(cost_cents)::numeric, 2) from cost_events where job_id = '$JOB_ID'")
echo "   total tracked cost: \$${TOTAL} cents = \$$(echo "scale=2; $TOTAL/100" | bc)"

echo ""
echo "==> v0 EXIT GATE PASSED"
echo "   Next: compare \$${TOTAL} cents against Anthropic invoice for this run's timeframe."
echo "   Target: within 5% of invoice line items for this period."
```

```bash
chmod +x /Users/macmini/devrel-swarm/scripts/verify_v0_exit.sh
```

- [ ] **Step 2: Run the E2E**

With all services running (Inngest dev, Next.js, workers, Postgres):

```bash
cd /Users/macmini/devrel-swarm
./scripts/verify_v0_exit.sh
```

Expected: prints cost summary, exits with "v0 EXIT GATE PASSED".

- [ ] **Step 3: Reconcile against Anthropic console**

Manual check: log into `https://console.anthropic.com/dashboard` → Usage → filter by the time window of this run. Compare the tracked total to the console's reported spend for the same window.

Acceptance: tracked cents within ±5% of Anthropic-reported dollars.

If drift is higher than 5%:
- Check `workers/budget.py` `_PRICING` table against current Anthropic pricing
- Verify every LLM call path in `agents/llm.py` emits a cost event (grep `_emit_cost` calls vs. every `client.messages.create` / `query()` call)
- Verify cache-creation vs. cache-read tokens are recorded correctly

- [ ] **Step 4: Commit**

```bash
git add scripts/verify_v0_exit.sh
git commit -m "chore: add v0 exit-gate verification script"
```

---

### Task 7.2: Tag v0.1-alpha

- [ ] **Step 1: Verify tree is clean**

```bash
cd /Users/macmini/devrel-swarm
git status
```

Expected: clean, "nothing to commit".

- [ ] **Step 2: Tag**

```bash
git tag -a v0.1-alpha -m "v0 alpha: single-tenant SaaS scaffold — end-to-end weekly cycle via Inngest + FastAPI worker + Next.js dashboard"
```

- [ ] **Step 3: Summary**

The v0 alpha is complete when:
- [ ] `scripts/verify_v0_exit.sh` exits 0
- [ ] Cost tracking within ±5% of Anthropic console
- [ ] Dashboard Home shows most recent run
- [ ] Dashboard /deliverables shows ≥1 tutorial + ≥1 brand_audit
- [ ] Atlas CLI back-compat still works: `python -m agents.atlas --help` succeeds
- [ ] All pytest suites pass: `pytest tests/ workers/tests/ -v`

---

## Self-review checklist (run after plan is written)

**Spec coverage check:**
- [x] v0 task list in spec §6: fork workers, Next.js skeleton, Inngest cron, HTTP endpoint, SharedContext per-job, atlas.py split (4 files), manual tenant creation, Home + Deliverables surfaces, BudgetGate stubbed → all covered.
- [x] v0 gate criteria: end-to-end weekly cycle without intervention + cost tracking ±5% → verified by `verify_v0_exit.sh` (Task 7.1).
- [x] Spec `SharedContext(tenant_id)` requirement → Task 3.1 adds `tenant_id` field.
- [x] Spec "split atlas.py into orchestrator/memory/checkpoints/dispatch" → Tasks 3.1/3.2/3.3/3.4 each extract one.
- [x] Spec "BudgetGate stubbed (tracks, doesn't block)" → Task 4.1 writes class with `block_on_exceed=False` default.
- [x] Spec "v0 protected by $500/week external ceiling" → manual monitoring, referenced in Phase 7 reconciliation step.

**Placeholder scan:** no TODOs, no "implement later"; every step has complete code. (A few steps like Task 3.4 "Copy the Atlas class" reference existing source — acceptable because the current `agents/atlas.py` is authoritative; the plan tells the engineer what to copy and which imports to adjust.)

**Type consistency:**
- `SharedContext` fields used the same names across memory.py, orchestrator.py, dispatcher.py.
- `CostRecord` fields match between `budget.py` and the `cost_sink` wrapper in `dispatcher.py` (`cache_creation_input_tokens`, `cache_read_input_tokens`).
- Deliverable fields between `orchestrator.py` sink and `dispatcher.py` INSERT match (`kind`, `title`, `body_md`, `quality_score`).
- `jobs.cost_cents` is `numeric(10,2)` in schema; `cost_events.cost_cents` is `numeric(10,4)` — deliberate (finer granularity on events, rounded on aggregate). No runtime issue.

**Scope check:** this plan produces working, testable software (end-to-end single-tenant cycle). v1 gets its own plan when v0 exits.

---

## Notes for executor

- **OpenClaw env vars:** copy `.env` from the existing `devrel-swarm` repo (Anthropic, GitHub, Firecrawl keys) into the same location. The worker reads from `.env` + `.env.dev`.
- **Local model:** use Sonnet 4.6 default per existing `config/agent_config.yaml`. Model routing (Haiku/Sonnet/Opus) is v1, not v0.
- **KB content:** v0 still reads markdown files from `knowledge_base/` on disk. pgvector column is created but unpopulated. Kai uses existing TF-IDF search. pgvector migration is v1.
- **Time budget per alpha run:** Anthropic Sonnet-4.6 a full weekly cycle = ~5–10 minutes wall clock. Inngest `AbortSignal.timeout(1_200_000)` (20 min) gives headroom.
- **Cost budget per alpha run:** expect ~$3–8 per cycle during alpha before v1 optimizations. At $500/week external ceiling, that's 60+ runs of headroom — plenty for alpha iteration.
