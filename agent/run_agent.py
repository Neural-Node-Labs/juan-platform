"""
C-01 · AGENT_LOOP
Orchestrates the full autonomous agent turn:
prompt assembly → LLM call → tool execution → compression → memory flush → response.
"""
from __future__ import annotations

import sys
import os
import time
import uuid
from typing import Any

# Allow imports from project root
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from logger import trace, warn, error as log_error
from juan_cli.runtime_provider import call_provider, ProviderError
from agent.prompt_builder      import PromptBuilder
from agent.context_compressor  import ContextCompressor
from agent.memory_manager      import MemoryManager
from agent.iteration_budget    import IterationBudget
from tools.registry            import ToolRegistry, ToolError
from juan_state                import get_db

# Context threshold (% of limit) that triggers compression
COMPRESSION_THRESHOLD = 0.75
CHARS_PER_TOKEN       = 4


class AgentError(Exception):
    def __init__(self, error_code: str, message: str,
                 iteration: int = 0, recoverable: bool = False, fallback_used: bool = False):
        super().__init__(message)
        self.error_code   = error_code
        self.iteration    = iteration
        self.recoverable  = recoverable
        self.fallback_used = fallback_used

    def to_dict(self) -> dict:
        return {
            "error_code":    self.error_code,
            "message":       str(self),
            "iteration":     self.iteration,
            "recoverable":   self.recoverable,
            "fallback_used": self.fallback_used,
        }


