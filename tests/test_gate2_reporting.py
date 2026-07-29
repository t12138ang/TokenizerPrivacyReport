from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from src.generate_gate2_reports import resource_extrapolation


class Gate2ReportingTest(unittest.TestCase):
    def test_shadow_extrapolation_scales_only_declared_components(self) -> None:
        with tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parent) as temporary:
            root = Path(temporary)
            target = root / "target"
            shadow = root / "shadow"
            target.mkdir()
            shadow.mkdir()
            (target / "metadata.json").write_text("m", encoding="utf-8")
            (target / "tokenizer.json").write_text("tt", encoding="utf-8")
            (shadow / "metadata.json").write_text("m", encoding="utf-8")
            (shadow / "tokenizer.json").write_text("ssss", encoding="utf-8")

            profiles = [
                {
                    "role": "target",
                    "elapsed_seconds": "2",
                    "metadata_path": str(target / "metadata.json"),
                },
                {
                    "role": "shadow",
                    "elapsed_seconds": "3",
                    "metadata_path": str(shadow / "metadata.json"),
                },
            ]
            summary = {
                "rows": [
                    {"attack": "compression_rate", "elapsed_seconds": 7},
                    {"attack": "vocabulary_overlap", "elapsed_seconds": 5},
                ],
                "run_state": {
                    "accumulated_elapsed_seconds": 20,
                    "peak_memory_bytes": 100,
                },
            }
            estimate = resource_extrapolation(
                profiles=profiles,
                summary=summary,
                observed_shadow_count=8,
                target_shadow_count=96,
            )

        observed = estimate["observed"]
        projected = estimate["estimated_for_target_shadow_count"]
        self.assertEqual(estimate["linear_scale_factor"], 12)
        self.assertEqual(observed["fixed_pipeline_seconds"], 12)
        self.assertEqual(projected["tokenizer_processing_seconds"], 38)
        self.assertEqual(projected["pipeline_seconds"], 108)
        self.assertEqual(projected["tokenizer_artifact_bytes"], 63)
        self.assertEqual(projected["sequential_peak_memory_reference_bytes"], 100)


if __name__ == "__main__":
    unittest.main()
