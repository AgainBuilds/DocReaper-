
"""Internet discovery through Gutendex and Open Library."""
from __future__ import annotations

import logging
import httpx

from .schemas import DiscoveryRecord

log = logging.getLogger("docreaper")

GUTENDEX_URL = "https://gutendex.com/books"
OPENLIBRARY_URL = "https://openlibrary.org/search.json"

GUTENDEX_FORMATS = (
    "application/pdf",
    "application/epub+zip",
    "text/plain; charset=utf-8",
    "text/plain",
    "text/html",
)


def _gutendex_download(formats):
    for mime in GUTENDEX_FORMATS:
        if mime in formats:
            return formats[mime]
    return None


async def discover_gutendex(client, subject, pages=2):
    records = []

    for page in range(1, pages + 1):
        params = {"topic": subject, "page": page}

        log.info(
            "GUTENDEX SEARCH | subject=%s | page=%d",
            subject, page
        )

        try:
            response = await client.get(
                GUTENDEX_URL,
                params=params
            )
            response.raise_for_status()
            data = response.json()

            log.info(
                "GUTENDEX RESPONSE | HTTP %d | %d results",
                response.status_code,
                len(data.get("results", []))
            )

        except (httpx.HTTPError, ValueError) as exc:
            log.exception("GUTENDEX ERROR | %s", exc)
            break

        page_count = 0

        for item in data.get("results", []):
            url = _gutendex_download(
                item.get("formats", {})
            )

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
                record.authors
            )

        log.info(
            "GUTENDEX PAGE COMPLETE | %d downloadable candidates",
            page_count
        )

        if not data.get("next"):
            break

    return records


async def discover_openlibrary(client, subject, pages=2):
    records = []

    for page in range(1, pages + 1):
        params = {
            "q": f"{subject} textbook",
            "fields": "title,author_name,first_publish_year,ia",
            "limit": 100,
            "page": page,
        }

        log.info(
            "OPEN LIBRARY SEARCH | subject=%s | page=%d",
            subject, page
        )

        try:
            response = await client.get(
                OPENLIBRARY_URL,
                params=params
            )
            response.raise_for_status()
            data = response.json()

            docs = data.get("docs", [])

            log.info(
                "OPEN LIBRARY RESPONSE | HTTP %d | %d records",
                response.status_code,
                len(docs)
            )

        except (httpx.HTTPError, ValueError) as exc:
            log.exception("OPEN LIBRARY ERROR | %s", exc)
            break

        for doc in docs:
            identifiers = doc.get("ia") or []

            if not identifiers:
                continue

            identifier = identifiers[0]

            record = DiscoveryRecord(
                url=(
                    f"https://archive.org/download/"
                    f"{identifier}/{identifier}.pdf"
                ),
                title=doc.get("title", "Untitled"),
                authors=", ".join(
                    doc.get("author_name", [])[:3]
                ),
                source="openlibrary",
                subject=subject,
                category="core",
                year=doc.get("first_publish_year"),
            )

            records.append(record)

            log.info(
                "CATALOGUE RECORD FOUND | %s | %s",
                record.title,
                record.authors
            )

        if len(docs) < 100:
            break

    return records


async def discover_all(client, subjects, pages_per_subject=2):
    records = []

    log.info(
        "DISCOVERY ENGINE STARTED | %d subjects",
        len(subjects)
    )

    for index, subject in enumerate(subjects, start=1):
        log.info(
            "SUBJECT SCAN | %d/%d | %s",
            index, len(subjects), subject
        )

        records.extend(
            await discover_openlibrary(
                client, subject, pages_per_subject
            )
        )

        records.extend(
            await discover_gutendex(
                client, subject, pages_per_subject
            )
        )

    log.info(
        "DISCOVERY FINISHED | %d total candidate records",
        len(records)
    )

    return records
