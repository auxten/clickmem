"""OS-level service installer: launchd on macOS, systemd --user on Linux.

The unit invokes ``<python> -m clickmem serve`` so it stays binary-name-
independent and uses whatever interpreter installed the package. Configuration
is env-driven (matches the plan's table): we forward
``CLICKMEM_SERVER_HOST`` / ``CLICKMEM_SERVER_PORT`` / ``CLICKMEM_API_KEY`` /
``CLICKMEM_BACKEND`` / ``CLICKMEM_DB_PATH`` / ``CLICKMEM_CH_*`` /
``CLICKMEM_LOG_LEVEL`` so the running service honours the same env the user
ran the install command with.
"""

from __future__ import annotations

import os
import platform
import plistlib
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

from clickmem.config import get_config


LAUNCHD_LABEL = "com.clickmem.server"
SYSTEMD_UNIT = "clickmem.service"


# ---------- Common helpers ------------------------------------------------


def _python_exec() -> str:
    return sys.executable or shutil.which("python") or shutil.which("python3") or "python3"


def _env_to_forward() -> dict[str, str]:
    cfg = get_config(refresh=True)
    forwarded = {
        "CLICKMEM_SERVER_HOST": cfg.server_host,
        "CLICKMEM_SERVER_PORT": str(cfg.server_port),
        "CLICKMEM_BACKEND": cfg.backend,
        "CLICKMEM_DB_PATH": str(cfg.db_path),
        "CLICKMEM_EMBEDDING_MODEL": cfg.embedding_model,
        "CLICKMEM_LOG_LEVEL": cfg.log_level,
        "PATH": os.environ.get("PATH", "/usr/local/bin:/usr/bin:/bin"),
    }
    if cfg.api_key:
        forwarded["CLICKMEM_API_KEY"] = cfg.api_key
    if cfg.ch_url:
        forwarded["CLICKMEM_CH_URL"] = cfg.ch_url
    if cfg.ch_user:
        forwarded["CLICKMEM_CH_USER"] = cfg.ch_user
    if cfg.ch_password:
        forwarded["CLICKMEM_CH_PASSWORD"] = cfg.ch_password
    if cfg.ch_database:
        forwarded["CLICKMEM_CH_DATABASE"] = cfg.ch_database
    return forwarded


def _log_dir() -> Path:
    p = Path.home() / ".clickmem" / "logs"
    p.mkdir(parents=True, exist_ok=True)
    return p


# ---------- macOS (launchd) -----------------------------------------------


def _launchd_plist_path() -> Path:
    return Path.home() / "Library" / "LaunchAgents" / f"{LAUNCHD_LABEL}.plist"


def _write_launchd_plist() -> Path:
    plist = {
        "Label": LAUNCHD_LABEL,
        "ProgramArguments": [_python_exec(), "-m", "clickmem", "serve"],
        "RunAtLoad": True,
        "KeepAlive": True,
        "EnvironmentVariables": _env_to_forward(),
        "StandardOutPath": str(_log_dir() / "server.out.log"),
        "StandardErrorPath": str(_log_dir() / "server.err.log"),
        "WorkingDirectory": str(Path.home()),
    }
    path = _launchd_plist_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "wb") as fh:
        plistlib.dump(plist, fh)
    return path


def _launchctl(*args: str) -> tuple[int, str]:
    try:
        proc = subprocess.run(
            ["launchctl", *args],
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
        )
        return proc.returncode, (proc.stdout + proc.stderr).strip()
    except FileNotFoundError:
        return 127, "launchctl not found (this needs a real install path on non-macOS)"
    except subprocess.TimeoutExpired:
        return 124, "launchctl timed out"


def install_launchd() -> dict[str, Any]:
    path = _write_launchd_plist()
    code1, out1 = _launchctl("unload", str(path))
    code, out = _launchctl("load", "-w", str(path))
    return {
        "ok": code == 0,
        "platform": "macos",
        "path": str(path),
        "load_rc": code,
        "load_out": out,
        "prior_unload_rc": code1,
        "prior_unload_out": out1,
    }


