"""8-stage editorial quality pipeline for content-producing agents.

Public entry point is `run_pipeline` in `editorial.py`. Agents (Kai, Mox,
Pax) replace their single `generate_with_revision` call with one call to
`run_pipeline`. Output includes the final text plus a revision trace
spanning every stage.
"""
