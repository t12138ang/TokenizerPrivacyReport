from __future__ import annotations

import importlib.util
import json
import tempfile
import unittest
from pathlib import Path

from src.utils.run_metadata import sha256_file, write_json_exclusive


@unittest.skipUnless(importlib.util.find_spec("tokenizers"), "tokenizers dependency is not installed")
class TokenizerBaselineTest(unittest.TestCase):
    def test_base_and_derived_reuse_validate_their_own_parameters(self) -> None:
        from src.tokenizer.common import materialize_tokenizer_artifact, train_base_tokenizer_artifact

        with tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parent) as temporary:
            root = Path(temporary)
            corpus = root / "texts.jsonl"
            corpus.write_text('{"site_id":"site_a","text":"alpha beta"}\n', encoding="utf-8")
            manifest = root / "manifest.json"
            write_json_exclusive(manifest, {
                "manifest_sha256": "manifest-test-hash",
                "corpus_sha256": sha256_file(corpus),
            })
            base_dir = root / "base"
            base_dir.mkdir()
            base_artifact = base_dir / "tokenizer.json"
            base_artifact.write_text("{}\n", encoding="utf-8")
            write_json_exclusive(base_dir / "metadata.json", {
                "status": "success",
                "artifact_sha256": sha256_file(base_artifact),
                "manifest_sha256": "manifest-test-hash",
                "corpus_sha256": sha256_file(corpus),
                "protocol": "strict_disjoint",
                "seed": 7,
                "role": "target",
                "shadow_id": None,
                "training_site_count": 1,
                "requested_vocab_size": 32,
                "tokenizers_threads": 1,
            })
            reused_base = train_base_tokenizer_artifact(
                corpus_path=corpus,
                training_site_ids=["site_a"],
                manifest_path=manifest,
                protocol="strict_disjoint",
                seed=7,
                max_vocab_size=32,
                role="target",
                shadow_id=None,
                output_dir=base_dir,
                tokenizers_threads=1,
            )
            self.assertTrue(reused_base["checkpoint_reused"])

            derived_dir = root / "derived"
            derived_dir.mkdir()
            derived_artifact = derived_dir / "tokenizer.json"
            derived_artifact.write_text("{}\n", encoding="utf-8")
            write_json_exclusive(derived_dir / "metadata.json", {
                "status": "success",
                "artifact_sha256": sha256_file(derived_artifact),
                "manifest_sha256": "manifest-test-hash",
                "method_id": "plain_bpe",
                "defense": "plain_bpe",
                "min_count_threshold": 0,
                "requested_vocab_size": 16,
                "base_artifact_sha256": sha256_file(base_artifact),
            })
            reused_derived = materialize_tokenizer_artifact(
                base_artifact=base_artifact,
                base_metadata_path=base_dir / "metadata.json",
                corpus_path=corpus,
                training_site_ids=["site_a"],
                manifest_path=manifest,
                vocab_size=16,
                method={"id": "plain_bpe", "defense": "plain_bpe", "min_count_threshold": 0},
                output_dir=derived_dir,
            )
            self.assertTrue(reused_derived["checkpoint_reused"])
            with self.assertRaisesRegex(RuntimeError, "mismatched derived"):
                materialize_tokenizer_artifact(
                    base_artifact=base_artifact,
                    base_metadata_path=base_dir / "metadata.json",
                    corpus_path=corpus,
                    training_site_ids=["site_a"],
                    manifest_path=manifest,
                    vocab_size=16,
                    method={"id": "other", "defense": "plain_bpe", "min_count_threshold": 0},
                    output_dir=derived_dir,
                )

    def test_truncation_and_min_count_are_loadable(self) -> None:
        from tokenizers import Tokenizer
        from tokenizers.models import BPE
        from tokenizers.pre_tokenizers import Whitespace
        from tokenizers.trainers import BpeTrainer

        from src.tokenizer.common import apply_posthoc_min_count, truncate_tokenizer

        with tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parent) as temporary:
            root = Path(temporary)
            corpus = root / "texts.jsonl"
            records = [
                {"site_id": "site_a", "text": "common common common alpha alpha rareone"},
                {"site_id": "site_a", "text": "common common beta beta raretwo"},
            ]
            corpus.write_text(
                "".join(json.dumps(record) + "\n" for record in records),
                encoding="utf-8",
            )
            base = Tokenizer(BPE(unk_token="[UNK]"))
            base.pre_tokenizer = Whitespace()
            base.train_from_iterator(
                (record["text"] for record in records),
                BpeTrainer(vocab_size=40, min_frequency=1, special_tokens=["[UNK]"], show_progress=False),
            )
            base_path = root / "base.json"
            base.save(str(base_path))
            requested = max(10, min(20, base.get_vocab_size()))
            truncated = truncate_tokenizer(base_path, requested)
            self.assertLessEqual(truncated.get_vocab_size(), requested)
            filtered, stats = apply_posthoc_min_count(
                truncated,
                corpus_path=corpus,
                site_set={"site_a"},
                threshold=3,
            )
            filtered_path = root / "filtered.json"
            filtered.save(str(filtered_path))
            reloaded = Tokenizer.from_file(str(filtered_path))
            self.assertTrue(reloaded.encode("common alpha").ids)
            self.assertLessEqual(reloaded.get_vocab_size(), truncated.get_vocab_size())
            self.assertGreater(stats["observed_token_occurrences"], 0)


if __name__ == "__main__":
    unittest.main()
