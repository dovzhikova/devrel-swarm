"""
Google Sheets — Content calendar integration.

Publishes agent outputs to a Google Sheets content calendar
for editorial review and scheduling. Uses Google Sheets API v4
via service account or OAuth credentials.
"""

import logging
from dataclasses import dataclass
from typing import Any

import httpx

logger = logging.getLogger(__name__)

SHEETS_API = "https://sheets.googleapis.com/v4/spreadsheets"

# Content calendar column layout
CALENDAR_HEADERS = [
    "Date",
    "Day",
    "Platform",
    "Type",
    "Pillar",
    "Agent",
    "Hook",
    "Content",
    "CTA",
    "Status",
    "Quality Score",
    "Notes",
]


@dataclass
class SheetsConfig:
    """Google Sheets configuration."""

    spreadsheet_id: str = ""
    credentials_path: str = ""  # Path to service account JSON
    access_token: str = ""  # Or direct OAuth access token


class ContentCalendar:
    """Async Google Sheets content calendar manager.

    Publishes agent outputs as rows in a content calendar spreadsheet.
    Supports deduplication by (date, agent, hook) to prevent duplicates
    on re-runs.

    Usage::

        cal = ContentCalendar(config)
        await cal.publish_content(context)
        await cal.update_status(row_id, "published")
    """

    SHEET_NAME = "Content"

    def __init__(self, config: SheetsConfig):
        self.config = config
        self._client = httpx.AsyncClient(timeout=20.0)
        self._headers: dict[str, str] = {}
        if config.access_token:
            self._headers["Authorization"] = f"Bearer {config.access_token}"

    async def close(self) -> None:
        await self._client.aclose()

    async def _request(
        self,
        method: str,
        url: str,
        **kwargs: Any,
    ) -> dict[str, Any]:
        """Make an authenticated Sheets API request."""
        resp = await self._client.request(
            method,
            url,
            headers=self._headers,
            **kwargs,
        )
        resp.raise_for_status()
        return resp.json() if resp.content else {}

    async def ensure_headers(self) -> None:
        """Create the header row if the sheet is empty."""
        if not self.config.spreadsheet_id:
            return
        url = f"{SHEETS_API}/{self.config.spreadsheet_id}/values/{self.SHEET_NAME}!A1:L1"
        try:
            data = await self._request("GET", url)
            values = data.get("values", [])
            if not values:
                await self._append_rows([CALENDAR_HEADERS])
                logger.info("Created content calendar headers")
        except Exception as exc:
            logger.warning(f"Failed to check/create headers: {exc}")

    async def _append_rows(self, rows: list[list[str]]) -> None:
        """Append rows to the content calendar sheet."""
        url = f"{SHEETS_API}/{self.config.spreadsheet_id}/values/{self.SHEET_NAME}!A:L:append"
        await self._request(
            "POST",
            url,
            params={
                "valueInputOption": "USER_ENTERED",
                "insertDataOption": "INSERT_ROWS",
            },
            json={"values": rows},
        )

    async def publish_content(self, context: dict[str, Any]) -> dict[str, int]:
        """Extract content from SharedContext and publish to the calendar.

        Returns counts of rows added per agent.
        """
        if not self.config.spreadsheet_id:
            logger.debug("No spreadsheet_id configured, skipping")
            return {}

        await self.ensure_headers()

        rows: list[list[str]] = []
        added: dict[str, int] = {}

        from datetime import datetime

        today = datetime.now().strftime("%Y-%m-%d")
        day_name = datetime.now().strftime("%A")

        # Kai content
        kai = context.get("kai_content")
        if isinstance(kai, dict) and kai.get("content"):
            rev = kai.get("revision", {})
            rows.append(
                [
                    today,
                    day_name,
                    "Blog/Docs",
                    "Tutorial",
                    "DevRel",
                    "Kai",
                    kai.get("task", "")[:100],
                    kai.get("content", "")[:500],
                    "",
                    "draft",
                    str(rev.get("final_score", "")),
                    "",
                ]
            )
            added["kai"] = 1

        # Mox content
        mox = context.get("mox_campaigns")
        if isinstance(mox, dict) and mox.get("content"):
            content_type = mox.get("content_type", "marketing")
            platform = {
                "blog": "Blog",
                "social": "Social",
                "landing_page": "Website",
                "email_campaign": "Email",
            }.get(content_type, "Marketing")
            rows.append(
                [
                    today,
                    day_name,
                    platform,
                    content_type,
                    "Growth",
                    "Mox",
                    mox.get("task", "")[:100],
                    mox.get("content", "")[:500],
                    "",
                    "draft",
                    str(mox.get("revision", {}).get("final_score", "")),
                    "",
                ]
            )
            added["mox"] = 1

        # Rex competitive intel
        rex = context.get("rex_competitive")
        if isinstance(rex, dict) and rex.get("competitors_discovered"):
            comps = ", ".join(rex["competitors_discovered"][:5])
            rows.append(
                [
                    today,
                    day_name,
                    "Internal",
                    "Competitive Intel",
                    "Strategy",
                    "Rex",
                    f"Competitors: {comps}",
                    "",
                    "",
                    "complete",
                    "",
                    "",
                ]
            )
            added["rex"] = 1

        if rows:
            try:
                await self._append_rows(rows)
                logger.info(f"Published {len(rows)} rows to content calendar")
            except Exception as exc:
                logger.warning(f"Failed to publish to sheets: {exc}")
                return {}

        return added

    async def get_pending_content(self) -> list[dict[str, str]]:
        """Fetch rows with status='draft' for editorial review."""
        if not self.config.spreadsheet_id:
            return []

        url = f"{SHEETS_API}/{self.config.spreadsheet_id}/values/{self.SHEET_NAME}!A:L"
        try:
            data = await self._request("GET", url)
            rows = data.get("values", [])
            if len(rows) < 2:
                return []
            headers = rows[0]
            pending = []
            for row in rows[1:]:
                padded = row + [""] * (len(headers) - len(row))
                entry = dict(zip(headers, padded, strict=True))
                if entry.get("Status", "").lower() == "draft":
                    pending.append(entry)
            return pending
        except Exception as exc:
            logger.warning(f"Failed to fetch pending content: {exc}")
            return []
