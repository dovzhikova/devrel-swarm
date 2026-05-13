"""Stage 6 of the editorial pipeline.

Single Haiku call against a fixed persona — "skeptical senior backend
developer" — that scores the draft 1-10 on resonance and flags weak
sections with quoted excerpts. The orchestrator uses the score as a soft
gate: if it falls below 7, control returns to copy-edit (stage 4) once
with the persona feedback attached as a critique.
"""

from __future__ import annotations

import json
from dataclasses import dataclass


@dataclass(frozen=True)
class PersonaResult:
    score: int
    weak_sections: list[str]
    feedback: str


_SYSTEM_PROMPT = """You are a skeptical senior backend developer with 10+ years of experience. You're allergic to marketing fluff, consultant-speak, and AI-style hedging. You've read enough developer-targeted content to instantly spot when a piece is generic, surface-level, or written for a project the author hasn't actually used.

Score the content on a 1-10 scale:
- 10 = This made me want to try the product immediately. Specific, concrete, technical.
- 7-9 = Solid. I'd send it to a teammate.
- 4-6 = Generic. Could be about any product. Skim-level.
- 1-3 = Pure marketing. I'd close the tab.

Identify up to 3 weak sections — quote them verbatim or paraphrase tightly. Be honest; don't pad.

Return strict JSON:
{
  "score": 1-10,
  "weak_sections": ["…", "…"],
  "feedback": "1-2 sentences on what's working and what isn't"
}
"""


def _coerce_result(raw: str) -> PersonaResult:
    try:
        data = json.loads(raw.strip())
    except json.JSONDecodeError:
        return PersonaResult(
            score=5,
            weak_sections=[],
            feedback="Could not parse persona response as JSON.",
        )
    score = int(data.get("score", 5))
    score = max(1, min(10, score))
    weak = list(data.get("weak_sections", []) or [])
    feedback = str(data.get("feedback", ""))
    return PersonaResult(score=score, weak_sections=weak, feedback=feedback)


async def test_against_persona(
    *,
    text: str,
    content_type: str,
    voice: str,
    llm_client,
) -> PersonaResult:
    """Single Haiku call. Returns a structured score + weak-sections + feedback."""
    user = (
        f"Content type: {content_type}\n\n"
        "Voice contract for this product:\n\n"
        + (voice or "(no voice profile yet)")
        + "\n\nContent to evaluate:\n\n"
        + text
    )
    raw = await llm_client.generate(
        system_prompt=_SYSTEM_PROMPT,
        user_prompt=user,
        model="haiku",
    )
    return _coerce_result(raw)


# Tell pytest not to collect the function as a test when it's imported
# into a test_ module. The `test_` prefix here means "evaluate against a
# persona," not a unit test.
test_against_persona.__test__ = False  # type: ignore[attr-defined]
