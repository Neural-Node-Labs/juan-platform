"""
C-20 · WORKSPACE_TOOL
Gives the agent safe read/write/exec access to a workspace directory.

Security invariants:
  1. ALL paths are resolved to absolute and verified to be inside WORKSPACE_ROOT.
     Any path that escapes (symlinks, .., absolute) is rejected — WORKSPACE_ESCAPE.
  2. WORKSPACE_ROOT is created on first use if it does not exist.
  3. Executable commands run inside the workspace via subprocess with a timeout.
  4. Command stdout+stderr are capped at 32KB to prevent response bloat.
  5. Dangerous shell patterns are blocked before execution.

CBD CONTRACT
═══════════════════════════════════════════════════════
IN:  operation  str   read|write|append|delete|list|mkdir|move|exists|run
     path       str   relative to workspace root
     content    str?  for write/append
     command    str?  for run (executed in workspace as cwd)
     timeout    int?  seconds, default 30, max 120

OUT: {"ok": bool, "result": str, "path": str, "exit_code": int}

ERROR CODES:
  WORKSPACE_ESCAPE   path resolves outside workspace root
  WORKSPACE_NOTFOUND file/dir does not exist for read/delete/move
  WORKSPACE_IOERR    OS-level read/write failure
  WORKSPACE_EXEC_TIMEOUT  command exceeded timeout
  WORKSPACE_EXEC_BLOCKED  command matched dangerous pattern
  WORKSPACE_EXEC_FAIL     command returned non-zero
═══════════════════════════════════════════════════════
"""
from __future__ import annotations

import os
import re
import shlex
import shutil
import subprocess
import time
from pathlib import Path
from typing import Any

# ── Config ────────────────────────────────────────────────────────────────────
WORKSPACE_ROOT = Path(
    os.environ.get("JUAN_WORKSPACE", "/data/workspace")
).resolve()

MAX_READ_BYTES  = 256 * 1024   # 256 KB per file read
MAX_OUTPUT_BYTES = 32 * 1024   # 32 KB command output cap
DEFAULT_TIMEOUT = 30
MAX_TIMEOUT     = 120

# ── Dangerous command patterns (blocked regardless of approval threshold) ─────
_BLOCKED = re.compile(
    r"rm\s+-rf\s+/|"
    r"mkfs|"
    r"dd\s+if=.*of=/dev|"
    r":\s*\(\s*\)\s*\{|"   # fork bomb
    r"chmod\s+-R\s+777\s+/|"
    r">\s*/etc/|"
    r"curl\s+.*\|\s*bash|"
    r"wget\s+.*\|\s*bash|"
    r"python\s+-c.*exec\s*\(|"
    r"eval\s+.*base64",
    re.IGNORECASE,
)

# ── Error ─────────────────────────────────────────────────────────────────────

class WorkspaceError(Exception):
    def __init__(self, error_code: str, message: str):
        super().__init__(message)
        self.error_code = error_code

# ── Path safety ───────────────────────────────────────────────────────────────

def _safe_path(rel: str) -> Path:
    """
    Resolve rel to absolute, assert it's inside WORKSPACE_ROOT.
    Raises WorkspaceError on any escape attempt.
    """
    if not rel or rel.strip() == "":
        return WORKSPACE_ROOT

    # Detect absolute paths explicitly before stripping — clearer error code
    if os.path.isabs(rel):
        raise WorkspaceError(
            "WORKSPACE_ESCAPE",
            f"Absolute path not allowed: '{rel}'"
        )
    rel = rel.lstrip("/")
    candidate = (WORKSPACE_ROOT / rel).resolve()

    # The core invariant — must be inside workspace
    try:
        candidate.relative_to(WORKSPACE_ROOT)
    except ValueError:
        raise WorkspaceError(
            "WORKSPACE_ESCAPE",
            f"Path '{rel}' resolves outside workspace root"
        )
    return candidate

def _ensure_workspace() -> None:
    WORKSPACE_ROOT.mkdir(parents=True, exist_ok=True)

# ── Operations ────────────────────────────────────────────────────────────────

