from __future__ import annotations

import json
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class Gate2ConfigTest(unittest.TestCase):
    def test_exact_bounded_attack_matrix(self) -> None:
        config = json.loads((ROOT / "configs/gate2_attack.json").read_text(encoding="utf-8"))
        self.assertEqual(config["protocols"], ["paper_aligned", "strict_disjoint"])
        self.assertEqual(config["seeds"], [20260726, 20260727, 20260728])
        self.assertEqual(config["vocab_sizes"], [2000, 4000, 8000])
        self.assertEqual(config["shadow_count"], 8)
        self.assertEqual([method["min_count_threshold"] for method in config["methods"]], [0, 16, 64])
        self.assertEqual(config["attacks"], ["compression_rate", "vocabulary_overlap"])
        combinations = (
            len(config["protocols"])
            * len(config["seeds"])
            * len(config["vocab_sizes"])
            * len(config["methods"])
            * len(config["attacks"])
        )
        self.assertEqual(combinations, 108)
        self.assertLessEqual(config["max_elapsed_seconds"], 21600)
        self.assertEqual(config["gate2_total_wall_clock_budget_seconds"], 21600)
        self.assertEqual(config["max_peak_memory_bytes"], 12 * 1024**3)

    def test_fixed_dataset_revision_and_site_counts(self) -> None:
        config = json.loads((ROOT / "configs/gate2_data.json").read_text(encoding="utf-8"))
        self.assertRegex(config["dataset_revision"], r"^[0-9a-f]{40}$")
        self.assertEqual(config["target_site_count"], 128)
        self.assertGreaterEqual(config["auxiliary_site_count"], 64)
        self.assertEqual(config["min_texts_per_site"], 5)
        self.assertEqual(config["max_texts_per_site"], 100)
        self.assertLessEqual(config["max_stream_records"], 750000)


if __name__ == "__main__":
    unittest.main()
