"""
tests/test_models.py

Unit tests for Finding, Severity, ChangeState, and ConsolidatedReview models.
No LLM calls needed — purely tests data model logic.
"""

import pytest
from models.finding import Finding, Severity
from models.shared_state import ChangeState, Patch, ConsolidatedReview


# ─── Severity tests ───────────────────────────────────────────────────────────

class TestSeverity:
    def test_ordering(self):
        assert Severity.CRITICAL > Severity.HIGH
        assert Severity.HIGH     > Severity.MEDIUM
        assert Severity.MEDIUM   > Severity.LOW
        assert Severity.LOW      > Severity.INFO

    def test_from_str_case_insensitive(self):
        assert Severity.from_str("critical") == Severity.CRITICAL
        assert Severity.from_str("CRITICAL") == Severity.CRITICAL
        assert Severity.from_str("Critical") == Severity.CRITICAL

    def test_from_str_all_values(self):
        for name in ["INFO", "LOW", "MEDIUM", "HIGH", "CRITICAL"]:
            assert Severity.from_str(name).name == name

    def test_from_str_unknown_defaults_low(self):
        assert Severity.from_str("UNKNOWN") == Severity.LOW

    def test_label_contains_severity_name(self):
        for sev in Severity:
            assert sev.name in sev.label().upper()


# ─── Finding tests ────────────────────────────────────────────────────────────

class TestFinding:
    def _make(self, **kwargs) -> Finding:
        defaults = dict(
            agent="TestAgent",
            severity=Severity.MEDIUM,
            category="test",
            title="Test finding",
            description="A test finding.",
        )
        defaults.update(kwargs)
        return Finding(**defaults)

    def test_id_auto_generated(self):
        f1 = self._make()
        f2 = self._make()
        assert f1.id != f2.id

    def test_dedup_key_normalises_title(self):
        f1 = self._make(file_path="app/auth.py", title="  Hard-coded API Key  ")
        f2 = self._make(file_path="app/auth.py", title="Hard-coded API Key")
        assert f1.dedup_key() == f2.dedup_key()

    def test_dedup_key_different_files_differ(self):
        f1 = self._make(file_path="a.py", title="Same title")
        f2 = self._make(file_path="b.py", title="Same title")
        assert f1.dedup_key() != f2.dedup_key()

    def test_to_dict_returns_dict(self):
        f = self._make()
        d = f.to_dict()
        assert isinstance(d, dict)
        assert d["title"] == "Test finding"

    def test_optional_fields_default_none(self):
        f = self._make()
        assert f.file_path is None
        assert f.line_number is None
        assert f.code_snippet is None
        assert f.suggestion is None


# ─── ChangeState tests ────────────────────────────────────────────────────────

class TestChangeState:
    def _make_state(self) -> ChangeState:
        return ChangeState(diff_text="--- a/foo.py\n+++ b/foo.py\n+x = 1\n")

    def _make_finding(self, severity=Severity.MEDIUM) -> Finding:
        return Finding(
            agent="A",
            severity=severity,
            category="test",
            title="Some issue",
            description="desc",
        )

    def test_add_finding(self):
        state = self._make_state()
        f = self._make_finding()
        state.add_finding(f)
        assert len(state.findings) == 1

    def test_add_findings_multiple(self):
        state = self._make_state()
        findings = [self._make_finding() for _ in range(5)]
        state.add_findings(findings)
        assert len(state.findings) == 5

    def test_sorted_findings_descending(self):
        state = self._make_state()
        for sev in [Severity.LOW, Severity.CRITICAL, Severity.MEDIUM]:
            state.add_finding(self._make_finding(severity=sev))
        sorted_f = state.sorted_findings()
        assert sorted_f[0].severity == Severity.CRITICAL
        assert sorted_f[-1].severity == Severity.LOW

    def test_top_finding_returns_highest(self):
        state = self._make_state()
        state.add_finding(self._make_finding(Severity.LOW))
        state.add_finding(self._make_finding(Severity.HIGH))
        state.add_finding(self._make_finding(Severity.MEDIUM))
        assert state.top_finding().severity == Severity.HIGH

    def test_top_finding_empty_returns_none(self):
        state = self._make_state()
        assert state.top_finding() is None


# ─── Patch tests ─────────────────────────────────────────────────────────────

class TestPatch:
    def test_patch_creation(self):
        p = Patch(
            finding_id="abc-123",
            finding_title="Hard-coded secret",
            file_path="app/auth.py",
            unified_diff="--- a/app/auth.py\n+++ b/app/auth.py\n-SECRET='x'\n+SECRET=os.getenv('SECRET')",
            explanation="Replace hard-coded value with env var.",
        )
        assert p.finding_id == "abc-123"
        assert "os.getenv" in p.unified_diff


# ─── ConsolidatedReview tests ─────────────────────────────────────────────────

class TestConsolidatedReview:
    def test_default_recommendation(self):
        review = ConsolidatedReview()
        assert review.merge_recommendation == "APPROVE"

    def test_counts_default_zero(self):
        review = ConsolidatedReview()
        assert review.critical_count == 0
        assert review.total_findings == 0
