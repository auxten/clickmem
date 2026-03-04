"""CLI for the memory system — typer-based commands."""

from __future__ import annotations

import json
import os
import shutil
import sys
from typing import Optional

import typer
from rich.console import Console
from rich.table import Table

from memory_core.db import MemoryDB
from memory_core.models import Memory, RetrievalConfig
from memory_core.import_openclaw import import_workspace_memories, import_sqlite_chunks

app = typer.Typer(name="memory", help="OpenClaw Memory — three-layer memory management")
console = Console()

_DB_PATH = os.environ.get("CLICKMEM_DB_PATH", os.path.expanduser("~/.openclaw/memory/chdb-data"))

# Singleton DB instance to avoid creating multiple chDB sessions
_db_instance: MemoryDB | None = None


def _get_db() -> MemoryDB:
    global _db_instance
    if _db_instance is None:
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


@app.command()
def remember(
    content: str = typer.Argument(..., help="Memory content to store"),
    layer: str = typer.Option("semantic", help="Memory layer: working, episodic, semantic"),
    category: str = typer.Option("knowledge", help="Memory category"),
    tags: Optional[str] = typer.Option(None, help="Comma-separated tags"),
    json_output: bool = typer.Option(False, "--json", help="Output as JSON"),
):
    """Store a memory."""
    if not content.strip():
        console.print("[red]Error: content cannot be empty[/red]")
        raise typer.Exit(code=1)

    db = _get_db()
    emb = _get_emb()

    tag_list = [t.strip() for t in tags.split(",")] if tags else []

    if layer == "working":
        mid = db.set_working(content)
        if json_output:
            typer.echo(json.dumps({"id": mid, "layer": "working", "status": "stored"}))
        else:
            console.print(f"[green]✓[/green] Working memory updated.")
        return

    m = Memory(
        content=content,
        layer=layer,
        category=category,
        tags=tag_list,
        embedding=emb.encode_document(content),
        source="cli",
    )
    db.insert(m)

    if json_output:
        typer.echo(json.dumps({
            "id": m.id,
            "layer": layer,
            "category": category,
            "status": "stored",
        }))
    else:
        console.print(
            f"[green]✓[/green] Stored [{layer}/{category}]: {content}\n"
            f"  id={m.id[:8]}  tags={','.join(tag_list)}"
        )


@app.command()
def recall(
    query: str = typer.Argument(..., help="Search query"),
    layer: Optional[str] = typer.Option(None, help="Filter by layer"),
    category: Optional[str] = typer.Option(None, help="Filter by category"),
    json_output: bool = typer.Option(False, "--json", help="Output as JSON"),
):
    """Semantic search for memories."""
    db = _get_db()
    emb = _get_emb()

    from memory_core.retrieval import hybrid_search
    cfg = RetrievalConfig(layer=layer, category=category)
    results = hybrid_search(db, emb, query, cfg=cfg)

    if json_output:
        typer.echo(json.dumps(results, default=str))
        return

    if not results:
        console.print("No matching memories found.")
        return

    # Group by layer
    by_layer: dict[str, list] = {}
    for r in results:
        by_layer.setdefault(r["layer"], []).append(r)

    for lyr, items in by_layer.items():
        console.print(f"\n── {lyr.capitalize()} {'─' * 40}")
        for r in items:
            score = r.get("final_score", 0)
            cat = r.get("category", "")
            console.print(f"  [{cat}] {r['content']}  (score={score:.2f})")


@app.command()
def forget(
    memory_id: str = typer.Argument(..., help="Memory ID or prefix"),
    json_output: bool = typer.Option(False, "--json", help="Output as JSON"),
):
    """Delete a memory."""
    db = _get_db()

    # Try exact match first
    m = db.get(memory_id)
    if m is None:
        # Try prefix match
        rows = db.query(
            f"SELECT id, content, layer, category FROM memories "
            f"WHERE startsWith(id, '{db._escape(memory_id)}') AND is_active = 1 LIMIT 1"
        )
        if rows:
            memory_id = rows[0]["id"]
            m = db.get(memory_id)

    if m is None:
        if json_output:
            typer.echo(json.dumps({"error": "not found"}))
        else:
            console.print(f"[red]Error: Memory not found: {memory_id}[/red]")
        raise typer.Exit(code=1)

    db.deactivate(memory_id)

    if json_output:
        typer.echo(json.dumps({"id": memory_id, "status": "deleted"}))
    else:
        console.print(
            f"[green]✓[/green] Forgotten: {memory_id[:8]} "
            f"[{m.layer}/{m.category}] {m.content}"
        )


@app.command()
def review(
    layer: str = typer.Option("semantic", help="Layer to review"),
    limit: int = typer.Option(100, help="Max entries to show"),
):
    """Browse memories by layer."""
    db = _get_db()

    if layer == "working":
        content = db.get_working()
        if content:
            console.print("\n[Working Memory]")
            console.print(content)
        else:
            console.print("No working memory set.")
        return

    memories = db.list_by_layer(layer, limit=limit)
    if not memories:
        console.print(f"No {layer} memories found.")
        return

    table = Table(title=f"{layer.capitalize()} Memory")
    table.add_column("ID", width=8)
    table.add_column("Category", width=10)
    table.add_column("Content", min_width=30)
    table.add_column("Date", width=16)

    for m in memories:
        date_str = ""
        if m.created_at:
            date_str = m.created_at.strftime("%Y-%m-%d %H:%M")
        table.add_row(m.id[:8], m.category, m.content[:60], date_str)

    console.print(table)


