# ClickMem Hook for OpenClaw

Three-layer, self-maintaining memory for OpenClaw agents.

## Events

- `agent:bootstrap` — Export memory context into workspace on session start
- `command:new` — Export fresh context when a new command session begins
- `command:reset` — Re-export context after session reset

## Metadata

- **name**: clickmem
- **version**: 0.1.0
- **description**: Local semantic memory powered by chDB + Qwen3-0.6B embedding
- **author**: auxten
- **repository**: https://github.com/auxten/clickmem
