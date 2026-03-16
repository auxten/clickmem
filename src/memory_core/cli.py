"""CLI for the memory system — typer-based commands."""

from __future__ import annotations

import json
import logging
import os
import re
import shutil
import sys
from datetime import datetime, timezone
from typing import Optional

import typer
from rich.console import Console
from rich.table import Table

from memory_core.models import Memory, RetrievalConfig
from memory_core.import_openclaw import import_workspace_memories, import_sqlite_chunks

_log_level = os.environ.get("CLICKMEM_LOG_LEVEL", "WARNING").upper()
logging.basicConfig(
    level=getattr(logging, _log_level, logging.WARNING),
    format="%(asctime)s %(name)s %(levelname)s %(message)s",
    stream=sys.stderr,
)

app = typer.Typer(name="memory", help="ClickMem — unified memory center for AI coding agents")
console = Console()

_DB_PATH = os.environ.get("CLICKMEM_DB_PATH", os.path.expanduser("~/.openclaw/memory/chdb-data"))

# ---------------------------------------------------------------------------
# Global --remote option  (set via env or per-invocation)
# ---------------------------------------------------------------------------

_remote_url: str | None = None
_remote_api_key: str | None = None
_force_local: bool = False


@app.callback()
def _global_options(
    remote: Optional[str] = typer.Option(
        None, "--remote", envvar="CLICKMEM_REMOTE",
        help='Remote server URL (e.g. http://192.168.1.100:9527). Use "auto" for mDNS discovery.',
    ),
    api_key: Optional[str] = typer.Option(
        None, "--api-key", envvar="CLICKMEM_API_KEY",
        help="API key for remote server authentication.",
    ),
    local: bool = typer.Option(
        False, "--local",
        help="Use embedded database directly (no server needed).",
    ),
):
    global _remote_url, _remote_api_key, _force_local
    _remote_url = remote
    _remote_api_key = api_key
    _force_local = local


# ---------------------------------------------------------------------------
# Transport helper — local or remote depending on --remote
# ---------------------------------------------------------------------------

_transport_instance = None


def _has_remote_config() -> bool:
    """Check if the user has explicitly configured a remote server."""
    return bool(
        _remote_url
        or os.environ.get("CLICKMEM_REMOTE")
        or os.environ.get("CLICKMEM_SERVER_HOST")
    )


def _get_transport():
    global _transport_instance
    if _transport_instance is None:
        if _force_local:
            from memory_core.transport import LocalTransport
            _transport_instance = LocalTransport()
        else:
            from memory_core.transport import get_transport
            try:
                _transport_instance = get_transport(remote=_remote_url, api_key=_remote_api_key)
            except RuntimeError:
                if _has_remote_config():
                    raise
                print("[clickmem] No server found, using local database.", file=sys.stderr)
                from memory_core.transport import LocalTransport
                _transport_instance = LocalTransport()
    return _transport_instance


# Legacy helpers used by commands that still do local-only work (import/export/uninstall)
_db_instance: "MemoryDB | None" = None  # type: ignore[name-defined]


def _get_db():
    global _db_instance
    if _db_instance is None:
        from memory_core.db import MemoryDB
        _db_instance = MemoryDB(_DB_PATH)
    return _db_instance


def _get_emb():
    from memory_core.embedding import EmbeddingEngine
    emb = EmbeddingEngine()
    emb.load()
    return emb


# ---------------------------------------------------------------------------
# Commands — all routed through Transport (local or remote)
# ---------------------------------------------------------------------------


@app.command()
def remember(
    content: str = typer.Argument(..., help="Memory content to store"),
    layer: str = typer.Option("semantic", help="Memory layer: working, episodic, semantic"),
    category: str = typer.Option("knowledge", help="Memory category"),
    tags: Optional[str] = typer.Option(None, help="Comma-separated tags"),
    no_upsert: bool = typer.Option(False, "--no-upsert", help="Skip smart upsert, force insert"),
    json_output: bool = typer.Option(False, "--json", help="Output as JSON"),
):
    """Store a memory."""
    if not content.strip():
        console.print("[red]Error: content cannot be empty[/red]")
        raise typer.Exit(code=1)

    tag_list = [t.strip() for t in tags.split(",")] if tags else []
    t = _get_transport()
    result = t.remember(content=content, layer=layer, category=category,
                        tags=tag_list, no_upsert=no_upsert)

    if json_output:
        typer.echo(json.dumps(result, default=str))
    elif layer == "working":
        console.print(f"[green]✓[/green] Working memory updated.")
    else:
        action = result.get("action", "ADD")
        if action in ("ADD", "FALLBACK_ADD"):
            console.print(
                f"[green]✓[/green] Stored [{layer}/{category}]: {content}\n"
                f"  id={str(result.get('id', '?'))[:8]}  tags={','.join(tag_list)}"
            )
        elif action == "UPSERT":
            console.print(f"[green]✓[/green] Upserted [{layer}/{category}]: {content}")
            if result.get("id"):
                console.print(f"  Added: {result['id'][:8]}")
            for u in result.get("updated", []):
                console.print(f"  Updated: {u['id'][:8]} → {u['new_content'][:60]}")
            for d in result.get("deleted", []):
                console.print(f"  Deleted: {d[:8]}")
        elif action == "MERGED":
            console.print(f"[green]✓[/green] Merged into existing memory")
            for u in result.get("updated", []):
                console.print(f"  Updated: {u['id'][:8]} → {u['new_content'][:60]}")
        else:
            console.print(f"[green]✓[/green] {result.get('status', 'stored')}")


@app.command()
def extract(
    text: str = typer.Argument(..., help="Conversation text to extract memories from"),
    session_id: str = typer.Option("", help="Session ID"),
    json_output: bool = typer.Option(False, "--json", help="Output as JSON"),
):
    """Extract structured memories from conversation text using LLM."""
    t = _get_transport()
    ids = t.extract(text=text, session_id=session_id)

    if json_output:
        typer.echo(json.dumps(ids))
    else:
        console.print(f"[green]✓[/green] Extracted {len(ids)} memories: {', '.join(str(i)[:8] for i in ids)}")


