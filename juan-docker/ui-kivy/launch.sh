#!/usr/bin/env bash
set -euo pipefail

echo "╔══════════════════════════════════════╗"
echo "║       JUAN Desktop Client            ║"
echo "╚══════════════════════════════════════╝"
echo ""

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV_DIR="$SCRIPT_DIR/.venv"

# ── Check Python 3.11+ ────────────────────────────────────────────────────────
PYTHON=""
for cmd in python3.12 python3.11 python3 python; do
    if command -v "$cmd" &>/dev/null; then
        VER=$("$cmd" -c "import sys; print(sys.version_info >= (3,11))" 2>/dev/null || echo "False")
        if [ "$VER" = "True" ]; then
            PYTHON="$cmd"
            break
        fi
    fi
done

if [ -z "$PYTHON" ]; then
    echo "[ERROR] Python 3.11+ not found."
    echo "        Install from https://python.org or via your package manager."
    exit 1
fi
echo "[OK] Using $("$PYTHON" --version)"

# ── macOS: check SDL2 dependency for Kivy ────────────────────────────────────
if [[ "$OSTYPE" == "darwin"* ]]; then
    if ! command -v brew &>/dev/null; then
        echo "[WARN] Homebrew not found. Some Kivy features may not work."
        echo "       Install from https://brew.sh if you encounter issues."
    fi
fi

# ── Virtual environment ───────────────────────────────────────────────────────
if [ ! -f "$VENV_DIR/bin/activate" ]; then
    echo "[SETUP] Creating virtual environment..."
    "$PYTHON" -m venv "$VENV_DIR"
fi

# shellcheck source=/dev/null
source "$VENV_DIR/bin/activate"
echo "[OK] Virtual environment activated"

# ── Install / update dependencies ────────────────────────────────────────────
if ! python -c "import kivy" &>/dev/null; then
    echo "[SETUP] Installing dependencies (first run — may take a minute)..."
    pip install --upgrade pip --quiet
    pip install -r "$SCRIPT_DIR/requirements.txt" --quiet
    echo "[OK] Dependencies installed"
else
    echo "[OK] Dependencies already installed"
fi

# ── Config overrides ──────────────────────────────────────────────────────────
[ -n "${JUAN_WS_URL:-}" ]   && echo "[CONFIG] WS URL:   $JUAN_WS_URL"
[ -n "${JUAN_HTTP_URL:-}" ] && echo "[CONFIG] HTTP URL: $JUAN_HTTP_URL"
[ -n "${JUAN_PASSWORD:-}" ] && echo "[CONFIG] Password: (set)"

# ── Launch ────────────────────────────────────────────────────────────────────
echo ""
echo "[LAUNCH] Starting Juan Desktop..."
echo "         Close the window or press Ctrl+C to exit."
echo ""

cd "$SCRIPT_DIR"
exec python main.py "$@"
