"""
C-17 · UI_GATEWAY
WebSocket + HTTP/SSE bridge between React/Kivy UIs and the Juan agent backend.
Production-grade: HMAC tokens, rate limiting, input validation, CSP headers.
"""
from __future__ import annotations

import asyncio
import hashlib
import hmac
import html
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
UI_SECRET        = os.environ.get("UI_SECRET", "juan-ui-secret-CHANGE-IN-PRODUCTION")
UI_PASSWORD      = os.environ.get("UI_PASSWORD", "")
TOKEN_TTL        = int(os.environ.get("UI_TOKEN_TTL", "86400"))   # 24h
MAX_CONTENT_LEN  = 32 * 1024                                       # 32KB
RATE_LIMIT_RPM   = int(os.environ.get("UI_RATE_LIMIT_RPM", "60")) # per minute
WS_PORT          = int(os.environ.get("UI_WS_PORT", "5001"))
HTTP_PORT        = int(os.environ.get("UI_HTTP_PORT", "5002"))

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
    """Issue a time-limited HMAC token. No database storage needed."""
    issued_at  = int(time.time())
    expires_at = issued_at + TOKEN_TTL
    nonce      = uuid.uuid4().hex[:16]
    payload    = f"{client_type}:{issued_at}:{expires_at}:{nonce}"
    sig        = _sign(payload)
    token      = f"{payload}:{sig}"
    # base64url-encode for safe transport
    import base64
    encoded = base64.urlsafe_b64encode(token.encode()).decode()
    return {"token": encoded, "expires_at": expires_at}

def verify_token(encoded: str) -> dict:
    """Verify token signature and expiry. Returns payload dict or raises."""
    try:
        import base64
        token    = base64.urlsafe_b64decode(encoded.encode()).decode()
        parts    = token.rsplit(":", 1)
        if len(parts) != 2:
            raise UIGatewayError("UIAUTH_FAIL", "Invalid token format", 401)
        payload, sig = parts
        expected     = _sign(payload)
        # Constant-time compare to prevent timing attacks
        if not hmac.compare_digest(sig, expected):
            raise UIGatewayError("UIAUTH_FAIL", "Token signature invalid", 401)
        p_parts = payload.split(":")
        if len(p_parts) != 4:
            raise UIGatewayError("UIAUTH_FAIL", "Token payload malformed", 401)
        client_type, issued_at, expires_at, nonce = p_parts
        if int(expires_at) < int(time.time()):
            raise UIGatewayError("UIAUTH_EXPIRED", "Token has expired", 401)
        return {"client_type": client_type, "expires_at": int(expires_at)}
    except UIGatewayError:
        raise
    except Exception as exc:
        raise UIGatewayError("UIAUTH_FAIL", f"Token verification failed: {exc}", 401)

# ── Input Validation ──────────────────────────────────────────────────────────

_UUID_RE    = re.compile(r'^[0-9a-f]{8}-?[0-9a-f]{4}-?[0-9a-f]{4}-?[0-9a-f]{4}-?[0-9a-f]{12}$', re.I)
_SAFE_TYPES = {"message", "command", "ping"}

