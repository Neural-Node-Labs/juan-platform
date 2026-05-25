# Juan — Project entrypoint
"""
Project Juan — Autonomous Agent Platform
Run with: python main.py [--mode gateway|acp|cron]
"""
from __future__ import annotations

import argparse
import sys
import os
from dotenv import load_dotenv

load_dotenv()

# Make project root importable
sys.path.insert(0, os.path.dirname(__file__))


def build_agent(session_key: str = ""):
    """Factory: creates a fresh AIAgent per session."""
    from agent.run_agent    import AIAgent
    from tools.registry     import ToolRegistry
    from tools.approval     import ApprovalGate
    from agent.memory_manager import MemoryManager

    gate     = ApprovalGate()
    registry = ToolRegistry(approval_gate=gate)

    # Register built-in tools here
    # registry.register("bash", bash_tool_fn, schema={...})

    return AIAgent(
        model         = os.environ.get("JUAN_MODEL", "deepseek-v4-flash"),
        provider      = os.environ.get("JUAN_PROVIDER", "deepseek"),
        tool_registry = registry,
    )


def run_gateway():
    from gateway.run import GatewayRunner
    runner = GatewayRunner(agent_factory=build_agent)
    platform = os.environ.get("JUAN_PLATFORM", "telegram")
    token    = os.environ.get("JUAN_BOT_TOKEN", "")
    if token:
        runner.add_platform(platform, token)
        print(f"[Juan] Gateway running on {platform}")
        runner.run_forever()
    else:
        print("[Juan] No JUAN_BOT_TOKEN set. Running in local REPL mode.")
        run_repl()


def run_acp():
    from acp_adapter.server import ACPServer
    from juan_state import get_db
    server = ACPServer(agent_factory=build_agent, session_store=get_db())
    print("[Juan] ACP server ready (stdin/stdout JSON-RPC)", file=sys.stderr)
    server.run()


def run_repl():
    """Simple interactive REPL for local testing."""
    import uuid
    agent      = build_agent()
    session_id = uuid.uuid4().hex
    print("Juan REPL — type 'exit' to quit")
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
        result = agent.run(user_message=msg, session_id=session_id)
        print(f"Juan: {result['response']}")
        print(f"  [{result['iterations_used']} iter | "
              f"{result['usage']['input_tokens']}+{result['usage']['output_tokens']} tokens]")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Project Juan Agent Platform")
    parser.add_argument("--mode", choices=["gateway", "acp", "repl"],
                        default="repl", help="Run mode")
    args = parser.parse_args()

    if args.mode == "gateway":
        run_gateway()
    elif args.mode == "acp":
        run_acp()
    else:
        run_repl()
