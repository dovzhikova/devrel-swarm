# Instantly AI Integration — Design Spec

## Goal

Integrate Instantly AI's cold email platform into the multi-agent system so that Pax (sales) and Mox (campaigns) can create campaigns, upload leads, send outreach emails, pull analytics, and triage replies — with human-in-the-loop approval for all follow-ups.

## Architecture

Thin async API client in `tools/instantly_client.py` following the same pattern as `PostHogClient` and `GitHubTools`. Pax and Mox call the client directly. Atlas orchestrates a reply triage loop as Stage 7 of the weekly cycle. Humans approve follow-ups via a new `--review-replies` CLI command.

## Tech Stack

- `httpx.AsyncClient` with Bearer token auth (Instantly API v2)
- `respx` for test mocking
- Dataclasses for DTOs
- Existing `agents/llm.py` for reply classification and follow-up drafting

---

## 1. Instantly API Client (`tools/instantly_client.py`)

### Authentication

Instantly API v2 uses Bearer token auth. The API key is passed via `Authorization: Bearer <key>` header on every request.

### Base URL

`https://api.instantly.ai` (configurable via `agent_config.yaml`).

### Rate Limits

Emails endpoint is rate-limited to 20 req/min. Other endpoints have higher limits. The client uses `tenacity` retry on 429 responses with exponential backoff (same approach as the existing LLM clients). No proactive rate limiter — reactive retry is sufficient and consistent with the rest of the codebase.

### DTOs

```python
@dataclass
class InstantlyLead:
    email: str
    first_name: str = ""
    last_name: str = ""
    company_name: str = ""
    title: str = ""
    custom_variables: dict[str, str] = field(default_factory=dict)
    # custom_variables map to personalization tags in email templates

@dataclass
class InstantlyCampaign:
    id: str
    name: str
    status: str  # "draft", "active", "paused", "completed"
    accounts: list[str]  # sending account emails
    sequences: list[dict]  # email steps

@dataclass
class InstantlyEmail:
    id: str
    campaign_id: str
    lead_email: str
    subject: str
    body: str
    is_reply: bool
    timestamp: str
    thread_id: str | None = None

@dataclass
class CampaignAnalytics:
    campaign_id: str
    campaign_name: str
    total_leads: int
    emails_sent: int
    emails_opened: int
    emails_replied: int
    emails_bounced: int
    open_rate: float
    reply_rate: float
    bounce_rate: float
```

### Methods

| Method | HTTP | Instantly Endpoint | Description |
|---|---|---|---|
| `create_campaign(name, sequences, accounts)` | `POST /api/v2/campaigns` | Create campaign with email steps |
| `get_campaign(campaign_id)` | `GET /api/v2/campaigns/:id` | Get campaign details |
| `list_campaigns(limit, skip)` | `GET /api/v2/campaigns` | List campaigns |
| `activate_campaign(campaign_id)` | `POST /api/v2/campaigns/:id/activate` | Launch campaign |
| `stop_campaign(campaign_id)` | `POST /api/v2/campaigns/:id/stop` | Pause campaign |
| `get_campaign_analytics(campaign_id)` | `GET /api/v2/campaigns/:id/analytics/overview` | Open/reply/bounce stats |
| `create_lead(email, ...)` | `POST /api/v2/leads` | Add single lead |
| `add_leads_bulk(campaign_id, leads)` | `POST /api/v2/leads/bulk-add` | Batch add up to 1000 leads |
| `list_leads(campaign_id)` | `POST /api/v2/leads/list` | List leads in campaign |
| `update_lead_interest(lead_id, status)` | `PATCH /api/v2/leads/:id/interest-status` | Update lead status |
| `list_emails(campaign_id, is_reply)` | `GET /api/v2/emails` | Fetch emails, filter for replies |
| `reply_to_email(email_id, campaign_id, body, thread_id)` | `POST /api/v2/emails/reply` | Send approved follow-up. Requires `email_id` and `campaign_id`; `thread_id` is passed if available from the original `InstantlyEmail`. |
| `create_lead_list(name)` | `POST /api/v2/lead-lists` | Create named lead list |

> **Note:** `create_webhook()` is deferred to a future iteration when we move from polling to real-time reply notifications. See Section 7.

### Error Handling

- Retry on 429 (rate limit) with exponential backoff using `tenacity`
- Retry on 5xx with 3 attempts
- Raise `InstantlyAPIError` on 4xx (non-retryable)
- Log all requests with structured logging (`extra={}`)

### Client Pattern

```python
class InstantlyClient:
    def __init__(self, api_key: str, base_url: str = "https://api.instantly.ai"):
        self._client = httpx.AsyncClient(
            base_url=base_url,
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=30.0,
        )

    async def _request(self, method, path, **kwargs) -> dict:
        # Retry logic, error handling, structured logging
        ...

    async def close(self):
        await self._client.aclose()
```

---

## 2. Pax Integration

### New method: `upload_leads()`

Accepts leads from three sources:

