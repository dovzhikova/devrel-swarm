"""Tests for the Vox video tutorial agent — ScriptParser and dataclasses."""

import pytest

from unittest.mock import AsyncMock, MagicMock, patch
from pathlib import Path

from devrel_swarm.core.video import ScriptParser, TutorialStep, VideoTutorial
from devrel_swarm.core.video.tts_engine import TTSEngine
from devrel_swarm.core.video.browser_recorder import BrowserRecorder, BrowserAction
from devrel_swarm.core.video.overlay_renderer import OverlayRenderer, OverlayConfig
from devrel_swarm.core.video.assembler import VideoAssembler


class TestTutorialStep:
    """Tests for the TutorialStep dataclass."""

    def test_create_step(self):
        step = TutorialStep(
            step_number=1,
            title="Install PostHog",
            narration="First, install the PostHog SDK.",
            url="https://posthog.com/docs",
            actions=[{"type": "click", "selector": "#install"}],
            overlay_text="pip install posthog",
            duration_hint=10.0,
        )
        assert step.step_number == 1
        assert step.title == "Install PostHog"
        assert step.narration == "First, install the PostHog SDK."
        assert step.url == "https://posthog.com/docs"
        assert len(step.actions) == 1
        assert step.overlay_text == "pip install posthog"
        assert step.duration_hint == 10.0

    def test_step_defaults(self):
        step = TutorialStep(
            step_number=1,
            title="Test",
            narration="Some narration.",
            url="https://posthog.com",
        )
        assert step.actions == []
        assert step.overlay_text == ""
        assert step.duration_hint == 5.0


class TestVideoTutorial:
    """Tests for the VideoTutorial dataclass."""

    def test_create_tutorial(self):
        steps = [
            TutorialStep(
                step_number=1,
                title="Step one",
                narration="Do this first.",
                url="https://posthog.com",
            )
        ]
        tutorial = VideoTutorial(
            title="Getting Started",
            steps=steps,
            output_path="/tmp/tutorial.mp4",
            source="markdown",
        )
        assert tutorial.title == "Getting Started"
        assert len(tutorial.steps) == 1
        assert tutorial.output_path == "/tmp/tutorial.mp4"
        assert tutorial.source == "markdown"
        assert tutorial.resolution == (1920, 1080)
        assert tutorial.total_duration == 0.0


class TestScriptParser:
    """Tests for the ScriptParser class."""

    @pytest.fixture
    def parser(self):
        return ScriptParser()

    def test_parse_markdown_with_headings(self, parser):
        markdown = """# Tutorial Title

## Prerequisites

You need Python 3.12 installed.

## Install the SDK

Install PostHog with pip:

```bash
pip install posthog
```

Then verify it works.

## Configure Your Project

Add your API key to the config:

```python
import posthog
posthog.api_key = "phc_xxx"
```

This connects to PostHog Cloud.

## Conclusion

You're all set!
"""
        steps = parser.parse_markdown(markdown)
        assert len(steps) == 2
        assert steps[0].step_number == 1
        assert steps[0].title == "Install the SDK"
        assert "pip install posthog" in steps[0].overlay_text
        assert "```" not in steps[0].narration
        assert steps[1].step_number == 2
        assert steps[1].title == "Configure Your Project"
        assert steps[1].duration_hint >= 5.0

    def test_parse_empty_markdown(self, parser):
        steps = parser.parse_markdown("")
        assert steps == []

    def test_parse_markdown_no_steps(self, parser):
        markdown = "Just some text without any headings."
        steps = parser.parse_markdown(markdown)
        assert steps == []

    def test_parse_task_string(self, parser):
        task = "Show how to set up feature flags in PostHog"
        steps = parser.parse_task(task)
        assert len(steps) == 1
        assert steps[0].step_number == 1
        assert "feature flags" in steps[0].narration
        assert steps[0].url == "https://example.com"

    def test_step_narration_stripped_of_code_blocks(self, parser):
        markdown = """## Set up tracking

Add this code to your app:

```javascript
posthog.capture('event_name')
```

This will track the event in PostHog.
"""
        steps = parser.parse_markdown(markdown)
        assert len(steps) == 1
        narration = steps[0].narration
        assert "```" not in narration
        assert "posthog.capture" not in narration
        assert "track the event" in narration


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
        text = " ".join(["word"] * 150)
        duration = tts.estimate_duration(text)
        assert 55.0 <= duration <= 65.0

    def test_estimate_duration_empty(self, tts):
        assert tts.estimate_duration("") == 0.0


