"""
FastAPI application — serves the web UI and API endpoints.
"""

from __future__ import annotations

import base64
import logging
import os
import time
from collections import defaultdict
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from vault.agent import VaultAgent
from vault.config import VaultConfig, config
from vault.security.encryption import derive_all_keys, generate_verification_token
from vault.security.session import session

logger = logging.getLogger(__name__)

agent: Optional[VaultAgent] = None

_unlock_attempts: dict[str, list[float]] = defaultdict(list)
UNLOCK_RATE_LIMIT = 5
UNLOCK_RATE_WINDOW = 60


def _check_rate_limit(client_ip: str) -> bool:
    """Returns True if the request should be blocked."""
    now = time.monotonic()
    attempts = _unlock_attempts[client_ip]
    _unlock_attempts[client_ip] = [t for t in attempts if now - t < UNLOCK_RATE_WINDOW]
    if len(_unlock_attempts[client_ip]) >= UNLOCK_RATE_LIMIT:
        return True
    _unlock_attempts[client_ip].append(now)
    return False


@asynccontextmanager
async def lifespan(application: FastAPI):
    global agent
    if config.salt_path.exists():
        salt = config.salt_path.read_bytes()
        token = config.token_path.read_bytes()
        session.configure(salt, token, config.session_timeout)

    agent = VaultAgent(config, session)
    agent.initialize()
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
    if request.url.scheme == "https":
        response.headers["Strict-Transport-Security"] = "max-age=63072000; includeSubDomains"
    return response

WEB_DIR = Path(__file__).parent / "web"
app.mount("/static", StaticFiles(directory=str(WEB_DIR / "static")), name="static")
templates = Jinja2Templates(directory=str(WEB_DIR / "templates"))


@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    initialized = config.salt_path.exists()
    if not initialized:
        return templates.TemplateResponse("setup.html", {"request": request})
    if session.is_locked:
        return templates.TemplateResponse("unlock.html", {"request": request})
    return templates.TemplateResponse("index.html", {"request": request})


@app.post("/api/init")
async def api_init(request: Request):
    data = await request.json()
    password = data.get("password", "")
    if len(password) < 8:
        raise HTTPException(400, "Password must be at least 8 characters")

    keys = derive_all_keys(password)
    token = generate_verification_token(password, keys.salt)

    config.ensure_dirs()
    config.salt_path.write_bytes(keys.salt)
    config.token_path.write_bytes(token)
    config.save()

    session.configure(keys.salt, token, config.session_timeout)
    session.unlock(password)

    global agent
    if agent:
        agent.shutdown()
    agent = VaultAgent(config, session)
    agent.initialize()

    return {"status": "ok", "message": "Vault initialized successfully"}


@app.post("/api/unlock")
async def api_unlock(request: Request):
    client_ip = request.client.host if request.client else "unknown"
    if _check_rate_limit(client_ip):
        raise HTTPException(429, "Too many unlock attempts. Try again in a minute.")

    data = await request.json()
    password = data.get("password", "")

    if not config.salt_path.exists():
        raise HTTPException(400, "Vault not initialized")

    salt = config.salt_path.read_bytes()
    token = config.token_path.read_bytes()
    session.configure(salt, token, config.session_timeout)

    if session.unlock(password):
        _unlock_attempts.pop(client_ip, None)
        return {"status": "ok"}
    raise HTTPException(401, "Incorrect password")


@app.post("/api/lock")
async def api_lock():
    session.lock()
    return {"status": "ok"}


@app.get("/api/status")
async def api_status():
    return {
        "initialized": config.salt_path.exists(),
        "locked": session.is_locked,
        "paranoid_mode": config.paranoid_mode,
    }


@app.post("/api/chat")
async def api_chat(
    message: str = Form(""),
    file: Optional[UploadFile] = File(None),
):
    if not agent:
        raise HTTPException(500, "Agent not initialized")
    if session.is_locked:
        raise HTTPException(401, "Vault is locked")

    file_data = None
    file_name = None
    if file and file.filename:
        file_data = await file.read()
        file_name = file.filename

    try:
        response = await agent.process(message, file_data=file_data, file_name=file_name)
        result = {"text": response.text}
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


@app.post("/api/change-password")
async def api_change_password(request: Request):
    if session.is_locked:
        raise HTTPException(401, "Vault is locked")
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

    old_keys = session.keys

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
    session.configure(new_keys.salt, new_token, config.session_timeout)
    session.unlock(new_pw)

    return {"status": "ok", "message": "Password changed successfully. All data re-encrypted."}


@app.post("/api/backup")
async def api_backup():
    if session.is_locked:
        raise HTTPException(401, "Vault is locked")
    try:
        from vault.backup import create_backup
        backup_path = create_backup(config)
        return {"status": "ok", "path": str(backup_path)}
    except Exception as e:
        raise HTTPException(500, str(e))
