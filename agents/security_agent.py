"""
agents/security_agent.py

Security Agent — scans the diff for security vulnerabilities and unsafe patterns.

Checks include:
  - Hard-coded credentials / API keys / passwords / tokens
  - SQL / command injection vulnerabilities
  - Insecure deserialization (pickle, eval, exec)
  - Use of deprecated / known-vulnerable functions
  - Missing input validation / sanitization
  - Insecure direct object references
  - Dangerous file / path operations
  - Unsafe cryptography (MD5, SHA1 for passwords)
  - Debug mode / verbose error info left enabled
  - CORS wildcard or overly permissive configurations
"""

from __future__ import annotations

import re
from typing import List

from agents.base_agent import BaseAgent
from models.finding import Finding, Severity
from models.shared_state import ChangeState

# ─── Prompts ──────────────────────────────────────────────────────────────────

SYSTEM_PROMPT = """\
You are the Security Agent in an automated code review board.
You are an expert application security engineer (AppSec).

Review the given unified diff for security vulnerabilities and unsafe patterns.
Only flag lines that are added (starting with '+').

Severity guide:
  CRITICAL — remote code execution, credential exposure, injection flaws
  HIGH     — insecure crypto, missing auth, dangerous deserialization
  MEDIUM   — missing validation, overly broad permissions, debug left in
  LOW      — minor security hygiene, informational security note

For each problem produce a JSON finding:
  severity    — CRITICAL | HIGH | MEDIUM | LOW
  category    — "security"
  title       — short name of the vulnerability (e.g. "Hard-coded API Key")
  description — what the risk is and what an attacker could do
  file_path   — file (or null)
  line_number — approximate line in new file (or null)
  code_snippet— the dangerous code (or null — do NOT redact secrets, show them)
  suggestion  — how to fix it

Return ONLY a JSON array. No prose.

```json
[ { ... } ]
```

If no security issues found, return: ```json\n[]\n```
"""

USER_PROMPT_TEMPLATE = """\
=== DIFF SUMMARY ===
{diff_summary}

=== FILES CHANGED ===
{file_paths}

=== UNIFIED DIFF ===
{diff_text}
===================

Perform a thorough security review. Focus on:
1. Hard-coded secrets, tokens, passwords, API keys
2. Injection flaws (SQL, OS command, LDAP, XPath, template)
3. Insecure functions: eval(), exec(), pickle.loads(), os.system(), subprocess with shell=True
4. Missing authentication / authorization checks
5. Insecure cryptography (MD5/SHA1 for passwords, ECB mode, hardcoded IV)
6. Unvalidated user input used in dangerous operations
7. Path traversal vulnerabilities
8. Sensitive data logged or exposed in errors
9. Debug flags, verbose errors, development configs left enabled
10. SSRF, open redirects, CORS wildcards

Return findings as JSON only.
"""

# ─── Static secret patterns (fast pre-scan without LLM) ──────────────────────

SECRET_PATTERNS = [
    (r'(?i)(password|passwd|pwd)\s*=\s*["\'][^"\']{4,}["\']',   "CRITICAL", "Hard-coded password"),
    (r'(?i)(api[_-]?key|apikey)\s*=\s*["\'][^"\']{8,}["\']',    "CRITICAL", "Hard-coded API key"),
    (r'(?i)(secret[_-]?key|secret)\s*=\s*["\'][^"\']{8,}["\']', "CRITICAL", "Hard-coded secret"),
    (r'(?i)(token)\s*=\s*["\'][^"\']{8,}["\']',                  "HIGH",     "Hard-coded token"),
    (r'(?i)(aws_access_key_id)\s*=\s*["\'][^"\']+["\']',         "CRITICAL", "Hard-coded AWS access key"),
    (r'(?i)(aws_secret_access_key)\s*=\s*["\'][^"\']+["\']',     "CRITICAL", "Hard-coded AWS secret key"),
    (r'AKIA[0-9A-Z]{16}',                                         "CRITICAL", "AWS key ID pattern detected"),
    (r'(?i)eval\s*\(',                                            "HIGH",     "Use of eval()"),
    (r'(?i)exec\s*\(',                                            "HIGH",     "Use of exec()"),
    (r'(?i)pickle\.loads?\s*\(',                                  "HIGH",     "Insecure deserialization (pickle)"),
    (r'(?i)subprocess.*shell\s*=\s*True',                         "HIGH",     "subprocess with shell=True"),
    (r'(?i)os\.system\s*\(',                                      "MEDIUM",   "Use of os.system()"),
    (r'(?i)hashlib\.md5\s*\(',                                    "MEDIUM",   "Weak hash (MD5)"),
    (r'(?i)hashlib\.sha1\s*\(',                                   "MEDIUM",   "Weak hash (SHA-1)"),
]


# ─── Agent ────────────────────────────────────────────────────────────────────

class SecurityAgent(BaseAgent):
    """Scans for security vulnerabilities and unsafe code patterns."""

    name = "SecurityAgent"
    system_prompt = SYSTEM_PROMPT

    async def run(self, state: ChangeState) -> None:
        # ── 1. Fast static scan for secrets / dangerous patterns ──────────
        static_findings = self._static_scan(state.diff_text)
        state.add_findings(static_findings)

        # ── 2. LLM deep scan ─────────────────────────────────────────────
        prompt = USER_PROMPT_TEMPLATE.format(
            diff_summary=state.diff_summary or "(not yet available)",
            file_paths=", ".join(state.file_paths) or "(unknown)",
            diff_text=state.diff_text[:8000],
        )
        raw = await self._chat(prompt)
        json_block = self._extract_json(raw)
        llm_findings = self._parse_findings(json_block, self.name)
        state.add_findings(llm_findings)

    # ── Static scanner ────────────────────────────────────────────────────────

    def _static_scan(self, diff: str) -> List[Finding]:
        """
        Lightweight regex scan that runs before the LLM.
        Only checks added lines (starting with '+').
        """
        findings: List[Finding] = []
        added_lines: List[tuple[int, str]] = []

        for i, line in enumerate(diff.splitlines(), start=1):
            if line.startswith("+") and not line.startswith("+++"):
                added_lines.append((i, line[1:]))  # strip the leading '+'

        for lineno, code in added_lines:
            for pattern, severity_str, title in SECRET_PATTERNS:
                m = re.search(pattern, code)
                if m:
                    findings.append(
                        Finding(
                            agent=self.name,
                            severity=Severity.from_str(severity_str),
                            category="security",
                            title=f"[Static] {title}",
                            description=(
                                f"Pattern '{pattern}' matched in added code. "
                                "This may indicate a hard-coded secret or dangerous function call."
                            ),
                            line_number=lineno,
                            code_snippet=code.strip()[:200],
                            suggestion="Remove the hard-coded value and load it from environment variables or a secrets manager.",
                        )
                    )
                    break  # one finding per line is enough for the static scan

        return findings
