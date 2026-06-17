"""Bulk-ingest a folder of Markdown notes (e.g. an Obsidian vault) into vector memory.

Walks *.md, chunks each note, and upserts chunks with a link back to the source
(`obsidian://` deep-link by default). Incremental: skips files unchanged since the
last run (by mtime). Prints machine-parsable progress lines the dashboard reads:

  INGEST total=<N>
  INGEST progress=<i> total=<N> (added|skipped|error)=<relpath> [chunks=<c>]
  INGEST done files=<N> added=<A> skipped=<S> chunks=<C>

Usage: <venv python> ingest.py --dir <folder> --data-dir <chroma dir> [--vault NAME]
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from datetime import datetime, timezone
from urllib.parse import quote

from vector_memory.chunk import chunk_markdown, split_frontmatter
from vector_memory.store import MemoryStore

# Windows consoles default to a legacy codepage (e.g. cp1251) → print() of
# non-ASCII (Cyrillic filenames/notes) raises UnicodeEncodeError. Force UTF-8.
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

SKIP_DIRS = {".obsidian", ".trash", ".git", "node_modules", ".smart-env", "__pycache__"}


def _walk_md(root: str) -> list[str]:
    out: list[str] = []
    for dp, dns, fns in os.walk(root):
        dns[:] = [d for d in dns if d not in SKIP_DIRS and not d.startswith(".")]
        for fn in fns:
            if fn.lower().endswith(".md"):
                out.append(os.path.join(dp, fn))
    return sorted(out)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dir", required=True)
    ap.add_argument("--data-dir", default=None, help="default: $VECTOR_MEMORY_DATA_DIR")
    ap.add_argument("--vault", default=None, help="Obsidian vault name (default: folder name)")
    args = ap.parse_args()

    data_dir = args.data_dir or os.environ.get("VECTOR_MEMORY_DATA_DIR") or os.path.join(
        os.path.expanduser("~"), ".hermes", "vector-memory"
    )
    args.data_dir = data_dir

    root = os.path.abspath(args.dir)
    if not os.path.isdir(root):
        print(f"INGEST error=not_a_directory dir={root}", flush=True)
        return 1
    vault = args.vault or os.path.basename(root.rstrip("/\\"))
    files = _walk_md(root)
    print(f"INGEST total={len(files)}", flush=True)

    os.makedirs(args.data_dir, exist_ok=True)
    state_path = os.path.join(args.data_dir, "ingest_state.json")
    try:
        with open(state_path, "r", encoding="utf-8") as f:
            state = json.load(f)
    except Exception:
        state = {}

    store = MemoryStore(args.data_dir)
    now = datetime.now(timezone.utc).isoformat()
    added = skipped = total_chunks = 0

    for i, path in enumerate(files, 1):
        rel = os.path.relpath(path, root).replace("\\", "/")
        try:
            mtime = os.path.getmtime(path)
        except OSError:
            mtime = 0
        if state.get(rel) == mtime:
            skipped += 1
            print(f"INGEST progress={i} total={len(files)} skipped={rel}", flush=True)
            continue
        try:
            text = open(path, encoding="utf-8", errors="ignore").read()
        except Exception:
            print(f"INGEST progress={i} total={len(files)} error={rel}", flush=True)
            continue

        meta, body = split_frontmatter(text)
        title = meta.get("title") or os.path.splitext(os.path.basename(path))[0]
        tags = meta.get("tags", "")
        file_no_ext = rel[:-3] if rel.lower().endswith(".md") else rel
        link = f"obsidian://open?vault={quote(vault)}&file={quote(file_no_ext)}"
        chunks = chunk_markdown(body)
        fid = hashlib.sha256(rel.encode("utf-8")).hexdigest()[:12]

        items = [
            {
                "id": f"{fid}-{idx}",
                "text": f"{title}\n\n{ch}" if idx == 0 else ch,
                "metadata": {
                    "kind": "note",
                    "source": "obsidian",
                    "title": title,
                    "link": link,
                    "tags": tags,
                    "path": rel,
                    "chunk": idx,
                    "created_at": now,
                },
            }
            for idx, ch in enumerate(chunks)
        ]
        if items:
            store.add(items)
        state[rel] = mtime
        added += 1
        total_chunks += len(chunks)
        print(f"INGEST progress={i} total={len(files)} added={rel} chunks={len(chunks)}", flush=True)

    try:
        with open(state_path, "w", encoding="utf-8") as f:
            json.dump(state, f)
    except Exception as exc:  # noqa: BLE001
        print(f"INGEST warn=state_write_failed {exc}", file=sys.stderr, flush=True)

    print(f"INGEST done files={len(files)} added={added} skipped={skipped} chunks={total_chunks}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
