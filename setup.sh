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

# ── 3. Smoke test ────────────────────────────────────────────────────

echo "▸ Smoke test..."
if uv run memory status --json < /dev/null >/dev/null 2>&1; then
    echo "  CLI works."
else
    echo "Error: smoke test failed — 'memory status' returned non-zero."
    exit 1
fi

# ── 4. Import OpenClaw history (if present) ──────────────────────────

if [ -d "$HOME/.openclaw" ]; then
    echo "▸ Importing OpenClaw history from ~/.openclaw ..."
    uv run memory import-openclaw --json < /dev/null || true
else
    echo "▸ No ~/.openclaw directory found, skipping history import."
fi

# ── 5. Install OpenClaw hook ──────────────────────────────────────────

OPENCLAW_CONFIG="$HOME/.openclaw/openclaw.json"
if [ -f "$OPENCLAW_CONFIG" ]; then
    echo "▸ Installing OpenClaw hook..."
    # OpenClaw's loadHooksFromDir scans subdirectories of extraDirs entries,
    # so we add the project root (which contains clickmem-hook/ subdirectory).
    python3 -c "
import json, sys
cfg_path = '$OPENCLAW_CONFIG'
project_dir = '$SCRIPT_DIR'
with open(cfg_path) as f:
    cfg = json.load(f)
hooks = cfg.setdefault('hooks', {}).setdefault('internal', {})
hooks['enabled'] = True
load = hooks.setdefault('load', {})
extra = load.setdefault('extraDirs', [])
if project_dir not in extra:
    extra.append(project_dir)
entries = hooks.setdefault('entries', {})
entries.setdefault('clickmem-hook', {'enabled': True})
with open(cfg_path, 'w') as f:
    json.dump(cfg, f, indent=2)
print('  Hook registered in', cfg_path)
" || echo "  Warning: failed to register hook"
else
    echo "▸ No ~/.openclaw/openclaw.json found, skipping hook installation."
fi

# ── 6. Install skill (slash command) ─────────────────────────────────

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

# ── 7. Done ──────────────────────────────────────────────────────────

echo ""
echo "═══════════════════════════════════════════"
echo " ClickMem deployed successfully!"
echo "═══════════════════════════════════════════"
echo ""
echo " Usage:"
echo "   memory status              # Show memory statistics"
echo "   memory remember \"...\"      # Store a memory"
echo "   memory recall \"query\"      # Semantic search"
echo "   memory review              # Browse memories"
echo ""
echo " Skill: /clickmem available in Claude Code"
echo ""
echo " Or use the full path:"
echo "   $SCRIPT_DIR/.venv/bin/memory status"
echo ""