@app.command()
def ingest(
    text: str = typer.Argument(..., help="Conversation text to ingest"),
    session_id: str = typer.Option("", help="Session ID"),
    source: str = typer.Option("cli", help="Source identifier (cursor, claude, openclaw, cli, import)"),
    json_output: bool = typer.Option(False, "--json", help="Output as JSON"),
):
    """Ingest raw conversation text: stores in raw_transcripts + extracts memories."""
    t = _get_transport()
    result = t.ingest(text=text, session_id=session_id, source=source)

    if json_output:
        typer.echo(json.dumps(result, default=str))
    else:
        raw_id = str(result.get("raw_id", "?"))[:8]
        ids = result.get("extracted_ids", [])
        console.print(
            f"[green]✓[/green] Ingested raw_id={raw_id}, "
            f"extracted {len(ids)} memories: {', '.join(str(i)[:8] for i in ids)}"
        )


@app.command()
def refine(
    dry_run: bool = typer.Option(False, "--dry-run", help="Report what would be refined without executing"),
    threshold: Optional[int] = typer.Option(None, "--threshold", help="Only run if unprocessed raw >= N"),
    json_output: bool = typer.Option(False, "--json", help="Output as JSON"),
):
    """Run continual refinement: deduplicate, merge, and quality-gate L2 memories."""
    t = _get_transport()

    if threshold is not None:
        st = t.status()
        raw = st.get("raw", {})
        unprocessed = raw.get("unprocessed", 0)
        if unprocessed < threshold:
            if json_output:
                typer.echo(json.dumps({"skipped": True, "unprocessed": unprocessed, "threshold": threshold}))
            else:
                console.print(f"Skipped: only {unprocessed} unprocessed raw (threshold={threshold})")
            return

    if dry_run:
        st = t.status()
        raw = st.get("raw", {})
        console.print(f"[yellow]Dry run[/yellow]")
        console.print(f"  Unprocessed raw: {raw.get('unprocessed', 0)}")
        console.print(f"  L2 semantic: {st.get('counts', {}).get('semantic', 0)}")
        return

    from memory_core.refinement import ContinualRefinement
    from memory_core.llm import get_llm_complete

    llm = get_llm_complete()
    if llm is None:
        console.print("[red]Error: no LLM available for refinement[/red]")
        raise typer.Exit(code=1)

    from memory_core.db import MemoryDB
    db = MemoryDB(_DB_PATH)
    emb = _get_emb()
    result = ContinualRefinement.run(db, emb, llm)

    if json_output:
        typer.echo(json.dumps(result, default=str))
    else:
        console.print(f"[green]✓[/green] Refinement complete:")
        console.print(f"  Re-extracted:    {result.get('reextracted', 0)}")
        console.print(f"  Clusters found:  {result.get('clusters_found', 0)}")
        console.print(f"  Merged:          {result.get('merged', 0)}")
        console.print(f"  Pruned:          {result.get('pruned', 0)}")


@app.command()
def recall(
    query: str = typer.Argument(..., help="Search query"),
    layer: Optional[str] = typer.Option(None, help="Filter by layer"),
    category: Optional[str] = typer.Option(None, help="Filter by category"),
    top_k: int = typer.Option(10, "--top-k", "-k", help="Max number of results"),
    min_score: float = typer.Option(0.0, "--min-score", help="Minimum relevance score threshold"),
    json_output: bool = typer.Option(False, "--json", help="Output as JSON"),
):
    """Semantic search for memories."""
    t = _get_transport()
    cfg = RetrievalConfig(layer=layer, category=category, top_k=top_k)
    results = t.recall(query, cfg=cfg, min_score=min_score)

    if json_output:
        typer.echo(json.dumps(results, default=str))
        return

    if not results:
        console.print("No matching memories found.")
        return

    by_source: dict[str, list] = {}
    for r in results:
        key = r.get("source", r.get("layer", "unknown"))
        by_source.setdefault(key, []).append(r)

    for src, items in by_source.items():
        console.print(f"\n── {src.capitalize()} {'─' * 40}")
        for r in items:
            score = r.get("final_score", 0)
            entity = r.get("entity_type", r.get("category", ""))
            console.print(f"  [{entity}] {r.get('content', '')[:80]}  (score={score:.2f})")


_UUID_PATTERN = re.compile(r'^[0-9a-f]{8}(-[0-9a-f]{4}){0,3}', re.IGNORECASE)


@app.command()
def forget(
    memory_id: str = typer.Argument(..., help="Memory ID, prefix, or content description"),
    json_output: bool = typer.Option(False, "--json", help="Output as JSON"),
):
    """Delete a memory by ID, prefix, or content search."""
    t = _get_transport()
    result = t.forget(memory_id)

    if "error" in result:
        if json_output:
            typer.echo(json.dumps(result))
        else:
            console.print(f"[red]Error: Memory not found: {memory_id}[/red]")
        raise typer.Exit(code=1)

    if json_output:
        typer.echo(json.dumps(result, default=str))
    else:
        console.print(
            f"[green]✓[/green] Forgotten: {result['id'][:8]} {result.get('content', '')}"
        )


@app.command()
def review(
    layer: str = typer.Option("semantic", help="Layer to review"),
    limit: int = typer.Option(100, help="Max entries to show"),
):
    """Browse memories by layer."""
    t = _get_transport()
    data = t.review(layer=layer, limit=limit)

    if layer == "working":
        if data:
            console.print("\n[Working Memory]")
            console.print(str(data))
        else:
            console.print("No working memory set.")
        return

    memories = data if isinstance(data, list) else []
    if not memories:
        console.print(f"No {layer} memories found.")
        return

    table = Table(title=f"{layer.capitalize()} Memory")
    table.add_column("ID", width=8)
    table.add_column("Category", width=10)
    table.add_column("Content", min_width=30)
    table.add_column("Date", width=16)

    for m in memories:
        if hasattr(m, "content"):
            date_str = m.created_at.strftime("%Y-%m-%d %H:%M") if m.created_at else ""
            table.add_row(m.id[:8], m.category, m.content[:60], date_str)
        elif isinstance(m, dict):
            date_str = str(m.get("created_at", ""))[:16]
            table.add_row(
                str(m.get("id", ""))[:8],
                m.get("category", ""),
                str(m.get("content", ""))[:60],
                date_str,
            )

    console.print(table)


