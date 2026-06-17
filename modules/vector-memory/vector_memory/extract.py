"""Zero-LLM heuristic artifact extraction from a single turn.

Pure stdlib so it can run anywhere (including inside the Hermes process if ever
needed). Finds the things worth remembering automatically: links, markdown
links, Obsidian notes, file paths, and explicit "remember this" cues. Each
artifact becomes an embeddable snippet + scalar metadata (with a link back to
the real source — the vector store is an INDEX, not a copy).
"""

from __future__ import annotations

import re

_URL = re.compile(r"https?://[^\s)>\]\"']+")
_MD_LINK = re.compile(r"\[([^\]]{1,120})\]\((https?://[^)\s]+|obsidian://[^)\s]+)\)")
_OBSIDIAN = re.compile(r"obsidian://[^\s)>\]\"']+")
_WIN_PATH = re.compile(r"[A-Za-z]:[\\/][^\s\"'<>|]+\.[A-Za-z0-9]{1,8}")
_REMEMBER = re.compile(
    r"\b(remember|запомни|запиши|save this|note that|keep in mind|для протокола|на будущее)\b",
    re.IGNORECASE,
)
_MAX_ITEMS = 12
_SNIPPET = 600


def _line_around(text: str, idx: int) -> str:
    start = text.rfind("\n", 0, idx) + 1
    end = text.find("\n", idx)
    if end == -1:
        end = len(text)
    return text[start:end].strip()


def _snippet(text: str, idx: int) -> str:
    line = _line_around(text, idx)
    if len(line) >= 40:
        return line[:_SNIPPET]
    # short line: widen to a character window around the hit
    lo = max(0, idx - _SNIPPET // 2)
    return text[lo : lo + _SNIPPET].strip()


def _kind_for(link: str) -> str:
    if link.startswith("obsidian://"):
        return "note"
    return "article"


def extract_artifacts(user_text: str = "", assistant_text: str = "") -> list[dict]:
    """Return a list of {text, metadata:{kind, link?, title?, source}}.

    `source` marks where it came from: web (url), obsidian, fs (file path),
    chat (explicit remember). created_at / session_id are added by the caller.
    """
    items: list[dict] = []
    seen: set[str] = set()

    def push(text: str, kind: str, source: str, link: str | None = None, title: str | None = None):
        key = link or text[:120]
        if not text.strip() or key in seen:
            return
        seen.add(key)
        md = {"kind": kind, "source": source}
        if link:
            md["link"] = link
        if title:
            md["title"] = title
        items.append({"text": text.strip(), "metadata": md})

    for source_text, origin in ((user_text or ""), "user"), ((assistant_text or ""), "assistant"):
        # markdown links first (they carry a title)
        for m in _MD_LINK.finditer(source_text):
            title, link = m.group(1).strip(), m.group(2).strip()
            src = "obsidian" if link.startswith("obsidian://") else "web"
            push(f"{title}\n\n{_snippet(source_text, m.start())}", _kind_for(link), src, link, title)

        for m in _OBSIDIAN.finditer(source_text):
            link = m.group(0)
            push(_snippet(source_text, m.start()), "note", "obsidian", link)

        for m in _URL.finditer(source_text):
            link = m.group(0).rstrip(".,);")
            if link.startswith("obsidian://"):
                continue
            push(_snippet(source_text, m.start()), "article", "web", link)

        for m in _WIN_PATH.finditer(source_text):
            link = m.group(0)
            push(_snippet(source_text, m.start()), "file", "fs", link, link.replace("\\", "/").split("/")[-1])

    # explicit "remember this" — capture the user's own statement
    if _REMEMBER.search(user_text or ""):
        push((user_text or "").strip()[: _SNIPPET * 2], "note", "chat")

    return items[:_MAX_ITEMS]
