# Vault -- Complete Guide

*Your secure, local-first personal AI vault. All data encrypted on your machine.*

---

## What is Vault?

Vault is a personal AI agent that lives on your computer and acts as your encrypted digital memory. It stores your identity documents, website passwords, and personal facts -- and gives them back when you ask, through natural conversation.

What makes it different from a notes app or a password manager:

- It UNDERSTANDS your documents. You upload a scan of your Aadhaar card, and later ask "What's my Aadhaar number?" -- it reads the text and answers.
- It works through conversation. You say "Remember my blood type is O+" and it stores it. Later you ask "What's my blood type?" and it answers "O+".
- It is deeply private. Everything is encrypted with military-grade AES-256-GCM. Your passwords never leave your machine. Not even your AI assistant sees them.
- It works as an MCP server, meaning any AI client you already use (Claude in Cursor, Claude Desktop, etc.) can access your vault through standard tools -- no separate app needed.

---

## The Big Picture: How Everything Fits Together

Vault has three ways to interact with it:

```
                          YOU
                           |
            +--------------+--------------+
            |              |              |
            v              v              v
      +-----------+  +-----------+  +-----------+
      |    CLI    |  |  Web UI   |  |    MCP    |
      |           |  |           |  |  Server   |
      | Terminal  |  | Browser   |  |           |
      | commands  |  | at :8000  |  | Claude,   |
      |           |  |           |  | Cursor,   |
      |           |  |           |  | any MCP   |
      |           |  |           |  | client    |
      +-----------+  +-----------+  +-----------+
            |              |              |
            +--------------+--------------+
                           |
                           v
                  +------------------+
                  |   VAULT CORE     |
                  |                  |
                  |  Agent logic     |
                  |  Intent detect   |
                  |  Query routing   |
                  +--------+---------+
                           |
            +--------------+--------------+
            |              |              |
            v              v              v
      +-----------+  +-----------+  +-----------+
      | Document  |  |Credential |  |  Memory   |
      | Processor |  | Manager   |  |  (Facts)  |
      |           |  |           |  |           |
      | PDF text  |  | NEVER     |  | Key-value |
      | OCR scan  |  | goes to   |  | store for |
      | Semantic  |  | any LLM   |  | personal  |
      | search    |  |           |  | info      |
      +-----------+  +-----------+  +-----------+
            |              |              |
            +--------------+--------------+
                           |
                           v
              +------------------------+
              |   ENCRYPTED STORAGE    |
              |                        |
              |  SQLite DB (encrypted) |
              |  File vault (AES-256)  |
              |  ChromaDB (vectors)    |
              |                        |
              |  All at ~/.vault/data/ |
              +------------------------+
```

The key insight: **Vault is a storage and retrieval engine, not an AI itself.** When you use it via MCP (which is the recommended approach), Claude or whatever AI you're chatting with handles the conversation, and Vault handles the secure encrypted storage. When you use it standalone (CLI or web UI), Vault uses OpenAI/Ollama for the conversational AI part.

---

## Three Interfaces Explained

### 1. MCP Server (Recommended -- What We're Using)

**What it is:** MCP (Model Context Protocol) is a standard created by Anthropic that lets AI assistants use external tools. Vault exposes itself as 17 MCP tools that any MCP-compatible AI can call.

**How it works in practice:** You're chatting with Claude in Cursor (like right now). You say "remember my blood type is O+". Claude sees it has a `vault_remember` tool available, calls it with key="blood type" and value="O+", and Vault encrypts and stores it in your local database. Later you ask "what's my blood type?" and Claude calls `vault_recall` to get the answer.

**Why this is better:** You don't need a separate app. Your AI assistant (which you're already using) becomes the interface to your vault. The AI handles understanding what you want; Vault handles secure storage.

**17 tools available via MCP:**

| Tool | What it does | Touches LLM? |
|---|---|---|
| vault_unlock | Unlock vault with master password | No |
| vault_lock | Lock vault, wipe keys from memory | No |
| vault_status | Check if vault is initialized/unlocked | No |
| vault_store_document | Store & encrypt a document | No |
| vault_search_documents | Search docs by name or content | No |
| vault_read_document | Read full extracted text from a doc | No |
| vault_list_documents | List all stored documents | No |
| vault_retrieve_document_file | Get original file back (as base64) | No |
| vault_store_credential | Save a website login | No, NEVER |
| vault_get_credential | Retrieve a login/password | No, NEVER |
| vault_list_credentials | List saved services | No |
| vault_delete_credential | Remove a credential | No |
| vault_remember | Store a personal fact | No |
| vault_recall | Retrieve a personal fact | No |
| vault_list_facts | List all stored facts | No |
| vault_delete_fact | Remove a fact | No |
| vault_search | Search across everything | No |

