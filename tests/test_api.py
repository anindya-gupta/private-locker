"""Integration tests for FastAPI endpoints — chat, upload-preview, sharing, reminders, expiry alerts."""

from __future__ import annotations

import json
import time
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi.testclient import TestClient

from vault.config import VaultConfig
from vault.security.encryption import derive_all_keys, generate_verification_token
from vault.security.session import Session

TEST_PASSWORD = "test-password-123"
TEST_SALT = b"\x00" * 32
SESSION_TOKEN = "test-token-abcdef"


@pytest.fixture
def app_client(tmp_path):
    """Create a test client with properly authenticated session."""
    test_config = VaultConfig(vault_dir=tmp_path)
    test_config.ensure_dirs()

    keys = derive_all_keys(TEST_PASSWORD, TEST_SALT)

    test_config.salt_path.parent.mkdir(parents=True, exist_ok=True)
    test_config.salt_path.write_bytes(TEST_SALT)
    token_bytes = generate_verification_token(TEST_PASSWORD, TEST_SALT)
    test_config.token_path.write_bytes(token_bytes)
    test_config.save()

    import vault.main as main_module
    import vault.config as config_module

    orig_config = main_module.config
    orig_agent = main_module.agent

    main_module.config = test_config
    config_module.config = test_config

    from vault.security.session import session_store
    session_store.configure(TEST_SALT, token_bytes, 3600)

    unlocked_session = Session()
    unlocked_session.configure(TEST_SALT, token_bytes, 3600)
    unlocked_session._keys = keys
    unlocked_session._locked = False
    unlocked_session._last_activity = time.time()
    session_store._sessions[SESSION_TOKEN] = unlocked_session

    from vault.agent import VaultAgent
    test_agent = VaultAgent(test_config, unlocked_session)
    test_agent.db.open()
    test_agent.db._conn.close()
    import sqlite3
    test_agent.db._conn = sqlite3.connect(str(test_config.db_path), check_same_thread=False)
    test_agent.db._conn.row_factory = sqlite3.Row
    test_agent.db._conn.execute("PRAGMA journal_mode=WAL")
    test_agent.db._conn.execute("PRAGMA foreign_keys=ON")
    test_agent.db.initialize_schema()

    mock_llm = MagicMock()
    mock_llm.detect_intent = AsyncMock(return_value={"intent": "general", "entities": {}, "confidence": 0.5})
    mock_llm.complete = AsyncMock(return_value="Hello from Vault!")
    mock_llm.extract_document_metadata = AsyncMock(return_value={
        "sub_category": None, "doctor": None, "doc_date": None,
        "keywords": [], "summary": None, "suggested_name": None, "expiry_date": None,
    })
    mock_llm.answer_document_question = AsyncMock(return_value="Answer from doc.")
    mock_llm.answer_multi_document_question = AsyncMock(return_value="Multi-doc answer.")
    mock_llm.extract_facts = AsyncMock(return_value=[])
    mock_llm.extract_birthdays = AsyncMock(return_value=[])
    test_agent.llm = mock_llm

    mock_vs = MagicMock()
    mock_vs.available = True
    mock_vs.search.return_value = []
    test_agent.vector_store = mock_vs

    main_module.agent = test_agent

    client = TestClient(main_module.app, raise_server_exceptions=False)
    client.cookies.set("vault_sid", SESSION_TOKEN)

    yield client, test_agent

    test_agent.db.close()
    main_module.config = orig_config
    main_module.agent = orig_agent
    session_store._sessions.pop(SESSION_TOKEN, None)


class TestChatEndpoint:
    def test_text_message(self, app_client):
        client, _ = app_client
        resp = client.post("/api/chat", data={"message": "hello"})
        assert resp.status_code == 200
        assert "text" in resp.json()

    def test_file_upload(self, app_client):
        client, _ = app_client
        resp = client.post("/api/chat", data={"message": "My Document"},
                          files={"file": ("test.txt", b"document content here", "text/plain")})
        assert resp.status_code == 200
        data = resp.json()
        assert "Stored" in data["text"]

    def test_duplicate_detection(self, app_client):
        client, _ = app_client
        content = b"unique duplicate test content xyz"
        client.post("/api/chat", data={"message": "First"},
                   files={"file": ("a.txt", content, "text/plain")})
        resp2 = client.post("/api/chat", data={"message": "Second"},
                           files={"file": ("b.txt", content, "text/plain")})
        data = resp2.json()
        assert data.get("duplicate_warning") is True

    def test_force_upload_bypasses_duplicate(self, app_client):
        client, _ = app_client
        content = b"force test content abc"
        client.post("/api/chat", data={"message": "First"},
                   files={"file": ("a.txt", content, "text/plain")})
        resp2 = client.post("/api/chat", data={"message": "Second", "force": "true"},
                           files={"file": ("b.txt", content, "text/plain")})
        assert "Stored" in resp2.json()["text"]