@app.command()
def status(
    json_output: bool = typer.Option(False, "--json", help="Output as JSON"),
):
    """Show memory stats, CEO Brain entities, import progress, and LLM config."""
    t = _get_transport()
    data = t.status()
    counts = data.get("counts", {})
    total = data.get("total", 0)

    from memory_core.llm import get_llm_info
    llm_info = get_llm_info()

    # CEO Brain entity counts
    ceo_counts = {}
    try:
        ceo_db = t._get_ceo_db()
        ceo_counts = ceo_db.count_all()
    except Exception:
        pass

    # Import state
    import_info = {}
    try:
        from memory_core.import_agent import ImportState
        istate = ImportState()
        job = istate.get_job()
        import_info = {
            "job_status": job.status,
            "sessions_imported": job.sessions_imported or istate.session_count,
            "docs_imported": job.docs_imported or istate.doc_count,
        }
        if job.status == "running" and job.pid:
            try:
                os.kill(job.pid, 0)
                import_info["pid"] = job.pid
                import_info["progress"] = job.progress
            except ProcessLookupError:
                import_info["job_status"] = "stale"
    except Exception:
        pass

    if json_output:
        data["llm"] = llm_info
        data["ceo"] = ceo_counts
        data["import"] = import_info
        typer.echo(json.dumps(data))
        return

    # Legacy memory
    raw = data.get("raw", {})
    console.print(f"\n[bold]Legacy Memory[/bold]")
    console.print(f"  L1 Episodic   {counts.get('episodic', 0):>4}")
    console.print(f"  L2 Semantic   {counts.get('semantic', 0):>4}")
    console.print(f"  Total         {total:>4}")
    if raw:
        console.print(f"  Raw transcripts  {raw.get('total', 0):>4} ({raw.get('unprocessed', 0)} unprocessed)")

    # CEO Brain
    if ceo_counts:
        console.print(f"\n[bold]CEO Brain[/bold]")
        console.print(f"  Projects      {ceo_counts.get('projects', 0):>4}")
        console.print(f"  Decisions     {ceo_counts.get('decisions', 0):>4}")
        console.print(f"  Principles    {ceo_counts.get('principles', 0):>4}")
        console.print(f"  Episodes      {ceo_counts.get('episodes', 0):>4}")

    # Import status
    if import_info:
        console.print(f"\n[bold]Import[/bold]")
        job_st = import_info.get("job_status", "")
        if job_st == "running":
            console.print(f"  Status: [yellow]running[/yellow] (PID {import_info.get('pid', '?')}, progress: {import_info.get('progress', '?')})")
        elif job_st == "completed":
            console.print(f"  Status: [green]completed[/green]")
        elif job_st == "stale":
            console.print(f"  Status: [red]stale[/red] (process gone)")
        console.print(f"  Sessions imported: {import_info.get('sessions_imported', 0)}")
        console.print(f"  Docs imported:     {import_info.get('docs_imported', 0)}")

    # LLM
    console.print(f"\n[bold]LLM[/bold] mode: {llm_info['mode']}")
    if llm_info.get("local_model"):
        backend = llm_info.get("local_backend", "not loaded")
        console.print(f"  Local:  {llm_info['local_model']} ({backend})")
    if llm_info.get("remote_model"):
        console.print(f"  Remote: {llm_info['remote_model']}")


@app.command()
def sql(
    query: str = typer.Argument(..., help="SQL query to execute"),
    json_output: bool = typer.Option(False, "--json", help="Output as JSON"),
):
    """Execute a raw SQL query."""
    t = _get_transport()
    try:
        results = t.sql(query)
    except Exception as e:
        if json_output:
            typer.echo(json.dumps({"error": str(e)}))
        else:
            console.print(f"[red]Error: {e}[/red]")
        raise typer.Exit(code=1)

    if json_output:
        typer.echo(json.dumps(results, default=str))
        return

    if not results:
        console.print("(empty result)")
        return

    table = Table()
    for col in results[0].keys():
        table.add_column(col)
    for row in results:
        table.add_row(*[str(v) for v in row.values()])
    console.print(table)


@app.command()
def maintain(
    dry_run: bool = typer.Option(False, "--dry-run", help="Show what would happen"),
    json_output: bool = typer.Option(False, "--json", help="Output as JSON"),
):
    """Run maintenance tasks."""
    t = _get_transport()
    result = t.maintain(dry_run=dry_run)

    if json_output:
        typer.echo(json.dumps(result))
    else:
        if dry_run:
            console.print("Dry run:")
            console.print(f"  Would clean {result.get('would_clean_stale', 0)} stale L1 entries")
            console.print(f"  Would purge {result.get('would_purge_deleted', 0)} soft-deleted entries")
            promo = result.get("promotion_candidates", {})
            if promo:
                console.print(f"  Promotion candidates: {promo}")
        else:
            console.print(f"[green]✓[/green] Maintenance complete:")
            console.print(f"  Stale cleaned: {result.get('stale_cleaned', 0)}")
            console.print(f"  Deleted purged: {result.get('deleted_purged', 0)}")
            console.print(f"  Compressed: {result.get('compressed', 0)}")
            console.print(f"  Promoted: {result.get('promoted', 0)}")
            console.print(f"  Reviewed: {result.get('reviewed', 0)}")


# ---------------------------------------------------------------------------
# Help command
# ---------------------------------------------------------------------------


@app.command(name="help")
def help_cmd(
    subcmd: Optional[str] = typer.Argument(None, help="Subcommand to get help for"),
):
    """Show help for ClickMem commands."""
    import click
    ctx = click.get_current_context()
    if subcmd:
        # Find the subcommand and invoke its --help
        root = ctx.parent
        if root:
            cmd = root.command
            if isinstance(cmd, click.MultiCommand):
                sub = cmd.get_command(root, subcmd)
                if sub:
                    sub_ctx = click.Context(sub, info_name=subcmd, parent=root)
                    typer.echo(sub.get_help(sub_ctx))
                    return
        console.print(f"[red]Unknown command: {subcmd}[/red]")
        raise typer.Exit(1)

    typer.echo(ctx.parent.command.get_help(ctx.parent) if ctx.parent else "")


# ---------------------------------------------------------------------------
# Discover command
# ---------------------------------------------------------------------------


@app.command(name="discover")
def discover_cmd(
    json_output: bool = typer.Option(False, "--json", help="Output as JSON"),
):
    """Detect installed AI agents, their conversation history, and hook status."""
    from memory_core.import_agent import discover_agents

    agents = discover_agents()

    if json_output:
        from dataclasses import asdict
        typer.echo(json.dumps([asdict(a) for a in agents], default=str))
        return

    table = Table(title="Installed Agents")
    table.add_column("Agent", style="bold")
    table.add_column("History Dir")
    table.add_column("Sessions", justify="right")
    table.add_column("Docs", justify="right")
    table.add_column("Hook")

    for a in agents:
        hook_status = "[green]installed[/green]" if a.hook_installed else "[dim]not installed[/dim]"
        display_dir = a.history_dir.replace(os.path.expanduser("~"), "~")
        table.add_row(a.name, display_dir, str(a.session_count), str(a.doc_count), hook_status)

    console.print(table)
    total = sum(a.session_count for a in agents)
    console.print(f"\n[dim]Total sessions available for import: {total}[/dim]")


