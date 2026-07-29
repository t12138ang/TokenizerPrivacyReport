import json
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def load_strict_json(path: Path) -> dict:
    def reject_constant(value: str) -> None:
        raise ValueError(f"non-standard JSON constant: {value}")

    return json.loads(path.read_text(encoding="utf-8"), parse_constant=reject_constant)


class SmokeResultSchemaTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.metrics_path = PROJECT_ROOT / "results" / "smoke" / "metrics.json"
        if not cls.metrics_path.is_file():
            raise AssertionError("run scripts/run_smoke.ps1 first")
        cls.metrics = load_strict_json(cls.metrics_path)

    def test_success_result_has_required_fields(self) -> None:
        metrics = self.metrics
        self.assertEqual(metrics["status"], "success")
        self.assertEqual(metrics["schema_version"], 1)
        self.assertEqual(metrics["phase"], "smoke_test")
        self.assertEqual(metrics["parameters"]["seed"], 20260726)
        self.assertEqual(metrics["data"]["dataset_count"], 8)
        self.assertEqual(metrics["data"]["document_count"], 96)
        self.assertEqual(metrics["data"]["member_dataset_count"], 4)
        self.assertEqual(metrics["data"]["non_member_dataset_count"], 4)
        self.assertEqual(metrics["tokenizers"]["target"]["count"], 1)
        self.assertEqual(metrics["tokenizers"]["shadow"]["count"], 1)
        self.assertEqual(metrics["tokenizers"]["target"]["actual_vocab_size"], 256)
        self.assertEqual(metrics["attack"]["method"], "compression_rate")
        self.assertGreaterEqual(metrics["attack"]["score"], 0.0)
        self.assertLessEqual(metrics["attack"]["score"], 1.0)
        self.assertGreater(metrics["performance"]["peak_memory_bytes"], 0)
        self.assertFalse(metrics["performance"]["gpu_used"])
        self.assertEqual(metrics["logging"]["error_count"], 0)
        self.assertIn("python_version", metrics["environment"])
        self.assertIn("packages", metrics["environment"])

    def test_membership_labels_and_score_direction_are_explicit(self) -> None:
        attack = self.metrics["attack"]
        details = attack["details"]
        members = [row for row in details if row["is_member"]]
        non_members = [row for row in details if not row["is_member"]]
        self.assertEqual(len(members), 4)
        self.assertEqual(len(non_members), 4)
        self.assertIn("bytes per target-tokenizer token", attack["definition"])
        self.assertGreater(
            min(row["membership_signal"] for row in members),
            max(row["membership_signal"] for row in non_members),
        )

    def test_roc_arrays_are_finite_or_json_null(self) -> None:
        attack = self.metrics["attack"]
        self.assertEqual(len(attack["fpr"]), len(attack["tpr"]))
        self.assertEqual(len(attack["thresholds"]), len(attack["fpr"]))
        for value in attack["fpr"] + attack["tpr"]:
            self.assertIsInstance(value, (int, float))
        self.assertIsNone(attack["thresholds"][0])


if __name__ == "__main__":
    unittest.main()