Notice: **none of these tools call any LLM.** They are all pure local encrypted database operations. The AI reasoning happens in whatever client is calling the tools (Claude, Cursor, etc.).

**MCP config for Cursor (already set up):**

The file `~/.cursor/mcp.json` tells Cursor where to find the Vault MCP server:

```json
{
  "mcpServers": {
    "vault": {
      "command": "/Users/anindyag/vault/.venv/bin/vault-mcp"
    }
  }
}
```

When Cursor starts, it launches the `vault-mcp` process. This process runs the Vault MCP server using the `FastMCP` framework from the MCP Python SDK. It communicates with Cursor over stdin/stdout using the MCP protocol (JSON-RPC messages). Cursor sends tool call requests, Vault processes them locally, and returns results.

### 2. Web UI

A browser-based interface at http://127.0.0.1:8000 with:

- **Chat view** -- talk to Vault conversationally
- **Documents view** -- browse stored documents as cards, upload via drag-and-drop
- **Credentials view** -- see stored services and usernames
- **Memory view** -- see all personal facts
- **Lock screen** -- master password prompt
- **Auto-lock** -- overlay appears when session times out

Start it with `vault serve` in the terminal.

The web UI uses its own built-in LLM integration (OpenAI or Ollama) for the conversational part, unlike MCP mode where the client brings its own AI.

### 3. CLI (Terminal)

Direct commands for power users:

```bash
vault init          # First-time setup
vault chat          # Interactive conversation
vault store <file>  # Store a document
vault docs          # List documents
vault cred list     # List credentials
vault cred add      # Add a credential
vault facts         # List facts
vault mcp           # Start as MCP server
vault serve         # Start web UI
vault backup        # Encrypted backup
vault restore <file># Restore from backup
vault lock          # Lock immediately
vault unlock        # Unlock
```

---

## Security Design: How Your Data is Protected

This is the most important section. Every design decision starts with security.

### The Encryption Chain

When you create your vault with `vault init`, here's exactly what happens:

```
YOU type a master password
         |
         v
  +------------------+
  | Argon2id KDF     |   Your password + random salt
  | (memory-hard)    |   → 256-bit master key
  | 64MB memory      |
  | 3 iterations     |   Takes ~0.5 seconds intentionally
  | 4 parallel lanes |   (makes brute force impractical)
  +--------+---------+
           |
           |  Master key is NEVER stored on disk.
           |  It exists only in RAM while unlocked.
           |
     +-----+-----+-----+
     |           |           |
     v           v           v
 +-------+  +-------+  +-------+
 | DB    |  | File  |  | Cred  |
 | Key   |  | Key   |  | Key   |
 +---+---+  +---+---+  +---+---+
     |           |           |
     v           v           v
 Encrypts    Encrypts    Encrypts
 doc text,   raw files   passwords,
 facts,      (PDFs,      usernames,
 metadata    images)     login data
```

Three separate keys are derived from the master key using BLAKE2b with unique domain separation tags. This means:

- Even if somehow the database key leaked, it can't decrypt your files or credentials.
- The credential key is completely isolated -- an extra layer of protection for your most sensitive data.

### What Each Technology Does

**Argon2id** -- Password to key conversion. This is the algorithm that turns your master password into a 256-bit encryption key. "Memory-hard" means it deliberately uses 64MB of RAM during computation, which makes it extremely expensive for an attacker to try millions of passwords (even with GPUs or specialized hardware). It won the Password Hashing Competition in 2015 and is the current industry standard. The "id" variant is a hybrid that resists both side-channel attacks and GPU attacks.

**AES-256-GCM** -- The actual encryption. AES (Advanced Encryption Standard) with 256-bit keys is what governments use for classified data. GCM (Galois/Counter Mode) adds authentication -- it doesn't just encrypt, it also detects if anyone tampers with the encrypted data. Every piece of data gets a random 12-byte nonce (number used once) so encrypting the same data twice produces different ciphertext.

**BLAKE2b** -- Key derivation from master key to purpose-specific keys. Faster than SHA-256 but equally secure. Used here with "personalization" strings (like "vault-db-v1") to derive different keys for different purposes from the same master key.

**SQLite** -- The database that stores document metadata, encrypted text, encrypted facts, and encrypted credentials. It's a single file on disk (vault.db). All sensitive fields are encrypted at the application level before being written to SQLite.

