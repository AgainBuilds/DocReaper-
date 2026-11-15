
"""Internet discovery through Gutendex and verified open Internet Archive files."""
from __future__ import annotations

import asyncio
import logging
from urllib.parse import quote

import httpx

from .schemas import DiscoveryRecord

log = logging.getLogger("docreaper")

GUTENDEX_URL = "https://gutendex.com/books"
OPENLIBRARY_URL = "https://openlibrary.org/search.json"
ARCHIVE_METADATA_URL = "https://archive.org/metadata"

GUTENDEX_FORMATS = (
    "application/pdf",
    "application/epub+zip",
    "text/plain; charset=utf-8",
    "text/plain",
    "text/html",
)

OPEN_LICENSE_MARKERS = (
    "creativecommons.org/publicdomain/mark/1.0",
    "creativecommons.org/publicdomain/zero/1.0",
    "creativecommons.org/licenses/by/4.0",
    "creativecommons.org/licenses/by-sa/4.0",
    "creativecommons.org/licenses/by/3.0",
    "creativecommons.org/licenses/by-sa/3.0",
    "creativecommons.org/licenses/by/2.5",
    "creativecommons.org/licenses/by-sa/2.5",
)


def _gutendex_download(formats):
    for mime in GUTENDEX_FORMATS:
        if mime in formats:
            return formats[mime]
    return None


def _is_explicitly_open(metadata):
    value = metadata.get("licenseurl") or metadata.get("rights") or ""
    if isinstance(value, list):
        value = " ".join(str(part) for part in value)

    normalized = str(value).strip().lower()

    if "public domain" in normalized:
        return True

    return any(marker in normalized for marker in OPEN_LICENSE_MARKERS)


def _is_true(value):
    return str(value).strip().lower() in {"1", "true", "yes"}


async def _archive_download_details(client, identifier, semaphore):
    """Resolve a real public file and verify its stated license."""
    url = f"{ARCHIVE_METADATA_URL}/{quote(identifier, safe='')}"

    async with semaphore:
        try:
            response = await client.get(url)
            response.raise_for_status()
            item = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            log.warning(
                "ARCHIVE METADATA ERROR | %s | %s",
                identifier,
                str(exc)[:250],
            )
            return None

    metadata = item.get("metadata") or {}
    files = item.get("files") or []

    if _is_true(metadata.get("access-restricted-item")):
        log.info("OPEN LIBRARY SKIP | Restricted item | %s", identifier)
        return None

    if not _is_explicitly_open(metadata):
        log.info(
            "OPEN LIBRARY SKIP | No verified redistribution license | %s",
            identifier,
        )
        return None

    candidates = []

    for entry in files:
        name = str(entry.get("name") or "")
        lowered = name.lower()

        if _is_true(entry.get("private")):
            continue

        if lowered.endswith(".pdf"):
            priority = 0
        elif lowered.endswith(".epub"):
            priority = 1
        else:
            continue

        try:
            size = int(entry.get("size") or 0)
        except (TypeError, ValueError):
            size = 0

        candidates.append((priority, size, name))

    if not candidates:
        log.info(
            "OPEN LIBRARY SKIP | No public PDF/EPUB file | %s",
            identifier,
        )
        return None

    candidates.sort(
        key=lambda candidate: (
            candidate[0],
            candidate[1] or 2**63,
        )
    )

    filename = candidates[0][2]

    download_url = (
        f"https://archive.org/download/{quote(identifier, safe='')}/"
        f"{quote(filename, safe='/')}"
    )

    license_name = (
        metadata.get("licenseurl")
        or metadata.get("rights")
        or "Public Domain"
    )

    return download_url, str(license_name)


