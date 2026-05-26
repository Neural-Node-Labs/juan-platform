"""
C-16 · DASHBOARD
Flask API + web UI for monitoring activity, submitting tasks,
managing cron jobs, and viewing sessions.
"""
from __future__ import annotations

import json
import os
import sys
import threading
import time
import uuid
from collections import deque
from functools import wraps

from flask import Flask, jsonify, render_template, request, Response, session
from flask_cors import CORS

# Allow imports from project root
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from logger import trace
from juan_state import get_db

app = Flask(__name__)
app.secret_key = os.environ.get("DASHBOARD_SECRET", "juan-dev-secret-changeme")
CORS(app)

DASHBOARD_PASSWORD = os.environ.get("DASHBOARD_PASSWORD", "")

# ── In-memory event ring buffer (last 500 events) ─────────────────────────────
_events: deque = deque(maxlen=500)
_events_lock   = threading.Lock()

# ── Hook logger into event buffer ─────────────────────────────────────────────
import logging

class DashboardHandler(logging.Handler):
    def emit(self, record: logging.LogRecord) -> None:
        try:
            msg = record.getMessage()
            if msg.startswith("{"):
                data = json.loads(msg)
                data["_id"] = uuid.uuid4().hex[:8]
                with _events_lock:
                    _events.append(data)
        except Exception:
            pass

_dashboard_handler = DashboardHandler()
logging.getLogger("juan").addHandler(_dashboard_handler)

# ── Auth helper ───────────────────────────────────────────────────────────────

def auth_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if not DASHBOARD_PASSWORD:
            return f(*args, **kwargs)
        if session.get("authenticated"):
            return f(*args, **kwargs)
        token = request.headers.get("X-Dashboard-Token", "")
        if token == DASHBOARD_PASSWORD:
            return f(*args, **kwargs)
        return jsonify({"error": "Unauthorized"}), 401
    return decorated

# ── Routes ────────────────────────────────────────────────────────────────────

@app.route("/")
def index():
    return render_template("index.html")

@app.route("/api/auth", methods=["POST"])
def auth():
    data = request.json or {}
    if not DASHBOARD_PASSWORD or data.get("password") == DASHBOARD_PASSWORD:
        session["authenticated"] = True
        return jsonify({"ok": True})
    return jsonify({"error": "Invalid password"}), 401

@app.route("/api/status")
@auth_required
def status():
    db = get_db()
    try:
        with db._read() as conn:
            session_count = conn.execute("SELECT COUNT(*) FROM sessions").fetchone()[0]
            active_count  = conn.execute(
                "SELECT COUNT(*) FROM sessions WHERE ended_at IS NULL"
            ).fetchone()[0]
            msg_count     = conn.execute("SELECT COUNT(*) FROM messages").fetchone()[0]
            token_totals  = conn.execute(
                "SELECT SUM(input_tokens), SUM(output_tokens), SUM(cache_tokens) FROM sessions"
            ).fetchone()
    except Exception:
        session_count = active_count = msg_count = 0
        token_totals  = (0, 0, 0)

    with _events_lock:
        event_count = len(_events)

    return jsonify({
        "status":        "running",
        "uptime_s":      round(time.time() - _start_time),
        "sessions":      session_count,
        "active":        active_count,
        "messages":      msg_count,
        "events_buffered": event_count,
        "tokens": {
            "input":  token_totals[0] or 0,
            "output": token_totals[1] or 0,
            "cache":  token_totals[2] or 0,
        },
        "model":    os.environ.get("JUAN_MODEL", "claude-sonnet-4-20250514"),
        "provider": os.environ.get("JUAN_PROVIDER", "anthropic"),
        "platform": os.environ.get("JUAN_PLATFORM", "—"),
    })

@app.route("/api/events")
@auth_required
def events():
    since_id = request.args.get("since", "")
    limit    = min(int(request.args.get("limit", 100)), 500)
    component = request.args.get("component", "")

    with _events_lock:
        all_events = list(_events)

    if since_id:
        ids = [e["_id"] for e in all_events]
        if since_id in ids:
            all_events = all_events[ids.index(since_id) + 1:]

    if component:
        all_events = [e for e in all_events if e.get("component") == component]

    return jsonify({"events": all_events[-limit:]})

