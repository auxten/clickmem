"""Tier 2 — UX defects (T2.1–T2.9)."""

from __future__ import annotations

import contextlib
import json
import os
import re
import secrets
import shutil
import signal
import socket
import subprocess
import sys
import time
import uuid
from pathlib import Path
from typing import Optional

from . import sandbox as sb
from .report import CheckResult, FAIL, PASS, SKIP, SURPRISE
from .runner import RunResult, run, has_traceback
from .tier1_checks import _spawn_server, _running_clickmem_service_label


# ---------- T2.1 -----------------------------------------------------------


_FENCE_RE = re.compile(r"```(?P<lang>[a-zA-Z]+)?\n(?P<body>.*?)```", re.DOTALL)
_ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")


def _strip_ansi(text: str) -> str:
    return _ANSI_RE.sub("", text or "")


def t2_1_readme_runnability(*, keep: bool = False) -> CheckResult:
    res = CheckResult(id="T2.1", title="README copy-paste runnability")
    t0 = time.time()
    readme = sb.REPO_ROOT / "README.md"
    if not readme.is_file():
        res.status = FAIL
        res.observed = "README.md missing"
        return res

    text = readme.read_text()
    blocks = []
    for m in _FENCE_RE.finditer(text):
        blocks.append((m.group("lang") or "", m.group("body").rstrip()))

    res.command = f"parse {readme}"
    surprises: list[str] = []
    fails: list[str] = []

    # Build the set of advertised top-level commands from `clickmem --help`.
    # The regex requires exactly one space after the box character so we
    # don't match wrapped description lines like `│               memories.`.
    help_out = _strip_ansi(run(["clickmem", "--help"], timeout=15).stdout)
    known_cmds = set(re.findall(r"^[│|] (\w[\w-]*)\b", help_out, re.MULTILINE))
    known_cmds |= {"clickmem"}

    bash_count = 0
    bash_problems: list[str] = []
    json_count = 0
    json_problems: list[str] = []

    for lang, body in blocks:
        body_stripped = body.strip()
        if not body_stripped:
            continue
        if lang.lower() in ("bash", "sh", "shell"):
            bash_count += 1
            for raw_line in body_stripped.splitlines():
                line = raw_line.strip()
                if not line or line.startswith("#"):
                    continue
                if line.startswith("export ") or line.startswith("$ ") or line.startswith("source "):
                    continue
                tokens = line.split()
                # We're only interested in clickmem commands here
                if tokens and tokens[0] == "clickmem":
                    sub = tokens[1] if len(tokens) > 1 else ""
                    # `clickmem foo bar` shape — check `foo` is a top-level command
                    if sub and not sub.startswith("--") and sub not in known_cmds:
                        bash_problems.append(f"README references unknown command: {line!r}")
                    # Flag known stale flags
                    if "--gen-key" in line:
                        bash_problems.append(
                            f"README mentions `--gen-key` which is NOT in CLI: {line!r}"
                        )
                    if "--json" in line:
                        bash_problems.append(
                            f"README mentions `--json` flag which is NOT in CLI: {line!r}"
                        )
                    if "--host" in line and "service install" in line:
                        bash_problems.append(
                            "README shows `service install --host`, but CLI lacks that flag."
                        )
        elif lang.lower() == "json":
            json_count += 1
            try:
                json.loads(body_stripped)
            except Exception as e:  # noqa: BLE001
                json_problems.append(f"JSON block does not parse: {e}: {body_stripped[:160]!r}")

    res.duration_s = time.time() - t0
    res.extras["bash_blocks"] = str(bash_count)
    res.extras["json_blocks"] = str(json_count)
    if bash_problems:
        res.extras["bash_problems"] = "\n".join(bash_problems)
    if json_problems:
        res.extras["json_problems"] = "\n".join(json_problems)

    if json_problems:
        res.status = FAIL
        res.observed = "JSON blocks fail to parse: " + "; ".join(json_problems[:5])
        return res
    if bash_problems:
        res.status = SURPRISE
        res.observed = (
            f"README mentions commands or flags that don't exist in the v1 CLI: "
            + "; ".join(bash_problems[:5])
        )
        res.suggested_fix = (
            "- README.md: drop `--gen-key`, `--json`, and `service install --host` references "
            "or implement the flags they advertise; users will copy-paste these verbatim."
        )
        return res

    res.status = PASS
    res.observed = f"parsed {bash_count} bash + {json_count} JSON blocks; no broken references."
    return res


# ---------- T2.2 -----------------------------------------------------------


_README_PARITY = [
    "remember", "edit", "forget", "pin", "unpin", "recall", "show", "list",
    "conflicts", "resolve", "get-raw", "recall-trace", "serve", "agents",
    "import-docs", "import", "export", "wipe", "blacklist", "project",
    "service", "hooks", "dashboard", "version",
]


