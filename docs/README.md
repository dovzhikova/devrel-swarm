# `devrel-origin` documentation

User-facing docs for `devrel-origin`. Internal architecture specs and implementation plans live in [`superpowers/`](superpowers/).

## Start here

- [**`quickstart.md`**](quickstart.md) — install, bootstrap, and run your first weekly cycle in 5 minutes.

## Reference

- [**`agents/argus.md`**](agents/argus.md) — the post-publish content performance analyst (the 13th agent, added in v0.2.4).
- [**`cli/analytics.md`**](cli/analytics.md) — full reference for the `devrel analytics` subgroup: `report`, `history`, `diff`, `calibration`, `summary`.

## Recipes

- [**`cookbook.md`**](cookbook.md) — common workflows: weekly cron, calibration loop, multi-project rollups, recovery, prompt overrides, budget caps.

## Top-level

The repo's [`README.md`](../README.md) covers the full CLI surface, the editorial pipeline architecture, and the 13-agent system layout. The [`CHANGELOG.md`](../CHANGELOG.md) tracks per-version changes.

## Contributing

If you change agent behavior, update both the relevant `docs/agents/*.md` reference and the `CHANGELOG.md`. New CLI verbs need an entry in `docs/cli/*.md`. The cookbook is intentionally short — recipes only land here when at least one user (you, by default) has actually used them and confirmed they work.
