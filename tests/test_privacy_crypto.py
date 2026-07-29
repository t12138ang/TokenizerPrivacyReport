from __future__ import annotations

import json
import shutil
import tempfile
import unittest
from collections import Counter
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path
from unittest.mock import patch

import numpy as np

from src.crypto.paillier_aggregation import (
    AggregationServer,
    DataClient,
    DecryptionSelectionServer,
    generate_keys,
    plaintext_aggregate,
)
from src.crypto.benchmark import run_once
from src.privacy.accountant import basic_composition, uniform_round_budget
from src.privacy.clipping import l1_clip_largest_remainder
from src.privacy.discrete_laplace import add_geometric_noise
from src.tokenizer.private_bpe import (
    IncrementalPairCorpus,
    aggregate_and_select,
    all_site_pair_counts,
    public_candidates,
    public_alphabet_base,
    compatible_top_indices,
    tokenizer_from_state,
    train_batched_private_bpe,
)
from src.utils.run_metadata import PROJECT_ROOT


class PrivacyPrimitiveTest(unittest.TestCase):
    def test_l1_clipping_is_bounded_nonnegative_and_deterministic(self) -> None:
        source = [2**70, 9, 7, 5, 3]
        first = l1_clip_largest_remainder(source, 17)
        second = l1_clip_largest_remainder(source, 17)
        self.assertEqual(first.shape, (5,))
        self.assertEqual(sum(map(int, first)), 17)
        self.assertTrue(all(int(value) >= 0 for value in first))
        self.assertEqual(first.tolist(), second.tolist())
        self.assertTrue(all(isinstance(value, int) for value in first.tolist()))

    def test_noise_and_basic_composition_are_explicit(self) -> None:
        rng = np.random.default_rng(20260726)
        noisy, noise = add_geometric_noise(
            np.asarray([4, 3, 2], dtype=object),
            epsilon=0.5,
            sensitivity=10,
            rng=rng,
        )
        self.assertEqual(noisy.shape, (3,))
        self.assertEqual(noise.shape, (3,))
        round_epsilon = uniform_round_budget(4.0, 8)
        account = basic_composition([round_epsilon] * 8)
        self.assertEqual(account["epsilon_total"], 4.0)
        self.assertEqual(account["delta"], 0.0)


class PaillierProtocolTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.keys = generate_keys(1024)

    def test_plain_and_he_aggregate_match_with_negative_noise(self) -> None:
        vectors = [[3, 0, 7], [2, 5, 1], [9, 2, 0]]
        noise = [-4, 3, -2]
        clients = [DataClient(self.keys.public_key) for _ in vectors]
        encrypted = [client.encrypt_vector(vector) for client, vector in zip(clients, vectors)]
        server_a = AggregationServer(self.keys.public_key)
        server_d = DecryptionSelectionServer(self.keys.private_key)
        aggregate = server_a.aggregate(encrypted)
        noisy = server_a.add_encrypted_noise(aggregate, noise)
        decrypted = server_d.decrypt_aggregate(noisy)
        expected = [value + delta for value, delta in zip(plaintext_aggregate(vectors), noise)]
        self.assertEqual(decrypted, expected)
        self.assertEqual(max(abs(left - right) for left, right in zip(decrypted, expected)), 0)

    def test_client_order_does_not_change_plaintext_aggregate(self) -> None:
        vectors = [[1, 2], [3, 4], [5, 6]]
        server_a = AggregationServer(self.keys.public_key)
        server_d = DecryptionSelectionServer(self.keys.private_key)
        client = DataClient(self.keys.public_key)
        forward = server_d.decrypt_aggregate(
            server_a.aggregate([client.encrypt_vector(vector) for vector in vectors])
        )
        reverse = server_d.decrypt_aggregate(
            server_a.aggregate([client.encrypt_vector(vector) for vector in reversed(vectors)])
        )
        self.assertEqual(forward, reverse)
        self.assertEqual(forward, plaintext_aggregate(vectors))

    def test_aggregation_server_has_no_private_key_or_decrypt_method(self) -> None:
        server_a = AggregationServer(self.keys.public_key)
        self.assertFalse(hasattr(server_a, "private_key"))
        self.assertFalse(hasattr(server_a, "decrypt"))

    def test_decryption_server_rejects_duplicate_merge_outputs(self) -> None:
        client = DataClient(self.keys.public_key)
        server_d = DecryptionSelectionServer(self.keys.private_key)
        selected, disclosure = server_d.decrypt_and_select(
            client.encrypt_vector([9, 8, 7]),
            ["first", "duplicate", "third"],
            batch_size=3,
            candidate_pairs=[("a", "bc"), ("ab", "c"), ("x", "y")],
        )
        self.assertEqual(selected, ["first", "third"])
        self.assertEqual(disclosure["returned_merge_id_count"], 2)

    def test_compatible_selection_matches_plain_and_reuses_key(self) -> None:
        vectors = [[5, 4, 3, 2, 1], [2, 2, 8, 9, 10]]
        candidates = [("a", "b"), ("b", "c"), ("d", "e"), ("d", "f"), ("g", "e")]
        expected, _ = aggregate_and_select(
            mode="plain",
            vectors=vectors,
            candidates=candidates,
            batch_size=4,
            clipping_bound=100,
            epsilon_round=None,
            rng=np.random.default_rng(1),
            key_bits=1024,
            real_paillier=False,
        )
        with patch("src.tokenizer.private_bpe.generate_keys") as key_generator:
            actual, metadata = aggregate_and_select(
                mode="he_only",
                vectors=vectors,
                candidates=candidates,
                batch_size=4,
                clipping_bound=100,
                epsilon_round=None,
                rng=np.random.default_rng(1),
                key_bits=1024,
                real_paillier=True,
                paillier_keys=self.keys,
            )
        key_generator.assert_not_called()
        self.assertEqual(actual, expected)
        self.assertTrue(metadata["real_paillier"])
        parallel, parallel_metadata = aggregate_and_select(
            mode="he_only",
            vectors=vectors,
            candidates=candidates,
            batch_size=4,
            clipping_bound=100,
            epsilon_round=None,
            rng=np.random.default_rng(1),
            key_bits=1024,
            real_paillier=True,
            paillier_keys=self.keys,
            paillier_worker_processes=2,
        )
        self.assertEqual(parallel, expected)
        self.assertEqual(parallel_metadata["worker_processes"], 2)
        clear_private, _ = aggregate_and_select(
            mode="sa_dp",
            vectors=vectors,
            candidates=candidates,
            batch_size=4,
            clipping_bound=10,
            epsilon_round=1.0,
            rng=np.random.default_rng(99),
            key_bits=1024,
            real_paillier=False,
        )
        encrypted_private, encrypted_private_metadata = aggregate_and_select(
            mode="sa_dp",
            vectors=vectors,
            candidates=candidates,
            batch_size=4,
            clipping_bound=10,
            epsilon_round=1.0,
            rng=np.random.default_rng(99),
            key_bits=1024,
            real_paillier=True,
            paillier_keys=self.keys,
            paillier_worker_processes=2,
        )
        self.assertEqual(encrypted_private, clear_private)
        self.assertGreaterEqual(encrypted_private_metadata["noise_l1"], 0)

    def test_parallel_benchmark_path_is_exact(self) -> None:
        with ProcessPoolExecutor(max_workers=2) as executor:
            result = run_once(
                self.keys,
                clients=2,
                dimension=4,
                seed=7,
                batch_size=2,
                executor=executor,
                worker_processes=2,
            )
        self.assertTrue(result["equality"])
        self.assertEqual(result["max_absolute_error"], 0)
        self.assertFalse(result["overflow_flag"])
        self.assertEqual(result["returned_merge_id_bytes"], 2)


