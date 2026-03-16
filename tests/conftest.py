"""Shared fixtures for all Vault tests."""

from __future__ import annotations

import os
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from vault.config import VaultConfig
from vault.security.encryption import derive_all_keys
from vault.storage.database import VaultDatabase
from vault.storage.file_vault import FileVault

TEST_PASSWORD = "test-master-password-123"
TEST_SALT = b"\x00" * 32


@pytest.fixture
def tmp_vault(tmp_path):
    """Provide a temporary vault directory."""
    return tmp_path


@pytest.fixture
def keys():
    """Derive encryption keys from a fixed test password."""
    return derive_all_keys(TEST_PASSWORD, TEST_SALT)


@pytest.fixture
def db(tmp_vault, keys):
    """Open an in-memory-like temp database with schema initialized."""
    db_path = tmp_vault / "data" / "vault.db"
    database = VaultDatabase(db_path)
    database.open()
    database.initialize_schema()
    yield database
    database.close()


@pytest.fixture
def file_vault(tmp_vault):
    """Provide a FileVault backed by a temp dir."""
    return FileVault(tmp_vault / "files")


@pytest.fixture
def config(tmp_vault):
    return VaultConfig(vault_dir=tmp_vault)


@pytest.fixture
def mock_llm():
    """A fully mocked LLM router that returns sensible defaults."""
    llm = MagicMock()
    llm.detect_intent = AsyncMock(return_value={"intent": "general", "entities": {}, "confidence": 0.5})
    llm.complete = AsyncMock(return_value="I'm Vault, your personal AI assistant.")
    llm.answer_document_question = AsyncMock(return_value="Based on the document, the answer is X.")
    llm.answer_multi_document_question = AsyncMock(return_value="Across your documents, the answer is Y.")
    llm.extract_facts = AsyncMock(return_value=[])
    llm.extract_birthdays = AsyncMock(return_value=[])
    llm.extract_document_metadata = AsyncMock(return_value={
        "sub_category": None, "doctor": None, "doc_date": None,
        "keywords": [], "summary": None, "suggested_name": None, "expiry_date": None,
    })
    return llm


@pytest.fixture
def mock_vector_store():
    """A mocked vector store."""
    vs = MagicMock()
    vs.available = True
    vs.search.return_value = []
    vs.add_document.return_value = None
    vs.delete_document.return_value = None
    return vs
