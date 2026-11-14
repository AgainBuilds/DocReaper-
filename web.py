
"""DocReaper dashboard, pipeline control and Google OAuth."""
from __future__ import annotations

import asyncio
import html
import logging
import secrets

from collections import deque
from datetime import datetime, timezone
from pathlib import Path

import httpx

from fastapi import (
    FastAPI,
    Header,
    HTTPException,
    Query,
    Request,
)

from fastapi.responses import (
    FileResponse,
    HTMLResponse,
    RedirectResponse,
)

from fastapi.staticfiles import StaticFiles

from docreaper.config import settings

from docreaper.google_drive import (
    authorization_url,
    create_state,
    exchange_code,
    verify_state,
)

from docreaper.pipeline import Pipeline
from docreaper.storage import Store


BASE_DIR = Path(__file__).resolve().parent


app = FastAPI(
    title="DocReaper Control Center",
    version="2.3.0",
)


app.mount(
    "/assets",
    StaticFiles(directory=BASE_DIR),
    name="assets",
)


# ---------------------------------------------------------
# GLOBAL STATE
# ---------------------------------------------------------

LOG_LIMIT = 1500

logs = deque(maxlen=LOG_LIMIT)

log_id = 0

worker = None

pause_event = asyncio.Event()
pause_event.set()

stop_event = asyncio.Event()


control = {
    "status": "idle",
    "started_at": None,
    "finished_at": None,
    "summary": None,
}


# ---------------------------------------------------------
# LOGGING
# ---------------------------------------------------------

class LiveLogHandler(logging.Handler):

    def emit(self, record):
        global log_id

        try:
            log_id += 1

            logs.append({
                "id": log_id,
                "time": datetime.fromtimestamp(
                    record.created,
                    timezone.utc,
                ).astimezone().isoformat(timespec="seconds"),
                "level": record.levelname,
                "message": self.format(record),
            })

        except Exception:
            self.handleError(record)


engine_logger = logging.getLogger("docreaper")

engine_logger.setLevel(logging.INFO)

if not any(
    isinstance(handler, LiveLogHandler)
    for handler in engine_logger.handlers
):
    live_handler = LiveLogHandler()

    live_handler.setFormatter(
        logging.Formatter("%(message)s")
    )

    engine_logger.addHandler(live_handler)


# ---------------------------------------------------------
# HELPERS
# ---------------------------------------------------------

def require_token(token: str):
    configured = settings.control_token.strip()

    if not configured:
        raise HTTPException(
            status_code=503,
            detail=(
                "Set DOCREAPER_CONTROL_TOKEN "
                "in the Render environment."
            ),
        )

    if not secrets.compare_digest(token, configured):
        raise HTTPException(
            status_code=401,
            detail="Invalid control token.",
        )


def get_stats():

    store = Store(settings.database_url)

    try:
        return store.stats()

    finally:
        store.close()


def oauth_configuration_ready():

    return all([
        settings.google_client_id,
        settings.google_redirect_uri,
        settings.google_oauth_state_secret,
    ])


# ---------------------------------------------------------
# DASHBOARD
# ---------------------------------------------------------

@app.get("/", include_in_schema=False)
def dashboard():

    return FileResponse(
        BASE_DIR / "index.html"
    )


