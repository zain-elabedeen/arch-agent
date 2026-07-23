"""Document scanning adapters."""

from __future__ import annotations

from pathlib import Path
from typing import Protocol

import requests

from agent.app.config import get_settings

ALLOWED_EXTENSIONS = {".pdf", ".md", ".txt", ".docx"}
MAX_DOCUMENT_BYTES = 25 * 1024 * 1024


class DocumentScanner(Protocol):
    def scan(self, filename: str, content: bytes) -> None: ...


def _validate_upload(filename: str, content: bytes) -> None:
    if Path(filename).suffix.lower() not in ALLOWED_EXTENSIONS:
        raise ValueError("Unsupported document format. Use PDF, Markdown, TXT, or DOCX.")
    if len(content) > MAX_DOCUMENT_BYTES:
        raise ValueError("Document exceeds the 25 MB upload limit.")


class DevAllowScanner:
    def scan(self, filename: str, content: bytes) -> None:
        _validate_upload(filename, content)


class ClamAVScanner:
    """Call the private scanner service before extraction or indexing."""

    def __init__(self, service_url: str | None, token: str | None):
        if not service_url:
            raise RuntimeError("ClamAV scanning requires ARCHAGENT_SCANNER_SERVICE_URL.")
        self.service_url = service_url.rstrip("/")
        self.token = token

    def scan(self, filename: str, content: bytes) -> None:
        _validate_upload(filename, content)
        headers = {"Authorization": f"Bearer {self.token}"} if self.token else {}
        response = requests.post(
            f"{self.service_url}/scan",
            files={"file": (Path(filename).name, content)},
            headers=headers,
            timeout=30,
        )
        if response.status_code != 200:
            raise RuntimeError("Document scanner rejected the upload.")
        if not bool(response.json().get("clean")):
            raise ValueError("Document failed malware scanning.")


def get_document_scanner() -> DocumentScanner:
    settings = get_settings()
    if settings.document_scan_mode == "clamav":
        return ClamAVScanner(settings.scanner_service_url, settings.scanner_service_token)
    return DevAllowScanner()
