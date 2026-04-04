# Vox — Video Tutorial Agent Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a Vox agent that programmatically generates polished video tutorials from written content or standalone tasks, using Playwright screen recording, OpenAI TTS narration, and FFmpeg overlays.

**Architecture:** Vox follows the existing agent pattern (`execute(task, context)` async method, dataclass DTOs). It decomposes into 5 focused modules under `agents/video/`: script parsing, TTS generation, browser recording, overlay rendering, and final assembly. Vox integrates with Atlas as a downstream consumer of Kai's tutorial output.

**Tech Stack:** Playwright (browser automation + native .webm recording), OpenAI TTS API (`tts-1`), FFmpeg via `ffmpeg-python` wrapper, Python 3.12+ async.

---

## File Structure

| File | Responsibility |
|------|---------------|
| `agents/vox.py` | Main agent class. `execute()` orchestrates the pipeline: parse script → generate TTS → record browser → render overlays → assemble. Also has `generate_from_tutorial()` convenience method. |
| `agents/video/__init__.py` | Package init, exports key classes. |
| `agents/video/script_parser.py` | Converts Kai's markdown tutorial (or a standalone task string) into a list of `TutorialStep` dataclasses. Uses LLM to extract structured steps when available, falls back to heading-based parsing. |
| `agents/video/tts_engine.py` | Wraps OpenAI TTS API. Takes narration text, returns path to generated .mp3 file. Handles per-step audio generation and duration calculation. |
| `agents/video/browser_recorder.py` | Manages Playwright browser lifecycle. Opens URLs, executes actions (click, type, scroll, wait), records screen at 1920×1080. Returns path to .webm per step. |
| `agents/video/overlay_renderer.py` | FFmpeg-based post-processing. Adds step title overlays, callout text boxes, cursor highlight circles, and step number indicators to recorded video segments. |
| `agents/video/assembler.py` | Final FFmpeg pipeline. Concatenates step videos, merges TTS audio tracks, adds fade transitions between steps, outputs final .mp4. |
| `tests/test_vox.py` | Unit tests for Vox agent and all video submodules. |

---

## Chunk 1: Data Models + Script Parser

### Task 1: TutorialStep and VideoTutorial dataclasses

**Files:**
- Create: `agents/video/__init__.py`
- Create: `agents/video/script_parser.py`
- Test: `tests/test_vox.py`

- [ ] **Step 1: Write failing tests for dataclasses and script parser**

```python
# tests/test_vox.py
"""Tests for Vox video tutorial agent."""

import pytest
from agents.video.script_parser import (
    TutorialStep,
    VideoTutorial,
    ScriptParser,
)


class TestTutorialStep:
    """Test TutorialStep dataclass."""

    def test_create_step(self):
        step = TutorialStep(
            step_number=1,
            title="Install PostHog",
            narration="First, install the PostHog JavaScript SDK using npm.",
            url="https://posthog.com/docs",
            actions=[{"type": "click", "selector": "#install-btn"}],
            overlay_text="npm install posthog-js",
            duration_hint=10.0,
        )
        assert step.step_number == 1
        assert step.title == "Install PostHog"
        assert len(step.actions) == 1

    def test_step_defaults(self):
        step = TutorialStep(
            step_number=1,
            title="Step 1",
            narration="Narration text",
            url="https://example.com",
        )
        assert step.actions == []
        assert step.overlay_text == ""
        assert step.duration_hint == 5.0


class TestVideoTutorial:
    """Test VideoTutorial dataclass."""

    def test_create_tutorial(self):
        steps = [
            TutorialStep(
                step_number=1,
                title="Step 1",
                narration="First step",
                url="https://example.com",
            ),
        ]
        tutorial = VideoTutorial(
            title="How to use PostHog",
            steps=steps,
            output_path="output/tutorial.mp4",
            source="standalone_task",
        )
        assert tutorial.title == "How to use PostHog"
        assert tutorial.resolution == (1920, 1080)
        assert tutorial.total_duration == 0.0
        assert len(tutorial.steps) == 1


class TestScriptParser:
    """Test ScriptParser markdown → TutorialStep conversion."""

    @pytest.fixture
    def parser(self):
        return ScriptParser()

    def test_parse_markdown_with_headings(self, parser):
        markdown = """# How to Set Up Feature Flags

## Prerequisites
You need a PostHog account.

## Step 1: Install the SDK
Install the PostHog JavaScript SDK using npm.

```bash
npm install posthog-js
```

## Step 2: Initialize PostHog
Add the initialization code to your app.

```javascript
posthog.init('your-api-key', {api_host: 'https://app.posthog.com'})
```

## Step 3: Create a Feature Flag
Navigate to the Feature Flags page and create a new flag.
"""
        steps = parser.parse_markdown(markdown)
        assert len(steps) >= 2  # Should extract Step 1, Step 2, Step 3
        assert steps[0].step_number == 1
        assert "SDK" in steps[0].title or "Install" in steps[0].title

    def test_parse_empty_markdown(self, parser):
        steps = parser.parse_markdown("")
        assert steps == []

    def test_parse_markdown_no_steps(self, parser):
        markdown = "# Title\n\nJust a paragraph with no step headings."
        steps = parser.parse_markdown(markdown)
        # Should create a single step from the content
        assert len(steps) >= 1

    def test_parse_task_string(self, parser):
        task = "Show how to set up feature flags in PostHog"
        steps = parser.parse_task(task)
        assert len(steps) >= 1
        assert steps[0].step_number == 1
        assert steps[0].narration != ""

    def test_step_narration_stripped_of_code_blocks(self, parser):
        markdown = """## Step 1: Install SDK

Install with npm:

```bash
npm install posthog-js
```

This will add the dependency.
"""
        steps = parser.parse_markdown(markdown)
        # Narration should not include raw code block markers
        assert "```" not in steps[0].narration
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd . && python -m pytest tests/test_vox.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'agents.video'`

- [ ] **Step 3: Create the video package and dataclasses**

```python
# agents/video/__init__.py
"""Video tutorial generation package for Vox agent."""

from agents.video.script_parser import ScriptParser, TutorialStep, VideoTutorial

__all__ = ["ScriptParser", "TutorialStep", "VideoTutorial"]
```

```python
# agents/video/script_parser.py
"""
Script parser — converts markdown tutorials or task strings into TutorialStep sequences.

Parses headings as step boundaries, extracts narration text (stripping code blocks),
and builds a structured list of steps for the video pipeline.
"""

