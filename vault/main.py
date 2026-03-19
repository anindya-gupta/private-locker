"""
FastAPI application — serves the web UI and API endpoints.

Uses per-client cookie-based sessions for security isolation.
"""

from __future__ import annotations

import base64
import json
import logging
import os
import secrets
import time
from collections import defaultdict
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, Optional

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from vault.agent import VaultAgent
from vault.config import VaultConfig, config
from vault.security.encryption import derive_all_keys, generate_verification_token
from vault.security.session import Session, session_store

logger = logging.getLogger(__name__)

COOKIE_NAME = "vault_sid"
COOKIE_MAX_AGE = 86400  # 24 hours

agent: Optional[VaultAgent] = None

_unlock_attempts: dict[str, list[float]] = defaultdict(list)
UNLOCK_RATE_LIMIT = 5
UNLOCK_RATE_WINDOW = 60


def _check_rate_limit(client_ip: str) -> bool:
    now = time.monotonic()
    attempts = _unlock_attempts[client_ip]
    _unlock_attempts[client_ip] = [t for t in attempts if now - t < UNLOCK_RATE_WINDOW]
    if len(_unlock_attempts[client_ip]) >= UNLOCK_RATE_LIMIT:
        return True
    _unlock_attempts[client_ip].append(now)
    return False


def _get_session(request: Request) -> Optional[Session]:
    """Extract session from cookie."""
    token = request.cookies.get(COOKIE_NAME)
    return session_store.get(token)


def _get_token(request: Request) -> Optional[str]:
    return request.cookies.get(COOKIE_NAME)


def _require_session(request: Request) -> Session:
    """Get session or raise 401."""
    s = _get_session(request)
    if s is None:
        raise HTTPException(401, "Not authenticated")
    return s


def _set_cookie(response: Response, token: str) -> Response:
    is_secure = os.environ.get("VAULT_INSECURE") != "1"
    response.set_cookie(
        COOKIE_NAME, token,
        max_age=COOKIE_MAX_AGE,
        httponly=True,
        secure=is_secure,
        samesite="strict",
        path="/",
    )
    return response


def _clear_cookie(response: Response) -> Response:
    response.delete_cookie(COOKIE_NAME, path="/")
    return response


def _load_username() -> Optional[str]:
    """Load username from database meta table if set."""
    if agent and agent.db._conn:
        try:
            row = agent.db._conn.execute(
                "SELECT value FROM meta WHERE key = 'username'"
            ).fetchone()
            return row["value"] if row else None
        except Exception:
            return None
    return None


@asynccontextmanager
async def lifespan(application: FastAPI):
    global agent
    dummy_session = Session()
    if config.salt_path.exists():
        salt = config.salt_path.read_bytes()
        token = config.token_path.read_bytes()
        dummy_session.configure(salt, token, config.session_timeout)
        session_store.configure(salt, token, config.session_timeout)

    agent = VaultAgent(config, dummy_session)
    agent.initialize()

    username = _load_username()
    if username:
        session_store.username = username

    logger.info("Vault agent initialized")
    yield
    if agent:
        agent.shutdown()


app = FastAPI(title="Vault", lifespan=lifespan)


@app.middleware("http")
async def security_headers(request: Request, call_next):
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["X-XSS-Protection"] = "1; mode=block"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
    response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate"
    response.headers["Pragma"] = "no-cache"
    if request.url.scheme == "https":
        response.headers["Strict-Transport-Security"] = "max-age=63072000; includeSubDomains"
    return response

WEB_DIR = Path(__file__).parent / "web"
app.mount("/static", StaticFiles(directory=str(WEB_DIR / "static")), name="static")
templates = Jinja2Templates(directory=str(WEB_DIR / "templates"))


# ===== Page Routes =====

@app.get("/", response_class=HTMLResponse)
async def root(request: Request):
    initialized = config.salt_path.exists()
    if not initialized:
        return RedirectResponse("/setup", status_code=302)
    s = _get_session(request)
    if s is None:
        return RedirectResponse("/login", status_code=302)
    return RedirectResponse("/app", status_code=302)


