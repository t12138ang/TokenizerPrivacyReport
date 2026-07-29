"""Validate the frozen Development defense matrix and shortlist."""

from __future__ import annotations

import argparse
import math
from pathlib import Path
from typing import Any

from src.utils.run_metadata import PROJECT_ROOT, strict_json_load


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path, required=True)
    return parser.parse_args()


def validate_finite(value: Any, location: str = "root") -> None:
    if isinstance(value, float) and not math.isfinite(value):
        raise ValueError(f"non-finite value at {location}")
    if isinstance(value, dict):
        for key, child in value.items():
            validate_finite(child, f"{location}.{key}")
    elif isinstance(value, list):
        for index, child in enumerate(value):
            validate_finite(child, f"{location}[{index}]")


def main() -> int:
    args = parse_args()
    config = strict_json_load(args.config.resolve())
    root = PROJECT_ROOT / config["results_root"] / "defenses"
    plan = strict_json_load(root / "development_search_plan.json")
    state = strict_json_load(root / "development" / "pipeline_state.json")
    shortlist = strict_json_load(root / "development_shortlist.json")
    if state.get("status") != "success":
        raise RuntimeError(f"Development defense pipeline is not success: {state.get('status')}")
    if shortlist.get("status") != "awaiting_downstream_macro_f1_constraint":
        raise RuntimeError("Development shortlist state is invalid")
    expected_tokenizers = (plan["configuration_count"] + 2) * len(plan["seeds"])
    tokenizers = sorted((root / "development" / "tokenizers").rglob("metadata.json"))
    expected_attacks = expected_tokenizers * len(config["attacks"])
    attacks = sorted((root / "development" / "attacks").rglob("*.json"))
    utilities = sorted((root / "development" / "utility").rglob("utility.json"))
    if len(tokenizers) != expected_tokenizers or len(attacks) != expected_attacks or len(utilities) != expected_tokenizers:
        raise RuntimeError(
            f"defense cardinality mismatch tokenizers={len(tokenizers)}/{expected_tokenizers} "
            f"attacks={len(attacks)}/{expected_attacks} utility={len(utilities)}/{expected_tokenizers}"
        )
    for path in tokenizers + attacks + utilities:
        result = strict_json_load(path)
        validate_finite(result)
        if result.get("status") != "success":
            raise RuntimeError(f"non-success defense artifact: {path}")
    print(
        f"status=success configurations={plan['configuration_count']} "
        f"tokenizers={len(tokenizers)} attacks={len(attacks)} utility={len(utilities)} "
        f"failures={state['failures']}"
    )
    for mode, rows in shortlist["shortlist"].items():
        print(f"shortlist_{mode}={len(rows)} ids={[row['id'] for row in rows]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
