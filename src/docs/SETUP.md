# Setup Guide — Project Juan

## Table of Contents

1. [Prerequisites](#1-prerequisites)
2. [First-time Docker Setup](#2-first-time-docker-setup)
3. [Running Without Docker](#3-running-without-docker)
4. [Connecting a Telegram Bot](#4-connecting-a-telegram-bot)
5. [Adding Custom Tools](#5-adding-custom-tools)
6. [Writing Plugins](#6-writing-plugins)
7. [Adding a New Platform Adapter](#7-adding-a-new-platform-adapter)
8. [IDE Integration (ACP)](#8-ide-integration-acp)
9. [Cron Jobs](#9-cron-jobs)
10. [Production Deployment](#10-production-deployment)
11. [Troubleshooting](#11-troubleshooting)

---

## 1. Prerequisites

- **Docker Engine** 24+ and **Docker Compose** v2+
- An **Anthropic API key** (or another supported provider's key)
- 512 MB RAM minimum; 1 GB+ recommended

### Check Docker installation

```bash
docker --version        # Docker version 24+
docker compose version  # Docker Compose version v2+
```

---

## 2. First-time Docker Setup

```bash
# 1. Enter the project directory
cd juan-docker

# 2. Create .env from template
cp .env.example .env

# 3. Edit .env — at minimum, set ANTHROPIC_API_KEY
nano .env

# 4. Create config directory and files
mkdir -p config data

# 5. (Optional) Add a personality file
cat > config/SOUL.md << 'EOF'
You are Juan, a highly capable autonomous AI assistant.
You think step by step, use tools proactively, and explain your reasoning.
EOF

# 6. (Optional) Add authorization config
cat > config/juan_auth.json << 'EOF'
{
  "pair_code": "CHANGE-THIS-SECRET",
  "allowlist": { "users": [], "chats": [] },
  "allow_all": true
}
EOF

# 7. Build and start
docker compose up -d

# 8. Check status
docker compose ps
docker compose logs -f juan
```

The dashboard is now available at **http://localhost:5000**.

---

## 3. Running Without Docker

### Requirements

```bash
python --version  # Python 3.11+
```

### Install dependencies

```bash
pip install flask flask-cors
# No other external dependencies required for core functionality
```

### Start

```bash
# Set environment variables
export ANTHROPIC_API_KEY=sk-ant-...

# Run interactive REPL
python main.py --mode repl

# Run dashboard only
python dashboard/app.py

# Run Telegram gateway
export JUAN_BOT_TOKEN=<your-token>
python main.py --mode gateway
```

---

## 4. Connecting a Telegram Bot

### Step 1: Create a bot

1. Open Telegram and message **@BotFather**
2. Send `/newbot`
3. Follow the prompts — you'll receive a token like `1234567890:ABCDefGhIJKlmNoPQRsTUVwxyZ`

### Step 2: Configure

In `.env`:

```env
JUAN_BOT_TOKEN=1234567890:ABCDefGhIJKlmNoPQRsTUVwxyZ
JUAN_PLATFORM=telegram
```

### Step 3: Set authorization

In `config/juan_auth.json`:

```json
{
  "pair_code": "MY-UNIQUE-SECRET-CODE",
  "allowlist": { "users": [], "chats": [] },
  "allow_all": false
}
```

### Step 4: Restart and pair

```bash
docker compose restart juan
```

Message your bot the pair code. It will confirm pairing and you can now chat with Juan.

### Step 5: Test

Send your bot: `Hello! What can you do?`

---

## 5. Adding Custom Tools

Open `main.py` and add tools to the `build_agent()` function:

```python
def build_agent(session_key: str = ""):
    from agent.run_agent import AIAgent
    from tools.registry import ToolRegistry
    from tools.approval import ApprovalGate

    gate     = ApprovalGate()
    registry = ToolRegistry(approval_gate=gate)

    # ── Register your custom tool ──────────────────────────
    def web_search(query: str = "") -> dict:
        """Search the web and return results."""
        # Your implementation here
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
                    "query": {
                        "type":        "string",
                        "description": "Search query"
                    }
                },
                "required": ["query"]
            }
        }
    )
    # ──────────────────────────────────────────────────────

    return AIAgent(
        model         = os.environ.get("JUAN_MODEL", "claude-sonnet-4-20250514"),
        provider      = os.environ.get("JUAN_PROVIDER", "anthropic"),
        tool_registry = registry,
    )
```

### Tool schema format

Juan uses Anthropic-style tool schemas:

```python
schema = {
    "name": "tool_name",
    "description": "What this tool does",
    "input_schema": {
        "type": "object",
        "properties": {
            "param1": {"type": "string", "description": "..."},
            "param2": {"type": "integer", "description": "..."}
        },
        "required": ["param1"]
    }
}
```

### Approval thresholds

By default, any tool call matching a dangerous pattern (rm -rf, sudo, etc.) requires human approval. Adjust the threshold:

```python
registry = ToolRegistry(
    approval_gate      = gate,
    approval_threshold = "high"  # low | medium | high | critical
)
```

---

## 6. Writing Plugins

Create a `.py` file in the `plugins/` directory:

```python
# plugins/my_plugin.py

# ── Hooks (optional) ──────────────────────────────────────────────────────
# Called when a new session starts
def on_session_start(context: dict) -> None:
    session_id = context.get("session_id", "")
    print(f"[my_plugin] New session: {session_id}")

# Called when an agent turn ends
def on_agent_end(context: dict) -> None:
    response  = context.get("response", "")
    usage     = context.get("usage", {})
    # e.g. log to external system

# Called before each tool execution
def on_tool_call(context: dict) -> None:
    tool_name = context.get("tool_name", "")
    print(f"[my_plugin] Tool called: {tool_name}")

# ── Extra tools (optional) ────────────────────────────────────────────────
def _my_custom_action(query: str = "") -> dict:
    return {"content": f"Plugin result for: {query}", "exit_code": 0}

TOOLS = {
    "my_custom_action": _my_custom_action
}

# ── Memory provider (optional, single-select) ─────────────────────────────
# Uncomment to make this plugin the memory provider (only one allowed)
# MEMORY_PROVIDER = True
# def on_flush(context: dict) -> None:
#     sync_to_external_memory(context)
```

Available hook events:

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

## 7. Adding a New Platform Adapter

Create a file in `gateway/platforms/`:

```python
# gateway/platforms/discord.py
import os
from gateway.platforms.base import BaseAdapter, MessageEvent

class DiscordAdapter(BaseAdapter):
    platform_name = "discord"

    def __init__(self, token: str = "", on_event=None):
        super().__init__(token or os.environ.get("DISCORD_BOT_TOKEN", ""), on_event)

    def _connect(self) -> None:
        # Initialize Discord client, start receiving events
        # Call self._emit(event) for each inbound message
        ...

    def _disconnect(self) -> None:
        # Shut down client
        ...

    def _send(self, chat_id: str, text: str, thread_id=None) -> None:
        # Send message to Discord channel
        ...

    def _normalize(self, raw: dict) -> MessageEvent:
        return MessageEvent(
            platform  = "discord",
            chat_id   = str(raw["channel_id"]),
            user_id   = str(raw["author"]["id"]),
            text      = raw.get("content", ""),
            thread_id = str(raw.get("referenced_message", {}).get("id", "")),
            raw       = raw,
        )
```

Register it in `gateway/platforms/base.py`:

```python
from gateway.platforms.discord import DiscordAdapter
ADAPTER_MAP["discord"] = DiscordAdapter
```

Set in `.env`:

```env
JUAN_PLATFORM=discord
DISCORD_BOT_TOKEN=your-discord-bot-token
```

---

## 8. IDE Integration (ACP)

Juan speaks **Agent Communication Protocol** over stdio JSON-RPC.

### VS Code (manual)

```bash
# In a terminal connected to the running container:
docker exec -i juan-agent python main.py --mode acp
```

Then pipe JSON-RPC messages:

```bash
echo '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"editor":"vscode","pid":1}}' \
  | docker exec -i juan-agent python main.py --mode acp
```

### Supported methods

| Method | Description |
|--------|-------------|
| `initialize` | Register editor and get capabilities |
| `chat/message` | Send a message and get a response |
| `chat/stream` | Streaming response (same as message currently) |
| `session/new` | Create a new session |
| `session/list` | List active sessions |
| `ping` | Health check |

---

## 9. Cron Jobs

### Via the Dashboard

1. Go to **http://localhost:5000** → **Cron Jobs**
2. Click **New Job**
3. Fill in schedule, task, and delivery target

### Via the API

```bash
curl -X POST http://localhost:5000/api/cron \
  -H "Content-Type: application/json" \
  -d '{
    "schedule": "every 6 hours",
    "task": "Search for the latest AI news and summarize it",
    "delivery_target": "telegram:YOUR_CHAT_ID"
  }'
```

### Schedule formats

| Format | Example | Interval |
|--------|---------|----------|
| `every N minutes` | `every 30 minutes` | 30 min |
| `every N hours` | `every 6 hours` | 6 hr |
| `every N days` | `every 1 days` | 1 day |
| `@hourly` | `@hourly` | 1 hr |
| `@daily` | `@daily` | 24 hr |
| `@weekly` | `@weekly` | 7 days |
| ISO 8601 | `PT15M`, `PT1H`, `P1D` | 15 min, 1 hr, 1 day |

---

## 10. Production Deployment

### Reverse proxy (nginx)

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
        proxy_set_header   X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header   X-Forwarded-Proto $scheme;
        proxy_http_version 1.1;
        proxy_set_header   Upgrade $http_upgrade;
        proxy_set_header   Connection "upgrade";
    }
}
```

### Security hardening

```bash
# In .env for production:
DASHBOARD_PASSWORD=your-strong-password
DASHBOARD_SECRET=your-64-char-random-secret
```

Generate a secret:

```bash
python3 -c "import secrets; print(secrets.token_hex(32))"
```

### Data backup

```bash
# Backup database and memory files
docker run --rm -v juan-data:/data -v $(pwd):/backup \
  alpine tar czf /backup/juan-backup-$(date +%Y%m%d).tar.gz /data
```

### Update

```bash
docker compose pull
docker compose down
docker compose up -d
```

---

## 11. Troubleshooting

### Dashboard not loading

```bash
docker compose ps          # Check container is running
docker compose logs juan   # Check for startup errors
```

### Agent not responding

1. Check `ANTHROPIC_API_KEY` is set in `.env`
2. Check logs: `docker compose logs -f juan 2>&1 | grep ERROR`
3. Test the API key: `curl https://api.anthropic.com/v1/models -H "x-api-key: $ANTHROPIC_API_KEY"`

### Telegram bot not receiving messages

1. Ensure `JUAN_BOT_TOKEN` is correct
2. Check the bot isn't already running elsewhere (token lock)
3. Look for `CONNECT_FAIL` in logs

### Database locked error

This means concurrent writes exceeded the retry budget. Usually resolves itself. If persistent:

```bash
docker compose restart juan
```

### Out of memory / context too long

Reduce `MAX_ITERATIONS` in `.env` or adjust `COMPRESSION_THRESHOLD` in `agent/run_agent.py`.

### Permission denied on data volume

```bash
docker compose down
docker volume rm juan-data
docker compose up -d
```

---

*Juan Agent Platform — Setup Guide*