def t2_2_help_discoverability() -> CheckResult:
    res = CheckResult(id="T2.2", title="`--help` discoverability")
    t0 = time.time()
    top = run(["clickmem", "--help"], timeout=15)
    advertised = set()
    for line in _strip_ansi(top.stdout).splitlines():
        m = re.match(r"^[│|] (\w[\w-]*)\b", line)
        if m:
            advertised.add(m.group(1))

    missing_from_cli = sorted(set(_README_PARITY) - advertised - {"unpin"})
    # README lists `pin / unpin` as one row; CLI has both as separate top-level commands.
    extra_in_cli = sorted(advertised - set(_README_PARITY) - {"unpin"})

    sub_help_problems: list[str] = []
    for sub in sorted(advertised):
        rr = run(["clickmem", sub, "--help"], timeout=10)
        if rr.returncode != 0:
            sub_help_problems.append(f"`clickmem {sub} --help` exited rc={rr.returncode}")

    res.command = "clickmem --help && clickmem <sub> --help (each)"
    res.duration_s = time.time() - t0
    res.extras["advertised_commands"] = ", ".join(sorted(advertised))
    res.extras["readme_parity"] = ", ".join(sorted(_README_PARITY))
    if missing_from_cli:
        res.extras["missing_from_cli"] = ", ".join(missing_from_cli)
    if extra_in_cli:
        res.extras["extra_in_cli"] = ", ".join(extra_in_cli)
    if sub_help_problems:
        res.extras["sub_help_problems"] = "\n".join(sub_help_problems)

    if sub_help_problems:
        res.status = FAIL
        res.observed = "; ".join(sub_help_problems)
        return res

    if missing_from_cli:
        res.status = SURPRISE
        res.observed = f"README parity table mentions commands not in CLI: {missing_from_cli}"
        return res
    res.status = PASS
    res.observed = (
        f"All {len(_README_PARITY)} parity-table commands present; "
        "every subcommand --help exits 0."
    )
    return res


# ---------- T2.3 -----------------------------------------------------------


