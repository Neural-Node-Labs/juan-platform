"""
C-13 · APPROVAL_GATE
Intercepts dangerous tool calls, requests human approval via the
gateway, blocks execution until decision received or timeout.
Default-deny on timeout or delivery failure (fail-safe).
"""
from __future__ import annotations

import re
import time
import threading
from typing import Callable
from logger import trace, warn

# ── Risk patterns ─────────────────────────────────────────────────────────────

RISK_PATTERNS: list[tuple[str, str]] = [
    # (regex, risk_level)
    (r"\brm\s+-rf\b",                          "critical"),
    (r"\bformat\b.*\b(disk|drive|volume)\b",   "critical"),
    (r"\bdrop\s+database\b",                    "critical"),
    (r"\bshutdown\b|\breboot\b|\bpoweroff\b",   "high"),
    (r"\bsudo\b",                               "high"),
    (r"\bchmod\s+777\b",                        "medium"),
    (r"\bcurl\b.*\|\s*sh\b",                    "critical"),
    (r"\bwget\b.*\|\s*sh\b",                    "critical"),
    (r"\bdd\s+if=",                             "high"),
    (r"\bkill\s+-9\b",                          "medium"),
]

RISK_ORDER = {"low": 0, "medium": 1, "high": 2, "critical": 3}


def detect_risk(tool_name: str, arguments: dict) -> str:
    """Returns risk level string for the given tool call."""
    text = tool_name + " " + str(arguments)
    max_risk = "low"
    for pattern, level in RISK_PATTERNS:
        if re.search(pattern, text, re.IGNORECASE):
            if RISK_ORDER.get(level, 0) > RISK_ORDER.get(max_risk, 0):
                max_risk = level
    return max_risk


class ApprovalGateError(Exception):
    def __init__(self, error_code: str, tool_name: str, message: str):
        super().__init__(message)
        self.error_code = error_code
        self.tool_name  = tool_name

    def to_dict(self) -> dict:
        return {"error_code": self.error_code, "tool_name": self.tool_name,
                "message": str(self), "default": "deny"}


class ApprovalGate:
    """
    C-13 · APPROVAL_GATE
    delivery_fn: async callable(session_id, message_text) → bool
                 used to ask the human for approval via the gateway
    """

    def __init__(self, delivery_fn: Callable | None = None):
        self._delivery_fn = delivery_fn
        # Pending approvals: {session_id: threading.Event + result}
        self._pending: dict[str, dict] = {}
        self._lock = threading.Lock()

    def needs_approval(self, tool_name: str, arguments: dict,
                       threshold: str = "medium") -> bool:
        level = detect_risk(tool_name, arguments)
        return RISK_ORDER.get(level, 0) >= RISK_ORDER.get(threshold, 1)

    def request(
        self,
        tool_name: str,
        arguments: dict,
        session_id: str,
        risk_level: str,
        timeout_s: int = 300,
    ) -> dict:
        """
        Sends an approval request and blocks until decision or timeout.
        Returns out_schema dict.
        Raises ApprovalGateError on delivery failure or critical timeout.
        """
        args_preview = str(arguments)[:200]
        trace("APPROVAL_GATE", "dangerous_detected",
              session_id=session_id,
              tool_name=tool_name,
              risk_level=risk_level,
              args_preview=args_preview)

        # Deliver approval request
        msg = (
            f"⚠️ APPROVAL REQUIRED\n"
            f"Tool: `{tool_name}`\n"
            f"Risk: {risk_level}\n"
            f"Args: {args_preview}\n\n"
            f"Reply `/approve`, `/deny`, or `/stop`."
        )
        delivered = self._deliver(session_id, msg)
        if not delivered:
            trace("APPROVAL_GATE", "approval_requested",
                  session_id=session_id, delivery_platform="FAILED")
            raise ApprovalGateError("DELIVERY_FAIL", tool_name, "Could not deliver approval request")

        trace("APPROVAL_GATE", "approval_requested",
              session_id=session_id, delivery_platform="gateway")

        # Block waiting for decision
        event = threading.Event()
        result_holder: dict = {}
        with self._lock:
            self._pending[session_id] = {"event": event, "result": result_holder}

        t0 = time.time()
        signalled = event.wait(timeout=timeout_s)
        latency = round((time.time() - t0) * 1000)

        with self._lock:
            self._pending.pop(session_id, None)

        if not signalled:
            trace("APPROVAL_GATE", "decision_received",
                  session_id=session_id, decision="timeout", latency_ms=latency)
            raise ApprovalGateError("TIMEOUT", tool_name, "Approval timed out — defaulting to deny")

        decision = result_holder.get("decision", "deny")
        trace("APPROVAL_GATE", "decision_received",
              session_id=session_id, decision=decision, latency_ms=latency)

        return {
            "approved":      decision == "approve",
            "user_decision": decision,
            "timestamp":     time.time(),
            "modified_args": result_holder.get("modified_args"),
        }

    def receive_decision(self, session_id: str, decision: str,
                         modified_args: dict | None = None) -> None:
        """
        Called by the gateway when the user replies /approve, /deny, /stop.
        This is the INLINE bypass path — never goes through background queue.
        """
        trace("APPROVAL_GATE", "bypass_command",
              session_id=session_id, command=decision, source="inline")
        with self._lock:
            pending = self._pending.get(session_id)
        if pending:
            pending["result"]["decision"] = decision
            pending["result"]["modified_args"] = modified_args
            pending["event"].set()

    def _deliver(self, session_id: str, message: str) -> bool:
        if self._delivery_fn is None:
            # No delivery configured — log and assume console delivery
            print(f"[APPROVAL] {session_id}: {message}")
            return True
        try:
            return bool(self._delivery_fn(session_id, message))
        except Exception as exc:
            warn("APPROVAL_GATE", f"Delivery function failed: {exc}")
            return False
