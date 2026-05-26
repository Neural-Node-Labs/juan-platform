# Project Juan — Solution Design Document

**Version**: 1.0.0  
**Methodology**: CBD-Interface-First  
**Date**: 2026-05-25  
**Status**: Phase II — Implemented

---

## 1. Executive Summary

Project Juan is a **production-grade autonomous AI agent platform** deployable as a single Docker stack. It provides a persistent agent loop capable of real tool execution, multi-platform messaging integration (14+ platforms), a web-based operations dashboard, and a provider-agnostic LLM runtime supporting 18+ providers.

The architecture follows a **Component-Based Design (CBD) Interface-First** methodology: every component's contract (IN schema, OUT schema, error codes, trace log) was fully specified before any implementation code was written. This guarantees clean boundaries, testable units, and safe integration sequencing.

---

## 2. System Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                         DOCKER STACK                            │
│                                                                 │
│  ┌──────────────┐    ┌──────────────┐    ┌──────────────────┐  │
│  │  DASHBOARD   │    │  JUAN AGENT  │    │  GATEWAY RUNNER  │  │
│  │  (Flask :5000│    │  (Python)    │    │  (Platform bots) │  │
│  │   + HTML UI) │    │              │    │                  │  │
│  └──────┬───────┘    └──────┬───────┘    └────────┬─────────┘  │
│         │                   │                     │            │
│         └───────────────────┼─────────────────────┘            │
│                             │                                   │
│                    ┌────────▼────────┐                         │
│                    │  SESSION_STORE  │                         │
│                    │  (SQLite WAL)   │                         │
│                    │  juan_state.db  │                         │
│                    └─────────────────┘                         │
│                                                                 │
│  ┌─────────────────────────────────────────────────────────┐   │
│  │                    VOLUMES                              │   │
│  │  juan-data/  → db, memory files, logs                  │   │
│  │  juan-config/ → SOUL.md, juan_auth.json, .env          │   │
│  └─────────────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────────────┘
```

### 2.1 Component Map

| ID | Component | Role | Risk |
|----|-----------|------|------|
| C-01 | AGENT_LOOP | Orchestrates full autonomous agent turn | ⚠️ CAUTION |
| C-02 | PROVIDER_RUNTIME | Routes and executes LLM API calls | ⚠️ CAUTION |
| C-03 | TOOL_REGISTRY | Discovers and dispatches tool calls concurrently | ⚠️ CAUTION |
| C-04 | SESSION_STORE | SQLite WAL persistence with FTS5 search | ⚠️ CAUTION |
| C-05 | GATEWAY_RUNNER | Multi-platform inbound message routing | ✅ GO |
| C-06 | PLATFORM_ADAPTOR | Normalises platform events to uniform schema | ⚠️ CAUTION |
| C-07 | PROMPT_BUILDER | Assembles system prompt from all sources | ✅ GO |
| C-08 | CONTEXT_COMPRESSOR | Summarises history when context limit approached | ⚠️ CAUTION |
| C-09 | MEMORY_MANAGER | Persists MEMORY.md / USER.md + plugin flush | ✅ GO |
| C-10 | AUTHORIZATION | 5-layer access control, default-deny | ✅ GO |
| C-11 | PLUGIN_MANAGER | Plugin lifecycle (pip / user / project) | ⚠️ CAUTION |
| C-12 | CRON_SCHEDULER | Scheduled jobs in isolated sessions | ✅ GO |
| C-13 | APPROVAL_GATE | Human-in-the-loop for dangerous tool calls | ✅ GO |
| C-14 | ITERATION_BUDGET | Per-session iteration limits and warnings | ✅ GO |
| C-15 | ACP_SERVER | stdio JSON-RPC for IDE integration | ✅ GO |
| C-16 | DASHBOARD | Web UI for monitoring and task submission | ✅ GO |

---

## 3. Data Flow

### 3.1 Inbound Message Flow (Gateway mode)

```
Platform SDK
    │
    ▼
PLATFORM_ADAPTOR.normalize(raw_event) → MessageEvent
    │
    ▼
