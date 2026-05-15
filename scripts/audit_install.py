"""ClickMem v1 install-experience audit harness.

One-shot, macOS-only. Drives every install-touching code path through a
sandbox, captures PASS / FAIL / SURPRISE / SKIP for each check, and emits a
dated markdown report at ``audit-results/audit-YYYY-MM-DD-HHMM.md``.

Plan: `.cursor/plans/clickmem-install-audit_*.plan.md`.
"""

from __future__ import annotations

import platform
import socket
import subprocess
import sys
import time
import traceback
from pathlib import Path
from typing import Callable, Optional

import typer

# Make the helper package importable when running this script directly.
HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

from audit_install_helpers import (  # noqa: E402
    sandbox as sb,
    tier1_checks,
    tier2_checks,
)
from audit_install_helpers.manual import MANUAL_CHECKLIST  # noqa: E402
from audit_install_helpers.report import (  # noqa: E402
    CheckResult,
    FAIL,
    PASS,
    Report,
    SKIP,
    SURPRISE,
    default_report_path,
)


app = typer.Typer(
    name="audit_install",
    help="ClickMem install-experience audit harness.",
    no_args_is_help=True,
    add_completion=False,
)


_TIER1: list[tuple[str, str, Callable[..., CheckResult]]] = [
    ("T1.1", "Clean wheel install", tier1_checks.t1_1_clean_install),
    ("T1.2", "Service install round-trip", tier1_checks.t1_2_service_install),
    ("T1.3", "Hooks install on fake $HOME", tier1_checks.t1_3_hooks_install),
    ("T1.4", "First-call cold start", tier1_checks.t1_4_cold_start),
    ("T1.5", "MCP stdio wire", tier1_checks.t1_5_mcp_stdio),
    ("T1.6", "Dashboard build + serve", tier1_checks.t1_6_dashboard),
    ("T1.7", "Bug regressions", tier1_checks.t1_7_bug_regressions),
]

_TIER2: list[tuple[str, str, Callable[..., CheckResult]]] = [
    ("T2.1", "README runnability", tier2_checks.t2_1_readme_runnability),
    ("T2.2", "--help discoverability", tier2_checks.t2_2_help_discoverability),
    ("T2.3", "Error message quality", tier2_checks.t2_3_error_messages),
    ("T2.4", "API key / auth flow", tier2_checks.t2_4_auth_flow),
    ("T2.5", "LAN mode (ssh mini)", tier2_checks.t2_5_lan_mode),
    ("T2.6", "Idempotency", tier2_checks.t2_6_idempotency),
    ("T2.7", "Import / export round trip", tier2_checks.t2_7_import_export),
    ("T2.8", "v0 residue detection", tier2_checks.t2_8_v0_residue),
    ("T2.9", "Cold import / startup cost", tier2_checks.t2_9_startup_cost),
]

ALL_CHECKS = _TIER1 + _TIER2
_BY_ID = {cid: (cid, title, fn) for cid, title, fn in ALL_CHECKS}


def _git_info() -> tuple[str, str]:
    try:
        branch = subprocess.check_output(
            ["git", "rev-parse", "--abbrev-ref", "HEAD"],
            cwd=sb.REPO_ROOT,
            text=True,
            timeout=5,
        ).strip()
    except Exception:
        branch = ""
    try:
        commit = subprocess.check_output(
            ["git", "rev-parse", "--short=10", "HEAD"],
            cwd=sb.REPO_ROOT,
            text=True,
            timeout=5,
        ).strip()
    except Exception:
        commit = ""
    return branch, commit


def _run_one(
    cid: str, title: str, fn: Callable[..., CheckResult], keep: bool, no_kwarg_ids: set[str]
) -> CheckResult:
    start = time.time()
    try:
        if cid in no_kwarg_ids:
            result = fn()
        else:
            result = fn(keep=keep)
        if not isinstance(result, CheckResult):
            raise TypeError(f"check {cid} returned {type(result).__name__}, expected CheckResult")
        if not result.id:
            result.id = cid
        if not result.title:
            result.title = title
        if not result.duration_s:
            result.duration_s = time.time() - start
    except KeyboardInterrupt:
        raise
    except Exception as e:  # noqa: BLE001
        result = CheckResult(
            id=cid,
            title=title,
            status=FAIL,
            duration_s=time.time() - start,
            error=f"{e!r}\n{traceback.format_exc()}",
            observed="harness raised uncaught exception (recorded so the run continues)",
        )
    return result


# Checks that don't accept a `keep` kwarg.
_NO_KWARG: set[str] = {"T2.2", "T2.5", "T2.9"}