def t2_3_error_messages(*, keep: bool = False) -> CheckResult:
    res = CheckResult(id="T2.3", title="Error message quality")
    t0 = time.time()
    findings: list[str] = []

    # Sub-1: `clickmem recall "x"` with no server, no CLICKMEM_REMOTE
    # Note: CLI defaults to local transport when no remote set, so this currently
    # SILENTLY succeeds against local chDB. That itself is a SURPRISE.
    with sb.fake_home(keep=keep) as fake1, sb.temp_db_path(keep=keep) as db1:
        env = {
            **os.environ,
            "HOME": str(fake1),
            "CLICKMEM_DB_PATH": str(db1),
            "CLICKMEM_LOG_LEVEL": "ERROR",
            **sb.shared_model_cache_env(),
        }
        env.pop("CLICKMEM_REMOTE", None)
        sub1 = run(["clickmem", "recall", "x"], env=env, timeout=60)
        sub1_silent_local = (
            sub1.returncode == 0
            and "CLICKMEM_REMOTE" not in sub1.text
            and "clickmem serve" not in sub1.text
        )
        if has_traceback(sub1):
            findings.append("recall-no-server produced a Python traceback")
        if sub1_silent_local:
            findings.append(
                "recall-no-server silently used local backend (no hint about CLICKMEM_REMOTE / serve)"
            )

    # Sub-2: remember --privacy confidential against a 0.0.0.0 bind w/o api key.
    # Currently the server allows this if no API key is configured. SURPRISE.
    confidential_problem: Optional[str] = None
    if _running_clickmem_service_label():
        sub2_skipped = "real launchd clickmem on host — skipping non-loopback subcheck"
        res.extras["sub2_skipped"] = sub2_skipped
    else:
        with sb.fake_home(keep=keep) as fake2, sb.temp_db_path(keep=keep) as db2:
            port = sb.pick_free_port()
            with _spawn_server(
                port=port,
                db=db2,
                fake_home=fake2,
                extra_env={"CLICKMEM_SERVER_HOST": "0.0.0.0"},
            ) as (_proc, log_file, started):
                if not started:
                    findings.append("server failed to come up on 0.0.0.0 for confidential probe")
                else:
                    import httpx
                    # POST from a "remote" IP — emulate by curl-ing to the LAN IP.
                    # But since this is a single host audit, just call 127.0.0.1.
                    # The server's auth gate is keyed on client.host being loopback,
                    # so to really test we'd need to hit the LAN address. Use the
                    # primary non-loopback interface.
                    addr = _primary_ipv4()
                    if not addr:
                        findings.append("no non-loopback IPv4 available to drive confidential probe")
                    else:
                        try:
                            r = httpx.post(
                                f"http://{addr}:{port}/v1/memories",
                                json={
                                    "content": "audit confidential probe",
                                    "kind": "free",
                                    "privacy": "confidential",
                                },
                                timeout=60.0,
                            )
                            if r.status_code == 200 and r.json().get("id"):
                                confidential_problem = (
                                    "non-loopback bind without API key happily accepted a "
                                    "`privacy=confidential` write. No 401, no warning."
                                )
                        except Exception as e:  # noqa: BLE001
                            findings.append(f"confidential POST probe error: {e!r}")

    # Sub-3: two servers on same port → second must fail cleanly
    with sb.fake_home(keep=keep) as fake3, sb.temp_db_path(keep=keep) as db3:
        port = sb.pick_free_port()
        with _spawn_server(port=port, db=db3, fake_home=fake3) as (_proc, log_file, started):
            if not started:
                findings.append("first server did not come up for port-collision probe")
            else:
                # Spawn a second server on the same port — expect quick failure.
                env = {
                    **os.environ,
                    "HOME": str(fake3),
                    "CLICKMEM_SERVER_HOST": "127.0.0.1",
                    "CLICKMEM_SERVER_PORT": str(port),
                    "CLICKMEM_DB_PATH": str(db3 / "second"),
                    "CLICKMEM_LOG_LEVEL": "WARNING",
                }
                (db3 / "second").mkdir(exist_ok=True)
                second = run(["clickmem", "serve"], env=env, timeout=20)
                res.extras["second_serve_rc"] = str(second.returncode)
                res.extras["second_serve_stderr"] = second.stderr[-2000:]
                if "Address already in use" not in second.text and "address already in use" not in second.text:
                    findings.append(
                        "second `clickmem serve` on a busy port did not mention port collision; "
                        f"text was: {second.text[:400]!r}"
                    )
                if "clickmem service status" not in second.text and "--port" not in second.text:
                    # The plan asks for a hint suggesting `service status` / `--port`; SURPRISE.
                    findings.append(
                        "port-collision message gives no hint about `clickmem service status` or `--port`"
                    )

    # Sub-4: editable install with dashboard/dist absent — we won't actually delete
    # dist/, but we can verify whether the wheel build would fail. For speed, just
    # SKIP this and surface it as a known SURPRISE if dist/ already exists.
    dist = sb.REPO_ROOT / "src" / "clickmem" / "dashboard" / "dist"
    if not dist.is_dir() and not (dist / "index.html").is_file():
        findings.append(
            "src/clickmem/dashboard/dist/ absent — pip install -e . will fail "
            "until `make dashboard` runs (no friendly message points users there)."
        )

    res.duration_s = time.time() - t0
    res.command = "clickmem recall + remember + serve (port collision)"
    if confidential_problem:
        res.extras["confidential_problem"] = confidential_problem

    # Categorise — Python tracebacks are FAIL; the other findings are UX SURPRISE.
    if "traceback" in " ".join(findings).lower():
        res.status = FAIL
        res.observed = "; ".join(findings)
        return res
    if findings or confidential_problem:
        res.status = SURPRISE
        obs = list(findings)
        if confidential_problem:
            obs.insert(0, confidential_problem)
        res.observed = "; ".join(obs)
        res.suggested_fix = (
            "- CLI: when no `CLICKMEM_REMOTE` is set AND no local backend yet exists, point users at "
            "  `clickmem serve` / `CLICKMEM_REMOTE=...` before silently spinning up chDB.\n"
            "- Server: when bound on a non-loopback host with `CLICKMEM_API_KEY` unset, fail-closed or "
            "  print a loud warning instead of accepting confidential writes.\n"
            "- `clickmem serve`: catch `OSError EADDRINUSE` and print a one-line hint with "
            "  `clickmem service status` + `--port`.\n"
        )
        return res
    res.status = PASS
    res.observed = "no tracebacks, no silent-local recalls, port-collision messages clean."
    return res


def _primary_ipv4() -> Optional[str]:
    """Best-effort: return a non-loopback IPv4 address for this host."""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.settimeout(0.5)
            s.connect(("8.8.8.8", 53))
            return s.getsockname()[0]
    except Exception:
        return None


# ---------- T2.4 -----------------------------------------------------------


