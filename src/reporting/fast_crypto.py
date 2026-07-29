"""Build a transparent measured/extrapolated Paillier report after a partial matrix cutoff.

This module is deliberately read-only with respect to the formal benchmark artifacts.  It
accepts only complete, successful 2048-bit cell JSON files as measured observations and
writes all derived files under ``results/final/report_fast``.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import math
import statistics
from pathlib import Path
from typing import Any, Callable

import numpy as np

from src.utils.run_metadata import (
    PROJECT_ROOT,
    environment_metadata,
    sha256_file,
    strict_json_load,
    utc_now,
    write_json_exclusive,
    write_text_exclusive,
)


TIMING_METRICS = (
    "encryption_seconds",
    "aggregation_seconds",
    "noise_encryption_seconds",
    "decryption_seconds",
    "top_b_selection_seconds",
    "round_total_seconds",
)
MODEL_SPECS: dict[str, tuple[tuple[str, ...], Callable[[np.ndarray, np.ndarray], np.ndarray], bool]] = {
    "M1": (("intercept", "clients", "K"), lambda c, k: np.column_stack((np.ones_like(c), c, k)), False),
    "M2": (("intercept", "clients_times_K"), lambda c, k: np.column_stack((np.ones_like(c), c * k)), False),
    "M3": (("intercept", "log_clients", "log_K"), lambda c, k: np.column_stack((np.ones_like(c), np.log(c), np.log(k))), True),
    "M4": (("intercept", "K", "clients_times_K"), lambda c, k: np.column_stack((np.ones_like(c), k, c * k)), False),
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--bootstrap-iterations", type=int, default=2000)
    parser.add_argument("--seed", type=int, default=20260729)
    return parser.parse_args()


def csv_text(rows: list[dict[str, Any]], fieldnames: list[str] | None = None) -> str:
    if not rows:
        raise ValueError("refusing to serialize an empty CSV")
    output = io.StringIO(newline="")
    names = fieldnames or list(rows[0])
    writer = csv.DictWriter(output, fieldnames=names, extrasaction="raise")
    writer.writeheader()
    writer.writerows(rows)
    return output.getvalue()


def finite_tree(value: Any) -> bool:
    if isinstance(value, float):
        return math.isfinite(value)
    if isinstance(value, dict):
        return all(finite_tree(child) for child in value.values())
    if isinstance(value, list):
        return all(finite_tree(child) for child in value)
    return True


def bootstrap_mean_ci(values: list[float], iterations: int, seed: int) -> tuple[float, float]:
    array = np.asarray(values, dtype=np.float64)
    if len(array) == 1:
        return float(array[0]), float(array[0])
    rng = np.random.default_rng(seed)
    means = np.empty(iterations, dtype=np.float64)
    for index in range(iterations):
        means[index] = float(np.mean(rng.choice(array, size=len(array), replace=True)))
    return float(np.quantile(means, 0.025)), float(np.quantile(means, 0.975))


def descriptive(values: list[float], iterations: int, seed: int) -> dict[str, float | int]:
    if not values or not all(math.isfinite(value) for value in values):
        raise ValueError("summary input is empty or non-finite")
    lower, upper = bootstrap_mean_ci(values, iterations, seed)
    return {
        "mean": statistics.fmean(values),
        "median": statistics.median(values),
        "sample_standard_deviation": statistics.stdev(values) if len(values) > 1 else 0.0,
        "minimum": min(values),
        "maximum": max(values),
        "ci95_lower": lower,
        "ci95_upper": upper,
        "n": len(values),
    }


def fit_model(name: str, clients: np.ndarray, dimensions: np.ndarray, values: np.ndarray) -> dict[str, Any]:
    labels, design_function, log_target = MODEL_SPECS[name]
    design = design_function(clients, dimensions)
    target = np.log(values) if log_target else values
    coefficients = np.linalg.lstsq(design, target, rcond=None)[0]
    fitted_transformed = design @ coefficients
    fitted = np.exp(fitted_transformed) if log_target else fitted_transformed
    residuals = values - fitted
    sse = max(float(np.sum(residuals**2)), np.finfo(float).tiny)
    n = len(values)
    p = len(coefficients)
    mean_value = float(np.mean(values))
    total_sum_squares = float(np.sum((values - mean_value) ** 2))
    r_squared = 1.0 - sse / total_sum_squares if total_sum_squares > 0 else 1.0
    aic = n * math.log(sse / n) + 2 * p
    bic = n * math.log(sse / n) + p * math.log(n)

    loo_predictions = np.empty(n, dtype=np.float64)
    for held_out in range(n):
        mask = np.arange(n) != held_out
        loo_design = design_function(clients[mask], dimensions[mask])
        loo_target = np.log(values[mask]) if log_target else values[mask]
        loo_coefficients = np.linalg.lstsq(loo_design, loo_target, rcond=None)[0]
        held_design = design_function(clients[held_out:held_out + 1], dimensions[held_out:held_out + 1])
        transformed = float((held_design @ loo_coefficients)[0])
        loo_predictions[held_out] = math.exp(transformed) if log_target else transformed
    absolute_errors = np.abs(values - loo_predictions)
    percentage_errors = absolute_errors / np.maximum(np.abs(values), np.finfo(float).eps)
    full_grid = np.array([(c, k) for c in (2, 4, 8, 16, 32, 64) for k in (128, 512, 1024, 2048, 4096)])
    all_design = design_function(full_grid[:, 0].astype(float), full_grid[:, 1].astype(float))
    all_predictions = all_design @ coefficients
    if log_target:
        all_predictions = np.exp(all_predictions)
    valid = bool(np.all(np.isfinite(all_predictions)) and np.all(all_predictions > 0))
    return {
        "model_name": name,
        "formula": {
            "M1": "T = a + b*clients + c*K",
            "M2": "T = a + b*clients*K",
            "M3": "log(T) = a + b*log(clients) + c*log(K)",
            "M4": "T = a + b*K + c*clients*K",
        }[name],
        "coefficient_names": list(labels),
        "coefficients": [float(value) for value in coefficients],
        "parameter_count": p,
        "log_target": log_target,
        "r_squared": float(r_squared),
        "mae": float(np.mean(np.abs(residuals))),
        "mape_percent": float(100.0 * np.mean(np.abs(residuals) / np.maximum(np.abs(values), np.finfo(float).eps))),
        "loocv_mae": float(np.mean(absolute_errors)),
        "loocv_mape_percent": float(100.0 * np.mean(percentage_errors)),
        "aic": float(aic),
        "bic": float(bic),
        "residual_summary": {
            "mean": float(np.mean(residuals)),
            "sample_standard_deviation": float(np.std(residuals, ddof=1)),
            "minimum": float(np.min(residuals)),
            "maximum": float(np.max(residuals)),
        },
        "valid_positive_predictions_on_frozen_grid": valid,
        "_coefficient_array": coefficients,
        "_residual_array": residuals,
        "_transformed_residual_array": target - fitted_transformed,
    }


def predict(model: dict[str, Any], clients: np.ndarray, dimensions: np.ndarray) -> np.ndarray:
    _, design_function, log_target = MODEL_SPECS[model["model_name"]]
    transformed = design_function(clients, dimensions) @ model["_coefficient_array"]
    return np.exp(transformed) if log_target else transformed


def prediction_interval(
    selected: dict[str, Any], clients: np.ndarray, dimensions: np.ndarray, values: np.ndarray,
    target_clients: float, target_dimension: float, iterations: int, seed: int,
) -> tuple[float, float]:
    name = selected["model_name"]
    _, design_function, log_target = MODEL_SPECS[name]
    rng = np.random.default_rng(seed)
    predictions = np.empty(iterations, dtype=np.float64)
    n = len(values)
    for index in range(iterations):
        sample = rng.choice(np.arange(n), size=n, replace=True)
        design = design_function(clients[sample], dimensions[sample])
        response = np.log(values[sample]) if log_target else values[sample]
        coefficients = np.linalg.lstsq(design, response, rcond=None)[0]
        target_design = design_function(np.asarray([target_clients]), np.asarray([target_dimension]))
        transformed = float((target_design @ coefficients)[0])
        if log_target:
            residual = float(rng.choice(selected["_transformed_residual_array"]))
            predictions[index] = math.exp(transformed + residual)
        else:
            residual = float(rng.choice(selected["_residual_array"]))
            predictions[index] = transformed + residual
    predictions = predictions[np.isfinite(predictions)]
    predictions = predictions[predictions > 0]
    if len(predictions) < iterations * 0.8:
        raise RuntimeError(f"unstable bootstrap prediction interval for {name}")
    return float(np.quantile(predictions, 0.025)), float(np.quantile(predictions, 0.975))


def main() -> int:
    args = parse_args()
    if args.bootstrap_iterations < 500:
        raise ValueError("at least 500 bootstrap iterations are required")
    config_path = args.config.resolve()
    config = strict_json_load(config_path)
    config_hash = sha256_file(config_path)
    paillier = config["paillier"]
    crypto_root = PROJECT_ROOT / config["results_root"] / "crypto"
    output_root = PROJECT_ROOT / config["results_root"] / "report_fast"
    if output_root.exists() and any(output_root.iterdir()):
        raise FileExistsError(f"fast-report output directory is not empty: {output_root}")
    output_root.mkdir(parents=True, exist_ok=True)

    expected = {
        (int(clients), int(dimension))
        for clients in paillier["client_counts"]
        for dimension in paillier["candidate_dimensions"]
    }
    cells: dict[tuple[int, int], tuple[Path, dict[str, Any]]] = {}
    source_hashes: dict[str, str] = {}
    for path in sorted((crypto_root / "cells").glob("clients_*_K_*.json")):
        cell = strict_json_load(path)
        if not finite_tree(cell):
            raise ValueError(f"non-finite formal cell: {path}")
        key = (int(cell["client_count"]), int(cell["candidate_dimension"]))
        if key not in expected or key in cells:
            raise RuntimeError(f"unexpected or duplicate formal cell: {key}")
        if (
            cell.get("status") != "success"
            or not cell.get("formal_real_paillier")
            or cell.get("config_sha256") != config_hash
            or int(cell.get("requested_key_bits", 0)) != int(paillier["formal_key_bits"])
            or int(cell.get("actual_modulus_bits", 0)) < int(paillier["formal_key_bits"])
            or int(cell.get("warmup_repetitions", -1)) != int(paillier["warmup_repetitions"])
            or int(cell.get("measured_repetitions", -1)) != int(paillier["measured_repetitions"])
        ):
            raise RuntimeError(f"invalid or provenance-mismatched formal cell: {path}")
        raw_measurements = cell.get("raw_measurements", [])
        correctness = cell.get("correctness", [])
        if len(raw_measurements) != paillier["measured_repetitions"] or len(correctness) != len(raw_measurements):
            raise RuntimeError(f"incomplete formal repetitions: {path}")
        if not all(row.get("equality") and row.get("max_absolute_error") == 0 and not row.get("overflow_flag") for row in correctness):
            raise AssertionError(f"Paillier correctness failure: {path}")
        cells[key] = (path, cell)
        source_hashes[str(path.relative_to(PROJECT_ROOT)).replace("\\", "/")] = sha256_file(path)
    if len(cells) < 4:
        raise RuntimeError("too few complete benchmark cells for scaling-model fitting")
    missing = sorted(expected - set(cells))

    key_generation_path = crypto_root / "key_generation.json"
    key_generation = strict_json_load(key_generation_path)
    if key_generation.get("status") != "success" or int(key_generation.get("actual_modulus_bits", 0)) < 2048:
        raise RuntimeError("formal 2048-bit key-generation measurement is missing")
    source_hashes[str(key_generation_path.relative_to(PROJECT_ROOT)).replace("\\", "/")] = sha256_file(key_generation_path)
    state_path = crypto_root / "benchmark_state.json"
    log_path = PROJECT_ROOT / "logs" / "final" / "crypto_bench.log"
    cutoff_path = PROJECT_ROOT / "logs" / "final" / "FAST_REPORT_CUTOFF.txt"
    for path in (state_path, log_path, cutoff_path):
        source_hashes[str(path.relative_to(PROJECT_ROOT)).replace("\\", "/")] = sha256_file(path)

    raw_rows: list[dict[str, Any]] = []
    summary_rows: list[dict[str, Any]] = []
    cell_metric_values: dict[str, dict[tuple[int, int], float]] = {metric: {} for metric in TIMING_METRICS}
    measured_summary_lookup: dict[tuple[int, int, str], dict[str, Any]] = {}
    for (clients, dimension), (path, cell) in sorted(cells.items()):
        communication = cell["communication"]
        measurements = cell["raw_measurements"]
        successes = sum(bool(row["equality"]) for row in measurements)
        failures = len(measurements) - successes
        for repetition, row in enumerate(measurements, start=1):
            raw_rows.append({
                "record_type": "benchmark_repetition",
                "clients": clients,
                "K": dimension,
                "key_bits": cell["actual_modulus_bits"],
                "repetition": repetition,
                "key_generation_seconds": "",
                "encryption_seconds": row["encryption_seconds"],
                "aggregation_seconds": row["aggregation_seconds"],
                "noise_encryption_seconds": row["noise_encryption_seconds"],
                "decryption_seconds": row["decryption_seconds"],
                "selection_seconds": row["top_b_selection_seconds"],
                "total_seconds": row["round_total_seconds"],
                "fixed_ciphertext_bytes": row["fixed_ciphertext_bytes"],
                "single_client_upstream_bytes": row["single_client_upstream_bytes"],
                "total_client_upstream_bytes": row["total_client_upstream_bytes"],
                "aggregation_to_decryption_bytes": row["aggregation_to_decryption_bytes"],
                "returned_merge_id_bytes": row["returned_merge_id_bytes"],
                "peak_memory_bytes": cell["peak_memory_bytes"],
                "success": int(bool(row["equality"])),
                "failure": int(not bool(row["equality"])),
                "source": str(path.relative_to(PROJECT_ROOT)).replace("\\", "/"),
            })
        for metric_index, metric in enumerate(TIMING_METRICS):
            values = [float(row[metric]) for row in measurements]
            stats = descriptive(values, args.bootstrap_iterations, args.seed + clients * 100_003 + dimension + metric_index)
            cell_metric_values[metric][(clients, dimension)] = float(stats["mean"])
            summary = {
                "clients": clients,
                "K": dimension,
                "key_bits": cell["actual_modulus_bits"],
                "metric": metric,
                "unit": "seconds",
                **stats,
                "successes": successes,
                "failures": failures,
                "source": str(path.relative_to(PROJECT_ROOT)).replace("\\", "/"),
            }
            summary_rows.append(summary)
            measured_summary_lookup[(clients, dimension, metric)] = summary
        for metric_index, (metric, value, unit) in enumerate((
            ("fixed_ciphertext_bytes", communication["fixed_ciphertext_bytes"], "bytes"),
            ("single_client_upstream_bytes", communication["single_client_upstream_bytes"], "bytes"),
            ("total_client_upstream_bytes", communication["total_client_upstream_bytes"], "bytes"),
            ("aggregation_to_decryption_bytes", communication["aggregation_to_decryption_bytes"], "bytes"),
            ("returned_merge_id_bytes", communication["returned_merge_id_bytes"], "bytes"),
            ("peak_memory_bytes", cell["peak_memory_bytes"], "bytes"),
        )):
            stats = descriptive([float(value)], args.bootstrap_iterations, args.seed + metric_index)
            summary_rows.append({
                "clients": clients, "K": dimension, "key_bits": cell["actual_modulus_bits"],
                "metric": metric, "unit": unit, **stats, "successes": successes,
                "failures": failures, "source": str(path.relative_to(PROJECT_ROOT)).replace("\\", "/"),
            })

    key_values = [float(value) for value in key_generation["raw_seconds"]]
    for repetition, value in enumerate(key_values, start=1):
        raw_rows.append({
            "record_type": "key_generation_repetition", "clients": "", "K": "",
            "key_bits": key_generation["actual_modulus_bits"], "repetition": repetition,
            "key_generation_seconds": value, "encryption_seconds": "", "aggregation_seconds": "",
            "noise_encryption_seconds": "", "decryption_seconds": "", "selection_seconds": "",
            "total_seconds": "", "fixed_ciphertext_bytes": "", "single_client_upstream_bytes": "",
            "total_client_upstream_bytes": "", "aggregation_to_decryption_bytes": "",
            "returned_merge_id_bytes": "", "peak_memory_bytes": "", "success": 1, "failure": 0,
            "source": str(key_generation_path.relative_to(PROJECT_ROOT)).replace("\\", "/"),
        })
    summary_rows.append({
        "clients": "", "K": "", "key_bits": key_generation["actual_modulus_bits"],
        "metric": "key_generation_seconds", "unit": "seconds",
        **descriptive(key_values, args.bootstrap_iterations, args.seed + 9_999_991),
        "successes": len(key_values), "failures": 0,
        "source": str(key_generation_path.relative_to(PROJECT_ROOT)).replace("\\", "/"),
    })

    ordered_keys = sorted(cells)
    training_clients = np.asarray([key[0] for key in ordered_keys], dtype=np.float64)
    training_dimensions = np.asarray([key[1] for key in ordered_keys], dtype=np.float64)
    model_payload: dict[str, Any] = {
        "schema_version": 1,
        "status": "success",
        "generated_at_utc": utc_now(),
        "selection_rule": "minimum LOOCV MAE; among models within 5% of the minimum choose fewer parameters, then lower LOOCV MAE",
        "prediction_interval": "2000 fixed-seed cell bootstrap fits plus residual resampling; widened to include all valid candidate-model point predictions",
        "training_cell_count": len(cells),
        "complete_cells": [{"clients": c, "K": k} for c, k in ordered_keys],
        "missing_cells": [{"clients": c, "K": k} for c, k in missing],
        "metrics": {},
        "bootstrap_iterations": args.bootstrap_iterations,
        "random_seed": args.seed,
        "config_sha256": config_hash,
    }
    extrapolated_rows: list[dict[str, Any]] = []
    combined_rows: list[dict[str, Any]] = []
    for metric_index, metric in enumerate(TIMING_METRICS):
        values = np.asarray([cell_metric_values[metric][key] for key in ordered_keys], dtype=np.float64)
        fitted_models = [fit_model(name, training_clients, training_dimensions, values) for name in MODEL_SPECS]
        valid_models = [row for row in fitted_models if row["valid_positive_predictions_on_frozen_grid"]]
        if not valid_models:
            raise RuntimeError(f"no valid scaling model for {metric}")
        best_mae = min(float(row["loocv_mae"]) for row in valid_models)
        near_best = [row for row in valid_models if float(row["loocv_mae"]) <= best_mae * 1.05]
        selected = min(near_best, key=lambda row: (int(row["parameter_count"]), float(row["loocv_mae"]), row["model_name"]))
        public_models = []
        for row in fitted_models:
            public_models.append({key: value for key, value in row.items() if not key.startswith("_")})
        metric_payload = {
            "selected_model": selected["model_name"],
            "selected_formula": selected["formula"],
            "models": public_models,
            "extrapolations": [],
        }
        for clients, dimension in sorted(expected):
            if (clients, dimension) in cells:
                summary = measured_summary_lookup[(clients, dimension, metric)]
                combined_rows.append({
                    "clients": clients, "K": dimension, "metric": metric,
                    "value": summary["mean"], "lower_bound": summary["ci95_lower"],
                    "upper_bound": summary["ci95_upper"], "data_source": "measured",
                    "model_name": "", "measured_n": summary["n"], "training_cell_count": "",
                })
                continue
            target_clients = np.asarray([float(clients)])
            target_dimension = np.asarray([float(dimension)])
            point = float(predict(selected, target_clients, target_dimension)[0])
            lower, upper = prediction_interval(
                selected, training_clients, training_dimensions, values, float(clients), float(dimension),
                args.bootstrap_iterations, args.seed + metric_index * 1_000_003 + clients * 1009 + dimension,
            )
            candidate_points = [
                float(predict(model, target_clients, target_dimension)[0])
                for model in valid_models
            ]
            lower = max(0.0, min(lower, *candidate_points))
            upper = max(upper, *candidate_points)
            row = {
                "clients": clients, "K": dimension, "metric": metric, "value": point,
                "lower_bound": lower, "upper_bound": upper, "data_source": "extrapolated",
                "model_name": selected["model_name"], "measured_n": 0,
                "training_cell_count": len(cells),
            }
            extrapolated_rows.append(row)
            combined_rows.append(row)
            metric_payload["extrapolations"].append(row)
        model_payload["metrics"][metric] = metric_payload

    if not all(math.isfinite(float(row[field])) for row in combined_rows for field in ("value", "lower_bound", "upper_bound")):
        raise ValueError("non-finite measured or extrapolated output")
    if any(float(row["lower_bound"]) > float(row["value"]) or float(row["value"]) > float(row["upper_bound"]) for row in combined_rows):
        raise AssertionError("prediction or confidence interval does not contain its point estimate")
    if {row["data_source"] for row in combined_rows} != {"measured", "extrapolated"}:
        raise AssertionError("combined output must retain both measured and extrapolated provenance")

    source_payload = {
        "schema_version": 1,
        "generated_at_utc": utc_now(),
        "complete_cell_count": len(cells),
        "source_hashes": dict(sorted(source_hashes.items())),
        "sorted_cell_manifest_sha256": hashlib.sha256(
            ("\n".join(f"{path}\t{digest}" for path, digest in sorted(source_hashes.items()) if "/cells/" in path) + "\n").encode("utf-8")
        ).hexdigest(),
    }
    write_text_exclusive(output_root / "crypto_measured_raw.csv", csv_text(raw_rows))
    write_text_exclusive(output_root / "crypto_measured_summary.csv", csv_text(summary_rows))
    write_json_exclusive(output_root / "crypto_scaling_models.json", model_payload)
    write_text_exclusive(output_root / "crypto_extrapolated.csv", csv_text(extrapolated_rows))
    write_text_exclusive(output_root / "crypto_combined.csv", csv_text(combined_rows))
    write_json_exclusive(output_root / "source_hashes.json", source_payload)
    write_json_exclusive(output_root / "fast_crypto_state.json", {
        "schema_version": 1,
        "status": "success",
        "generated_at_utc": utc_now(),
        "measured_complete_cells": len(cells),
        "expected_cells": len(expected),
        "extrapolated_cells": len(missing),
        "measured_repetition_rows": len(cells) * int(paillier["measured_repetitions"]),
        "key_generation_repetition_rows": len(key_values),
        "failures": 0,
        "missing_combinations": [{"clients": c, "K": k} for c, k in missing],
        "environment": environment_metadata(),
    })
    print(
        f"status=success measured_cells={len(cells)} extrapolated_cells={len(missing)} "
        f"raw_benchmark_rows={len(cells) * int(paillier['measured_repetitions'])} failures=0"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
