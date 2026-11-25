"""
agents/test_coverage_agent.py

Test-Coverage Agent — verifies that the submitted change is adequately tested.

Checks include:
  - Are new functions / classes accompanied by tests?
  - Is there a corresponding test file for each modified module?
  - Are critical paths (error handling, edge cases) covered?
  - Are there only assertions that test real behaviour (not trivially `assert True`)?
  - Does the diff modify existing tests in a way that weakens coverage?
  - Are integration / e2e tests needed but missing?
"""

from __future__ import annotations

import re
from typing import List

from agents.base_agent import BaseAgent
from models.finding import Finding, Severity
from models.shared_state import ChangeState

# ─── Prompts ──────────────────────────────────────────────────────────────────

SYSTEM_PROMPT = """\
You are the Test-Coverage Agent in an automated code review board.
You specialise in software testing, test quality, and coverage analysis.

Review the given unified diff and assess whether the change is adequately tested.

Severity guide:
  HIGH   — new public function/class with zero tests; critical path untested
  MEDIUM — existing tests weakened; edge cases missing; test assertions too trivial
  LOW    — test file naming or structure issues; minor coverage gaps
  INFO   — positive observation (tests present and appear thorough)

For each concern produce a JSON finding:
  severity    — HIGH | MEDIUM | LOW | INFO
  category    — "test-coverage"
  title       — short description
  description — why this matters and what is missing
  file_path   — relevant source or test file (or null)
  line_number — approximate line (or null)
  code_snippet— relevant snippet (or null)
  suggestion  — what test should be added

Return ONLY a JSON array. No prose.

```json
[ { ... } ]
```

If coverage appears adequate, return a single INFO finding saying so.
"""

USER_PROMPT_TEMPLATE = """\
=== DIFF SUMMARY ===
{diff_summary}

=== FILES CHANGED ===
{file_paths}

=== UNIFIED DIFF ===
{diff_text}
===================

Analyse this diff for test coverage:
1. Identify every new function, class, or method introduced.
2. Check whether a corresponding test exists in the diff or among the changed files.
3. Look for edge cases: empty input, None, out-of-range values, error paths.
4. Flag any existing test that was deleted or weakened.
5. Check if integration / end-to-end tests are needed.

Return findings as JSON only.
"""

# ─── Test file heuristics ─────────────────────────────────────────────────────

TEST_FILE_PATTERN = re.compile(r"(test_|_test\.|\.test\.|spec\.)", re.IGNORECASE)
FUNCTION_PATTERN  = re.compile(r"^\+\s*(?:async\s+)?def\s+(\w+)\s*\(", re.MULTILINE)
CLASS_PATTERN     = re.compile(r"^\+\s*class\s+(\w+)\s*[:(]",           re.MULTILINE)


# ─── Agent ────────────────────────────────────────────────────────────────────

class TestCoverageAgent(BaseAgent):
    """Verifies that the submitted diff is adequately tested."""

    __test__ = False  # Prevent pytest from collecting this as a test class
    name = "TestCoverageAgent"
    system_prompt = SYSTEM_PROMPT

    async def run(self, state: ChangeState) -> None:
        # ── 1. Static heuristics ──────────────────────────────────────────
        static_findings = self._static_check(state)
        state.add_findings(static_findings)

        # ── 2. LLM analysis ───────────────────────────────────────────────
        prompt = USER_PROMPT_TEMPLATE.format(
            diff_summary=state.diff_summary or "(not yet available)",
            file_paths=", ".join(state.file_paths) or "(unknown)",
            diff_text=state.diff_text[:8000],
        )
        raw = await self._chat(prompt)
        json_block = self._extract_json(raw)
        llm_findings = self._parse_findings(json_block, self.name)
        state.add_findings(llm_findings)

    # ── Static helpers ────────────────────────────────────────────────────────

    def _static_check(self, state: ChangeState) -> List[Finding]:
        findings: List[Finding] = []

        source_files = [f for f in state.file_paths if not TEST_FILE_PATTERN.search(f)]
        test_files   = [f for f in state.file_paths if TEST_FILE_PATTERN.search(f)]

        # Flag source files with no corresponding test file in the diff
        if source_files and not test_files:
            findings.append(
                Finding(
                    agent=self.name,
                    severity=Severity.HIGH,
                    category="test-coverage",
                    title="No test files found in this PR",
                    description=(
                        f"This PR modifies {len(source_files)} source file(s) "
                        f"({', '.join(source_files[:3])}{'...' if len(source_files) > 3 else ''}) "
                        "but includes no test files. All new or changed behaviour should be covered "
                        "by automated tests."
                    ),
                    suggestion=(
                        "Add unit or integration tests for each modified module. "
                        "Name test files `test_<module>.py` or `<module>_test.py`."
                    ),
                )
            )

        # Count newly introduced functions / classes in the diff
        new_funcs  = FUNCTION_PATTERN.findall(state.diff_text)
        new_classes = CLASS_PATTERN.findall(state.diff_text)
        if (new_funcs or new_classes) and not test_files:
            names = new_funcs[:3] + new_classes[:3]
            findings.append(
                Finding(
                    agent=self.name,
                    severity=Severity.HIGH,
                    category="test-coverage",
                    title="New symbols introduced without tests",
                    description=(
                        f"New functions/classes detected: {', '.join(names)}"
                        f"{'...' if len(new_funcs) + len(new_classes) > 6 else ''}. "
                        "None of these have accompanying tests in this PR."
                    ),
                    suggestion="Write at least one happy-path and one error-path test per new public function/class.",
                )
            )

        return findings