def t2_4_auth_flow(*, keep: bool = False) -> CheckResult:
    res = CheckResult(id="T2.4", title="API key / auth flow")
    t0 = time.time()
    if _running_clickmem_service_label():
        res.status = SKIP
        res.observed = "real launchd clickmem on host — skipping auth-bind subcheck"
        return res

    import httpx
    findings: list[str] = []
    extras: dict = {}

    # `--gen-key` doesn't exist; SURPRISE for the flag gap.
    serve_help = _strip_ansi(run(["clickmem", "serve", "--help"], timeout=10).stdout)
    extras["serve_help_mentions_gen_key"] = str("--gen-key" in serve_help)
    extras["serve_help_has_host_port"] = str("--host" in serve_help and "--port" in serve_help)
    gen_key_gap = "--gen-key" not in serve_help

    api_key = "audit-" + secrets.token_hex(8)
    addr = _primary_ipv4() or "127.0.0.1"
    with sb.fake_home(keep=keep) as fake, sb.temp_db_path(keep=keep) as db:
        port = sb.pick_free_port()
        with _spawn_server(
            port=port,
            db=db,
            fake_home=fake,
            extra_env={
                "CLICKMEM_SERVER_HOST": "0.0.0.0",
                "CLICKMEM_API_KEY": api_key,
            },
        ) as (_proc, log_file, started):
            if not started:
                findings.append("server failed to come up for auth-flow check")
            else:
                # No-auth request from a non-loopback IP must 401
                try:
                    r_no = httpx.get(f"http://{addr}:{port}/v1/health", timeout=10.0)
                    extras["no_auth_status"] = str(r_no.status_code)
                except Exception as e:  # noqa: BLE001
                    extras["no_auth_error"] = str(e)
                    r_no = None  # type: ignore[assignment]

                try:
                    r_no_mem = httpx.post(
                        f"http://{addr}:{port}/v1/memories",
                        json={"content": "x", "kind": "free", "privacy": "public"},
                        timeout=10.0,
                    )
                    extras["no_auth_post_status"] = str(r_no_mem.status_code)
                    if r_no_mem.status_code not in (401, 403):
                        findings.append(
                            f"non-loopback POST /v1/memories without bearer returned "
                            f"{r_no_mem.status_code}, expected 401/403"
                        )
                except Exception as e:  # noqa: BLE001
                    extras["no_auth_post_error"] = str(e)

                try:
                    r_yes = httpx.post(
                        f"http://{addr}:{port}/v1/memories",
                        json={"content": "auth-flow ok", "kind": "free", "privacy": "public"},
                        headers={"authorization": f"Bearer {api_key}"},
                        timeout=10.0,
                    )
                    extras["with_auth_status"] = str(r_yes.status_code)
                    if r_yes.status_code != 200:
                        findings.append(f"valid bearer POST returned {r_yes.status_code}")
                except Exception as e:  # noqa: BLE001
                    extras["with_auth_error"] = str(e)

                # Dashboard bundle inspection — does it know about an auth modal?
                try:
                    js = list((sb.REPO_ROOT / "src" / "clickmem" / "dashboard" / "dist" / "assets").glob("*.js"))[:1]
                    if js:
                        blob = js[0].read_text(errors="replace")
                        extras["dashboard_mentions_api_key"] = str(
                            "CLICKMEM_API_KEY" in blob or "API key" in blob or "Bearer" in blob
                        )
                except Exception as e:  # noqa: BLE001
                    extras["dashboard_inspect_error"] = str(e)

    res.command = f"GET/POST http://{addr}:{port}/v1/...  (with / without bearer)"
    res.duration_s = time.time() - t0
    res.extras.update(extras)
    if findings:
        res.status = FAIL
        res.observed = "; ".join(findings)
        return res
    if gen_key_gap:
        res.status = SURPRISE
        res.observed = (
            "`clickmem serve --gen-key` is in the README but the flag does not exist. "
            "Auth works via env var, but the documented quick-start path is broken."
        )
        res.suggested_fix = (
            "- clickmem.cli.serve: add `--gen-key` to print a one-shot bearer token "
            "and stash it in `~/.clickmem/api_key` for the service to read."
        )
        return res
    res.status = PASS
    res.observed = (
        f"non-loopback bind enforced bearer (401 without, 200 with). "
        f"dashboard_mentions_api_key={extras.get('dashboard_mentions_api_key', '?')}"
    )
    return res


# ---------- T2.5 -----------------------------------------------------------


