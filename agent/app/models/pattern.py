from __future__ import annotations

from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, Field


Impact = Literal["low", "medium", "high"]
Effort = Literal["low", "medium", "high"]
Confidence = Literal["low", "medium", "high"]


class PatternConstraint(BaseModel):
    """
    Constraints are intentionally simple for the MVP: a structured predicate
    expressed as "signal/operator/value" or a required topology property.

    This avoids free-form text evaluation, keeps critic logic deterministic,
    and remains explainable.
    """

    kind: Literal["signal", "topology"] = "signal"
    key: str
    operator: Literal["gt", "gte", "lt", "lte", "eq", "neq", "exists"] = "exists"
    value: Optional[float] = None
    message: str = ""


class ArchitecturePattern(BaseModel):
    id: str
    name: str
    category: str
    summary: str

    use_when: List[PatternConstraint] = Field(default_factory=list)
    avoid_when: List[PatternConstraint] = Field(default_factory=list)

    # Concrete, copy/paste-able suggestions. MVP keeps these as short strings.
    solutions: List[str] = Field(default_factory=list)
    tradeoffs: List[str] = Field(default_factory=list)

    impact: Impact = "medium"
    effort: Effort = "medium"
    confidence: Confidence = "medium"

    tags: List[str] = Field(default_factory=list)
    metadata: Dict[str, Any] = Field(default_factory=dict)

