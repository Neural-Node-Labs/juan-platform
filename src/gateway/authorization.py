"""
C-10 · AUTHORIZATION
Five-layer access control for all inbound gateway messages.
Default-deny on any exception (fail-safe).

Layers (evaluated in order, first match wins):
  1. platform_allow_all   — platform-level open access flag
  2. allowlist            — explicit user/chat allowlist
  3. dm_pairing           — DM pair-code verification
  4. global_allow_all     — global open access flag
  5. denied               — default
"""
from __future__ import annotations

import json
import os
from logger import trace, warn

CONFIG_PATH  = "juan_auth.json"
PAIRING_PATH = "juan_pairing.json"


def _load_json(path: str) -> dict:
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return json.load(fh)
    except FileNotFoundError:
        return {}
    except Exception as exc:
        raise RuntimeError(f"CONFIG_READ_FAIL: {exc}") from exc


def check(platform: str, user_id: str, chat_id: str, message_text: str) -> dict:
    """
    C-10 entry point.
    Returns {"authorized": bool, "reason": str, "method": str}.
    Never raises — any exception defaults to deny.
    """
    try:
        return _evaluate(platform, user_id, chat_id, message_text)
    except Exception as exc:
        warn("AUTHORIZATION", f"Auth exception (defaulting deny): {exc}")
        return {"authorized": False, "reason": str(exc), "method": "denied"}


def _evaluate(platform: str, user_id: str, chat_id: str, message_text: str) -> dict:
    # Load config
    try:
        config = _load_json(CONFIG_PATH)
    except Exception as exc:
        trace("AUTHORIZATION", "auth_method",
              layer_reached=1, method_used="denied")
        trace("AUTHORIZATION", "deny_reason",
              platform=platform, user_id=user_id, reason="CONFIG_READ_FAIL")
        return {"authorized": False, "reason": "CONFIG_READ_FAIL", "method": "denied"}

    # Layer 1 — platform_allow_all
    platform_cfg = config.get("platforms", {}).get(platform, {})
    if platform_cfg.get("allow_all"):
        trace("AUTHORIZATION", "auth_method",
              layer_reached=1, method_used="platform_allow_all")
        return {"authorized": True, "reason": "platform allow-all", "method": "platform_allow_all"}

    # Layer 2 — allowlist
    allowlist = config.get("allowlist", {})
    if user_id in allowlist.get("users", []) or chat_id in allowlist.get("chats", []):
        trace("AUTHORIZATION", "auth_method",
              layer_reached=2, method_used="allowlist")
        trace("AUTHORIZATION", "allowlist_hit", platform=platform, user_id=user_id)
        return {"authorized": True, "reason": "allowlist", "method": "allowlist"}

    # Layer 3 — DM pairing
    try:
        pairing = _load_json(PAIRING_PATH)
    except Exception as exc:
        trace("AUTHORIZATION", "deny_reason",
              platform=platform, user_id=user_id, reason="PAIRING_DB_FAIL")
        return {"authorized": False, "reason": "PAIRING_DB_FAIL", "method": "denied"}

    paired_users = pairing.get("paired", {})
    if user_id in paired_users:
        trace("AUTHORIZATION", "auth_method",
              layer_reached=3, method_used="dm_pairing")
        trace("AUTHORIZATION", "pair_code_checked", valid=True, user_id=user_id)
        return {"authorized": True, "reason": "DM paired", "method": "dm_pairing"}

    # Check if this IS a pairing request
    pair_code = config.get("pair_code", "")
    if pair_code and message_text.strip() == pair_code:
        # Register pairing
        try:
            paired_users[user_id] = {"platform": platform, "chat_id": chat_id}
            with open(PAIRING_PATH, "w", encoding="utf-8") as fh:
                json.dump({"paired": paired_users}, fh)
        except Exception:
            pass
        trace("AUTHORIZATION", "pair_code_checked", valid=True, user_id=user_id)
        return {"authorized": True, "reason": "pair code matched", "method": "dm_pairing"}

    # Layer 4 — global_allow_all
    if config.get("allow_all"):
        trace("AUTHORIZATION", "auth_method",
              layer_reached=4, method_used="global_allow_all")
        return {"authorized": True, "reason": "global allow-all", "method": "global_allow_all"}

    # Layer 5 — deny
    trace("AUTHORIZATION", "auth_method",
          layer_reached=5, method_used="denied")
    trace("AUTHORIZATION", "deny_reason",
          platform=platform, user_id=user_id, reason="no matching rule")
    return {"authorized": False, "reason": "no matching rule", "method": "denied"}