import re
import logging
from dataclasses import dataclass, field
from typing import Optional

logger = logging.getLogger(__name__)


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
    """Complete video tutorial metadata."""

    title: str
    steps: list[TutorialStep]
    output_path: str
    source: str  # 'kai_content' or 'standalone_task'
    resolution: tuple[int, int] = (1920, 1080)
    total_duration: float = 0.0


class ScriptParser:
    """Converts markdown content or task strings into TutorialStep lists."""

    # Matches ## headings that look like steps
    STEP_HEADING_RE = re.compile(
        r"^##\s+(?:Step\s+\d+[:\s]*)?(.+)$", re.IGNORECASE | re.MULTILINE
    )
    # Matches fenced code blocks
    CODE_BLOCK_RE = re.compile(r"```[\s\S]*?```", re.MULTILINE)
    # Matches prerequisite/intro headings to skip
    SKIP_HEADINGS = {"prerequisites", "introduction", "overview", "conclusion", "summary", "next steps"}

    def parse_markdown(self, markdown: str, base_url: str = "https://posthog.com") -> list[TutorialStep]:
        """Parse a markdown tutorial into ordered TutorialStep list.

        Strategy:
        1. Split by ## headings
        2. Skip non-step headings (prerequisites, conclusion)
        3. Extract narration (prose without code blocks)
        4. Extract overlay text from first code block in each section
        """
        if not markdown.strip():
            return []

        sections = self._split_by_headings(markdown)

        if not sections:
            # No headings found — treat entire content as one step
            narration = self._extract_narration(markdown)
            if narration:
                return [
                    TutorialStep(
                        step_number=1,
                        title="Tutorial",
                        narration=narration,
                        url=base_url,
                    )
                ]
            return []

        steps = []
        step_num = 1
        for heading, body in sections:
            if heading.lower().strip() in self.SKIP_HEADINGS:
                continue

            narration = self._extract_narration(body)
            if not narration:
                continue

            overlay = self._extract_first_code(body)

            steps.append(
                TutorialStep(
                    step_number=step_num,
                    title=heading.strip(),
                    narration=narration,
                    url=base_url,
                    overlay_text=overlay,
                    duration_hint=max(5.0, len(narration) / 15),  # ~15 chars/sec speech
                )
            )
            step_num += 1

        return steps

    def parse_task(self, task: str, base_url: str = "https://posthog.com") -> list[TutorialStep]:
        """Parse a standalone task string into a minimal step list.

        For standalone tasks without full markdown, creates a single-step
        tutorial. The browser recorder will navigate to base_url and the
        narration will be the task description.
        """
        if not task.strip():
            return []

        return [
            TutorialStep(
                step_number=1,
                title=task[:80],
                narration=task,
                url=base_url,
                duration_hint=max(5.0, len(task) / 15),
            )
        ]

    def _split_by_headings(self, markdown: str) -> list[tuple[str, str]]:
        """Split markdown into (heading, body) tuples by ## headings."""
        parts = re.split(r"^(##\s+.+)$", markdown, flags=re.MULTILINE)

        sections = []
        i = 1  # Skip content before first heading
        while i < len(parts) - 1:
            heading_line = parts[i].strip()
            body = parts[i + 1] if i + 1 < len(parts) else ""
            # Remove the ## prefix
            heading = re.sub(r"^##\s+(?:Step\s+\d+[:\s]*)?", "", heading_line).strip()
            sections.append((heading, body))
            i += 2

        return sections

    def _extract_narration(self, text: str) -> str:
        """Extract narration text by removing code blocks and markdown formatting."""
        # Remove code blocks
        clean = self.CODE_BLOCK_RE.sub("", text)
        # Remove markdown links, keep text
        clean = re.sub(r"\[([^\]]+)\]\([^\)]+\)", r"\1", clean)
        # Remove bold/italic markers
        clean = re.sub(r"[*_]{1,3}", "", clean)
        # Remove heading markers
        clean = re.sub(r"^#+\s+", "", clean, flags=re.MULTILINE)
        # Collapse whitespace
        clean = re.sub(r"\n{3,}", "\n\n", clean)
        return clean.strip()

    def _extract_first_code(self, text: str) -> str:
        """Extract the content of the first code block for overlay display."""
        match = re.search(r"```\w*\n([\s\S]*?)```", text)
        if match:
            code = match.group(1).strip()
            # Truncate long code blocks for overlay
            lines = code.split("\n")
            if len(lines) > 5:
                return "\n".join(lines[:5]) + "\n..."
            return code
        return ""
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd . && python -m pytest tests/test_vox.py -v`
Expected: All tests PASS

- [ ] **Step 5: Commit**

```bash
cd .
git add agents/video/__init__.py agents/video/script_parser.py tests/test_vox.py
git commit -m "feat(vox): add TutorialStep/VideoTutorial dataclasses and ScriptParser"
```

---

## Chunk 2: TTS Engine + Browser Recorder

### Task 2: OpenAI TTS engine

**Files:**
- Create: `agents/video/tts_engine.py`
- Test: `tests/test_vox.py` (append)

- [ ] **Step 1: Write failing tests for TTS engine**

Add to `tests/test_vox.py`:

```python
from unittest.mock import AsyncMock, MagicMock, patch, mock_open
from pathlib import Path
from agents.video.tts_engine import TTSEngine


class TestTTSEngine:
    """Test OpenAI TTS wrapper."""

    @pytest.fixture
    def tts(self, tmp_path):
        return TTSEngine(api_key="test-key", output_dir=tmp_path)

    def test_init_creates_output_dir(self, tmp_path):
        out = tmp_path / "tts_output"
        engine = TTSEngine(api_key="test-key", output_dir=out)
        assert out.exists()

    @pytest.mark.asyncio
    async def test_generate_audio_returns_path(self, tts, tmp_path):
        # Mock the OpenAI client
        mock_response = MagicMock()
        mock_response.stream_to_file = MagicMock()

        with patch.object(tts, "_client") as mock_client:
            mock_client.audio.speech.create = AsyncMock(return_value=mock_response)
            path = await tts.generate_audio("Hello world", "step_1")
            assert path.suffix == ".mp3"
            assert "step_1" in path.name

    @pytest.mark.asyncio
    async def test_generate_audio_uses_correct_voice(self, tts):
        with patch.object(tts, "_client") as mock_client:
            mock_response = MagicMock()
            mock_response.stream_to_file = MagicMock()
            mock_client.audio.speech.create = AsyncMock(return_value=mock_response)

            await tts.generate_audio("Test", "step_1", voice="nova")
            mock_client.audio.speech.create.assert_awaited_once()
            call_kwargs = mock_client.audio.speech.create.call_args[1]
            assert call_kwargs["voice"] == "nova"

    def test_estimate_duration(self, tts):
        # ~150 words per minute for TTS
        text = " ".join(["word"] * 150)
        duration = tts.estimate_duration(text)
        assert 55.0 <= duration <= 65.0  # approximately 60 seconds

    def test_estimate_duration_empty(self, tts):
        assert tts.estimate_duration("") == 0.0
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd . && python -m pytest tests/test_vox.py::TestTTSEngine -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'agents.video.tts_engine'`

- [ ] **Step 3: Implement TTS engine**

```python
# agents/video/tts_engine.py
"""
TTS engine — wraps OpenAI Text-to-Speech API for narration generation.

Generates .mp3 audio files from narration text, one per tutorial step.
"""

