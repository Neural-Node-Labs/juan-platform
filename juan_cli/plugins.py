"""
C-11 · PLUGIN_MANAGER
Discovers, loads, and lifecycle-manages plugins from three sources:
  1. pip entry points (juan.plugins)
  2. user plugin directory (~/.juan/plugins/)
  3. project plugin directory (./plugins/)
Fires hooks at defined gateway and agent events.
"""
from __future__ import annotations

import importlib
import importlib.metadata
import os
import sys
import time
from typing import Any, Callable

from logger import trace, warn

HOOK_EVENTS = {
    "on_message", "on_agent_start", "on_agent_end",
    "on_tool_call", "on_tool_result", "on_session_start", "on_session_end",
}

PLUGIN_DIRS = [
    os.path.expanduser("~/.juan/plugins"),
    os.path.join(os.getcwd(), "plugins"),
]


class PluginError(Exception):
    def __init__(self, error_code: str, plugin_name: str, message: str):
        super().__init__(message)
        self.error_code  = error_code
        self.plugin_name = plugin_name

    def to_dict(self) -> dict:
        return {"error_code": self.error_code, "plugin_name": self.plugin_name, "message": str(self)}


class PluginManager:
    """C-11 · PLUGIN_MANAGER"""

    def __init__(self):
        # Hooks: event → list of (plugin_name, callable)
        self._hooks: dict[str, list[tuple[str, Callable]]] = {e: [] for e in HOOK_EVENTS}
        self._tools: dict[str, Callable] = {}
        self._errors: list[dict]         = []
        # Single-select specialized plugins
        self._memory_provider: str | None  = None
        self._context_engine:  str | None  = None

    def discover_and_load(self) -> None:
        """Load all plugins from all three sources."""
        self._load_from_entry_points()
        for d in PLUGIN_DIRS:
            if os.path.isdir(d):
                self._load_from_directory(d)

    # ── Loading ────────────────────────────────────────────────────────────────

    def _load_from_entry_points(self) -> None:
        try:
            eps = importlib.metadata.entry_points(group="juan.plugins")
            for ep in eps:
                self._load_one(ep.name, ep.load, source="pip")
        except Exception as exc:
            warn("PLUGIN_MANAGER", f"Entry point discovery failed: {exc}")

    def _load_from_directory(self, directory: str) -> None:
        if directory not in sys.path:
            sys.path.insert(0, directory)
        for fname in sorted(os.listdir(directory)):
            if fname.endswith(".py") and not fname.startswith("_"):
                name = fname[:-3]
                self._load_one(name, lambda n=name: importlib.import_module(n), source="file")

    def _load_one(self, name: str, loader: Callable, source: str) -> None:
        try:
            mod = loader()
            self._register_plugin(name, mod)
            trace("PLUGIN_MANAGER", "plugin_discovered",
                  source=source, plugin_name=name)
        except PluginError:
            raise
        except Exception as exc:
            err = {"plugin_name": name, "error_code": "PLUGIN_LOAD_FAIL", "message": str(exc)}
            self._errors.append(err)
            warn("PLUGIN_MANAGER", f"Plugin load failed (skipping): {name}: {exc}")

    def _register_plugin(self, name: str, mod: Any) -> None:
        # Register hooks
        for event in HOOK_EVENTS:
            fn = getattr(mod, f"on_{event.removeprefix('on_')}", None) or getattr(mod, event, None)
            if callable(fn):
                self._hooks[event].append((name, fn))

        # Register tools
        tools = getattr(mod, "TOOLS", {})
        for tool_name, tool_fn in tools.items():
            if tool_name in self._tools:
                raise PluginError("TOOL_CONFLICT", name,
                                  f"Tool '{tool_name}' already registered")
            self._tools[tool_name] = tool_fn
            trace("PLUGIN_MANAGER", "tool_registered",
                  tool_name=tool_name, plugin_name=name)

        # Single-select: memory provider
        if hasattr(mod, "MEMORY_PROVIDER"):
            if self._memory_provider:
                raise PluginError("TOOL_CONFLICT", name,
                                  f"Memory provider already set to {self._memory_provider}")
            self._memory_provider = name

        # Single-select: context engine
        if hasattr(mod, "CONTEXT_ENGINE"):
            if self._context_engine:
                raise PluginError("TOOL_CONFLICT", name,
                                  f"Context engine already set to {self._context_engine}")
            self._context_engine = name

    # ── Hook firing ────────────────────────────────────────────────────────────

    def fire(self, event: str, context: dict,
             plugin_type: str | None = None) -> dict:
        """
        Fire all hooks for the given event.
        Returns {"hooks_fired": [...], "tools_registered": [...], "errors": [...]}
        """
        fired: list[str] = []
        new_errors: list[dict] = []

        for plugin_name, fn in self._hooks.get(event, []):
            t0 = time.time()
            try:
                fn(context)
                fired.append(plugin_name)
                trace("PLUGIN_MANAGER", "hook_fired",
                      event=event, plugin_name=plugin_name,
                      duration_ms=round((time.time() - t0) * 1000))
            except Exception as exc:
                err = {"plugin_name": plugin_name, "error_code": "HOOK_ERROR", "message": str(exc)}
                new_errors.append(err)
                warn("PLUGIN_MANAGER",
                     f"Hook error in {plugin_name}.{event} (continuing): {exc}")
                trace("PLUGIN_MANAGER", "plugin_error",
                      plugin_name=plugin_name, error_type="HOOK_ERROR")

        self._errors.extend(new_errors)
        return {
            "hooks_fired":      fired,
            "tools_registered": list(self._tools.keys()),
            "errors":           new_errors,
        }

    def get_tool_hooks(self) -> dict[str, Callable]:
        return dict(self._tools)
