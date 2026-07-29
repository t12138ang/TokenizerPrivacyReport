from __future__ import annotations

import math
import json
import tempfile
import unittest
from collections import Counter
from pathlib import Path

from src.attacks.frequency_estimation import fit_zipf_exponent, frequency_estimation_scores
from src.attacks.token_statistics import (
    SubstringVocabularyMatcher,
    aggregate_substring_token_counts,
    site_token_counts,
)
from tokenizers import Tokenizer
from tokenizers.models import BPE


class FrequencyEstimationTest(unittest.TestCase):
    def test_substring_matcher_reports_overlaps_once(self) -> None:
        matcher = SubstringVocabularyMatcher({"a", "an", "ana", "na", "!", "x y"})
        self.assertEqual(matcher.unique_matches("banana"), {"a", "an", "ana", "na"})
        self.assertEqual(matcher.unique_matches("!!"), {"!"})
        self.assertEqual(matcher.unique_matches("none"), set())

    def test_site_counts_match_official_word_substring_semantics(self) -> None:
        tokenizer = Tokenizer(BPE(vocab={"[UNK]": 0, "a": 1, "an": 2, "ana": 3, "na": 4, "!": 5}, merges=[], unk_token="[UNK]"))
        with tempfile.TemporaryDirectory() as temporary:
            corpus = Path(temporary) / "corpus.jsonl"
            corpus.write_text(
                json.dumps({"site_id": "site-a", "text": "banana banana!!"}) + "\n"
                + json.dumps({"site_id": "site-b", "text": "unselected"}) + "\n",
                encoding="utf-8",
            )
            counts = site_token_counts(tokenizer, corpus, {"site-a"})
            self.assertEqual(counts["site-a"]["a"], 2)
            self.assertEqual(counts["site-a"]["an"], 2)
            self.assertEqual(counts["site-a"]["ana"], 2)
            self.assertEqual(counts["site-a"]["na"], 2)
            self.assertEqual(counts["site-a"]["!"], 1)
            aggregate = aggregate_substring_token_counts(tokenizer, corpus, {"site-a"})
            self.assertEqual(aggregate, counts["site-a"])

    def test_weighted_discrete_fit_is_finite_and_deterministic(self) -> None:
        vocab = {f"token-{index}": index for index in range(1, 1001)}
        counts = Counter({
            token: max(1, int(2_000_000 / ((rank + 1) ** 2.0)))
            for token, rank in vocab.items()
        })
        first = fit_zipf_exponent(vocab, counts)
        second = fit_zipf_exponent(vocab, counts)
        self.assertEqual(first, second)
        self.assertEqual(first["fit_method"], "weighted_discrete_power_law_mle_ks")
        self.assertTrue(1.0 < float(first["alpha"]) < 4.0)
        self.assertGreaterEqual(int(first["xmin"]), 1)
        self.assertTrue(math.isfinite(float(first["ks_distance"])))

    def test_frequency_scores_preserve_fixed_higher_member_direction(self) -> None:
        vocab = {"[UNK]": 0, "common": 1, "rare-member": 20}
        site_counts = {
            "member-like": Counter({"rare-member": 8, "common": 2}),
            "nonmember-like": Counter({"rare-member": 1, "common": 9}),
        }
        scores = frequency_estimation_scores(
            target_vocab=vocab,
            site_counts=site_counts,
            background_counts=Counter({"rare-member": 10, "common": 100}),
            involved_sites={"member-like"},
            zipf_alpha=2.0,
            xmin=1,
        )
        self.assertGreater(scores["member-like"], scores["nonmember-like"])
        self.assertTrue(all(math.isfinite(value) for value in scores.values()))


if __name__ == "__main__":
    unittest.main()
