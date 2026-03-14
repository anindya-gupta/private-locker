# Vault — Secure Personal AI Agent

A local-first, encrypted personal AI assistant that stores your documents, credentials, and personal facts — and retrieves them when you ask.

**Everything stays on your machine, encrypted with AES-256-GCM. Your data never leaves unless you explicitly use a cloud LLM.**

## Features

- **Document Storage** — Store Aadhaar, passport, certificates, etc. Text is extracted via OCR and made searchable.
- **Smart Q&A** — Ask "What is my Aadhaar number?" and Vault reads it from your stored document.
- **Credential Manager** — Store website logins. Credentials are double-encrypted and *never* sent to any LLM.
- **Personal Memory** — Tell Vault your blood type, allergies, preferences. It remembers and recalls on demand.
- **Semantic Search** — Find documents by meaning, not just keywords.
- **Paranoid Mode** — Toggle to use a local Ollama model with zero network activity.

## Security

- Master password with Argon2id key derivation (memory-hard, brute-force resistant)
- AES-256-GCM encryption for all data at rest
- Three separate derived keys: database, files, credentials
- Session auto-lock after inactivity
- Credentials never touch the LLM — purely local lookup
- No telemetry, no analytics

## Quick Start

```bash
# Install
cd vault
pip install -e .

# Initialize (creates encrypted vault)
vault init

# Interactive chat
vault chat

# Start web UI
vault serve
# Open http://127.0.0.1:8000
```

## CLI Commands

| Command | Description |
|---------|-------------|
| `vault init` | First-time setup with master password |
| `vault chat` | Interactive chat in terminal |
| `vault store <file>` | Store a document |
| `vault docs` | List stored documents |
| `vault cred list` | List stored credentials |
| `vault cred add` | Add a credential |
| `vault cred get --service netflix` | Retrieve a credential |
| `vault facts` | List stored personal facts |
| `vault backup` | Create encrypted backup |
| `vault restore <file>` | Restore from backup |
| `vault serve` | Start web UI |
| `vault lock` | Lock the vault |

## Configuration

After init, config is at `~/.vault/config.yaml`:

```yaml
llm_provider: openai        # or "anthropic", "ollama"
llm_model: gpt-4o-mini      # cloud model to use
ollama_model: llama3.1:8b   # local model for paranoid mode
paranoid_mode: false         # true = all-local, zero network
session_timeout: 300         # auto-lock in seconds
ocr_enabled: true            # enable OCR for scanned documents
```

## Environment Variables

Set your LLM API key:

```bash
export OPENAI_API_KEY=sk-...
# or
export ANTHROPIC_API_KEY=sk-ant-...
```

For fully local operation (paranoid mode), no API keys are needed — just install Ollama.

## Backup & Restore

```bash
# Create backup
vault backup
# -> Saved to ~/.vault/vault_backup_20260312_143000.vbak

# Restore on new machine
vault restore ~/vault_backup_20260312_143000.vbak
vault unlock
```

Backups are safe to store on cloud drives — they're encrypted and need your master password to read.

## Requirements

- Python 3.11+
- Tesseract OCR (for scanned document support): `brew install tesseract`
- Ollama (optional, for paranoid mode): https://ollama.ai