**ChromaDB** -- Local vector database for semantic search. When you store a document, the extracted text is converted into a mathematical vector (embedding) that captures its meaning. When you search "identity documents", ChromaDB finds documents that are semantically related, even if they don't contain those exact words. Runs 100% locally, no API calls.

**sentence-transformers** -- Creates the vector embeddings for ChromaDB. Uses the `all-MiniLM-L6-v2` model which runs locally on your machine. Converts text into 384-dimensional vectors.

**PyMuPDF (fitz)** -- Extracts text from PDF files. Opens the PDF, reads each page, and pulls out all the text content. Runs locally, no network calls.

**Tesseract OCR (via pytesseract)** -- Optical Character Recognition for scanned images. When you upload a photo of your Aadhaar card, Tesseract reads the text in the image. Runs locally. Requires `brew install tesseract` on Mac.

**FastAPI** -- The web framework powering both the web UI (HTML served at localhost:8000) and the API endpoints. Lightweight, fast, async-capable Python framework.

**FastMCP** -- Part of the MCP Python SDK. Provides the decorator-based interface for defining MCP tools. Handles the JSON-RPC protocol, stdin/stdout communication with MCP clients, tool discovery, and argument validation.

**litellm** -- Unified interface for calling different LLM providers (OpenAI, Anthropic, Ollama). Used in the standalone CLI/web modes (not in MCP mode, where the client brings its own AI). A single `await litellm.acompletion(model="gpt-4o-mini", ...)` call works with any provider.

**Typer + Rich** -- CLI framework. Typer handles command parsing and help text. Rich provides the colored output, tables, and panels you see in the terminal.

### Password Verification (Without Storing the Password)

Your master password is never stored. So how does Vault know if you typed the right password?

During `vault init`, Vault encrypts a known piece of text ("VAULT_VERIFY_TOKEN_V1") with the DB key and saves the encrypted result. When you unlock, it derives the key from your password and tries to decrypt that token. If decryption succeeds and the result matches the known text, the password is correct. If not, the key is wrong, decryption fails, and access is denied.

### Session Management

When unlocked, the three encryption keys live in RAM (Python memory). After 5 minutes of no activity (configurable), the session auto-locks: keys are set to `None` and garbage collected. You need the master password again.

This means: even if someone gets access to your running Mac while you're away, the keys will have been wiped from memory after 5 minutes.

### What Goes to the Cloud vs What Stays Local

| What you do | In MCP mode | In standalone mode |
|---|---|---|
| "Store my Aadhaar card" | 100% local | 100% local |
| "What is my Aadhaar number?" | Claude reads the extracted text* | OpenAI gets text snippet |
| "Show me my passport" | 100% local | 100% local |
| "Save my Netflix password" | 100% local, NEVER external | 100% local, NEVER external |
| "What's my Netflix password?" | 100% local | 100% local |
| "Remember my blood type is O+" | 100% local | 100% local |
| "What's my blood type?" | 100% local | 100% local |

*In MCP mode, when you ask about a document, Vault returns the extracted text to Claude (the MCP client). Claude then reads and reasons over it. The text does go to Anthropic's API at that point, but your original file never leaves your machine, and credential data is never exposed this way.

---

## How Document Storage Works (Step by Step)

When you upload a file (say, a photo of your Aadhaar card):

```
1. aadhaar.jpg arrives (raw bytes)
         |
         v
2. Detect file type → "image"
         |
         v
3. Tesseract OCR extracts text:
   "GOVERNMENT OF INDIA
    Aadhaar - UID
    Name: Anindya Gupta
    DOB: 15/01/1990
    Aadhaar No: 1234 5678 9012"
         |
    +----+----+
    |         |
    v         v
4a. Raw file           4b. Extracted text
    encrypted               encrypted
    with FILE key           with DB key
    → saved as              → stored in
    uuid.enc                SQLite
    |                        |
    v                        v
5a. Stored in           5b. Also embedded
    ~/.vault/data/          as vector in
    files/                  ChromaDB for
                            semantic search
```

Auto-categorization kicks in: the keyword "Aadhaar" is detected, so the document is filed under "identity".

Later, when you ask "What is my Aadhaar number?":

```
1. Your question arrives
         |
         v
2. ChromaDB semantic search
   finds the Aadhaar document
         |
         v
3. Encrypted text is decrypted
   (DB key, in memory only)
         |
         v
4. Text is returned to the AI
   (Claude via MCP, or OpenAI
   in standalone mode)
         |
         v
5. AI reads the text:
   "Aadhaar No: 1234 5678 9012"
         |
         v
6. Answer: "Your Aadhaar number
   is 1234 5678 9012"
```

