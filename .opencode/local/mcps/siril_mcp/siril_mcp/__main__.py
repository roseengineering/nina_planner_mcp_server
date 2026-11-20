"""Run the MCP server in stdio mode (``python -m nina_planner``)."""

from .server import mcp


def main() -> None:
    """Serve the MCP toolset over stdio until the client disconnects."""
    mcp.run()


if __name__ == "__main__":
    main()
