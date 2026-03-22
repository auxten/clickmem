#!/usr/bin/env python3
"""One-time data cleanup script for CEO Brain quality issues.

Run this AFTER deploying new code (so prune_weak_principles is available).
This script requires exclusive access to the chDB database, so make sure
no MCP server or other ClickMem process is running.

Usage:
    python scripts/cleanup_data.py --dry-run   # Preview only
    python scripts/cleanup_data.py             # Execute cleanup
"""

import argparse
import os
import sys
from datetime import datetime, timezone
from pathlib import Path


def _default_db_path() -> str:
    """Resolve the chDB data path (same logic as MemoryDB)."""
    env = os.environ.get("CLICKMEM_DB_PATH", "")
    if env and env != ":memory:":
        return env
    return str(Path.home() / ".openclaw" / "memory" / "chdb-data")


def main():
    parser = argparse.ArgumentParser(description="ClickMem CEO Brain data cleanup")
    parser.add_argument("--dry-run", action="store_true", help="Preview only")
    parser.add_argument("--db-path", default=None, help="chDB data path (auto-detected if omitted)")
    args = parser.parse_args()

    db_path = args.db_path or _default_db_path()
    print(f"Using DB path: {db_path}")

    from memory_core.ceo_db import CeoDB

    db = CeoDB(db_path)
    counts = db.count_all()
    print(f"=== Current State ===")
    for k, v in counts.items():
        print(f"  {k}: {v}")
    print()

    if counts["decisions"] == 0:
        print("ERROR: No data found. Is the chDB database accessible?")
        print("Make sure no other ClickMem process (MCP/server) is running.")
        sys.exit(1)

    # ──────────────────────────────────────────────────────────────────
    # Step 1: Identify projects
    # ──────────────────────────────────────────────────────────────────
    projects = db.list_projects()
    project_map = {p.name: p.id for p in projects}
    print("=== Projects ===")
    for p in projects:
        print(f"  {p.id[:12]} | {p.name:20} | {p.status[:40]}")
    print()

    clickmem_id = project_map.get("clickmem", "")
    chdb_sup_id = project_map.get("chdb-supervisor", "")
    downloads_id = project_map.get("Downloads", "")

    # ──────────────────────────────────────────────────────────────────
    # Step 2: Reassign misattributed decisions from clickmem
    # ──────────────────────────────────────────────────────────────────
    if clickmem_id:
        decisions = db.list_decisions(project_id=clickmem_id, limit=100)
        # Marketing/ops decisions that don't belong to clickmem
        misattributed_keywords = [
            "Budget Adjustment", "Campaign", "PT-based", "Zero Spend",
            "Bid Adjustment", "invalid config key", "crash-loop",
        ]
        reassigned = 0
        for d in decisions:
            is_misattributed = any(kw.lower() in d.title.lower() for kw in misattributed_keywords)
            if is_misattributed:
                print(f"  REASSIGN decision: {d.title[:60]} ({d.domain}) → global")
                if not args.dry_run:
                    db.update_decision(d.id, project_id="")
                reassigned += 1
        print(f"{'Would reassign' if args.dry_run else 'Reassigned'} {reassigned} decisions from clickmem → global")
        print()

    # ──────────────────────────────────────────────────────────────────
    # Step 3: Reassign Downloads decisions to chdb-supervisor
    # ──────────────────────────────────────────────────────────────────
    if downloads_id and chdb_sup_id:
        decisions = db.list_decisions(project_id=downloads_id, limit=100)
        chdb_keywords = [
            "ClickHouse", "SIGINT", "subprocess", "use()", "metadata access",
            "SQL generation", "remote table", "ORDER BY", "testing",
        ]
        reassigned = 0
        for d in decisions:
            is_chdb = any(kw.lower() in d.title.lower() or kw.lower() in d.choice.lower()
                          for kw in chdb_keywords)
            if is_chdb:
                target = "chdb-supervisor"
                target_id = chdb_sup_id
            else:
                target = "global"
                target_id = ""
            print(f"  REASSIGN decision: {d.title[:50]} ({d.domain}) → {target}")
            if not args.dry_run:
                db.update_decision(d.id, project_id=target_id)
            reassigned += 1
        print(f"{'Would reassign' if args.dry_run else 'Reassigned'} {reassigned} decisions from Downloads")
        print()

    # ──────────────────────────────────────────────────────────────────
    # Step 4: Reassign global chdb/pandas decisions to chdb-supervisor
    # ──────────────────────────────────────────────────────────────────
    if chdb_sup_id:
        decisions = db.list_decisions(project_id=None, limit=500)
        global_decisions = [d for d in decisions if not d.project_id]
        chdb_global_keywords = [
            "pyenv", "pandas 3", "dtype", "__del__", "SIGABRT",
            "arm64 CI", "None vs nan",
        ]
        reassigned = 0
        for d in global_decisions:
            is_chdb = any(kw.lower() in d.title.lower() for kw in chdb_global_keywords)
            if is_chdb:
                print(f"  REASSIGN decision: {d.title[:50]} → chdb-supervisor")
                if not args.dry_run:
                    db.update_decision(d.id, project_id=chdb_sup_id)
                reassigned += 1
        print(f"{'Would reassign' if args.dry_run else 'Reassigned'} {reassigned} global decisions → chdb-supervisor")
        print()

    # ──────────────────────────────────────────────────────────────────
    # Step 5: Prune weak principles
    # ──────────────────────────────────────────────────────────────────
    from memory_core.ceo_maintenance import CEOMaintenance
    pruned = CEOMaintenance.prune_weak_principles(db, min_age_days=14, dry_run=args.dry_run)
    print(f"{'Would prune' if args.dry_run else 'Pruned'} {len(pruned)} weak principles")
    if pruned[:5]:
        for p in pruned[:5]:
            print(f"  {p['content'][:60]} (conf={p['confidence']:.0%}, ev={p['evidence_count']}, age={p['age_days']}d)")
        if len(pruned) > 5:
            print(f"  ... and {len(pruned) - 5} more")
    print()

    # ──────────────────────────────────────────────────────────────────
    # Step 6: Update project metadata
    # ──────────────────────────────────────────────────────────────────
    updates = {
        "clickmem": {"status": "building", "description": "Unified memory system for AI coding agents with CEO Brain architecture"},
        "botsChat-macApp": {"status": "maintaining"},
        "Downloads": {"status": "inactive", "description": "Not a real project - auto-created from Downloads directory"},
    }
    for name, fields in updates.items():
        pid = project_map.get(name)
        if pid:
            print(f"  UPDATE project '{name}': {', '.join(f'{k}={v[:30]}' for k, v in fields.items())}")
            if not args.dry_run:
                db.update_project(pid, **fields)
    print()

    # ──────────────────────────────────────────────────────────────────
    # Summary
    # ──────────────────────────────────────────────────────────────────
    if args.dry_run:
        print("=== DRY RUN COMPLETE — no changes made ===")
        print("Run without --dry-run to execute cleanup.")
    else:
        final = db.count_all()
        print("=== CLEANUP COMPLETE ===")
        for k in final:
            print(f"  {k}: {counts[k]} → {final[k]}")


if __name__ == "__main__":
    main()
