"""Cron batch: distill salient, recall-worthy facts from recent conversation.

Runs off the conversation's critical path (script-only cron job). Reads new
messages from Hermes' state.db since a high-water-mark, asks an OpenAI-compatible
LLM to extract durable facts (preferences, decisions, projects, people, key
context — NOT chit-chat), and upserts them into the same Chroma store the recall
MCP reads.

Uses OpenRouter by default (OpenAI-compatible). You provide an OpenRouter API
key and pick a model; the base url defaults to OpenRouter. If no key/model
resolves it logs "disabled" and exits 0 — heuristic capture keeps working.

Env:
  VECTOR_MEMORY_DATA_DIR   chroma dir (shared with capture/recall)
  VECTOR_MEMORY_STATE_DB   path to ~/.hermes/state.db
  VECTOR_SUMMARY_API_KEY   OpenRouter API key (else falls back to $OPENROUTER_API_KEY)
  VECTOR_SUMMARY_MODEL     OpenRouter model id (e.g. openai/gpt-4o-mini)
  VECTOR_SUMMARY_BASE_URL  base url (default https://openrouter.ai/api/v1)
  VECTOR_SUMMARY_MAX_MSGS  max messages per run (default 200)
"""

from __future__ import annotations

import json
import os
import sqlite3
import sys
from datetime import datetime, timezone

from vector_memory.store import MemoryStore

# Force UTF-8 I/O so printing non-ASCII (Cyrillic facts) doesn't crash on Windows.
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

_SYS = (
    "You extract durable, recall-worthy facts from a chat between a user and an "
    "assistant. Return ONLY a JSON array; each item: {\"text\": concise standalone "
    "fact, \"title\": short label, \"kind\": one of preference|decision|project|"
    "person|fact}. Capture lasting things the user would later ask about ('did I "
    "mention...'). Ignore small talk, transient task chatter, and anything already "
    "obvious. Return [] if nothing is worth keeping."
)


_OPENROUTER = "https://openrouter.ai/api/v1"


def _resolve_llm() -> tuple[str, str | None, str | None]:
    """(base_url, api_key, model) for the summary LLM — OpenRouter by default."""
    base_url = os.environ.get("VECTOR_SUMMARY_BASE_URL") or _OPENROUTER
    api_key = os.environ.get("VECTOR_SUMMARY_API_KEY") or os.environ.get("OPENROUTER_API_KEY")
    model = os.environ.get("VECTOR_SUMMARY_MODEL")
    return base_url, api_key, model


def _hwm_path(data_dir: str) -> str:
    return os.path.join(data_dir, "summary_hwm.json")


def _read_hwm(data_dir: str) -> int:
    try:
        with open(_hwm_path(data_dir), "r", encoding="utf-8") as f:
            return int(json.load(f).get("last_id", 0))
    except Exception:
        return 0


def _write_hwm(data_dir: str, last_id: int) -> None:
    try:
        with open(_hwm_path(data_dir), "w", encoding="utf-8") as f:
            json.dump({"last_id": last_id}, f)
    except Exception as exc:  # noqa: BLE001
        print(f"vector-summary: hwm write failed: {exc}", file=sys.stderr)


def _current_max_id(state_db: str) -> int:
    con = sqlite3.connect(f"file:{state_db}?mode=ro", uri=True)
    try:
        row = con.execute("SELECT COALESCE(MAX(id), 0) FROM messages").fetchone()
        return int(row[0] or 0)
    finally:
        con.close()


def _fetch_messages(state_db: str, after_id: int, limit: int) -> list[tuple]:
    con = sqlite3.connect(f"file:{state_db}?mode=ro", uri=True)
    try:
        cur = con.execute(
            "SELECT id, role, content FROM messages "
            "WHERE id > ? AND role IN ('user','assistant') AND content IS NOT NULL AND content != '' "
            "ORDER BY id ASC LIMIT ?",
            (after_id, limit),
        )
        return cur.fetchall()
    finally:
        con.close()


def _call_llm(base_url: str, api_key: str, model: str, transcript: str) -> list[dict]:
    import httpx

    resp = httpx.post(
        base_url.rstrip("/") + "/chat/completions",
        headers={
            "Authorization": f"Bearer {api_key}",
            # Optional OpenRouter attribution (ignored by other OpenAI-compatible APIs)
            "HTTP-Referer": "https://github.com/your-org/hermes-tools",
            "X-Title": "Hermes vector-memory",
        },
        json={
            "model": model,
            "temperature": 0,
            "messages": [
                {"role": "system", "content": _SYS},
                {"role": "user", "content": transcript[:24000]},
            ],
        },
        timeout=120,
    )
    resp.raise_for_status()
    content = resp.json()["choices"][0]["message"]["content"].strip()
    if content.startswith("```"):
        content = content.strip("`")
        content = content[content.find("\n") + 1 :] if "\n" in content else content
    start, end = content.find("["), content.rfind("]")
    if start == -1 or end == -1:
        return []
    try:
        data = json.loads(content[start : end + 1])
        return [d for d in data if isinstance(d, dict) and d.get("text")]
    except Exception:
        return []


def main() -> int:
    data_dir = os.environ.get("VECTOR_MEMORY_DATA_DIR") or os.path.join(
        os.path.expanduser("~"), ".hermes", "vector-memory"
    )
    state_db = os.environ.get("VECTOR_MEMORY_STATE_DB") or os.path.join(
        os.path.expanduser("~"), ".hermes", "state.db"
    )
    max_msgs = int(os.environ.get("VECTOR_SUMMARY_MAX_MSGS", "200"))
    base_url, api_key, model = _resolve_llm()

    if not (api_key and model):
        print(
            "vector-summary: disabled — set VECTOR_SUMMARY_API_KEY (OpenRouter key) "
            "and VECTOR_SUMMARY_MODEL to enable"
        )
        return 0
    if not os.path.exists(state_db):
        print(f"vector-summary: state.db not found at {state_db}")
        return 0

    # First run: anchor the high-water-mark at the latest message and summarize
    # only NEW messages going forward (no backfill of old history — that's the
    # separate, explicit ingest path).
    if not os.path.exists(_hwm_path(data_dir)):
        os.makedirs(data_dir, exist_ok=True)
        latest = _current_max_id(state_db)
        _write_hwm(data_dir, latest)
        print(f"vector-summary: initialized at message {latest} — summarizing only new messages from now on")
        return 0

    after = _read_hwm(data_dir)
    rows = _fetch_messages(state_db, after, max_msgs)
    if not rows:
        print("vector-summary: nothing new")
        return 0

    transcript = "\n".join(f"{role}: {content}" for (_id, role, content) in rows)
    try:
        facts = _call_llm(base_url, api_key, model, transcript)
    except Exception as exc:  # noqa: BLE001
        print(f"vector-summary: LLM call failed: {exc}", file=sys.stderr)
        return 0  # don't advance hwm; retry next tick

    now = datetime.now(timezone.utc).isoformat()
    items = [
        {
            "text": f.get("text", ""),
            "metadata": {
                "kind": f.get("kind", "fact"),
                "title": f.get("title"),
                "source": "summary",
                "created_at": now,
            },
        }
        for f in facts
    ]
    if items:
        os.makedirs(data_dir, exist_ok=True)
        result = MemoryStore(data_dir).add(items)
        print(f"vector-summary: stored {result.get('added', 0)} fact(s) from {len(rows)} messages")
    else:
        print(f"vector-summary: no salient facts in {len(rows)} messages")

    _write_hwm(data_dir, rows[-1][0])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
