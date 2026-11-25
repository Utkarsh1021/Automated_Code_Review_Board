"""
models/finding.py

Defines the core data structures used by all agents:
  - Severity  — ordered enum for ranking findings
  - Finding   — a single issue reported by any specialist agent
"""

from __future__ import annotations

import uuid
from enum import IntEnum
from typing import Optional

from pydantic import BaseModel, Field, ConfigDict


class Severity(IntEnum):
    """Ordered severity levels. Higher value == more severe."""
    INFO = 1
    LOW = 2
    MEDIUM = 3
    HIGH = 4
    CRITICAL = 5

    @classmethod
    def from_str(cls, value: str) -> "Severity":
        """Case-insensitive factory from string."""
        mapping = {
            "info": cls.INFO,
            "low": cls.LOW,
            "medium": cls.MEDIUM,
            "high": cls.HIGH,
            "critical": cls.CRITICAL,
        }
        return mapping.get(value.lower(), cls.LOW)

    def label(self) -> str:
        """Human-readable coloured label (plain text version)."""
        labels = {
            Severity.CRITICAL: "🔴 CRITICAL",
            Severity.HIGH:     "🟠 HIGH",
            Severity.MEDIUM:   "🟡 MEDIUM",
            Severity.LOW:      "🔵 LOW",
            Severity.INFO:     "⚪ INFO",
        }
        return labels[self]


class Finding(BaseModel):
    """
    A single review finding produced by a specialist agent.

    Fields
    ------
    id          : unique identifier (UUID4, auto-generated)
    agent       : name of the agent that created this finding
    severity    : Severity enum value used for ranking & merge decision
    category    : short category tag, e.g. "security", "style", "coverage"
    title       : one-line summary of the issue
    description : detailed explanation
    file_path   : optional path to the affected file
    line_number : optional line number within that file
    code_snippet: optional verbatim code excerpt
    suggestion  : optional inline fix hint (brief; full patches come from FixSuggestionAgent)
    """

    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    agent: str
    severity: Severity
    category: str
    title: str
    description: str
    file_path: Optional[str] = None
    line_number: Optional[int] = None
    code_snippet: Optional[str] = None
    suggestion: Optional[str] = None

    # ── helpers ───────────────────────────────────────────────────────────────

    def dedup_key(self) -> str:
        """
        Returns a normalised key used for deduplication by the Reviewer Manager.
        Two findings with the same key (same file + title) are considered duplicates.
        """
        return f"{self.file_path or ''}::{self.title.lower().strip()}"

    def to_dict(self) -> dict:
        return self.model_dump()

    model_config = ConfigDict(use_enum_values=False)