# ---------------------------------------------------------------------------
# Hooks sub-app
# ---------------------------------------------------------------------------

hooks_app = typer.Typer(name="hooks", help="Manage agent hooks (install, check status)")
app.add_typer(hooks_app)


@hooks_app.command(name="install")
def hooks_install(
    agent: str = typer.Option("all", "--agent", "-a", help="Agent to install hooks for (claude-code|cursor|openclaw|all)"),
    server_url: str = typer.Option("http://127.0.0.1:9527", "--server-url", help="ClickMem server URL for hooks"),
):
    """Install hooks for AI agents so they automatically send data to ClickMem."""
    installed = []

    if agent in ("claude-code", "all"):
        ok = _install_claude_hooks(server_url)
        if ok:
            installed.append("claude-code")

    if agent in ("cursor", "all"):
        ok = _install_cursor_hooks()
        if ok:
            installed.append("cursor")

    if agent in ("openclaw", "all"):
        ok = _install_openclaw_hooks()
        if ok:
            installed.append("openclaw")

    if installed:
        console.print(f"[green]Hooks installed for: {', '.join(installed)}[/green]")
    else:
        console.print("[yellow]No hooks installed.[/yellow]")


@hooks_app.command(name="status")
def hooks_status_cmd():
    """Check which agents have hooks installed."""
    from memory_core.import_agent import discover_agents

    agents = discover_agents()
    for a in agents:
        status = "[green]installed[/green]" if a.hook_installed else "[red]not installed[/red]"
        console.print(f"  {a.name}: {status}")


def _install_claude_hooks(server_url: str) -> bool:
    settings_path = os.path.expanduser("~/.claude/settings.json")
    try:
        data = {}
        if os.path.exists(settings_path):
            with open(settings_path) as f:
                data = json.load(f)

        hooks = data.setdefault("hooks", {})
        hook_url = f"{server_url}/hooks/claude-code"
        hook_entry = {"hooks": [{"type": "http", "url": hook_url}]}

        for event in ("SessionStart", "UserPromptSubmit", "Stop", "SessionEnd"):
            event_hooks = hooks.get(event, [])
            if not isinstance(event_hooks, list):
                event_hooks = []
            already = any(
                hook_url in json.dumps(h) for h in event_hooks
            )
            if not already:
                event_hooks.append(hook_entry)
            hooks[event] = event_hooks

        data["hooks"] = hooks
        os.makedirs(os.path.dirname(settings_path), exist_ok=True)
        with open(settings_path, "w") as f:
            json.dump(data, f, indent=2)
            f.write("\n")

        console.print(f"  Claude Code: hooks -> {hook_url}")
        return True
    except Exception as e:
        console.print(f"  [red]Claude Code hook install failed: {e}[/red]")
        return False


def _install_cursor_hooks() -> bool:
    src = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), "cursor-hooks")
    if not os.path.isdir(src):
        # Try relative to package
        for candidate in [
            os.path.join(os.path.dirname(__file__), "..", "..", "cursor-hooks"),
            os.path.expanduser("~/clickmem/cursor-hooks"),
        ]:
            if os.path.isdir(candidate):
                src = candidate
                break

    if not os.path.isdir(src):
        console.print("  [yellow]Cursor hooks source not found; skipping[/yellow]")
        return False

    dst = os.path.expanduser("~/.cursor/hooks/clickmem")
    try:
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        if os.path.islink(dst):
            os.unlink(dst)
        elif os.path.exists(dst):
            shutil.rmtree(dst)
        os.symlink(os.path.abspath(src), dst)
        console.print(f"  Cursor: {dst} -> {src}")
        return True
    except Exception as e:
        console.print(f"  [red]Cursor hook install failed: {e}[/red]")
        return False


def _install_openclaw_hooks() -> bool:
    oc_config = os.path.expanduser("~/.openclaw/openclaw.json")
    plugin_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), "clickmem-plugin")

    if not os.path.isdir(plugin_dir):
        console.print("  [yellow]OpenClaw plugin source not found; skipping[/yellow]")
        return False

    if not os.path.exists(oc_config):
        console.print("  [yellow]OpenClaw not installed; skipping[/yellow]")
        return False

    try:
        with open(oc_config) as f:
            config = json.load(f)

        plugins = config.setdefault("plugins", {})
        paths = plugins.setdefault("paths", [])
        abs_path = os.path.abspath(plugin_dir)
        if abs_path not in paths:
            paths.append(abs_path)

        enabled = plugins.setdefault("enabled", [])
        if "@openclaw/clickmem" not in enabled:
            enabled.append("@openclaw/clickmem")

        with open(oc_config, "w") as f:
            json.dump(config, f, indent=2)
            f.write("\n")

        console.print(f"  OpenClaw: plugin added at {abs_path}")
        return True
    except Exception as e:
        console.print(f"  [red]OpenClaw hook install failed: {e}[/red]")
        return False


# ---------------------------------------------------------------------------
# One-click setup command
# ---------------------------------------------------------------------------


