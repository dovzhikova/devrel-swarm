"""
Desktop recorder — captures desktop app sessions using FFmpeg screen recording
and PyAutoGUI for mouse/keyboard automation.

Use this when the tutorial target is a native desktop app rather than a browser.
"""

import asyncio
import logging
import platform
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

VALID_DESKTOP_ACTIONS = {"click", "type", "wait", "scroll", "move", "hotkey", "screenshot"}


@dataclass
class DesktopAction:
    """A single desktop automation action."""

    action_type: str  # "click", "type", "wait", "scroll", "move", "hotkey", "screenshot"
    x: Optional[int] = None  # screen x coordinate
    y: Optional[int] = None  # screen y coordinate
    value: Optional[str] = None  # text to type or hotkey combo (e.g. "command+c")
    delay: float = 0.5  # seconds to wait after action


def _get_ffmpeg_input_format() -> tuple[str, str]:
    """Return FFmpeg input format and device for the current platform."""
    system = platform.system()
    if system == "Darwin":
        return "avfoundation", "1:none"  # screen:audio — "1" is main display
    elif system == "Linux":
        return "x11grab", ":0.0"
    elif system == "Windows":
        return "gdigrab", "desktop"
    raise RuntimeError(f"Unsupported platform for screen recording: {system}")


class DesktopRecorder:
    """Records desktop sessions using FFmpeg screen capture + PyAutoGUI automation."""

    def __init__(
        self,
        output_dir: Path,
        width: int = 1920,
        height: int = 1080,
        framerate: int = 30,
    ):
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.width = width
        self.height = height
        self.framerate = framerate
        self._has_ffmpeg = shutil.which("ffmpeg") is not None
        self._has_pyautogui = self._check_pyautogui()

    @staticmethod
    def _check_pyautogui() -> bool:
        try:
            import pyautogui  # noqa: F401
            return True
        except ImportError:
            return False

    async def record_step(
        self,
        actions: list[DesktopAction],
        filename_prefix: str,
        duration_hint: float = 10.0,
        app_name: Optional[str] = None,
    ) -> Path:
        """Record a desktop step with FFmpeg screen capture.

        Args:
            actions: List of desktop actions to perform during recording.
            filename_prefix: Output filename prefix.
            duration_hint: Minimum recording duration in seconds.
            app_name: Optional app name to bring to foreground before recording.

        Returns:
            Path to recorded .mp4 file.
        """
        if not self._has_ffmpeg:
            raise RuntimeError("FFmpeg not found — required for desktop recording")

        output_path = self.output_dir / f"{filename_prefix}.mp4"

        # Bring app to foreground if specified
        if app_name:
            await self._activate_app(app_name)
            await asyncio.sleep(1.0)  # wait for app to come forward

        # Start FFmpeg recording in background
        ffmpeg_process = await self._start_recording(output_path, duration_hint)

        try:
            # Wait a moment for recording to begin
            await asyncio.sleep(0.5)

            # Execute actions
            for action in actions:
                await self._execute_action(action)

            # Hold for remaining duration
            elapsed = sum(a.delay for a in actions) + 0.5
            remaining = max(0, duration_hint - elapsed)
            if remaining > 0:
                await asyncio.sleep(remaining)

        finally:
            # Stop recording
            ffmpeg_process.terminate()
            _stdout, stderr = await ffmpeg_process.communicate()
            # FFmpeg returns non-zero when terminated mid-encode (rc=255 on SIGTERM
            # is expected); log only when we have a genuine error code with stderr.
            if ffmpeg_process.returncode not in (0, -15, 255) and stderr:
                logger.error(
                    "Desktop recorder FFmpeg failed (rc=%d). stderr:\n%s",
                    ffmpeg_process.returncode,
                    stderr.decode(errors="replace"),
                )

        logger.info(f"Desktop step recorded: {output_path}")
        return output_path

    async def _start_recording(
        self, output_path: Path, duration: float
    ) -> asyncio.subprocess.Process:
        """Start FFmpeg screen recording as a background process."""
        input_format, input_device = _get_ffmpeg_input_format()

        cmd = [
            "ffmpeg", "-y",
            "-f", input_format,
        ]

        # Platform-specific options
        if input_format == "avfoundation":
            cmd.extend(["-framerate", str(self.framerate)])
            cmd.extend(["-video_size", f"{self.width}x{self.height}"])
            cmd.extend(["-capture_cursor", "1"])
        elif input_format == "x11grab":
            cmd.extend(["-framerate", str(self.framerate)])
            cmd.extend(["-video_size", f"{self.width}x{self.height}"])
        elif input_format == "gdigrab":
            cmd.extend(["-framerate", str(self.framerate)])

        cmd.extend(["-i", input_device])
        cmd.extend(["-t", str(duration + 2)])  # extra buffer
        cmd.extend(["-c:v", "libx264", "-preset", "ultrafast", "-crf", "23"])
        cmd.extend([str(output_path)])

        logger.info(f"Starting desktop recording: {output_path.name}")
        process = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.PIPE,
        )
        return process

    async def _execute_action(self, action: DesktopAction) -> None:
        """Execute a desktop automation action using PyAutoGUI."""
        if not self._has_pyautogui:
            logger.warning("PyAutoGUI not available — skipping action")
            await asyncio.sleep(action.delay)
            return

        import pyautogui
        pyautogui.PAUSE = 0.1  # small pause between pyautogui calls

        try:
            if action.action_type == "click" and action.x is not None and action.y is not None:
                pyautogui.click(action.x, action.y)
            elif action.action_type == "type" and action.value:
                pyautogui.typewrite(action.value, interval=0.05)
            elif action.action_type == "move" and action.x is not None and action.y is not None:
                pyautogui.moveTo(action.x, action.y, duration=0.3)
            elif action.action_type == "scroll":
                clicks = int(action.value) if action.value else -3
                pyautogui.scroll(clicks)
            elif action.action_type == "hotkey" and action.value:
                keys = action.value.split("+")
                pyautogui.hotkey(*keys)
            elif action.action_type == "wait":
                pass  # delay handled below
            elif action.action_type == "screenshot":
                # Take a screenshot (useful for debugging)
                shot_path = self.output_dir / f"debug_{action.value or 'shot'}.png"
                pyautogui.screenshot(str(shot_path))

            await asyncio.sleep(action.delay)

        except Exception as exc:
            logger.warning(f"Desktop action failed ({action.action_type}): {exc}")
            await asyncio.sleep(action.delay)

    async def _activate_app(self, app_name: str) -> None:
        """Bring a desktop app to the foreground."""
        system = platform.system()
        try:
            if system == "Darwin":
                subprocess.run(
                    ["osascript", "-e", f'tell application "{app_name}" to activate'],
                    capture_output=True, timeout=5,
                )
            elif system == "Linux":
                subprocess.run(
                    ["wmctrl", "-a", app_name],
                    capture_output=True, timeout=5,
                )
            elif system == "Windows":
                # PowerShell approach
                subprocess.run(
                    ["powershell", "-Command",
                     f"(New-Object -ComObject WScript.Shell).AppActivate('{app_name}')"],
                    capture_output=True, timeout=5,
                )
        except Exception as exc:
            logger.warning(f"Failed to activate app '{app_name}': {exc}")

    def parse_actions(self, action_dicts: list[dict]) -> list[DesktopAction]:
        """Convert raw action dicts to DesktopAction objects."""
        actions = []
        for d in action_dicts:
            action_type = d.get("type", "")
            if action_type not in VALID_DESKTOP_ACTIONS:
                logger.warning(f"Skipping unknown desktop action type: {action_type}")
                continue
            actions.append(
                DesktopAction(
                    action_type=action_type,
                    x=d.get("x"),
                    y=d.get("y"),
                    value=d.get("value"),
                    delay=d.get("delay", 0.5),
                )
            )
        return actions