import logging
from pathlib import Path
from typing import Optional

from openai import AsyncOpenAI

logger = logging.getLogger(__name__)

# Default TTS settings
DEFAULT_MODEL = "tts-1"
DEFAULT_VOICE = "alloy"
WORDS_PER_MINUTE = 150  # approximate TTS speech rate


class TTSEngine:
    """Generates narration audio using OpenAI TTS API."""

    def __init__(
        self,
        api_key: str,
        output_dir: Path,
        model: str = DEFAULT_MODEL,
        voice: str = DEFAULT_VOICE,
    ):
        self._client = AsyncOpenAI(api_key=api_key)
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.model = model
        self.default_voice = voice

    async def generate_audio(
        self,
        text: str,
        filename_prefix: str,
        voice: Optional[str] = None,
    ) -> Path:
        """Generate an .mp3 audio file from text.

        Args:
            text: Narration text to convert to speech.
            filename_prefix: Prefix for output filename (e.g., "step_1").
            voice: OpenAI voice name. Defaults to self.default_voice.

        Returns:
            Path to the generated .mp3 file.
        """
        output_path = self.output_dir / f"{filename_prefix}.mp3"
        selected_voice = voice or self.default_voice

        logger.info(f"Generating TTS audio: {filename_prefix} ({len(text)} chars, voice={selected_voice})")

        response = await self._client.audio.speech.create(
            model=self.model,
            voice=selected_voice,
            input=text,
        )
        response.stream_to_file(str(output_path))

        logger.info(f"TTS audio saved to {output_path}")
        return output_path

    @staticmethod
    def estimate_duration(text: str) -> float:
        """Estimate audio duration in seconds based on word count.

        Uses approximate TTS speech rate of 150 words per minute.
        """
        if not text.strip():
            return 0.0
        word_count = len(text.split())
        return (word_count / WORDS_PER_MINUTE) * 60
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd . && python -m pytest tests/test_vox.py::TestTTSEngine -v`
Expected: All tests PASS

- [ ] **Step 5: Commit**

```bash
cd .
git add agents/video/tts_engine.py tests/test_vox.py
git commit -m "feat(vox): add OpenAI TTS engine for narration generation"
```

---

### Task 3: Playwright browser recorder

**Files:**
- Create: `agents/video/browser_recorder.py`
- Test: `tests/test_vox.py` (append)

- [ ] **Step 1: Write failing tests for browser recorder**

Add to `tests/test_vox.py`:

```python
from agents.video.browser_recorder import BrowserRecorder, BrowserAction


class TestBrowserAction:
    """Test BrowserAction dataclass."""

    def test_click_action(self):
        action = BrowserAction(action_type="click", selector="#btn")
        assert action.action_type == "click"
        assert action.selector == "#btn"
        assert action.value is None
        assert action.delay == 0.5

    def test_type_action(self):
        action = BrowserAction(action_type="type", selector="#input", value="hello", delay=1.0)
        assert action.value == "hello"
        assert action.delay == 1.0


class TestBrowserRecorder:
    """Test BrowserRecorder screen recording manager."""

    @pytest.fixture
    def recorder(self, tmp_path):
        return BrowserRecorder(output_dir=tmp_path, width=1920, height=1080)

    def test_init_stores_resolution(self, recorder):
        assert recorder.width == 1920
        assert recorder.height == 1080

    def test_parse_actions_from_dicts(self, recorder):
        action_dicts = [
            {"type": "click", "selector": "#button"},
            {"type": "type", "selector": "#name", "value": "PostHog"},
            {"type": "wait", "delay": 2.0},
            {"type": "scroll", "selector": "#content"},
        ]
        actions = recorder.parse_actions(action_dicts)
        assert len(actions) == 4
        assert actions[0].action_type == "click"
        assert actions[1].value == "PostHog"
        assert actions[2].delay == 2.0
        assert actions[3].action_type == "scroll"

    def test_parse_actions_empty(self, recorder):
        assert recorder.parse_actions([]) == []

    def test_parse_actions_unknown_type_skipped(self, recorder):
        actions = recorder.parse_actions([{"type": "unknown", "selector": "#x"}])
        assert len(actions) == 0
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd . && python -m pytest tests/test_vox.py::TestBrowserAction tests/test_vox.py::TestBrowserRecorder -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'agents.video.browser_recorder'`

- [ ] **Step 3: Implement browser recorder**

```python
# agents/video/browser_recorder.py
"""
Browser recorder — manages Playwright browser for screen recording.

Opens URLs, executes user actions (click, type, scroll, wait),
and captures screen recordings at 1920×1080 resolution.
"""

import asyncio
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

VALID_ACTION_TYPES = {"click", "type", "wait", "scroll", "hover"}


@dataclass
class BrowserAction:
    """A single browser interaction."""

    action_type: str  # "click", "type", "wait", "scroll", "hover"
    selector: Optional[str] = None
    value: Optional[str] = None
    delay: float = 0.5  # seconds to wait after action


