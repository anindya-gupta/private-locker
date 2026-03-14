"""
Vault MCP Server — exposes Vault as MCP tools for Claude, Cursor, and other MCP clients.

This lets any MCP-compatible AI client interact with your encrypted vault:
  - Store and retrieve documents
  - Look up credentials (100% local, never sent to client LLM)
  - Remember and recall personal facts
  - Search across all stored information

Security: The vault must be unlocked before MCP tools work. Credential
passwords are returned directly to the calling client — the MCP server
itself does no LLM processing on sensitive data.
"""

from __future__ import annotations

import base64
import getpass
import logging
import sys
from pathlib import Path
from typing import Optional

from mcp.server.fastmcp import FastMCP

from vault.config import VaultConfig, config
from vault.processors.credentials import CredentialManager
from vault.processors.document import extract_text, guess_category
from vault.processors.memory import MemoryManager
from vault.security.session import session
from vault.storage.database import VaultDatabase
from vault.storage.file_vault import FileVault
from vault.storage.vector_store import VectorStore

logger = logging.getLogger(__name__)

mcp = FastMCP(
    "Vault",
    instructions=(
        "Vault is a secure personal data store. Use these tools to store/retrieve "
        "the user's documents, credentials, and personal facts. All data is encrypted "
        "locally. Credential tools NEVER send passwords to any external LLM — they "
        "return results directly. Always confirm before storing or deleting data."
    ),
)

_db: Optional[VaultDatabase] = None
_file_vault: Optional[FileVault] = None
_vector_store: Optional[VectorStore] = None
_cred_manager: Optional[CredentialManager] = None
_memory: Optional[MemoryManager] = None


def _init_storage():
    global _db, _file_vault, _vector_store, _cred_manager, _memory
    if _db is not None:
        return
    _db = VaultDatabase(config.db_path)
    _db.open()
    _db.initialize_schema()
    _file_vault = FileVault(config.files_dir)
    _vector_store = VectorStore(config.chroma_dir, config.embedding_model)
    _vector_store.initialize()
    _cred_manager = CredentialManager(_db)
    _memory = MemoryManager(_db)


def _require_unlocked():
    if session.is_locked:
        raise PermissionError(
            "Vault is locked. Run `vault unlock` in your terminal first, "
            "or use the vault_unlock tool with your master password."
        )
    return session.keys


# --- Auth tools ---

@mcp.tool()
def vault_unlock(password: str) -> str:
    """
    Unlock the vault with the master password.
    Must be called before any other vault tool can be used.
    The password is used to derive encryption keys and is never stored.
    """
    if not config.salt_path.exists():
        return "Error: Vault not initialized. Run `vault init` in your terminal first."

    salt = config.salt_path.read_bytes()
    token = config.token_path.read_bytes()
    session.configure(salt, token, config.session_timeout)

    if session.unlock(password):
        _init_storage()
        return "Vault unlocked successfully."
    return "Incorrect password."


@mcp.tool()
def vault_lock() -> str:
    """Lock the vault. All encryption keys are wiped from memory."""
    session.lock()
    return "Vault locked."


@mcp.tool()
def vault_status() -> str:
    """Check whether the vault is initialized and unlocked."""
    initialized = config.salt_path.exists()
    locked = session.is_locked
    return (
        f"Initialized: {initialized}\n"
        f"Locked: {locked}\n"
        f"Paranoid mode: {config.paranoid_mode}"
    )


# --- Document tools ---

@mcp.tool()
def vault_store_document(
    name: str,
    file_content_base64: str,
    filename: str,
    category: str = "",
) -> str:
    """
    Store a document in the encrypted vault.

    Args:
        name: A human-readable name for the document (e.g. "My Aadhaar Card")
        file_content_base64: The file contents encoded as base64
        filename: Original filename with extension (e.g. "aadhaar.pdf")
        category: Optional category (identity, financial, medical, education, legal, insurance, general)
    """
    keys = _require_unlocked()
    _init_storage()

    file_data = base64.b64decode(file_content_base64)

    file_ref = _file_vault.store(file_data, keys.file_key, filename)
    extracted = extract_text(file_data, filename)
    auto_category = category or guess_category(filename, extracted)

    doc_id = _db.store_document(
        name=name,
        category=auto_category,
        encryption_key=keys.db_key,
        file_ref=file_ref,
        extracted_text=extracted or None,
        tags=[auto_category, filename.split(".")[-1]],
    )

    if extracted:
        _vector_store.add_document(doc_id, extracted, {"name": name, "category": auto_category})

    result = f"Stored '{name}' under [{auto_category}]."
    if extracted:
        preview = extracted[:300] + "..." if len(extracted) > 300 else extracted
        result += f"\n\nExtracted text:\n{preview}"
    else:
        result += "\n(No text could be extracted from this file.)"
    return result


