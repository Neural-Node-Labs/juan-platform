# CBD Common Patterns

Common patterns that appear repeatedly in CBD systems. Each includes the problem it solves, the contract shape, and the rationale — not just the mechanics.

---

## 1. Retry with Jitter Backoff

**Problem:** External services (APIs, databases) fail transiently. Immediate retry hammers the service; fixed-interval retry causes thundering herd.

**When to use:** Any component that calls an external service with transient failures: LLM APIs, databases under write contention, network services.

**Contract shape:**
```
Error codes:
  RATE_LIMITED   → HTTP 429. Retryable. Backoff and retry.
  UPSTREAM_ERROR → HTTP 5xx. Retryable up to max_retries. Then raise.
  AUTH_FAIL      → HTTP 401/403. NOT retryable. Raise immediately.
  TIMEOUT        → Request timed out. Retryable once. Then raise.

Trace events:
  retry_n  → {attempt, error_type, backoff_ms}
  (emitted before each retry, not after success)
```

**Rationale:** Auth failures are not transient — retrying wastes quota and delays the error. Rate limits and server errors are transient. The jitter (random offset added to the delay) prevents multiple clients from retrying in sync after a service recovers.

**Implementation sketch:**
```python
for attempt in range(max_retries):
    try:
        return call()
    except TransientError as exc:
        if not exc.retryable or attempt == max_retries - 1:
            raise
        backoff = min(base * (2 ** attempt) + random.uniform(0, 1), max_backoff)
        trace("retry_n", attempt=attempt+1, backoff_ms=round(backoff*1000))
        time.sleep(backoff)
```

---

## 2. One-Shot Fallback

**Problem:** A primary resource (model, service, provider) fails after retries. A backup exists but should only be tried once — you don't want an infinite retry chain across multiple providers.

**When to use:** LLM provider routing (primary model → fallback model), primary database → read replica, primary API → mirror.

**Contract shape:**
```
Error codes:
  FALLBACK_EXHAUSTED → Both primary and fallback failed. Not retryable. Raise.

Trace events:
  fallback_activated → {from_provider, to_provider, trigger_reason, http_status}
  (emitted exactly once when fallback fires)
```

**Rationale:** Fallback is a one-time safety net, not a retry loop. If the fallback also fails, surface the error — don't chain to a third option silently. The trace event records *why* fallback fired (rate limit, auth failure, timeout) so you can diagnose provider health from logs.

**Implementation sketch:**
```python
try:
    return call_primary()
except PrimaryError as exc:
    trace("fallback_activated", from_provider=primary, to_provider=fallback,
          trigger_reason=exc.error_code)
    try:
        return call_fallback()
    except FallbackError as exc2:
        raise ExhaustedError("FALLBACK_EXHAUSTED", ...) from exc2
```

---

## 3. Default-Deny Gate

**Problem:** A component controls access, authorization, or safety. Any bug, misconfiguration, or unexpected input must result in the safe action — not the permissive one.

**When to use:** Authorization checks, approval gates, content filters, any component where "failing open" is catastrophic.

**Contract shape:**
```
Error codes:
  (Any exception code)  → NOT retryable. Default: DENY. Caller must honour this.

Trace events:
  auth_method   → {layer_reached, method_used}   (emitted on every decision)
  deny_reason   → {reason, platform, user_id}    (emitted on every deny)

Safety invariant (write this explicitly in the contract):
  "On ANY exception — including unexpected errors not listed above —
   the component MUST return the deny/block/reject action.
   It MUST NOT default to the permissive action under any circumstances."
```

**Rationale:** This is a safety property, not a performance property. The invariant must be stated in the contract so every caller knows what to expect, and so future implementers know they cannot break it without violating the interface. Defensive code in callers — catching exceptions and defaulting to deny — is not enough on its own; the contract must require it.

**Implementation sketch:**
```python
def check(platform, user_id, chat_id, message_text):
    try:
        return _evaluate(...)       # five-layer evaluation
    except Exception as exc:
        warn("AUTH", f"Exception defaulting to deny: {exc}")
        return {"authorized": False, "reason": str(exc), "method": "denied"}
        # Never: raise exc  (that would let callers decide the default)
        # Never: return {"authorized": True, ...}  (fail-open)
```

---

## 4. Fan-Out with Isolated Failures

**Problem:** Multiple independent operations must run concurrently, and one failing must not cancel the others.

**When to use:** Parallel tool dispatch, concurrent API calls, batch processing where items are independent.

**Contract shape:**
```
OUT schema:
  results : array[{item_id, content, exit_code, error?}]
  (Every item appears in results — successes and failures alike)
  (No item is silently dropped)

Error codes:
  EXEC_FATAL  → One tool call failed. The result array contains this item
                with exit_code != 0 and error set. Other results are unaffected.
  (No top-level error is raised for individual item failures)

Trace events:
  exec_end   → {item_id, exit_code, duration_ms}   (one per item, always)
  error_caught → {item_id, error_type, message}     (one per failing item)
```