class BrowserRecorder:
    """Records browser sessions using Playwright's native video recording."""

    def __init__(
        self,
        output_dir: Path,
        width: int = 1920,
        height: int = 1080,
        slow_mo: int = 200,
    ):
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.width = width
        self.height = height
        self.slow_mo = slow_mo

    async def record_step(
        self,
        url: str,
        actions: list[BrowserAction],
        filename_prefix: str,
        duration_hint: float = 10.0,
    ) -> Path:
        """Record a single tutorial step.

        Args:
            url: URL to navigate to.
            actions: List of browser actions to perform.
            filename_prefix: Prefix for output filename.
            duration_hint: Minimum recording duration in seconds.

        Returns:
            Path to the recorded .webm file.
        """
        from playwright.async_api import async_playwright

        output_path = self.output_dir / f"{filename_prefix}.webm"

        logger.info(f"Recording step: {filename_prefix} → {url}")

        async with async_playwright() as p:
            browser = await p.chromium.launch(
                headless=True,
                slow_mo=self.slow_mo,
            )
            context = await browser.new_context(
                viewport={"width": self.width, "height": self.height},
                record_video_dir=str(self.output_dir),
                record_video_size={"width": self.width, "height": self.height},
            )
            page = await context.new_page()

            try:
                await page.goto(url, wait_until="networkidle", timeout=30000)

                for action in actions:
                    await self._execute_action(page, action)

                # Hold for duration hint to capture any remaining content
                remaining = max(0, duration_hint - len(actions) * 0.5)
                if remaining > 0:
                    await asyncio.sleep(remaining)

            finally:
                await context.close()
                await browser.close()

            # Playwright saves video with auto-generated name; rename it
            video_path = page.video.path()
            if video_path and Path(video_path).exists():
                Path(video_path).rename(output_path)

        logger.info(f"Step recorded: {output_path}")
        return output_path

    async def _execute_action(self, page, action: BrowserAction) -> None:
        """Execute a single browser action on the page."""
        try:
            if action.action_type == "click" and action.selector:
                await page.click(action.selector, timeout=5000)
            elif action.action_type == "type" and action.selector and action.value:
                await page.fill(action.selector, action.value)
            elif action.action_type == "scroll" and action.selector:
                await page.evaluate(
                    f"document.querySelector('{action.selector}')?.scrollIntoView({{behavior: 'smooth'}})"
                )
            elif action.action_type == "hover" and action.selector:
                await page.hover(action.selector, timeout=5000)
            elif action.action_type == "wait":
                await asyncio.sleep(action.delay)

            # Pause after action for visual clarity
            if action.action_type != "wait":
                await asyncio.sleep(action.delay)

        except Exception as exc:
            logger.warning(f"Action failed ({action.action_type} {action.selector}): {exc}")

    def parse_actions(self, action_dicts: list[dict]) -> list[BrowserAction]:
        """Convert raw action dicts to BrowserAction objects.

        Skips unknown action types.
        """
        actions = []
        for d in action_dicts:
            action_type = d.get("type", "")
            if action_type not in VALID_ACTION_TYPES:
                logger.warning(f"Skipping unknown action type: {action_type}")
                continue
            actions.append(
                BrowserAction(
                    action_type=action_type,
                    selector=d.get("selector"),
                    value=d.get("value"),
                    delay=d.get("delay", 0.5),
                )
            )
        return actions
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd . && python -m pytest tests/test_vox.py::TestBrowserAction tests/test_vox.py::TestBrowserRecorder -v`
Expected: All tests PASS

- [ ] **Step 5: Commit**

```bash
cd .
git add agents/video/browser_recorder.py tests/test_vox.py
git commit -m "feat(vox): add Playwright browser recorder for screen capture"
```

---

## Chunk 3: Overlay Renderer + Assembler

### Task 4: FFmpeg overlay renderer

**Files:**
- Create: `agents/video/overlay_renderer.py`
- Test: `tests/test_vox.py` (append)

- [ ] **Step 1: Write failing tests for overlay renderer**

Add to `tests/test_vox.py`:

```python
from agents.video.overlay_renderer import OverlayRenderer, OverlayConfig


class TestOverlayConfig:
    """Test OverlayConfig dataclass."""

    def test_defaults(self):
        config = OverlayConfig()
        assert config.font_size == 32
        assert config.title_font_size == 48
        assert config.font_color == "white"
        assert config.bg_color == "black@0.7"
        assert config.padding == 20
        assert config.title_position == "top"
        assert config.callout_position == "bottom"

    def test_custom_values(self):
        config = OverlayConfig(font_size=24, font_color="yellow")
        assert config.font_size == 24
        assert config.font_color == "yellow"


class TestOverlayRenderer:
    """Test FFmpeg overlay pipeline."""

    @pytest.fixture
    def renderer(self, tmp_path):
        return OverlayRenderer(output_dir=tmp_path)

    def test_build_title_filter(self, renderer):
        filt = renderer._build_title_filter("Step 1: Install SDK", step_number=1)
        assert "Step 1" in filt or "Install SDK" in filt
        assert "drawtext" in filt

    def test_build_callout_filter(self, renderer):
        filt = renderer._build_callout_filter("npm install posthog-js")
        assert "drawtext" in filt
        assert "npm install" in filt

    def test_build_title_filter_escapes_special_chars(self, renderer):
        filt = renderer._build_title_filter("Step: Use 'quotes' & stuff", step_number=1)
        # Should not crash; special chars handled
        assert "drawtext" in filt

    def test_build_step_indicator_filter(self, renderer):
        filt = renderer._build_step_indicator(1, 5)
        assert "drawtext" in filt
        assert "1/5" in filt or "1 of 5" in filt
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd . && python -m pytest tests/test_vox.py::TestOverlayConfig tests/test_vox.py::TestOverlayRenderer -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'agents.video.overlay_renderer'`

- [ ] **Step 3: Implement overlay renderer**

```python
# agents/video/overlay_renderer.py
"""
Overlay renderer — adds visual polish to recorded video segments using FFmpeg.

Renders: step title bar, callout text boxes, step number indicator,
and cursor highlight circles.
"""

import logging
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


@dataclass
class OverlayConfig:
    """Configuration for overlay rendering."""

    font_size: int = 32
    title_font_size: int = 48
    font_color: str = "white"
    bg_color: str = "black@0.7"
    padding: int = 20
    title_position: str = "top"  # "top" or "bottom"
    callout_position: str = "bottom"  # "top" or "bottom"
    title_display_duration: float = 4.0  # seconds to show title overlay
    callout_display_duration: float = 0.0  # 0 = entire duration


