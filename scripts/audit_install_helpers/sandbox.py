"""Sandbox primitives — ephemeral venvs, fake HOMEs, free ports.

Every check that mutates host state runs through one of these context
managers so the audit cannot wreck `~/.claude/`, `~/.cursor/`, the real
launchd domain, or the user's chDB store.
"""

from __future__ import annotations

import contextlib
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import uuid
from pathlib import Path
from typing import Iterator, Optional


REPO_ROOT = Path(__file__).resolve().parents[2]


def real_home() -> Path:
    """The actual user's $HOME at audit launch (captured before any override)."""
    return Path(os.environ.get("AUDIT_REAL_HOME") or os.path.expanduser("~"))


def shared_model_cache_env() -> dict[str, str]:
    """Env overrides that let a fake-$HOME process reuse the host HF cache.

    Re-downloading sentence-transformers / torch into every fake $HOME would
    take 30–60 s per spawn and ~2 GB of disk. We point HF_HOME and the
    sentence-transformers cache at the real user's cache so the audit reuses
    whatever is already on disk.
    """
    rh = real_home()
    hf = rh / ".cache" / "huggingface"
    st = rh / ".cache" / "sentence_transformers"
    out = {
        "HF_HOME": str(hf),
        "TRANSFORMERS_CACHE": str(hf / "hub"),
        "HUGGINGFACE_HUB_CACHE": str(hf / "hub"),
        "SENTENCE_TRANSFORMERS_HOME": str(st),
        "TORCH_HOME": str(rh / ".cache" / "torch"),
    }
    return out


def pick_free_port() -> int:
    """Bind+release a loopback socket to claim a free port from the kernel."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def wait_for_port(host: str, port: int, *, timeout: float = 30.0, interval: float = 0.25) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.settimeout(0.5)
            try:
                s.connect((host, port))
                return True
            except OSError:
                time.sleep(interval)
    return False


def wait_for_http_ok(url: str, *, timeout: float = 60.0, interval: float = 0.5) -> bool:
    import httpx

    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            r = httpx.get(url, timeout=3.0)
            if r.status_code == 200:
                return True
        except Exception:
            pass
        time.sleep(interval)
    return False


@contextlib.contextmanager
def fake_home(template: Optional[Path] = None, *, keep: bool = False) -> Iterator[Path]:
    root = Path(tempfile.mkdtemp(prefix="clickmem-audit-home-"))
    try:
        if template is not None and template.is_dir():
            for child in template.iterdir():
                dst = root / child.name
                if child.is_dir():
                    shutil.copytree(child, dst)
                else:
                    shutil.copy2(child, dst)
        (root / "Library" / "LaunchAgents").mkdir(parents=True, exist_ok=True)
        (root / ".clickmem").mkdir(parents=True, exist_ok=True)
        yield root
    finally:
        if not keep:
            shutil.rmtree(root, ignore_errors=True)


@contextlib.contextmanager
def ephemeral_venv(*, keep: bool = False, system_site_packages: bool = False) -> Iterator[Path]:
    """Create a fresh venv. With ``system_site_packages`` the venv inherits
    the parent interpreter's installed packages (used by T1.1 to keep the
    install-machinery test fast without re-downloading torch & friends every
    run).
    """
    root = Path(tempfile.mkdtemp(prefix="clickmem-audit-venv-"))
    venv = root / "venv"
    try:
        venv_args = [sys.executable, "-m", "venv"]
        if system_site_packages:
            venv_args.append("--system-site-packages")
        venv_args.append(str(venv))
        subprocess.run(
            venv_args,
            check=True,
            capture_output=True,
            timeout=60,
        )
        pip = venv / "bin" / "pip"
        # Match the requirements floor without spending forever upgrading pip.
        subprocess.run(
            [str(pip), "install", "--quiet", "--upgrade", "pip"],
            check=True,
            capture_output=True,
            timeout=120,
        )
        yield venv
    finally:
        if not keep:
            shutil.rmtree(root, ignore_errors=True)


def venv_python(venv: Path) -> Path:
    return venv / "bin" / "python"


def venv_bin(venv: Path, name: str) -> Path:
    return venv / "bin" / name


@contextlib.contextmanager
def temp_db_path(*, keep: bool = False) -> Iterator[Path]:
    root = Path(tempfile.mkdtemp(prefix="clickmem-audit-db-"))
    try:
        yield root
    finally:
        if not keep:
            shutil.rmtree(root, ignore_errors=True)


def fixtures_dir() -> Path:
    return Path(__file__).resolve().parent / "fixtures"


def fake_home_template() -> Path:
    return fixtures_dir() / "fake_home_template"
