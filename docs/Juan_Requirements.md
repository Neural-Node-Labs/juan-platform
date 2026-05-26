# Juan Platform — Requirements Specification

**Document ID:** REQ-001  
**Version:** 1.0  
**Date:** 25 May 2026  
**Status:** Phase II — Implemented

---

## Table of Contents

1. [Introduction](#1-introduction)
2. [Functional Requirements](#2-functional-requirements)
3. [Non-Functional Requirements](#3-non-functional-requirements)
4. [Acceptance Criteria](#4-acceptance-criteria)
5. [Constraints & Assumptions](#5-constraints--assumptions)
6. [Out of Scope](#6-out-of-scope)

---

## 1. Introduction

This document defines the complete functional, non-functional, platform, and operational requirements for Project Juan — an autonomous AI agent platform designed to be self-hosted, provider-agnostic, and deployable as a single Docker stack.

Requirements are classified by priority (**MUST** / **SHOULD** / **MAY**) and mapped to the 16 implementation components identified in the Solution Design.

### 1.1 Scope

Project Juan provides:

- A persistent autonomous agent loop capable of real tool execution over multiple iterations
- Multi-platform messaging integration (14+ platforms) via a normalised adaptor layer
- A provider-agnostic LLM runtime supporting 18+ models with automatic fallback
- A web-based operations dashboard for monitoring, task submission, and cron management
- Persistent session storage with full-text search
- IDE integration via the Agent Communication Protocol (ACP) over stdio JSON-RPC

### 1.2 Definitions

| Term | Definition |
|------|------------|
| **Agent turn** | One complete cycle: system prompt assembly → LLM call → optional tool execution → response delivery |
| **Session** | A persistent conversation thread identified by `platform:chat_id:user_id` |
| **Provider** | An LLM API service (Anthropic, OpenAI, Groq, etc.) |
| **Tool** | A registered callable that the agent may invoke during a turn |
| **Iteration** | One LLM call + optional tool dispatch within a single agent turn |
| **Platform** | A messaging service connected via a PlatformAdaptor (Telegram, Discord, etc.) |
| **SOUL.md** | Markdown file defining the agent's personality and base instructions |
| **Cron job** | A scheduled task that fires the agent loop at a defined interval |

---

## 2. Functional Requirements

### 2.1 Agent Loop (C-01)

| ID | Priority | Comp | Requirement |
|----|----------|------|-------------|
| F-01 | **MUST** | C-01 | The agent loop SHALL execute a complete autonomous turn comprising: prompt assembly, LLM call, optional tool dispatch, session storage, and memory flush. |
| F-02 | **MUST** | C-01 | The agent loop SHALL support up to 90 consecutive LLM iterations per turn, configurable via `MAX_ITERATIONS`. |
| F-03 | **MUST** | C-01 | The agent loop SHALL emit `warning_at_80pct` when 80% of the iteration budget is consumed, and SHALL halt with `summary_triggered` at exhaustion. |
| F-04 | **MUST** | C-01 | The agent loop SHALL reconstruct conversation history from SESSION_STORE on each turn. |
| F-05 | SHOULD | C-01 | The agent loop SHOULD support subagent mode with a separate 50-iteration budget. |

### 2.2 LLM Provider Runtime (C-02)

| ID | Priority | Comp | Requirement |
|----|----------|------|-------------|
| F-06 | **MUST** | C-02 | The provider runtime SHALL support Anthropic (claude-\*), OpenAI (gpt-\*), Groq, Mistral, Cohere, Together, Fireworks, DeepSeek, xAI, Gemini, Ollama, and LM Studio. |
| F-07 | **MUST** | C-02 | The provider runtime SHALL normalise all provider responses to a common schema: `content`, `role`, `finish_reason`, `tool_calls`, `usage`. |
| F-08 | **MUST** | C-02 | On HTTP 5xx or timeout, the runtime SHALL retry with exponential backoff (max 3 attempts), then activate a one-shot fallback model if configured. |
| F-09 | **MUST** | C-02 | On `AUTH_FAIL` (401/403), the runtime SHALL NOT retry and SHALL immediately activate fallback. |
| F-10 | **MUST** | C-02 | API credentials SHALL be resolved from environment variables only; never hardcoded or logged. |

### 2.3 Tool Registry & Execution (C-03, C-13)

| ID | Priority | Comp | Requirement |
|----|----------|------|-------------|
| F-11 | **MUST** | C-03 | The tool registry SHALL support registration of arbitrary Python callables with Anthropic-format JSON schemas. |
| F-12 | **MUST** | C-03 | When multiple tool calls are returned in a single LLM response, they SHALL be dispatched concurrently via `ThreadPoolExecutor`. |
| F-13 | **MUST** | C-03 | A failure in one concurrent tool call SHALL NOT cancel sibling calls. |
| F-14 | **MUST** | C-13 | Before executing any tool call with risk level ≥ medium, the system SHALL request human approval via the gateway. |
| F-15 | **MUST** | C-13 | If no human approval is received within 300 seconds, the system SHALL default to DENY (fail-safe). |
| F-16 | **MUST** | C-13 | Risk levels SHALL be detected by regex pattern matching on tool name and argument text: `low` / `medium` / `high` / `critical`. |
| F-17 | SHOULD | C-03 | Individual tool calls SHOULD time out after 120 seconds. |

### 2.4 Session Storage (C-04)

| ID | Priority | Comp | Requirement |
|----|----------|------|-------------|
| F-18 | **MUST** | C-04 | All session metadata, message history, and token usage SHALL be persisted in SQLite with WAL mode enabled. |
| F-19 | **MUST** | C-04 | The schema SHALL include a FTS5 virtual table on message content with fallback to LIKE search. |
| F-20 | **MUST** | C-04 | Write contention SHALL be handled with jitter-backoff retry (up to 15 attempts) using `BEGIN IMMEDIATE` transactions. |
| F-21 | **MUST** | C-04 | The schema version SHALL be tracked in a `_meta` table and migrations applied automatically on startup. |
| F-22 | SHOULD | C-04 | A WAL checkpoint SHOULD be triggered after each session end. |

### 2.5 Gateway & Platform Integration (C-05, C-06, C-10)

| ID | Priority | Comp | Requirement |
|----|----------|------|-------------|
| F-23 | **MUST** | C-05 | The gateway SHALL route inbound messages through: authorization check → slash-command intercept → agent dispatch. |
| F-24 | **MUST** | C-05 | A per-session threading lock SHALL prevent concurrent agent turns for the same session. |
| F-25 | **MUST** | C-06 | All platform events SHALL be normalised to a uniform `MessageEvent` schema before processing. |
| F-26 | **MUST** | C-06 | The Telegram platform adaptor SHALL be implemented using long-polling. |
| F-27 | **MUST** | C-10 | Access control SHALL evaluate five layers in order: `platform_allow_all` → `allowlist` → `dm_pairing` → `global_allow_all` → `denied`. |
| F-28 | **MUST** | C-10 | On any authorization exception, the system SHALL default to DENY. |
| F-29 | SHOULD | C-05 | Built-in slash commands `/approve`, `/deny`, `/stop`, `/help`, `/reset` SHOULD be handled inline without agent invocation. |

### 2.6 Memory, Prompts & Context (C-07, C-08, C-09)

| ID | Priority | Comp | Requirement |
|----|----------|------|-------------|
| F-30 | **MUST** | C-07 | The prompt builder SHALL assemble the system prompt from `SOUL.md`, `MEMORY.md`, `USER.md`, tool schemas, skills, and context files. |
| F-31 | **MUST** | C-07 | The system prompt SHALL be frozen at turn start and rebuilt only on `/model` command. |
| F-32 | SHOULD | C-07 | Anthropic prompt caching markers SHOULD be applied to static sections ≥ 1,024 tokens. |
| F-33 | **MUST** | C-08 | When conversation history exceeds 75% of the model context limit, compression SHALL be triggered before the next LLM call. |
| F-34 | **MUST** | C-08 | Memory MUST be flushed before compression begins. |
| F-35 | **MUST** | C-08 | Compression SHALL preserve the last 20 messages verbatim and MUST NOT split tool call / tool result pairs. |
| F-36 | **MUST** | C-09 | `MEMORY.md` and `USER.md` writes SHALL use atomic file replacement (write-then-rename). |
| F-37 | **MUST** | C-09 | Memory flush SHALL be retried once on write failure, then continue without throwing. |

### 2.7 Scheduling, Plugins & IDE (C-11, C-12, C-15)

| ID | Priority | Comp | Requirement |
|----|----------|------|-------------|
| F-38 | **MUST** | C-12 | The cron scheduler SHALL support schedules expressed as: `every N minutes/hours/days`, `@hourly/@daily/@weekly`, and ISO 8601 duration strings. |
| F-39 | **MUST** | C-12 | Each cron job SHALL run in an isolated session distinct from all gateway sessions. |
| F-40 | SHOULD | C-12 | Failed cron jobs SHOULD be retried once before emitting `AGENT_FIRE_FAIL`. |
| F-41 | SHOULD | C-11 | The plugin manager SHOULD discover plugins from three sources: pip entry points, `~/.juan/plugins/`, and `./plugins/`. |
| F-42 | SHOULD | C-11 | A plugin that fails to load SHOULD be skipped; other plugins SHALL continue to load. |
| F-43 | SHOULD | C-15 | The ACP server SHOULD implement JSON-RPC 2.0 over stdio supporting: `initialize`, `chat/message`, `session/new`, `session/list`, `ping`. |

### 2.8 Dashboard (C-16)

| ID | Priority | Comp | Requirement |
|----|----------|------|-------------|
| F-44 | **MUST** | C-16 | The dashboard SHALL expose a REST API on port 5000 serving: `/api/status`, `/api/events`, `/api/sessions`, `/api/tasks`, `/api/cron`, `/api/config`. |
| F-45 | **MUST** | C-16 | The `/api/events/stream` endpoint SHALL deliver structured log events via Server-Sent Events (SSE). |
| F-46 | **MUST** | C-16 | Task submissions via `POST /api/tasks` SHALL execute in an isolated `dashboard:` session and return a `task_id` for polling. |
| F-47 | SHOULD | C-16 | The dashboard UI SHOULD provide: system overview, real-time activity feed, task submission, session browser, cron management, and settings view. |
| F-48 | SHOULD | C-16 | API key values SHOULD be masked in `/api/config` responses (first 4 + last 4 characters shown). |

---

## 3. Non-Functional Requirements

### 3.1 Performance

| ID | Priority | Requirement |
|----|----------|-------------|
| NF-01 | **MUST** | The system SHALL handle at least 10 concurrent gateway sessions without degradation on a single-core host. |
| NF-02 | **MUST** | SQLite write contention SHALL resolve within 15 retry attempts before raising `LOCK_TIMEOUT`. |
| NF-03 | SHOULD | LLM API calls SHOULD complete within 120 seconds before timing out. |
| NF-04 | SHOULD | The dashboard SHALL return `/api/status` in under 200ms under normal load. |

### 3.2 Security

| ID | Priority | Requirement |
|----|----------|-------------|
| NF-05 | **MUST** | The container process SHALL run as a non-root user (`juan`, UID 1000). |
| NF-06 | **MUST** | All API credentials SHALL be supplied via environment variables; never embedded in source code or Docker image layers. |
| NF-07 | **MUST** | The authorization layer SHALL default to DENY on any exception. |
| NF-08 | **MUST** | The approval gate SHALL default to DENY on timeout. |
| NF-09 | **MUST** | Config volumes SHALL be mounted read-only in production. |
| NF-10 | SHOULD | In production, the dashboard SHOULD be protected by a password (`DASHBOARD_PASSWORD`) and placed behind a TLS-terminating reverse proxy. |

### 3.3 Reliability & Observability

| ID | Priority | Requirement |
|----|----------|-------------|
| NF-11 | **MUST** | All 16 components SHALL emit structured JSON trace events to stdout on every significant operation. |
| NF-12 | **MUST** | No component failure SHALL crash the top-level process; all exceptions SHALL be caught and logged with `component` and `error_code` fields. |
| NF-13 | **MUST** | The Docker container SHALL expose a health check on `GET /api/status` returning HTTP 200. |
| NF-14 | SHOULD | Log events SHOULD be filterable by component, event type, and `session_id` using standard tools (`jq`, `grep`). |

### 3.4 Portability & Operations

| ID | Priority | Requirement |
|----|----------|-------------|
| NF-15 | **MUST** | The platform SHALL be deployable with a single `docker compose up -d` command. |
| NF-16 | **MUST** | All persistent data SHALL be stored in a named Docker volume (`juan-data`). |
| NF-17 | **MUST** | The Python runtime SHALL require no packages beyond `flask`, `flask-cors`, and the standard library. |
| NF-18 | SHOULD | The platform SHOULD run without Docker using only `python main.py --mode repl`. |

---

## 4. Acceptance Criteria

The platform is accepted when all of the following pass:

| AC-ID | Criterion | Test Method | Status |
|-------|-----------|-------------|--------|
| AC-01 | All 16 component unit tests pass with no errors | Automated | ✓ PASS |
| AC-02 | SESSION_STORE creates, reads, FTS-searches, and ends a session in `:memory:` mode | Automated | ✓ PASS |
| AC-03 | ITERATION_BUDGET warns at 80% and blocks at 100% | Automated | ✓ PASS |
| AC-04 | TOOL_REGISTRY dispatches concurrent tools; one failure does not abort siblings | Automated | ✓ PASS |
| AC-05 | APPROVAL_GATE detects `rm -rf /` as critical and `ls` as low | Automated | ✓ PASS |
| AC-06 | AUTHORIZATION denies unknown users by default | Automated | ✓ PASS |
| AC-07 | ACP_SERVER handles `initialize` and `ping` over stdio JSON-RPC | Automated | ✓ PASS |
| AC-08 | DASHBOARD `/api/status`, `/api/events`, `/api/config` return HTTP 200 | Automated | ✓ PASS |
| AC-09 | CRON_SCHEDULER parses `every 5 minutes`, `@hourly`, `PT15M` correctly | Automated | ✓ PASS |
| AC-10 | Docker image builds without errors; container starts and dashboard responds at `:5000` | Manual | Pending |
| AC-11 | Telegram bot receives a message and the agent responds within 30 seconds | Manual | Pending |
| AC-12 | Submitting a task via the dashboard returns a response within 60 seconds | Manual | Pending |

---

## 5. Constraints & Assumptions

- Python 3.11 or later is available in the runtime environment
- The host has outbound HTTPS access to LLM provider APIs
- At least one valid LLM API key is provided at startup
- SQLite 3.38+ is available (required for FTS5 support); Python 3.11 ships with SQLite 3.39+
- For Telegram integration, a bot token is required from @BotFather
- In-memory (`:memory:`) SQLite mode is supported for testing only; production deployments use a file-backed DB on a mounted volume
- The platform does not guarantee message delivery ordering across concurrent sessions

---

## 6. Out of Scope

- Multi-node distributed deployment (single-host only in v1.0)
- PostgreSQL or other database backends (SQLite only)
- Real-time streaming of LLM token output to end users
- Built-in web scraping, browser automation, or code execution tools (these are registered by operators)
- OAuth or SSO for the dashboard (password protection only in v1.0)

---

*Project Juan — Requirements Specification REQ-001 v1.0*  
*© 2026 Project Juan. All rights reserved.*
