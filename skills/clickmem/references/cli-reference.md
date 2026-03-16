# ClickMem CLI Reference

## Global Options

| Option | Env Var | Description |
|--------|---------|-------------|
| `--remote URL` | `CLICKMEM_REMOTE` | Remote server URL (or `"auto"` for mDNS) |
| `--api-key KEY` | `CLICKMEM_API_KEY` | API key for remote auth |
| `--local` | — | Use embedded DB directly (no server) |

---

## help

```
memory help [SUBCMD]
```

Show help for all commands, or detailed help for a specific subcommand.

---

## discover

```
memory discover [--json]
```

Detect installed AI agents, their conversation history, and hook status.

---

## hooks install

```
memory hooks install [--agent claude-code|cursor|openclaw|all] [--server-url URL]
```

Install hooks for AI agents.

## hooks status

```
memory hooks status
```

Show which agents have hooks installed.

---

## import

```
memory import [OPTIONS]
```

| Option | Default | Description |
|--------|---------|-------------|
| `--agent` | `all` | Agent: `claude-code`, `cursor`, `openclaw`, `all` |
| `--foreground` | `False` | Run synchronously (default: async background) |
| `--remote URL` | — | Destination server URL |
| `--path DIR` | — | Scan a directory for CLAUDE.md/AGENTS.md |

---

## status

```
memory status [--json]
```

Shows: legacy memory counts, CEO Brain entities, import progress, LLM config.

---

## recall

```
memory recall <query> [OPTIONS]
```

| Option | Default | Description |
|--------|---------|-------------|
| `--top-k` | `10` | Max results |
| `--min-score` | `0.0` | Minimum relevance score |
| `--layer` | — | Filter: `episodic`, `semantic` |
| `--category` | — | Filter: `preference`, `decision`, etc. |
| `--json` | `False` | JSON output |

---

## remember

```
memory remember <content> [OPTIONS]
```

| Option | Default | Description |
|--------|---------|-------------|
| `--layer` | `semantic` | `working`, `episodic`, `semantic` |
| `--category` | `knowledge` | `preference`, `decision`, `knowledge`, `person`, `project`, `insight` |
| `--tags` | — | Comma-separated tags |
| `--json` | `False` | JSON output |

---

## forget

```
memory forget <id_or_query> [--json]
```

Delete by UUID, prefix, or semantic search match.

---

## maintain

```
memory maintain [--dry-run] [--json]
```

Run maintenance: stale cleanup, CEO principle dedup, episode expiry, decision outcome validation.

---

## portfolio

```
memory portfolio [--json]
```

All projects overview with decision/episode counts.

## brief

```
memory brief [--project-id ID] [--query TEXT] [--json]
```

Detailed project briefing with principles, decisions, recent activity.

## projects / decisions / principles

```
memory projects [--status STATUS] [--json]
memory decisions [--project-id ID] [--limit N] [--json]
memory principles [--project-id ID] [--json]
```

---

## serve

```
memory serve [--host HOST] [--port PORT] [--debug] [--no-mcp]
```

Start REST API + MCP SSE server.

## mcp

```
memory mcp [--transport stdio|sse]
```

Start MCP server for Claude Code / Cursor.

## service

```
memory service install|uninstall|start|stop|status|logs
```
