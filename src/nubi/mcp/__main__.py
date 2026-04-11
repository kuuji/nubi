"""Entry point for running the Nubi MCP server as a module.

Usage:
    python -m nubi.mcp

The server will start on the port specified by NUBI_MCP_PORT environment
variable (default: 8080) using streamable HTTP transport.
"""

from __future__ import annotations

from nubi.mcp.server import mcp

if __name__ == "__main__":
    mcp.run(transport="streamable-http", host="0.0.0.0")
