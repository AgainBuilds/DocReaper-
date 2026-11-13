
"""DocReaper dashboard and live pipeline control."""
from __future__ import annotations

import asyncio
import logging
import secrets
from collections import deque
from datetime import datetime, timezone
from pathlib import Path

import httpx
from fastapi import FastAPI, Header, HTTPException, Query
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from docreaper.config import settings
from docreaper.pipeline import Pipeline
from docreaper.storage import Store

BASE_DIR = Path(__file__).resolve().parent

app = FastAPI(
    title="DocReaper Control Center",
    version="2.1.0",
)

app.mount(
    "/assets",
    StaticFiles(directory=BASE_DIR),
    name="assets",
)

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


class LiveLogHandler(logging.Handler):
    def emit(self, record):
        global log_id

        try:
            log_id += 1

            logs.append({
                "id": log_id,
                "time": datetime.fromtimestamp(
                    record.created,
                    timezone.utc
                ).astimezone().isoformat(
                    timespec="seconds"
                ),
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
    handler = LiveLogHandler()
    handler.setFormatter(
        logging.Formatter("%(message)s")
    )
    engine_logger.addHandler(handler)


def require_token(token):
    configured = settings.control_token.strip()

    if not configured:
        raise HTTPException(
            status_code=503,
            detail="Set DOCREAPER_CONTROL_TOKEN in Render first."
        )

    if not secrets.compare_digest(token, configured):
        raise HTTPException(
            status_code=401,
            detail="Invalid control token."
        )


def get_stats():
    store = Store(settings.database_url)

    try:
        return store.stats()
    finally:
        store.close()


async def collection_worker():
    store = None
    client = None

    try:
        engine_logger.info("WORKER | Initializing collection engine")

        store = Store(settings.database_url)

        client = httpx.AsyncClient(
            follow_redirects=True,
            headers={"User-Agent": settings.user_agent},
        )

        pipeline = Pipeline(
            store,
            client=client,
            pause_event=pause_event,
            stop_event=stop_event,
        )

        engine_logger.info(
            "TARGET | %d unique books",
            settings.target_unique_books
        )

        summary = await pipeline.run()

        control["summary"] = summary

        if stop_event.is_set():
            control["status"] = "stopped"
            engine_logger.warning("WORKER | Safely stopped")
        else:
            control["status"] = "completed"
            engine_logger.info("WORKER | Collection completed")

        engine_logger.info("SUMMARY | %s", summary)

    except Exception:
        control["status"] = "failed"
        engine_logger.exception("WORKER | Fatal failure")

    finally:
        if client:
            await client.aclose()

        if store:
            store.close()

        control["finished_at"] = (
            datetime.now(timezone.utc).isoformat()
        )


@app.get("/", include_in_schema=False)
def dashboard():
    return FileResponse(BASE_DIR / "index.html")


@app.get("/health")
def health():
    return {
        "status": "ok",
        "service": "docreaper-dashboard"
    }


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
def books(limit: int = Query(default=10, ge=1, le=50)):
    store = Store(settings.database_url)

    try:
        return {"books": store.recent_books(limit)}
    finally:
        store.close()


@app.get("/api/control/status")
def control_status():
    stats = get_stats()

    return {
        **control,
        "books": stats["books"],
        "pending": stats["discovery_pending"],
        "target": settings.target_unique_books,
    }


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


@app.post("/api/control/start")
async def start_collection(
    x_control_token: str = Header(default="")
):
    global worker

    require_token(x_control_token)

    if worker and not worker.done():
        raise HTTPException(
            status_code=409,
            detail="Collection already active."
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

    engine_logger.info("CONTROL | START command received")

    worker = asyncio.create_task(
        collection_worker()
    )

    return {"status": "running"}


@app.post("/api/control/pause")
async def pause_collection(
    x_control_token: str = Header(default="")
):
    require_token(x_control_token)

    if control["status"] != "running":
        raise HTTPException(
            status_code=409,
            detail="Engine is not running."
        )

    pause_event.clear()
    control["status"] = "paused"

    engine_logger.warning("CONTROL | PAUSE requested")

    return {"status": "paused"}


@app.post("/api/control/resume")
async def resume_collection(
    x_control_token: str = Header(default="")
):
    require_token(x_control_token)

    if control["status"] != "paused":
        raise HTTPException(
            status_code=409,
            detail="Engine is not paused."
        )

    pause_event.set()
    control["status"] = "running"

    engine_logger.info("CONTROL | RESUME requested")

    return {"status": "running"}


@app.post("/api/control/stop")
async def stop_collection(
    x_control_token: str = Header(default="")
):
    require_token(x_control_token)

    if control["status"] not in ("running", "paused"):
        raise HTTPException(
            status_code=409,
            detail="No active collection."
        )

    stop_event.set()
    pause_event.set()
    control["status"] = "stopping"

    engine_logger.warning(
        "CONTROL | STOP requested; finishing current operation"
    )

    return {"status": "stopping"}
