# CBD Worked Example — APPROVAL_GATE (C-13)

This file walks through all six CBD Interface-First steps applied to a real component from Project Juan. Use it as a model when specifying your own components.

---

## The scenario

We are building an autonomous AI agent that can execute shell commands. Before executing any dangerous command (`rm -rf`, `sudo`, `dd if=`, etc.), the system must pause and ask a human for approval. This component handles that intercept.

---

## Step 1 — Name & Single Responsibility

**ID:** `C-13 APPROVAL_GATE`

**Responsibility:** Intercepts tool calls classified as dangerous, requests human approval via the gateway, and blocks execution until a decision is received or the timeout expires.

**Risk:** ⚠ CAUTION — this component's failure mode directly affects whether dangerous commands execute. Timeout must default to DENY, not APPROVE.

---

## Step 2 — IN Schema

```
IN SCHEMA — APPROVAL_GATE

  .needs_approval(tool_name, arguments, threshold)
    tool_name   : string                              required
    arguments   : dict                                required
    threshold   : enum[low|medium|high|critical]      default: "medium"

  .request(tool_name, arguments, session_id, risk_level, timeout_s)
    tool_name   : string                              required
    arguments   : dict                                required
    session_id  : string                              required
    risk_level  : enum[low|medium|high|critical]      required
    timeout_s   : integer, 1–3600                     default: 300

  .receive_decision(session_id, decision, modified_args)
    session_id    : string                            required
    decision      : enum[approve|deny|stop]           required
    modified_args : dict                              optional
```

---

## Step 3 — OUT Schema

```
OUT SCHEMA — APPROVAL_GATE

  .needs_approval() →
    boolean   (true = intercept; false = execute directly)

  .request() →
    approved        : boolean
    user_decision   : enum[approve|deny|stop|timeout]
    timestamp       : float (unix)
    modified_args   : dict | null

  .request() on error →
    raises ApprovalGateError
      error_code : string
      tool_name  : string
      message    : string
      default    : "deny"   ← always present; callers must honour this
```

---

## Step 4 — Error Codes

```
ERROR CODES — APPROVAL_GATE

  DELIVERY_FAIL  →  Gateway could not deliver the approval request to the user.
                    NOT retryable. Raise immediately. Callers must treat as deny.

  TIMEOUT        →  No decision received within timeout_s seconds.
                    NOT retryable. Raise. Default action is DENY — never APPROVE.
                    (This is the critical safety invariant of this component.)
```

**Safety invariant:** Any exception from `.request()` — including `DELIVERY_FAIL`, `TIMEOUT`, or any unexpected error — must result in the tool call being blocked. The gate fails closed, never open. This invariant must be documented on the error type and enforced by callers.

---

## Step 5 — Trace Log

```
TRACE EVENTS — APPROVAL_GATE
  component: "APPROVAL_GATE"

  dangerous_detected  →  {session_id, tool_name, risk_level, args_preview}
                         Emitted when needs_approval() returns true.

  approval_requested  →  {session_id, delivery_platform}
                         Emitted when the human has been successfully notified.

  decision_received   →  {session_id, decision, latency_ms}
                         Emitted when a decision arrives (approve / deny / timeout).

  bypass_command      →  {session_id, command, source}
                         Emitted when /approve, /deny, or /stop is received inline.
```

---

## Step 6 — Adaptor Definition

```
ADAPTORS — APPROVAL_GATE

  Exposes:    ApprovalGateAdaptor
  Used by:    TOOL_REGISTRY (C-03) calls .needs_approval() and .request()
              GATEWAY_RUNNER (C-05) calls .receive_decision() on slash commands

  Delivery:   APPROVAL_GATE uses GatewayAdaptor to send the approval message
              to the user (injected as delivery_fn at construction time)

  Protocol:   Synchronous blocking call. .request() holds the calling thread
              until decision or timeout. This is intentional — tool execution
              must not proceed until a decision is made.

  Threading:  One threading.Event per pending session_id. receive_decision()
              signals the event; request() waits on it. Lock guards the registry.
```

---

## Implementation sketch (after contract is complete)

```python
class ApprovalGate:
    def needs_approval(self, tool_name, arguments, threshold="medium"):
        risk = detect_risk(tool_name, arguments)   # regex patterns
        return RISK_ORDER[risk] >= RISK_ORDER[threshold]

    def request(self, tool_name, arguments, session_id, risk_level, timeout_s=300):
        # 1. Deliver via gateway
        delivered = self._deliver(session_id, format_message(tool_name, arguments))
        if not delivered:
            raise ApprovalGateError("DELIVERY_FAIL", tool_name, "...")

        # 2. Register pending event and block
        event = threading.Event()
        self._pending[session_id] = {"event": event, "result": {}}
        signalled = event.wait(timeout=timeout_s)

        # 3. Default deny on any non-approval
        if not signalled:
            raise ApprovalGateError("TIMEOUT", tool_name, "...")
        decision = self._pending[session_id]["result"].get("decision", "deny")
        return {"approved": decision == "approve", "user_decision": decision, ...}

    def receive_decision(self, session_id, decision, modified_args=None):
        # Called by gateway on /approve, /deny, /stop — signals the waiting thread
        pending = self._pending.get(session_id)
        if pending:
            pending["result"]["decision"] = decision
            pending["event"].set()
```

---

## Acceptance tests (written from the contract, not the implementation)

```python
# AC-1: Low-risk tool never triggers approval
gate = ApprovalGate()
assert not gate.needs_approval("bash", {"cmd": "ls -la"})

# AC-2: Critical-risk tool always triggers approval
assert gate.needs_approval("bash", {"cmd": "rm -rf /"})
assert gate.needs_approval("bash", {"cmd": "sudo shutdown -r now"})

# AC-3: Timeout defaults to DENY — never APPROVE
gate = ApprovalGate(delivery_fn=lambda sid, msg: True)
# No one calls receive_decision — simulate timeout
with pytest.raises(ApprovalGateError) as exc:
    gate.request("bash", {"cmd": "sudo rm /"}, "sess-1", "critical", timeout_s=0.01)
assert exc.value.error_code == "TIMEOUT"
assert exc.value.default == "deny"   # must be on the exception

# AC-4: Approval path works end-to-end
def approve_immediately(session_id, msg):
    threading.Timer(0.01, lambda: gate.receive_decision(session_id, "approve")).start()
    return True
gate2 = ApprovalGate(delivery_fn=approve_immediately)
result = gate2.request("bash", {"cmd": "sudo ls"}, "sess-2", "high", timeout_s=5)
assert result["approved"] is True

# AC-5: Delivery failure raises DELIVERY_FAIL (not a silent deny)
gate3 = ApprovalGate(delivery_fn=lambda sid, msg: False)
with pytest.raises(ApprovalGateError) as exc:
    gate3.request("bash", {"cmd": "sudo rm /"}, "sess-3", "critical")
assert exc.value.error_code == "DELIVERY_FAIL"
```

---

## What made this contract good

- The **safety invariant** (fail closed, never open) is stated explicitly in the error codes, in the adaptor definition, and in the implementation sketch. It cannot be accidentally ignored.
- **Caller responsibility is explicit**: every caller of `.request()` must handle `ApprovalGateError` as a deny. This is not assumed — it is written.
- **The trace log covers the complete decision lifecycle**: detect → request → decide. If any step is missing from logs, something went wrong.
- **Acceptance tests come from the OUT schema and error codes** — not from reading the implementation. This means you could swap the implementation entirely and the tests would still be valid.
