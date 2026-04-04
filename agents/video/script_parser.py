"""
ScriptParser — Converts markdown scripts and task strings into
structured TutorialStep sequences for video generation.
"""

import logging
import re
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)

# --- Regex patterns ---
STEP_HEADING_RE = re.compile(r"^##\s+(?:Step\s+\d+[:\s]*)?(.+)$", re.MULTILINE)
CODE_BLOCK_RE = re.compile(r"```[\s\S]*?```")

# Headings to skip (lowercased for comparison)
SKIP_HEADINGS = {
    "prerequisites",
    "introduction",
    "overview",
    "conclusion",
    "summary",
    "next steps",
}


@dataclass
class TutorialStep:
    """A single step in a video tutorial."""

    step_number: int
    title: str
    narration: str
    url: str
    actions: list[dict] = field(default_factory=list)
    overlay_text: str = ""
    duration_hint: float = 5.0


@dataclass
class VideoTutorial:
    """A complete video tutorial definition."""

    title: str
    steps: list[TutorialStep]
    output_path: str
    source: str
    resolution: tuple[int, int] = (1920, 1080)
    total_duration: float = 0.0


class ScriptParser:
    """Parses markdown scripts and task strings into TutorialStep sequences."""

    def parse_markdown(
        self, markdown: str, base_url: str = "https://example.com"
    ) -> list[TutorialStep]:
        """Split markdown by ## headings into TutorialSteps.

        Skips prerequisite/conclusion sections. Extracts narration
        (stripped of code blocks) and overlay text from first code block.
        """
        sections = self._split_by_headings(markdown)
        steps: list[TutorialStep] = []
        step_number = 1

        for title, body in sections:
            # Skip non-content sections
            if title.strip().lower() in SKIP_HEADINGS:
                continue

            narration = self._extract_narration(body)
            if not narration.strip():
                continue

            overlay = self._extract_first_code(body)
            duration = max(5.0, len(narration) / 15)

            steps.append(
                TutorialStep(
                    step_number=step_number,
                    title=title.strip(),
                    narration=narration,
                    url=base_url,
                    overlay_text=overlay,
                    duration_hint=round(duration, 1),
                )
            )
            step_number += 1

        return steps

    def parse_task(
        self, task: str, base_url: str = "https://example.com"
    ) -> list[TutorialStep]:
        """Create a single TutorialStep from a task string."""
        narration = self._extract_narration(task)
        duration = max(5.0, len(narration) / 15)

        return [
            TutorialStep(
                step_number=1,
                title=task[:80].strip(),
                narration=narration,
                url=base_url,
                duration_hint=round(duration, 1),
            )
        ]

    def _split_by_headings(self, markdown: str) -> list[tuple[str, str]]:
        """Split markdown into (heading, body) tuples by ## headings."""
        matches = list(STEP_HEADING_RE.finditer(markdown))
        if not matches:
            return []

        sections: list[tuple[str, str]] = []
        for i, match in enumerate(matches):
            title = match.group(1)
            start = match.end()
            end = matches[i + 1].start() if i + 1 < len(matches) else len(markdown)
            body = markdown[start:end]
            sections.append((title, body))

        return sections

    def _extract_narration(self, text: str) -> str:
        """Remove code blocks, markdown links, bold/italic, collapse whitespace."""
        # Remove code blocks
        cleaned = CODE_BLOCK_RE.sub("", text)
        # Remove markdown links: [text](url) → text
        cleaned = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", cleaned)
        # Remove bold and italic markers
        cleaned = re.sub(r"\*{1,2}([^*]+)\*{1,2}", r"\1", cleaned)
        cleaned = re.sub(r"_{1,2}([^_]+)_{1,2}", r"\1", cleaned)
        # Collapse whitespace
        cleaned = re.sub(r"\s+", " ", cleaned).strip()
        return cleaned

    def _extract_first_code(self, text: str) -> str:
        """Extract content of the first code block, truncated to 5 lines."""
        match = re.search(r"```(?:\w*\n)?([\s\S]*?)```", text)
        if not match:
            return ""
        code = match.group(1).strip()
        lines = code.splitlines()
        if len(lines) > 5:
            lines = lines[:5]
            lines.append("...")
        return "\n".join(lines)
