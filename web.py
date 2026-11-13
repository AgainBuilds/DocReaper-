
"""DocReaper web dashboard and read-only catalogue API."""

from pathlib import Path

from fastapi import FastAPI, Query
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from docreaper.config import settings
from docreaper.storage import Store

BASE_DIR = Path(__file__).resolve().parent

app = FastAPI(
    title="DocReaper Control Center",
    version="1.0.0",
    description="Dashboard for the DocReaper academic book collection.",
)

app.mount(
    "/assets",
    StaticFiles(directory=BASE_DIR),
    name="assets",
)


@app.get("/", include_in_schema=False)
def dashboard():
    return FileResponse(BASE_DIR / "index.html")


@app.get("/health")
def health():
    return {
        "status": "ok",
        "service": "docreaper-dashboard",
    }


@app.get("/api/overview")
def overview():
    store = Store(settings.database_url)

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
            "by_subject": stats["by_subject"],
        }
    finally:
        store.close()


@app.get("/api/books")
def books(limit: int = Query(default=10, ge=1, le=50)):
    store = Store(settings.database_url)

    try:
        return {"books": store.recent_books(limit)}
    finally:
        store.close()