@app.command(name="setup")
def setup_cmd(
    remote: Optional[str] = typer.Option(None, "--remote", "-r",
                                         help="Remote server URL (import destination)"),
    skip_import: bool = typer.Option(False, "--skip-import", help="Skip conversation history import"),
):
    """One-click setup: install service, install hooks, discover agents, import history."""
    import subprocess as sp

    steps = [
        ("Install service", ["service", "install"]),
        ("Install hooks", ["hooks", "install"]),
    ]

    console.print("\n[bold]ClickMem Setup[/bold]\n")

    # Step 1-2: service + hooks
    for label, cmd_args in steps:
        console.print(f"[bold]>>> {label}[/bold]")
        try:
            full_cmd = [sys.executable, "-m", "memory_core.cli"] + cmd_args
            result = sp.run(full_cmd, capture_output=True, text=True, timeout=60)
            if result.returncode == 0:
                console.print(f"  [green]done[/green]")
            else:
                console.print(f"  [yellow]warning[/yellow]: {result.stderr.strip()[:200]}")
        except Exception as e:
            console.print(f"  [yellow]skipped[/yellow]: {e}")

    # Step 3: wait for server
    console.print("[bold]>>> Waiting for server...[/bold]")
    import time
    for attempt in range(10):
        try:
            t = _get_transport()
            h = t.health()
            if h.get("status") == "ok":
                console.print(f"  [green]server ready[/green] ({h.get('total_memories', 0)} memories)")
                break
        except Exception:
            pass
        if attempt == 9:
            console.print("  [red]server not responding after 30s[/red]")
            console.print("  Check: memory service logs")
            raise typer.Exit(1)
        time.sleep(3)

    # Step 4: discover agents
    console.print("[bold]>>> Discovering agents...[/bold]")
    from memory_core.import_agent import discover_agents
    agents = discover_agents()
    total_sessions = 0
    for a in agents:
        hook = "[green]hook[/green]" if a.hook_installed else "[dim]no hook[/dim]"
        console.print(f"  {a.name}: {a.session_count} sessions, {a.doc_count} docs ({hook})")
        total_sessions += a.session_count

    # Step 5: import
    if skip_import:
        console.print("[bold]>>> Import skipped[/bold] (--skip-import)")
    elif total_sessions == 0:
        console.print("[bold]>>> No sessions to import[/bold]")
    else:
        console.print(f"[bold]>>> Importing {total_sessions} sessions...[/bold] (background)")
        import_args = [sys.executable, "-m", "memory_core.cli", "import", "--agent", "all"]
        if remote:
            import_args.extend(["--remote", remote])

        log_dir = os.path.expanduser("~/.clickmem")
        os.makedirs(log_dir, exist_ok=True)
        log_file = os.path.join(log_dir, "import.log")

        with open(log_file, "a") as lf:
            proc = sp.Popen(import_args, stdout=lf, stderr=lf, start_new_session=True)
        console.print(f"  Started PID {proc.pid}")
        console.print(f"  Log: {log_file}")

    # Summary
    console.print("\n[bold]Setup complete.[/bold]")
    console.print("  memory status        — check stats + import progress")
    console.print("  memory recall <q>    — search memories")
    console.print("  memory help          — all commands\n")


# ---------------------------------------------------------------------------
# Import command
# ---------------------------------------------------------------------------


@app.command(name="import")
def import_cmd(
    agent: str = typer.Option("all", "--agent", "-a",
                              help="Agent to import from (claude-code|cursor|openclaw|all)"),
    foreground: bool = typer.Option(False, "--foreground", "-f",
                                   help="Run synchronously in foreground (default: background)"),
    remote: Optional[str] = typer.Option(None, "--remote", "-r",
                                         help="Remote server URL for import destination"),
    path: Optional[str] = typer.Option(None, "--path", "-p",
                                       help="Scan a directory for CLAUDE.md/AGENTS.md to import"),
):
    """Import historical conversations and knowledge docs from AI agents."""
    from memory_core.import_agent import ImportState, run_import, ImportJob

    state = ImportState()

    if not foreground:
        # Async: fork to background
        import subprocess as sp
        cmd = [sys.executable, "-m", "memory_core.cli", "import", "--agent", agent, "--foreground"]
        if remote:
            cmd.extend(["--remote", remote])

        job = ImportJob(
            job_id=f"import-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S')}",
            started_at=datetime.now(timezone.utc).isoformat(),
            agent=agent,
            status="running",
        )

        log_dir = os.path.expanduser("~/.clickmem")
        os.makedirs(log_dir, exist_ok=True)
        log_file = os.path.join(log_dir, "import.log")

        with open(log_file, "a") as lf:
            proc = sp.Popen(cmd, stdout=lf, stderr=lf, start_new_session=True)

        job.pid = proc.pid
        state.set_job(job)

        console.print(f"[green]Import started[/green] (PID {proc.pid})")
        console.print(f"  Agent: {agent}")
        console.print(f"  Log: {log_file}")
        console.print(f"\nUse [bold]memory status[/bold] to check progress.")
        return

    # Foreground mode
    if remote:
        from memory_core.transport import RemoteTransport
        t = RemoteTransport(remote, api_key=_remote_api_key or os.environ.get("CLICKMEM_API_KEY", ""))
    else:
        t = _get_transport()

    from datetime import datetime, timezone

    job = ImportJob(
        job_id=f"import-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S')}",
        started_at=datetime.now(timezone.utc).isoformat(),
        agent=agent,
        status="running",
        pid=os.getpid(),
    )
    state.set_job(job)

    agents_list = [agent] if agent != "all" else ["all"]

    def _on_progress(sessions: int, docs: int, msg: str):
        job.progress = sessions
        job.sessions_imported = stats.get("sessions_imported", 0)
        job.docs_imported = stats.get("docs_imported", 0)
        if sessions % 5 == 0:
            state.set_job(job)
        console.print(f"  [{sessions}] {msg}")

    stats: dict = {}
    try:
        stats = run_import(t, agents_list, state, on_progress=_on_progress)

        if path:
            from memory_core.import_agent import scan_path, build_text_with_header
            path_docs = scan_path(path)
            for doc in path_docs:
                if state.is_doc_current(doc.path):
                    stats["docs_skipped"] = stats.get("docs_skipped", 0) + 1
                    continue
                text = build_text_with_header(
                    doc.content, doc_type=doc.doc_type,
                    project_name=doc.project_name, cwd=doc.cwd,
                    github_url=doc.github_url,
                )
                try:
                    t.ingest(text=text, session_id=f"doc-{os.path.basename(doc.path)}", source="import", cwd=doc.cwd)
                    state.mark_doc(doc.path)
                    stats["docs_imported"] = stats.get("docs_imported", 0) + 1
                    console.print(f"  [doc] {doc.doc_type}/{doc.project_name}")
                except Exception as e:
                    stats["errors"] = stats.get("errors", 0) + 1
                    console.print(f"  [red]doc error: {e}[/red]")

        job.status = "completed"
        job.sessions_imported = stats.get("sessions_imported", 0)
        job.docs_imported = stats.get("docs_imported", 0)
        job.total = stats.get("sessions_imported", 0) + stats.get("sessions_skipped", 0)
    except Exception as e:
        job.status = "failed"
        job.error = str(e)
        logger.exception("Import failed")
    finally:
        state.set_job(job)

    console.print(f"\n[bold]Import {'completed' if job.status == 'completed' else 'FAILED'}[/bold]")
    console.print(f"  Sessions: {stats.get('sessions_imported', 0)} imported, {stats.get('sessions_skipped', 0)} skipped")
    console.print(f"  Docs:     {stats.get('docs_imported', 0)} imported, {stats.get('docs_skipped', 0)} skipped")
    if stats.get("errors"):
        console.print(f"  [red]Errors: {stats['errors']}[/red]")


