"""
Video assembler — final FFmpeg pipeline for concatenation and audio merging.
Concatenates step videos, merges TTS audio tracks per step, outputs final .mp4.
"""

import asyncio
import logging
from pathlib import Path

logger = logging.getLogger(__name__)

# Hard cap on FFmpeg subprocess wall-clock time (seconds). 5 minutes is
# generous for normal merge/concat work but stops a stuck encoder from
# hanging the whole pipeline.
FFMPEG_TIMEOUT_S = 300


async def _communicate_with_timeout(process: asyncio.subprocess.Process):
    """Run ``process.communicate()`` with a hard timeout.

    On timeout: send SIGKILL, await the reaper, then raise RuntimeError so
    the surrounding pipeline error path engages and the caller logs the
    failure instead of waiting indefinitely.
    """
    try:
        return await asyncio.wait_for(
            process.communicate(), timeout=FFMPEG_TIMEOUT_S
        )
    except asyncio.TimeoutError as exc:
        process.kill()
        await process.wait()
        raise RuntimeError(
            f"FFmpeg subprocess timed out after {FFMPEG_TIMEOUT_S}s; killed"
        ) from exc


class VideoAssembler:
    def __init__(self, output_dir: Path):
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)

    async def assemble(
        self,
        step_videos: list[Path],
        step_audios: list[Path],
        output_filename: str = "tutorial.mp4",
    ) -> Path:
        if len(step_videos) != len(step_audios):
            raise ValueError(
                f"Mismatch: {len(step_videos)} videos vs {len(step_audios)} audios"
            )
        merged_steps = []
        for i, (video, audio) in enumerate(zip(step_videos, step_audios, strict=True)):
            merged_path = self.output_dir / f"merged_step_{i + 1}.mp4"
            await self._merge_audio_video(video, audio, merged_path)
            merged_steps.append(merged_path)
        final_path = self.output_dir / output_filename
        if len(merged_steps) == 1:
            merged_steps[0].rename(final_path)
        else:
            await self._concatenate_videos(merged_steps, final_path)
        for p in merged_steps:
            if p.exists():
                p.unlink()
        logger.info(f"Final video assembled: {final_path}")
        return final_path

    async def _merge_audio_video(
        self, video_path: Path, audio_path: Path, output_path: Path
    ) -> None:
        cmd = self._build_audio_merge_cmd(video_path, audio_path, output_path)
        logger.info(f"Merging audio+video: {output_path.name}")
        process = await asyncio.create_subprocess_exec(
            *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
        )
        _, stderr = await _communicate_with_timeout(process)
        if process.returncode != 0:
            err_text = stderr.decode()
            logger.error(f"FFmpeg merge failed: {err_text[:500]}")
            raise RuntimeError(f"Audio/video merge failed: {err_text[:200]}")

    async def _concatenate_videos(
        self, video_paths: list[Path], output_path: Path
    ) -> None:
        concat_file = self.output_dir / "concat_list.txt"
        concat_file.write_text(self._build_concat_file_content(video_paths))
        cmd = [
            "ffmpeg", "-y", "-f", "concat", "-safe", "0",
            "-i", str(concat_file), "-c", "copy", str(output_path),
        ]
        logger.info(f"Concatenating {len(video_paths)} steps")
        process = await asyncio.create_subprocess_exec(
            *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
        )
        _, stderr = await _communicate_with_timeout(process)
        concat_file.unlink(missing_ok=True)
        if process.returncode != 0:
            err_text = stderr.decode()
            logger.error(f"FFmpeg concat failed: {err_text[:500]}")
            raise RuntimeError(
                f"Video concatenation failed: {err_text[:200]}"
            )

    def _build_concat_file_content(self, video_paths: list[Path]) -> str:
        lines = [f"file '{path}'" for path in video_paths]
        return "\n".join(lines)

    def _build_audio_merge_cmd(
        self, video_path: Path, audio_path: Path, output_path: Path
    ) -> list[str]:
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