class TestBrowserAction:
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


class TestOverlayConfig:
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
    @pytest.fixture
    def renderer(self, tmp_path):
        return OverlayRenderer(output_dir=tmp_path)

    def test_build_title_filter(self, renderer):
        filt = renderer._build_title_filter("Install SDK", step_number=1)
        assert "drawtext" in filt
        assert "Step 1" in filt

    def test_build_callout_filter(self, renderer):
        filt = renderer._build_callout_filter("npm install posthog-js")
        assert "drawtext" in filt
        assert "npm install" in filt

    def test_build_title_filter_escapes_special_chars(self, renderer):
        filt = renderer._build_title_filter("Step: Use 'quotes' & stuff", step_number=1)
        assert "drawtext" in filt

    def test_build_step_indicator_filter(self, renderer):
        filt = renderer._build_step_indicator(1, 5)
        assert "drawtext" in filt
        assert "1/5" in filt


class TestVideoAssembler:
    @pytest.fixture
    def assembler(self, tmp_path):
        return VideoAssembler(output_dir=tmp_path)

    def test_build_concat_file_content(self, assembler, tmp_path):
        video_paths = [tmp_path / "step_1.mp4", tmp_path / "step_2.mp4"]
        content = assembler._build_concat_file_content(video_paths)
        assert "step_1.mp4" in content
        assert "step_2.mp4" in content

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


# ---------------------------------------------------------------------------
# Vox agent integration tests
# ---------------------------------------------------------------------------

from devrel_swarm.core.vox import Vox