# ---------------------------------------------------------------------------
# Service management
# ---------------------------------------------------------------------------

service_app = typer.Typer(name="service", help="Manage the ClickMem background service (launchd / systemd)")
app.add_typer(service_app)


@service_app.command(name="install")
def service_install(
    host: str = typer.Option(
        os.environ.get("CLICKMEM_SERVER_HOST", "0.0.0.0"),
        "--host", "-H", help="Bind address (0.0.0.0 for LAN). Env: CLICKMEM_SERVER_HOST",
    ),
    port: int = typer.Option(
        int(os.environ.get("CLICKMEM_SERVER_PORT", "9527")),
        "--port", "-p", help="HTTP port for the server. Env: CLICKMEM_SERVER_PORT",
    ),
):
    """Install and start ClickMem as a background service."""
    from memory_core.service import install
    try:
        path = install(host=host, port=port)
        console.print(f"[green]✓[/green] Service installed and started")
        console.print(f"  Config: {path}")
        console.print(f"  Listen: {host}:{port}")
        console.print(f"\n  Check status:  memory service status")
        console.print(f"  View logs:     memory service logs")
    except Exception as e:
        console.print(f"[red]Error: {e}[/red]")
        raise typer.Exit(code=1)


@service_app.command(name="uninstall")
def service_uninstall():
    """Stop and remove the ClickMem background service."""
    from memory_core.service import uninstall
    removed = uninstall()
    if removed:
        console.print("[green]✓[/green] Service stopped and removed")
    else:
        console.print("Service was not installed.")


@service_app.command(name="start")
def service_start():
    """Start the ClickMem background service."""
    from memory_core.service import start
    try:
        start()
        console.print("[green]✓[/green] Service started")
    except Exception as e:
        console.print(f"[red]Error: {e}[/red]")
        raise typer.Exit(code=1)


@service_app.command(name="stop")
def service_stop():
    """Stop the ClickMem background service."""
    from memory_core.service import stop
    stop()
    console.print("[green]✓[/green] Service stopped")


@service_app.command(name="status")
def service_status(
    json_output: bool = typer.Option(False, "--json", help="Output as JSON"),
):
    """Show the ClickMem service status."""
    from memory_core.service import status
    info = status()

    if json_output:
        typer.echo(json.dumps(info, default=str))
        return

    state = "[green]running[/green]" if info["running"] else "[red]stopped[/red]"
    console.print(f"  Status:    {state}")
    console.print(f"  Installed: {'yes' if info['installed'] else 'no'}")
    if info.get("pid"):
        console.print(f"  PID:       {info['pid']}")
    if info.get("config"):
        console.print(f"  Config:    {info['config']}")


@service_app.command(name="logs")
def service_logs(
    follow: bool = typer.Option(False, "-f", "--follow", help="Follow log output"),
    lines: int = typer.Option(50, "-n", "--lines", help="Number of lines to show"),
):
    """Show ClickMem service logs."""
    from memory_core.service import log_path
    lp = log_path()
    if not lp.exists():
        console.print("No log file found yet.")
        return

    if follow:
        os.execvp("tail", ["tail", "-f", "-n", str(lines), str(lp)])
    else:
        import subprocess
        result = subprocess.run(
            ["tail", "-n", str(lines), str(lp)],
            capture_output=True, text=True,
        )
        if result.stdout:
            console.print(result.stdout, end="")


# ---------------------------------------------------------------------------
# Server commands
# ---------------------------------------------------------------------------


@app.command()
def serve(
    host: str = typer.Option(
        os.environ.get("CLICKMEM_SERVER_HOST", "127.0.0.1"),
        "--host", "-H", help="Bind address (use 0.0.0.0 for LAN). Env: CLICKMEM_SERVER_HOST",
    ),
    port: int = typer.Option(
        int(os.environ.get("CLICKMEM_SERVER_PORT", "9527")),
        "--port", "-p", help="HTTP port. Env: CLICKMEM_SERVER_PORT",
    ),
    debug: bool = typer.Option(False, "--debug", help="Enable /v1/sql endpoint"),
    no_mcp: bool = typer.Option(False, "--no-mcp", help="Disable MCP SSE on /sse (REST-only)"),
    no_mdns: bool = typer.Option(False, "--no-mdns", help="Disable mDNS registration"),
    api_key_gen: bool = typer.Option(False, "--gen-key", help="Generate and print a new API key"),
):
    """Start the ClickMem server (REST API + MCP SSE on a single port)."""
    if api_key_gen:
        from memory_core.auth import generate_api_key
        key = generate_api_key()
        console.print(f"Generated API key: [bold]{key}[/bold]")
        console.print(f"Set it: export CLICKMEM_API_KEY={key}")
        return

    enable_mcp = not no_mcp
    console.print(f"Starting ClickMem server on {host}:{port}")
    if enable_mcp:
        console.print(f"  REST API: http://{host}:{port}/v1/...")
        console.print(f"  MCP SSE:  http://{host}:{port}/sse")
    if debug:
        console.print("[yellow]⚠ Debug mode: /v1/sql endpoint is enabled[/yellow]")
    if os.environ.get("CLICKMEM_API_KEY"):
        console.print("[green]✓[/green] API key authentication enabled")
    else:
        console.print("[yellow]⚠ No API key set — server is open (set CLICKMEM_API_KEY to secure)[/yellow]")

    from memory_core.server import run_server
    run_server(host=host, port=port, debug=debug, register_mdns=not no_mdns, mcp=enable_mcp)


@app.command()
def mcp(
    transport: str = typer.Option("stdio", help="Transport mode: stdio"),
):
    """Start the ClickMem MCP server over stdio (for Claude Code / Cursor)."""
    if transport == "stdio":
        from memory_core.mcp_server import main_stdio
        main_stdio()
    else:
        console.print(
            f"[red]Unknown transport: {transport}.[/red]\n"
            "Use 'stdio' for local MCP, or 'memory serve' for LAN (REST + MCP SSE on one port)."
        )
        raise typer.Exit(code=1)