@app.get("/setup", response_class=HTMLResponse)
async def setup_page(request: Request):
    if config.salt_path.exists():
        return RedirectResponse("/login", status_code=302)
    return templates.TemplateResponse("setup.html", {"request": request})


@app.get("/login", response_class=HTMLResponse)
async def login_page(request: Request):
    if not config.salt_path.exists():
        return RedirectResponse("/setup", status_code=302)
    s = _get_session(request)
    if s is not None:
        return RedirectResponse("/app", status_code=302)
    has_username = session_store.username is not None
    mode = request.query_params.get("mode", "")
    password_only = (mode == "lock") and has_username
    return templates.TemplateResponse("unlock.html", {
        "request": request,
        "has_username": has_username,
        "password_only": password_only,
    })


@app.get("/app", response_class=HTMLResponse)
@app.get("/app/{path:path}", response_class=HTMLResponse)
async def app_page(request: Request, path: str = ""):
    if not config.salt_path.exists():
        return RedirectResponse("/setup", status_code=302)
    s = _get_session(request)
    if s is None:
        return RedirectResponse("/login", status_code=302)
    return templates.TemplateResponse("index.html", {"request": request})


# ===== API Endpoints =====

@app.post("/api/init")
async def api_init(request: Request):
    data = await request.json()
    password = data.get("password", "")
    username = data.get("username", "").strip()
    if len(password) < 8:
        raise HTTPException(400, "Password must be at least 8 characters")

    keys = derive_all_keys(password)
    token = generate_verification_token(password, keys.salt)

    config.ensure_dirs()
    config.salt_path.write_bytes(keys.salt)
    config.token_path.write_bytes(token)
    config.save()

    session_store.configure(keys.salt, token, config.session_timeout, username=username or None)

    global agent
    dummy = Session()
    dummy.configure(keys.salt, token, config.session_timeout)
    if agent:
        agent.shutdown()
    agent = VaultAgent(config, dummy)
    agent.initialize()

    if username:
        agent.db._conn.execute(
            "INSERT OR REPLACE INTO meta (key, value) VALUES (?, ?)",
            ("username", username),
        )
        agent.db._conn.commit()

    sid = session_store.unlock(password, username=username or None)
    if not sid:
        raise HTTPException(500, "Failed to create session after init")

    response = JSONResponse({"status": "ok", "message": "Vault initialized successfully"})
    _set_cookie(response, sid)
    return response


@app.post("/api/unlock")
async def api_unlock(request: Request):
    client_ip = request.client.host if request.client else "unknown"
    if _check_rate_limit(client_ip):
        raise HTTPException(429, "Too many unlock attempts. Try again in a minute.")

    data = await request.json()
    password = data.get("password", "")
    username = data.get("username", "").strip() or None

    if not config.salt_path.exists():
        raise HTTPException(400, "Vault not initialized")

    if not session_store.is_configured:
        salt = config.salt_path.read_bytes()
        tok = config.token_path.read_bytes()
        stored_user = _load_username()
        session_store.configure(salt, tok, config.session_timeout, username=stored_user)

    sid = session_store.unlock(password, username=username)
    if sid:
        _unlock_attempts.pop(client_ip, None)
        response = JSONResponse({"status": "ok"})
        _set_cookie(response, sid)
        return response

    raise HTTPException(401, "Incorrect credentials")


@app.post("/api/lock")
async def api_lock(request: Request):
    token = _get_token(request)
    if token:
        session_store.lock(token)
    response = JSONResponse({"status": "ok"})
    _clear_cookie(response)
    return response


@app.post("/api/logout")
async def api_logout(request: Request):
    token = _get_token(request)
    if token:
        session_store.destroy(token)
    response = JSONResponse({"status": "ok"})
    _clear_cookie(response)
    return response


