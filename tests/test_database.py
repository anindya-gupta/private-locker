"""Aggressive tests for VaultDatabase — documents, credentials, facts, reminders, search, migration."""

from __future__ import annotations

import time

import pytest


class TestDocuments:
    def test_store_and_retrieve(self, db, keys):
        doc_id = db.store_document("Aadhaar Card", "identity", keys.db_key, extracted_text="UID 1234 5678 9012")
        doc = db.get_document(doc_id, keys.db_key)
        assert doc is not None
        assert doc["name"] == "Aadhaar Card"
        assert doc["category"] == "identity"
        assert "1234 5678 9012" in doc["extracted_text"]

    def test_store_with_content_hash(self, db, keys):
        doc_id = db.store_document("Test", "general", keys.db_key, content_hash="abc123")
        found = db.find_by_content_hash("abc123")
        assert found is not None
        assert found["id"] == doc_id
        assert found["name"] == "Test"

    def test_find_by_hash_not_found(self, db, keys):
        assert db.find_by_content_hash("nonexistent") is None

    def test_duplicate_hash_detection(self, db, keys):
        db.store_document("First", "general", keys.db_key, content_hash="same_hash")
        found = db.find_by_content_hash("same_hash")
        assert found["name"] == "First"

    def test_store_without_text(self, db, keys):
        doc_id = db.store_document("Binary File", "general", keys.db_key, file_ref="ref123")
        doc = db.get_document(doc_id, keys.db_key)
        assert doc["extracted_text"] is None
        assert doc["file_ref"] == "ref123"

    def test_store_with_tags(self, db, keys):
        tags = ["medical", "eye", "sub:eye", "doctor:Sharma"]
        doc_id = db.store_document("Eye Rx", "medical", keys.db_key, tags=tags)
        doc = db.get_document(doc_id, keys.db_key)
        assert doc["tags"] == tags

    def test_list_documents_all(self, db, keys):
        db.store_document("Doc A", "identity", keys.db_key)
        db.store_document("Doc B", "medical", keys.db_key)
        db.store_document("Doc C", "financial", keys.db_key)
        docs = db.list_documents(keys.db_key)
        assert len(docs) == 3

    def test_list_documents_by_category(self, db, keys):
        db.store_document("Aadhaar", "identity", keys.db_key)
        db.store_document("Passport", "identity", keys.db_key)
        db.store_document("Blood Report", "medical", keys.db_key)
        docs = db.list_documents(keys.db_key, category="identity")
        assert len(docs) == 2
        assert all(d["category"] == "identity" for d in docs)

    def test_search_by_exact_name(self, db, keys):
        db.store_document("Eye Prescription", "medical", keys.db_key)
        results = db.search_documents("Eye Prescription", keys.db_key)
        assert len(results) == 1

    def test_search_by_partial_name(self, db, keys):
        db.store_document("Eye Prescription Dr Sharma", "medical", keys.db_key)
        results = db.search_documents("Prescription", keys.db_key)
        assert len(results) == 1

    def test_search_by_category(self, db, keys):
        db.store_document("Something", "medical", keys.db_key)
        results = db.search_documents("medical", keys.db_key)
        assert len(results) == 1

    def test_search_by_tags(self, db, keys):
        db.store_document("Eye Checkup", "medical", keys.db_key, tags=["medical", "sub:eye", "doctor:Bansal"])
        results = db.search_documents("eye", keys.db_key)
        assert len(results) >= 1

    def test_search_word_fallback(self, db, keys):
        """The key bug: 'eye prescriptions' should find 'Eye Prescription' via word split."""
        db.store_document("Eye Prescription", "medical", keys.db_key)
        results = db.search_documents("eye prescriptions", keys.db_key)
        assert len(results) >= 1, "Word-level fallback should match 'eye' from 'eye prescriptions'"

    def test_search_with_stopwords(self, db, keys):
        """'share me my eye prescriptions' should still find docs after stopword removal."""
        db.store_document("Eye Prescription", "medical", keys.db_key, tags=["medical", "sub:eye"])
        results = db.search_documents("share me my eye prescriptions", keys.db_key)
        assert len(results) >= 1

    def test_search_returns_multiple_matches(self, db, keys):
        """Multiple eye docs should all be returned."""
        db.store_document("Eye Prescription Jan 2025", "medical", keys.db_key, tags=["medical", "sub:eye"])
        db.store_document("Eye Prescription Mar 2026", "medical", keys.db_key, tags=["medical", "sub:eye"])
        db.store_document("Skin Report", "medical", keys.db_key, tags=["medical", "sub:skin"])
        results = db.search_documents("eye", keys.db_key)
        assert len(results) >= 2

    def test_search_no_results(self, db, keys):
        db.store_document("Aadhaar", "identity", keys.db_key)
        results = db.search_documents("xyznonexistent", keys.db_key)
        assert len(results) == 0

    def test_delete_document(self, db, keys):
        doc_id = db.store_document("To Delete", "general", keys.db_key)
        assert db.delete_document(doc_id) is True
        assert db.get_document(doc_id, keys.db_key) is None

    def test_delete_nonexistent_document(self, db, keys):
        assert db.delete_document("fake-id") is False

    def test_update_document_meta(self, db, keys):
        doc_id = db.store_document("Old Name", "general", keys.db_key, tags=["general"])
        success = db.update_document_meta(doc_id, category="medical", tags=["medical", "sub:eye"])
        assert success is True
        doc = db.get_document(doc_id, keys.db_key)
        assert doc["category"] == "medical"
        assert "sub:eye" in doc["tags"]

    def test_update_meta_no_changes(self, db, keys):
        doc_id = db.store_document("Test", "general", keys.db_key)
        assert db.update_document_meta(doc_id) is False