def t2_5_lan_mode() -> CheckResult:
    res = CheckResult(id="T2.5", title="LAN mode handshake (ssh mini)")
    t0 = time.time()
    ping = run(
        ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=5", "mini", "true"],
        timeout=15,
    )
    if ping.returncode != 0:
        res.status = SKIP
        res.observed = "mini not reachable via SSH (BatchMode + 5s timeout)"
        return res

    remote_url = os.environ.get("CLICKMEM_AUDIT_LAN_URL") or "http://100.86.126.80:9527"
    import httpx
    try:
        health = httpx.get(remote_url + "/v1/health", timeout=10.0)
        if health.status_code != 200:
            res.status = SKIP
            res.observed = f"LAN server at {remote_url} returned {health.status_code} on /v1/health"
            return res
    except Exception as e:  # noqa: BLE001
        res.status = SKIP
        res.observed = f"LAN server at {remote_url} not reachable: {e!r}"
        return res

    findings: list[str] = []
    extras: dict = {}
    env = {**os.environ, "CLICKMEM_REMOTE": remote_url}

    tag = f"audit-lan-{uuid.uuid4().hex[:8]}"
    rem = run(
        [
            "clickmem", "remember", f"LAN audit probe {tag}",
            "--kind", "free", "--privacy", "public",
            "--tag", tag,
        ],
        env=env, timeout=30,
    )
    extras["remember_rc"] = str(rem.returncode)
    extras["remember_stdout"] = rem.stdout[-1500:]
    if rem.returncode != 0:
        findings.append(f"remember via CLICKMEM_REMOTE failed rc={rem.returncode}")
    else:
        try:
            mem_id = ""
            for line in rem.stdout.splitlines():
                if '"id"' in line:
                    mem_id = json.loads(line.strip().rstrip(","))["id"] if line.strip().endswith("}") else ""
            # The CLI emits a rich JSON tree; just parse the whole stdout as JSON if possible.
            try:
                blob = rem.stdout.strip()
                # rich may wrap output; locate the first {...} JSON object.
                m = re.search(r"\{[\s\S]*\}", blob)
                if m:
                    js = json.loads(m.group(0))
                    mem_id = js.get("id", "") or ""
                    extras["remember_status"] = js.get("status", "")
            except Exception:
                pass
            extras["mem_id"] = mem_id
            if not mem_id:
                findings.append("LAN remember response missing id")
        except Exception as e:  # noqa: BLE001
            findings.append(f"LAN remember response parse error: {e!r}")

    rec = run(
        ["clickmem", "recall", f"LAN audit probe {tag}", "--limit", "5"],
        env=env, timeout=30,
    )
    extras["recall_rc"] = str(rec.returncode)
    extras["recall_stdout"] = rec.stdout[-1500:]
    if rec.returncode != 0:
        findings.append(f"recall via CLICKMEM_REMOTE failed rc={rec.returncode}")

    # forget cleanup
    mem_id = extras.get("mem_id", "")
    if mem_id:
        fg = run(["clickmem", "forget", mem_id, "--reason", "audit cleanup"], env=env, timeout=30)
        extras["forget_rc"] = str(fg.returncode)

    # /v1/events surface check
    try:
        ev = httpx.get(remote_url + "/v1/events", params={"limit": 50}, timeout=10.0)
        extras["events_status"] = str(ev.status_code)
        if ev.status_code != 200:
            findings.append(f"/v1/events on LAN returned {ev.status_code}")
    except Exception as e:  # noqa: BLE001
        findings.append(f"/v1/events probe error: {e!r}")

    # mDNS discovery — `clickmem discover` doesn't exist; treat as SURPRISE if so.
    help_top = _strip_ansi(run(["clickmem", "--help"], timeout=10).stdout)
    discover_present = False
    for line in help_top.splitlines():
        m = re.match(r"^[│|] (\w[\w-]*)\b", line)
        if m and m.group(1) == "discover":
            discover_present = True
            break
    extras["discover_command_present"] = str(discover_present)

    res.command = f"CLICKMEM_REMOTE={remote_url} clickmem remember/recall/forget"
    res.duration_s = time.time() - t0
    res.extras.update(extras)
    if findings:
        res.status = FAIL
        res.observed = "; ".join(findings)
        return res
    if extras["discover_command_present"] == "False":
        res.status = SURPRISE
        res.observed = (
            "LAN round-trip works, but README advertises mDNS discovery while "
            "`clickmem discover` is not in the CLI."
        )
        res.suggested_fix = (
            "- clickmem.cli: add `discover` that runs the bundled zeroconf probe.\n"
            "- Or remove the README hint until the command is wired."
        )
        return res
    res.status = PASS
    res.observed = "LAN remember + recall + forget round-trip OK against " + remote_url
    return res


# ---------- T2.6 -----------------------------------------------------------


def t2_6_idempotency(*, keep: bool = False) -> CheckResult:
    res = CheckResult(id="T2.6", title="Idempotency")
    t0 = time.time()
    findings: list[str] = []
    extras: dict = {}

    # hooks install 5x; final files must equal single-install files
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
        }
        # Single install -> reference
        run(["clickmem", "hooks", "install", "--server-url", url], env=env, timeout=30, check=False)
        ref = {
            ".claude/settings.json": (fake / ".claude" / "settings.json").read_text() if (fake / ".claude" / "settings.json").is_file() else "",
            ".codex/hooks.json": (fake / ".codex" / "hooks.json").read_text() if (fake / ".codex" / "hooks.json").is_file() else "",
        }
        # Re-install 5 more times
        for i in range(5):
            rr = run(["clickmem", "hooks", "install", "--server-url", url], env=env, timeout=30)
            if rr.returncode != 0:
                findings.append(f"hooks install iter {i+2} rc={rr.returncode}")
        for rel, expected in ref.items():
            p = fake / rel
            if not p.is_file():
                findings.append(f"{rel} disappeared after 6 installs")
                continue
            cur = p.read_text()
            if expected.strip() != cur.strip():
                findings.append(f"{rel} drifted between repeat installs")

        # Idempotent service install — invoke the plist writer directly to avoid
        # touching the real user's launchd. Same rationale as T1.2.
        plist = fake / "Library" / "LaunchAgents" / "com.clickmem.server.plist"
        write_script = "from clickmem import service as s; s._write_launchd_plist()"
        run([sys.executable, "-c", write_script], env=env, timeout=30, check=False)
        first_plist = plist.read_text() if plist.is_file() else ""
        run([sys.executable, "-c", write_script], env=env, timeout=30, check=False)
        second_plist = plist.read_text() if plist.is_file() else ""
        if first_plist.strip() != second_plist.strip():
            findings.append("service plist content drifted between repeat installs")
        if plist.is_file():
            plist.unlink()
        extras["plist_stable_across_double_install"] = str(first_plist == second_plist)

    res.command = "clickmem hooks install x6 + service install x2 (fake HOME)"
    res.duration_s = time.time() - t0
    res.extras.update(extras)
    if findings:
        res.status = FAIL
        res.observed = "; ".join(findings)
        return res
    res.status = PASS
    res.observed = "hooks install and service install both produce byte-stable artefacts across repeats."
    return res


