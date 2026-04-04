# Contributing to OpenClaw

OpenClaw is an open-source project (MIT license) hosted at github.com/openclaw/openclaw. Contributions are welcome, with clear guidelines to maintain quality and focus.

## Development Setup

```bash
git clone https://github.com/openclaw/openclaw.git
cd openclaw
pnpm install
pnpm build
pnpm openclaw onboard   # or: pnpm dev
```

Runtime requirements: Node 22+ (Node 24 recommended), pnpm for builds from source.

## Code Style

- **Language**: TypeScript (ESM) with strict typing. Avoid `any`.
- **Formatting**: oxfmt (`pnpm format` to check, `pnpm format:fix` to fix).
- **Linting**: oxlint with type-aware rules (`pnpm lint`).
- **Pre-commit**: `pnpm check` runs format, typecheck, and lint.
- **Written English**: American spelling and grammar in all code, comments, docs, and UI strings.
- **File size**: aim for under 500 LOC; split/refactor when it improves clarity.
- **Comments**: add brief comments for tricky or non-obvious logic.

## Testing

- Framework: Vitest with V8 coverage thresholds (70% lines/branches/functions/statements).
- Test files: colocated `*.test.ts` matching source names.
- Run tests: `pnpm test` (or `pnpm test:coverage` for coverage).
- Live tests (require real API keys): `OPENCLAW_LIVE_TEST=1 pnpm test:live`.
- Docker E2E: `pnpm test:docker:all`.

## PR Guidelines

- **One PR = one issue/topic.** Do not bundle unrelated changes.
- PRs over 5,000 changed lines are reviewed only in exceptional circumstances.
- Do not open large batches of tiny PRs at once.
- Small related fixes can be grouped into one focused PR.
- Bug-fix PRs require evidence: symptom reproduction, verified root cause with file/line, regression test when feasible.
- Pure test additions generally do not need a changelog entry.

## What Will Not Be Merged (For Now)

- New core skills when they can live on ClawHub
- Full documentation translations (planned for AI-generated translations later)
- Commercial service integrations outside model-provider category
- Wrapper channels around already-supported channels
- First-class MCP runtime in core (mcporter provides the integration path)
- Agent-hierarchy frameworks or heavy orchestration layers

## Plugin Contributions

The preferred path for new capabilities is the plugin system:

- Build and maintain plugins in your own repository
- Distribute via npm
- List on the community plugins page
- Core plugin additions require a strong product or security justification

## Project Structure

- `src/` -- core source (CLI, commands, gateway, media pipeline)
- `extensions/` -- workspace packages for channel/feature plugins
- `docs/` -- documentation (Mintlify-hosted at docs.openclaw.ai)
- `apps/` -- native companion apps (macOS, iOS, Android)
- `skills/` -- bundled skills
- `test/` -- additional test infrastructure
- `scripts/` -- build, release, and CI scripts

## Community

- **Discord**: discord.gg/clawd
- **GitHub Issues**: github.com/openclaw/openclaw/issues
- **Docs**: docs.openclaw.ai
- **DeepWiki**: deepwiki.com/openclaw/openclaw
