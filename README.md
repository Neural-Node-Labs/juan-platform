# 🤖 Project Juan

**Autonomous AI Agent Platform** — Docker-ready, multi-platform, production-grade.

## What is Juan?

Juan is a self-hosted AI agent platform that:

- 🔁 **Runs autonomous agent loops** with real tool execution
- 💬 **Connects to 14+ messaging platforms** (Telegram, Discord, Slack, WhatsApp, Signal, iMessage, Matrix, and more)
- 🧠 **Maintains persistent memory** across conversations
- 🔀 **Supports 18+ LLM providers** with automatic fallback (Anthropic, OpenAI, Groq, Mistral, Ollama, etc.)
- 📊 **Ships a web dashboard** to monitor activity, submit tasks, manage cron jobs, and view logs
- 🔌 **Integrates with IDEs** via ACP (VS Code, Zed, JetBrains)
- 🔒 **Human-in-the-loop approval** for dangerous tool calls

---

## Quick Start (Docker)

### 1. Unzip and enter the project

```bash
unzip juan_platform_docker.zip
cd juan-docker
```

### 2. Configure

```bash
cp .env.example .env
# Edit .env and add your ANTHROPIC_API_KEY (and optionally JUAN_BOT_TOKEN)
```

### 3. Start

```bash
docker compose up -d
```

### 4. Open the dashboard

**http://localhost:5000**

---

## Configuration

### `.env` Variables

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `ANTHROPIC_API_KEY` | ✅ | — | Anthropic API key |
| `OPENAI_API_KEY` | ☐ | — | OpenAI (fallback / direct) |
| `JUAN_MODEL` | ☐ | `claude-sonnet-4-20250514` | Default model |
| `JUAN_PROVIDER` | ☐ | `anthropic` | Default provider |
| `JUAN_BOT_TOKEN` | ☐ | — | Telegram bot token |
| `JUAN_PLATFORM` | ☐ | `telegram` | Gateway platform |
| `DASHBOARD_SECRET` | ☐ | `changeme` | Dashboard session secret |
| `DASHBOARD_PASSWORD` | ☐ | — | Optional dashboard password |
| `MAX_ITERATIONS` | ☐ | `90` | Agent loop iteration cap |

### Agent Personality (`config/SOUL.md`)

```markdown
You are Juan, a highly capable autonomous AI assistant.
You are direct, precise, and always explain your reasoning.
```

### Authorization (`config/juan_auth.json`)

```json
{
  "pair_code": "MY-SECRET-CODE",
  "allowlist": { "users": ["123456789"], "chats": [] },
  "platforms": { "telegram": { "allow_all": false } }
}
```

Users pair by sending the `pair_code` to the bot in a DM.

---

## Dashboard

Visit **http://localhost:5000** for:

- **Overview** — system status, session counts, token usage
- **Activity Feed** — real-time structured log stream
- **Task Submission** — send tasks to the agent directly from the browser
- **Sessions** — browse and inspect all conversations
- **Cron Jobs** — create and manage scheduled tasks
- **Settings** — view configuration

---

## Project Structure

```
src/
├── main.py                 # Entry point
├── juan_state.py           # SESSION_STORE (SQLite WAL + FTS5)
├── logger.py               # Structured JSON logging
├── agent/                  # AGENT_LOOP, PROMPT_BUILDER, MEMORY, COMPRESSOR
├── juan_cli/               # PROVIDER_RUNTIME, PLUGIN_MANAGER
├── tools/                  # TOOL_REGISTRY, APPROVAL_GATE
├── gateway/                # GATEWAY_RUNNER, AUTHORIZATION, PLATFORM_ADAPTOR
├── cron/                   # CRON_SCHEDULER
├── acp_adapter/            # ACP_SERVER (IDE JSON-RPC)
├── dashboard/              # Flask API + web UI
├── plugins/                # Drop custom plugins here
├── config/                 # SOUL.md, juan_auth.json (mounted read-only)
├── docs/                   # SOLUTION_DESIGN.md, SETUP.md
├── Dockerfile
├── docker-compose.yml
└── .env.example
```

---

## Logs

```bash
docker compose logs -f juan
# Filter with jq:
docker compose logs -f juan 2>&1 | grep '^{' | jq 'select(.event == "turn_end")'
```

## Full Documentation

See `docs/SOLUTION_DESIGN.md` for complete architecture, data flow diagrams, security design, and failure mode analysis.

See `docs/SETUP.md` for detailed setup, plugin authoring, and platform adapter guides.

---

## CBD Development Skill

This project includes the **`cbd-development` skill** for Claude Code, pre-installed at:

```
.claude/skills/cbd-skill/
```

The skill teaches Claude the **Component-Based Design (CBD) Interface-First** methodology used to build this platform. When you open this project in Claude Code, Claude will automatically use it when you ask it to design, architect, or extend any part of the system.

### What the skill enables

- Design new components with full IN/OUT/Error/Trace contracts before writing code
- Extend the platform (new tools, platform adaptors, plugins) following the same methodology
- Ask Claude to "spec out a new component" or "design the contract for X" and it will produce a complete CBD specification

### Skill files

| File | Purpose |
|------|---------|
| `.claude/skills/cbd-skill/SKILL.md` | Main skill — 6-step sequence, component map template, delivery guide |
| `.claude/skills/cbd-skill/references/worked-example.md` | Complete worked example (APPROVAL_GATE, all 6 steps) |
| `.claude/skills/cbd-skill/references/contract-template.md` | Copy-paste template for specifying any new component |
| `.claude/skills/cbd-skill/references/patterns.md` | 7 named patterns with rationale and contract shapes |
| `docs/cbd-development.skill` | Distributable `.skill` file — install in any other Claude Code project |

### Installing in another project

Copy `docs/cbd-development.skill` to your project, then:

```bash
mkdir -p .claude/skills
cd .claude/skills
unzip path/to/cbd-development.skill
```
