"""The package's two entry points: `python -m nina_planner[.server]`.

Two different questions, two tests:

- the HANDSHAKE test spawns the real `python -m nina_planner` and speaks
  JSON-RPC to it with the MCP SDK's own stdio client — no mocks, no NINA.
  It fails if the entry point stops booting FastMCP, if a tool stops
  registering, or if anything writes junk to stdout and corrupts the
  protocol stream (the failure mode that makes a configured MCP server
  "connect" but never answer).
- the WIRING tests run the modules in-process with ``mcp.run`` patched,
  which is what proves the ``if __name__ == "__main__"`` guards actually
  reach the server loop. A subprocess cannot record coverage back into
  this process, so this is the leg that closes those lines.
"""

from __future__ import annotations

import asyncio
import runpy
import sys
import unittest
import warnings
from unittest import mock

from mcp import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client

# The tools an MCP client is configured to call on this server; a handful
# from each area so a partial registration cannot pass.
EXPECTED_TOOLS = {
    "get_nina_status",
    "get_site_equipment_status",
    "get_site_profile",
    "load_sequence",
    "start_sequence",
    "get_plan_progress",
    "audit_plans",
    "best_targets",
    "sun_times",
    "moon_times",
    "resolve_target",
}

HANDSHAKE_TIMEOUT_S = 60


class EntryPointHandshakeTest(unittest.IsolatedAsyncioTestCase):
    async def test_python_m_nina_planner_speaks_mcp(self):
        async def handshake():
            params = StdioServerParameters(
                command=sys.executable, args=["-m", "nina_planner"]
            )
            async with stdio_client(params) as (read, write):
                async with ClientSession(read, write) as session:
                    init = await session.initialize()
                    tools = await session.list_tools()
                    return init, tools

        init, tools = await asyncio.wait_for(handshake(), HANDSHAKE_TIMEOUT_S)

        self.assertEqual(init.serverInfo.name, "nina-planner")
        names = {tool.name for tool in tools.tools}
        self.assertEqual(
            EXPECTED_TOOLS - names,
            set(),
            msg=f"missing from tools/list: {sorted(EXPECTED_TOOLS - names)}",
        )


class EntryPointWiringTest(unittest.TestCase):
    """`mcp.run()` is reached from both ``__main__`` guards."""

    @staticmethod
    def _run_module(module: str) -> mock.MagicMock:
        from nina_planner.app import mcp as server_mcp

        with mock.patch.object(server_mcp, "run") as run:
            # runpy re-executes an already-imported module under __main__;
            # its "found in sys.modules" RuntimeWarning is expected here.
            with warnings.catch_warnings():
                warnings.simplefilter("ignore", RuntimeWarning)
                runpy.run_module(module, run_name="__main__")
        run.assert_called_once_with()
        return run

    def test_nina_planner_main_runs_the_server(self):
        self._run_module("nina_planner.__main__")

    def test_server_module_runs_the_server_when_executed_directly(self):
        # `python -m nina_planner.server` — the same guard server.py keeps
        # for running the file as a script.
        self._run_module("nina_planner.server")


if __name__ == "__main__":
    unittest.main()
