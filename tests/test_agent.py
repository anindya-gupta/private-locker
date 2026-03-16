"""Aggressive tests for VaultAgent — local resolution, reminders, document store with duplicates, search."""

from __future__ import annotations

from datetime import datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from vault.agent import AgentResponse, VaultAgent
from vault.config import VaultConfig
from vault.security.encryption import derive_all_keys
from vault.security.session import Session


TEST_PASSWORD = "test-master-password-123"
TEST_SALT = b"\x00" * 32


@pytest.fixture
def agent(tmp_path, mock_llm, mock_vector_store):
    """Create a fully wired agent with real DB but mocked LLM and vector store."""
    config = VaultConfig(vault_dir=tmp_path)
    config.ensure_dirs()

    keys = derive_all_keys(TEST_PASSWORD, TEST_SALT)
    session = MagicMock(spec=Session)
    session.is_locked = False
    session.keys = keys

    agent = VaultAgent(config, session)
    agent.db.open()
    agent.db.initialize_schema()
    agent.llm = mock_llm
    agent.vector_store = mock_vector_store
    yield agent
    agent.db.close()


class TestLocalResolution:
    """Test that common queries are answered locally without LLM."""

    @pytest.mark.asyncio
    async def test_lock_vault(self, agent):
        resp = agent._try_local_resolution("lock", agent.session.keys)
        assert resp is not None
        assert "locked" in resp.text.lower()

    @pytest.mark.asyncio
    async def test_list_documents_empty(self, agent):
        resp = agent._try_local_resolution("show my documents", agent.session.keys)
        assert "No documents" in resp.text

    @pytest.mark.asyncio
    async def test_list_documents_with_data(self, agent):
        agent.db.store_document("Aadhaar", "identity", agent.session.keys.db_key)
        resp = agent._try_local_resolution("show my documents", agent.session.keys)
        assert "Aadhaar" in resp.text

    @pytest.mark.asyncio
    async def test_remember_fact(self, agent):
        resp = agent._try_local_resolution("remember my blood type is O+", agent.session.keys)
        assert resp is not None
        assert "blood type" in resp.text
        assert "o+" in resp.text.lower()

    @pytest.mark.asyncio
    async def test_recall_fact(self, agent):
        agent.memory.remember("blood type", "O+", agent.session.keys.db_key)
        resp = agent._try_local_resolution("what is my blood type?", agent.session.keys)
        assert resp is not None
        assert "O+" in resp.text

    @pytest.mark.asyncio
    async def test_save_birthday(self, agent):
        resp = agent._try_local_resolution("save birthday: John, March 15", agent.session.keys)
        assert resp is not None
        assert "John" in resp.text

    @pytest.mark.asyncio
    async def test_list_birthdays_empty(self, agent):
        resp = agent._try_local_resolution("show birthdays", agent.session.keys)
        assert "No birthdays" in resp.text

    @pytest.mark.asyncio
    async def test_reminder_triggers_fall_through(self, agent):
        """Reminder triggers should NOT be locally resolved — they fall through to LLM."""
        resp = agent._try_local_resolution("remind me to renew passport in 30 days", agent.session.keys)
        assert resp is None

    @pytest.mark.asyncio
    async def test_delete_triggers_fall_through(self, agent):
        resp = agent._try_local_resolution("delete my netflix credentials", agent.session.keys)
        assert resp is None

    @pytest.mark.asyncio
    async def test_doc_query_triggers_fall_through(self, agent):
        resp = agent._try_local_resolution("show me my latest eye prescription", agent.session.keys)
        assert resp is None

    @pytest.mark.asyncio
    async def test_credential_lookup(self, agent):
        agent.cred_manager.store("netflix", agent.session.keys.cred_key, username="user@test.com", password="pass123")
        resp = agent._try_local_resolution("what is my netflix password", agent.session.keys)
        assert resp is not None
        assert "pass123" in resp.text


