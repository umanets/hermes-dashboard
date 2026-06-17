# vector-memory — persistent semantic memory for Hermes

A **bundle** module: enable it from the dashboard and the client's Hermes gains
auto-captured, searchable long-term memory.

## Parts

| Part | File(s) | Role |
|---|---|---|
| Recall MCP | `server.py`, `vector_memory/store.py` | `memory_recall` / `memory_add` / `memory_recent` over a local Chroma store |
| Capture hook | `bundle/hooks/vector-capture/` + `capture_store.py`, `vector_memory/extract.py` | `agent:end` → heuristic artifact capture (links/files/notes), background, **zero LLM** |
| Recall skill | `bundle/skills/memory/vector-recall/SKILL.md` | tells the agent when/how to recall |
| Summary cron | `summarize_job.py` (+ manifest `provides.cron`) | optional LLM fact distillation over new messages (high-water-mark) |

Capture (write) and recall (read) share one on-disk Chroma dir
(`VECTOR_MEMORY_DATA_DIR`, default `<hermes-root>/vector-memory`). The store is an
**index** — it embeds a snippet and keeps a `link` back to the real artifact
(url / file path / `obsidian://` uri).

## Requirements

Local CPU only — no GPU, no DB server, no account. Default embedding is ONNX
MiniLM (downloaded once). ~2 GB RAM is comfortable; RAM scales with #items
(~2 KB/vector). See the manifest for env vars.

## Install (via the dashboard)

The dashboard installs the whole bundle automatically and **cross-platform
(Windows + Ubuntu/macOS)**: it copies the module under `<hermes-root>/modules/`,
creates the venv, runs `pip install -e .`, writes the MCP block + skill + hook +
`.env`, and registers the cron job — no terminal needed.

Manual fallback (only if bootstrapping by hand):

```bash
cd <hermes-root>/modules/vector-memory
python -m venv .venv
.venv/Scripts/python -m pip install -e .   # Windows
.venv/bin/python    -m pip install -e .    # Ubuntu/macOS
```

Then restart the Hermes session.

### Summary cron (LLM fact distillation) — OpenRouter

The optional summary cron uses **OpenRouter** (OpenAI-compatible). To enable it:

- `VECTOR_SUMMARY_API_KEY` — your OpenRouter key (or set `OPENROUTER_API_KEY`)
- `VECTOR_SUMMARY_MODEL` — an OpenRouter model id, default `openai/gpt-4o-mini`
- `VECTOR_SUMMARY_BASE_URL` — defaults to `https://openrouter.ai/api/v1`

Leave the key blank to run **capture-only** (heuristic, zero LLM) — the cron
logs `disabled` and exits.

## Test (offline, stdlib)

```bash
python -m unittest modules/vector-memory/tests/test_extract.py
```
