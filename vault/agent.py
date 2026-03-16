"""
Core agent — the brain of Vault.

Routes user queries to the right handler:
  - Credential lookups -> local only, never LLM
  - Fact recalls -> local DB first, LLM only if complex
  - Document queries -> vector search + LLM reasoning
  - Store commands -> appropriate storage backend
"""

from __future__ import annotations

import csv
import io
import logging
import re
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Optional

from vault.config import VaultConfig
from vault.llm.router import LLMRouter
from vault.processors.credentials import CredentialManager
from vault.processors.document import extract_text, guess_category, extract_document_metadata
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
            if file_name.lower().endswith(".csv") and self._looks_like_birthday_file(file_data):
                return self._handle_birthday_csv(file_data, keys)
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
            "store_birthdays": self._handle_store_birthdays,
            "recall_birthdays": self._handle_recall_birthdays,
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

        birthday_patterns = [
            r"(?:save|store|add|remember)\s+birthday\s*:\s*(.+?)\s*[,\-]\s*(.+)",
            r"(?:save|store|add|remember)\s+(.+?)(?:'s|s)\s+birthday\s+(?:on|is|as|:)\s+(.+)",
            r"(.+?)(?:'s|s)\s+birthday\s+(?:is|on)\s+(.+)",
            r"(?:save|store|add|remember)\s+(?:that\s+)?(.+?)\s+(?:birthday|bday)\s+(?:is|on)\s+(.+)",
            r"birthday\s*:\s*(.+?)\s*[,\-]\s*(.+)",
        ]
        for bp in birthday_patterns:
            birthday_save = re.match(bp, lower)
            if birthday_save:
                name = birthday_save.group(1).strip().rstrip("'s").rstrip("s ")
                date_str = birthday_save.group(2).strip().rstrip(".")
                self.memory.store_birthdays_bulk([{"name": name, "date": date_str}], keys.db_key)
                return AgentResponse(text=f"Saved {name.title()}'s birthday ({date_str}).")

        if lower in ("list birthdays", "show birthdays", "show my birthdays", "upcoming birthdays", "birthdays"):
            bdays = self.memory.list_all(keys.db_key, category="birthday")
            if not bdays:
                return AgentResponse(text="No birthdays stored yet. You can add one with: save birthday: John, March 15")
            return AgentResponse(text=self._format_birthday_list(bdays))

        bday_query = re.search(r"when\s+is\s+(.+?)(?:'s|s)\s+birthday", lower)
        if bday_query:
            name_q = bday_query.group(1).strip().lower()
            bdays = self.memory.list_all(keys.db_key, category="birthday")
            for b in bdays:
                if name_q in b["key"]:
                    return AgentResponse(text=f"{b['key'].title()}'s birthday is {b['value']}.")
            return AgentResponse(text=f"I don't have a birthday stored for '{name_q.title()}'.")

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

        doc_query_triggers = [
            "prescription", "report", "certificate", "document", "doc ",
            "statement", "receipt", "invoice", "policy", "checkup",
        ]
        temporal_words = ["last", "latest", "recent", "newest", "first", "oldest", "earliest", "all my", "show all", "every"]
        if any(t in lower for t in doc_query_triggers) and any(t in lower for t in temporal_words):
            return None  # fall through to LLM -> query_document with smart multi-doc handling

        delete_patterns = [
            r"^(?:delete|remove)\s+(?:my\s+)?(?:the\s+)?(\w+)\s+(?:credential|password|login)s?$",
            r"^(?:delete|remove)\s+(?:my\s+)?(?:the\s+)?(.+?)(?:\s+document|\s+doc|\s+file)$",
            r"^(?:delete|remove|forget)\s+(?:my\s+)?(?:the\s+)?(?:fact\s+(?:about\s+)?|memory\s+(?:about\s+)?)(.+)$",
        ]
        for dp in delete_patterns:
            if re.match(dp, lower):
                return None  # fall through to LLM intent detection -> _handle_delete_item

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

        tags = [category, file_name.split(".")[-1]]
        regex_meta = extract_document_metadata(file_name, extracted, category) if extracted else {}

        llm_meta: dict = {}
        if extracted:
            try:
                llm_meta = await self.llm.extract_document_metadata(name, category, extracted)
            except Exception as e:
                logger.warning("LLM metadata extraction failed: %s", e)

        merged_meta = {**regex_meta, **{k: v for k, v in llm_meta.items() if v}}

        if merged_meta.get("sub_category"):
            tags.append(f"sub:{merged_meta['sub_category']}")
        if merged_meta.get("doctor"):
            tags.append(f"doctor:{merged_meta['doctor']}")
        if merged_meta.get("doc_date"):
            tags.append(f"date:{merged_meta['doc_date']}")
        if merged_meta.get("summary"):
            tags.append(f"summary:{merged_meta['summary']}")
        for kw in merged_meta.get("keywords", []):
            tags.append(f"kw:{kw}")

        vector_meta = {"name": name, "category": category}
        if merged_meta.get("sub_category"):
            vector_meta["sub_category"] = merged_meta["sub_category"]
        if merged_meta.get("doctor"):
            vector_meta["doctor"] = merged_meta["doctor"]
        if merged_meta.get("doc_date"):
            vector_meta["doc_date"] = merged_meta["doc_date"]

        doc_id = self.db.store_document(
            name=name,
            category=category,
            encryption_key=keys.db_key,
            file_ref=file_ref,
            extracted_text=extracted or None,
            tags=tags,
        )

        if extracted:
            self.vector_store.add_document(doc_id, extracted, vector_meta)

        summary = f"Stored '{name}' under [{category}]."
        if merged_meta.get("sub_category"):
            summary += f" Sub-type: {merged_meta['sub_category']}."
        if merged_meta.get("doctor"):
            summary += f" Doctor: {merged_meta['doctor']}."
        if merged_meta.get("doc_date"):
            summary += f" Date: {merged_meta['doc_date']}."
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
        lower = message.lower()

        wants_latest = any(w in lower for w in ["last", "latest", "most recent", "newest", "current"])
        wants_oldest = any(w in lower for w in ["first", "oldest", "earliest"])
        wants_all = any(w in lower for w in ["all my", "show all", "every", "list all", "all of"])

        candidates = self._find_relevant_documents(message, keys)

        if not candidates:
            return AgentResponse(text="I couldn't find any documents matching your query. Try uploading the document first.")

        if wants_latest or wants_oldest:
            sorted_docs = self._sort_documents_by_date(candidates, newest_first=wants_latest)
            doc = sorted_docs[0]
            answer = await self.llm.answer_document_question(
                message, doc["name"], doc["extracted_text"]
            )
            date_info = self._get_doc_date_label(doc)
            source_note = f"\n\n_Source: {doc['name']}"
            if date_info:
                source_note += f" ({date_info})"
            source_note += "_"
            return AgentResponse(text=answer + source_note, data={"source_document": doc["name"]})

        if wants_all or len(candidates) > 1:
            context_parts = []
            for i, doc in enumerate(candidates[:5], 1):
                date_label = self._get_doc_date_label(doc)
                header = f"[Document {i}] {doc['name']}"
                if date_label:
                    header += f" ({date_label})"
                context_parts.append(f"{header}\n{doc['extracted_text'][:1500]}")

            documents_context = "\n\n---\n\n".join(context_parts)
            answer = await self.llm.answer_multi_document_question(message, documents_context)
            sources = [d["name"] for d in candidates[:5]]
            return AgentResponse(text=answer, data={"source_documents": sources})

        doc = candidates[0]
        answer = await self.llm.answer_document_question(message, doc["name"], doc["extracted_text"])
        return AgentResponse(text=answer, data={"source_document": doc["name"]})

    def _find_relevant_documents(self, query: str, keys: Any) -> list[dict]:
        """Find all relevant documents for a query using vector search + keyword fallback."""
        seen_ids: set[str] = set()
        candidates: list[dict] = []

        results = self.vector_store.search(query, n_results=10)
        for r in results:
            doc = self.db.get_document(r["id"], keys.db_key)
            if doc and doc.get("extracted_text") and doc["id"] not in seen_ids:
                doc["_distance"] = r.get("distance", 1.0)
                candidates.append(doc)
                seen_ids.add(doc["id"])

        keyword_docs = self.db.search_documents(query, keys.db_key)
        for d in keyword_docs:
            if d.get("extracted_text") and d["id"] not in seen_ids:
                candidates.append(d)
                seen_ids.add(d["id"])

        lower = query.lower()
        tag_filters = self._extract_query_filters(lower)
        if tag_filters:
            all_docs = self.db.list_documents(keys.db_key)
            for d in all_docs:
                if d["id"] in seen_ids or not d.get("extracted_text"):
                    continue
                doc_tags = " ".join(d.get("tags", [])).lower()
                doc_text = (d.get("name", "") + " " + (d.get("extracted_text") or "")[:500]).lower()
                if any(f in doc_tags or f in doc_text for f in tag_filters):
                    candidates.append(d)
                    seen_ids.add(d["id"])

        return candidates

    @staticmethod
    def _extract_query_filters(query: str) -> list[str]:
        """Pull out filter terms from the query for tag/text matching."""
        filters = []
        medical_terms = {
            "eye": "eye", "vision": "eye", "ophthalmol": "eye", "spectacle": "eye", "glasses": "eye",
            "skin": "skin", "dermatol": "skin",
            "dental": "dental", "tooth": "dental", "teeth": "dental",
            "cardiac": "cardiac", "heart": "cardiac", "ecg": "cardiac",
            "ortho": "orthopedic", "bone": "orthopedic", "fracture": "orthopedic",
            "blood test": "blood", "blood report": "blood", "cbc": "blood",
        }
        for term, subcat in medical_terms.items():
            if term in query:
                filters.append(subcat)

        doctor_match = re.search(r"(?:dr\.?|doctor)\s+(\w+(?:\s+\w+)?)", query, re.IGNORECASE)
        if doctor_match:
            filters.append(doctor_match.group(1).lower())

        return filters

    def _sort_documents_by_date(self, docs: list[dict], newest_first: bool = True) -> list[dict]:
        """Sort documents by date -- tries tag-embedded date first, falls back to created_at."""
        def _get_sort_date(doc: dict) -> float:
            for tag in doc.get("tags", []):
                if tag.startswith("date:"):
                    date_str = tag[5:]
                    try:
                        return datetime.strptime(date_str, "%Y-%m-%d").timestamp()
                    except ValueError:
                        pass
            return doc.get("created_at", 0)

        return sorted(docs, key=_get_sort_date, reverse=newest_first)

    @staticmethod
    def _get_doc_date_label(doc: dict) -> str | None:
        """Get a human-readable date label from a document's tags or created_at."""
        for tag in doc.get("tags", []):
            if tag.startswith("date:"):
                return tag[5:]
        created = doc.get("created_at")
        if created:
            return datetime.fromtimestamp(created).strftime("%b %d, %Y")
        return None

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
        lower = message.lower()

        if any(w in lower for w in ["credential", "password", "login"]):
            service_match = re.search(
                r"(?:delete|remove|forget)\s+(?:my\s+)?(?:the\s+)?(\w+)\s+(?:credential|password|login)",
                lower,
            )
            if not service_match:
                service_match = re.search(
                    r"(?:credential|password|login)\s+(?:for\s+)?(\w+)", lower,
                )
            if service_match:
                service = service_match.group(1)
                creds = self.cred_manager.list_all(keys.cred_key)
                matches = [c for c in creds if service in c["service"].lower()]
                if matches:
                    for c in matches:
                        self.db.delete_credential(c["id"])
                    names = ", ".join(c["service"] for c in matches)
                    return AgentResponse(text=f"Deleted credential(s): {names}")
                return AgentResponse(text=f"No credentials found matching '{service}'.")

        if any(w in lower for w in ["document", "doc", "file"]):
            doc_match = re.search(
                r"(?:delete|remove)\s+(?:my\s+)?(?:the\s+)?(.+?)(?:\s+document|\s+doc|\s+file)",
                lower,
            )
            if not doc_match:
                doc_match = re.search(
                    r"(?:document|doc|file)\s+(?:called\s+|named\s+)?[\"']?(.+?)[\"']?\s*$",
                    lower,
                )
            if doc_match:
                query = doc_match.group(1).strip()
                docs = self.db.search_documents(query, keys.db_key)
                if docs:
                    doc = docs[0]
                    if doc.get("file_ref"):
                        self.file_vault.delete(doc["file_ref"])
                    self.vector_store.delete_document(doc["id"])
                    self.db.delete_document(doc["id"])
                    return AgentResponse(text=f"Deleted document: {doc['name']}")
                return AgentResponse(text=f"No documents found matching '{query}'.")

        if any(w in lower for w in ["fact", "memory", "remember"]):
            fact_match = re.search(
                r"(?:delete|remove|forget)\s+(?:my\s+)?(?:the\s+)?(?:fact\s+(?:about\s+)?|memory\s+(?:about\s+)?)?(.+?)(?:\s+fact|\s+memory)?$",
                lower,
            )
            if fact_match:
                key = fact_match.group(1).strip()
                facts = self.memory.list_all(keys.db_key)
                matches = [f for f in facts if key in f["key"].lower() or key in f["value"].lower()]
                if matches:
                    for f in matches:
                        self.db.delete_fact(f["id"])
                    keys_deleted = ", ".join(f["key"] for f in matches)
                    return AgentResponse(text=f"Deleted fact(s): {keys_deleted}")
                return AgentResponse(text=f"No facts found matching '{key}'.")

        all_docs = self.db.search_documents(message, keys.db_key)
        if all_docs:
            doc = all_docs[0]
            if doc.get("file_ref"):
                self.file_vault.delete(doc["file_ref"])
            self.vector_store.delete_document(doc["id"])
            self.db.delete_document(doc["id"])
            return AgentResponse(text=f"Deleted document: {doc['name']}")

        return AgentResponse(
            text="Please specify what you'd like to delete. For example:\n"
                 "  - \"delete my Netflix credentials\"\n"
                 "  - \"delete Aadhaar document\"\n"
                 "  - \"forget my blood type fact\"\n\n"
                 "You can also delete items directly from the Documents, Credentials, or Memory views."
        )

    async def _try_document_answer(self, message: str, keys: Any) -> Optional[AgentResponse]:
        """Try to answer a question from stored documents. Returns None if no docs help."""
        candidates = self._find_relevant_documents(message, keys)
        if not candidates:
            return None

        if len(candidates) == 1:
            doc = candidates[0]
            answer = await self.llm.answer_document_question(message, doc["name"], doc["extracted_text"])
            if answer and "not" not in answer.lower()[:30] and "sorry" not in answer.lower()[:30]:
                return AgentResponse(text=answer, data={"source_document": doc["name"]})
            return None

        context_parts = []
        for i, doc in enumerate(candidates[:3], 1):
            date_label = self._get_doc_date_label(doc)
            header = f"[Document {i}] {doc['name']}"
            if date_label:
                header += f" ({date_label})"
            context_parts.append(f"{header}\n{doc['extracted_text'][:1500]}")

        documents_context = "\n\n---\n\n".join(context_parts)
        answer = await self.llm.answer_multi_document_question(message, documents_context)
        if answer and "not" not in answer.lower()[:30] and "sorry" not in answer.lower()[:30]:
            return AgentResponse(text=answer, data={"source_documents": [d["name"] for d in candidates[:3]]})

        return None

    async def _handle_store_birthdays(self, message: str, entities: dict, keys: Any) -> AgentResponse:
        """Handle storing one or more birthdays from a chat message."""
        entries = await self.llm.extract_birthdays(message)
        if not entries:
            return AgentResponse(
                text="I couldn't parse any birthdays from that. Try a format like:\n"
                     "  save birthday: John, March 15\n"
                     "or paste a list:\n"
                     "  John - March 15\n"
                     "  Sarah - April 2"
            )
        count = self.memory.store_birthdays_bulk(entries, keys.db_key)
        if count == 1:
            e = entries[0]
            return AgentResponse(text=f"Saved {e['name'].title()}'s birthday ({e['date']}).")
        names = ", ".join(e["name"].title() for e in entries[:5])
        suffix = f" and {count - 5} more" if count > 5 else ""
        return AgentResponse(text=f"Saved {count} birthdays: {names}{suffix}.")

    async def _handle_recall_birthdays(self, message: str, entities: dict, keys: Any) -> AgentResponse:
        """Handle queries about birthdays."""
        bdays = self.memory.list_all(keys.db_key, category="birthday")
        if not bdays:
            return AgentResponse(text="No birthdays stored yet. You can add one with: save birthday: John, March 15")

        name_query = entities.get("key", "")
        if not name_query:
            match = re.search(r"(?:when is|what is)\s+(\w[\w\s]*?)(?:'s)?\s+birthday", message, re.IGNORECASE)
            if match:
                name_query = match.group(1).strip().lower()

        if name_query:
            for b in bdays:
                if name_query in b["key"]:
                    return AgentResponse(text=f"{b['key'].title()}'s birthday is {b['value']}.")
            return AgentResponse(text=f"I don't have a birthday stored for '{name_query.title()}'.")

        return AgentResponse(text=self._format_birthday_list(bdays))

    def _format_birthday_list(self, bdays: list[dict[str, Any]]) -> str:
        today = datetime.now()
        upcoming = []
        for b in bdays:
            parsed = self._parse_birthday_date(b["value"])
            if parsed:
                this_year = parsed.replace(year=today.year)
                if this_year < today.replace(hour=0, minute=0, second=0, microsecond=0):
                    this_year = this_year.replace(year=today.year + 1)
                days_until = (this_year - today.replace(hour=0, minute=0, second=0, microsecond=0)).days
                upcoming.append((days_until, b["key"].title(), b["value"], this_year))
            else:
                upcoming.append((9999, b["key"].title(), b["value"], None))

        upcoming.sort(key=lambda x: x[0])
        lines = [f"Birthdays ({len(upcoming)}):"]
        for days_until, name, date_str, dt in upcoming:
            if days_until == 0:
                tag = " (TODAY!)"
            elif days_until == 1:
                tag = " (tomorrow)"
            elif days_until <= 30:
                tag = f" (in {days_until} days)"
            else:
                tag = ""
            lines.append(f"  - {name}: {date_str}{tag}")
        return "\n".join(lines)

    @staticmethod
    def _parse_birthday_date(date_str: str) -> Optional[datetime]:
        cleaned = re.sub(r"(\d+)(st|nd|rd|th)\b", r"\1", date_str.strip())
        formats = [
            "%B %d", "%B %d, %Y", "%b %d", "%b %d, %Y",
            "%d %B", "%d %B, %Y", "%d %b", "%d %b, %Y",
            "%m/%d", "%m/%d/%Y", "%d-%m", "%d-%m-%Y",
            "%d %m", "%m %d",
        ]
        for fmt in formats:
            try:
                dt = datetime.strptime(cleaned, fmt)
                if dt.year == 1900:
                    dt = dt.replace(year=datetime.now().year)
                return dt
            except ValueError:
                continue
        return None

    @staticmethod
    def _looks_like_birthday_file(file_data: bytes) -> bool:
        try:
            text = file_data.decode("utf-8")[:1000].lower()
            return any(kw in text for kw in ["birthday", "birth", "dob", "date", "name"])
        except UnicodeDecodeError:
            return False

    def _handle_birthday_csv(self, file_data: bytes, keys: Any) -> AgentResponse:
        try:
            text = file_data.decode("utf-8")
        except UnicodeDecodeError:
            return AgentResponse(text="Could not read the CSV file. Please ensure it's UTF-8 encoded.")

        reader = csv.DictReader(io.StringIO(text))
        entries = []
        for row in reader:
            name = row.get("name") or row.get("Name") or row.get("NAME") or ""
            date_val = (
                row.get("birthday") or row.get("Birthday") or row.get("date")
                or row.get("Date") or row.get("dob") or row.get("DOB") or ""
            )
            if name.strip() and date_val.strip():
                entries.append({"name": name.strip(), "date": date_val.strip()})

        if not entries:
            return AgentResponse(
                text="Couldn't parse any birthdays from the CSV. Expected columns: name, birthday (or date/dob)."
            )

        count = self.memory.store_birthdays_bulk(entries, keys.db_key)
        return AgentResponse(text=f"Imported {count} birthdays from the CSV file.")

    async def _handle_general(self, message: str, keys: Any) -> AgentResponse:
        doc_result = await self._try_document_answer(message, keys)
        if doc_result:
            return doc_result

        from vault.llm.prompts import SYSTEM_PROMPT
        response = await self.llm.complete(message, system=SYSTEM_PROMPT)
        return AgentResponse(text=response)
