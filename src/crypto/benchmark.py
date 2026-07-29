"""Checkpointed real-Paillier correctness and performance benchmark matrix."""

from __future__ import annotations

import argparse
import os
import statistics
import time
import traceback
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path
from typing import Any

import numpy as np

from src.crypto.paillier_aggregation import (
    AggregationServer,
    DataClient,
    DecryptionSelectionServer,
    ProtocolKeys,
    generate_keys,
    plaintext_aggregate,
)
from src.privacy.discrete_laplace import two_sided_geometric
from src.tokenizer.private_bpe import compatible_top_indices
from src.utils.run_metadata import (
    PROJECT_ROOT,
    environment_metadata,
    peak_working_set_bytes,
    setup_logger,
    sha256_file,
    strict_json_load,
    utc_now,
    write_json_atomic_replace,
    write_json_exclusive,
)


def atomic_state(path: Path, payload: dict[str, Any]) -> None:
    write_json_atomic_replace(path, payload)


def distribution(values: list[float]) -> dict[str, float]:
    return {
        "median": float(statistics.median(values)),
        "mean": float(statistics.fmean(values)),
        "sample_standard_deviation": float(statistics.stdev(values)) if len(values) > 1 else 0.0,
        "p95": float(np.percentile(np.asarray(values), 95)),
    }


def encrypt_vector_worker(arguments: tuple[Any, list[int]]) -> tuple[list[Any], float]:
    public_key, vector = arguments
    started = time.perf_counter()
    result = DataClient(public_key).encrypt_vector(vector)
    return result, time.perf_counter() - started


def decrypt_vector_worker(arguments: tuple[Any, list[Any]]) -> tuple[list[int], float]:
    private_key, vector = arguments
    started = time.perf_counter()
    result = DecryptionSelectionServer(private_key).decrypt_aggregate(vector)
    return result, time.perf_counter() - started


def chunks(values: list[Any], count: int) -> list[list[Any]]:
    count = max(1, min(count, len(values)))
    return [values[index::count] for index in range(count)]


