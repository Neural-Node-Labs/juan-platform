"""
Kivy UI gateway client.
Handles: HMAC token auth, WebSocket (primary), HTTP+SSE (fallback),
input validation, rate limiting awareness, reconnection.
"""
from __future__ import annotations

import base64
import json
import os
import re
import threading
import time
import uuid
from typing import Callable

import requests

try:
    import websocket as ws_lib  # websocket-client
    HAS_WS = True
except ImportError:
    HAS_WS = False

from utils.config import HTTP_URL, WS_URL, TOKEN_FILE

# ── Constants ─────────────────────────────────────────────────────────────────
MAX_CONTENT_BYTES = 32 * 1024
RECONNECT_DELAYS  = [1, 2, 4, 8, 16]  # seconds
PING_INTERVAL     = 25

# ── Validation (mirrors server-side) ─────────────────────────────────────────

def validate_content(text: str) -> str | None:
    """Returns error string or None if valid."""
    if not text.strip():
        return "Message cannot be empty"
    if len(text.encode("utf-8")) > MAX_CONTENT_BYTES:
        return f"Message too long (max {MAX_CONTENT_BYTES // 1024}KB)"
    return None

# ── Token persistence ─────────────────────────────────────────────────────────

def _load_token() -> dict | None:
    try:
        with open(TOKEN_FILE) as f:
            t = json.load(f)
        if t.get("expires_at", 0) < time.time() + 60:
            TOKEN_FILE.unlink(missing_ok=True)
            return None
        return t
    except Exception:
        return None

def _save_token(token_data: dict) -> None:
    try:
        with open(TOKEN_FILE, "w") as f:
            json.dump(token_data, f)
    except Exception:
        pass

def _clear_token() -> None:
    try:
        TOKEN_FILE.unlink(missing_ok=True)
    except Exception:
        pass

# ── Client ────────────────────────────────────────────────────────────────────