---

## How Credential Storage Works

Credentials get extra protection. They use a separate encryption key and a completely separate code path that NEVER involves any LLM.

```
You: "My Netflix login is user@email.com, password is secret123"
         |
         v
Parse with regex:
  service = "netflix"
  username = "user@email.com"
  password = "secret123"
         |
    +----+----+
    |         |
    v         v
username      password
encrypted     encrypted
with CRED     with CRED
key           key
    |         |
    +----+----+
         |
         v
Stored in SQLite
(double encrypted:
 field-level AES + DB-level)
```

Retrieval is a direct database lookup. No AI involved:

```
You: "What's my Netflix password?"
         |
         v
DB query: WHERE service = 'netflix'
         |
         v
Decrypt username + password with CRED key
         |
         v
Return directly: "user@email.com / secret123"
```

---

## How MCP Communication Works

When you talk to Claude in Cursor and it needs your vault:

```
You (in Cursor): "What's my Netflix password?"
         |
         v
Claude (Anthropic API) decides:
  "I should use the vault_get_credential tool"
         |
         v
Cursor sends JSON-RPC over stdin to vault-mcp process:
  {
    "method": "tools/call",
    "params": {
      "name": "vault_get_credential",
      "arguments": {"service": "netflix"}
    }
  }
         |
         v
vault-mcp process (on YOUR Mac):
  1. Checks session is unlocked
  2. Queries encrypted SQLite DB
  3. Decrypts credential with CRED key
  4. Returns plaintext result
         |
         v
Cursor receives result, Claude shows you:
  "Your Netflix credentials:
   Username: user@email.com
   Password: secret123"
```

The vault-mcp process runs locally. It's a long-running process that Cursor launches on startup and communicates with over stdin/stdout. No network server, no ports, no HTTP -- just direct process communication.

---

## Configuration

After `vault init`, your config lives at `~/.vault/config.yaml`:

```yaml
llm_provider: openai          # "openai", "anthropic", or "ollama"
llm_model: gpt-4o-mini        # cloud model (for standalone CLI/web mode only)
ollama_model: llama3.1:8b     # local model for paranoid mode
paranoid_mode: false           # true = fully offline
session_timeout: 300           # auto-lock after 5 minutes
ocr_enabled: true              # OCR for scanned documents
```

In MCP mode, `llm_provider` and `llm_model` are irrelevant because the MCP client (Claude) handles all the AI reasoning. These settings only matter for standalone `vault chat` and `vault serve` modes.

---

## Backup and Recovery

### Create a Backup

```bash
vault backup
# Creates: ~/.vault/vault_backup_20260312_143000.vbak
```

The .vbak file is a compressed tar archive of your entire encrypted data directory. It's safe to store on iCloud Drive, Google Drive, a USB stick -- because all the data inside is still AES-256-GCM encrypted. Without your master password, the backup is unreadable.

### Restore on a New Machine

```bash
# On the new machine:
cd ~/vault && pip install -e .
vault restore ~/path/to/backup.vbak
vault unlock   # enter your master password
# Everything is back
```

### If Your Mac is Lost or Stolen

- Your data on the stolen Mac is encrypted. Without your master password, it's unreadable.
- If you have a backup elsewhere, restore it on a new machine and you lose nothing.
- If you have NO backup, your data is gone forever (but also unreadable by the thief).

---

## Accessing from Your Phone

Right now, Vault runs on your Mac. When your Mac is off, you can't access it. Options for phone access:

**Same Wi-Fi (simplest):** Run `vault serve --host 0.0.0.0` and access from your phone's browser at `http://<your-mac-ip>:8000`. Works when both devices are on the same network.

**Tailscale (any network, Mac must be on):** Install Tailscale on Mac and phone. Access Vault from anywhere via the Tailscale private IP. End-to-end encrypted. Mac needs to be awake.

**Raspberry Pi (always-on, Mac not needed):** Run Vault on a Pi plugged into your home router. It runs 24/7 on ~3 watts of power. Use Tailscale to access from anywhere. Your Mac becomes just another client.

---

## Quick Start

```bash
# 1. Go to the project
cd ~/vault

# 2. Activate the virtual environment
source .venv/bin/activate

# 3. Set your OpenAI key (only needed for standalone CLI/web mode)
export OPENAI_API_KEY=sk-your-key-here

# 4. Initialize (first time only)
vault init

# 5. For MCP: restart Cursor, and the vault tools are available
# 6. For web UI: vault serve, then open http://127.0.0.1:8000
# 7. For terminal: vault chat
```

