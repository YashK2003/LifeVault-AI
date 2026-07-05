"""Run the MCP server as a module entry point."""
from .server import mcp

if __name__ == "__main__":
    mcp.run(transport="stdio")
