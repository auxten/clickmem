"""Typer + Rich CLI.

Every revision op (``remember`` / ``edit`` / ``forget`` / ``pin`` / ``unpin``
/ ``blacklist``) and every inspection command (``recall`` / ``show`` /
``list`` / ``conflicts`` / ``resolve`` / ``get-raw`` / ``recall-trace`` /
``project link``) is wired through :mod:`clickmem.transport` so the same
binary works against a local backend or a remote LAN server.

Operational commands (``serve`` / ``service`` / ``hooks`` / ``agents`` /
``import`` / ``export`` / ``dashboard open`` / ``wipe``) live alongside; the
ones that depend on Phase 6/7 modules return clear "not yet implemented in
this phase" payloads instead of failing silently.
"""

from __future__ import annotations

import json
import os
import shutil
import sys
import webbrowser
from pathlib import Path
from typing import Optional

import typer
from rich.console import Console
from rich.json import JSON
from rich.table import Table

from clickmem import __version__
from clickmem.config import get_config
from clickmem.transport import get_transport


app = typer.Typer(
    name="clickmem",
    help="ClickMem - local-first explicit-memory system for AI coding agents.",
    no_args_is_help=True,
    add_completion=False,
)
console = Console()

_MEMORY_METADATA_HELP = (
    "Memory writes require explicit scope and tags.\n"
    "Choose exactly one scope:\n"
    "  --project owner/repo    project-scoped memory, e.g. --project auxten/clickmem\n"
    "  --global                global memory shared across projects\n"
    "Add at least one --tag.\n"
    "Examples:\n"
    "  clickmem remember \"Use mini as deploy target\" --project auxten/clickmem --tag workflow --tag deployment\n"
    "  clickmem remember \"Never log API keys\" --global --tag security"
)


def _print(value, raw_json: bool = False) -> None:
    if raw_json:
        console.print_json(data=value)
        return
    if isinstance(value, (dict, list)):
        console.print(JSON.from_data(value))
    else:
        console.print(value)


def _resolve_memory_scope(project_id: Optional[str], global_scope: bool) -> str:
    project = (project_id or "").strip()
    if bool(project) == bool(global_scope):
        console.print(f"[red]{_MEMORY_METADATA_HELP}[/red]")
        raise typer.Exit(code=2)
    return "global" if global_scope else project


def _require_tags(tags: list[str]) -> list[str]:
    cleaned = [t.strip() for t in tags if t.strip()]
    if not cleaned:
        console.print(f"[red]{_MEMORY_METADATA_HELP}[/red]")
        raise typer.Exit(code=2)
    return cleaned


# ---------- core revision operations --------------------------------------


@app.command()
def remember(
    content: str = typer.Argument(..., help="Memory text. Wrap multi-word strings in quotes."),
    kind: str = typer.Option("free", help="principle | decision | fact | doc | free"),
    project_id: Optional[str] = typer.Option(None, "--project", "--project-id", help="required project id, e.g. auxten/clickmem"),
    global_scope: bool = typer.Option(False, "--global", help="explicitly write a global memory"),
    privacy: str = typer.Option("private", help="public | private | confidential"),
    tag: list[str] = typer.Option([], "--tag", help="repeatable tag value"),
    pinned: bool = typer.Option(False, "--pin/--no-pin", help="pin on commit"),
    source: str = typer.Option("cli"),
    source_ref: str = typer.Option(""),
    agent: str = typer.Option(""),
) -> None:
    """Expand: commit a new memory."""
    resolved_project = _resolve_memory_scope(project_id, global_scope)
    tags = _require_tags(list(tag))
    result = get_transport().remember(
        content,
        kind=kind,
        project_id=resolved_project,
        privacy=privacy,
        tags=tags,
        pinned=pinned,
        source=source,
        source_ref=source_ref,
        agent=agent,
    )
    _print(result)


@app.command()
def edit(
    memory_id: str = typer.Argument(...),
    content: Optional[str] = typer.Option(None),
    kind: Optional[str] = typer.Option(None),
    privacy: Optional[str] = typer.Option(None),
    project_id: Optional[str] = typer.Option(None),
    tag: list[str] = typer.Option([], "--tag", help="replace tag list"),
    pinned: Optional[bool] = typer.Option(None, "--pin/--no-pin"),
    agent: str = typer.Option(""),
) -> None:
    """Revise: edit an existing memory."""
    kwargs: dict[str, object] = {"agent": agent}
    if content is not None:
        kwargs["content"] = content
    if kind is not None:
        kwargs["kind"] = kind
    if privacy is not None:
        kwargs["privacy"] = privacy
    if project_id is not None:
        kwargs["project_id"] = project_id
    if tag:
        kwargs["tags"] = list(tag)
    if pinned is not None:
        kwargs["pinned"] = pinned
    _print(get_transport().edit(memory_id, **kwargs))


