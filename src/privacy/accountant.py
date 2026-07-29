"""Explicit basic sequential-composition accountant used by the prototype."""

from __future__ import annotations

import math


def uniform_round_budget(epsilon_total: float, rounds: int) -> float:
    if not math.isfinite(epsilon_total) or epsilon_total <= 0:
        raise ValueError("epsilon_total must be finite and positive")
    if not isinstance(rounds, int) or rounds <= 0:
        raise ValueError("rounds must be a positive integer")
    return epsilon_total / rounds


def basic_composition(round_budgets: list[float]) -> dict[str, float | int | str]:
    if not round_budgets or any(not math.isfinite(value) or value <= 0 for value in round_budgets):
        raise ValueError("round budgets must be finite and positive")
    return {
        "accountant": "basic_sequential_composition",
        "rounds": len(round_budgets),
        "epsilon_total": math.fsum(round_budgets),
        "delta": 0.0,
    }
