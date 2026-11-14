
"""DocReaper dashboard: persistent Supabase status, logs and controls."""

from __future__ import annotations

import html
import secrets
from pathlib import Path

import httpx
from fastapi import FastAPI, Header, HTTPException, Query, Request
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

from docreaper.config import settings
from docreaper.google_drive import (
    authorization_url,
    create_state,
    exchange_code,
    verify_state,
)
from docreaper.storage import Store


BASE_DIR = Path(__file__).resolve().parent

app = FastAPI(
    title="DocReaper Control Center",
    version="3.0.0",
)

app.mount(
    "/assets",
    StaticFiles(directory=BASE_DIR),
    name="assets",
)


# ---------------------------------------------------------
# DATABASE HELPERS
# ---------------------------------------------------------

def get_store() -> Store:
    return Store(settings.database_url)


def require_token(token: str) -> None:
    configured = settings.control_token.strip()

    if not configured:
        raise HTTPException(
            status_code=503,
            detail="Set DOCREAPER_CONTROL_TOKEN in Render environment.",
        )

    if not secrets.compare_digest(token, configured):
        raise HTTPException(
            status_code=401,
            detail="Invalid control token.",
        )


def oauth_configuration_ready() -> bool:
    return all([
        settings.google_client_id,
        settings.google_redirect_uri,
        settings.google_oauth_state_secret,
    ])


# ---------------------------------------------------------
# DASHBOARD AND HEALTH
# ---------------------------------------------------------

@app.get("/", include_in_schema=False)
def dashboard():
    return FileResponse(BASE_DIR / "index.html")


@app.get("/health")
def health():
    store = get_store()

    try:
        stats = store.stats()
        state = store.get_worker_state()

        return {
            "status": "ok",
            "service": "docreaper-dashboard",
            "database": "connected",
            "worker_status": state.get("status", "idle"),
            "books": stats["books"],
        }

    finally:
        store.close()


# ---------------------------------------------------------
# GOOGLE DRIVE OAUTH
# ---------------------------------------------------------

@app.get("/auth/google", include_in_schema=False)
async def google_auth():
    if not oauth_configuration_ready():
        raise HTTPException(
            status_code=503,
            detail="Google OAuth environment variables are incomplete.",
        )

    state = create_state(settings.google_oauth_state_secret)

    response = RedirectResponse(
        authorization_url(
            settings.google_client_id,
            settings.google_redirect_uri,
            state,
        ),
        status_code=302,
    )

    response.set_cookie(
        key="docreaper_oauth_state",
        value=state,
        httponly=True,
        secure=True,
        samesite="lax",
        max_age=600,
    )

    return response


@app.get("/auth/google/callback", include_in_schema=False)
async def google_callback(
    request: Request,
    code: str = "",
    state: str = "",
    error: str = "",
):
    if error:
        raise HTTPException(
            status_code=400,
            detail="Google authorization was not completed.",
        )

    if not code or not state:
        raise HTTPException(
            status_code=400,
            detail="Missing authorization code or state.",
        )

    if not oauth_configuration_ready():
        raise HTTPException(
            status_code=503,
            detail="Google OAuth configuration is incomplete.",
        )

    cookie_state = request.cookies.get("docreaper_oauth_state")

    if not cookie_state or not secrets.compare_digest(
        cookie_state,
        state,
    ):
        raise HTTPException(
            status_code=400,
            detail="OAuth browser session mismatch. Start again.",
        )

    if not verify_state(
        state,
        settings.google_oauth_state_secret,
    ):
        raise HTTPException(
            status_code=400,
            detail="OAuth state is invalid or expired.",
        )

    try:
        tokens = await exchange_code(
            code=code,
            client_id=settings.google_client_id,
            client_secret=settings.google_client_secret,
            redirect_uri=settings.google_redirect_uri,
        )

    except httpx.HTTPStatusError:
        raise HTTPException(
            status_code=502,
            detail="Google token exchange failed. Check Render logs.",
        )

    refresh_token = tokens.get("refresh_token")

    if not refresh_token:
        raise HTTPException(
            status_code=400,
            detail=(
                "Google did not return a refresh token. "
                "Check OAuth consent configuration and authorize again."
            ),
        )

    safe_token = html.escape(refresh_token)

    page = f"""
    <!doctype html>
    <html>
    <head>
      <meta name="viewport" content="width=device-width, initial-scale=1">
      <title>DocReaper Drive Connected</title>
      <style>
        body {{
          font-family: Arial, sans-serif;
          max-width: 760px;
          margin: 40px auto;
          padding: 20px;
          line-height: 1.6;
          background: #f7f8fa;
          color: #172033;
        }}
        .panel {{
          background: white;
          padding: 24px;
          border-radius: 12px;
        }}
        textarea {{
          width: 100%;
          min-height: 110px;
          box-sizing: border-box;
          padding: 12px;
          overflow-wrap: anywhere;
        }}
        button {{
          padding: 12px 18px;
          margin-top: 12px;
          cursor: pointer;
        }}
      </style>
    </head>
    <body>
      <div class="panel">
        <h2>Google authorization successful</h2>
        <p>Copy this refresh token into your Render environment:</p>
        <strong>DOCREAPER_GOOGLE_DRIVE_REFRESH_TOKEN</strong>
        <textarea id="token" readonly>{safe_token}</textarea>
        <button onclick="
          navigator.clipboard.writeText(
            document.getElementById('token').value
          )
        ">Copy refresh token</button>
        <p>Keep this token private. Never commit it to GitHub.</p>
        <p>After adding it to Render, redeploy DocReaper.</p>
      </div>
    </body>
    </html>
    """

    response = HTMLResponse(page)
    response.delete_cookie("docreaper_oauth_state")
    return response


