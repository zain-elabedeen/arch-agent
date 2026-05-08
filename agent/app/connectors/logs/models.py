"""Source-neutral raw log input types used by log connector implementations."""

from __future__ import annotations

from dataclasses import dataclass
from typing import List


@dataclass(frozen=True)
class RawLogBatch:
    """
    Raw log payload for one source resource.

    ``resource`` is the source-local unit, such as a Kubernetes pod today or a
    Cloud Run revision / VM instance / Datadog service stream later.
    """

    source: str
    service: str
    namespace: str
    resource: str
    container: str | None
    lines: List[str]
    read_error: str | None = None
