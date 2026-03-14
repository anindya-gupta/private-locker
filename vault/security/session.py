"""
Session management with auto-lock timeout.

Holds derived keys in memory only while unlocked.
Keys are zeroed out on lock.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Optional

from vault.security.encryption import DerivedKeys, derive_all_keys, verify_password

DEFAULT_TIMEOUT_SECONDS = 300  # 5 minutes


@dataclass
class Session:
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
        """Configure session with vault parameters (called on startup)."""
        self._salt = salt
        self._verification_token = verification_token
        self._timeout = timeout

    def unlock(self, password: str) -> bool:
        """Unlock the session by verifying the master password and deriving keys."""
        if self._salt is None or self._verification_token is None:
            raise RuntimeError("Session not configured. Run 'vault init' first.")

        if not verify_password(password, self._salt, self._verification_token):
            return False

        self._keys = derive_all_keys(password, self._salt)
        self._locked = False
        self._touch()
        return True

    def lock(self) -> None:
        """Lock the session and wipe keys from memory."""
        self._keys = None
        self._locked = True
        self._last_activity = 0.0

    def _touch(self) -> None:
        """Update the last activity timestamp."""
        self._last_activity = time.time()

    def set_timeout(self, seconds: int) -> None:
        self._timeout = seconds


session = Session()
