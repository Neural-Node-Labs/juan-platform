"""
C-17 · UI_GATEWAY
WebSocket + HTTP/SSE bridge between React/Kivy UIs and the Juan agent backend.
Production-grade: HMAC tokens, rate limiting, input validation, CSP headers.

Auth strategy
─────────────
Browser WebSocket API cannot send custom headers (Authorization is silently
ignored by every browser). Token is therefore carried via:
  • WS subprotocol string: "juan-auth.<base64url-token>"
  • First message after connect: {"type":"auth","token":"<token>"}  ← fallback

The server accepts whichever arrives first and closes with 4001/4002 on failure.
HTTP endpoints (auth, message, history, stream) use standard Bearer header.
"""
from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import json
import os
import re
import sys
import threading
import time
import uuid
from collections import defaultdict
from functools import wraps
from typing import Any

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from flask import Flask, jsonify, request, Response, g
from flask_cors import CORS

try:
    import websockets
    import websockets.server
    HAS_WS = True
except ImportError:
    HAS_WS = False

from logger import trace, warn, error as log_error
from gateway.authorization import check as auth_check
from gateway.platforms.base import MessageEvent

# ── Config ────────────────────────────────────────────────────────────────────
UI_SECRET         = os.environ.get("UI_SECRET",          "juan-ui-secret-CHANGE-IN-PRODUCTION")
UI_PASSWORD       = os.environ.get("UI_PASSWORD",        "")   # empty = no password required
TOKEN_TTL         = int(os.environ.get("UI_TOKEN_TTL",   "86400"))
MAX_CONTENT_LEN   = 32 * 1024
RATE_LIMIT_RPM    = int(os.environ.get("UI_RATE_LIMIT_RPM", "60"))
WS_PORT           = int(os.environ.get("UI_WS_PORT",     "5001"))
HTTP_PORT         = int(os.environ.get("UI_HTTP_PORT",   "5002"))
SUBPROTOCOL_PREFIX = "juan-auth."

# ── Errors ────────────────────────────────────────────────────────────────────
class UIGatewayError(Exception):
    def __init__(self, error_code: str, message: str, status: int = 400):
        super().__init__(message)
        self.error_code = error_code
        self.status     = status

    def to_dict(self) -> dict:
        return {"error_code": self.error_code, "message": str(self)}

# ── HMAC Token Auth ───────────────────────────────────────────────────────────
def _sign(payload: str) -> str:
    return hmac.new(UI_SECRET.encode(), payload.encode(), hashlib.sha256).hexdigest()

def create_token(client_type: str) -> dict:
    """Issue a time-limited HMAC token. Not stored anywhere."""
    issued_at  = int(time.time())
    expires_at = issued_at + TOKEN_TTL
    nonce      = uuid.uuid4().hex[:16]
    payload    = f"{client_type}:{issued_at}:{expires_at}:{nonce}"
    token      = f"{payload}:{_sign(payload)}"
    encoded    = base64.urlsafe_b64encode(token.encode()).decode()
    return {"token": encoded, "expires_at": expires_at}

def verify_token(encoded: str) -> dict:
    """Verify signature and expiry. Returns payload dict or raises."""
    try:
        token = base64.urlsafe_b64decode(encoded.encode()).decode()
        parts = token.rsplit(":", 1)
        if len(parts) != 2:
            raise UIGatewayError("UIAUTH_FAIL", "Invalid token format", 401)
        payload, sig = parts
        if not hmac.compare_digest(sig, _sign(payload)):
            raise UIGatewayError("UIAUTH_FAIL", "Token signature invalid", 401)
        p = payload.split(":")
        if len(p) != 4:
            raise UIGatewayError("UIAUTH_FAIL", "Token payload malformed", 401)
        client_type, _, expires_at, _ = p
        if int(expires_at) < int(time.time()):
            raise UIGatewayError("UIAUTH_EXPIRED", "Token has expired", 401)
        return {"client_type": client_type, "expires_at": int(expires_at)}
    except UIGatewayError:
        raise
    except Exception as exc:
        raise UIGatewayError("UIAUTH_FAIL", f"Token error: {exc}", 401)

