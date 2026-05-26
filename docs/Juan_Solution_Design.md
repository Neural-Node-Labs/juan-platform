# Juan Platform — Solution Design

**Document ID:** SD-001  
**Version:** 1.0  
**Date:** 25 May 2026  
**Methodology:** Component-Based Design (CBD) Interface-First  
**Status:** Phase II — Implemented

---

## Table of Contents

1. [Executive Summary](#1-executive-summary)
2. [Methodology: CBD Interface-First](#2-methodology-cbd-interface-first)
3. [Component Map](#3-component-map)
4. [System Architecture](#4-system-architecture)
5. [Data Flows](#5-data-flows)
6. [Adaptor Layer](#6-adaptor-layer)
7. [Security Design](#7-security-design)
8. [Observability](#8-observability)
9. [Persistence](#9-persistence)
10. [Failure Modes & Recovery](#10-failure-modes--recovery)
11. [Implementation Sequence](#11-implementation-sequence)

---

## 1. Executive Summary

Project Juan is a production-grade autonomous AI agent platform built on the **Component-Based Design (CBD) Interface-First** methodology. Every component's contract — input schema, output schema, error codes, and trace log — was fully specified before implementation began, guaranteeing clean boundaries, independent testability, and safe integration sequencing.

The platform deploys as a single Docker stack, persists state in SQLite with WAL mode, and requires only two external Python dependencies (`flask`, `flask-cors`). It connects to 18+ LLM providers and 14+ messaging platforms through typed adaptor layers, with no direct coupling between components.

---

## 2. Methodology: CBD Interface-First

Each of the 16 components was designed following this sequence:

1. Define the component's single responsibility
2. Specify the IN schema (inputs), OUT schema (outputs), and error codes exhaustively
3. Define the trace log events the component emits
4. Identify which adaptors it communicates through
5. Implement against the contract, not against other components
6. Write acceptance tests that validate the contract boundaries

This approach allows any component to be replaced independently, enables parallel development, and makes failure isolation deterministic.

---

## 3. Component Map

| ID | Component | Responsibility | File | Risk |
|----|-----------|----------------|------|------|
| C-01 | **AGENT_LOOP** | Orchestrates each autonomous agent turn end-to-end | `agent/run_agent.py` | ⚠ CAUTION |
| C-02 | **PROVIDER_RUNTIME** | Routes and executes LLM API calls across 18+ providers | `juan_cli/runtime_provider.py` | ⚠ CAUTION |
| C-03 | **TOOL_REGISTRY** | Registers, validates, and dispatches tool calls concurrently | `tools/registry.py` | ⚠ CAUTION |
| C-04 | **SESSION_STORE** | SQLite WAL persistence with FTS5 full-text search | `juan_state.py` | ⚠ CAUTION |
| C-05 | **GATEWAY_RUNNER** | Routes inbound messages; two-level guard; slash commands | `gateway/run.py` | ✓ GO |
| C-06 | **PLATFORM_ADAPTOR** | Normalises raw platform events to uniform `MessageEvent` | `gateway/platforms/base.py` | ⚠ CAUTION |
| C-07 | **PROMPT_BUILDER** | Assembles system prompt from all sources + cache markers | `agent/prompt_builder.py` | ✓ GO |
| C-08 | **CONTEXT_COMPRESSOR** | Summarises middle turns when context limit is approached | `agent/context_compressor.py` | ⚠ CAUTION |
| C-09 | **MEMORY_MANAGER** | Reads/writes `MEMORY.md` and `USER.md`; fires plugin hooks | `agent/memory_manager.py` | ✓ GO |
| C-10 | **AUTHORIZATION** | Five-layer access control; default-deny on any exception | `gateway/authorization.py` | ✓ GO |
| C-11 | **PLUGIN_MANAGER** | Discovers and lifecycle-manages plugins from three sources | `juan_cli/plugins.py` | ⚠ CAUTION |
| C-12 | **CRON_SCHEDULER** | Runs scheduled jobs in isolated sessions with delivery | `cron/scheduler.py` | ✓ GO |
| C-13 | **APPROVAL_GATE** | Intercepts dangerous tool calls; requests human decision | `tools/approval.py` | ✓ GO |
| C-14 | **ITERATION_BUDGET** | Enforces per-session loop limits; warns at 80% | `agent/iteration_budget.py` | ✓ GO |
| C-15 | **ACP_SERVER** | stdio JSON-RPC server for VS Code / Zed / JetBrains | `acp_adapter/server.py` | ✓ GO |
| C-16 | **DASHBOARD** | Flask REST API + SSE + web UI for ops and monitoring | `dashboard/app.py` | ✓ GO |

---

## 4. System Architecture

The platform runs as a single Docker container exposing one port (5000 for the dashboard). The gateway and dashboard run as concurrent background threads managed by the entrypoint script. All state is stored in a named Docker volume.

```
┌──────────────────────────────────────────────────────────────────────┐
│                         DOCKER CONTAINER                             │
│                                                                      │
│  ┌─────────────────┐   ┌─────────────────┐   ┌──────────────────┐  │
│  │   DASHBOARD     │   │   AGENT LOOP    │   │  GATEWAY RUNNER  │  │
│  │  Flask :5000    │   │  run_agent.py   │   │  gateway/run.py  │  │
│  │  SSE + REST API │   │  (per session)  │   │  (per platform)  │  │
│  └────────┬────────┘   └────────┬────────┘   └────────┬─────────┘  │
│           │                     │                     │            │
│           └─────────────────────┼─────────────────────┘            │
│                                 │                                   │
│                    ┌────────────▼───────────┐                      │
│                    │     SESSION_STORE       │                      │
│                    │  SQLite WAL + FTS5      │                      │
│                    │  /data/juan_state.db    │                      │
│                    └─────────────────────────┘                      │
│                                                                      │
│  Volumes: juan-data (rw) /data    ·    config (ro) /config          │
└──────────────────────────────────────────────────────────────────────┘
```

---

## 5. Data Flows

### 5.1 Inbound Message Flow (Gateway Mode)

```
Platform SDK (Telegram, Discord, etc.)
    │
    ▼
C-06  PLATFORM_ADAPTOR.normalize(raw) ─────────────────► MessageEvent
    │
    ▼
C-05  GATEWAY_RUNNER._handle_event(event)
    │
    ├── C-10  AUTHORIZATION.check() ──── DENY ─────────► error reply
    │
    ├── Level-1 guard: per-session threading.Lock()
    │
    ├── Level-2 guard: slash command?
    │       /approve /deny /stop  ──► C-13 APPROVAL_GATE.receive_decision()
    │       /help /reset          ──► inline handler
    │
    └── C-01  AGENT_LOOP.run(message, session_id)
                │
                ├── C-07  PROMPT_BUILDER.build()
                ├── C-14  ITERATION_BUDGET.check()
                ├── C-08  CONTEXT_COMPRESSOR.compress()  ← if near limit
                ├── C-02  PROVIDER_RUNTIME.call_provider()
                ├── C-03  TOOL_REGISTRY.dispatch()        ← if tool_calls
                │         └── C-13 APPROVAL_GATE.request() ← if dangerous
                ├── C-04  SESSION_STORE.append_message()
                └── C-09  MEMORY_MANAGER.flush()          ← on turn end
```

### 5.2 Dashboard Task Submission Flow

```
Browser  POST /api/tasks  {"message": "..."}
    │
    ▼
C-16  Dashboard API  →  background thread
    │
    ▼
C-01  AGENT_LOOP.run(message, session_id="dashboard:<uuid>")
    │
    ▼
Result stored in C-04 SESSION_STORE
    │
    ▼
Browser  GET /api/tasks/<task_id>  →  {status, response, usage}
```

### 5.3 Cron Scheduler Flow

```
C-12  CronScheduler._tick()  (every 10 seconds)
    │
    ▼
For each due job:
    ├── session_id = "cron:<job_id>:<run_id>"  (isolated — no gateway history)
    ├── C-01  AGENT_LOOP.run(job.task, session_id)
    └── delivery_fn(platform, chat_id, response)
```

---

## 6. Adaptor Layer

No component imports another directly. All inter-component communication passes through typed adaptors. This enforces the contract boundary and makes each component independently replaceable.

| Adaptor | Bridges | Protocol |
|---------|---------|----------|
| ProviderAdaptor | AGENT_LOOP ↔ PROVIDER_RUNTIME | Python function call + exponential retry wrapper |
| ToolAdaptor | AGENT_LOOP ↔ TOOL_REGISTRY | `ThreadPoolExecutor` concurrent dispatch |
| SessionAdaptor | Multiple ↔ SESSION_STORE | SQLite WAL, `BEGIN IMMEDIATE`, jitter retry |
| TerminalAdaptor | TOOL_REGISTRY ↔ terminal backends | subprocess / Docker SDK / SSH |
| BrowserAdaptor | TOOL_REGISTRY ↔ browser backends | Playwright / Puppeteer / CDP |
| MCPAdaptor | TOOL_REGISTRY ↔ MCP servers | stdio / HTTP JSON-RPC |
| PlatformAdaptor | GATEWAY_RUNNER ↔ platform SDKs | WebSocket / REST long-poll / Webhooks |
| GatewayAdaptor | CRON + APPROVAL ↔ GATEWAY_RUNNER | Internal `threading.Event` + callback |
| AgentAdaptor | GATEWAY + PLUGINS ↔ AGENT_LOOP | Background thread + interrupt event |
| MemoryAdaptor | MEMORY_MANAGER ↔ plugin providers | Plugin hook interface (`on_flush`) |
| PluginAdaptor | PLUGIN_MANAGER ↔ installed plugins | pip entry points / `importlib` discovery |

---

## 7. Security Design

### 7.1 Authorization Layers (C-10)

Five layers are evaluated in order. The first matching layer grants or denies access. Any exception at any layer causes immediate **DENY** (fail-safe).

| Layer | Method | Description |
|-------|--------|-------------|
| 1 | `platform_allow_all` | Per-platform flag for open access. Development/testing only; must not be set in production. |
| 2 | `allowlist` | Explicit `user_id` and `chat_id` allowlist in `juan_auth.json`. Checked before pairing. |
| 3 | `dm_pairing` | User sends a secret `pair_code` in a DM to self-register. Pairing persisted to `juan_pairing.json`. |
| 4 | `global_allow_all` | Global open-access flag. Lower priority than allowlist; for simple single-owner deployments. |
| 5 | `denied` | **Default.** Any user not matched by layers 1–4 is denied with no error detail exposed. |

### 7.2 Approval Gate Risk Model (C-13)

| Risk Level | Pattern Examples | Default Action |
|------------|-----------------|----------------|
| `critical` | `rm -rf`, `format disk`, `drop database`, `curl \| sh`, `dd if=` | Block; require `/approve` |
| `high` | `sudo`, `shutdown`, `reboot`, `kill -9` | Block; require `/approve` |
| `medium` | `chmod 777` | Block; require `/approve` (configurable threshold) |
| `low` | `ls`, `cat`, `echo`, `python script.py` | Execute directly; no approval needed |

**Timeout behaviour:** If no `/approve` or `/deny` is received within 300 seconds, the gate defaults to **DENY** — it never fails open.

**Bypass commands:** `/approve`, `/deny`, `/stop` are handled inline by the gateway. They never pass through a background queue, eliminating race conditions.

### 7.3 Docker Security

- Container runs as non-root user `juan` (UID 1000)
- Secrets supplied via environment variables; never baked into image layers
- Config volume mounted read-only (`:ro`) in production
- Only port 5000 exposed; all platform connections are outbound
- Production deployments must use a reverse proxy (nginx / Caddy) with TLS

---

## 8. Observability

Every component emits structured JSON trace events to stdout on every significant operation. The dashboard captures these via a logging handler and streams them to the browser via SSE.

```json
{
  "timestamp": 1748123456.789,
  "phase":     "runtime",
  "component": "AGENT_LOOP",
  "event":     "turn_end",
  "session_id": "telegram:123456:789012",
  "end_reason": "stop",
  "iterations_used": 3,
  "total_tokens": 2840,
  "duration_ms": 4120
}
```

### Key Events by Component

| Component | Key Events |
|-----------|-----------|
| AGENT_LOOP | `turn_start`, `tool_dispatch`, `tool_result`, `token_count`, `compression_triggered`, `turn_end` |
| PROVIDER_RUNTIME | `provider_selected`, `fallback_activated`, `retry_n`, `latency_ms` |
| TOOL_REGISTRY | `tool_resolved`, `approval_required`, `exec_start`, `exec_end`, `error_caught` |
| SESSION_STORE | `db_write`, `db_read`, `fts_query`, `contention_retry`, `checkpoint`, `migration_run` |
| GATEWAY_RUNNER | `message_received`, `auth_check`, `session_key_built`, `guard_level1`, `guard_level2`, `agent_dispatched`, `response_sent` |
| APPROVAL_GATE | `dangerous_detected`, `approval_requested`, `decision_received`, `bypass_command` |
| CRON_SCHEDULER | `tick`, `job_due`, `agent_fired`, `delivery_sent`, `job_error` |
| CONTEXT_COMPRESSOR | `compression_start`, `flush_triggered`, `summary_generated`, `lineage_created`, `compression_end` |

### Filtering Logs

```bash
# Agent turns only
docker compose logs juan 2>&1 | grep '^{' | jq 'select(.event == "turn_end")'

# Tool executions
docker compose logs juan 2>&1 | grep '^{' | jq 'select(.component == "TOOL_REGISTRY")'

# Errors only
docker compose logs juan 2>&1 | grep '^{' | jq 'select(.level == "ERROR")'

# By session
docker compose logs juan 2>&1 | grep '^{' | jq 'select(.session_id == "telegram:123:456")'
```

---

## 9. Persistence

### 9.1 SQLite Schema v11

```sql
-- Session metadata
CREATE TABLE sessions (
    session_id    TEXT PRIMARY KEY,
    parent_id     TEXT,
    platform      TEXT,
    chat_id       TEXT,
    user_id       TEXT,
    model         TEXT,
    created_at    REAL,
    ended_at      REAL,
    input_tokens  INTEGER DEFAULT 0,
    output_tokens INTEGER DEFAULT 0,
    cache_tokens  INTEGER DEFAULT 0
);

-- Full conversation history
CREATE TABLE messages (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id   TEXT NOT NULL REFERENCES sessions(session_id),
    role         TEXT NOT NULL,
    content      TEXT NOT NULL,
    tool_call_id TEXT,
    tool_name    TEXT,
    created_at   REAL NOT NULL
);

-- FTS5 virtual table (auto-populated via INSERT trigger)
CREATE VIRTUAL TABLE messages_fts USING fts5(
    content, session_id UNINDEXED,
    content='messages', content_rowid='id'
);

-- Schema version tracking
CREATE TABLE _meta (key TEXT PRIMARY KEY, value TEXT);
```

**WAL mode** enables concurrent reads during writes. Write contention is handled with jitter-backoff retry (up to 15 attempts). FTS5 provides full-text search with automatic fallback to `LIKE` if FTS fails.

### 9.2 File-based Memory

| File | Content | Manager |
|------|---------|---------|
| `SOUL.md` | Agent personality — base system prompt. Read-only at runtime. | PROMPT_BUILDER (read); operator (write) |
| `MEMORY.md` | Agent's evolving cross-session knowledge. | MEMORY_MANAGER (read/write) |
| `USER.md` | User profile and preferences, updated per conversation. | MEMORY_MANAGER (read/write) |
| `juan_auth.json` | Authorization config: `pair_code`, `allowlist`, platform flags. | AUTHORIZATION (read) |
| `juan_pairing.json` | Self-registered DM pairs. Auto-created on first pairing. | AUTHORIZATION (read/write) |

---

## 10. Failure Modes & Recovery

| Component | Failure | Recovery Strategy |
|-----------|---------|-------------------|
| PROVIDER_RUNTIME | HTTP 5xx / timeout | Exponential backoff × 3, then one-shot fallback model |
| PROVIDER_RUNTIME | AUTH_FAIL (401/403) | No retry — immediately activate fallback model |
| SESSION_STORE | WAL lock timeout | Jitter retry × 15 with exponential backoff; emit `LOCK_TIMEOUT` |
| TOOL_REGISTRY | Tool raises exception | Per-tool try-catch; error stored in result; sibling tools continue |
| APPROVAL_GATE | Timeout (300s) | Default **DENY** — fail-safe, never fail-open |
| CONTEXT_COMPRESSOR | Summary LLM call fails | Fallback to first-100-chars truncation per turn |
| AUTHORIZATION | Any exception | Default **DENY** — fail-safe, error logged |
| GATEWAY_RUNNER | Agent loop crashes | Error reply sent to user; session lock released; loop continues |
| PLUGIN_MANAGER | Plugin raises on load | Skip that plugin; log `PLUGIN_LOAD_FAIL`; continue loading others |
| MEMORY_MANAGER | Write fails | Retry once; if second attempt fails, log and continue without crashing |

---

## 11. Implementation Sequence

Components were implemented in dependency order to allow each to be tested in isolation before being integrated:

| # | Component | File | Status |
|---|-----------|------|--------|
| 1 | SESSION_STORE | `juan_state.py` | ✓ Implemented |
| 2 | PROVIDER_RUNTIME | `juan_cli/runtime_provider.py` | ✓ Implemented |
| 3 | ITERATION_BUDGET | `agent/iteration_budget.py` | ✓ Implemented |
| 4 | PROMPT_BUILDER | `agent/prompt_builder.py` | ✓ Implemented |
| 5 | AGENT_LOOP | `agent/run_agent.py` | ✓ Implemented |
| 6 | TOOL_REGISTRY | `tools/registry.py` | ✓ Implemented |
| 7 | APPROVAL_GATE | `tools/approval.py` | ✓ Implemented |
| 8 | CONTEXT_COMPRESSOR | `agent/context_compressor.py` | ✓ Implemented |
| 9 | MEMORY_MANAGER | `agent/memory_manager.py` | ✓ Implemented |
| 10 | AUTHORIZATION | `gateway/authorization.py` | ✓ Implemented |
| 11 | PLATFORM_ADAPTOR | `gateway/platforms/base.py` | ✓ Implemented (Telegram) |
| 12 | GATEWAY_RUNNER | `gateway/run.py` | ✓ Implemented |
| 13 | PLUGIN_MANAGER | `juan_cli/plugins.py` | ✓ Implemented |
| 14 | CRON_SCHEDULER | `cron/scheduler.py` | ✓ Implemented |
| 15 | ACP_SERVER | `acp_adapter/server.py` | ✓ Implemented |
| 16 | DASHBOARD | `dashboard/app.py + templates/` | ✓ Implemented |

---

*Project Juan — Solution Design SD-001 v1.0*  
*© 2026 Project Juan. All rights reserved.*
