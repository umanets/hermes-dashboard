"""Stdlib-only tests for the heuristic extractor (no chromadb needed).

Run: python -m unittest discover -s modules/vector-memory/tests
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from vector_memory.extract import extract_artifacts  # noqa: E402


class TestExtract(unittest.TestCase):
    def test_plain_url(self):
        items = extract_artifacts(user_text="check this https://example.com/great-article please")
        links = [i["metadata"].get("link") for i in items]
        self.assertIn("https://example.com/great-article", links)
        self.assertEqual(items[0]["metadata"]["source"], "web")

    def test_markdown_link_keeps_title(self):
        items = extract_artifacts(assistant_text="see [The Pragmatic Programmer](https://book.example/pp)")
        m = next(i for i in items if i["metadata"].get("link") == "https://book.example/pp")
        self.assertEqual(m["metadata"]["title"], "The Pragmatic Programmer")
        self.assertTrue(m["text"].startswith("The Pragmatic Programmer"))

    def test_obsidian_link(self):
        items = extract_artifacts(user_text="my note obsidian://open?vault=Main&file=Ideas%2FX")
        m = next(i for i in items if i["metadata"]["source"] == "obsidian")
        self.assertEqual(m["metadata"]["kind"], "note")

    def test_windows_file_path(self):
        items = extract_artifacts(user_text=r"the report is at D:\work\notes\report.md ok")
        m = next(i for i in items if i["metadata"]["kind"] == "file")
        self.assertEqual(m["metadata"]["link"], r"D:\work\notes\report.md")
        self.assertEqual(m["metadata"]["title"], "report.md")

    def test_explicit_remember(self):
        items = extract_artifacts(user_text="запомни: я предпочитаю тёмную тему и Vim")
        notes = [i for i in items if i["metadata"]["kind"] == "note" and i["metadata"]["source"] == "chat"]
        self.assertTrue(notes)

    def test_nothing_to_capture(self):
        self.assertEqual(extract_artifacts(user_text="ok thanks", assistant_text="you're welcome"), [])

    def test_dedup_same_link(self):
        items = extract_artifacts(
            user_text="https://x.io/a",
            assistant_text="as you said, https://x.io/a is good",
        )
        links = [i["metadata"].get("link") for i in items]
        self.assertEqual(links.count("https://x.io/a"), 1)


if __name__ == "__main__":
    unittest.main()
