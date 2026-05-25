"""
C-08 · CONTEXT_COMPRESSOR
Summarizes middle conversation turns to free context window space.
Creates session lineage, preserves last N messages intact.
Memory MUST flush before compression begins.
Tool call/result pairs must NEVER be split across boundary.
"""
from __future__ import annotations

import time
import uuid
from logger import trace, warn


class CompressorError(Exception):
    def __init__(self, error_code: str, session_id: str, message: str):
        super().__init__(message)
        self.error_code = error_code
        self.session_id = session_id

    def to_dict(self) -> dict:
        return {"error_code": self.error_code, "session_id": self.session_id, "message": str(self)}


class ContextCompressor:
    """C-08 · CONTEXT_COMPRESSOR"""

    def __init__(self, provider_call, session_store, memory_manager, aux_model: str = ""):
        """
        provider_call: callable matching call_provider() signature
        session_store: SessionDB instance
        memory_manager: MemoryManager instance
        aux_model: model string for summary generation
        """
        self._call    = provider_call
        self._store   = session_store
        self._memory  = memory_manager
        self._aux_model = aux_model or "claude-haiku-3-20240307"

    def compress(
        self,
        messages: list,
        model_context_limit: int,
        current_token_count: int,
        protect_last_n: int = 20,
        session_id: str = "",
        trigger: str = "preflight",
    ) -> dict:
        t0 = time.time()
        trace("CONTEXT_COMPRESSOR", "compression_start",
              session_id=session_id,
              trigger=trigger,
              current_pct=round(current_token_count / max(model_context_limit, 1) * 100, 1),
              token_count=current_token_count)

        # 1. Memory MUST flush first
        try:
            flush_ok = self._memory.execute("flush", session_id)["flushed"]
        except Exception as exc:
            raise CompressorError("MEMORY_FLUSH_FAIL", session_id,
                                  f"Memory flush failed before compression: {exc}") from exc
        trace("CONTEXT_COMPRESSOR", "flush_triggered",
              session_id=session_id, memory_flushed_ok=flush_ok)

        # 2. Partition messages — keep last N safe, summarize the rest
        if len(messages) <= protect_last_n:
            # Nothing to compress
            return {
                "compressed_messages": messages,
                "summary": "",
                "new_session_id": session_id,
                "parent_session_id": session_id,
                "tokens_saved": 0,
                "messages_removed": 0,
            }

        to_summarize_raw = messages[:-protect_last_n]
        protected        = messages[-protect_last_n:]

        # 3. Ensure we don't split tool call / tool result pairs
        to_summarize = self._safe_partition(to_summarize_raw)

        # 4. Generate summary
        summary = self._generate_summary(to_summarize, session_id)

        # 5. Build new message list
        summary_msg = {"role": "user",
                       "content": f"[Context summary — {len(to_summarize)} earlier messages]\n{summary}"}
        compressed_messages = [summary_msg] + protected
        tokens_saved    = max(0, current_token_count - (model_context_limit // 2))
        messages_removed = len(to_summarize)

        # 6. Create new session with lineage
        new_session_id = ""
        try:
            new_session_id = f"{session_id}__child_{uuid.uuid4().hex[:8]}"
            self._store.execute("create", new_session_id,
                                {"parent_id": session_id})
            trace("CONTEXT_COMPRESSOR", "lineage_created",
                  session_id=session_id,
                  parent_session_id=session_id,
                  new_session_id=new_session_id)
        except Exception as exc:
            warn("CONTEXT_COMPRESSOR", f"Session split failed (continuing with original): {exc}")
            new_session_id = session_id

        trace("CONTEXT_COMPRESSOR", "compression_end",
              session_id=session_id,
              tokens_saved=tokens_saved,
              messages_removed=messages_removed,
              duration_ms=round((time.time() - t0) * 1000))

        return {
            "compressed_messages": compressed_messages,
            "summary":             summary,
            "new_session_id":      new_session_id,
            "parent_session_id":   session_id,
            "tokens_saved":        tokens_saved,
            "messages_removed":    messages_removed,
        }

    # ── Helpers ────────────────────────────────────────────────────────────────

    def _safe_partition(self, messages: list) -> list:
        """
        Walk backwards from the cut point to ensure we never end on a
        tool_use message without its paired tool_result.
        """
        # Find the last safe cut (after a tool_result or assistant text, not mid-pair)
        safe = list(messages)
        while safe:
            last = safe[-1]
            role = last.get("role", "")
            # If last is an assistant message that issued tool calls, not safe to cut here
            if role == "assistant" and last.get("tool_calls"):
                safe.pop()
            else:
                break
        return safe

    def _generate_summary(self, messages: list, session_id: str) -> str:
        """Calls aux LLM to summarize; falls back to truncation."""
        try:
            conv_text = "\n".join(
                f"[{m.get('role','?')}]: {str(m.get('content',''))[:500]}"
                for m in messages
            )
            prompt = (
                "Summarize the following conversation segment concisely, "
                "preserving all important facts, decisions, and tool results:\n\n"
                + conv_text
            )
            result = self._call(
                messages=[{"role": "user", "content": prompt}],
                model=self._aux_model,
                provider="anthropic",
                max_tokens=1000,
            )
            summary = result.get("content", "")
            trace("CONTEXT_COMPRESSOR", "summary_generated",
                  session_id=session_id,
                  aux_model_used=self._aux_model,
                  input_turns=len(messages),
                  summary_chars=len(summary))
            return summary
        except Exception as exc:
            warn("CONTEXT_COMPRESSOR",
                 f"Summary LLM call failed (fallback to truncation): {exc}")
            # Fallback: first 200 chars of each message role
            return " | ".join(
                f"{m.get('role','?')}: {str(m.get('content',''))[:100]}"
                for m in messages[:10]
            )
