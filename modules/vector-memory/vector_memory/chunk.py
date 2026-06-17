"""Markdown chunking + frontmatter split — pure stdlib (no chromadb), so it's
unit-testable on its own. Chunks by heading sections, then packs/splits to a
target size so each embedding stays focused (MiniLM has a small token window).
"""

from __future__ import annotations

import re

_FM = re.compile(r"^﻿?---\r?\n(.*?)\r?\n---\r?\n?", re.S)
_HEADING_SPLIT = re.compile(r"(?m)^(?=#{1,6}\s)")
_PARA_SPLIT = re.compile(r"\n\s*\n")


def split_frontmatter(text: str) -> tuple[dict, str]:
    """Return (frontmatter dict (best-effort), body without frontmatter)."""
    m = _FM.match(text or "")
    if not m:
        return {}, text or ""
    meta: dict[str, str] = {}
    for line in m.group(1).splitlines():
        kv = re.match(r"^([A-Za-z_][\w-]*):\s*(.*)$", line)
        if kv:
            meta[kv.group(1)] = kv.group(2).strip()
    return meta, text[m.end():]


def chunk_markdown(text: str, max_chars: int = 800, overlap: int = 80) -> list[str]:
    """Split markdown into <=max_chars chunks, preferring heading/paragraph boundaries."""
    text = (text or "").strip()
    if not text:
        return []
    blocks = [b.strip() for b in _HEADING_SPLIT.split(text) if b.strip()] or [text]
    chunks: list[str] = []
    cur = ""  # pack consecutive small heading-sections together up to max_chars

    def flush() -> None:
        nonlocal cur
        if cur:
            chunks.append(cur)
            cur = ""

    for block in blocks:
        if len(block) <= max_chars:
            if cur and len(cur) + len(block) + 2 > max_chars:
                flush()
            cur = f"{cur}\n\n{block}" if cur else block
            continue
        # block bigger than the target: flush the packed buffer, then split it
        flush()
        for para in _PARA_SPLIT.split(block):
            if len(para) > max_chars:
                flush()
                step = max(1, max_chars - overlap)
                for i in range(0, len(para), step):
                    chunks.append(para[i : i + max_chars])
                continue
            if cur and len(cur) + len(para) + 2 > max_chars:
                flush()
            cur = f"{cur}\n\n{para}" if cur else para
        flush()
    flush()
    return chunks