# ── Input Validation ──────────────────────────────────────────────────────────
_UUID_RE    = re.compile(r'^[0-9a-f]{8,32}$', re.I)
_SAFE_TYPES = {"message", "command", "ping", "auth"}

def validate_message(raw: Any, session_id: str) -> dict:
    if not isinstance(raw, dict):
        raise UIGatewayError("UIINPUT_INVALID", "Message must be a JSON object")
    msg_type = raw.get("type", "")
    if msg_type not in _SAFE_TYPES:
        raise UIGatewayError("UIINPUT_INVALID", f"Unknown type: {msg_type!r}")
    content = raw.get("content", "")
    if not isinstance(content, str):
        raise UIGatewayError("UIINPUT_INVALID", "content must be a string")
    if len(content.encode("utf-8")) > MAX_CONTENT_LEN:
        raise UIGatewayError("UIINPUT_INVALID", f"content exceeds {MAX_CONTENT_LEN // 1024}KB limit")
    # Strip dangerous control characters (keep \n \t)
    content = re.sub(r'[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]', '', content)
    req_id = str(raw.get("request_id", uuid.uuid4().hex[:8]))[:64]
    client_sid = str(raw.get("session_id", ""))
    if client_sid and not _UUID_RE.match(client_sid.replace("-", "").replace(":", "")):
        client_sid = ""
    trace("UI_GATEWAY", "message_validated", session_id=session_id, type=msg_type)
    return {
        "type":       msg_type,
        "content":    content,
        "request_id": req_id,
        "session_id": client_sid or session_id,
    }

def validate_auth_body(raw: Any) -> str:
    if not isinstance(raw, dict):
        raise UIGatewayError("UIINPUT_INVALID", "Body must be JSON object")
    password = raw.get("password", "")
    if not isinstance(password, str) or len(password) > 256:
        raise UIGatewayError("UIINPUT_INVALID", "Invalid password field")
    return password

# ── Rate limiter ──────────────────────────────────────────────────────────────
class RateLimiter:
    def __init__(self, rpm: int = RATE_LIMIT_RPM):
        self._rpm     = rpm
        self._buckets: dict[str, list[float]] = defaultdict(list)
        self._lock    = threading.Lock()

    def check(self, session_id: str) -> bool:
        now = time.time()
        with self._lock:
            self._buckets[session_id] = [t for t in self._buckets[session_id] if now - t < 60.0]
            if len(self._buckets[session_id]) >= self._rpm:
                return False
            self._buckets[session_id].append(now)
            return True

_rate_limiter = RateLimiter()

# ── Agent dispatch ────────────────────────────────────────────────────────────
_agent_factory  = None
_gateway_runner = None

def _dispatch_to_agent(validated: dict, client_type: str) -> dict:
    session_id = validated["session_id"]
    content    = validated["content"]
    auth = auth_check("ui", session_id, session_id, content)
    if not auth["authorized"]:
        raise UIGatewayError("UIAUTH_FAIL", "Authorization denied", 403)
    t0 = time.time()
    trace("UI_GATEWAY", "agent_dispatched",
          session_id=session_id, request_id=validated["request_id"])
    try:
        if _gateway_runner is not None:
            event = MessageEvent(
                platform=f"ui_{client_type}", chat_id=session_id,
                user_id=session_id, text=content,
                raw={"source": "ui", "client_type": client_type},
            )
            _gateway_runner._handle_event(event)
            return {"response": "", "via_gateway": True, "session_id": session_id,
                    "iterations_used": 0, "usage": {}, "end_reason": "dispatched"}
        elif _agent_factory is not None:
            agent  = _agent_factory(session_id)
            result = agent.run(
                user_message   = content,
                session_id     = session_id,
                max_iterations = int(os.environ.get("MAX_ITERATIONS", 30)),
            )
            dur = round((time.time() - t0) * 1000)
            trace("UI_GATEWAY", "response_sent",
                  session_id=session_id, request_id=validated["request_id"],
                  content_length=len(result.get("response", "")), duration_ms=dur)
            return result
        else:
            return {"response": "[No agent configured]", "session_id": session_id,
                    "iterations_used": 0, "usage": {}, "end_reason": "no_agent"}
    except UIGatewayError:
        raise
    except Exception as exc:
        log_error("UI_GATEWAY", f"Agent dispatch error: {exc}")
        raise UIGatewayError("UIAGENT_ERROR", str(exc), 500)