def _read(path: str, **_) -> dict:
    p = _safe_path(path)
    if not p.exists():
        raise WorkspaceError("WORKSPACE_NOTFOUND", f"Not found: {path}")
    if p.is_dir():
        raise WorkspaceError("WORKSPACE_NOTFOUND", f"Path is a directory: {path}")
    try:
        data = p.read_bytes()[:MAX_READ_BYTES]
        try:
            text = data.decode("utf-8")
        except UnicodeDecodeError:
            text = data.decode("utf-8", errors="replace")
        truncated = len(p.read_bytes()) > MAX_READ_BYTES
        suffix = f"\n[truncated at {MAX_READ_BYTES // 1024}KB]" if truncated else ""
        return {"ok": True, "result": text + suffix, "path": str(p.relative_to(WORKSPACE_ROOT)), "exit_code": 0}
    except OSError as e:
        raise WorkspaceError("WORKSPACE_IOERR", str(e))

def _write(path: str, content: str = "", **_) -> dict:
    p = _safe_path(path)
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")
        return {"ok": True, "result": f"Written {len(content)} chars to {p.relative_to(WORKSPACE_ROOT)}", "path": str(p.relative_to(WORKSPACE_ROOT)), "exit_code": 0}
    except OSError as e:
        raise WorkspaceError("WORKSPACE_IOERR", str(e))

def _append(path: str, content: str = "", **_) -> dict:
    p = _safe_path(path)
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        with open(p, "a", encoding="utf-8") as f:
            f.write(content)
        return {"ok": True, "result": f"Appended {len(content)} chars to {p.relative_to(WORKSPACE_ROOT)}", "path": str(p.relative_to(WORKSPACE_ROOT)), "exit_code": 0}
    except OSError as e:
        raise WorkspaceError("WORKSPACE_IOERR", str(e))

def _delete(path: str, **_) -> dict:
    p = _safe_path(path)
    if not p.exists():
        raise WorkspaceError("WORKSPACE_NOTFOUND", f"Not found: {path}")
    try:
        if p.is_dir():
            shutil.rmtree(p)
            return {"ok": True, "result": f"Deleted directory: {p.relative_to(WORKSPACE_ROOT)}", "path": str(p.relative_to(WORKSPACE_ROOT)), "exit_code": 0}
        else:
            p.unlink()
            return {"ok": True, "result": f"Deleted: {p.relative_to(WORKSPACE_ROOT)}", "path": str(p.relative_to(WORKSPACE_ROOT)), "exit_code": 0}
    except OSError as e:
        raise WorkspaceError("WORKSPACE_IOERR", str(e))

def _list(path: str = "", **_) -> dict:
    p = _safe_path(path)
    if not p.exists():
        raise WorkspaceError("WORKSPACE_NOTFOUND", f"Directory not found: {path or '(workspace root)'}")
    if not p.is_dir():
        raise WorkspaceError("WORKSPACE_NOTFOUND", f"Not a directory: {path}")
    try:
        entries = sorted(p.iterdir(), key=lambda e: (e.is_file(), e.name))
        lines = []
        for e in entries:
            rel = e.relative_to(WORKSPACE_ROOT)
            if e.is_dir():
                lines.append(f"[DIR]  {rel}/")
            else:
                size = e.stat().st_size
                lines.append(f"[FILE] {rel}  ({size:,} bytes)")
        result = "\n".join(lines) if lines else "(empty)"
        return {"ok": True, "result": result, "path": str(p.relative_to(WORKSPACE_ROOT) if p != WORKSPACE_ROOT else "."), "exit_code": 0}
    except OSError as e:
        raise WorkspaceError("WORKSPACE_IOERR", str(e))

def _mkdir(path: str, **_) -> dict:
    p = _safe_path(path)
    try:
        p.mkdir(parents=True, exist_ok=True)
        return {"ok": True, "result": f"Created directory: {p.relative_to(WORKSPACE_ROOT)}", "path": str(p.relative_to(WORKSPACE_ROOT)), "exit_code": 0}
    except OSError as e:
        raise WorkspaceError("WORKSPACE_IOERR", str(e))

def _move(path: str, content: str = "", **_) -> dict:
    """content field is reused as destination path."""
    src = _safe_path(path)
    dst = _safe_path(content)
    if not src.exists():
        raise WorkspaceError("WORKSPACE_NOTFOUND", f"Source not found: {path}")
    try:
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(src), str(dst))
        return {"ok": True, "result": f"Moved {src.relative_to(WORKSPACE_ROOT)} → {dst.relative_to(WORKSPACE_ROOT)}", "path": str(dst.relative_to(WORKSPACE_ROOT)), "exit_code": 0}
    except OSError as e:
        raise WorkspaceError("WORKSPACE_IOERR", str(e))

def _exists(path: str, **_) -> dict:
    p = _safe_path(path)
    exists = p.exists()
    kind   = "directory" if p.is_dir() else "file" if p.is_file() else "not found"
    return {"ok": True, "result": kind, "path": path, "exit_code": 0 if exists else 1}