@app.get("/api/status")
async def api_status(request: Request):
    s = _get_session(request)
    return {
        "initialized": config.salt_path.exists(),
        "locked": s is None,
        "has_username": session_store.username is not None,
        "paranoid_mode": config.paranoid_mode,
        "active_sessions": session_store.active_count(),
    }


@app.post("/api/upload-preview")
async def api_upload_preview(
    request: Request,
    file: UploadFile = File(...),
):
    """Extract metadata and suggest a name without storing anything."""
    _require_session(request)
    if not agent:
        raise HTTPException(500, "Agent not initialized")

    file_data = await file.read()
    file_name = file.filename or "unknown"

    from vault.processors.document import extract_text, guess_category, extract_document_metadata

    extracted = extract_text(file_data, file_name)
    category = guess_category(file_name, extracted)
    regex_meta = extract_document_metadata(file_name, extracted, category) if extracted else {}

    llm_meta: dict = {}
    if extracted:
        try:
            llm_meta = await agent.llm.extract_document_metadata(file_name, category, extracted)
        except Exception:
            pass

    merged = {**regex_meta, **{k: v for k, v in llm_meta.items() if v}}

    suggested_name = merged.get("suggested_name") or file_name
    if suggested_name == file_name and merged.get("summary"):
        suggested_name = merged["summary"][:60]

    return {
        "suggested_name": suggested_name,
        "category": category,
        "sub_category": merged.get("sub_category"),
        "doctor": merged.get("doctor"),
        "doc_date": merged.get("doc_date"),
        "summary": merged.get("summary"),
        "has_text": bool(extracted),
    }


@app.post("/api/chat")
async def api_chat(
    request: Request,
    message: str = Form(""),
    file: Optional[UploadFile] = File(None),
    force: bool = Form(False),
):
    if not agent:
        raise HTTPException(500, "Agent not initialized")
    s = _require_session(request)

    file_data = None
    file_name = None
    if file and file.filename:
        file_data = await file.read()
        file_name = file.filename

    old_session = agent.session
    agent.session = s
    try:
        response = await agent.process(message, file_data=file_data, file_name=file_name, force=force)
        result: dict[str, Any] = {"text": response.text}
        if response.data and response.data.get("duplicate_warning"):
            result["duplicate_warning"] = True
            result["existing_name"] = response.data.get("existing_name")
        if response.data and response.data.get("doc_id"):
            s._last_doc_id = response.data.get("doc_id")
            s._last_doc_name = response.data.get("doc_name") or response.data.get("doc_id")
        if response.data and response.data.get("create_share_for_doc_id"):
            try:
                share_url, _token, expires_in = _create_share_link_for_doc(
                    request, response.data["create_share_for_doc_id"], s
                )
                result["share_url"] = share_url
                result["share_expires_in"] = expires_in
            except HTTPException:
                pass
        if response.file_data and response.file_name:
            result["file"] = {
                "name": response.file_name,
                "data": base64.b64encode(response.file_data).decode("ascii"),
            }
        return result
    except PermissionError:
        raise HTTPException(401, "Session expired")
    except Exception as e:
        logger.error("Chat error: %s", e)
        raise HTTPException(500, str(e))
    finally:
        agent.session = old_session


