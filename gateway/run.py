"""
C-05 · GATEWAY_RUNNER
Routes inbound messages from all platforms through:
  - Authorization (C-10)
  - Slash-command dispatch
  - Two-level message guard
  - Agent dispatch (C-01)
"""
from __future__ import annotations

import asyncio
import threading
import time
import uuid
from typing import Callable, Any

from logger import trace, warn, error as log_error
from gateway.authorization import check as auth_check
from gateway.platforms.base import MessageEvent, BaseAdapter, get_adapter


class GatewayError(Exception):
    def __init__(self, error_code: str, platform: str, user_id: str, message: str):
        super().__init__(message)
        self.error_code = error_code
        self.platform   = platform
        self.user_id    = user_id

    def to_dict(self) -> dict:
        return {
            "error_code": self.error_code,
            "platform":   self.platform,
            "user_id":    self.user_id,
            "message":    str(self),
        }


# ── Two-level guard ───────────────────────────────────────────────────────────
# Level 1: per-session concurrency queue  (one agent turn at a time per session)
# Level 2: slash-command intercept / inline dispatch
#   /stop /approve /deny — route to approval gate
#   /model /reset /help  — handled inline

BUILTIN_COMMANDS = {"/help", "/reset", "/model", "/stop", "/approve", "/deny", "/sessions", "/export"}


class GatewayRunner:
    """
    C-05 · GATEWAY_RUNNER
    """

    def __init__(self, agent_factory: Callable | None = None,
                 approval_gate=None):
        """
        agent_factory: callable(session_key) → AIAgent instance
        approval_gate: ApprovalGate instance (optional)
        """
        self._agent_factory  = agent_factory
        self._approval_gate  = approval_gate
        self._adapters: dict[str, BaseAdapter] = {}
        # Level-1 guard: per-session lock
        self._session_locks: dict[str, threading.Lock] = {}
        self._lock_registry  = threading.Lock()

    # ── Adapter management ────────────────────────────────────────────────────

    def add_platform(self, platform: str, token: str = "") -> None:
        adapter = get_adapter(platform, token=token, on_event=self._handle_event)
        adapter.connect()
        self._adapters[platform] = adapter
        trace("GATEWAY_RUNNER", "message_received",
              platform=platform, chat_id="", user_id="", text_length=0)

    def remove_platform(self, platform: str) -> None:
        adapter = self._adapters.pop(platform, None)
        if adapter:
            adapter.disconnect("removed")

    def deliver(self, platform: str, chat_id: str, text: str,
                thread_id: str | None = None) -> bool:
        adapter = self._adapters.get(platform)
        if not adapter:
            warn("GATEWAY_RUNNER", f"No adapter for platform {platform}")
            return False
        return adapter.send(chat_id, text, thread_id)

    # ── Inbound event handler ─────────────────────────────────────────────────

    def _handle_event(self, event: MessageEvent) -> None:
        """Called by platform adapters on every inbound message."""
        t0 = time.time()
        trace("GATEWAY_RUNNER", "message_received",
              platform=event.platform, chat_id=event.chat_id,
              user_id=event.user_id, text_length=len(event.text))

        # Auth check
        try:
            auth = auth_check(event.platform, event.user_id,
                              event.chat_id, event.text)
        except Exception as exc:
            auth = {"authorized": False, "reason": str(exc), "method": "denied"}
        trace("GATEWAY_RUNNER", "auth_check",
              method=auth["method"], authorized=auth["authorized"],
              reason=auth.get("reason", ""))

        if not auth["authorized"]:
            self.deliver(event.platform, event.chat_id,
                         "❌ Unauthorized. Send the pair code to get access.")
            return

        # Session key
        session_key = f"{event.platform}:{event.chat_id}:{event.user_id}"
        trace("GATEWAY_RUNNER", "session_key_built", session_key=session_key)

        # Level-1 guard — queue (one at a time per session)
        lock = self._get_session_lock(session_key)
        acquired = lock.acquire(blocking=True, timeout=30)
        if not acquired:
            warn("GATEWAY_RUNNER", f"Level-1 guard timeout for {session_key}")
            self.deliver(event.platform, event.chat_id,
                         "⏳ Still processing your previous message…")
            trace("GATEWAY_RUNNER", "guard_level1", queued_or_passed="timeout")
            return
        trace("GATEWAY_RUNNER", "guard_level1", queued_or_passed="passed")

        try:
            # Level-2 guard — slash commands
            text = event.text.strip()
            if text.startswith("/"):
                cmd = text.split()[0].lower()
                trace("GATEWAY_RUNNER", "guard_level2", command_intercepted=cmd)
                self._handle_command(cmd, text, event, session_key)
                return
            trace("GATEWAY_RUNNER", "guard_level2", command_intercepted=None)

            # Agent dispatch
            task_id = uuid.uuid4().hex
            trace("GATEWAY_RUNNER", "agent_dispatched",
                  session_key=session_key, task_id=task_id)
            try:
                agent = self._agent_factory(session_key) if self._agent_factory else None
                if agent is None:
                    # Fallback: echo
                    response = f"[No agent configured] Echo: {event.text}"
                else:
                    result   = agent.run(
                        user_message = event.text,
                        session_id   = session_key,
                        task_id      = task_id,
                    )
                    response = result.get("response", "")
            except Exception as exc:
                log_error("GATEWAY_RUNNER", f"Agent crashed: {exc}")
                response = "⚠️ Something went wrong. Please try again."

            delivered = self.deliver(event.platform, event.chat_id, response,
                                     thread_id=event.thread_id)
            dur = round((time.time() - t0) * 1000)
            trace("GATEWAY_RUNNER", "response_sent",
                  platform=event.platform, duration_ms=dur, char_count=len(response))
        finally:
            lock.release()

    def _handle_command(self, cmd: str, text: str,
                        event: MessageEvent, session_key: str) -> None:
        """Built-in slash commands."""
        try:
            if cmd in ("/approve", "/deny", "/stop"):
                if self._approval_gate:
                    self._approval_gate.receive_decision(session_key, cmd.lstrip("/"))
                self.deliver(event.platform, event.chat_id,
                             f"✅ Decision '{cmd}' received.")
            elif cmd == "/help":
                self.deliver(event.platform, event.chat_id,
                             "Commands: /help /reset /model /stop /approve /deny /sessions /export")
            elif cmd == "/reset":
                self.deliver(event.platform, event.chat_id,
                             "🔄 Session reset. Start fresh!")
            else:
                self.deliver(event.platform, event.chat_id,
                             f"Unknown command: {cmd}")
        except Exception as exc:
            warn("GATEWAY_RUNNER", f"Command handler failed for {cmd}: {exc}")
            self.deliver(event.platform, event.chat_id,
                         f"❌ Command failed: {exc}")

    # ── Helpers ────────────────────────────────────────────────────────────────

    def _get_session_lock(self, session_key: str) -> threading.Lock:
        with self._lock_registry:
            if session_key not in self._session_locks:
                self._session_locks[session_key] = threading.Lock()
            return self._session_locks[session_key]

    def run_forever(self) -> None:
        """Block the main thread, keeping adapters alive."""
        try:
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            for adapter in self._adapters.values():
                adapter.disconnect("shutdown")
