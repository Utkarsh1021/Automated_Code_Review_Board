"""
agents/fix_suggestion_agent.py

Fix-Suggestion Agent — Group-of-3 add-on.

Takes the single highest-severity Finding produced by all other agents and
generates a concrete, ready-to-apply unified diff patch that resolves it.

The agent:
  1. Receives the top Finding and the original diff context.
  2. Uses the LLM to reason about the safest, minimal fix.
  3. Produces a Patch object with:
       - unified_diff : git-style patch text
       - explanation  : why this fix addresses the issue
"""

from __future__ import annotations

import re

from agents.base_agent import BaseAgent
from models.finding import Finding
from models.shared_state import ChangeState, Patch

# ─── Prompts ──────────────────────────────────────────────────────────────────

SYSTEM_PROMPT = """\
You are the Fix-Suggestion Agent in an automated code review board.
You are an expert software engineer.

You will be given:
  1. A specific security or quality finding (the highest-severity issue found in the PR).
  2. The relevant code context from the diff.

Your job is to produce a CONCRETE, MINIMAL fix for that exact finding.

Respond in this EXACT format:

EXPLANATION:
<2-4 sentences explaining what you changed and why it fixes the issue>

PATCH:
```diff
--- a/<file_path>
+++ b/<file_path>
@@ -<line>,<count> +<line>,<count> @@
 <context line>
-<removed line>
+<added line>
 <context line>
```

Rules:
  - The patch must be a valid unified diff (git diff format).
  - Only change what is strictly necessary to fix the specific finding.
  - Do not add unrelated improvements.
  - If the finding is about a hard-coded secret, replace it with an os.environ.get() call.
  - If the finding is about eval()/exec(), replace with a safe alternative.
  - Keep the patch minimal — prefer targeted fixes over rewrites.
"""

USER_PROMPT_TEMPLATE = """\
=== FINDING TO FIX ===
Severity  : {severity}
Category  : {category}
Title     : {title}
File      : {file_path}
Line      : {line_number}
Description: {description}
Code Snippet:
{code_snippet}

=== DIFF CONTEXT ===
{diff_context}
===================

Produce a concrete unified-diff patch that fixes exactly this finding.
"""


# ─── Agent ────────────────────────────────────────────────────────────────────

class FixSuggestionAgent(BaseAgent):
    """Proposes a concrete patch for the highest-severity finding."""

    name = "FixSuggestionAgent"
    system_prompt = SYSTEM_PROMPT

    async def run(self, state: ChangeState) -> None:
        top = state.top_finding()
        if top is None:
            # No findings — nothing to fix
            state.patch_suggestion = None
            return

        # Narrow the diff context to the relevant file (if known)
        diff_context = self._extract_file_context(state.diff_text, top)

        prompt = USER_PROMPT_TEMPLATE.format(
            severity=top.severity.name,
            category=top.category,
            title=top.title,
            file_path=top.file_path or "unknown",
            line_number=top.line_number or "unknown",
            description=top.description,
            code_snippet=top.code_snippet or "(see diff context)",
            diff_context=diff_context[:6000],
        )
        raw = await self._chat(prompt)

        explanation = self._extract_explanation(raw)
        unified_diff = self._extract_patch(raw)

        state.patch_suggestion = Patch(
            finding_id=top.id,
            finding_title=top.title,
            file_path=top.file_path,
            unified_diff=unified_diff,
            explanation=explanation,
        )

    # ── Helpers ───────────────────────────────────────────────────────────────

    @staticmethod
    def _extract_file_context(diff: str, finding: Finding) -> str:
        """Return only the diff hunk(s) for the finding's file."""
        if not finding.file_path:
            return diff

        lines = diff.splitlines(keepends=True)
        result, in_target = [], False
        for line in lines:
            if re.match(r"^diff --git", line):
                in_target = finding.file_path in line
            if in_target:
                result.append(line)
        return "".join(result) if result else diff

    @staticmethod
    def _extract_explanation(raw: str) -> str:
        m = re.search(
            r"EXPLANATION:\s*(.*?)(?=PATCH:|```diff|$)", raw, re.DOTALL | re.IGNORECASE
        )
        if m:
            return m.group(1).strip()
        return raw[:300]

    @staticmethod
    def _extract_patch(raw: str) -> str:
        m = re.search(r"```diff\s*([\s\S]+?)```", raw, re.IGNORECASE)
        if m:
            return m.group(1).strip()
        # Fallback: everything after PATCH:
        m2 = re.search(r"PATCH:\s*([\s\S]+)", raw, re.IGNORECASE)
        if m2:
            return m2.group(1).strip()
        return "(no patch generated)"