class JuanClient:
    """
    Manages the connection from the Kivy UI to the Juan UI Gateway (C-17).
    Thread-safe. Callbacks fire on the network thread; Kivy must schedule
    UI updates via Clock.schedule_once().
    """

    def __init__(
        self,
        ws_url:   str = WS_URL,
        http_url: str = HTTP_URL,
    ) -> None:
        self._ws_url    = ws_url
        self._http_url  = http_url
        self._token:    dict | None = _load_token()
        self._ws:       ws_lib.WebSocketApp | None = None
        self._ws_thread: threading.Thread | None   = None
        self._reconnect_count = 0
        self._reconnect_timer: threading.Timer | None = None
        self._running = False
        self._session_id = uuid.uuid4().hex

        # Callbacks — all optional
        self.on_message:    Callable[[dict], None] | None = None
        self.on_status:     Callable[[str, str | None], None] | None = None
        self.on_thinking:   Callable[[bool], None] | None = None

    # ── Auth ──────────────────────────────────────────────────────────────────

    def authenticate(self, password: str) -> tuple[bool, str]:
        """Returns (success, error_message)."""
        try:
            r = requests.post(
                f"{self._http_url}/ui/auth",
                json={"password": password},
                headers={"X-Client-Type": "kivy"},
                timeout=10,
            )
            if r.status_code == 200:
                data = r.json()
                self._token = data
                _save_token(data)
                return True, ""
            err = r.json().get("message", "Authentication failed")
            return False, err
        except requests.Timeout:
            return False, "Connection timed out. Is Juan running?"
        except requests.ConnectionError:
            return False, f"Cannot reach {self._http_url}. Is Juan running?"
        except Exception as exc:
            return False, str(exc)

    # ── Connect ───────────────────────────────────────────────────────────────

    def connect(self) -> None:
        if not self._token:
            self._emit_status("auth", "Authentication required")
            return
        if not HAS_WS:
            self._connect_http_fallback()
            return
        self._running = True
        self._emit_status("connecting", None)
        self._start_ws()

    def disconnect(self) -> None:
        self._running = False
        if self._reconnect_timer:
            self._reconnect_timer.cancel()
        if self._ws:
            try: self._ws.close()
            except Exception: pass
        self._emit_status("disconnected", None)

    # ── Send ──────────────────────────────────────────────────────────────────

    def send(self, content: str) -> str | None:
        """
        Validate and send a message.
        Returns error string on failure, None on success.
        """
        err = validate_content(content)
        if err:
            return err

        request_id = uuid.uuid4().hex[:16]
        payload    = json.dumps({
            "type":       "message",
            "content":    content,
            "session_id": self._session_id,
            "request_id": request_id,
        })

        if self._ws and self._ws.sock and getattr(self._ws.sock, 'connected', False):
            try:
                self._ws.send(payload)
                return None
            except Exception:
                pass  # fall through to HTTP

        return self._send_http(content, request_id)

    def ping(self) -> None:
        if self._ws and self._ws.sock:
            try:
                self._ws.send(json.dumps({"type": "ping"}))
            except Exception:
                pass

    # ── WebSocket internals ───────────────────────────────────────────────────

    def _start_ws(self) -> None:
        token = self._token["token"] if self._token else ""
        headers = {
            "Authorization":  f"Bearer {token}",
            "X-Client-Type":  "kivy",
        }
        self._ws = ws_lib.WebSocketApp(
            self._ws_url,
            header=headers,
            on_open    = self._on_ws_open,
            on_message = self._on_ws_message,
            on_error   = self._on_ws_error,
            on_close   = self._on_ws_close,
        )
        self._ws_thread = threading.Thread(
            target=self._ws.run_forever,
            kwargs={"ping_interval": PING_INTERVAL, "ping_timeout": 8},
            daemon=True,
            name="juan-kivy-ws",
        )
        self._ws_thread.start()

    def _on_ws_open(self, ws) -> None:
        self._reconnect_count = 0
        self._emit_status("connected", None)

    def _on_ws_message(self, ws, raw: str) -> None:
        try:
            data = json.loads(raw)
        except Exception:
            return

        msg_type = data.get("type", "")

        if msg_type == "pong":
            return
        if msg_type == "status" and data.get("status") == "thinking":
            self._emit_thinking(True)
            return
        if msg_type == "response":
            self._emit_thinking(False)
            if self.on_message:
                self.on_message({
                    "role":    "assistant",
                    "content": data.get("content", ""),
                    "metadata": data.get("metadata", {}),
                })
            return
        if msg_type == "error":
            self._emit_thinking(False)
            code = data.get("error_code", "")
            if code in ("UIAUTH_FAIL", "UIAUTH_EXPIRED"):
                _clear_token()
                self._token = None
                self._emit_status("auth", data.get("message", "Session expired"))
                return
            if self.on_message:
                self.on_message({
                    "role":    "error",
                    "content": data.get("message", "An error occurred"),
                })

    def _on_ws_error(self, ws, error) -> None:
        self._emit_status("error", str(error))

    def _on_ws_close(self, ws, close_status_code, close_msg) -> None:
        self._emit_thinking(False)
        if not self._running:
            return
        # Auth failures
        if close_status_code in (4001, 4002):
            _clear_token()
            self._token = None
            self._emit_status("auth", close_msg or "Authentication required")
            return
        # Schedule reconnect
        delay = RECONNECT_DELAYS[min(self._reconnect_count, len(RECONNECT_DELAYS)-1)]
        self._reconnect_count += 1
        self._emit_status("disconnected", f"Reconnecting in {delay}s…")
        self._reconnect_timer = threading.Timer(delay, self._start_ws)
        self._reconnect_timer.start()

    # ── HTTP fallback ─────────────────────────────────────────────────────────

    def _send_http(self, content: str, request_id: str) -> str | None:
        """HTTP POST fallback when WebSocket is unavailable."""
        if not self._token:
            return "Not authenticated"
        try:
            r = requests.post(
                f"{self._http_url}/ui/message",
                json={"content": content, "session_id": self._session_id,
                      "request_id": request_id, "type": "message"},
                headers={
                    "Authorization":  f"Bearer {self._token['token']}",
                    "X-Client-Type":  "kivy",
                    "Content-Type":   "application/json",
                },
                timeout=120,
            )
            if r.status_code == 429:
                return "Too many messages — slow down"
            if r.status_code == 401:
                _clear_token()
                self._token = None
                self._emit_status("auth", "Session expired")
                return "Authentication expired"
            if r.ok:
                data = r.json()
                self._emit_thinking(False)
                if self.on_message:
                    self.on_message({
                        "role":    "assistant",
                        "content": data.get("content", ""),
                        "metadata": data.get("metadata", {}),
                    })
                return None
            return f"Server error: {r.status_code}"
        except requests.Timeout:
            return "Request timed out"
        except Exception as exc:
            return str(exc)

    def _connect_http_fallback(self) -> None:
        self._emit_status("connected", "HTTP mode (no WebSocket)")

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _emit_status(self, status: str, message: str | None) -> None:
        if self.on_status:
            self.on_status(status, message)

    def _emit_thinking(self, thinking: bool) -> None:
        if self.on_thinking:
            self.on_thinking(thinking)

    @property
    def session_id(self) -> str:
        return self._session_id

    def new_session(self) -> None:
        self._session_id = uuid.uuid4().hex
