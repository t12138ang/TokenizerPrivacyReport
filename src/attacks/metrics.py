"""Fixed-direction attack metrics and stratified bootstrap confidence intervals."""

from __future__ import annotations

from typing import Any

import numpy as np
from sklearn.metrics import average_precision_score, precision_recall_curve, roc_auc_score, roc_curve


SCORE_DIRECTION = "higher_is_more_member"
POSITIVE_CLASS = "target tokenizer training member website"


def _fast_scalar_metrics(labels: np.ndarray, scores: np.ndarray) -> dict[str, float]:
    """Compute scalar ranking metrics with exact handling of tied scores."""
    order = np.argsort(-scores, kind="mergesort")
    sorted_scores = scores[order]
    sorted_labels = labels[order]
    positives = int(np.sum(sorted_labels))
    negatives = len(sorted_labels) - positives
    if positives <= 0 or negatives <= 0:
        raise ValueError("metrics require both binary classes")
    group_ends = np.r_[np.flatnonzero(sorted_scores[:-1] != sorted_scores[1:]), len(scores) - 1]
    true_positive = np.cumsum(sorted_labels)[group_ends].astype(np.float64)
    predicted_positive = (group_ends + 1).astype(np.float64)
    false_positive = predicted_positive - true_positive
    tpr = np.r_[0.0, true_positive / positives]
    fpr = np.r_[0.0, false_positive / negatives]
    precision = true_positive / predicted_positive
    recall = true_positive / positives
    recall_increment = np.diff(np.r_[0.0, recall])
    return {
        "roc_auc": float(np.trapezoid(tpr, fpr)),
        "balanced_accuracy": float(np.max((tpr + 1.0 - fpr) / 2.0)),
        "tpr_at_fpr_le_0_01": float(np.max(tpr[fpr <= 0.01])),
        "tpr_at_fpr_le_0_001": float(np.max(tpr[fpr <= 0.001])),
        "average_precision": float(np.sum(recall_increment * precision)),
    }


def point_metrics(labels: np.ndarray, scores: np.ndarray) -> dict[str, Any]:
    if labels.ndim != 1 or scores.ndim != 1 or labels.shape != scores.shape:
        raise ValueError("labels and scores must be same-length one-dimensional arrays")
    if set(np.unique(labels)) != {0, 1}:
        raise ValueError("metrics require both binary classes encoded as 0/1")
    fpr, tpr, thresholds = roc_curve(labels, scores, pos_label=1)
    precision, recall, pr_thresholds = precision_recall_curve(labels, scores, pos_label=1)
    valid_001 = np.where(fpr <= 0.01)[0]
    valid_0001 = np.where(fpr <= 0.001)[0]
    return {
        "roc_auc": float(roc_auc_score(labels, scores)),
        "balanced_accuracy": float(np.max(1.0 - (fpr + (1.0 - tpr)) / 2.0)),
        "tpr_at_fpr_le_0_01": float(np.max(tpr[valid_001])) if valid_001.size else 0.0,
        "tpr_at_fpr_le_0_001": float(np.max(tpr[valid_0001])) if valid_0001.size else 0.0,
        "average_precision": float(average_precision_score(labels, scores)),
        "fpr": [float(value) for value in fpr],
        "tpr": [float(value) for value in tpr],
        "thresholds": [float(value) if np.isfinite(value) else None for value in thresholds],
        "precision": [float(value) for value in precision],
        "recall": [float(value) for value in recall],
        "pr_thresholds": [float(value) for value in pr_thresholds],
    }


def bootstrap_confidence_intervals(
    labels: np.ndarray,
    scores: np.ndarray,
    *,
    seed: int,
    iterations: int,
    confidence: float,
) -> dict[str, dict[str, float]]:
    member_indices = np.where(labels == 1)[0]
    nonmember_indices = np.where(labels == 0)[0]
    if not member_indices.size or not nonmember_indices.size:
        raise ValueError("bootstrap requires both positive and negative samples")
    rng = np.random.default_rng(seed)
    names = [
        "roc_auc",
        "balanced_accuracy",
        "tpr_at_fpr_le_0_01",
        "tpr_at_fpr_le_0_001",
        "average_precision",
    ]
    samples: dict[str, list[float]] = {name: [] for name in names}
    for _ in range(iterations):
        draw = np.concatenate(
            [
                rng.choice(member_indices, size=member_indices.size, replace=True),
                rng.choice(nonmember_indices, size=nonmember_indices.size, replace=True),
            ]
        )
        values = _fast_scalar_metrics(labels[draw], scores[draw])
        for name in names:
            samples[name].append(float(values[name]))
    alpha = 1.0 - confidence
    return {
        name: {
            "lower": float(np.quantile(values, alpha / 2.0)),
            "upper": float(np.quantile(values, 1.0 - alpha / 2.0)),
        }
        for name, values in samples.items()
    }


def compute_attack_metrics(
    labels: list[int],
    scores: list[float],
    *,
    seed: int,
    bootstrap_iterations: int,
    bootstrap_confidence: float,
) -> dict[str, Any]:
    label_array = np.asarray(labels, dtype=np.int64)
    score_array = np.asarray(scores, dtype=np.float64)
    if not np.all(np.isfinite(score_array)):
        raise ValueError("attack scores contain non-finite values")
    result = point_metrics(label_array, score_array)
    result["bootstrap"] = {
        "method": "stratified website-level percentile bootstrap",
        "iterations": bootstrap_iterations,
        "confidence": bootstrap_confidence,
        "seed": seed,
        "confidence_intervals": bootstrap_confidence_intervals(
            label_array,
            score_array,
            seed=seed,
            iterations=bootstrap_iterations,
            confidence=bootstrap_confidence,
        ),
    }
    result["positive_class"] = POSITIVE_CLASS
    result["score_direction"] = SCORE_DIRECTION
    return result
