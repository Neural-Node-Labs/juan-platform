"""
C-21 · SWARM_ORCHESTRATOR
Decomposes a complex task into subtasks, spawns isolated child agents
to execute them (concurrently or sequentially), and aggregates results.

CBD CONTRACT
═══════════════════════════════════════════════════════
Responsibility:
  Given a task description, produce a plan (list of subtasks), spawn one
  AIAgent per subtask as an isolated session, collect all results, and
  return a synthesised summary.

  The orchestrator is itself a tool registered with ToolRegistry, so any
  agent can delegate work to the swarm by calling the 'swarm' tool.

IN SCHEMA (tool call arguments):
  task        str   The high-level task to decompose
  strategy    str?  "parallel" | "sequential" | "auto"  (default: auto)
  max_agents  int?  Maximum concurrent child agents     (default: 4, max: 8)
  model       str?  Override model for child agents     (default: inherit)

OUT SCHEMA:
  {
    "ok":          bool,
    "result":      str,        ← synthesised summary
    "subtasks":    list[dict], ← [{id, task, status, result, session_id}]
    "plan":        str,        ← the decomposition reasoning
    "agents_used": int,
    "exit_code":   int,
  }

ERROR CODES:
  SWARM_PLAN_FAIL     LLM failed to produce a valid plan
  SWARM_CHILD_FAIL    All child agents failed
  SWARM_PARTIAL_FAIL  Some child agents failed (partial results returned)
  SWARM_LIMIT_EXCEED  Requested more agents than max_agents allows
═══════════════════════════════════════════════════════
"""
from __future__ import annotations

import json
import os
import re
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed, TimeoutError as FutTimeout
from typing import Any

from logger import trace, warn, error as log_error

MAX_AGENTS_HARD  = 8
DEFAULT_MAX      = 4
CHILD_TIMEOUT    = 300   # seconds per child agent
PLAN_MAX_TOKENS  = 1000

# ── Error ──────────────────────────────────────────────────────────────────────

class SwarmError(Exception):
    def __init__(self, error_code: str, message: str):
        super().__init__(message)
        self.error_code = error_code

# ── Plan parser ────────────────────────────────────────────────────────────────

_TASK_RE = re.compile(
    r'(?:TASK\s*\d+|^\d+\.|\*\*Task\s*\d+\*\*)[:\s]+(.+?)(?=(?:TASK\s*\d+|^\d+\.|\*\*Task|\Z))',
    re.IGNORECASE | re.MULTILINE | re.DOTALL,
)

def _parse_plan(text: str) -> list[str]:
    """Extract subtask descriptions from LLM plan text."""
    # Try JSON first
    try:
        clean = re.sub(r'```(?:json)?|```', '', text).strip()
        data  = json.loads(clean)
        if isinstance(data, list):
            return [str(item.get("task", item) if isinstance(item, dict) else item).strip()
                    for item in data if item]
    except Exception:
        pass

    # Try numbered list
    lines = [l.strip() for l in text.splitlines() if l.strip()]
    tasks = []
    for line in lines:
        m = re.match(r'^(?:\d+[\.\):]|\-|\*)\s*(.+)$', line)
        if m:
            tasks.append(m.group(1).strip())
    if tasks:
        return tasks

    # Try TASK markers
    matches = _TASK_RE.findall(text)
    if matches:
        return [m.strip() for m in matches if m.strip()]

    # Fallback: each non-empty line is a subtask
    return [l for l in lines if len(l) > 10][:MAX_AGENTS_HARD]


def _should_run_parallel(subtasks: list[str], strategy: str) -> bool:
    """Decide parallel vs sequential based on strategy + task heuristics."""
    if strategy == "parallel":
        return True
    if strategy == "sequential":
        return False
    # auto: parallel if subtasks appear independent
    dependency_words = {"then", "after", "once", "following", "based on result",
                        "using output", "next step", "step 2", "step 3"}
    text = " ".join(subtasks).lower()
    return not any(w in text for w in dependency_words)


# ── Swarm orchestrator ─────────────────────────────────────────────────────────

