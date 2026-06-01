# CBD Contract Template

Copy this template for each component. Fill every section before writing any implementation code.

---

## Component: `C-XX  COMPONENT_NAME`

**Responsibility:**  
_(One sentence. One job. If you need two sentences, split the component.)_

**Risk level:** ✓ GO / ⚠ CAUTION  
_(GO: well-understood, low blast radius. CAUTION: touches external services, auth, data, or dangerous operations.)_

---

## IN Schema

Define every input field. For each: name, type, whether it's required, any constraints, and what it means. Use indentation to show nested structure.

```
IN SCHEMA — COMPONENT_NAME

  operation   : enum[op1|op2|op3]       required
  session_id  : string, max 256 chars   required
  payload     : object                  optional
    ├── field_a : string                (op1 only)
    ├── field_b : integer, 1–1000       (op1 and op2)
    └── field_c : boolean               default: false
```

---

## OUT Schema

Define every output field for both the success case and the failure case.

```
OUT SCHEMA — COMPONENT_NAME

  Success:
    ok          : boolean
    result      : string | null
    items       : array[{id, name, value}]   (op2 only)
    metadata    : {created_at: float, duration_ms: integer}

  Failure (raises ComponentError):
    error_code  : string   (see Error Codes below)
    operation   : string
    message     : string
    retries     : integer  (if applicable)
```

---

## Error Codes

Name every failure mode. State whether it is retryable and what the caller should do.

```
ERROR CODES — COMPONENT_NAME

  CODE_ONE    →  When this happens. Retryable: YES/NO. Recovery: what caller does.
  CODE_TWO    →  When this happens. Retryable: YES/NO. Recovery: what caller does.
  CODE_THREE  →  When this happens. Retryable: YES/NO. Recovery: what caller does.
```

**Default-deny rule:** If this component controls access, authorisation, or safety, state explicitly what the safe default is on any unhandled exception. Write it here.

---

## Trace Log

Define every structured event this component must emit. Consistency matters — use the same field names across all events.

```
TRACE EVENTS — COMPONENT_NAME
  component: "COMPONENT_NAME"

  event_one   →  {session_id, field_a, field_b, duration_ms}
  event_two   →  {session_id, result_count, duration_ms}
  event_error →  {session_id, error_code, message}
```

All events automatically include: `timestamp`, `phase: "runtime"`, `component`, `event`.

---

## Adaptor Definition

Define how this component communicates with others. No component imports another directly — only adaptors cross boundaries.

```
ADAPTORS — COMPONENT_NAME

  Exposes:   ComponentAdaptor
  Called by: OTHER_COMPONENT_A (for operation X)
             OTHER_COMPONENT_B (for operation Y)

  Calls:     OtherAdaptor (to do Z)

  Protocol:  [function call | HTTP | subprocess | threading.Event | queue]
  Contract:  All callers use .execute(operation, id, payload) → dict
  Errors:    ComponentError raised; callers must catch and handle explicitly
```

---

## Acceptance Tests (write before implementing)

Write 3–5 tests that validate the contract boundaries. These tests should work against any correct implementation of this component — they test the contract, not the code.

```python
# Test 1: [what this tests]
# Test 2: [what this tests]
# Test 3: error code is correct on failure
# Test 4: [edge case from the error codes]
# Test 5: [safety invariant if applicable]
```

---

## Implementation notes (optional)

Any constraints the implementer should know that aren't captured in the contract itself. Technology choices, performance hints, known gotchas.

- Note 1
- Note 2
