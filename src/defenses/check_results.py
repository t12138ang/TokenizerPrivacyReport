"""Read-only completeness and numeric audit for Development/Main defenses."""

from __future__ import annotations

import argparse
import math
from pathlib import Path
from typing import Any

from src.utils.run_metadata import PROJECT_ROOT, sha256_file, strict_json_load


def validate_finite(value: Any, location: str = "root") -> None:
    if isinstance(value, float) and not math.isfinite(value):
        raise ValueError(f"non-finite value at {location}")
    if isinstance(value, dict):
        for key, child in value.items():
            validate_finite(child, f"{location}.{key}")
    elif isinstance(value, list):
        for index, child in enumerate(value):
            validate_finite(child, f"{location}[{index}]")


def validate_artifacts(paths: list[Path]) -> None:
    for path in paths:
        result = strict_json_load(path)
        validate_finite(result, str(path))
        if result.get("status") != "success":
            raise RuntimeError(f"non-success defense artifact: {path}")


def check_development(config: dict[str, Any], root: Path) -> None:
    plan = strict_json_load(root / "development_search_plan.json")
    state = strict_json_load(root / "development" / "pipeline_state.json")
    shortlist = strict_json_load(root / "development_shortlist.json")
    if state.get("status") != "success" or int(state["completed_tasks"]) != int(state["expected_tasks"]):
        raise RuntimeError("Development defense pipeline is incomplete")
    if int(state.get("failures", 0)) != 0:
        raise RuntimeError(f"Development defense failures={state['failures']}")
    if shortlist.get("status") != "awaiting_downstream_macro_f1_constraint":
        raise RuntimeError("Development shortlist state is invalid")
    expected_tokenizers = (int(plan["configuration_count"]) + 2) * len(plan["seeds"])
    tokenizers = sorted((root / "development" / "tokenizers").rglob("metadata.json"))
    attacks = sorted((root / "development" / "attacks").rglob("*.json"))
    utilities = sorted((root / "development" / "utility").rglob("utility.json"))
    plain_utilities = sorted((root / "development" / "plain_utility").rglob("utility.json"))
    expected_attacks = expected_tokenizers * len(config["attacks"])
    if (len(tokenizers), len(attacks), len(utilities), len(plain_utilities)) != (
        expected_tokenizers, expected_attacks, expected_tokenizers, len(plan["seeds"]),
    ):
        raise RuntimeError(
            "Development cardinality mismatch: "
            f"tokenizers={len(tokenizers)}/{expected_tokenizers} "
            f"attacks={len(attacks)}/{expected_attacks} "
            f"utility={len(utilities)}/{expected_tokenizers} "
            f"plain_utility={len(plain_utilities)}/{len(plan['seeds'])}"
        )
    validate_artifacts(tokenizers + attacks + utilities + plain_utilities)
    print(
        f"stage=development status=success configurations={plan['configuration_count']} "
        f"tokenizers={len(tokenizers)} attacks={len(attacks)} utility={len(utilities) + len(plain_utilities)} "
        f"failures={state['failures']}"
    )
    for mode, rows in shortlist["shortlist"].items():
        print(f"shortlist_{mode}={len(rows)} ids={[row['id'] for row in rows]}")


def check_main(config: dict[str, Any], root: Path) -> None:
    selection_path = root / "main_selection.json"
    selection = strict_json_load(selection_path)
    if selection.get("status") != "frozen_before_main_results":
        raise RuntimeError("Main selection was not frozen")
    if selection.get("development_only") is not True or selection.get("main_results_read") is not False:
        raise RuntimeError("Main selection provenance is invalid")
    state = strict_json_load(root / "main" / "pipeline_state.json")
    if state.get("selection_sha256") != sha256_file(selection_path):
        raise RuntimeError("Main state/selection hash mismatch")
    if state.get("status") != "success" or int(state["completed_tasks"]) != int(state["expected_tasks"]):
        raise RuntimeError("Main defense pipeline is incomplete")
    if int(state.get("failures", 0)) != 0:
        raise RuntimeError(f"Main defense failures={state['failures']}")
    seeds = list(config["seeds"])
    configuration_count = 2 + len(selection["selected"])
    expected_tokenizers = configuration_count * len(seeds)
    tokenizers = sorted((root / "main" / "tokenizers").rglob("metadata.json"))
    attacks = sorted((root / "main" / "attacks").rglob("*.json"))
    utilities = sorted((root / "main" / "utility").rglob("utility.json"))
    baseline_utilities = sorted((root / "main" / "baseline_utility").rglob("utility.json"))
    expected_attacks = expected_tokenizers * len(config["attacks"])
    expected_baseline_utility = len(config["baseline_methods"]) * len(seeds)
    if (len(tokenizers), len(attacks), len(utilities), len(baseline_utilities)) != (
        expected_tokenizers, expected_attacks, expected_tokenizers, expected_baseline_utility,
    ):
        raise RuntimeError(
            "Main cardinality mismatch: "
            f"tokenizers={len(tokenizers)}/{expected_tokenizers} "
            f"attacks={len(attacks)}/{expected_attacks} "
            f"utility={len(utilities)}/{expected_tokenizers} "
            f"baseline_utility={len(baseline_utilities)}/{expected_baseline_utility}"
        )
    validate_artifacts(tokenizers + attacks + utilities + baseline_utilities)
    equivalence = strict_json_load(root / "main" / "he_plain_equivalence.json")
    if equivalence.get("status") != "success" or equivalence.get("all_exact") is not True:
        raise RuntimeError("Main protocol-Plain/HE-only artifact equivalence failed")
    validate_finite(equivalence, "he_plain_equivalence")
    print(
        f"stage=main status=success selected={[row['id'] for row in selection['selected']]} "
        f"tokenizers={len(tokenizers)} attacks={len(attacks)} "
        f"utility={len(utilities) + len(baseline_utilities)} failures={state['failures']} he_plain_exact=true"
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--stage", choices=("development", "main", "all"), default="all")
    args = parser.parse_args()
    config = strict_json_load(args.config.resolve())
    root = PROJECT_ROOT / config["results_root"] / "defenses"
    if args.stage in {"development", "all"}:
        check_development(config, root)
    if args.stage in {"main", "all"}:
        check_main(config, root)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
