"""
Credential manager.

All credential operations are 100% local.
Credentials are double-encrypted (DB-level + field-level with cred_key).
The LLM NEVER sees credential data.
"""

from __future__ import annotations

import re
from typing import Any, Optional

from vault.storage.database import VaultDatabase


class CredentialManager:
    def __init__(self, db: VaultDatabase):
        self._db = db

    def store(
        self,
        service: str,
        cred_key: bytes,
        username: Optional[str] = None,
        password: Optional[str] = None,
        url: Optional[str] = None,
        notes: Optional[str] = None,
    ) -> str:
        return self._db.store_credential(
            service=service,
            cred_key=cred_key,
            username=username,
            password=password,
            url=url,
            notes=notes,
        )

    def get(self, service: str, cred_key: bytes) -> Optional[dict[str, Any]]:
        return self._db.get_credential(service, cred_key)

    def list_all(self, cred_key: bytes) -> list[dict[str, Any]]:
        return self._db.list_credentials(cred_key)

    def update(self, cred_id: str, cred_key: bytes, **kwargs: Any) -> bool:
        return self._db.update_credential(cred_id, cred_key, **kwargs)

    def delete(self, cred_id: str) -> bool:
        return self._db.delete_credential(cred_id)

    @staticmethod
    def parse_credential_input(text: str) -> dict[str, Optional[str]]:
        """
        Try to extract service, username, password from natural language.
        E.g. "My Netflix login is user@email.com, password is xyz123"
        """
        result: dict[str, Optional[str]] = {
            "service": None,
            "username": None,
            "password": None,
            "url": None,
        }

        service_patterns = [
            r"(?:my\s+)?(\w+)\s+(?:login|password|credentials?|account)",
            r"(?:for|on)\s+(\w+)",
            r"(\w+)\s+(?:username|user|email|id)\s+is",
        ]
        for pattern in service_patterns:
            match = re.search(pattern, text, re.IGNORECASE)
            if match:
                result["service"] = match.group(1).strip()
                break

        user_patterns = [
            r"(?:username|user|login|email|id)\s+(?:is|:)\s*([^\s,]+)",
            r"([a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+)",
        ]
        for pattern in user_patterns:
            match = re.search(pattern, text, re.IGNORECASE)
            if match:
                result["username"] = match.group(1).strip()
                break

        pass_patterns = [
            r"(?:password|pass|pwd)\s+(?:is|:)\s*([^\s,]+)",
            r"(?:password|pass|pwd)\s*[:=]\s*([^\s,]+)",
        ]
        for pattern in pass_patterns:
            match = re.search(pattern, text, re.IGNORECASE)
            if match:
                result["password"] = match.group(1).strip()
                break

        url_match = re.search(r"(https?://[^\s,]+)", text)
        if url_match:
            result["url"] = url_match.group(1)

        return result

    @staticmethod
    def format_credential(cred: dict[str, Any], mask_password: bool = True) -> str:
        """Format a credential for display."""
        lines = [f"Service: {cred['service']}"]
        if cred.get("username"):
            lines.append(f"Username: {cred['username']}")
        if cred.get("password"):
            if mask_password:
                lines.append(f"Password: {'*' * len(cred['password'])}")
            else:
                lines.append(f"Password: {cred['password']}")
        if cred.get("url"):
            lines.append(f"URL: {cred['url']}")
        if cred.get("notes"):
            lines.append(f"Notes: {cred['notes']}")
        return "\n".join(lines)
