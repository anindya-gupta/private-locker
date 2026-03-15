"""
Personal memory/facts store.

Stores key-value facts about the user (blood type, allergies, preferences, etc.)
with local-first lookup and optional LLM extraction.
"""

from __future__ import annotations

import re
from typing import Any, Optional

from vault.storage.database import VaultDatabase


class MemoryManager:
    def __init__(self, db: VaultDatabase):
        self._db = db

    def remember(
        self,
        key: str,
        value: str,
        encryption_key: bytes,
        category: str = "general",
    ) -> str:
        return self._db.store_fact(
            key=key,
            value=value,
            encryption_key=encryption_key,
            category=category,
            source="user",
        )

    def recall(self, key: str, encryption_key: bytes) -> Optional[str]:
        fact = self._db.get_fact(key, encryption_key)
        if fact:
            return fact["value"]
        return None

    def search(self, query: str, encryption_key: bytes) -> list[dict[str, Any]]:
        return self._db.search_facts(query, encryption_key)

    def list_all(self, encryption_key: bytes, category: Optional[str] = None) -> list[dict[str, Any]]:
        return self._db.list_facts(encryption_key, category)

    def forget(self, fact_id: str) -> bool:
        return self._db.delete_fact(fact_id)

    def store_birthdays_bulk(
        self,
        entries: list[dict[str, str]],
        encryption_key: bytes,
    ) -> int:
        """Store multiple birthday entries. Each entry: {name, date}. Returns count stored."""
        count = 0
        for entry in entries:
            name = entry.get("name", "").strip()
            date_str = entry.get("date", "").strip()
            if name and date_str:
                self._db.store_fact(
                    key=name.lower(),
                    value=date_str,
                    encryption_key=encryption_key,
                    category="birthday",
                    source="user",
                )
                count += 1
        return count

    @staticmethod
    def parse_remember_input(text: str) -> tuple[Optional[str], Optional[str], str]:
        """
        Parse natural language 'remember' statements.
        Returns (key, value, category).

        Examples:
          "Remember my blood type is O+"  -> ("blood type", "O+", "medical")
          "My birthday is Jan 15"         -> ("birthday", "Jan 15", "personal")
          "Remember that I'm allergic to peanuts" -> ("allergy", "peanuts", "medical")
        """
        patterns = [
            (r"(?:remember\s+)?(?:that\s+)?my\s+(.+?)\s+is\s+(.+)", None),
            (r"(?:remember\s+)?(?:that\s+)?i(?:'m|\s+am)\s+allergic\s+to\s+(.+)", ("allergy", None)),
            (r"(?:remember\s+)?(?:that\s+)?i\s+(?:have|got)\s+(.+)", ("condition", None)),
            (r"(?:remember\s+)?(?:that\s+)?i\s+(?:like|prefer|love)\s+(.+)", ("preference", None)),
        ]

        for pattern, override in patterns:
            match = re.search(pattern, text, re.IGNORECASE)
            if match:
                groups = match.groups()
                if override:
                    key = override[0]
                    value = groups[0].strip().rstrip(".")
                else:
                    key = groups[0].strip().rstrip(".")
                    value = groups[1].strip().rstrip(".") if len(groups) > 1 else groups[0].strip().rstrip(".")
                category = _guess_fact_category(key)
                return key, value, category

        return None, None, "general"

    @staticmethod
    def format_facts(facts: list[dict[str, Any]]) -> str:
        if not facts:
            return "No facts stored yet."
        lines = []
        current_cat = None
        for fact in sorted(facts, key=lambda f: (f["category"], f["key"])):
            if fact["category"] != current_cat:
                current_cat = fact["category"]
                lines.append(f"\n[{current_cat.upper()}]")
            lines.append(f"  {fact['key']}: {fact['value']}")
        return "\n".join(lines)


def _guess_fact_category(key: str) -> str:
    lower = key.lower()
    if any(kw in lower for kw in ["birthday", "birth date", "birthdate", "bday", "dob"]):
        return "birthday"
    categories = {
        "medical": ["blood", "allergy", "allergic", "medication", "doctor", "hospital", "condition", "diagnosis"],
        "personal": ["birth", "age", "name", "address", "phone", "email", "spouse", "partner"],
        "financial": ["bank", "account", "salary", "income", "pan", "tax"],
        "preferences": ["like", "prefer", "favorite", "colour", "color", "food", "hobby"],
        "work": ["company", "job", "office", "manager", "designation", "employee"],
    }
    for cat, keywords in categories.items():
        if any(kw in lower for kw in keywords):
            return cat
    return "general"