1. **CSV file path** — Reads CSV, maps columns to `InstantlyLead` fields
2. **List of dicts** — Direct programmatic input
3. **Upstream context** — Extracts GitHub contributors from `sage_triage` (users who filed issues = potential leads if they have public emails)

Batches into groups of 1000 and calls `add_leads_bulk()`.

```python
async def upload_leads(
    self,
    campaign_id: str,
    leads: list[dict] | None = None,
    csv_path: Path | None = None,
    context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    # Parse leads from whichever source is provided
    # Batch into groups of 1000
    # Call instantly_client.add_leads_bulk() for each batch
    # Return summary: total uploaded, failed, skipped
```

### New method: `draft_followups()`

Takes triaged replies and generates personalized follow-ups via LLM:

```python
async def draft_followups(
    self,
    replies: list[dict],
    context: dict[str, Any] | None = None,
) -> list[dict]:
    # For each reply categorized as "interested" or "objection":
    #   - Build prompt with original email, reply text, KB context
    #   - LLM generates follow-up draft
    #   - Return list of {reply_id, email_id, draft_subject, draft_body, category}
```

### Modified `execute()`

New asset type keyword mapping:

```python
ASSET_KEYWORDS = {
    ...existing...,
    "instantly_campaign": ["instantly", "cold email", "outreach campaign"],
    "lead_upload": ["upload leads", "import leads", "add leads"],
    "triage_replies": ["triage", "replies", "follow-up"],
}
```

When `asset_type == "instantly_campaign"`:
1. Generate email sequence via LLM (existing outreach/nurture flow)
2. Create campaign in Instantly
3. Return campaign ID and status

When `asset_type == "lead_upload"`:
1. Call `upload_leads()` with provided CSV path or lead data
2. Return upload summary

When `asset_type == "triage_replies"`:
1. Fetch replies via `self.instantly_client.list_emails(is_reply=True)`
2. Classify each reply via LLM into: `interested`, `objection`, `not_now`, `unsubscribe`, `auto_reply`
3. Draft follow-ups for `interested` and `objection` replies via `draft_followups()`
4. Return triaged replies with drafts

### Constructor change

```python
def __init__(
    self,
    api_client: PostHogClient,
    knowledge_base_path: Path,
    llm_client: Optional[LLMClient] = None,
    instantly_client: Optional[InstantlyClient] = None,  # NEW
    product_name: str = "the target product",
):
```

---

## 3. Mox Integration

### New method: `push_campaign()`

Takes generated campaign content and pushes it to Instantly:

```python
async def push_campaign(
    self,
    campaign_name: str,
    email_sequences: list[dict],  # [{subject, body, delay_days}]
    accounts: list[str] | None = None,
) -> dict[str, Any]:
    # Create campaign via instantly_client
    # Return campaign ID and details
```

### New method: `pull_campaign_stats()`

Fetches analytics for all active campaigns:

```python
async def pull_campaign_stats(
    self,
    campaign_ids: list[str] | None = None,
) -> dict[str, Any]:
    # If no IDs provided, list all campaigns and get stats for active ones
    # Aggregate: sum counts (sent, opened, replied, bounced),
    #            average rates (open_rate, reply_rate, bounce_rate)
    # Return shape:
    # {
    #     "total_campaigns": int,
    #     "total_sent": int,
    #     "total_opened": int,
    #     "total_replied": int,
    #     "total_bounced": int,
    #     "avg_open_rate": float,
    #     "avg_reply_rate": float,
    #     "avg_bounce_rate": float,
    #     "per_campaign": [CampaignAnalytics.to_dict(), ...],
    # }
```

### Modified `execute()`

New content type:

```python
CONTENT_KEYWORDS = {
    ...existing...,
    "email_campaign": ["email campaign", "cold email", "drip campaign"],
}
```

When `content_type == "email_campaign"`:
1. Generate email sequence content via LLM
2. Push to Instantly via `push_campaign()`
3. Return campaign details

### Constructor change

```python
def __init__(
    self,
    ...existing...,
    instantly_client: Optional[InstantlyClient] = None,  # NEW
):
```

---

## 4. Atlas Weekly Cycle — Stage 7

### New SharedContext fields

```python
@dataclass
class SharedContext:
    ...existing fields...
    instantly_campaigns: dict[str, Any] = field(default_factory=dict)
    instantly_analytics: dict[str, Any] = field(default_factory=dict)
    instantly_replies: dict[str, Any] = field(default_factory=dict)
```

**Important:** `SharedContext.to_dict()` is manually maintained — all three new fields must be added to it. Also add a `SharedContext.load(archive_dir)` classmethod that reads the most recent `context_*.json` file from the archive directory.

### Stage 7: Instantly Sync (after Vox)

All Instantly interactions go through agent delegation (not direct client calls from Atlas) to preserve retry logic, structured logging, and `DelegationResult` tracking.

