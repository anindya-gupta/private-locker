"""
Encrypted SQLite database layer.

Uses SQLCipher when available, falls back to standard SQLite with
application-level encryption for sensitive fields.
"""

from __future__ import annotations

import json
import sqlite3
import time
import uuid
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Generator, Optional

from vault.security.encryption import decrypt, encrypt

SCHEMA_VERSION = 1

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS documents (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    category TEXT NOT NULL DEFAULT 'general',
    file_ref TEXT,
    extracted_text BLOB,
    tags TEXT DEFAULT '[]',
    content_hash TEXT,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS credentials (
    id TEXT PRIMARY KEY,
    service TEXT NOT NULL,
    username BLOB,
    password BLOB,
    url TEXT,
    notes BLOB,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS facts (
    id TEXT PRIMARY KEY,
    category TEXT NOT NULL DEFAULT 'general',
    key TEXT NOT NULL,
    value BLOB NOT NULL,
    source TEXT DEFAULT 'user',
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS reminders (
    id TEXT PRIMARY KEY,
    title BLOB NOT NULL,
    due_date TEXT NOT NULL,
    repeat_interval TEXT,
    source_doc_id TEXT,
    status TEXT NOT NULL DEFAULT 'active',
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_documents_category ON documents(category);
CREATE INDEX IF NOT EXISTS idx_documents_name ON documents(name);
CREATE INDEX IF NOT EXISTS idx_credentials_service ON credentials(service);
CREATE INDEX IF NOT EXISTS idx_facts_category ON facts(category);
CREATE INDEX IF NOT EXISTS idx_facts_key ON facts(key);
CREATE INDEX IF NOT EXISTS idx_reminders_due ON reminders(due_date);
CREATE INDEX IF NOT EXISTS idx_reminders_status ON reminders(status);
"""


class VaultDatabase:
    def __init__(self, db_path: Path):
        self._db_path = db_path
        self._conn: Optional[sqlite3.Connection] = None

    def open(self) -> None:
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(self._db_path))
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA foreign_keys=ON")

    def initialize_schema(self) -> None:
        if self._conn is None:
            raise RuntimeError("Database not open")
        row = self._conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='meta'"
        ).fetchone()
        if row:
            self._migrate()
            return
        self._conn.executescript(SCHEMA_SQL)
        self._conn.execute(
            "INSERT OR REPLACE INTO meta (key, value) VALUES (?, ?)",
            ("schema_version", str(SCHEMA_VERSION)),
        )
        self._conn.commit()

    def _migrate(self) -> None:
        cols = {r[1] for r in self._conn.execute("PRAGMA table_info(documents)").fetchall()}
        if "content_hash" not in cols:
            self._conn.execute("ALTER TABLE documents ADD COLUMN content_hash TEXT")
            self._conn.commit()

        tables = {r[0] for r in self._conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()}
        if "reminders" not in tables:
            self._conn.executescript("""
                CREATE TABLE IF NOT EXISTS reminders (
                    id TEXT PRIMARY KEY,
                    title BLOB NOT NULL,
                    due_date TEXT NOT NULL,
                    repeat_interval TEXT,
                    source_doc_id TEXT,
                    status TEXT NOT NULL DEFAULT 'active',
                    created_at REAL NOT NULL,
                    updated_at REAL NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_reminders_due ON reminders(due_date);
                CREATE INDEX IF NOT EXISTS idx_reminders_status ON reminders(status);
            """)

    def close(self) -> None:
        if self._conn:
            self._conn.close()
            self._conn = None

    @contextmanager
    def transaction(self) -> Generator[sqlite3.Cursor, None, None]:
        if self._conn is None:
            raise RuntimeError("Database not open")
        cursor = self._conn.cursor()
        try:
            yield cursor
            self._conn.commit()
        except Exception:
            self._conn.rollback()
            raise

    # --- Documents ---

    def store_document(
        self,
        name: str,
        category: str,
        encryption_key: bytes,
        file_ref: Optional[str] = None,
        extracted_text: Optional[str] = None,
        tags: Optional[list[str]] = None,
        content_hash: Optional[str] = None,
    ) -> str:
        doc_id = str(uuid.uuid4())
        now = time.time()
        enc_text = encrypt(extracted_text.encode("utf-8"), encryption_key) if extracted_text else None
        with self.transaction() as cur:
            cur.execute(
                """INSERT INTO documents (id, name, category, file_ref, extracted_text, tags, content_hash, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (doc_id, name, category, file_ref, enc_text, json.dumps(tags or []), content_hash, now, now),
            )
        return doc_id

    def find_by_content_hash(self, content_hash: str) -> Optional[dict[str, Any]]:
        if self._conn is None:
            raise RuntimeError("Database not open")
        row = self._conn.execute(
            "SELECT id, name, category FROM documents WHERE content_hash = ?",
            (content_hash,),
        ).fetchone()
        return dict(row) if row else None

    def get_document(self, doc_id: str, encryption_key: bytes) -> Optional[dict[str, Any]]:
        if self._conn is None:
            raise RuntimeError("Database not open")
        row = self._conn.execute("SELECT * FROM documents WHERE id = ?", (doc_id,)).fetchone()
        if row is None:
            return None
        return self._decrypt_document_row(row, encryption_key)

    def search_documents(self, query: str, encryption_key: bytes) -> list[dict[str, Any]]:
        if self._conn is None:
            raise RuntimeError("Database not open")
        rows = self._conn.execute(
            "SELECT * FROM documents WHERE name LIKE ? OR category LIKE ? OR tags LIKE ?",
            (f"%{query}%", f"%{query}%", f"%{query}%"),
        ).fetchall()
        if rows:
            return [self._decrypt_document_row(r, encryption_key) for r in rows]

        words = [w for w in query.lower().split() if len(w) > 2 and w not in (
            "the", "my", "me", "all", "show", "share", "get", "find", "give",
            "with", "from", "for", "and", "this", "that",
        )]
        if not words:
            return []
        conditions = []
        params: list[str] = []
        for word in words:
            conditions.append("(LOWER(name) LIKE ? OR LOWER(category) LIKE ? OR LOWER(tags) LIKE ?)")
            params.extend([f"%{word}%", f"%{word}%", f"%{word}%"])
        sql = f"SELECT * FROM documents WHERE {' OR '.join(conditions)}"
        rows = self._conn.execute(sql, params).fetchall()
        return [self._decrypt_document_row(r, encryption_key) for r in rows]

    def list_documents(self, encryption_key: bytes, category: Optional[str] = None) -> list[dict[str, Any]]:
        if self._conn is None:
            raise RuntimeError("Database not open")
        if category:
            rows = self._conn.execute(
                "SELECT * FROM documents WHERE category = ? ORDER BY updated_at DESC", (category,)
            ).fetchall()
        else:
            rows = self._conn.execute("SELECT * FROM documents ORDER BY updated_at DESC").fetchall()
        return [self._decrypt_document_row(r, encryption_key) for r in rows]

    def update_document_meta(self, doc_id: str, category: Optional[str] = None, tags: Optional[list[str]] = None) -> bool:
        if self._conn is None:
            raise RuntimeError("Database not open")
        updates = []
        params: list[Any] = []
        if category is not None:
            updates.append("category = ?")
            params.append(category)
        if tags is not None:
            updates.append("tags = ?")
            params.append(json.dumps(tags))
        if not updates:
            return False
        updates.append("updated_at = ?")
        params.append(time.time())
        params.append(doc_id)
        with self.transaction() as cur:
            cur.execute(f"UPDATE documents SET {', '.join(updates)} WHERE id = ?", params)
            return cur.rowcount > 0

    def delete_document(self, doc_id: str) -> bool:
        with self.transaction() as cur:
            cur.execute("DELETE FROM documents WHERE id = ?", (doc_id,))
            return cur.rowcount > 0

    def _decrypt_document_row(self, row: sqlite3.Row, key: bytes) -> dict[str, Any]:
        text = None
        if row["extracted_text"]:
            text = decrypt(row["extracted_text"], key).decode("utf-8")
        return {
            "id": row["id"],
            "name": row["name"],
            "category": row["category"],
            "file_ref": row["file_ref"],
            "extracted_text": text,
            "tags": json.loads(row["tags"]),
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        }

    # --- Credentials ---

    def store_credential(
        self,
        service: str,
        cred_key: bytes,
        username: Optional[str] = None,
        password: Optional[str] = None,
        url: Optional[str] = None,
        notes: Optional[str] = None,
    ) -> str:
        cred_id = str(uuid.uuid4())
        now = time.time()
        enc_user = encrypt(username.encode("utf-8"), cred_key) if username else None
        enc_pass = encrypt(password.encode("utf-8"), cred_key) if password else None
        enc_notes = encrypt(notes.encode("utf-8"), cred_key) if notes else None
        with self.transaction() as cur:
            cur.execute(
                """INSERT INTO credentials (id, service, username, password, url, notes, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (cred_id, service.lower(), enc_user, enc_pass, url, enc_notes, now, now),
            )
        return cred_id

    def get_credential(self, service: str, cred_key: bytes) -> Optional[dict[str, Any]]:
        if self._conn is None:
            raise RuntimeError("Database not open")
        row = self._conn.execute(
            "SELECT * FROM credentials WHERE service = ? ORDER BY updated_at DESC LIMIT 1",
            (service.lower(),),
        ).fetchone()
        if row is None:
            return None
        return self._decrypt_credential_row(row, cred_key)

    def list_credentials(self, cred_key: bytes) -> list[dict[str, Any]]:
        if self._conn is None:
            raise RuntimeError("Database not open")
        rows = self._conn.execute("SELECT * FROM credentials ORDER BY service").fetchall()
        return [self._decrypt_credential_row(r, cred_key) for r in rows]

    def update_credential(
        self,
        cred_id: str,
        cred_key: bytes,
        username: Optional[str] = None,
        password: Optional[str] = None,
        url: Optional[str] = None,
        notes: Optional[str] = None,
    ) -> bool:
        if self._conn is None:
            raise RuntimeError("Database not open")
        updates = []
        params: list[Any] = []
        if username is not None:
            updates.append("username = ?")
            params.append(encrypt(username.encode("utf-8"), cred_key))
        if password is not None:
            updates.append("password = ?")
            params.append(encrypt(password.encode("utf-8"), cred_key))
        if url is not None:
            updates.append("url = ?")
            params.append(url)
        if notes is not None:
            updates.append("notes = ?")
            params.append(encrypt(notes.encode("utf-8"), cred_key))
        if not updates:
            return False
        updates.append("updated_at = ?")
        params.append(time.time())
        params.append(cred_id)
        with self.transaction() as cur:
            cur.execute(f"UPDATE credentials SET {', '.join(updates)} WHERE id = ?", params)
            return cur.rowcount > 0

    def delete_credential(self, cred_id: str) -> bool:
        with self.transaction() as cur:
            cur.execute("DELETE FROM credentials WHERE id = ?", (cred_id,))
            return cur.rowcount > 0

    def _decrypt_credential_row(self, row: sqlite3.Row, key: bytes) -> dict[str, Any]:
        return {
            "id": row["id"],
            "service": row["service"],
            "username": decrypt(row["username"], key).decode("utf-8") if row["username"] else None,
            "password": decrypt(row["password"], key).decode("utf-8") if row["password"] else None,
            "url": row["url"],
            "notes": decrypt(row["notes"], key).decode("utf-8") if row["notes"] else None,
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        }

    # --- Facts ---

    def store_fact(
        self,
        key: str,
        value: str,
        encryption_key: bytes,
        category: str = "general",
        source: str = "user",
    ) -> str:
        fact_id = str(uuid.uuid4())
        now = time.time()
        enc_value = encrypt(value.encode("utf-8"), encryption_key)
        existing = self._conn.execute(
            "SELECT id FROM facts WHERE key = ? AND category = ?", (key.lower(), category)
        ).fetchone()
        if existing:
            with self.transaction() as cur:
                cur.execute(
                    "UPDATE facts SET value = ?, source = ?, updated_at = ? WHERE id = ?",
                    (enc_value, source, now, existing["id"]),
                )
            return existing["id"]
        with self.transaction() as cur:
            cur.execute(
                """INSERT INTO facts (id, category, key, value, source, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (fact_id, category, key.lower(), enc_value, source, now, now),
            )
        return fact_id

    def get_fact(self, key: str, encryption_key: bytes) -> Optional[dict[str, Any]]:
        if self._conn is None:
            raise RuntimeError("Database not open")
        row = self._conn.execute(
            "SELECT * FROM facts WHERE key = ? ORDER BY updated_at DESC LIMIT 1",
            (key.lower(),),
        ).fetchone()
        if row is None:
            return None
        return self._decrypt_fact_row(row, encryption_key)

    def search_facts(self, query: str, encryption_key: bytes) -> list[dict[str, Any]]:
        if self._conn is None:
            raise RuntimeError("Database not open")
        rows = self._conn.execute(
            "SELECT * FROM facts WHERE key LIKE ? OR category LIKE ?",
            (f"%{query.lower()}%", f"%{query.lower()}%"),
        ).fetchall()
        return [self._decrypt_fact_row(r, encryption_key) for r in rows]

    def list_facts(self, encryption_key: bytes, category: Optional[str] = None) -> list[dict[str, Any]]:
        if self._conn is None:
            raise RuntimeError("Database not open")
        if category:
            rows = self._conn.execute(
                "SELECT * FROM facts WHERE category = ? ORDER BY key", (category,)
            ).fetchall()
        else:
            rows = self._conn.execute("SELECT * FROM facts ORDER BY category, key").fetchall()
        return [self._decrypt_fact_row(r, encryption_key) for r in rows]

    def delete_fact(self, fact_id: str) -> bool:
        with self.transaction() as cur:
            cur.execute("DELETE FROM facts WHERE id = ?", (fact_id,))
            return cur.rowcount > 0

    def _decrypt_fact_row(self, row: sqlite3.Row, key: bytes) -> dict[str, Any]:
        return {
            "id": row["id"],
            "category": row["category"],
            "key": row["key"],
            "value": decrypt(row["value"], key).decode("utf-8"),
            "source": row["source"],
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        }

    # --- Reminders ---

    def store_reminder(
        self,
        title: str,
        due_date: str,
        encryption_key: bytes,
        repeat_interval: Optional[str] = None,
        source_doc_id: Optional[str] = None,
    ) -> str:
        rem_id = str(uuid.uuid4())
        now = time.time()
        enc_title = encrypt(title.encode("utf-8"), encryption_key)
        with self.transaction() as cur:
            cur.execute(
                """INSERT INTO reminders (id, title, due_date, repeat_interval, source_doc_id, status, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, 'active', ?, ?)""",
                (rem_id, enc_title, due_date, repeat_interval, source_doc_id, now, now),
            )
        return rem_id

    def list_reminders(self, encryption_key: bytes, status: Optional[str] = "active") -> list[dict[str, Any]]:
        if self._conn is None:
            raise RuntimeError("Database not open")
        if status:
            rows = self._conn.execute(
                "SELECT * FROM reminders WHERE status = ? ORDER BY due_date", (status,)
            ).fetchall()
        else:
            rows = self._conn.execute("SELECT * FROM reminders ORDER BY due_date").fetchall()
        return [self._decrypt_reminder_row(r, encryption_key) for r in rows]

    def delete_reminder(self, rem_id: str) -> bool:
        with self.transaction() as cur:
            cur.execute("DELETE FROM reminders WHERE id = ?", (rem_id,))
            return cur.rowcount > 0

    def complete_reminder(self, rem_id: str) -> bool:
        with self.transaction() as cur:
            cur.execute(
                "UPDATE reminders SET status = 'completed', updated_at = ? WHERE id = ?",
                (time.time(), rem_id),
            )
            return cur.rowcount > 0

    def _decrypt_reminder_row(self, row: sqlite3.Row, key: bytes) -> dict[str, Any]:
        return {
            "id": row["id"],
            "title": decrypt(row["title"], key).decode("utf-8"),
            "due_date": row["due_date"],
            "repeat_interval": row["repeat_interval"],
            "source_doc_id": row["source_doc_id"],
            "status": row["status"],
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        }
