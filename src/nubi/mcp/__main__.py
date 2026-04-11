"""Entry point for running the Nubi MCP server as a module.

Usage:
    python -m nubi.mcp [--version]

The server will start on the port specified by NUBI_MCP_PORT environment
variable (default: 8080) using streamable HTTP transport.
"""

from __future__ import annotations

import sys
from argparse import ArgumentParser

from nubi import __version__


def main() -> None:
    """Parse arguments and run the MCP server."""
    parser = ArgumentParser(prog="nubi-mcp", description="Nubi MCP server")
    parser.add_argument(
        "--version",
        action="store_true",
        help="Print the version number and exit",
    )
    args = parser.parse_args()

    if args.version:
        print(f"nubi-mcp {__version__}")
        sys.exit(0)

    from nubi.mcp.server import mcp

    mcp.run(transport="streamable-http")


if __name__ == "__main__":
    main()
