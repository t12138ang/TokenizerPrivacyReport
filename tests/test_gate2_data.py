import tempfile
import time
import unittest
from logging import getLogger
from pathlib import Path
from unittest.mock import patch

from src.data.build_pilot_manifest import build_manifest
from src.data.stream_c4_websites import (
    contains_source_marker_or_url,
    normalize_host,
    normalize_text,
    site_id_from_host,
    validate_text,
    second_pass,
)
from src.utils.run_metadata import PROJECT_ROOT, canonical_sha256


class Gate2DataUnitTest(unittest.TestCase):
    def test_host_normalization_and_hash_id(self) -> None:
        host = normalize_host("HTTPS://WWW.Example.COM:443/a?q=1")
        self.assertEqual(host, "example.com")
        site_id = site_id_from_host(host)
        self.assertRegex(site_id, r"^site_[0-9a-f]{20}$")
        self.assertNotIn("example", site_id)
        self.assertIsNone(normalize_host("not-a-url"))

    def test_text_validation_does_not_modify_content(self) -> None:
        config = {
            "min_text_chars": 20,
            "max_text_chars": 1000,
            "min_alpha_chars": 10,
            "min_ascii_letter_ratio": 0.6,
        }
        text = "A natural English paragraph with enough alphabetic content for validation."
        cleaned, reason, stats = validate_text(text, config)
        self.assertIsNone(reason)
        self.assertEqual(cleaned, text)
        self.assertEqual(stats["byte_count"], len(text.encode("utf-8")))
        self.assertEqual(normalize_text("  Hello\tWORLD  "), "hello world")
        self.assertTrue(contains_source_marker_or_url("Read https://other.example/a", "example.com"))
        self.assertTrue(contains_source_marker_or_url("Copyright Example.COM", "example.com"))
        self.assertFalse(contains_source_marker_or_url(text, "example.com"))

    def test_protocol_manifests_enforce_boundaries_and_shadow_coverage(self) -> None:
        sites = [f"site_{index:020x}" for index in range(192)]
        corpus_index = {
            "dataset_id": "allenai/c4",
            "dataset_revision": "revision",
            "corpus_sha256": "a" * 64,
            "site_counts": {site: 100 for site in sites},
        }
        with tempfile.TemporaryDirectory(dir=PROJECT_ROOT / "tests") as temporary:
            index_path = Path(temporary) / "site_index.json"
            index_path.write_text("{}\n", encoding="utf-8")
            paper = build_manifest(
                protocol="paper_aligned",
                seed=20260726,
                target_sites=sites[:128],
                extra_auxiliary_sites=sites[128:],
                corpus_index=corpus_index,
                corpus_index_path=index_path,
            )
            strict = build_manifest(
                protocol="strict_disjoint",
                seed=20260726,
                target_sites=sites[:128],
                extra_auxiliary_sites=sites[128:],
                corpus_index=corpus_index,
                corpus_index_path=index_path,
            )

        self.assertEqual(len(paper["target_member_site_ids"]), 64)
        self.assertEqual(len(paper["target_nonmember_site_ids"]), 64)
        self.assertEqual(len(paper["target_auxiliary_overlap_site_ids"]), 128)
        self.assertEqual(strict["target_auxiliary_overlap_site_ids"], [])
        self.assertEqual(len(strict["auxiliary_pool_site_ids"]), 64)
        for manifest in (paper, strict):
            stored_hash = manifest.pop("manifest_sha256")
            self.assertEqual(stored_hash, canonical_sha256(manifest))
            targets = manifest["target_pool_site_ids"]
            for site in targets:
                in_count = sum(site in plan["training_site_ids"] for plan in manifest["shadow_plans"])
                self.assertGreater(in_count, 0)
                self.assertLess(in_count, 8)

    def test_second_pass_requires_minimum_not_cap(self) -> None:
        samples = [
            {"url": "https://alpha.example/a", "text": "A" * 120},
            {"url": "https://beta.example/b", "text": "B" * 120},
        ]
        sites = [site_id_from_host("alpha.example"), site_id_from_host("beta.example")]
        config = {
            "min_text_chars": 20,
            "max_text_chars": 1000,
            "min_alpha_chars": 10,
            "min_ascii_letter_ratio": 0.6,
            "min_texts_per_site": 1,
            "max_texts_per_site": 3,
            "progress_every_records": 100,
            "max_elapsed_seconds": 60,
            "max_peak_memory_bytes": 12 * 1024**3,
            "seed": 1,
        }
        with tempfile.TemporaryDirectory(dir=PROJECT_ROOT / "tests") as temporary:
            output = Path(temporary) / "texts.jsonl.partial"
            with patch("src.data.stream_c4_websites.make_stream", return_value=iter(samples)):
                result = second_pass(config, sites, len(samples), output, getLogger("test"), time.perf_counter())
        self.assertEqual(result["site_counts"], {site: 1 for site in sorted(sites)})


if __name__ == "__main__":
    unittest.main()