@app.post("/api/change-password")
async def api_change_password(request: Request):
    s = _require_session(request)
    data = await request.json()
    current = data.get("current_password", "")
    new_pw = data.get("new_password", "")
    if len(new_pw) < 8:
        raise HTTPException(400, "New password must be at least 8 characters")

    salt = config.salt_path.read_bytes()
    from vault.security.encryption import verify_password
    if not verify_password(current, salt, config.token_path.read_bytes()):
        raise HTTPException(401, "Current password is incorrect")

    new_keys = derive_all_keys(new_pw)
    new_token = generate_verification_token(new_pw, new_keys.salt)

    old_keys = s.keys

    all_docs = agent.db.list_documents(old_keys.db_key)
    all_creds = agent.db.list_credentials(old_keys.cred_key)
    all_facts = agent.db.list_facts(old_keys.db_key)

    for doc in all_docs:
        if doc.get("extracted_text"):
            agent.db.store_document(
                name=doc["name"], category=doc["category"],
                encryption_key=new_keys.db_key, file_ref=doc.get("file_ref"),
                extracted_text=doc["extracted_text"], tags=doc.get("tags", []),
            )
            agent.db.delete_document(doc["id"])

    for cred in all_creds:
        agent.db.store_credential(
            service=cred["service"], cred_key=new_keys.cred_key,
            username=cred.get("username"), password=cred.get("password"),
            url=cred.get("url"), notes=cred.get("notes"),
        )
        agent.db.delete_credential(cred["id"])

    for fact in all_facts:
        agent.db.store_fact(
            key=fact["key"], value=fact["value"],
            encryption_key=new_keys.db_key, category=fact["category"],
        )
        agent.db.delete_fact(fact["id"])

    config.salt_path.write_bytes(new_keys.salt)
    config.token_path.write_bytes(new_token)

    stored_user = _load_username()
    session_store.lock_all()
    session_store.configure(new_keys.salt, new_token, config.session_timeout, username=stored_user)

    sid = session_store.unlock(new_pw, username=stored_user)
    response = JSONResponse({"status": "ok", "message": "Password changed. All data re-encrypted."})
    if sid:
        _set_cookie(response, sid)
    return response


@app.post("/api/backup")
async def api_backup(request: Request):
    _require_session(request)
    try:
        from vault.backup import create_backup
        backup_path = create_backup(config)
        return {"status": "ok", "path": str(backup_path)}
    except Exception as e:
        raise HTTPException(500, str(e))


# ===== Reindex Documents =====

@app.post("/api/reindex")
async def api_reindex(request: Request):
    """Re-process all existing documents to extract and store rich metadata."""
    s = _require_session(request)
    if not agent or not agent.db._conn:
        raise HTTPException(500, "Database not available")

    from vault.processors.document import extract_document_metadata, guess_category

    docs = agent.db.list_documents(s.keys.db_key)
    updated = 0
    errors = 0

    for doc in docs:
        try:
            text = doc.get("extracted_text") or ""
            if not text:
                continue

            category = guess_category(doc["name"], text)
            regex_meta = extract_document_metadata(doc["name"], text, category)

            llm_meta: dict = {}
            try:
                llm_meta = await agent.llm.extract_document_metadata(doc["name"], category, text)
            except Exception:
                pass

            merged = {**regex_meta, **{k: v for k, v in llm_meta.items() if v}}

            tags = [category, doc["name"].rsplit(".", 1)[-1] if "." in doc["name"] else "unknown"]
            if merged.get("sub_category"):
                tags.append(f"sub:{merged['sub_category']}")
            if merged.get("doctor"):
                tags.append(f"doctor:{merged['doctor']}")
            if merged.get("doc_date"):
                tags.append(f"date:{merged['doc_date']}")
            if merged.get("summary"):
                tags.append(f"summary:{merged['summary']}")
            for kw in merged.get("keywords", []):
                tags.append(f"kw:{kw}")

            agent.db.update_document_meta(doc["id"], category=category, tags=tags)

            vector_meta = {"name": doc["name"], "category": category}
            if merged.get("sub_category"):
                vector_meta["sub_category"] = merged["sub_category"]
            if merged.get("doctor"):
                vector_meta["doctor"] = merged["doctor"]
            if merged.get("doc_date"):
                vector_meta["doc_date"] = merged["doc_date"]
            agent.vector_store.add_document(doc["id"], text, vector_meta)

            updated += 1
        except Exception as e:
            logger.error("Reindex failed for doc %s: %s", doc["id"], e)
            errors += 1

    return {
        "status": "ok",
        "total": len(docs),
        "updated": updated,
        "skipped": len(docs) - updated - errors,
        "errors": errors,
    }