# ---------------------------------------------------------
# OVERVIEW AND CATALOGUE
# ---------------------------------------------------------

@app.get("/api/overview")
def overview():
    store = get_store()

    try:
        stats = store.stats()
        target = settings.target_unique_books
        total = stats["books"]

        return {
            "target": target,
            "books": total,
            "progress_percent": (
                round(min(total / target * 100, 100), 1)
                if target else 0
            ),
            "pending": stats["discovery_pending"],
            "kept": stats["discovery_kept"],
            "duplicates": stats["discovery_duplicate"],
            "failed": stats["discovery_failed"],
            "skipped": stats["discovery_skipped"],
            "by_subject": stats["by_subject"],
        }

    finally:
        store.close()


@app.get("/api/books")
def books(
    limit: int = Query(default=10, ge=1, le=50),
):
    store = get_store()

    try:
        return {"books": store.recent_books(limit)}
    finally:
        store.close()


# ---------------------------------------------------------
# PERSISTENT WORKER STATUS
# ---------------------------------------------------------

@app.get("/api/control/status")
def control_status():
    store = get_store()

    try:
        state = store.get_worker_state()
        stats = store.stats()

        return {
            "status": state.get("status", "idle"),
            "started_at": state.get("last_started_at"),
            "finished_at": state.get("last_finished_at"),
            "heartbeat_at": state.get("heartbeat_at"),
            "last_error": state.get("last_error", ""),
            "summary": state.get("summary", {}),
            "pause_requested": state.get("pause_requested", False),
            "stop_requested": state.get("stop_requested", False),
            "books": stats["books"],
            "pending": stats["discovery_pending"],
            "target": settings.target_unique_books,
        }

    finally:
        store.close()


# ---------------------------------------------------------
# PERSISTENT ENGINE LOGS
# ---------------------------------------------------------

@app.get("/api/logs")
def get_logs(
    after: int = Query(default=0, ge=0),
    limit: int = Query(default=250, ge=1, le=500),
):
    store = get_store()

    try:
        entries = store.recent_logs(
            after=after,
            limit=limit,
        )

        latest_id = entries[-1]["id"] if entries else after

        return {
            "logs": entries,
            "latest_id": latest_id,
        }

    finally:
        store.close()


# ---------------------------------------------------------
# COLLECTION CONTROLS
# ---------------------------------------------------------

@app.post("/api/control/start")
async def start_collection(
    x_control_token: str = Header(default=""),
):
    require_token(x_control_token)

    raise HTTPException(
        status_code=409,
        detail=(
            "The collector runs through GitHub Actions. "
            "Open GitHub → Actions → DocReaper Collector → Run workflow. "
            "Dashboard-triggered runs will be added after GitHub "
            "workflow authentication is configured."
        ),
    )


@app.post("/api/control/pause")
async def pause_collection(
    x_control_token: str = Header(default=""),
):
    require_token(x_control_token)

    store = get_store()

    try:
        state = store.get_worker_state()

        if state.get("status") != "running":
            raise HTTPException(
                status_code=409,
                detail="No running collector is currently registered.",
            )

        store.update_worker_state(
            pause_requested=True,
            status="paused",
        )

        store.add_log(
            "CONTROL | Pause requested from dashboard",
            level="WARNING",
        )

        return {"status": "paused"}

    finally:
        store.close()


@app.post("/api/control/resume")
async def resume_collection(
    x_control_token: str = Header(default=""),
):
    require_token(x_control_token)

    store = get_store()

    try:
        state = store.get_worker_state()

        if not state.get("pause_requested"):
            raise HTTPException(
                status_code=409,
                detail="Collector is not paused.",
            )

        store.update_worker_state(
            pause_requested=False,
            status="running",
        )

        store.add_log(
            "CONTROL | Resume requested from dashboard",
            level="INFO",
        )

        return {"status": "running"}

    finally:
        store.close()


@app.post("/api/control/stop")
async def stop_collection(
    x_control_token: str = Header(default=""),
):
    require_token(x_control_token)

    store = get_store()

    try:
        state = store.get_worker_state()

        if state.get("status") not in ("running", "paused"):
            raise HTTPException(
                status_code=409,
                detail="No active collector is registered.",
            )

        store.update_worker_state(
            stop_requested=True,
            pause_requested=False,
            status="stopping",
        )

        store.add_log(
            "CONTROL | Stop requested from dashboard",
            level="WARNING",
        )

        return {"status": "stopping"}

    finally:
        store.close()
