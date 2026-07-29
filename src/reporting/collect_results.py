"""Collect final result tables and paired website-level bootstrap comparisons."""

from __future__ import annotations

import argparse
import csv
import io
import math
import statistics
import time
from collections import defaultdict
from pathlib import Path
from typing import Any

import numpy as np

from src.attacks.metrics import _fast_scalar_metrics
from src.utils.run_metadata import (
    PROJECT_ROOT,
    canonical_sha256,
    environment_metadata,
    peak_working_set_bytes,
    sha256_file,
    strict_json_load,
    utc_now,
    write_json_exclusive,
    write_text_exclusive,
)


ATTACK_METRICS = (
    "roc_auc", "average_precision", "balanced_accuracy",
    "tpr_at_fpr_le_0_01", "tpr_at_fpr_le_0_001",
)


def write_csv_exclusive(path: Path, rows: list[dict[str, Any]]) -> None:
    if not rows:
        raise ValueError(f"refusing to write empty CSV: {path}")
    buffer = io.StringIO(newline="")
    writer = csv.DictWriter(buffer, fieldnames=list(rows[0]))
    writer.writeheader()
    writer.writerows(rows)
    write_text_exclusive(path, buffer.getvalue())


def sample_std(values: list[float]) -> float:
    return statistics.stdev(values) if len(values) > 1 else 0.0


def assert_finite(value: Any, location: str = "root") -> None:
    if isinstance(value, float) and not math.isfinite(value):
        raise ValueError(f"non-finite value at {location}")
    if isinstance(value, dict):
        for key, child in value.items():
            assert_finite(child, f"{location}.{key}")
    elif isinstance(value, list):
        for index, child in enumerate(value):
            assert_finite(child, f"{location}[{index}]")


