"""
agents/reviewer_manager.py

Reviewer Manager — the orchestrator of the entire review pipeline.

Architecture: Fan-out / Fan-in

  Fan-out phase:
    Runs DiffAgent first (its summary is used by later agents),
    then fans out StyleAgent, SecurityAgent, TestCoverageAgent in parallel.

  Fan-in phase:
    Collects all findings, deduplicates by (file, title) key,
    sorts by severity (descending), then invokes FixSuggestionAgent on the top finding.

  Decision:
    Applies the merge-recommendation logic and builds the final ConsolidatedReview.
"""

from __future__ import annotations

import asyncio
import logging
from collections import defaultdict
from typing import Dict, List

from agents.diff_agent import DiffAgent
from agents.fix_suggestion_agent import FixSuggestionAgent
from agents.security_agent import SecurityAgent
from agents.style_agent import StyleAgent
from agents.test_coverage_agent import TestCoverageAgent
from config import config
from models.finding import Finding, Severity
from models.shared_state import ChangeState, ConsolidatedReview, MergeRecommendation

logger = logging.getLogger(__name__)


class ReviewerManager:
    """
    Orchestrates the full fan-out / fan-in review pipeline.

    Usage::

        manager = ReviewerManager()
        state   = ChangeState(diff_text=..., pr_title=...)
        review  = await manager.run(state)
    """

    def __init__(self):
        self.diff_agent        = DiffAgent()
        self.style_agent       = StyleAgent()
        self.security_agent    = SecurityAgent()
        self.test_agent        = TestCoverageAgent()
        self.fix_agent         = FixSuggestionAgent()

    # ── Main entry point ──────────────────────────────────────────────────────

    async def run(self, state: ChangeState) -> ConsolidatedReview:
        """
        Execute the full pipeline and return a ConsolidatedReview.

        Pipeline steps:
          1. DiffAgent  — parses diff, sets state.diff_summary
          2. [parallel] StyleAgent, SecurityAgent, TestCoverageAgent
          3. Fan-in     — deduplicate & sort findings
          4. FixSuggestionAgent — patch for top finding
          5. Build ConsolidatedReview
        """
        logger.info("=== Reviewer Manager: starting review pipeline ===")

        # ── Step 1: Diff Agent (sequential — others depend on diff_summary) ──
        logger.info("[1/4] Running DiffAgent …")
        await self.diff_agent.run(state)
        logger.info(
            "      DiffAgent done. Files: %s, +%d/-%d",
            len(state.file_paths), state.additions, state.deletions,
        )

        # ── Step 2: Fan-out — specialist agents in parallel ───────────────────
        logger.info("[2/4] Fan-out: StyleAgent, SecurityAgent, TestCoverageAgent …")
        await asyncio.gather(
            self._run_agent(self.style_agent,    state, "StyleAgent"),
            self._run_agent(self.security_agent, state, "SecurityAgent"),
            self._run_agent(self.test_agent,     state, "TestCoverageAgent"),
        )
        logger.info("      Fan-out complete. Raw findings: %d", len(state.findings))

        # ── Step 3: Fan-in — deduplicate & sort ───────────────────────────────
        logger.info("[3/4] Fan-in: deduplication & ranking …")
        state.findings = self._deduplicate(state.findings)
        logger.info("      After dedup: %d findings", len(state.findings))

        # ── Step 4: Fix-Suggestion Agent ──────────────────────────────────────
        logger.info("[4/4] Running FixSuggestionAgent …")
        await self._run_agent(self.fix_agent, state, "FixSuggestionAgent")

        # ── Step 5: Build the consolidated review ─────────────────────────────
        review = self._build_review(state)
        logger.info(
            "=== Review complete: %s | %d findings ===",
            review.merge_recommendation, review.total_findings,
        )
        return review

    # ── Helper: safe agent runner ─────────────────────────────────────────────

    @staticmethod
    async def _run_agent(agent, state: ChangeState, label: str) -> None:
        """Run an agent, catching and logging any exception so others still run."""
        try:
            await agent.run(state)
        except Exception as exc:
            logger.error("Agent %s failed: %s", label, exc, exc_info=True)
            # Inject a placeholder finding so the manager knows something went wrong
            from models.finding import Finding, Severity
            state.add_finding(
                Finding(
                    agent=label,
                    severity=Severity.LOW,
                    category="system",
                    title=f"{label} encountered an error",
                    description=str(exc),
                    suggestion="Check agent logs for details.",
                )
            )

    # ── Deduplication ─────────────────────────────────────────────────────────

    @staticmethod
    def _deduplicate(findings: List[Finding]) -> List[Finding]:
        """
        Remove duplicate findings.

        Deduplication strategy:
          - Group by dedup_key() = (file_path, normalised_title)
          - Keep the finding with the highest severity in each group
          - Return sorted descending by severity
        """
        seen: Dict[str, Finding] = {}
        for f in findings:
            key = f.dedup_key()
            if key not in seen or f.severity > seen[key].severity:
                seen[key] = f
        return sorted(seen.values(), key=lambda f: f.severity, reverse=True)

    # ── Merge recommendation ──────────────────────────────────────────────────

    @staticmethod
    def _decide(findings: List[Finding]) -> tuple[str, str]:
        """
        Apply the merge-recommendation policy.

        Returns (recommendation, reason).
        """
        blocking_threshold = Severity.from_str(config.BLOCKING_SEVERITY)

        # Count by severity
        counts = defaultdict(int)
        for f in findings:
            counts[f.severity] += 1

        critical = counts[Severity.CRITICAL]
        high     = counts[Severity.HIGH]
        medium   = counts[Severity.MEDIUM]
        low      = counts[Severity.LOW]

        if critical > 0:
            return (
                MergeRecommendation.REQUEST_CHANGES,
                f"❌ {critical} CRITICAL finding(s) must be resolved before merging.",
            )
        if high > 0:
            return (
                MergeRecommendation.REQUEST_CHANGES,
                f"⚠️  {high} HIGH-severity finding(s) require attention before merging.",
            )
        if medium > 0:
            return (
                MergeRecommendation.COMMENT,
                f"💬 {medium} MEDIUM finding(s) noted. Approve with comments — address before next PR.",
            )
        if low > 0:
            return (
                MergeRecommendation.APPROVE,
                f"✅ Only LOW/INFO findings ({low} low). Safe to merge with minor notes.",
            )
        return (
            MergeRecommendation.APPROVE,
            "✅ No significant findings. Safe to merge.",
        )

    # ── Build ConsolidatedReview ──────────────────────────────────────────────

    def _build_review(self, state: ChangeState) -> ConsolidatedReview:
        sorted_findings = state.sorted_findings()
        recommendation, reason = self._decide(sorted_findings)

        # Group by severity label
        grouped: Dict[str, List[Finding]] = defaultdict(list)
        for f in sorted_findings:
            grouped[f.severity.label()].append(f)

        # Count by severity
        counts = defaultdict(int)
        for f in sorted_findings:
            counts[f.severity] += 1

        return ConsolidatedReview(
            pr_title=state.pr_title,
            diff_summary=state.diff_summary,
            findings_by_severity=dict(grouped),
            all_findings=sorted_findings,
            patch_suggestion=state.patch_suggestion,
            merge_recommendation=recommendation,
            recommendation_reason=reason,
            total_findings=len(sorted_findings),
            critical_count=counts[Severity.CRITICAL],
            high_count=counts[Severity.HIGH],
            medium_count=counts[Severity.MEDIUM],
            low_count=counts[Severity.LOW],
            info_count=counts[Severity.INFO],
        )
