"""
C-07 · PROMPT_BUILDER
Assembles the complete system prompt from personality (SOUL.md), memory
files, tool schemas, skill files, and context files.
Applies Anthropic cache markers when requested.
System prompt is frozen mid-conversation; only rebuilt on /model command.
"""
from __future__ import annotations

import json
import os
import time
from typing import Any

from logger import trace, warn

SOUL_PATH   = "SOUL.md"
FALLBACK_PERSONA = (
    "You are Juan, a helpful AI assistant. "
    "You have access to tools and will use them to help the user."
)

# Rough chars-per-token estimate (provider-agnostic)
CHARS_PER_TOKEN = 4


class PromptBuilderError(Exception):
    def __init__(self, error_code: str, section: str, message: str):
        super().__init__(message)
        self.error_code = error_code
        self.section    = section

    def to_dict(self) -> dict:
        return {"error_code": self.error_code, "section": self.section, "message": str(self)}


class PromptBuilder:
    """C-07 · PROMPT_BUILDER"""

    def build(
        self,
        session_id: str,
        model: str,
        tools: list,
        memory_files: dict | None = None,
        skills: list | None = None,
        context_files: list | None = None,
        apply_caching: bool = False,
    ) -> dict:
        """
        Returns out_schema dict:
          system_prompt, cache_markers?, token_estimate, sections_built
        """
        t0       = time.time()
        sections: list[str] = []
        parts:    list[str] = []
        errors:   list[str] = []

        # ── SOUL.md ──────────────────────────────────────────────────────────
        soul = self._load_soul(errors)
        parts.append(soul)
        sections.append("soul")

        # ── Memory files ──────────────────────────────────────────────────────
        memory_files = memory_files or {}
        mem_text = self._load_memory(memory_files, errors)
        if mem_text:
            parts.append(mem_text)
            sections.append("memory")

        # ── Tool schemas ──────────────────────────────────────────────────────
        tool_text = self._inject_tools(tools, errors)
        if tool_text:
            parts.append(tool_text)
            sections.append("tools")

        # ── Skills ────────────────────────────────────────────────────────────
        skill_text = self._inject_skills(skills or [], errors)
        if skill_text:
            parts.append(skill_text)
            sections.append("skills")

        # ── Context files ─────────────────────────────────────────────────────
        ctx_text = self._inject_context(context_files or [], errors)
        if ctx_text:
            parts.append(ctx_text)
            sections.append("context")

        system_prompt   = "\n\n".join(parts)
        token_estimate  = len(system_prompt) // CHARS_PER_TOKEN
        cache_markers: list = []

        # ── Cache markers (Anthropic only) ────────────────────────────────────
        if apply_caching and "anthropic" in model.lower():
            cache_markers = self._apply_cache_markers(parts, errors)
            sections.append("cache_markers")

        trace("PROMPT_BUILDER", "prompt_built",
              session_id=session_id,
              total_chars=len(system_prompt),
              token_estimate=token_estimate,
              duration_ms=round((time.time() - t0) * 1000))

        return {
            "system_prompt":  system_prompt,
            "cache_markers":  cache_markers if cache_markers else None,
            "token_estimate": token_estimate,
            "sections_built": sections,
        }

    # ── Private helpers ───────────────────────────────────────────────────────

    def _load_soul(self, errors: list) -> str:
        try:
            with open(SOUL_PATH, "r", encoding="utf-8") as fh:
                text = fh.read().strip()
            trace("PROMPT_BUILDER", "soul_loaded", char_count=len(text))
            return text
        except Exception as exc:
            warn("PROMPT_BUILDER", f"SOUL.md missing: {exc} — using fallback persona")
            errors.append("SOUL_MISSING")
            return FALLBACK_PERSONA

    def _load_memory(self, memory_files: dict, errors: list) -> str:
        parts = []
        try:
            mem = memory_files.get("MEMORY_md", "")
            usr = memory_files.get("USER_md", "")
            if mem:
                parts.append(f"## Agent Memory\n{mem}")
            if usr:
                parts.append(f"## User Profile\n{usr}")
            trace("PROMPT_BUILDER", "memory_loaded",
                  memory_md_chars=len(mem), user_md_chars=len(usr))
        except Exception as exc:
            warn("PROMPT_BUILDER", f"Memory load failed: {exc}")
            errors.append("MEMORY_READ_FAIL")
        return "\n\n".join(parts)

    def _inject_tools(self, tools: list, errors: list) -> str:
        valid: list[dict] = []
        for t in tools:
            try:
                # Validate minimal schema
                assert "name" in t, "tool missing 'name'"
                valid.append(t)
            except Exception as exc:
                warn("PROMPT_BUILDER", f"Invalid tool schema skipped: {exc}")
                errors.append("TOOL_SCHEMA_INVALID")
        if not valid:
            return ""
        schema_str = json.dumps(valid, indent=2)
        trace("PROMPT_BUILDER", "tools_injected",
              tool_count=len(valid),
              schema_token_estimate=len(schema_str) // CHARS_PER_TOKEN)
        return f"## Available Tools\n```json\n{schema_str}\n```"

    def _inject_skills(self, skills: list, errors: list) -> str:
        if not skills:
            return ""
        text = "\n".join(f"- {s}" for s in skills)
        trace("PROMPT_BUILDER", "skills_injected", skill_count=len(skills))
        return f"## Active Skills\n{text}"

    def _inject_context(self, context_files: list, errors: list) -> str:
        parts: list[str] = []
        for path in context_files:
            try:
                with open(path, "r", encoding="utf-8") as fh:
                    parts.append(f"### {os.path.basename(path)}\n{fh.read()}")
            except Exception as exc:
                warn("PROMPT_BUILDER", f"Context file {path} failed: {exc}")
        return "\n\n".join(parts)

    def _apply_cache_markers(self, parts: list, errors: list) -> list:
        """
        Returns a list of section indices where cache breakpoints should be inserted.
        Anthropic recommends marking the end of large static sections.
        """
        try:
            markers = []
            cumulative = 0
            for i, part in enumerate(parts):
                cumulative += len(part)
                if cumulative >= 1024 * CHARS_PER_TOKEN:  # ~1k tokens
                    markers.append({"section_index": i, "cumulative_chars": cumulative})
            trace("PROMPT_BUILDER", "cache_marked",
                  marker_count=len(markers), eligible_sections=len(parts))
            return markers
        except Exception as exc:
            warn("PROMPT_BUILDER", f"Cache marker failed: {exc}")
            errors.append("CACHE_MARKER_FAIL")
            return []
