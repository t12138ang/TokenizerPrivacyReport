"""Deterministic L1 clipping and bounded integerization."""

from __future__ import annotations

from collections.abc import Sequence

import numpy as np


def l1_clip_largest_remainder(values: Sequence[int], bound: int) -> np.ndarray:
    """Clip a nonnegative integer vector to L1 ``bound`` deterministically.

    If clipping is required, Hamilton's largest-remainder method converts the
    scaled floating-point quotas back to integers while preserving a total of
    exactly ``bound``.  Ties are resolved by the original coordinate index.
    """
    if not isinstance(bound, int) or isinstance(bound, bool) or bound < 0:
        raise ValueError("bound must be a nonnegative integer")
    vector = np.asarray(values)
    if vector.ndim != 1:
        raise ValueError("values must be one-dimensional")
    if vector.dtype.kind == "O":
        if not all(isinstance(value, (int, np.integer)) and not isinstance(value, bool) for value in vector.tolist()):
            raise TypeError("values must contain integers")
    elif vector.dtype.kind not in "iu":
        if vector.dtype.kind == "f" and np.all(np.isfinite(vector)) and np.all(vector == np.floor(vector)):
            vector = vector.astype(object)
        else:
            raise TypeError("values must contain integers")
    integers = [int(value) for value in vector.tolist()]
    if any(value < 0 for value in integers):
        raise ValueError("values must be nonnegative")
    total = sum(integers)
    if total <= bound:
        return np.asarray(integers, dtype=object)
    if bound == 0:
        return np.zeros(len(integers), dtype=object)

    numerators = [value * bound for value in integers]
    floors = [value // total for value in numerators]
    fractions = [value % total for value in numerators]
    remaining = bound - sum(floors)
    order = sorted(range(len(integers)), key=lambda index: (-fractions[index], index))
    for index in order[:remaining]:
        floors[index] += 1
    result = np.asarray(floors, dtype=object)
    if any(int(value) < 0 for value in result) or sum(map(int, result)) > bound:
        raise AssertionError("clipping invariant violated")
    return result
