"""
C-09 · MEMORY_MANAGER
Manages read/write of persistent MEMORY.md and USER.md files.
Coordinates memory provider plugins.  Triggers flush lifecycle.
Non-fatal on write failure (retry once, then continue).
"""
from __future__ import annotations

import os
import time
from logger import trace, warn

MEMORY_PATH = "MEMORY.md"
USER_PATH   = "USER.md"


class MemoryError(Exception):
    def __init__(self, error_code: str, operation: str, message: str):
        super().__init__(message)
        self.error_code = error_code
        self.operation  = operation

    def to_dict(self) -> dict:
        return {"error_code": self.error_code, "operation": self.operation, "message": str(self)}


class MemoryManager:
    """C-09 · MEMORY_MANAGER"""

    def __init__(self, provider_hooks: list | None = None):
        # provider_hooks: list of callables(event, session_id, content) → None
        self._hooks = provider_hooks or []

    def execute(self, operation: str, session_id: str,
                content: str | None = None, provider: str | None = None) -> dict:
        ops = {
            "read":           self._read,
            "write":          self._write,
            "flush":          self._flush,
            "on_session_end": self._on_session_end,
        }
        if operation not in ops:
            raise MemoryError("READ_FAIL", operation, f"Unknown operation: {operation}")
        return ops[operation](session_id, content, provider)

    # ── Operations ─────────────────────────────────────────────────────────────

    def _read(self, session_id: str, content: str | None, provider: str | None) -> dict:
        mem_md = self._read_file(MEMORY_PATH)
        usr_md = self._read_file(USER_PATH)
        trace("PROMPT_BUILDER", "memory_loaded",
              memory_md_chars=len(mem_md), user_md_chars=len(usr_md),
              session_id=session_id)
        return {"memory_md": mem_md, "user_md": usr_md, "flushed": False, "provider_ok": True}

    def _write(self, session_id: str, content: str | None, provider: str | None) -> dict:
        if not content:
            return {"memory_md": "", "user_md": "", "flushed": False, "provider_ok": True}
        ok = self._write_file(MEMORY_PATH, content, session_id, "write_memory_md")
        return {
            "memory_md":   content if ok else "",
            "user_md":     "",
            "flushed":     False,
            "provider_ok": True,
        }

    def _flush(self, session_id: str, content: str | None, provider: str | None) -> dict:
        t0 = time.time()
        trace("MEMORY_MANAGER", "flush_start", session_id=session_id, trigger="explicit")
        mem_md = self._read_file(MEMORY_PATH)
        usr_md = self._read_file(USER_PATH)

        # Fire provider hooks
        provider_ok = True
        for hook in self._hooks:
            try:
                hook("flush", session_id, {"memory_md": mem_md, "user_md": usr_md})
                trace("MEMORY_MANAGER", "provider_hook_fired",
                      provider_name=getattr(hook, "__name__", "hook"), event="flush",
                      session_id=session_id)
            except Exception as exc:
                warn("MEMORY_MANAGER", f"Provider hook failed (continuing): {exc}")
                provider_ok = False

        trace("MEMORY_MANAGER", "flush_end",
              session_id=session_id,
              duration_ms=round((time.time() - t0) * 1000),
              success=True)
        return {"memory_md": mem_md, "user_md": usr_md, "flushed": True, "provider_ok": provider_ok}

    def _on_session_end(self, session_id: str, content: str | None, provider: str | None) -> dict:
        return self._flush(session_id, content, provider)

    # ── File helpers ───────────────────────────────────────────────────────────

    def _read_file(self, path: str) -> str:
        try:
            with open(path, "r", encoding="utf-8") as fh:
                return fh.read()
        except FileNotFoundError:
            return ""
        except Exception as exc:
            warn("MEMORY_MANAGER", f"Read failed {path}: {exc}")
            return ""

    def _write_file(self, path: str, content: str,
                    session_id: str, trace_event: str) -> bool:
        for attempt in range(2):
            try:
                tmp = path + ".tmp"
                with open(tmp, "w", encoding="utf-8") as fh:
                    fh.write(content)
                os.replace(tmp, path)
                trace("MEMORY_MANAGER", trace_event,
                      session_id=session_id, char_count=len(content))
                return True
            except Exception as exc:
                if attempt == 0:
                    warn("MEMORY_MANAGER", f"Write failed {path} (retrying): {exc}")
                else:
                    warn("MEMORY_MANAGER", f"Write failed {path} (giving up): {exc}")
        return False
