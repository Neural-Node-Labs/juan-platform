# Juan Platform — Interface Reference

Every public interface extracted directly from source. Grouped by component in implementation order.

---

## How to read this document

```
ClassName(constructor_args)         ← how to instantiate
  .method(args) -> return_type      ← public methods
  raises: ErrorClass                ← exceptions to handle

Operations passed to .execute() or .dispatch() are listed inline.
```

---

## C-04 · SESSION_STORE
**File:** `juan_state.py`

```python
# Singleton — use this, don't instantiate SessionDB directly
get_db(path: str = "juan_state.db") -> SessionDB

SessionDB(db_path: str = "juan_state.db")
  .execute(operation: str, session_id: str, payload: dict | None = None) -> dict
  raises: SessionStoreError
```

**Operations for `.execute()`:**

| operation | payload fields | returns |
|-----------|---------------|---------|
| `create` | `platform`, `chat_id`, `user_id`, `model`, `parent_id?` | `{ok: bool}` |
| `append_message` | `role`, `content`, `tool_call_id?`, `tool_name?`, `usage?` | `{ok: bool}` |
| `get_messages` | _(none)_ | `{messages: list, ok: bool}` |
| `search` | `query: str` | `{search_results: list, ok: bool}` |
| `end` | _(none)_ | `{ok: bool}` |
| `prune` | `keep_last: int = 50` | `{ok: bool}` |
| `export` | _(none)_ | `{session: dict, messages: list, ok: bool}` |

**Error codes:** `MIGRATION_FAIL` · `WRITE_FAIL` · `READ_FAIL` · `LOCK_TIMEOUT`

---

## C-02 · PROVIDER_RUNTIME
**File:** `juan_cli/runtime_provider.py`

```python
call_provider(
    messages:       list,
    model:          str,
    provider:       str,
    api_mode:       str | None = None,
    max_tokens:     int = 8000,
    stream:         bool = False,
    fallback_model: dict | None = None,   # {"provider": str, "model": str}
    tools:          list | None = None,
) -> dict
raises: ProviderError
```

**Returns:**
```python
{
    "content":       str,
    "role":          "assistant",
    "finish_reason": str,
    "tool_calls":    list,
    "reasoning":     str | None,
    "provider_used": str,
    "fallback_used": bool,
    "usage": {
        "input_tokens":       int,
        "output_tokens":      int,
        "cache_read_tokens":  int,
        "cache_write_tokens": int,
        "reasoning_tokens":   int,
    }
}
```

**Supported providers:** `anthropic` · `openai` · `openrouter` · `groq` · `mistral` · `cohere` · `together` · `fireworks` · `deepseek` · `xai` · `gemini` · `ollama` · `lmstudio` · `custom`

**Error codes:** `AUTH_FAIL` · `RATE_LIMITED` · `UPSTREAM_ERROR` · `TIMEOUT` · `FALLBACK_EXHAUSTED`

---

## C-14 · ITERATION_BUDGET
**File:** `agent/iteration_budget.py`

```python
IterationBudget(
    session_id:     str,
    max_iterations: int = 90,
    is_subagent:    bool = False,
    subagent_max:   int = 50,
)
  .check(current_iteration: int) -> dict
  # Never raises — swallows all exceptions internally
```

**Returns:**
```python
{
    "allowed":           bool,
    "remaining":         int,
    "warning_level":     "ok" | "warning_80pct" | "exhausted",
    "summary_triggered": bool,
}
```

---

## C-07 · PROMPT_BUILDER
**File:** `agent/prompt_builder.py`

```python
PromptBuilder()
  .build(
      session_id:    str,
      model:         str,
      tools:         list,
      memory_files:  dict | None = None,   # {"MEMORY_md": str, "USER_md": str}
      skills:        list | None = None,
      context_files: list | None = None,   # file paths
      apply_caching: bool = False,
  ) -> dict
  raises: PromptBuilderError
```