def uninstall_launchd() -> dict[str, Any]:
    path = _launchd_plist_path()
    out = ""
    code = 0
    if path.is_file():
        code, out = _launchctl("unload", str(path))
        try:
            path.unlink()
        except OSError as e:
            return {"ok": False, "platform": "macos", "path": str(path), "error": str(e)}
    return {"ok": True, "platform": "macos", "path": str(path), "unload_rc": code, "unload_out": out}


# ---------- Linux (systemd --user) ----------------------------------------


def _systemd_unit_path() -> Path:
    return Path.home() / ".config" / "systemd" / "user" / SYSTEMD_UNIT


def _systemd_unit_body() -> str:
    env_lines = "\n".join(f"Environment={k}={v}" for k, v in _env_to_forward().items())
    return (
        "[Unit]\n"
        "Description=ClickMem - explicit-memory server for AI coding agents\n"
        "After=network.target\n\n"
        "[Service]\n"
        "Type=simple\n"
        f"ExecStart={_python_exec()} -m clickmem serve\n"
        f"WorkingDirectory={Path.home()}\n"
        f"{env_lines}\n"
        "Restart=on-failure\n"
        "RestartSec=2s\n"
        f"StandardOutput=append:{_log_dir() / 'server.out.log'}\n"
        f"StandardError=append:{_log_dir() / 'server.err.log'}\n\n"
        "[Install]\n"
        "WantedBy=default.target\n"
    )


def _systemctl(*args: str) -> tuple[int, str]:
    try:
        proc = subprocess.run(
            ["systemctl", "--user", *args],
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
        )
        return proc.returncode, (proc.stdout + proc.stderr).strip()
    except FileNotFoundError:
        return 127, "systemctl not found"
    except subprocess.TimeoutExpired:
        return 124, "systemctl timed out"


def install_systemd() -> dict[str, Any]:
    path = _systemd_unit_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(_systemd_unit_body(), encoding="utf-8")
    code_r, out_r = _systemctl("daemon-reload")
    code_e, out_e = _systemctl("enable", "--now", SYSTEMD_UNIT)
    return {
        "ok": code_e == 0,
        "platform": "linux",
        "path": str(path),
        "daemon_reload_rc": code_r,
        "daemon_reload_out": out_r,
        "enable_rc": code_e,
        "enable_out": out_e,
    }


def uninstall_systemd() -> dict[str, Any]:
    path = _systemd_unit_path()
    code_d, out_d = _systemctl("disable", "--now", SYSTEMD_UNIT)
    if path.is_file():
        try:
            path.unlink()
        except OSError as e:
            return {"ok": False, "platform": "linux", "path": str(path), "error": str(e)}
    code_r, out_r = _systemctl("daemon-reload")
    return {
        "ok": True,
        "platform": "linux",
        "path": str(path),
        "disable_rc": code_d,
        "disable_out": out_d,
        "daemon_reload_rc": code_r,
        "daemon_reload_out": out_r,
    }


# ---------- Dispatch ------------------------------------------------------


def install() -> dict[str, Any]:
    sysname = platform.system().lower()
    if sysname == "darwin":
        return install_launchd()
    if sysname.startswith("linux"):
        return install_systemd()
    return {
        "ok": False,
        "platform": sysname,
        "error": "unsupported platform",
        "note": "this needs a real install path on Windows; currently only macOS (launchd) and Linux (systemd --user) are wired",
    }


def uninstall() -> dict[str, Any]:
    sysname = platform.system().lower()
    if sysname == "darwin":
        return uninstall_launchd()
    if sysname.startswith("linux"):
        return uninstall_systemd()
    return {"ok": False, "platform": sysname, "error": "unsupported platform"}


def status() -> dict[str, Any]:
    sysname = platform.system().lower()
    if sysname == "darwin":
        path = _launchd_plist_path()
        code, out = _launchctl("list", LAUNCHD_LABEL)
        return {"platform": "macos", "path": str(path), "installed": path.is_file(), "list_rc": code, "list_out": out}
    if sysname.startswith("linux"):
        path = _systemd_unit_path()
        code, out = _systemctl("status", SYSTEMD_UNIT)
        return {"platform": "linux", "path": str(path), "installed": path.is_file(), "status_rc": code, "status_out": out}
    return {"platform": sysname, "installed": False, "error": "unsupported platform"}


__all__ = ["install", "uninstall", "status", "LAUNCHD_LABEL", "SYSTEMD_UNIT"]
