"""
C-04 · SESSION_STORE
Persists all session metadata, message history, token usage, billing,
and supports full-text search with lineage tracking.
SQLite WAL mode, schema v11.  Jitter retry on write contention.
"""
from __future__ import annotations

import json
import random
import sqlite3
import threading
import time
import uuid
from contextlib import contextmanager
from typing import Any

from logger import trace, warn, error as log_error

DB_PATH        = "juan_state.db"
SCHEMA_VERSION = 11
MAX_RETRIES    = 15

# ── Schema ────────────────────────────────────────────────────────────────────

_DDL = """
PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;

CREATE TABLE IF NOT EXISTS _meta (key TEXT PRIMARY KEY, value TEXT);

CREATE TABLE IF NOT EXISTS sessions (
    session_id    TEXT PRIMARY KEY,
    parent_id     TEXT,
    platform      TEXT,
    chat_id       TEXT,
    user_id       TEXT,
    model         TEXT,
    created_at    REAL,
    ended_at      REAL,
    input_tokens  INTEGER DEFAULT 0,
    output_tokens INTEGER DEFAULT 0,
    cache_tokens  INTEGER DEFAULT 0
);

CREATE TABLE IF NOT EXISTS messages (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id   TEXT NOT NULL REFERENCES sessions(session_id),
    role         TEXT NOT NULL,
    content      TEXT NOT NULL,
    tool_call_id TEXT,
    tool_name    TEXT,
    created_at   REAL NOT NULL
);

CREATE VIRTUAL TABLE IF NOT EXISTS messages_fts USING fts5(
    content, session_id UNINDEXED,
    content='messages', content_rowid='id'
);

CREATE TRIGGER IF NOT EXISTS messages_ai AFTER INSERT ON messages BEGIN
    INSERT INTO messages_fts(rowid, content, session_id)
    VALUES (new.id, new.content, new.session_id);
END;
"""

# ── Error codes ────────────────────────────────────────────────────────────────

class SessionStoreError(Exception):
    def __init__(self, error_code: str, operation: str, message: str, retries: int = 0):
        super().__init__(message)
        self.error_code = error_code
        self.operation  = operation
        self.retries    = retries

    def to_dict(self) -> dict:
        return {
            "error_code": self.error_code,
            "operation":  self.operation,
            "retries":    self.retries,
            "message":    str(self),
        }

# ── SessionDB ─────────────────────────────────────────────────────────────────

