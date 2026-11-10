"""Storage of unique book files: Google Cloud Storage with local fallback."""
from __future__ import annotations

import hashlib
from pathlib import Path

from .config import settings


class CloudStore:
    def __init__(self, bucket: str | None = None, prefix: str | None = None,
                 local_dir: str | None = None):
        self.bucket_name = bucket if bucket is not None else settings.gcs_bucket
        self.prefix = (prefix if prefix is not None else settings.gcs_prefix)
        self.prefix = self.prefix.strip("/")
        self.local_dir = Path(local_dir or settings.local_library_dir)

    def save(self, data: bytes, filename: str) -> str:
        digest = hashlib.sha256(data).hexdigest()[:16]
        safe = "".join(c if c.isalnum() or c in "._-" else "_" for c in filename)
        object_name = f"{digest}_{safe[:80]}"
        if self.prefix:
            object_name = f"{self.prefix}/{object_name}"
        if self.bucket_name:
            return self._upload_gcs(data, object_name)
        return self._save_local(data, object_name)

    def _upload_gcs(self, data: bytes, object_name: str) -> str:
        from google.cloud import storage  # optional dependency

        client = storage.Client()
        bucket = client.bucket(self.bucket_name)
        bucket.blob(object_name).upload_from_string(data)
        return f"gs://{self.bucket_name}/{object_name}"

    def _save_local(self, data: bytes, object_name: str) -> str:
        path = self.local_dir / object_name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        return str(path)
