---
name: cbd-development
description: Component-Based Design (CBD) Interface-First methodology for designing and building software systems. Use this skill whenever the user wants to architect, design, or build a system, platform, API, or application using a structured component methodology — especially when they mention components, interfaces, contracts, schemas, system design, software architecture, designing before coding, or building something with multiple interacting parts. Also trigger for requests like "design this system before we build it", "define the contracts between services", "spec out the components", "create an architecture for X", or "how should we structure this platform". This skill ensures all component contracts (input schemas, output schemas, error codes, trace events) are fully specified before any implementation begins.
---

# Component-Based Design (CBD) — Interface-First

A methodology for designing and building systems where **every component's contract is fully specified before any code is written**.

This produces systems that are independently testable, safely composable, and easy to debug — because every interface is an explicit, agreed-upon contract, not an assumption.

---

## When to use this skill

Use CBD when you are:
- Designing a new platform, agent, API, or multi-service system
- Breaking a large system into implementable parts
- Specifying what components should do before writing them
- Creating something where multiple pieces need to interoperate reliably
- Working on a system that needs to be testable and maintainable

---

## The CBD Interface-First Sequence

Every component follows these six steps in order. Do not start implementing until Steps 1–5 are complete.

```
1. NAME & RESPONSIBILITY  →  One sentence, one job
2. IN SCHEMA              →  Every input field, type, and constraint
3. OUT SCHEMA             →  Every output field, including success and error shapes
4. ERROR CODES            →  Every named failure mode with its recovery strategy
5. TRACE LOG              →  Every structured event this component must emit
6. ADAPTOR DEFINITION     →  How it communicates with other components
────────────────────────────────────────────────
    ↓  Only now:
7. IMPLEMENT              →  Code against the contract
8. TEST                   →  Validate contract boundaries, not internals
```

The reason for this order: once you know what a component promises (IN, OUT, ERRORS), you can write acceptance tests for it independently. Once you know its adaptor, you can wire it to other components without coupling. Implementation becomes filling in a box whose edges are already defined.

---

## Step-by-step guide

### Step 1 — Name & Single Responsibility

Give the component a short, descriptive ID (e.g. `C-04 SESSION_STORE`) and write exactly one sentence defining its responsibility. If you need two sentences, the component has two responsibilities — split it.

**Good:** `SESSION_STORE persists conversation history and token usage to SQLite with WAL mode.`  
**Bad:** `SESSION_STORE persists conversations and also compresses context and manages memory files.`

Assign a risk level: ✓ GO (well-understood, low blast radius) or ⚠ CAUTION (interacts with external services, data, or dangerous operations).

### Step 2 — IN Schema

Define every input the component accepts. For each field: name, type, required/optional, constraints, and what it means.

```
IN SCHEMA — SESSION_STORE
  operation  : enum[create|append_message|get_messages|search|end|prune]  required
  session_id : string, max 256 chars                                        required
  payload    : object, shape depends on operation                           optional
    ├── role     : enum[user|assistant|tool]   (append_message only)
    ├── content  : string, max 1MB             (append_message only)
    └── query    : string                      (search only)
```

### Step 3 — OUT Schema

Define every field the component returns — for both success and failure cases. No implicit fields. No "you know what this means".

```
OUT SCHEMA — SESSION_STORE
  ok          : boolean
  messages    : array[{role, content, created_at}]   (get_messages only)
  search_results : array[{session_id, content, role}] (search only)

  on error →
  error_code  : string  (see Error Codes)
  operation   : string
  retries     : integer
  message     : string
```

### Step 4 — Error Codes

Name every failure mode. Each error code gets: a trigger condition, whether it is retryable, and the expected recovery strategy.

```
ERROR CODES — SESSION_STORE
  MIGRATION_FAIL   →  Schema migration failed on startup. NOT retryable. Fatal.
  WRITE_FAIL       →  INSERT/UPDATE failed. NOT retryable. Log and surface.
  READ_FAIL        →  SELECT failed. NOT retryable. Log and surface.
  LOCK_TIMEOUT     →  WAL contention exceeded 15 retries. NOT retryable. Raise.
```