async def discover_gutendex(client, subject, pages=2):
    records = []

    for page in range(1, pages + 1):
        params = {"topic": subject, "page": page}

        log.info(
            "GUTENDEX SEARCH | subject=%s | page=%d",
            subject,
            page,
        )

        try:
            response = await client.get(
                GUTENDEX_URL,
                params=params,
            )
            response.raise_for_status()
            data = response.json()

            log.info(
                "GUTENDEX RESPONSE | HTTP %d | %d results",
                response.status_code,
                len(data.get("results", [])),
            )

        except (httpx.HTTPError, ValueError) as exc:
            log.exception("GUTENDEX ERROR | %s", exc)
            break

        page_count = 0

        for item in data.get("results", []):
            url = _gutendex_download(item.get("formats", {}))

            if not url:
                continue

            summaries = item.get("summaries") or [""]

            record = DiscoveryRecord(
                url=url,
                title=item.get("title", "Untitled"),
                authors=", ".join(
                    author.get("name", "")
                    for author in item.get("authors", [])
                ),
                source="gutenberg",
                subject=subject,
                category="reference",
                description=summaries[0][:500],
            )

            records.append(record)
            page_count += 1

            log.info(
                "BOOK DISCOVERED | %s | %s",
                record.title,
                record.authors,
            )

        log.info(
            "GUTENDEX PAGE COMPLETE | %d downloadable candidates",
            page_count,
        )

        if not data.get("next"):
            break

    return records


async def discover_openlibrary(
    client,
    subject,
    pages=2,
    *,
    metadata_cache=None,
    metadata_semaphore=None,
):
    """Discover Open Library records only when IA confirms open files."""
    records = []
    cache = metadata_cache if metadata_cache is not None else {}
    semaphore = metadata_semaphore or asyncio.Semaphore(6)

    for page in range(1, pages + 1):
        params = {
            "q": f"{subject} textbook",
            "fields": "title,author_name,first_publish_year,ia",
            "limit": 100,
            "page": page,
        }

        log.info(
            "OPEN LIBRARY SEARCH | subject=%s | page=%d",
            subject,
            page,
        )

        try:
            response = await client.get(
                OPENLIBRARY_URL,
                params=params,
            )
            response.raise_for_status()
            data = response.json()
            docs = data.get("docs", [])

            log.info(
                "OPEN LIBRARY RESPONSE | HTTP %d | %d records",
                response.status_code,
                len(docs),
            )

        except (httpx.HTTPError, ValueError) as exc:
            log.exception("OPEN LIBRARY ERROR | %s", exc)
            break

        async def resolve(doc):
            identifiers = doc.get("ia") or []

            if not identifiers:
                return None

            identifier = identifiers[0]

            if identifier not in cache:
                cache[identifier] = await _archive_download_details(
                    client,
                    identifier,
                    semaphore,
                )

            details = cache[identifier]

            if not details:
                return None

            download_url, license_name = details

            return DiscoveryRecord(
                url=download_url,
                title=doc.get("title", "Untitled"),
                authors=", ".join(doc.get("author_name", [])[:3]),
                source="openlibrary_verified",
                subject=subject,
                category="core",
                year=doc.get("first_publish_year"),
                description=(
                    f"Internet Archive file; license: {license_name}"
                ),
            )

        resolved = await asyncio.gather(
            *(resolve(doc) for doc in docs)
        )

        page_count = 0

        for record in resolved:
            if record is None:
                continue

            records.append(record)
            page_count += 1

            log.info(
                "BOOK DISCOVERED | VERIFIED OPEN FILE | %s | %s",
                record.title,
                record.authors,
            )

        log.info(
            "OPEN LIBRARY PAGE COMPLETE | "
            "%d verified downloadable candidates",
            page_count,
        )

        if len(docs) < 100:
            break

    return records


async def discover_all(client, subjects, pages_per_subject=2):
    records = []
    metadata_cache = {}
    metadata_semaphore = asyncio.Semaphore(6)

    log.info(
        "DISCOVERY ENGINE STARTED | %d subjects",
        len(subjects),
    )

    for index, subject in enumerate(subjects, start=1):
        log.info(
            "SUBJECT SCAN | %d/%d | %s",
            index,
            len(subjects),
            subject,
        )

        records.extend(
            await discover_openlibrary(
                client,
                subject,
                pages_per_subject,
                metadata_cache=metadata_cache,
                metadata_semaphore=metadata_semaphore,
            )
        )

        records.extend(
            await discover_gutendex(
                client,
                subject,
                pages_per_subject,
            )
        )

    log.info(
        "DISCOVERY FINISHED | %d verified downloadable candidates",
        len(records),
    )

    return records