class SwarmOrchestrator:
    """
    C-21 · SWARM_ORCHESTRATOR

    agent_factory : callable() → AIAgent
    call_provider  : callable matching call_provider() signature (for planning)
    model          : default model string
    provider       : default provider string
    """

    def __init__(
        self,
        agent_factory,
        call_provider,
        model:    str = "",
        provider: str = "",
    ) -> None:
        self._agent_factory = agent_factory
        self._call_provider = call_provider
        self._model    = model    or os.environ.get("JUAN_MODEL",    "claude-sonnet-4-20250514")
        self._provider = provider or os.environ.get("JUAN_PROVIDER", "anthropic")

    # ── Main entry point ───────────────────────────────────────────────────────

    def run(
        self,
        task:        str,
        strategy:    str = "auto",
        max_agents:  int = DEFAULT_MAX,
        model:       str = "",
        parent_session_id: str = "",
    ) -> dict:
        """Decompose, spawn, aggregate."""
        max_agents = min(max(1, int(max_agents)), MAX_AGENTS_HARD)
        child_model = model or self._model
        t0 = time.time()

        swarm_id = uuid.uuid4().hex[:8]
        trace("SWARM_ORCHESTRATOR", "swarm_started",
              session_id=parent_session_id,
              swarm_id=swarm_id,
              task_preview=task[:80],
              strategy=strategy,
              max_agents=max_agents)

        # ── 1. Plan ────────────────────────────────────────────────────────────
        try:
            plan_text, subtasks = self._plan(task, max_agents, child_model)
        except SwarmError as e:
            return self._error_result(e.error_code, str(e), swarm_id)

        if not subtasks:
            return self._error_result("SWARM_PLAN_FAIL",
                                      "Planner returned no subtasks", swarm_id)

        # Cap to max_agents
        subtasks = subtasks[:max_agents]

        trace("SWARM_ORCHESTRATOR", "plan_ready",
              session_id=parent_session_id,
              swarm_id=swarm_id,
              subtask_count=len(subtasks),
              parallel=_should_run_parallel(subtasks, strategy))

        # ── 2. Execute child agents ────────────────────────────────────────────
        parallel = _should_run_parallel(subtasks, strategy)
        results  = (self._run_parallel(subtasks, swarm_id, child_model, parent_session_id)
                    if parallel
                    else self._run_sequential(subtasks, swarm_id, child_model, parent_session_id))

        # ── 3. Assess outcomes ────────────────────────────────────────────────
        succeeded = [r for r in results if r["status"] == "done"]
        failed    = [r for r in results if r["status"] == "error"]

        if not succeeded:
            return self._error_result(
                "SWARM_CHILD_FAIL",
                f"All {len(failed)} child agents failed.\n" +
                "\n".join(f"  [{r['id']}] {r['result']}" for r in failed),
                swarm_id,
                subtasks=results,
            )

        # ── 4. Synthesise ─────────────────────────────────────────────────────
        summary = self._synthesise(task, results, child_model)

        duration = round((time.time() - t0) * 1000)
        trace("SWARM_ORCHESTRATOR", "swarm_complete",
              session_id=parent_session_id,
              swarm_id=swarm_id,
              agents_used=len(subtasks),
              succeeded=len(succeeded),
              failed=len(failed),
              duration_ms=duration)

        error_code = "SWARM_PARTIAL_FAIL" if failed else None
        return {
            "ok":          True,
            "result":      summary,
            "subtasks":    results,
            "plan":        plan_text,
            "agents_used": len(subtasks),
            "exit_code":   0 if not failed else 2,
            **({"error_code": error_code} if error_code else {}),
        }

    # ── Planning ───────────────────────────────────────────────────────────────

    def _plan(self, task: str, max_agents: int, model: str) -> tuple[str, list[str]]:
        """Ask the LLM to decompose the task. Returns (plan_text, subtasks)."""
        prompt = (
            f"You are a task decomposition engine. Break this task into at most {max_agents} "
            f"independent subtasks that can each be completed by a separate AI agent.\n\n"
            f"TASK: {task}\n\n"
            f"Respond with a numbered list of subtasks. Each subtask should be:\n"
            f"- Self-contained (the agent gets only the subtask description)\n"
            f"- Specific enough to produce a useful result on its own\n"
            f"- No more than 2 sentences\n\n"
            f"If the task cannot be meaningfully decomposed, respond with a single subtask "
            f"that is the original task verbatim.\n\n"
            f"Numbered list:"
        )
        try:
            result = self._call_provider(
                messages  = [{"role": "user", "content": prompt}],
                model     = model,
                provider  = self._provider,
                max_tokens = PLAN_MAX_TOKENS,
            )
            plan_text = result.get("content", "")
            subtasks  = _parse_plan(plan_text)
            if not subtasks:
                raise SwarmError("SWARM_PLAN_FAIL", "Could not parse subtasks from plan")
            return plan_text, subtasks
        except SwarmError:
            raise
        except Exception as exc:
            raise SwarmError("SWARM_PLAN_FAIL", f"Planning LLM call failed: {exc}") from exc

    # ── Child execution ────────────────────────────────────────────────────────

    def _run_one(self, subtask_id: str, subtask: str,
                 swarm_id: str, model: str, parent_session_id: str) -> dict:
        session_id = f"swarm:{swarm_id}:{subtask_id}"
        trace("SWARM_ORCHESTRATOR", "child_started",
              session_id=parent_session_id,
              child_session_id=session_id,
              subtask_id=subtask_id,
              task_preview=subtask[:60])
        t0 = time.time()
        try:
            agent  = self._agent_factory(session_id)
            result = agent.run(
                user_message   = subtask,
                session_id     = session_id,
                is_subagent    = True,
                max_iterations = int(os.environ.get("MAX_ITERATIONS", 30)),
            )
            duration = round((time.time() - t0) * 1000)
            trace("SWARM_ORCHESTRATOR", "child_done",
                  session_id=parent_session_id,
                  child_session_id=session_id,
                  subtask_id=subtask_id,
                  duration_ms=duration,
                  end_reason=result.get("end_reason", ""))
            return {
                "id":         subtask_id,
                "task":       subtask,
                "status":     "done",
                "result":     result.get("response", ""),
                "session_id": session_id,
                "usage":      result.get("usage", {}),
                "duration_ms": duration,
            }
        except Exception as exc:
            duration = round((time.time() - t0) * 1000)
            log_error("SWARM_ORCHESTRATOR", f"Child {subtask_id} failed: {exc}")
            trace("SWARM_ORCHESTRATOR", "child_failed",
                  session_id=parent_session_id,
                  child_session_id=session_id,
                  subtask_id=subtask_id,
                  error=str(exc)[:120])
            return {
                "id":         subtask_id,
                "task":       subtask,
                "status":     "error",
                "result":     str(exc),
                "session_id": session_id,
                "usage":      {},
                "duration_ms": duration,
            }

    def _run_parallel(self, subtasks: list[str], swarm_id: str,
                      model: str, parent_session_id: str) -> list[dict]:
        results = [None] * len(subtasks)
        with ThreadPoolExecutor(max_workers=len(subtasks)) as pool:
            futures = {
                pool.submit(
                    self._run_one,
                    str(i), task, swarm_id, model, parent_session_id
                ): i
                for i, task in enumerate(subtasks)
            }
            for future in as_completed(futures, timeout=CHILD_TIMEOUT + 30):
                idx = futures[future]
                try:
                    results[idx] = future.result(timeout=CHILD_TIMEOUT)
                except FutTimeout:
                    results[idx] = {
                        "id": str(idx), "task": subtasks[idx],
                        "status": "error", "result": "Timed out",
                        "session_id": f"swarm:{swarm_id}:{idx}",
                        "usage": {}, "duration_ms": CHILD_TIMEOUT * 1000,
                    }
                except Exception as exc:
                    results[idx] = {
                        "id": str(idx), "task": subtasks[idx],
                        "status": "error", "result": str(exc),
                        "session_id": f"swarm:{swarm_id}:{idx}",
                        "usage": {}, "duration_ms": 0,
                    }
        return [r for r in results if r is not None]

    def _run_sequential(self, subtasks: list[str], swarm_id: str,
                        model: str, parent_session_id: str) -> list[dict]:
        results = []
        for i, task in enumerate(subtasks):
            result = self._run_one(str(i), task, swarm_id, model, parent_session_id)
            results.append(result)
            # Stop on failure in sequential mode
            if result["status"] == "error":
                warn("SWARM_ORCHESTRATOR",
                     f"Sequential child {i} failed — stopping chain")
                break
        return results

    # ── Synthesis ──────────────────────────────────────────────────────────────

    def _synthesise(self, original_task: str, results: list[dict], model: str) -> str:
        """Ask LLM to synthesise all subtask results into a final answer."""
        parts = [f"ORIGINAL TASK: {original_task}\n"]
        for r in results:
            status_tag = "✓" if r["status"] == "done" else "✗ FAILED"
            parts.append(f"[{status_tag}] Subtask {r['id']}: {r['task']}\nResult: {r['result'][:500]}")

        prompt = "\n\n".join(parts) + (
            "\n\nSynthesise these results into a single coherent response "
            "to the original task. Be concise and direct."
        )
        try:
            result = self._call_provider(
                messages  = [{"role": "user", "content": prompt}],
                model     = model,
                provider  = self._provider,
                max_tokens = 2000,
            )
            return result.get("content", "")
        except Exception as exc:
            warn("SWARM_ORCHESTRATOR", f"Synthesis LLM failed: {exc}")
            # Fallback: concatenate results
            return "\n\n".join(
                f"**Subtask {r['id']}** ({r['task'][:60]}):\n{r['result']}"
                for r in results if r["status"] == "done"
            )

    # ── Helpers ────────────────────────────────────────────────────────────────

    @staticmethod
    def _error_result(error_code: str, message: str,
                      swarm_id: str, subtasks: list | None = None) -> dict:
        return {
            "ok":          False,
            "result":      f"[{error_code}] {message}",
            "subtasks":    subtasks or [],
            "plan":        "",
            "agents_used": 0,
            "exit_code":   1,
            "error_code":  error_code,
        }


