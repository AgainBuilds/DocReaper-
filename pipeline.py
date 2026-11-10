"""The six-step DocReaper pipeline."""
from __future__ import annotations

import asyncio
import logging
from urllib.parse import urlparse

import httpx

from . import extraction
from .cloud import CloudStore
from .config import settings
from .discovery import discover_all
from .downloader import DownloadError, download
from .fingerprints import fingerprint
from .schemas import Book
from .storage import Store

log = logging.getLogger("docreaper")

SOURCE_LICENSES = {
    "gutenberg": "public domain",
    "openlibrary": "public domain scan (verify)",
}


class Pipeline:
    def __init__(self, store: Store, cloud: CloudStore | None = None, *,
                 client: httpx.AsyncClient | None = None,
                 target: int | None = None, threshold: float | None = None,
                 min_chars: int | None = None):
        self.store = store
        self.cloud = cloud or CloudStore()
        self.client = client or httpx.AsyncClient(follow_redirects=True)
        self.target = target if target is not None else settings.target_unique_books
        self.threshold = (threshold if threshold is not None
                          else settings.near_duplicate_threshold)
        self.min_chars = (min_chars if min_chars is not None
                          else settings.min_text_chars)

    # Step 1: internet discovery
    async def discover(self, pages: int | None = None) -> int:
        records = await discover_all(
            self.client, settings.subjects,
            pages if pages is not None else settings.pages_per_subject,
        )
        added = sum(1 for rec in records if self.store.add_discovery(rec))
        log.info("Discovered %d new URLs", added)
        return added

    # Steps 2-5: download, analyse, filter, store
    def ingest_bytes(self, data: bytes, *, text: str, fmt: str, title: str,
                     authors: str = "", year: int | None = None,
                     subject: str = "", category: str = "",
                     description: str = "", url: str = "", source: str = "",
                     license: str = "unknown") -> dict:
        fp = fingerprint(data, text)
        if self.store.has_file_sha256(fp.file_sha256):
            return {"status": "duplicate", "reason": "identical file", "url": url}
        if self.store.has_text_sha256(fp.text_sha256):
            return {"status": "duplicate", "reason": "identical text", "url": url}
        near = self.store.near_duplicate(fp.shingles, fp.word_count, self.threshold)
        if near:
            return {"status": "duplicate", "reason": "near_duplicate",
                    "duplicate_of": near, "url": url}
        name = urlparse(url).path.rsplit("/", 1)[-1] if url else ""
        filename = name or f"{fp.file_sha256[:12]}.{fmt}"
        path = self.cloud.save(data, filename)
        book = Book(title=title, authors=authors, year=year, subject=subject,
                    description=description, source_url=url, fmt=fmt,
                    size_bytes=len(data), license=license,
                    metadata={"category": category})
        book_id = self.store.add_book(book, fp, path)
        return {"status": "kept", "book_id": book_id, "path": path, "url": url}

    async def process_one(self, item: dict) -> dict:
        url = item["url"]
        try:
            data, ctype, final_url = await download(
                self.client, url,
                timeout=settings.request_timeout,
                max_bytes=settings.max_download_mb * 1024 * 1024,
                retries=settings.download_retries,
                delay=settings.download_delay_seconds,
                user_agent=settings.user_agent,
            )
            text, fmt = extraction.extract_bytes(data, ctype)
            if len(text.strip()) < self.min_chars:
                raise extraction.ExtractionError("Extracted text is too short")
            result = self.ingest_bytes(
                data, text=text, fmt=fmt, title=item.get("title") or "Untitled",
                authors=item.get("authors", ""), year=item.get("year"),
                subject=item.get("subject", ""), category=item.get("category", ""),
                description=item.get("description", ""), url=final_url,
                source=item.get("source", ""),
                license=SOURCE_LICENSES.get(item.get("source", ""), "unknown"),
            )
        except (DownloadError, extraction.ExtractionError, ValueError) as exc:
            self.store.set_discovery_status(url, "failed", str(exc)[:300])
            return {"status": "failed", "url": url, "error": str(exc)}
        self.store.set_discovery_status(url, result["status"])
        return result

    # Step 6: run until the target
    async def run(self, batch_size: int = 50) -> dict:
        summary = {"kept": 0, "duplicate": 0, "failed": 0}
        while self.store.count_books() < self.target:
            queue = self.store.pending_discoveries(batch_size)
            if not queue:
                if not await self.discover():
                    break
                continue
            for item in queue:
                if self.store.count_books() >= self.target:
                    break
                outcome = await self.process_one(item)
                summary[outcome["status"]] += 1
                await asyncio.sleep(settings.download_delay_seconds)
        summary["total_books"] = self.store.count_books()
        return summary
