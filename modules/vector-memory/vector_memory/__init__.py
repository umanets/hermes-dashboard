"""Vector memory for Hermes — persistent semantic recall over a local Chroma store.

Capture (write) is driven by the agent:end hook (heuristics) + an optional cron
LLM-summary; recall (read) is exposed to the agent via the MCP server. Both sides
operate on the same on-disk Chroma directory (VECTOR_MEMORY_DATA_DIR).
"""

__all__ = ["MemoryStore", "extract_artifacts"]

from .store import MemoryStore
from .extract import extract_artifacts
