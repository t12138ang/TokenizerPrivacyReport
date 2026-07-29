"""Apply the frozen Development-only utility constraint and freeze Main defenses."""

from __future__ import annotations

import argparse
import statistics
from pathlib import Path
from typing import Any

from src.utils.run_metadata import PROJECT_ROOT, environment_metadata, sha256_file, strict_json_load, utc_now, write_json_exclusive


def mean_validation_macro(root: Path, method: str, seeds: list[int]) -> tuple[float, list[float]]:
    values = []
    for seed in seeds:
        result = strict_json_load(root / method / str(seed) / "result.json")
        if result.get("test_used_for_model_selection") is not False:
            raise RuntimeError(f"downstream result does not exclude test-based selection: {method}/{seed}")
        values.append(float(result["best_validation_macro_f1"]))
    return statistics.fmean(values), values


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    output = args.output.resolve()
    if output.exists():
        raise FileExistsError(f"refusing to overwrite frozen Main selection: {output}")
    config_path = args.config.resolve()
    config = strict_json_load(config_path)
    result_root = PROJECT_ROOT / config["results_root"]
    if (result_root / "defenses" / "main").exists():
        raise RuntimeError("Main defense output already exists; Development selection cannot now be frozen")
    shortlist_path = result_root / "defenses" / "development_shortlist.json"
    shortlist = strict_json_load(shortlist_path)
    downstream_state_path = result_root / "downstream" / "development_state.json"
    downstream_state = strict_json_load(downstream_state_path)
    if downstream_state.get("status") != "success":
        raise RuntimeError("Development downstream tasks are incomplete")
    seeds = [int(value) for value in config["seeds"]]
    downstream_root = result_root / "downstream" / "development"
    plain_mean, plain_values = mean_validation_macro(downstream_root, "plain_bpe", seeds)
    evaluated: list[dict[str, Any]] = []
    ranking_by_id = {row["id"]: row for row in shortlist["ranking"]}
    threshold_points = float(config["selection_rule"]["max_macro_f1_drop_points"])
    for mode in ("local_dp", "sa_dp"):
        for candidate in shortlist["shortlist"][mode]:
            macro_mean, raw = mean_validation_macro(downstream_root, candidate["id"], seeds)
            drop_points = 100.0 * (plain_mean - macro_mean)
            evaluated.append({
                **candidate,
                "downstream_validation_macro_f1_by_seed": raw,
                "downstream_validation_macro_f1_mean": macro_mean,
                "validation_macro_f1_drop_points_vs_plain": drop_points,
                "passes_macro_f1_constraint": drop_points <= threshold_points,
                "development_mean_attack_auc": ranking_by_id[candidate["id"]]["mean_attack_auc"],
                "development_c4_mean_token_increase_fraction": ranking_by_id[candidate["id"]]["c4_mean_token_increase_fraction"],
                "cryptographic_work_proxy_K_per_batch": ranking_by_id[candidate["id"]]["cryptographic_work_proxy_K_per_batch"],
            })
    def objective_key(row: dict[str, Any]) -> tuple[float, float, float, float]:
        return (
            row["development_mean_attack_auc"],
            row["development_c4_mean_token_increase_fraction"],
            row["cryptographic_work_proxy_K_per_batch"],
            row["epsilon_total"],
        )

    selected: list[dict[str, Any]] = []
    local_eligible = [
        row for row in evaluated if row["mode"] == "local_dp" and row["passes_macro_f1_constraint"]
    ]
    local_eligible.sort(key=objective_key)
    if not local_eligible:
        raise RuntimeError("frozen selection rule has no eligible local_dp configuration")
    selected.append({**local_eligible[0], "report_role": "local_dp_primary"})

    sa_eligible = [
        row for row in evaluated if row["mode"] == "sa_dp" and row["passes_macro_f1_constraint"]
    ]
    sa_eligible.sort(key=objective_key)
    required_sa = int(config["defense_main_configuration"]["sa_dp_report_count"])
    if len(sa_eligible) < required_sa:
        raise RuntimeError(
            f"frozen selection rule has only {len(sa_eligible)} eligible sa_dp configurations; "
            f"requires {required_sa}"
        )
    primary = sa_eligible[0]
    selected.append({**primary, "report_role": "sa_dp_primary_tradeoff"})
    if required_sa > 1:
        remaining = [row for row in sa_eligible if row["id"] != primary["id"]]
        remaining.sort(key=lambda row: (float(row["epsilon_total"]), *objective_key(row)))
        selected.append({**remaining[0], "report_role": "sa_dp_strong_privacy_comparator"})
    if required_sa > 2:
        already = {row["id"] for row in selected}
        selected.extend(
            {**row, "report_role": "sa_dp_additional"}
            for row in sa_eligible
            if row["id"] not in already
        )
        selected = selected[: 1 + required_sa]
    payload = {
        "schema_version": 1,
        "status": "frozen_before_main_results",
        "created_at_utc": utc_now(),
        "development_only": True,
        "main_results_read": False,
        "config_sha256": sha256_file(config_path),
        "development_shortlist_sha256": sha256_file(shortlist_path),
        "development_downstream_state_sha256": sha256_file(downstream_state_path),
        "selection_metric": "best_validation_macro_f1",
        "test_metrics_read_for_selection": False,
        "plain_validation_macro_f1_by_seed": plain_values,
        "plain_validation_macro_f1_mean": plain_mean,
        "validation_macro_f1_drop_threshold_points": threshold_points,
        "evaluated": evaluated,
        "selected": selected,
        "selection_rule": config["selection_rule"],
        "environment": environment_metadata(),
    }
    write_json_exclusive(output, payload)
    print("status=frozen selected=" + ",".join(row["id"] for row in selected))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
