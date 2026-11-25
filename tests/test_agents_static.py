"""
tests/test_agents_static.py

Tests for the static (non-LLM) logic inside each agent:
  - DiffAgent: file path extraction, line counts, summary extraction
  - SecurityAgent: static regex scanner
  - TestCoverageAgent: static heuristics (no test files in diff)
  - ReviewerManager: deduplication, merge-recommendation logic

No OpenAI calls are made in these tests (all LLM methods are mocked).
"""

from __future__ import annotations

import pytest
from unittest.mock import AsyncMock, patch

from agents.diff_agent import DiffAgent
from agents.security_agent import SecurityAgent
from agents.test_coverage_agent import TestCoverageAgent
from agents.reviewer_manager import ReviewerManager
from models.finding import Finding, Severity
from models.shared_state import ChangeState, MergeRecommendation


# ─── Sample diff fixtures ─────────────────────────────────────────────────────

SAMPLE_DIFF = """\
diff --git a/app/auth.py b/app/auth.py
index 1234abc..5678def 100644
--- a/app/auth.py
+++ b/app/auth.py
@@ -1,5 +1,10 @@
 import os
+SECRET_KEY = "hardcoded_secret_123"
+password = "admin"
+
+def login(user, pw):
+    result = eval(user)
+    return result
diff --git a/app/utils.py b/app/utils.py
--- a/app/utils.py
+++ b/app/utils.py
@@ -1,3 +1,5 @@
 def helper():
+    pass
+import subprocess
+subprocess.run("ls", shell=True)
"""


# ─── DiffAgent static tests ───────────────────────────────────────────────────

class TestDiffAgentStatic:
    def setup_method(self):
        # Prevent kernel/LLM initialisation
        with patch("agents.base_agent.BaseAgent._build_kernel", return_value=None):
            self.agent = DiffAgent.__new__(DiffAgent)

    def test_extract_file_paths(self):
        paths = DiffAgent._extract_file_paths(SAMPLE_DIFF)
        assert "app/auth.py" in paths
        assert "app/utils.py" in paths

    def test_extract_file_paths_ignores_dev_null(self):
        diff = "--- /dev/null\n+++ b/new_file.py\n"
        paths = DiffAgent._extract_file_paths(diff)
        assert "/dev/null" not in paths
        assert "new_file.py" in paths

    def test_count_lines(self):
        additions, deletions = DiffAgent._count_lines(SAMPLE_DIFF)
        assert additions > 0
        assert deletions >= 0

    def test_extract_summary_from_response(self):
        raw = "SUMMARY:\nThis PR adds authentication.\n\nFINDINGS:\n```json\n[]\n```"
        summary = DiffAgent._extract_summary(raw)
        assert "authentication" in summary.lower()

    def test_extract_summary_fallback(self):
        raw = "No markers here just some text about the change."
        summary = DiffAgent._extract_summary(raw)
        assert len(summary) > 0


# ─── SecurityAgent static tests ──────────────────────────────────────────────

class TestSecurityAgentStatic:
    def setup_method(self):
        with patch("agents.base_agent.BaseAgent._build_kernel", return_value=None):
            self.agent = SecurityAgent.__new__(SecurityAgent)
            self.agent.name = "SecurityAgent"

    def test_detects_hardcoded_password(self):
        diff = "+password = 'hunter2'\n"
        findings = self.agent._static_scan(diff)
        titles = [f.title for f in findings]
        assert any("password" in t.lower() for t in titles)

    def test_detects_hardcoded_api_key(self):
        diff = '+api_key = "AKIAIOSFODNN7EXAMPLE"\n'
        findings = self.agent._static_scan(diff)
        # Should match AWS key or api_key pattern
        assert len(findings) >= 1

    def test_detects_eval(self):
        diff = "+    result = eval(user_input)\n"
        findings = self.agent._static_scan(diff)
        assert any("eval" in f.title.lower() for f in findings)

    def test_detects_pickle(self):
        diff = "+data = pickle.loads(raw_bytes)\n"
        findings = self.agent._static_scan(diff)
        assert any("pickle" in f.title.lower() for f in findings)

    def test_detects_subprocess_shell(self):
        diff = "+subprocess.run(cmd, shell=True)\n"
        findings = self.agent._static_scan(diff)
        assert any("subprocess" in f.title.lower() for f in findings)

    def test_ignores_deleted_lines(self):
        # Lines starting with '-' are deletions — should NOT be flagged
        diff = "-password = 'hunter2'\n"
        findings = self.agent._static_scan(diff)
        assert len(findings) == 0

    def test_ignores_context_lines(self):
        diff = " password = 'hunter2'\n"
        findings = self.agent._static_scan(diff)
        assert len(findings) == 0

    def test_detects_md5(self):
        diff = "+h = hashlib.md5(data.encode()).hexdigest()\n"
        findings = self.agent._static_scan(diff)
        assert any("md5" in f.title.lower() for f in findings)

    def test_severity_is_critical_for_password(self):
        diff = '+PASSWORD = "mypass123"\n'
        findings = self.agent._static_scan(diff)
        # At least one finding should be CRITICAL or HIGH
        max_sev = max((f.severity for f in findings), default=Severity.INFO)
        assert max_sev >= Severity.HIGH


# ─── TestCoverageAgent static tests ──────────────────────────────────────────