@app.command()
def forget(
    memory_id: str = typer.Argument(...),
    reason: str = typer.Option(""),
    agent: str = typer.Option(""),
) -> None:
    """Contract: mark a memory contracted (excluded from recall)."""
    _print(get_transport().forget(memory_id, reason=reason, agent=agent))


@app.command()
def pin(memory_id: str = typer.Argument(...), agent: str = typer.Option("")) -> None:
    """Reinforce: pin a memory."""
    _print(get_transport().pin(memory_id, agent=agent))


@app.command()
def unpin(memory_id: str = typer.Argument(...), agent: str = typer.Option("")) -> None:
    """Unpin a memory."""
    _print(get_transport().unpin(memory_id, agent=agent))


# ---------- inspection ---------------------------------------------------


@app.command()
def recall(
    query: str = typer.Argument(...),
    project_id: str = typer.Option(""),
    limit: int = typer.Option(10),
    cross_project: bool = typer.Option(False, "--cross-project"),
    include_confidential: bool = typer.Option(False, "--include-confidential"),
    kind: Optional[str] = typer.Option(None),
    tag: list[str] = typer.Option([], "--tag", help="repeatable tag filter"),
    tag_mode: str = typer.Option("any", "--tag-mode", help="any | all"),
    timeout_seconds: float = typer.Option(5.0, "--timeout-seconds", help="fail-open recall timeout"),
    agent: str = typer.Option(""),
) -> None:
    """Run embedding recall against the brain."""
    res = get_transport().recall(
        query,
        project_id=project_id,
        limit=limit,
        include_confidential=include_confidential,
        cross_project=cross_project,
        kind=kind,
        tags=list(tag),
        tag_mode=tag_mode,
        timeout_seconds=timeout_seconds,
        agent=agent,
    )
    hits = res.get("hits", [])
    if not hits:
        if res.get("warning"):
            console.print(f"[yellow]{res['warning']}[/yellow]")
        console.print("[dim]no hits[/dim]")
        return
    table = Table(show_lines=True)
    table.add_column("score", justify="right")
    table.add_column("kind")
    table.add_column("project")
    table.add_column("privacy")
    table.add_column("pinned")
    table.add_column("id")
    table.add_column("content")
    for h in hits:
        table.add_row(
            f"{h.get('score', 0.0):.3f}",
            h.get("kind", ""),
            h.get("project_id", "") or "-",
            h.get("privacy", ""),
            "*" if h.get("pinned") else "",
            h.get("id", "")[:12],
            (h.get("content") or "")[:200],
        )
    console.print(table)


@app.command("recall-trace")
def recall_trace_cmd(
    query: str = typer.Argument(...),
    project_id: str = typer.Option(""),
    limit: int = typer.Option(10),
    cross_project: bool = typer.Option(False, "--cross-project"),
    include_confidential: bool = typer.Option(False, "--include-confidential"),
    kind: Optional[str] = typer.Option(None),
    tag: list[str] = typer.Option([], "--tag", help="repeatable tag filter"),
    tag_mode: str = typer.Option("any", "--tag-mode", help="any | all"),
    timeout_seconds: float = typer.Option(5.0, "--timeout-seconds", help="fail-open recall timeout"),
) -> None:
    """Recall with per-candidate scoring breakdown."""
    _print(get_transport().recall_trace(
        query,
        project_id=project_id,
        limit=limit,
        include_confidential=include_confidential,
        cross_project=cross_project,
        kind=kind,
        tags=list(tag),
        tag_mode=tag_mode,
        timeout_seconds=timeout_seconds,
    ))


@app.command()
def show(
    memory_id: str = typer.Argument(...),
    history: bool = typer.Option(False, "--history"),
    neighbors: bool = typer.Option(False, "--neighbors"),
) -> None:
    """Show a single memory; optionally history and neighbours."""
    _print(get_transport().show(memory_id, with_history=history, with_neighbors=neighbors))


@app.command("list")
def list_cmd(
    project_id: Optional[str] = typer.Option(None, "--project"),
    privacy: Optional[str] = typer.Option(None, "--privacy"),
    kind: Optional[str] = typer.Option(None, "--kind"),
    status: Optional[str] = typer.Option(None, "--status"),
    pinned: Optional[bool] = typer.Option(None, "--pinned/--unpinned"),
    source: Optional[str] = typer.Option(None, "--source"),
    search: Optional[str] = typer.Option(None, "--search"),
    offset: int = typer.Option(0),
    limit: int = typer.Option(50),
) -> None:
    """List memories with filters."""
    _print(get_transport().list_memories(
        project_id=project_id,
        privacy=privacy,
        kind=kind,
        status=status,
        pinned=pinned,
        source=source,
        search=search,
        offset=offset,
        limit=limit,
    ))


