"""Markdown report writer and check result recorder.

A single :class:`Report` collects :class:`CheckResult` rows and flushes a
markdown report at the end. Status is one of PASS / FAIL / SURPRISE / SKIP.
"""

from __future__ import annotations

import datetime
import io
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, List, Optional


PASS = "PASS"
FAIL = "FAIL"
SURPRISE = "SURPRISE"
SKIP = "SKIP"
STATUSES = (PASS, FAIL, SURPRISE, SKIP)


@dataclass
class CheckResult:
    id: str
    title: str
    status: str = SKIP
    duration_s: float = 0.0
    command: str = ""
    observed: str = ""
    suggested_fix: str = ""
    extras: dict = field(default_factory=dict)
    error: Optional[str] = None

    def to_markdown(self) -> str:
        lines: List[str] = []
        lines.append(f"### {self.id} — {self.title}")
        lines.append("")
        lines.append(f"Status: **{self.status}**")
        if self.duration_s:
            lines.append(f"Duration: {self.duration_s:.2f} s")
        if self.command:
            lines.append(f"Command: `{self.command}`")
        if self.observed:
            lines.append("")
            lines.append("Observed:")
            lines.append("")
            lines.append(_fence(self.observed))
        if self.suggested_fix:
            lines.append("")
            lines.append("Suggested fix:")
            lines.append("")
            lines.append(self.suggested_fix.rstrip())
        if self.error:
            lines.append("")
            lines.append(f"Harness error: `{self.error.strip()}`")
        if self.extras:
            lines.append("")
            lines.append("Extras:")
            lines.append("")
            for k, v in self.extras.items():
                if isinstance(v, str) and "\n" in v:
                    lines.append(f"- {k}:")
                    lines.append(_fence(v))
                else:
                    sval = str(v)
                    if len(sval) > 4000:
                        sval = sval[:4000] + " …"
                    lines.append(f"- {k}: `{sval}`")
        lines.append("")
        return "\n".join(lines)


def _fence(text: str) -> str:
    if not text:
        return "```\n```"
    text = text.rstrip()
    fence = "```"
    while fence in text:
        fence += "`"
    return f"{fence}\n{text}\n{fence}"


@dataclass
class Report:
    results: List[CheckResult] = field(default_factory=list)
    started_at: datetime.datetime = field(default_factory=lambda: datetime.datetime.now(datetime.timezone.utc))
    machine: str = ""
    branch: str = ""
    commit: str = ""

    def record(self, item: CheckResult) -> None:
        self.results.append(item)

    def counts(self) -> dict:
        out = {s: 0 for s in STATUSES}
        for r in self.results:
            out[r.status] = out.get(r.status, 0) + 1
        return out

    def to_markdown(self, *, manual_checklist: str = "") -> str:
        buf = io.StringIO()
        buf.write(f"# ClickMem install-experience audit\n\n")
        buf.write(f"_Generated: {self.started_at.isoformat(timespec='seconds')}_  \n")
        if self.machine:
            buf.write(f"_Host: `{self.machine}`_  \n")
        if self.branch or self.commit:
            buf.write(f"_Branch: `{self.branch}` @ `{self.commit}`_  \n")
        buf.write("\n")

        counts = self.counts()
        buf.write("## Summary\n\n")
        buf.write("| Status | Count |\n|---|---|\n")
        for s in STATUSES:
            buf.write(f"| **{s}** | {counts.get(s, 0)} |\n")
        buf.write("\n")
        buf.write("| Check | Status | Duration |\n|---|---|---|\n")
        for r in self.results:
            buf.write(f"| {r.id} — {r.title} | **{r.status}** | {r.duration_s:.2f}s |\n")
        buf.write("\n---\n\n")

        for r in self.results:
            buf.write(r.to_markdown())
            buf.write("\n")

        if manual_checklist:
            buf.write("\n---\n\n")
            buf.write("## Manual checklist (dashboard, 5 minutes)\n\n")
            buf.write(manual_checklist.strip())
            buf.write("\n")
        return buf.getvalue()


def default_report_path(report_dir: Path) -> Path:
    stamp = datetime.datetime.now().strftime("%Y-%m-%d-%H%M")
    return report_dir / f"audit-{stamp}.md"
