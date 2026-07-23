"""Small private ClamAV HTTP adapter for uploaded document scanning."""

from __future__ import annotations

import os
import subprocess
import tempfile
from pathlib import Path

from fastapi import FastAPI, File, Header, HTTPException, UploadFile

app = FastAPI(title="ArchAgent Scanner")


def _authorize(authorization: str | None) -> None:
    expected = os.environ.get("SCANNER_SERVICE_TOKEN")
    if expected and authorization != f"Bearer {expected}":
        raise HTTPException(status_code=401, detail="Scanner token is required.")


@app.get("/healthz")
def healthz() -> dict[str, bool]:
    return {"ok": True}


@app.post("/scan")
async def scan(file: UploadFile = File(...), authorization: str | None = Header(default=None)) -> dict[str, bool]:
    _authorize(authorization)
    suffix = Path(file.filename or "upload.bin").suffix
    with tempfile.NamedTemporaryFile(suffix=suffix) as temp:
        temp.write(await file.read())
        temp.flush()
        result = subprocess.run(
            ["clamscan", "--no-summary", temp.name],
            check=False,
            capture_output=True,
            text=True,
            timeout=30,
        )
    if result.returncode not in {0, 1}:
        raise HTTPException(status_code=503, detail="Scanner unavailable.")
    return {"clean": result.returncode == 0}
