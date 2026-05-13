"""
Overlay renderer — adds visual polish to recorded video segments using FFmpeg.
Renders: step title bar, callout text boxes, step number indicator.
"""

import asyncio
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

# Hard cap on FFmpeg subprocess wall-clock time (seconds). 5 minutes is
# generous for normal overlay encoding but stops a stuck encoder from
# hanging the whole pipeline.
FFMPEG_TIMEOUT_S = 300


async def _communicate_with_timeout(process: asyncio.subprocess.Process):
    """Run ``process.communicate()`` with a hard timeout; kill on timeout."""
    try:
        return await asyncio.wait_for(process.communicate(), timeout=FFMPEG_TIMEOUT_S)
    except asyncio.TimeoutError as exc:
        process.kill()
        await process.wait()
        raise RuntimeError(
            f"FFmpeg subprocess timed out after {FFMPEG_TIMEOUT_S}s; killed"
        ) from exc


@dataclass
class OverlayConfig:
    font_size: int = 32
    title_font_size: int = 48
    font_color: str = "white"
    bg_color: str = "black@0.7"
    padding: int = 20
    title_position: str = "top"
    callout_position: str = "bottom"
    title_display_duration: float = 4.0
    callout_display_duration: float = 0.0


class OverlayRenderer:
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
        output_path = self.output_dir / f"{filename_prefix}_overlaid.mp4"
        filters = []
        filters.append(self._build_title_filter(title, step_number))
        filters.append(self._build_step_indicator(step_number, total_steps))
        if callout_text:
            filters.append(self._build_callout_filter(callout_text))
        filter_chain = ",".join(filters)
        cmd = [
            "ffmpeg",
            "-y",
            "-i",
            str(video_path),
            "-vf",
            filter_chain,
            "-c:v",
            "libx264",
            "-preset",
            "fast",
            "-crf",
            "23",
            "-c:a",
            "copy",
            str(output_path),
        ]
        logger.info(f"Rendering overlays for {filename_prefix}")
        process = await asyncio.create_subprocess_exec(
            *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
        )
        _, stderr = await _communicate_with_timeout(process)
        if process.returncode != 0:
            err_text = stderr.decode()
            logger.error(f"FFmpeg overlay failed: {err_text[:500]}")
            raise RuntimeError(f"FFmpeg overlay rendering failed: {err_text[:200]}")
        logger.info(f"Overlays rendered: {output_path}")
        return output_path

    def _build_title_filter(self, title: str, step_number: int) -> str:
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
        text = text.replace("\\", "\\\\")
        text = text.replace("'", "'\\''")
        text = text.replace(":", "\\:")
        text = text.replace("%", "%%")
        text = text.replace("\n", " ")
        return text