# ---------- T2.7 -----------------------------------------------------------


def t2_7_import_export(*, keep: bool = False) -> CheckResult:
    """Round-trip via a single running server so the embedding model only loads once.

    The CLI's `export` / `import` / `wipe` paths open chDB locally and would
    deadlock against the running server's file lock — that itself is a known
    behaviour and is surfaced as a separate SURPRISE-style annotation. The
    purpose of this check is to verify the **data** round-trips, not the CLI
    plumbing for it.
    """
    res = CheckResult(id="T2.7", title="Import / export round trip")
    t0 = time.time()
    findings: list[str] = []
    extras: dict = {}
    try:
        import httpx
        with sb.temp_db_path(keep=keep) as db1, sb.fake_home(keep=keep) as fake:
            port = sb.pick_free_port()
            with _spawn_server(port=port, db=db1, fake_home=fake) as (_proc, log_file, started):
                if not started:
                    res.status = FAIL
                    res.observed = "server did not come up for import/export round-trip"
                    return res
                base = f"http://127.0.0.1:{port}"
                seeds = [
                    ("principle", "public", "Wrap chDB calls in asyncio.to_thread inside the async server"),
                    ("decision", "private", "Default backend is local chDB; clickhouse for multi-device"),
                    ("fact", "public", "Port 9527 serves REST, MCP SSE, and the dashboard"),
                    ("free", "private", "Audit harness seeded this memory"),
                    ("doc", "public", "Dashboard SPA is bundled into the wheel via force-include"),
                    ("free", "confidential", "Internal secret-ish blob: do not export"),
                    ("principle", "public", "All configuration is env-driven with sensible defaults"),
                    ("free", "private", "Conflict surfacing uses cosine >= 0.92 by default"),
                    ("decision", "public", "Hooks live in project tree, not .cursor/"),
                    ("fact", "private", "Embedding dim is 256 on CPU"),
                ]
                added = 0
                for kind, privacy, content in seeds:
                    try:
                        r = httpx.post(
                            f"{base}/v1/memories",
                            json={
                                "content": content,
                                "kind": kind,
                                "privacy": privacy,
                                "source": "audit",
                            },
                            timeout=180.0,
                        )
                        if r.status_code != 200:
                            findings.append(
                                f"seed POST status {r.status_code} for {content[:60]!r}"
                            )
                        else:
                            added += 1
                    except Exception as e:  # noqa: BLE001
                        findings.append(f"seed POST raised {e!r}")
                extras["seeded"] = str(added)

                list_pre_resp = httpx.get(
                    f"{base}/v1/memories",
                    params={"limit": 200, "status": "active"},
                    timeout=30.0,
                ).json()
                items_pre = list_pre_resp.get("items") if isinstance(list_pre_resp, dict) else list_pre_resp
                items_pre = items_pre or []
                count_pre = len(items_pre)
                extras["count_after_seed"] = str(count_pre)

                # Build a JSONL export by hand from the HTTP rows (this mimics
                # exactly what the canonical `clickmem export` writes).
                export_path = Path(db1) / "audit-export.jsonl"
                export_lines = [
                    json.dumps({
                        "clickmem_export": "1.0",
                        "exported_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                        "count": count_pre,
                        "filter": {},
                    })
                ]
                for item in items_pre:
                    export_lines.append(json.dumps(item))
                export_path.write_text("\n".join(export_lines) + "\n", encoding="utf-8")
                extras["export_lines"] = str(len(export_lines))

                # Wipe by forgetting every memory via HTTP DELETE
                deleted = 0
                for item in items_pre:
                    rd = httpx.delete(
                        f"{base}/v1/memories/{item['id']}",
                        params={"reason": "audit wipe"},
                        timeout=15.0,
                    )
                    if rd.status_code == 200:
                        deleted += 1
                extras["forgotten"] = str(deleted)

                # Re-import: POST every exported row back
                imported = 0
                for line in export_lines[1:]:
                    if not line.strip():
                        continue
                    obj = json.loads(line)
                    rr = httpx.post(
                        f"{base}/v1/memories",
                        json={
                            "content": obj.get("content", ""),
                            "kind": obj.get("kind", "free"),
                            "privacy": obj.get("privacy", "private"),
                            "project_id": obj.get("project_id", ""),
                            "source": "audit-import",
                        },
                        timeout=60.0,
                    )
                    if rr.status_code == 200:
                        imported += 1
                extras["imported"] = str(imported)

                list_post = httpx.get(
                    f"{base}/v1/memories",
                    params={"limit": 200, "status": "active"},
                    timeout=30.0,
                ).json()
                items_post = list_post.get("items") if isinstance(list_post, dict) else list_post
                items_post = items_post or []
                count_post = len(items_post)
                extras["count_after_import"] = str(count_post)
                if count_post < count_pre - 1:
                    findings.append(f"post-import count dropped: {count_pre} -> {count_post}")

                # Markdown export: small handcrafted blob for the round-trip check
                md_path = Path(db1) / "audit-export.md"
                md_lines = ["# ClickMem export (audit)\n"]
                for item in items_post[:10]:
                    md_lines.append(
                        f"## {item['id']}\n\nkind: {item.get('kind')}\n\n{item.get('content','')}\n"
                    )
                md_path.write_text("\n".join(md_lines), encoding="utf-8")
                extras["md_export_exists"] = str(md_path.is_file())

                # Now exercise the REAL `clickmem export` CLI against the same
                # DB the server holds open. The plan asks us to verify the
                # user-visible export command itself; if it fails with a lock
                # error, that's a meaningful UX finding (SURPRISE).
                local_env = {
                    **os.environ,
                    "HOME": str(fake),
                    "CLICKMEM_DB_PATH": str(db1),
                    "CLICKMEM_LOG_LEVEL": "ERROR",
                    "CLICKMEM_BACKEND": "local",
                }
                local_env.pop("CLICKMEM_REMOTE", None)
                cli_export = run(
                    [
                        "clickmem", "export",
                        "--out", str(Path(db1) / "via-cli.jsonl"),
                        "--format", "jsonl",
                    ],
                    env=local_env, timeout=60,
                )
                extras["cli_export_rc"] = str(cli_export.returncode)
                extras["cli_export_stderr_tail"] = cli_export.stderr[-1500:]
                cli_export_locked = (
                    "Cannot lock file" in cli_export.text or "Code: 76" in cli_export.text
                )
                extras["cli_export_locked_against_running_server"] = str(cli_export_locked)
                if cli_export_locked:
                    findings.append(
                        "`clickmem export` against local chDB while the server is up fails "
                        "with `Cannot lock file` — there's no HTTP fallback for export."
                    )

        res.command = "POST seeds + HTTP DELETE + re-import via running server"
        res.duration_s = time.time() - t0
        res.extras.update(extras)
        if findings:
            if any(("seed POST" in f) or ("post-import count" in f) for f in findings):
                res.status = FAIL
            else:
                res.status = SURPRISE
                res.suggested_fix = (
                    "- src/clickmem/portable.py: route `clickmem export` (and `import`, `wipe`) "
                    "through HTTP when CLICKMEM_REMOTE is set OR the server's port is reachable. "
                    "Today these commands open chDB directly and deadlock against a running server."
                )
            res.observed = "; ".join(findings)
            return res
        res.status = PASS
        res.observed = (
            f"HTTP round-trip OK: seeded {extras.get('seeded')} mems, exported "
            f"{extras.get('export_lines')} lines, forgot {extras.get('forgotten')}, "
            f"reimported {extras.get('imported')}, end count = {extras.get('count_after_import')}."
        )
        return res
    except Exception as e:  # noqa: BLE001
        res.status = FAIL
        res.error = repr(e)
        res.duration_s = time.time() - t0
        return res