def _run(command: str = "", timeout: int = DEFAULT_TIMEOUT, **_) -> dict:
    if not command.strip():
        raise WorkspaceError("WORKSPACE_EXEC_FAIL", "No command provided")
    if _BLOCKED.search(command):
        raise WorkspaceError("WORKSPACE_EXEC_BLOCKED", f"Command matches blocked pattern: {command[:80]}")

    timeout = min(max(1, int(timeout)), MAX_TIMEOUT)
    _ensure_workspace()

    t0 = time.time()
    try:
        result = subprocess.run(
            command,
            shell=True,
            cwd=str(WORKSPACE_ROOT),
            capture_output=True,
            timeout=timeout,
            env={**os.environ, "HOME": str(WORKSPACE_ROOT), "PWD": str(WORKSPACE_ROOT)},
        )
        combined = (result.stdout + result.stderr)[:MAX_OUTPUT_BYTES]
        output   = combined.decode("utf-8", errors="replace")
        elapsed  = round((time.time() - t0) * 1000)
        if len(result.stdout + result.stderr) > MAX_OUTPUT_BYTES:
            output += f"\n[output truncated at {MAX_OUTPUT_BYTES // 1024}KB]"
        return {
            "ok":        result.returncode == 0,
            "result":    output or "(no output)",
            "path":      ".",
            "exit_code": result.returncode,
        }
    except subprocess.TimeoutExpired:
        raise WorkspaceError("WORKSPACE_EXEC_TIMEOUT", f"Command timed out after {timeout}s: {command[:80]}")
    except OSError as e:
        raise WorkspaceError("WORKSPACE_EXEC_FAIL", str(e))

# ── Dispatch ──────────────────────────────────────────────────────────────────

_OPS = {
    "read":   _read,
    "write":  _write,
    "append": _append,
    "delete": _delete,
    "list":   _list,
    "mkdir":  _mkdir,
    "move":   _move,
    "exists": _exists,
    "run":    _run,
}

def workspace_tool(
    operation: str,
    path: str = "",
    content: str = "",
    command: str = "",
    timeout: int = DEFAULT_TIMEOUT,
) -> dict:
    """
    C-20 · WORKSPACE_TOOL entry point.
    Registered with ToolRegistry as 'workspace'.
    """
    _ensure_workspace()
    op = _OPS.get(operation)
    if op is None:
        return {
            "ok":        False,
            "result":    f"Unknown operation '{operation}'. Valid: {sorted(_OPS)}",
            "path":      path,
            "exit_code": 1,
        }
    try:
        return op(path=path, content=content, command=command, timeout=timeout)
    except WorkspaceError as e:
        return {
            "ok":        False,
            "result":    f"[{e.error_code}] {e}",
            "path":      path,
            "exit_code": 1,
        }
    except Exception as e:
        return {
            "ok":        False,
            "result":    f"[WORKSPACE_IOERR] Unexpected error: {e}",
            "path":      path,
            "exit_code": 1,
        }

# ── Tool schema (Anthropic format) ────────────────────────────────────────────


WORKSPACE_SCHEMA = {
    "name": "workspace",
    "description": (
        "Read, write, and manage files and folders in the agent workspace. "
        "Also run shell commands inside the workspace directory. "
        "All paths are relative to the workspace root — you cannot access files outside it."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "operation": {
                "type": "string",
                "enum": ["read", "write", "append", "delete", "list", "mkdir", "move", "exists", "run"],
                "description": (
                    "read: read a file. "
                    "write: create or overwrite a file (provide content). "
                    "append: append to a file. "
                    "delete: delete a file or directory. "
                    "list: list directory contents. "
                    "mkdir: create a directory. "
                    "move: move/rename (path=source, content=destination). "
                    "exists: check if path exists. "
                    "run: run a shell command in the workspace (provide command)."
                ),
            },
            "path": {
                "type": "string",
                "description": "Relative path within the workspace. E.g. 'src/main.py' or 'reports/output.csv'.",
            },
            "content": {
                "type": "string",
                "description": "File content for write/append, or destination path for move.",
            },
            "command": {
                "type": "string",
                "description": "Shell command to run (for operation=run). Runs in the workspace directory.",
            },
            "timeout": {
                "type": "integer",
                "description": "Timeout in seconds for run operations (default 30, max 120).",
            },
        },
        "required": ["operation"],
    },
}