**Default-deny principle:** For any component that controls access or authorization, the default on any exception or unrecognised input must be the safe action (deny, reject, skip). Never default to the permissive action.

### Step 5 — Trace Log

Define every structured event this component must emit. Each event has a component name, event name, and a fixed set of fields. This drives observability, dashboards, and debugging.

```
TRACE EVENTS — SESSION_STORE
  component: "SESSION_STORE"

  db_write         →  {operation, session_id, rows_affected, duration_ms}
  db_read          →  {operation, session_id, rows_returned, duration_ms}
  fts_query        →  {query_text, results_count, duration_ms}
  contention_retry →  {attempt, jitter_ms}
  checkpoint       →  {wal_frames, duration_ms}
  migration_run    →  {from_version, to_version, duration_ms}
```

All events share: `timestamp`, `phase: "runtime"`, `component`, `event`, `session_id?`, `duration_ms?`.

### Step 6 — Adaptor Definition

Define how this component talks to other components. Adaptors are the boundary layer — no component should import another directly.

```
ADAPTORS — SESSION_STORE
  Exposes:  SessionAdaptor (called by AGENT_LOOP, GATEWAY_RUNNER, DASHBOARD)
  Protocol: Python method call → dict result
  Contract: All callers use .execute(operation, session_id, payload) → dict
  Error:    SessionStoreError raised, never swallowed by callers
```

Adaptors enforce the rule: **components talk to contracts, not to each other.**

---

## Component Map template

Use this table to track all components in the system before implementation starts:

| ID | Component | Responsibility | Adaptor | Risk | Status |
|----|-----------|----------------|---------|------|--------|
| C-01 | NAME | One-sentence job | AdaptorName | ✓ GO / ⚠ CAUTION | 🔴 Spec / 🟡 Ready / 🟢 Built |

Fill every row before any row reaches 🟢 Built.

---

## Implementation sequence rules

Once all contracts are specified, implement in this order:

1. **Leaf components first** — components with no dependencies on other custom components (e.g. session store, logger, config loader). These can be tested in complete isolation.
2. **Core engine next** — components that depend only on leaves (e.g. provider runtime, tool registry).
3. **Orchestrators last** — components that wire everything together (e.g. agent loop, gateway runner).

This order means every component you implement has fully-tested dependencies underneath it. You never implement against an interface that isn't already working.

---

## Failure modes are first-class citizens

Every component must define what happens when it breaks — before implementation. The four patterns:

| Pattern | When to use | Example |
|---------|-------------|---------|
| **Retry with backoff** | Transient external failures | LLM API 5xx, DB lock contention |
| **Fallback** | One strategy fails, try another | Primary model → fallback model |
| **Skip and continue** | One item fails, others must not | Plugin load failure, tool error |
| **Default-deny** | Authorization or safety gates | Auth check throws → deny access |

Write these into the error codes before writing any implementation code.

---

## Worked example — specifying a single component

See [`references/worked-example.md`](references/worked-example.md) for a complete walk-through of all six steps applied to a real component (APPROVAL_GATE), including the full contract, implementation sketch, and acceptance tests.

---

## Delivering a CBD design

When the user asks you to design a system using CBD, produce:

1. **Component Map** — table of all components, IDs, responsibilities, and risk levels
2. **Implementation Sequence** — ordered list with reasoning
3. **Component Contracts** — full IN/OUT/ERRORS/TRACE for each component (use the reference template)
4. **Adaptor Layer** — table of all adaptors, what they bridge, and their protocol

For large systems (10+ components), present the Component Map and Sequence first and confirm with the user before writing all contracts — contracts for a 15-component system are long and you want to be sure the map is right.

For small systems (≤ 5 components), you can write all contracts in one pass.

---

## Reference files

- [`references/worked-example.md`](references/worked-example.md) — Complete CBD specification for APPROVAL_GATE, from contract through acceptance tests
- [`references/contract-template.md`](references/contract-template.md) — Copy-paste template for a single component's full contract
- [`references/patterns.md`](references/patterns.md) — Common patterns: retry, fallback, default-deny, fan-out, compression trigger, context guard
