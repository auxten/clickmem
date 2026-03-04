# ClickMem CLI Reference

Complete parameter reference for the `memory` command.

## Environment

| Variable | Default | Description |
|----------|---------|-------------|
| `CLICKMEM_DB_PATH` | `~/.openclaw/memory/chdb-data` | Database storage path |

---

## remember

Store a memory.

```
memory remember <content> [OPTIONS]
```

| Argument/Option | Type | Default | Description |
|-----------------|------|---------|-------------|
| `content` | STRING | **required** | Memory content to store |
| `--layer` | STRING | `semantic` | Memory layer: `working`, `episodic`, `semantic` |
| `--category` | STRING | `knowledge` | Category: `preference`, `decision`, `knowledge`, `person`, `project`, `workflow`, `insight`, `context` |
| `--tags` | STRING | `None` | Comma-separated tags |
| `--json` | BOOL | `False` | Output as JSON |

**JSON output**:
```json
{"id": "abc12345", "layer": "semantic", "category": "knowledge", "status": "stored"}
```

---

## recall

Semantic search for memories.

```
memory recall <query> [OPTIONS]
```

| Argument/Option | Type | Default | Description |
|-----------------|------|---------|-------------|
| `query` | STRING | **required** | Search query |
| `--layer` | STRING | `None` | Filter by layer |
| `--category` | STRING | `None` | Filter by category |
| `--top-k`, `-k` | INT | `10` | Max results |
| `--min-score` | FLOAT | `0.0` | Minimum relevance score |
| `--json` | BOOL | `False` | Output as JSON |

**JSON output**:
```json
[{"id": "...", "content": "...", "layer": "...", "category": "...", "final_score": 0.85}]
```

---

## forget

Delete a memory by ID or ID prefix.

```
memory forget <memory_id> [OPTIONS]
```

| Argument/Option | Type | Default | Description |
|-----------------|------|---------|-------------|
| `memory_id` | STRING | **required** | Memory ID or prefix |
| `--json` | BOOL | `False` | Output as JSON |

**JSON output**:
```json
{"id": "abc12345", "status": "deleted"}
```

---

## status

Show per-layer statistics.

```
memory status [OPTIONS]
```

| Option | Type | Default | Description |
|--------|------|---------|-------------|
| `--json` | BOOL | `False` | Output as JSON |

**JSON output**:
```json
{"counts": {"working": 0, "episodic": 5, "semantic": 42}, "total": 47}
```

---

## review

Browse memories by layer.

```
memory review [OPTIONS]
```

| Option | Type | Default | Description |
|--------|------|---------|-------------|
| `--layer` | STRING | `semantic` | Layer to review |
| `--limit` | INT | `100` | Max entries |

Output: Rich table with ID, Category, Content, Date.

---

## sql

Execute a raw SQL query against the memory database.

```
memory sql <query> [OPTIONS]
```

| Argument/Option | Type | Default | Description |
|-----------------|------|---------|-------------|
| `query` | STRING | **required** | SQL query |
| `--json` | BOOL | `False` | Output as JSON |

---

## maintain

Run maintenance tasks (clean stale, purge deleted, compress, promote).

```
memory maintain [OPTIONS]
```

| Option | Type | Default | Description |
|--------|------|---------|-------------|
| `--dry-run` | BOOL | `False` | Preview without modifying |
| `--json` | BOOL | `False` | Output as JSON |

**JSON output (dry-run)**:
```json
{"dry_run": true, "would_clean_stale": 3, "would_purge_deleted": 1, "promotion_candidates": {}}
```

---

## export-context

Export memories to workspace .md files or JSON.

```
memory export-context [<workspace_path>] [OPTIONS]
```

| Argument/Option | Type | Default | Description |
|-----------------|------|---------|-------------|
| `workspace_path` | PATH | `""` | Workspace directory (omit for `--content` mode) |
| `--json` | BOOL | `False` | Output as JSON |
| `--content` | BOOL | `False` | Return markdown as JSON instead of writing files |
| `--max-items`, `-n` | INT | `50` | Max entries per section |
| `--max-chars`, `-c` | INT | `8000` | Max chars per section |

---

## import-openclaw

Import memory history from OpenClaw data directory.

```
memory import-openclaw [OPTIONS]
```

| Option | Type | Default | Description |
|--------|------|---------|-------------|
| `--openclaw-dir` | PATH | `~/.openclaw` | OpenClaw data directory |
| `--json` | BOOL | `False` | Output as JSON |

---

## uninstall

Uninstall clickmem and optionally export memories.

```
memory uninstall [OPTIONS]
```

| Option | Type | Default | Description |
|--------|------|---------|-------------|
| `--export` | BOOL | `False` | Export memories to OpenClaw .md before removing |
| `--openclaw-dir` | PATH | `~/.openclaw` | OpenClaw data directory |
| `-y`, `--yes` | BOOL | `False` | Skip confirmation |
| `--json` | BOOL | `False` | Output as JSON |
