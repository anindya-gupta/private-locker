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

from vault.security.encryption import DerivedKeys, derive_all_keys, verify_password

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
        if not verify_password(password, self._salt, self._verification_token):
            return False
        self._keys = derive_all_keys(password, self._salt)
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


class SessionStore:
    """Per-client session store for the web server. Maps tokens -> DerivedKeys."""

    def __init__(self) -> None:
        self._sessions: dict[str, Session] = {}
        self._salt: Optional[bytes] = None
        self._verification_token: Optional[bytes] = None
        self._timeout: int = DEFAULT_TIMEOUT_SECONDS
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
        """Verify credentials, create a new client session, return the token (or None)."""
        if self._salt is None or self._verification_token is None:
            raise RuntimeError("SessionStore not configured.")

        if self._username and username != self._username:
            return None

        if not verify_password(password, self._salt, self._verification_token):
            return None

        keys = derive_all_keys(password, self._salt)
        token = secrets.token_urlsafe(SESSION_TOKEN_BYTES)
        client = Session()
        client.configure(self._salt, self._verification_token, self._timeout)
        client._keys = keys
        client._locked = False
        client._touch()
        self._sessions[token] = client
        return token

    def get(self, token: Optional[str]) -> Optional[Session]:
        if not token:
            return None
        client = self._sessions.get(token)
        if client is None:
            return None
        if client.is_locked:
            del self._sessions[token]
            return None
        return client

    def lock(self, token: str) -> None:
        client = self._sessions.get(token)
        if client:
            client.lock()
            del self._sessions[token]

    def destroy(self, token: str) -> None:
        client = self._sessions.pop(token, None)
        if client:
            client.lock()

    def lock_all(self) -> None:
        for client in self._sessions.values():
            client.lock()
        self._sessions.clear()

    def active_count(self) -> int:
        return len(self._sessions)


session_store = SessionStore()

# Legacy global for CLI usage
session = Session()