class AIAgent:
    """
    C-01 · AGENT_LOOP
    """

    def __init__(
        self,
        model:          str           = "claude-sonnet-4-20250514",
        provider:       str           = "anthropic",
        fallback_model: dict | None   = None,
        tool_registry:  ToolRegistry  | None = None,
        memory_manager: MemoryManager | None = None,
        context_limit:  int           = 200_000,
        approval_gate   = None,
    ):
        self._model          = model
        self._provider       = provider
        self._fallback       = fallback_model
        self._registry       = tool_registry or ToolRegistry()
        self._memory         = memory_manager or MemoryManager()
        self._prompt_builder = PromptBuilder()
        self._db             = get_db()
        self._context_limit  = context_limit
        self._compressor     = ContextCompressor(
            provider_call  = call_provider,
            session_store  = self._db,
            memory_manager = self._memory,
            aux_model      = model,
        )

    # ── Public entry point ────────────────────────────────────────────────────

    def run(
        self,
        user_message:          str,
        session_id:            str | None = None,
        task_id:               str | None = None,
        system_prompt:         str | None = None,
        conversation_history:  list | None = None,
        max_iterations:        int  = 90,
        is_subagent:           bool = False,
    ) -> dict:
        """
        C-01 entry point. Returns out_schema dict.
        """
        session_id = session_id or uuid.uuid4().hex
        task_id    = task_id    or uuid.uuid4().hex

        trace("AGENT_LOOP", "turn_start",
              session_id=session_id, task_id=task_id,
              model=self._model, iteration=0)

        # Ensure session exists
        try:
            self._db.execute("create", session_id)
        except Exception:
            pass  # may already exist

        # Load conversation history if not supplied
        if conversation_history is None:
            try:
                res = self._db.execute("get_messages", session_id)
                conversation_history = res.get("messages", [])
            except Exception:
                conversation_history = []

        # Build system prompt if not supplied
        if system_prompt is None:
            try:
                mem = self._memory.execute("read", session_id)
                memory_files = {
                    "MEMORY_md": mem.get("memory_md", ""),
                    "USER_md":   mem.get("user_md", ""),
                }
            except Exception:
                memory_files = {}
            pb_result  = self._prompt_builder.build(
                session_id   = session_id,
                model        = self._model,
                tools        = self._registry.get_tool_schemas(),
                memory_files = memory_files,
            )
            system_prompt = pb_result["system_prompt"]

        # Append user message to history
        messages = list(conversation_history)
        messages.append({"role": "user", "content": user_message})
        try:
            self._db.execute("append_message", session_id,
                             {"role": "user", "content": user_message})
        except Exception as exc:
            warn("AGENT_LOOP", f"Session write failed: {exc}")

        # Iteration state
        budget          = IterationBudget(session_id, max_iterations, is_subagent)
        iteration       = 0
        total_input     = 0
        total_output    = 0
        total_cache     = 0
        tool_calls_made = 0
        compression_triggered = False
        end_reason      = "stop"
        response_text   = ""
        fallback_used   = False

        # ── Main loop ──────────────────────────────────────────────────────────
        while True:
            # Budget check
            budget_result = budget.check(iteration)
            if not budget_result["allowed"]:
                end_reason = "budget_exhausted"
                break

            # Context compression check
            estimated_tokens = sum(len(str(m.get("content", ""))) for m in messages) // CHARS_PER_TOKEN
            if estimated_tokens > self._context_limit * COMPRESSION_THRESHOLD:
                try:
                    comp = self._compressor.compress(
                        messages            = messages,
                        model_context_limit = self._context_limit,
                        current_token_count = estimated_tokens,
                        session_id          = session_id,
                        trigger             = "preflight",
                    )
                    messages              = comp["compressed_messages"]
                    session_id            = comp["new_session_id"]
                    compression_triggered = True
                    trace("AGENT_LOOP", "compression_triggered",
                          session_id=session_id,
                          threshold_pct=COMPRESSION_THRESHOLD,
                          tokens_before=estimated_tokens,
                          tokens_after=estimated_tokens - comp["tokens_saved"])
                except Exception as exc:
                    warn("AGENT_LOOP", f"Compression failed (continuing): {exc}")

            # LLM call
            try:
                llm_result = call_provider(
                    messages       = messages,
                    model          = self._model,
                    provider       = self._provider,
                    max_tokens     = 8000,
                    fallback_model = self._fallback,
                    tools          = self._registry.get_tool_schemas() or None,
                )
                fallback_used = llm_result.get("fallback_used", False)
            except ProviderError as exc:
                log_error("AGENT_LOOP", f"LLM call failed: {exc}")
                end_reason = "error"
                raise AgentError("PROVIDER_FAIL", str(exc),
                                  iteration=iteration, recoverable=False,
                                  fallback_used=fallback_used)

            # Accumulate usage
            usage = llm_result.get("usage", {})
            total_input  += usage.get("input_tokens",  0)
            total_output += usage.get("output_tokens", 0)
            total_cache  += usage.get("cache_read_tokens", 0)
            trace("AGENT_LOOP", "token_count",
                  session_id=session_id,
                  input=usage.get("input_tokens", 0),
                  output=usage.get("output_tokens", 0),
                  cache=usage.get("cache_read_tokens", 0),
                  cumulative=total_input + total_output)

            # Append assistant response
            assistant_msg = {
                "role":       "assistant",
                "content":    llm_result.get("content", ""),
                "tool_calls": llm_result.get("tool_calls", []),
            }
            messages.append(assistant_msg)
            response_text = llm_result.get("content", "")
            try:
                self._db.execute("append_message", session_id,
                                 {"role": "assistant", "content": response_text, "usage": usage})
            except Exception as exc:
                warn("AGENT_LOOP", f"Session append failed: {exc}")

            # Check finish reason
            finish_reason = llm_result.get("finish_reason", "stop")
            tool_calls    = llm_result.get("tool_calls", [])

            if finish_reason in ("stop", "end_turn") and not tool_calls:
                end_reason = "stop"
                break

            # Tool execution
            if tool_calls:
                trace("AGENT_LOOP", "tool_dispatch",
                      session_id=session_id,
                      tool_name=",".join(t.get("name", t.get("type", "?")) for t in tool_calls),
                      args_hash="batch",
                      iteration_n=iteration)
                try:
                    dispatch_result = self._registry.dispatch(
                        tool_calls = tool_calls,
                        task_id    = task_id,
                        session_id = session_id,
                    )
                    results = dispatch_result.get("results", [])
                    tool_calls_made += len(results)

                    for r in results:
                        trace("AGENT_LOOP", "tool_result",
                              session_id=session_id,
                              tool_name=r.get("tool_name", ""),
                              exit_code=r.get("exit_code", 0),
                              duration_ms=r.get("duration_ms", 0))
                        # Feed results back as tool messages
                        messages.append({
                            "role":        "tool",
                            "content":     r.get("content", ""),
                            "tool_call_id": r.get("tool_call_id", ""),
                        })
                        try:
                            self._db.execute("append_message", session_id, {
                                "role":        "tool",
                                "content":     r.get("content", ""),
                                "tool_call_id": r.get("tool_call_id", ""),
                                "tool_name":   r.get("tool_name", ""),
                            })
                        except Exception:
                            pass
                except ToolError as exc:
                    log_error("AGENT_LOOP", f"Tool fatal: {exc}")
                    end_reason = "error"
                    raise AgentError("TOOL_FATAL", str(exc), iteration=iteration,
                                      recoverable=False) from exc

            iteration += 1

        # ── End of loop ────────────────────────────────────────────────────────
        # Memory flush on end
        try:
            self._memory.execute("on_session_end", session_id)
        except Exception as exc:
            warn("AGENT_LOOP", f"Memory flush on end failed: {exc}")

        try:
            self._db.execute("end", session_id)
        except Exception:
            pass

        trace("AGENT_LOOP", "turn_end",
              session_id=session_id,
              end_reason=end_reason,
              iterations_used=iteration,
              total_tokens=total_input + total_output)

        return {
            "response":              response_text,
            "session_id":            session_id,
            "task_id":               task_id,
            "iterations_used":       iteration,
            "tool_calls_made":       tool_calls_made,
            "usage": {
                "input_tokens":  total_input,
                "output_tokens": total_output,
                "cache_tokens":  total_cache,
            },
            "compression_triggered": compression_triggered,
            "end_reason":            end_reason,
        }