@app.command()
def status(
    json_output: bool = typer.Option(False, "--json", help="Output as JSON"),
):
    """Show per-layer statistics."""
    db = _get_db()
    counts = db.count_by_layer()
    total = db.count()
    stats_data = db.stats()

    if json_output:
        typer.echo(json.dumps({
            "counts": counts,
            "total": total,
            "breakdown": stats_data,
        }))
        return

    console.print(f"\nL0 Working    {counts.get('working', 0):>4} entries")
    console.print(f"L1 Episodic   {counts.get('episodic', 0):>4} entries")
    console.print(f"L2 Semantic   {counts.get('semantic', 0):>4} entries")
    console.print(f"{'─' * 35}")
    console.print(f"Total         {total:>4} entries")


@app.command()
def sql(
    query: str = typer.Argument(..., help="SQL query to execute"),
    json_output: bool = typer.Option(False, "--json", help="Output as JSON"),
):
    """Execute a raw SQL query."""
    db = _get_db()
    try:
        results = db.query(query)
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
    db = _get_db()

    if dry_run:
        stale = db.find_stale_episodic()
        deleted = db.find_deleted()
        tag_freqs = db.get_tag_frequencies()

        result = {
            "dry_run": True,
            "would_clean_stale": len(stale),
            "would_purge_deleted": len(deleted),
            "promotion_candidates": dict(tag_freqs),
        }

        if json_output:
            typer.echo(json.dumps(result))
        else:
            console.print(f"Dry run:")
            console.print(f"  Would clean {len(stale)} stale L1 entries")
            console.print(f"  Would purge {len(deleted)} soft-deleted entries")
            if tag_freqs:
                console.print(f"  Promotion candidates: {dict(tag_freqs)}")
        return

    from memory_core.maintenance_mod import maintenance as maint
    emb = _get_emb()

    def _mock_llm(prompt: str) -> str:
        from tests.helpers.mock_llm import MockLLMComplete
        return MockLLMComplete()(prompt)

    result = maint.run_all(db, llm_complete=_mock_llm, emb=emb)

    if json_output:
        typer.echo(json.dumps(result))
    else:
        console.print(f"[green]✓[/green] Maintenance complete:")
        console.print(f"  Stale cleaned: {result['stale_cleaned']}")
        console.print(f"  Deleted purged: {result['deleted_purged']}")
        console.print(f"  Compressed: {result['compressed']}")
        console.print(f"  Promoted: {result['promoted']}")
        console.print(f"  Reviewed: {result['reviewed']}")


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
    workspace_path: str = typer.Argument(..., help="Workspace directory to export into"),
    json_output: bool = typer.Option(False, "--json", help="Output as JSON"),
):
    """Export memories to workspace .md files (for hook integration)."""
    from memory_core.md_sync_mod import md_sync

    workspace_path = os.path.expanduser(workspace_path)
    os.makedirs(workspace_path, exist_ok=True)

    db = _get_db()

    memory_md = md_sync.export_memory_md(db, workspace_path)
    daily_md = md_sync.export_daily_md(db, workspace_path)

    result = {
        "memory_md": memory_md,
        "daily_md": daily_md,
    }

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

    # ── 1. Export back to OpenClaw workspace .md files ────────────────
    if export_back:
        db = _get_db()
        # Find all existing workspace dirs and export into each
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

    # ── 2. Confirm before destructive actions ─────────────────────────
    if not yes and not json_output:
        console.print("\nThis will:")
        console.print("  - Remove clickmem chDB data (~/.openclaw/memory/chdb-data/)")
        console.print("  - Uninstall the OpenClaw hook")
        confirm = typer.confirm("Continue?")
        if not confirm:
            console.print("Aborted.")
            raise typer.Exit(code=0)

    # ── 3. Remove OpenClaw hook from config ──────────────────────────
    openclaw_config = os.path.expanduser("~/.openclaw/openclaw.json")
    if os.path.isfile(openclaw_config):
        try:
            with open(openclaw_config) as f:
                cfg = json.load(f)
            hooks = cfg.get("hooks", {}).get("internal", {})
            # Remove from extraDirs
            extra = hooks.get("load", {}).get("extraDirs", [])
            # Find and remove any path containing "clickmem"
            new_extra = [d for d in extra if "clickmem" not in d]
            if len(new_extra) != len(extra):
                hooks.get("load", {})["extraDirs"] = new_extra
            # Remove from entries
            entries = hooks.get("entries", {})
            entries.pop("clickmem-hook", None)
            with open(openclaw_config, "w") as f:
                json.dump(cfg, f, indent=2)
            result["hook_removed"] = True
        except Exception:
            pass

    # ── 4. Remove chDB data directory ─────────────────────────────────
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
