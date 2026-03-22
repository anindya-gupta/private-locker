"""
User registry for multi-tenant Vault.

Stores user accounts in a central SQLite database. Each user gets their
own isolated vault directory with separate DB, files, and vector store.
Passwords are never stored — only Argon2id-derived verification tokens.
"""

from __future__ import annotations

import sqlite3
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional


@dataclass
class UserRecord:
    user_id: str
    username: str
    email: str
    salt: bytes
    verification_token: bytes
    created_at: str
    vault_dir: Path


class UserRegistry:
    """Central user database. One instance shared by the app."""

    def __init__(self, base_dir: Path) -> None:
        self._base_dir = base_dir
        self._db_path = base_dir / "users.db"
        self._conn: Optional[sqlite3.Connection] = None

    def open(self) -> None:
        self._base_dir.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(self._db_path))
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA foreign_keys=ON")
        self._conn.execute("""
            CREATE TABLE IF NOT EXISTS users (
                user_id     TEXT PRIMARY KEY,
                username    TEXT UNIQUE NOT NULL COLLATE NOCASE,
                email       TEXT NOT NULL DEFAULT '',
                salt        BLOB NOT NULL,
                verify_token BLOB NOT NULL,
                created_at  TEXT NOT NULL
            )
        """)
        self._conn.commit()

    def close(self) -> None:
        if self._conn:
            self._conn.close()
            self._conn = None

    def _vault_dir_for(self, user_id: str) -> Path:
        return self._base_dir / "users" / user_id

    def create_user(self, username: str, email: str, salt: bytes, verification_token: bytes) -> UserRecord:
        if not self._conn:
            raise RuntimeError("UserRegistry not open")

        existing = self._conn.execute(
            "SELECT user_id FROM users WHERE username = ? COLLATE NOCASE", (username,)
        ).fetchone()
        if existing:
            raise ValueError(f"Username '{username}' already taken")

        user_id = uuid.uuid4().hex[:16]
        now = datetime.now(timezone.utc).isoformat()

        vault_dir = self._vault_dir_for(user_id)
        vault_dir.mkdir(parents=True, exist_ok=True)
        (vault_dir / "data").mkdir(exist_ok=True)
        (vault_dir / "data" / "files").mkdir(exist_ok=True)
        (vault_dir / "data" / "chroma").mkdir(exist_ok=True)

        salt_path = vault_dir / "data" / ".salt"
        token_path = vault_dir / "data" / ".verify_token"
        salt_path.write_bytes(salt)
        token_path.write_bytes(verification_token)

        self._conn.execute(
            "INSERT INTO users (user_id, username, email, salt, verify_token, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (user_id, username, email, salt, verification_token, now),
        )
        self._conn.commit()

        return UserRecord(
            user_id=user_id, username=username, email=email,
            salt=salt, verification_token=verification_token,
            created_at=now, vault_dir=vault_dir,
        )

    def get_by_username(self, username: str) -> Optional[UserRecord]:
        if not self._conn:
            return None
        row = self._conn.execute(
            "SELECT * FROM users WHERE username = ? COLLATE NOCASE", (username,)
        ).fetchone()
        if not row:
            return None
        return UserRecord(
            user_id=row["user_id"], username=row["username"], email=row["email"],
            salt=row["salt"], verify_token=row["verify_token"],
            created_at=row["created_at"],
            vault_dir=self._vault_dir_for(row["user_id"]),
        )

    def get_by_id(self, user_id: str) -> Optional[UserRecord]:
        if not self._conn:
            return None
        row = self._conn.execute(
            "SELECT * FROM users WHERE user_id = ?", (user_id,)
        ).fetchone()
        if not row:
            return None
        return UserRecord(
            user_id=row["user_id"], username=row["username"], email=row["email"],
            salt=row["salt"], verify_token=row["verify_token"],
            created_at=row["created_at"],
            vault_dir=self._vault_dir_for(row["user_id"]),
        )

    def list_users(self) -> list[UserRecord]:
        if not self._conn:
            return []
        rows = self._conn.execute("SELECT * FROM users ORDER BY created_at").fetchall()
        return [
            UserRecord(
                user_id=r["user_id"], username=r["username"], email=r["email"],
                salt=r["salt"], verify_token=r["verify_token"],
                created_at=r["created_at"],
                vault_dir=self._vault_dir_for(r["user_id"]),
            )
            for r in rows
        ]

    def user_count(self) -> int:
        if not self._conn:
            return 0
        row = self._conn.execute("SELECT COUNT(*) as cnt FROM users").fetchone()
        return row["cnt"] if row else 0

    def has_users(self) -> bool:
        return self.user_count() > 0