class TestCoverageAgentStatic:
    def setup_method(self):
        with patch("agents.base_agent.BaseAgent._build_kernel", return_value=None):
            self.agent = TestCoverageAgent.__new__(TestCoverageAgent)
            self.agent.name = "TestCoverageAgent"

    def _state_with_files(self, file_paths, diff=""):
        state = ChangeState(diff_text=diff or "+def foo(): pass\n")
        state.file_paths = file_paths
        return state

    def test_no_test_files_emits_high_finding(self):
        state = self._state_with_files(["app/auth.py", "app/utils.py"])
        findings = self.agent._static_check(state)
        severities = [f.severity for f in findings]
        assert Severity.HIGH in severities

    def test_test_file_present_no_flag(self):
        state = self._state_with_files(
            ["app/auth.py", "tests/test_auth.py"],
            diff="+def foo(): pass\n",
        )
        findings = self.agent._static_check(state)
        # Should not flag missing tests if test file present
        high_findings = [f for f in findings if f.severity == Severity.HIGH
                         and "no test" in f.title.lower()]
        assert len(high_findings) == 0

    def test_new_functions_without_tests_flagged(self):
        diff = "+def new_function(x):\n+    return x * 2\n"
        state = self._state_with_files(["app/core.py"], diff=diff)
        findings = self.agent._static_check(state)
        titles = " ".join(f.title.lower() for f in findings)
        assert "symbol" in titles or "test" in titles


# ─── ReviewerManager logic tests (no LLM) ────────────────────────────────────

class TestReviewerManagerLogic:
    def setup_method(self):
        # Create ReviewerManager without initialising agent kernels
        with patch("agents.base_agent.BaseAgent._build_kernel", return_value=None):
            self.rm = ReviewerManager.__new__(ReviewerManager)

    def _finding(self, severity: Severity, title="issue", file="f.py") -> Finding:
        return Finding(
            agent="X", severity=severity, category="test",
            title=title, description="desc", file_path=file,
        )

    # ── Deduplication ─────────────────────────────────────────────────────────

    def test_dedup_removes_exact_duplicates(self):
        f1 = self._finding(Severity.MEDIUM, "Same title")
        f2 = self._finding(Severity.MEDIUM, "Same title")
        result = ReviewerManager._deduplicate([f1, f2])
        assert len(result) == 1

    def test_dedup_keeps_highest_severity(self):
        f_low  = self._finding(Severity.LOW,    "Same title")
        f_high = self._finding(Severity.HIGH,   "Same title")
        result = ReviewerManager._deduplicate([f_low, f_high])
        assert len(result) == 1
        assert result[0].severity == Severity.HIGH

    def test_dedup_different_titles_kept(self):
        f1 = self._finding(Severity.MEDIUM, "Issue A")
        f2 = self._finding(Severity.MEDIUM, "Issue B")
        result = ReviewerManager._deduplicate([f1, f2])
        assert len(result) == 2

    def test_dedup_different_files_kept(self):
        f1 = Finding(agent="X", severity=Severity.MEDIUM, category="c",
                     title="Same", description="d", file_path="a.py")
        f2 = Finding(agent="X", severity=Severity.MEDIUM, category="c",
                     title="Same", description="d", file_path="b.py")
        result = ReviewerManager._deduplicate([f1, f2])
        assert len(result) == 2

    def test_dedup_result_sorted_descending(self):
        findings = [
            self._finding(Severity.LOW,      "A", "a.py"),
            self._finding(Severity.CRITICAL, "B", "b.py"),
            self._finding(Severity.MEDIUM,   "C", "c.py"),
        ]
        result = ReviewerManager._deduplicate(findings)
        assert result[0].severity == Severity.CRITICAL
        assert result[-1].severity == Severity.LOW

    # ── Merge recommendation ──────────────────────────────────────────────────

    def test_critical_triggers_request_changes(self):
        findings = [self._finding(Severity.CRITICAL)]
        rec, reason = ReviewerManager._decide(findings)
        assert rec == MergeRecommendation.REQUEST_CHANGES
        assert "CRITICAL" in reason

    def test_high_triggers_request_changes(self):
        findings = [self._finding(Severity.HIGH)]
        rec, _ = ReviewerManager._decide(findings)
        assert rec == MergeRecommendation.REQUEST_CHANGES

    def test_medium_only_triggers_comment(self):
        findings = [self._finding(Severity.MEDIUM)]
        rec, _ = ReviewerManager._decide(findings)
        assert rec == MergeRecommendation.COMMENT

    def test_low_only_triggers_approve(self):
        findings = [self._finding(Severity.LOW)]
        rec, _ = ReviewerManager._decide(findings)
        assert rec == MergeRecommendation.APPROVE

    def test_no_findings_triggers_approve(self):
        rec, _ = ReviewerManager._decide([])
        assert rec == MergeRecommendation.APPROVE

    def test_mixed_critical_high_still_request_changes(self):
        findings = [
            self._finding(Severity.LOW),
            self._finding(Severity.HIGH, "B", "b.py"),
            self._finding(Severity.CRITICAL, "C", "c.py"),
        ]
        rec, reason = ReviewerManager._decide(findings)
        assert rec == MergeRecommendation.REQUEST_CHANGES
        assert "CRITICAL" in reason
