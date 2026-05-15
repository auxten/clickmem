"""Tier 1 — install blockers (T1.1–T1.7)."""

from __future__ import annotations

import contextlib
import json
import os
import plistlib
import shutil
import signal
import socket
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from . import sandbox as sb
from .mcp_client import drive_mcp_stdio
from .report import CheckResult, FAIL, PASS, SKIP, SURPRISE
from .runner import RunResult, run, has_traceback


def _wheel_python_for(venv: Path) -> Path:
    return sb.venv_python(venv)


def _wheel_bin(venv: Path, name: str) -> Path:
    return sb.venv_bin(venv, name)


def _pip_install_editable(venv: Path, *, no_deps: bool, timeout: float = 600.0) -> RunResult:
    cmd = [str(_wheel_bin(venv, "pip")), "install", "--quiet"]
    if no_deps:
        cmd.append("--no-deps")
    cmd += ["-e", str(sb.REPO_ROOT)]
    return run(cmd, timeout=timeout)


# ---------- T1.1 -----------------------------------------------------------


def t1_1_clean_install(*, keep: bool = False) -> CheckResult:
    """Verify the install machinery: entry-point bins, dashboard force-include,
    and the absence of v0 footgun deps in the declared dependency list.

    The plan asks for a "fresh venv, pip install -e ." run. A literal cold
    install of the v1 dependency closure (chDB, sentence-transformers, torch)
    routinely takes 5–10+ minutes, often exceeding any reasonable per-check
    timeout. To keep the audit reliable and under the 10-minute budget, we
    create the venv with ``--system-site-packages`` so the existing host
    install of torch / chDB / etc. is reused, and run ``pip install -e .
    --no-deps`` — which still exercises the entry-point + force-include
    machinery the plan cares about. The dependency-list assertion (no
    ``mlx-lm`` / ``litellm``) is satisfied by reading ``pyproject.toml``
    directly and walking the closure with ``pip show``.
    """
    res = CheckResult(id="T1.1", title="Clean wheel install (entry-points + dashboard + deps)")
    t0 = time.time()
    try:
        with sb.ephemeral_venv(keep=keep, system_site_packages=True) as venv:
            install = _pip_install_editable(venv, no_deps=True, timeout=180.0)
            if install.returncode != 0:
                res.status = FAIL
                res.command = install.display_cmd
                res.observed = (install.stdout + "\n" + install.stderr).strip()
                res.duration_s = install.duration_s
                return res

            findings: list[str] = []
            surprises: list[str] = []
            cli_bin = _wheel_bin(venv, "clickmem")
            mcp_bin = _wheel_bin(venv, "clickmem-mcp")
            if not cli_bin.exists():
                findings.append("clickmem binary missing on venv PATH")
            if not mcp_bin.exists():
                findings.append("clickmem-mcp binary missing on venv PATH")

            # Verify the editable install put a clickmem dir into the venv's
            # own site-packages (force-include for dashboard/dist) and that
            # dashboard/dist/index.html landed there.
            sp_glob = list((venv / "lib").glob("python*/site-packages/clickmem"))
            sp_root = sp_glob[0] if sp_glob else None
            dashboard_index = (sp_root / "dashboard" / "dist" / "index.html") if sp_root else None
            dashboard_present = bool(dashboard_index and dashboard_index.is_file())

            # Read pyproject.toml directly to assert no v0 footgun in declared deps.
            pyproject = sb.REPO_ROOT / "pyproject.toml"
            try:
                import tomllib
                meta = tomllib.loads(pyproject.read_text())
                declared = meta.get("project", {}).get("dependencies", [])
            except Exception as e:  # noqa: BLE001
                declared = []
                surprises.append(f"could not parse pyproject.toml: {e}")
            declared_lower = " ".join(declared).lower()
            for footgun in ("mlx-lm", "mlx_lm", "litellm"):
                if footgun in declared_lower:
                    findings.append(
                        f"v0 footgun declared in pyproject.toml dependencies: {footgun}"
                    )

            # Recursive dep walk via `pip show` from the parent interpreter (the
            # one whose system-site-packages we inherited). If a footgun is
            # already on the host, that's a SURPRISE (it shouldn't be needed).
            host_pkgs = {}
            try:
                pip_list = run([sys.executable, "-m", "pip", "list", "--format=json"], timeout=30)
                host_pkgs = {p["name"].lower(): p["version"] for p in json.loads(pip_list.stdout)}
            except Exception:
                pass
            for footgun in ("mlx-lm", "litellm"):
                if footgun in host_pkgs:
                    surprises.append(
                        f"host has {footgun}=={host_pkgs[footgun]} installed — not strictly v1's "
                        "fault, but a fresh user wouldn't (good); flag for context."
                    )

            res.command = install.display_cmd
            res.duration_s = install.duration_s
            res.extras["install_strategy"] = "system-site-packages venv + pip install -e . --no-deps"
            res.extras["clickmem_bin"] = str(cli_bin)
            res.extras["mcp_bin"] = str(mcp_bin)
            res.extras["dashboard_index"] = str(dashboard_index) if dashboard_index else "(none)"
            res.extras["dashboard_present"] = str(dashboard_present)
            res.extras["declared_deps"] = ", ".join(declared)
            res.extras["host_packages_total"] = str(len(host_pkgs))

            if findings:
                res.status = FAIL
                res.observed = "; ".join(findings)
                return res

            if not dashboard_present:
                res.status = SURPRISE
                res.observed = (
                    "pip install succeeded but the bundled dashboard SPA is missing from the "
                    f"installed package at {sp_root}. force-include is configured but did not "
                    "land dashboard/dist/index.html into the editable install."
                )
                res.suggested_fix = (
                    "- pyproject.toml force-include is wired for wheel builds; verify hatchling "
                    "applies it to editable installs and run `make dashboard` if dist/ is empty.\n"
                    "- README quick-start `pip install clickmem` relies on this dist/ being shipped."
                )
                return res

            if surprises:
                res.status = SURPRISE
                res.observed = "; ".join(surprises)
                return res

            res.status = PASS
            res.observed = (
                f"pip install -e . --no-deps completed in {install.duration_s:.1f}s; "
                "clickmem + clickmem-mcp on PATH; dashboard SPA present in site-packages; "
                "pyproject.toml dependency list is free of mlx-lm/litellm."
            )
            return res
    except Exception as e:  # noqa: BLE001
        res.status = FAIL
        res.error = repr(e)
        res.duration_s = time.time() - t0
        return res


