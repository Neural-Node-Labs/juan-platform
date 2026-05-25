"""
C-06 · PLATFORM_ADAPTOR
Normalizes raw platform events into a uniform MessageEvent.
Delivers outbound messages via platform-specific SDKs.
Start with Telegram; other platforms extend BaseAdapter.
"""
from __future__ import annotations

import time
import threading
from abc import ABC, abstractmethod
from typing import Any, Callable
from logger import trace, warn

# ── Uniform event schema ──────────────────────────────────────────────────────

class MessageEvent:
    __slots__ = ("platform", "chat_id", "user_id", "text",
                 "thread_id", "timestamp", "raw")

    def __init__(self, platform: str, chat_id: str, user_id: str, text: str,
                 thread_id: str | None = None, raw: dict | None = None):
        self.platform  = platform
        self.chat_id   = chat_id
        self.user_id   = user_id
        self.text      = text
        self.thread_id = thread_id
        self.timestamp = time.time()
        self.raw       = raw or {}

    def to_dict(self) -> dict:
        return {
            "platform":  self.platform,
            "chat_id":   self.chat_id,
            "user_id":   self.user_id,
            "text":      self.text,
            "thread_id": self.thread_id,
            "timestamp": self.timestamp,
        }


# ── Adaptor errors ────────────────────────────────────────────────────────────

class AdaptorError(Exception):
    def __init__(self, error_code: str, platform_name: str, message: str):
        super().__init__(message)
        self.error_code   = error_code
        self.platform_name = platform_name

    def to_dict(self) -> dict:
        return {"error_code": self.error_code, "platform_name": self.platform_name,
                "message": str(self)}


# ── Base adapter ──────────────────────────────────────────────────────────────

class BaseAdapter(ABC):
    """
    All platform adapters extend this class.
    Thread-safe token lock prevents dual-profile conflicts.
    """

    platform_name: str = "base"

    def __init__(self, token: str = "", on_event: Callable | None = None):
        self._token    = token
        self._on_event = on_event  # callback(MessageEvent) → None
        self._lock     = threading.Lock()
        self._connected = False

    # ── Lifecycle ─────────────────────────────────────────────────────────────

    def connect(self) -> None:
        if not self._lock.acquire(blocking=False):
            raise AdaptorError("TOKEN_LOCK_CONFLICT", self.platform_name,
                               "Another instance already holds this token")
        try:
            self._connect()
            self._connected = True
            trace("PLATFORM_ADAPTOR", "token_lock_acquired",
                  platform=self.platform_name,
                  bot_token_hash=str(hash(self._token))[-8:])
        except AdaptorError:
            self._lock.release()
            raise
        except Exception as exc:
            self._lock.release()
            raise AdaptorError("CONNECT_FAIL", self.platform_name, str(exc)) from exc

    def disconnect(self, reason: str = "normal") -> None:
        try:
            self._disconnect()
        except Exception:
            pass
        self._connected = False
        if self._lock.locked():
            self._lock.release()
        trace("PLATFORM_ADAPTOR", "disconnect",
              platform=self.platform_name, reason=reason)

    # ── Outbound ──────────────────────────────────────────────────────────────

    def send(self, chat_id: str, text: str, thread_id: str | None = None) -> bool:
        for attempt in range(2):
            try:
                self._send(chat_id, text, thread_id)
                trace("PLATFORM_ADAPTOR", "send_called",
                      platform=self.platform_name,
                      chat_id=chat_id, char_count=len(text), success=True)
                return True
            except Exception as exc:
                if attempt == 1:
                    warn("PLATFORM_ADAPTOR",
                         f"Send failed {self.platform_name}/{chat_id}: {exc}")
                    trace("PLATFORM_ADAPTOR", "send_called",
                          platform=self.platform_name,
                          chat_id=chat_id, char_count=len(text), success=False)
                    return False
        return False

    # ── Event normalisation ───────────────────────────────────────────────────

    def _emit(self, event: MessageEvent) -> None:
        trace("PLATFORM_ADAPTOR", "event_normalized",
              platform=self.platform_name,
              chat_id=event.chat_id,
              event_type="message")
        if self._on_event:
            try:
                self._on_event(event)
            except Exception as exc:
                warn("PLATFORM_ADAPTOR", f"on_event callback failed: {exc}")

    def _parse_event(self, raw: dict) -> MessageEvent | None:
        try:
            return self._normalize(raw)
        except Exception as exc:
            warn("PLATFORM_ADAPTOR",
                 f"Event parse failed ({self.platform_name}): {exc}")
            return None  # discard, do not crash

    # ── Abstract interface ────────────────────────────────────────────────────

    @abstractmethod
    def _connect(self) -> None: ...

    @abstractmethod
    def _disconnect(self) -> None: ...

    @abstractmethod
    def _send(self, chat_id: str, text: str, thread_id: str | None) -> None: ...

    @abstractmethod
    def _normalize(self, raw: dict) -> MessageEvent: ...