@app.get("/health")
def health():

    return {
        "status": "ok",
        "service": "docreaper-dashboard",
    }


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

    state = create_state(
        settings.google_oauth_state_secret
    )

    redirect = RedirectResponse(
        authorization_url(
            settings.google_client_id,
            settings.google_redirect_uri,
            state,
        ),
        status_code=302,
    )

    redirect.set_cookie(
        key="docreaper_oauth_state",
        value=state,
        httponly=True,
        secure=True,
        samesite="lax",
        max_age=600,
    )

    return redirect


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

    cookie_state = request.cookies.get(
        "docreaper_oauth_state"
    )

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

        engine_logger.exception(
            "GOOGLE OAUTH | Token exchange failed"
        )

        raise HTTPException(
            status_code=502,
            detail=(
                "Google token exchange failed. "
                "Check the Render logs."
            ),
        )

    refresh_token = tokens.get("refresh_token")

    if not refresh_token:

        raise HTTPException(
            status_code=400,
            detail=(
                "Google did not return a refresh token. "
                "Check the OAuth consent configuration "
                "and authorize DocReaper again."
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

        <p>
          Copy this refresh token into your Render environment variable:
        </p>

        <strong>DOCREAPER_GOOGLE_DRIVE_REFRESH_TOKEN</strong>

        <textarea id="token" readonly>{safe_token}</textarea>

        <button onclick="
          navigator.clipboard.writeText(
            document.getElementById('token').value
          )
        ">
          Copy refresh token
        </button>

        <p>
          Keep this token private.
          Never commit it to GitHub.
        </p>

        <p>
          After adding it to Render, redeploy DocReaper.
        </p>

      </div>
    </body>
    </html>
    """

    response = HTMLResponse(page)

    response.delete_cookie(
        "docreaper_oauth_state"
    )

    return response


# ---------------------------------------------------------
# STATISTICS AND CATALOGUE
# ---------------------------------------------------------

@app.get("/api/overview")
def overview():

    stats = get_stats()

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
        "by_subject": stats["by_subject"],
    }


@app.get("/api/books")
def books(
    limit: int = Query(
        default=10,
        ge=1,
        le=50,
    )
):

    store = Store(settings.database_url)

    try:

        return {
            "books": store.recent_books(limit)
        }

    finally:
        store.close()


# ---------------------------------------------------------
# PIPELINE STATUS
# ---------------------------------------------------------

@app.get("/api/control/status")
def control_status():

    stats = get_stats()

    return {
        **control,
        "books": stats["books"],
        "pending": stats["discovery_pending"],
        "target": settings.target_unique_books,
    }


# ---------------------------------------------------------
# LIVE LOGS
# ---------------------------------------------------------

@app.get("/api/logs")
def get_logs(
    after: int = Query(default=0, ge=0),
    limit: int = Query(default=250, ge=1, le=500),
):

    entries = [
        item for item in logs
        if item["id"] > after
    ]

    return {
        "logs": entries[-limit:],
        "latest_id": log_id,
    }


# ---------------------------------------------------------
# COLLECTION WORKER
# ---------------------------------------------------------

async def collection_worker():

    store = None
    client = None

    try:

        engine_logger.info(
            "WORKER | Initializing collection engine"
        )

        store = Store(settings.database_url)

        client = httpx.AsyncClient(
            follow_redirects=True,
            headers={
                "User-Agent": settings.user_agent
            },
        )

        pipeline = Pipeline(
            store,
            client=client,
            pause_event=pause_event,
            stop_event=stop_event,
        )

        engine_logger.info(
            "TARGET | %d unique books",
            settings.target_unique_books,
        )

        summary = await pipeline.run()

        control["summary"] = summary

        if stop_event.is_set():

            control["status"] = "stopped"

            engine_logger.warning(
                "WORKER | Safely stopped"
            )

        else:

            control["status"] = "completed"

            engine_logger.info(
                "WORKER | Collection completed"
            )

        engine_logger.info(
            "SUMMARY | %s",
            summary,
        )

    except Exception:

        control["status"] = "failed"

        engine_logger.exception(
            "WORKER | Fatal failure"
        )

    finally:

        if client:
            await client.aclose()

        if store:
            store.close()

        control["finished_at"] = datetime.now(
            timezone.utc
        ).isoformat()


# ---------------------------------------------------------
# START COLLECTION
# ---------------------------------------------------------

@app.post("/api/control/start")
async def start_collection(
    x_control_token: str = Header(default=""),
):

    global worker

    require_token(x_control_token)

    if worker and not worker.done():

        raise HTTPException(
            status_code=409,
            detail="Collection is already active.",
        )

    pause_event.set()

    stop_event.clear()

    control.update({
        "status": "running",

        "started_at": datetime.now(
            timezone.utc
        ).isoformat(),

        "finished_at": None,
        "summary": None,
    })

    engine_logger.info(
        "CONTROL | START command received"
    )

    worker = asyncio.create_task(
        collection_worker()
    )

    return {
        "status": "running"
    }


# ---------------------------------------------------------
# PAUSE COLLECTION
# ---------------------------------------------------------

@app.post("/api/control/pause")
async def pause_collection(
    x_control_token: str = Header(default=""),
):

    require_token(x_control_token)

    if control["status"] != "running":

        raise HTTPException(
            status_code=409,
            detail="Engine is not running.",
        )

    pause_event.clear()

    control["status"] = "paused"

    engine_logger.warning(
        "CONTROL | PAUSE requested"
    )

    return {
        "status": "paused"
    }


# ---------------------------------------------------------
# RESUME COLLECTION
# ---------------------------------------------------------

@app.post("/api/control/resume")
async def resume_collection(
    x_control_token: str = Header(default=""),
):

    require_token(x_control_token)

    if control["status"] != "paused":

        raise HTTPException(
            status_code=409,
            detail="Engine is not paused.",
        )

    pause_event.set()

    control["status"] = "running"

    engine_logger.info(
        "CONTROL | RESUME requested"
    )

    return {
        "status": "running"
    }


# ---------------------------------------------------------
# STOP COLLECTION
# ---------------------------------------------------------

@app.post("/api/control/stop")
async def stop_collection(
    x_control_token: str = Header(default=""),
):

    require_token(x_control_token)

    if control["status"] not in (
        "running",
        "paused",
    ):

        raise HTTPException(
            status_code=409,
            detail="No active collection.",
        )

    stop_event.set()

    pause_event.set()

    control["status"] = "stopping"

    engine_logger.warning(
        "CONTROL | STOP requested; finishing current operation"
    )

    return {
        "status": "stopping"
    }
