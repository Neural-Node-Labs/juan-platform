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
    from juan_state                   import get_db
    from tools.builtin.workspace      import workspace_tool, WORKSPACE_SCHEMA
    from tools.builtin.swarm          import swarm_tool, SWARM_SCHEMA, _orchestrator
    from juan_cli.runtime_provider    import call_provider

    gate     = ApprovalGate()
    registry = ToolRegistry(approval_gate=gate, approval_threshold="high")
    memory   = MemoryManager()

    # ── C-20  Workspace tool ──────────────────────────────────────────────────
    registry.register("workspace", workspace_tool, WORKSPACE_SCHEMA)

    # ── C-21  Swarm tool ──────────────────────────────────────────────────────
    # Swarm needs a reference to agent factory + provider — inject lazily
    if _orchestrator is not None:
        registry.register("swarm", swarm_tool, SWARM_SCHEMA)
    else:
        # Lazy: will be registered after init_swarm is called below
        pass

    return AIAgent(
        model          = os.environ.get("JUAN_MODEL",    "claude-sonnet-4-20250514"),
        provider       = os.environ.get("JUAN_PROVIDER", "anthropic"),
        tool_registry  = registry,
        memory_manager = memory,
    )


def _init_swarm():
    """Initialise the swarm orchestrator once at startup."""
    from tools.builtin.swarm          import init_swarm
    from juan_cli.runtime_provider    import call_provider
    init_swarm(
        agent_factory = build_agent,
        call_provider = call_provider,
        model    = os.environ.get("JUAN_MODEL",    "claude-sonnet-4-20250514"),
        provider = os.environ.get("JUAN_PROVIDER", "anthropic"),
    )
    # Now re-register so future agents have the swarm tool
    from tools.builtin.swarm import _orchestrator
    from tools.builtin.swarm import swarm_tool, SWARM_SCHEMA
    # The tool is already registered in build_agent if _orchestrator is set
    print("[Juan] Swarm orchestrator ready")


def _ensure_workspace():
    import pathlib
    ws = pathlib.Path(WORKSPACE_ROOT)
    ws.mkdir(parents=True, exist_ok=True)
    print(f"[Juan] Workspace: {ws}")


def run_gateway():
    from gateway.run    import GatewayRunner
    from tools.approval import ApprovalGate

    _init_swarm()
    _ensure_workspace()
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
    from juan_state          import get_db

    _init_swarm()
    _ensure_workspace()
    server = ACPServer(agent_factory=build_agent, session_store=get_db(DB_PATH))
    print("[Juan] ACP server (stdin/stdout JSON-RPC)", file=sys.stderr)
    server.run()


def run_repl():
    import uuid
    _init_swarm()
    _ensure_workspace()
    agent      = build_agent()
    session_id = uuid.uuid4().hex
    ws_path    = os.environ.get("JUAN_WORKSPACE", "/data/workspace")
    print(f"Juan REPL — workspace: {ws_path}")
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
