import unittest
from collections import Counter

import numpy as np

from src.attacks.metrics import SCORE_DIRECTION, _fast_scalar_metrics, compute_attack_metrics, point_metrics
from src.attacks.frequency_estimation import frequency_estimation_scores
from src.attacks.merge_similarity import merge_similarity_scores
from src.attacks.naive_bayes import naive_bayes_scores


class AttackDirectionTest(unittest.TestCase):
    def test_higher_member_scores_match_positive_class(self) -> None:
        labels = np.asarray([1, 1, 1, 0, 0, 0])
        scores = np.asarray([0.95, 0.85, 0.75, 0.25, 0.15, 0.05])
        self.assertEqual(SCORE_DIRECTION, "higher_is_more_member")
        self.assertEqual(point_metrics(labels, scores)["roc_auc"], 1.0)

    def test_negating_scores_exposes_direction_reversal(self) -> None:
        labels = np.asarray([1, 1, 1, 0, 0, 0])
        scores = np.asarray([0.95, 0.85, 0.75, 0.25, 0.15, 0.05])
        correct_auc = point_metrics(labels, scores)["roc_auc"]
        reversed_auc = point_metrics(labels, -scores)["roc_auc"]
        self.assertGreater(correct_auc, 0.5)
        self.assertLess(reversed_auc, 0.5)
        self.assertAlmostEqual(correct_auc + reversed_auc, 1.0)

    def test_perfect_random_constant_and_imbalanced_cases(self) -> None:
        perfect = compute_attack_metrics(
            [0, 0, 1, 1], [0.1, 0.2, 0.8, 0.9], seed=1,
            bootstrap_iterations=20, bootstrap_confidence=0.95,
        )
        self.assertEqual(perfect["roc_auc"], 1.0)
        self.assertEqual(perfect["average_precision"], 1.0)
        constant = compute_attack_metrics(
            [0, 0, 1, 1], [0.5, 0.5, 0.5, 0.5], seed=2,
            bootstrap_iterations=20, bootstrap_confidence=0.95,
        )
        self.assertEqual(constant["roc_auc"], 0.5)
        random_like = compute_attack_metrics(
            [0, 1, 0, 1], [0.1, 0.2, 0.3, 0.4], seed=3,
            bootstrap_iterations=20, bootstrap_confidence=0.95,
        )
        self.assertAlmostEqual(random_like["roc_auc"], 0.75)
        imbalanced = compute_attack_metrics(
            [0, 0, 0, 0, 1], [0.1, 0.2, 0.3, 0.4, 0.9], seed=4,
            bootstrap_iterations=20, bootstrap_confidence=0.95,
        )
        self.assertEqual(imbalanced["roc_auc"], 1.0)
        self.assertIn("tpr_at_fpr_le_0_001", imbalanced)
        self.assertEqual(len(imbalanced["precision"]), len(imbalanced["recall"]))

    def test_shadow_attack_primitives_have_fixed_member_direction(self) -> None:
        target_vocab = {"common": 5, "rare": 9, "other": 10}
        site_counts = {
            "member": Counter({"rare": 10, "common": 1}),
            "nonmember": Counter({"rare": 1, "common": 10}),
        }
        background = Counter({"rare": 10, "common": 100, "other": 2})
        involved = {"member", "nonmember"}
        nb = naive_bayes_scores(
            target_vocab=target_vocab,
            site_counts=site_counts,
            background_counts=background,
            involved_sites=involved,
            top_k=3,
        )
        frequency = frequency_estimation_scores(
            target_vocab=target_vocab,
            site_counts=site_counts,
            background_counts=background,
            involved_sites=involved,
            zipf_alpha=1.0,
            xmin=5,
        )
        self.assertGreater(nb["member"], nb["nonmember"])
        self.assertGreater(frequency["member"], frequency["nonmember"])

        similarity = merge_similarity_scores(
            target_vocab={"a": 0, "b": 1, "c": 2},
            shadow_vocabs=[
                {"a": 0, "b": 1, "c": 2},
                {"a": 2, "b": 0, "c": 1},
            ],
            shadow_training_sites=[{"member"}, {"nonmember"}],
            target_sites=["member", "nonmember"],
        )
        self.assertGreater(similarity["member"], similarity["nonmember"])

    def test_naive_bayes_exact_zero_survival_is_maximal_and_tied(self) -> None:
        scores = naive_bayes_scores(
            target_vocab={"rare": 2, "common": 1},
            site_counts={
                "saturated-rare": Counter({"rare": 10}),
                "saturated-common": Counter({"common": 20}),
                "finite": Counter({"rare": 9}),
            },
            background_counts=Counter({"rare": 10, "common": 20}),
            involved_sites={"saturated-rare", "saturated-common", "finite"},
            top_k=2,
        )
        self.assertEqual(scores["saturated-rare"], scores["saturated-common"])
        self.assertGreater(scores["saturated-rare"], scores["finite"])

    def test_fast_bootstrap_metrics_match_sklearn_with_ties(self) -> None:
        rng = np.random.default_rng(20260726)
        for _ in range(20):
            labels = np.asarray([0] * 11 + [1] * 7, dtype=np.int64)
            scores = rng.integers(0, 5, size=len(labels)).astype(np.float64)
            expected = point_metrics(labels, scores)
            actual = _fast_scalar_metrics(labels, scores)
            for name in actual:
                self.assertAlmostEqual(actual[name], expected[name], places=12)


if __name__ == "__main__":
    unittest.main()
