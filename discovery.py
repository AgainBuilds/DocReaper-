
"""Sequential discovery through Gutendex and verified Internet Archive files."""
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

# Only accept actual book formats. Do not treat HTML landing pages
# or arbitrary plain-text responses as downloadable book files.
GUTENDEX_FORMATS = (
    "application/pdf",
    "application/epub+zip",
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
        url = formats.get(mime)
        if url:
            return url
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
    """Resolve a public PDF/EPUB and verify its stated license."""
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


async def discover_gutendex(
    client,
    subject,
    pages=2,
    on_record=None,
):
    records = []

    for page in range(1, pages + 1):
        log.info(
            "GUTENDEX SEARCH | subject=%s | page=%d",
            subject,
            page,
        )

        try:
            response = await client.get(
                GUTENDEX_URL,
                params={"topic": subject, "page": page},
            )
            response.raise_for_status()
            data = response.json()

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

            # Crucial: process this candidate before discovering
            # the next one.
            if on_record is not None:
                keep_going = await on_record(record)
                if keep_going is False:
                    log.info("DISCOVERY | Stopped by collection target")
                    return records

        log.info(
            "GUTENDEX PAGE COMPLETE | %d PDF/EPUB candidates",
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
    on_record=None,
):
    """Discover open Internet Archive files one record at a time."""
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

        except (httpx.HTTPError, ValueError) as exc:
            log.exception("OPEN LIBRARY ERROR | %s", exc)
            break

        page_count = 0

        # Deliberately sequential. Do not gather an entire page
        # before allowing the collector to process its first result.
        for doc in docs:
            identifiers = doc.get("ia") or []

            if not identifiers:
                continue

            identifier = identifiers[0]

            if identifier not in cache:
                cache[identifier] = await _archive_download_details(
                    client,
                    identifier,
                    semaphore,
                )

            details = cache[identifier]

            if not details:
                continue

            download_url, license_name = details

            record = DiscoveryRecord(
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

            records.append(record)
            page_count += 1

            log.info(
                "BOOK DISCOVERED | VERIFIED OPEN FILE | %s",
                record.title,
            )

            if on_record is not None:
                keep_going = await on_record(record)
                if keep_going is False:
                    log.info("DISCOVERY | Stopped by collection target")
                    return records

        log.info(
            "OPEN LIBRARY PAGE COMPLETE | "
            "%d verified PDF/EPUB candidates",
            page_count,
        )

        if len(docs) < 100:
            break

    return records


async def discover_all(
    client,
    subjects,
    pages_per_subject=2,
    on_record=None,
):
    """Discover and hand off each candidate before continuing."""
    records = []
    metadata_cache = {}
    metadata_semaphore = asyncio.Semaphore(6)

    log.info(
        "DISCOVERY ENGINE STARTED | %d subjects",
        len(subjects),
    )

    stopped = False

    async def emit(record):
        nonlocal stopped

        if on_record is None:
            return True

        keep_going = await on_record(record)

        if keep_going is False:
            stopped = True
            return False

        return True

    for index, subject in enumerate(subjects, start=1):
        if stopped:
            break

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
                on_record=emit if on_record is not None else None,
            )
        )

        if stopped:
            break

        records.extend(
            await discover_gutendex(
                client,
                subject,
                pages_per_subject,
                on_record=emit if on_record is not None else None,
            )
        )

    log.info(
        "DISCOVERY FINISHED | %d candidates handed off",
        len(records),
    )

    return records
