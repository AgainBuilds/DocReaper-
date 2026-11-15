
"""HTTP downloader with retries, size limits and file validation."""
from __future__ import annotations

import asyncio
import logging

import httpx

log = logging.getLogger("docreaper")


class DownloadError(RuntimeError):
    pass


def _validate_book_bytes(data: bytes, content_type: str) -> None:
    """Accept actual PDF and ZIP-based EPUB files only."""
    if not data:
        raise DownloadError("The server returned an empty file.")

    content_type = (content_type or "").lower()

    if "text/html" in content_type:
        raise DownloadError(
            "The download URL returned an HTML page, not a book."
        )

    if data.startswith(b"%PDF-"):
        return

    # EPUB files are ZIP containers. The extraction stage will
    # distinguish EPUB from other ZIP-based formats.
    if data.startswith(b"PK"):
        return

    raise DownloadError(
        "Downloaded data is not a recognized PDF or EPUB file."
    )


async def download(
    client: httpx.AsyncClient,
    url: str,
    *,
    timeout: float,
    max_bytes: int,
    retries: int,
    delay: float,
    user_agent: str,
) -> tuple[bytes, str, str]:

    last_error = None
    attempts = max(1, retries)

    for attempt in range(1, attempts + 1):
        try:
            log.info(
                "HTTP REQUEST | GET %s | attempt %d/%d",
                url,
                attempt,
                attempts,
            )

            async with client.stream(
                "GET",
                url,
                timeout=timeout,
                headers={"User-Agent": user_agent},
            ) as response:

                response.raise_for_status()

                content_type = response.headers.get(
                    "content-type",
                    "unknown",
                )

                content_length = response.headers.get(
                    "content-length",
                    "",
                )

                log.info(
                    "HTTP RESPONSE | %s | TYPE=%s | SIZE=%s",
                    response.status_code,
                    content_type,
                    content_length or "unknown",
                )

                if content_length:
                    try:
                        declared_size = int(content_length)
                    except (TypeError, ValueError):
                        declared_size = 0

                    if declared_size > max_bytes:
                        raise DownloadError(
                            "File exceeds configured size limit."
                        )

                chunks = []
                size = 0
                next_report = 1024 * 1024

                async for chunk in response.aiter_bytes(65536):
                    if not chunk:
                        continue

                    size += len(chunk)

                    if size > max_bytes:
                        raise DownloadError(
                            "Download exceeded maximum permitted size."
                        )

                    chunks.append(chunk)

                    if size >= next_report:
                        log.info(
                            "DOWNLOAD PROGRESS | %.2f MB received",
                            size / (1024 * 1024),
                        )
                        next_report += 1024 * 1024

                data = b"".join(chunks)

                _validate_book_bytes(data, content_type)

                final_url = str(response.url)

                log.info(
                    "DOWNLOAD COMPLETE | %.2f MB | %s",
                    len(data) / (1024 * 1024),
                    final_url,
                )

                return data, content_type, final_url

        except (httpx.HTTPError, DownloadError) as exc:
            last_error = exc

            log.warning(
                "REQUEST FAILED | Attempt %d/%d | %s",
                attempt,
                attempts,
                str(exc),
            )

            if attempt < attempts:
                wait = delay * attempt
                log.info("RETRY | Waiting %.1f seconds", wait)
                await asyncio.sleep(wait)

    raise DownloadError(
        f"Could not download {url}: {last_error}"
    )