class TestCredentials:
    def test_store_and_get(self, db, keys):
        cred_id = db.store_credential("netflix", keys.cred_key, username="user@test.com", password="pass123")
        cred = db.get_credential("netflix", keys.cred_key)
        assert cred is not None
        assert cred["service"] == "netflix"
        assert cred["username"] == "user@test.com"
        assert cred["password"] == "pass123"

    def test_case_insensitive_lookup(self, db, keys):
        db.store_credential("Netflix", keys.cred_key, username="u", password="p")
        cred = db.get_credential("netflix", keys.cred_key)
        assert cred is not None

    def test_nonexistent_credential(self, db, keys):
        assert db.get_credential("nothing", keys.cred_key) is None

    def test_list_credentials(self, db, keys):
        db.store_credential("netflix", keys.cred_key, username="u1", password="p1")
        db.store_credential("spotify", keys.cred_key, username="u2", password="p2")
        creds = db.list_credentials(keys.cred_key)
        assert len(creds) == 2

    def test_delete_credential(self, db, keys):
        cred_id = db.store_credential("todele", keys.cred_key, username="u", password="p")
        assert db.delete_credential(cred_id) is True

    def test_update_credential(self, db, keys):
        cred_id = db.store_credential("service", keys.cred_key, username="old", password="old_pass")
        db.update_credential(cred_id, keys.cred_key, password="new_pass")
        cred = db.get_credential("service", keys.cred_key)
        assert cred["password"] == "new_pass"


class TestFacts:
    def test_store_and_get(self, db, keys):
        db.store_fact("blood type", "O+", keys.db_key, category="medical")
        fact = db.get_fact("blood type", keys.db_key)
        assert fact is not None
        assert fact["value"] == "O+"
        assert fact["category"] == "medical"

    def test_upsert_existing_fact(self, db, keys):
        db.store_fact("blood type", "O+", keys.db_key)
        db.store_fact("blood type", "A+", keys.db_key)
        fact = db.get_fact("blood type", keys.db_key)
        assert fact["value"] == "A+", "Should overwrite with new value"

    def test_search_facts(self, db, keys):
        db.store_fact("blood type", "O+", keys.db_key, category="medical")
        db.store_fact("city", "Bangalore", keys.db_key, category="personal")
        results = db.search_facts("blood", keys.db_key)
        assert len(results) >= 1

    def test_list_facts_by_category(self, db, keys):
        db.store_fact("blood type", "O+", keys.db_key, category="medical")
        db.store_fact("city", "Bangalore", keys.db_key, category="personal")
        medical = db.list_facts(keys.db_key, category="medical")
        assert len(medical) == 1

    def test_delete_fact(self, db, keys):
        fact_id = db.store_fact("temp", "value", keys.db_key)
        assert db.delete_fact(fact_id) is True
        assert db.get_fact("temp", keys.db_key) is None