# ===== Database Viewer =====

@app.get("/api/db-viewer")
async def api_db_viewer_summary(request: Request):
    _require_session(request)
    if not agent or not agent.db._conn:
        raise HTTPException(500, "Database not available")

    conn = agent.db._conn
    tables = {}
    for tbl in ["meta", "documents", "credentials", "facts"]:
        count = conn.execute(f"SELECT COUNT(*) as c FROM {tbl}").fetchone()["c"]
        cols = [row[1] for row in conn.execute(f"PRAGMA table_info({tbl})").fetchall()]
        tables[tbl] = {"count": count, "columns": cols}

    return {"tables": tables}


@app.get("/api/db-viewer/{table}")
async def api_db_viewer_table(request: Request, table: str):
    s = _require_session(request)
    if not agent or not agent.db._conn:
        raise HTTPException(500, "Database not available")
    if table not in ("meta", "documents", "credentials", "facts"):
        raise HTTPException(400, "Invalid table name")

    keys = s.keys

    if table == "meta":
        rows = agent.db._conn.execute("SELECT * FROM meta").fetchall()
        return {"rows": [dict(r) for r in rows]}

    if table == "documents":
        docs = agent.db.list_documents(keys.db_key)
        for d in docs:
            if d.get("extracted_text") and len(d["extracted_text"]) > 200:
                d["extracted_text"] = d["extracted_text"][:200] + "..."
        return {"rows": docs}

    if table == "credentials":
        creds = agent.db.list_credentials(keys.cred_key)
        for c in creds:
            if c.get("password"):
                c["password"] = "****" + c["password"][-2:] if len(c["password"]) > 2 else "****"
        return {"rows": creds}

    if table == "facts":
        facts = agent.db.list_facts(keys.db_key)
        return {"rows": facts}

    return {"rows": []}


# ===== Expiry Alerts =====

@app.get("/api/expiry-alerts")
async def api_expiry_alerts(request: Request):
    s = _require_session(request)
    if not agent or not agent.db._conn:
        raise HTTPException(500, "Database not available")

    from datetime import datetime, timedelta

    keys = s.keys
    docs = agent.db.list_documents(keys.db_key)
    alerts = []
    today = datetime.now().date()
    threshold = today + timedelta(days=90)

    for doc in docs:
        tags = doc.get("tags", [])
        if isinstance(tags, str):
            import json as _json
            try:
                tags = _json.loads(tags)
            except Exception:
                tags = []
        for tag in tags:
            if tag.startswith("expiry:"):
                date_str = tag[7:]
                try:
                    expiry = datetime.strptime(date_str, "%Y-%m-%d").date()
                except ValueError:
                    continue
                days_until = (expiry - today).days
                if days_until <= 90:
                    status = "expired" if days_until < 0 else "expiring_soon"
                    alerts.append({
                        "doc_id": doc["id"],
                        "name": doc["name"],
                        "category": doc.get("category", "general"),
                        "expiry_date": date_str,
                        "days_until": days_until,
                        "status": status,
                    })
                break

    alerts.sort(key=lambda a: a["days_until"])
    return {"alerts": alerts}


# ===== Stats endpoint for dashboard =====