# ── Telegram adapter ──────────────────────────────────────────────────────────

class TelegramAdapter(BaseAdapter):
    """
    Telegram Bot API adapter using long-polling.
    Set TELEGRAM_BOT_TOKEN env var or pass token= directly.
    """
    platform_name = "telegram"
    BASE = "https://api.telegram.org/bot"

    def __init__(self, token: str = "", on_event: Callable | None = None):
        super().__init__(token or os.environ.get("TELEGRAM_BOT_TOKEN", ""), on_event)
        self._offset   = 0
        self._poll_thr: threading.Thread | None = None
        self._stop_evt = threading.Event()

    def _connect(self) -> None:
        import os
        if not self._token:
            raise AdaptorError("AUTH_FAIL", "telegram", "No bot token provided")
        self._stop_evt.clear()
        self._poll_thr = threading.Thread(target=self._poll_loop, daemon=True)
        self._poll_thr.start()

    def _disconnect(self) -> None:
        self._stop_evt.set()
        if self._poll_thr:
            self._poll_thr.join(timeout=5)

    def _poll_loop(self) -> None:
        import urllib.request, json as _json
        while not self._stop_evt.is_set():
            try:
                url  = f"{self.BASE}{self._token}/getUpdates?timeout=30&offset={self._offset}"
                with urllib.request.urlopen(url, timeout=35) as resp:
                    data = _json.loads(resp.read())
                for upd in data.get("result", []):
                    self._offset = upd["update_id"] + 1
                    msg = upd.get("message") or upd.get("edited_message")
                    if not msg:
                        continue
                    event = self._parse_event(upd)
                    if event:
                        self._emit(event)
            except Exception as exc:
                if not self._stop_evt.is_set():
                    warn("PLATFORM_ADAPTOR", f"Telegram poll error: {exc}")
                    self._stop_evt.wait(5)

    def _send(self, chat_id: str, text: str, thread_id: str | None) -> None:
        import urllib.request, urllib.parse, json as _json
        # Telegram has a 4096 char limit — chunk if needed
        chunks = [text[i:i+4096] for i in range(0, max(len(text), 1), 4096)]
        for chunk in chunks:
            payload = {"chat_id": chat_id, "text": chunk}
            if thread_id:
                payload["reply_to_message_id"] = thread_id
            data    = _json.dumps(payload).encode()
            req     = urllib.request.Request(
                f"{self.BASE}{self._token}/sendMessage",
                data=data,
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            urllib.request.urlopen(req, timeout=30)

    def _normalize(self, raw: dict) -> MessageEvent:
        msg = raw.get("message") or raw.get("edited_message", {})
        return MessageEvent(
            platform  = "telegram",
            chat_id   = str(msg["chat"]["id"]),
            user_id   = str(msg["from"]["id"]),
            text      = msg.get("text", ""),
            thread_id = str(msg.get("reply_to_message", {}).get("message_id", "")),
            raw       = raw,
        )


# ── Adapter factory ───────────────────────────────────────────────────────────

import os

ADAPTER_MAP: dict[str, type[BaseAdapter]] = {
    "telegram": TelegramAdapter,
    # Other platforms: discord, slack, whatsapp, signal, matrix, etc.
    # Add as they are implemented.
}

def get_adapter(platform: str, token: str = "",
                on_event: Callable | None = None) -> BaseAdapter:
    cls = ADAPTER_MAP.get(platform)
    if cls is None:
        raise AdaptorError("CONNECT_FAIL", platform, f"No adapter for platform: {platform}")
    return cls(token=token, on_event=on_event)