@mcp.tool()
def vault_search_documents(query: str) -> str:
    """
    Search stored documents by name, category, or content.
    Uses semantic search when available, falls back to keyword search.

    Args:
        query: What to search for (e.g. "aadhaar", "passport", "bank statement")
    """
    keys = _require_unlocked()
    _init_storage()

    results = _vector_store.search(query, n_results=5)
    found_docs = []

    if results:
        for r in results:
            doc = _db.get_document(r["id"], keys.db_key)
            if doc:
                found_docs.append(doc)
    else:
        found_docs = _db.search_documents(query, keys.db_key)

    if not found_docs:
        return f"No documents matching '{query}' found."

    lines = [f"Found {len(found_docs)} document(s):\n"]
    for doc in found_docs:
        lines.append(f"- [{doc['category']}] {doc['name']}")
        if doc.get("extracted_text"):
            preview = doc["extracted_text"][:200]
            lines.append(f"  Text: {preview}...")
    return "\n".join(lines)


@mcp.tool()
def vault_read_document(query: str) -> str:
    """
    Read the full extracted text of a stored document.
    Use this to answer questions about document contents (e.g. "What is my Aadhaar number?").

    Args:
        query: Document name or search term to find the right document
    """
    keys = _require_unlocked()
    _init_storage()

    results = _vector_store.search(query, n_results=3)
    doc = None

    if results:
        doc = _db.get_document(results[0]["id"], keys.db_key)

    if not doc:
        docs = _db.search_documents(query, keys.db_key)
        if docs:
            doc = docs[0]

    if not doc:
        return f"No document matching '{query}' found."

    if doc.get("extracted_text"):
        return f"Document: {doc['name']} [{doc['category']}]\n\n{doc['extracted_text']}"
    return f"Document '{doc['name']}' found but no text was extracted from it."


@mcp.tool()
def vault_list_documents(category: str = "") -> str:
    """
    List all stored documents, optionally filtered by category.

    Args:
        category: Optional filter (identity, financial, medical, education, legal, insurance, general)
    """
    keys = _require_unlocked()
    _init_storage()

    docs = _db.list_documents(keys.db_key, category=category or None)
    if not docs:
        return "No documents stored." + (f" (category: {category})" if category else "")

    lines = [f"Stored documents ({len(docs)}):\n"]
    for d in docs:
        lines.append(f"- [{d['category']}] {d['name']}")
    return "\n".join(lines)


@mcp.tool()
def vault_retrieve_document_file(query: str) -> str:
    """
    Retrieve a stored document file as base64. Use this when the user wants to
    download or view the original file (not just the text).

    Args:
        query: Document name or search term
    """
    keys = _require_unlocked()
    _init_storage()

    docs = _db.search_documents(query, keys.db_key)
    if not docs:
        return f"No document matching '{query}' found."

    doc = docs[0]
    if not doc.get("file_ref"):
        return f"Document '{doc['name']}' has no attached file."

    try:
        file_data, original_name = _file_vault.retrieve(doc["file_ref"], keys.file_key)
        b64 = base64.b64encode(file_data).decode("ascii")
        return f"File: {original_name}\nBase64: {b64}"
    except FileNotFoundError:
        return f"Document '{doc['name']}' record exists but the file is missing from disk."


# --- Credential tools (100% local, never touches any LLM) ---

@mcp.tool()
def vault_store_credential(
    service: str,
    username: str = "",
    password: str = "",
    url: str = "",
    notes: str = "",
) -> str:
    """
    Store a website/service credential. Credentials are double-encrypted and
    NEVER sent to any LLM — they are stored and retrieved purely locally.

    Args:
        service: Service name (e.g. "netflix", "gmail", "amazon")
        username: Username or email
        password: Password
        url: Optional URL for the service
        notes: Optional notes
    """
    keys = _require_unlocked()
    _init_storage()

    _cred_manager.store(
        service=service,
        cred_key=keys.cred_key,
        username=username or None,
        password=password or None,
        url=url or None,
        notes=notes or None,
    )
    parts = [f"Credential for '{service}' stored securely."]
    if username:
        parts.append(f"Username: {username}")
    if password:
        parts.append("Password: [stored]")
    return "\n".join(parts)


