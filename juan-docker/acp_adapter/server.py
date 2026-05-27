"""
C-15 · ACP_SERVER
Exposes the agent over the ACP protocol (stdio JSON-RPC) for IDE
integration with VS Code, Zed, and JetBrains.
Run as a subprocess; the IDE speaks JSON-RPC over stdin/stdout.
"""
from __future__ import annotations

import json
import sys
import uuid
import threading
from typing import Any, Callable

from logger import trace, warn, error as log_error

SUPPORTED_EDITORS = {"vscode", "zed", "jetbrains"}


class ACPError(Exception):
    def __init__(self, error_code: str, message: str):
        super().__init__(message)
        self.error_code = error_code

    def to_dict(self) -> dict:
        return {"error_code": self.error_code, "message": str(self)}


class ACPServer:
    """
    C-15 · ACP_SERVER
    Reads JSON-RPC 2.0 messages from stdin, writes responses to stdout.
    """

    def __init__(self, agent_factory: Callable | None = None,
                 session_store=None):
        self._agent_factory = agent_factory
        self._session_store = session_store
        self._editor: str | None = None
        self._pid:    int | None = None
        self._active_sessions: dict[str, Any] = {}

    # ── Lifecycle ─────────────────────────────────────────────────────────────

    def run(self, infile=None, outfile=None) -> None:
        """
        Block reading JSON-RPC from infile (default stdin) and writing to outfile.
        """
        infile  = infile  or sys.stdin
        outfile = outfile or sys.stdout
        self._out = outfile

        trace("ACP_SERVER", "acp_connect", editor="unknown", pid=0)

        for line in infile:
            line = line.strip()
            if not line:
                continue
            try:
                msg = json.loads(line)
            except json.JSONDecodeError as exc:
                self._send_error(None, "PARSE_ERROR", f"JSON parse error: {exc}", outfile)
                continue

            req_id = msg.get("id")
            method = msg.get("method", "")
            params = msg.get("params", {})

            trace("ACP_SERVER", "acp_request",
                  method=method, params_size=len(json.dumps(params)))

            try:
                result = self._dispatch(method, params)
                self._send_result(req_id, result, outfile)
            except ACPError as exc:
                self._send_error(req_id, exc.error_code, str(exc), outfile)
            except Exception as exc:
                log_error("ACP_SERVER", f"Unhandled error in {method}: {exc}")
                self._send_error(req_id, "AGENT_ERROR", str(exc), outfile)

        trace("ACP_SERVER", "acp_disconnect",
              editor=self._editor or "unknown", reason="stdin_closed")

    # ── Dispatch ──────────────────────────────────────────────────────────────

    def _dispatch(self, method: str, params: dict) -> dict:
        handlers = {
            "initialize":      self._handle_initialize,
            "chat/message":    self._handle_message,
            "chat/stream":     self._handle_stream,
            "session/new":     self._handle_new_session,
            "session/list":    self._handle_list_sessions,
            "ping":            lambda p: {"pong": True},
        }
        fn = handlers.get(method)
        if fn is None:
            raise ACPError("METHOD_NOT_FOUND", f"Unknown method: {method!r}")
        return fn(params)

    def _handle_initialize(self, params: dict) -> dict:
        editor = params.get("editor", "").lower()
        if editor not in SUPPORTED_EDITORS:
            warn("ACP_SERVER", f"Unknown editor {editor!r} (proceeding anyway)")
        self._editor = editor
        self._pid    = params.get("pid", 0)
        trace("ACP_SERVER", "acp_connect", editor=editor, pid=self._pid)
        return {
            "server": "juan-acp",
            "version": "1.0.0",
            "capabilities": ["chat", "stream", "session"],
        }

    def _handle_message(self, params: dict) -> dict:
        message    = params.get("message", "")
        session_id = params.get("session_id") or uuid.uuid4().hex
        try:
            agent  = self._agent_factory() if self._agent_factory else None
            if agent is None:
                return {"result": {"response": f"Echo: {message}"},
                        "status": "ok", "stream_delta": None}
            result = agent.run(user_message=message, session_id=session_id)
            return {"result": result, "status": "ok", "stream_delta": None}
        except Exception as exc:
            raise ACPError("AGENT_ERROR", str(exc)) from exc

    def _handle_stream(self, params: dict) -> dict:
        # Streaming: for now delegate to non-streaming; real streaming
        # would push multiple JSON-RPC notifications
        return self._handle_message(params)

    def _handle_new_session(self, params: dict) -> dict:
        sid = uuid.uuid4().hex
        if self._session_store:
            self._session_store.execute("create", sid, params)
        return {"session_id": sid, "status": "ok"}

    def _handle_list_sessions(self, params: dict) -> dict:
        return {"sessions": list(self._active_sessions.keys()), "status": "ok"}

    # ── Wire format ───────────────────────────────────────────────────────────

    def _send_result(self, req_id: Any, result: dict, outfile) -> None:
        msg = json.dumps({"jsonrpc": "2.0", "id": req_id, "result": result})
        outfile.write(msg + "\n")
        outfile.flush()

    def _send_error(self, req_id: Any, code: str, message: str, outfile) -> None:
        msg = json.dumps({
            "jsonrpc": "2.0",
            "id": req_id,
            "error": {"code": code, "message": message},
        })
        outfile.write(msg + "\n")
        outfile.flush()
