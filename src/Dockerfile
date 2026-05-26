FROM python:3.11-slim

# ── System deps ───────────────────────────────────────────────────────────────
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl ca-certificates sqlite3 \
    && rm -rf /var/lib/apt/lists/*

# ── Non-root user ─────────────────────────────────────────────────────────────
RUN groupadd -r juan && useradd -r -g juan -u 1000 -m -d /home/juan juan

# ── Python deps ───────────────────────────────────────────────────────────────
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# ── Application code ──────────────────────────────────────────────────────────
COPY --chown=juan:juan . .

# ── Data & config directories ─────────────────────────────────────────────────
RUN mkdir -p /data /config /app/plugins \
    && chown -R juan:juan /data /config /app/plugins

# ── Volumes ───────────────────────────────────────────────────────────────────
VOLUME ["/data", "/config"]

# ── Environment defaults ──────────────────────────────────────────────────────
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    JUAN_DB_PATH=/data/juan_state.db \
    JUAN_MODEL=claude-sonnet-4-20250514 \
    JUAN_PROVIDER=anthropic \
    JUAN_PLATFORM=telegram \
    DASHBOARD_PORT=5000 \
    MAX_ITERATIONS=90 \
    LOG_LEVEL=INFO

EXPOSE 5000

USER juan

ENTRYPOINT ["/app/entrypoint.sh"]