class SessionDB:
    """
    C-04 · SESSION_STORE implementation.
    Leaf component — no adaptors.
    For in-memory mode (:memory:) a single persistent connection is kept open
    on a dedicated thread (check_same_thread=False) behind a lock.
    For file-mode each operation opens its own connection (WAL allows
    concurrent readers, and we use BEGIN IMMEDIATE for writers).
    """

    def __init__(self, db_path: str = DB_PATH):
        self._path    = db_path
        self._in_mem  = db_path == ":memory:"
        self._lock    = threading.Lock()
        # For :memory: we keep one shared connection alive
        self._mem_conn: sqlite3.Connection | None = None
        self._init_db()

    # ── Connection helpers ────────────────────────────────────────────────────

    def _new_conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self._path, timeout=30, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        return conn

    def _get_conn(self) -> sqlite3.Connection:
        """Return the shared connection (memory) or a fresh one (file)."""
        if self._in_mem:
            return self._mem_conn  # type: ignore[return-value]
        return self._new_conn()

    def _release_conn(self, conn: sqlite3.Connection) -> None:
        """Close connection unless it's the shared in-memory one."""
        if not self._in_mem:
            conn.close()

    # ── Init ──────────────────────────────────────────────────────────────────

    def _init_db(self) -> None:
        t0 = time.time()
        try:
            if self._in_mem:
                self._mem_conn = self._new_conn()
                conn = self._mem_conn
            else:
                conn = self._new_conn()

            conn.executescript(_DDL)

            row     = conn.execute(
                "SELECT value FROM _meta WHERE key='schema_version'"
            ).fetchone()
            current = int(row["value"]) if row else 0
            if current < SCHEMA_VERSION:
                self._migrate(conn, current)
                conn.execute(
                    "INSERT OR REPLACE INTO _meta VALUES ('schema_version', ?)",
                    (str(SCHEMA_VERSION),),
                )
            conn.commit()

            if not self._in_mem:
                conn.close()

            trace("SESSION_STORE", "migration_run",
                  from_version=current, to_version=SCHEMA_VERSION,
                  duration_ms=round((time.time() - t0) * 1000))
        except Exception as exc:
            log_error("SESSION_STORE", f"Migration failed: {exc}")
            raise SessionStoreError("MIGRATION_FAIL", "init", str(exc)) from exc

    def _migrate(self, conn: sqlite3.Connection, from_version: int) -> None:
        # Future migrations here; DDL above is authoritative for v11.
        pass

    # ── Write context manager ─────────────────────────────────────────────────

    @contextmanager
    def _write(self, retries: int = MAX_RETRIES):
        """
        For memory DB: acquires Python lock and uses shared conn.
        For file DB:   opens connection, BEGIN IMMEDIATE, jitter retry.
        """
        if self._in_mem:
            with self._lock:
                conn = self._mem_conn
                conn.execute("BEGIN")  # type: ignore[union-attr]
                try:
                    yield conn
                    conn.commit()  # type: ignore[union-attr]
                except Exception:
                    conn.rollback()  # type: ignore[union-attr]
                    raise
            return

        # File mode — jitter retry
        for attempt in range(retries):
            conn = self._new_conn()
            try:
                conn.execute("BEGIN IMMEDIATE")
                try:
                    yield conn
                    conn.commit()
                    conn.close()
                    return
                except Exception:
                    conn.rollback()
                    conn.close()
                    raise
            except sqlite3.OperationalError as exc:
                conn.close()
                if "locked" not in str(exc).lower() or attempt == retries - 1:
                    raise SessionStoreError("LOCK_TIMEOUT", "write", str(exc), retries=attempt)
                jitter = random.uniform(0.01, 0.05) * (2 ** attempt)
                trace("SESSION_STORE", "contention_retry",
                      attempt=attempt, jitter_ms=round(jitter * 1000))
                time.sleep(jitter)

    @contextmanager
    def _read(self):
        """Read-only query context."""
        if self._in_mem:
            with self._lock:
                yield self._mem_conn
            return
        conn = self._new_conn()
        try:
            yield conn
        finally:
            conn.close()

    # ── Public API ────────────────────────────────────────────────────────────

    def execute(self, operation: str, session_id: str, payload: dict | None = None) -> dict:
        payload = payload or {}
        ops = {
            "create":         self._create,
            "append_message": self._append_message,
            "get_messages":   self._get_messages,
            "search":         self._search,
            "end":            self._end,
            "prune":          self._prune,
            "export":         self._export,
        }
        if operation not in ops:
            raise SessionStoreError("READ_FAIL", operation, f"Unknown operation: {operation}")
        return ops[operation](session_id, payload)

    # ── Operations ────────────────────────────────────────────────────────────

    def _create(self, session_id: str, payload: dict) -> dict:
        t0 = time.time()
        try:
            with self._write() as conn:
                conn.execute(
                    """INSERT OR IGNORE INTO sessions
                       (session_id, parent_id, platform, chat_id, user_id, model, created_at)
                       VALUES (?,?,?,?,?,?,?)""",
                    (session_id, payload.get("parent_id"),
                     payload.get("platform",""), payload.get("chat_id",""),
                     payload.get("user_id",""), payload.get("model",""), time.time()),
                )
            trace("SESSION_STORE", "db_write", operation="create",
                  session_id=session_id, rows_affected=1,
                  duration_ms=round((time.time()-t0)*1000))
            return {"ok": True}
        except SessionStoreError:
            raise
        except Exception as exc:
            raise SessionStoreError("WRITE_FAIL", "create", str(exc)) from exc

    def _append_message(self, session_id: str, payload: dict) -> dict:
        t0 = time.time()
        try:
            with self._write() as conn:
                conn.execute(
                    """INSERT INTO messages
                       (session_id, role, content, tool_call_id, tool_name, created_at)
                       VALUES (?,?,?,?,?,?)""",
                    (session_id, payload["role"], payload["content"],
                     payload.get("tool_call_id"), payload.get("tool_name"), time.time()),
                )
                usage = payload.get("usage", {})
                if usage:
                    conn.execute(
                        """UPDATE sessions SET
                           input_tokens  = input_tokens  + ?,
                           output_tokens = output_tokens + ?,
                           cache_tokens  = cache_tokens  + ?
                           WHERE session_id = ?""",
                        (usage.get("input_tokens",0), usage.get("output_tokens",0),
                         usage.get("cache_tokens",0), session_id),
                    )
            trace("SESSION_STORE", "db_write", operation="append_message",
                  session_id=session_id, rows_affected=1,
                  duration_ms=round((time.time()-t0)*1000))
            return {"ok": True}
        except SessionStoreError:
            raise
        except Exception as exc:
            raise SessionStoreError("WRITE_FAIL", "append_message", str(exc)) from exc

    def _get_messages(self, session_id: str, payload: dict) -> dict:
        t0 = time.time()
        try:
            with self._read() as conn:
                rows = conn.execute(
                    "SELECT role, content, tool_call_id, tool_name, created_at "
                    "FROM messages WHERE session_id=? ORDER BY id",
                    (session_id,),
                ).fetchall()
            messages = [dict(r) for r in rows]
            trace("SESSION_STORE", "db_read", operation="get_messages",
                  session_id=session_id, rows_returned=len(messages),
                  duration_ms=round((time.time()-t0)*1000))
            return {"messages": messages, "ok": True}
        except Exception as exc:
            raise SessionStoreError("READ_FAIL", "get_messages", str(exc)) from exc

    def _search(self, session_id: str, payload: dict) -> dict:
        t0 = time.time()
        query = payload.get("query", "")
        try:
            with self._read() as conn:
                try:
                    rows = conn.execute(
                        "SELECT m.session_id, m.role, m.content, m.created_at "
                        "FROM messages_fts f JOIN messages m ON f.rowid=m.id "
                        "WHERE messages_fts MATCH ? ORDER BY rank LIMIT 20",
                        (query,),
                    ).fetchall()
                except sqlite3.OperationalError:
                    warn("SESSION_STORE", "FTS failed, falling back to LIKE")
                    rows = conn.execute(
                        "SELECT session_id, role, content, created_at "
                        "FROM messages WHERE content LIKE ? LIMIT 20",
                        (f"%{query}%",),
                    ).fetchall()
            results = [dict(r) for r in rows]
            trace("SESSION_STORE", "fts_query", query_text=query,
                  results_count=len(results),
                  duration_ms=round((time.time()-t0)*1000))
            return {"search_results": results, "ok": True}
        except Exception as exc:
            raise SessionStoreError("READ_FAIL", "search", str(exc)) from exc

    def _end(self, session_id: str, payload: dict) -> dict:
        t0 = time.time()
        try:
            with self._write() as conn:
                conn.execute(
                    "UPDATE sessions SET ended_at=? WHERE session_id=?",
                    (time.time(), session_id),
                )
                if not self._in_mem:
                    try:
                        result = conn.execute("PRAGMA wal_checkpoint(PASSIVE)").fetchone()
                        wal_frames = result[1] if result else 0
                        trace("SESSION_STORE", "checkpoint", wal_frames=wal_frames,
                              duration_ms=0)
                    except Exception as ce:
                        warn("SESSION_STORE", f"Checkpoint non-fatal: {ce}")
            trace("SESSION_STORE", "db_write", operation="end",
                  session_id=session_id, rows_affected=1,
                  duration_ms=round((time.time()-t0)*1000))
            return {"ok": True}
        except SessionStoreError:
            raise
        except Exception as exc:
            raise SessionStoreError("WRITE_FAIL", "end", str(exc)) from exc

    def _prune(self, session_id: str, payload: dict) -> dict:
        keep_last = payload.get("keep_last", 50)
        t0 = time.time()
        try:
            with self._write() as conn:
                conn.execute(
                    """DELETE FROM messages WHERE session_id=? AND id NOT IN (
                       SELECT id FROM messages WHERE session_id=? ORDER BY id DESC LIMIT ?)""",
                    (session_id, session_id, keep_last),
                )
            trace("SESSION_STORE", "db_write", operation="prune",
                  session_id=session_id, keep_last=keep_last,
                  duration_ms=round((time.time()-t0)*1000))
            return {"ok": True}
        except SessionStoreError:
            raise
        except Exception as exc:
            raise SessionStoreError("WRITE_FAIL", "prune", str(exc)) from exc

    def _export(self, session_id: str, payload: dict) -> dict:
        msgs = self._get_messages(session_id, {})
        with self._read() as conn:
            session = conn.execute(
                "SELECT * FROM sessions WHERE session_id=?", (session_id,)
            ).fetchone()
        return {
            "session":  dict(session) if session else None,
            "messages": msgs["messages"],
            "ok": True,
        }


# ── Singleton helper ──────────────────────────────────────────────────────────

_db: SessionDB | None = None

def get_db(path: str = DB_PATH) -> SessionDB:
    global _db
    if _db is None:
        _db = SessionDB(path)
    return _db
