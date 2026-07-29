"""Strict completeness and numeric audit for the final five-attack matrix."""

from __future__ import annotations

import argparse
import csv
import math
from pathlib import Path
from typing import Any

from src.attacks.metrics import POSITIVE_CLASS, SCORE_DIRECTION
from src.utils.run_metadata import PROJECT_ROOT, strict_json_load, utc_now, write_json_exclusive


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path, required=True)
    return parser.parse_args()


def assert_finite(value: Any, path: str = "root") -> None:
    if isinstance(value, float) and not math.isfinite(value):
        raise ValueError(f"non-finite value at {path}")
    if isinstance(value, dict):
        for key, child in value.items():
            assert_finite(child, f"{path}.{key}")
    elif isinstance(value, list):
        for index, child in enumerate(value):
            assert_finite(child, f"{path}[{index}]")


def main() -> int:
    args = parse_args()
    config = strict_json_load(args.config.resolve())
    root = PROJECT_ROOT / config["results_root"]
    run_root = root / "runs"
    state = strict_json_load(run_root / "attack_pipeline_state.json")
    if state.get("status") != "success":
        raise RuntimeError(f"attack pipeline state is not success: {state.get('status')}")
    expected_standard = 2 * len(config["seeds"]) * len(config["vocab_sizes"]) * len(config["baseline_methods"]) * len(config["attacks"])
    expected_sensitivity = (len(config["shadow_sensitivity_counts"]) - 1) * 2
    files = sorted((run_root / "attacks").rglob("*.json"))
    sensitivity_files = sorted((run_root / "shadow_sensitivity").rglob("*.json"))
    if len(files) != expected_standard:
        raise RuntimeError(f"standard attack result count {len(files)} != {expected_standard}")
    if len(sensitivity_files) != expected_sensitivity:
        raise RuntimeError(f"sensitivity result count {len(sensitivity_files)} != {expected_sensitivity}")
    rows = []
    keys = set()
    for path in files + sensitivity_files:
        result = strict_json_load(path)
        assert_finite(result)
        if result.get("status") != "success":
            raise RuntimeError(f"non-success result: {path}")
        metrics = result["metrics"]
        if metrics.get("positive_class") != POSITIVE_CLASS or metrics.get("score_direction") != SCORE_DIRECTION:
            raise RuntimeError(f"metric class/direction mismatch: {path}")
        if len(result["details"]) != result["data"]["site_count"]:
            raise RuntimeError(f"detail cardinality mismatch: {path}")
        key = (result["scale"], result["seed"], result["requested_vocab_size"],
               result["method_id"], result["attack"], result["shadow_count"])
        if key in keys:
            raise RuntimeError(f"duplicate attack combination: {key}")
        keys.add(key)
        rows.append({
            "scale": result["scale"], "seed": result["seed"],
            "vocab_size": result["requested_vocab_size"], "actual_vocab_size": result["actual_vocab_size"],
            "method_id": result["method_id"], "attack": result["attack"],
            "shadow_count": result["shadow_count"], "roc_auc": metrics["roc_auc"],
            "average_precision": metrics["average_precision"],
            "balanced_accuracy": metrics["balanced_accuracy"],
            "tpr_at_fpr_le_0_01": metrics["tpr_at_fpr_le_0_01"],
            "tpr_at_fpr_le_0_001": metrics["tpr_at_fpr_le_0_001"],
            "elapsed_seconds": result["elapsed_seconds"], "peak_memory_bytes": result["peak_memory_bytes"],
            "source": str(path.relative_to(PROJECT_ROOT)).replace("\\", "/"),
        })
    csv_path = root / "attack_summary.csv"
    if csv_path.exists():
        raise FileExistsError(f"refusing to overwrite summary: {csv_path}")
    csv_path.parent.mkdir(parents=True, exist_ok=True)
    with csv_path.open("x", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    summary = {
        "schema_version": 1, "status": "success", "generated_at_utc": utc_now(),
        "expected_standard_results": expected_standard, "actual_standard_results": len(files),
        "expected_sensitivity_results": expected_sensitivity, "actual_sensitivity_results": len(sensitivity_files),
        "unique_combination_count": len(keys), "failure_count": state["failures"],
        "pipeline_peak_memory_bytes": state.get("peak_memory_bytes"),
        "pipeline_elapsed_seconds": state.get("accumulated_elapsed_seconds"),
        "summary_csv": str(csv_path.relative_to(PROJECT_ROOT)).replace("\\", "/"),
    }
    write_json_exclusive(root / "attack_summary.json", summary)
    print(f"status=success standard={len(files)}/{expected_standard} sensitivity={len(sensitivity_files)}/{expected_sensitivity}")
    print(f"failures={state['failures']} peak_memory_bytes={state.get('peak_memory_bytes')} elapsed_seconds={state.get('accumulated_elapsed_seconds')}")
    print(f"summary={csv_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