class TestStoreDocument:
    """Test document storage with duplicates, force, metadata."""

    @pytest.mark.asyncio
    async def test_basic_store(self, agent):
        resp = await agent._handle_store_document(
            "My Aadhaar", b"aadhaar content", "aadhaar.txt",
            agent.session.keys, force=False,
        )
        assert "Stored" in resp.text
        assert "identity" in resp.text.lower() or "Aadhaar" in resp.text

    @pytest.mark.asyncio
    async def test_duplicate_hash_detected(self, agent):
        await agent._handle_store_document("First", b"same content", "a.txt", agent.session.keys, force=False)
        resp = await agent._handle_store_document("Second", b"same content", "b.txt", agent.session.keys, force=False)
        assert resp.data and resp.data.get("duplicate_warning") is True

    @pytest.mark.asyncio
    async def test_duplicate_force_upload(self, agent):
        await agent._handle_store_document("First", b"same content", "a.txt", agent.session.keys, force=False)
        resp = await agent._handle_store_document("Second", b"same content", "b.txt", agent.session.keys, force=True)
        assert "Stored" in resp.text

    @pytest.mark.asyncio
    async def test_suggested_name_fallback(self, agent):
        """When message is blank and LLM suggests a name, agent should use it."""
        agent.llm.extract_document_metadata.return_value = {
            "suggested_name": "Eye Prescription - Dr. Bansal",
            "sub_category": "eye",
            "doctor": "Bansal",
            "doc_date": "2026-03-15",
            "keywords": ["eye", "prescription"],
            "summary": "Eye prescription from Dr Bansal",
            "expiry_date": None,
        }
        resp = await agent._handle_store_document(
            "", b"prescription content for eye", "scan001.txt",
            agent.session.keys, force=False,
        )
        assert "Eye Prescription" in resp.text

    @pytest.mark.asyncio
    async def test_user_name_overrides_suggested(self, agent):
        agent.llm.extract_document_metadata.return_value = {
            "suggested_name": "LLM Name",
            "sub_category": None, "doctor": None, "doc_date": None,
            "keywords": [], "summary": None, "expiry_date": None,
        }
        resp = await agent._handle_store_document(
            "My Custom Name", b"content", "file.txt",
            agent.session.keys, force=False,
        )
        assert "My Custom Name" in resp.text

    @pytest.mark.asyncio
    async def test_expiry_tag_stored(self, agent):
        agent.llm.extract_document_metadata.return_value = {
            "suggested_name": None, "sub_category": None, "doctor": None,
            "doc_date": None, "keywords": [], "summary": None,
            "expiry_date": "2027-06-15",
        }
        resp = await agent._handle_store_document(
            "Passport", b"Passport content. Valid until: 15/06/2027",
            "passport.txt", agent.session.keys, force=False,
        )
        docs = agent.db.list_documents(agent.session.keys.db_key)
        assert len(docs) == 1
        tags = docs[0]["tags"]
        expiry_tags = [t for t in tags if t.startswith("expiry:")]
        assert len(expiry_tags) >= 1


