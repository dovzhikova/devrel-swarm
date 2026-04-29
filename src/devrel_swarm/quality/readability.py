"""Pure-Python readability scoring: Flesch Reading Ease, sentence-length
statistics, jargon density. No LLM calls. Used as stage 7 of the
editorial pipeline.

Flesch Reading Ease formula:
  FRE = 206.835 - 1.015 * (words/sentences) - 84.6 * (syllables/words)

Higher FRE = easier to read. Style.md targets per content-type are
expressed in this scale (e.g., tutorial 50-65 = "fairly difficult").

Jargon density: fraction of content words >= 12 characters. Imperfect but
catches the obvious "academic-ese" drift; tuned alongside style.md targets.

Drift tolerance: a score that's within 10 points of the target range
(below min or above max) does not flag. The pipeline only reverts to copy
edit (stage 4) if drift exceeds 10 points OR sentence length exits the
target range entirely.
"""

from __future__ import annotations

import re
import statistics
from dataclasses import dataclass

from devrel_swarm.quality.style import ContentTypeTargets

DRIFT_TOLERANCE = 10  # Flesch points

_VOWELS = set("aeiouy")
_WORD_RE = re.compile(r"\b[a-zA-Z']+\b")
_SENTENCE_END = re.compile(r"[.!?]+(?=\s|$)")


def count_syllables(word: str) -> int:
    """Approximate syllable count.

    Algorithm:
    1. Count vowel groups (consecutive vowels = one group). y counts as
       a vowel.
    2. Silent terminal-e rule: if word ends in `e` AND syllables > 1,
       drop one.
    3. `[consonant]le` exception: words ending in consonant + "le"
       (simple, syllable, table) keep the final "le" syllable. The
       silent-e rule does not fire for these.
    """
    if not word:
        return 0
    word = word.lower()
    syllables = 0
    prev_vowel = False
    for ch in word:
        is_v = ch in _VOWELS
        if is_v and not prev_vowel:
            syllables += 1
        prev_vowel = is_v
    # Terminal silent-e, but preserve `[consonant]le` endings.
    if word.endswith("e") and syllables > 1:
        ends_in_consonant_le = (
            len(word) >= 3
            and word[-2:] == "le"
            and word[-3] not in _VOWELS
        )
        if not ends_in_consonant_le:
            syllables -= 1
    return max(1, syllables)


@dataclass(frozen=True)
class ReadabilityScores:
    flesch_reading_ease: float
    mean_sentence_length: float
    sentence_length_variance: float
    jargon_density: float
    word_count: int
    sentence_count: int


def _split_sentences(text: str) -> list[str]:
    """Split on terminal punctuation. Filters empty fragments."""
    pieces = _SENTENCE_END.split(text)
    out = [p.strip() for p in pieces if p.strip()]
    return out


def _words_in(text: str) -> list[str]:
    return _WORD_RE.findall(text)


def compute_readability(text: str) -> ReadabilityScores:
    """Compute all readability metrics for `text`."""
    sentences = _split_sentences(text)
    sentence_count = len(sentences)
    words = _words_in(text)
    word_count = len(words)

    if word_count == 0 or sentence_count == 0:
        return ReadabilityScores(0.0, 0.0, 0.0, 0.0, word_count, sentence_count)

    syllable_total = sum(count_syllables(w) for w in words)
    flesch = (
        206.835
        - 1.015 * (word_count / sentence_count)
        - 84.6 * (syllable_total / word_count)
    )

    sentence_lengths = [len(_words_in(s)) for s in sentences]
    mean_sl = statistics.mean(sentence_lengths)
    var_sl = statistics.pvariance(sentence_lengths) if sentence_count > 1 else 0.0

    long_words = sum(1 for w in words if len(w) >= 12)
    jargon = long_words / word_count

    return ReadabilityScores(
        flesch_reading_ease=round(flesch, 2),
        mean_sentence_length=round(mean_sl, 2),
        sentence_length_variance=round(var_sl, 2),
        jargon_density=round(jargon, 4),
        word_count=word_count,
        sentence_count=sentence_count,
    )


def check_against_target(
    scores: ReadabilityScores,
    target: ContentTypeTargets,
) -> list[str]:
    """Return a list of human-readable issues, or empty if scores meet the
    target within DRIFT_TOLERANCE for Flesch and exactly for sentence length."""
    issues: list[str] = []

    if scores.flesch_reading_ease < target.flesch_min - DRIFT_TOLERANCE:
        issues.append(
            f"Flesch reading ease {scores.flesch_reading_ease} is "
            f"{target.flesch_min - scores.flesch_reading_ease:.1f} points below "
            f"min {target.flesch_min} (drift > {DRIFT_TOLERANCE})."
        )
    elif scores.flesch_reading_ease > target.flesch_max + DRIFT_TOLERANCE:
        issues.append(
            f"Flesch reading ease {scores.flesch_reading_ease} is "
            f"{scores.flesch_reading_ease - target.flesch_max:.1f} points above "
            f"max {target.flesch_max} (drift > {DRIFT_TOLERANCE})."
        )

    if scores.mean_sentence_length < target.sentence_len_min:
        issues.append(
            f"Mean sentence length {scores.mean_sentence_length} below "
            f"min {target.sentence_len_min}."
        )
    elif scores.mean_sentence_length > target.sentence_len_max:
        issues.append(
            f"Mean sentence length {scores.mean_sentence_length} above "
            f"max {target.sentence_len_max}."
        )

    return issues