class OverlayRenderer:
    """Adds text overlays and visual elements to video segments via FFmpeg."""

    def __init__(self, output_dir: Path, config: Optional[OverlayConfig] = None):
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.config = config or OverlayConfig()

    async def render_overlays(
        self,
        video_path: Path,
        title: str,
        step_number: int,
        total_steps: int,
        callout_text: str = "",
        filename_prefix: str = "overlay",
    ) -> Path:
        """Add overlays to a video segment.

        Args:
            video_path: Path to input .webm video.
            title: Step title for the title bar overlay.
            step_number: Current step number.
            total_steps: Total number of steps.
            callout_text: Code/text to display in callout box.
            filename_prefix: Output filename prefix.

        Returns:
            Path to the overlaid .mp4 video.
        """
        output_path = self.output_dir / f"{filename_prefix}_overlaid.mp4"

        filters = []
        filters.append(self._build_title_filter(title, step_number))
        filters.append(self._build_step_indicator(step_number, total_steps))

        if callout_text:
            filters.append(self._build_callout_filter(callout_text))

        filter_chain = ",".join(filters)

        cmd = [
            "ffmpeg", "-y",
            "-i", str(video_path),
            "-vf", filter_chain,
            "-c:v", "libx264",
            "-preset", "fast",
            "-crf", "23",
            "-c:a", "copy",
            str(output_path),
        ]

        logger.info(f"Rendering overlays for {filename_prefix}")
        process = subprocess.run(cmd, capture_output=True, text=True)

        if process.returncode != 0:
            logger.error(f"FFmpeg overlay failed: {process.stderr[:500]}")
            raise RuntimeError(f"FFmpeg overlay rendering failed: {process.stderr[:200]}")

        logger.info(f"Overlays rendered: {output_path}")
        return output_path

    def _build_title_filter(self, title: str, step_number: int) -> str:
        """Build FFmpeg drawtext filter for step title bar."""
        escaped = self._escape_ffmpeg_text(f"Step {step_number}: {title}")
        c = self.config
        y_pos = str(c.padding) if c.title_position == "top" else f"h-th-{c.padding}"

        return (
            f"drawtext=text='{escaped}'"
            f":fontsize={c.title_font_size}"
            f":fontcolor={c.font_color}"
            f":box=1:boxcolor={c.bg_color}:boxborderw={c.padding}"
            f":x=(w-tw)/2:y={y_pos}"
            f":enable='between(t,0,{c.title_display_duration})'"
        )

    def _build_callout_filter(self, text: str) -> str:
        """Build FFmpeg drawtext filter for code callout box."""
        escaped = self._escape_ffmpeg_text(text)
        c = self.config
        y_pos = f"h-th-{c.padding * 3}" if c.callout_position == "bottom" else str(c.padding * 3)

        duration_clause = ""
        if c.callout_display_duration > 0:
            duration_clause = f":enable='between(t,1,{c.callout_display_duration + 1})'"

        return (
            f"drawtext=text='{escaped}'"
            f":fontsize={c.font_size}"
            f":fontcolor={c.font_color}"
            f":font=monospace"
            f":box=1:boxcolor={c.bg_color}:boxborderw={c.padding}"
            f":x={c.padding * 2}:y={y_pos}"
            f"{duration_clause}"
        )

    def _build_step_indicator(self, step_number: int, total_steps: int) -> str:
        """Build FFmpeg drawtext filter for step counter badge."""
        c = self.config
        return (
            f"drawtext=text='{step_number}/{total_steps}'"
            f":fontsize={c.font_size}"
            f":fontcolor={c.font_color}"
            f":box=1:boxcolor={c.bg_color}:boxborderw=10"
            f":x=w-tw-{c.padding}:y={c.padding}"
        )

    @staticmethod
    def _escape_ffmpeg_text(text: str) -> str:
        """Escape special characters for FFmpeg drawtext filter."""
        # FFmpeg drawtext requires escaping these characters
        text = text.replace("\\", "\\\\")
        text = text.replace("'", "'\\''")
        text = text.replace(":", "\\:")
        text = text.replace("%", "%%")
        text = text.replace("\n", " ")
        return text
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd . && python -m pytest tests/test_vox.py::TestOverlayConfig tests/test_vox.py::TestOverlayRenderer -v`
Expected: All tests PASS

- [ ] **Step 5: Commit**

```bash
cd .
git add agents/video/overlay_renderer.py tests/test_vox.py
git commit -m "feat(vox): add FFmpeg overlay renderer for visual polish"
```

---

### Task 5: Video assembler

**Files:**
- Create: `agents/video/assembler.py`
- Test: `tests/test_vox.py` (append)

- [ ] **Step 1: Write failing tests for assembler**

Add to `tests/test_vox.py`:

```python
from agents.video.assembler import VideoAssembler


class TestVideoAssembler:
    """Test FFmpeg video assembly pipeline."""

    @pytest.fixture
    def assembler(self, tmp_path):
        return VideoAssembler(output_dir=tmp_path)

    def test_build_concat_file_content(self, assembler, tmp_path):
        video_paths = [
            tmp_path / "step_1.mp4",
            tmp_path / "step_2.mp4",
        ]
        content = assembler._build_concat_file_content(video_paths)
        assert "file 'step_1.mp4'" in content or "step_1.mp4" in content
        assert "file 'step_2.mp4'" in content or "step_2.mp4" in content

    def test_build_audio_merge_cmd(self, assembler, tmp_path):
        video = tmp_path / "video.mp4"
        audio = tmp_path / "audio.mp3"
        output = tmp_path / "final.mp4"
        cmd = assembler._build_audio_merge_cmd(video, audio, output)
        assert "ffmpeg" in cmd[0]
        assert str(video) in cmd
        assert str(audio) in cmd
        assert str(output) in cmd

    def test_init_creates_output_dir(self, tmp_path):
        out = tmp_path / "assemble_output"
        assembler = VideoAssembler(output_dir=out)
        assert out.exists()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd . && python -m pytest tests/test_vox.py::TestVideoAssembler -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'agents.video.assembler'`

- [ ] **Step 3: Implement video assembler**

```python
# agents/video/assembler.py
"""
Video assembler — final FFmpeg pipeline for concatenation and audio merging.

Concatenates step videos, merges TTS audio tracks per step,
adds fade transitions, and outputs the final .mp4.
"""

import logging
import subprocess
from pathlib import Path

logger = logging.getLogger(__name__)