class TestReminders:
    @pytest.mark.asyncio
    async def test_set_reminder_relative_days(self, agent):
        resp = await agent._handle_set_reminder(
            "remind me to renew passport in 30 days", {}, agent.session.keys,
        )
        assert "Renew passport" in resp.text
        expected_date = (datetime.now() + timedelta(days=30)).strftime("%Y-%m-%d")
        assert expected_date in resp.text

    @pytest.mark.asyncio
    async def test_set_reminder_relative_weeks(self, agent):
        resp = await agent._handle_set_reminder(
            "remind me to call doctor in 2 weeks", {}, agent.session.keys,
        )
        assert "Call doctor" in resp.text

    @pytest.mark.asyncio
    async def test_set_reminder_relative_months(self, agent):
        resp = await agent._handle_set_reminder(
            "remind me to get eye checkup in 6 months", {}, agent.session.keys,
        )
        assert "Eye checkup" in resp.text or "eye checkup" in resp.text.lower()

    @pytest.mark.asyncio
    async def test_set_reminder_with_date(self, agent):
        resp = await agent._handle_set_reminder(
            "remind me to file taxes on March 31", {}, agent.session.keys,
        )
        assert "File taxes" in resp.text or "file taxes" in resp.text.lower()

    @pytest.mark.asyncio
    async def test_set_reminder_tomorrow(self, agent):
        resp = await agent._handle_set_reminder(
            "remind me to buy groceries on tomorrow", {}, agent.session.keys,
        )
        expected = (datetime.now() + timedelta(days=1)).strftime("%Y-%m-%d")
        assert expected in resp.text

    @pytest.mark.asyncio
    async def test_list_reminders_empty(self, agent):
        resp = await agent._handle_list_reminders("show my reminders", {}, agent.session.keys)
        assert "no active reminders" in resp.text.lower()

    @pytest.mark.asyncio
    async def test_list_reminders_with_data(self, agent):
        agent.db.store_reminder("Test Reminder", "2026-04-01", agent.session.keys.db_key)
        resp = await agent._handle_list_reminders("show my reminders", {}, agent.session.keys)
        assert "Test Reminder" in resp.text

    @pytest.mark.asyncio
    async def test_follow_up_pattern(self, agent):
        resp = await agent._handle_set_reminder(
            "follow up on passport renewal in 15 days", {}, agent.session.keys,
        )
        assert "passport renewal" in resp.text.lower()


class TestDocumentRetrieval:
    """Test the multi-document retrieval that was previously broken."""

    @pytest.mark.asyncio
    async def test_single_doc_returns_file(self, agent):
        data = b"eye prescription content"
        ref = agent.file_vault.store(data, agent.session.keys.file_key, "eye_rx.pdf")
        agent.db.store_document("Eye Prescription", "medical", agent.session.keys.db_key,
                               file_ref=ref, tags=["medical", "sub:eye"])
        resp = await agent._handle_retrieve_document(
            "download eye prescription", {"document": "eye prescription"}, agent.session.keys,
        )
        assert resp.file_data == data

    @pytest.mark.asyncio
    async def test_multiple_docs_lists_them(self, agent):
        """When multiple docs match, should list them all (the bug that was fixed)."""
        for name in ["Eye Prescription Jan 2025", "Eye Prescription Mar 2026"]:
            ref = agent.file_vault.store(b"content", agent.session.keys.file_key, "rx.pdf")
            agent.db.store_document(name, "medical", agent.session.keys.db_key,
                                   file_ref=ref, tags=["medical", "sub:eye"])
        resp = await agent._handle_retrieve_document(
            "share me my eye prescriptions", {"document": "eye prescriptions"}, agent.session.keys,
        )
        assert "2 matching documents" in resp.text or "Found 2" in resp.text

    @pytest.mark.asyncio
    async def test_latest_doc_returned(self, agent):
        for i, (name, date_tag) in enumerate([
            ("Eye Rx Old", "date:2024-01-01"),
            ("Eye Rx New", "date:2026-03-01"),
        ]):
            ref = agent.file_vault.store(f"content {i}".encode(), agent.session.keys.file_key, "rx.pdf")
            agent.db.store_document(name, "medical", agent.session.keys.db_key,
                                   file_ref=ref, tags=["medical", "sub:eye", date_tag])
        resp = await agent._handle_retrieve_document(
            "give me my latest eye prescription", {"document": "eye prescription"}, agent.session.keys,
        )
        assert "Eye Rx New" in resp.text
        assert resp.file_data is not None

    @pytest.mark.asyncio
    async def test_no_matching_doc(self, agent):
        resp = await agent._handle_retrieve_document(
            "give me my tax return", {"document": "tax return"}, agent.session.keys,
        )
        assert "no documents" in resp.text.lower() or "not found" in resp.text.lower() or "No documents" in resp.text


