"""
User registry for multi-tenant Vault.

Stores user accounts in a central SQLite database. Each user gets their
own isolated vault directory with separate DB, files, and vector store.
Passwords are never stored — only Argon2id-derived verification tokens.
Includes RSA public keys for cross-user document sharing.
"""

from __future__ import annotations

import sqlite3
import uuid
from dataclasses import dataclass, field
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
    public_key: bytes = b""


@dataclass
class ShareRecord:
    share_id: str
    from_user_id: str
    to_user_id: str
    doc_name: str
    encrypted_file_key: bytes
    encrypted_file_data: bytes
    created_at: str


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
                public_key  BLOB NOT NULL DEFAULT '',
                created_at  TEXT NOT NULL
            )
        """)
        self._conn.execute("""
            CREATE TABLE IF NOT EXISTS shares (
                share_id            TEXT PRIMARY KEY,
                from_user_id        TEXT NOT NULL,
                to_user_id          TEXT NOT NULL,
                doc_name            TEXT NOT NULL,
                encrypted_file_key  BLOB NOT NULL,
                encrypted_file_data BLOB NOT NULL,
                created_at          TEXT NOT NULL,
                FOREIGN KEY (from_user_id) REFERENCES users(user_id),
                FOREIGN KEY (to_user_id) REFERENCES users(user_id)
            )
        """)
        # Migration: add public_key column if missing
        cols = [r[1] for r in self._conn.execute("PRAGMA table_info(users)").fetchall()]
        if "public_key" not in cols:
            self._conn.execute("ALTER TABLE users ADD COLUMN public_key BLOB NOT NULL DEFAULT ''")
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

        from vault.security.encryption import generate_rsa_keypair

        user_id = uuid.uuid4().hex[:16]
        now = datetime.now(timezone.utc).isoformat()
        private_pem, public_pem = generate_rsa_keypair()

        vault_dir = self._vault_dir_for(user_id)
        vault_dir.mkdir(parents=True, exist_ok=True)
        (vault_dir / "data").mkdir(exist_ok=True)
        (vault_dir / "data" / "files").mkdir(exist_ok=True)
        (vault_dir / "data" / "chroma").mkdir(exist_ok=True)

        salt_path = vault_dir / "data" / ".salt"
        token_path = vault_dir / "data" / ".verify_token"
        privkey_path = vault_dir / "data" / ".private_key.pem"
        salt_path.write_bytes(salt)
        token_path.write_bytes(verification_token)
        privkey_path.write_bytes(private_pem)

        self._conn.execute(
            "INSERT INTO users (user_id, username, email, salt, verify_token, public_key, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (user_id, username, email, salt, verification_token, public_pem, now),
        )
        self._conn.commit()

        return UserRecord(
            user_id=user_id, username=username, email=email,
            salt=salt, verification_token=verification_token,
            created_at=now, vault_dir=vault_dir, public_key=public_pem,
        )

    def _row_to_record(self, row) -> UserRecord:
        return UserRecord(
            user_id=row["user_id"], username=row["username"], email=row["email"],
            salt=row["salt"], verification_token=row["verify_token"],
            created_at=row["created_at"],
            vault_dir=self._vault_dir_for(row["user_id"]),
            public_key=row["public_key"] if "public_key" in row.keys() else b"",
        )

    def get_by_username(self, username: str) -> Optional[UserRecord]:
        if not self._conn:
            return None
        row = self._conn.execute(
            "SELECT * FROM users WHERE username = ? COLLATE NOCASE", (username,)
        ).fetchone()
        if not row:
            return None
        return self._row_to_record(row)

    def get_by_id(self, user_id: str) -> Optional[UserRecord]:
        if not self._conn:
            return None
        row = self._conn.execute(
            "SELECT * FROM users WHERE user_id = ?", (user_id,)
        ).fetchone()
        if not row:
            return None
        return self._row_to_record(row)

    def list_users(self) -> list[UserRecord]:
        if not self._conn:
            return []
        rows = self._conn.execute("SELECT * FROM users ORDER BY created_at").fetchall()
        return [self._row_to_record(r) for r in rows]

    def user_count(self) -> int:
        if not self._conn:
            return 0
        row = self._conn.execute("SELECT COUNT(*) as cnt FROM users").fetchone()
        return row["cnt"] if row else 0

    def has_users(self) -> bool:
        return self.user_count() > 0

    # ===== Cross-User Sharing =====

    def create_share(
        self, from_user_id: str, to_user_id: str, doc_name: str,
        encrypted_file_key: bytes, encrypted_file_data: bytes,
    ) -> ShareRecord:
        if not self._conn:
            raise RuntimeError("UserRegistry not open")
        share_id = uuid.uuid4().hex[:16]
        now = datetime.now(timezone.utc).isoformat()
        self._conn.execute(
            "INSERT INTO shares (share_id, from_user_id, to_user_id, doc_name, "
            "encrypted_file_key, encrypted_file_data, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (share_id, from_user_id, to_user_id, doc_name, encrypted_file_key, encrypted_file_data, now),
        )
        self._conn.commit()
        return ShareRecord(
            share_id=share_id, from_user_id=from_user_id, to_user_id=to_user_id,
            doc_name=doc_name, encrypted_file_key=encrypted_file_key,
            encrypted_file_data=encrypted_file_data, created_at=now,
        )

    def list_shares_for_user(self, user_id: str) -> list[ShareRecord]:
        if not self._conn:
            return []
        rows = self._conn.execute(
            "SELECT * FROM shares WHERE to_user_id = ? ORDER BY created_at DESC", (user_id,)
        ).fetchall()
        return [
            ShareRecord(
                share_id=r["share_id"], from_user_id=r["from_user_id"],
                to_user_id=r["to_user_id"], doc_name=r["doc_name"],
                encrypted_file_key=r["encrypted_file_key"],
                encrypted_file_data=r["encrypted_file_data"],
                created_at=r["created_at"],
            )
            for r in rows
        ]

    def get_share(self, share_id: str) -> Optional[ShareRecord]:
        if not self._conn:
            return None
        row = self._conn.execute("SELECT * FROM shares WHERE share_id = ?", (share_id,)).fetchone()
        if not row:
            return None
        return ShareRecord(
            share_id=row["share_id"], from_user_id=row["from_user_id"],
            to_user_id=row["to_user_id"], doc_name=row["doc_name"],
            encrypted_file_key=row["encrypted_file_key"],
            encrypted_file_data=row["encrypted_file_data"],
            created_at=row["created_at"],
        )

    def delete_share(self, share_id: str) -> bool:
        if not self._conn:
            return False
        cursor = self._conn.execute("DELETE FROM shares WHERE share_id = ?", (share_id,))
        self._conn.commit()
        return cursor.rowcount > 0