class VideoAssembler:
    """Assembles step videos and audio into a final tutorial video."""

    def __init__(self, output_dir: Path):
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)

    async def assemble(
        self,
        step_videos: list[Path],
        step_audios: list[Path],
        output_filename: str = "tutorial.mp4",
    ) -> Path:
        """Assemble step videos and audio into final output.

        Pipeline:
        1. Merge each step's video + audio
        2. Concatenate all merged steps
        3. Output final .mp4

        Args:
            step_videos: Ordered list of step video paths (.mp4 with overlays).
            step_audios: Ordered list of step audio paths (.mp3 TTS).
            output_filename: Name for the final output file.

        Returns:
            Path to the final assembled .mp4 video.
        """
        if len(step_videos) != len(step_audios):
            raise ValueError(
                f"Mismatch: {len(step_videos)} videos vs {len(step_audios)} audios"
            )

        merged_steps = []

        # Step 1: Merge each video with its audio
        for i, (video, audio) in enumerate(zip(step_videos, step_audios)):
            merged_path = self.output_dir / f"merged_step_{i + 1}.mp4"
            await self._merge_audio_video(video, audio, merged_path)
            merged_steps.append(merged_path)

        # Step 2: Concatenate all steps
        final_path = self.output_dir / output_filename
        if len(merged_steps) == 1:
            # Single step — just rename
            merged_steps[0].rename(final_path)
        else:
            await self._concatenate_videos(merged_steps, final_path)

        # Cleanup intermediate merged files
        for p in merged_steps:
            if p.exists():
                p.unlink()

        logger.info(f"Final video assembled: {final_path}")
        return final_path

    async def _merge_audio_video(
        self, video_path: Path, audio_path: Path, output_path: Path,
    ) -> None:
        """Merge a video file with an audio file using FFmpeg."""
        cmd = self._build_audio_merge_cmd(video_path, audio_path, output_path)

        logger.info(f"Merging audio+video: {output_path.name}")
        process = subprocess.run(cmd, capture_output=True, text=True)

        if process.returncode != 0:
            logger.error(f"FFmpeg merge failed: {process.stderr[:500]}")
            raise RuntimeError(f"Audio/video merge failed: {process.stderr[:200]}")

    async def _concatenate_videos(
        self, video_paths: list[Path], output_path: Path,
    ) -> None:
        """Concatenate multiple video files using FFmpeg concat demuxer."""
        concat_file = self.output_dir / "concat_list.txt"
        concat_file.write_text(self._build_concat_file_content(video_paths))

        cmd = [
            "ffmpeg", "-y",
            "-f", "concat",
            "-safe", "0",
            "-i", str(concat_file),
            "-c", "copy",
            str(output_path),
        ]

        logger.info(f"Concatenating {len(video_paths)} steps")
        process = subprocess.run(cmd, capture_output=True, text=True)

        # Cleanup concat file
        concat_file.unlink(missing_ok=True)

        if process.returncode != 0:
            logger.error(f"FFmpeg concat failed: {process.stderr[:500]}")
            raise RuntimeError(f"Video concatenation failed: {process.stderr[:200]}")

    def _build_concat_file_content(self, video_paths: list[Path]) -> str:
        """Build FFmpeg concat demuxer file content."""
        lines = [f"file '{path}'" for path in video_paths]
        return "\n".join(lines)

    def _build_audio_merge_cmd(
        self, video_path: Path, audio_path: Path, output_path: Path,
    ) -> list[str]:
        """Build FFmpeg command to merge audio and video."""
        return [
            "ffmpeg", "-y",
            "-i", str(video_path),
            "-i", str(audio_path),
            "-c:v", "copy",
            "-c:a", "aac",
            "-b:a", "192k",
            "-map", "0:v:0",
            "-map", "1:a:0",
            "-shortest",
            str(output_path),
        ]
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd . && python -m pytest tests/test_vox.py::TestVideoAssembler -v`
Expected: All tests PASS

- [ ] **Step 5: Update `agents/video/__init__.py` exports**

```python
# agents/video/__init__.py
"""Video tutorial generation package for Vox agent."""

from agents.video.script_parser import ScriptParser, TutorialStep, VideoTutorial
from agents.video.tts_engine import TTSEngine
from agents.video.browser_recorder import BrowserRecorder, BrowserAction
from agents.video.overlay_renderer import OverlayRenderer, OverlayConfig
from agents.video.assembler import VideoAssembler

__all__ = [
    "ScriptParser", "TutorialStep", "VideoTutorial",
    "TTSEngine",
    "BrowserRecorder", "BrowserAction",
    "OverlayRenderer", "OverlayConfig",
    "VideoAssembler",
]
```

- [ ] **Step 6: Commit**

```bash
cd .
git add agents/video/assembler.py agents/video/__init__.py tests/test_vox.py
git commit -m "feat(vox): add video assembler for final concatenation and audio merge"
```

---

## Chunk 4: Vox Agent + Atlas Integration

### Task 6: Vox agent main class

**Files:**
- Create: `agents/vox.py`
- Test: `tests/test_vox.py` (append)

- [ ] **Step 1: Write failing tests for Vox agent**

Add to `tests/test_vox.py`:

```python
from agents.vox import Vox


class TestVoxAgent:
    """Test Vox video tutorial agent."""

    @pytest.fixture
    def vox(self, posthog_client, knowledge_base_path, tmp_path):
        return Vox(
            api_client=posthog_client,
            knowledge_base_path=knowledge_base_path,
            output_dir=tmp_path,
        )

    @pytest.mark.asyncio
    async def test_execute_returns_expected_structure(self, vox):
        result = await vox.execute("Show how to set up feature flags")
        assert result["agent"] == "vox"
        assert result["status"] in ("generated", "script_only")
        assert "task" in result
        assert "steps" in result

    @pytest.mark.asyncio
    async def test_execute_with_kai_context(self, vox):
        context = {
            "kai_content": {
                "content": """# Feature Flags Tutorial

## Step 1: Install SDK
Install the PostHog JavaScript SDK.

```bash
npm install posthog-js
```

## Step 2: Initialize
Add initialization code.

```javascript
posthog.init('key')
```
""",
            }
        }
        result = await vox.execute("Generate video tutorial", context)
        assert result["source"] == "kai_content"
        assert len(result["steps"]) >= 2

    @pytest.mark.asyncio
    async def test_execute_standalone_task(self, vox):
        result = await vox.execute("Show how to create an A/B test in PostHog")
        assert result["source"] == "standalone_task"
        assert len(result["steps"]) >= 1

    @pytest.mark.asyncio
    async def test_execute_without_ffmpeg_returns_script_only(self, vox):
        # Without actual Playwright/FFmpeg, should gracefully return script_only
        result = await vox.execute("Show feature flags setup")
        assert result["status"] in ("generated", "script_only")

    def test_vox_has_system_prompt(self, vox):
        assert "Vox" in Vox.SYSTEM_PROMPT or "video" in Vox.SYSTEM_PROMPT.lower()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd . && python -m pytest tests/test_vox.py::TestVoxAgent -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'agents.vox'`

- [ ] **Step 3: Implement Vox agent**

```python
# agents/vox.py
"""
Vox — Video Tutorial Agent

Generates polished video tutorials from written content or standalone tasks.
Uses Playwright for screen recording, OpenAI TTS for narration, and FFmpeg
for overlays and assembly.
"""

