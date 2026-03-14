"""
Core agent — the brain of Vault.

Routes user queries to the right handler:
  - Credential lookups -> local only, never LLM
  - Fact recalls -> local DB first, LLM only if complex
  - Document queries -> vector search + LLM reasoning
  - Store commands -> appropriate storage backend
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Any, Optional

from vault.config import VaultConfig
from vault.llm.router import LLMRouter
from vault.processors.credentials import CredentialManager
from vault.processors.document import extract_text, guess_category
from vault.processors.memory import MemoryManager
from vault.security.session import Session
from vault.storage.database import VaultDatabase
from vault.storage.file_vault import FileVault
from vault.storage.vector_store import VectorStore

logger = logging.getLogger(__name__)


@dataclass
class AgentResponse:
    text: str
    data: Optional[dict[str, Any]] = None
    file_data: Optional[bytes] = None
    file_name: Optional[str] = None


class VaultAgent:
    def __init__(self, config: VaultConfig, session: Session):
        self.config = config
        self.session = session
        self.db = VaultDatabase(config.db_path)
        self.file_vault = FileVault(config.files_dir)
        self.vector_store = VectorStore(config.chroma_dir, config.embedding_model)
        self.cred_manager = CredentialManager(self.db)
        self.memory = MemoryManager(self.db)
        self.llm = LLMRouter(
            provider=config.llm_provider,
            model=config.llm_model,
            ollama_model=config.ollama_model,
            paranoid_mode=config.paranoid_mode,
        )

    def initialize(self) -> None:
        self.db.open()
        self.db.initialize_schema()
        self.vector_store.initialize()

    def shutdown(self) -> None:
        self.db.close()
        self.session.lock()

    async def process(self, message: str, file_data: Optional[bytes] = None, file_name: Optional[str] = None) -> AgentResponse:
        """Main entry point — process a user message and return a response."""
        if self.session.is_locked:
            return AgentResponse(text="Vault is locked. Please unlock with your master password first.")

        keys = self.session.keys

        if file_data and file_name:
            return await self._handle_store_document(message, file_data, file_name, keys)

        local_result = self._try_local_resolution(message, keys)
        if local_result:
            return local_result

        intent = await self.llm.detect_intent(message)
        intent_type = intent.get("intent", "general")
        entities = intent.get("entities", {})

        handlers = {
            "store_credential": self._handle_store_credential,
            "retrieve_credential": self._handle_retrieve_credential,
            "remember_fact": self._handle_remember_fact,
            "recall_fact": self._handle_recall_fact,
            "query_document": self._handle_query_document,
            "retrieve_document": self._handle_retrieve_document,
            "store_document": self._handle_store_document_prompt,
            "list_items": self._handle_list_items,
            "delete_item": self._handle_delete_item,
        }

        handler = handlers.get(intent_type)
        if handler:
            return await handler(message, entities, keys)

        return await self._handle_general(message, keys)

    def _try_local_resolution(self, message: str, keys: Any) -> Optional[AgentResponse]:
        """Try to answer without LLM — purely local lookups."""
        lower = message.lower().strip()

        cred_patterns = [
            r"(?:what(?:'s| is))\s+my\s+(\w+)\s+(?:password|login|username|credentials?)",
            r"(?:show|get|retrieve|give)\s+(?:me\s+)?(?:my\s+)?(\w+)\s+(?:password|login|credentials?)",
            r"(\w+)\s+(?:password|login|username)",
        ]
        for pattern in cred_patterns:
            match = re.search(pattern, lower)
            if match:
                service = match.group(1)
                cred = self.cred_manager.get(service, keys.cred_key)
                if cred:
                    return AgentResponse(
                        text=self.cred_manager.format_credential(cred, mask_password=False),
                        data=cred,
                    )

        fact_patterns = [
            r"what(?:'s| is)\s+my\s+(.+?)(?:\?|$)",
            r"(?:do you (?:know|remember))\s+my\s+(.+?)(?:\?|$)",
        ]
        for pattern in fact_patterns:
            match = re.search(pattern, lower)
            if match:
                key = match.group(1).strip().rstrip("?")
                value = self.memory.recall(key, keys.db_key)
                if value:
                    return AgentResponse(text=f"Your {key} is: {value}")

        remember_patterns = [
            r"(?:remember|note|save)\s+(?:that\s+)?my\s+(.+?)\s+is\s+(.+?)(?:\.|$)",
            r"my\s+(.+?)\s+is\s+(.+?)(?:\.|$)",
        ]
        if any(word in lower for word in ["remember", "note that", "save that"]):
            for pattern in remember_patterns:
                match = re.search(pattern, lower)
                if match:
                    key = match.group(1).strip()
                    value = match.group(2).strip().rstrip(".")
                    category = MemoryManager.parse_remember_input(message)[2]
                    self.memory.remember(key, value, keys.db_key, category)
                    return AgentResponse(text=f"Got it! I'll remember that your {key} is {value}.")

        if lower in ("list credentials", "show credentials", "show my passwords", "list passwords"):
            creds = self.cred_manager.list_all(keys.cred_key)
            if not creds:
                return AgentResponse(text="No credentials stored yet.")
            lines = [f"Stored credentials ({len(creds)}):"]
            for c in creds:
                lines.append(f"  - {c['service']}: {c.get('username', 'N/A')}")
            return AgentResponse(text="\n".join(lines))

        if lower in ("list facts", "show facts", "what do you know about me", "what do you remember"):
            facts = self.memory.list_all(keys.db_key)
            return AgentResponse(text=MemoryManager.format_facts(facts))

        if lower in ("list documents", "show documents", "show my documents", "my documents"):
            docs = self.db.list_documents(keys.db_key)
            if not docs:
                return AgentResponse(text="No documents stored yet.")
            lines = [f"Stored documents ({len(docs)}):"]
            for d in docs:
                lines.append(f"  - [{d['category']}] {d['name']}")
            return AgentResponse(text="\n".join(lines))

        if lower in ("lock", "lock vault", "lock the vault"):
            self.session.lock()
            return AgentResponse(text="Vault locked. Stay safe!")

        return None

    async def _handle_store_document(
        self,
        message: str,
        file_data: bytes,
        file_name: str,
        keys: Any,
    ) -> AgentResponse:
        file_ref = self.file_vault.store(file_data, keys.file_key, file_name)

        extracted = extract_text(file_data, file_name)
        category = guess_category(file_name, extracted)

        name = message.strip() if message.strip() and message.strip().lower() not in ("", "store", "upload") else file_name

        doc_id = self.db.store_document(
            name=name,
            category=category,
            encryption_key=keys.db_key,
            file_ref=file_ref,
            extracted_text=extracted or None,
            tags=[category, file_name.split(".")[-1]],
        )

        if extracted:
            self.vector_store.add_document(doc_id, extracted, {"name": name, "category": category})

        summary = f"Stored '{name}' under [{category}]."
        if extracted:
            preview = extracted[:200] + "..." if len(extracted) > 200 else extracted
            summary += f"\n\nExtracted text preview:\n{preview}"
        else:
            summary += "\n(No text could be extracted from this file.)"

        return AgentResponse(text=summary, data={"doc_id": doc_id, "category": category})

    async def _handle_store_credential(self, message: str, entities: dict, keys: Any) -> AgentResponse:
        parsed = CredentialManager.parse_credential_input(message)
        service = parsed.get("service") or entities.get("service")

        if not service:
            return AgentResponse(text="Which service is this for? (e.g., 'Netflix', 'Gmail')")

        cred_id = self.cred_manager.store(
            service=service,
            cred_key=keys.cred_key,
            username=parsed.get("username"),
            password=parsed.get("password"),
            url=parsed.get("url"),
        )
        parts = [f"Saved credentials for {service}."]
        if parsed.get("username"):
            parts.append(f"Username: {parsed['username']}")
        if parsed.get("password"):
            parts.append("Password: [stored securely]")
        return AgentResponse(text="\n".join(parts))

    async def _handle_retrieve_credential(self, message: str, entities: dict, keys: Any) -> AgentResponse:
        service = entities.get("service")
        if not service:
            match = re.search(r"(\w+)\s+(?:password|login|credentials?|account)", message, re.IGNORECASE)
            if match:
                service = match.group(1)

        if not service:
            return AgentResponse(text="Which service do you want credentials for?")

        cred = self.cred_manager.get(service, keys.cred_key)
        if cred:
            return AgentResponse(
                text=self.cred_manager.format_credential(cred, mask_password=False),
                data=cred,
            )
        return AgentResponse(text=f"No credentials found for '{service}'.")

    async def _handle_remember_fact(self, message: str, entities: dict, keys: Any) -> AgentResponse:
        key_parsed, value_parsed, category = MemoryManager.parse_remember_input(message)

        if key_parsed and value_parsed:
            self.memory.remember(key_parsed, value_parsed, keys.db_key, category)
            return AgentResponse(text=f"Got it! I'll remember that your {key_parsed} is {value_parsed}.")

        facts = await self.llm.extract_facts(message)
        if facts:
            stored = []
            for fact in facts:
                k, v = fact.get("key", ""), fact.get("value", "")
                if k and v:
                    self.memory.remember(k, v, keys.db_key)
                    stored.append(f"{k}: {v}")
            if stored:
                return AgentResponse(text="Remembered:\n" + "\n".join(f"  - {s}" for s in stored))

        return await self._handle_general(message, keys)

    async def _handle_recall_fact(self, message: str, entities: dict, keys: Any) -> AgentResponse:
        key = entities.get("key", "")
        if key:
            value = self.memory.recall(key, keys.db_key)
            if value:
                return AgentResponse(text=f"Your {key} is: {value}")

        match = re.search(r"what(?:'s| is)\s+my\s+(.+?)(?:\?|$)", message, re.IGNORECASE)
        if match:
            key = match.group(1).strip()
            value = self.memory.recall(key, keys.db_key)
            if value:
                return AgentResponse(text=f"Your {key} is: {value}")

        facts = self.memory.search(message, keys.db_key)
        if facts:
            return AgentResponse(text=MemoryManager.format_facts(facts))

        doc_result = await self._try_document_answer(message, keys)
        if doc_result:
            return doc_result

        return AgentResponse(text="I don't have that information. You can tell me and I'll remember it.")

    async def _handle_query_document(self, message: str, entities: dict, keys: Any) -> AgentResponse:
        doc = None

        results = self.vector_store.search(message, n_results=3)
        if results:
            doc = self.db.get_document(results[0]["id"], keys.db_key)

        if not doc or not doc.get("extracted_text"):
            docs = self.db.search_documents(message, keys.db_key)
            if docs:
                for d in docs:
                    if d.get("extracted_text"):
                        doc = d
                        break

        if not doc or not doc.get("extracted_text"):
            all_docs = self.db.list_documents(keys.db_key)
            for d in all_docs:
                if d.get("extracted_text"):
                    doc = d
                    break

        if not doc or not doc.get("extracted_text"):
            return AgentResponse(text="I couldn't find any documents with readable text. Try uploading the document first.")

        answer = await self.llm.answer_document_question(message, doc["name"], doc["extracted_text"])
        return AgentResponse(text=answer, data={"source_document": doc["name"]})

    async def _handle_retrieve_document(self, message: str, entities: dict, keys: Any) -> AgentResponse:
        doc_name = entities.get("document", "")
        docs = self.db.search_documents(doc_name or message, keys.db_key)

        if not docs:
            return AgentResponse(text=f"No documents matching '{doc_name or message}' found.")

        doc = docs[0]
        if doc.get("file_ref"):
            try:
                file_data, original_name = self.file_vault.retrieve(doc["file_ref"], keys.file_key)
                return AgentResponse(
                    text=f"Here's your document: {doc['name']}",
                    file_data=file_data,
                    file_name=original_name,
                )
            except FileNotFoundError:
                return AgentResponse(text=f"Document '{doc['name']}' record exists but the file is missing.")

        return AgentResponse(text=f"Document '{doc['name']}' found but has no attached file.")

    async def _handle_store_document_prompt(self, message: str, entities: dict, keys: Any) -> AgentResponse:
        return AgentResponse(text="Please upload a file along with your message. You can drag and drop it in the web UI or use the CLI upload command.")

    async def _handle_list_items(self, message: str, entities: dict, keys: Any) -> AgentResponse:
        lower = message.lower()
        parts = []

        if "credential" in lower or "password" in lower or "login" in lower:
            creds = self.cred_manager.list_all(keys.cred_key)
            if creds:
                parts.append(f"Credentials ({len(creds)}):")
                for c in creds:
                    parts.append(f"  - {c['service']}: {c.get('username', 'N/A')}")
        elif "fact" in lower or "remember" in lower or "know" in lower:
            facts = self.memory.list_all(keys.db_key)
            parts.append(MemoryManager.format_facts(facts))
        elif "document" in lower or "file" in lower:
            docs = self.db.list_documents(keys.db_key)
            if docs:
                parts.append(f"Documents ({len(docs)}):")
                for d in docs:
                    parts.append(f"  - [{d['category']}] {d['name']}")
            else:
                parts.append("No documents stored.")
        else:
            docs = self.db.list_documents(keys.db_key)
            creds = self.cred_manager.list_all(keys.cred_key)
            facts = self.memory.list_all(keys.db_key)

            if docs:
                parts.append(f"Documents ({len(docs)}):")
                for d in docs:
                    parts.append(f"  - [{d['category']}] {d['name']}")
            if creds:
                parts.append(f"\nCredentials ({len(creds)}):")
                for c in creds:
                    parts.append(f"  - {c['service']}: {c.get('username', 'N/A')}")
            if facts:
                parts.append("\nFacts:")
                parts.append(MemoryManager.format_facts(facts))
            if not parts:
                parts.append("Your vault is empty. Start by storing a document, credential, or telling me something about yourself.")

        return AgentResponse(text="\n".join(parts))

    async def _handle_delete_item(self, message: str, entities: dict, keys: Any) -> AgentResponse:
        return AgentResponse(text="To delete an item, please specify what you want to remove (e.g., 'delete my Netflix credentials' or 'delete Aadhaar document').")

    async def _try_document_answer(self, message: str, keys: Any) -> Optional[AgentResponse]:
        """Try to answer a question from stored documents. Returns None if no docs help."""
        doc = None

        results = self.vector_store.search(message, n_results=3)
        if results:
            doc = self.db.get_document(results[0]["id"], keys.db_key)

        if not doc or not doc.get("extracted_text"):
            all_docs = self.db.list_documents(keys.db_key)
            for d in all_docs:
                if d.get("extracted_text"):
                    doc = d
                    break

        if not doc or not doc.get("extracted_text"):
            return None

        answer = await self.llm.answer_document_question(message, doc["name"], doc["extracted_text"])
        if answer and "not" not in answer.lower()[:30] and "sorry" not in answer.lower()[:30]:
            return AgentResponse(text=answer, data={"source_document": doc["name"]})

        return None

    async def _handle_general(self, message: str, keys: Any) -> AgentResponse:
        doc_result = await self._try_document_answer(message, keys)
        if doc_result:
            return doc_result

        from vault.llm.prompts import SYSTEM_PROMPT
        response = await self.llm.complete(message, system=SYSTEM_PROMPT)
        return AgentResponse(text=response)
