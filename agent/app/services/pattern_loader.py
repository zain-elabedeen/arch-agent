from __future__ import annotations

import json
import os
from dataclasses import dataclass
from typing import Dict, List, Protocol, TypedDict

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
        "Set AGENT_PATTERN_STORE=filesystem or implement a Postgres repository."
    )


def load_patterns(settings: Settings) -> List[ArchitecturePattern]:
    return get_pattern_repository(settings).list_patterns()


class MappedPattern(TypedDict):
    pattern: str
    priority: int
    reason: str


SMELL_TO_PATTERN_MAP: Dict[str, List[MappedPattern]] = {
    "read_scaling_bottleneck": [
        {"pattern": "read_replicas", "priority": 1, "reason": "Distribute read load"},
        {"pattern": "caching", "priority": 2, "reason": "Reduce database reads"},
        {"pattern": "load_balancing", "priority": 3, "reason": "Balance traffic across instances"},
    ],
    "cpu_saturation": [
        {"pattern": "horizontal_scaling", "priority": 1, "reason": "Increase compute capacity"},
        {"pattern": "load_balancing", "priority": 2, "reason": "Distribute load evenly"},
        {"pattern": "connection_pooling", "priority": 3, "reason": "Reduce connection overhead"},
    ],
    "queue_backlog": [
        {"pattern": "queue_partitioning", "priority": 1, "reason": "Increase throughput"},
        {"pattern": "backpressure", "priority": 2, "reason": "Prevent overload"},
        {"pattern": "async_processing", "priority": 3, "reason": "Offload work to background workers"},
    ],
    "coupling_risk": [
        {"pattern": "service_decomposition", "priority": 1, "reason": "Reduce coupling by splitting responsibilities"},
        {"pattern": "api_gateway", "priority": 2, "reason": "Centralize cross-cutting concerns at the edge"},
    ],
    "high_error_rate": [
        {"pattern": "circuit_breaker", "priority": 1, "reason": "Prevent cascading failures"},
        {"pattern": "retry_with_backoff", "priority": 2, "reason": "Handle transient errors safely"},
        {"pattern": "bulkhead", "priority": 3, "reason": "Isolate failures and resource contention"},
    ],
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
        ranked = self.get_ranked_patterns_for_smell(smell_type)
        return [item["pattern"] for item in ranked]

    def get_ranked_patterns_for_smell(self, smell_type: str) -> List[Dict[str, object]]:
        mapping = SMELL_TO_PATTERN_MAP.get(smell_type, [])
        results: List[Dict[str, object]] = []
        for item in mapping:
            pattern_obj = self.get_by_id(item["pattern"])
            if pattern_obj:
                results.append(
                    {
                        "pattern": pattern_obj,
                        "priority": item["priority"],
                        "reason": item["reason"],
                    }
                )
        return sorted(results, key=lambda x: int(x["priority"]))


def load_pattern_store(settings: Settings) -> PatternStore:
    return PatternStore.load_patterns(settings.patterns_path)

