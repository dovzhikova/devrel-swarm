"""Anti-slop pipeline stage 5.

Three-step matching:
1. Regex blocklist (deterministic, fast). Word-boundary, case-insensitive.
2. LLM lint (Haiku). Catches context-sensitive slop the regex misses
   (verbose intros, vague intensifiers in unusual phrasings).
3. Force-rewrite (Sonnet). One targeted rewrite call with all hits listed.
   If the rewrite still trips the blocklist on re-check, the orchestrator
   aborts loud, see editorial.py.

Haiku's lint output occasionally hallucinates phrases that don't appear in
the source (verbatim from a real abort: 'replace this blockquote', 'replace
with' in a draft that contained neither). Hallucinated phrases would then go
into force_rewrite's flagged list and re-appear on the post-rewrite re-check,
falsely tripping the abort-loud condition. _verify_lint_hits filters out any
phrase that does not actually appear in the text (case-insensitive substring),
so only real slop survives into the rewrite + abort loop.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class SlopHit:
    phrase: str
    start: int
    end: int


def parse_blocklist(md: str) -> list[str]:
    """Parse `slop-blocklist.md`. Returns lowercased phrases, one per
    non-comment, non-blank line. Lines starting with `#` are comments."""
    out: list[str] = []
    for raw in md.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        out.append(line.lower())
    return out


def find_slop(text: str, blocklist: list[str]) -> list[SlopHit]:
    """Word-boundary, case-insensitive regex match. Returns one hit per
    occurrence, in order."""
    hits: list[SlopHit] = []
    text_lower = text.lower()
    for phrase in blocklist:
        pattern = r"\b" + re.escape(phrase) + r"\b"
        for m in re.finditer(pattern, text_lower):
            hits.append(SlopHit(phrase=phrase, start=m.start(), end=m.end()))
    hits.sort(key=lambda h: h.start)
    return hits


_LINT_SYSTEM = (
    "You are an editor screening AI-written content for tells the regex "
    "blocklist would miss: verbose intros, vague intensifiers in unusual "
    "phrasings, hedging that doesn't appear in the blocklist verbatim. "
    "Return a flat list, one phrase per line, lowercase, no bullets, no "
    "explanations. If nothing concerning, return an empty response."
)


def _normalize_lint_lines(raw: str) -> list[str]:
    out: list[str] = []
    for line in raw.splitlines():
        s = line.strip()
        if not s or s.startswith("#"):
            continue
        # Strip leading bullets / ordinals.
        s = re.sub(r"^[\-\*•\d\.\)]+\s*", "", s)
        if s:
            out.append(s.lower())
    return out


def _verify_lint_hits(text: str, candidates: list[str]) -> list[str]:
    """Drop hallucinated lint hits (phrases Haiku flagged that don't appear).

    Case-insensitive substring match. We deliberately don't require word
    boundaries because Haiku sometimes returns slightly truncated phrases
    (e.g. 'in essence' for the substring 'in essence,'); a substring match
    accepts those while still rejecting fully-fabricated phrases. Logs the
    filtered set at INFO so we can monitor Haiku's hallucination rate without
    polluting user-facing output.
    """
    text_lower = text.lower()
    real: list[str] = []
    hallucinated: list[str] = []
    for phrase in candidates:
        if phrase and phrase in text_lower:
            real.append(phrase)
        else:
            hallucinated.append(phrase)
    if hallucinated:
        logger.info(
            "slop_lint_filtered_hallucinations",
            extra={
                "filtered_count": len(hallucinated),
                "kept_count": len(real),
                "filtered": hallucinated[:10],  # cap log size
            },
        )
    return real


async def llm_lint(text: str, voice: str, llm_client) -> list[str]:
    """Haiku-powered second-pass slop detector.

    Verifies each Haiku-flagged phrase actually appears in the source so
    hallucinated flags don't propagate into force_rewrite or trigger the
    orchestrator's post-rewrite abort-loud check.
    """
    user = (
        "Voice contract for this product:\n\n" + (voice or "(none)") + "\n\n"
        "Content to screen:\n\n" + text + "\n\n"
        "List the phrases that read as AI-written, one per line. Empty if clean. "
        "Only list phrases that appear verbatim in the content above, exactly as "
        "they appear there; do not paraphrase, abbreviate, or invent phrases."
    )
    raw = await llm_client.generate(
        system_prompt=_LINT_SYSTEM,
        user_prompt=user,
        model="haiku",
    )
    return _verify_lint_hits(text, _normalize_lint_lines(raw))


_REWRITE_SYSTEM = (
    "You are a rewrite editor. The reader has flagged specific phrases as "
    "AI-written. Rewrite the content so none of the flagged phrases (or "
    "their close synonyms) appear, while preserving meaning, structure, "
    "and the project's voice. Return only the rewritten content — no "
    "preamble, no explanation."
)


async def force_rewrite(
    text: str,
    regex_hits: list[SlopHit],
    llm_lint_hits: list[str],
    voice: str,
    llm_client,
) -> str:
    """Single Sonnet rewrite with the full flagged list. Caller is
    responsible for re-running `find_slop` + `llm_lint` to verify the
    rewrite cleared the issues."""
    flagged = sorted({h.phrase for h in regex_hits} | set(llm_lint_hits))
    flagged_listing = "\n".join(f"- {p}" for p in flagged)
    user = (
        "Voice contract:\n\n" + (voice or "(none)") + "\n\n"
        "Flagged phrases (do not let any of these appear in the rewrite, "
        "and avoid close synonyms):\n\n" + flagged_listing + "\n\n"
        "Original content:\n\n" + text
    )
    rewritten = await llm_client.generate(
        system_prompt=_REWRITE_SYSTEM,
        user_prompt=user,
        model="sonnet",
    )
    return rewritten.strip()