class TestReminders:
    def test_store_and_list(self, db, keys):
        rem_id = db.store_reminder("Renew passport", "2026-06-15", keys.db_key)
        reminders = db.list_reminders(keys.db_key)
        assert len(reminders) == 1
        assert reminders[0]["title"] == "Renew passport"
        assert reminders[0]["due_date"] == "2026-06-15"
        assert reminders[0]["status"] == "active"

    def test_multiple_reminders_ordered(self, db, keys):
        db.store_reminder("Later", "2026-12-01", keys.db_key)
        db.store_reminder("Sooner", "2026-04-01", keys.db_key)
        reminders = db.list_reminders(keys.db_key)
        assert reminders[0]["due_date"] <= reminders[1]["due_date"]

    def test_complete_reminder(self, db, keys):
        rem_id = db.store_reminder("Done", "2026-01-01", keys.db_key)
        assert db.complete_reminder(rem_id) is True
        active = db.list_reminders(keys.db_key, status="active")
        assert len(active) == 0
        completed = db.list_reminders(keys.db_key, status="completed")
        assert len(completed) == 1

    def test_delete_reminder(self, db, keys):
        rem_id = db.store_reminder("Delete Me", "2026-01-01", keys.db_key)
        assert db.delete_reminder(rem_id) is True
        assert db.list_reminders(keys.db_key) == []

    def test_delete_nonexistent_reminder(self, db, keys):
        assert db.delete_reminder("fake-id") is False

    def test_complete_nonexistent_reminder(self, db, keys):
        assert db.complete_reminder("fake-id") is False

    def test_list_all_statuses(self, db, keys):
        db.store_reminder("A", "2026-01-01", keys.db_key)
        rem_id = db.store_reminder("B", "2026-02-01", keys.db_key)
        db.complete_reminder(rem_id)
        all_rems = db.list_reminders(keys.db_key, status=None)
        assert len(all_rems) == 2

    def test_reminder_with_repeat_interval(self, db, keys):
        rem_id = db.store_reminder("Monthly", "2026-04-01", keys.db_key, repeat_interval="30d")
        rems = db.list_reminders(keys.db_key)
        assert rems[0]["repeat_interval"] == "30d"


class TestMigration:
    def test_fresh_schema_has_reminders_table(self, db, keys):
        tables = {r[0] for r in db._conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()}
        assert "reminders" in tables

    def test_fresh_schema_has_content_hash(self, db, keys):
        cols = {r[1] for r in db._conn.execute("PRAGMA table_info(documents)").fetchall()}
        assert "content_hash" in cols

    def test_migrate_adds_missing_columns(self, tmp_path, keys):
        """Simulate an old DB without content_hash or reminders, and verify migration."""
        from vault.storage.database import VaultDatabase

        db_path = tmp_path / "migrate_test.db"
        old_db = VaultDatabase(db_path)
        old_db.open()
        old_db._conn.executescript("""
            CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
            INSERT INTO meta (key, value) VALUES ('schema_version', '1');
            CREATE TABLE documents (
                id TEXT PRIMARY KEY, name TEXT, category TEXT DEFAULT 'general',
                file_ref TEXT, extracted_text BLOB, tags TEXT DEFAULT '[]',
                created_at REAL NOT NULL, updated_at REAL NOT NULL
            );
            CREATE TABLE credentials (
                id TEXT PRIMARY KEY, service TEXT, username BLOB, password BLOB,
                url TEXT, notes BLOB, created_at REAL NOT NULL, updated_at REAL NOT NULL
            );
            CREATE TABLE facts (
                id TEXT PRIMARY KEY, category TEXT, key TEXT, value BLOB,
                source TEXT DEFAULT 'user', created_at REAL NOT NULL, updated_at REAL NOT NULL
            );
        """)
        old_db._conn.commit()
        old_db.close()

        new_db = VaultDatabase(db_path)
        new_db.open()
        new_db.initialize_schema()

        cols = {r[1] for r in new_db._conn.execute("PRAGMA table_info(documents)").fetchall()}
        assert "content_hash" in cols

        tables = {r[0] for r in new_db._conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()}
        assert "reminders" in tables
        new_db.close()