@app.route("/api/events/stream")
@auth_required
def events_stream():
    """Server-Sent Events stream for real-time log feed."""
    def generate():
        last_seen = len(_events)
        yield "data: {\"type\":\"connected\"}\n\n"
        while True:
            time.sleep(0.5)
            with _events_lock:
                current = list(_events)
            new = current[last_seen:]
            last_seen = len(current)
            for ev in new:
                yield f"data: {json.dumps(ev)}\n\n"

    return Response(generate(), mimetype="text/event-stream",
                    headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

@app.route("/api/sessions")
@auth_required
def sessions():
    db   = get_db()
    page = int(request.args.get("page", 1))
    per  = min(int(request.args.get("per", 20)), 100)
    offset = (page - 1) * per
    try:
        with db._read() as conn:
            rows = conn.execute(
                """SELECT session_id, parent_id, platform, chat_id, user_id,
                          model, created_at, ended_at,
                          input_tokens, output_tokens, cache_tokens
                   FROM sessions ORDER BY created_at DESC LIMIT ? OFFSET ?""",
                (per, offset)
            ).fetchall()
            total = conn.execute("SELECT COUNT(*) FROM sessions").fetchone()[0]
        return jsonify({
            "sessions": [dict(r) for r in rows],
            "total":    total,
            "page":     page,
            "per":      per,
        })
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500

@app.route("/api/sessions/<session_id>/messages")
@auth_required
def session_messages(session_id: str):
    db = get_db()
    try:
        result = db.execute("get_messages", session_id)
        return jsonify(result)
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500

# ── Task submission ───────────────────────────────────────────────────────────

_task_results: dict[str, dict] = {}
_task_lock = threading.Lock()

@app.route("/api/tasks", methods=["POST"])
@auth_required
def submit_task():
    data    = request.json or {}
    message = data.get("message", "").strip()
    if not message:
        return jsonify({"error": "message is required"}), 400

    task_id    = uuid.uuid4().hex
    session_id = f"dashboard:{task_id[:8]}"

    with _task_lock:
        _task_results[task_id] = {"status": "pending", "session_id": session_id}

    def run():
        try:
            # Import here to avoid circular imports at startup
            from main import build_agent
            agent  = build_agent(session_id)
            result = agent.run(
                user_message = message,
                session_id   = session_id,
                task_id      = task_id,
                max_iterations = int(os.environ.get("MAX_ITERATIONS", 30)),
            )
            with _task_lock:
                _task_results[task_id] = {
                    "status":     "done",
                    "session_id": session_id,
                    "response":   result.get("response", ""),
                    "iterations": result.get("iterations_used", 0),
                    "usage":      result.get("usage", {}),
                    "end_reason": result.get("end_reason", "stop"),
                }
        except Exception as exc:
            with _task_lock:
                _task_results[task_id] = {
                    "status":  "error",
                    "session_id": session_id,
                    "error":   str(exc),
                }

    threading.Thread(target=run, daemon=True).start()
    trace("DASHBOARD", "task_submitted", session_id=session_id, task_id=task_id)
    return jsonify({"task_id": task_id, "session_id": session_id, "status": "pending"})

@app.route("/api/tasks/<task_id>")
@auth_required
def task_status(task_id: str):
    with _task_lock:
        result = _task_results.get(task_id)
    if result is None:
        return jsonify({"error": "Task not found"}), 404
    return jsonify(result)

@app.route("/api/tasks")
@auth_required
def list_tasks():
    with _task_lock:
        tasks = [{"task_id": k, **v} for k, v in _task_results.items()]
    tasks.sort(key=lambda t: t.get("task_id", ""), reverse=True)
    return jsonify({"tasks": tasks[-50:]})  # last 50

# ── Cron management ───────────────────────────────────────────────────────────

_scheduler = None  # Set by entrypoint

@app.route("/api/cron", methods=["GET"])
@auth_required
def list_cron():
    if _scheduler is None:
        return jsonify({"jobs": []})
    return jsonify(_scheduler.execute("list_jobs"))

@app.route("/api/cron", methods=["POST"])
@auth_required
def add_cron():
    data = request.json or {}
    if _scheduler is None:
        return jsonify({"error": "Scheduler not running"}), 503
    try:
        result = _scheduler.execute(
            "add_job",
            job_id          = data.get("job_id"),
            schedule        = data.get("schedule", "@daily"),
            task            = data.get("task", ""),
            delivery_target = data.get("delivery_target", ""),
        )
        return jsonify(result)
    except Exception as exc:
        return jsonify({"error": str(exc)}), 400

@app.route("/api/cron/<job_id>", methods=["DELETE"])
@auth_required
def delete_cron(job_id: str):
    if _scheduler is None:
        return jsonify({"error": "Scheduler not running"}), 503
    try:
        result = _scheduler.execute("remove_job", job_id=job_id)
        return jsonify(result)
    except Exception as exc:
        return jsonify({"error": str(exc)}), 404

# ── Config (masked) ───────────────────────────────────────────────────────────

@app.route("/api/config")
@auth_required
def config():
    def mask(val):
        if not val:
            return "—"
        if len(val) > 8:
            return val[:4] + "****" + val[-4:]
        return "****"

    keys = ["ANTHROPIC_API_KEY", "OPENAI_API_KEY", "GROQ_API_KEY",
            "MISTRAL_API_KEY", "JUAN_BOT_TOKEN"]
    return jsonify({
        "model":    os.environ.get("JUAN_MODEL", "claude-sonnet-4-20250514"),
        "provider": os.environ.get("JUAN_PROVIDER", "anthropic"),
        "platform": os.environ.get("JUAN_PLATFORM", "—"),
        "max_iterations": os.environ.get("MAX_ITERATIONS", "90"),
        "api_keys": {k: mask(os.environ.get(k, "")) for k in keys},
        "soul_md_present": os.path.exists("SOUL.md") or os.path.exists("config/SOUL.md"),
        "auth_json_present": os.path.exists("juan_auth.json") or os.path.exists("config/juan_auth.json"),
    })

# ── Startup ───────────────────────────────────────────────────────────────────

_start_time = time.time()

def set_scheduler(scheduler):
    global _scheduler
    _scheduler = scheduler

if __name__ == "__main__":
    port = int(os.environ.get("DASHBOARD_PORT", 5000))
    print(f"[Dashboard] Starting on http://0.0.0.0:{port}")
    app.run(host="0.0.0.0", port=port, debug=False, threaded=True)
