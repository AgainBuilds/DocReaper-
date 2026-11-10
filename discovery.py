"""Internet discovery of open-access undergraduate textbooks."""
from __future__ import annotations

import httpx

from .schemas import DiscoveryRecord

GUTENDEX_URL = "https://gutendex.com/books"
OPENLIBRARY_URL = "https://openlibrary.org/search.json"

GUTENDEX_FORMATS = (
    "application/pdf",
    "application/epub+zip",
    "text/plain; charset=utf-8",
    "text/plain",
    "text/html",
)


def _gutendex_download(formats: dict) -> str | None:
    for mime in GUTENDEX_FORMATS:
        if mime in formats:
            return formats[mime]
    return None


async def discover_gutendex(
    client: httpx.AsyncClient, subject: str, pages: int = 2
) -> list[DiscoveryRecord]:
    records: list[DiscoveryRecord] = []
    for page in range(1, pages + 1):
        try:
            resp = await client.get(
                GUTENDEX_URL, params={"topic": subject, "page": page}
            )
            resp.raise_for_status()
            data = resp.json()
        except (httpx.HTTPError, ValueError):
            break
        for item in data.get("results", []):
            url = _gutendex_download(item.get("formats", {}))
            if not url:
                continue
            summaries = item.get("summaries") or [""]
            records.append(DiscoveryRecord(
                url=url,
                title=item.get("title", "Untitled"),
                authors=", ".join(a.get("name", "") for a in item.get("authors", [])),
                source="gutenberg",
                subject=subject,
                category="reference",
                description=summaries[0][:500],
            ))
        if not data.get("next"):
            break
    return records


async def discover_openlibrary(
    client: httpx.AsyncClient, subject: str, pages: int = 2
) -> list[DiscoveryRecord]:
    records: list[DiscoveryRecord] = []
    for page in range(1, pages + 1):
        try:
            resp = await client.get(OPENLIBRARY_URL, params={
                "q": f"{subject} textbook",
                "fields": "title,author_name,first_publish_year,ia",
                "limit": 100,
                "page": page,
            })
            resp.raise_for_status()
            docs = resp.json().get("docs", [])
        except (httpx.HTTPError, ValueError):
            break
        for doc in docs:
            ocaids = doc.get("ia") or []
            if not ocaids:
                continue
            ocaid = ocaids[0]
            records.append(DiscoveryRecord(
                url=f"https://archive.org/download/{ocaid}/{ocaid}.pdf",
                title=doc.get("title", "Untitled"),
                authors=", ".join(doc.get("author_name", [])[:3]),
                source="openlibrary",
                subject=subject,
                category="core",
                year=doc.get("first_publish_year"),
            ))
        if len(docs) < 100:
            break
    return records


async def discover_all(
    client: httpx.AsyncClient, subjects: list[str], pages_per_subject: int = 2
) -> list[DiscoveryRecord]:
    records: list[DiscoveryRecord] = []
    for subject in subjects:
        records += await discover_openlibrary(client, subject, pages_per_subject)
        records += await discover_gutendex(client, subject, pages_per_subject)
    return records
