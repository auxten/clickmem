#!/usr/bin/env bash
set -euo pipefail

# ── ClickMem — One-click deploy ──────────────────────────────────────
# Usage:
#   git clone https://github.com/auxten/clickmem && cd clickmem && ./setup.sh
# Or:
#   curl -fsSL https://raw.githubusercontent.com/auxten/clickmem/main/setup.sh | bash

INSTALL_DIR="${CLICKMEM_DIR:-$HOME/clickmem}"

# Detect curl-pipe mode: BASH_SOURCE is unset/empty when piped
if [ -z "${BASH_SOURCE[0]:-}" ] || [ "${BASH_SOURCE[0]}" = "bash" ]; then
    # Running via: curl ... | bash
    if [ -d "$INSTALL_DIR/.git" ]; then
        echo "▸ Updating existing clickmem at $INSTALL_DIR ..."
        git -C "$INSTALL_DIR" pull --ff-only || true
    else
        echo "▸ Cloning clickmem to $INSTALL_DIR ..."
        git clone https://github.com/auxten/clickmem "$INSTALL_DIR"
    fi
    cd "$INSTALL_DIR"
else
    # Running via: ./setup.sh (local clone)
    cd "$(dirname "${BASH_SOURCE[0]}")"
fi

SCRIPT_DIR="$(pwd)"

# ── 1. Environment checks ───────────────────────────────────────────

echo "▸ Checking environment..."

