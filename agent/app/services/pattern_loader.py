from __future__ import annotations

import json
import os
from dataclasses import dataclass
from typing import Iterable, List, Protocol

from agent.app.config import Settings
from agent.app.models.pattern import ArchitecturePattern


class PatternRepository(Protocol):
    def list_patterns(self) -> List[ArchitecturePattern]: ...


@dataclass(frozen=True)
class FilesystemPatternRepository:
    patterns_path: str

    def list_patterns(self) -> List[ArchitecturePattern]:
        patterns: List[ArchitecturePattern] = []
        if not os.path.isdir(self.patterns_path):
            return patterns

        for name in sorted(os.listdir(self.patterns_path)):
            if not name.endswith(".json"):
                continue
            path = os.path.join(self.patterns_path, name)
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            patterns.append(ArchitecturePattern.model_validate(data))
        return patterns


def get_pattern_repository(settings: Settings) -> PatternRepository:
    """
    MVP uses filesystem loading, but we keep the seam for Postgres.

    A Postgres implementation would satisfy PatternRepository and be injected
    here based on settings.pattern_store.
    """

    if settings.pattern_store == "filesystem":
        return FilesystemPatternRepository(patterns_path=settings.patterns_path)

    # Designed-for-Postgres behavior: fail fast with a helpful message.
    raise RuntimeError(
        "pattern_store=postgres is not yet implemented in the MVP. "
        "Set ARCHAGENT_PATTERN_STORE=filesystem or implement a Postgres repository."
    )


def load_patterns(settings: Settings) -> List[ArchitecturePattern]:
    return get_pattern_repository(settings).list_patterns()

