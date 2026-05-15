# Sample AGENTS.md — audit fixture

## Learned Principles

- Always wrap chDB calls in `asyncio.to_thread()` inside the async server
- Prefer event-driven hooks over periodic cron
- Memory garbage compounds; require explicit commits, never auto-extract from transcripts
- Single port 9527 serves REST, MCP SSE, and the dashboard

## Project facts

- The dashboard SPA is bundled into the wheel via `force-include`
- chDB is the default backend; ClickHouse Cloud is the cluster mode

## Edge-case bullets (importer should still pick these up)

- a one-liner with no period
- A bullet ending without trailing punctuation just to be ornery
