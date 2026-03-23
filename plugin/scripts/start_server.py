"""
HCE MCP Server Launcher
========================
Auto-installs HCE from GitHub if not present, then starts the MCP server.
Works on both Windows (Git Bash / CMD) and Linux (Cowork VM).

Requires GitHub access to denson/hypergraph_code_explorer (private repo).
Git credentials must be configured (SSH key, credential helper, or GH CLI).
"""

import subprocess
import sys


def log(msg: str):
    """Print a status message to stderr (visible to the user, not to MCP)."""
    print(f"[HCE] {msg}", file=sys.stderr, flush=True)


def ensure_installed():
    """Install HCE from GitHub if not already available."""
    log("Checking for HCE installation...")
    try:
        import hypergraph_code_explorer  # noqa: F401
        log(f"HCE found (version: {getattr(hypergraph_code_explorer, '__version__', 'unknown')})")
        import mcp  # noqa: F401
        log("MCP dependency found. Ready.")
        return
    except ImportError:
        pass

    log("HCE not found — downloading from GitHub...")
    log("This may take a minute on first run.")
    try:
        subprocess.check_call(
            [
                sys.executable, "-m", "pip", "install",
                "hypergraph-code-explorer[server] @ git+https://github.com/denson/hypergraph_code_explorer.git",
                "--break-system-packages",
                "--quiet",
            ],
            stderr=sys.stderr,
        )
        log("HCE installed successfully. Starting MCP server...")
    except subprocess.CalledProcessError:
        print(
            "\n"
            "ERROR: Failed to install HCE.\n"
            "\n"
            "This is most likely a GitHub authentication issue.\n"
            "The repo denson/hypergraph_code_explorer is private — you need\n"
            "Git credentials configured to access it.\n"
            "\n"
            "Options:\n"
            "  1. SSH key:  git clone git@github.com:denson/hypergraph_code_explorer.git\n"
            "  2. GH CLI:   gh auth login\n"
            "  3. HTTPS:    git credential helper (stored or manager-core)\n"
            "\n"
            "Once credentials are set up, restart Claude to retry.\n",
            file=sys.stderr,
        )
        sys.exit(1)


if __name__ == "__main__":
    ensure_installed()
    from hypergraph_code_explorer.mcp_server import main
    main()
