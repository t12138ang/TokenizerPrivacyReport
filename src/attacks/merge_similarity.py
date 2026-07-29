"""Official-style site-specific in/out merge-rank similarity attack."""

from __future__ import annotations

from collections.abc import Sequence

import numpy as np


def safe_rank_correlation(left: dict[str, int], right: dict[str, int]) -> float:
    shared = sorted(set(left) & set(right))
    if len(shared) < 2:
        return 0.0
    x = np.asarray([left[token] + 1 for token in shared], dtype=np.float64)
    y = np.asarray([right[token] + 1 for token in shared], dtype=np.float64)
    if np.std(x) == 0 or np.std(y) == 0:
        return 0.0
    value = float(np.corrcoef(x, y)[0, 1])
    return value if np.isfinite(value) else 0.0


def merge_similarity_scores(
    *,
    target_vocab: dict[str, int],
    shadow_vocabs: Sequence[dict[str, int]],
    shadow_training_sites: Sequence[set[str]],
    target_sites: Sequence[str],
) -> dict[str, float]:
    if len(shadow_vocabs) != len(shadow_training_sites):
        raise ValueError("shadow vocab/training metadata count mismatch")
    correlations = [safe_rank_correlation(vocab, target_vocab) for vocab in shadow_vocabs]
    result = {}
    for site in target_sites:
        inside = [value for value, sites in zip(correlations, shadow_training_sites) if site in sites]
        outside = [value for value, sites in zip(correlations, shadow_training_sites) if site not in sites]
        if not inside or not outside:
            raise ValueError(f"site lacks shadow in/out coverage: {site}")
        result[site] = (float(np.mean(inside)) - float(np.mean(outside)) + 2.0) / 4.0
    return result
