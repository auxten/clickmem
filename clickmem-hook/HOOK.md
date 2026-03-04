---
name: clickmem-hook
description: "Local semantic memory powered by chDB + Qwen3-0.6B embedding"
homepage: https://github.com/auxten/clickmem
metadata:
  {
    "openclaw":
      {
        "emoji": "🧠",
        "events": ["agent:bootstrap", "command:new", "command:reset"],
        "install": [{ "id": "path", "kind": "path", "label": "Linked from clickmem" }],
      },
  }
---

# ClickMem Hook

Three-layer, self-maintaining memory for OpenClaw agents.

## What It Does

On `agent:bootstrap`, `command:new`, or `command:reset`:

1. Exports L2 semantic memories to `<workspace>/MEMORY.md`
2. Exports today's L1 episodic memories to `<workspace>/memory/YYYY-MM-DD.md`

This gives the agent full context of your long-term knowledge and recent events at session start.

## Requirements

- clickmem installed (`~/clickmem/.venv/bin/memory` must exist)
