"""
C-23 · OS_TOOL
Gives the agent access to OS-level operations beyond the workspace jail.
DISABLED by default — requires JUAN_ENABLE_OS_TOOL=true in environment.

When enabled, the agent can:
  - env      : read environment variables (filtered — secrets masked)
  - processes: list running processes
  - sysinfo  : CPU, memory, disk usage
  - network  : list network interfaces
  - which    : find a binary in PATH
  - shell    : run an arbitrary shell command (approval gate applies)

Security:
  - Disabled unless JUAN_ENABLE_OS_TOOL=true
  - Secret env vars (containing KEY, TOKEN, SECRET, PASSWORD, PASS) are masked
  - shell operation triggers the APPROVAL_GATE at the "high" threshold
  - shell commands are NOT run inside the workspace jail (use workspace.run for that)
  - Output capped at 32KB

CBD CONTRACT
═══════════════════════════════════════════════════════
IN:  operation  str   env|processes|sysinfo|network|which|shell
     command    str?  for shell operation
     name       str?  for env (specific var) or which (binary name)
     timeout    int?  for shell, default 30, max 120

OUT: {"ok": bool, "result": str, "exit_code": int}

ERROR CODES:
  OS_DISABLED   JUAN_ENABLE_OS_TOOL is not set or is not "true"
  OS_EXEC_FAIL  shell command failed
  OS_EXEC_TIMEOUT shell command timed out
  OS_EXEC_BLOCKED dangerous pattern matched
═══════════════════════════════════════════════════════
"""
from __future__ import annotations

import os
import re
import subprocess
import time

from logger import trace, warn

# ── Config ────────────────────────────────────────────────────────────────────
ENABLED         = os.environ.get("JUAN_ENABLE_OS_TOOL", "").lower() in ("true", "1", "yes")
MAX_OUTPUT      = 32 * 1024
DEFAULT_TIMEOUT = 30
MAX_TIMEOUT     = 120

# Env var names that should be masked
_SECRET_RE = re.compile(
    r'(key|token|secret|password|pass|api|auth|credential|private)',
    re.IGNORECASE
)

# Dangerous patterns (same set as workspace, applied to shell commands)
_BLOCKED = re.compile(
    r"rm\s+-rf\s+/|mkfs|dd\s+if=.*of=/dev|:\s*\(\s*\)\s*\{|"
    r"chmod\s+-R\s+777\s+/|>\s*/etc/|curl\s+.*\|\s*bash|"
    r"wget\s+.*\|\s*bash|python\s+-c.*exec\s*\(|eval\s+.*base64",
    re.IGNORECASE,
)

class OsToolError(Exception):
    def __init__(self, error_code: str, message: str):
        super().__init__(message)
        self.error_code = error_code

# ── Operations ────────────────────────────────────────────────────────────────
def _env(name: str = "", **_) -> dict:
    if name:
        val = os.environ.get(name, "")
        if val and _SECRET_RE.search(name):
            val = val[:4] + "****" + val[-4:] if len(val) > 8 else "****"
        return {"ok": True, "result": f"{name}={val}", "exit_code": 0}

    lines = []
    for k, v in sorted(os.environ.items()):
        if _SECRET_RE.search(k):
            v = v[:4] + "****" + v[-4:] if len(v) > 8 else "****"
        lines.append(f"{k}={v}")
    return {"ok": True, "result": "\n".join(lines[:200]), "exit_code": 0}

def _processes(**_) -> dict:
    try:
        r = subprocess.run(["ps", "aux"], capture_output=True, timeout=10)
        out = r.stdout.decode("utf-8", errors="replace")[:MAX_OUTPUT]
        return {"ok": True, "result": out, "exit_code": r.returncode}
    except FileNotFoundError:
        # Windows fallback
        r = subprocess.run(["tasklist"], capture_output=True, timeout=10, shell=True)
        return {"ok": True, "result": r.stdout.decode(errors="replace")[:MAX_OUTPUT], "exit_code": 0}
    except Exception as e:
        return {"ok": False, "result": str(e), "exit_code": 1}

def _sysinfo(**_) -> dict:
    lines = []
    try:
        import shutil
        total, used, free = shutil.disk_usage("/")
        lines.append(f"Disk /  total={total//1024//1024}MB used={used//1024//1024}MB free={free//1024//1024}MB")
    except Exception: pass
    try:
        with open("/proc/meminfo") as f:
            for line in f.readlines()[:5]:
                lines.append(line.strip())
    except Exception: pass
    try:
        with open("/proc/loadavg") as f:
            lines.append("Load avg: " + f.read().strip())
    except Exception: pass
    try:
        import platform
        lines.append(f"Platform: {platform.platform()}")
        lines.append(f"Python:   {platform.python_version()}")
    except Exception: pass
    return {"ok": True, "result": "\n".join(lines) or "sysinfo unavailable", "exit_code": 0}

