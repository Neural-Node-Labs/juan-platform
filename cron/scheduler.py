"""
C-12 · CRON_SCHEDULER
Manages scheduled agent jobs with multi-format schedules.
Fires jobs via isolated agent sessions.
Delivers results to configured platforms.
Cron sessions are isolated from gateway history.
"""
from __future__ import annotations

import re
import time
import uuid
import threading
from datetime import datetime, timezone
from typing import Any, Callable

from logger import trace, warn, error as log_error

# ── Simple schedule parser ─────────────────────────────────────────────────────
# Supports:
#   "every N minutes/hours/days"
#   "@daily", "@hourly", "@weekly"
#   ISO-8601 interval "PT15M", "PT1H", "P1D"

class ScheduleError(Exception): pass


def _parse_schedule(schedule: str) -> float:
    """Returns interval in seconds."""
    s = schedule.strip().lower()
    if s == "@hourly":   return 3600
    if s == "@daily":    return 86400
    if s == "@weekly":   return 604800
    # "every N unit"
    m = re.match(r"every\s+(\d+)\s*(second|minute|hour|day)s?", s)
    if m:
        n, unit = int(m.group(1)), m.group(2)
        return n * {"second": 1, "minute": 60, "hour": 3600, "day": 86400}[unit]
    # ISO 8601 duration (basic)
    m = re.match(r"pt?(\d+)([smhd])", s.upper().replace("PT", "pt"))
    if m:
        n, unit = int(m.group(1)), m.group(2).lower()
        return n * {"s": 1, "m": 60, "h": 3600, "d": 86400}[unit]
    raise ScheduleError(f"Cannot parse schedule: {schedule!r}")


# ── Job data ───────────────────────────────────────────────────────────────────

class Job:
    def __init__(self, job_id: str, schedule: str, task: str,
                 delivery_target: str = ""):
        self.job_id          = job_id
        self.schedule        = schedule
        self.interval_s      = _parse_schedule(schedule)
        self.task            = task
        self.delivery_target = delivery_target  # "platform:chat_id"
        self.next_run        = time.time() + self.interval_s
        self.disabled        = False

    def to_dict(self) -> dict:
        return {
            "job_id":          self.job_id,
            "schedule":        self.schedule,
            "interval_s":      self.interval_s,
            "task":            self.task,
            "delivery_target": self.delivery_target,
            "next_run":        self.next_run,
            "disabled":        self.disabled,
        }


class CronError(Exception):
    def __init__(self, error_code: str, job_id: str, message: str):
        super().__init__(message)
        self.error_code = error_code
        self.job_id     = job_id

    def to_dict(self) -> dict:
        return {"error_code": self.error_code, "job_id": self.job_id, "message": str(self)}


# ── Scheduler ─────────────────────────────────────────────────────────────────