@app.command()
def conflicts(project_id: Optional[str] = typer.Option(None, "--project")) -> None:
    """List unresolved conflicts."""
    _print(get_transport().conflicts(project_id=project_id))


@app.command()
def resolve(
    memory_id: str = typer.Argument(...),
    revise: Optional[str] = typer.Option(None, "--revise", help="peer id whose conflict is resolved by your edit"),
    contract: Optional[str] = typer.Option(None, "--contract", help="peer id to contract"),
    allow: bool = typer.Option(False, "--allow", help="keep both, clear the conflict flag"),
) -> None:
    """Resolve a conflict for a memory."""
    if allow:
        _print(get_transport().resolve(memory_id, "allow"))
        return
    if revise:
        _print(get_transport().resolve(memory_id, "revise", peer_id=revise))
        return
    if contract:
        _print(get_transport().resolve(memory_id, "contract", peer_id=contract))
        return
    raise typer.BadParameter("resolve requires one of --revise / --contract / --allow")


@app.command("get-raw")
def get_raw_cmd(
    session_id: Optional[str] = typer.Argument(None),
    last: int = typer.Option(50, "--last"),
    agent: Optional[str] = typer.Option(None),
) -> None:
    """Retrieve raw transcripts (cold storage; never used by recall)."""
    _print(get_transport().get_raw(session_id=session_id, last=last, agent=agent))


# ---------- blacklist -----------------------------------------------------

blacklist_app = typer.Typer(help="Refuse: manage blacklist patterns.", no_args_is_help=True)
app.add_typer(blacklist_app, name="blacklist")


@blacklist_app.command("add")
def blacklist_add_cmd(
    pattern: str = typer.Argument(...),
    scope: str = typer.Option("global"),
    reason: str = typer.Option(""),
) -> None:
    _print(get_transport().blacklist_add(pattern, scope=scope, reason=reason))


@blacklist_app.command("remove")
def blacklist_remove_cmd(blacklist_id: str = typer.Argument(...)) -> None:
    _print(get_transport().blacklist_remove(blacklist_id))


@blacklist_app.command("list")
def blacklist_list_cmd() -> None:
    _print(get_transport().blacklist_list())


# ---------- projects ------------------------------------------------------

project_app = typer.Typer(help="Project ops.", no_args_is_help=True)
app.add_typer(project_app, name="project")


@project_app.command("link")
def project_link_cmd(
    a: str = typer.Argument(...),
    b: str = typer.Argument(...),
    reason: str = typer.Option(""),
) -> None:
    """Whitelist cross-project recall between two projects."""
    _print(get_transport().project_link(a, b, reason=reason))


@project_app.command("list")
def project_list_cmd() -> None:
    _print(get_transport().projects_list())


# ---------- operational ---------------------------------------------------


@app.command()
def serve(
    host: Optional[str] = typer.Option(None, help="override CLICKMEM_SERVER_HOST"),
    port: Optional[int] = typer.Option(None, help="override CLICKMEM_SERVER_PORT"),
) -> None:
    """Run the FastAPI server (REST + MCP SSE + dashboard)."""
    if host:
        os.environ["CLICKMEM_SERVER_HOST"] = host
    if port:
        os.environ["CLICKMEM_SERVER_PORT"] = str(port)
    from clickmem.server import main as serve_main

    serve_main()


service_app = typer.Typer(help="Manage the OS-level service (launchd/systemd).", no_args_is_help=True)
app.add_typer(service_app, name="service")


@service_app.command("install")
def service_install() -> None:
    """Install the launchd (macOS) / systemd --user (Linux) unit."""
    from clickmem import service as service_mod

    _print(service_mod.install())


@service_app.command("uninstall")
def service_uninstall() -> None:
    """Uninstall the OS-level service unit."""
    from clickmem import service as service_mod

    _print(service_mod.uninstall())


@service_app.command("status")
def service_status() -> None:
    """Report whether the service is running and what version answers /v1/health."""
    from clickmem import service as service_mod

    cfg = get_config(refresh=True)
    info: dict[str, object] = {
        "config_host": cfg.server_host,
        "config_port": cfg.server_port,
        "remote_url": cfg.remote_url or "",
        "service": service_mod.status(),
    }
    try:
        info["health"] = get_transport().health()
    except Exception as e:  # noqa: BLE001
        info["health_error"] = str(e)
    _print(info)


hooks_app = typer.Typer(help="Install agent-side hooks (raw landing + recall).", no_args_is_help=True)
app.add_typer(hooks_app, name="hooks")


@hooks_app.command("install")
def hooks_install(
    agent: Optional[str] = typer.Option(None, "--agent", help="restrict to one adapter"),
    server_url: Optional[str] = typer.Option(None, "--server-url", help="override target server"),
) -> None:
    """Install hooks for one or every detected agent."""
    from clickmem.hooks_install import install as install_hooks

    _print(install_hooks(agent=agent, server_url=server_url))


