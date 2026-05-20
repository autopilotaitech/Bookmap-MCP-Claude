"""Pax AI - floating dark-glass quant chat over Bookmap MCP dashboard.

Phase 0: HTTP server scaffold with placeholder index + /api/snapshot proxy.
See docs/superpowers/specs/2026-05-19-pax-ai-design.md.
"""

__version__ = "0.1.0"

# Pax AI HTTP listen port. The dashboard is :18888, overview UI is :18890,
# Pax AI is :18891. Keep ports in this neighborhood so verify-runtime can
# enumerate them as a contiguous block.
DEFAULT_PORT = 18891
DASHBOARD_URL = "http://127.0.0.1:18888/api/snapshot"
