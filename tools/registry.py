"""
C-03 · TOOL_REGISTRY
Discovers, validates, and dispatches tool calls.
Runs parallel calls concurrently via ThreadPoolExecutor.
Handles dangerous-command approval gating.
Never lets one tool failure kill parallel siblings.
"""
from __future__ import annotations

import time
import hashlib
import json
from concurrent.futures import ThreadPoolExecutor, as_completed, TimeoutError as FuturesTimeout
from typing import Callable, Any

from logger import trace, warn
from tools.approval import ApprovalGate, detect_risk

TOOL_TIMEOUT = 120  # seconds per tool call
WORKER_THREADS = 8


class ToolError(Exception):
    def __init__(self, error_code: str, tool_name: str, tool_call_id: str, message: str):
        super().__init__(message)
        self.error_code   = error_code
        self.tool_name    = tool_name
        self.tool_call_id = tool_call_id

    def to_dict(self) -> dict:
        return {
            "error_code":   self.error_code,
            "tool_name":    self.tool_name,
            "tool_call_id": self.tool_call_id,
            "message":      str(self),
        }


class ToolRegistry:
    """
    C-03 · TOOL_REGISTRY

    Register tools with .register(name, fn, schema).
    Dispatch tool calls with .dispatch(...).
    """

    def __init__(self, approval_gate: ApprovalGate | None = None,
                 approval_threshold: str = "medium"):
        self._handlers: dict[str, Callable] = {}
        self._schemas:  dict[str, dict]     = {}
        self._gate      = approval_gate or ApprovalGate()
        self._threshold = approval_threshold

    # ── Registration ──────────────────────────────────────────────────────────

    def register(self, name: str, fn: Callable, schema: dict | None = None) -> None:
        self._handlers[name] = fn
        self._schemas[name]  = schema or {"name": name, "description": ""}

    def get_tool_schemas(self) -> list[dict]:
        return list(self._schemas.values())

    # ── Dispatch ──────────────────────────────────────────────────────────────

    def dispatch(
        self,
        tool_calls: list[dict],
        task_id: str,
        session_id: str,
        concurrent: bool = True,
    ) -> dict:
        """
        Dispatch one or more tool calls.
        Returns {"results": [...]} matching out_schema.
        """
        if concurrent and len(tool_calls) > 1:
            return self._dispatch_concurrent(tool_calls, task_id, session_id)
        else:
            results = [self._run_one(tc, task_id, session_id) for tc in tool_calls]
            return {"results": results}

    # ── Concurrent execution ──────────────────────────────────────────────────

    def _dispatch_concurrent(self, tool_calls: list, task_id: str, session_id: str) -> dict:
        results = [None] * len(tool_calls)
        with ThreadPoolExecutor(max_workers=min(WORKER_THREADS, len(tool_calls))) as pool:
            futures = {
                pool.submit(self._run_one, tc, task_id, session_id): i
                for i, tc in enumerate(tool_calls)
            }
            for future in as_completed(futures, timeout=TOOL_TIMEOUT + 10):
                idx = futures[future]
                try:
                    results[idx] = future.result()
                except Exception as exc:
                    # Never let one sibling's error kill others
                    results[idx] = {
                        "tool_call_id": tool_calls[idx].get("id", ""),
                        "tool_name":    tool_calls[idx].get("name", ""),
                        "content":      f"ERROR: {exc}",
                        "exit_code":    1,
                        "duration_ms":  0,
                        "error":        str(exc),
                    }
        return {"results": [r for r in results if r is not None]}

    # ── Single tool execution ─────────────────────────────────────────────────

    def _run_one(self, tool_call: dict, task_id: str, session_id: str) -> dict:
        tool_call_id = tool_call.get("id", "")
        tool_name    = tool_call.get("name", "")
        arguments    = tool_call.get("arguments", {})
        if isinstance(arguments, str):
            import json as _json
            try:
                arguments = _json.loads(arguments)
            except Exception:
                arguments = {}

        args_hash = hashlib.sha256(json.dumps(arguments, sort_keys=True).encode()).hexdigest()[:8]
        t0 = time.time()

        # Resolve handler
        try:
            handler = self._handlers[tool_name]
        except KeyError:
            trace("TOOL_REGISTRY", "error_caught",
                  session_id=session_id,
                  tool_call_id=tool_call_id,
                  error_type="TOOL_NOT_FOUND", message=f"No handler for {tool_name!r}")
            raise ToolError("TOOL_NOT_FOUND", tool_name, tool_call_id,
                            f"Tool '{tool_name}' not registered")

        trace("TOOL_REGISTRY", "tool_resolved",
              session_id=session_id, tool_name=tool_name,
              handler_file=getattr(handler, "__module__", "?"))

        # Approval gate
        risk_level = detect_risk(tool_name, arguments)
        if self._gate.needs_approval(tool_name, arguments, self._threshold):
            trace("TOOL_REGISTRY", "approval_required",
                  session_id=session_id, tool_name=tool_name,
                  risk_level=risk_level, args_preview=str(arguments)[:100])
            try:
                decision = self._gate.request(tool_name, arguments, session_id, risk_level)
                if not decision["approved"]:
                    raise ToolError("APPROVAL_DENIED", tool_name, tool_call_id,
                                    "User denied tool execution")
                if decision.get("modified_args"):
                    arguments = decision["modified_args"]
            except Exception as exc:
                if isinstance(exc, ToolError):
                    raise
                raise ToolError("APPROVAL_DENIED", tool_name, tool_call_id,
                                f"Approval failed: {exc}") from exc

        # Execute
        trace("TOOL_REGISTRY", "exec_start",
              session_id=session_id, tool_call_id=tool_call_id,
              tool_name=tool_name, backend=getattr(handler, "_backend", "python"))
        try:
            with ThreadPoolExecutor(max_workers=1) as pool:
                future = pool.submit(handler, **arguments)
                try:
                    result = future.result(timeout=TOOL_TIMEOUT)
                except FuturesTimeout:
                    future.cancel()
                    raise ToolError("EXEC_TIMEOUT", tool_name, tool_call_id,
                                    f"Tool '{tool_name}' timed out after {TOOL_TIMEOUT}s")
        except ToolError:
            raise
        except Exception as exc:
            duration_ms = round((time.time() - t0) * 1000)
            trace("TOOL_REGISTRY", "error_caught",
                  session_id=session_id, tool_call_id=tool_call_id,
                  error_type="EXEC_FATAL", message=str(exc))
            raise ToolError("EXEC_FATAL", tool_name, tool_call_id, str(exc)) from exc

        duration_ms = round((time.time() - t0) * 1000)
        # Normalise result
        if isinstance(result, dict):
            content   = result.get("content", str(result))
            exit_code = result.get("exit_code", 0)
        else:
            content   = str(result)
            exit_code = 0

        trace("TOOL_REGISTRY", "exec_end",
              session_id=session_id, tool_call_id=tool_call_id,
              exit_code=exit_code, duration_ms=duration_ms)

        return {
            "tool_call_id": tool_call_id,
            "tool_name":    tool_name,
            "content":      content,
            "exit_code":    exit_code,
            "duration_ms":  duration_ms,
        }