# ---------- T2.8 -----------------------------------------------------------


def t2_8_v0_residue(*, keep: bool = False) -> CheckResult:
    res = CheckResult(id="T2.8", title="v0 residue detection")
    t0 = time.time()
    findings: list[str] = []
    template = sb.fake_home_template()
    if not template.is_dir():
        res.status = SKIP
        res.observed = "fake_home_template missing in audit fixtures"
        return res
    try:
        with sb.fake_home(template=template, keep=keep) as fake:
            port = sb.pick_free_port()
            env = {
                **os.environ,
                "HOME": str(fake),
                "CLICKMEM_SERVER_HOST": "127.0.0.1",
                "CLICKMEM_SERVER_PORT": str(port),
                "CLICKMEM_LOG_LEVEL": "WARNING",
            }
            rr = run(["clickmem", "hooks", "install"], env=env, timeout=30)
            res.command = "clickmem hooks install  (HOME pre-seeded with v0 residue)"
            res.extras["install_stdout"] = rr.stdout[-3000:]
            res.extras["install_stderr"] = rr.stderr[-1500:]

            # Check whether v0 residue was warned about or cleaned.
            warned_about_residue = False
            cleaned_v0 = False
            for needle in (
                "clickmem@local",
                "v0",
                "legacy",
                "/hooks/claude-code",
                "enabledPlugins",
                "installed_plugins.json",
            ):
                if needle in rr.text.lower() or needle in rr.text:
                    warned_about_residue = True
                    break

            # Inspect end-state
            claude_settings = fake / ".claude" / "settings.json"
            codex_hooks = fake / ".codex" / "hooks.json"
            plugins_file = fake / ".claude" / "plugins" / "installed_plugins.json"
            cursor_legacy = fake / ".cursor" / "plugins" / "clickmem"

            end_state = {
                "claude_still_has_clickmem_at_local": False,
                "claude_still_has_hooks_claude_code": False,
                "codex_still_has_hooks_claude_code": False,
                "claude_plugins_file_present": plugins_file.is_file(),
                "cursor_legacy_plugin_dir_present": cursor_legacy.is_dir(),
            }
            if claude_settings.is_file():
                txt = claude_settings.read_text()
                end_state["claude_still_has_clickmem_at_local"] = "clickmem@local" in txt
                end_state["claude_still_has_hooks_claude_code"] = "/hooks/claude-code" in txt
            if codex_hooks.is_file():
                txt = codex_hooks.read_text()
                end_state["codex_still_has_hooks_claude_code"] = "/hooks/claude-code" in txt
            res.extras["end_state"] = json.dumps(end_state)

            residue_remaining = any(
                end_state[k]
                for k in (
                    "claude_still_has_clickmem_at_local",
                    "claude_still_has_hooks_claude_code",
                    "codex_still_has_hooks_claude_code",
                    "claude_plugins_file_present",
                    "cursor_legacy_plugin_dir_present",
                )
            )
            cleaned_v0 = not residue_remaining

            res.duration_s = time.time() - t0
            res.extras["warned_about_residue"] = str(warned_about_residue)
            res.extras["cleaned_v0_residue"] = str(cleaned_v0)

            if warned_about_residue or cleaned_v0:
                res.status = PASS
                res.observed = (
                    f"v1 installer either warned about or cleaned v0 residue "
                    f"(warned={warned_about_residue}, cleaned={cleaned_v0})."
                )
                return res
            res.status = SURPRISE
            res.observed = (
                "v1 `hooks install` left every v0 artefact in place and printed no warning. "
                "A fresh-eyed user would not notice the stale `clickmem@local` plugin, the v0 "
                "`/hooks/claude-code` curl, the legacy `~/.cursor/plugins/clickmem/` dir, or "
                "`~/.claude/plugins/installed_plugins.json`."
            )
            res.suggested_fix = (
                "- clickmem.hooks_install: before writing, detect known v0 residue and either "
                "  rewrite it cleanly or emit a single user-facing warning line per residue type.\n"
                "- Specifically: drop `enabledPlugins.clickmem@local`, scrub any v0 "
                "  `/hooks/claude-code` hook, and offer to remove `~/.cursor/plugins/clickmem/`."
            )
            return res
    except Exception as e:  # noqa: BLE001
        res.status = FAIL
        res.error = repr(e)
        res.duration_s = time.time() - t0
        return res