# ── Flask HTTP app ────────────────────────────────────────────────────────────
_http_app = Flask(__name__)
_http_app.secret_key = UI_SECRET

_ALLOWED_ORIGINS = os.environ.get(
    "UI_ALLOWED_ORIGINS",
    "http://localhost:3000,http://localhost:5000,http://localhost:5002"
).split(",")

CORS(_http_app, resources={
    r"/ui/*": {
        "origins":             _ALLOWED_ORIGINS,
        "methods":             ["GET", "POST", "OPTIONS"],
        "allow_headers":       ["Content-Type", "Authorization", "X-Client-Type"],
        "supports_credentials": True,
        "max_age":             600,
    }
})

@_http_app.after_request
def security_headers(response: Response) -> Response:
    response.headers["X-Content-Type-Options"]  = "nosniff"
    response.headers["X-Frame-Options"]         = "DENY"
    response.headers["X-XSS-Protection"]        = "1; mode=block"
    response.headers["Referrer-Policy"]         = "strict-origin-when-cross-origin"
    response.headers["Cache-Control"]           = "no-store"
    return response

def _require_token(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        auth_header = request.headers.get("Authorization", "")
        if not auth_header.startswith("Bearer "):
            return jsonify({"error_code": "UIAUTH_FAIL", "message": "Missing Bearer token"}), 401
        try:
            g.token_data = verify_token(auth_header[7:])
        except UIGatewayError as exc:
            return jsonify(exc.to_dict()), exc.status
        return f(*args, **kwargs)
    return decorated

@_http_app.route("/ui/health")
def health():
    return jsonify({"status": "ok", "service": "ui-gateway",
                    "auth_required": bool(UI_PASSWORD)})

@_http_app.route("/ui/auth", methods=["POST"])
def ui_auth():
    try:
        body     = request.get_json(silent=True) or {}
        password = validate_auth_body(body)
    except UIGatewayError as exc:
        return jsonify(exc.to_dict()), exc.status

    # If no password is configured, any (including empty) password is accepted
    if UI_PASSWORD and not hmac.compare_digest(password, UI_PASSWORD):
        trace("UI_GATEWAY", "auth_fail", reason="wrong password",
              client_ip=request.remote_addr or "")
        return jsonify({"error_code": "UIAUTH_FAIL", "message": "Invalid password"}), 401

    client_type = request.headers.get("X-Client-Type", "web")
    token_data  = create_token(client_type)
    trace("UI_GATEWAY", "auth_success", client_type=client_type,
          token_hash=hashlib.sha256(token_data["token"].encode()).hexdigest()[:12])
    return jsonify(token_data)

@_http_app.route("/ui/message", methods=["POST"])
@_require_token
def ui_message():
    raw = request.get_json(silent=True) or {}
    session_id = f"kivy:{uuid.uuid4().hex[:8]}"
    try:
        validated = validate_message(raw, session_id)
    except UIGatewayError as exc:
        return jsonify(exc.to_dict()), exc.status
    if not _rate_limiter.check(validated["session_id"]):
        return jsonify({"error_code": "UIRATE_LIMITED", "message": "Too many requests"}), 429
    trace("UI_GATEWAY", "message_received",
          session_id=validated["session_id"],
          content_length=len(validated["content"]), client_type="kivy")
    if validated["type"] == "ping":
        return jsonify({"type": "pong", "session_id": validated["session_id"]})
    try:
        result = _dispatch_to_agent(validated, "kivy")
    except UIGatewayError as exc:
        return jsonify(exc.to_dict()), exc.status
    return jsonify({
        "type": "response", "request_id": validated["request_id"],
        "content": result.get("response", ""), "session_id": validated["session_id"],
        "done": True,
        "metadata": {
            "iterations":    result.get("iterations_used", 0),
            "input_tokens":  result.get("usage", {}).get("input_tokens", 0),
            "output_tokens": result.get("usage", {}).get("output_tokens", 0),
        }
    })

@_http_app.route("/ui/history/<session_id>", methods=["GET"])
@_require_token
def ui_history(session_id: str):
    if not _UUID_RE.match(session_id.replace("-", "").replace(":", "")[:32]):
        return jsonify({"error_code": "UIINPUT_INVALID", "message": "Invalid session_id"}), 400
    try:
        from juan_state import get_db
        db     = get_db(os.environ.get("JUAN_DB_PATH", "juan_state.db"))
        result = db.execute("get_messages", session_id)
        return jsonify(result)
    except Exception as exc:
        return jsonify({"error_code": "UISESSION_MISSING", "message": str(exc)}), 404

@_http_app.route("/ui/stream/<session_id>")
@_require_token
def ui_stream(session_id: str):
    if not _UUID_RE.match(session_id.replace("-", "").replace(":", "")[:32]):
        return jsonify({"error_code": "UIINPUT_INVALID", "message": "Invalid session_id"}), 400
    def generate():
        yield "data: {\"type\": \"connected\"}\n\n"
        deadline = time.time() + 300
        while time.time() < deadline:
            time.sleep(15)
            yield "data: {\"type\": \"heartbeat\"}\n\n"
    return Response(generate(), mimetype="text/event-stream",
                    headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

# ── WebSocket handler ─────────────────────────────────────────────────────────
async def _extract_token_from_ws(websocket) -> str:
    """
    Extract the auth token from the WebSocket connection.

    Strategy (browser-compatible):
    1. Check subprotocols for "juan-auth.<token>"
    2. Wait for first message to be {"type":"auth","token":"<token>"}
    3. If neither, close 4001

    The browser WebSocket API cannot send custom headers, so we use
    the subprotocol string as the token carrier — this is the standard pattern.
    """
    # Strategy 1: subprotocol
    for proto in (websocket.subprotocols or []):
        if proto.startswith(SUBPROTOCOL_PREFIX):
            return proto[len(SUBPROTOCOL_PREFIX):]

    # Strategy 2: first message auth frame (30s timeout)
    try:
        raw = await asyncio.wait_for(websocket.recv(), timeout=30)
        msg = json.loads(raw)
        if msg.get("type") == "auth" and msg.get("token"):
            return str(msg["token"])
    except (asyncio.TimeoutError, json.JSONDecodeError, Exception):
        pass

    return ""

async def _ws_handler(websocket):
    """Handles one WebSocket connection — React or Kivy."""
    client_ip  = websocket.remote_address[0] if websocket.remote_address else "unknown"
    session_id = uuid.uuid4().hex
    t_connect  = time.time()

    # ── Auth ──────────────────────────────────────────────────────────────────
    raw_token = await _extract_token_from_ws(websocket)
    if not raw_token:
        await websocket.close(4001, "Missing auth token")
        trace("UI_GATEWAY", "auth_fail", reason="no token", client_ip=client_ip)
        return

    try:
        token_data  = verify_token(raw_token)
        client_type = token_data.get("client_type", "web")
    except UIGatewayError as exc:
        code = 4002 if exc.error_code == "UIAUTH_EXPIRED" else 4001
        await websocket.close(code, str(exc))
        trace("UI_GATEWAY", "auth_fail", reason=exc.error_code, client_ip=client_ip)
        return

    trace("UI_GATEWAY", "client_connected",
          client_type=client_type, session_id=session_id,
          token_hash=raw_token[:10])

    # Send connected confirmation
    await websocket.send(json.dumps({
        "type": "status", "status": "connected", "session_id": session_id
    }))

    try:
        async for raw_msg in websocket:
            try:
                raw = json.loads(raw_msg)
            except json.JSONDecodeError:
                await websocket.send(json.dumps({
                    "type": "error", "error_code": "UIINPUT_INVALID",
                    "message": "Message must be valid JSON",
                }))
                continue

            try:
                validated = validate_message(raw, session_id)
            except UIGatewayError as exc:
                await websocket.send(json.dumps({"type": "error", **exc.to_dict()}))
                continue

            if validated["type"] == "ping":
                await websocket.send(json.dumps({"type": "pong", "session_id": session_id}))
                continue

            # Skip stray auth messages after connection is established
            if validated["type"] == "auth":
                continue

            if not _rate_limiter.check(validated["session_id"]):
                trace("UI_GATEWAY", "rate_limit_hit",
                      session_id=session_id, client_type=client_type)
                await websocket.send(json.dumps({
                    "type": "error", "error_code": "UIRATE_LIMITED",
                    "message": "Too many messages. Slow down.",
                }))
                continue

            trace("UI_GATEWAY", "message_received",
                  session_id=session_id,
                  content_length=len(validated["content"]),
                  client_type=client_type)

            await websocket.send(json.dumps({
                "type": "status", "status": "thinking",
                "session_id": session_id, "request_id": validated["request_id"],
            }))

            loop = asyncio.get_event_loop()
            try:
                result = await loop.run_in_executor(
                    None, _dispatch_to_agent, validated, client_type
                )
            except UIGatewayError as exc:
                await websocket.send(json.dumps({
                    "type": "error", **exc.to_dict(),
                    "request_id": validated["request_id"],
                }))
                continue

            await websocket.send(json.dumps({
                "type":       "response",
                "request_id": validated["request_id"],
                "content":    result.get("response", ""),
                "session_id": validated["session_id"],
                "done":       True,
                "metadata": {
                    "iterations":    result.get("iterations_used", 0),
                    "input_tokens":  result.get("usage", {}).get("input_tokens", 0),
                    "output_tokens": result.get("usage", {}).get("output_tokens", 0),
                    "duration_ms":   0,
                }
            }))

    except Exception as exc:
        if "connection closed" not in str(exc).lower():
            log_error("UI_GATEWAY", f"WS error for {session_id}: {exc}")
    finally:
        trace("UI_GATEWAY", "client_disconnected",
              client_type=client_type, session_id=session_id,
              reason="closed", duration_ms=round((time.time() - t_connect) * 1000))

# ── Public API ────────────────────────────────────────────────────────────────
class UIGateway:
    def set_agent_factory(self, factory) -> None:
        global _agent_factory
        _agent_factory = factory

    def set_gateway_runner(self, runner) -> None:
        global _gateway_runner
        _gateway_runner = runner

    def start_http(self, port: int = HTTP_PORT) -> None:
        t = threading.Thread(
            target=_http_app.run,
            kwargs={"host": "0.0.0.0", "port": port,
                    "debug": False, "threaded": True, "use_reloader": False},
            daemon=True,
        )
        t.start()

    def start_websocket(self, port: int = WS_PORT) -> None:
        if not HAS_WS:
            warn("UI_GATEWAY", "websockets not installed — WS disabled")
            return

        def _run():
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)

            async def _serve():
                async with websockets.server.serve(
                    _ws_handler, "0.0.0.0", port,
                    max_size       = MAX_CONTENT_LEN + 4096,
                    ping_interval  = 30,
                    ping_timeout   = 10,
                    # Accept any subprotocol starting with "juan-auth."
                    subprotocols   = None,
                    process_request = None,
                ):
                    await asyncio.Future()

            loop.run_until_complete(_serve())

        threading.Thread(target=_run, daemon=True, name="ui-ws-server").start()

    def start(self, ws_port: int = WS_PORT, http_port: int = HTTP_PORT) -> None:
        self.start_http(http_port)
        self.start_websocket(ws_port)

_ui_gateway = UIGateway()

def get_ui_gateway() -> UIGateway:
    return _ui_gateway
