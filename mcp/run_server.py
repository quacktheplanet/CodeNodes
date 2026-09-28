"""Start the CodeNodes MCP server without installing anything (the Claude Code plugin runs
this): puts this folder on the path and runs codenodes_mcp. Standard library only."""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from codenodes_mcp.server import main  # noqa: E402

if __name__ == "__main__":
    main()
