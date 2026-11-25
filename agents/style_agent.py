"""
agents/style_agent.py

Style Agent — checks the submitted diff for style and readability problems.

Checks include:
  - Naming conventions (functions, variables, classes)
  - Line length violations
  - Overly complex functions / deeply nested code
  - Missing or incomplete docstrings / comments
  - Magic numbers / hard-coded constants
  - Dead code (unreachable branches, unused imports)
  - Inconsistent formatting
"""

from __future__ import annotations

from agents.base_agent import BaseAgent
from models.shared_state import ChangeState

# ─── Prompts ──────────────────────────────────────────────────────────────────

SYSTEM_PROMPT = """\
You are the Style Agent in an automated code review board.
You specialise in code style, readability, and maintainability.

Review the given unified diff and identify style / readability problems.
Only flag lines that are added (lines starting with '+').
Do NOT report deleted lines as new issues.

For each problem produce a JSON finding with these exact fields:
  severity    — one of: MEDIUM, LOW, INFO
  category    — "style"
  title       — a short one-line issue name
  description — detailed explanation of why it's a problem
  file_path   — file where the issue occurs (or null)
  line_number — approximate line number in the new file (or null)
  code_snippet— the offending code excerpt (or null)
  suggestion  — a concrete, brief fix

Return ONLY a JSON array — no prose before or after.

```json
[
  { ... },
  ...
]
```

If you find no issues, return an empty array: ```json\n[]\n```
"""

USER_PROMPT_TEMPLATE = """\
=== DIFF SUMMARY (from DiffAgent) ===
{diff_summary}

=== FILES CHANGED ===
{file_paths}

=== UNIFIED DIFF ===
{diff_text}
===================

Check every added line (+) for style and readability issues.
Focus on: naming, complexity, documentation, magic numbers, dead code,
formatting consistency. Return your findings as JSON only.
"""


# ─── Agent ────────────────────────────────────────────────────────────────────

class StyleAgent(BaseAgent):
    """Checks style and readability issues in the diff."""

    name = "StyleAgent"
    system_prompt = SYSTEM_PROMPT

    async def run(self, state: ChangeState) -> None:
        prompt = USER_PROMPT_TEMPLATE.format(
            diff_summary=state.diff_summary or "(not yet available)",
            file_paths=", ".join(state.file_paths) or "(unknown)",
            diff_text=state.diff_text[:8000],
        )
        raw = await self._chat(prompt)
        json_block = self._extract_json(raw)
        findings = self._parse_findings(json_block, self.name)
        state.add_findings(findings)
