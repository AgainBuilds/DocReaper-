
"""DocReaper collection and processing engine."""

from __future__ import annotations

import asyncio
import logging
from urllib.parse import urlparse

import httpx

from . import extraction
from .cloud import CloudStore
from .config import settings
from .discovery import discover_all
from .downloader import download
from .fingerprints import fingerprint
from .schemas import Book
from .storage import Store

log = logging.getLogger("docreaper")

SOURCE_LICENSES = {
    "gutenberg": "public domain",
    "openlibrary_verified": (
        "Internet Archive metadata: verified public-domain "
        "or Creative Commons license"
    ),
}


class Pipeline:
    def __init__(
        self,
        store,
        cloud=None,
        *,
        client=None,
        target=None,
        threshold=None,
        min_chars=None,
        pause_event=None,
        stop_event=None,
    ):
        self.store = store
        self.cloud = cloud or CloudStore()

        self.client = client or httpx.AsyncClient(
            follow_redirects=True,
            headers={"User-Agent": settings.user_agent},
        )

        self.target = (
            target
            if target is not None
            else settings.target_unique_books
        )

        self.threshold = (
            threshold
            if threshold is not None
            else settings.near_duplicate_threshold
        )

        self.min_chars = (
            min_chars
            if min_chars is not None
            else settings.min_text_chars
        )

        self.pause_event = pause_event or asyncio.Event()
        self.pause_event.set()

        self.stop_event = stop_event or asyncio.Event()

    # ---------------------------------------------------------
    # DURABLE WORKER CONTROL
    # ---------------------------------------------------------

    async def checkpoint(self):
        """
        Check both local asyncio controls and persistent
        database controls before processing another candidate.
        """

        if self.stop_event.is_set():
            return False

        try:
            state = self.store.get_worker_state()

            if state.get("stop_requested"):
                self.stop_event.set()
                log.warning(
                    "CONTROL | Persistent STOP request detected"
                )
                return False

            if state.get("pause_requested"):
                log.warning(
                    "CONTROL | Persistent PAUSE request detected"
                )

                while True:
                    if self.stop_event.is_set():
                        return False

                    await asyncio.sleep(5)

                    state = self.store.get_worker_state()

                    if state.get("stop_requested"):
                        self.stop_event.set()
                        return False

                    if not state.get("pause_requested"):
                        log.info(
                            "CONTROL | Persistent RESUME detected"
                        )
                        break

        except Exception:
            log.exception(
                "CONTROL | Could not read persistent worker state"
            )

        if not self.pause_event.is_set():
            log.warning(
                "CONTROL | Local pause requested"
            )

        await self.pause_event.wait()

        return not self.stop_event.is_set()

    # ---------------------------------------------------------
    # DISCOVERY
    # ---------------------------------------------------------

    async def discover(self, pages=None):
        page_count = (
            pages
            if pages is not None
            else settings.pages_per_subject
        )

        log.info(
            "DISCOVERY | Starting internet discovery"
        )

        records = await discover_all(
            self.client,
            settings.subjects,
            page_count,
        )

        added = 0

        for record in records:
            if self.store.add_discovery(record):
                added += 1

        log.info(
            "DISCOVERY | Candidates=%d | Newly queued=%d",
            len(records),
            added,
        )

        return added

    # ---------------------------------------------------------
    # DOCUMENT INGESTION
    # ---------------------------------------------------------

    def ingest_bytes(
        self,
        data,
        *,
        text,
        fmt,
        title,
        authors="",
        year=None,
        subject="",
        category="",
        description="",
        url="",
        source="",
        license="unknown",
    ):
        log.info(
            "FINGERPRINT | Analysing document: %s",
            title,
        )

        fp = fingerprint(data, text)

        log.info(
            "SHA256 | FILE=%s | TEXT=%s",
            fp.file_sha256,
            fp.text_sha256,
        )

        log.info(
            "TEXT ANALYSIS | Words=%d | Shingles=%d",
            fp.word_count,
            len(fp.shingles),
        )

        # Exact file duplicate.
        if self.store.has_file_sha256(fp.file_sha256):
            log.warning(
                "DUPLICATE | Identical file detected | %s",
                title,
            )

            return {
                "status": "duplicate",
                "reason": "identical file",
                "url": url,
            }

        # Exact extracted-text duplicate.
        if self.store.has_text_sha256(fp.text_sha256):
            log.warning(
                "DUPLICATE | Identical extracted text | %s",
                title,
            )

            return {
                "status": "duplicate",
                "reason": "identical text",
                "url": url,
            }

        # Mathematical similarity comparison.
        near = self.store.near_duplicate(
            fp.shingles,
            fp.word_count,
            self.threshold,
        )

        if near:
            log.warning(
                "NEAR DUPLICATE | %s | Similarity=%.4f",
                title,
                near["score"],
            )

            return {
                "status": "duplicate",
                "reason": "near_duplicate",
                "duplicate_of": near,
                "url": url,
            }

        log.info(
            "DEDUPLICATION | Passed exact and similarity checks"
        )

        filename = (
            urlparse(url).path.rsplit("/", 1)[-1]
            if url
            else f"{fp.file_sha256[:12]}.{fmt}"
        )

        if not filename:
            filename = f"{fp.file_sha256[:12]}.{fmt}"

        log.info(
            "STORAGE | Saving %s | %.2f MB",
            filename,
            len(data) / (1024 * 1024),
        )

        # Cloud storage must complete before catalogue insertion.
        path = self.cloud.save(data, filename)

        book = Book(
            title=title,
            authors=authors,
            year=year,
            subject=subject,
            description=description,
            source_url=url,
            fmt=fmt,
            size_bytes=len(data),
            license=license,
            metadata={
                "category": category,
                "source": source,
            },
        )

        book_id = self.store.add_book(
            book,
            fp,
            path,
        )

        log.info(
            "CATALOGUE | BOOK #%d SAVED | %s",
            book_id,
            title,
        )

        return {
            "status": "kept",
            "book_id": book_id,
            "path": path,
            "url": url,
        }

    # ---------------------------------------------------------
    # PROCESS ONE DISCOVERY RECORD
    # ---------------------------------------------------------

    async def process_one(self, item):
        url = item["url"]
        title = item.get("title") or "Untitled"
        source = item.get("source", "")

        log.info(
            "PROCESSOR | %s | SOURCE=%s | SUBJECT=%s",
            title,
            source,
            item.get("subject", ""),
        )

        # Reject Open Library records whose redistribution
        # rights have not been verified by discovery.py.
        if source == "openlibrary":
            reason = (
                "Unverified Open Library record rejected."
            )

            self.store.set_discovery_status(
                url,
                "skipped",
                reason,
            )

            log.warning(
                "RIGHTS CHECK | Unverified record skipped | %s",
                title,
            )

            return {
                "status": "skipped",
                "url": url,
                "reason": reason,
            }

        # Verified Open Library records are allowed through.
        if source == "openlibrary_verified":
            log.info(
                "RIGHTS CHECK | Verified open file accepted | %s",
                title,
            )

        # Count the attempt before starting network work.
        self.store.mark_discovery_attempt(url)

        try:
            log.info(
                "DOWNLOAD | Starting retrieval | %s",
                url,
            )

            data, content_type, final_url = await download(
                self.client,
                url,
                timeout=settings.request_timeout,
                max_bytes=(
                    settings.max_download_mb * 1024 * 1024
                ),
                retries=settings.download_retries,
                delay=settings.download_delay_seconds,
                user_agent=settings.user_agent,
            )

            log.info(
                "DOWNLOAD | Received %.2f MB",
                len(data) / (1024 * 1024),
            )

            log.info(
                "EXTRACTION | Detecting document format | %s",
                content_type,
            )

            text, fmt = extraction.extract_bytes(
                data,
                content_type,
            )

            log.info(
                "EXTRACTION | Format=%s | Characters=%d",
                fmt.upper(),
                len(text),
            )

            if len(text.strip()) < self.min_chars:
                raise ValueError(
                    "Extracted text is below minimum length"
                )

            result = self.ingest_bytes(
                data,
                text=text,
                fmt=fmt,
                title=title,
                authors=item.get("authors", ""),
                year=item.get("year"),
                subject=item.get("subject", ""),
                category=item.get("category", ""),
                description=item.get("description", ""),
                url=final_url,
                source=source,
                license=SOURCE_LICENSES.get(
                    source,
                    "unknown",
                ),
            )

        except Exception as exc:
            error_message = str(exc)[:1000]

            self.store.set_discovery_status(
                url,
                "failed",
                error_message,
            )

            log.exception(
                "PROCESSING FAILURE | %s",
                title,
            )

            return {
                "status": "failed",
                "url": url,
                "error": error_message,
            }

        self.store.set_discovery_status(
            url,
            result["status"],
        )

        log.info(
            "PROCESSING COMPLETE | %s | STATUS=%s",
            title,
            result["status"],
        )

        return result

    # ---------------------------------------------------------
    # MAIN COLLECTION LOOP
    # ---------------------------------------------------------

    async def run(self, batch_size=50):
        summary = {
            "kept": 0,
            "duplicate": 0,
            "failed": 0,
            "skipped": 0,
            "processed": 0,
        }

        log.info(
            "ENGINE START | Current=%d | Target=%d",
            self.store.count_books(),
            self.target,
        )

        while self.store.count_books() < self.target:

            if not await self.checkpoint():
                break

            queue = self.store.pending_discoveries(
                batch_size
            )

            if not queue:
                log.info(
                    "QUEUE EMPTY | Starting discovery cycle"
                )

                added = await self.discover()

                if not added:
                    log.warning(
                        "NO NEW RECORDS | No fresh candidates found"
                    )
                    break

                continue

            log.info(
                "QUEUE | Loaded %d candidates",
                len(queue),
            )

            for item in queue:

                if not await self.checkpoint():
                    break

                if self.store.count_books() >= self.target:
                    break

                result = await self.process_one(item)

                status = result["status"]

                if status in summary:
                    summary[status] += 1

                summary["processed"] += 1

                log.info(
                    "PROGRESS | Books=%d/%d | Processed=%d",
                    self.store.count_books(),
                    self.target,
                    summary["processed"],
                )

                await asyncio.sleep(
                    settings.download_delay_seconds
                )

            if self.stop_event.is_set():
                break

        summary["total_books"] = self.store.count_books()

        if summary["total_books"] >= self.target:
            log.info(
                "TARGET REACHED | %d unique books",
                summary["total_books"],
            )

        log.info(
            "ENGINE FINISHED | %s",
            summary,
        )

        return summary
