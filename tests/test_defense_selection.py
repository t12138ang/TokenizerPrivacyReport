from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from src.defenses.select_main import mean_validation_macro


class DefenseSelectionTest(unittest.TestCase):
    def test_selection_uses_validation_not_test_macro_f1(self) -> None:
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as temporary:
            root = Path(temporary)
            for seed, validation, test in ((1, 0.70, 0.99), (2, 0.80, 0.01)):
                directory = root / "candidate" / str(seed)
                directory.mkdir(parents=True)
                (directory / "result.json").write_text(
                    json.dumps({
                        "best_validation_macro_f1": validation,
                        "test": {"macro_f1": test},
                        "test_used_for_model_selection": False,
                    }),
                    encoding="utf-8",
                )
            mean, raw = mean_validation_macro(root, "candidate", [1, 2])
            self.assertEqual(raw, [0.70, 0.80])
            self.assertAlmostEqual(mean, 0.75)

    def test_selection_rejects_ambiguous_test_usage(self) -> None:
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as temporary:
            path = Path(temporary) / "candidate" / "1"
            path.mkdir(parents=True)
            (path / "result.json").write_text(
                json.dumps({"best_validation_macro_f1": 0.7, "test_used_for_model_selection": True}),
                encoding="utf-8",
            )
            with self.assertRaisesRegex(RuntimeError, "test-based selection"):
                mean_validation_macro(Path(temporary), "candidate", [1])


if __name__ == "__main__":
    unittest.main()