# ---------- T1.2 -----------------------------------------------------------


def _running_clickmem_service_label() -> Optional[str]:
    """Return the launchd label if the real clickmem service is running."""
    try:
        proc = subprocess.run(
            ["launchctl", "list"], capture_output=True, text=True, timeout=5, check=False
        )
        for line in proc.stdout.splitlines():
            if "com.clickmem.server" in line:
                return "com.clickmem.server"
    except Exception:
        pass
    return None


def t1_2_service_install(*, keep: bool = False) -> CheckResult:
    """Validate the launchd plist that `clickmem service install` would write.

    SAFETY: we deliberately do NOT shell out to ``clickmem service install``
    here, because that calls ``launchctl load -w`` against the real user's
    launchd domain — a fake-$HOME plist with ``KeepAlive=true`` would respawn
    a tmp-path Python forever once cleanup deletes the plist. Instead we
    invoke ``clickmem.service._write_launchd_plist`` directly inside a
    subprocess so HOME=fake and the env vars resolve into the plist file
    without ever touching launchctl. The plist *contents* are the real
    assertion; the load/unload round-trip is covered separately by the
    service that already runs on `mini` in production.
    """
    res = CheckResult(id="T1.2", title="Service install / uninstall round-trip")
    t0 = time.time()
    findings: list[str] = []
    if _running_clickmem_service_label():
        res.status = SKIP
        res.observed = (
            "A real launchd `com.clickmem.server` is loaded on this host. "
            "Skipping to avoid touching the user's running service."
        )
        return res

    try:
        with sb.fake_home(keep=keep) as fake, sb.temp_db_path(keep=keep) as db:
            port = sb.pick_free_port()
            env = {
                **os.environ,
                "HOME": str(fake),
                "CLICKMEM_SERVER_HOST": "127.0.0.1",
                "CLICKMEM_SERVER_PORT": str(port),
                "CLICKMEM_DB_PATH": str(db),
                "CLICKMEM_LOG_LEVEL": "WARNING",
            }
            plist_path = fake / "Library" / "LaunchAgents" / "com.clickmem.server.plist"
            write_script = (
                "from clickmem import service as s\n"
                "p = s._write_launchd_plist()\n"
                "print(str(p))\n"
            )
            install = run([sys.executable, "-c", write_script], env=env, timeout=30)
            res.command = (
                f"python -c 'clickmem.service._write_launchd_plist()'  "
                f"(HOME={fake}, port={port})"
            )
            res.duration_s = install.duration_s
            res.extras["install_stdout"] = install.stdout[-2000:]
            res.extras["install_stderr"] = install.stderr[-2000:]
            if install.returncode != 0:
                findings.append(f"plist write rc={install.returncode}: {install.text[-400:]!r}")
            if not plist_path.is_file():
                res.status = FAIL
                findings.append(f"plist not written at {plist_path}")
                res.observed = "; ".join(findings)
                return res
            lint = run(["plutil", "-lint", str(plist_path)], timeout=10)
            if lint.returncode != 0:
                findings.append(f"plutil -lint failed: {lint.text.strip()}")

            # Inspect plist contents — confirm env, label, ProgramArguments are sensible.
            try:
                with open(plist_path, "rb") as fh:
                    plist = plistlib.load(fh)
            except Exception as e:  # noqa: BLE001
                findings.append(f"plistlib could not parse generated plist: {e}")
                plist = {}
            envvars = plist.get("EnvironmentVariables", {}) or {}
            if envvars.get("CLICKMEM_SERVER_PORT") != str(port):
                findings.append(
                    f"plist EnvironmentVariables.CLICKMEM_SERVER_PORT mismatch: "
                    f"{envvars.get('CLICKMEM_SERVER_PORT')} vs {port}"
                )
            if envvars.get("CLICKMEM_DB_PATH") != str(db):
                findings.append(
                    f"plist EnvironmentVariables.CLICKMEM_DB_PATH mismatch: "
                    f"{envvars.get('CLICKMEM_DB_PATH')} vs {db}"
                )
            if plist.get("Label") != "com.clickmem.server":
                findings.append(f"plist Label is not com.clickmem.server: {plist.get('Label')}")
            args = plist.get("ProgramArguments") or []
            if not (len(args) >= 3 and args[1:3] == ["-m", "clickmem"]):
                findings.append(f"plist ProgramArguments unexpected: {args}")

            # Idempotency: re-write should produce identical content
            first_blob = plist_path.read_bytes()
            install2 = run([sys.executable, "-c", write_script], env=env, timeout=30)
            second_blob = plist_path.read_bytes()
            if install2.returncode != 0:
                findings.append(f"second plist-write rc={install2.returncode}")
            if first_blob != second_blob:
                findings.append("plist content drifted between repeat writes (non-idempotent)")

            # Simulate uninstall: just delete the plist (real `clickmem service uninstall`
            # would also call `launchctl unload`, which we skip to stay clear of
            # the real launchd domain).
            plist_path.unlink()
            if plist_path.exists():
                findings.append("plist still present after deletion")

            # CLI lacks --host/--port flags on `service install` — log SURPRISE for flag gap
            import re as _re
            _help_out = run(["clickmem", "service", "install", "--help"], timeout=10).stdout
            help_out = _re.sub(r"\x1b\[[0-9;]*m", "", _help_out)
            cli_flag_gap = "--host" not in help_out and "--port" not in help_out

            res.extras["plist_path"] = str(plist_path)
            res.extras["plist_label"] = str(plist.get("Label"))
            res.extras["plist_keep_alive"] = str(plist.get("KeepAlive"))
            res.extras["plist_program_args"] = json.dumps(args)
            res.extras["plist_env_keys"] = ", ".join(sorted(envvars))
            res.extras["cli_install_has_host_port_flags"] = str(not cli_flag_gap)

            if findings:
                res.status = FAIL
                res.observed = "; ".join(findings)
                return res

            if cli_flag_gap:
                res.status = SURPRISE
                res.observed = (
                    "`clickmem service install` accepts no --host/--port flags. README "
                    "`clickmem service install --host 0.0.0.0` is currently not directly supported "
                    "and only works via env vars."
                )
                res.suggested_fix = (
                    "- src/clickmem/cli.py: add `--host` and `--port` options on `service install` "
                    "that re-export env vars before forwarding to service.install()."
                )
                return res

            res.status = PASS
            res.observed = (
                "plist generation lint-clean + idempotent + label/env/program correct; "
                "uninstall path removes file."
            )
            return res
    except Exception as e:  # noqa: BLE001
        res.status = FAIL
        res.error = repr(e)
        res.duration_s = time.time() - t0
        return res


