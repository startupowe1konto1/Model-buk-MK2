import json
import os
import tempfile
import unittest
from io import StringIO
from unittest.mock import patch

from backtest.report_io import write_or_print


class TestWriteOrPrint(unittest.TestCase):
    def test_prints_text_when_no_out_prefix(self):
        with patch("sys.stdout", new_callable=StringIO) as out:
            write_or_print("hello\n", {"a": 1}, None)
        self.assertIn("hello", out.getvalue())

    def test_writes_md_and_json_when_out_prefix_given(self):
        with tempfile.TemporaryDirectory() as tmp:
            prefix = os.path.join(tmp, "sub", "report")
            write_or_print("# tytul\n", {"n": 3}, prefix)
            with open(prefix + ".md", encoding="utf-8") as fh:
                self.assertEqual(fh.read(), "# tytul\n")
            with open(prefix + ".json", encoding="utf-8") as fh:
                self.assertEqual(json.load(fh), {"n": 3})

    def test_creates_missing_output_directory(self):
        with tempfile.TemporaryDirectory() as tmp:
            prefix = os.path.join(tmp, "nested", "dir", "report")
            self.assertFalse(os.path.isdir(os.path.dirname(prefix)))
            write_or_print("x\n", {}, prefix)
            self.assertTrue(os.path.isfile(prefix + ".md"))
            self.assertTrue(os.path.isfile(prefix + ".json"))

    def test_json_written_with_non_ascii_kept_readable(self):
        """ensure_ascii=False - polskie znaki maja zostac w pliku jako
        znaki, nie jako \\uXXXX. Traci sie to latwo przy nieuwaznym refaktorze
        (domyslne json.dump ma ensure_ascii=True)."""
        with tempfile.TemporaryDirectory() as tmp:
            prefix = os.path.join(tmp, "report")
            write_or_print("x\n", {"opis": "błąd kalibracji róznic"}, prefix)
            with open(prefix + ".json", "rb") as fh:
                raw = fh.read()
            self.assertIn("błąd kalibracji róznic".encode("utf-8"), raw)
            self.assertNotIn(b"\\u", raw)


if __name__ == "__main__":
    unittest.main()