def validate_message(raw: Any, session_id: str) -> dict:
    """
    Validate and sanitise an inbound UI message.
    Returns cleaned dict or raises UIGatewayError.
    Never trusts the client.
    """
    if not isinstance(raw, dict):
        raise UIGatewayError("UIINPUT_INVALID", "Message must be a JSON object", 400)

    msg_type = raw.get("type", "")
    if msg_type not in _SAFE_TYPES:
        trace("UI_GATEWAY", "validation_fail",
              session_id=session_id, field="type", reason=f"unknown type: {msg_type!r}")
        raise UIGatewayError("UIINPUT_INVALID", f"Unknown message type: {msg_type!r}", 400)

    content = raw.get("content", "")
    if not isinstance(content, str):
        raise UIGatewayError("UIINPUT_INVALID", "content must be a string", 400)
    if len(content.encode("utf-8")) > MAX_CONTENT_LEN:
        raise UIGatewayError("UIINPUT_INVALID",
                             f"content exceeds {MAX_CONTENT_LEN // 1024}KB limit", 400)
    # Strip null bytes and control characters (keep newlines/tabs)
    content = re.sub(r'[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]', '', content)

    req_id = raw.get("request_id", uuid.uuid4().hex[:8])
    if not isinstance(req_id, str) or len(req_id) > 64:
        req_id = uuid.uuid4().hex[:8]

    # session_id from client must be valid UUID format if supplied
    client_sid = raw.get("session_id", "")
    if client_sid and not _UUID_RE.match(str(client_sid)):
        client_sid = ""  # ignore malformed, auto-assign below

    trace("UI_GATEWAY", "message_validated",
          session_id=session_id, type=msg_type)

    return {
        "type":       msg_type,
        "content":    content,
        "request_id": req_id,
        "session_id": client_sid or session_id,
    }

def validate_auth_body(raw: Any) -> str:
    """Validate /ui/auth POST body. Returns password string."""
    if not isinstance(raw, dict):
        raise UIGatewayError("UIINPUT_INVALID", "Body must be JSON object", 400)
    password = raw.get("password", "")
    if not isinstance(password, str) or len(password) > 256:
        raise UIGatewayError("UIINPUT_INVALID", "Invalid password field", 400)
    return password

# ── Rate limiter ──────────────────────────────────────────────────────────────

class RateLimiter:
    """Token bucket per session_id — 60 messages/minute default."""
    def __init__(self, rpm: int = RATE_LIMIT_RPM):
        self._rpm     = rpm
        self._buckets: dict[str, list[float]] = defaultdict(list)
        self._lock    = threading.Lock()

    def check(self, session_id: str) -> bool:
        """Returns True if allowed, False if rate-limited."""
        now    = time.time()
        window = 60.0
        with self._lock:
            bucket = self._buckets[session_id]
            # Remove timestamps outside the window
            self._buckets[session_id] = [t for t in bucket if now - t < window]
            if len(self._buckets[session_id]) >= self._rpm:
                return False
            self._buckets[session_id].append(now)
            return True

_rate_limiter = RateLimiter()

# ── SSE response registry ─────────────────────────────────────────────────────

_sse_queues: dict[str, asyncio.Queue] = {}
_sse_lock   = threading.Lock()

def _get_sse_queue(session_id: str) -> asyncio.Queue:
    with _sse_lock:
        if session_id not in _sse_queues:
            _sse_queues[session_id] = asyncio.Queue(maxsize=100)
        return _sse_queues[session_id]

# ── Agent dispatch ────────────────────────────────────────────────────────────

_agent_factory = None  # set by UIGateway.set_agent_factory()
_gateway_runner = None  # set by UIGateway.set_gateway_runner()

