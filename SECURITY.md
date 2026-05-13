# Security

`devrel-origin` is a CLI that runs on your machine and reads/writes your repo.
This document covers what it touches, what it doesn't, and how to report issues.

## Data handling

- **Local-only by default.** All state lives in the `.devrel/` directory inside
  the project you ran `devrel init` against — `config.toml`, `voice.md`,
  `style.md`, `slop-blocklist.md`, `kb/`, `deliverables/`, `state.db`. There is
  no central server. The CLI does not phone home.
- **No telemetry.** No analytics, no error reporting, no usage pings to any
  service operated by the maintainer. Outbound traffic only goes to providers
  you have configured (Anthropic, GitHub, PostHog, Instantly, Apollo, Telegram,
  Resend, Google Sheets).
- **API keys** are read from environment variables (see `config/env.example`).
  They are not persisted to `.devrel/state.db`. Do not commit `.env` files.
- **State DB.** `.devrel/state.db` is a SQLite database holding job history,
  cost-tracking events, content pieces, metric history, and Argus
  recommendations. It contains your inputs and the LLM outputs derived from
  them. Treat it like any other project artifact — it is yours to keep, share,
  or delete.
- **Deletion.** `rm -rf .devrel/` removes everything `devrel-origin` ever wrote
  for that project. There is nothing to unsubscribe from.

## Third-party providers

When you use the CLI, prompts and content are sent to whichever providers you
have keys configured for. Each provider has its own data-handling policy:

| Provider   | What gets sent                                       |
|------------|------------------------------------------------------|
| Anthropic  | All agent prompts and editorial pipeline traffic     |
| OpenAI     | TTS narration scripts (Vox only, optional)           |
| GitHub     | Issue/PR queries via your PAT                        |
| PostHog    | Read-only analytics queries                          |
| Instantly  | Lead lists + email sequences                         |
| Apollo.io  | Company/contact enrichment queries                   |
| Firecrawl  | Web search queries                                   |
| Brave      | Web search queries (fallback)                        |
| Telegram   | Digest messages to your bot                          |
| Google     | Sheets reads/writes against your spreadsheet         |

Anthropic and OpenAI are queried under standard API terms. If you require Zero
Data Retention, configure your account accordingly with each provider — the
CLI does not change their defaults.

## Reporting a vulnerability

If you find a security issue, **please do not open a public issue**. Email
[dovzhikova@gmail.com](mailto:dovzhikova@gmail.com) with:

- a description of the issue,
- the steps to reproduce,
- the version (`devrel --version`) and platform you observed it on,
- any proof-of-concept code you've put together.

You should expect a first response within 5 business days. If the issue is
confirmed, a fix will land in a patch release and you'll be credited in the
CHANGELOG (unless you'd rather stay anonymous).

## Scope

The maintainer treats the following as in-scope for security reports:

- Code execution from untrusted inputs (LLM outputs, scraped content, KB files)
- Path traversal via project paths or content IDs
- SQL injection in `.devrel/state.db` queries
- Secret leakage to logs, telemetry, or third-party providers beyond the ones
  the user explicitly configured
- Subprocess invocations that bypass `shutil.which` validation

Out of scope:

- Bugs that require physical access to the user's machine
- Issues in third-party providers (report to them directly)
- Theoretical issues without a working repro