**Returns:**
```python
{
    "system_prompt":  str,
    "cache_markers":  list | None,
    "token_estimate": int,
    "sections_built": list[str],   # ["soul", "memory", "tools", "skills", "context"]
}
```

**Error codes:** `SOUL_MISSING` · `MEMORY_READ_FAIL` · `TOOL_SCHEMA_INVALID` · `CACHE_MARKER_FAIL`

---

## C-01 · AGENT_LOOP
**File:** `agent/run_agent.py`

```python
AIAgent(
    model:          str = "claude-sonnet-4-20250514",
    provider:       str = "anthropic",
    fallback_model: dict | None = None,
    tool_registry:  ToolRegistry | None = None,
    memory_manager: MemoryManager | None = None,
    context_limit:  int = 200_000,
    approval_gate:  ApprovalGate | None = None,
)
  .run(
      user_message:         str,
      session_id:           str | None = None,
      task_id:              str | None = None,
      system_prompt:        str | None = None,
      conversation_history: list | None = None,
      max_iterations:       int = 90,
      is_subagent:          bool = False,
  ) -> dict
  raises: AgentError
```

**Returns:**
```python
{
    "response":              str,
    "session_id":            str,
    "task_id":               str,
    "iterations_used":       int,
    "tool_calls_made":       int,
    "compression_triggered": bool,
    "end_reason":            "stop" | "budget_exhausted" | "error",
    "usage": {
        "input_tokens":  int,
        "output_tokens": int,
        "cache_tokens":  int,
    }
}
```

**Error codes:** `PROVIDER_FAIL` · `TOOL_FATAL`

---

## C-03 · TOOL_REGISTRY
**File:** `tools/registry.py`

```python
ToolRegistry(
    approval_gate:      ApprovalGate | None = None,
    approval_threshold: str = "medium",   # "low" | "medium" | "high" | "critical"
)
  .register(name: str, fn: Callable, schema: dict | None = None) -> None
  .get_tool_schemas() -> list[dict]
  .dispatch(
      tool_calls: list[dict],
      task_id:    str,
      session_id: str,
      concurrent: bool = True,
  ) -> dict
  raises: ToolError
```

**`tool_calls` item shape:**
```python
{"id": str, "name": str, "arguments": dict | str}
```

**Returns:**
```python
{
    "results": [
        {
            "tool_call_id": str,
            "tool_name":    str,
            "content":      str,
            "exit_code":    int,   # 0 = success
            "duration_ms":  int,
        }
    ]
}
```

**Error codes:** `TOOL_NOT_FOUND` · `APPROVAL_DENIED` · `EXEC_TIMEOUT` · `EXEC_FATAL`

---

## C-13 · APPROVAL_GATE
**File:** `tools/approval.py`

```python
# Module-level helper
detect_risk(tool_name: str, arguments: dict) -> str
# Returns: "low" | "medium" | "high" | "critical"

ApprovalGate(delivery_fn: Callable | None = None)
  .needs_approval(
      tool_name:  str,
      arguments:  dict,
      threshold:  str = "medium",
  ) -> bool

  .request(
      tool_name:  str,
      arguments:  dict,
      session_id: str,
      risk_level: str,
      timeout_s:  int = 300,
  ) -> dict
  raises: ApprovalGateError   # DELIVERY_FAIL | TIMEOUT — both default to deny

  .receive_decision(
      session_id:    str,
      decision:      str,        # "approve" | "deny" | "stop"
      modified_args: dict | None = None,
  ) -> None
```

**`.request()` returns:**
```python
{
    "approved":      bool,
    "user_decision": "approve" | "deny" | "stop",
    "timestamp":     float,
    "modified_args": dict | None,
}
```

**Safety invariant:** Any exception from `.request()` must be treated as deny. The gate never fails open.

**Error codes:** `DELIVERY_FAIL` · `TIMEOUT`

---

## C-08 · CONTEXT_COMPRESSOR
**File:** `agent/context_compressor.py`

