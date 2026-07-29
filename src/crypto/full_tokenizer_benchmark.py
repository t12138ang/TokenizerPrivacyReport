"""Run one complete real-2048-bit SA-DP-BPE training and verify its artifact."""

from __future__ import annotations

import argparse
import time
import traceback
from pathlib import Path
from typing import Any

from src.tokenizer.private_bpe import train_batched_private_bpe
from src.utils.run_metadata import (
    PROJECT_ROOT,
    environment_metadata,
    peak_working_set_bytes,
    setup_logger,
    sha256_file,
    strict_json_load,
    utc_now,
    write_json_exclusive,
)


def _main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--log", type=Path, required=True)
    args = parser.parse_args()
    config_path = args.config.resolve()
    config = strict_json_load(config_path)
    logger = setup_logger("final.crypto.full_tokenizer", args.log.resolve())
    summary_path = PROJECT_ROOT / config["summary_path"]
    output_dir = PROJECT_ROOT / config["output_dir"]
    scale = str(config["scale"])
    seed = int(config["seed"])
    manifest_path = (
        PROJECT_ROOT / "data" / "final" / "manifests" / scale
        / str(config["protocol"]) / f"seed_{seed}.json"
    )
    config_hash = sha256_file(config_path)
    manifest_hash = strict_json_load(manifest_path)["manifest_sha256"]
    if summary_path.exists():
        prior = strict_json_load(summary_path)
        artifact = PROJECT_ROOT / prior["real_paillier_artifact"]
        reference = PROJECT_ROOT / prior["cleartext_reference_artifact"]
        if (
            prior.get("status") != "success"
            or not prior.get("artifact_exact_match")
            or prior.get("config_sha256") != config_hash
            or prior.get("manifest_sha256") != manifest_hash
        ):
            raise RuntimeError("existing full-tokenizer cryptographic result is not successful")
        if not artifact.is_file() or sha256_file(artifact) != prior["real_paillier_artifact_sha256"]:
            raise RuntimeError("existing full-tokenizer Paillier artifact hash mismatch")
        if not reference.is_file() or sha256_file(reference) != prior["cleartext_reference_artifact_sha256"]:
            raise RuntimeError("existing full-tokenizer cleartext reference hash mismatch")
        print(f"status=success checkpoint_reused=true elapsed_seconds={prior['actual_elapsed_seconds']}")
        return 0
    if int(config["key_bits"]) != 2048:
        raise ValueError("complete-tokenizer formal run requires exactly 2048-bit Paillier")
    clear_dir = (
        PROJECT_ROOT / "results" / "final" / "defenses" / scale / "tokenizers"
        / str(config["cleartext_reference_method_id"]) / str(seed)
    )
    clear_metadata = strict_json_load(clear_dir / "metadata.json")
    if clear_metadata.get("status") != "success" or clear_metadata.get("aggregation_execution") != "protocol_equivalent_cleartext":
        raise RuntimeError("cleartext SA-DP protocol reference is incomplete")
    started = time.perf_counter()

    def progress(record: dict[str, Any]) -> None:
        elapsed = time.perf_counter() - started
        completed = int(record["round"])
        planned = int(record["planned_rounds"])
        rate = completed / elapsed if elapsed else 0.0
        logger.info(
            "stage=full-tokenizer-crypto | scale=%s | protocol=%s | method=%s | attack=n/a | "
            "epsilon=%s | clipping=%s | batch=%s | K=%s | vocab=%s | seed=%s | task=%d/%d | "
            "shadow=n/a | elapsed=%.3fs | eta=%.3fs | successes=%d | failures=0 | log=%s | current_vocab=%s",
            scale, config["protocol"], config["real_method_id"], config["epsilon_total"],
            config["clipping_percentile"], config["batch_merge_size"], config["candidate_pool_size"],
            config["requested_vocab_size"], seed, completed, planned, elapsed,
            (planned - completed) / rate if rate else 0.0, completed, args.log, record["vocab_size"],
        )

    result = train_batched_private_bpe(
        manifest_path=manifest_path,
        output_dir=output_dir,
        mode="sa_dp",
        method_id=str(config["real_method_id"]),
        requested_vocab_size=int(config["requested_vocab_size"]),
        candidate_pool_size=int(config["candidate_pool_size"]),
        clipping_percentile=int(config["clipping_percentile"]),
        batch_size=int(config["batch_merge_size"]),
        epsilon_total=float(config["epsilon_total"]),
        key_bits=int(config["key_bits"]),
        real_paillier=True,
        progress_callback=progress,
        checkpoint_every_rounds=1,
        paillier_worker_processes=int(config["worker_processes"]),
    )
    real_artifact = PROJECT_ROOT / result["artifact"]
    clear_artifact = clear_dir / "tokenizer.json"
    exact = sha256_file(real_artifact) == sha256_file(clear_artifact)
    round_hashes_equal = (
        [row["candidate_pool_hash"] for row in result["rounds"]]
        == [row["candidate_pool_hash"] for row in clear_metadata["rounds"]]
    )
    selected_merges_equal = (
        [row["selected_merges"] for row in result["rounds"]]
        == [row["selected_merges"] for row in clear_metadata["rounds"]]
    )
    if not exact or not round_hashes_equal or not selected_merges_equal:
        raise AssertionError(
            "real Paillier full-tokenizer output differs from the identical-noise cleartext protocol reference"
        )
    actual_elapsed = float(result["elapsed_seconds"])
    clear_elapsed = float(clear_metadata["elapsed_seconds"])
    payload = {
        "schema_version": 1,
        "status": "success",
        "formal_real_paillier": True,
        "complete_tokenizer_training_measured": True,
        "scale": scale,
        "protocol": config["protocol"],
        "seed": seed,
        "requested_vocab_size": config["requested_vocab_size"],
        "actual_vocab_size": result["actual_vocab_size"],
        "actual_rounds": result["actual_rounds"],
        "epsilon_total": config["epsilon_total"],
        "clipping_percentile": config["clipping_percentile"],
        "batch_merge_size": config["batch_merge_size"],
        "candidate_pool_size": config["candidate_pool_size"],
        "requested_key_bits": config["key_bits"],
        "actual_modulus_bits": result["paillier_actual_modulus_bits"],
        "worker_processes": config["worker_processes"],
        "started_at_utc": result["started_at_utc"],
        "actual_elapsed_seconds": actual_elapsed,
        "cleartext_reference_elapsed_seconds": clear_elapsed,
        "actual_to_cleartext_time_ratio": actual_elapsed / clear_elapsed,
        "paillier_key_generation_seconds": result["paillier_key_generation_seconds"],
        "summed_round_crypto": result["crypto_totals"],
        "peak_memory_bytes": result["peak_memory_bytes"],
        "artifact_exact_match": exact,
        "candidate_pool_hashes_exact_match": round_hashes_equal,
        "selected_merges_exact_match": selected_merges_equal,
        "real_paillier_artifact": str(real_artifact.relative_to(PROJECT_ROOT)).replace("\\", "/"),
        "real_paillier_artifact_sha256": sha256_file(real_artifact),
        "cleartext_reference_artifact": str(clear_artifact.relative_to(PROJECT_ROOT)).replace("\\", "/"),
        "cleartext_reference_artifact_sha256": sha256_file(clear_artifact),
        "config_sha256": config_hash,
        "manifest_sha256": manifest_hash,
        "completed_at_utc": utc_now(),
        "environment": environment_metadata(),
        "peak_process_memory_bytes_at_summary": peak_working_set_bytes(),
    }
    write_json_exclusive(summary_path, payload)
    print(
        f"status=success actual_elapsed_seconds={actual_elapsed:.3f} rounds={result['actual_rounds']} "
        f"artifact_exact_match=true peak_memory_bytes={result['peak_memory_bytes']}"
    )
    return 0


def main() -> int:
    try:
        return _main()
    except BaseException as exc:
        failure_path = (
            PROJECT_ROOT / "results" / "final" / "crypto" / "failures"
            / f"full_tokenizer_{utc_now().replace(':', '').replace('+', '_')}.json"
        )
        write_json_exclusive(failure_path, {
            "schema_version": 1,
            "status": "failed",
            "exception_type": type(exc).__name__,
            "message": str(exc),
            "traceback": traceback.format_exc(),
            "failed_at_utc": utc_now(),
            "environment": environment_metadata(),
        })
        raise


if __name__ == "__main__":
    raise SystemExit(main())
