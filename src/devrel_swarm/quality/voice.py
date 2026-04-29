"""Load voice.md as a single string for prompt injection."""

from __future__ import annotations

from devrel_swarm.project.paths import ProjectPaths


def load_voice(paths: ProjectPaths) -> str:
    """Return the full text of `.devrel/voice.md`, or "" if the file is
    missing. The orchestrator injects this verbatim into editorial-stage
    system prompts as the project's voice contract.
    """
    if not paths.voice_file.is_file():
        return ""
    return paths.voice_file.read_text(encoding="utf-8")
