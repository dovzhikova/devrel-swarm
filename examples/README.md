# examples

Standalone scripts that exercise the package. Not part of the published wheel.

| File | Purpose |
|------|---------|
| `run_100_leads.py` | Apollo search → per-lead LLM personalization → Instantly bulk upload. Run with `python examples/run_100_leads.py`. |
| `run_sales_pipeline.py` | Full Rex (intel) → Pax (outreach) sales flow. Run with `python examples/run_sales_pipeline.py`. |

Each script expects the env vars from `config/env.example`. They were the original entry points before the `devrel` CLI existed; the equivalents now are `devrel intel <competitor>` and `devrel sales outreach <company>`.