def _network(**_) -> dict:
    try:
        r = subprocess.run(["ip", "addr"], capture_output=True, timeout=10)
        return {"ok": True, "result": r.stdout.decode(errors="replace")[:MAX_OUTPUT], "exit_code": 0}
    except FileNotFoundError:
        try:
            r = subprocess.run(["ifconfig"], capture_output=True, timeout=10)
            return {"ok": True, "result": r.stdout.decode(errors="replace")[:MAX_OUTPUT], "exit_code": 0}
        except Exception as e:
            return {"ok": False, "result": str(e), "exit_code": 1}

def _which(name: str = "", **_) -> dict:
    if not name:
        return {"ok": False, "result": "Provide name=<binary>", "exit_code": 1}
    import shutil
    path = shutil.which(name)
    if path:
        return {"ok": True, "result": path, "exit_code": 0}
    return {"ok": False, "result": f"{name}: not found in PATH", "exit_code": 1}

def _shell(command: str = "", timeout: int = DEFAULT_TIMEOUT, **_) -> dict:
    if not command.strip():
        raise OsToolError("OS_EXEC_FAIL", "No command provided")
    if _BLOCKED.search(command):
        raise OsToolError("OS_EXEC_BLOCKED",
                          f"Command matches blocked pattern: {command[:80]}")
    timeout = min(max(1, int(timeout)), MAX_TIMEOUT)
    trace("OS_TOOL", "shell_run", command_preview=command[:80])
    t0 = time.time()
    try:
        result = subprocess.run(
            command, shell=True,
            capture_output=True, timeout=timeout,
        )
        combined = (result.stdout + result.stderr)[:MAX_OUTPUT]
        output   = combined.decode("utf-8", errors="replace")
        if len(result.stdout + result.stderr) > MAX_OUTPUT:
            output += f"\n[output truncated at {MAX_OUTPUT//1024}KB]"
        return {"ok": result.returncode == 0, "result": output or "(no output)",
                "exit_code": result.returncode}
    except subprocess.TimeoutExpired:
        raise OsToolError("OS_EXEC_TIMEOUT", f"Command timed out after {timeout}s")
    except OSError as e:
        raise OsToolError("OS_EXEC_FAIL", str(e))

# ── Dispatch ──────────────────────────────────────────────────────────────────
_OPS = {
    "env":       _env,
    "processes": _processes,
    "sysinfo":   _sysinfo,
    "network":   _network,
    "which":     _which,
    "shell":     _shell,
}

def os_tool(
    operation: str  = "sysinfo",
    name:      str  = "",
    command:   str  = "",
    timeout:   int  = DEFAULT_TIMEOUT,
) -> dict:
    """C-23 · OS_TOOL entry point registered as 'os'."""
    if not ENABLED:
        return {
            "ok":       False,
            "result":   (
                "OS tool is disabled. "
                "Set JUAN_ENABLE_OS_TOOL=true in your .env to enable it. "
                "Use the workspace tool for file and shell operations inside the workspace."
            ),
            "exit_code": 1,
        }
    op = _OPS.get(operation)
    if op is None:
        return {"ok": False,
                "result": f"Unknown operation '{operation}'. Valid: {sorted(_OPS)}",
                "exit_code": 1}
    try:
        return op(name=name, command=command, timeout=timeout)
    except OsToolError as e:
        return {"ok": False, "result": f"[{e.error_code}] {e}", "exit_code": 1}
    except Exception as e:
        return {"ok": False, "result": f"[OS_EXEC_FAIL] Unexpected: {e}", "exit_code": 1}

# ── Schema ────────────────────────────────────────────────────────────────────
OS_TOOL_SCHEMA = {
    "type": "custom",
    "name": "os",
    "description": (
        "Access OS-level information and run system-wide shell commands. "
        "ONLY available when JUAN_ENABLE_OS_TOOL=true. "
        "For file and workspace-scoped commands, use the workspace tool instead."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "operation": {
                "type": "string",
                "enum": ["env", "processes", "sysinfo", "network", "which", "shell"],
                "description": (
                    "env: list/read environment variables (secrets masked). "
                    "processes: list running processes. "
                    "sysinfo: CPU/memory/disk/platform info. "
                    "network: list network interfaces. "
                    "which: find a binary in PATH. "
                    "shell: run an arbitrary shell command (requires approval)."
                ),
            },
            "name": {
                "type": "string",
                "description": "For env: specific variable name. For which: binary name.",
            },
            "command": {
                "type": "string",
                "description": "Shell command for operation=shell.",
            },
            "timeout": {
                "type": "integer",
                "description": "Timeout seconds for shell (default 30, max 120).",
            },
        },
        "required": ["operation"],
    },
}
