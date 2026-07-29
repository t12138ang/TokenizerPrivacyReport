"""Validate and summarize every formal 2048-bit Paillier benchmark cell."""

from __future__ import annotations

import argparse
import csv
import io
import math
from pathlib import Path
from typing import Any

from src.utils.run_metadata import (
    PROJECT_ROOT,
    environment_metadata,
    sha256_file,
    strict_json_load,
    utc_now,
    write_json_exclusive,
    write_text_exclusive,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path, required=True)
    return parser.parse_args()


def finite(value: Any) -> bool:
    if isinstance(value, float):
        return math.isfinite(value)
    if isinstance(value, dict):
        return all(finite(child) for child in value.values())
    if isinstance(value, list):
        return all(finite(child) for child in value)
    return True


def main() -> int:
    args = parse_args()
    config_path = args.config.resolve()
    config = strict_json_load(config_path)
    config_hash = sha256_file(config_path)
    paillier = config["paillier"]
    root = PROJECT_ROOT / config["results_root"] / "crypto"
    smoke = strict_json_load(root / "correctness_smoke_1024.json")
    if smoke.get("status") != "success" or not smoke.get("development_only"):
        raise RuntimeError("1024-bit development smoke missing or mislabeled")
    state = strict_json_load(root / "benchmark_state.json")
    if state.get("status") != "success":
        raise RuntimeError(f"formal benchmark is not successful: {state.get('status')}")
    expected = len(paillier["client_counts"]) * len(paillier["candidate_dimensions"])
    full = strict_json_load(root / "full_tokenizer_summary.json")
    if (
        full.get("status") != "success"
        or not full.get("formal_real_paillier")
        or not full.get("complete_tokenizer_training_measured")
        or full.get("requested_key_bits") != 2048
        or full.get("actual_modulus_bits", 0) < 2048
        or not full.get("artifact_exact_match")
    ):
        raise RuntimeError("actual complete-tokenizer 2048-bit Paillier benchmark failed")
    files = sorted((root / "cells").glob("*.json"))
    if len(files) != expected:
        raise RuntimeError(f"benchmark cell count {len(files)} != {expected}")
    rows = []
    combinations = set()
    for path in files:
        cell = strict_json_load(path)
        if not finite(cell):
            raise ValueError(f"non-finite benchmark value: {path}")
        if cell.get("status") != "success" or not cell.get("formal_real_paillier"):
            raise RuntimeError(f"invalid formal benchmark cell: {path}")
        if cell.get("config_sha256") != config_hash:
            raise RuntimeError(f"benchmark cell/config provenance mismatch: {path}")
        if cell["requested_key_bits"] != 2048 or cell["actual_modulus_bits"] < 2048:
            raise RuntimeError(f"non-formal Paillier modulus: {path}")
        if cell["warmup_repetitions"] != paillier["warmup_repetitions"]:
            raise RuntimeError(f"warmup count mismatch: {path}")
        if cell["measured_repetitions"] != paillier["measured_repetitions"]:
            raise RuntimeError(f"measurement count mismatch: {path}")
        if cell["parallel_worker_processes"] != paillier["parallel_worker_processes"]:
            raise RuntimeError(f"worker-process count mismatch: {path}")
        if len(cell["correctness"]) != paillier["measured_repetitions"]:
            raise RuntimeError(f"correctness repetition mismatch: {path}")
        if not all(item["equality"] and item["max_absolute_error"] == 0 and not item["overflow_flag"] for item in cell["correctness"]):
            raise AssertionError(f"Paillier correctness mismatch: {path}")
        key = (cell["client_count"], cell["candidate_dimension"])
        if key in combinations:
            raise RuntimeError(f"duplicate benchmark combination: {key}")
        combinations.add(key)
        timing = cell["statistics_seconds"]
        communication = cell["communication"]
        rows.append({
            "client_count": key[0], "candidate_dimension": key[1],
            "actual_modulus_bits": cell["actual_modulus_bits"],
            "round_median_seconds": timing["round_total_seconds"]["median"],
            "round_mean_seconds": timing["round_total_seconds"]["mean"],
            "round_sample_std_seconds": timing["round_total_seconds"]["sample_standard_deviation"],
            "round_p95_seconds": timing["round_total_seconds"]["p95"],
            "encryption_median_seconds": timing["encryption_seconds"]["median"],
            "aggregation_median_seconds": timing["aggregation_seconds"]["median"],
            "noise_encryption_median_seconds": timing["noise_encryption_seconds"]["median"],
            "decryption_median_seconds": timing["decryption_seconds"]["median"],
            "selection_median_seconds": timing["top_b_selection_seconds"]["median"],
            "single_client_upstream_bytes": communication["single_client_upstream_bytes"],
            "total_client_upstream_bytes": communication["total_client_upstream_bytes"],
            "aggregation_to_decryption_bytes": communication["aggregation_to_decryption_bytes"],
            "returned_merge_id_bytes": communication["returned_merge_id_bytes"],
            "ciphertext_expansion_over_int64": communication["ciphertext_expansion_over_int64"],
            "peak_memory_bytes": cell["peak_memory_bytes"],
            "source": str(path.relative_to(PROJECT_ROOT)).replace("\\", "/"),
        })
    expected_combinations = {
        (int(clients), int(dimension))
        for clients in paillier["client_counts"]
        for dimension in paillier["candidate_dimensions"]
    }
    if combinations != expected_combinations:
        raise RuntimeError("formal benchmark combination set differs from the frozen matrix")
    csv_path = root / "benchmark_summary.csv"
    buffer = io.StringIO(newline="")
    writer = csv.DictWriter(buffer, fieldnames=list(rows[0]))
    writer.writeheader()
    writer.writerows(sorted(rows, key=lambda row: (row["client_count"], row["candidate_dimension"])))
    write_text_exclusive(csv_path, buffer.getvalue())
    write_json_exclusive(root / "benchmark_summary.json", {
        "schema_version": 1, "status": "success", "generated_at_utc": utc_now(),
        "config_sha256": config_hash,
        "formal_key_bits": 2048, "expected_cells": expected, "actual_cells": len(files),
        "warmup_repetitions_per_cell": paillier["warmup_repetitions"],
        "measured_repetitions_per_cell": paillier["measured_repetitions"],
        "all_plaintext_he_equal": True, "maximum_absolute_error": 0,
        "overflow_count": 0, "failure_count": state["failures"],
        "elapsed_seconds": state.get("elapsed_seconds"),
        "peak_memory_bytes": state.get("peak_memory_bytes"),
        "full_tokenizer_actual_elapsed_seconds": full["actual_elapsed_seconds"],
        "full_tokenizer_actual_to_cleartext_time_ratio": full["actual_to_cleartext_time_ratio"],
        "full_tokenizer_peak_memory_bytes": full["peak_memory_bytes"],
        "full_tokenizer_artifact_exact_match": full["artifact_exact_match"],
        "summary_csv": str(csv_path.relative_to(PROJECT_ROOT)).replace("\\", "/"),
        "summary_csv_sha256": sha256_file(csv_path),
        "environment": environment_metadata(),
    })
    print(f"status=success cells={len(files)}/{expected} modulus=2048-bit errors=0 overflow=0")
    print(f"summary={csv_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