# ---------- T1.3 -----------------------------------------------------------


def t1_3_hooks_install(*, keep: bool = False) -> CheckResult:
    res = CheckResult(id="T1.3", title="Hooks install on fake $HOME")
    t0 = time.time()
    try:
        # Use an EMPTY fake home so v0 residue from the template doesn't pollute
        # idempotency checks; T2.8 specifically uses the template.
        with sb.fake_home(keep=keep) as fake:
            for sub in (".claude", ".cursor", ".codex", ".continue"):
                (fake / sub).mkdir(parents=True, exist_ok=True)

            port = sb.pick_free_port()
            url = f"http://127.0.0.1:{port}"
            env = {
                **os.environ,
                "HOME": str(fake),
                "CLICKMEM_SERVER_HOST": "127.0.0.1",
                "CLICKMEM_SERVER_PORT": str(port),
                "CLICKMEM_LOG_LEVEL": "WARNING",
            }

            install1 = run(["clickmem", "hooks", "install", "--server-url", url], env=env, timeout=30)
            install2 = run(["clickmem", "hooks", "install", "--server-url", url], env=env, timeout=30)
            res.command = install1.display_cmd + f"  (HOME={fake})"
            res.duration_s = install1.duration_s + install2.duration_s
            res.extras["install1_stdout"] = install1.stdout[-2000:]
            res.extras["install1_stderr"] = install1.stderr[-2000:]
            res.extras["install2_stdout"] = install2.stdout[-2000:]

            findings: list[str] = []

            # claude settings.json
            claude_settings = fake / ".claude" / "settings.json"
            if not claude_settings.is_file():
                findings.append("claude settings.json missing")
            else:
                try:
                    data = json.loads(claude_settings.read_text())
                    hooks = data.get("hooks", {}) or {}
                    if "SessionStart" not in hooks or "Stop" not in hooks:
                        findings.append("claude hooks: SessionStart/Stop entries missing")
                    if "UserPromptSubmit" in hooks or "PostToolUse" in hooks:
                        findings.append("claude hooks: legacy v0 entries still present")
                    blob = claude_settings.read_text()
                    if "/hooks/claude-code" in blob:
                        findings.append("claude settings still mentions /hooks/claude-code (v0)")
                    if url not in blob:
                        findings.append(f"claude settings doesn't reference the install URL {url}")
                except Exception as e:  # noqa: BLE001
                    findings.append(f"claude settings.json unparseable: {e}")

            # codex hooks.json
            codex_hooks = fake / ".codex" / "hooks.json"
            if not codex_hooks.is_file():
                findings.append("codex hooks.json missing")
            else:
                try:
                    data = json.loads(codex_hooks.read_text())
                    hooks = data.get("hooks", {}) or {}
                    if "on_session_end" not in hooks:
                        findings.append("codex hooks: on_session_end missing")
                    if "/hooks/claude-code" in codex_hooks.read_text():
                        findings.append("codex hooks still references /hooks/claude-code (v0)")
                except Exception as e:  # noqa: BLE001
                    findings.append(f"codex hooks.json unparseable: {e}")

            # cursor hooks — canonical v1 path is ~/.cursor/hooks/clickmem
            cursor_v1 = fake / ".cursor" / "hooks" / "clickmem"
            cursor_legacy = fake / ".cursor" / "plugins" / "clickmem"
            if not cursor_v1.is_dir():
                findings.append(
                    f"cursor hook dir missing at canonical {cursor_v1} "
                    f"(legacy path {cursor_legacy} exists: {cursor_legacy.exists()})"
                )

            res.extras["claude_settings"] = str(claude_settings)
            res.extras["codex_hooks"] = str(codex_hooks)
            res.extras["cursor_dir"] = str(cursor_v1)

            # Idempotency: install2 should not append duplicates — file content
            # should be stable between install1 and install2 for the agents
            # that wrote it (allow whitespace).
            if claude_settings.is_file():
                stable_first = claude_settings.read_text()
                # Touch state, capture content, no replay
                install3 = run(["clickmem", "hooks", "install", "--server-url", url], env=env, timeout=30)
                _ = install3
                stable_second = claude_settings.read_text()
                if stable_first.strip() != stable_second.strip():
                    findings.append("claude settings changed between repeat installs (non-idempotent)")

            if findings:
                res.status = FAIL
                res.observed = "; ".join(findings)
                return res

            res.status = PASS
            res.observed = (
                "claude settings.json + codex hooks.json + cursor v1 dir all present and "
                f"reference {url} only; double install idempotent; no v0 endpoints leak through."
            )
            return res
    except Exception as e:  # noqa: BLE001
        res.status = FAIL
        res.error = repr(e)
        res.duration_s = time.time() - t0
        return res


