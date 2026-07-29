"""Build immutable downstream-training plans from frozen Development decisions."""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any

from src.utils.run_metadata import (
    PROJECT_ROOT,
    canonical_sha256,
    environment_metadata,
    sha256_file,
    strict_json_load,
    utc_now,
    write_json_exclusive,
)


def task(method_id: str, seed: int, tokenizer_dir: Path, output_dir: Path) -> dict[str, Any]:
    metadata = tokenizer_dir / "metadata.json"
    artifact = tokenizer_dir / "tokenizer.json"
    if not metadata.is_file() or not artifact.is_file():
        raise FileNotFoundError(f"downstream tokenizer is incomplete: {tokenizer_dir}")
    details = strict_json_load(metadata)
    artifact_hash = sha256_file(artifact)
    if details.get("artifact_sha256") != artifact_hash:
        raise RuntimeError(f"tokenizer hash mismatch: {tokenizer_dir}")
    return {
        "task_id": f"{method_id}__seed_{seed}",
        "method_id": method_id,
        "seed": seed,
        "tokenizer_dir": str(tokenizer_dir.relative_to(PROJECT_ROOT)).replace("\\", "/"),
        "tokenizer_sha256": artifact_hash,
        "output_dir": str(output_dir.relative_to(PROJECT_ROOT)).replace("\\", "/"),
    }


def build_development(config: dict[str, Any]) -> list[dict[str, Any]]:
    result_root = PROJECT_ROOT / config["results_root"]
    shortlist_path = result_root / "defenses" / "development_shortlist.json"
    shortlist = strict_json_load(shortlist_path)
    if shortlist.get("status") != "awaiting_downstream_macro_f1_constraint":
        raise RuntimeError("Development defense shortlist is not ready")
    seeds = [int(value) for value in config["seeds"]]
    vocab = int(config["development_search_design"]["vocab_size"])
    frozen_search = strict_json_load(result_root / "defenses" / "development_search_plan.json")
    reference = config["development_search_design"]["reference"]
    epsilon_sweep = [
        row["id"]
        for row in frozen_search["configurations"]
        if row["mode"] == "sa_dp"
        and row["clipping_percentile"] == reference["clipping_percentile"]
        and row["batch_merge_size"] == reference["batch_merge_size"]
        and row["candidate_pool_size"] == reference["candidate_pool_size"]
    ]
    methods = ["plain_bpe"] + [
        row["id"]
        for mode in ("local_dp", "sa_dp")
        for row in shortlist["shortlist"][mode]
    ] + epsilon_sweep
    methods = list(dict.fromkeys(methods))
    tasks = []
    for method in methods:
        for seed in seeds:
            if method == "plain_bpe":
                tokenizer_dir = (
                    result_root / "runs" / "tokenizers" / "development" / str(seed)
                    / f"vocab_{vocab}" / method / "target"
                )
            else:
                tokenizer_dir = result_root / "defenses" / "development" / "tokenizers" / method / str(seed)
            output = result_root / "downstream" / "development" / method / str(seed)
            tasks.append(task(method, seed, tokenizer_dir, output))
    return tasks


def build_main(config: dict[str, Any]) -> list[dict[str, Any]]:
    result_root = PROJECT_ROOT / config["results_root"]
    selection = strict_json_load(result_root / "defenses" / "main_selection.json")
    main_state = strict_json_load(result_root / "defenses" / "main" / "pipeline_state.json")
    if selection.get("status") != "frozen_before_main_results":
        raise RuntimeError("Main defense selection is not frozen")
    if main_state.get("status") != "success":
        raise RuntimeError("Main defense pipeline must complete before its downstream plan")
    seeds = [int(value) for value in config["seeds"]]
    vocab = int(config["defense_main_configuration"]["vocab_size"])
    baseline_methods = [row["id"] for row in config["baseline_methods"]]
    private_methods = ["he_only_reference"] + [row["id"] for row in selection["selected"]]
    tasks = []
    for method in baseline_methods + private_methods:
        for seed in seeds:
            if method in baseline_methods:
                tokenizer_dir = (
                    result_root / "runs" / "tokenizers" / "main" / str(seed)
                    / f"vocab_{vocab}" / method / "target"
                )
            else:
                tokenizer_dir = result_root / "defenses" / "main" / "tokenizers" / method / str(seed)
            output = result_root / "downstream" / "main" / method / str(seed)
            tasks.append(task(method, seed, tokenizer_dir, output))
    return tasks


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--stage", choices=("development", "main"), required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    config_path = args.config.resolve()
    config = strict_json_load(config_path)
    tasks = build_development(config) if args.stage == "development" else build_main(config)
    payload = {
        "schema_version": 1,
        "status": "frozen",
        "stage": args.stage,
        "created_at_utc": utc_now(),
        "config_path": str(config_path.relative_to(PROJECT_ROOT)).replace("\\", "/"),
        "config_sha256": sha256_file(config_path),
        "downstream_config_sha256": sha256_file(PROJECT_ROOT / "configs" / "downstream.json"),
        "task_count": len(tasks),
        "tasks": tasks,
        "environment": environment_metadata(),
    }
    payload["plan_content_sha256"] = canonical_sha256(payload)
    write_json_exclusive(args.output.resolve(), payload)
    print(f"status=frozen stage={args.stage} tasks={len(tasks)} output={args.output.resolve()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