class PrivateBpeTest(unittest.TestCase):
    def setUp(self) -> None:
        root = PROJECT_ROOT / "tests" / ".tmp"
        root.mkdir(parents=True, exist_ok=True)
        self.temp_dir = Path(tempfile.mkdtemp(prefix="private-bpe-", dir=root))

    def tearDown(self) -> None:
        shutil.rmtree(self.temp_dir)

    def test_public_candidates_and_tiny_sa_dp_training(self) -> None:
        public_texts = {"public": ["public public patterns only"]}
        vocab, merges = public_alphabet_base(public_texts)
        tokenizer = tokenizer_from_state(vocab, merges)
        candidates, metadata = public_candidates(
            tokenizer,
            public_texts,
            vocab,
            32,
        )
        self.assertTrue(candidates)
        self.assertEqual(metadata["candidate_count"], len(candidates))

        corpus = self.temp_dir / "corpus.jsonl"
        records = [
            {"site_id": "target-a", "text": "alpha alpha privacy tokenizer"},
            {"site_id": "target-b", "text": "beta beta privacy tokenizer"},
            {"site_id": "public-a", "text": "alpha beta public tokenizer"},
            {"site_id": "public-b", "text": "privacy public patterns"},
        ]
        with corpus.open("w", encoding="utf-8", newline="\n") as handle:
            for record in records:
                handle.write(json.dumps(record) + "\n")
        manifest = self.temp_dir / "manifest.json"
        payload = {
            "scale": "unit",
            "seed": 20260726,
            "corpus_path": str(corpus.relative_to(PROJECT_ROOT)).replace("\\", "/"),
            "target_training_site_ids": ["target-a", "target-b"],
            "public_candidate_site_ids": ["public-a", "public-b"],
            "manifest_sha256": "unit-manifest",
            "corpus_sha256": "unit-corpus",
        }
        manifest.write_text(json.dumps(payload), encoding="utf-8")
        initial_size = len(vocab)
        result = train_batched_private_bpe(
            manifest_path=manifest,
            output_dir=self.temp_dir / "output",
            mode="sa_dp",
            requested_vocab_size=128,
            candidate_pool_size=32,
            clipping_percentile=90,
            batch_size=1,
            epsilon_total=4.0,
            key_bits=1024,
            real_paillier=False,
        )
        self.assertEqual(result["status"], "success")
        self.assertGreater(result["actual_vocab_size"], initial_size)
        self.assertEqual(result["public_target_overlap_count"], 0)
        self.assertFalse(result["real_paillier"])
        self.assertLessEqual(result["privacy_accountant"]["epsilon_total"], 4.0)
        reused = train_batched_private_bpe(
            manifest_path=manifest,
            output_dir=self.temp_dir / "output",
            mode="sa_dp",
            requested_vocab_size=128,
            candidate_pool_size=32,
            clipping_percentile=90,
            batch_size=1,
            epsilon_total=4.0,
            key_bits=1024,
            real_paillier=False,
        )
        self.assertTrue(reused["checkpoint_reused"])
        with self.assertRaisesRegex(RuntimeError, "mismatched"):
            train_batched_private_bpe(
                manifest_path=manifest,
                output_dir=self.temp_dir / "output",
                mode="sa_dp",
                requested_vocab_size=128,
                candidate_pool_size=16,
                clipping_percentile=90,
                batch_size=1,
                epsilon_total=4.0,
                key_bits=1024,
                real_paillier=False,
            )

        def interrupt_after_first_round(progress: dict[str, object]) -> None:
            if progress["round"] == 1:
                raise RuntimeError("intentional unit-test interruption")

        interrupted_dir = self.temp_dir / "interrupted"
        with self.assertRaisesRegex(RuntimeError, "intentional unit-test interruption"):
            train_batched_private_bpe(
                manifest_path=manifest,
                output_dir=interrupted_dir,
                mode="sa_dp",
                requested_vocab_size=128,
                candidate_pool_size=32,
                clipping_percentile=90,
                batch_size=1,
                epsilon_total=4.0,
                key_bits=1024,
                real_paillier=False,
                progress_callback=interrupt_after_first_round,
                checkpoint_every_rounds=1,
            )
        self.assertEqual(json.loads((interrupted_dir / "training_checkpoint.json").read_text())["completed_rounds"], 1)
        resumed = train_batched_private_bpe(
            manifest_path=manifest,
            output_dir=interrupted_dir,
            mode="sa_dp",
            requested_vocab_size=128,
            candidate_pool_size=32,
            clipping_percentile=90,
            batch_size=1,
            epsilon_total=4.0,
            key_bits=1024,
            real_paillier=False,
            checkpoint_every_rounds=1,
        )
        self.assertEqual(resumed["status"], "success")

    def test_incremental_counts_respect_boundaries_and_match_tokenizer(self) -> None:
        texts = {
            "site-a": ["abab cdab", "ab cd"],
            "site-b": ["cdab abab"],
        }
        vocab, _ = public_alphabet_base(texts)
        base = tokenizer_from_state(vocab, [])
        corpus = IncrementalPairCorpus.from_site_texts(base, texts)
        self.assertNotIn(("b", "c"), corpus.global_pair_counts)
        corpus.apply_compatible_merges([("a", "b"), ("c", "d")])
        merged_vocab = dict(vocab)
        merged_vocab["ab"] = len(merged_vocab)
        merged_vocab["cd"] = len(merged_vocab)
        reference = all_site_pair_counts(
            tokenizer_from_state(merged_vocab, [("a", "b"), ("c", "d")]),
            texts,
        )
        expected = Counter()
        for counts in reference.values():
            expected.update(counts)
        self.assertEqual(corpus.global_pair_counts, expected)
        for pair in expected:
            for site in texts:
                self.assertEqual(corpus.pair_site_counts[pair][site], reference[site][pair])

    def test_batch_compatibility_allows_shared_left_or_right_only(self) -> None:
        candidates = [("a", "b"), ("a", "c"), ("d", "b"), ("b", "e")]
        selected = compatible_top_indices([9, 8, 7, 6], candidates, 4)
        self.assertEqual(selected, [0, 1, 2])

    def test_batch_compatibility_rejects_duplicate_output_symbols(self) -> None:
        candidates = [("a", "bc"), ("ab", "c"), ("x", "y")]
        selected = compatible_top_indices([9, 8, 7], candidates, 3)
        self.assertEqual(selected, [0, 2])


if __name__ == "__main__":
    unittest.main()
