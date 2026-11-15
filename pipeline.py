
"""DocReaper sequential collection and processing engine."""
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
        """Respect persistent and local pause/stop requests."""
        if self.stop_event.is_set():
            return False

        try:
            state = self.store.get_worker_state()

            if state.get("stop_requested"):
                self.stop_event.set()
                log.warning("CONTROL | STOP request detected")
                return False

            while state.get("pause_requested"):
                log.warning("CONTROL | Collection paused")

                if self.stop_event.is_set():
                    return False

                await asyncio.sleep(5)
                state = self.store.get_worker_state()

                if state.get("stop_requested"):
                    self.stop_event.set()
                    return False

            if not state.get("pause_requested"):
                pass

        except Exception:
            log.exception("CONTROL | Could not read worker state")

        await self.pause_event.wait()
        return not self.stop_event.is_set()

    async def discover(self, pages=None, on_record=None):
        """Run discovery, optionally handing each candidate to a callback."""
        page_count = (
            pages if pages is not None
            else settings.pages_per_subject
        )

        log.info("DISCOVERY | Starting sequential discovery")

        return await discover_all(
            self.client,
            settings.subjects,
            page_count,
            on_record=on_record,
        )

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
        """Fingerprint, deduplicate, upload and catalogue a book."""
        log.info("FINGERPRINT | Analysing: %s", title)

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

        if self.store.has_file_sha256(fp.file_sha256):
            log.warning("DUPLICATE | Identical file | %s", title)
            return {"status": "duplicate", "reason": "identical file"}

        if self.store.has_text_sha256(fp.text_sha256):
            log.warning("DUPLICATE | Identical text | %s", title)
            return {"status": "duplicate", "reason": "identical text"}

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
            }

        filename = (
            urlparse(url).path.rsplit("/", 1)[-1]
            if url else ""
        )

        if not filename:
            filename = f"{fp.file_sha256[:12]}.{fmt}"

        log.info(
            "STORAGE | Uploading %s | %.2f MB",
            filename,
            len(data) / (1024 * 1024),
        )

        # Do not catalogue until cloud upload succeeds.
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

        book_id = self.store.add_book(book, fp, path)

        log.info(
            "CATALOGUE | BOOK #%d SAVED | %s",
            book_id,
            title,
        )

        return {
            "status": "kept",
            "book_id": book_id,
            "path": path,
        }

    async def process_one(self, item):
        """Download and process one discovery record."""
        url = item["url"]
        title = item.get("title") or "Untitled"
        source = item.get("source", "")

        log.info(
            "PROCESSOR | %s | SOURCE=%s | SUBJECT=%s",
            title,
            source,
            item.get("subject", ""),
        )

        if source == "openlibrary":
            reason = "Unverified Open Library record rejected."
            self.store.set_discovery_status(url, "skipped", reason)
            log.warning("RIGHTS CHECK | Rejected | %s", title)
            return {"status": "skipped", "reason": reason}

        if source == "openlibrary_verified":
            log.info("RIGHTS CHECK | Verified open file | %s", title)

        self.store.mark_discovery_attempt(url)

        try:
            log.info("DOWNLOAD | Starting retrieval | %s", url)

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
                "DOWNLOAD | Received %.2f MB",
                len(data) / (1024 * 1024),
            )

            log.info(
                "EXTRACTION | Detecting document | %s",
                content_type,
            )

            text, fmt = extraction.extract_bytes(data, content_type)

            if fmt not in {"pdf", "epub"}:
                raise ValueError(
                    f"Unsupported book format detected: {fmt}"
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
                license=SOURCE_LICENSES.get(source, "unknown"),
            )

        except Exception as exc:
            message = str(exc)[:1000]

            self.store.set_discovery_status(
                url,
                "failed",
                message,
            )

            log.exception("PROCESSING FAILURE | %s", title)

            return {
                "status": "failed",
                "url": url,
                "error": message,
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

    async def run(self, batch_size=50):
        """Process queued books, then discover and process one at a time."""
        summary = {
            "kept": 0,
            "duplicate": 0,
            "failed": 0,
            "skipped": 0,
            "processed": 0,
            "discovered": 0,
        }

        log.info(
            "ENGINE START | Current=%d | Target=%d",
            self.store.count_books(),
            self.target,
        )

        async def process_candidate(record):
            """Persist and process a candidate before discovery continues."""
            if not await self.checkpoint():
                return False

            if self.store.count_books() >= self.target:
                log.info("TARGET REACHED | Stopping discovery")
                return False

            if not self.store.add_discovery(record):
                log.info(
                    "DISCOVERY | Already known, skipping | %s",
                    record.title,
                )
                return True

            summary["discovered"] += 1

            log.info(
                "QUEUE | Candidate saved | %s",
                record.title,
            )

            result = await self.process_one({
                "url": record.url,
                "title": record.title,
                "authors": record.authors,
                "source": record.source,
                "subject": record.subject,
                "category": record.category,
                "year": record.year,
                "description": record.description,
            })

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

            await asyncio.sleep(settings.download_delay_seconds)

            return self.store.count_books() < self.target

        while self.store.count_books() < self.target:
            if not await self.checkpoint():
                break

            # Recover previously queued or due-for-retry candidates.
            queue = self.store.pending_discoveries(batch_size)

            if queue:
                log.info(
                    "QUEUE | Processing %d saved candidates",
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

                continue

            log.info(
                "QUEUE EMPTY | Discovering and processing next book"
            )

            before = summary["discovered"]

            await self.discover(on_record=process_candidate)

            if self.store.count_books() >= self.target:
                break

            # No new URLs means this discovery cycle did not add
            # any work. Do not spin forever over the same records.
            if summary["discovered"] == before:
                log.warning(
                    "NO NEW CANDIDATES | Discovery cycle produced "
                    "no new queue entries"
                )
                break

        summary["total_books"] = self.store.count_books()

        if summary["total_books"] >= self.target:
            log.info(
                "TARGET REACHED | %d unique books",
                summary["total_books"],
            )

        log.info("ENGINE FINISHED | %s", summary)

        return summary
