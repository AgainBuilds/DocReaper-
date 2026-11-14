
"""Standalone DocReaper collector for scheduled GitHub Actions runs."""

from __future__ import annotations

import asyncio
import logging
import os
import signal
from datetime import datetime, timezone

import httpx

from docreaper.cloud import CloudStore
from docreaper.config import settings
from docreaper.pipeline import Pipeline
from docreaper.storage import Store


log = logging.getLogger("docreaper")

RUN_TIMEOUT_SECONDS = 50 * 60
HEARTBEAT_INTERVAL_SECONDS = 20


class DatabaseLogHandler(logging.Handler):
    """Persist crawler logs in the database."""

    def __init__(self, store: Store, run_id: int):
        super().__init__()
        self.store = store
        self.run_id = run_id

    def emit(self, record: logging.LogRecord) -> None:
        try:
            message = self.format(record)

            self.store.add_log(
                message=message,
                level=record.levelname,
                run_id=self.run_id,
            )
        except Exception:
            # Never allow a logging failure to crash collection.
            self.handleError(record)


def configure_logging(store: Store, run_id: int):
    logger = logging.getLogger("docreaper")
    logger.setLevel(logging.INFO)
    logger.propagate = False

    # Prevent duplicate handlers if the worker is reused.
    for handler in list(logger.handlers):
        logger.removeHandler(handler)
        handler.close()

    formatter = logging.Formatter(
        "%(asctime)s | %(levelname)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    console_handler = logging.StreamHandler()
    console_handler.setFormatter(formatter)

    database_handler = DatabaseLogHandler(store, run_id)
    database_handler.setFormatter(formatter)

    logger.addHandler(console_handler)
    logger.addHandler(database_handler)

    return logger


def validate_environment() -> None:
    """Fail early if durable database or cloud storage is missing."""

    if not settings.database_url.startswith(
        ("postgresql://", "postgres://")
    ):
        raise RuntimeError(
            "DOCREAPER_DATABASE_URL must point to PostgreSQL. "
            "Do not run the production collector with temporary SQLite."
        )

    drive_configured = bool(
        settings.google_client_id
        and settings.google_drive_refresh_token
    )

    gcs_configured = bool(settings.gcs_bucket)

    if not drive_configured and not gcs_configured:
        raise RuntimeError(
            "No persistent cloud storage is configured. "
            "Set Google Drive credentials or DOCREAPER_GCS_BUCKET "
            "before starting the collector."
        )

    if (
        settings.google_drive_refresh_token
        and not settings.google_client_id
    ):
        raise RuntimeError(
            "Google Drive refresh token exists, but "
            "DOCREAPER_GOOGLE_CLIENT_ID is missing."
        )


async def heartbeat_loop(
    store: Store,
    stop_event: asyncio.Event,
) -> None:
    """Keep the persistent worker heartbeat fresh."""

    while not stop_event.is_set():
        try:
            store.update_worker_state(
                heartbeat_at=datetime.now(
                    timezone.utc
                ).isoformat()
            )
        except Exception:
            log.exception("HEARTBEAT | Database update failed")

        try:
            await asyncio.wait_for(
                stop_event.wait(),
                timeout=HEARTBEAT_INTERVAL_SECONDS,
            )
        except asyncio.TimeoutError:
            pass


async def run_worker() -> int:
    validate_environment()

    store = Store(settings.database_url)
    run_id = None
    heartbeat_stop = asyncio.Event()
    heartbeat_task = None
    client = None
    pipeline = None

    initial_book_count = 0
    summary = {}
    failure = ""

    try:
        run_id = store.start_run()
        logger = configure_logging(store, run_id)

        initial_book_count = store.count_books()

        # Reset stale control flags when a fresh scheduled run starts.
        store.update_worker_state(
            status="running",
            pause_requested=False,
            stop_requested=False,
            heartbeat_at=datetime.now(
                timezone.utc
            ).isoformat(),
            last_started_at=datetime.now(
                timezone.utc
            ).isoformat(),
            last_error="",
            summary={
                "run_id": run_id,
                "starting_books": initial_book_count,
            },
        )

        logger.info("=" * 65)
        logger.info("DOCREAPER WORKER STARTED")
        logger.info("Run ID: %s", run_id)
        logger.info("Database: PostgreSQL")
        logger.info("Starting catalogue: %s books", initial_book_count)
        logger.info("Target: %s unique books", settings.target_unique_books)
        logger.info("Cloud storage: configured")
        logger.info("=" * 65)

        client = httpx.AsyncClient(
            follow_redirects=True,
            timeout=httpx.Timeout(settings.request_timeout),
            headers={
                "User-Agent": settings.user_agent,
            },
        )

        pipeline = Pipeline(
            store,
            client=client,
        )

        heartbeat_task = asyncio.create_task(
            heartbeat_loop(store, heartbeat_stop)
        )

        logger.info("ENGINE | Starting collection pipeline")

        summary = await asyncio.wait_for(
            pipeline.run(),
            timeout=RUN_TIMEOUT_SECONDS,
        )

        final_books = store.count_books()
        books_added = max(0, final_books - initial_book_count)

        state = store.get_worker_state()

        if state.get("stop_requested"):
            final_status = "stopped"
        elif final_books >= settings.target_unique_books:
            final_status = "completed"
        else:
            final_status = "completed"

        summary.update({
            "run_id": run_id,
            "starting_books": initial_book_count,
            "total_books": final_books,
            "books_added": books_added,
        })

        logger.info("=" * 65)
        logger.info("WORKER FINISHED")
        logger.info("Status: %s", final_status)
        logger.info("Books added this run: %s", books_added)
        logger.info("Total books: %s", final_books)
        logger.info("Candidates processed: %s", summary.get("processed", 0))
        logger.info("Summary: %s", summary)
        logger.info("=" * 65)

        store.finish_run(
            run_id,
            status=final_status,
            books_added=books_added,
            candidates_processed=summary.get("processed", 0),
            summary=summary,
        )

        store.update_worker_state(
            status=final_status,
            heartbeat_at=datetime.now(
                timezone.utc
            ).isoformat(),
            last_finished_at=datetime.now(
                timezone.utc
            ).isoformat(),
            summary=summary,
        )

        return 0

    except asyncio.TimeoutError:
        failure = (
            f"Collection exceeded the "
            f"{RUN_TIMEOUT_SECONDS // 60}-minute run limit."
        )

        log.exception("WORKER | Time limit reached")
        return_code = 1

    except Exception as exc:
        failure = str(exc)[:2000]

        log.exception("WORKER | Fatal collection error")
        return_code = 1

    finally:
        heartbeat_stop.set()

        if heartbeat_task:
            await asyncio.gather(
                heartbeat_task,
                return_exceptions=True,
            )

        if client:
            await client.aclose()

        if run_id is not None:
            try:
                final_books = store.count_books()
                books_added = max(
                    0,
                    final_books - initial_book_count,
                )

                if failure:
                    store.finish_run(
                        run_id,
                        status="failed",
                        books_added=books_added,
                        candidates_processed=summary.get(
                            "processed", 0
                        ),
                        error=failure,
                        summary=summary,
                    )

                    store.update_worker_state(
                        status="failed",
                        last_error=failure,
                        last_finished_at=datetime.now(
                            timezone.utc
                        ).isoformat(),
                        heartbeat_at=datetime.now(
                            timezone.utc
                        ).isoformat(),
                    )

            except Exception:
                logging.exception(
                    "WORKER | Could not finalize run record"
                )

        store.close()

    return return_code


def main() -> None:
    try:
        exit_code = asyncio.run(run_worker())
    except KeyboardInterrupt:
        logging.warning("WORKER | Interrupted")
        exit_code = 130

    raise SystemExit(exit_code)


if __name__ == "__main__":
    main()