@hooks_app.command("uninstall")
def hooks_uninstall(
    agent: Optional[str] = typer.Option(None, "--agent", help="restrict to one adapter"),
) -> None:
    """Uninstall ClickMem hooks for one or every detected agent."""
    from clickmem.hooks_install import uninstall as uninstall_hooks

    _print(uninstall_hooks(agent=agent))


@app.command()
def agents(
    install: Optional[str] = typer.Option(None, help="install hooks for the named agent"),
    uninstall: Optional[str] = typer.Option(None, help="uninstall hooks for the named agent"),
    test: Optional[str] = typer.Option(None, help="test connectivity for the named agent"),
) -> None:
    """List detected adapters; optional install/test ops."""
    from clickmem.agents import (
        install as agents_install,
        list_agents,
        test as agents_test,
        uninstall as agents_uninstall,
    )

    if install:
        _print(agents_install(install))
        return
    if uninstall:
        _print(agents_uninstall(uninstall))
        return
    if test:
        _print(agents_test(test))
        return
    cfg = get_config()
    if cfg.remote_url:
        import httpx

        r = httpx.get(f"{cfg.remote_url.rstrip('/')}/v1/agents", timeout=15.0)
        _print(r.json())
        return

    _print(list_agents())


@app.command("import-docs")
def import_docs_cmd(
    path: Path = typer.Argument(Path("."), help="repo root to scan"),
    dry_run: bool = typer.Option(False, "--dry-run"),
) -> None:
    """Import AGENTS.md / CLAUDE.md / `.cursor/rules/*.mdc` as memories."""
    from clickmem.import_docs import run as run_import

    _print(run_import(path, dry_run=dry_run))


@app.command("import")
def import_cmd(
    src: Path = typer.Argument(...),
    re_embed: bool = typer.Option(False, "--re-embed", help="recompute embeddings on import"),
) -> None:
    """Import memories from a JSONL bundle produced by `clickmem export`."""
    from clickmem.portable import import_jsonl

    _print(import_jsonl(src, re_embed=re_embed))


@app.command("export")
def export_cmd(
    out: Path = typer.Option(Path("clickmem-export.jsonl"), "--out"),
    fmt: str = typer.Option("jsonl", "--format", help="jsonl | markdown"),
    project_id: Optional[str] = typer.Option(None, "--project"),
    privacy: Optional[str] = typer.Option(None, "--privacy"),
    since: Optional[str] = typer.Option(None),
) -> None:
    """Export memories to JSONL (canonical) or Markdown (human-readable)."""
    from clickmem.portable import export_jsonl, export_markdown

    fmt = (fmt or "jsonl").lower()
    if fmt == "markdown":
        _print(export_markdown(out, project_id=project_id, privacy=privacy, since=since))
    elif fmt == "jsonl":
        _print(export_jsonl(out, project_id=project_id, privacy=privacy, since=since))
    else:
        raise typer.BadParameter(f"unknown export format: {fmt!r}")


dashboard_app = typer.Typer(help="Dashboard helpers.", no_args_is_help=True)
app.add_typer(dashboard_app, name="dashboard")


@dashboard_app.command("open")
def dashboard_open() -> None:
    """Open the dashboard in the default browser."""
    cfg = get_config(refresh=True)
    base = cfg.remote_url or cfg.server_url()
    url = f"{base.rstrip('/')}/dashboard/"
    console.print(f"opening {url}")
    try:
        webbrowser.open(url)
    except Exception as e:  # noqa: BLE001
        console.print(f"[red]failed to open browser:[/red] {e}")


@app.command()
def wipe(
    yes: bool = typer.Option(False, "--yes", help="confirm: this drops every memory"),
) -> None:
    """Drop every memory / project / blacklist / event / raw row."""
    if not yes:
        console.print("[yellow]refusing wipe without --yes[/yellow]")
        raise typer.Exit(code=1)
    from clickmem.backend import get_backend

    backend = get_backend()
    for table in ("memories", "memory_history", "projects", "blacklist", "raw_transcripts", "events"):
        try:
            backend.execute(f"TRUNCATE TABLE IF EXISTS {table}")
        except Exception as e:  # noqa: BLE001
            console.print(f"[red]failed to truncate {table}: {e}[/red]")
    console.print({"status": "wiped"})


@app.command()
def version() -> None:
    """Print version + config snapshot."""
    cfg = get_config(refresh=True)
    _print({
        "version": __version__,
        "backend": cfg.backend,
        "remote": cfg.remote_url or "",
        "server": cfg.server_url(),
        "db_path": str(cfg.db_path),
        "embedding_model": cfg.embedding_model,
    })


def main() -> None:  # convenience hook for `python -m clickmem`
    app()


if __name__ == "__main__":
    main()
