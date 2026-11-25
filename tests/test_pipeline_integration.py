"""
tests/test_pipeline_integration.py

Integration test for the full review pipeline using mocked LLM responses.
Verifies the fan-out / fan-in coordination, deduplication, and that
the final ConsolidatedReview is correctly assembled.

No real OpenAI API calls are made.
Uses asyncio.run() directly — no pytest-asyncio required.
"""

from __future__ import annotations

import asyncio
import json
from unittest.mock import patch, MagicMock

import pytest

from agents.reviewer_manager import ReviewerManager
from models.finding import Finding, Severity
from models.shared_state import ChangeState, MergeRecommendation


# ─── Sample diff ──────────────────────────────────────────────────────────────

MOCK_DIFF = """\
diff --git a/app/auth.py b/app/auth.py
--- a/app/auth.py
+++ b/app/auth.py
@@ -1,3 +1,8 @@
 import os
+SECRET_KEY = "hardcoded_secret_xyz"
+
+def login(user, pw):
+    result = eval(user)
+    return result
"""

# ─── Mock LLM response helpers ────────────────────────────────────────────────

DIFF_RESPONSE = (
    "SUMMARY:\nThis PR adds a login function and hard-codes a secret key.\n\n"
    "FINDINGS:\n```json\n"
    '[{"severity":"INFO","category":"diff","title":"Added login function",'
    '"description":"A new login function was introduced.","file_path":"app/auth.py",'
    '"line_number":null,"code_snippet":null,"suggestion":null}]\n```'
)

STYLE_RESPONSE = json.dumps([{
    "severity": "LOW",
    "category": "style",
    "title": "Missing docstring on login()",
    "description": "The login function lacks a docstring.",
    "file_path": "app/auth.py",
    "line_number": 4,
    "code_snippet": "def login(user, pw):",
    "suggestion": "Add a docstring.",
}])

SECURITY_RESPONSE = json.dumps([{
    "severity": "CRITICAL",
    "category": "security",
    "title": "Arbitrary code execution via eval()",
    "description": "eval() on user input enables arbitrary code execution.",
    "file_path": "app/auth.py",
    "line_number": 5,
    "code_snippet": "result = eval(user)",
    "suggestion": "Never use eval() on user-supplied input.",
}])

TEST_RESPONSE = json.dumps([{
    "severity": "HIGH",
    "category": "test-coverage",
    "title": "No tests for login()",
    "description": "The new login function has no unit tests.",
    "file_path": "app/auth.py",
    "line_number": None,
    "code_snippet": None,
    "suggestion": "Add tests/test_auth.py.",
}])

FIX_RESPONSE = (
    "EXPLANATION:\nReplace eval() with safe input handling.\n\n"
    "PATCH:\n```diff\n"
    "--- a/app/auth.py\n"
    "+++ b/app/auth.py\n"
    "@@ -4,2 +4,3 @@\n"
    " def login(user, pw):\n"
    "-    result = eval(user)\n"
    "+    result = str(user).strip()\n"
    "     return result\n"
    "```"
)


# ─── Helper: build a manager with mocked _chat methods ────────────────────────

def _make_manager() -> ReviewerManager:
    with patch("agents.base_agent.BaseAgent._build_kernel", return_value=MagicMock()):
        manager = ReviewerManager()

    async def diff_chat(_):    return DIFF_RESPONSE
    async def style_chat(_):   return STYLE_RESPONSE
    async def sec_chat(_):     return SECURITY_RESPONSE
    async def test_chat(_):    return TEST_RESPONSE
    async def fix_chat(_):     return FIX_RESPONSE

    manager.diff_agent._chat     = diff_chat
    manager.style_agent._chat    = style_chat
    manager.security_agent._chat = sec_chat
    manager.test_agent._chat     = test_chat
    manager.fix_agent._chat      = fix_chat
    return manager


def _run(coro):
    """Helper: run a coroutine synchronously in its own event loop."""
    return asyncio.get_event_loop().run_until_complete(coro)


# ─── Integration tests ────────────────────────────────────────────────────────

