"""Reusable, service-independent music integration layer for Hermes.

The Hermes MCP tools (see ``server.py``) are thin wrappers around the services
defined here. Nothing in this package imports Hermes; it is a standalone library
so it can be unit-tested and reused outside of the agent.
"""

__all__ = ["models", "errors", "adapter", "config", "matching", "registry"]

__version__ = "0.1.0"