GATEWAY_RUNNER._handle_event(MessageEvent)
    │
    ├─ AUTHORIZATION.check() ──── deny → error reply
    │
    ├─ session_key = platform:chat_id:user_id
    │
    ├─ Level-1 guard (per-session threading.Lock)
    │
    ├─ Level-2 slash command intercept
    │       /approve /deny /stop → APPROVAL_GATE.receive_decision()
    │       /help /reset /model  → inline handler
    │
    └─ AGENT_LOOP.run(user_message, session_id)
            │
            ├─ PROMPT_BUILDER.build()
            ├─ PROVIDER_RUNTIME.call_provider()
            ├─ TOOL_REGISTRY.dispatch()  (if tool_calls)
            │       └─ APPROVAL_GATE.request()  (if dangerous)
            ├─ CONTEXT_COMPRESSOR.compress()  (if near limit)
            ├─ SESSION_STORE.execute("append_message")
            └─ MEMORY_MANAGER.execute("flush")  (on end)
```

### 3.2 Dashboard Task Submission Flow

```
Browser → POST /api/tasks
    │
    ▼
Dashboard API validates request
    │
    ▼
AGENT_LOOP.run(task, session_id="dashboard:<uuid>")
    │
    ▼
Result stored in SESSION_STORE
    │
    ▼
GET /api/tasks/<id>/result → Browser
```

### 3.3 Cron Flow

```
CronScheduler._tick()  (every 10s)
    │
    ▼
For each due job:
    ├─ session_id = "cron:<job_id>:<run_id>"  (isolated)
    ├─ AGENT_LOOP.run(job.task, session_id)
    └─ delivery_fn(platform, chat_id, result)