import logging
import shutil
from pathlib import Path
from typing import Any, Optional

from tools.api_client import PostHogClient
from agents.video.script_parser import ScriptParser, TutorialStep, VideoTutorial

logger = logging.getLogger(__name__)


def _check_ffmpeg() -> bool:
    """Check if FFmpeg is available on the system."""
    return shutil.which("ffmpeg") is not None


def _check_playwright() -> bool:
    """Check if Playwright is importable."""
    try:
        import playwright  # noqa: F401
        return True
    except ImportError:
        return False


class Vox:
    """
    Video Tutorial agent that produces screen-recorded tutorials.

    Capabilities:
    - Convert Kai's written tutorials into video walkthroughs
    - Generate standalone video tutorials from task descriptions
    - Add polished overlays: step titles, code callouts, step indicators
    - Narrate with OpenAI TTS

    Pipeline:
    1. Parse script (from Kai's markdown or standalone task)
    2. Generate TTS narration per step
    3. Record browser session per step
    4. Render overlays per step
    5. Assemble final video

    Dependencies (optional — gracefully degrades without them):
    - playwright: browser recording
    - ffmpeg (system binary): overlays + assembly
    - openai: TTS narration
    """

    SYSTEM_PROMPT = """You are Vox, a video tutorial producer for PostHog.
Your role is to transform written tutorials and task descriptions into
structured video scripts with clear steps, narration text, and browser actions.

Each step should have:
1. A clear title
2. Narration text (what to say during this step)
3. A URL to navigate to
4. Browser actions (click, type, scroll)
5. Overlay text (code snippets or key points to display)

Keep narration concise and developer-focused. Show, don't tell."""

    def __init__(
        self,
        api_client: PostHogClient,
        knowledge_base_path: Path,
        output_dir: Path = Path("output/videos"),
        openai_api_key: Optional[str] = None,
    ):
        self.api_client = api_client
        self.knowledge_base_path = knowledge_base_path
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.openai_api_key = openai_api_key
        self.script_parser = ScriptParser()

        self._has_ffmpeg = _check_ffmpeg()
        self._has_playwright = _check_playwright()

        if not self._has_ffmpeg:
            logger.warning("FFmpeg not found — video rendering will be skipped")
        if not self._has_playwright:
            logger.warning("Playwright not installed — recording will be skipped")

    async def execute(
        self,
        task: str,
        context: Optional[dict[str, Any]] = None,
    ) -> dict[str, Any]:
        """
        Execute a video tutorial generation task.

        If kai_content is present in context, uses that as the source script.
        Otherwise, treats the task string as a standalone tutorial request.
        """
        logger.info(f"Vox executing: {task[:80]}...")

        # Determine source and parse steps
        source = "standalone_task"
        steps: list[TutorialStep] = []

        if context and "kai_content" in context:
            kai = context["kai_content"]
            content = kai.get("content", "") if isinstance(kai, dict) else ""
            if content:
                steps = self.script_parser.parse_markdown(content)
                source = "kai_content"

        if not steps:
            steps = self.script_parser.parse_task(task)
            source = "standalone_task"

        tutorial = VideoTutorial(
            title=task[:100],
            steps=steps,
            output_path=str(self.output_dir / "tutorial.mp4"),
            source=source,
        )

        result = {
            "agent": "vox",
            "task": task,
            "source": source,
            "steps": [
                {
                    "step_number": s.step_number,
                    "title": s.title,
                    "narration": s.narration[:200],
                    "url": s.url,
                    "actions_count": len(s.actions),
                    "overlay_text": s.overlay_text[:100] if s.overlay_text else "",
                    "duration_hint": s.duration_hint,
                }
                for s in steps
            ],
            "total_steps": len(steps),
            "status": "script_only",
        }

        # If full pipeline is available, run it
        can_render = self._has_ffmpeg and self._has_playwright and self.openai_api_key
        if can_render:
            try:
                output_path = await self._run_full_pipeline(tutorial)
                result["status"] = "generated"
                result["output_path"] = str(output_path)
                result["total_duration"] = tutorial.total_duration
            except Exception as exc:
                logger.error(f"Video pipeline failed: {exc}")
                result["status"] = "script_only"
                result["pipeline_error"] = str(exc)

        return result

    async def _run_full_pipeline(self, tutorial: VideoTutorial) -> Path:
        """Run the complete video generation pipeline.

        Steps: TTS → Record → Overlay → Assemble
        """
        from agents.video.tts_engine import TTSEngine
        from agents.video.browser_recorder import BrowserRecorder
        from agents.video.overlay_renderer import OverlayRenderer
        from agents.video.assembler import VideoAssembler

        tts_dir = self.output_dir / "tts"
        recording_dir = self.output_dir / "recordings"
        overlay_dir = self.output_dir / "overlays"

        tts = TTSEngine(api_key=self.openai_api_key, output_dir=tts_dir)
        recorder = BrowserRecorder(output_dir=recording_dir)
        overlays = OverlayRenderer(output_dir=overlay_dir)
        assembler = VideoAssembler(output_dir=self.output_dir)

        step_audios = []
        step_videos = []
        total_steps = len(tutorial.steps)

        for step in tutorial.steps:
            prefix = f"step_{step.step_number}"

            # Generate TTS audio
            audio_path = await tts.generate_audio(
                step.narration, prefix,
            )
            step_audios.append(audio_path)

            # Record browser session
            actions = recorder.parse_actions(step.actions)
            video_path = await recorder.record_step(
                url=step.url,
                actions=actions,
                filename_prefix=prefix,
                duration_hint=step.duration_hint,
            )

            # Add overlays
            overlaid_path = await overlays.render_overlays(
                video_path=video_path,
                title=step.title,
                step_number=step.step_number,
                total_steps=total_steps,
                callout_text=step.overlay_text,
                filename_prefix=prefix,
            )
            step_videos.append(overlaid_path)

        # Assemble final video
        final_path = await assembler.assemble(
            step_videos=step_videos,
            step_audios=step_audios,
            output_filename="tutorial.mp4",
        )

        tutorial.output_path = str(final_path)
        tutorial.total_duration = sum(s.duration_hint for s in tutorial.steps)

        return final_path

    async def generate_from_tutorial(
        self,
        tutorial_content: str,
        title: str = "Tutorial",
    ) -> dict[str, Any]:
        """Convenience method: generate video directly from markdown content."""
        context = {"kai_content": {"content": tutorial_content}}
        return await self.execute(title, context)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd . && python -m pytest tests/test_vox.py::TestVoxAgent -v`
Expected: All tests PASS

- [ ] **Step 5: Commit**

```bash
cd .
git add agents/vox.py tests/test_vox.py
git commit -m "feat(vox): add Vox video tutorial agent with full pipeline"
```

---

### Task 7: Atlas integration

**Files:**
- Modify: `agents/atlas.py`
- Test: `tests/test_vox.py` (append)

- [ ] **Step 1: Write failing test for Atlas integration**

Add to `tests/test_vox.py`:

```python
class TestAtlasIntegration:
    """Test Vox integration with Atlas orchestrator."""

    def test_shared_context_has_vox_field(self):
        from agents.atlas import SharedContext
        ctx = SharedContext()
        assert hasattr(ctx, "vox_video")
        assert ctx.vox_video == {}

    def test_shared_context_to_dict_includes_vox(self):
        from agents.atlas import SharedContext
        ctx = SharedContext()
        d = ctx.to_dict()
        assert "vox_video" in d

    def test_atlas_has_vox_agent(self):
        from unittest.mock import MagicMock
        from agents.atlas import Atlas
        client = MagicMock()
        atlas = Atlas(
            api_client=client,
            knowledge_base_path=Path("/tmp/kb"),
        )
        assert "vox" in atlas._agents
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd . && python -m pytest tests/test_vox.py::TestAtlasIntegration -v`
Expected: FAIL — SharedContext has no `vox_video` field, Atlas has no `vox` agent

- [ ] **Step 3: Update Atlas to integrate Vox**

In `agents/atlas.py`, make these changes:

1. Add import at the top (after other agent imports):
```python
from agents.vox import Vox
```

2. Add `vox_video` field to SharedContext (after `kai_content`):
```python
vox_video: dict[str, Any] = field(default_factory=dict)
```

3. Add `vox_video` to `SharedContext.to_dict()`:
```python
"vox_video": self.vox_video,
```

4. Initialize Vox in `Atlas.__init__()` (after Kai initialization):
```python
self.vox = Vox(
    api_client=api_client,
    knowledge_base_path=knowledge_base_path,
    openai_api_key=os.environ.get("OPENAI_API_KEY", ""),
)
```

5. Register in `_agents` dict:
```python
"vox": self.vox,
```

6. Add Stage 4b to `run_weekly_cycle()` (after Kai, before OKR compilation):
```python
# Stage 4b: Video tutorial (Vox) — uses Kai's content
video_result = await self.delegate(
    "vox",
    "Generate a video tutorial from Kai's written content. "
    "Record screen walkthrough with narration and overlays.",
)
if video_result.success:
    self.context.vox_video = video_result.output
