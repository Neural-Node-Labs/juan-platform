FROM python:3.11-slim AS base

# ── System deps ───────────────────────────────────────────────────────────────
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl ca-certificates sqlite3 nodejs npm \
    && rm -rf /var/lib/apt/lists/*

# ── Non-root user ─────────────────────────────────────────────────────────────
RUN groupadd -r juan && useradd -r -g juan -u 1000 -m -d /home/juan juan

WORKDIR /app

# ── Python deps ───────────────────────────────────────────────────────────────
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# ── React UI build stage ──────────────────────────────────────────────────────
FROM base AS react-builder
ARG VITE_APP_ENV=production
ENV VITE_APP_ENV=$VITE_APP_ENV
COPY ui-react/package.json ui-react/package-lock.json* ./ui-react/
RUN cd ui-react && npm install --silent
COPY ui-react/ ./ui-react/
RUN cd ui-react && npm run build

# ── Final image ───────────────────────────────────────────────────────────────
FROM base AS final

WORKDIR /app

# Copy Python application
COPY --chown=juan:juan . .

# Copy built React app
COPY --from=react-builder --chown=juan:juan /app/ui-react/dist ./ui-react/dist

# Directories
RUN mkdir -p /data/workspace /config /app/plugins \
    && chown -R juan:juan /data /config /app /app/plugins

VOLUME ["/data", "/config"]

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    JUAN_DB_PATH=/data/juan_state.db \
    JUAN_WORKSPACE=/data/workspace \
    JUAN_MODEL=claude-sonnet-4-20250514 \
    JUAN_PROVIDER=anthropic \
    JUAN_PLATFORM=telegram \
    DASHBOARD_PORT=5000 \
    UI_WS_PORT=5001 \
    UI_HTTP_PORT=5002 \
    MAX_ITERATIONS=90 \
    LOG_LEVEL=INFO

EXPOSE 5000 5001 5002

USER juan

ENTRYPOINT ["/app/entrypoint.sh"]