@app.command(name="discover-server")
def discover_server_cmd(
    timeout: float = typer.Option(3.0, help="Discovery timeout in seconds"),
    json_output: bool = typer.Option(False, "--json", help="Output as JSON"),
):
    """Discover ClickMem servers on the local network via mDNS."""
    try:
        from memory_core.discovery import discover
    except ImportError:
        console.print("[red]Error: zeroconf not installed. Run: pip install clickmem[server][/red]")
        raise typer.Exit(code=1)

    console.print(f"Scanning for ClickMem servers ({timeout}s)...")
    servers = discover(timeout=timeout)

    if json_output:
        typer.echo(json.dumps(servers))
        return

    if not servers:
        console.print("No ClickMem servers found on LAN.")
        return

    for s in servers:
        ver = s.get("properties", {}).get("version", "?")
        api = s.get("properties", {}).get("api", "?")
        console.print(f"  [green]✓[/green] {s['host']}:{s['port']}  v{ver}  ({api})")
    console.print(f"\nTo connect: memory recall 'query' --remote http://{servers[0]['host']}:{servers[0]['port']}")


# ---------------------------------------------------------------------------
# Legacy local-only commands (import / export / uninstall)
# ---------------------------------------------------------------------------


@app.command(name="import-openclaw")
def import_openclaw(
    openclaw_dir: str = typer.Option(
        os.path.expanduser("~/.openclaw"),
        help="Path to OpenClaw data directory",
    ),
    json_output: bool = typer.Option(False, "--json", help="Output as JSON"),
):
    """Import OpenClaw memory history into clickmem."""
    openclaw_dir = os.path.expanduser(openclaw_dir)
    if not os.path.isdir(openclaw_dir):
        if json_output:
            typer.echo(json.dumps({"error": f"Directory not found: {openclaw_dir}"}))
        else:
            console.print(f"[red]Error: Directory not found: {openclaw_dir}[/red]")
        raise typer.Exit(code=1)

    db = _get_db()
    emb = _get_emb()

    md_result = import_workspace_memories(db, emb, openclaw_dir)
    sqlite_result = import_sqlite_chunks(db, emb, openclaw_dir)

    total = {
        "workspace_memories": md_result,
        "sqlite_chunks": sqlite_result,
        "total_imported": md_result["imported"] + sqlite_result["imported"],
        "total_skipped": md_result["skipped"] + sqlite_result["skipped"],
    }

    if json_output:
        typer.echo(json.dumps(total))
    else:
        console.print(f"[green]✓[/green] OpenClaw import complete:")
        console.print(f"  Workspace .md files: {md_result['imported']} imported, {md_result['skipped']} skipped")
        console.print(f"  SQLite chunks:       {sqlite_result['imported']} imported, {sqlite_result['skipped']} skipped")
        console.print(f"  Total:               {total['total_imported']} imported")


@app.command(name="export-context")
def export_context(
    workspace_path: str = typer.Argument("", help="Workspace directory to export into (omit for --content mode)"),
    json_output: bool = typer.Option(False, "--json", help="Output as JSON"),
    content_mode: bool = typer.Option(False, "--content", help="Output markdown content as JSON (no file writes)"),
    max_items: int = typer.Option(50, "--max-items", "-n", help="Max memory entries per section"),
    max_chars: int = typer.Option(8000, "--max-chars", "-c", help="Max chars per section"),
):
    """Export memories to workspace .md files or as JSON content."""
    from memory_core.md_sync_mod import md_sync

    db = _get_db()
    limits = dict(max_items=max_items, max_chars=max_chars)

    if content_mode:
        result = {
            "memory_md": md_sync.format_memory_md(db, **limits),
            "daily_md": md_sync.format_daily_md(db, **limits),
        }
        typer.echo(json.dumps(result))
        return

    if not workspace_path:
        console.print("[red]Error: workspace_path required (or use --content)[/red]")
        raise typer.Exit(code=1)

    workspace_path = os.path.expanduser(workspace_path)
    os.makedirs(workspace_path, exist_ok=True)

    memory_md = md_sync.export_memory_md(db, workspace_path, **limits)
    daily_md = md_sync.export_daily_md(db, workspace_path, **limits)

    result = {"memory_md": memory_md, "daily_md": daily_md}

    if json_output:
        typer.echo(json.dumps(result))
    else:
        console.print(f"[green]✓[/green] Exported context to {workspace_path}")
        console.print(f"  {memory_md}")
        console.print(f"  {daily_md}")


@app.command()
def uninstall(
    export_back: bool = typer.Option(
        False, "--export", help="Export memories back to OpenClaw .md files before uninstalling",
    ),
    openclaw_dir: str = typer.Option(
        os.path.expanduser("~/.openclaw"),
        help="Path to OpenClaw data directory",
    ),
    yes: bool = typer.Option(False, "-y", "--yes", help="Skip confirmation prompt"),
    json_output: bool = typer.Option(False, "--json", help="Output as JSON"),
):
    """Uninstall clickmem: optionally export memories back, remove hook and data."""
    from memory_core.md_sync_mod import md_sync

    openclaw_dir = os.path.expanduser(openclaw_dir)
    result: dict = {"exported_workspaces": [], "hook_removed": False, "data_removed": False}

    if export_back:
        db = _get_db()
        if os.path.isdir(openclaw_dir):
            import glob
            workspace_dirs = sorted(glob.glob(os.path.join(openclaw_dir, "workspace-*")))
            for ws in workspace_dirs:
                ws_name = os.path.basename(ws)
                try:
                    md_sync.export_memory_md(db, ws)
                    md_sync.export_daily_md(db, ws)
                    result["exported_workspaces"].append(ws_name)
                except Exception as e:
                    if not json_output:
                        console.print(f"  [yellow]Warning: failed to export to {ws_name}: {e}[/yellow]")

            if not json_output:
                n = len(result["exported_workspaces"])
                console.print(f"[green]✓[/green] Exported memories to {n} workspace(s)")

    if not yes and not json_output:
        console.print("\nThis will:")
        console.print("  - Remove clickmem chDB data (~/.openclaw/memory/chdb-data/)")
        console.print("  - Uninstall the OpenClaw hook")
        confirm = typer.confirm("Continue?")
        if not confirm:
            console.print("Aborted.")
            raise typer.Exit(code=0)

    openclaw_config = os.path.expanduser("~/.openclaw/openclaw.json")
    if os.path.isfile(openclaw_config):
        try:
            with open(openclaw_config) as f:
                cfg = json.load(f)
            hooks = cfg.get("hooks", {}).get("internal", {})
            extra = hooks.get("load", {}).get("extraDirs", [])
            new_extra = [d for d in extra if "clickmem" not in d]
            if len(new_extra) != len(extra):
                hooks.get("load", {})["extraDirs"] = new_extra
            entries = hooks.get("entries", {})
            entries.pop("clickmem-hook", None)
            with open(openclaw_config, "w") as f:
                json.dump(cfg, f, indent=2)
            result["hook_removed"] = True
        except Exception:
            pass

    chdb_data = os.path.expanduser("~/.openclaw/memory/chdb-data")
    if os.path.isdir(chdb_data):
        shutil.rmtree(chdb_data)
        result["data_removed"] = True

    if json_output:
        typer.echo(json.dumps(result))
    else:
        console.print(f"\n[green]✓[/green] ClickMem uninstalled.")
        if result["hook_removed"]:
            console.print("  Hook removed.")
        if result["data_removed"]:
            console.print("  chDB data removed.")
        if result["exported_workspaces"]:
            console.print(f"  Memories exported to {len(result['exported_workspaces'])} workspace(s).")


