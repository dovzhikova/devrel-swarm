"""
Vox — Video Tutorial Agent

Generates polished video tutorials from written content or standalone tasks.
Uses Playwright for screen recording, OpenAI TTS for narration, and FFmpeg
for overlays and assembly.
"""

import logging
import re
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from devrel_swarm.core.video.script_parser import ScriptParser, TutorialStep, VideoTutorial
from devrel_swarm.tools.api_client import PostHogClient
from devrel_swarm.tools.search_tools import SearchTools

logger = logging.getLogger(__name__)


def _slug(text: str, max_len: int = 32) -> str:
    """Slugify a free-form task string for use in filenames.

    Lowercases, replaces runs of non-alphanumeric chars with single hyphens,
    strips leading/trailing hyphens, truncates to ``max_len``, and falls
    back to "tutorial" when the result would otherwise be empty (e.g.,
    the input was all whitespace or punctuation).
    """
    return re.sub(r"[^a-zA-Z0-9]+", "-", text.lower()).strip("-")[:max_len] or "tutorial"


def _check_ffmpeg() -> bool:
    return shutil.which("ffmpeg") is not None


def _check_playwright() -> bool:
    try:
        import playwright  # noqa: F401
        return True
    except ImportError:
        return False


class Vox:
    """
    Video Tutorial agent that produces screen-recorded tutorials.

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

    SYSTEM_PROMPT = """You are Vox, a video tutorial producer for OpenClaw.
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
        search_tools: Optional[SearchTools] = None,
    ):
        self.api_client = api_client
        self.knowledge_base_path = knowledge_base_path
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.openai_api_key = openai_api_key
        self.search_tools = search_tools
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
        """Execute the video tutorial generation pipeline.

        Args:
            task: Description of the tutorial to produce.
            context: Optional dict; if it contains 'kai_content' with a
                     'content' key, that markdown is parsed into steps.

        Returns:
            Dict with agent name, parsed steps, status, and output path
            (when full pipeline runs).
        """
        logger.info(f"Vox executing: {task[:80]}...")

        # Validate against official docs to ensure accuracy
        if self.search_tools:
            try:
                official_docs = await self.search_tools.fetch_official_docs(task)
                if official_docs:
                    logger.info(
                        f"Fetched official docs for validation ({len(official_docs)} chars)"
                    )
            except Exception as exc:
                logger.warning(f"Official docs fetch failed: {exc}")

        source = "standalone_task"
        steps: list[TutorialStep] = []

        # Try to parse from Kai's markdown output first
        if context and "kai_content" in context:
            kai = context["kai_content"]
            content = kai.get("content", "") if isinstance(kai, dict) else ""
            if content:
                steps = self.script_parser.parse_markdown(content)
                source = "kai_content"

        # Fall back to parsing the task string directly
        if not steps:
            steps = self.script_parser.parse_task(task)
            source = "standalone_task"

        ts = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
        output_filename = f"{ts}-{_slug(task)}.mp4"
        tutorial = VideoTutorial(
            title=task[:100],
            steps=steps,
            output_path=str(self.output_dir / output_filename),
            source=source,
        )

        result: dict[str, Any] = {
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

        can_render = (
            self._has_ffmpeg and self._has_playwright and self.openai_api_key
        )
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
        """Run the full TTS + recording + overlay + assembly pipeline."""
        from devrel_swarm.core.video.assembler import VideoAssembler
        from devrel_swarm.core.video.browser_recorder import BrowserRecorder
        from devrel_swarm.core.video.desktop_recorder import DesktopRecorder
        from devrel_swarm.core.video.overlay_renderer import OverlayRenderer
        from devrel_swarm.core.video.tts_engine import TTSEngine

        tts_dir = self.output_dir / "tts"
        recording_dir = self.output_dir / "recordings"
        overlay_dir = self.output_dir / "overlays"

        tts = TTSEngine(api_key=self.openai_api_key, output_dir=tts_dir)
        recorder = BrowserRecorder(output_dir=recording_dir)
        desktop_recorder = DesktopRecorder(output_dir=recording_dir)
        overlays = OverlayRenderer(output_dir=overlay_dir)
        assembler = VideoAssembler(output_dir=self.output_dir)

        step_audios: list[Path] = []
        step_videos: list[Path] = []
        total_steps = len(tutorial.steps)

        for step in tutorial.steps:
            prefix = f"step_{step.step_number}"

            audio_path = await tts.generate_audio(step.narration, prefix)
            step_audios.append(audio_path)

            # Choose recorder based on step type:
            # URLs starting with "http" use browser recording;
            # everything else (app names like "Figma", "VS Code") uses desktop recording.
            if step.url.startswith("http"):
                actions = recorder.parse_actions(step.actions)
                video_path = await recorder.record_step(
                    url=step.url,
                    actions=actions,
                    filename_prefix=prefix,
                    duration_hint=step.duration_hint,
                )
            else:
                desktop_actions = desktop_recorder.parse_actions(step.actions)
                video_path = await desktop_recorder.record_step(
                    actions=desktop_actions,
                    filename_prefix=prefix,
                    duration_hint=step.duration_hint,
                    app_name=step.url if step.url else None,
                )

            overlaid_path = await overlays.render_overlays(
                video_path=video_path,
                title=step.title,
                step_number=step.step_number,
                total_steps=total_steps,
                callout_text=step.overlay_text,
                filename_prefix=prefix,
            )
            step_videos.append(overlaid_path)

        final_path = await assembler.assemble(
            step_videos=step_videos,
            step_audios=step_audios,
            output_filename=Path(tutorial.output_path).name,
        )

        tutorial.output_path = str(final_path)
        tutorial.total_duration = sum(s.duration_hint for s in tutorial.steps)
        return final_path

    async def generate_from_tutorial(
        self, tutorial_content: str, title: str = "Tutorial"
    ) -> dict[str, Any]:
        """Convenience method: generate a video from raw tutorial markdown.

        Args:
            tutorial_content: Markdown content with ## headings for steps.
            title: Title for the tutorial task.

        Returns:
            Same dict as execute().
        """
        context = {"kai_content": {"content": tutorial_content}}
        return await self.execute(title, context)
