"""
main.py — Entry point for the Automated Code Review Board.

Usage
-----
  # Review a diff file:
  python main.py --diff path/to/change.diff

  # Review a diff piped from git:
  git diff HEAD~1 | python main.py --stdin

  # Review the built-in demo diff:
  python main.py --demo

  # Also export JSON report:
  python main.py --demo --json-out review.json

Options
-------
  --diff FILE        Path to a unified diff file
  --stdin            Read diff from stdin
  --demo             Use the built-in demo diff (no file needed)
  --pr-title TEXT    Pull request title
  --pr-desc  TEXT    Pull request description
  --json-out FILE    Also write a JSON report to this file
  --log-level LEVEL  Logging verbosity (DEBUG, INFO, WARNING) [default: WARNING]
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from pathlib import Path

from agents.reviewer_manager import ReviewerManager
from models.shared_state import ChangeState
from renderer import render_review, render_json

# ─── Demo diff (a deliberately flawed snippet for demonstration) ──────────────

DEMO_DIFF = r"""
diff --git a/app/auth.py b/app/auth.py
index 1234abc..5678def 100644
--- a/app/auth.py
+++ b/app/auth.py
@@ -1,10 +1,35 @@
 import hashlib
+import os
+import pickle
+import subprocess
 from flask import Flask, request, jsonify
 
 app = Flask(__name__)
 
-SECRET_KEY = os.environ.get("SECRET_KEY")
+SECRET_KEY = "super_secret_hardcoded_key_123!"
+DATABASE_PASSWORD = "admin1234"
+AWS_ACCESS_KEY_ID = "AKIAIOSFODNN7EXAMPLE"
+AWS_SECRET_ACCESS_KEY = "wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY"
+
+def login(username, password):
+    # Hash password with MD5 (fast!)
+    hashed = hashlib.md5(password.encode()).hexdigest()
+    query = f"SELECT * FROM users WHERE username='{username}' AND password='{hashed}'"
+    result = db.execute(query)
+    return result
+
+def run_report(report_name):
+    # Run report generation script
+    os.system(f"python reports/{report_name}.py")
+
+def load_user_data(data_bytes):
+    # Deserialize user preferences
+    return pickle.loads(data_bytes)
+
+def execute_admin_command(cmd):
+    result = subprocess.run(cmd, shell=True, capture_output=True)
+    return result.stdout
+
+DEBUG = True
+app.config['DEBUG'] = True
+app.config['PROPAGATE_EXCEPTIONS'] = True

diff --git a/app/utils.py b/app/utils.py
index aaaaaaa..bbbbbbb 100644
--- a/app/utils.py
+++ b/app/utils.py
@@ -1,5 +1,30 @@
+import re
+
 def processUserInput(inputData):
-    return inputData
+    x = eval(inputData)  # evaluate user expression
+    return x
+
+def calculateTax(amount,rate,discount,surcharge,penalty,bonus,adjustment):
+    # very long parameter list and complex logic
+    if amount > 0:
+        if rate > 0:
+            if discount > 0:
+                result = amount * rate - discount + surcharge
+                if penalty:
+                    result += penalty * 2
+                    if bonus:
+                        result -= bonus
+                        if adjustment:
+                            result = result * adjustment
+    return result
+
+def getUserById(id):
+    sql = "SELECT * FROM users WHERE id=" + str(id)
+    return db.query(sql)
+
+API_KEY = "sk-proj-abc123def456xyz789"
+TIMEOUT = 30
+MAX_RETRIES = 3
"""

DEMO_PR_TITLE = "feat: Add authentication module and utility functions"
DEMO_PR_DESC  = "Adds login, report generation, and user data utilities for the new auth flow."


# ─── CLI ──────────────────────────────────────────────────────────────────────

def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Automated Code Review Board — multi-agent PR reviewer",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    source = parser.add_mutually_exclusive_group()
    source.add_argument("--diff",  metavar="FILE", help="Path to a unified diff file")
    source.add_argument("--stdin", action="store_true", help="Read diff from stdin")
    source.add_argument("--demo",  action="store_true", help="Use built-in demo diff")

    parser.add_argument("--pr-title",  default="", help="Pull request title")
    parser.add_argument("--pr-desc",   default="", help="Pull request description")
    parser.add_argument("--json-out",  metavar="FILE", help="Write JSON report to file")
    parser.add_argument(
        "--log-level",
        default="WARNING",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        help="Logging verbosity",
    )
    return parser.parse_args()


# ─── Async main ───────────────────────────────────────────────────────────────

async def async_main() -> None:
    args = parse_args()

    logging.basicConfig(
        level=getattr(logging, args.log_level),
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    # ── Load diff text ────────────────────────────────────────────────────────
    if args.demo:
        diff_text  = DEMO_DIFF
        pr_title   = args.pr_title or DEMO_PR_TITLE
        pr_desc    = args.pr_desc  or DEMO_PR_DESC
    elif args.stdin:
        diff_text = sys.stdin.read()
        pr_title  = args.pr_title
        pr_desc   = args.pr_desc
    elif args.diff:
        diff_path = Path(args.diff)
        if not diff_path.exists():
            print(f"Error: diff file not found: {args.diff}", file=sys.stderr)
            sys.exit(1)
        diff_text = diff_path.read_text(encoding="utf-8")
        pr_title  = args.pr_title
        pr_desc   = args.pr_desc
    else:
        # Default to demo if no source specified
        print("[INFO] No diff source specified — running demo. Use --help for options.\n")
        diff_text = DEMO_DIFF
        pr_title  = DEMO_PR_TITLE
        pr_desc   = DEMO_PR_DESC

    if not diff_text.strip():
        print("Error: diff is empty.", file=sys.stderr)
        sys.exit(1)

    # ── Build shared state ────────────────────────────────────────────────────
    state = ChangeState(
        diff_text=diff_text,
        pr_title=pr_title,
        pr_description=pr_desc,
    )

    # ── Run the pipeline ──────────────────────────────────────────────────────
    manager = ReviewerManager()
    review  = await manager.run(state)

    # ── Render to terminal ────────────────────────────────────────────────────
    render_review(review)

    # ── Optionally write JSON ─────────────────────────────────────────────────
    if args.json_out:
        json_path = Path(args.json_out)
        json_path.write_text(render_json(review), encoding="utf-8")
        print(f"\n[JSON report saved to: {json_path}]")


def main() -> None:
    asyncio.run(async_main())


if __name__ == "__main__":
    main()