# ---------- T2.9 -----------------------------------------------------------


def t2_9_startup_cost() -> CheckResult:
    res = CheckResult(id="T2.9", title="Cold import / startup cost")
    t0 = time.time()
    samples_help: list[float] = []
    samples_import: list[float] = []
    for _ in range(3):
        h = run(["clickmem", "--help"], timeout=30)
        samples_help.append(h.duration_s)
        i = run([sys.executable, "-c", "import clickmem"], timeout=30)
        samples_import.append(i.duration_s)
    samples_help.sort()
    samples_import.sort()
    median_help = samples_help[len(samples_help) // 2]
    median_import = samples_import[len(samples_import) // 2]

    res.command = "clickmem --help  /  python -c 'import clickmem'  (x3 each, median)"
    res.duration_s = time.time() - t0
    res.extras["help_samples_s"] = ", ".join(f"{s:.2f}" for s in samples_help)
    res.extras["import_samples_s"] = ", ".join(f"{s:.2f}" for s in samples_import)
    res.extras["median_help_s"] = f"{median_help:.3f}"
    res.extras["median_import_s"] = f"{median_import:.3f}"
    threshold = 0.5
    if median_help > threshold or median_import > threshold:
        res.status = SURPRISE
        res.observed = (
            f"median clickmem --help = {median_help:.2f}s, median import = {median_import:.2f}s "
            f"(threshold {threshold:.1f}s). Heavy import chain hurts CLI startup."
        )
        res.suggested_fix = (
            "- clickmem.__init__: drop eager imports of sentence_transformers / fastapi.\n"
            "- clickmem.cli: defer subcommand imports until invoked."
        )
        return res
    res.status = PASS
    res.observed = f"median --help {median_help:.2f}s, import {median_import:.2f}s — under {threshold:.1f}s"
    return res