def paired_auc_bootstrap(
    baseline: dict[str, Any], candidate: dict[str, Any], *, iterations: int, seed: int
) -> dict[str, float]:
    baseline_by_site = {row["site_id"]: row for row in baseline["details"]}
    candidate_by_site = {row["site_id"]: row for row in candidate["details"]}
    if set(baseline_by_site) != set(candidate_by_site):
        raise RuntimeError("paired attack results contain different website sets")
    sites = sorted(baseline_by_site)
    labels = np.asarray([int(bool(baseline_by_site[site]["is_member"])) for site in sites], dtype=np.int64)
    candidate_labels = np.asarray([int(bool(candidate_by_site[site]["is_member"])) for site in sites], dtype=np.int64)
    if not np.array_equal(labels, candidate_labels):
        raise RuntimeError("paired attack results disagree on membership labels")
    plain_scores = np.asarray([float(baseline_by_site[site]["score"]) for site in sites])
    candidate_scores = np.asarray([float(candidate_by_site[site]["score"]) for site in sites])
    member = np.flatnonzero(labels == 1)
    nonmember = np.flatnonzero(labels == 0)
    rng = np.random.default_rng(seed)
    differences = np.empty(iterations, dtype=np.float64)
    for index in range(iterations):
        draw = np.concatenate((
            rng.choice(member, size=len(member), replace=True),
            rng.choice(nonmember, size=len(nonmember), replace=True),
        ))
        differences[index] = (
            _fast_scalar_metrics(labels[draw], candidate_scores[draw])["roc_auc"]
            - _fast_scalar_metrics(labels[draw], plain_scores[draw])["roc_auc"]
        )
    point = float(candidate["metrics"]["roc_auc"] - baseline["metrics"]["roc_auc"])
    return {
        "candidate_minus_plain_auc": point,
        "bootstrap_mean_difference": float(np.mean(differences)),
        "ci95_lower": float(np.quantile(differences, 0.025)),
        "ci95_upper": float(np.quantile(differences, 0.975)),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument(
        "--fast-report", action="store_true",
        help="accept a documented partial Paillier matrix and consume report_fast measured/extrapolated outputs",
    )
    args = parser.parse_args()
    started = time.perf_counter()
    config = strict_json_load(args.config.resolve())
    result_root = PROJECT_ROOT / config["results_root"]
    table_root = result_root / "tables"
    if table_root.exists() and any(table_root.iterdir()):
        raise FileExistsError(f"final table directory already contains output: {table_root}")
    required_states = {
        "baseline_attacks": result_root / "runs" / "attack_pipeline_state.json",
        "development_defenses": result_root / "defenses" / "development" / "pipeline_state.json",
        "development_downstream": result_root / "downstream" / "development_state.json",
        "main_defenses": result_root / "defenses" / "main" / "pipeline_state.json",
        "main_downstream": result_root / "downstream" / "main_state.json",
        "crypto": result_root / "crypto" / "benchmark_state.json",
    }
    for name, path in required_states.items():
        state = strict_json_load(path)
        if (
            name == "crypto"
            and args.fast_report
            and int(state.get("completed_cells", 0)) > 0
            and int(state.get("failures", 0)) == 0
        ):
            continue
        if state.get("status") != "success":
            raise RuntimeError(f"required stage {name} is not successful: {path}")
    selection = strict_json_load(result_root / "defenses" / "main_selection.json")
    if selection.get("status") != "frozen_before_main_results":
        raise RuntimeError("Main selection is missing or not frozen")
    seeds = [int(value) for value in config["seeds"]]
    vocab = int(config["defense_main_configuration"]["vocab_size"])
    baseline_methods = [row["id"] for row in config["baseline_methods"]]
    private_methods = ["he_only_reference"] + [row["id"] for row in selection["selected"]]
    report_methods = baseline_methods + private_methods
    selected_by_id = {row["id"]: row for row in selection["selected"]}
    parameter_rows: dict[str, dict[str, Any]] = {}
    for row in config["baseline_methods"]:
        parameter_rows[row["id"]] = {
            "mode": row["defense"], "epsilon_total": "", "clipping_percentile": "",
            "batch_merge_size": 1, "candidate_pool_size": "",
            "min_count_threshold": row["min_count_threshold"],
        }
    reference = config["development_search_design"]["reference"]
    parameter_rows["he_only_reference"] = {
        "mode": "he_only", "epsilon_total": "", "clipping_percentile": reference["clipping_percentile"],
        "batch_merge_size": reference["batch_merge_size"], "candidate_pool_size": reference["candidate_pool_size"],
        "min_count_threshold": "",
    }
    for method, row in selected_by_id.items():
        parameter_rows[method] = {
            "mode": row["mode"], "epsilon_total": row["epsilon_total"],
            "clipping_percentile": row["clipping_percentile"], "batch_merge_size": row["batch_merge_size"],
            "candidate_pool_size": row["candidate_pool_size"], "min_count_threshold": "",
        }

    raw_results: dict[tuple[str, int, str], dict[str, Any]] = {}
    attack_rows: list[dict[str, Any]] = []
    for method in report_methods:
        for seed in seeds:
            for attack in config["attacks"]:
                if method in baseline_methods:
                    path = (
                        result_root / "runs" / "attacks" / "main" / str(seed)
                        / f"vocab_{vocab}" / method / f"{attack}.json"
                    )
                else:
                    path = result_root / "defenses" / "main" / "attacks" / method / str(seed) / f"{attack}.json"
                result = strict_json_load(path)
                assert_finite(result, str(path))
                if result.get("status") != "success":
                    raise RuntimeError(f"non-success attack result: {path}")
                raw_results[(method, seed, attack)] = result
                metrics = result["metrics"]
                confidence = metrics["bootstrap"]["confidence_intervals"]
                attack_rows.append({
                    "method_id": method, **parameter_rows[method], "seed": seed, "attack": attack,
                    **{metric: metrics[metric] for metric in ATTACK_METRICS},
                    "roc_auc_ci95_lower": confidence["roc_auc"]["lower"],
                    "roc_auc_ci95_upper": confidence["roc_auc"]["upper"],
                    "site_count": result["data"]["site_count"], "shadow_count": result["shadow_count"],
                    "elapsed_seconds": result["elapsed_seconds"], "peak_memory_bytes": result["peak_memory_bytes"],
                    "source": str(path.relative_to(PROJECT_ROOT)).replace("\\", "/"),
                })
    write_csv_exclusive(table_root / "attack_results.csv", attack_rows)

    aggregate_rows = []
    grouped: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for row in attack_rows:
        grouped[(row["method_id"], row["attack"])].append(row)
    for (method, attack), rows in sorted(grouped.items()):
        aggregate_rows.append({
            "method_id": method, "attack": attack,
            **{
                f"{metric}_{suffix}": function([float(row[metric]) for row in rows])
                for metric in ATTACK_METRICS
                for suffix, function in (("mean", statistics.fmean), ("sample_std", sample_std))
            },
            "seed_count": len(rows),
            "sources": ";".join(row["source"] for row in rows),
        })
    write_csv_exclusive(table_root / "attack_aggregates.csv", aggregate_rows)

    paired_rows = []
    iterations = min(int(config["bootstrap_iterations"]), 2000) if args.fast_report else int(config["bootstrap_iterations"])
    for method in report_methods:
        if method == "plain_bpe":
            continue
        for seed in seeds:
            for attack_index, attack in enumerate(config["attacks"]):
                compared = paired_auc_bootstrap(
                    raw_results[("plain_bpe", seed, attack)], raw_results[(method, seed, attack)],
                    iterations=iterations,
                    seed=seed + attack_index * 1009 + report_methods.index(method) * 100_003,
                )
                paired_rows.append({
                    "method_id": method, "baseline_method_id": "plain_bpe", "seed": seed,
                    "attack": attack, **compared, "bootstrap_iterations": iterations,
                    "resampling_unit": "website; paired and stratified by membership",
                })
    write_csv_exclusive(table_root / "paired_auc_differences.csv", paired_rows)

    utility_rows = []
    for method in report_methods:
        for seed in seeds:
            if method in baseline_methods:
                path = result_root / "defenses" / "main" / "baseline_utility" / method / str(seed) / "utility.json"
            else:
                path = result_root / "defenses" / "main" / "utility" / method / str(seed) / "utility.json"
            result = strict_json_load(path)
            for source, metrics in result["sources"].items():
                utility_rows.append({
                    "method_id": method, "seed": seed, "source_corpus": source,
                    "bytes_per_token": metrics["bytes_per_token"],
                    "characters_per_token": metrics["characters_per_token"],
                    "mean_tokens_per_sentence": metrics["mean_tokens_per_sentence"],
                    "mean_tokens_per_document": metrics["mean_tokens_per_document"],
                    "actual_vocab_size": result["actual_vocab_size"], "merge_count": result["merge_count"],
                    "rare_word_mean_split_length": metrics["rare_word_mean_split_length"],
                    "long_tail_single_token_coverage": metrics["long_tail_single_token_coverage"],
                    "tokenizer_training_seconds": result["tokenizer_training_seconds"],
                    "tokenizer_artifact_bytes": result["tokenizer_artifact_bytes"],
                    "source": str(path.relative_to(PROJECT_ROOT)).replace("\\", "/"),
                })
    write_csv_exclusive(table_root / "tokenizer_utility.csv", utility_rows)

    downstream_rows = []
    for method in report_methods:
        for seed in seeds:
            path = result_root / "downstream" / "main" / method / str(seed) / "result.json"
            result = strict_json_load(path)
            downstream_rows.append({
                "method_id": method, "seed": seed, "accuracy": result["test"]["accuracy"],
                "macro_f1": result["test"]["macro_f1"],
                "class_0_f1": result["test"]["per_class_f1"][0],
                "class_1_f1": result["test"]["per_class_f1"][1],
                "class_2_f1": result["test"]["per_class_f1"][2],
                "class_3_f1": result["test"]["per_class_f1"][3],
                "best_validation_epoch": result["best_validation_epoch"],
                "best_validation_macro_f1": result["best_validation_macro_f1"],
                "test_inference_seconds": result["test_inference_seconds"],
                "mean_sequence_length": result["mean_sequence_length"],
                "test_truncated_fraction": result["test_truncated_fraction"],
                "training_elapsed_seconds": result["training_elapsed_seconds"],
                "peak_gpu_memory_bytes": result["peak_gpu_memory_bytes"],
                "peak_process_memory_bytes": result["peak_process_memory_bytes"],
                "device": result["device"],
                "source": str(path.relative_to(PROJECT_ROOT)).replace("\\", "/"),
            })
    write_csv_exclusive(table_root / "downstream_results.csv", downstream_rows)
    downstream_aggregate = []
    for method in report_methods:
        rows = [row for row in downstream_rows if row["method_id"] == method]
        downstream_aggregate.append({
            "method_id": method,
            "accuracy_mean": statistics.fmean(float(row["accuracy"]) for row in rows),
            "accuracy_sample_std": sample_std([float(row["accuracy"]) for row in rows]),
            "macro_f1_mean": statistics.fmean(float(row["macro_f1"]) for row in rows),
            "macro_f1_sample_std": sample_std([float(row["macro_f1"]) for row in rows]),
            "training_seconds_mean": statistics.fmean(float(row["training_elapsed_seconds"]) for row in rows),
            "seed_count": len(rows), "sources": ";".join(row["source"] for row in rows),
        })
    write_csv_exclusive(table_root / "downstream_aggregates.csv", downstream_aggregate)

    development_plan = strict_json_load(result_root / "defenses" / "development_search_plan.json")
    development_configs = [
        {"id": "private_plain_reference", "mode": "plain", **reference, "epsilon_total": None},
        {"id": "he_only_reference", "mode": "he_only", **reference, "epsilon_total": None},
    ] + list(development_plan["configurations"])
    development_attack_rows = []
    development_utility_rows = []
    for candidate in development_configs:
        for seed in seeds:
            for attack in config["attacks"]:
                path = result_root / "defenses" / "development" / "attacks" / candidate["id"] / str(seed) / f"{attack}.json"
                result = strict_json_load(path)
                development_attack_rows.append({
                    "method_id": candidate["id"], "mode": candidate["mode"],
                    "epsilon_total": "" if candidate["epsilon_total"] is None else candidate["epsilon_total"],
                    "clipping_percentile": candidate["clipping_percentile"],
                    "batch_merge_size": candidate["batch_merge_size"],
                    "candidate_pool_size": candidate["candidate_pool_size"],
                    "seed": seed, "attack": attack,
                    **{metric: result["metrics"][metric] for metric in ATTACK_METRICS},
                    "source": str(path.relative_to(PROJECT_ROOT)).replace("\\", "/"),
                })
            utility_path = result_root / "defenses" / "development" / "utility" / candidate["id"] / str(seed) / "utility.json"
            utility = strict_json_load(utility_path)
            for source_name, metrics in utility["sources"].items():
                development_utility_rows.append({
                    "method_id": candidate["id"], "mode": candidate["mode"],
                    "epsilon_total": "" if candidate["epsilon_total"] is None else candidate["epsilon_total"],
                    "clipping_percentile": candidate["clipping_percentile"],
                    "batch_merge_size": candidate["batch_merge_size"],
                    "candidate_pool_size": candidate["candidate_pool_size"],
                    "seed": seed, "source_corpus": source_name,
                    "bytes_per_token": metrics["bytes_per_token"],
                    "mean_tokens_per_document": metrics["mean_tokens_per_document"],
                    "actual_vocab_size": utility["actual_vocab_size"],
                    "tokenizer_training_seconds": utility["tokenizer_training_seconds"],
                    "source": str(utility_path.relative_to(PROJECT_ROOT)).replace("\\", "/"),
                })
    write_csv_exclusive(table_root / "development_defense_attacks.csv", development_attack_rows)
    write_csv_exclusive(table_root / "development_tokenizer_utility.csv", development_utility_rows)

    development_downstream_rows = []
    development_downstream_plan = strict_json_load(result_root / "downstream" / "development_plan.json")
    for task in development_downstream_plan["tasks"]:
        path = PROJECT_ROOT / task["output_dir"] / "result.json"
        result = strict_json_load(path)
        development_downstream_rows.append({
            "method_id": task["method_id"], "seed": task["seed"],
            "accuracy": result["test"]["accuracy"], "macro_f1": result["test"]["macro_f1"],
            "best_validation_epoch": result["best_validation_epoch"],
            "training_elapsed_seconds": result["training_elapsed_seconds"],
            "source": str(path.relative_to(PROJECT_ROOT)).replace("\\", "/"),
        })
    write_csv_exclusive(table_root / "development_downstream.csv", development_downstream_rows)

    sensitivity_rows = []
    for path in sorted((result_root / "runs" / "shadow_sensitivity").rglob("*.json")):
        result = strict_json_load(path)
        sensitivity_rows.append({
            "attack": result["attack"], "shadow_count": result["shadow_count"],
            "seed": result["seed"], "vocab_size": result["requested_vocab_size"],
            **{metric: result["metrics"][metric] for metric in ATTACK_METRICS},
            "source": str(path.relative_to(PROJECT_ROOT)).replace("\\", "/"),
        })
    sensitivity_seed = int(config["shadow_sensitivity_configuration"]["seed"])
    for attack in ("vocabulary_overlap", "merge_similarity"):
        path = (
            result_root / "runs" / "attacks" / "main" / str(sensitivity_seed)
            / "vocab_16000" / "plain_bpe" / f"{attack}.json"
        )
        result = strict_json_load(path)
        sensitivity_rows.append({
            "attack": result["attack"], "shadow_count": result["shadow_count"],
            "seed": result["seed"], "vocab_size": result["requested_vocab_size"],
            **{metric: result["metrics"][metric] for metric in ATTACK_METRICS},
            "source": str(path.relative_to(PROJECT_ROOT)).replace("\\", "/"),
        })
    write_csv_exclusive(table_root / "shadow_sensitivity.csv", sensitivity_rows)

    if args.fast_report:
        fast_root = result_root / "report_fast"
        with (fast_root / "crypto_combined.csv").open("r", encoding="utf-8-sig", newline="") as handle:
            combined = list(csv.DictReader(handle))
        with (fast_root / "crypto_measured_summary.csv").open("r", encoding="utf-8-sig", newline="") as handle:
            measured_summary = list(csv.DictReader(handle))
        combined_lookup = {
            (int(row["clients"]), int(row["K"]), row["metric"]): row for row in combined
        }
        measured_lookup = {
            (int(row["clients"]), int(row["K"]), row["metric"]): row
            for row in measured_summary if row["clients"] and row["K"]
        }
        crypto_rows = []
        for clients in map(int, config["paillier"]["client_counts"]):
            for dimension in map(int, config["paillier"]["candidate_dimensions"]):
                total = combined_lookup[(clients, dimension, "round_total_seconds")]
                measured = total["data_source"] == "measured"
                total_summary = measured_lookup.get((clients, dimension, "round_total_seconds"), {})
                peak = measured_lookup.get((clients, dimension, "peak_memory_bytes"), {})
                fixed_ciphertext_bytes = 512
                crypto_rows.append({
                    "client_count": clients,
                    "candidate_dimension": dimension,
                    "actual_modulus_bits": config["paillier"]["formal_key_bits"],
                    "round_mean_seconds": total["value"],
                    "round_median_seconds": total_summary.get("median", ""),
                    "round_sample_std_seconds": total_summary.get("sample_standard_deviation", ""),
                    "round_min_seconds": total_summary.get("minimum", ""),
                    "round_max_seconds": total_summary.get("maximum", ""),
                    "round_ci95_lower_seconds": total["lower_bound"],
                    "round_ci95_upper_seconds": total["upper_bound"],
                    "encryption_mean_seconds": combined_lookup[(clients, dimension, "encryption_seconds")]["value"],
                    "aggregation_mean_seconds": combined_lookup[(clients, dimension, "aggregation_seconds")]["value"],
                    "noise_encryption_mean_seconds": combined_lookup[(clients, dimension, "noise_encryption_seconds")]["value"],
                    "decryption_mean_seconds": combined_lookup[(clients, dimension, "decryption_seconds")]["value"],
                    "selection_mean_seconds": combined_lookup[(clients, dimension, "top_b_selection_seconds")]["value"],
                    "single_client_upstream_bytes": fixed_ciphertext_bytes * dimension,
                    "total_client_upstream_bytes": fixed_ciphertext_bytes * dimension * clients,
                    "aggregation_to_decryption_bytes": fixed_ciphertext_bytes * dimension,
                    "returned_merge_id_bytes": 64,
                    "ciphertext_expansion_over_int64": fixed_ciphertext_bytes / 8,
                    "peak_memory_bytes": peak.get("mean", ""),
                    "data_source": total["data_source"],
                    "model_name": total["model_name"],
                    "measured_n": total["measured_n"],
                    "source": "results/final/report_fast/crypto_combined.csv",
                })
    else:
        crypto_source = result_root / "crypto" / "benchmark_summary.csv"
        with crypto_source.open("r", encoding="utf-8-sig", newline="") as handle:
            crypto_rows = list(csv.DictReader(handle))
    write_csv_exclusive(table_root / "crypto_benchmark.csv", crypto_rows)
    full_crypto_path = result_root / "crypto" / "full_tokenizer_summary.json"
    if full_crypto_path.exists():
        full_crypto = strict_json_load(full_crypto_path)
        write_csv_exclusive(table_root / "full_tokenizer_crypto.csv", [{
            "scale": full_crypto["scale"],
            "seed": full_crypto["seed"],
            "requested_vocab_size": full_crypto["requested_vocab_size"],
            "actual_vocab_size": full_crypto["actual_vocab_size"],
            "actual_rounds": full_crypto["actual_rounds"],
            "candidate_pool_size": full_crypto["candidate_pool_size"],
            "actual_modulus_bits": full_crypto["actual_modulus_bits"],
            "worker_processes": full_crypto["worker_processes"],
            "actual_elapsed_seconds": full_crypto["actual_elapsed_seconds"],
            "cleartext_reference_elapsed_seconds": full_crypto["cleartext_reference_elapsed_seconds"],
            "actual_to_cleartext_time_ratio": full_crypto["actual_to_cleartext_time_ratio"],
            "peak_memory_bytes": full_crypto["peak_memory_bytes"],
            "artifact_exact_match": full_crypto["artifact_exact_match"],
            "source": str(full_crypto_path.relative_to(PROJECT_ROOT)).replace("\\", "/"),
        }])

    data_validation = strict_json_load(PROJECT_ROOT / "data" / "final" / "validation.json")
    data_rows = []
    for scale, protocols in data_validation["protocols"].items():
        for seed, row in protocols.items():
            data_rows.append({
                "scale": scale, "seed": seed, "target_site_count": row["target_site_count"],
                "member_site_count": row["member_site_count"], "nonmember_site_count": row["nonmember_site_count"],
                "shadow_auxiliary_site_count": row["shadow_auxiliary_site_count"],
                "public_candidate_site_count": row["public_candidate_site_count"],
                "manifest_sha256": row["manifest_sha256"], "errors": len(row["errors"]),
            })
    write_csv_exclusive(table_root / "dataset_statistics.csv", data_rows)

    source_files = sorted({Path(row["source"]) for row in attack_rows + utility_rows + downstream_rows})
    source_hashes = {
        str(path).replace("\\", "/"): sha256_file(PROJECT_ROOT / path) for path in source_files
    }
    table_files = sorted(table_root.glob("*.csv"))
    registry = []
    destinations = {
        "attack_results": "Table 4; Table 5; Figure 11",
        "attack_aggregates": "Table 4; Table 5",
        "paired_auc_differences": "Table 5",
        "tokenizer_utility": "Table 6; Figure 11",
        "downstream_results": "Table 7; Figure 11",
        "downstream_aggregates": "Table 7",
        "development_defense_attacks": "Figure 6; Figure 12",
        "development_tokenizer_utility": "Figure 7; Figure 12",
        "development_downstream": "Figure 8",
        "shadow_sensitivity": "Figure 5",
        "crypto_benchmark": "Table 8; Figure 9; Figure 10",
        "full_tokenizer_crypto": "Table 8",
        "dataset_statistics": "Table 3",
    }
    for path in table_files:
        with path.open("r", encoding="utf-8-sig", newline="") as handle:
            for index, row in enumerate(csv.DictReader(handle), start=1):
                numeric_fields = []
                for key, value in row.items():
                    try:
                        number = float(value)
                    except (TypeError, ValueError):
                        continue
                    if math.isfinite(number):
                        numeric_fields.append((key, number))
                for metric, value in numeric_fields:
                    registry.append({
                        "result_id": f"{path.stem}:{index}:{metric}",
                        "source": str(path.relative_to(PROJECT_ROOT)).replace("\\", "/"),
                        "method": row.get("method_id", "n/a"),
                        "dataset": row.get("source_corpus", row.get("scale", "main")),
                        "seed": row.get("seed", "aggregate"), "metric": metric, "value": value,
                        "figure_table": destinations[path.stem], "paper_section": "experiments",
                    })
    write_json_exclusive(result_root / "result_registry.json", {
        "schema_version": 1, "status": "success", "generated_at_utc": utc_now(),
        "entry_count": len(registry), "entries": registry,
        "source_hashes": source_hashes,
        "table_hashes": {
            str(path.relative_to(PROJECT_ROOT)).replace("\\", "/"): sha256_file(path) for path in table_files
        },
        "generation_elapsed_seconds": time.perf_counter() - started,
        "peak_memory_bytes": peak_working_set_bytes(), "environment": environment_metadata(),
        "content_sha256": canonical_sha256(registry),
    })
    print(f"status=success tables={len(table_files)} registry_entries={len(registry)} elapsed={time.perf_counter()-started:.3f}s")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