```

---

## 4. Adaptor Layer

All inter-component communication goes through typed adaptors. No component imports another component directly — they communicate through the adaptor interfaces.

| Adaptor | Bridges | Protocol |
|---------|---------|----------|
| ProviderAdaptor | AGENT_LOOP ↔ PROVIDER_RUNTIME | Function call + retry wrapper |
| ToolAdaptor | AGENT_LOOP ↔ TOOL_REGISTRY | Async dispatch, ThreadPoolExecutor |
| SessionAdaptor | Multiple ↔ SESSION_STORE | SQLite WAL, BEGIN IMMEDIATE, jitter retry |
| TerminalAdaptor | TOOL_REGISTRY ↔ terminal backends | subprocess / Docker SDK / SSH |
| BrowserAdaptor | TOOL_REGISTRY ↔ browser backends | Playwright / Puppeteer / CDP |
| MCPAdaptor | TOOL_REGISTRY ↔ MCP servers | stdio / HTTP JSON-RPC |
| PlatformAdaptor | GATEWAY_RUNNER ↔ platform SDKs | WebSocket / REST / long-poll |
| GatewayAdaptor | CRON + APPROVAL ↔ GATEWAY_RUNNER | Internal event queue |
| AgentAdaptor | GATEWAY + PLUGINS ↔ AGENT_LOOP | Async task + interrupt event |
| MemoryAdaptor | MEMORY_MANAGER ↔ plugin providers | Plugin interface |
| PluginAdaptor | PLUGIN_MANAGER ↔ installed plugins | pip entry points / file discovery |

---

## 5. Security Design

### 5.1 Authorization (C-10)
Five layers evaluated in order; first match wins; default-deny on any exception:

1. **platform_allow_all** — per-platform open access (dev/testing only)
2. **allowlist** — explicit user_id / chat_id allowlist in `juan_auth.json`
3. **dm_pairing** — user sends secret pair code in DM to self-register
4. **global_allow_all** — global open access flag
5. **denied** — default

### 5.2 Approval Gate (C-13)
Dangerous tool calls are intercepted before execution:
- Risk levels: `low` → `medium` → `high` → `critical`
- Detection: regex patterns on tool name + arguments
- Action: gateway message requesting `/approve` or `/deny`
- Timeout: default 300s → **default deny** (fail-safe)
- Bypass: `/approve`, `/deny`, `/stop` handled inline (never via background queue)

### 5.3 Docker Security
- No root process in container (runs as `juan` user, UID 1000)
- Secrets via environment variables, never baked into image
- Data volumes mounted read-write; config volume mounted read-only
- Network: only port 5000 (dashboard) exposed by default

---

## 6. Observability

All components emit **structured JSON trace events** to stdout:

```json
{
  "timestamp": 1748123456.789,
  "phase": "runtime",
  "component": "AGENT_LOOP",
  "event": "turn_end",
  "session_id": "telegram:123456:789012",
  "end_reason": "stop",
  "iterations_used": 3,
  "total_tokens": 2840,
  "duration_ms": 4120
}
```

**Key trace events by component:**

| Component | Events |
|-----------|--------|
| AGENT_LOOP | turn_start, tool_dispatch, tool_result, token_count, compression_triggered, turn_end |
| PROVIDER_RUNTIME | provider_selected, fallback_activated, retry_n, latency_ms |
| TOOL_REGISTRY | tool_resolved, approval_required, exec_start, exec_end, error_caught |
| SESSION_STORE | db_write, db_read, fts_query, contention_retry, checkpoint, migration_run |
| GATEWAY_RUNNER | message_received, auth_check, session_key_built, guard_level1, guard_level2, agent_dispatched, response_sent |
| APPROVAL_GATE | dangerous_detected, approval_requested, decision_received, bypass_command |
| CRON_SCHEDULER | tick, job_due, agent_fired, delivery_sent, job_error |

The dashboard aggregates these events in real time using SQLite and the `/api/events` endpoint.

---

## 7. Persistence

### 7.1 SQLite Schema (v11)

```sql
sessions   — session metadata, platform, user, token counters
messages   — full conversation history with FTS5 virtual table
_meta      — schema version
```

WAL mode enables concurrent reads during writes. Write contention is handled with jitter retry (up to 15 attempts). FTS5 provides full-text search with fallback to LIKE.

### 7.2 File-based Memory

| File | Content | Manager |
|------|---------|---------|
| `MEMORY.md` | Agent's evolving knowledge | MEMORY_MANAGER |
| `USER.md` | User profile and preferences | MEMORY_MANAGER |
| `SOUL.md` | Agent personality (read-only) | PROMPT_BUILDER |

---

## 8. Failure Modes & Recovery

| Component | Failure | Recovery |
|-----------|---------|----------|
| PROVIDER_RUNTIME | HTTP 5xx / timeout | Exponential backoff × 3, then one-shot fallback model |
| PROVIDER_RUNTIME | AUTH_FAIL | No retry; fallback immediately |
| SESSION_STORE | Lock timeout | Jitter retry × 15; emit LOCK_TIMEOUT |
| TOOL_REGISTRY | Tool exception | Per-tool try-catch; siblings continue |
| APPROVAL_GATE | Timeout | Default deny (fail-safe) |
| CONTEXT_COMPRESSOR | Summary LLM fail | Fallback to truncation-only |
| AUTHORIZATION | Any exception | Default deny (fail-safe) |
| GATEWAY_RUNNER | Agent crash | Error reply to user; loop continues |
| PLUGIN_MANAGER | Plugin load fail | Skip plugin; continue loading others |

---

## 9. Implementation Order (Phase II Completed)

```
1.  SESSION_STORE       ✅  juan_state.py
2.  PROVIDER_RUNTIME    ✅  juan_cli/runtime_provider.py
3.  ITERATION_BUDGET    ✅  agent/iteration_budget.py
4.  PROMPT_BUILDER      ✅  agent/prompt_builder.py
5.  AGENT_LOOP          ✅  agent/run_agent.py
6.  TOOL_REGISTRY       ✅  tools/registry.py
7.  APPROVAL_GATE       ✅  tools/approval.py
8.  CONTEXT_COMPRESSOR  ✅  agent/context_compressor.py
9.  MEMORY_MANAGER      ✅  agent/memory_manager.py
10. AUTHORIZATION       ✅  gateway/authorization.py
11. PLATFORM_ADAPTOR    ✅  gateway/platforms/base.py  (Telegram implemented)
12. GATEWAY_RUNNER      ✅  gateway/run.py
13. PLUGIN_MANAGER      ✅  juan_cli/plugins.py
14. CRON_SCHEDULER      ✅  cron/scheduler.py
15. ACP_SERVER          ✅  acp_adapter/server.py
16. DASHBOARD           ✅  dashboard/  (Flask API + web UI)
```

---

## 10. Known Risks & Mitigations

| Risk | Severity | Mitigation |
|------|----------|------------|
| LLM non-determinism in agent loop | HIGH | Iteration budget (C-14) caps runaway loops |
| SQLite WAL contention under load | HIGH | Jitter retry × 15; WAL checkpoint after session end |
| Compression hallucination in summary | MEDIUM | Fallback to truncation; tool/result pairs never split |
| Third-party platform server downtime (BlueBubbles, iMessage) | MEDIUM | Platform adapter per-error retry + CONNECT_FAIL emit |
| API key exposure | HIGH | Env vars only; never in image; .env in .gitignore |
| Dangerous command false negatives | MEDIUM | Regex patterns + manual approval gate threshold config |

---

*This document is the System Source of Truth for Project Juan architecture.*  
*Generated: 2026-05-25*
