"""Exact two-sided geometric noise for integer-valued L1-sensitive queries."""

from __future__ import annotations

import math

import numpy as np


def two_sided_geometric(
    *, epsilon: float, sensitivity: int, size: int | tuple[int, ...], rng: np.random.Generator
) -> np.ndarray:
    """Sample discrete Laplace noise as a difference of two geometrics.

    For alpha=exp(-epsilon/sensitivity), this has probability mass
    ``(1-alpha)/(1+alpha) * alpha**abs(z)`` and implements pure epsilon-DP
    for an integer vector query with L1 sensitivity at most ``sensitivity``.
    """
    if not math.isfinite(epsilon) or epsilon <= 0:
        raise ValueError("epsilon must be finite and positive")
    if not isinstance(sensitivity, int) or sensitivity <= 0:
        raise ValueError("sensitivity must be a positive integer")
    alpha = math.exp(-epsilon / sensitivity)
    success_probability = -math.expm1(-epsilon / sensitivity)
    if not 0 < success_probability <= 1:
        raise ValueError("invalid geometric success probability")
    left = rng.geometric(success_probability, size=size).astype(object) - 1
    right = rng.geometric(success_probability, size=size).astype(object) - 1
    return left - right


def add_geometric_noise(
    values: np.ndarray, *, epsilon: float, sensitivity: int, rng: np.random.Generator
) -> tuple[np.ndarray, np.ndarray]:
    vector = np.asarray(values, dtype=object)
    noise = two_sided_geometric(
        epsilon=epsilon, sensitivity=sensitivity, size=vector.shape, rng=rng
    )
    return vector + noise, noise
