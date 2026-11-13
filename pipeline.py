
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
    "openlibrary": "unverified",
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
            follow_redirects=True
        )

        self.target = (
            target if target is not None
            else settings.target_unique_books
        )

        self.threshold = (
            threshold if threshold is not None
            else settings.near_duplicate_threshold
        )

        self.min_chars = (
            min_chars if min_chars is not None
            else settings.min_text_chars
        )

        self.pause_event = pause_event or asyncio.Event()
        self.pause_event.set()

        self.stop_event = stop_event or asyncio.Event()

    async def checkpoint(self):
        if self.stop_event.is_set():
            return False

        if not self.pause_event.is_set():
            log.warning(
                "CONTROL | Paused at safe processing checkpoint"
            )

        await self.pause_event.wait()

        return not self.stop_event.is_set()

    async def discover(self, pages=None):
        page_count = (
            pages if pages is not None
            else settings.pages_per_subject
        )

        log.info("DISCOVERY | Beginning internet discovery")

        records = await discover_all(
            self.client,
            settings.subjects,
            page_count
        )

        added = sum(
            1 for record in records
            if self.store.add_discovery(record)
        )

        log.info(
            "DISCOVERY | %d candidates found | %d new records queued",
            len(records), added
        )

        return added

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
        log.info("FINGERPRINT | Analysing %s", title)

        fp = fingerprint(data, text)

        log.info(
            "SHA256 | FILE=%s | TEXT=%s",
            fp.file_sha256,
            fp.text_sha256
        )

        log.info(
            "TEXT ANALYSIS | Words=%d | Shingles=%d",
            fp.word_count,
            len(fp.shingles)
        )

        if self.store.has_file_sha256(fp.file_sha256):
            log.warning(
                "DUPLICATE | Identical file detected | %s",
                title
            )
            return {
                "status": "duplicate",
                "reason": "identical file",
                "url": url,
            }

        if self.store.has_text_sha256(fp.text_sha256):
            log.warning(
                "DUPLICATE | Identical extracted text | %s",
                title
            )
            return {
                "status": "duplicate",
                "reason": "identical text",
                "url": url,
            }

        near = self.store.near_duplicate(
            fp.shingles,
            fp.word_count,
            self.threshold
        )

        if near:
            log.warning(
                "NEAR DUPLICATE | %s | Similarity=%.4f",
                title,
                near["score"]
            )

            return {
                "status": "duplicate",
                "reason": "near_duplicate",
                "duplicate_of": near,
                "url": url,
            }

        log.info("DEDUPLICATION | Passed all checks")

        filename = (
            urlparse(url).path.rsplit("/", 1)[-1]
            if url else f"{fp.file_sha256[:12]}.{fmt}"
        )

        log.info(
            "STORAGE | Saving %s | %.2f MB",
            filename,
            len(data) / (1024 * 1024)
        )

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
            metadata={"category": category},
        )

        book_id = self.store.add_book(
            book, fp, path
        )

        log.info(
            "CATALOGUE | BOOK #%d SAVED | %s",
            book_id, title
        )

        return {
            "status": "kept",
            "book_id": book_id,
            "path": path,
            "url": url,
        }

    async def process_one(self, item):
        url = item["url"]
        title = item.get("title") or "Untitled"
        source = item.get("source", "")

        log.info(
            "PROCESSOR | %s | SOURCE=%s | SUBJECT=%s",
            title,
            source,
            item.get("subject", "")
        )

        # Do not bulk-download Open Library scans without
        # verifying the specific item's redistribution rights.
        if source == "openlibrary":
            reason = (
                "Rights not verified; metadata retained only."
            )

            self.store.set_discovery_status(
                url, "skipped", reason
            )

            log.warning(
                "RIGHTS CHECK | Skipped download | %s",
                title
            )

            return {
                "status": "skipped",
                "url": url,
                "reason": reason,
            }

        try:
            log.info(
                "DOWNLOAD | Initiating retrieval | %s",
                url
            )

            data, content_type, final_url = await download(
                self.client,
                url,
                timeout=settings.request_timeout,
                max_bytes=settings.max_download_mb * 1024 * 1024,
                retries=settings.download_retries,
                delay=settings.download_delay_seconds,
                user_agent=settings.user_agent,
            )

            log.info(
                "EXTRACTION | Detecting format | %s",
                content_type
            )

            text, fmt = extraction.extract_bytes(
                data, content_type
            )

            log.info(
                "EXTRACTION | Format=%s | Characters=%d",
                fmt.upper(),
                len(text)
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
                    source, "unknown"
                ),
            )

        except Exception as exc:
            self.store.set_discovery_status(
                url, "failed", str(exc)[:300]
            )

            log.exception(
                "PROCESSING FAILURE | %s",
                title
            )

            return {
                "status": "failed",
                "url": url,
                "error": str(exc),
            }

        self.store.set_discovery_status(
            url, result["status"]
        )

        log.info(
            "PROCESSING COMPLETE | %s | STATUS=%s",
            title,
            result["status"]
        )

        return result

    async def run(self, batch_size=50):
        summary = {
            "kept": 0,
            "duplicate": 0,
            "failed": 0,
            "skipped": 0,
        }

        log.info(
            "ENGINE START | Current=%d | Target=%d",
            self.store.count_books(),
            self.target
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

                if not await self.discover():
                    log.warning(
                        "NO NEW RECORDS | Ending collection run"
                    )
                    break

                continue

            log.info(
                "QUEUE | %d pending records loaded",
                len(queue)
            )

            for item in queue:

                if not await self.checkpoint():
                    break

                if self.store.count_books() >= self.target:
                    break

                result = await self.process_one(item)

                summary[result["status"]] += 1

                await asyncio.sleep(
                    settings.download_delay_seconds
                )

            if self.stop_event.is_set():
                break

        summary["total_books"] = self.store.count_books()

        log.info(
            "ENGINE FINISHED | %s",
            summary
        )

        return summary
