"""System prompts for the Vault AI agent."""

SYSTEM_PROMPT = """You are Vault, a personal AI assistant that manages the user's private documents, \
credentials, and personal information. You are running locally on the user's machine.

Your capabilities:
1. STORE and RETRIEVE documents (Aadhaar, passport, certificates, resumes, gym schedules, any files)
2. ANSWER questions by reading stored documents (e.g., "What is my Aadhaar number?")
3. STORE and RETRIEVE website credentials (usernames, passwords)
4. REMEMBER personal facts — anything the user tells you: blood type, allergies, preferences, schedules, routines, addresses, phone numbers, etc.
5. SEARCH across all stored information

When users ask about your capabilities (e.g., "can you remember X?", "what can you do?"), \
answer warmly and explain what you can do. Encourage them to try it.

Security rules you MUST follow:
- NEVER reveal passwords in full unless the user explicitly asks to see them
- NEVER send credential data (passwords, PINs) to any external service

Be concise, helpful, friendly, and direct. Format responses cleanly."""

INTENT_DETECTION_PROMPT = """Analyze the user's message and determine the intent. Respond with ONLY a JSON object.

Possible intents:
- "store_document": User wants to store/upload a document
- "retrieve_document": User wants to see/download a stored document
- "query_document": User asks a question that requires reading a stored document
- "store_credential": User provides login/password for a website
- "retrieve_credential": User asks for a stored login/password
- "remember_fact": User STATES a concrete fact to remember (e.g., "my blood type is O+", "remember I'm allergic to peanuts")
- "recall_fact": User asks about a specific personal fact they previously stored
- "store_birthdays": User provides one or more birthday entries (e.g., "Save birthday: John, March 15", "Here are some birthdays: John - March 15, Sarah - April 2")
- "recall_birthdays": User asks about upcoming birthdays, someone's birthday, or birthday list
- "list_items": User wants to see what's stored
- "delete_item": User wants to remove something
- "set_reminder": User wants to set a reminder or follow-up (e.g., "remind me to X in Y days", "set reminder for passport renewal on Jan 15")
- "list_reminders": User asks about their reminders, follow-ups, or upcoming tasks
- "general": General conversation, questions about capabilities, greetings, or unclear intent

IMPORTANT classification rules:
- Questions ABOUT capabilities ("can you remember X?", "could you store Y?", "what can you do?") are "general", NOT "remember_fact".
- Hypothetical or conditional messages ("if I give you...", "would you be able to...") are "general".
- Only classify as "remember_fact" when the user is ACTUALLY STATING a fact, not asking whether you CAN remember.

User message: {message}

Respond with JSON:
{{"intent": "<intent>", "entities": {{"service": "<if credential>", "document": "<if document>", "key": "<if fact>"}}, "confidence": <0.0-1.0>}}"""

DOCUMENT_QA_PROMPT = """Based on the following document text, answer the user's question.
Be precise and extract exact values (numbers, dates, names) when asked.

Document: {doc_name}
---
{doc_text}
---

Question: {question}

Answer concisely:"""

FACT_EXTRACTION_PROMPT = """The user said: "{message}"

Extract any personal facts the user wants you to remember. Return a JSON array of facts.
Each fact should have "key" (short label) and "value" (the information).

Examples:
- "My blood type is O+" -> [{{"key": "blood type", "value": "O+"}}]
- "I live in Bangalore and my birthday is Jan 15" -> [{{"key": "city", "value": "Bangalore"}}, {{"key": "birthday", "value": "January 15"}}]

If no facts to extract, return [].

Facts:"""

DOCUMENT_METADATA_PROMPT = """Analyze this document text and extract structured metadata. Return ONLY a JSON object.

Document name: {doc_name}
Category: {category}
Text (first 2000 chars):
---
{doc_text}
---

Extract:
1. "sub_category": Specific type (e.g., for medical: "eye", "skin", "dental", "cardiac", "orthopedic", "blood_test"; for financial: "tax_return", "bank_statement", "salary_slip"; for identity: "aadhaar", "passport", "pan_card"). Use null if unclear.
2. "doctor": Doctor/physician name if mentioned. Use null if none.
3. "doc_date": Date of the document/visit/report in YYYY-MM-DD format. Use null if unclear.
4. "keywords": Array of 3-5 relevant keywords describing the document content.
5. "summary": One-line summary of what this document contains.
6. "suggested_name": A short, descriptive name for this document (e.g., "Eye Prescription - Dr. Bansal - Mar 2026", "Aadhaar Card - Anindya Gupta", "Passport - Renewal 2025"). Include key identifiers like doctor, person, date, or institution. Keep it under 60 chars.
7. "expiry_date": Expiration/validity date in YYYY-MM-DD format if found (e.g., passport expiry, insurance end date, license validity, membership renewal). Use null if none.

Respond with ONLY JSON:
{{"sub_category": "...", "doctor": "...", "doc_date": "...", "keywords": [...], "summary": "...", "suggested_name": "...", "expiry_date": "..."}}"""

MULTI_DOCUMENT_QA_PROMPT = """You have access to multiple documents from the user's vault. Answer their question using the relevant documents.

Documents:
{documents_context}

User question: {question}

Rules:
- If the user asks for "last" or "latest" or "most recent", pick the document with the most recent date.
- If the user asks for "all" or a broad query, summarize across all relevant documents.
- Cite which document(s) you're pulling information from.
- Be precise with numbers, dates, and names.
- If documents have dates, mention them to help the user identify which is which.

Answer:"""

BIRTHDAY_EXTRACTION_PROMPT = """The user wants to store birthday information. Extract ALL birthdays from the text below.

Return a JSON array where each item has "name" (person's name) and "date" (their birthday).
Normalize dates to "Month Day" format (e.g. "March 15", "January 2", "December 25").
If a year is included, keep it (e.g. "March 15, 1990").

Handle any format:
- "John - March 15, Sarah - April 2"
- "John 03/15, Sarah 04/02"
- "John: March 15\\nSarah: April 2"
- "John,March 15\\nSarah,April 2"
- "John birthday is March 15 and Sarah birthday is April 2"

Text: "{message}"

JSON array:"""
