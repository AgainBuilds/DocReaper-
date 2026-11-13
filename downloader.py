
"""Polite HTTP downloader with detailed live telemetry."""
from __future__ import annotations

import asyncio
import logging
import httpx

log = logging.getLogger("docreaper")


class DownloadError(RuntimeError):
    pass


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

    for attempt in range(1, retries + 1):
        try:
            log.info(
                "HTTP REQUEST | GET %s | attempt %d/%d",
                url, attempt, retries
            )

            async with client.stream(
                "GET",
                url,
                timeout=timeout,
                headers={"User-Agent": user_agent}
            ) as response:

                response.raise_for_status()

                content_type = response.headers.get(
                    "content-type", "unknown"
                )

                content_length = response.headers.get(
                    "content-length", "unknown"
                )

                log.info(
                    "HTTP RESPONSE | %s | TYPE=%s | SIZE=%s",
                    response.status_code,
                    content_type,
                    content_length
                )

                if content_length != "unknown":
                    if int(content_length) > max_bytes:
                        raise DownloadError("File exceeds size limit")

                chunks = []
                size = 0
                next_report = 1024 * 1024

                async for chunk in response.aiter_bytes(65536):
                    size += len(chunk)

                    if size > max_bytes:
                        raise DownloadError(
                            "Download exceeded maximum permitted size"
                        )

                    chunks.append(chunk)

                    if size >= next_report:
                        log.info(
                            "DOWNLOAD PROGRESS | %.2f MB received",
                            size / (1024 * 1024)
                        )
                        next_report += 1024 * 1024

                final_url = str(response.url)

                log.info(
                    "DOWNLOAD COMPLETE | %.2f MB | %s",
                    size / (1024 * 1024),
                    final_url
                )

                return (
                    b"".join(chunks),
                    content_type,
                    final_url
                )

        except (httpx.HTTPError, DownloadError) as exc:
            last_error = exc

            log.warning(
                "REQUEST FAILED | Attempt %d | %s",
                attempt, str(exc)
            )

            if attempt < retries:
                wait = delay * attempt
                log.info("RETRY | Waiting %.1f seconds", wait)
                await asyncio.sleep(wait)

    raise DownloadError(
        f"Could not download {url}: {last_error}"
    )
