# tests/test_atlas_replies.py
"""Tests for Atlas Instantly integration — Stage 7 and review-replies."""

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from devrel_swarm.core.atlas import Atlas, SharedContext, process_draft


class TestSharedContextInstantly:
    """Test new SharedContext fields and load()."""

    def test_new_fields_default_empty(self):
        ctx = SharedContext(week_of="2026-W12")
        assert ctx.instantly_campaigns == {}
        assert ctx.instantly_analytics == {}
        assert ctx.instantly_replies == {}

    def test_to_dict_includes_instantly_fields(self):
        ctx = SharedContext(
            week_of="2026-W12",
            instantly_analytics={"total_sent": 100},
        )
        d = ctx.to_dict()
        assert "instantly_campaigns" in d
        assert "instantly_analytics" in d
        assert "instantly_replies" in d
        assert d["instantly_analytics"]["total_sent"] == 100

    def test_save_includes_instantly_fields(self, tmp_path):
        ctx = SharedContext(
            week_of="2026-W12",
            instantly_analytics={"total_sent": 50},
        )
        ctx.save(tmp_path)
        saved = json.loads((tmp_path / "context_2026-W12.json").read_text())
        assert saved["instantly_analytics"]["total_sent"] == 50

    def test_load_most_recent(self, tmp_path):
        (tmp_path / "context_2026-W11.json").write_text(json.dumps(
            {"week_of": "2026-W11", "sage_triage": {}, "echo_social": {},
             "iris_themes": {}, "nova_experiments": {}, "kai_content": {},
             "vox_video": {}, "dex_docs": {}, "rex_competitive": {},
             "pax_sales": {}, "mox_campaigns": {}, "okr_progress": {},
             "instantly_campaigns": {}, "instantly_analytics": {},
             "instantly_replies": {"drafts": [{"id": "old"}]}},
        ))
        (tmp_path / "context_2026-W12.json").write_text(json.dumps(
            {"week_of": "2026-W12", "sage_triage": {}, "echo_social": {},
             "iris_themes": {}, "nova_experiments": {}, "kai_content": {},
             "vox_video": {}, "dex_docs": {}, "rex_competitive": {},
             "pax_sales": {}, "mox_campaigns": {}, "okr_progress": {},
             "instantly_campaigns": {}, "instantly_analytics": {},
             "instantly_replies": {"drafts": [{"id": "new"}]}},
        ))
        ctx = SharedContext.load(tmp_path)
        assert ctx.week_of == "2026-W12"
        assert ctx.instantly_replies["drafts"][0]["id"] == "new"

    def test_load_empty_dir(self, tmp_path):
        ctx = SharedContext.load(tmp_path)
        assert ctx.week_of != ""
        assert ctx.instantly_replies == {}

    def test_load_nonexistent_dir(self, tmp_path):
        ctx = SharedContext.load(tmp_path / "nonexistent")
        assert ctx.instantly_replies == {}


class TestAtlasStage7:
    """Test Stage 7 Instantly sync in weekly cycle."""

    @pytest.mark.asyncio
    async def test_stage7_delegates_to_mox_and_pax(
        self, posthog_client, knowledge_base_path, mock_llm_client, tmp_path,
    ):
        mock_instantly = MagicMock()
        mock_instantly.close = AsyncMock()

        atlas = Atlas(
            api_client=posthog_client,
            knowledge_base_path=knowledge_base_path,
            llm_client=mock_llm_client,
            instantly_client=mock_instantly,
            archive_dir=tmp_path / "archive",
        )
        atlas.BASE_DELAY = 0.001

        for agent in atlas._agents.values():
            agent.execute = AsyncMock(return_value={
                "agent": "mock", "status": "ok",
            })

        await atlas.run_weekly_cycle()

        mox_calls = [
            c for c in atlas.mox.execute.call_args_list
            if "analytics" in str(c).lower() or "instantly" in str(c).lower()
        ]
        pax_calls = [
            c for c in atlas.pax.execute.call_args_list
            if "triage" in str(c).lower() or "replies" in str(c).lower()
        ]
        assert len(mox_calls) >= 1, "Mox should be delegated analytics pull"
        assert len(pax_calls) >= 1, "Pax should be delegated reply triage"

    @pytest.mark.asyncio
    async def test_stage7_skipped_without_client(
        self, posthog_client, knowledge_base_path, mock_llm_client, tmp_path,
    ):
        atlas = Atlas(
            api_client=posthog_client,
            knowledge_base_path=knowledge_base_path,
            llm_client=mock_llm_client,
            archive_dir=tmp_path / "archive",
        )
        atlas.BASE_DELAY = 0.001

        for agent in atlas._agents.values():
            agent.execute = AsyncMock(return_value={"agent": "mock", "status": "ok"})

        ctx = await atlas.run_weekly_cycle()
        assert ctx.instantly_analytics == {}