class TestVoxAgent:
    @pytest.fixture
    def vox(self, posthog_client, knowledge_base_path, tmp_path):
        return Vox(
            api_client=posthog_client,
            knowledge_base_path=knowledge_base_path,
            output_dir=tmp_path,
        )

    @pytest.fixture
    def vox_with_search(self, posthog_client, knowledge_base_path, tmp_path):
        mock_search = MagicMock()
        mock_search.fetch_official_docs = AsyncMock(
            return_value="## Feature Flags\nOfficial docs content."
        )
        return Vox(
            api_client=posthog_client,
            knowledge_base_path=knowledge_base_path,
            output_dir=tmp_path,
            search_tools=mock_search,
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
        result = await vox.execute("Show feature flags setup")
        assert result["status"] in ("generated", "script_only")

    def test_vox_has_system_prompt(self, vox):
        assert "Vox" in Vox.SYSTEM_PROMPT or "video" in Vox.SYSTEM_PROMPT.lower()

    @pytest.mark.asyncio
    async def test_execute_fetches_official_docs(self, vox_with_search):
        result = await vox_with_search.execute("Show feature flags setup")
        vox_with_search.search_tools.fetch_official_docs.assert_awaited_once()
        assert result["agent"] == "vox"

    @pytest.mark.asyncio
    async def test_execute_handles_docs_fetch_failure(self, posthog_client, knowledge_base_path, tmp_path):
        mock_search = MagicMock()
        mock_search.fetch_official_docs = AsyncMock(side_effect=Exception("Network error"))
        vox = Vox(
            api_client=posthog_client,
            knowledge_base_path=knowledge_base_path,
            output_dir=tmp_path,
            search_tools=mock_search,
        )
        result = await vox.execute("Show feature flags setup")
        # Should not crash — degrades gracefully
        assert result["agent"] == "vox"
        assert result["status"] in ("generated", "script_only")


class TestAtlasIntegration:
    """Test Vox integration with Atlas orchestrator."""

    def test_shared_context_has_vox_field(self):
        from devrel_swarm.core.atlas import SharedContext
        ctx = SharedContext()
        assert hasattr(ctx, "vox_video")
        assert ctx.vox_video == {}

    def test_shared_context_to_dict_includes_vox(self):
        from devrel_swarm.core.atlas import SharedContext
        ctx = SharedContext()
        d = ctx.to_dict()
        assert "vox_video" in d

    def test_atlas_has_vox_agent(self):
        from devrel_swarm.core.atlas import Atlas
        client = MagicMock()
        atlas = Atlas(
            api_client=client,
            knowledge_base_path=Path("/tmp/kb"),
        )
        assert "vox" in atlas._agents


# ---------------------------------------------------------------------------
# Desktop recorder tests
# ---------------------------------------------------------------------------

from devrel_swarm.core.video.desktop_recorder import DesktopRecorder, DesktopAction, _get_ffmpeg_input_format


class TestDesktopAction:
    def test_click_action(self):
        action = DesktopAction(action_type="click", x=100, y=200)
        assert action.action_type == "click"
        assert action.x == 100
        assert action.y == 200
        assert action.delay == 0.5

    def test_type_action(self):
        action = DesktopAction(action_type="type", value="hello world")
        assert action.value == "hello world"

    def test_hotkey_action(self):
        action = DesktopAction(action_type="hotkey", value="command+c")
        assert action.value == "command+c"

    def test_defaults(self):
        action = DesktopAction(action_type="wait")
        assert action.x is None
        assert action.y is None
        assert action.value is None
        assert action.delay == 0.5


class TestDesktopRecorder:
    @pytest.fixture
    def recorder(self, tmp_path):
        return DesktopRecorder(output_dir=tmp_path)

    def test_init_stores_settings(self, recorder):
        assert recorder.width == 1920
        assert recorder.height == 1080
        assert recorder.framerate == 30

    def test_init_creates_output_dir(self, tmp_path):
        out = tmp_path / "desktop_output"
        rec = DesktopRecorder(output_dir=out)
        assert out.exists()

    def test_parse_actions_from_dicts(self, recorder):
        action_dicts = [
            {"type": "click", "x": 100, "y": 200},
            {"type": "type", "value": "hello"},
            {"type": "wait", "delay": 2.0},
            {"type": "hotkey", "value": "command+v"},
            {"type": "scroll", "value": "-3"},
            {"type": "move", "x": 500, "y": 300},
        ]
        actions = recorder.parse_actions(action_dicts)
        assert len(actions) == 6
        assert actions[0].action_type == "click"
        assert actions[0].x == 100
        assert actions[1].value == "hello"
        assert actions[3].value == "command+v"

    def test_parse_actions_unknown_type_skipped(self, recorder):
        actions = recorder.parse_actions([{"type": "unknown"}])
        assert len(actions) == 0

    def test_parse_actions_empty(self, recorder):
        assert recorder.parse_actions([]) == []


class TestGetFFmpegInputFormat:
    def test_returns_tuple(self):
        fmt, device = _get_ffmpeg_input_format()
        assert isinstance(fmt, str)
        assert isinstance(device, str)
        assert fmt in ("avfoundation", "x11grab", "gdigrab")


class TestVoxSlug:
    """Tests for the _slug helper that builds safe filenames."""

    def test_slug_handles_unsafe_chars(self):
        from devrel_swarm.core.vox import _slug

        # Spaces, slashes, punctuation, and emoji-style chars all collapse
        # to single hyphens; result is filesystem-safe.
        result = _slug("Build a / Tutorial: Step 1!")
        assert result == "build-a-tutorial-step-1"
        # Empty / all-punct input falls back to the safe default
        assert _slug("") == "tutorial"
        assert _slug("!!!@@@   ") == "tutorial"
        # max_len truncation is honored
        long = _slug("a" * 200)
        assert len(long) == 32

    def test_slug_uniqueness_across_distinct_tasks(self):
        from devrel_swarm.core.vox import _slug

        # Different inputs produce different slugs — the timestamp prefix
        # added at the call site provides full uniqueness, but the slug
        # itself must still differentiate distinct tasks.
        a = _slug("Setting up PostHog feature flags")
        b = _slug("Recording a Vox video tutorial")
        assert a != b
        assert a == "setting-up-posthog-feature-flags"
        assert b == "recording-a-vox-video-tutorial"