# ── Tool wrapper ───────────────────────────────────────────────────────────────

_orchestrator: SwarmOrchestrator | None = None

def init_swarm(agent_factory, call_provider, model="", provider="") -> None:
    global _orchestrator
    _orchestrator = SwarmOrchestrator(
        agent_factory=agent_factory,
        call_provider=call_provider,
        model=model,
        provider=provider,
    )

def swarm_tool(
    task:        str,
    strategy:    str = "auto",
    max_agents:  int = DEFAULT_MAX,
    model:       str = "",
) -> dict:
    """
    C-21 entry point registered as the 'swarm' tool.
    """
    if _orchestrator is None:
        return {
            "ok":        False,
            "result":    "Swarm not initialised. Call init_swarm() at startup.",
            "exit_code": 1,
        }
    return _orchestrator.run(
        task       = task,
        strategy   = strategy,
        max_agents = max_agents,
        model      = model,
    )




SWARM_SCHEMA = {
    "name": "swarm",
    "description": (
        "Decompose a complex task into subtasks and run each one with a separate agent instance. "
        "Use this when a task is too large, has independent parts that can run in parallel, "
        "or benefits from specialised sub-agents. "
        "Returns synthesised results from all agents."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "task": {
                "type": "string",
                "description": "The high-level task to decompose and execute across multiple agents.",
            },
            "strategy": {
                "type": "string",
                "enum": ["parallel", "sequential", "auto"],
                "description": (
                    "parallel: run all subtasks at the same time. "
                    "sequential: run subtasks one after another (each can see previous output). "
                    "auto: let the orchestrator decide based on task dependencies (default)."
                ),
            },
            "max_agents": {
                "type": "integer",
                "description": f"Maximum number of parallel agents to spawn (1–{MAX_AGENTS_HARD}, default {DEFAULT_MAX}).",
            },
            "model": {
                "type": "string",
                "description": "Override the model used for child agents. Defaults to the parent agent's model.",
            },
        },
        "required": ["task"],
    },
}