```python
ContextCompressor(
    provider_call:  Callable,   # call_provider()
    session_store:  SessionDB,
    memory_manager: MemoryManager,
    aux_model:      str = "",
)
  .compress(
      messages:            list,
      model_context_limit: int,
      current_token_count: int,
      protect_last_n:      int = 20,
      session_id:          str = "",
      trigger:             str = "preflight",
  ) -> dict
  raises: CompressorError
```

**Returns:**
```python
{
    "compressed_messages": list,
    "summary":             str,
    "new_session_id":      str,
    "parent_session_id":   str,
    "tokens_saved":        int,
    "messages_removed":    int,
}
```

**Error codes:** `MEMORY_FLUSH_FAIL`

---

## C-09 · MEMORY_MANAGER
**File:** `agent/memory_manager.py`

```python
MemoryManager(provider_hooks: list | None = None)
  .execute(
      operation:  str,
      session_id: str,
      content:    str | None = None,
      provider:   str | None = None,
  ) -> dict
  raises: MemoryError
```

**Operations for `.execute()`:**

| operation | payload | returns |
|-----------|---------|---------|
| `read` | _(none)_ | `{memory_md: str, user_md: str, flushed: bool, provider_ok: bool}` |
| `write` | `content: str` | same shape |
| `flush` | _(none)_ | same shape, `flushed: True` |
| `on_session_end` | _(none)_ | same as `flush` |

**Error codes:** `READ_FAIL` · `WRITE_FAIL`

---

## C-10 · AUTHORIZATION
**File:** `gateway/authorization.py`

```python
# Module-level function — no class
check(
    platform:     str,
    user_id:      str,
    chat_id:      str,
    message_text: str,
) -> dict
# Never raises — any exception returns {"authorized": False}
```

**Returns:**
```python
{
    "authorized": bool,
    "reason":     str,
    "method":     "platform_allow_all" | "allowlist" | "dm_pairing"
                  | "global_allow_all" | "denied",
}
```

---

## C-06 · PLATFORM_ADAPTOR
**File:** `gateway/platforms/base.py`

```python
# Data class — normalised inbound event
MessageEvent(
    platform:  str,
    chat_id:   str,
    user_id:   str,
    text:      str,
    thread_id: str | None = None,
    raw:       dict | None = None,
)
  .to_dict() -> dict

# Abstract base — extend to add new platforms
BaseAdapter(token: str = "", on_event: Callable | None = None)
  .connect() -> None
  .disconnect(reason: str = "normal") -> None
  .send(chat_id: str, text: str, thread_id: str | None = None) -> bool
  raises: AdaptorError

# Abstract methods every subclass must implement:
  ._connect() -> None
  ._disconnect() -> None
  ._send(chat_id: str, text: str, thread_id: str | None) -> None
  ._normalize(raw: dict) -> MessageEvent

# Factory
get_adapter(
    platform:  str,
    token:     str = "",
    on_event:  Callable | None = None,
) -> BaseAdapter
raises: AdaptorError   # if platform not in ADAPTER_MAP

# Implementations shipped:
TelegramAdapter(token: str = "", on_event: Callable | None = None)
```

**ADAPTER_MAP** (register new platforms here):
```python
ADAPTER_MAP: dict[str, type[BaseAdapter]] = {
    "telegram": TelegramAdapter,
    # "discord": DiscordAdapter,  ← add here
}
```

**Error codes:** `AUTH_FAIL` · `CONNECT_FAIL` · `TOKEN_LOCK_CONFLICT`

---

## C-05 · GATEWAY_RUNNER
**File:** `gateway/run.py`

```python
GatewayRunner(
    agent_factory: Callable | None = None,  # (session_key: str) -> AIAgent
    approval_gate: ApprovalGate | None = None,
)
  .add_platform(platform: str, token: str = "") -> None
  .remove_platform(platform: str) -> None
  .deliver(
      platform:  str,
      chat_id:   str,
      text:      str,
      thread_id: str | None = None,
  ) -> bool
  .run_forever() -> None
  raises: GatewayError
```