class CronScheduler:
    """C-12 · CRON_SCHEDULER"""

    def __init__(self, agent_factory: Callable | None = None,
                 delivery_fn: Callable | None = None,
                 tick_interval: float = 10.0):
        """
        agent_factory: callable() → AIAgent
        delivery_fn:   callable(platform, chat_id, text) → bool
        """
        self._jobs:       dict[str, Job] = {}
        self._lock        = threading.Lock()
        self._agent_factory = agent_factory
        self._delivery_fn   = delivery_fn
        self._tick_interval = tick_interval
        self._timer: threading.Timer | None = None
        self._running = False

    def start(self) -> None:
        self._running = True
        self._schedule_tick()

    def stop(self) -> None:
        self._running = False
        if self._timer:
            self._timer.cancel()

    # ── Job management ─────────────────────────────────────────────────────────

    def execute(self, operation: str, job_id: str | None = None,
                schedule: str | None = None, task: str | None = None,
                delivery_target: str | None = None) -> dict:
        ops = {
            "tick":       self._tick,
            "add_job":    self._add_job,
            "remove_job": self._remove_job,
            "list_jobs":  self._list_jobs,
        }
        if operation not in ops:
            return {"jobs": [], "error": f"Unknown operation: {operation}"}
        return ops[operation](job_id, schedule, task, delivery_target)

    def _add_job(self, job_id: str | None, schedule: str | None,
                 task: str | None, delivery_target: str | None) -> dict:
        job_id = job_id or uuid.uuid4().hex[:8]
        try:
            job = Job(job_id, schedule or "@daily", task or "", delivery_target or "")
        except ScheduleError as exc:
            warn("CRON_SCHEDULER", f"Schedule parse failed for job {job_id}: {exc}")
            raise CronError("SCHEDULE_PARSE_FAIL", job_id, str(exc)) from exc
        with self._lock:
            self._jobs[job_id] = job
        return {"jobs": [job.to_dict()], "next_run": job.next_run}

    def _remove_job(self, job_id: str | None, *_) -> dict:
        with self._lock:
            job = self._jobs.pop(job_id or "", None)
        if not job:
            raise CronError("JOB_NOT_FOUND", str(job_id), "Job not found")
        return {"jobs": []}

    def _list_jobs(self, *_) -> dict:
        with self._lock:
            return {"jobs": [j.to_dict() for j in self._jobs.values()]}

    def _tick(self, *_) -> dict:
        now = time.time()
        due: list[Job] = []
        with self._lock:
            for job in self._jobs.values():
                if not job.disabled and job.next_run <= now:
                    due.append(job)
                    job.next_run = now + job.interval_s

        trace("CRON_SCHEDULER", "tick",
              jobs_checked=len(self._jobs), due_count=len(due), timestamp=now)

        for job in due:
            overdue_ms = round((now - (job.next_run - job.interval_s)) * 1000)
            trace("CRON_SCHEDULER", "job_due",
                  job_id=job.job_id, schedule=job.schedule, overdue_ms=overdue_ms)
            threading.Thread(target=self._fire_job, args=(job,), daemon=True).start()

        return {"jobs": [j.to_dict() for j in due]}

    def _fire_job(self, job: Job) -> None:
        task_id = uuid.uuid4().hex
        # Isolated session — NOT the gateway session
        session_id = f"cron:{job.job_id}:{task_id[:8]}"
        try:
            if self._agent_factory is None:
                response = f"[No agent] Task: {job.task}"
            else:
                agent  = self._agent_factory()
                result = agent.run(user_message=job.task, session_id=session_id, task_id=task_id)
                response = result.get("response", "")
            trace("CRON_SCHEDULER", "agent_fired",
                  job_id=job.job_id, task_id=task_id)
        except Exception as exc:
            log_error("CRON_SCHEDULER", f"Agent fire failed for job {job.job_id}: {exc}")
            # Retry once
            try:
                if self._agent_factory:
                    result = self._agent_factory().run(user_message=job.task, session_id=session_id)
                    response = result.get("response", "")
                else:
                    raise
            except Exception as exc2:
                warn("CRON_SCHEDULER", f"Job {job.job_id} retry also failed: {exc2}")
                trace("CRON_SCHEDULER", "job_error",
                      job_id=job.job_id, error_code="AGENT_FIRE_FAIL")
                return

        # Deliver
        if job.delivery_target and self._delivery_fn:
            try:
                parts    = job.delivery_target.split(":", 1)
                platform = parts[0]
                chat_id  = parts[1] if len(parts) > 1 else ""
                ok = self._delivery_fn(platform, chat_id, response)
                trace("CRON_SCHEDULER", "delivery_sent",
                      job_id=job.job_id, target_platform=platform, success=ok)
            except Exception as exc:
                warn("CRON_SCHEDULER", f"Delivery failed for job {job.job_id}: {exc}")
                trace("CRON_SCHEDULER", "job_error",
                      job_id=job.job_id, error_code="DELIVERY_FAIL")

    # ── Tick loop ──────────────────────────────────────────────────────────────

    def _schedule_tick(self) -> None:
        if not self._running:
            return
        self._tick()
        self._timer = threading.Timer(self._tick_interval, self._schedule_tick)
        self._timer.daemon = True
        self._timer.start()
