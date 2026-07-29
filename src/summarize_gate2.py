"""Validate all expected Gate 2 results and create immutable summaries."""

from __future__ import annotations

import argparse
import csv
import time
from pathlib import Path
from typing import Any

from src.gate2_attack_pipeline import model_dir, result_path
from src.utils.run_metadata import (
    PROJECT_ROOT,
    environment_metadata,
    setup_logger,
    strict_json_load,
    utc_now,
    write_json_exclusive,
)


METRIC_NAMES = ("roc_auc", "balanced_accuracy", "tpr_at_fpr_le_0_01", "average_precision")


def write_csv_exclusive(path: Path, fieldnames: list[str], rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--log", type=Path, required=True)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    config = strict_json_load(args.config.resolve())
    logger = setup_logger("gate2.summarize", args.log.resolve())
    started = time.perf_counter()
    results_root = PROJECT_ROOT / config["results_root"]
    run_root = results_root / "runs"
    summary_csv = results_root / "summary.csv"
    summary_json = results_root / "summary.json"
    resource_csv = results_root / "resource_profile.csv"
    for path in (summary_csv, summary_json, resource_csv):
        if path.exists():
            raise FileExistsError(f"refusing to overwrite Gate 2 summary: {path}")

    rows: list[dict[str, Any]] = []
    for protocol in config["protocols"]:
        for seed_value in config["seeds"]:
            seed = int(seed_value)
            for vocab_value in config["vocab_sizes"]:
                vocab = int(vocab_value)
                for method in config["methods"]:
                    for attack in config["attacks"]:
                        path = result_path(run_root, protocol, seed, vocab, method["id"], attack)
                        result = strict_json_load(path)
                        if result.get("status") != "success":
                            raise RuntimeError(f"non-success attack result: {path}")
                        metrics = result["metrics"]
                        if metrics.get("score_direction") != "higher_is_more_member":
                            raise RuntimeError(f"unexpected score direction: {path}")
                        row: dict[str, Any] = {
                            "protocol": protocol,
                            "seed": seed,
                            "vocab_size": vocab,
                            "actual_vocab_size": result["actual_vocab_size"],
                            "method_id": method["id"],
                            "defense": method["defense"],
                            "min_count_threshold": method["min_count_threshold"],
                            "attack": attack,
                            "site_count": result["data"]["site_count"],
                            "member_site_count": result["data"]["member_site_count"],
                            "nonmember_site_count": result["data"]["nonmember_site_count"],
                            "score_direction": metrics["score_direction"],
                            "positive_class": metrics["positive_class"],
                            "elapsed_seconds": result["elapsed_seconds"],
                            "peak_memory_bytes": result["peak_memory_bytes"],
                            "result_path": str(path.relative_to(PROJECT_ROOT)),
                        }
                        intervals = metrics["bootstrap"]["confidence_intervals"]
                        for name in METRIC_NAMES:
                            row[name] = metrics[name]
                            row[f"{name}_ci_lower"] = intervals[name]["lower"]
                            row[f"{name}_ci_upper"] = intervals[name]["upper"]
                        rows.append(row)

    expected = (
        len(config["protocols"])
        * len(config["seeds"])
        * len(config["vocab_sizes"])
        * len(config["methods"])
        * len(config["attacks"])
    )
    if len(rows) != expected:
        raise RuntimeError(f"expected {expected} attack rows, found {len(rows)}")

    resource_rows: list[dict[str, Any]] = []
    for path in sorted((run_root / "tokenizers").rglob("metadata.json")):
        metadata = strict_json_load(path)
        resource_rows.append(
            {
                "kind": metadata["kind"],
                "protocol": metadata["protocol"],
                "seed": metadata["seed"],
                "vocab_size": metadata.get("requested_vocab_size", ""),
                "actual_vocab_size": metadata["actual_vocab_size"],
                "method_id": metadata.get("method_id", "base"),
                "role": metadata["role"],
                "shadow_id": "" if metadata["shadow_id"] is None else metadata["shadow_id"],
                "training_site_count": metadata["training_site_count"],
                "training_text_count": metadata["training_text_count"],
                "training_byte_count": metadata["training_byte_count"],
                "elapsed_seconds": metadata["elapsed_seconds"],
                "peak_memory_bytes": metadata["peak_memory_bytes"],
                "artifact_sha256": metadata["artifact_sha256"],
                "metadata_path": str(path.relative_to(PROJECT_ROOT)),
            }
        )
    expected_resources = (
        len(config["protocols"]) * len(config["seeds"]) * (1 + int(config["shadow_count"]))
    ) * (1 + len(config["vocab_sizes"]) * len(config["methods"]))
    if len(resource_rows) != expected_resources:
        raise RuntimeError(f"expected {expected_resources} tokenizer profiles, found {len(resource_rows)}")

    write_csv_exclusive(summary_csv, list(rows[0]), rows)
    write_csv_exclusive(resource_csv, list(resource_rows[0]), resource_rows)
    state = strict_json_load(run_root / "run_state.json")
    if state.get("status") != "success":
        raise RuntimeError("pipeline state is not success")
    summary = {
        "schema_version": 1,
        "status": "success",
        "generated_at_utc": utc_now(),
        "expected_attack_results": expected,
        "completed_attack_results": len(rows),
        "expected_tokenizer_profiles": expected_resources,
        "completed_tokenizer_profiles": len(resource_rows),
        "protocols": config["protocols"],
        "seeds": config["seeds"],
        "vocab_sizes": config["vocab_sizes"],
        "methods": config["methods"],
        "attacks": config["attacks"],
        "bootstrap_iterations": config["bootstrap_iterations"],
        "score_direction": "higher_is_more_member",
        "rows": rows,
        "run_state": state,
        "environment": environment_metadata(),
    }
    write_json_exclusive(summary_json, summary)
    logger.info(
        "Gate 2 summary complete rows=%d resources=%d elapsed=%.3fs",
        len(rows),
        len(resource_rows),
        time.perf_counter() - started,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
