"""
Browser recorder — manages Playwright browser for screen recording.
Opens URLs, executes user actions (click, type, scroll, wait),
and captures screen recordings at 1920x1080 resolution.
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

    action_type: str
    selector: Optional[str] = None
    value: Optional[str] = None
    delay: float = 0.5


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
        from playwright.async_api import async_playwright

        output_path = self.output_dir / f"{filename_prefix}.webm"
        logger.info(f"Recording step: {filename_prefix} -> {url}")

        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True, slow_mo=self.slow_mo)
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
                remaining = max(0, duration_hint - len(actions) * 0.5)
                if remaining > 0:
                    await asyncio.sleep(remaining)
            finally:
                # Capture video path BEFORE closing context — page.video
                # becomes invalid after context.close() finalizes the recording.
                video_path = await page.video.path() if page.video else None
                await context.close()
                await browser.close()
                if video_path and Path(video_path).exists():
                    Path(video_path).rename(output_path)

        logger.info(f"Step recorded: {output_path}")
        return output_path

    async def _execute_action(self, page, action: BrowserAction) -> None:
        try:
            if action.action_type == "click" and action.selector:
                await page.click(action.selector, timeout=5000)
            elif action.action_type == "type" and action.selector and action.value:
                await page.fill(action.selector, action.value)
            elif action.action_type == "scroll" and action.selector:
                await page.evaluate(
                    f"document.querySelector('{action.selector}')"
                    f"?.scrollIntoView({{behavior: 'smooth'}})"
                )
            elif action.action_type == "hover" and action.selector:
                await page.hover(action.selector, timeout=5000)
            elif action.action_type == "wait":
                await asyncio.sleep(action.delay)
            if action.action_type != "wait":
                await asyncio.sleep(action.delay)
        except Exception as exc:
            logger.warning(f"Action failed ({action.action_type} {action.selector}): {exc}")

    def parse_actions(self, action_dicts: list[dict]) -> list[BrowserAction]:
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
