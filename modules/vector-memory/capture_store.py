"""Capture worker invoked by the agent:end hook (runs in the module venv).

Reads a JSON turn payload from stdin: {"user": str, "assistant": str,
"session_id": str}. Extracts artifacts heuristically (zero LLM) and upserts them
into the Chroma store. Designed to be fired-and-forgotten in the background by
the hook, so it must never raise into the caller — it just logs and exits.
"""

from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone

from vector_memory.extract import extract_artifacts
from vector_memory.store import MemoryStore

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass


def main() -> int:
    data_dir = os.environ.get("VECTOR_MEMORY_DATA_DIR") or (
        sys.argv[1] if len(sys.argv) > 1 else os.path.join(os.path.expanduser("~"), ".hermes", "vector-memory")
    )
    try:
        payload = json.loads(sys.stdin.read() or "{}")
    except Exception:
        return 0

    items = extract_artifacts(payload.get("user", ""), payload.get("assistant", ""))
    if not items:
        return 0

    now = datetime.now(timezone.utc).isoformat()
    session = payload.get("session_id", "")
    for it in items:
        it["metadata"]["created_at"] = now
        if session:
            it["metadata"]["session_id"] = session

    try:
        os.makedirs(data_dir, exist_ok=True)
        result = MemoryStore(data_dir).add(items)
        print(f"vector-capture: stored {result.get('added', 0)} artifact(s)", file=sys.stderr)
    except Exception as exc:  # noqa: BLE001
        print(f"vector-capture: store failed: {exc}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
