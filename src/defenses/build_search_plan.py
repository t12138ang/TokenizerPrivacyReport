"""Materialize the predeclared non-Cartesian Development defense search plan."""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any

from src.utils.run_metadata import environment_metadata, sha256_file, strict_json_load, utc_now, write_json_exclusive


def identifier(mode: str, values: dict[str, Any]) -> str:
    return (
        f"{mode}_eps{values['epsilon_total']}_C{values['clipping_percentile']}_"
        f"b{values['batch_merge_size']}_K{values['candidate_pool_size']}"
    ).replace(".", "p")


def one_factor(reference: dict[str, Any], grid: dict[str, list[Any]], mode: str) -> list[dict[str, Any]]:
    configurations: list[dict[str, Any]] = []
    seen = set()
    dimensions = list(grid) if mode == "sa_dp" else ["epsilon_total", "clipping_percentile"]
    for dimension in dimensions:
        for value in grid[dimension]:
            candidate = dict(reference)
            candidate[dimension] = value
            key = tuple(sorted(candidate.items()))
            if key in seen:
                continue
            seen.add(key)
            configurations.append({"id": identifier(mode, candidate), "mode": mode, **candidate})
    return configurations


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    config_path = args.config.resolve()
    config = strict_json_load(config_path)
    search = config["development_search_design"]
    reference = search["reference"]
    grid = config["development_grid"]
    configurations = one_factor(reference, grid, "sa_dp") + one_factor(reference, grid, "local_dp")
    if args.output.resolve().exists():
        raise FileExistsError(f"refusing to overwrite frozen search plan: {args.output.resolve()}")
    payload = {
        "schema_version": 1,
        "status": "frozen_before_development_results",
        "created_at_utc": utc_now(),
        "config_path": str(config_path),
        "config_sha256": sha256_file(config_path),
        "scale": "development",
        "protocol": "strict_disjoint",
        "vocab_size": int(search["vocab_size"]),
        "seeds": config["seeds"],
        "search_design": search,
        "configuration_count": len(configurations),
        "configurations": configurations,
        "selection_rule": config["selection_rule"],
        "main_results_permitted_for_selection": False,
        "environment": environment_metadata(),
    }
    write_json_exclusive(args.output.resolve(), payload)
    print(f"status=frozen configurations={len(configurations)} output={args.output.resolve()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
