"""Log-domain implementation of the official Naive-Bayes-style attack."""

from __future__ import annotations

import math
from collections import Counter


SATURATED_LOG_RISK = 1e300

def naive_bayes_scores(
    *,
    target_vocab: dict[str, int],
    site_counts: dict[str, Counter[str]],
    background_counts: Counter[str],
    involved_sites: set[str],
    top_k: int,
) -> dict[str, float]:
    if not background_counts or not involved_sites or top_k <= 0:
        raise ValueError("background counts, involved sites, and positive top_k are required")
    selected = [
        token
        for token, _ in sorted(target_vocab.items(), key=lambda item: (-item[1], item[0]))[:top_k]
    ]
    scores = {}
    for site, counts in site_counts.items():
        log_survival = 0.0
        saturated = False
        for token in selected:
            site_count = int(counts[token])
            if site_count <= 0:
                continue
            denominator = int(background_counts[token])
            if site not in involved_sites:
                denominator += site_count
            probability = site_count / denominator if denominator else 0.0
            if probability >= 1.0:
                saturated = True
                break
            probability = max(probability, 0.0)
            log_survival += math.log1p(-probability)
        # This log-risk is a monotone, non-saturating form of the official
        # 1-product(1-p) score and therefore preserves ranking metrics.
        scores[site] = SATURATED_LOG_RISK if saturated else -log_survival
    return scores