class TestUploadPreview:
    def test_preview_returns_metadata(self, app_client):
        client, _ = app_client
        resp = client.post("/api/upload-preview",
                          files={"file": ("aadhaar.txt", b"UID 1234 5678 9012 Aadhaar Card", "text/plain")})
        assert resp.status_code == 200
        data = resp.json()
        assert "category" in data
        assert "has_text" in data

    def test_preview_binary_file(self, app_client):
        client, _ = app_client
        resp = client.post("/api/upload-preview",
                          files={"file": ("photo.zip", b"\x00\x01\x02\x03", "application/zip")})
        assert resp.status_code == 200
        data = resp.json()
        assert data["has_text"] is False


class TestRemindersAPI:
    def test_create_reminder(self, app_client):
        client, _ = app_client
        resp = client.post("/api/reminders",
                          content=json.dumps({"title": "Renew Passport", "due_date": "2026-06-15"}),
                          headers={"Content-Type": "application/json"})
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "created"
        assert "id" in data

    def test_list_reminders(self, app_client):
        client, _ = app_client
        client.post("/api/reminders",
                   content=json.dumps({"title": "Test Rem", "due_date": "2026-04-01"}),
                   headers={"Content-Type": "application/json"})
        resp = client.get("/api/reminders")
        assert resp.status_code == 200
        rems = resp.json()["reminders"]
        assert len(rems) >= 1
        assert rems[0]["title"] == "Test Rem"

    def test_complete_reminder(self, app_client):
        client, _ = app_client
        create_resp = client.post("/api/reminders",
                                 content=json.dumps({"title": "To Complete", "due_date": "2026-01-01"}),
                                 headers={"Content-Type": "application/json"})
        rem_id = create_resp.json()["id"]
        resp = client.post(f"/api/reminders/{rem_id}/complete")
        assert resp.status_code == 200
        assert resp.json()["status"] == "completed"

    def test_delete_reminder(self, app_client):
        client, _ = app_client
        create_resp = client.post("/api/reminders",
                                 content=json.dumps({"title": "Delete Me", "due_date": "2026-01-01"}),
                                 headers={"Content-Type": "application/json"})
        rem_id = create_resp.json()["id"]
        resp = client.delete(f"/api/reminders/{rem_id}")
        assert resp.status_code == 200

    def test_delete_nonexistent_reminder(self, app_client):
        client, _ = app_client
        resp = client.delete("/api/reminders/fake-id")
        assert resp.status_code == 404