```python
# Stage 7: Instantly analytics + reply triage
if self.instantly_client:
    # 7a: Mox pulls campaign analytics
    analytics_result = await self.delegate(
        "mox",
        "Pull campaign analytics from Instantly for all active campaigns.",
    )
    if analytics_result.success:
        self.context.instantly_analytics = analytics_result.output

    # 7b: Pax fetches and triages replies
    triage_result = await self.delegate(
        "pax",
        "Fetch new email replies from Instantly, triage them, and draft follow-ups for interested leads.",
    )
    if triage_result.success:
        self.context.instantly_replies = triage_result.output
```

Pax's `execute()` handles reply fetching internally via `self.instantly_client.list_emails()` when the task matches the `"triage_replies"` asset type. This keeps Atlas as a pure orchestrator.

### OKR compilation additions

```python
def _compile_okrs(self) -> dict[str, Any]:
    return {
        ...existing...,
        "emails_sent": self.context.instantly_analytics.get("total_sent", 0),
        "emails_opened": self.context.instantly_analytics.get("total_opened", 0),
        "emails_replied": self.context.instantly_analytics.get("total_replied", 0),
        "reply_rate": self.context.instantly_analytics.get("avg_reply_rate", 0),
        "followups_pending": len(
            self.context.instantly_replies.get("drafts", [])
        ),
    }
```

### New CLI command: `--review-replies`

```python
parser.add_argument(
    "--review-replies",
    action="store_true",
    help="Review and approve pending follow-up email drafts",
)
```

Interactive flow:
1. Load latest context via `SharedContext.load(archive_dir)` (new classmethod that reads the most recent `context_*.json` file). If no archive exists, re-fetch replies directly from Instantly via the client.
2. For each draft with `status == "pending_approval"`:
   - Display: original email, reply, drafted follow-up
   - Prompt: `[a]pprove / [e]dit / [s]kip / [r]eject`
   - If approved: call `reply_to_email()` and mark sent
   - If edited: open in `$EDITOR`, then send
   - If skipped: leave as pending
   - If rejected: discard

---

## 5. Configuration

### Environment variables

```bash
# .env.example addition
INSTANTLY_API_KEY=your_instantly_api_key_here
```

### agent_config.yaml addition

```yaml
instantly:
  base_url: "https://api.instantly.ai"
  rate_limit_rpm: 50
  bulk_batch_size: 1000
  reply_check_enabled: true
```

### Atlas constructor wiring

```python
instantly_client = (
    InstantlyClient(api_key=os.environ.get("INSTANTLY_API_KEY", ""))
    if os.environ.get("INSTANTLY_API_KEY")
    else None
)

atlas = Atlas(
    ...existing...,
    instantly_client=instantly_client,
)
```

Atlas passes `instantly_client` to Pax and Mox during initialization.

**Lifecycle:** `InstantlyClient.close()` must be called in Atlas's `main()` finally block alongside the other client cleanup calls.

---

## 6. Files Created / Modified

| Action | File | Changes |
|---|---|---|
| **Create** | `tools/instantly_client.py` | Full async client with 14 methods, DTOs, error handling |
| **Modify** | `agents/pax.py` | Add `instantly_client` param, `upload_leads()`, `draft_followups()`, new asset types |
| **Modify** | `agents/mox.py` | Add `instantly_client` param, `push_campaign()`, `pull_campaign_stats()`, new content type |
| **Modify** | `agents/atlas.py` | Stage 7, new SharedContext fields, `--review-replies` CLI, pass client to Pax/Mox |
| **Modify** | `agents/types.py` | Add TypedDicts (see below) |
| **Modify** | `config/env.example` | Add `INSTANTLY_API_KEY` |
| **Modify** | `config/agent_config.yaml` | Add `instantly:` section |
| **Create** | `tests/test_instantly_client.py` | Unit tests for all 14 client methods (respx mocks) |
| **Create** | `tests/test_pax_instantly.py` | Test lead upload (CSV + dict + context), follow-up drafting |
| **Create** | `tests/test_mox_instantly.py` | Test campaign push, analytics pull |
| **Create** | `tests/test_atlas_replies.py` | Test Stage 7 triage loop, review-replies flow |

### New TypedDicts for `agents/types.py`

```python
class InstantlyAnalyticsResult(TypedDict):
    agent: str
    status: str
    total_campaigns: int
    total_sent: int
    total_opened: int
    total_replied: int
    total_bounced: int
    avg_open_rate: float
    avg_reply_rate: float
    avg_bounce_rate: float
    per_campaign: list[dict]

class InstantlyRepliesResult(TypedDict):
    agent: str
    status: str
    total_replies: int
    categories: dict  # {"interested": int, "objection": int, ...}
    drafts: list[dict]  # [{reply_id, email_id, draft_subject, draft_body, category, status}]
```

---

## 7. Out of Scope (future)

- Webhook-based real-time reply notifications (polling is fine for MVP)
- Automatic A/B testing of email variants
- CRM sync (Salesforce/HubSpot → Instantly lead push)
- Unsubscribe handling automation
- Multi-workspace Instantly support