def _run_selected(
    items: list[tuple[str, str, Callable[..., CheckResult]]],
    *,
    keep: bool,
    report_dir: Path,
    skip_ids: Optional[set[str]] = None,
) -> Report:
    report = Report()
    report.machine = socket.gethostname()
    report.branch, report.commit = _git_info()
    skip_ids = skip_ids or set()

    report_dir.mkdir(parents=True, exist_ok=True)
    out_path = default_report_path(report_dir)

    for cid, title, fn in items:
        if cid in skip_ids:
            typer.echo(f"[audit] skipping {cid} — {title}  (per --skip)", err=True)
            report.record(
                CheckResult(
                    id=cid,
                    title=title,
                    status=SKIP,
                    observed="skipped via --skip command-line option",
                )
            )
            continue
        typer.echo(f"[audit] running {cid} — {title}", err=True)
        result = _run_one(cid, title, fn, keep=keep, no_kwarg_ids=_NO_KWARG)
        report.record(result)
        typer.echo(f"[audit] {cid} -> {result.status} ({result.duration_s:.1f}s)", err=True)
        # Flush an incremental report after every check so a mid-run kill still
        # leaves a usable artefact on disk.
        try:
            out_path.write_text(
                report.to_markdown(manual_checklist=MANUAL_CHECKLIST), encoding="utf-8"
            )
        except Exception as e:  # noqa: BLE001
            typer.echo(f"[audit] WARNING: incremental report flush failed: {e!r}", err=True)

    out_path.write_text(report.to_markdown(manual_checklist=MANUAL_CHECKLIST), encoding="utf-8")
    typer.echo(f"\n[audit] report written to {out_path}", err=True)
    counts = report.counts()
    typer.echo(
        f"[audit] PASS={counts[PASS]}  FAIL={counts[FAIL]}  "
        f"SURPRISE={counts[SURPRISE]}  SKIP={counts[SKIP]}",
        err=True,
    )
    return report


def _default_report_dir() -> Path:
    return sb.REPO_ROOT / "audit-results"


def _parse_skip(skip: list[str]) -> set[str]:
    """Accept --skip T1.1 --skip T2.3 or --skip "T1.1,T2.3"."""
    out: set[str] = set()
    for s in skip or []:
        for item in s.split(","):
            item = item.strip()
            if item:
                out.add(item)
    return out


@app.command("all")
def all_(
    keep: bool = typer.Option(False, "--keep", help="preserve sandboxes for inspection"),
    report_dir: Path = typer.Option(
        _default_report_dir(),
        "--report-dir",
        help="where to write the markdown report",
    ),
    skip: list[str] = typer.Option([], "--skip", help="repeat or comma-separate check ids to skip"),
) -> None:
    """Run every Tier 1 + Tier 2 check."""
    _run_selected(ALL_CHECKS, keep=keep, report_dir=report_dir, skip_ids=_parse_skip(skip))


@app.command("tier1")
def tier1(
    keep: bool = typer.Option(False, "--keep"),
    report_dir: Path = typer.Option(_default_report_dir(), "--report-dir"),
    skip: list[str] = typer.Option([], "--skip"),
) -> None:
    """Run only Tier 1 checks (install blockers)."""
    _run_selected(_TIER1, keep=keep, report_dir=report_dir, skip_ids=_parse_skip(skip))


@app.command("tier2")
def tier2(
    keep: bool = typer.Option(False, "--keep"),
    report_dir: Path = typer.Option(_default_report_dir(), "--report-dir"),
    skip: list[str] = typer.Option([], "--skip"),
) -> None:
    """Run only Tier 2 checks (UX defects)."""
    _run_selected(_TIER2, keep=keep, report_dir=report_dir, skip_ids=_parse_skip(skip))


@app.command("check")
def check(
    check_id: str = typer.Argument(..., help="e.g. T1.3 or T2.7"),
    keep: bool = typer.Option(False, "--keep"),
    report_dir: Path = typer.Option(_default_report_dir(), "--report-dir"),
) -> None:
    """Run a single check by id."""
    cid = check_id.strip()
    if cid not in _BY_ID:
        raise typer.BadParameter(f"unknown check {cid}; one of {sorted(_BY_ID)}")
    item = _BY_ID[cid]
    _run_selected([item], keep=keep, report_dir=report_dir)


@app.command("list-checks")
def list_checks() -> None:
    """Print the catalog of checks."""
    for cid, title, _ in ALL_CHECKS:
        typer.echo(f"{cid}\t{title}")


if __name__ == "__main__":
    if platform.system().lower() != "darwin":
        typer.echo("audit harness is macOS-only; current platform: " + platform.system(), err=True)
    app()