---

## Prerequisites

- **macOS** with Apple Silicon (M1/M2/M3/M4) or Intel
- **Python 3.11+** (check with `python3 --version`)
- **Tesseract OCR** for scanned documents: `brew install tesseract`
- **OpenAI API key** (only for standalone mode, not needed for MCP):
  ```bash
  export OPENAI_API_KEY=sk-...
  ```

---

## Project Files

```
~/vault/                            # PROJECT CODE
|-- vault/
|   |-- __init__.py                 # Package version
|   |-- agent.py                    # Brain: intent detect + query routing
|   |-- backup.py                   # Backup/restore logic
|   |-- cli.py                      # Terminal commands
|   |-- config.py                   # Configuration management
|   |-- main.py                     # Web server (FastAPI)
|   |-- mcp_server.py              # MCP server (17 tools for Claude/Cursor)
|   |-- security/
|   |   |-- encryption.py           # AES-256-GCM, Argon2id, BLAKE2b
|   |   |-- session.py              # Session lock/unlock, auto-timeout
|   |-- storage/
|   |   |-- database.py             # SQLite database with encrypted fields
|   |   |-- file_vault.py           # Encrypted file storage on disk
|   |   |-- vector_store.py         # ChromaDB semantic search
|   |-- processors/
|   |   |-- document.py             # PDF/image text extraction, OCR
|   |   |-- credentials.py          # Credential CRUD (never touches LLM)
|   |   |-- memory.py               # Personal facts store
|   |-- llm/
|   |   |-- router.py               # LLM switching (cloud vs Ollama)
|   |   |-- prompts.py              # AI prompts (standalone mode only)
|   |-- web/
|       |-- static/css/style.css    # Dark theme
|       |-- static/js/app.js        # Frontend logic
|       |-- templates/*.html        # Chat UI, lock screen, setup
|-- .venv/                          # Python virtual environment
|-- pyproject.toml                  # Package config + dependencies
|-- requirements.txt                # Dependencies list
|-- README.md                       # Short readme

~/.vault/                           # YOUR DATA (encrypted)
|-- config.yaml                     # Settings (non-sensitive)
|-- data/
    |-- vault.db                    # Encrypted database
    |-- files/                      # Encrypted document files
    |-- chroma/                     # Semantic search index
    |-- .salt                       # Argon2id salt
    |-- .verify_token               # Password verification token

~/.cursor/mcp.json                  # MCP config (tells Cursor about Vault)
```

---

## FAQ

**Q: What if I forget my master password?**
Your data is permanently lost. There is no recovery. This is by design -- if we could recover it, so could an attacker.

**Q: Is my data safe if my Mac is stolen?**
Yes. Everything under `~/.vault/data/` is encrypted with AES-256-GCM. Without your master password, it is computationally infeasible to read.

**Q: Do AI companies see my passwords?**
No. Credentials use a completely separate code path with direct encrypted database lookups. No LLM (whether OpenAI, Anthropic, or local) ever sees your passwords.

**Q: In MCP mode, what does Claude see?**
Claude sees the results of vault tool calls. For document queries, it sees extracted text. For credentials, it sees the decrypted username/password (so it can show you). For facts, it sees the value. Claude does NOT see your raw files or your master password. The MCP tool results stay within your Cursor session.

**Q: Does Claude see document text when I ask questions?**
Yes, in MCP mode, when you ask "What's my Aadhaar number?", Claude calls vault_read_document, gets back the extracted text, and reads it to find the answer. That text goes through Anthropic's API. If this concerns you, use the standalone CLI with paranoid mode (Ollama) for fully local processing.

**Q: Can I use this without internet?**
Mostly yes. All storage, retrieval, and credential operations work offline. Only AI-powered document Q&A needs internet (or Ollama for local). In MCP mode, you need internet for Claude, but all vault operations are local.

**Q: Can I use both MCP mode and the web UI?**
Yes, but not simultaneously from the same vault. The MCP server and the web server are separate processes. They both read from the same encrypted data on disk, but the session (unlock state, keys in memory) is per-process. You'd need to unlock in each one separately.

**Q: How do I move Vault to a new Mac?**
Run `vault backup`, copy the .vbak file, install Vault on the new Mac, run `vault restore`, then `vault unlock`. Copy your `~/.cursor/mcp.json` too if using MCP mode.

**Q: Where does the code live vs where does my data live?**
Code: `~/vault/`. Data: `~/.vault/`. They're separate. You can delete and reinstall the code without losing data.