# ---------------------------------------------------------------------------
# CEO Brain commands
# ---------------------------------------------------------------------------


def _get_ceo_db():
    t = _get_transport()
    return t._get_ceo_db()


@app.command(name="portfolio")
def portfolio_cmd(
    json_output: bool = typer.Option(False, "--json", help="Output as JSON"),
):
    """Show all projects overview."""
    from memory_core.ceo_skills import ceo_portfolio
    t = _get_transport()
    ceo_db = t._get_ceo_db()
    emb = t._get_emb()
    result = ceo_portfolio(ceo_db, emb)

    if json_output:
        typer.echo(json.dumps(result, default=str))
        return

    projects = result.get("projects", [])
    if not projects:
        console.print("[dim]No projects found.[/dim]")
        return

    table = Table(title="CEO Portfolio")
    table.add_column("Name", style="bold")
    table.add_column("Status")
    table.add_column("Decisions")
    table.add_column("Episodes")
    table.add_column("Latest Activity")

    for p in projects:
        table.add_row(
            p["name"], p["status"],
            str(p["recent_decisions"]), str(p["recent_episodes"]),
            p.get("latest_activity", "")[:60],
        )
    console.print(table)

    totals = result.get("totals", {})
    if totals:
        console.print(f"\n[dim]Totals: {totals}[/dim]")


@app.command(name="brief")
def brief_cmd(
    project_id: str = typer.Option("", "--project-id", "-p", help="Project ID"),
    query: str = typer.Option("", "--query", "-q", help="Search query"),
    json_output: bool = typer.Option(False, "--json", help="Output as JSON"),
):
    """Get a detailed project briefing."""
    from memory_core.ceo_skills import ceo_brief
    t = _get_transport()
    ceo_db = t._get_ceo_db()
    emb = t._get_emb()
    result = ceo_brief(ceo_db, emb, project_id=project_id, query=query)

    if json_output:
        typer.echo(json.dumps(result, default=str))
        return

    if "project" in result:
        p = result["project"]
        console.print(f"\n[bold]{p['name']}[/bold] ({p['status']})")
        if p.get("description"):
            console.print(f"  {p['description']}")

    if result.get("principles"):
        console.print("\n[bold]Principles:[/bold]")
        for p in result["principles"][:5]:
            console.print(f"  [{p['confidence']:.0%}] {p['content']}")

    if result.get("decisions"):
        console.print("\n[bold]Recent Decisions:[/bold]")
        for d in result["decisions"][:5]:
            console.print(f"  - {d['title']}: {d['choice']}")

    if result.get("recent_activity"):
        console.print("\n[bold]Recent Activity:[/bold]")
        for a in result["recent_activity"][:3]:
            console.print(f"  - {a['content'][:80]}")


@app.command(name="projects")
def projects_cmd(
    status_filter: Optional[str] = typer.Option(None, "--status", "-s", help="Filter by status"),
    json_output: bool = typer.Option(False, "--json", help="Output as JSON"),
):
    """List all projects."""
    ceo_db = _get_ceo_db()
    projects = ceo_db.list_projects(status=status_filter)

    if json_output:
        typer.echo(json.dumps([{
            "id": p.id, "name": p.name, "status": p.status,
            "description": p.description[:100],
        } for p in projects], default=str))
        return

    if not projects:
        console.print("[dim]No projects found.[/dim]")
        return

    table = Table(title="Projects")
    table.add_column("ID", style="dim")
    table.add_column("Name", style="bold")
    table.add_column("Status")
    table.add_column("Description")

    for p in projects:
        table.add_row(p.id[:8], p.name, p.status, p.description[:60])
    console.print(table)


@app.command(name="decisions")
def decisions_cmd(
    project_id: Optional[str] = typer.Option(None, "--project-id", "-p", help="Filter by project"),
    limit: int = typer.Option(20, "--limit", "-n", help="Max results"),
    json_output: bool = typer.Option(False, "--json", help="Output as JSON"),
):
    """List decisions."""
    ceo_db = _get_ceo_db()
    decisions = ceo_db.list_decisions(project_id=project_id, limit=limit)

    if json_output:
        typer.echo(json.dumps([{
            "id": d.id, "title": d.title, "choice": d.choice,
            "outcome_status": d.outcome_status, "domain": d.domain,
        } for d in decisions], default=str))
        return

    if not decisions:
        console.print("[dim]No decisions found.[/dim]")
        return

    table = Table(title="Decisions")
    table.add_column("ID", style="dim")
    table.add_column("Title", style="bold")
    table.add_column("Choice")
    table.add_column("Status")
    table.add_column("Domain")

    for d in decisions:
        table.add_row(d.id[:8], d.title, d.choice[:40], d.outcome_status, d.domain)
    console.print(table)


@app.command(name="principles")
def principles_cmd(
    project_id: Optional[str] = typer.Option(None, "--project-id", "-p", help="Filter by project"),
    json_output: bool = typer.Option(False, "--json", help="Output as JSON"),
):
    """List principles."""
    ceo_db = _get_ceo_db()
    principles = ceo_db.list_principles(project_id=project_id)

    if json_output:
        typer.echo(json.dumps([{
            "id": p.id, "content": p.content, "confidence": p.confidence,
            "evidence_count": p.evidence_count, "domain": p.domain,
        } for p in principles], default=str))
        return

    if not principles:
        console.print("[dim]No principles found.[/dim]")
        return

    table = Table(title="Principles")
    table.add_column("Confidence", justify="right")
    table.add_column("Content")
    table.add_column("Evidence", justify="right")
    table.add_column("Domain")

    for p in principles:
        table.add_row(f"{p.confidence:.0%}", p.content[:60], str(p.evidence_count), p.domain)
    console.print(table)
