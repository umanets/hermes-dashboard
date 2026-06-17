---
name: vector-recall
description: "Persistent VECTOR memory — store and recall things by topic. Use whenever the user asks to remember/save something (\"remember…\", \"save this\", \"запомни…\", \"запиши…\", \"не забудь…\") OR refers to something from before that is NOT in the current context (\"did I mention…\", \"we talked about…\", \"what was that article/file/note about…\", \"напомни что я говорил про…\"). This is the searchable long-term archive — SEPARATE from Hermes' small always-in-context built-in profile. On an explicit save request ALWAYS call memory_add here so it's recallable later by topic, not only the built-in memory."
metadata:
  hermes:
    tags: [memory, vector, recall, remember, semantic-search, obsidian]
    related_skills: [obsidian]
---

# Vector memory (persistent, searchable)

This install has a **vector memory** (the `vector-memory` MCP) that auto-captures
artifacts and stores anything the user explicitly asks to remember, and lets you
recall it by topic later. It is **separate from Hermes' built-in memory**: the
built-in profile is a tiny, always-in-context digest (capped, ~2 KB); this is an
unbounded, **searchable archive** queried on demand.

Tools:
- `memory_add(text, title?, link?, kind?, tags?)` — store something now.
- `memory_recall(query, k=6, kind?)` — semantic search; returns snippets + a
  `metadata.link` back to the source + a relevance score.
- `memory_recent(limit)` — list the latest items.

## Capture on explicit request  ← important

When the user asks to remember / save / note something ("remember…", "save this",
"запомни…", "запиши…", "не забудь, что…"), **ALWAYS call `memory_add`** with a
concise standalone statement — even if you also update the built-in profile.
Built-in memory alone is a small capped digest and will **not** be searchable by
topic later; the vector store is what makes "did I mention X weeks ago" work.

- `text`: the fact/note, self-contained (so it stands on its own out of context).
- `title`: a short label; `link`: a url / file path / `obsidian://` uri if there's
  an artifact behind it.
- `kind`: one of `preference | decision | project | person | note | fact | article | file`.

Then confirm briefly that it's saved to long-term (vector) memory.

**Routing — built-in vs vector:**
- **Vector (`memory_add`)** — anything the user wants to *recall later by topic*:
  facts, notes, links, articles, files, decisions, "remember that…". Default here
  on any explicit save request.
- **Built-in profile** — core, always-relevant identity/preferences that must be
  in context every session (Hermes manages this itself).
- When unsure, prefer vector `memory_add` — it's searchable and unbounded.

## Recall

Trigger on references to the past that aren't already in context ("did I mention…",
"напомни что я говорил про…", "что за статью я кидал про…").

1. `memory_recall(query=<the topic, in the user's words>, k=6)`; add `kind=` to
   narrow when the user is specific ("that *article*…").
2. If results are thin, recall again with reworded / broader queries before
   concluding nothing is stored.
3. Use the returned `metadata.link` to open the **real source** (Obsidian note /
   file / url) for full content — the store holds a snippet + link, not the full
   artifact.
4. Bring the recalled material into the session, answer / act, and cite the link.

## Don't
- Don't rely ONLY on built-in memory for a user-requested "remember" — also store
  it in vector memory.
- Don't use recall for facts already visible in the current conversation or the
  built-in profile.
- Don't dump raw matches at the user; open the linked source and synthesize.
