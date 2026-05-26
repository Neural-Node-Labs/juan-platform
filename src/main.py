"""
Project Juan — Main Entrypoint
Modes: repl | gateway | acp
The dashboard runs as a separate process (started by entrypoint.sh).
"""
from __future__ import annotations

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(__file__))

# Resolve DB path from env (set by Docker)
DB_PATH = os.environ.get("JUAN_DB_PATH", "juan_state.db")


def build_agent(session_key: str = ""):
    """Factory: creates a fresh AIAgent per call."""
    from agent.run_agent    import AIAgent
    from tools.registry     import ToolRegistry
    from tools.approval     import ApprovalGate
    from agent.memory_manager import MemoryManager
    from juan_state         import get_db

    db       = get_db(DB_PATH)
    gate     = ApprovalGate()
    registry = ToolRegistry(approval_gate=gate)
    memory   = MemoryManager()

    # ── Register built-in / custom tools here ────────────────────────────────
    # Example:
    # def echo_tool(text: str = "") -> dict:
    #     return {"content": text, "exit_code": 0}
    # registry.register("echo", echo_tool, schema={"name":"echo","description":"Echo text"})
    # ─────────────────────────────────────────────────────────────────────────

    return AIAgent(
        model         = os.environ.get("JUAN_MODEL", "claude-sonnet-4-20250514"),
        provider      = os.environ.get("JUAN_PROVIDER", "anthropic"),
        tool_registry = registry,
        memory_manager= memory,
    )


def run_gateway():
    from gateway.run import GatewayRunner
    from tools.approval import ApprovalGate

    gate   = ApprovalGate()
    runner = GatewayRunner(agent_factory=build_agent, approval_gate=gate)

    platform = os.environ.get("JUAN_PLATFORM", "telegram")
    token    = os.environ.get("JUAN_BOT_TOKEN", "")

    if token:
        print(f"[Juan] Starting gateway on platform: {platform}")
        runner.add_platform(platform, token)
        runner.run_forever()
    else:
        print("[Juan] No JUAN_BOT_TOKEN — starting local REPL instead")
        run_repl()


def run_acp():
    from acp_adapter.server import ACPServer
    from juan_state import get_db

    server = ACPServer(agent_factory=build_agent, session_store=get_db(DB_PATH))
    print("[Juan] ACP server ready (stdin/stdout JSON-RPC)", file=sys.stderr)
    server.run()


def run_repl():
    import uuid
    agent      = build_agent()
    session_id = uuid.uuid4().hex
    print("Juan REPL — type 'exit' to quit\n")
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
              f"{result['usage']['input_tokens']}+{result['usage']['output_tokens']} tok "
              f"| {result['end_reason']}]\n")


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