# Python >= 3.10 — try python3, then versioned binaries (python3.13 down to 3.10)
PYTHON=""
for candidate in python3 python3.13 python3.12 python3.11 python3.10; do
    if command -v "$candidate" &>/dev/null; then
        PY_VERSION=$("$candidate" -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')
        PY_MAJOR=$(echo "$PY_VERSION" | cut -d. -f1)
        PY_MINOR=$(echo "$PY_VERSION" | cut -d. -f2)
        if [ "$PY_MAJOR" -ge 3 ] && [ "$PY_MINOR" -ge 10 ]; then
            PYTHON="$candidate"
            break
        fi
    fi
done

if [ -z "$PYTHON" ]; then
    # As a last resort, let uv install Python automatically
    if command -v uv &>/dev/null; then
        echo "  No Python >= 3.10 found; letting uv install one..."
        uv python install 3.12
        PYTHON="python3.12"
        PY_VERSION="3.12"
    else
        echo "Error: Python >= 3.10 not found. Install Python 3.10+ or uv first."
        exit 1
    fi
fi
echo "  Python $PY_VERSION ($PYTHON)"

# uv
if ! command -v uv &>/dev/null; then
    echo "Error: uv not found. Install it first:"
    echo "  curl -LsSf https://astral.sh/uv/install.sh | sh"
    exit 1
fi
echo "  uv $(uv --version 2>/dev/null | head -1)"

# ── 2. Install dependencies ─────────────────────────────────────────

echo "▸ Installing dependencies..."
uv sync --python "$PYTHON" --extra dev

# ── 3. Install & start background service ────────────────────────────

echo "▸ Installing ClickMem service..."
uv run memory service install < /dev/null 2>&1 | sed 's/^/  /'

# ── 4. Smoke test (with retry — server may need a few seconds) ──────

echo "▸ Smoke test..."
for i in 1 2 3 4 5 6; do
    if uv run memory status --json < /dev/null >/dev/null 2>&1; then
        echo "  CLI works (via API server on port ${CLICKMEM_SERVER_PORT:-9527})."
        break
    fi
    if [ "$i" -eq 6 ]; then
        echo "Error: smoke test failed — server not responding after 30s."
        echo "  Check: uv run memory service logs"
        exit 1
    fi
    echo "  Waiting for server to start... (${i}/6)"
    sleep 5
done

# ── 5. Import OpenClaw history (if present) ──────────────────────────

if [ -d "$HOME/.openclaw" ]; then
    echo "▸ Importing OpenClaw history from ~/.openclaw ..."
    uv run memory import-openclaw --json < /dev/null || true
else
    echo "▸ No ~/.openclaw directory found, skipping history import."
fi

# ── 6. Install OpenClaw plugin ─────────────────────────────────────────

OPENCLAW_CONFIG="$HOME/.openclaw/openclaw.json"

if [ -f "$OPENCLAW_CONFIG" ]; then
    echo "▸ Installing OpenClaw plugin..."
    python3 -c "
import json
cfg_path = '$OPENCLAW_CONFIG'
plugin_dir = '$SCRIPT_DIR/clickmem-plugin'
with open(cfg_path) as f:
    cfg = json.load(f)
plugins = cfg.setdefault('plugins', {})
# Add plugin load path for discovery
load = plugins.setdefault('load', {})
paths = load.setdefault('paths', [])
if plugin_dir not in paths:
    paths.append(plugin_dir)
# Enable plugin entry
entries = plugins.setdefault('entries', {})
entries['clickmem'] = {'enabled': True}
# Set as memory slot
slots = plugins.setdefault('slots', {})
slots['memory'] = 'clickmem'
# Clean up old hook references if present
hooks = cfg.get('hooks', {}).get('internal', {})
hooks.get('entries', {}).pop('clickmem-hook', None)
hooks.get('installs', {}).pop('clickmem-hook', None)
hook_extra = hooks.get('load', {}).get('extraDirs', [])
if hook_extra:
    hooks['load']['extraDirs'] = [d for d in hook_extra if 'clickmem' not in d]
with open(cfg_path, 'w') as f:
    json.dump(cfg, f, indent=2)
print('  Plugin registered in', cfg_path)
" || echo "  Warning: failed to register plugin"
else
    echo "▸ No ~/.openclaw/openclaw.json found, skipping plugin installation."
fi

# ── 7. Install skill (slash command) ─────────────────────────────────

SKILL_SRC="$SCRIPT_DIR/skills/clickmem/SKILL.md"

# Claude Code: symlink into ~/.claude/commands/
CLAUDE_CMD_DIR="$HOME/.claude/commands"
if [ -d "$HOME/.claude" ]; then
    echo "▸ Installing Claude Code skill..."
    mkdir -p "$CLAUDE_CMD_DIR"
    CLAUDE_LINK="$CLAUDE_CMD_DIR/clickmem.md"
    if [ -L "$CLAUDE_LINK" ] || [ -f "$CLAUDE_LINK" ]; then
        rm "$CLAUDE_LINK"
    fi
    ln -s "$SKILL_SRC" "$CLAUDE_LINK"
    echo "  Skill linked: $CLAUDE_LINK → $SKILL_SRC"
else
    echo "▸ No ~/.claude directory found, skipping Claude Code skill installation."
fi

# ── 7.5. Install Claude Code hooks (HTTP hooks for auto recall/capture) ──

CLAUDE_SETTINGS="$HOME/.claude/settings.json"
if [ -d "$HOME/.claude" ]; then
    echo "▸ Installing Claude Code hooks..."
    python3 -c "
import json, os

settings_path = '$CLAUDE_SETTINGS'
port = os.environ.get('CLICKMEM_SERVER_PORT', '9527')
host = os.environ.get('CLICKMEM_SERVER_HOST', '127.0.0.1')
url = f'http://{host}:{port}/hooks/claude-code'

if os.path.exists(settings_path):
    with open(settings_path) as f:
        settings = json.load(f)
else:
    settings = {}

hooks = settings.setdefault('hooks', {})
hook_handler = {'type': 'http', 'url': url, 'timeout': 30}

for event in ['SessionStart', 'UserPromptSubmit', 'Stop', 'SessionEnd']:
    groups = hooks.get(event, [])
    # Remove old clickmem entries (by URL pattern)
    groups = [g for g in groups if not any(
        '/hooks/claude-code' in h.get('url', '')
        for h in g.get('hooks', [])
    )]
    groups.append({'hooks': [hook_handler]})
    hooks[event] = groups

with open(settings_path, 'w') as f:
    json.dump(settings, f, indent=2)
print('  Hooks registered in', settings_path)
print('  Events: SessionStart, UserPromptSubmit, Stop, SessionEnd')
print('  Endpoint:', url)
" || echo "  Warning: failed to register Claude Code hooks"
    echo "  Claude Code hooks enable auto-recall on session start and auto-capture after each response."
else
    echo "▸ No ~/.claude directory found, skipping Claude Code hooks installation."
fi

# OpenClaw: add skills/ to openclaw.json skills directories
if [ -f "$OPENCLAW_CONFIG" ]; then
    echo "▸ Registering skill with OpenClaw..."
    python3 -c "
import json
cfg_path = '$OPENCLAW_CONFIG'
skills_dir = '$SCRIPT_DIR/skills'
with open(cfg_path) as f:
    cfg = json.load(f)
skills = cfg.setdefault('skills', {})
extra = skills.setdefault('extraDirs', [])
if skills_dir not in extra:
    extra.append(skills_dir)
with open(cfg_path, 'w') as f:
    json.dump(cfg, f, indent=2)
print('  Skill directory registered in', cfg_path)
" || echo "  Warning: failed to register skill directory"
fi

# ── 8. Install Cursor hooks (user-level, works for all projects) ───────

echo "▸ Installing Cursor hooks (user-level)..."
CURSOR_USER_DIR="$HOME/.cursor"
CURSOR_HOOKS_SRC="$SCRIPT_DIR/cursor-hooks"
CURSOR_HOOKS_DST="$CURSOR_USER_DIR/hooks/clickmem"
CURSOR_HOOKS_JSON="$CURSOR_USER_DIR/hooks.json"

if [ -d "$CURSOR_HOOKS_SRC" ]; then
    mkdir -p "$CURSOR_USER_DIR/hooks"

    # Symlink the hooks implementation
    if [ -L "$CURSOR_HOOKS_DST" ] || [ -d "$CURSOR_HOOKS_DST" ]; then
        rm -rf "$CURSOR_HOOKS_DST"
    fi
    ln -s "$CURSOR_HOOKS_SRC" "$CURSOR_HOOKS_DST"
    echo "  Linked: $CURSOR_HOOKS_DST → $CURSOR_HOOKS_SRC"

    # Generate/merge hooks.json with absolute paths
    HOOK_CMD="node $CURSOR_HOOKS_DST/hook-handler.js"
    python3 -c "
import json, os
hooks_json = '$CURSOR_HOOKS_JSON'
cmd = '$HOOK_CMD'
hook_entry = [{'command': cmd}]
hook_events = [
    'sessionStart', 'sessionEnd',
    'beforeSubmitPrompt', 'afterAgentResponse', 'afterAgentThought',
    'beforeShellExecution', 'afterShellExecution',
    'beforeMCPExecution', 'afterMCPExecution',
    'beforeReadFile', 'afterFileEdit',
    'stop', 'beforeTabFileRead', 'afterTabFileEdit',
]
if os.path.exists(hooks_json):
    with open(hooks_json) as f:
        cfg = json.load(f)
else:
    cfg = {'version': 1, 'hooks': {}}
for event in hook_events:
    existing = cfg['hooks'].get(event, [])
    # Remove old clickmem entries
    existing = [e for e in existing if 'clickmem' not in e.get('command', '')]
    existing.extend(hook_entry)
    cfg['hooks'][event] = existing
with open(hooks_json, 'w') as f:
    json.dump(cfg, f, indent=2)
print('  hooks.json updated:', hooks_json)
"
    echo "  Cursor hooks active globally for all projects."
else
    echo "  Warning: cursor-hooks/ directory not found — skipping."
fi

# ── 9. Auto-import agent history (first install only) ────────────────

STATE_FILE="$HOME/.clickmem/import-state.json"
if [ ! -f "$STATE_FILE" ]; then
    echo ""
    echo "▸ First install detected — importing agent conversation history..."
    echo "  This runs in the background. Use 'memory status' to check progress."
    uv run memory import --agent all < /dev/null 2>&1 || true
else
    echo ""
    echo "▸ Import state exists, skipping auto-import. Run 'memory import' to update."
fi

# ── 10. Done ──────────────────────────────────────────────────────────

echo ""
echo "═══════════════════════════════════════════"
echo " ClickMem deployed successfully!"
echo "═══════════════════════════════════════════"
echo ""
echo " Usage:"
echo "   memory status              # Show memory stats + import progress"
echo "   memory discover            # Detect installed agents"
echo "   memory import              # Import agent history"
echo "   memory recall \"query\"      # Semantic search"
echo "   memory help                # Show all commands"
echo ""
echo " Service:"
echo "   memory service status      # Check background service"
echo "   memory service logs -f     # Follow server logs"
echo ""
echo " Or use the full path:"
echo "   $SCRIPT_DIR/.venv/bin/memory status"
echo ""