@app.get("/api/stats")
async def api_stats(request: Request):
    s = _require_session(request)
    if not agent or not agent.db._conn:
        raise HTTPException(500, "Database not available")

    conn = agent.db._conn
    doc_count = conn.execute("SELECT COUNT(*) as c FROM documents").fetchone()["c"]
    cred_count = conn.execute("SELECT COUNT(*) as c FROM credentials").fetchone()["c"]
    fact_count = conn.execute("SELECT COUNT(*) as c FROM facts").fetchone()["c"]
    birthday_count = conn.execute("SELECT COUNT(*) as c FROM facts WHERE category = 'birthday'").fetchone()["c"]

    upcoming = 0
    if birthday_count > 0:
        from datetime import datetime
        bdays = agent.memory.list_all(s.keys.db_key, category="birthday")
        today = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
        for b in bdays:
            parsed = agent._parse_birthday_date(b["value"])
            if parsed:
                this_year = parsed.replace(year=today.year)
                if this_year < today:
                    this_year = this_year.replace(year=today.year + 1)
                if (this_year - today).days <= 30:
                    upcoming += 1

    expiring_count = 0
    try:
        from datetime import timedelta
        exp_docs = agent.db.list_documents(s.keys.db_key)
        exp_today = today.date() if hasattr(today, 'date') else today
        for d in exp_docs:
            dtags = d.get("tags", [])
            if isinstance(dtags, str):
                import json as _json
                try:
                    dtags = _json.loads(dtags)
                except Exception:
                    dtags = []
            for t in dtags:
                if t.startswith("expiry:"):
                    try:
                        exp = datetime.strptime(t[7:], "%Y-%m-%d").date()
                        if (exp - exp_today).days <= 90:
                            expiring_count += 1
                    except ValueError:
                        pass
                    break
    except Exception:
        pass

    return {
        "documents": doc_count,
        "credentials": cred_count,
        "facts": fact_count,
        "active_sessions": session_store.active_count(),
        "total_birthdays": birthday_count,
        "upcoming_birthdays": upcoming,
        "expiring_soon": expiring_count,
    }


@app.get("/api/documents")
async def api_list_documents(request: Request):
    s = _require_session(request)
    if not agent or not agent.db._conn:
        raise HTTPException(500, "Database not available")
    docs = agent.db.list_documents(s.keys.db_key)
    return {"documents": [{
        "id": d["id"], "name": d["name"], "category": d["category"],
        "tags": d.get("tags", []),
        "created_at": d.get("created_at"),
        "updated_at": d.get("updated_at"),
    } for d in docs]}


@app.get("/api/credentials")
async def api_list_credentials(request: Request):
    s = _require_session(request)
    if not agent or not agent.db._conn:
        raise HTTPException(500, "Database not available")
    creds = agent.db.list_credentials(s.keys.cred_key)
    return {"credentials": [{
        "id": c["id"], "service": c["service"],
        "username": c.get("username", ""),
        "url": c.get("url", ""),
        "created_at": c.get("created_at"),
    } for c in creds]}


@app.get("/api/facts")
async def api_list_facts(request: Request):
    s = _require_session(request)
    if not agent or not agent.db._conn:
        raise HTTPException(500, "Database not available")
    facts = agent.db.list_facts(s.keys.db_key)
    return {"facts": [{
        "id": f["id"], "category": f.get("category", "general"),
        "key": f["key"], "value": f["value"],
        "created_at": f.get("created_at"),
    } for f in facts]}


@app.get("/api/reminders")
async def api_list_reminders(request: Request):
    s = _require_session(request)
    if not agent or not agent.db._conn:
        raise HTTPException(500, "Database not available")
    reminders = agent.db.list_reminders(s.keys.db_key)
    from datetime import datetime as _dt
    today = _dt.now().date()
    result = []
    for r in reminders:
        try:
            due = _dt.strptime(r["due_date"], "%Y-%m-%d").date()
            days_until = (due - today).days
        except ValueError:
            days_until = None
        result.append({
            "id": r["id"],
            "title": r["title"],
            "due_date": r["due_date"],
            "repeat_interval": r.get("repeat_interval"),
            "status": r["status"],
            "days_until": days_until,
            "created_at": r.get("created_at"),
        })
    return {"reminders": result}


@app.post("/api/reminders")
async def api_create_reminder(request: Request):
    s = _require_session(request)
    if not agent or not agent.db._conn:
        raise HTTPException(500, "Database not available")
    body = await request.json()
    title = body.get("title", "")
    due_date = body.get("due_date", "")
    if not title or not due_date:
        raise HTTPException(400, "title and due_date are required")
    rem_id = agent.db.store_reminder(
        title=title,
        due_date=due_date,
        encryption_key=s.keys.db_key,
        repeat_interval=body.get("repeat_interval"),
    )
    return {"id": rem_id, "status": "created"}


