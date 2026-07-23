"""Object-storage adapters for uploaded knowledge originals."""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import Protocol

from agent.app.config import get_settings


class StorageBackend(Protocol):
    def put_bytes(self, object_key: str, content: bytes) -> None: ...
    def read_bytes(self, object_key: str) -> bytes: ...
    def delete(self, object_key: str) -> None: ...
    def quarantine(self, object_key: str) -> str: ...
    def local_path(self, object_key: str) -> Path | None: ...


class LocalFilesystemStorage:
    def __init__(self, root: str):
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    def _path(self, object_key: str) -> Path:
        path = (self.root / object_key).resolve()
        if self.root not in path.parents:
            raise ValueError("Invalid storage object key.")
        return path

    def put_bytes(self, object_key: str, content: bytes) -> None:
        path = self._path(object_key)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)

    def read_bytes(self, object_key: str) -> bytes:
        return self._path(object_key).read_bytes()

    def delete(self, object_key: str) -> None:
        self._path(object_key).unlink(missing_ok=True)

    def quarantine(self, object_key: str) -> str:
        destination_key = f"quarantine/{object_key}"
        destination = self._path(destination_key)
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(self._path(object_key)), destination)
        return destination_key

    def local_path(self, object_key: str) -> Path:
        return self._path(object_key)


class GCSStorage:
    """Store active originals separately from quarantined uploads."""

    def __init__(self, bucket: str | None, quarantine_bucket: str | None):
        if not bucket:
            raise RuntimeError("GCS storage requires ARCHAGENT_GCP_STORAGE_BUCKET.")
        if not quarantine_bucket:
            raise RuntimeError("GCS storage requires ARCHAGENT_GCP_QUARANTINE_BUCKET.")
        self.bucket = bucket
        self.quarantine_bucket = quarantine_bucket

    def _client(self):
        try:
            from google.cloud import storage  # type: ignore[reportMissingImports]
        except Exception as exc:
            raise RuntimeError("GCS storage requires google-cloud-storage.") from exc
        return storage.Client()

    def put_bytes(self, object_key: str, content: bytes) -> None:
        self._client().bucket(self.bucket).blob(object_key).upload_from_string(content)

    def read_bytes(self, object_key: str) -> bytes:
        return self._client().bucket(self.bucket).blob(object_key).download_as_bytes()

    def delete(self, object_key: str) -> None:
        self._client().bucket(self.bucket).blob(object_key).delete()

    def quarantine(self, object_key: str) -> str:
        client = self._client()
        source_bucket = client.bucket(self.bucket)
        source = source_bucket.blob(object_key)
        destination_key = f"quarantine/{object_key}"
        source_bucket.copy_blob(source, client.bucket(self.quarantine_bucket), destination_key)
        source.delete()
        return destination_key

    def local_path(self, object_key: str) -> None:
        return None


def get_storage_backend() -> StorageBackend:
    settings = get_settings()
    if settings.storage_backend == "gcs":
        return GCSStorage(settings.gcp_storage_bucket, settings.gcp_quarantine_bucket)
    return LocalFilesystemStorage(settings.local_storage_path)
