"""
Project Juan — Structured JSON Logger
All components emit trace events through this module.
"""
import json
import time
import logging
import sys
from typing import Any

_log = logging.getLogger("juan")
_log.setLevel(logging.DEBUG)
_handler = logging.StreamHandler(sys.stdout)
_handler.setFormatter(logging.Formatter("%(message)s"))
_log.addHandler(_handler)


def trace(component: str, event: str, session_id: str = "", **kwargs: Any) -> None:
    record = {
        "timestamp": time.time(),
        "phase": "runtime",
        "component": component,
        "event": event,
        "session_id": session_id,
        "duration_ms": kwargs.pop("duration_ms", None),
        **kwargs,
    }
    _log.debug(json.dumps({k: v for k, v in record.items() if v is not None}))


def warn(component: str, msg: str, **kwargs: Any) -> None:
    _log.warning(json.dumps({"component": component, "level": "WARN", "msg": msg, **kwargs}))


def error(component: str, msg: str, **kwargs: Any) -> None:
    _log.error(json.dumps({"component": component, "level": "ERROR", "msg": msg, **kwargs}))