class TestShareAPI:
    def _upload_and_get_id(self, client):
        client.post("/api/chat", data={"message": "Test Doc"},
                   files={"file": ("doc.txt", b"shareable content", "text/plain")})
        docs_resp = client.get("/api/documents")
        return docs_resp.json()["documents"][0]["id"]

    def test_create_share_link(self, app_client):
        client, _ = app_client
        doc_id = self._upload_and_get_id(client)
        resp = client.post("/api/share/create",
                          content=json.dumps({"doc_id": doc_id}),
                          headers={"Content-Type": "application/json"})
        assert resp.status_code == 200
        data = resp.json()
        assert "share_path" in data
        assert "token" in data

    def test_download_shared_file(self, app_client):
        client, _ = app_client
        doc_id = self._upload_and_get_id(client)
        share_resp = client.post("/api/share/create",
                                content=json.dumps({"doc_id": doc_id}),
                                headers={"Content-Type": "application/json"})
        token = share_resp.json()["token"]
        dl_resp = client.get(f"/api/share/{token}")
        assert dl_resp.status_code == 200
        assert dl_resp.content == b"shareable content"

    def test_expired_share_link(self, app_client):
        client, _ = app_client
        resp = client.get("/api/share/nonexistent-token")
        assert resp.status_code == 404

    def test_share_nonexistent_doc(self, app_client):
        client, _ = app_client
        resp = client.post("/api/share/create",
                          content=json.dumps({"doc_id": "fake-doc-id"}),
                          headers={"Content-Type": "application/json"})
        assert resp.status_code == 404

    def test_chat_share_intent_creates_link(self, app_client):
        """Sending 'email this to me' via chat should return a share_path when a doc was just retrieved."""
        client, agent = app_client
        doc_id = self._upload_and_get_id(client)
        agent.llm.detect_intent = AsyncMock(return_value={
            "intent": "share_document",
            "entities": {"document": ""},
            "confidence": 0.95,
        })
        from vault.security.session import session_store
        sess = session_store._sessions[SESSION_TOKEN]
        sess._last_doc_id = doc_id
        resp = client.post("/api/chat", data={"message": "email this to me"})
        assert resp.status_code == 200
        data = resp.json()
        assert "share_path" in data, f"Expected share_path in response, got: {data}"
        assert "share_expires_in" in data
        assert data["share_path"].startswith("/api/share/")

    def test_chat_share_by_name(self, app_client):
        """Sending 'share my Test Doc' should find the doc by name and return a share link."""
        client, agent = app_client
        self._upload_and_get_id(client)
        agent.llm.detect_intent = AsyncMock(return_value={
            "intent": "share_document",
            "entities": {"document": "Test Doc"},
            "confidence": 0.95,
        })
        resp = client.post("/api/chat", data={"message": "share my Test Doc"})
        assert resp.status_code == 200
        data = resp.json()
        assert "share_path" in data, f"Expected share_path in response, got: {data}"

    def test_chat_share_no_doc_found(self, app_client):
        """When no doc matches, agent should ask the user to specify."""
        client, agent = app_client
        agent.llm.detect_intent = AsyncMock(return_value={
            "intent": "share_document",
            "entities": {"document": "nonexistent thing"},
            "confidence": 0.95,
        })
        resp = client.post("/api/chat", data={"message": "share my nonexistent thing"})
        assert resp.status_code == 200
        data = resp.json()
        assert "share_path" not in data
        assert "don't know which document" in data["text"].lower() or "tell me" in data["text"].lower()

    def test_retrieve_doc_sets_last_doc_id(self, app_client):
        """Retrieving a document via chat should set _last_doc_id on the session."""
        client, agent = app_client
        doc_id = self._upload_and_get_id(client)
        docs = client.get("/api/documents").json()["documents"]
        doc_name = docs[0]["name"]
        agent.llm.detect_intent = AsyncMock(return_value={
            "intent": "retrieve_document",
            "entities": {"document": doc_name},
            "confidence": 0.95,
        })
        resp = client.post("/api/chat", data={"message": f"show me {doc_name}"})
        assert resp.status_code == 200
        from vault.security.session import session_store
        sess = session_store._sessions[SESSION_TOKEN]
        assert sess._last_doc_id == doc_id


class TestExpiryAlerts:
    def test_no_alerts(self, app_client):
        client, _ = app_client
        resp = client.get("/api/expiry-alerts")
        assert resp.status_code == 200
        assert resp.json()["alerts"] == []

    def test_expiring_doc_shown(self, app_client):
        client, agent = app_client
        from datetime import datetime, timedelta
        keys = agent.session.keys
        expiry = (datetime.now() + timedelta(days=20)).strftime("%Y-%m-%d")
        agent.db.store_document("Passport", "identity", keys.db_key,
                               tags=["identity", f"expiry:{expiry}"])
        resp = client.get("/api/expiry-alerts")
        alerts = resp.json()["alerts"]
        assert len(alerts) == 1
        assert alerts[0]["name"] == "Passport"
        assert alerts[0]["days_until"] <= 30

    def test_non_expiring_doc_not_shown(self, app_client):
        client, agent = app_client
        from datetime import datetime, timedelta
        keys = agent.session.keys
        future = (datetime.now() + timedelta(days=365)).strftime("%Y-%m-%d")
        agent.db.store_document("Far Future", "identity", keys.db_key,
                               tags=["identity", f"expiry:{future}"])
        resp = client.get("/api/expiry-alerts")
        assert len(resp.json()["alerts"]) == 0


class TestDocumentsAPI:
    def test_list_documents(self, app_client):
        client, _ = app_client
        client.post("/api/chat", data={"message": "Doc1"},
                   files={"file": ("a.txt", b"content here", "text/plain")})
        resp = client.get("/api/documents")
        assert resp.status_code == 200
        docs = resp.json()["documents"]
        assert len(docs) >= 1

    def test_delete_document(self, app_client):
        client, _ = app_client
        client.post("/api/chat", data={"message": "To Delete"},
                   files={"file": ("d.txt", b"delete me", "text/plain")})
        docs = client.get("/api/documents").json()["documents"]
        doc_id = docs[0]["id"]
        resp = client.delete(f"/api/documents/{doc_id}")
        assert resp.status_code == 200
        remaining = client.get("/api/documents").json()["documents"]
        assert all(d["id"] != doc_id for d in remaining)


class TestStatsEndpoint:
    def test_stats_includes_expiring_count(self, app_client):
        client, _ = app_client
        resp = client.get("/api/stats")
        assert resp.status_code == 200
        data = resp.json()
        assert "documents" in data
        assert "credentials" in data
        assert "facts" in data
        assert "expiring_soon" in data
