# tests/test_pax_instantly.py
"""Tests for Pax Instantly AI integration."""

import json
from unittest.mock import AsyncMock, MagicMock

import pytest

from devrel_swarm.core.pax import Pax


@pytest.fixture
def mock_instantly():
    client = MagicMock()
    client.add_leads_bulk = AsyncMock(return_value={"added": 3, "skipped": 0})
    client.create_campaign = AsyncMock(
        return_value=MagicMock(
            id="camp_1",
            name="Test",
            status="draft",
        )
    )
    client.list_emails = AsyncMock(return_value=[])
    client.close = AsyncMock()
    return client


@pytest.fixture
def pax_with_instantly(posthog_client, knowledge_base_path, mock_llm_client, mock_instantly):
    return Pax(
        api_client=posthog_client,
        knowledge_base_path=knowledge_base_path,
        llm_client=mock_llm_client,
        instantly_client=mock_instantly,
    )


class TestPaxUploadLeads:
    """Test lead upload from various sources."""

    @pytest.mark.asyncio
    async def test_upload_from_dict_list(self, pax_with_instantly, mock_instantly):
        leads = [
            {"email": "a@co.com", "first_name": "Alice"},
            {"email": "b@co.com", "first_name": "Bob"},
        ]
        mock_instantly.add_leads_bulk = AsyncMock(return_value={"added": 2, "skipped": 0})
        result = await pax_with_instantly.upload_leads("camp_1", leads=leads)
        assert result["total_uploaded"] == 2
        mock_instantly.add_leads_bulk.assert_called_once()

    @pytest.mark.asyncio
    async def test_upload_from_csv(self, pax_with_instantly, mock_instantly, tmp_path):
        csv_file = tmp_path / "leads.csv"
        csv_file.write_text("email,first_name,last_name\na@co.com,Alice,A\nb@co.com,Bob,B\n")
        mock_instantly.add_leads_bulk = AsyncMock(return_value={"added": 2, "skipped": 0})
        result = await pax_with_instantly.upload_leads("camp_1", csv_path=csv_file)
        assert result["total_uploaded"] == 2

    @pytest.mark.asyncio
    async def test_upload_batches_over_1000(self, pax_with_instantly, mock_instantly):
        leads = [{"email": f"user{i}@co.com"} for i in range(1500)]
        mock_instantly.add_leads_bulk = AsyncMock(
            side_effect=[{"added": 1000, "skipped": 0}, {"added": 500, "skipped": 0}]
        )
        pax_with_instantly.BULK_BATCH_SIZE = 1000
        result = await pax_with_instantly.upload_leads("camp_1", leads=leads)
        assert result["total_uploaded"] == 1500
        assert result["batches"] == 2

    @pytest.mark.asyncio
    async def test_upload_from_context(self, pax_with_instantly, mock_instantly):
        mock_instantly.add_leads_bulk = AsyncMock(return_value={"added": 1, "skipped": 0})
        context = {
            "sage_triage": {
                "issues": [
                    {"author": "dev1", "author_email": "dev1@github.com"},
                    {"author": "dev2"},  # no email — should be skipped
                ],
            },
        }
        result = await pax_with_instantly.upload_leads("camp_1", context=context)
        assert result["total_uploaded"] == 1

    @pytest.mark.asyncio
    async def test_upload_no_client(self, posthog_client, knowledge_base_path, mock_llm_client):
        pax = Pax(
            api_client=posthog_client,
            knowledge_base_path=knowledge_base_path,
            llm_client=mock_llm_client,
        )
        result = await pax.upload_leads("camp_1", leads=[{"email": "a@co.com"}])
        assert result["total_uploaded"] == 0


class TestPaxDraftFollowups:
    """Test follow-up email drafting from triaged replies."""

    @pytest.mark.asyncio
    async def test_drafts_for_interested(self, pax_with_instantly, mock_llm_client):
        mock_llm_client.generate = AsyncMock(
            return_value=json.dumps(
                {
                    "subject": "Re: Great to hear from you",
                    "body": "Thanks for your interest! Here's how to get started...",
                }
            )
        )
        replies = [
            {
                "reply_id": "r1",
                "email_id": "e1",
                "category": "interested",
                "body": "Sounds interesting, tell me more",
                "lead_email": "a@co.com",
            },
        ]
        drafts = await pax_with_instantly.draft_followups(replies)
        assert len(drafts) == 1
        assert drafts[0]["category"] == "interested"
        assert drafts[0]["status"] == "pending_approval"

    @pytest.mark.asyncio
    async def test_skips_unsubscribe_and_auto_reply(self, pax_with_instantly):
        replies = [
            {
                "reply_id": "r1",
                "email_id": "e1",
                "category": "unsubscribe",
                "body": "Please remove me",
                "lead_email": "a@co.com",
            },
            {
                "reply_id": "r2",
                "email_id": "e2",
                "category": "auto_reply",
                "body": "Out of office",
                "lead_email": "b@co.com",
            },
        ]
        drafts = await pax_with_instantly.draft_followups(replies)
        assert len(drafts) == 0

    @pytest.mark.asyncio
    async def test_drafts_for_objection(self, pax_with_instantly, mock_llm_client):
        mock_llm_client.generate = AsyncMock(
            return_value=json.dumps(
                {
                    "subject": "Re: Addressing your concern",
                    "body": "I understand your concern about pricing...",
                }
            )
        )
        replies = [
            {
                "reply_id": "r1",
                "email_id": "e1",
                "category": "objection",
                "body": "Too expensive for us",
                "lead_email": "a@co.com",
            },
        ]
        drafts = await pax_with_instantly.draft_followups(replies)
        assert len(drafts) == 1


class TestPaxTriageRepliesExecute:
    """Test the triage_replies path via execute()."""

    @pytest.mark.asyncio
    async def test_execute_triage_replies_type(self, pax_with_instantly):
        result = await pax_with_instantly.execute("Triage email replies and draft follow-ups")
        assert result["agent"] == "pax"
        assert result["asset_type"] == "triage_replies"