@app.delete("/api/reminders/{rem_id}")
async def api_delete_reminder(request: Request, rem_id: str):
    s = _require_session(request)
    if not agent or not agent.db._conn:
        raise HTTPException(500, "Database not available")
    if agent.db.delete_reminder(rem_id):
        return {"status": "deleted"}
    raise HTTPException(404, "Reminder not found")


@app.post("/api/reminders/{rem_id}/complete")
async def api_complete_reminder(request: Request, rem_id: str):
    s = _require_session(request)
    if not agent or not agent.db._conn:
        raise HTTPException(500, "Database not available")
    if agent.db.complete_reminder(rem_id):
        return {"status": "completed"}
    raise HTTPException(404, "Reminder not found")


# ===== Document Sharing =====

_share_tokens: dict[str, dict] = {}
SHARE_TOKEN_TTL = 600  # 10 minutes


def _cleanup_expired_tokens() -> None:
    now = time.time()
    expired = [t for t, d in _share_tokens.items() if now - d["created_at"] > SHARE_TOKEN_TTL]
    for t in expired:
        del _share_tokens[t]


def _create_share_link_for_doc(request: Request, doc_id: str, s: Session) -> tuple[str, str, int]:
    """Create a temporary share link for a document; return (share_url, token, expires_in)."""
    if not agent or not agent.db._conn:
        raise HTTPException(500, "Database not available")
    doc = agent.db.get_document(doc_id, s.keys.db_key)
    if not doc:
        raise HTTPException(404, "Document not found")
    if not doc.get("file_ref"):
        raise HTTPException(400, "Document has no file attached")
    file_data, original_name = agent.file_vault.retrieve(doc["file_ref"], s.keys.file_key)
    _cleanup_expired_tokens()
    token = secrets.token_urlsafe(32)
    _share_tokens[token] = {
        "file_data": file_data,
        "file_name": original_name,
        "doc_name": doc["name"],
        "created_at": time.time(),
    }
    base_url = str(request.base_url).rstrip("/")
    share_url = f"{base_url}/api/share/{token}"
    return share_url, token, SHARE_TOKEN_TTL


@app.post("/api/share/create")
async def api_share_create(request: Request):
    """Create a temporary share link for a document."""
    s = _require_session(request)
    body = await request.json()
    doc_id = body.get("doc_id", "")
    if not doc_id:
        raise HTTPException(400, "doc_id required")
    share_url, token, expires_in = _create_share_link_for_doc(request, doc_id, s)
    return {"share_url": share_url, "token": token, "expires_in": expires_in}


@app.get("/api/share/{token}")
async def api_share_download(token: str):
    """Download a shared document via temporary token."""
    _cleanup_expired_tokens()
    entry = _share_tokens.get(token)
    if not entry:
        raise HTTPException(404, "Share link expired or invalid")

    return Response(
        content=entry["file_data"],
        media_type="application/octet-stream",
        headers={"Content-Disposition": f'attachment; filename="{entry["file_name"]}"'},
    )


