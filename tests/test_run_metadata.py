from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from src.utils.run_metadata import (
    strict_json_load,
    write_json_atomic_replace,
    write_json_exclusive,
    write_text_exclusive,
)


class ExclusiveOutputTest(unittest.TestCase):
    def test_json_publish_is_complete_and_refuses_overwrite(self) -> None:
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as temporary:
            output = Path(temporary) / "result.json"
            write_json_exclusive(output, {"status": "success", "value": 7})
            self.assertEqual(strict_json_load(output)["value"], 7)
            self.assertFalse(output.with_suffix(".json.partial").exists())
            with self.assertRaises(FileExistsError):
                write_json_exclusive(output, {"status": "replacement"})
            self.assertEqual(strict_json_load(output)["value"], 7)

    def test_existing_partial_is_preserved_for_audit(self) -> None:
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as temporary:
            output = Path(temporary) / "report.txt"
            partial = output.with_suffix(".txt.partial")
            partial.write_text("interrupted\n", encoding="utf-8")
            with self.assertRaises(FileExistsError):
                write_text_exclusive(output, "new")
            self.assertEqual(partial.read_text(encoding="utf-8"), "interrupted\n")
            self.assertFalse(output.exists())

    def test_mutable_checkpoint_replaces_only_complete_files(self) -> None:
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as temporary:
            output = Path(temporary) / "state.json"
            write_json_atomic_replace(output, {"generation": 1})
            write_json_atomic_replace(output, {"generation": 2})
            self.assertEqual(strict_json_load(output), {"generation": 2})
            partial = output.with_suffix(".json.partial")
            partial.write_text("crash residue\n", encoding="utf-8")
            with self.assertRaises(FileExistsError):
                write_json_atomic_replace(output, {"generation": 3})
            self.assertEqual(strict_json_load(output), {"generation": 2})
            self.assertEqual(partial.read_text(encoding="utf-8"), "crash residue\n")


if __name__ == "__main__":
    unittest.main()
