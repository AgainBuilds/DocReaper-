"""Polite HTTP downloader with retries and a size cap."""
from __future__ import annotations

import asyncio

import httpx


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
    """Return (data, content_type, final_url). Raises DownloadError."""
    last_error: Exception | None = None
    for attempt in range(1, retries + 1):
        try:
            async with client.stream(
                "GET", url, timeout=timeout, headers={"User-Agent": user_agent}
            ) as resp:
                resp.raise_for_status()
                declared = resp.headers.get("content-length")
                if declared and int(declared) > max_bytes:
                    raise DownloadError(f"File too large: {declared} bytes")
                chunks: list[bytes] = []
                size = 0
                async for chunk in resp.aiter_bytes(65536):
                    size += len(chunk)
                    if size > max_bytes:
                        raise DownloadError("File exceeds download size limit")
                    chunks.append(chunk)
                return (
                    b"".join(chunks),
                    resp.headers.get("content-type", ""),
                    str(resp.url),
                )
        except (httpx.HTTPError, DownloadError) as exc:
            last_error = exc
            if attempt < retries:
                await asyncio.sleep(delay * attempt)
    raise DownloadError(f"Failed to download {url}: {last_error}")