**Built-in slash commands** (handled before agent dispatch):

| Command | Action |
|---------|--------|
| `/approve` | Routes to `ApprovalGate.receive_decision("approve")` |
| `/deny` | Routes to `ApprovalGate.receive_decision("deny")` |
| `/stop` | Routes to `ApprovalGate.receive_decision("stop")` |
| `/help` | Returns command list |
| `/reset` | Session reset acknowledgement |

---

## C-12 · CRON_SCHEDULER
**File:** `cron/scheduler.py`

```python
CronScheduler(
    agent_factory:  Callable | None = None,  # () -> AIAgent
    delivery_fn:    Callable | None = None,  # (platform, chat_id, text) -> bool
    tick_interval:  float = 10.0,            # seconds between ticks
)
  .start() -> None
  .stop() -> None
  .execute(
      operation:       str,
      job_id:          str | None = None,
      schedule:        str | None = None,
      task:            str | None = None,
      delivery_target: str | None = None,  # "platform:chat_id"
  ) -> dict
  raises: CronError
```

**Operations for `.execute()`:**

| operation | required args | returns |
|-----------|--------------|---------|
| `add_job` | `schedule`, `task` | `{jobs: [job], next_run: float}` |
| `remove_job` | `job_id` | `{jobs: []}` |
| `list_jobs` | _(none)_ | `{jobs: list}` |
| `tick` | _(none)_ | `{jobs: [due_jobs]}` |

**Schedule formats:** `"every N minutes/hours/days"` · `"@hourly"` · `"@daily"` · `"@weekly"` · `"PT15M"` · `"PT1H"` · `"P1D"`

**Error codes:** `SCHEDULE_PARSE_FAIL` · `JOB_NOT_FOUND` · `AGENT_FIRE_FAIL` · `DELIVERY_FAIL`

---

## C-11 · PLUGIN_MANAGER
**File:** `juan_cli/plugins.py`

```python
PluginManager()
  .discover_and_load() -> None
  .fire(
      event:       str,
      context:     dict,
      plugin_type: str | None = None,
  ) -> dict
  .get_tool_hooks() -> dict[str, Callable]
  raises: PluginError   # on load only — fire() never raises
```

**`.fire()` returns:**
```python
{
    "hooks_fired":      list[str],
    "tools_registered": list[str],
    "errors":           list[dict],
}
```

**Hook events:** `on_message` · `on_agent_start` · `on_agent_end` · `on_tool_call` · `on_tool_result` · `on_session_start` · `on_session_end`

**Plugin discovery order:** pip entry points (`juan.plugins`) → `~/.juan/plugins/` → `./plugins/`

**Plugin contract** (what a plugin file may export):

```python
# Any of these — all optional
def on_agent_end(context: dict) -> None: ...
def on_tool_call(context: dict) -> None: ...
# ... any on_<event> function

TOOLS = {"tool_name": callable}          # registers additional tools
MEMORY_PROVIDER = True                   # single-select; only one plugin may set this
CONTEXT_ENGINE  = True                   # single-select; only one plugin may set this
```

**Error codes:** `PLUGIN_LOAD_FAIL` · `TOOL_CONFLICT` · `HOOK_ERROR`

---

## C-15 · ACP_SERVER
**File:** `acp_adapter/server.py`

```python
ACPServer(
    agent_factory: Callable | None = None,  # () -> AIAgent
    session_store: SessionDB | None = None,
)
  .run(infile=None, outfile=None) -> None
  # Reads JSON-RPC 2.0 from infile (default: stdin)
  # Writes JSON-RPC 2.0 to outfile (default: stdout)
  # Blocks until EOF
```

**JSON-RPC methods:**

