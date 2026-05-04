"""8-stage editorial quality pipeline for content-producing agents.

Public entry point is `run_pipeline` in `editorial.py`. Agents (Kai, Mox,
Pax) replace their single `generate_with_revision` call with one call to
`generate_with_pipeline` (this module), which dispatches to the editorial
pipeline when a `.devrel/` project is available and falls back to the
legacy revision loop otherwise. Output includes the final text plus the
strengths and issues summary the calling agent stores on its result.
"""

from __future__ import annotations


async def generate_with_pipeline(
    *,
    llm_client,
    system_prompt: str,
    user_prompt: str,
    content_type: str,
    logger,
) -> tuple[str, list[str], list[str]]:
    """Generate content via the editorial pipeline, falling back to the
    legacy revision loop when there is no .devrel/ project or the pipeline
    aborts on unrecoverable slop. Returns (final_text, strengths, issues).
    The fallback path is logged via the provided logger so the calling
    agent's logs surface why the pipeline didn't run."""
    # Imports are kept inside the function to avoid circular-import risk at
    # module load (quality is imported by editorial; editorial imports
    # project.paths which is fine, but we also want this helper to remain
    # cheap to import from agents).
    from devrel_swarm.project.paths import (
        ProjectNotFoundError,
        ProjectPaths,
        find_devrel_root,
    )
    from devrel_swarm.quality.editorial import AbortLoud, run_pipeline

    draft, _ = await llm_client.generate(
        system_prompt=system_prompt,
        user_prompt=user_prompt,
    )
    try:
        paths = ProjectPaths.from_root(find_devrel_root())
        result = await run_pipeline(
            initial_draft=draft,
            content_type=content_type,
            project_paths=paths,
            llm_client=llm_client,
        )
        strengths = [result.stages[-1].detail] if result.stages else []
        issues = [i for s in result.stages for i in s.issues]
        return result.final_text, strengths, issues
    except (ProjectNotFoundError, AbortLoud) as e:
        logger.warning("editorial pipeline unavailable, using single-revision: %s", e)
        content, trace = await llm_client.generate_with_revision(
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            min_score=7,
            max_rounds=2,
        )
        strengths = trace.critiques[-1].strengths if trace.critiques else []
        issues = trace.critiques[-1].issues if trace.critiques else []
        return content, strengths, issues


__all__ = ["generate_with_pipeline"]