def run_once(
    keys: ProtocolKeys,
    *,
    clients: int,
    dimension: int,
    seed: int,
    batch_size: int,
    executor: ProcessPoolExecutor | None = None,
    worker_processes: int = 1,
) -> dict[str, Any]:
    rng = np.random.default_rng(seed)
    vectors = rng.integers(0, 11, size=(clients, dimension), dtype=np.int64).tolist()
    noise = list(map(int, two_sided_geometric(
        epsilon=1.0, sensitivity=64, size=dimension, rng=rng
    )))
    client = DataClient(keys.public_key)
    server_a = AggregationServer(keys.public_key)
    server_d = DecryptionSelectionServer(keys.private_key)
    round_started = time.perf_counter()

    phase = time.perf_counter()
    if executor is None:
        encrypted_with_times = [(client.encrypt_vector(vector), 0.0) for vector in vectors]
        client_durations = []
    else:
        encrypted_with_times = list(executor.map(
            encrypt_vector_worker, [(keys.public_key, vector) for vector in vectors]
        ))
        client_durations = [duration for _, duration in encrypted_with_times]
    encrypted_vectors = [vector for vector, _ in encrypted_with_times]
    encryption_seconds = time.perf_counter() - phase

    phase = time.perf_counter()
    encrypted_aggregate = server_a.aggregate(encrypted_vectors)
    aggregation_seconds = time.perf_counter() - phase

    phase = time.perf_counter()
    if executor is None:
        noisy_aggregate = server_a.add_encrypted_noise(encrypted_aggregate, noise)
        noise_cpu_seconds = 0.0
    else:
        noise_chunks = chunks(noise, worker_processes)
        encrypted_noise_parts = list(executor.map(
            encrypt_vector_worker, [(keys.public_key, part) for part in noise_chunks]
        ))
        encrypted_noise = [None] * dimension
        for part_index, (part, _) in enumerate(encrypted_noise_parts):
            for position, value in zip(range(part_index, dimension, len(noise_chunks)), part):
                encrypted_noise[position] = value
        noisy_aggregate = [left + right for left, right in zip(encrypted_aggregate, encrypted_noise)]
        noise_cpu_seconds = sum(duration for _, duration in encrypted_noise_parts)
    noise_encryption_seconds = time.perf_counter() - phase

    phase = time.perf_counter()
    if executor is None:
        decrypted = server_d.decrypt_aggregate(noisy_aggregate)
        decryption_cpu_seconds = 0.0
    else:
        encrypted_chunks = chunks(list(noisy_aggregate), worker_processes)
        decrypted_parts = list(executor.map(
            decrypt_vector_worker, [(keys.private_key, part) for part in encrypted_chunks]
        ))
        decrypted = [0] * dimension
        for part_index, (part, _) in enumerate(decrypted_parts):
            for position, value in zip(range(part_index, dimension, len(encrypted_chunks)), part):
                decrypted[position] = value
        decryption_cpu_seconds = sum(duration for _, duration in decrypted_parts)
    decryption_seconds = time.perf_counter() - phase

    phase = time.perf_counter()
    selected = compatible_top_indices(
        decrypted,
        [(f"L{index}", f"R{index}") for index in range(dimension)],
        min(batch_size, dimension),
    )
    selection_seconds = time.perf_counter() - phase
    round_seconds = time.perf_counter() - round_started

    expected_plain = [
        value + delta for value, delta in zip(plaintext_aggregate(vectors), noise)
    ]
    errors = [abs(left - right) for left, right in zip(decrypted, expected_plain)]
    fixed_ciphertext_bytes = (keys.public_key.nsquare.bit_length() + 7) // 8
    observed_ciphertext_bits = encrypted_vectors[0][0].ciphertext(be_secure=False).bit_length()
    maximum_absolute_plaintext = max(map(abs, expected_plain), default=0)
    overflow = maximum_absolute_plaintext > keys.public_key.max_int
    return {
        "encryption_seconds": encryption_seconds,
        "client_encryption_cpu_seconds": sum(client_durations),
        "single_count_encryption_seconds": (
            sum(client_durations) / (clients * dimension) if client_durations
            else encryption_seconds / (clients * dimension)
        ),
        "single_client_vector_encryption_seconds": (
            statistics.fmean(client_durations) if client_durations else encryption_seconds / clients
        ),
        "aggregation_seconds": aggregation_seconds,
        "noise_encryption_seconds": noise_encryption_seconds,
        "noise_encryption_cpu_seconds": noise_cpu_seconds,
        "decryption_seconds": decryption_seconds,
        "decryption_cpu_seconds": decryption_cpu_seconds,
        "single_count_decryption_seconds": (
            decryption_cpu_seconds / dimension if decryption_cpu_seconds else decryption_seconds / dimension
        ),
        "top_b_selection_seconds": selection_seconds,
        "round_total_seconds": round_seconds,
        "plaintext_aggregate": expected_plain,
        "decrypted_he_aggregate": decrypted,
        "equality": decrypted == expected_plain,
        "max_absolute_error": max(errors, default=0),
        "overflow_flag": overflow,
        "maximum_absolute_plaintext": maximum_absolute_plaintext,
        "paillier_max_int": keys.public_key.max_int,
        "selected_merge_ids": selected,
        "observed_ciphertext_bits": observed_ciphertext_bits,
        "fixed_ciphertext_bytes": fixed_ciphertext_bytes,
        "single_client_upstream_bytes": dimension * fixed_ciphertext_bytes,
        "total_client_upstream_bytes": clients * dimension * fixed_ciphertext_bytes,
        "aggregation_to_decryption_bytes": dimension * fixed_ciphertext_bytes,
        "returned_merge_id_bytes": len(selected) * (((dimension - 1).bit_length() + 7) // 8),
        "ciphertext_expansion_over_int64": fixed_ciphertext_bytes / 8.0,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--log", type=Path, required=True)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    config_path = args.config.resolve()
    config = strict_json_load(config_path)
    crypto = config["paillier"]
    logger = setup_logger("final.crypto", args.log.resolve())
    root = PROJECT_ROOT / config["results_root"] / "crypto"
    cells = [
        (int(clients), int(dimension))
        for clients in crypto["client_counts"]
        for dimension in crypto["candidate_dimensions"]
    ]
    state_path = root / "benchmark_state.json"
    config_hash = sha256_file(config_path)
    started = time.perf_counter()
    if state_path.exists():
        state = strict_json_load(state_path)
        if state.get("config_sha256") != config_hash:
            raise RuntimeError("crypto benchmark config hash mismatch")
        if state.get("status") == "success":
            logger.info("crypto benchmark already complete; no result overwritten")
            return 0
    else:
        state = {
            "schema_version": 1,
            "status": "running",
            "created_at_utc": utc_now(),
            "config_sha256": config_hash,
            "expected_cells": len(cells),
            "completed_cells": 0,
            "failures": 0,
            "environment": environment_metadata(),
        }
        atomic_state(state_path, state)

    warmups = int(crypto["warmup_repetitions"])
    repetitions = int(crypto["measured_repetitions"])
    key_bits = int(crypto["formal_key_bits"])
    worker_processes = int(crypto["parallel_worker_processes"])
    key_times = []
    keys: ProtocolKeys | None = None
    key_generation_path = root / "key_generation.json"
    if not key_generation_path.exists():
        key_generation_started_at = utc_now()
        for index in range(warmups + repetitions):
            phase = time.perf_counter()
            generated = generate_keys(key_bits)
            duration = time.perf_counter() - phase
            if index >= warmups:
                key_times.append(duration)
            keys = generated
        assert keys is not None
        write_json_exclusive(key_generation_path, {
            "schema_version": 1,
            "status": "success",
            "started_at_utc": key_generation_started_at,
            "completed_at_utc": utc_now(),
            "config_sha256": config_hash,
            "requested_key_bits": key_bits,
            "actual_modulus_bits": keys.actual_modulus_bits,
            "warmup_repetitions": warmups,
            "measured_repetitions": repetitions,
            "raw_seconds": key_times,
            "statistics_seconds": distribution(key_times),
            "environment": environment_metadata(),
        })
    else:
        # Keys are intentionally not serialized; resumption creates a fresh formal key.
        key_generation = strict_json_load(key_generation_path)
        if (
            key_generation.get("status") != "success"
            or key_generation.get("config_sha256") != config_hash
            or int(key_generation.get("requested_key_bits", -1)) != key_bits
            or int(key_generation.get("warmup_repetitions", -1)) != warmups
            or int(key_generation.get("measured_repetitions", -1)) != repetitions
        ):
            raise RuntimeError("stale or mismatched key-generation benchmark result")
        keys = generate_keys(key_bits)

    completed = int(state.get("completed_cells", 0))
    try:
        with ProcessPoolExecutor(max_workers=worker_processes) as executor:
          for cell_index, (clients, dimension) in enumerate(cells):
            output = root / "cells" / f"clients_{clients}_K_{dimension}.json"
            if output.exists():
                existing = strict_json_load(output)
                if (
                    existing.get("status") != "success"
                    or existing.get("config_sha256") != config_hash
                    or int(existing.get("client_count", -1)) != clients
                    or int(existing.get("candidate_dimension", -1)) != dimension
                    or int(existing.get("requested_key_bits", -1)) != key_bits
                    or int(existing.get("warmup_repetitions", -1)) != warmups
                    or int(existing.get("measured_repetitions", -1)) != repetitions
                    or int(existing.get("parallel_worker_processes", -1)) != worker_processes
                ):
                    raise RuntimeError(f"stale, mismatched, or non-success benchmark cell: {output}")
                continue
            cell_started_at = utc_now()
            raw = []
            for repetition in range(warmups + repetitions):
                record = run_once(
                    keys,
                    clients=clients,
                    dimension=dimension,
                    seed=20260726 + cell_index * 1000 + repetition,
                    batch_size=32,
                    executor=executor,
                    worker_processes=worker_processes,
                )
                if not record["equality"] or record["max_absolute_error"] != 0 or record["overflow_flag"]:
                    raise AssertionError(f"Paillier correctness failure clients={clients}, K={dimension}")
                if repetition >= warmups:
                    raw.append(record)
                logger.info(
                    "stage=crypto-benchmark | scale=matrix | protocol=two-server-paillier | method=SA-DP-BPE | "
                    "attack=n/a | epsilon=1 | clipping=64 | batch=32 | vocab=n/a | seed=%d | task=%d/%d | "
                    "shadow=n/a | elapsed=%.3fs | eta=n/a | successes=%d | failures=%d | log=%s | "
                    "clients=%d K=%d repetition=%d/%d warmup=%s",
                    20260726 + cell_index * 1000 + repetition, cell_index + 1, len(cells),
                    time.perf_counter() - started, completed, state["failures"], args.log,
                    clients, dimension, repetition + 1, warmups + repetitions, repetition < warmups,
                )
            timing_names = [
                "encryption_seconds", "single_count_encryption_seconds",
                "single_client_vector_encryption_seconds", "client_encryption_cpu_seconds",
                "aggregation_seconds", "noise_encryption_seconds", "noise_encryption_cpu_seconds",
                "decryption_seconds", "decryption_cpu_seconds",
                "single_count_decryption_seconds", "top_b_selection_seconds", "round_total_seconds",
            ]
            result = {
                "schema_version": 1,
                "status": "success",
                "started_at_utc": cell_started_at,
                "config_sha256": config_hash,
                "formal_real_paillier": True,
                "requested_key_bits": key_bits,
                "actual_modulus_bits": keys.actual_modulus_bits,
                "client_count": clients,
                "candidate_dimension": dimension,
                "warmup_repetitions": warmups,
                "measured_repetitions": repetitions,
                "parallel_worker_processes": worker_processes,
                "statistics_seconds": {
                    name: distribution([float(record[name]) for record in raw]) for name in timing_names
                },
                "communication": {
                    key: raw[0][key] for key in (
                        "fixed_ciphertext_bytes", "single_client_upstream_bytes",
                        "total_client_upstream_bytes", "aggregation_to_decryption_bytes",
                        "returned_merge_id_bytes", "ciphertext_expansion_over_int64",
                    )
                },
                "correctness": [
                    {
                        "plaintext_aggregate": record["plaintext_aggregate"],
                        "decrypted_he_aggregate": record["decrypted_he_aggregate"],
                        "equality": record["equality"],
                        "max_absolute_error": record["max_absolute_error"],
                        "overflow_flag": record["overflow_flag"],
                        "maximum_absolute_plaintext": record["maximum_absolute_plaintext"],
                        "paillier_max_int": record["paillier_max_int"],
                    }
                    for record in raw
                ],
                "raw_measurements": [
                    {key: value for key, value in record.items()
                     if key not in {"plaintext_aggregate", "decrypted_he_aggregate"}}
                    for record in raw
                ],
                "peak_memory_bytes": peak_working_set_bytes(),
                "completed_at_utc": utc_now(),
                "environment": environment_metadata(),
            }
            write_json_exclusive(output, result)
            completed += 1
            state.update({
                "completed_cells": completed,
                "updated_at_utc": utc_now(),
                "peak_memory_bytes": max(int(state.get("peak_memory_bytes", 0)), int(peak_working_set_bytes() or 0)),
                "elapsed_seconds": time.perf_counter() - started,
            })
            atomic_state(state_path, state)
        state.update({"status": "success", "completed_cells": len(cells),
                      "completed_at_utc": utc_now(), "elapsed_seconds": time.perf_counter() - started})
        atomic_state(state_path, state)
        return 0
    except BaseException as exc:
        failure_path = root / "failures" / f"failure_{utc_now().replace(':', '').replace('+', '_')}.json"
        write_json_exclusive(failure_path, {
            "schema_version": 1,
            "status": "failed",
            "exception_type": type(exc).__name__,
            "message": str(exc),
            "traceback": traceback.format_exc(),
            "failed_at_utc": utc_now(),
            "environment": environment_metadata(),
        })
        state.update({"status": "failed", "failures": int(state.get("failures", 0)) + 1,
                      "updated_at_utc": utc_now(), "error": f"{type(exc).__name__}: {exc}",
                      "last_failure": str(failure_path.relative_to(PROJECT_ROOT)).replace("\\", "/")})
        atomic_state(state_path, state)
        logger.exception("crypto benchmark failed")
        raise


if __name__ == "__main__":
    raise SystemExit(main())
