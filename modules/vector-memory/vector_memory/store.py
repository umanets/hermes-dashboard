"""Thin wrapper over a local persistent Chroma collection.

Design notes:
- Default embedding = Chroma's built-in ONNX MiniLM (all-MiniLM-L6-v2, 384-dim,
  CPU). No API key, no server; the model is fetched once on first use.
- Chroma metadata values must be scalars (str/int/float/bool) — lists are NOT
  allowed, so tags are stored as a comma-joined string.
- IDs are stable (hash of the artifact link, else the text) so re-capturing the
  same article/note/file upserts instead of duplicating.
"""

from __future__ import annotations

import hashlib
from typing import Any, Optional

_COLLECTION = "memory"


def stable_id(text: str, metadata: Optional[dict] = None) -> str:
    basis = (metadata or {}).get("link") or text
    return hashlib.sha256(basis.encode("utf-8")).hexdigest()[:16]


def _scalar_meta(md: Optional[dict]) -> dict:
    """Coerce metadata to Chroma-safe scalars (lists -> comma string)."""
    out: dict[str, Any] = {}
    for k, v in (md or {}).items():
        if v is None:
            continue
        if isinstance(v, (list, tuple, set)):
            out[k] = ", ".join(str(x) for x in v)
        elif isinstance(v, (str, int, float, bool)):
            out[k] = v
        else:
            out[k] = str(v)
    return out or {"kind": "note"}


class MemoryStore:
    def __init__(self, data_dir: str, collection: str = _COLLECTION) -> None:
        import chromadb  # imported lazily so stdlib-only consumers (extract) don't need it

        self._client = chromadb.PersistentClient(path=data_dir)
        self._col = self._client.get_or_create_collection(
            name=collection, metadata={"hnsw:space": "cosine"}
        )

    def add(self, items: list[dict]) -> dict:
        """items: [{text, metadata}]. Upserts by stable id; skips empty text."""
        ids: list[str] = []
        docs: list[str] = []
        metas: list[dict] = []
        seen: set[str] = set()
        for it in items:
            text = (it.get("text") or "").strip()
            if not text:
                continue
            md = _scalar_meta(it.get("metadata"))
            _id = it.get("id") or stable_id(text, md)  # explicit id wins (e.g. per-chunk ingest)
            if _id in seen:
                continue
            seen.add(_id)
            ids.append(_id)
            docs.append(text)
            metas.append(md)
        if not ids:
            return {"added": 0, "ids": []}
        self._col.upsert(ids=ids, documents=docs, metadatas=metas)
        return {"added": len(ids), "ids": ids}

    def query(self, text: str, k: int = 6, where: Optional[dict] = None) -> list[dict]:
        res = self._col.query(query_texts=[text], n_results=max(1, k), where=where or None)
        out: list[dict] = []
        ids = (res.get("ids") or [[]])[0]
        docs = (res.get("documents") or [[]])[0]
        metas = (res.get("metadatas") or [[]])[0]
        dists = (res.get("distances") or [[]])[0]
        for i in range(len(ids)):
            out.append(
                {
                    "id": ids[i],
                    "text": docs[i],
                    "metadata": metas[i] or {},
                    "score": round(1.0 - float(dists[i]), 4) if i < len(dists) else None,
                }
            )
        return out

    def recent(self, limit: int = 10) -> list[dict]:
        got = self._col.get(include=["documents", "metadatas"])
        rows = []
        ids = got.get("ids") or []
        docs = got.get("documents") or []
        metas = got.get("metadatas") or []
        for i in range(len(ids)):
            rows.append({"id": ids[i], "text": docs[i], "metadata": metas[i] or {}})
        rows.sort(key=lambda r: r["metadata"].get("created_at", ""), reverse=True)
        return rows[: max(1, limit)]

    def delete(self, ids: list[str]) -> dict:
        if ids:
            self._col.delete(ids=ids)
        return {"deleted": len(ids)}

    def count(self) -> int:
        return self._col.count()
