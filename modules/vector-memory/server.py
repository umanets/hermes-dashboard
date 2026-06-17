"""Vector memory MCP server (FastMCP, stdio) — the RECALL (read) side.

The agent uses these tools to recall past artifacts/notes the capture hook and
cron summary stored. Reads the same on-disk Chroma dir as the capture side
(VECTOR_MEMORY_DATA_DIR).

Run standalone:  VECTOR_MEMORY_DATA_DIR=... python server.py
"""

from __future__ import annotations

import logging
import os
from datetime import datetime, timezone

from mcp.server.fastmcp import FastMCP

from vector_memory.store import MemoryStore

logging.basicConfig(level=logging.INFO)

DATA_DIR = os.environ.get("VECTOR_MEMORY_DATA_DIR") or os.path.join(
    os.path.expanduser("~"), ".hermes", "vector-memory"
)

mcp = FastMCP("vector-memory")
_store: MemoryStore | None = None


def store() -> MemoryStore:
    global _store
    if _store is None:
        os.makedirs(DATA_DIR, exist_ok=True)
        _store = MemoryStore(DATA_DIR)
    return _store


@mcp.tool()
def memory_recall(query: str, k: int = 6, kind: str | None = None) -> dict:
    """Semantically recall past notes/artifacts ("did I ever mention X?").

    Returns matches with the original text snippet, a link back to the source
    (url / file path / obsidian:// uri), and a relevance score. Use the link to
    open the real artifact for full content.
    """
    where = {"kind": kind} if kind else None
    try:
        return {"query": query, "matches": store().query(query, k=k, where=where)}
    except Exception as exc:  # noqa: BLE001
        return {"error": str(exc), "matches": []}


@mcp.tool()
def memory_add(
    text: str,
    title: str | None = None,
    link: str | None = None,
    kind: str = "note",
    tags: list[str] | None = None,
) -> dict:
    """Explicitly remember something now (e.g. the user said "remember that ...").

    `link` is a stable reference (url/file/obsidian uri) used for dedup; `text`
    is what gets embedded for later recall.
    """
    md = {
        "kind": kind,
        "title": title,
        "link": link,
        "tags": tags or [],
        "source": "chat",
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    try:
        return store().add([{"text": text, "metadata": md}])
    except Exception as exc:  # noqa: BLE001
        return {"error": str(exc), "added": 0}


@mcp.tool()
def memory_recent(limit: int = 10) -> dict:
    """List the most recently remembered items."""
    try:
        return {"items": store().recent(limit=limit), "total": store().count()}
    except Exception as exc:  # noqa: BLE001
        return {"error": str(exc), "items": []}


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
