# Project Juan — Autonomous Agent Platform

**Version**: 1.0.0 | **Methodology**: CBD-Interface-First | **Status**: Phase II — Implemented

## Architecture

15 components implemented in the blueprint's recommended sequence:

```
C-04 SESSION_STORE       juan_state.py                   SQLite WAL + FTS5, schema v11
C-02 PROVIDER_RUNTIME    juan_cli/runtime_provider.py    18+ providers, retry, fallback
C-14 ITERATION_BUDGET    agent/iteration_budget.py       80% warn, exhaustion summary
C-07 PROMPT_BUILDER      agent/prompt_builder.py         SOUL.md + memory + tools + cache markers
C-01 AGENT_LOOP          agent/run_agent.py              Full autonomous turn orchestration
C-03 TOOL_REGISTRY       tools/registry.py               Concurrent dispatch + approval gating
C-13 APPROVAL_GATE       tools/approval.py               Human-in-the-loop, default-deny
C-08 CONTEXT_COMPRESSOR  agent/context_compressor.py     Lossy summarisation + session lineage
C-09 MEMORY_MANAGER      agent/memory_manager.py         MEMORY.md / USER.md + plugin hooks
C-10 AUTHORIZATION       gateway/authorization.py        5-layer access control, default-deny
C-06 PLATFORM_ADAPTOR    gateway/platforms/base.py       Telegram + extensible BaseAdapter
C-05 GATEWAY_RUNNER      gateway/run.py                  Two-level guard + slash commands
C-11 PLUGIN_MANAGER      juan_cli/plugins.py             pip / user / project plugin discovery
C-12 CRON_SCHEDULER      cron/scheduler.py               Isolated cron sessions + delivery
C-15 ACP_SERVER          acp_adapter/server.py           stdio JSON-RPC for VS Code/Zed/JetBrains
```

## Quick Start

### Local REPL (no API key needed for testing)
```bash
python main.py --mode repl
```

### Telegram Gateway
```bash
export ANTHROPIC_API_KEY=sk-ant-...
export JUAN_BOT_TOKEN=<telegram-bot-token>
python main.py --mode gateway
```

### IDE Integration (ACP)
```bash
python main.py --mode acp   # reads stdin, writes stdout JSON-RPC
```

## Configuration

| File | Purpose |
|------|---------|
| `SOUL.md` | Agent personality / system prompt base |
| `MEMORY.md` | Persistent agent memory (auto-managed) |
| `USER.md` | User profile (auto-managed) |
| `juan_auth.json` | Authorization config (allowlist, pair code) |
| `juan_pairing.json` | DM pairing state (auto-managed) |

### `juan_auth.json` example
```json
{
  "pair_code": "SECRET123",
  "allowlist": { "users": ["123456789"], "chats": [] },
  "platforms": { "telegram": { "allow_all": false } }
}
```

## Environment Variables

| Variable | Purpose |
|----------|---------|
| `ANTHROPIC_API_KEY` | Anthropic API key |
| `JUAN_MODEL` | Model to use (default: `claude-sonnet-4-20250514`) |
| `JUAN_PROVIDER` | Provider (default: `anthropic`) |
| `JUAN_BOT_TOKEN` | Platform bot token |
| `JUAN_PLATFORM` | Platform name (default: `telegram`) |

## Extending

### Register a tool
```python
from tools.registry import ToolRegistry
registry = ToolRegistry()
registry.register(
    "my_tool",
    lambda query="": {"content": f"Result: {query}", "exit_code": 0},
    schema={"name": "my_tool", "description": "Does something", "input_schema": {...}}
)
```

### Write a plugin
```python
# plugins/my_plugin.py
def on_session_start(context):
    print(f"New session: {context['session_id']}")

TOOLS = {
    "my_custom_tool": lambda **kwargs: {"content": "done", "exit_code": 0}
}
```

### Add a platform adapter
```python
from gateway.platforms.base import BaseAdapter, MessageEvent

class MyPlatformAdapter(BaseAdapter):
    platform_name = "myplatform"
    def _connect(self): ...
    def _disconnect(self): ...
    def _send(self, chat_id, text, thread_id): ...
    def _normalize(self, raw) -> MessageEvent: ...
```

## Logging

All components emit structured JSON to stdout:
```json
{"timestamp": 1779..., "phase": "runtime", "component": "AGENT_LOOP",
 "event": "turn_end", "session_id": "...", "end_reason": "stop",
 "iterations_used": 3, "total_tokens": 1420}
```

Set `JUAN_LOG_FILE` to redirect to a file; pipe through `jq` for filtering.