@mcp.tool()
def vault_get_credential(service: str) -> str:
    """
    Retrieve stored credentials for a service. Returns username and password.
    This is a 100% local operation — no data is sent to any external service.

    Args:
        service: Service name (e.g. "netflix", "gmail")
    """
    keys = _require_unlocked()
    _init_storage()

    cred = _cred_manager.get(service, keys.cred_key)
    if not cred:
        return f"No credentials found for '{service}'."

    return CredentialManager.format_credential(cred, mask_password=False)


@mcp.tool()
def vault_list_credentials() -> str:
    """List all stored credentials (service names and usernames only, passwords hidden)."""
    keys = _require_unlocked()
    _init_storage()

    creds = _cred_manager.list_all(keys.cred_key)
    if not creds:
        return "No credentials stored."

    lines = [f"Stored credentials ({len(creds)}):\n"]
    for c in creds:
        lines.append(f"- {c['service']}: {c.get('username', 'N/A')}")
    return "\n".join(lines)


@mcp.tool()
def vault_delete_credential(service: str) -> str:
    """
    Delete stored credentials for a service.

    Args:
        service: Service name to delete credentials for
    """
    keys = _require_unlocked()
    _init_storage()

    cred = _cred_manager.get(service, keys.cred_key)
    if not cred:
        return f"No credentials found for '{service}'."

    _cred_manager.delete(cred["id"])
    return f"Credentials for '{service}' deleted."


# --- Memory/Facts tools ---

@mcp.tool()
def vault_remember(key: str, value: str, category: str = "general") -> str:
    """
    Store a personal fact about the user.

    Args:
        key: What this fact is about (e.g. "blood type", "birthday", "allergy")
        value: The value (e.g. "O+", "January 15", "peanuts")
        category: Optional category (personal, medical, financial, work, preferences, general)
    """
    keys = _require_unlocked()
    _init_storage()

    _memory.remember(key, value, keys.db_key, category)
    return f"Remembered: {key} = {value} [{category}]"


@mcp.tool()
def vault_recall(key: str) -> str:
    """
    Recall a stored personal fact.

    Args:
        key: What to recall (e.g. "blood type", "birthday")
    """
    keys = _require_unlocked()
    _init_storage()

    value = _memory.recall(key, keys.db_key)
    if value:
        return f"{key}: {value}"

    facts = _memory.search(key, keys.db_key)
    if facts:
        return MemoryManager.format_facts(facts)

    return f"No fact stored for '{key}'."


@mcp.tool()
def vault_list_facts(category: str = "") -> str:
    """
    List all stored personal facts, optionally filtered by category.

    Args:
        category: Optional filter (personal, medical, financial, work, preferences, general)
    """
    keys = _require_unlocked()
    _init_storage()

    facts = _memory.list_all(keys.db_key, category=category or None)
    if not facts:
        return "No facts stored." + (f" (category: {category})" if category else "")

    return MemoryManager.format_facts(facts)


@mcp.tool()
def vault_delete_fact(key: str) -> str:
    """
    Delete a stored personal fact.

    Args:
        key: The fact key to delete (e.g. "blood type")
    """
    keys = _require_unlocked()
    _init_storage()

    fact = _db.get_fact(key, keys.db_key)
    if not fact:
        return f"No fact found for '{key}'."

    _memory.forget(fact["id"])
    return f"Fact '{key}' deleted."


# --- Search everything ---

@mcp.tool()
def vault_search(query: str) -> str:
    """
    Search across all stored data — documents, credentials, and facts.

    Args:
        query: What to search for
    """
    keys = _require_unlocked()
    _init_storage()

    results = []

    docs = _db.search_documents(query, keys.db_key)
    if docs:
        results.append(f"Documents ({len(docs)}):")
        for d in docs:
            results.append(f"  - [{d['category']}] {d['name']}")

    creds = _cred_manager.list_all(keys.cred_key)
    matching_creds = [c for c in creds if query.lower() in c["service"]]
    if matching_creds:
        results.append(f"\nCredentials ({len(matching_creds)}):")
        for c in matching_creds:
            results.append(f"  - {c['service']}: {c.get('username', 'N/A')}")

    facts = _memory.search(query, keys.db_key)
    if facts:
        results.append(f"\nFacts ({len(facts)}):")
        for f in facts:
            results.append(f"  - {f['key']}: {f['value']}")

    if not results:
        return f"Nothing found for '{query}'."

    return "\n".join(results)


def run_mcp_server():
    """Entry point for the MCP server."""
    if config.salt_path.exists():
        salt = config.salt_path.read_bytes()
        token = config.token_path.read_bytes()
        session.configure(salt, token, config.session_timeout)

    mcp.run()
