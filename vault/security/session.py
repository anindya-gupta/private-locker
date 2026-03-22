"""
Per-client session management with token-based auth.

Each browser/device gets its own session with independent
auto-lock timeout. Keys are held in memory only while unlocked.
"""

from __future__ import annotations

import secrets
import time
from dataclasses import dataclass, field
from typing import Optional

from vault.security.encryption import DerivedKeys, derive_all_keys, verify_password_and_derive_keys

DEFAULT_TIMEOUT_SECONDS = 300
SESSION_TOKEN_BYTES = 32


@dataclass
class Session:
    """Single-client session. Used by CLI and as the building block for SessionStore."""
    _keys: Optional[DerivedKeys] = field(default=None, repr=False)
    _last_activity: float = 0.0
    _timeout: int = DEFAULT_TIMEOUT_SECONDS
    _salt: Optional[bytes] = None
    _verification_token: Optional[bytes] = None
    _locked: bool = True
    _last_doc_id: Optional[str] = None
    _last_doc_name: Optional[str] = None

    @property
    def is_locked(self) -> bool:
        if self._locked or self._keys is None:
            return True
        if time.time() - self._last_activity > self._timeout:
            self.lock()
            return True
        return False

    @property
    def keys(self) -> DerivedKeys:
        if self.is_locked:
            raise PermissionError("Session is locked. Unlock with your master password.")
        self._touch()
        return self._keys  # type: ignore[return-value]

    def configure(self, salt: bytes, verification_token: bytes, timeout: int = DEFAULT_TIMEOUT_SECONDS) -> None:
        self._salt = salt
        self._verification_token = verification_token
        self._timeout = timeout

    def unlock(self, password: str) -> bool:
        if self._salt is None or self._verification_token is None:
            raise RuntimeError("Session not configured. Run 'vault init' first.")
        keys = verify_password_and_derive_keys(password, self._salt, self._verification_token)
        if keys is None:
            return False
        self._keys = keys
        self._locked = False
        self._touch()
        return True

    def lock(self) -> None:
        self._keys = None
        self._locked = True
        self._last_activity = 0.0

    def _touch(self) -> None:
        self._last_activity = time.time()

    def set_timeout(self, seconds: int) -> None:
        self._timeout = seconds


@dataclass
class SessionEntry:
    """Session bound to a specific user."""
    user_id: str
    session: Session


class SessionStore:
    """Per-client session store for the web server. Maps tokens -> (user_id, Session)."""

    def __init__(self) -> None:
        self._entries: dict[str, SessionEntry] = {}
        self._timeout: int = DEFAULT_TIMEOUT_SECONDS
        # Legacy single-user fields (kept for backward compat with CLI)
        self._salt: Optional[bytes] = None
        self._verification_token: Optional[bytes] = None
        self._username: Optional[str] = None

    def configure(self, salt: bytes, verification_token: bytes,
                  timeout: int = DEFAULT_TIMEOUT_SECONDS,
                  username: Optional[str] = None) -> None:
        self._salt = salt
        self._verification_token = verification_token
        self._timeout = timeout
        self._username = username

    @property
    def is_configured(self) -> bool:
        return self._salt is not None and self._verification_token is not None

    @property
    def username(self) -> Optional[str]:
        return self._username

    @username.setter
    def username(self, value: Optional[str]) -> None:
        self._username = value

    def unlock(self, password: str, username: Optional[str] = None) -> Optional[str]:
        """Legacy single-user unlock. Use unlock_user() for multi-user."""
        if self._salt is None or self._verification_token is None:
            raise RuntimeError("SessionStore not configured.")
        if self._username and username != self._username:
            return None
        keys = verify_password_and_derive_keys(password, self._salt, self._verification_token)
        if keys is None:
            return None
        token = secrets.token_urlsafe(SESSION_TOKEN_BYTES)
        client = Session()
        client.configure(self._salt, self._verification_token, self._timeout)
        client._keys = keys
        client._locked = False
        client._touch()
        self._entries[token] = SessionEntry(user_id="__legacy__", session=client)
        return token

    def unlock_user(self, user_id: str, password: str,
                    salt: bytes, verification_token: bytes) -> Optional[str]:
        """Multi-user unlock: verify password against user-specific salt/token."""
        keys = verify_password_and_derive_keys(password, salt, verification_token)
        if keys is None:
            return None
        token = secrets.token_urlsafe(SESSION_TOKEN_BYTES)
        client = Session()
        client.configure(salt, verification_token, self._timeout)
        client._keys = keys
        client._locked = False
        client._touch()
        self._entries[token] = SessionEntry(user_id=user_id, session=client)
        return token

    def get(self, token: Optional[str]) -> Optional[Session]:
        if not token:
            return None
        entry = self._entries.get(token)
        if entry is None:
            return None
        if entry.session.is_locked:
            del self._entries[token]
            return None
        return entry.session

    def get_user_id(self, token: Optional[str]) -> Optional[str]:
        if not token:
            return None
        entry = self._entries.get(token)
        if entry is None:
            return None
        if entry.session.is_locked:
            del self._entries[token]
            return None
        return entry.user_id

    def lock(self, token: str) -> None:
        entry = self._entries.get(token)
        if entry:
            entry.session.lock()
            del self._entries[token]

    def destroy(self, token: str) -> None:
        entry = self._entries.pop(token, None)
        if entry:
            entry.session.lock()

    def lock_all(self) -> None:
        for entry in self._entries.values():
            entry.session.lock()
        self._entries.clear()

    def active_count(self) -> int:
        return len(self._entries)


session_store = SessionStore()

# Legacy global for CLI usage
session = Session()