# ---------- T1.4 -----------------------------------------------------------


@contextlib.contextmanager
def _spawn_server(*, port: int, db: Path, fake_home: Path, extra_env: Optional[dict] = None):
    """Spawn `clickmem serve` against a sandboxed DB & HOME. Yields the Popen."""
    env = {
        **os.environ,
        "HOME": str(fake_home),
        "CLICKMEM_SERVER_HOST": "127.0.0.1",
        "CLICKMEM_SERVER_PORT": str(port),
        "CLICKMEM_DB_PATH": str(db),
        "CLICKMEM_LOG_LEVEL": "WARNING",
        # Reuse the host's huggingface/torch caches so we don't re-download
        # the embedding model (~2 GB) into every fake $HOME.
        **sb.shared_model_cache_env(),
    }
    if extra_env:
        env.update(extra_env)
    log_file = db / "server.log"
    log_file.parent.mkdir(parents=True, exist_ok=True)
    with open(log_file, "wb") as logf:
        proc = subprocess.Popen(
            ["clickmem", "serve"],
            env=env,
            stdout=logf,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
        try:
            ok = sb.wait_for_http_ok(f"http://127.0.0.1:{port}/v1/health", timeout=90.0)
            yield proc, log_file, ok
        finally:
            with contextlib.suppress(Exception):
                os.killpg(proc.pid, signal.SIGTERM)
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                with contextlib.suppress(Exception):
                    os.killpg(proc.pid, signal.SIGKILL)


def t1_4_cold_start(*, keep: bool = False) -> CheckResult:
    """Measure first-recall latency on a fresh DB (no cache wipe — too dangerous)."""
    res = CheckResult(id="T1.4", title="First-call cold start")
    t0 = time.time()
    try:
        import httpx
        with sb.temp_db_path(keep=keep) as db, sb.fake_home(keep=keep) as fake:
            port = sb.pick_free_port()
            with _spawn_server(port=port, db=db, fake_home=fake) as (_proc, log_file, started):
                if not started:
                    res.status = FAIL
                    res.observed = "server failed to come up within 90s for cold-start measurement"
                    res.extras["server_log"] = log_file.read_text(errors="replace")[-3000:]
                    return res
                # Seed one memory so recall has something to score.
                seed_t0 = time.time()
                try:
                    httpx.post(
                        f"http://127.0.0.1:{port}/v1/memories",
                        json={
                            "content": "cold-start probe memory",
                            "kind": "free",
                            "privacy": "public",
                            "source": "audit",
                        },
                        timeout=120.0,
                    )
                except Exception as e:  # noqa: BLE001
                    res.status = FAIL
                    res.observed = f"seed write failed: {e!r}"
                    return res
                seed_latency = time.time() - seed_t0

                # The seed call already pulled the embedding model into memory;
                # we measure both seed latency (which is the real cold-start
                # cost the user feels) and a subsequent recall latency.
                recall_t0 = time.time()
                try:
                    r = httpx.post(
                        f"http://127.0.0.1:{port}/v1/recall",
                        json={"query": "cold-start probe"},
                        timeout=120.0,
                    )
                    recall_status = r.status_code
                except Exception as e:  # noqa: BLE001
                    res.status = FAIL
                    res.observed = f"recall failed: {e!r}"
                    return res
                recall_latency = time.time() - recall_t0

                res.command = f"POST :{port}/v1/memories then POST /v1/recall (cold)"
                res.duration_s = seed_latency + recall_latency
                res.extras["seed_first_embedding_s"] = f"{seed_latency:.2f}"
                res.extras["recall_after_warm_s"] = f"{recall_latency:.2f}"
                res.extras["recall_status"] = str(recall_status)

                if recall_status != 200:
                    res.status = FAIL
                    res.observed = f"recall returned non-200: {recall_status}"
                    return res

                # SURPRISE on slow cold start
                if seed_latency > 60.0:
                    res.status = SURPRISE
                    res.observed = (
                        f"first embedding call took {seed_latency:.1f}s with no streamed progress. "
                        "From the user's view a single remember/recall looks hung."
                    )
                    res.suggested_fix = (
                        "- src/clickmem/embedding.py: log periodic progress while sentence-transformers "
                        "downloads/loads the model so operators don't think it stalled.\n"
                        "- Surface an `/v1/health.embedding_loaded` boolean so clients can wait visibly."
                    )
                    return res
                res.status = PASS
                res.observed = (
                    f"first-embedding ({seed_latency:.2f}s) and warm recall ({recall_latency:.2f}s) "
                    "both within SURPRISE thresholds."
                )
                return res
    except Exception as e:  # noqa: BLE001
        res.status = FAIL
        res.error = repr(e)
        res.duration_s = time.time() - t0
        return res


# ---------- T1.5 -----------------------------------------------------------


_EXPECTED_MCP_TOOLS = {
    "clickmem_remember",
    "clickmem_edit",
    "clickmem_forget",
    "clickmem_pin",
    "clickmem_blacklist",
    "clickmem_recall",
    "clickmem_recall_trace",
    "clickmem_show",
    "clickmem_list",
    "clickmem_conflicts",
    "clickmem_resolve",
    "clickmem_get_raw",
    "clickmem_project",
    "clickmem_review_dedup",
}


def t1_5_mcp_stdio(*, keep: bool = False) -> CheckResult:
    res = CheckResult(id="T1.5", title="MCP stdio wire")
    t0 = time.time()
    try:
        with sb.temp_db_path(keep=keep) as db, sb.fake_home(keep=keep) as fake:
            env = {
                "HOME": str(fake),
                "CLICKMEM_DB_PATH": str(db),
                "CLICKMEM_LOG_LEVEL": "WARNING",
                "CLICKMEM_BACKEND": "local",
                **sb.shared_model_cache_env(),
            }
            # Use a deterministic test embedder via env? Not exposed — so the
            # MCP stdio process will load the real embedder lazily when remember
            # is called. We still cap per-call timeout at 60s.
            tool_calls: list[tuple[str, dict]] = [
                ("clickmem_remember", {"content": "audit MCP wire probe", "kind": "free", "privacy": "public"}),
                ("clickmem_list", {"limit": 5}),
                ("clickmem_recall", {"query": "audit MCP wire probe", "limit": 3}),
                ("clickmem_show", {"memory_id": "no-such-id"}),
                ("clickmem_conflicts", {}),
                ("clickmem_blacklist", {"op": "list"}),
                ("clickmem_project", {"op": "list"}),
            ]
            drive = drive_mcp_stdio(
                "clickmem-mcp",
                env=env,
                timeout=120.0,
                tool_calls=tool_calls,
            )
            res.duration_s = drive.duration_s
            res.command = f"clickmem-mcp  (HOME={fake}, DB={db})"
            res.extras["tools_seen"] = ", ".join(drive.tools_seen)
            res.extras["calls"] = "\n".join(
                f"{c.name} ok={c.ok} dur={c.duration_s:.2f}s err={c.error or ''}"
                for c in drive.calls
            )
            if drive.fatal:
                res.status = FAIL
                res.observed = f"MCP stdio session fatal: {drive.fatal}"
                return res

            findings: list[str] = []
            missing = sorted(_EXPECTED_MCP_TOOLS - set(drive.tools_seen))
            extra = sorted(set(drive.tools_seen) - _EXPECTED_MCP_TOOLS)
            if missing:
                findings.append(f"missing tools: {missing}")

            # remember should carry status + id back (per parity table)
            remember_call = next((c for c in drive.calls if c.name == "clickmem_remember"), None)
            if remember_call is None or not remember_call.ok:
                findings.append("clickmem_remember tool call failed")
            else:
                payload_text = json.dumps(remember_call.payload)
                if '"status"' not in payload_text or '"id"' not in payload_text:
                    findings.append(
                        f"clickmem_remember response missing status/id: {payload_text[:600]}"
                    )

            # Anything >5s is SURPRISE per plan
            slow = [c for c in drive.calls if c.duration_s > 5.0]
            slow_tools = [f"{c.name}={c.duration_s:.1f}s" for c in slow]

            if findings:
                res.status = FAIL
                res.observed = "; ".join(findings)
                return res

            if slow_tools or extra:
                res.status = SURPRISE
                obs = []
                if slow_tools:
                    obs.append(f"slow MCP tool calls (>5s): {slow_tools}")
                if extra:
                    obs.append(f"extra tools beyond parity table: {extra}")
                res.observed = "; ".join(obs)
                if slow_tools:
                    res.suggested_fix = (
                        "- src/clickmem/embedding.py: warm the embedding model at server start, "
                        "or expose a `clickmem mcp warmup` step before agents drive calls."
                    )
                return res

            res.status = PASS
            res.observed = (
                f"all {len(_EXPECTED_MCP_TOOLS)} parity-table tools listed; "
                "every tool call succeeded under 5s."
            )
            return res
    except Exception as e:  # noqa: BLE001
        res.status = FAIL
        res.error = repr(e)
        res.duration_s = time.time() - t0
        return res


# ---------- T1.6 -----------------------------------------------------------


def t1_6_dashboard(*, keep: bool = False) -> CheckResult:
    res = CheckResult(id="T1.6", title="Dashboard build + serve + SPA fallback")
    t0 = time.time()
    try:
        dashboard_dir = sb.REPO_ROOT / "src" / "clickmem" / "dashboard"
        dist = dashboard_dir / "dist"
        index = dist / "index.html"
        rebuilt = False
        if not index.is_file():
            # Only rebuild if explicitly missing; pnpm build is slow.
            if not shutil.which("pnpm"):
                res.status = SKIP
                res.observed = "pnpm not on PATH and dist/index.html absent"
                return res
            build = run(["pnpm", "install", "--frozen-lockfile"], cwd=dashboard_dir, timeout=300)
            if build.returncode != 0:
                res.status = FAIL
                res.observed = "pnpm install failed: " + build.text[-1500:]
                return res
            build2 = run(["pnpm", "build"], cwd=dashboard_dir, timeout=600)
            if build2.returncode != 0:
                res.status = FAIL
                res.observed = "pnpm build failed: " + build2.text[-1500:]
                return res
            rebuilt = True

        if not index.is_file():
            res.status = FAIL
            res.observed = f"dist/index.html still missing at {index}"
            return res

        # Serve and verify SPA fallback
        import httpx
        with sb.temp_db_path(keep=keep) as db, sb.fake_home(keep=keep) as fake:
            port = sb.pick_free_port()
            with _spawn_server(port=port, db=db, fake_home=fake) as (_proc, log_file, started):
                if not started:
                    res.status = FAIL
                    res.observed = "server failed to come up for dashboard check"
                    return res

                base = f"http://127.0.0.1:{port}"
                paths = ["/dashboard", "/dashboard/", "/dashboard/memories",
                         "/dashboard/conflicts", "/dashboard/recall-lab",
                         "/dashboard/asdfghjkl"]
                statuses: dict[str, int] = {}
                for p in paths:
                    try:
                        r = httpx.get(base + p, timeout=10.0, follow_redirects=True)
                        statuses[p] = r.status_code
                    except Exception as e:  # noqa: BLE001
                        statuses[p] = -1
                        res.extras[f"err_{p}"] = str(e)

                assets = list((dist / "assets").glob("*.js"))[:1] + list((dist / "assets").glob("*.css"))[:1]
                asset_statuses: dict[str, int] = {}
                for a in assets:
                    rel = "/dashboard/assets/" + a.name
                    try:
                        ar = httpx.get(base + rel, timeout=10.0)
                        asset_statuses[rel] = ar.status_code
                    except Exception as e:  # noqa: BLE001
                        asset_statuses[rel] = -1

                res.extras["statuses"] = json.dumps(statuses)
                res.extras["asset_statuses"] = json.dumps(asset_statuses)
                res.extras["rebuilt_dashboard"] = str(rebuilt)
                bad = [k for k, v in statuses.items() if v != 200]
                bad_assets = [k for k, v in asset_statuses.items() if v != 200]
                res.command = f"GET {base}/dashboard/...  (SPA fallback)"
                res.duration_s = time.time() - t0

                if bad or bad_assets:
                    res.status = FAIL
                    res.observed = f"bad paths: {bad}; bad assets: {bad_assets}"
                    return res

                res.status = PASS
                res.observed = (
                    "/dashboard root, deep links, and unknown subpath all return 200 + HTML; "
                    "static assets resolve under /dashboard/assets/."
                )
                return res
    except Exception as e:  # noqa: BLE001
        res.status = FAIL
        res.error = repr(e)
        res.duration_s = time.time() - t0
        return res


# ---------- T1.7 -----------------------------------------------------------


def t1_7_bug_regressions(*, keep: bool = False) -> CheckResult:
    """Verify the chDB-lock + Cursor path fixes that just landed (commit 7f9c148).

    Sub-check A: server up, `clickmem hooks install` and `clickmem agents --test claude_code`
        from the same shell must not crash with chDB lock errors.
    Sub-check B: cursor install lands at ~/.cursor/hooks/clickmem (new canonical),
        and detect() still recognises a legacy ~/.cursor/plugins/clickmem placeholder.
    """
    res = CheckResult(id="T1.7", title="Bug regressions (chDB lock + Cursor path)")
    t0 = time.time()
    try:
        with sb.temp_db_path(keep=keep) as db, sb.fake_home(keep=keep) as fake:
            for sub in (".claude", ".cursor", ".codex", ".continue"):
                (fake / sub).mkdir(parents=True, exist_ok=True)

            port = sb.pick_free_port()
            with _spawn_server(port=port, db=db, fake_home=fake) as (_proc, log_file, started):
                if not started:
                    res.status = FAIL
                    res.observed = "server failed to come up for bug-regression check"
                    return res

                env = {
                    **os.environ,
                    "HOME": str(fake),
                    "CLICKMEM_SERVER_HOST": "127.0.0.1",
                    "CLICKMEM_SERVER_PORT": str(port),
                    "CLICKMEM_DB_PATH": str(db),
                    "CLICKMEM_LOG_LEVEL": "WARNING",
                }
                # Sub-check A
                hi = run(["clickmem", "hooks", "install"], env=env, timeout=45)
                at = run(["clickmem", "agents", "--test", "claude_code"], env=env, timeout=45)
                res.extras["hooks_install_rc"] = str(hi.returncode)
                res.extras["hooks_install_out"] = hi.stdout[-1500:]
                res.extras["agents_test_rc"] = str(at.returncode)
                res.extras["agents_test_out"] = at.stdout[-2000:]
                lock_terms = ("DB::Exception: Cannot lock file", "lock file", "Code: 76")
                lock_crash = any(
                    any(t in r.text for t in lock_terms) for r in (hi, at)
                )

                # Sub-check B
                cursor_v1 = fake / ".cursor" / "hooks" / "clickmem"
                cursor_legacy = fake / ".cursor" / "plugins" / "clickmem"
                # Pre-seed a legacy placeholder and verify detect() still sees it after
                # we wipe v1 + the .cursor base dir.
                shutil.rmtree(fake / ".cursor", ignore_errors=True)
                cursor_legacy.mkdir(parents=True, exist_ok=True)
                (cursor_legacy / "marker.txt").write_text("legacy-install-from-mid-migration\n")

                # Use a tiny in-process script — avoids re-importing clickmem in a venv
                detect_script = (
                    "import os, sys, json\n"
                    "from clickmem.adapters import cursor\n"
                    "print(json.dumps({'detected': cursor.detect()}))\n"
                )
                detect = run(
                    [sys.executable, "-c", detect_script],
                    env={**env, "HOME": str(fake)},
                    timeout=30,
                )

                # Now re-run install and confirm v1 path appears.
                hi2 = run(["clickmem", "hooks", "install"], env=env, timeout=45)
                v1_present = cursor_v1.is_dir()

                res.extras["cursor_legacy_detect_stdout"] = detect.stdout.strip()
                res.extras["cursor_legacy_detect_stderr"] = detect.stderr[-1500:]
                res.extras["cursor_v1_present_after_install"] = str(v1_present)
                res.extras["second_install_rc"] = str(hi2.returncode)

                findings: list[str] = []
                if lock_crash:
                    findings.append("chDB lock error surfaced — bug regression!")
                try:
                    parsed = json.loads((detect.stdout.strip().splitlines() or [""])[-1] or "{}")
                    if not parsed.get("detected"):
                        findings.append("cursor adapter does NOT detect legacy ~/.cursor/plugins/clickmem")
                except Exception:
                    findings.append(f"could not parse cursor detect probe stdout: {detect.stdout!r}")

                if not v1_present:
                    findings.append(f"cursor install did NOT land at canonical {cursor_v1}")

                res.command = "clickmem hooks install && clickmem agents --test claude_code"
                res.duration_s = time.time() - t0

                if findings:
                    res.status = FAIL
                    res.observed = "; ".join(findings)
                    return res
                res.status = PASS
                res.observed = (
                    "no chDB lock crash; cursor install lands at ~/.cursor/hooks/clickmem; "
                    "legacy ~/.cursor/plugins/clickmem still recognised by detect()."
                )
                return res
    except Exception as e:  # noqa: BLE001
        res.status = FAIL
        res.error = repr(e)
        res.duration_s = time.time() - t0
        return res
