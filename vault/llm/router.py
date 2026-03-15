"""
LLM Router — switches between cloud and local (Ollama) based on config.

In paranoid mode, all calls go through Ollama locally. Zero network activity.
"""

from __future__ import annotations

import json
import logging
from typing import Any, Optional

logger = logging.getLogger(__name__)


class LLMRouter:
    def __init__(
        self,
        provider: str = "openai",
        model: str = "gpt-4o-mini",
        ollama_model: str = "llama3.1:8b",
        paranoid_mode: bool = False,
    ):
        self._provider = provider
        self._model = model
        self._ollama_model = ollama_model
        self._paranoid_mode = paranoid_mode

    @property
    def is_paranoid(self) -> bool:
        return self._paranoid_mode

    def set_paranoid(self, enabled: bool) -> None:
        self._paranoid_mode = enabled
        logger.info("Paranoid mode %s", "enabled" if enabled else "disabled")

    def _get_model_string(self) -> str:
        if self._paranoid_mode:
            return f"ollama/{self._ollama_model}"
        if self._provider == "ollama":
            return f"ollama/{self._ollama_model}"
        return self._model

    async def complete(
        self,
        prompt: str,
        system: Optional[str] = None,
        temperature: float = 0.3,
        max_tokens: int = 1024,
    ) -> str:
        """Send a completion request via litellm."""
        try:
            import litellm

            litellm.telemetry = False

            messages = []
            if system:
                messages.append({"role": "system", "content": system})
            messages.append({"role": "user", "content": prompt})

            response = await litellm.acompletion(
                model=self._get_model_string(),
                messages=messages,
                temperature=temperature,
                max_tokens=max_tokens,
            )
            return response.choices[0].message.content or ""
        except ImportError:
            logger.error("litellm not installed. Install with: pip install litellm")
            return "Error: LLM integration not available. Install litellm."
        except Exception as e:
            logger.error("LLM call failed: %s", e)
            return f"Error communicating with LLM: {e}"

    async def detect_intent(self, message: str) -> dict[str, Any]:
        """Use the LLM to detect user intent from a message."""
        from vault.llm.prompts import INTENT_DETECTION_PROMPT

        prompt = INTENT_DETECTION_PROMPT.format(message=message)
        response = await self.complete(prompt, temperature=0.1, max_tokens=256)

        try:
            cleaned = response.strip()
            if cleaned.startswith("```"):
                cleaned = cleaned.split("\n", 1)[1].rsplit("```", 1)[0]
            return json.loads(cleaned)
        except (json.JSONDecodeError, IndexError):
            return {"intent": "general", "entities": {}, "confidence": 0.5}

    async def answer_document_question(self, question: str, doc_name: str, doc_text: str) -> str:
        from vault.llm.prompts import DOCUMENT_QA_PROMPT

        prompt = DOCUMENT_QA_PROMPT.format(
            doc_name=doc_name,
            doc_text=doc_text[:4000],
            question=question,
        )
        return await self.complete(prompt, temperature=0.1)

    async def extract_facts(self, message: str) -> list[dict[str, str]]:
        from vault.llm.prompts import FACT_EXTRACTION_PROMPT

        prompt = FACT_EXTRACTION_PROMPT.format(message=message)
        response = await self.complete(prompt, temperature=0.1, max_tokens=512)

        try:
            cleaned = response.strip()
            if cleaned.startswith("```"):
                cleaned = cleaned.split("\n", 1)[1].rsplit("```", 1)[0]
            facts = json.loads(cleaned)
            if isinstance(facts, list):
                return facts
            return []
        except (json.JSONDecodeError, IndexError):
            return []

    async def extract_birthdays(self, message: str) -> list[dict[str, str]]:
        from vault.llm.prompts import BIRTHDAY_EXTRACTION_PROMPT

        prompt = BIRTHDAY_EXTRACTION_PROMPT.format(message=message)
        response = await self.complete(prompt, temperature=0.1, max_tokens=2048)

        try:
            cleaned = response.strip()
            if cleaned.startswith("```"):
                cleaned = cleaned.split("\n", 1)[1].rsplit("```", 1)[0]
            entries = json.loads(cleaned)
            if isinstance(entries, list):
                return [e for e in entries if e.get("name") and e.get("date")]
            return []
        except (json.JSONDecodeError, IndexError):
            return []
