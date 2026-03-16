# Vault — Complete Technical Architecture

> Encrypted personal AI assistant. All data stays on your machine.

---

## Table of Contents

1. [System Overview](#1-system-overview)
2. [Deployment Architecture](#2-deployment-architecture)
3. [Database & Storage Layer](#3-database--storage-layer)
4. [Security Architecture](#4-security-architecture)
5. [API Endpoints](#5-api-endpoints)
6. [AI / LLM Pipeline](#6-ai--llm-pipeline)
7. [Data Flow Diagrams](#7-data-flow-diagrams)
8. [Web UI Architecture](#8-web-ui-architecture)
9. [Birthday Tracker](#9-birthday-tracker)
10. [MCP Integration](#10-mcp-integration)
11. [CLI Commands](#11-cli-commands)
12. [Paranoid Mode](#12-paranoid-mode)
13. [Backup & Recovery](#13-backup--recovery)
14. [Dependencies](#14-dependencies)
15. [Configuration Reference](#15-configuration-reference)

---

## 1. System Overview

Vault is a self-hosted, encrypted personal AI assistant that stores documents, login credentials, and personal facts — all encrypted at rest with military-grade cryptography. It uses an LLM (OpenAI or local Ollama) for natural-language interaction and document question-answering, while ensuring that raw data never leaves the deployment environment.

### What Vault Does

| Capability | Example |
|-----------|---------|
| **Document storage** | Upload Aadhaar card, passport, resume — retrieve on demand |
| **Document Q&A (RAG)** | "What college did I graduate from?" → reads your uploaded resume |
| **Credential management** | "My Netflix login is user@email.com, password xyz" → encrypted storage |
| **Personal memory** | "Remember my blood type is O+" → encrypted fact storage |
| **Birthday tracking** | "Remember Ankit's birthday on 30th Sept" → stored and shown on dashboard |
| **Semantic search** | "Find my tax documents" → vector similarity search |

### High-Level Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                        INTERNET                             │
└──────────────────────────┬──────────────────────────────────┘
                           │ HTTPS (443)
                           ▼
                ┌─────────────────────┐
                │   Caddy (Reverse    │  Automatic TLS via
                │   Proxy + HTTPS)    │  Let's Encrypt
                └──────────┬──────────┘
                           │ HTTP (8080, internal)
                           ▼
                ┌─────────────────────┐
                │   FastAPI (Vault)   │  Python app serving
                │   Port 8080        │  Web UI + REST API
                └──────────┬──────────┘
                           │
              ┌────────────┼────────────┐
              ▼            ▼            ▼
     ┌──────────────┐ ┌─────────┐ ┌──────────┐
     │    Agent     │ │ Session │ │   Web    │
     │  (AI Logic)  │ │  Store  │ │   UI     │
     └──────┬───────┘ └─────────┘ └──────────┘
            │
    ┌───────┼───────┐
    ▼       ▼       ▼
┌───────┐ ┌─────┐ ┌─────────┐
│  LLM  │ │Store│ │ Vector  │
│Router │ │Layer│ │  Store  │
└───┬───┘ └──┬──┘ └────┬────┘
    │        │          │
    ▼        ▼          ▼
 OpenAI   SQLite     ChromaDB
 / Ollama + Files    (Embeddings)
```

---

## 2. Deployment Architecture

Vault runs as two Docker containers orchestrated by Docker Compose on a Linux VM (Google Cloud e2-micro, Ubuntu 22.04).

### Docker Compose Services

| Service | Image | Purpose | Ports |
|---------|-------|---------|-------|
| **vault** | Built from `Dockerfile` | Python app (FastAPI + Uvicorn) | 8080 (internal only) |
| **caddy** | `caddy:2-alpine` | Reverse proxy, automatic HTTPS | 80, 443 (public) |

### Container: vault

```
Base: python:3.11-slim
System packages: build-essential, libsqlcipher-dev, tesseract-ocr,
                 tesseract-ocr-eng, libgl1, libglib2.0-0
Working dir: /app
Entry point: vault serve --host 0.0.0.0 --port 8080
```

Environment variables:
- `VAULT_DIR=/data` — all encrypted data stored in the `/data` Docker volume
- `OPENAI_API_KEY` — read from `.env` file or host environment (never committed to git)
- `PYTHONUNBUFFERED=1`

### Container: caddy

Caddy provides:
- **Automatic HTTPS** via Let's Encrypt (zero-config TLS certificate provisioning)
- **Gzip compression** for responses
- **Server header removal** (hides server identity)
- **Reverse proxy** to `vault:8080` on the internal Docker network

Caddyfile:
```
myprivatelockr.com {
    reverse_proxy vault:8080
    encode gzip
    header {
        -Server
    }
}
```

### Docker Volumes

| Volume | Mount | Contents |
|--------|-------|----------|
| `vault_data` | `/data` inside vault container | Encrypted database, files, ChromaDB, salt, verification token |
| `caddy_data` | `/data` inside caddy container | TLS certificates from Let's Encrypt |
| `caddy_config` | `/config` inside caddy container | Caddy server configuration |

### Network Flow

```
Internet ──► :443 (Caddy) ──► :8080 (Vault)
                                    │
                                    ▼
                              /data volume
                            (encrypted storage)
```

Port 8080 is **never exposed to the internet**. Only Caddy's ports 80 and 443 are public. All communication between the browser and Vault is HTTPS-encrypted in transit, and all data is AES-256-GCM encrypted at rest.

---

## 3. Database & Storage Layer

Vault uses three storage systems, each serving a distinct purpose:

| System | Technology | What It Stores |
|--------|-----------|----------------|
| **Relational DB** | SQLite (plain) | Document metadata, credentials, facts (incl. birthdays), app config |
| **File Store** | Encrypted files on disk | Original document files (PDF, images, etc.) |
| **Vector DB** | ChromaDB (local) | Document text embeddings for semantic search |

### 3.1 SQLite Database

**Location:** `{vault_dir}/data/vault.db`

The database is plain SQLite (not SQLCipher). Encryption is handled at the **application level** — every sensitive field is encrypted with AES-256-GCM before being written to the database. This means even if someone obtains the `.db` file, all sensitive columns contain opaque encrypted blobs.

#### Schema

**`meta` table** — application configuration

```sql
CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
```

Stores: `schema_version`, `username`.

**`documents` table** — uploaded document metadata

```sql
CREATE TABLE IF NOT EXISTS documents (
    id             TEXT PRIMARY KEY,
    name           TEXT NOT NULL,
    category       TEXT NOT NULL DEFAULT 'general',
    file_ref       TEXT,                          -- UUID pointing to FileVault
    extracted_text BLOB,                          -- AES-256-GCM encrypted
    tags           TEXT DEFAULT '[]',
    created_at     REAL NOT NULL,
    updated_at     REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_documents_category ON documents(category);
CREATE INDEX IF NOT EXISTS idx_documents_name ON documents(name);
```

| Column | Encrypted? | Key Used | Notes |
|--------|-----------|----------|-------|
| `id` | No | — | UUID, primary key |
| `name` | No | — | Original filename |
| `category` | No | — | `general`, `identity`, `financial`, `medical`, etc. |
| `file_ref` | No | — | UUID linking to FileVault |
| `extracted_text` | **Yes** | `db_key` | OCR/text content of the document |
| `tags` | No | — | JSON array of tags |

**`credentials` table** — stored login credentials

```sql
CREATE TABLE IF NOT EXISTS credentials (
    id         TEXT PRIMARY KEY,
    service    TEXT NOT NULL,
    username   BLOB,                              -- AES-256-GCM encrypted
    password   BLOB,                              -- AES-256-GCM encrypted
    url        TEXT,
    notes      BLOB,                              -- AES-256-GCM encrypted
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_credentials_service ON credentials(service);
```

| Column | Encrypted? | Key Used |
|--------|-----------|----------|
| `service` | No | — |
| `username` | **Yes** | `cred_key` |
| `password` | **Yes** | `cred_key` |
| `notes` | **Yes** | `cred_key` |

**`facts` table** — personal facts, memories, and birthdays

```sql
CREATE TABLE IF NOT EXISTS facts (
    id         TEXT PRIMARY KEY,
    category   TEXT NOT NULL DEFAULT 'general',
    key        TEXT NOT NULL,
    value      BLOB NOT NULL,                     -- AES-256-GCM encrypted
    source     TEXT DEFAULT 'user',
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_facts_category ON facts(category);
CREATE INDEX IF NOT EXISTS idx_facts_key ON facts(key);
```

| Column | Encrypted? | Key Used |
|--------|-----------|----------|
| `key` | No | — |
| `value` | **Yes** | `db_key` |

Fact categories include: `general`, `personal`, `medical`, `financial`, `preferences`, `work`, and **`birthday`** (for birthday tracking).

### 3.2 FileVault (Encrypted File Storage)

**Location:** `{vault_dir}/data/files/`

Original document files (PDFs, images, etc.) are stored as encrypted files on disk, separate from the database. Each file consists of two parts:

| File | Contents |
|------|----------|
| `{uuid}.enc` | AES-256-GCM encrypted file content |
| `{uuid}.meta` | AES-256-GCM encrypted original filename |

Both are encrypted with the `file_key` (derived from the master password).

**Operations:**

| Method | Description |
|--------|-------------|
| `store(data, key, filename)` | Encrypt file + metadata, write to disk, return UUID |
| `retrieve(file_id, key)` | Decrypt and return `(bytes, original_filename)` |
| `delete(file_id)` | Remove both `.enc` and `.meta` files |
| `exists(file_id)` | Check if file exists on disk |
| `list_files()` | List all stored file UUIDs |

### 3.3 ChromaDB (Vector Store)

**Location:** `{vault_dir}/data/chroma/`

ChromaDB is a local vector database used for **semantic search** over document content. When a document is uploaded, its extracted text is converted into a 384-dimensional embedding vector and stored in ChromaDB.

| Setting | Value |
|---------|-------|
| Collection name | `vault_documents` |
| Distance metric | Cosine similarity |
| Embedding model | `all-MiniLM-L6-v2` (384 dimensions) |
| Storage | Local persistent directory |

**Important:** ChromaDB stores the **plain text** of documents for indexing purposes. The security model relies on the entire `{vault_dir}` being access-controlled at the OS/container level. The encrypted copy in SQLite is the authoritative secure store.

**Operations:**

| Method | Description |
|--------|-------------|
| `add_document(doc_id, text, metadata)` | Upsert document text as vector embedding |
| `search(query, n_results=5)` | Semantic search — returns matching document IDs and text |
| `delete_document(doc_id)` | Remove document from vector index |

---

## 4. Security Architecture

### 4.1 Key Derivation

Vault uses a three-layer key derivation scheme:

```
Master Password (user input)
        │
        ▼
   ┌─────────────────────┐
   │  Argon2id KDF       │   time_cost=3, memory_cost=64MB,
   │  + 32-byte salt     │   parallelism=4, hash_len=32 bytes
   └──────────┬──────────┘
              │
              ▼
       256-bit Master Key
              │
    ┌─────────┼─────────┐
    ▼         ▼         ▼
┌───────┐ ┌───────┐ ┌───────┐
│BLAKE2b│ │BLAKE2b│ │BLAKE2b│   person="vault-db-v1"
│db_key │ │file_  │ │cred_  │   person="vault-file-v1"
│       │ │key    │ │key    │   person="vault-cred-v1"
└───────┘ └───────┘ └───────┘
```

| Parameter | Value |
|-----------|-------|
| KDF algorithm | Argon2id (Type.ID) |
| Salt length | 32 bytes (cryptographically random) |
| Time cost | 3 iterations |
| Memory cost | 65,536 KB (64 MB) |
| Parallelism | 4 threads |
| Output key length | 32 bytes (256 bits) |
| Purpose key derivation | BLAKE2b with 16-byte `person` parameter |

**Why Argon2id?** It is the winner of the Password Hashing Competition and is resistant to both GPU-based and side-channel attacks. The 64 MB memory cost makes brute-force attacks extremely expensive.

**Why three separate keys?** Compartmentalization. Compromising one key purpose does not expose data protected by the other two.

### 4.2 Encryption

| Algorithm | Usage |
|-----------|-------|
| **AES-256-GCM** | All data at rest (document text, credentials, facts, files) |
| Nonce | 12 bytes, cryptographically random, per encryption operation |
| Authentication | GCM provides built-in authentication (AEAD) |

Encrypted blob format: `nonce (12 bytes) || ciphertext || GCM auth tag (16 bytes)`

Every single encrypt operation generates a fresh random nonce, ensuring that encrypting the same plaintext twice produces different ciphertext.

### 4.3 What Is Encrypted vs. What Is Not

| Data | Encrypted? | Notes |
|------|-----------|-------|
| Document file content | Yes (file_key) | `.enc` files on disk |
| Document extracted text | Yes (db_key) | In SQLite `documents.extracted_text` |
| Document filename | Yes (file_key) | `.meta` files on disk |
| Document name/category/tags | No | Metadata for search |
| Credential username | Yes (cred_key) | In SQLite |
| Credential password | Yes (cred_key) | In SQLite |
| Credential service name | No | Needed for lookup |
| Fact value | Yes (db_key) | In SQLite |
| Fact key/category | No | Needed for lookup |
| ChromaDB embeddings | No | Plain text for vector search |
| Master password | Never stored | Only verification token stored |
| Argon2 salt | On disk (plain) | Required for key derivation |
| Verification token | On disk (plain) | Derived from password, not reversible |

### 4.4 Session Management

Vault uses **per-browser, token-based sessions** managed by `SessionStore`. Each browser/device gets its own isolated session with its own encryption keys in memory.

```
Browser A (desktop)  ──cookie:token_A──► SessionStore
                                            │
Browser B (phone)    ──cookie:token_B──►    ├── token_A → Session (keys, last_active)
                                            ├── token_B → Session (keys, last_active)
                                            └── ...
```

| Behavior | Detail |
|----------|--------|
| **Unlock** | Password (+ optional username) verified; new session token created; `DerivedKeys` held in memory for that session |
| **Lock** | Session's keys wiped; cookie cleared; redirects to `/login?mode=lock` (password-only) |
| **Logout** | Session destroyed from `SessionStore`; cookie cleared; redirects to `/login` (username + password) |
| **Auto-lock** | After 300 seconds of inactivity (configurable via `session_timeout`) |
| **Activity tracking** | Every access to `session.keys` resets the inactivity timer |
| **Process restart** | All sessions lost; every user must re-enter password |
| **Cookie** | `vault_sid`, HTTP-only, SameSite=Lax, Secure in production |

**Lock vs. Logout:** Locking preserves the username context (password-only reauth). Logging out destroys the session entirely (full username + password reauth).

### 4.5 Password Verification

Vault never stores the master password. Instead:

1. On first setup, a **verification token** is generated: `generate_verification_token(password, salt)` — encrypts a known plaintext (`b"VAULT_VERIFY_TOKEN_V1"`) with the derived `db_key`.
2. On unlock, the token is recomputed and compared to the stored token.
3. The token cannot be reversed to obtain the password.

Stored files:
- `{vault_dir}/data/.salt` — 32-byte Argon2 salt
- `{vault_dir}/data/.verify_token` — verification token

### 4.6 Username Support

Vault optionally supports a username set during initial setup. When configured:
- The username is stored in the `meta` table (plaintext, not a secret)
- `SessionStore` enforces username match during unlock
- The login page shows both username and password fields
- The lock screen shows password only (username already known)

### 4.7 Rate Limiting

The `/api/unlock` endpoint is rate-limited:

| Parameter | Value |
|-----------|-------|
| Max attempts | 5 per IP address |
| Window | 60 seconds |
| Response on exceed | HTTP 429 "Too many unlock attempts. Try again in a minute." |

Implementation: per-IP attempt timestamps stored in a `defaultdict(list)`, pruned each request.

### 4.8 HTTP Security Headers

Applied to every response via middleware:

| Header | Value | Purpose |
|--------|-------|---------|
| `X-Content-Type-Options` | `nosniff` | Prevents MIME-type sniffing |
| `X-Frame-Options` | `DENY` | Prevents clickjacking via iframes |
| `X-XSS-Protection` | `1; mode=block` | Legacy XSS protection |
| `Referrer-Policy` | `strict-origin-when-cross-origin` | Limits referrer leakage |
| `Permissions-Policy` | `camera=(), microphone=(), geolocation=()` | Disables device APIs |
| `Strict-Transport-Security` | `max-age=63072000; includeSubDomains` | HSTS (HTTPS only) |

Additionally, Caddy strips the `Server` header to hide server identity.

---

## 5. API Endpoints

All endpoints are served by FastAPI (Uvicorn) on port 8080 (Docker) or port 8000 (local development).

### Page Routes

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/` | Redirects to `/setup`, `/login`, or `/app` based on vault state |
| `GET` | `/setup` | First-time setup page (create username + master password) |
| `GET` | `/login` | Login/unlock page. Accepts `?mode=lock` for password-only mode |
| `GET` | `/app` | Main application (SPA shell) |
| `GET` | `/app/{path}` | Client-side routes: `/app/chat`, `/app/docs`, `/app/credentials`, `/app/memory`, `/app/database` |

### API Endpoint Reference

| Method | Path | Auth | Rate Limited | Description |
|--------|------|:---:|:---:|-------------|
| `POST` | `/api/init` | No | No | First-time initialization — sets username + master password |
| `POST` | `/api/unlock` | No | **Yes** (5/min/IP) | Unlock vault with username + password |
| `POST` | `/api/lock` | No | No | Lock session, wipe keys, clear cookie |
| `POST` | `/api/logout` | No | No | Destroy session entirely, clear cookie |
| `GET` | `/api/status` | No | No | Returns `{initialized, locked, has_username, paranoid_mode, active_sessions}` |
| `POST` | `/api/chat` | **Yes** | No | Main interaction endpoint (text + optional file upload) |
| `POST` | `/api/change-password` | **Yes** | No | Change master password, re-encrypt all data |
| `POST` | `/api/backup` | **Yes** | No | Create encrypted backup file |
| `GET` | `/api/stats` | **Yes** | No | Dashboard stats: documents, credentials, facts, sessions, birthdays |
| `GET` | `/api/birthdays` | **Yes** | No | All stored birthdays sorted by proximity with `days_until` |
| `GET` | `/api/db-viewer` | **Yes** | No | Database summary (table names, column info, row counts) |
| `GET` | `/api/db-viewer/{table}` | **Yes** | No | Rows for a specific table (passwords masked) |

### Request/Response Details

**`POST /api/init`**
```
Request:  { "username": "string (optional)", "password": "string (min 8 chars)" }
Response: { "status": "initialized" }
```

**`POST /api/unlock`**
```
Request:  { "password": "string", "username": "string (if configured)" }
Response: { "status": "unlocked" }    — 200 (sets vault_sid cookie)
           { "detail": "..." }        — 401 (wrong credentials)
           { "detail": "..." }        — 429 (rate limited)
```

**`POST /api/lock`**
```
Response: { "status": "locked" }      — clears cookie
```

**`POST /api/logout`**
```
Response: { "status": "logged_out" }  — destroys session, clears cookie
```

**`GET /api/status`**
```
Response: {
    "initialized": true,
    "locked": true/false,
    "has_username": true/false,
    "paranoid_mode": false,
    "active_sessions": 2
}
```

**`POST /api/chat`**
```
Request:  FormData { message: "string", file?: File }
Response: { "text": "string", "file"?: { "name": "string", "data": "base64" } }
           — 401 if no valid session
```

**`GET /api/stats`**
```
Response: {
    "documents": 5,
    "credentials": 3,
    "facts": 12,
    "active_sessions": 1,
    "total_birthdays": 8,
    "upcoming_birthdays": 2
}
```

**`GET /api/birthdays`**
```
Response: {
    "birthdays": [
        { "name": "Ankit", "date": "30th sept", "days_until": 199 },
        { "name": "Rishika Didi", "date": "20th mar", "days_until": 5 }
    ]
}
```

**`POST /api/change-password`**
```
Request:  { "current_password": "string", "new_password": "string (min 8)" }
Response: { "status": "password_changed" }
```

### Page Routing Logic

```
GET / →
  if not initialized (no .salt file) → redirect /setup
  if no valid session cookie         → redirect /login
  if valid session                   → redirect /app
```

---

## 6. AI / LLM Pipeline

### 6.1 LLM Configuration

| Setting | Default | Description |
|---------|---------|-------------|
| Provider | `openai` | LLM provider via LiteLLM |
| Model | `gpt-4o-mini` | Default cloud model |
| Ollama model | `llama3.1:8b` | Local model for paranoid mode |
| Temperature | 0.1–0.3 | Low for factual accuracy |

LiteLLM provides a unified interface. Model string resolution:

```
if paranoid_mode or provider == "ollama":
    model = "ollama/{ollama_model}"        → "ollama/llama3.1:8b"
else:
    model = "{llm_model}"                  → "gpt-4o-mini"
```

### 6.2 Message Processing Flow

```
User sends message (+ optional file)
            │
            ▼
   ┌──────────────────┐
   │ Session locked?  │──── Yes ──► "Vault is locked..."
   └────────┬─────────┘
            │ No
            ▼
   ┌──────────────────┐
   │ Birthday CSV?    │──── Yes ──► _handle_birthday_csv()
   │ (file upload)    │              (parse CSV, store birthdays)
   └────────┬─────────┘
            │ No
            ▼
   ┌──────────────────┐
   │ File attached?   │──── Yes ──► _handle_store_document()
   └────────┬─────────┘              (skip intent detection)
            │ No
            ▼
   ┌──────────────────┐
   │ Local resolution │──── Match ──► Direct handler
   │ (regex patterns) │              (no LLM call)
   └────────┬─────────┘
            │ No match
            ▼
   ┌──────────────────┐
   │ LLM Intent       │   temperature=0.1
   │ Detection        │   → {intent, entities, confidence}
   └────────┬─────────┘
            │
            ▼
   ┌──────────────────┐
   │ Route to handler │
   │ based on intent  │
   └──────────────────┘
```

### 6.3 Local Resolution (No LLM)

These patterns are handled instantly via regex, without any LLM call:

| Pattern | Handler |
|---------|---------|
| "my {service} login/password is..." | Store credential |
| "what is my {service} login/password?" | Retrieve credential |
| "remember my {key} is {value}" | Store fact |
| "what is my {key}?" | Recall fact |
| "save birthday: Name, Date" | Store birthday |
| "remember Name's birthday on Date" | Store birthday |
| "Name's birthday is Date" | Store birthday |
| "list birthdays" / "show birthdays" | List all birthdays |
| "when is Name's birthday?" | Query specific birthday |
| "list documents/credentials/facts" | List items |
| "lock vault" | Lock session |

### 6.4 Intent Types (12)

| Intent | Description | LLM Used? |
|--------|-------------|-----------|
| `store_document` | Upload/store a document | No (file attached) |
| `retrieve_document` | Download a stored document | No |
| `query_document` | Ask a question about document content | **Yes** (RAG) |
| `store_credential` | Save a login/password | No |
| `retrieve_credential` | Get a stored login/password | No |
| `remember_fact` | Store a personal fact | Sometimes (extraction) |
| `recall_fact` | Retrieve a stored fact | No |
| `store_birthdays` | Store one or more birthday entries | Sometimes (extraction) |
| `recall_birthdays` | Query about birthdays | No |
| `list_items` | List stored items | No |
| `delete_item` | Delete an item | No |
| `general` | General chat, greetings, questions | **Yes** |

### 6.5 LLM Calls

| Method | Prompt Used | Temperature | Max Tokens | When |
|--------|-----------|:-----------:|:----------:|------|
| `detect_intent()` | INTENT_DETECTION_PROMPT | 0.1 | 256 | Every non-local-resolved message |
| `answer_document_question()` | DOCUMENT_QA_PROMPT | 0.1 | 1024 | Document Q&A (RAG) |
| `extract_facts()` | FACT_EXTRACTION_PROMPT | 0.1 | 512 | When user states facts in free-form |
| `extract_birthdays()` | BIRTHDAY_EXTRACTION_PROMPT | 0.1 | 2048 | Bulk birthday parsing from freeform text |
| `complete()` | SYSTEM_PROMPT | 0.3 | 1024 | General conversation |

### 6.6 System Prompts

| Prompt | Purpose |
|--------|---------|
| **SYSTEM_PROMPT** | Defines Vault's identity and capabilities for general chat |
| **INTENT_DETECTION_PROMPT** | Classifies user message into one of 12 intents; returns JSON with `{intent, entities, confidence}` |
| **DOCUMENT_QA_PROMPT** | RAG prompt: given document name, text, and question — answer from the document only |
| **FACT_EXTRACTION_PROMPT** | Extracts structured `[{key, value}]` pairs from free-form text |
| **BIRTHDAY_EXTRACTION_PROMPT** | Extracts `[{name, date}]` pairs from birthday lists in any format |

---

## 7. Data Flow Diagrams

### 7.1 Document Upload

```
User uploads file (e.g. passport.pdf)
        │
        ▼
┌──────────────────────┐
│ 1. Encrypt file      │   AES-256-GCM with file_key
│    Store on disk     │   → {uuid}.enc + {uuid}.meta
│    (FileVault)       │   Returns file_ref (UUID)
└──────────┬───────────┘
           │
           ▼
┌──────────────────────┐
│ 2. Extract text      │   PDF → PyMuPDF (fitz)
│                      │   Image → Tesseract OCR
│                      │   Text → direct read
└──────────┬───────────┘
           │
           ▼
┌──────────────────────┐
│ 3. Categorize        │   Guess category from filename + content
│                      │   → identity / financial / medical / general
└──────────┬───────────┘
           │
           ▼
┌──────────────────────┐
│ 4. Store metadata    │   SQLite: name, category, file_ref,
│    in database       │   encrypted extracted_text, tags
│    (VaultDatabase)   │   Encrypted with db_key
└──────────┬───────────┘
           │
           ▼
┌──────────────────────┐
│ 5. Index for search  │   ChromaDB: document text → 384-dim vector
│    (VectorStore)     │   Using all-MiniLM-L6-v2 model
└──────────────────────┘
```

### 7.2 Document Query (RAG)

```
User asks: "What college did I graduate from?"
        │
        ▼
┌──────────────────────┐
│ 1. Intent detection  │   LLM → "query_document"
└──────────┬───────────┘
           │
           ▼
┌──────────────────────┐
│ 2. Vector search     │   ChromaDB semantic search
│                      │   → top 5 matching document IDs
└──────────┬───────────┘
           │
           ▼
┌──────────────────────┐
│ 3. Load document     │   SQLite: decrypt extracted_text
│    from database     │   using db_key
└──────────┬───────────┘
           │
           ▼
┌──────────────────────┐
│ 4. LLM answers       │   DOCUMENT_QA_PROMPT:
│    from document     │   "Given this document, answer..."
│                      │   temperature=0.1
└──────────┬───────────┘
           │
           ▼
┌──────────────────────┐
│ 5. Return answer     │   "You graduated from XYZ University"
│    + source document │
└──────────────────────┘
```

Fallback chain: Vector search → Keyword search (SQL LIKE) → Scan all documents with text.

### 7.3 Credential Storage

```
User says: "My Netflix login is user@email.com password abc123"
        │
        ▼
┌──────────────────────┐
│ 1. Local resolution  │   Regex matches credential pattern
│    (no LLM needed)   │   Extracts: service, username, password
└──────────┬───────────┘
           │
           ▼
┌──────────────────────┐
│ 2. Encrypt fields    │   AES-256-GCM with cred_key:
│                      │   • username → encrypted blob
│                      │   • password → encrypted blob
│                      │   • notes → encrypted blob
└──────────┬───────────┘
           │
           ▼
┌──────────────────────┐
│ 3. Store in SQLite   │   INSERT into credentials table
│                      │   service name stored in plaintext
│                      │   (needed for lookup)
└──────────────────────┘
```

Retrieval: `"What's my Netflix password?"` → regex match → `db.get_credential("netflix", cred_key)` → decrypt → return. No LLM involved.

### 7.4 Fact Storage

```
User says: "My blood type is O+"
        │
        ▼
┌──────────────────────┐
│ 1. Parse input       │   Regex: "my {key} is {value}"
│    (MemoryManager)   │   → key="blood type", value="O+"
└──────────┬───────────┘
           │
     ┌─────┴──── No regex match? ──────┐
     │                                  ▼
     │                         ┌──────────────────┐
     │                         │ LLM extracts     │
     │                         │ facts as JSON    │
     │                         │ [{key, value}]   │
     │                         └────────┬─────────┘
     │                                  │
     ▼◄─────────────────────────────────┘
┌──────────────────────┐
│ 2. Categorize        │   _guess_fact_category():
│                      │   birthday / medical / personal / financial /
│                      │   preferences / work / general
└──────────┬───────────┘
           │
           ▼
┌──────────────────────┐
│ 3. Encrypt value     │   AES-256-GCM with db_key
│    Store in SQLite   │   Key stored plaintext (for lookup)
│    (facts table)     │   Value stored as encrypted blob
└──────────────────────┘
```

---

## 8. Web UI Architecture

### 8.1 Technology Stack

The web UI is a single-page application built with vanilla JavaScript (no frameworks), server-side rendered HTML templates via Jinja2, and a dark-themed CSS design system.

| Layer | Technology |
|-------|-----------|
| Templates | Jinja2 (server-rendered) |
| JavaScript | Vanilla ES6+, no build step |
| CSS | Custom properties, flexbox/grid, media queries |
| Fonts | Outfit (auth titles), system font stack (app) |
| Routing | `history.pushState` for SPA navigation |

### 8.2 Authentication Pages

The setup (`/setup`) and login (`/login`) pages use a **full-viewport split layout** with an immersive animated orb:

```
┌──────────────────────────────────────────────────┐
│                                                  │
│   ┌──────────────┐   ┌────────────────────────┐  │
│   │              │   │                        │  │
│   │  Animated    │   │   Brand + Title        │  │
│   │  Vault Orb   │   │   Username field       │  │
│   │  (SVG rings, │   │   Password field       │  │
│   │   particles, │   │   Submit button        │  │
│   │   parallax)  │   │                        │  │
│   │              │   │                        │  │
│   └──────────────┘   └────────────────────────┘  │
│     .auth-visual          .auth-panel            │
│                                                  │
└──────────────────────────────────────────────────┘
                    Desktop layout

On mobile (≤768px): stacks vertically (orb on top, form below)
```

**Orb animation features (`auth-effects.js`):**
- Concentric SVG rings with spin/pulse animations
- Orbiting dots around the orb
- Background particle field
- Mouse parallax (desktop) / device orientation parallax (mobile)
- Input-reactive effects: rings pulse brighter as user types, dots speed up
- **Reject animation:** Rings/icon flash red, form shakes on wrong password
- **Unlock animation:** Choreographed 6-phase sequence — ring acceleration, color shift to green, shackle lift, shockwave particle burst, expanding rings, container fade-out

**Visual states:**
- Setup: Green-tinted "welcoming" orb
- Login: Amber/gold "locked" orb with shackle icon
- Lock screen: Password-only, "Vault Locked" heading
- Logout screen: Username + password, "Welcome back" heading

### 8.3 Main Application

The main app (`/app`) is a sidebar-based SPA with five views:

| View | Path | Features |
|------|------|----------|
| **Chat** | `/app/chat` | AI conversation, file upload, typing indicators, toast notifications |
| **Documents** | `/app/docs` | Card grid, drag-and-drop upload, search-as-you-type, 3D tilt effect |
| **Credentials** | `/app/credentials` | List view, search-as-you-type, password masking |
| **Memory** | `/app/memory` | Categorized facts, search-as-you-type |
| **Database** | `/app/database` | Raw database viewer with table tabs |

**Dashboard (Chat view) components:**
- **Stat cards:** Documents, Credentials, Facts, Sessions, Birthdays (with animated counters)
- **Birthday widget:** Shows all stored birthdays sorted by proximity, with badges (TODAY, Tomorrow, In X days) and highlighting for upcoming entries
- **Ambient particle canvas:** Mouse-reactive floating particles with connection lines

**UI features:**
- Dark theme with glassmorphism (backdrop-filter blur, semi-transparent backgrounds)
- Skeleton loaders during data fetching
- Toast notifications for success/error feedback
- Swipe navigation between tabs (mobile)
- Keyboard shortcuts (Cmd/Ctrl+K to focus chat, Escape to close modals)
- Click ripple effects on send button
- Responsive layout: sidebar on desktop, bottom tab bar on mobile
- Status polling every 30 seconds (auto-redirect to login if session expires)
- `prefers-reduced-motion` support disables non-essential animations

---

## 9. Birthday Tracker

Vault includes a birthday tracking feature that stores friends' birthdays and surfaces upcoming ones on the dashboard.

### Storage

Birthdays are stored as facts in the `facts` table with `category = 'birthday'`:
- `key`: Person's name (lowercase)
- `value`: Date string (encrypted with `db_key`)

### Input Methods

| Method | Example | LLM Required? |
|--------|---------|:---:|
| Single entry (chat) | "Remember Ankit's birthday on 30th Sept" | No (regex) |
| Structured entry | "save birthday: John, March 15" | No (regex) |
| Bulk paste (chat) | "John - March 15, Sarah - April 2, Mike - Dec 25" | **Yes** (extraction) |
| CSV file upload | Upload `.csv` with `name` and `birthday`/`date` columns | No (CSV parser) |

### Date Parsing

The date parser handles multiple formats by stripping ordinal suffixes (st, nd, rd, th) and trying both day-first and month-first orders:

| Format | Example |
|--------|---------|
| Full month + day | "March 15", "September 30" |
| Abbreviated month + day | "Mar 15", "Sept 30" |
| Day + month (ordinal) | "15th March", "30th Sept" |
| Numeric | "03/15", "03/15/1990" |
| With year | "March 15, 1990" |

### Dashboard Display

- **Birthday stat card:** Shows total count of stored birthdays; label includes "(X soon)" when birthdays are within 30 days
- **Birthday widget:** Below stat cards, lists all birthdays sorted by proximity
  - Badges: `TODAY` (red pulse), `Tomorrow` (orange), `In X days` (blue for ≤7 days), `Xd` (neutral for others)
  - Upcoming entries (≤7 days) highlighted with orange left border

### API

- `GET /api/birthdays` — Returns all birthdays with `days_until` for each, sorted by proximity
- `GET /api/stats` — Includes `total_birthdays` and `upcoming_birthdays` (within 30 days) counts

---

## 10. MCP Integration

Vault exposes 17 tools via the **Model Context Protocol (MCP)**, allowing external AI clients (Cursor, Claude Desktop, etc.) to interact with Vault programmatically.

### MCP Tools

| Category | Tool | Description |
|----------|------|-------------|
| **Auth** | `vault_unlock` | Unlock with master password |
| | `vault_lock` | Lock vault |
| | `vault_status` | Check initialized/locked/paranoid state |
| **Documents** | `vault_store_document` | Store document (base64 content) |
| | `vault_search_documents` | Semantic/keyword search |
| | `vault_read_document` | Read extracted text |
| | `vault_list_documents` | List all or by category |
| | `vault_retrieve_document_file` | Download file as base64 |
| **Credentials** | `vault_store_credential` | Store login credentials |
| | `vault_get_credential` | Retrieve by service |
| | `vault_list_credentials` | List all services |
| | `vault_delete_credential` | Delete by service |
| **Memory** | `vault_remember` | Store a fact |
| | `vault_recall` | Recall a fact |
| | `vault_list_facts` | List all facts |
| | `vault_delete_fact` | Delete a fact |
| **Search** | `vault_search` | Cross-search docs, credentials, facts |

### How It Works

```
External AI Client (e.g. Cursor)
        │
        │  MCP Protocol (stdio)
        ▼
┌──────────────────┐
│ FastMCP Server   │   vault/mcp_server.py
│ (17 tools)       │
└────────┬─────────┘
         │
         ▼
┌──────────────────┐
│ Same storage +   │   Shares the same database, FileVault,
│ encryption layer │   ChromaDB, and session as the web UI
└──────────────────┘
```

Entry point: `vault mcp` (CLI) or `vault-mcp` (installed script).

---

## 11. CLI Commands

Vault includes a full command-line interface built with Typer + Rich.

| Command | Description |
|---------|-------------|
| `vault init` | Create vault directory, set master password, generate salt and verification token |
| `vault unlock` | Unlock with master password (interactive prompt) |
| `vault lock` | Lock vault and wipe keys from memory |
| `vault chat` | Interactive chat mode (supports `quit`, `lock` commands) |
| `vault store <file>` | Store a document file (optional `--name` flag) |
| `vault docs` | List all stored documents |
| `vault cred list` | List all stored credentials |
| `vault cred add --service <name>` | Add a credential (interactive) |
| `vault cred get --service <name>` | Retrieve a credential |
| `vault cred delete --service <name>` | Delete a credential |
| `vault facts` | List all stored facts |
| `vault serve` | Start web UI server (default `127.0.0.1:8000`, Docker uses `0.0.0.0:8080`) |
| `vault mcp` | Start MCP server for AI client integration |
| `vault backup` | Create encrypted backup (optional `--output` path) |
| `vault restore <backup_file>` | Restore from `.vbak` backup file |

---

## 12. Paranoid Mode

Paranoid mode ensures **zero network activity** — no data ever leaves your machine, not even to an LLM API.

| Setting | Normal Mode | Paranoid Mode |
|---------|-------------|---------------|
| LLM provider | OpenAI (cloud) | Ollama (local) |
| Model | `gpt-4o-mini` | `llama3.1:8b` |
| Network calls | OpenAI API only | None |
| Encryption | Same | Same |
| Storage | Same | Same |
| Quality | Higher (GPT-4o-mini) | Good (Llama 3.1 8B) |

**How to enable:**
```yaml
# In {vault_dir}/config.yaml
paranoid_mode: true
```

When paranoid mode is active, `llm_router._get_model_string()` always returns `"ollama/{ollama_model}"`, routing all LLM calls to a locally running Ollama instance. Requires Ollama to be installed and the model pulled (`ollama pull llama3.1:8b`).

---

## 13. Backup & Recovery

### Encrypted Backup

```bash
# CLI
vault backup --output ./my-backup.vbak

# API
POST /api/backup  (requires unlocked session)

# Docker (volume-level)
docker run --rm \
  -v vault_vault_data:/data \
  -v $(pwd):/backup \
  alpine tar czf /backup/vault-backup.tar.gz /data
```

### Restore

```bash
vault restore ./my-backup.vbak
```

### Password Recovery

**There is no password recovery mechanism.** This is by design.

If you forget your master password:
- The Argon2 salt and verification token cannot be reversed.
- The AES-256-GCM encrypted data is unrecoverable without the derived keys.
- The only option is to delete the vault and start fresh.

This is the fundamental trade-off of true zero-knowledge encryption: maximum security in exchange for full responsibility over the master password.

---

## 14. Dependencies

### Python Packages

| Package | Version | Purpose |
|---------|---------|---------|
| `fastapi` | >=0.104.0 | Web framework and REST API |
| `uvicorn[standard]` | >=0.24.0 | ASGI server |
| `typer[all]` | >=0.9.0 | CLI framework |
| `rich` | >=13.0.0 | Terminal formatting |
| `cryptography` | >=41.0.0 | AES-256-GCM encryption |
| `argon2-cffi` | >=23.1.0 | Argon2id key derivation |
| `pysqlcipher3` | >=1.2.0 | SQLite bindings (with SQLCipher support) |
| `chromadb` | >=0.4.0 | Local vector database |
| `sentence-transformers` | >=2.2.0 | Text embedding models |
| `litellm` | >=1.0.0 | Unified LLM client (OpenAI, Ollama, etc.) |
| `PyMuPDF` | >=1.23.0 | PDF text extraction |
| `pytesseract` | >=0.3.10 | OCR (Optical Character Recognition) |
| `Pillow` | >=10.0.0 | Image processing |
| `python-multipart` | >=0.0.6 | File upload handling |
| `jinja2` | >=3.1.0 | HTML template rendering |
| `pyyaml` | >=6.0.0 | YAML config parsing |
| `aiofiles` | >=23.0.0 | Async file operations |
| `mcp[cli]` | >=1.0.0 | Model Context Protocol SDK |

### System Packages (in Docker)

| Package | Purpose |
|---------|---------|
| `build-essential` | Compilation tools for native extensions |
| `libsqlcipher-dev` | SQLCipher development headers |
| `tesseract-ocr` | OCR engine |
| `tesseract-ocr-eng` | English OCR language data |
| `libgl1` | OpenGL (required by some image processing libs) |
| `libglib2.0-0` | GLib (required by some native packages) |

---

## 15. Configuration Reference

Configuration file: `{vault_dir}/config.yaml`

| Key | Default | Description |
|-----|---------|-------------|
| `llm_provider` | `"openai"` | LLM provider: `openai`, `anthropic`, or `ollama` |
| `llm_model` | `"gpt-4o-mini"` | Cloud LLM model name |
| `ollama_model` | `"llama3.1:8b"` | Local LLM model for Ollama |
| `paranoid_mode` | `false` | Force all LLM calls to local Ollama |
| `session_timeout` | `300` | Seconds of inactivity before auto-lock |
| `ocr_enabled` | `true` | Enable OCR for image documents |
| `embedding_model` | `"all-MiniLM-L6-v2"` | Sentence transformer model for embeddings |

### Directory Structure (on disk)

```
{vault_dir}/                        # ~/.vault (local) or /data (Docker)
├── config.yaml                     # User configuration
└── data/
    ├── .salt                       # 32-byte Argon2 salt
    ├── .verify_token               # Password verification token
    ├── vault.db                    # SQLite database (encrypted fields)
    ├── files/                      # Encrypted document files
    │   ├── {uuid}.enc             # AES-256-GCM encrypted content
    │   └── {uuid}.meta            # AES-256-GCM encrypted filename
    └── chroma/                     # ChromaDB vector database
        └── (internal files)        # Managed by ChromaDB
```

### Environment Variables

| Variable | Required | Description |
|----------|----------|-------------|
| `VAULT_DIR` | No | Override vault data directory (default: `~/.vault`) |
| `OPENAI_API_KEY` | Yes (unless paranoid) | OpenAI API key for LLM calls |

---

*This document describes Vault as deployed. All encryption parameters, API endpoints, and architectural decisions are reflected in the codebase at the time of writing. Last updated: March 2026.*
