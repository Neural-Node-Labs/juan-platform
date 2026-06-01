"""
Project Juan — Main Entrypoint
Modes: repl | gateway | acp | ui-gateway
"""
from __future__ import annotations

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(__file__))

DB_PATH        = os.environ.get("JUAN_DB_PATH",    "juan_state.db")
WORKSPACE_ROOT = os.environ.get("JUAN_WORKSPACE",  "/data/workspace")


def build_agent(session_key: str = ""):
    from agent.run_agent              import AIAgent
    from tools.registry               import ToolRegistry
    from tools.approval               import ApprovalGate
    from agent.memory_manager         import MemoryManager
    from tools.builtin.workspace      import workspace_tool, WORKSPACE_SCHEMA
    from tools.builtin.swarm          import swarm_tool, SWARM_SCHEMA, _orchestrator
    from tools.builtin.skill_tool     import skill_tool, SKILL_TOOL_SCHEMA
    from tools.builtin.os_tool        import os_tool, OS_TOOL_SCHEMA

    gate     = ApprovalGate()
    # OS tool uses "high" threshold — shell requires explicit /approve
    registry = ToolRegistry(approval_gate=gate, approval_threshold="high")
    memory   = MemoryManager()

    # C-20 Workspace — file/folder/shell inside jail
    registry.register("workspace", workspace_tool, WORKSPACE_SCHEMA)

    # C-22 Skill — list/read/create skills at runtime
    registry.register("skill", skill_tool, SKILL_TOOL_SCHEMA)

    # C-23 OS — system-wide access (only if env flag set)
    registry.register("os", os_tool, OS_TOOL_SCHEMA)

    # C-21 Swarm — multi-agent task decomposition
    if _orchestrator is not None:
        registry.register("swarm", swarm_tool, SWARM_SCHEMA)

    # Validate all schemas before returning — fail fast, loud error
    errors = registry.validate_schemas()
    if errors:
        import sys as _sys
        print(f"[WARN] Tool schema errors detected:\n" +
              "\n".join(f"  • {e}" for e in errors), file=_sys.stderr)

    return AIAgent(
        model          = os.environ.get("JUAN_MODEL",    "claude-sonnet-4-20250514"),
        provider       = os.environ.get("JUAN_PROVIDER", "anthropic"),
        tool_registry  = registry,
        memory_manager = memory,
    )


def _init_swarm():
    from tools.builtin.swarm       import init_swarm
    from juan_cli.runtime_provider import call_provider
    init_swarm(
        agent_factory = build_agent,
        call_provider = call_provider,
        model    = os.environ.get("JUAN_MODEL",    "claude-sonnet-4-20250514"),
        provider = os.environ.get("JUAN_PROVIDER", "anthropic"),
    )
    print("[Juan] Swarm orchestrator ready")


def _ensure_workspace():
    import pathlib
    ws = pathlib.Path(WORKSPACE_ROOT)
    ws.mkdir(parents=True, exist_ok=True)
    print(f"[Juan] Workspace: {ws}")


def _log_os_tool_status():
    enabled = os.environ.get("JUAN_ENABLE_OS_TOOL", "").lower() in ("true", "1", "yes")
    status = "ENABLED" if enabled else "disabled (set JUAN_ENABLE_OS_TOOL=true to enable)"
    print(f"[Juan] OS tool:   {status}")


def run_gateway():
    from gateway.run    import GatewayRunner
    from tools.approval import ApprovalGate

    _init_swarm()
    _ensure_workspace()
    _log_os_tool_status()
    gate   = ApprovalGate()
    runner = GatewayRunner(agent_factory=build_agent, approval_gate=gate)

    platform = os.environ.get("JUAN_PLATFORM", "telegram")
    token    = os.environ.get("JUAN_BOT_TOKEN", "")

    if token:
        print(f"[Juan] Gateway: {platform}")
        runner.add_platform(platform, token)
        runner.run_forever()
    else:
        print("[Juan] No JUAN_BOT_TOKEN — REPL mode")
        run_repl()


def run_ui_gateway():
    from gateway.ui_gateway import get_ui_gateway

    _init_swarm()
    _ensure_workspace()
    _log_os_tool_status()
    gw = get_ui_gateway()
    gw.set_agent_factory(build_agent)

    ws_port   = int(os.environ.get("UI_WS_PORT",   "5001"))
    http_port = int(os.environ.get("UI_HTTP_PORT", "5002"))
    print(f"[Juan] UI Gateway: WS :{ws_port}  HTTP :{http_port}")
    gw.start(ws_port=ws_port, http_port=http_port)

    import time
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        pass


def run_acp():
    from acp_adapter.server import ACPServer
    from juan_state         import get_db

    _init_swarm()
    _ensure_workspace()
    _log_os_tool_status()
    server = ACPServer(agent_factory=build_agent, session_store=get_db(DB_PATH))
    print("[Juan] ACP server (stdin/stdout JSON-RPC)", file=sys.stderr)
    server.run()


def run_repl():
    import uuid
    _init_swarm()
    _ensure_workspace()
    _log_os_tool_status()
    agent      = build_agent()
    session_id = uuid.uuid4().hex

    print(f"Juan REPL — workspace: {WORKSPACE_ROOT}")
    print("Type 'exit' to quit\n")
    while True:
        try:
            msg = input("You: ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\nBye!")
            break
        if msg.lower() in ("exit", "quit"):
            break
        if not msg:
            continue
        result = agent.run(
            user_message   = msg,
            session_id     = session_id,
            max_iterations = int(os.environ.get("MAX_ITERATIONS", 30)),
        )
        print(f"\nJuan: {result['response']}")
        print(f"  [{result['iterations_used']} iter | "
              f"{result['usage']['input_tokens']}+{result['usage']['output_tokens']} tok | "
              f"{result['end_reason']}]\n")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Project Juan")
    parser.add_argument("--mode",
                        choices=["gateway", "acp", "repl", "ui-gateway"],
                        default="repl")
    args = parser.parse_args()

    if   args.mode == "gateway":    run_gateway()
    elif args.mode == "acp":        run_acp()
    elif args.mode == "ui-gateway": run_ui_gateway()
    else:                           run_repl()