def _dispatch_to_agent(validated: dict, client_type: str) -> dict:
    """
    Create a MessageEvent and route it through the existing GatewayRunner,
    OR call the agent factory directly if no gateway runner is set.
    Returns agent result dict.
    """
    session_id = validated["session_id"]
    content    = validated["content"]

    # Security: authorise the UI platform
    auth = auth_check("ui", session_id, session_id, content)
    if not auth["authorized"]:
        raise UIGatewayError("UIAUTH_FAIL", "Authorization denied", 403)

    t0 = time.time()
    trace("UI_GATEWAY", "agent_dispatched",
          session_id=session_id, request_id=validated["request_id"])

    try:
        if _gateway_runner is not None:
            # Route via the existing gateway infrastructure
            event = MessageEvent(
                platform  = f"ui_{client_type}",
                chat_id   = session_id,
                user_id   = session_id,
                text      = content,
                raw       = {"source": "ui", "client_type": client_type},
            )
            # Inject directly — GatewayRunner handles locking and history
            _gateway_runner._handle_event(event)
            # Response is delivered via SSE/WS callback; return placeholder
            return {"response": "", "via_gateway": True,
                    "session_id": session_id, "iterations_used": 0,
                    "usage": {}, "end_reason": "dispatched"}

        elif _agent_factory is not None:
            agent  = _agent_factory(session_id)
            result = agent.run(
                user_message   = content,
                session_id     = session_id,
                max_iterations = int(os.environ.get("MAX_ITERATIONS", 30)),
            )
            dur = round((time.time() - t0) * 1000)
            trace("UI_GATEWAY", "response_sent",
                  session_id=session_id,
                  request_id=validated["request_id"],
                  content_length=len(result.get("response", "")),
                  duration_ms=dur)
            return result
        else:
            return {"response": "[No agent configured]",
                    "session_id": session_id, "iterations_used": 0,
                    "usage": {}, "end_reason": "no_agent"}
    except UIGatewayError:
        raise
    except Exception as exc:
        log_error("UI_GATEWAY", f"Agent dispatch error: {exc}")
        raise UIGatewayError("UIAGENT_ERROR", str(exc), 500)

# ── HTTP Flask app (auth + Kivy fallback) ─────────────────────────────────────

_http_app = Flask(__name__)
_http_app.secret_key = UI_SECRET

# Security headers on every response
@_http_app.after_request
def security_headers(response: Response) -> Response:
    response.headers["X-Content-Type-Options"]  = "nosniff"
    response.headers["X-Frame-Options"]         = "DENY"
    response.headers["X-XSS-Protection"]        = "1; mode=block"
    response.headers["Referrer-Policy"]         = "strict-origin-when-cross-origin"
    response.headers["Content-Security-Policy"] = (
        "default-src 'self'; "
        "script-src 'self' 'unsafe-inline'; "
        "style-src 'self' 'unsafe-inline'; "
        "connect-src 'self' ws: wss:;"
    )
    response.headers["Cache-Control"] = "no-store"
    return response

CORS(_http_app, resources={
    r"/ui/*": {
        "origins": os.environ.get("UI_ALLOWED_ORIGINS", "http://localhost:3000").split(","),
        "methods": ["GET", "POST", "OPTIONS"],
        "allow_headers": ["Content-Type", "Authorization"],
        "supports_credentials": True,
        "max_age": 600,
    }
})

