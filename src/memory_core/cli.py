"""CLI for the memory system — typer-based commands."""

from __future__ import annotations

import json
import logging
import os
import re
import shutil
import sys
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
):
    global _remote_url, _remote_api_key
    _remote_url = remote
    _remote_api_key = api_key


# ---------------------------------------------------------------------------
# Transport helper — local or remote depending on --remote
# ---------------------------------------------------------------------------

_transport_instance = None


def _get_transport():
    global _transport_instance
    if _transport_instance is None:
        from memory_core.transport import get_transport
        _transport_instance = get_transport(remote=_remote_url, api_key=_remote_api_key)
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
    try:
        from memory_core.embedding import EmbeddingEngine
        emb = EmbeddingEngine()
        emb.load()
        return emb
    except Exception:
        from tests.helpers.mock_embedding import MockEmbeddingEngine
        emb = MockEmbeddingEngine(dimension=256)
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

    by_layer: dict[str, list] = {}
    for r in results:
        by_layer.setdefault(r["layer"], []).append(r)

    for lyr, items in by_layer.items():
        console.print(f"\n── {lyr.capitalize()} {'─' * 40}")
        for r in items:
            score = r.get("final_score", 0)
            cat = r.get("category", "")
            console.print(f"  [{cat}] {r['content']}  (score={score:.2f})")


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
    """Show per-layer statistics and LLM configuration."""
    t = _get_transport()
    data = t.status()
    counts = data.get("counts", {})
    total = data.get("total", 0)

    from memory_core.llm import get_llm_info
    llm_info = get_llm_info()

    if json_output:
        data["llm"] = llm_info
        typer.echo(json.dumps(data))
        return

    raw = data.get("raw", {})
    console.print(f"\nL0 Working    {counts.get('working', 0):>4} entries  (deprecated)")
    console.print(f"L1 Episodic   {counts.get('episodic', 0):>4} entries")
    console.print(f"L2 Semantic   {counts.get('semantic', 0):>4} entries")
    console.print(f"{'─' * 40}")
    console.print(f"Total         {total:>4} entries")
    if raw:
        console.print(f"\nRaw transcripts  {raw.get('total', 0):>4} total")
        console.print(f"  Processed      {raw.get('processed', 0):>4}")
        console.print(f"  Unprocessed    {raw.get('unprocessed', 0):>4}")
    console.print(f"\nLLM mode: {llm_info['mode']}")
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


@app.command(name="discover")
def discover_cmd(
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
