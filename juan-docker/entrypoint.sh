#!/bin/bash
set -e

echo "╔══════════════════════════════════════════╗"
echo "║        JUAN AGENT PLATFORM v1.0          ║"
echo "╚══════════════════════════════════════════╝"

# ── Config resolution ─────────────────────────────────────────────────────────
for src in /config/SOUL.md /app/config/SOUL.md; do
    [ -f "$src" ] && cp "$src" /app/SOUL.md && echo "[entrypoint] SOUL.md  → $src" && break
done

if [ -f "/config/juan_auth.json" ]; then
    cp /config/juan_auth.json /app/juan_auth.json
    echo "[entrypoint] auth    → /config/juan_auth.json"
elif [ -f "/app/config/juan_auth.json" ]; then
    cp /app/config/juan_auth.json /app/juan_auth.json
else
    echo '{"allow_all":true}' > /app/juan_auth.json
    echo "[entrypoint] auth    → open access (dev mode)"
fi

mkdir -p /data
touch /data/.healthcheck

# ── UI Mode ───────────────────────────────────────────────────────────────────
# Valid values: react | python | both  (default: both)
UI_MODE="${UI_MODE:-both}"
REACT_DIST="/app/ui-react/dist/index.html"

# Validate and resolve
case "$UI_MODE" in
    react)
        if [ ! -f "$REACT_DIST" ]; then
            echo "[entrypoint] WARNING: UI_MODE=react but React build not found."
            echo "             Falling back to UI_MODE=python."
            UI_MODE="python"
        fi
        ;;
    python|both) ;;
    *)
        echo "[entrypoint] WARNING: Unknown UI_MODE='$UI_MODE'. Using 'both'."
        UI_MODE="both"
        ;;
esac

echo ""
echo "[entrypoint] UI_MODE:   $UI_MODE"
echo "[entrypoint] Model:     ${JUAN_MODEL:-claude-sonnet-4-20250514}"
echo "[entrypoint] Provider:  ${JUAN_PROVIDER:-anthropic}"
echo "[entrypoint] Platform:  ${JUAN_PLATFORM:-telegram}"
echo ""

# Export so Python picks it up
export UI_MODE

# ── Port layout ───────────────────────────────────────────────────────────────
case "$UI_MODE" in
    react)
        echo "[entrypoint] Port 5000 → React UI  (/ = React SPA)"
        echo "[entrypoint]              /ops       = Python dashboard"
        ;;
    python)
        echo "[entrypoint] Port 5000 → Python UI (/ = Python dashboard)"
        echo "[entrypoint]              /chat      = React SPA (if built)"
        ;;
    both)
        echo "[entrypoint] Port 5000 → Both UIs"
        echo "[entrypoint]              /          = Python dashboard"
        echo "[entrypoint]              /chat      = React SPA"
        ;;
esac
echo "[entrypoint] Port 5001 → WebSocket  (React + Kivy)"
echo "[entrypoint] Port 5002 → HTTP/SSE   (Kivy + /ui/auth)"
echo ""

PIDS=()
cleanup() {
    echo "[entrypoint] Shutting down..."
    for pid in "${PIDS[@]}"; do kill "$pid" 2>/dev/null || true; done
    wait
}
trap cleanup SIGTERM SIGINT EXIT

# 1. Dashboard / React server (always — handles both depending on UI_MODE)
python /app/dashboard/app.py &
PIDS+=($!)
echo "[entrypoint] Dashboard/UI server started (pid $!)"

# 2. UI Gateway — WebSocket + HTTP bridge (always)
python /app/main.py --mode ui-gateway &
PIDS+=($!)
echo "[entrypoint] UI Gateway started        (pid $!)"

# 3. Platform gateway — only if bot token provided
if [ -n "${JUAN_BOT_TOKEN}" ]; then
    python /app/main.py --mode gateway &
    PIDS+=($!)
    echo "[entrypoint] Platform gateway started  (${JUAN_PLATFORM:-telegram}, pid $!)"
else
    echo "[entrypoint] No JUAN_BOT_TOKEN         platform gateway disabled"
fi

echo ""
echo "[entrypoint] All services running."
wait -n 2>/dev/null || wait
