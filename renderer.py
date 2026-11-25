"""
renderer.py

Renders a ConsolidatedReview to the terminal using Rich for beautiful,
colour-coded output. Also provides JSON export.
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import List

from rich.console import Console
from rich.markdown import Markdown
from rich.panel import Panel
from rich.rule import Rule
from rich.table import Table
from rich import box

from models.finding import Finding, Severity
from models.shared_state import ConsolidatedReview, MergeRecommendation

console = Console()


# ─── Colour map ───────────────────────────────────────────────────────────────

SEVERITY_STYLE = {
    Severity.CRITICAL: "bold red",
    Severity.HIGH:     "bold yellow",
    Severity.MEDIUM:   "yellow",
    Severity.LOW:      "cyan",
    Severity.INFO:     "dim",
}

RECOMMENDATION_STYLE = {
    MergeRecommendation.REQUEST_CHANGES: "bold red",
    MergeRecommendation.COMMENT:         "bold yellow",
    MergeRecommendation.APPROVE:         "bold green",
}

RECOMMENDATION_ICON = {
    MergeRecommendation.REQUEST_CHANGES: "❌  REQUEST CHANGES",
    MergeRecommendation.COMMENT:         "💬  APPROVE WITH COMMENTS",
    MergeRecommendation.APPROVE:         "✅  APPROVE",
}


# ─── Main render function ─────────────────────────────────────────────────────

def render_review(review: ConsolidatedReview) -> None:
    """Print the full consolidated review to stdout using Rich."""
    _header(review)
    _summary_panel(review)
    _stats_table(review)
    _findings_section(review)
    _fix_suggestion_section(review)
    _verdict_panel(review)
    _footer()


def render_json(review: ConsolidatedReview) -> str:
    """Return the review as a pretty-printed JSON string."""
    data = {
        "generated_at": datetime.now().isoformat(),
        "pr_title": review.pr_title,
        "diff_summary": review.diff_summary,
        "merge_recommendation": review.merge_recommendation,
        "recommendation_reason": review.recommendation_reason,
        "statistics": {
            "total": review.total_findings,
            "critical": review.critical_count,
            "high": review.high_count,
            "medium": review.medium_count,
            "low": review.low_count,
            "info": review.info_count,
        },
        "findings": [
            {
                "id": f.id,
                "agent": f.agent,
                "severity": f.severity.name,
                "category": f.category,
                "title": f.title,
                "description": f.description,
                "file_path": f.file_path,
                "line_number": f.line_number,
                "code_snippet": f.code_snippet,
                "suggestion": f.suggestion,
            }
            for f in review.all_findings
        ],
        "patch_suggestion": (
            {
                "finding_id": review.patch_suggestion.finding_id,
                "finding_title": review.patch_suggestion.finding_title,
                "file_path": review.patch_suggestion.file_path,
                "explanation": review.patch_suggestion.explanation,
                "unified_diff": review.patch_suggestion.unified_diff,
            }
            if review.patch_suggestion
            else None
        ),
    }
    return json.dumps(data, indent=2)


# ─── Sections ─────────────────────────────────────────────────────────────────

def _header(review: ConsolidatedReview) -> None:
    console.print()
    console.rule("[bold blue]🤖  AUTOMATED CODE REVIEW BOARD[/bold blue]", style="blue")
    if review.pr_title:
        console.print(f"\n[bold]PR:[/bold] {review.pr_title}\n", justify="center")


def _summary_panel(review: ConsolidatedReview) -> None:
    if review.diff_summary:
        console.print(
            Panel(
                review.diff_summary,
                title="[bold cyan]📄 Diff Summary[/bold cyan]",
                border_style="cyan",
                padding=(1, 2),
            )
        )
        console.print()


def _stats_table(review: ConsolidatedReview) -> None:
    table = Table(
        title="📊 Findings Summary",
        box=box.ROUNDED,
        show_header=True,
        header_style="bold blue",
    )
    table.add_column("Severity",  style="bold", width=12)
    table.add_column("Count",     justify="center", width=8)
    table.add_column("Blocks Merge?", justify="center", width=14)

    rows = [
        (Severity.CRITICAL, review.critical_count, "YES"),
        (Severity.HIGH,     review.high_count,     "YES"),
        (Severity.MEDIUM,   review.medium_count,   "With comment"),
        (Severity.LOW,      review.low_count,       "No"),
        (Severity.INFO,     review.info_count,      "No"),
    ]
    for sev, cnt, blocks in rows:
        style = SEVERITY_STYLE[sev]
        table.add_row(
            f"[{style}]{sev.label()}[/{style}]",
            f"[{style}]{cnt}[/{style}]" if cnt else "0",
            blocks if cnt else "-",
        )
    console.print(table)
    console.print(f"\n[dim]Total findings (after deduplication): {review.total_findings}[/dim]\n")


def _findings_section(review: ConsolidatedReview) -> None:
    if not review.all_findings:
        console.print("[green]No findings. The code looks clean![/green]\n")
        return

    console.rule("[bold]📋 Findings by Severity[/bold]")
    console.print()

    for sev_label, findings in review.findings_by_severity.items():
        # Find the Severity enum value from the first finding
        sev = findings[0].severity if findings else Severity.INFO
        style = SEVERITY_STYLE[sev]

        console.print(f"[{style}]{'─' * 60}[/{style}]")
        console.print(f"[{style}]  {sev_label}  ({len(findings)} finding{'s' if len(findings) != 1 else ''})[/{style}]")
        console.print(f"[{style}]{'─' * 60}[/{style}]")
        console.print()

        for idx, finding in enumerate(findings, start=1):
            _render_finding(finding, idx, style)


def _render_finding(finding: Finding, idx: int, style: str) -> None:
    sev = finding.severity
    sev_style = SEVERITY_STYLE[sev]

    # Title line
    title_line = f"[{sev_style}][{idx}] {finding.title}[/{sev_style}]"
    if finding.file_path:
        loc = finding.file_path
        if finding.line_number:
            loc += f":{finding.line_number}"
        title_line += f"  [dim]({loc})[/dim]"
    console.print(title_line)

    # Agent tag
    console.print(f"    [dim]Agent: {finding.agent} | Category: {finding.category}[/dim]")

    # Description
    console.print(f"    {finding.description}")

    # Code snippet
    if finding.code_snippet:
        console.print(f"\n    [dim]Code:[/dim]")
        for line in finding.code_snippet.splitlines()[:8]:
            console.print(f"    [dim on black]  {line}[/dim on black]")

    # Suggestion
    if finding.suggestion:
        console.print(f"\n    [green]💡 Suggestion:[/green] {finding.suggestion}")

    console.print()


def _fix_suggestion_section(review: ConsolidatedReview) -> None:
    patch = review.patch_suggestion
    if not patch:
        return

    console.rule("[bold magenta]🔧 Fix-Suggestion Agent — Proposed Patch[/bold magenta]", style="magenta")
    console.print()
    console.print(Panel(
        f"[bold]Finding:[/bold] {patch.finding_title}\n"
        f"[bold]File:[/bold]    {patch.file_path or 'unknown'}\n\n"
        f"[bold]Explanation:[/bold]\n{patch.explanation}",
        title="[magenta]Fix Context[/magenta]",
        border_style="magenta",
        padding=(1, 2),
    ))

    if patch.unified_diff and patch.unified_diff != "(no patch generated)":
        console.print("\n[bold]Unified Diff Patch:[/bold]")
        # Print diff lines with colour
        for line in patch.unified_diff.splitlines():
            if line.startswith("+++") or line.startswith("---"):
                console.print(f"[bold white]{line}[/bold white]")
            elif line.startswith("+"):
                console.print(f"[green]{line}[/green]")
            elif line.startswith("-"):
                console.print(f"[red]{line}[/red]")
            elif line.startswith("@@"):
                console.print(f"[cyan]{line}[/cyan]")
            else:
                console.print(f"[dim]{line}[/dim]")
    console.print()


def _verdict_panel(review: ConsolidatedReview) -> None:
    rec = review.merge_recommendation
    style = RECOMMENDATION_STYLE.get(rec, "white")
    icon  = RECOMMENDATION_ICON.get(rec, rec)

    console.print(
        Panel(
            f"[{style}]{icon}[/{style}]\n\n{review.recommendation_reason}",
            title="[bold]⚖️  Merge Recommendation[/bold]",
            border_style=style.replace("bold ", ""),
            padding=(1, 4),
        )
    )
    console.print()


def _footer() -> None:
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    console.rule(f"[dim]Review generated at {ts} | Automated Code Review Board[/dim]", style="dim")
    console.print()
