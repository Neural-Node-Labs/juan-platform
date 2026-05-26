#!/bin/bash
set -e

echo "╔══════════════════════════════════════════╗"
echo "║        JUAN AGENT PLATFORM v1.0          ║"
echo "╚══════════════════════════════════════════╝"

# Resolve config file paths (prefer /config volume, fall back to ./config)
if [ -f "/config/SOUL.md" ]; then
    cp /config/SOUL.md /app/SOUL.md
    echo "[entrypoint] Loaded SOUL.md from /config"
elif [ -f "/app/config/SOUL.md" ]; then
    cp /app/config/SOUL.md /app/SOUL.md
    echo "[entrypoint] Loaded SOUL.md from ./config"
else
    echo "[entrypoint] No SOUL.md found — using fallback persona"
fi

if [ -f "/config/juan_auth.json" ]; then
    cp /config/juan_auth.json /app/juan_auth.json
    echo "[entrypoint] Loaded juan_auth.json from /config"
elif [ -f "/app/config/juan_auth.json" ]; then
    cp /app/config/juan_auth.json /app/juan_auth.json
    echo "[entrypoint] Loaded juan_auth.json from ./config"
else
    echo "[entrypoint] No juan_auth.json — creating open-access default"
    echo '{"allow_all":true}' > /app/juan_auth.json
fi

# Ensure data dir is writable
mkdir -p /data
touch /data/.healthcheck

echo "[entrypoint] Database path: ${JUAN_DB_PATH:-/data/juan_state.db}"
echo "[entrypoint] Provider:      ${JUAN_PROVIDER:-anthropic}"
echo "[entrypoint] Model:         ${JUAN_MODEL:-claude-sonnet-4-20250514}"
echo "[entrypoint] Platform:      ${JUAN_PLATFORM:-telegram}"
echo "[entrypoint] Dashboard:     http://0.0.0.0:${DASHBOARD_PORT:-5000}"
echo ""

# ── Start processes ───────────────────────────────────────────────────────────

# PID tracking
PIDS=()

cleanup() {
    echo "[entrypoint] Shutting down…"
    for pid in "${PIDS[@]}"; do
        kill "$pid" 2>/dev/null || true
    done
    wait
    echo "[entrypoint] Stopped."
}
trap cleanup SIGTERM SIGINT EXIT

# 1. Dashboard (always)
echo "[entrypoint] Starting dashboard on :${DASHBOARD_PORT:-5000}"
python /app/dashboard/app.py &
PIDS+=($!)

# 2. Gateway (only if bot token provided)
if [ -n "${JUAN_BOT_TOKEN}" ]; then
    echo "[entrypoint] Starting gateway (${JUAN_PLATFORM:-telegram})"
    python /app/main.py --mode gateway &
    PIDS+=($!)
else
    echo "[entrypoint] No JUAN_BOT_TOKEN set — gateway disabled"
    echo "[entrypoint] Use the dashboard at http://localhost:${DASHBOARD_PORT:-5000} to submit tasks"
fi

# Wait for any child to exit
wait -n 2>/dev/null || wait
