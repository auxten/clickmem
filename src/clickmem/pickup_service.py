"""Install the serverless Daily Pickup user timer on macOS or Linux."""

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


LAUNCHD_LABEL = "com.clickmem.pickup"
SYSTEMD_SERVICE = "clickmem-pickup.service"
SYSTEMD_TIMER = "clickmem-pickup.timer"


def _python_exec() -> str:
    return sys.executable or shutil.which("python3") or "python3"


def _log_dir() -> Path:
    path = Path.home() / ".clickmem" / "logs"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _env(summarizer: str, agents: list[str]) -> dict[str, str]:
    cfg = get_config(refresh=True)
    values = {
        "CLICKMEM_SERVER_HOST": cfg.server_host,
        "CLICKMEM_SERVER_PORT": str(cfg.server_port),
        "CLICKMEM_PICKUP_SUMMARIZER": summarizer,
        "CLICKMEM_PICKUP_AGENTS": ",".join(agents),
        "CLICKMEM_PICKUP_LOOKBACK_HOURS": str(cfg.pickup_lookback_hours),
        "CLICKMEM_PICKUP_MAX_SESSION_CHARS": str(cfg.pickup_max_session_chars),
        "CLICKMEM_PICKUP_SPOOL_DIR": str(cfg.pickup_spool_dir),
        "CLICKMEM_PICKUP_NOTIFY": "true" if cfg.pickup_notify else "false",
        "PATH": os.environ.get("PATH", "/usr/local/bin:/usr/bin:/bin"),
    }
    if cfg.remote_url:
        values["CLICKMEM_REMOTE"] = cfg.remote_url
    if cfg.api_key:
        values["CLICKMEM_API_KEY"] = cfg.api_key
    if cfg.pickup_command:
        values["CLICKMEM_PICKUP_COMMAND"] = cfg.pickup_command
    return values


def _run(argv: list[str]) -> tuple[int, str]:
    try:
        proc = subprocess.run(argv, capture_output=True, text=True, timeout=20, check=False)
        return proc.returncode, (proc.stdout + proc.stderr).strip()
    except (FileNotFoundError, subprocess.TimeoutExpired) as exc:
        return 127, str(exc)


def _launchd_path() -> Path:
    return Path.home() / "Library" / "LaunchAgents" / f"{LAUNCHD_LABEL}.plist"


def _install_launchd(hour: int, minute: int, summarizer: str, agents: list[str]) -> dict[str, Any]:
    path = _launchd_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "Label": LAUNCHD_LABEL,
        "ProgramArguments": [_python_exec(), "-m", "clickmem", "pickup", "now"],
        "StartCalendarInterval": {"Hour": hour, "Minute": minute},
        "EnvironmentVariables": _env(summarizer, agents),
        "StandardOutPath": str(_log_dir() / "pickup.out.log"),
        "StandardErrorPath": str(_log_dir() / "pickup.err.log"),
        "WorkingDirectory": str(Path.home()),
    }
    with open(path, "wb") as handle:
        plistlib.dump(payload, handle)
    _run(["launchctl", "unload", str(path)])
    code, output = _run(["launchctl", "load", "-w", str(path)])
    return {"ok": code == 0, "platform": "macos", "path": str(path), "output": output}


def _systemd_dir() -> Path:
    return Path.home() / ".config" / "systemd" / "user"


def _install_systemd(hour: int, minute: int, summarizer: str, agents: list[str]) -> dict[str, Any]:
    directory = _systemd_dir()
    directory.mkdir(parents=True, exist_ok=True)
    service = directory / SYSTEMD_SERVICE
    timer = directory / SYSTEMD_TIMER
    env_lines = "\n".join(
        f'Environment="{key}={value.replace(chr(34), chr(92) + chr(34))}"'
        for key, value in _env(summarizer, agents).items()
    )
    service.write_text(
        "[Unit]\nDescription=ClickMem Daily Pickup\n\n"
        "[Service]\nType=oneshot\n"
        f"ExecStart={_python_exec()} -m clickmem pickup now\n"
        f"WorkingDirectory={Path.home()}\n{env_lines}\n",
        encoding="utf-8",
    )
    timer.write_text(
        "[Unit]\nDescription=Run ClickMem Daily Pickup\n\n"
        f"[Timer]\nOnCalendar=*-*-* {hour:02d}:{minute:02d}:00\nPersistent=true\n\n"
        "[Install]\nWantedBy=timers.target\n",
        encoding="utf-8",
    )
    _run(["systemctl", "--user", "daemon-reload"])
    code, output = _run(["systemctl", "--user", "enable", "--now", SYSTEMD_TIMER])
    return {
        "ok": code == 0,
        "platform": "linux",
        "service": str(service),
        "timer": str(timer),
        "output": output,
    }


def install(
    *,
    hour: int,
    minute: int,
    summarizer: str,
    agents: list[str],
) -> dict[str, Any]:
    if not 0 <= hour <= 23 or not 0 <= minute <= 59:
        raise ValueError("pickup schedule must be a valid 24-hour time")
    system = platform.system().lower()
    if system == "darwin":
        return _install_launchd(hour, minute, summarizer, agents)
    if system.startswith("linux"):
        return _install_systemd(hour, minute, summarizer, agents)
    return {"ok": False, "platform": system, "error": "unsupported platform"}


def uninstall() -> dict[str, Any]:
    system = platform.system().lower()
    if system == "darwin":
        path = _launchd_path()
        _run(["launchctl", "unload", str(path)])
        if path.is_file():
            path.unlink()
        return {"ok": True, "platform": "macos", "path": str(path)}
    if system.startswith("linux"):
        _run(["systemctl", "--user", "disable", "--now", SYSTEMD_TIMER])
        paths = [_systemd_dir() / SYSTEMD_SERVICE, _systemd_dir() / SYSTEMD_TIMER]
        for path in paths:
            if path.is_file():
                path.unlink()
        _run(["systemctl", "--user", "daemon-reload"])
        return {"ok": True, "platform": "linux", "paths": [str(path) for path in paths]}
    return {"ok": False, "platform": system, "error": "unsupported platform"}


def status() -> dict[str, Any]:
    system = platform.system().lower()
    if system == "darwin":
        path = _launchd_path()
        code, output = _run(["launchctl", "list", LAUNCHD_LABEL])
        return {"platform": "macos", "installed": path.is_file(), "active": code == 0, "output": output}
    if system.startswith("linux"):
        path = _systemd_dir() / SYSTEMD_TIMER
        code, output = _run(["systemctl", "--user", "is-active", SYSTEMD_TIMER])
        return {"platform": "linux", "installed": path.is_file(), "active": code == 0, "output": output}
    return {"platform": system, "installed": False, "active": False}