class TestAtlasOKRInstantly:
    """Test OKR compilation includes Instantly metrics."""

    def test_okr_includes_email_metrics(
        self, posthog_client, knowledge_base_path, mock_llm_client,
    ):
        atlas = Atlas(
            api_client=posthog_client,
            knowledge_base_path=knowledge_base_path,
            llm_client=mock_llm_client,
        )
        atlas.context = SharedContext(
            week_of="2026-W12",
            instantly_analytics={
                "total_sent": 100, "total_opened": 50,
                "total_replied": 10, "avg_reply_rate": 0.1,
            },
            instantly_replies={"drafts": [{"id": "d1"}, {"id": "d2"}]},
        )
        okrs = atlas._compile_okrs()
        assert okrs["emails_sent"] == 100
        assert okrs["emails_replied"] == 10
        assert okrs["followups_pending"] == 2


class TestReviewRepliesCLI:
    """Test --review-replies interactive flow."""

    @pytest.mark.asyncio
    async def test_review_replies_loads_context(self, tmp_path):
        (tmp_path / "context_2026-W12.json").write_text(json.dumps({
            "week_of": "2026-W12", "sage_triage": {}, "echo_social": {},
            "iris_themes": {}, "nova_experiments": {}, "kai_content": {},
            "vox_video": {}, "dex_docs": {}, "rex_competitive": {},
            "pax_sales": {}, "mox_campaigns": {}, "okr_progress": {},
            "instantly_campaigns": {}, "instantly_analytics": {},
            "instantly_replies": {
                "drafts": [
                    {"reply_id": "r1", "email_id": "e1",
                     "draft_subject": "Re: Hello",
                     "draft_body": "Thanks for your interest!",
                     "category": "interested",
                     "status": "pending_approval"},
                ]
            },
        }))
        ctx = SharedContext.load(tmp_path)
        drafts = ctx.instantly_replies.get("drafts", [])
        pending = [d for d in drafts if d.get("status") == "pending_approval"]
        assert len(pending) == 1

    @pytest.mark.asyncio
    async def test_review_approve_sends_reply(self):
        mock_instantly = MagicMock()
        mock_instantly.reply_to_email = AsyncMock(return_value={"status": "sent"})

        draft = {
            "reply_id": "r1", "email_id": "e1",
            "draft_subject": "Re: Hello", "draft_body": "Thanks!",
            "category": "interested", "status": "pending_approval",
        }

        with patch("builtins.input", return_value="a"):
            result = await process_draft(draft, mock_instantly)
            assert result == "approved"
            mock_instantly.reply_to_email.assert_called_once()

    @pytest.mark.asyncio
    async def test_review_skip_leaves_pending(self):
        mock_instantly = MagicMock()

        draft = {
            "reply_id": "r1", "email_id": "e1",
            "draft_subject": "Re: Hello", "draft_body": "Thanks!",
            "category": "interested", "status": "pending_approval",
        }

        with patch("builtins.input", return_value="s"):
            result = await process_draft(draft, mock_instantly)
            assert result == "skipped"
            mock_instantly.reply_to_email.assert_not_called()

    @pytest.mark.asyncio
    async def test_review_reject_discards(self):
        mock_instantly = MagicMock()

        draft = {
            "reply_id": "r1", "email_id": "e1",
            "draft_subject": "Re: Hello", "draft_body": "Thanks!",
            "category": "interested", "status": "pending_approval",
        }

        with patch("builtins.input", return_value="r"):
            result = await process_draft(draft, mock_instantly)
            assert result == "rejected"
            mock_instantly.reply_to_email.assert_not_called()