class TestProcessMethod:
    """Test the top-level process() routing."""

    @pytest.mark.asyncio
    async def test_locked_vault(self, agent):
        agent.session.is_locked = True
        resp = await agent.process("hello")
        assert "locked" in resp.text.lower()

    @pytest.mark.asyncio
    async def test_file_upload_routes_to_store(self, agent):
        resp = await agent.process("My Document", file_data=b"content", file_name="doc.txt")
        assert "Stored" in resp.text

    @pytest.mark.asyncio
    async def test_local_resolution_takes_priority(self, agent):
        resp = await agent.process("lock")
        assert "locked" in resp.text.lower()
        agent.llm.detect_intent.assert_not_called()

    @pytest.mark.asyncio
    async def test_force_parameter_passed(self, agent):
        await agent.process("First Upload", file_data=b"same data", file_name="a.txt")
        resp_dup = await agent.process("Second", file_data=b"same data", file_name="b.txt")
        assert resp_dup.data and resp_dup.data.get("duplicate_warning")
        resp_forced = await agent.process("Third", file_data=b"same data", file_name="c.txt", force=True)
        assert "Stored" in resp_forced.text


class TestDocumentSearch:
    """Test the _find_relevant_documents and _extract_query_filters."""

    def test_extract_eye_filter(self):
        filters = VaultAgent._extract_query_filters("show me my eye prescription")
        assert "eye" in filters

    def test_extract_dental_filter(self):
        filters = VaultAgent._extract_query_filters("last dental checkup")
        assert "dental" in filters

    def test_extract_doctor_filter(self):
        filters = VaultAgent._extract_query_filters("prescriptions from dr. sharma")
        assert "sharma" in filters

    def test_extract_no_filters(self):
        filters = VaultAgent._extract_query_filters("show me my documents")
        assert len(filters) == 0

    def test_sort_by_date_newest_first(self, agent):
        docs = [
            {"tags": ["date:2024-01-01"], "created_at": 0},
            {"tags": ["date:2026-03-01"], "created_at": 0},
            {"tags": ["date:2025-06-15"], "created_at": 0},
        ]
        sorted_docs = agent._sort_documents_by_date(docs, newest_first=True)
        assert sorted_docs[0]["tags"][0] == "date:2026-03-01"

    def test_sort_by_date_oldest_first(self, agent):
        docs = [
            {"tags": ["date:2026-03-01"], "created_at": 0},
            {"tags": ["date:2024-01-01"], "created_at": 0},
        ]
        sorted_docs = agent._sort_documents_by_date(docs, newest_first=False)
        assert sorted_docs[0]["tags"][0] == "date:2024-01-01"

    def test_get_doc_date_label_from_tag(self):
        doc = {"tags": ["medical", "date:2026-03-15"], "created_at": None}
        assert VaultAgent._get_doc_date_label(doc) == "2026-03-15"

    def test_get_doc_date_label_fallback_to_created(self):
        import time
        doc = {"tags": ["general"], "created_at": time.time()}
        label = VaultAgent._get_doc_date_label(doc)
        assert label is not None


class TestBirthdayParsing:
    @pytest.mark.parametrize("date_str", [
        "March 15",
        "march 15",
        "15 March",
        "Mar 15",
        "15 Mar",
        "3/15",
        "15-03",
        "March 15, 2000",
    ])
    def test_parse_birthday_date(self, date_str):
        result = VaultAgent._parse_birthday_date(date_str)
        assert result is not None, f"Failed to parse: {date_str}"

    @pytest.mark.parametrize("date_str", [
        "20th March",
        "March 20th",
        "1st January",
        "3rd Feb",
    ])
    def test_parse_ordinal_dates(self, date_str):
        result = VaultAgent._parse_birthday_date(date_str)
        assert result is not None, f"Failed to parse ordinal: {date_str}"
