"""Subprocess wrappers shared by every check.

Each helper returns a small dataclass so the call sites stay terse and
the report layer can dump command/exit/stdout/stderr/duration cleanly.
"""

from __future__ import annotations

import os
import shlex
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Mapping, Optional, Sequence


@dataclass
class RunResult:
    cmd: list[str]
    returncode: int
    stdout: str
    stderr: str
    duration_s: float
    timed_out: bool = False
    cwd: str = ""
    env_overrides: dict[str, str] = field(default_factory=dict)

    @property
    def text(self) -> str:
        return (self.stdout or "") + (self.stderr or "")

    @property
    def display_cmd(self) -> str:
        return " ".join(shlex.quote(p) for p in self.cmd)


def run(
    cmd: Sequence[str],
    *,
    timeout: float = 60.0,
    env: Optional[Mapping[str, str]] = None,
    cwd: Optional[Path] = None,
    input_text: Optional[str] = None,
    extra_env: Optional[Mapping[str, str]] = None,
    check: bool = False,
) -> RunResult:
    full_env = os.environ.copy()
    if env is not None:
        full_env = dict(env)
    if extra_env:
        full_env.update(extra_env)
    # Keep rich/typer output plain so the audit can grep it without
    # tripping over ANSI escapes. Force these (not setdefault) — the parent
    # shell usually has TERM=xterm-256color already, and Rich respects
    # TERM=dumb but not NO_COLOR alone for some output paths.
    full_env["NO_COLOR"] = "1"
    full_env["TERM"] = "dumb"
    full_env["FORCE_COLOR"] = "0"
    t0 = time.time()
    timed_out = False
    try:
        proc = subprocess.run(
            list(cmd),
            capture_output=True,
            text=True,
            timeout=timeout,
            env=full_env,
            cwd=str(cwd) if cwd else None,
            input=input_text,
        )
        rc = proc.returncode
        out = proc.stdout or ""
        err = proc.stderr or ""
    except subprocess.TimeoutExpired as e:
        timed_out = True
        rc = 124
        out = (e.stdout.decode("utf-8", errors="replace") if isinstance(e.stdout, bytes) else (e.stdout or "")) or ""
        err = (e.stderr.decode("utf-8", errors="replace") if isinstance(e.stderr, bytes) else (e.stderr or "")) or ""
    except FileNotFoundError as e:
        rc = 127
        out = ""
        err = str(e)
    elapsed = time.time() - t0

    overrides = {k: v for k, v in (extra_env or {}).items()}
    result = RunResult(
        cmd=list(cmd),
        returncode=rc,
        stdout=out,
        stderr=err,
        duration_s=elapsed,
        timed_out=timed_out,
        cwd=str(cwd) if cwd else "",
        env_overrides=overrides,
    )
    if check and rc != 0:
        raise RuntimeError(
            f"command failed (rc={rc}, timed_out={timed_out}): {result.display_cmd}\n"
            f"stdout: {out[:2000]}\nstderr: {err[:2000]}"
        )
    return result


def assert_in_output(result: RunResult, needle: str) -> bool:
    return needle in (result.stdout or "") or needle in (result.stderr or "")


def has_traceback(result: RunResult) -> bool:
    # Plain Python traceback OR Rich/Typer-styled traceback panel.
    text = result.text
    if "Traceback (most recent call last):" in text:
        return True
    if "Traceback" in text and ("─ Traceback" in text or "╭─" in text):
        return True
    return False
