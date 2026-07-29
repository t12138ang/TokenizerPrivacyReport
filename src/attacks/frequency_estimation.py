"""Numerically stable frequency-estimation membership score."""

from __future__ import annotations

import math
from collections import Counter

import numpy as np
from scipy.optimize import minimize_scalar
from scipy.special import zeta


SPECIAL_TOKENS = {"[UNK]", "[CLS]", "[SEP]", "[PAD]", "[MASK]"}


def fit_zipf_exponent(vocab: dict[str, int], counts: Counter[str]) -> dict[str, float | int | str]:
    """Fit a weighted discrete power-law tail without expanding 10M samples.

    The official implementation repeats each vocabulary rank in proportion to
    its token frequency and applies ``powerlaw.Fit``.  This equivalent compact
    representation evaluates a deterministic grid of candidate ``xmin`` values,
    maximizes the weighted discrete likelihood, and selects the lowest KS
    distance.  Single-character and special tokens are excluded to match the
    upstream construction of its frequency sample.
    """
    points = sorted(
        (int(rank) + 1, int(counts[token]))
        for token, rank in vocab.items()
        if int(counts[token]) > 0 and len(token) > 1 and token not in SPECIAL_TOKENS
    )
    if len(points) < 3:
        return {
            "alpha": 2.0,
            "xmin": int(points[0][0]) if points else 1,
            "point_count": len(points),
            "weighted_observation_count": sum(weight for _, weight in points),
            "candidate_xmin_count": 0,
            "ks_distance": 0.0,
            "fit_method": "weighted_discrete_power_law_mle_ks_fallback",
        }
    ranks = np.asarray([rank for rank, _ in points], dtype=np.float64)
    weights = np.asarray([weight for _, weight in points], dtype=np.float64)
    maximum_start = max(0, len(points) - max(20, math.ceil(len(points) * 0.05)))
    candidate_count = min(128, maximum_start + 1)
    candidate_indices = np.unique(
        np.linspace(0, maximum_start, num=candidate_count, dtype=np.int64)
    )
    best: tuple[float, int, float, int] | None = None
    for start in candidate_indices:
        tail_ranks = ranks[start:]
        tail_weights = weights[start:]
        xmin = int(tail_ranks[0])
        total_weight = float(np.sum(tail_weights))
        weighted_log_sum = float(np.dot(tail_weights, np.log(tail_ranks)))

        def negative_log_likelihood(alpha: float) -> float:
            normalization = float(zeta(alpha, xmin))
            if not math.isfinite(normalization) or normalization <= 0:
                return math.inf
            return alpha * weighted_log_sum + total_weight * math.log(normalization)

        optimization = minimize_scalar(
            negative_log_likelihood,
            bounds=(1.000001, 10.0),
            method="bounded",
            options={"xatol": 1e-7, "maxiter": 200},
        )
        if not optimization.success or not math.isfinite(float(optimization.fun)):
            continue
        alpha = float(optimization.x)
        empirical = np.cumsum(tail_weights) / total_weight
        theoretical = 1.0 - zeta(alpha, tail_ranks + 1.0) / zeta(alpha, xmin)
        ks_distance = float(np.max(np.abs(empirical - theoretical)))
        candidate = (ks_distance, xmin, alpha, len(tail_ranks))
        if best is None or candidate[:2] < best[:2]:
            best = candidate
    if best is None:
        raise RuntimeError("weighted discrete power-law fit failed for every candidate xmin")
    ks_distance, xmin, alpha, tail_points = best
    return {
        "alpha": alpha,
        "xmin": xmin,
        "point_count": len(points),
        "tail_point_count": tail_points,
        "weighted_observation_count": int(np.sum(weights)),
        "candidate_xmin_count": len(candidate_indices),
        "ks_distance": ks_distance,
        "fit_method": "weighted_discrete_power_law_mle_ks",
    }


def frequency_estimation_scores(
    *,
    target_vocab: dict[str, int],
    site_counts: dict[str, Counter[str]],
    background_counts: Counter[str],
    involved_sites: set[str],
    zipf_alpha: float,
    xmin: int,
) -> dict[str, float]:
    if not background_counts or not involved_sites or xmin < 1:
        raise ValueError("background counts, involved sites, and positive xmin are required")
    vocab_size = max(len(target_vocab), xmin + 1)
    denominator_sum = math.fsum(
        rank ** (-max(zipf_alpha, 0.0)) for rank in range(xmin + 1, vocab_size + 1)
    )
    scores = {}
    for site, counts in site_counts.items():
        signals = []
        for token, count in counts.items():
            rank = target_vocab.get(token)
            if rank is None or rank + 1 < xmin + 1 or count <= 0 or token not in background_counts:
                continue
            denominator = int(background_counts[token])
            if site not in involved_sites:
                denominator += int(count)
            if denominator <= 0:
                continue
            rarity = math.log(max((rank + 1) ** max(zipf_alpha, 0.0) * denominator_sum, 1e-300))
            signals.append((count / denominator) * rarity)
        scores[site] = max(signals, default=0.0)
    return scores
