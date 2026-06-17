"""Stdlib-only tests for the markdown chunker (no chromadb)."""

import os
import sys
import unittest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from vector_memory.chunk import chunk_markdown, split_frontmatter  # noqa: E402


class TestChunk(unittest.TestCase):
    def test_frontmatter_split(self):
        meta, body = split_frontmatter("---\ntitle: My Note\ntags: a, b\n---\n# Body\ntext")
        self.assertEqual(meta["title"], "My Note")
        self.assertEqual(meta["tags"], "a, b")
        self.assertTrue(body.strip().startswith("# Body"))

    def test_no_frontmatter(self):
        meta, body = split_frontmatter("# Just markdown\nhi")
        self.assertEqual(meta, {})
        self.assertIn("Just markdown", body)

    def test_small_note_one_chunk(self):
        self.assertEqual(len(chunk_markdown("# Title\nshort body")), 1)

    def test_packs_small_sections_then_splits(self):
        text = "# A\n" + "x" * 100 + "\n# B\n" + "y" * 100
        # small sections pack together under a generous limit…
        self.assertEqual(len(chunk_markdown(text, max_chars=300)), 1)
        # …but split when they don't fit
        self.assertGreaterEqual(len(chunk_markdown(text, max_chars=150)), 2)

    def test_large_block_hard_wrapped(self):
        chunks = chunk_markdown("para " * 1000, max_chars=800)
        self.assertTrue(all(len(c) <= 800 for c in chunks))
        self.assertGreater(len(chunks), 1)

    def test_empty(self):
        self.assertEqual(chunk_markdown("   "), [])


if __name__ == "__main__":
    unittest.main()
