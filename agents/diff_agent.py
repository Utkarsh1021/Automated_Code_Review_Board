"""
agents/diff_agent.py

Diff Agent — the first agent in the pipeline.

Responsibilities:
  1. Parse file paths, addition/deletion counts from the raw unified diff.
  2. Use the LLM to produce a human-readable summary of the change.
  3. Emit INFO-level findings that describe what was changed and why.

This agent's diff_summary is stored on the shared ChangeState so that all
subsequent agents receive the same context.
"""

from __future__ import annotations

import re
from typing import List

from agents.base_agent import BaseAgent
from models.finding import Finding, Severity
from models.shared_state import ChangeState

# ─── Prompt ───────────────────────────────────────────────────────────────────

SYSTEM_PROMPT = """\
You are the Diff Agent in an automated code review board.
Your job is to read a unified diff (git diff format) and produce:

1. A concise, plain-English summary (3-6 sentences) explaining:
   - What the change does functionally
   - Which files are modified and why
   - Any architectural or structural implications

2. A JSON array of INFO-level findings — one per logical concern
   (e.g. large file, deleted code, renamed function).

Return your response in this EXACT format:

SUMMARY:
<your 3-6 sentence summary here>

FINDINGS:
```json
[
  {
    "severity": "INFO",
    "category": "diff",
    "title": "<short title>",
    "description": "<what this observation means>",
    "file_path": "<affected file or null>",
    "line_number": null,
    "code_snippet": null,
    "suggestion": null
  }
]
```
"""

USER_PROMPT_TEMPLATE = """\
PR Title: {pr_title}
PR Description: {pr_description}

=== UNIFIED DIFF ===
{diff_text}
===================

Analyse this diff and respond exactly as instructed.
"""


# ─── Agent ────────────────────────────────────────────────────────────────────

class DiffAgent(BaseAgent):
    """Parses and summarises the submitted code diff."""

    name = "DiffAgent"
    system_prompt = SYSTEM_PROMPT

    async def run(self, state: ChangeState) -> None:
        # ── 1. Static parse: file paths, +/- counts ───────────────────────
        state.file_paths = self._extract_file_paths(state.diff_text)
        state.additions, state.deletions = self._count_lines(state.diff_text)

        # ── 2. LLM call ───────────────────────────────────────────────────
        prompt = USER_PROMPT_TEMPLATE.format(
            pr_title=state.pr_title or "(no title)",
            pr_description=state.pr_description or "(no description)",
            diff_text=state.diff_text[:8000],  # guard token limit
        )
        raw = await self._chat(prompt)

        # ── 3. Extract summary ────────────────────────────────────────────
        state.diff_summary = self._extract_summary(raw)

        # ── 4. Extract findings ───────────────────────────────────────────
        json_block = self._extract_json(raw)
        findings = self._parse_findings(json_block, self.name)

        # Add a meta finding about the diff size if large
        if state.additions + state.deletions > 500:
            findings.append(
                Finding(
                    agent=self.name,
                    severity=Severity.LOW,
                    category="diff",
                    title="Large diff — consider splitting the PR",
                    description=(
                        f"This PR adds {state.additions} lines and removes "
                        f"{state.deletions} lines across {len(state.file_paths)} files. "
                        "Large PRs are harder to review thoroughly and increase the risk "
                        "of merging defects."
                    ),
                    suggestion="Split into smaller, focused PRs when possible.",
                )
            )

        state.add_findings(findings)

    # ── Static helpers ────────────────────────────────────────────────────────

    @staticmethod
    def _extract_file_paths(diff: str) -> List[str]:
        """Extract all file paths that appear in '--- a/...' or '+++ b/...' headers."""
        paths = set()
        for line in diff.splitlines():
            m = re.match(r"^(?:\+\+\+|---)\s+[ab]/(.+)", line)
            if m:
                p = m.group(1).strip()
                if p != "/dev/null":
                    paths.add(p)
        return sorted(paths)

    @staticmethod
    def _count_lines(diff: str) -> tuple[int, int]:
        additions = sum(
            1 for ln in diff.splitlines()
            if ln.startswith("+") and not ln.startswith("+++")
        )
        deletions = sum(
            1 for ln in diff.splitlines()
            if ln.startswith("-") and not ln.startswith("---")
        )
        return additions, deletions

    @staticmethod
    def _extract_summary(raw: str) -> str:
        """Pull the text between SUMMARY: and FINDINGS: markers."""
        m = re.search(r"SUMMARY:\s*(.*?)(?=FINDINGS:|```|$)", raw, re.DOTALL | re.IGNORECASE)
        if m:
            return m.group(1).strip()
        # Fallback: return first paragraph
        lines = [ln for ln in raw.splitlines() if ln.strip()]
        return " ".join(lines[:5]) if lines else raw[:300]
