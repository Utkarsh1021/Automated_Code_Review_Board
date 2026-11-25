"""
models/shared_state.py

ChangeState  — the shared mutable object passed to every agent.
               Agents read the diff and write their findings into it.

ConsolidatedReview — the final output produced by the Reviewer Manager.

Patch        — a proposed fix from the Fix-Suggestion Agent.
"""

from __future__ import annotations

import threading
from typing import List, Optional

from pydantic import BaseModel, Field, ConfigDict, PrivateAttr

from .finding import Finding, Severity


# ─── Patch ────────────────────────────────────────────────────────────────────

class Patch(BaseModel):
    """Concrete fix proposed by the Fix-Suggestion Agent."""
    finding_id: str                   # ID of the Finding being fixed
    finding_title: str
    file_path: Optional[str] = None
    unified_diff: str = ""            # git-style unified diff
    explanation: str = ""             # why this patch fixes the issue


# ─── ChangeState ──────────────────────────────────────────────────────────────

class ChangeState(BaseModel):
    """
    Shared state object for one code-review run.

    Agents read `diff_text` and append to `findings`.
    The Reviewer Manager reads all findings to build the final review.

    Thread-safety: `add_finding()` and `add_findings()` use an internal lock
    so that agents running concurrently via asyncio / threads can safely write.
    """

    # ── Input ─────────────────────────────────────────────────────────────────
    diff_text: str = ""               # raw unified diff (from git diff / PR)
    pr_title: str = ""               # optional PR / commit title
    pr_description: str = ""         # optional PR description

    # ── Extracted by DiffAgent ───────────────────────────────────────────────
    file_paths: List[str] = Field(default_factory=list)
    additions: int = 0
    deletions: int = 0
    diff_summary: str = ""           # human-readable summary from DiffAgent

    # ── Accumulated by specialist agents ─────────────────────────────────────
    findings: List[Finding] = Field(default_factory=list)

    # ── Produced by FixSuggestionAgent ───────────────────────────────────────
    patch_suggestion: Optional[Patch] = None

    # ── Internal ─────────────────────────────────────────────────────────────
    _lock: threading.Lock = PrivateAttr(default_factory=threading.Lock)

    model_config = ConfigDict(arbitrary_types_allowed=True)

    # ── Thread-safe writers ───────────────────────────────────────────────────

    def add_finding(self, finding: Finding) -> None:
        with self._lock:
            self.findings.append(finding)

    def add_findings(self, new_findings: List[Finding]) -> None:
        with self._lock:
            self.findings.extend(new_findings)

    # ── Convenience readers ───────────────────────────────────────────────────

    def sorted_findings(self) -> List[Finding]:
        """Return findings sorted descending by severity."""
        return sorted(self.findings, key=lambda f: f.severity, reverse=True)

    def top_finding(self) -> Optional[Finding]:
        """Return the single most severe finding, or None."""
        fs = self.sorted_findings()
        return fs[0] if fs else None


# ─── ConsolidatedReview ───────────────────────────────────────────────────────

class MergeRecommendation(str):
    APPROVE = "APPROVE"
    COMMENT = "COMMENT"
    REQUEST_CHANGES = "REQUEST_CHANGES"


class ConsolidatedReview(BaseModel):
    """Final output of the entire review pipeline."""

    pr_title: str = ""
    diff_summary: str = ""

    # Grouped findings: key = severity label string, value = list of findings
    findings_by_severity: dict = Field(default_factory=dict)

    # Flat sorted list (for iteration)
    all_findings: List[Finding] = Field(default_factory=list)

    # Top finding fix
    patch_suggestion: Optional[Patch] = None

    # Verdict
    merge_recommendation: str = MergeRecommendation.APPROVE
    recommendation_reason: str = ""

    # Stats
    total_findings: int = 0
    critical_count: int = 0
    high_count: int = 0
    medium_count: int = 0
    low_count: int = 0
    info_count: int = 0