class TestPipelineIntegration:

    def setup_method(self):
        self.manager = _make_manager()
        self.state   = ChangeState(
            diff_text=MOCK_DIFF,
            pr_title="feat: add login",
            pr_description="Adds login function.",
        )

    def _run_pipeline(self):
        return _run(self.manager.run(self.state))

    def test_pipeline_runs_to_completion(self):
        review = self._run_pipeline()
        assert review is not None

    def test_diff_summary_populated(self):
        self._run_pipeline()
        assert len(self.state.diff_summary) > 0
        assert "login" in self.state.diff_summary.lower()

    def test_file_paths_extracted(self):
        self._run_pipeline()
        assert "app/auth.py" in self.state.file_paths

    def test_findings_collected_from_all_agents(self):
        review = self._run_pipeline()
        agent_names = {f.agent for f in review.all_findings}
        assert "StyleAgent"        in agent_names
        assert "SecurityAgent"     in agent_names
        assert "TestCoverageAgent" in agent_names

    def test_critical_finding_triggers_request_changes(self):
        review = self._run_pipeline()
        assert review.merge_recommendation == MergeRecommendation.REQUEST_CHANGES

    def test_findings_sorted_by_severity_descending(self):
        review = self._run_pipeline()
        severities = [f.severity for f in review.all_findings]
        assert severities == sorted(severities, reverse=True)

    def test_patch_suggestion_generated(self):
        review = self._run_pipeline()
        assert review.patch_suggestion is not None
        combined = (review.patch_suggestion.unified_diff + review.patch_suggestion.explanation).lower()
        assert "eval" in combined or "safe" in combined or "replace" in combined

    def test_counts_match_findings(self):
        review = self._run_pipeline()
        total = (review.critical_count + review.high_count +
                 review.medium_count + review.low_count + review.info_count)
        assert total == review.total_findings

    def test_findings_grouped_by_severity(self):
        review = self._run_pipeline()
        grouped_labels = list(review.findings_by_severity.keys())
        assert any("CRITICAL" in label for label in grouped_labels)

    def test_duplicate_findings_deduplicated(self):
        f1 = Finding(agent="A", severity=Severity.HIGH, category="sec",
                     title="Duplicate finding", description="d", file_path="f.py")
        f2 = Finding(agent="B", severity=Severity.MEDIUM, category="sec",
                     title="Duplicate finding", description="d", file_path="f.py")
        result = ReviewerManager._deduplicate([f1, f2])
        assert len(result) == 1
        assert result[0].severity == Severity.HIGH


# ─── Edge case tests ──────────────────────────────────────────────────────────

class TestPipelineEdgeCases:

    def _make_empty_manager(self) -> ReviewerManager:
        with patch("agents.base_agent.BaseAgent._build_kernel", return_value=MagicMock()):
            manager = ReviewerManager()

        async def empty(_): return "[]"
        async def diff_empty(_):
            return "SUMMARY:\nMinor change.\n\nFINDINGS:\n```json\n[]\n```"

        manager.diff_agent._chat     = diff_empty
        manager.style_agent._chat    = empty
        manager.security_agent._chat = empty
        manager.test_agent._chat     = empty
        manager.fix_agent._chat      = empty
        return manager

    def test_empty_diff_does_not_crash(self):
        manager = self._make_empty_manager()
        state = ChangeState(diff_text="", pr_title="empty")
        review = _run(manager.run(state))
        assert review is not None

    def test_all_info_findings_gives_approve(self):
        with patch("agents.base_agent.BaseAgent._build_kernel", return_value=MagicMock()):
            manager = ReviewerManager()

        info_response = json.dumps([{
            "severity": "INFO", "category": "diff", "title": "Minor note",
            "description": "d", "file_path": None,
            "line_number": None, "code_snippet": None, "suggestion": None,
        }])

        async def info_chat(_): return info_response
        async def fix_empty(_): return "EXPLANATION:\nNothing to fix.\nPATCH:\n```diff\n```"

        for agent in [manager.diff_agent, manager.style_agent,
                      manager.security_agent, manager.test_agent]:
            agent._chat = info_chat
        manager.fix_agent._chat = fix_empty

        state = ChangeState(diff_text="+ x = 1\n", pr_title="tiny")
        review = _run(manager.run(state))
        assert review.merge_recommendation == MergeRecommendation.APPROVE
