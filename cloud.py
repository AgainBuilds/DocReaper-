
"""Storage of unique book files: Google Drive, GCS, or local fallback."""
from __future__ import annotations

import hashlib
import logging
from pathlib import Path

from .config import settings


logger = logging.getLogger("docreaper")


class CloudStore:
    def __init__(
        self,
        bucket: str | None = None,
        prefix: str | None = None,
        local_dir: str | None = None,
    ):
        self.bucket_name = (
            bucket if bucket is not None else settings.gcs_bucket
        )

        self.prefix = (
            prefix if prefix is not None else settings.gcs_prefix
        ).strip("/")

        self.local_dir = Path(
            local_dir or settings.local_library_dir
        )

    def save(self, data: bytes, filename: str) -> str:
        """Save a downloaded book to the configured storage backend."""

        if not isinstance(data, bytes):
            raise TypeError("Book data must be bytes.")

        if not data:
            raise ValueError("Cannot save an empty book.")

        digest = hashlib.sha256(data).hexdigest()[:16]

        safe_filename = "".join(
            char if char.isalnum() or char in "._-" else "_"
            for char in Path(filename).name
        )

        if not safe_filename:
            safe_filename = "untitled_book"

        object_name = f"{digest}_{safe_filename[:80]}"

        if self.prefix:
            object_name = f"{self.prefix}/{object_name}"

        # Google Drive takes priority when a refresh token is configured.
        if settings.google_drive_refresh_token:
            return self._upload_drive(data, object_name)

        # Google Cloud Storage is the second option.
        if self.bucket_name:
            return self._upload_gcs(data, object_name)

        # Local storage is the fallback.
        return self._save_local(data, object_name)

    def _upload_drive(self, data: bytes, filename: str) -> str:
        """Upload a book to the user's Google Drive."""

        from .google_drive import upload_bytes

        if not settings.google_client_id:
            raise RuntimeError(
                "Google Drive Client ID is missing."
            )

        if not settings.google_drive_refresh_token:
            raise RuntimeError(
                "Google Drive refresh token is missing."
            )

        result = upload_bytes(
            data,
            filename,
            client_id=settings.google_client_id,
            client_secret=settings.google_client_secret,
            refresh_token=settings.google_drive_refresh_token,
            folder_id=settings.google_drive_folder_id,
        )

        file_id = result.get("id")

        if not file_id:
            raise RuntimeError(
                "Google Drive upload returned no file ID."
            )

        link = result.get("webViewLink")

        if not link:
            link = f"https://drive.google.com/file/d/{file_id}/view"

        logger.info(
            "GOOGLE DRIVE | Uploaded %s | FILE_ID=%s",
            result.get("name", filename),
            file_id,
        )

        return link

    def _upload_gcs(self, data: bytes, object_name: str) -> str:
        """Upload a book to Google Cloud Storage."""

        from google.cloud import storage

        client = storage.Client()
        bucket = client.bucket(self.bucket_name)

        blob = bucket.blob(object_name)
        blob.upload_from_string(data)

        logger.info(
            "GCS | Uploaded %s",
            object_name,
        )

        return f"gs://{self.bucket_name}/{object_name}"

    def _save_local(self, data: bytes, object_name: str) -> str:
        """Save a book to the local filesystem."""

        path = self.local_dir / object_name

        path.parent.mkdir(
            parents=True,
            exist_ok=True,
        )

        path.write_bytes(data)

        logger.info(
            "LOCAL STORAGE | Saved %s",
            path,
        )

        return str(path)