def _require_token(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        auth_header = request.headers.get("Authorization", "")
        if not auth_header.startswith("Bearer "):
            return jsonify({"error_code": "UIAUTH_FAIL",
                            "message": "Missing Bearer token"}), 401
        try:
            token_data = verify_token(auth_header[7:])
            g.token_data = token_data
        except UIGatewayError as exc:
            return jsonify(exc.to_dict()), exc.status
        return f(*args, **kwargs)
    return decorated

@_http_app.route("/ui/health")
def health():
    return jsonify({"status": "ok", "service": "ui-gateway"})

@_http_app.route("/ui/auth", methods=["POST"])
def ui_auth():
    try:
        body     = request.get_json(silent=True) or {}
        password = validate_auth_body(body)
    except UIGatewayError as exc:
        return jsonify(exc.to_dict()), exc.status

    if UI_PASSWORD and password != UI_PASSWORD:
        # Constant-time compare
        hmac.compare_digest(password, UI_PASSWORD)
        trace("UI_GATEWAY", "auth_fail",
              reason="wrong password",
              client_ip=request.remote_addr or "")
        return jsonify({"error_code": "UIAUTH_FAIL",
                        "message": "Invalid password"}), 401

    client_type = request.headers.get("X-Client-Type", "web")
    token_data  = create_token(client_type)
    trace("UI_GATEWAY", "auth_success",
          client_type=client_type,
          token_hash=hashlib.sha256(token_data["token"].encode()).hexdigest()[:12])
    return jsonify(token_data)

@_http_app.route("/ui/message", methods=["POST"])
@_require_token
def ui_message():
    """Kivy HTTP fallback for environments without WebSocket support."""
    raw = request.get_json(silent=True) or {}
    session_id = g.token_data.get("client_type", "kivy") + ":" + uuid.uuid4().hex[:8]

    try:
        validated = validate_message(raw, session_id)
    except UIGatewayError as exc:
        return jsonify(exc.to_dict()), exc.status

    if not _rate_limiter.check(validated["session_id"]):
        trace("UI_GATEWAY", "rate_limit_hit",
              session_id=validated["session_id"], client_type="kivy")
        return jsonify({"error_code": "UIRATE_LIMITED",
                        "message": "Too many requests"}), 429

    trace("UI_GATEWAY", "message_received",
          session_id=validated["session_id"],
          content_length=len(validated["content"]),
          client_type="kivy")

    if validated["type"] == "ping":
        return jsonify({"type": "pong", "session_id": validated["session_id"]})

    try:
        result = _dispatch_to_agent(validated, "kivy")
    except UIGatewayError as exc:
        return jsonify(exc.to_dict()), exc.status

    return jsonify({
        "type":       "response",
        "request_id": validated["request_id"],
        "content":    result.get("response", ""),
        "session_id": validated["session_id"],
        "done":       True,
        "metadata": {
            "iterations":    result.get("iterations_used", 0),
            "input_tokens":  result.get("usage", {}).get("input_tokens", 0),
            "output_tokens": result.get("usage", {}).get("output_tokens", 0),
        }
    })

@_http_app.route("/ui/history/<session_id>", methods=["GET"])
@_require_token
def ui_history(session_id: str):
    """Return message history for a session."""
    if not _UUID_RE.match(session_id.replace("-", "").replace(":", "")):
        return jsonify({"error_code": "UIINPUT_INVALID",
                        "message": "Invalid session_id"}), 400
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
    """SSE stream for Kivy clients — delivers agent responses as they complete."""
    if not _UUID_RE.match(session_id.replace("-", "").replace(":", "")):
        return jsonify({"error_code": "UIINPUT_INVALID",
                        "message": "Invalid session_id"}), 400

    def generate():
        yield "data: {\"type\": \"connected\"}\n\n"
        queue = _get_sse_queue(session_id)
        while True:
            try:
                item = queue.get(timeout=30)
                if item is None:  # sentinel: close stream
                    break
                yield f"data: {json.dumps(item)}\n\n"
            except Exception:
                yield "data: {\"type\": \"heartbeat\"}\n\n"

    return Response(generate(), mimetype="text/event-stream",
                    headers={"Cache-Control": "no-cache",
                             "X-Accel-Buffering": "no"})

# ── WebSocket handler ─────────────────────────────────────────────────────────

async def _ws_handler(websocket):
    """Handles one WebSocket connection — React or Kivy."""
    client_ip   = websocket.remote_address[0] if websocket.remote_address else "unknown"
    client_type = websocket.request_headers.get("X-Client-Type", "web")
    session_id  = uuid.uuid4().hex
    t_connect   = time.time()

    # ── Auth handshake ────────────────────────────────────────────────────────
    auth_header = websocket.request_headers.get("Authorization", "")
    if not auth_header.startswith("Bearer "):
        await websocket.close(4001, "Missing Bearer token")
        trace("UI_GATEWAY", "auth_fail",
              reason="missing token", client_ip=client_ip)
        return
    try:
        token_data  = verify_token(auth_header[7:])
        client_type = token_data.get("client_type", client_type)
    except UIGatewayError as exc:
        code = 4002 if exc.error_code == "UIAUTH_EXPIRED" else 4001
        await websocket.close(code, str(exc))
        trace("UI_GATEWAY", "auth_fail",
              reason=exc.error_code, client_ip=client_ip)
        return

    trace("UI_GATEWAY", "client_connected",
          client_type=client_type, session_id=session_id,
          token_hash=auth_header[7:17])

    try:
        async for raw_msg in websocket:
            # Parse JSON
            try:
                raw = json.loads(raw_msg)
            except json.JSONDecodeError:
                await websocket.send(json.dumps({
                    "type": "error",
                    "error_code": "UIINPUT_INVALID",
                    "message": "Message must be valid JSON",
                }))
                continue

            # Validate
            try:
                validated = validate_message(raw, session_id)
            except UIGatewayError as exc:
                await websocket.send(json.dumps({
                    "type": "error", **exc.to_dict()
                }))
                continue

            # Ping
            if validated["type"] == "ping":
                await websocket.send(json.dumps({"type": "pong",
                                                 "session_id": session_id}))
                continue

            # Rate limit
            if not _rate_limiter.check(validated["session_id"]):
                trace("UI_GATEWAY", "rate_limit_hit",
                      session_id=session_id, client_type=client_type)
                await websocket.send(json.dumps({
                    "type": "error",
                    "error_code": "UIRATE_LIMITED",
                    "message": "Too many messages. Slow down.",
                }))
                continue

            trace("UI_GATEWAY", "message_received",
                  session_id=session_id,
                  content_length=len(validated["content"]),
                  client_type=client_type)

            # Send typing indicator
            await websocket.send(json.dumps({
                "type":       "status",
                "status":     "thinking",
                "session_id": session_id,
                "request_id": validated["request_id"],
            }))

            # Dispatch to agent in thread pool (avoid blocking asyncio loop)
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
            trace("UI_GATEWAY", "client_disconnected",
                  client_type=client_type, session_id=session_id,
                  reason="error", duration_ms=round((time.time()-t_connect)*1000))
    else:
        trace("UI_GATEWAY", "client_disconnected",
              client_type=client_type, session_id=session_id,
              reason="normal", duration_ms=round((time.time()-t_connect)*1000))

# ── Public API ────────────────────────────────────────────────────────────────

class UIGateway:
    """
    C-17 · UI_GATEWAY public interface.
    Owns a WebSocket server (port 5001) and HTTP server (port 5002).
    """

    def set_agent_factory(self, factory) -> None:
        global _agent_factory
        _agent_factory = factory

    def set_gateway_runner(self, runner) -> None:
        global _gateway_runner
        _gateway_runner = runner

    def start_http(self, port: int = HTTP_PORT, threaded: bool = True) -> None:
        """Start Flask HTTP server in a background thread."""
        t = threading.Thread(
            target=_http_app.run,
            kwargs={"host": "0.0.0.0", "port": port,
                    "debug": False, "threaded": threaded,
                    "use_reloader": False},
            daemon=True,
        )
        t.start()
        trace("UI_GATEWAY", "client_connected",
              client_type="http", session_id="", token_hash="server_start")

    def start_websocket(self, port: int = WS_PORT) -> None:
        """Start asyncio WebSocket server in a background thread."""
        if not HAS_WS:
            warn("UI_GATEWAY", "websockets package not installed — WS disabled")
            return

        def _run():
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)

            async def _serve():
                async with websockets.server.serve(
                    _ws_handler, "0.0.0.0", port,
                    max_size=MAX_CONTENT_LEN + 4096,
                    ping_interval=30,
                    ping_timeout=10,
                ):
                    await asyncio.Future()  # run forever

            loop.run_until_complete(_serve())

        t = threading.Thread(target=_run, daemon=True, name="ui-ws-server")
        t.start()

    def start(self, ws_port: int = WS_PORT, http_port: int = HTTP_PORT) -> None:
        """Start both servers."""
        self.start_http(http_port)
        self.start_websocket(ws_port)
        trace("UI_GATEWAY", "client_connected",
              client_type="startup", session_id="all", token_hash="")


_ui_gateway = UIGateway()

def get_ui_gateway() -> UIGateway:
    return _ui_gateway
