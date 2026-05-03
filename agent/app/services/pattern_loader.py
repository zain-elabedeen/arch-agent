from __future__ import annotations

import json
import os
from dataclasses import dataclass
from typing import Dict, List, Protocol

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


SMELL_TO_PATTERN: Dict[str, List[str]] = {
    "read_scaling_bottleneck": ["read_replicas", "caching"],
    "queue_backlog": ["queue_partitioning"],
}


@dataclass
class PatternStore:
    patterns_path: str
    patterns: Dict[str, ArchitecturePattern]

    @classmethod
    def load_patterns(cls, patterns_path: str) -> "PatternStore":
        """
        Load all JSON pattern files from app/patterns into a dict keyed by id.
        """
        loaded: Dict[str, ArchitecturePattern] = {}
        if not os.path.isdir(patterns_path):
            return cls(patterns_path=patterns_path, patterns=loaded)

        for name in sorted(os.listdir(patterns_path)):
            if not name.endswith(".json"):
                continue
            path = os.path.join(patterns_path, name)
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            pattern = ArchitecturePattern.model_validate(data)
            loaded[pattern.id] = pattern
        return cls(patterns_path=patterns_path, patterns=loaded)

    def get_by_id(self, pattern_id: str) -> ArchitecturePattern | None:
        return self.patterns.get(pattern_id)

    def get_all(self) -> List[ArchitecturePattern]:
        return list(self.patterns.values())

    def get_patterns_for_smell(self, smell_type: str) -> List[ArchitecturePattern]:
        pattern_ids = SMELL_TO_PATTERN.get(smell_type, [])
        if not pattern_ids:
            return []
        return [self.patterns[pid] for pid in pattern_ids if pid in self.patterns]


def load_pattern_store(settings: Settings) -> PatternStore:
    return PatternStore.load_patterns(settings.patterns_path)

