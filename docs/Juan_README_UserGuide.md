# Juan Platform — User Guide

**Document ID:** UG-001  
**Version:** 1.0  
**Date:** 25 May 2026

---

## Table of Contents

1. [Overview](#1-overview)
2. [Prerequisites](#2-prerequisites)
3. [Quick Start (Docker)](#3-quick-start-docker)
4. [Environment Variables](#4-environment-variables)
5. [Configuration Files](#5-configuration-files)
6. [Connecting Telegram](#6-connecting-telegram)
7. [Dashboard Reference](#7-dashboard-reference)
8. [Dashboard API Reference](#8-dashboard-api-reference)
9. [Adding Custom Tools](#9-adding-custom-tools)
10. [Writing Plugins](#10-writing-plugins)
11. [Cron Jobs](#11-cron-jobs)
12. [Monitoring Logs](#12-monitoring-logs)
13. [Production Hardening](#13-production-hardening)
14. [Updating](#14-updating)
15. [Troubleshooting](#15-troubleshooting)

---

## 1. Overview

Project Juan is a self-hosted autonomous AI agent platform. Once deployed, it listens on messaging platforms (Telegram, Discord, Slack, and more), receives tasks, executes them using real tools, and responds — all autonomously. A web dashboard provides real-time monitoring, direct task submission, and scheduled job management.

| Capability | Detail |
|------------|--------|
| **Autonomous agent loop** | Up to 90 LLM iterations per turn with tool execution and memory |
| **LLM providers** | 18+ providers: Anthropic, OpenAI, Groq, Mistral, Cohere, DeepSeek, xAI, Gemini, Ollama, and more |
| **Messaging platforms** | 14+ platforms via typed adaptors: Telegram implemented; Discord, Slack, WhatsApp extensible |
| **Persistent memory** | `MEMORY.md` and `USER.md` updated across conversations; searchable session history |
| **Dashboard** | Web UI at `:5000` — overview, live feed, task submission, sessions, cron jobs, settings |
| **Scheduling** | Cron jobs with flexible schedules; isolated sessions; delivery to any platform |
| **IDE integration** | ACP (Agent Communication Protocol) via stdio JSON-RPC for VS Code, Zed, JetBrains |
| **Security** | 5-layer auth; human-in-the-loop approval for dangerous tools; default-deny everywhere |

---

## 2. Prerequisites

| Requirement | Version | Notes |
|-------------|---------|-------|
| Docker Engine | 24+ | Required for Docker deployment |
| Docker Compose | v2+ | Included with Docker Desktop |
| Anthropic API Key | Any | Or substitute any supported provider key |
| Python | 3.11+ | Only required for non-Docker local development |
| RAM | 512 MB min | 1 GB+ recommended for production |
| Disk | 1 GB min | For Docker image + data volume |

### Verify Docker

```bash
docker --version        # Docker version 24+
docker compose version  # Docker Compose version v2+
```

---

## 3. Quick Start (Docker)

### Step 1 — Unzip the project

```bash
unzip juan_platform_docker.zip
cd juan-docker
```

### Step 2 — Create configuration

```bash
cp .env.example .env
# Open .env and set ANTHROPIC_API_KEY=sk-ant-api03-...
```

### Step 3 — Start the stack

```bash
docker compose up -d

# Verify it started:
docker compose ps
docker compose logs -f juan
```

### Step 4 — Open the dashboard

Navigate to **http://localhost:5000** in your browser. You will see the Overview panel with system status. Submit a task from the Tasks panel to verify the agent is responding.

---

## 4. Environment Variables

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `ANTHROPIC_API_KEY` | ✓ | — | Anthropic API key (`sk-ant-...`) |
| `OPENAI_API_KEY` | | — | OpenAI key for fallback or direct use |
| `GROQ_API_KEY` | | — | Groq key for fast inference |
| `MISTRAL_API_KEY` | | — | Mistral key |
| `JUAN_MODEL` | | `claude-sonnet-4-20250514` | Default LLM model string |
| `JUAN_PROVIDER` | | `anthropic` | Default LLM provider name |
| `JUAN_BOT_TOKEN` | | — | Platform bot token (Telegram, Discord, etc.) |
| `JUAN_PLATFORM` | | `telegram` | Active messaging platform |
| `MAX_ITERATIONS` | | `90` | Agent loop iteration cap per turn |
| `DASHBOARD_PORT` | | `5000` | Dashboard HTTP port |
| `DASHBOARD_SECRET` | | `changeme` | Flask session secret — **change in production** |
| `DASHBOARD_PASSWORD` | | — | Optional dashboard password |
| `LOG_LEVEL` | | `INFO` | Log verbosity: `DEBUG` / `INFO` / `WARNING` |

---

## 5. Configuration Files

### 5.1 Agent Personality (`config/SOUL.md`)

`SOUL.md` defines the agent's base persona and standing instructions. It is the first section of every system prompt. Edit it to customise how Juan behaves across all conversations.

```markdown
You are Juan, a highly capable autonomous AI assistant.

You think step by step, use tools proactively, and always explain
your reasoning before taking action.
You prefer to verify assumptions with tools rather than guessing.
```

If `SOUL.md` is missing, a sensible fallback persona is used automatically.

### 5.2 Authorization (`config/juan_auth.json`)

Controls who may interact with Juan via messaging platforms. Five-layer evaluation: `platform_allow_all` → `allowlist` → `dm_pairing` → `global_allow_all` → `denied`.

```json
{
  "pair_code": "CHANGE-THIS-TO-A-STRONG-SECRET",
  "allowlist": {
    "users": ["123456789"],
    "chats": ["-1001234567890"]
  },
  "allow_all": false,
  "platforms": {
    "telegram": { "allow_all": false }
  }
}
```

To add a new user: have them send the `pair_code` to the bot in a DM. They are automatically registered without any manual edit.

> **Note:** Set `"allow_all": true` only during development. Always use an explicit allowlist or pair code in production.

---

## 6. Connecting Telegram

1. Open Telegram and message **@BotFather**
2. Send `/newbot` and follow the prompts
3. Copy the bot token (format: `1234567890:ABCDef...`)
4. Add `JUAN_BOT_TOKEN=<token>` to `.env`
5. Run `docker compose restart juan`
6. Send your `pair_code` to the bot in a DM to authorise yourself

The bot will confirm pairing and begin responding to messages immediately.

---

## 7. Dashboard Reference

Visit **http://localhost:5000** to access the dashboard.

| Panel | Features |
|-------|----------|
| **Overview** | System status badge, uptime, session count, active sessions, total token usage, recent activity mini-feed |
| **Activity Feed** | Real-time structured log stream via SSE. Filterable by component and searchable by keyword. Colour-coded by event type. |
| **Submit Task** | Text input to send any task directly to the agent. Results appear in real time with iteration count and token usage. |
| **Sessions** | Paginated table of all sessions with platform, user, model, token counts, and start time. Click any row to view the full conversation. |
| **Cron Jobs** | Create scheduled tasks with flexible schedule strings and optional delivery targets. Delete existing jobs. |
| **Settings** | Current runtime config: model, provider, platform, max iterations. API key values masked. Config file presence indicated. |

---

## 8. Dashboard API Reference

| Method | Endpoint | Description |
|--------|----------|-------------|
| `GET` | `/api/status` | System status: uptime, session counts, token totals, model/provider/platform |
| `GET` | `/api/events` | Last N log events. Query: `?since=<id>&limit=<n>&component=<name>` |
| `GET` | `/api/events/stream` | Server-Sent Events stream of live log events |
| `GET` | `/api/sessions` | Paginated session list. Query: `?page=<n>&per=<n>` |
| `GET` | `/api/sessions/<id>/messages` | Full message history for a session |
| `POST` | `/api/tasks` | Submit task: `{"message": "..."}`. Returns `{task_id, session_id, status}` |
| `GET` | `/api/tasks/<id>` | Poll task status: `{status, response, iterations, usage}` |
| `GET` | `/api/tasks` | List last 50 submitted tasks |
| `GET` | `/api/cron` | List all cron jobs |
| `POST` | `/api/cron` | Create cron job: `{schedule, task, delivery_target}` |
| `DELETE` | `/api/cron/<id>` | Delete a cron job |
| `GET` | `/api/config` | View configuration (API keys masked) |

---

## 9. Adding Custom Tools

Register tools in the `build_agent()` function in `main.py`. Each tool is a Python callable that returns a dict with `content` and `exit_code`.

```python
def web_search(query: str = "") -> dict:
    results = my_search_api(query)
    return {"content": results, "exit_code": 0}

registry.register(
    name   = "web_search",
    fn     = web_search,
    schema = {
        "name":        "web_search",
        "description": "Search the web for current information",
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Search query"}
            },
            "required": ["query"]
        }
    }
)
```

### Tool approval thresholds

By default, any tool call matching a dangerous pattern requires human approval. Adjust the threshold:

```python
registry = ToolRegistry(
    approval_gate      = gate,
    approval_threshold = "high"  # low | medium | high | critical
)
```

---

## 10. Writing Plugins

Drop a `.py` file in the `plugins/` directory. It is auto-discovered on startup. Plugins can register hooks, expose additional tools, or act as the memory provider.

```python
# plugins/my_plugin.py

def on_agent_end(context: dict) -> None:
    """Called after every agent turn completes."""
    log_to_external_system(context)

def on_tool_call(context: dict) -> None:
    """Called before each tool execution."""
    print(f"Tool: {context['tool_name']}")

TOOLS = {
    "my_tool": lambda query="": {"content": do_thing(query), "exit_code": 0}
}
```

### Available hook events

| Event | When fired |
|-------|-----------|
| `on_session_start` | New session created |
| `on_session_end` | Session ended |
| `on_agent_start` | Agent turn begins |
| `on_agent_end` | Agent turn ends |
| `on_tool_call` | Before tool execution |
| `on_tool_result` | After tool execution |
| `on_message` | Any inbound gateway message |

---

## 11. Cron Jobs

### 11.1 Schedule Formats

| Format | Example | Interval |
|--------|---------|----------|
| `every N minutes/hours/days` | `every 30 minutes` | 30 minutes |
| `@hourly` / `@daily` / `@weekly` | `@daily` | 24 hours |
| ISO 8601 duration | `PT15M` / `PT1H` / `P1D` | 15 min / 1 hr / 1 day |

### 11.2 Creating via Dashboard

1. Go to **http://localhost:5000** → **Cron Jobs**
2. Enter the task description, schedule, and delivery target
3. Click **+ ADD JOB**

### 11.3 Creating via API

```bash
curl -X POST http://localhost:5000/api/cron \
  -H "Content-Type: application/json" \
  -d '{
    "schedule": "every 6 hours",
    "task": "Search for the latest AI news and write a 3-point summary",
    "delivery_target": "telegram:123456789"
  }'
```

### 11.4 Delivery targets

The `delivery_target` field uses the format `platform:chat_id`. Examples:

```
telegram:123456789
telegram:-1001234567890
```

Leave blank to skip delivery (result is stored in SESSION_STORE only).

---

## 12. Monitoring Logs

All log output is structured JSON. Use the dashboard Activity Feed for browser-based monitoring, or filter from the terminal:

```bash
# Follow live
docker compose logs -f juan

# Agent turns only
docker compose logs juan 2>&1 | grep '^{' | jq 'select(.event == "turn_end")'

# Tool executions
docker compose logs juan 2>&1 | grep '^{' | jq 'select(.component == "TOOL_REGISTRY")'

# Errors only
docker compose logs juan 2>&1 | grep '^{' | jq 'select(.level == "ERROR")'

# By session
docker compose logs juan 2>&1 | grep '^{' | jq 'select(.session_id == "telegram:123:456")'

# Token usage summary
docker compose logs juan 2>&1 | grep '^{' | jq 'select(.event == "turn_end") | {session_id, tokens: .total_tokens, iter: .iterations_used}'
```

---

## 13. Production Hardening

- [ ] Set `DASHBOARD_PASSWORD` to a strong password in `.env`
- [ ] Set `DASHBOARD_SECRET` to a 64-character random string in `.env`
- [ ] Set `"allow_all": false` in `config/juan_auth.json`
- [ ] Configure an explicit `allowlist` or use `pair_code` for user onboarding
- [ ] Mount `config/` as read-only (`:ro`) in `docker-compose.yml`
- [ ] Place the dashboard behind nginx or Caddy with TLS
- [ ] Schedule regular backups of the `juan-data` volume
- [ ] Set `LOG_LEVEL=WARNING` to reduce log volume

### Generate a strong secret

```bash
python3 -c "import secrets; print(secrets.token_hex(32))"
```

### Nginx reverse proxy (minimal config)

```nginx
server {
    listen 443 ssl;
    server_name juan.yourdomain.com;

    ssl_certificate     /etc/ssl/certs/juan.crt;
    ssl_certificate_key /etc/ssl/private/juan.key;

    location / {
        proxy_pass         http://localhost:5000;
        proxy_set_header   Host $host;
        proxy_set_header   X-Real-IP $remote_addr;
        proxy_http_version 1.1;
        proxy_set_header   Upgrade $http_upgrade;
        proxy_set_header   Connection "upgrade";
    }
}
```

### Data backup

```bash
docker run --rm \
  -v juan-data:/data \
  -v $(pwd):/backup \
  alpine tar czf /backup/juan-backup-$(date +%Y%m%d).tar.gz /data
```

---

## 14. Updating

```bash
# Pull new image (when available)
docker compose pull

# Restart with zero data loss — data is in the named volume
docker compose down
docker compose up -d
```

The `juan-data` volume persists across updates. Your sessions, memory files, and cron jobs are preserved automatically.

---

## 15. Troubleshooting

| Symptom | Resolution |
|---------|-----------|
| **Dashboard not loading at `:5000`** | Run `docker compose ps` to check the container is `Up`. Check `docker compose logs juan` for startup errors. |
| **Agent not responding to messages** | Check `ANTHROPIC_API_KEY` is set in `.env`. Run `docker compose logs juan \| grep ERROR`. Verify the API key with a direct curl to the provider. |
| **Telegram bot not receiving messages** | Ensure `JUAN_BOT_TOKEN` is correct and the bot is not already running elsewhere. Look for `TOKEN_LOCK_CONFLICT` in logs. |
| **`Database locked` in logs** | Normal under burst load — resolves automatically via jitter retry. If persistent, run `docker compose restart juan`. |
| **Task stuck at `PENDING`** | The agent may be waiting for an LLM response. Check for `TIMEOUT` events in the activity feed. Verify provider API key and network connectivity. |
| **`SOUL.md missing` warning** | Create `config/SOUL.md` with your agent personality. A fallback persona is used until it exists. |
| **Permission denied on data volume** | Run `docker compose down && docker volume rm juan-data && docker compose up -d`. Note: this clears all session history. |

### Useful diagnostic commands

```bash
# Check container health
docker inspect juan-agent --format='{{.State.Health.Status}}'

# View last 100 log lines
docker compose logs --tail=100 juan

# Enter the container for debugging
docker exec -it juan-agent bash

# Check database directly
docker exec juan-agent sqlite3 /data/juan_state.db ".tables"
docker exec juan-agent sqlite3 /data/juan_state.db "SELECT COUNT(*) FROM sessions;"
```

---

## Project Structure

```
juan-docker/
├── main.py                       # Entry point (repl / gateway / acp modes)
├── juan_state.py                 # C-04 SESSION_STORE (SQLite WAL + FTS5)
├── logger.py                     # Structured JSON logging
│
├── agent/
│   ├── run_agent.py              # C-01 AGENT_LOOP
│   ├── prompt_builder.py         # C-07 PROMPT_BUILDER
│   ├── context_compressor.py     # C-08 CONTEXT_COMPRESSOR
│   ├── memory_manager.py         # C-09 MEMORY_MANAGER
│   └── iteration_budget.py       # C-14 ITERATION_BUDGET
│
├── juan_cli/
│   ├── runtime_provider.py       # C-02 PROVIDER_RUNTIME
│   └── plugins.py                # C-11 PLUGIN_MANAGER
│
├── tools/
│   ├── registry.py               # C-03 TOOL_REGISTRY
│   └── approval.py               # C-13 APPROVAL_GATE
│
├── gateway/
│   ├── run.py                    # C-05 GATEWAY_RUNNER
│   ├── authorization.py          # C-10 AUTHORIZATION
│   └── platforms/
│       └── base.py               # C-06 PLATFORM_ADAPTOR (Telegram + BaseAdapter)
│
├── cron/
│   └── scheduler.py              # C-12 CRON_SCHEDULER
│
├── acp_adapter/
│   └── server.py                 # C-15 ACP_SERVER
│
├── dashboard/
│   ├── app.py                    # C-16 DASHBOARD (Flask REST API + SSE)
│   └── templates/
│       └── index.html            # Web UI
│
├── plugins/                      # Drop custom plugins here
├── config/                       # Mounted read-only in Docker
│   ├── SOUL.md                   # Agent personality
│   └── juan_auth.json            # Authorization config
│
├── docs/
│   ├── SOLUTION_DESIGN.md
│   ├── REQUIREMENTS.md
│   └── SETUP.md
│
├── Dockerfile
├── docker-compose.yml
├── entrypoint.sh
├── requirements.txt
└── .env.example
```

---

*Project Juan — User Guide UG-001 v1.0*  
*© 2026 Project Juan. All rights reserved.*
