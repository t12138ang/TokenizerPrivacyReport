from __future__ import annotations

import logging
import time
import unittest
from unittest.mock import patch

from src.data.build_final_manifests import prefix_balanced_probe_assignments
from src.data.stream_final_c4 import pass1


class FinalDataTest(unittest.TestCase):
    def test_shadow_assignments_are_balanced_at_every_required_prefix(self) -> None:
        sites = [f"site_{index:020x}" for index in range(31)]
        assignments = prefix_balanced_probe_assignments(sites, 96, 20260726)
        self.assertEqual(len(assignments), 96)
        for prefix in (8, 16, 32, 64, 96):
            for site in sites:
                self.assertEqual(
                    sum(site in shadow for shadow in assignments[:prefix]),
                    prefix // 2,
                )

    def test_first_pass_builds_disjoint_buffered_candidate_pools(self) -> None:
        samples = []
        for site_index in range(12):
            host = f"site{site_index}.example"
            for document in range(3):
                samples.append(
                    {
                        "url": f"https://{host}/{document}",
                        "text": (
                            f"Natural English content number {document} for independent host {site_index}. "
                            "This paragraph has enough alphabetic characters for deterministic validation."
                        ),
                    }
                )
        config = {
            "seed": 20260726,
            "dataset_revision": "test-revision",
            "scales": {
                "development": {
                    "target_site_count": 2,
                    "shadow_auxiliary_site_count": 1,
                    "public_candidate_site_count": 1,
                    "min_texts_per_site": 2,
                    "max_texts_per_site": 5,
                },
                "main": {
                    "target_site_count": 4,
                    "shadow_auxiliary_site_count": 2,
                    "public_candidate_site_count": 2,
                    "min_texts_per_site": 3,
                    "max_texts_per_site": 5,
                },
            },
            "candidate_buffer_multiplier": 1.0,
            "min_text_chars": 20,
            "max_text_chars": 1000,
            "min_alpha_chars": 10,
            "min_ascii_letter_ratio": 0.6,
            "max_stream_records": 100,
            "progress_every_records": 1000,
            "max_elapsed_seconds": 60,
            "max_peak_memory_bytes": 12 * 1024**3,
            "min_free_disk_bytes": 0,
            "corpus_dir": "data/final/test",
        }
        with patch("src.data.stream_final_c4.make_stream", return_value=iter(samples)):
            result = pass1(
                config,
                logging.getLogger("test.final.data"),
                __import__("pathlib").Path("test.log"),
                time.perf_counter(),
            )
        main = set(result["main_candidates"])
        development = set(result["development_candidates"])
        self.assertEqual(len(main), 8)
        self.assertEqual(len(development), 4)
        self.assertFalse(main & development)
        self.assertEqual(result["scan_limit"], 35)


if __name__ == "__main__":
    unittest.main()