| Method | Params | Returns |
|--------|--------|---------|
| `initialize` | `{editor: str, pid: int}` | `{server, version, capabilities}` |
| `chat/message` | `{message: str, session_id?: str}` | `{result: agent_result, status, stream_delta}` |
| `chat/stream` | same as `chat/message` | same |
| `session/new` | `{...}` | `{session_id, status}` |
| `session/list` | `{}` | `{sessions: list, status}` |
| `ping` | `{}` | `{pong: true}` |

**Wire format:**
```json
// Request
{"jsonrpc": "2.0", "id": 1, "method": "chat/message", "params": {"message": "..."}}

// Success response
{"jsonrpc": "2.0", "id": 1, "result": {...}}

// Error response
{"jsonrpc": "2.0", "id": 1, "error": {"code": "AGENT_ERROR", "message": "..."}}
```

**Error codes:** `PARSE_ERROR` · `METHOD_NOT_FOUND` · `AGENT_ERROR`

---

## C-16 · DASHBOARD
**File:** `dashboard/app.py`  **Port:** `5000`

```
GET  /                              Web UI
POST /api/auth                      {password} → {ok}
GET  /api/status                    System status
GET  /api/events                    ?since=<id>&limit=<n>&component=<name>
GET  /api/events/stream             SSE stream of live log events
GET  /api/sessions                  ?page=<n>&per=<n>
GET  /api/sessions/<id>/messages    Full conversation
POST /api/tasks                     {message} → {task_id, session_id, status}
GET  /api/tasks/<id>                {status, response, iterations, usage, error?}
GET  /api/tasks                     Last 50 tasks
GET  /api/cron                      List jobs
POST /api/cron                      {schedule, task, delivery_target?}
DEL  /api/cron/<id>                 Delete job
GET  /api/config                    Config (API keys masked)
```

**`/api/status` response:**
```python
{
    "status":          "running",
    "uptime_s":        int,
    "sessions":        int,
    "active":          int,
    "messages":        int,
    "events_buffered": int,
    "tokens":          {"input": int, "output": int, "cache": int},
    "model":           str,
    "provider":        str,
    "platform":        str,
}
```

---

## LOGGER
**File:** `logger.py`

```python
trace(component: str, event: str, session_id: str = "", **kwargs) -> None
warn(component:  str, msg: str,   **kwargs) -> None
error(component: str, msg: str,   **kwargs) -> None
```

All output is structured JSON to stdout:
```json
{
  "timestamp":  1748123456.789,
  "phase":      "runtime",
  "component":  "AGENT_LOOP",
  "event":      "turn_end",
  "session_id": "telegram:123:456",
  "duration_ms": 4120
}
```

---

## Interface dependency map

```
                    LOGGER  ←──────────────────────────── all components
                       │
          ┌────────────┴────────────────┐
          │                             │
    SESSION_STORE               PROVIDER_RUNTIME
          │                             │
          │              ┌──────────────┘
          │              │
    ┌─────▼──────────────▼─────┐
    │         AGENT_LOOP        │
    └──┬──────────┬─────────────┘
       │          │
  PROMPT_BUILDER  │         TOOL_REGISTRY ──→ APPROVAL_GATE
       │          │              │
  MEMORY_MANAGER  │              │
       │          │              │
  CONTEXT_COMPRESSOR             │
                  │              │
            ┌─────▼──────────────▼────┐
            │      GATEWAY_RUNNER      │
            └──┬────────────┬──────────┘
               │            │
        AUTHORIZATION   PLATFORM_ADAPTOR
                            │
                    (Telegram / Discord / ...)


CRON_SCHEDULER ──────────────────────────────→ AGENT_LOOP
PLUGIN_MANAGER ──→ TOOL_REGISTRY / hooks into GATEWAY + AGENT
ACP_SERVER     ──────────────────────────────→ AGENT_LOOP
DASHBOARD      ──→ SESSION_STORE + AGENT_LOOP + CRON_SCHEDULER
```