**Rationale:** Callers need every result, even the failures — they may retry individual failures, log them differently, or surface them to the user. Silently dropping failed items is worse than surfacing the error. The contract guarantees a result for every input.

**Implementation sketch:**
```python
results = [None] * len(items)
with ThreadPoolExecutor(max_workers=N) as pool:
    futures = {pool.submit(process, item): i for i, item in enumerate(items)}
    for future in as_completed(futures):
        idx = futures[future]
        try:
            results[idx] = future.result()
        except Exception as exc:
            # Never let one failure propagate — capture it in the result
            results[idx] = {"item_id": items[idx].id, "exit_code": 1, "error": str(exc)}
            trace("error_caught", item_id=items[idx].id, error_type=type(exc).__name__)
return {"results": results}
```

---

## 5. Compression Trigger

**Problem:** A resource (context window, buffer, queue) has a hard limit. The component must detect when it's approaching the limit and trigger a reduction step before the limit is hit, not after.

**When to use:** LLM context window management, log buffer rotation, memory pressure management.

**Contract shape:**
```
IN schema:
  current_size      : integer
  limit             : integer
  threshold_pct     : float, 0.0–1.0   default: 0.75

OUT schema:
  triggered         : boolean
  size_before       : integer
  size_after        : integer
  reduction         : integer

Trace events:
  compression_triggered → {trigger, current_pct, tokens_before, tokens_after}
  (emitted only when triggered=true)

Invariant:
  "Compression MUST be triggered when current_size / limit >= threshold_pct.
   It MUST NOT wait until the limit is reached — at that point it is too late."
```

**Rationale:** Triggering at 75% gives the compression step room to work. If compression is triggered at 100%, the next LLM call may already exceed the limit before the compressed history can be substituted. The threshold is configurable because different systems have different compression overhead.

**Sequencing constraint** (write this in the adaptor section):
```
Memory MUST be flushed BEFORE compression begins.
Reason: compression summarises the conversation. If memory is not flushed first,
the summary may not include the latest memory updates, and those updates will be
lost in the compressed history.
```

---

## 6. Context Guard (Two-Level)

**Problem:** A shared resource (thread, queue, agent loop) must not be accessed concurrently by the same key, and certain inputs (commands, admin actions) must be intercepted before reaching the main handler.

**When to use:** Gateway routing, session-level serialization, command interception.

**Contract shape:**
```
Level 1 — Serialization guard:
  Per-key lock (threading.Lock per session_id)
  Timeout: if lock not acquired within N seconds → reject with BUSY
  Trace: guard_level1 → {queued_or_passed: "passed"|"timeout"}

Level 2 — Input intercept:
  Pattern match on input (e.g. starts with "/")
  Matched inputs → route to command handler, bypass main processor
  Trace: guard_level2 → {command_intercepted: string|null}
```

**Rationale:** Level 1 prevents two messages from the same session triggering two concurrent agent turns — which would cause interleaved history writes and unpredictable state. Level 2 intercepts control commands (/stop, /approve) before they enter the agent loop — where they'd be treated as user messages and generate a (wrong) LLM response. The two levels are distinct concerns and should be implemented as distinct checks, in order.

---

## 7. Atomic File Write

**Problem:** A file that another component reads (e.g. `MEMORY.md`) must never be in a half-written state. If the write crashes partway through, the reader must see the old version, not a corrupt partial write.

**When to use:** Any component that writes configuration, state, or memory files that other components read.

**Contract shape:**
```
Error codes:
  WRITE_FAIL  → Write to temp file or rename failed.
                Retryable: YES, once.
                Recovery: log, continue — do not raise (non-fatal by design).

Trace events:
  file_written → {path, char_count}   (emitted after successful rename)
```

**Rationale:** Write to `path.tmp`, then `os.replace(path.tmp, path)`. On POSIX systems, `os.replace` is atomic — the old file is available to readers until the new file is fully written and the rename completes. Readers never see a partial file. One retry is included because transient filesystem errors (permissions, locks) are common in Docker volume mounts.

**Implementation sketch:**
```python
tmp = path + ".tmp"
with open(tmp, "w") as fh:
    fh.write(content)
os.replace(tmp, path)    # atomic on POSIX
```

---

## Pattern selection guide

| Situation | Pattern to use |
|-----------|---------------|
| External API that can fail transiently | Retry with Jitter Backoff |
| External API with a backup alternative | One-Shot Fallback |
| Access control, auth, safety gate | Default-Deny Gate |
| Multiple independent items to process | Fan-Out with Isolated Failures |
| Resource approaching a hard limit | Compression Trigger |
| Shared resource with privileged inputs | Context Guard (Two-Level) |
| File that must never be partially written | Atomic File Write |
| Two of the above combined | Name both; implement in sequence |
