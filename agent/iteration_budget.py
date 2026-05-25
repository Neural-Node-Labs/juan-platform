"""
C-14 · ITERATION_BUDGET
Enforces per-session and per-subagent iteration limits.
Emits warnings at 80%, triggers summary on exhaustion.
Synchronous, never throws — wraps itself in try-catch.
"""
from __future__ import annotations
from logger import trace, warn

DEFAULT_MAX   = 90
SUBAGENT_MAX  = 50
WARN_THRESHOLD = 0.80


class IterationBudget:
    """
    C-14 implementation.  Call .check() before every agent iteration.
    """

    def __init__(self, session_id: str, max_iterations: int = DEFAULT_MAX,
                 is_subagent: bool = False, subagent_max: int = SUBAGENT_MAX):
        try:
            if max_iterations < 1:
                warn("ITERATION_BUDGET", f"Invalid max_iterations={max_iterations}, using default {DEFAULT_MAX}")
                max_iterations = DEFAULT_MAX
            self._session_id   = session_id
            self._max          = subagent_max if is_subagent else max_iterations
            self._is_subagent  = is_subagent
        except Exception:
            self._session_id   = session_id
            self._max          = DEFAULT_MAX
            self._is_subagent  = is_subagent

    def check(self, current_iteration: int) -> dict:
        """
        Returns out_schema dict.  Never raises.
        """
        try:
            remaining = self._max - current_iteration
            pct       = current_iteration / self._max if self._max else 1.0

            if remaining <= 0:
                trace("ITERATION_BUDGET", "budget_exhausted",
                      session_id=self._session_id, summary_triggered=True)
                return {
                    "allowed":           False,
                    "remaining":         0,
                    "warning_level":     "exhausted",
                    "summary_triggered": True,
                }

            if pct >= WARN_THRESHOLD:
                trace("ITERATION_BUDGET", "warning_at_80pct",
                      session_id=self._session_id, remaining=remaining)
                level = "warning_80pct"
            else:
                level = "ok"

            trace("ITERATION_BUDGET", "budget_check",
                  session_id=self._session_id,
                  iteration=current_iteration,
                  max=self._max,
                  remaining=remaining,
                  is_subagent=self._is_subagent)

            return {
                "allowed":           True,
                "remaining":         remaining,
                "warning_level":     level,
                "summary_triggered": False,
            }
        except Exception:
            # Budget check must never throw
            return {
                "allowed":           False,
                "remaining":         0,
                "warning_level":     "exhausted",
                "summary_triggered": False,
            }
