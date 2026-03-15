"""
FastAPI application — serves the web UI and API endpoints.

Uses per-client cookie-based sessions for security isolation.
"""

from __future__ import annotations

import base64
import json
import logging
import os
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


@app.post("/api/chat")
async def api_chat(
    request: Request,
    message: str = Form(""),
    file: Optional[UploadFile] = File(None),
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
        response = await agent.process(message, file_data=file_data, file_name=file_name)
        result: dict[str, Any] = {"text": response.text}
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

    return {
        "documents": doc_count,
        "credentials": cred_count,
        "facts": fact_count,
        "active_sessions": session_store.active_count(),
        "total_birthdays": birthday_count,
        "upcoming_birthdays": upcoming,
    }


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
