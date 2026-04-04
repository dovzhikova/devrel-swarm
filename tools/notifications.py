"""
Notifications — Telegram and email delivery for agent outputs.

Provides async delivery of content digests and alerts to configured channels.
"""

import asyncio
import functools
import logging
import smtplib
import ssl
from dataclasses import dataclass
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from typing import Any

import httpx

logger = logging.getLogger(__name__)

TELEGRAM_API = "https://api.telegram.org"


@dataclass
class NotificationConfig:
    """Notification channel configuration."""

    telegram_bot_token: str = ""
    telegram_chat_id: str = ""
    email_smtp_host: str = "smtp.gmail.com"
    email_smtp_port: int = 587
    email_sender: str = ""
    email_password: str = ""  # App password for Gmail
    email_recipients: list[str] | None = None


class NotificationService:
    """Async notification delivery to Telegram and email.

    Usage::

        svc = NotificationService(config)
        await svc.send_telegram("Pipeline complete!")
        await svc.send_email("Weekly Report", html_body)
        await svc.send_digest(context)  # Auto-formats and sends both
    """

    def __init__(self, config: NotificationConfig):
        self.config = config
        self._client = httpx.AsyncClient(timeout=15.0)

    async def close(self) -> None:
        await self._client.aclose()

    # -- Telegram ---------------------------------------------------------

    async def send_telegram(self, message: str, parse_mode: str = "Markdown") -> bool:
        """Send a message to the configured Telegram chat."""
        if not self.config.telegram_bot_token or not self.config.telegram_chat_id:
            logger.debug("Telegram not configured, skipping")
            return False

        try:
            url = (
                f"{TELEGRAM_API}/bot{self.config.telegram_bot_token}/sendMessage"
            )
            resp = await self._client.post(url, json={
                "chat_id": self.config.telegram_chat_id,
                "text": message[:4096],
                "parse_mode": parse_mode,
            })
            resp.raise_for_status()
            logger.info("Telegram message sent")
            return True
        except Exception as exc:
            logger.warning(f"Telegram send failed: {exc}")
            return False

    # -- Email ------------------------------------------------------------

    async def send_email(
        self,
        subject: str,
        html_body: str,
        recipients: list[str] | None = None,
    ) -> bool:
        """Send an HTML email via SMTP."""
        cfg = self.config
        if not cfg.email_sender or not cfg.email_password:
            logger.debug("Email not configured, skipping")
            return False

        to_addrs = recipients or cfg.email_recipients or []
        if not to_addrs:
            logger.warning("No email recipients configured")
            return False

        msg = MIMEMultipart("alternative")
        msg["Subject"] = subject
        msg["From"] = cfg.email_sender
        msg["To"] = ", ".join(to_addrs)
        msg.attach(MIMEText(html_body, "html"))

        def _send_sync() -> None:
            ctx = ssl.create_default_context()
            with smtplib.SMTP(cfg.email_smtp_host, cfg.email_smtp_port) as server:
                server.starttls(context=ctx)
                server.login(cfg.email_sender, cfg.email_password)
                server.sendmail(cfg.email_sender, to_addrs, msg.as_string())

        try:
            loop = asyncio.get_running_loop()
            await loop.run_in_executor(None, _send_sync)
            logger.info(f"Email sent to {to_addrs}")
            return True
        except Exception as exc:
            logger.warning(f"Email send failed: {exc}")
            return False

    # -- Digest -----------------------------------------------------------

    async def send_digest(
        self, context: dict[str, Any], mode: str = "daily",
    ) -> dict[str, bool]:
        """Format and send a content digest from SharedContext.

        Args:
            context: SharedContext.to_dict() output.
            mode: "daily" for brief content summary, "weekly" for full report.

        Returns:
            Dict with "telegram" and "email" delivery status.
        """
        if mode == "weekly":
            telegram_msg = self._format_weekly_telegram(context)
            email_html = self._format_weekly_email(context)
            subject = f"Weekly Agent Report — {context.get('week_of', 'unknown')}"
        else:
            telegram_msg = self._format_daily_telegram(context)
            email_html = self._format_daily_email(context)
            subject = "Daily Content Digest"

        tg_ok = await self.send_telegram(telegram_msg)
        email_ok = await self.send_email(subject, email_html)
        return {"telegram": tg_ok, "email": email_ok}

    def _format_daily_telegram(self, ctx: dict[str, Any]) -> str:
        """Format a brief daily Telegram message."""
        lines = [f"📊 *Daily Digest — {ctx.get('week_of', '')}*\n"]

        if ctx.get("kai_content"):
            task = ctx["kai_content"].get("task", "content")[:80]
            lines.append(f"✍️ Kai: {task}")

        if ctx.get("echo_social"):
            total = ctx["echo_social"].get("total_mentions", 0)
            lines.append(f"👂 Echo: {total} social mentions")

        if ctx.get("sage_triage"):
            issues = ctx["sage_triage"].get("issues", [])
            lines.append(f"🔍 Sage: {len(issues)} issues triaged")

        okr = ctx.get("okr_progress", {})
        audit = okr.get("brand_audit", {})
        if audit:
            score = audit.get("overall_score", "?")
            lines.append(f"🛡️ Sentinel: brand score {score}/100")

        return "\n".join(lines)

    def _format_weekly_telegram(self, ctx: dict[str, Any]) -> str:
        """Format a full weekly Telegram report."""
        lines = [f"📋 *Weekly Report — {ctx.get('week_of', '')}*\n"]

        okr = ctx.get("okr_progress", {})
        lines.append(f"Content produced: {'✅' if okr.get('content_produced') else '❌'}")
        lines.append(f"Issues triaged: {okr.get('issues_triaged', 0)}")
        lines.append(f"Social mentions: {okr.get('social_mentions_found', 0)}")
        lines.append(f"Themes found: {okr.get('themes_identified', 0)}")
        lines.append(f"Experiments designed: {okr.get('experiments_designed', 0)}")
        lines.append(f"Competitors analyzed: {okr.get('competitors_analyzed', 0)}")

        health = okr.get("pre_health", {})
        if health:
            lines.append(f"\n🏥 Health: {health.get('overall_score', '?')}/100")
            for alert in health.get("alerts", [])[:3]:
                lines.append(f"  ⚠️ {alert}")

        return "\n".join(lines)

    def _format_daily_email(self, ctx: dict[str, Any]) -> str:
        """Format a daily email digest as HTML."""
        sections = []

        if ctx.get("kai_content"):
            kai = ctx["kai_content"]
            rev = kai.get("revision", {})
            sections.append(f"""
            <div style="border-left:4px solid #4CAF50;padding-left:16px;margin:16px 0">
                <h3 style="margin:0">✍️ Kai — Content</h3>
                <p><b>Task:</b> {kai.get('task', 'N/A')[:100]}</p>
                <p><b>Quality score:</b> {rev.get('final_score', 'N/A')}/10
                   ({rev.get('rounds', 0)} revision rounds)</p>
            </div>""")

        if ctx.get("echo_social"):
            echo = ctx["echo_social"]
            sections.append(f"""
            <div style="border-left:4px solid #2196F3;padding-left:16px;margin:16px 0">
                <h3 style="margin:0">👂 Echo — Social</h3>
                <p><b>Mentions:</b> {echo.get('total_mentions', 0)}</p>
            </div>""")

        body = "\n".join(sections) if sections else "<p>No content generated today.</p>"
        return f"""
        <html><body style="font-family:system-ui,sans-serif;max-width:600px;margin:0 auto">
            <h2>Daily Content Digest</h2>
            {body}
        </body></html>"""

    def _format_weekly_email(self, ctx: dict[str, Any]) -> str:
        """Format a weekly email report as HTML."""
        okr = ctx.get("okr_progress", {})
        audit = okr.get("brand_audit", {})

        return f"""
        <html><body style="font-family:system-ui,sans-serif;max-width:600px;margin:0 auto">
            <h2>Weekly Agent Report — {ctx.get('week_of', '')}</h2>
            <table style="width:100%;border-collapse:collapse">
                <tr><td style="padding:8px;border-bottom:1px solid #eee"><b>Content produced</b></td>
                    <td style="padding:8px;border-bottom:1px solid #eee">{'✅' if okr.get('content_produced') else '❌'}</td></tr>
                <tr><td style="padding:8px;border-bottom:1px solid #eee"><b>Issues triaged</b></td>
                    <td style="padding:8px;border-bottom:1px solid #eee">{okr.get('issues_triaged', 0)}</td></tr>
                <tr><td style="padding:8px;border-bottom:1px solid #eee"><b>Social mentions</b></td>
                    <td style="padding:8px;border-bottom:1px solid #eee">{okr.get('social_mentions_found', 0)}</td></tr>
                <tr><td style="padding:8px;border-bottom:1px solid #eee"><b>Themes identified</b></td>
                    <td style="padding:8px;border-bottom:1px solid #eee">{okr.get('themes_identified', 0)}</td></tr>
                <tr><td style="padding:8px;border-bottom:1px solid #eee"><b>Experiments</b></td>
                    <td style="padding:8px;border-bottom:1px solid #eee">{okr.get('experiments_designed', 0)}</td></tr>
                <tr><td style="padding:8px;border-bottom:1px solid #eee"><b>Brand audit score</b></td>
                    <td style="padding:8px;border-bottom:1px solid #eee">{audit.get('overall_score', 'N/A')}/100</td></tr>
            </table>
        </body></html>"""