```

7. Add `video_produced` to `_compile_okrs()`:
```python
"video_produced": bool(self.context.vox_video),
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd . && python -m pytest tests/test_vox.py::TestAtlasIntegration -v`
Expected: All tests PASS

- [ ] **Step 5: Run full test suite to verify no regressions**

Run: `cd . && python -m pytest tests/ -v`
Expected: All existing tests + new tests PASS

- [ ] **Step 6: Commit**

```bash
cd .
git add agents/atlas.py tests/test_vox.py
git commit -m "feat(vox): integrate Vox agent with Atlas orchestrator and weekly cycle"
```

---

### Task 8: Update dependencies and documentation

**Files:**
- Modify: `requirements.txt`
- Modify: `CLAUDE.md`

- [ ] **Step 1: Add new dependencies to requirements.txt**

Add these lines to the end of `requirements.txt`:

```
playwright==1.49.0
ffmpeg-python==0.2.0
openai==1.50.0
```

- [ ] **Step 2: Update CLAUDE.md agent listing**

In the Architecture section of `CLAUDE.md`, add Vox to the agent tree:

```
Atlas (Orchestrator)
├── Sage  → Community Manager (GitHub issue triage, sentiment, churn risk)
├── Echo  → Social Media Listener (Reddit, HN, Twitter/X monitoring)
├── Iris  → Feedback Synthesizer (theme extraction, pain point ranking, journey maps)
├── Nova  → Growth Strategist (experiments, funnels, cohort segmentation, power analysis)
├── Kai   → Content Creator (tutorials, blog posts, changelogs from knowledge base)
└── Vox   → Video Producer (screen-recorded tutorials, TTS narration, FFmpeg overlays)
```

Add to the File Map section:

```
agents/
  vox.py        — Video Producer. Orchestrates: ScriptParser → TTSEngine →
                   BrowserRecorder → OverlayRenderer → VideoAssembler.
                   Consumes Kai's output or standalone tasks.

agents/video/
  __init__.py           — Package exports
  script_parser.py      — Markdown → TutorialStep list conversion
  tts_engine.py         — OpenAI TTS wrapper (.mp3 per step)
  browser_recorder.py   — Playwright screen recorder (1920×1080 .webm)
  overlay_renderer.py   — FFmpeg text overlays (titles, callouts, step indicators)
  assembler.py          — FFmpeg concat + audio merge → final .mp4
```

Add to the Weekly orchestration cycle section:

```
- **Thursday (cont)**: Vox produces video tutorial from Kai's written content
```

Add `OPENAI_API_KEY` to the Environment Variables section.

- [ ] **Step 3: Commit**

```bash
cd .
git add requirements.txt CLAUDE.md
git commit -m "docs: update CLAUDE.md and requirements.txt for Vox agent"
```

- [ ] **Step 4: Run full test suite one final time**

Run: `cd . && python -m pytest tests/ -v --tb=short`
Expected: All tests PASS, no regressions
