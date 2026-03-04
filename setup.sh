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

# Python >= 3.10
if ! command -v python3 &>/dev/null; then
    echo "Error: python3 not found. Install Python 3.10+ first."
    exit 1
fi

PY_VERSION=$(python3 -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')
PY_MAJOR=$(echo "$PY_VERSION" | cut -d. -f1)
PY_MINOR=$(echo "$PY_VERSION" | cut -d. -f2)

if [ "$PY_MAJOR" -lt 3 ] || { [ "$PY_MAJOR" -eq 3 ] && [ "$PY_MINOR" -lt 10 ]; }; then
    echo "Error: Python >= 3.10 required (found $PY_VERSION)"
    exit 1
fi
echo "  Python $PY_VERSION"

# uv
if ! command -v uv &>/dev/null; then
    echo "Error: uv not found. Install it first:"
    echo "  curl -LsSf https://astral.sh/uv/install.sh | sh"
    exit 1
fi
echo "  uv $(uv --version 2>/dev/null | head -1)"

# ── 2. Install dependencies ─────────────────────────────────────────

echo "▸ Installing dependencies..."
uv sync --python python3 --extra dev

# ── 3. Quick test (skip semantic tests to save time) ─────────────────

echo "▸ Running quick tests..."
if uv run pytest tests/ -m "not semantic" -q; then
    echo "  All tests passed."
else
    echo "Warning: Some tests failed. Check output above."
fi

# ── 4. Import OpenClaw history (if present) ──────────────────────────

if [ -d "$HOME/.openclaw" ]; then
    echo "▸ Importing OpenClaw history from ~/.openclaw ..."
    uv run memory import-openclaw --json || true
else
    echo "▸ No ~/.openclaw directory found, skipping history import."
fi

# ── 5. Install OpenClaw hook (if openclaw is installed) ──────────────

if command -v openclaw &>/dev/null; then
    echo "▸ Installing OpenClaw hook..."
    openclaw hooks install "$SCRIPT_DIR/clickmem-hook" --link || true
else
    echo "▸ openclaw CLI not found, skipping hook installation."
fi

# ── 6. Done ──────────────────────────────────────────────────────────

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
echo " Or use the full path:"
echo "   $SCRIPT_DIR/.venv/bin/memory status"
echo ""