@app.post("/api/share/email")
async def api_share_email(request: Request):
    """Send a document as email attachment via SMTP."""
    s = _require_session(request)
    if not agent or not agent.db._conn:
        raise HTTPException(500, "Database not available")

    cfg = agent.config
    if not cfg.smtp_host or not cfg.smtp_user:
        raise HTTPException(400, "SMTP not configured. Set SMTP_HOST, SMTP_USER, SMTP_PASSWORD, SMTP_FROM environment variables.")

    body = await request.json()
    doc_id = body.get("doc_id", "")
    to_email = body.get("to_email", "")
    if not doc_id or not to_email:
        raise HTTPException(400, "doc_id and to_email are required")

    doc = agent.db.get_document(doc_id, s.keys.db_key)
    if not doc:
        raise HTTPException(404, "Document not found")
    if not doc.get("file_ref"):
        raise HTTPException(400, "Document has no file attached")

    file_data, original_name = agent.file_vault.retrieve(doc["file_ref"], s.keys.file_key)

    import smtplib
    from email.mime.application import MIMEApplication
    from email.mime.multipart import MIMEMultipart
    from email.mime.text import MIMEText

    msg = MIMEMultipart()
    msg["Subject"] = f"Shared from Vault: {doc['name']}"
    msg["From"] = cfg.smtp_from or cfg.smtp_user
    msg["To"] = to_email

    msg.attach(MIMEText(f"Document \"{doc['name']}\" has been shared with you from Vault.", "plain"))

    attachment = MIMEApplication(file_data, Name=original_name)
    attachment["Content-Disposition"] = f'attachment; filename="{original_name}"'
    msg.attach(attachment)

    try:
        with smtplib.SMTP(cfg.smtp_host, cfg.smtp_port) as smtp:
            smtp.ehlo()
            smtp.starttls()
            smtp.login(cfg.smtp_user, cfg.smtp_password)
            smtp.sendmail(msg["From"], [to_email], msg.as_string())
        return {"status": "sent", "to": to_email}
    except Exception as e:
        logger.error("SMTP error: %s", e)
        raise HTTPException(500, f"Failed to send email: {str(e)}")


@app.delete("/api/documents/{doc_id}")
async def api_delete_document(request: Request, doc_id: str):
    s = _require_session(request)
    if not agent or not agent.db._conn:
        raise HTTPException(500, "Database not available")

    doc = agent.db.get_document(doc_id, s.keys.db_key)
    if not doc:
        raise HTTPException(404, "Document not found")

    if doc.get("file_ref"):
        agent.file_vault.delete(doc["file_ref"])
    agent.vector_store.delete_document(doc_id)
    agent.db.delete_document(doc_id)

    return {"status": "ok", "message": f"Deleted document: {doc['name']}"}


@app.delete("/api/credentials/{cred_id}")
async def api_delete_credential(request: Request, cred_id: str):
    s = _require_session(request)
    if not agent or not agent.db._conn:
        raise HTTPException(500, "Database not available")

    creds = agent.db.list_credentials(s.keys.cred_key)
    cred = next((c for c in creds if c["id"] == cred_id), None)
    if not cred:
        raise HTTPException(404, "Credential not found")

    agent.db.delete_credential(cred_id)
    return {"status": "ok", "message": f"Deleted credential: {cred['service']}"}


@app.delete("/api/facts/{fact_id}")
async def api_delete_fact(request: Request, fact_id: str):
    s = _require_session(request)
    if not agent or not agent.db._conn:
        raise HTTPException(500, "Database not available")

    facts = agent.db.list_facts(s.keys.db_key)
    fact = next((f for f in facts if f["id"] == fact_id), None)
    if not fact:
        raise HTTPException(404, "Fact not found")

    agent.db.delete_fact(fact_id)
    return {"status": "ok", "message": f"Deleted fact: {fact['key']}"}


@app.get("/api/birthdays")
async def api_birthdays(request: Request):
    s = _require_session(request)
    if not agent or not agent.db._conn:
        raise HTTPException(500, "Database not available")

    from datetime import datetime

    bdays = agent.memory.list_all(s.keys.db_key, category="birthday")
    today = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
    result = []

    for b in bdays:
        parsed = agent._parse_birthday_date(b["value"])
        days_until = None
        if parsed:
            this_year = parsed.replace(year=today.year)
            if this_year < today:
                this_year = this_year.replace(year=today.year + 1)
            days_until = (this_year - today).days
        result.append({
            "name": b["key"].title(),
            "date": b["value"],
            "days_until": days_until,
        })

    result.sort(key=lambda x: (x["days_until"] if x["days_until"] is not None else 9999))
    return {"birthdays": result}
