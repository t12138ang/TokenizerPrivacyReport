"""Run frozen Main private-BPE defenses without using Main results for selection."""

from __future__ import annotations

import argparse
import os
import time
import traceback
from pathlib import Path
from typing import Any

from src.attacks.final_attack import run_final_attack
from src.evaluation.tokenizer_utility import evaluate_tokenizer_utility
from src.tokenizer.private_bpe import train_batched_private_bpe
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


def baseline_model(root: Path, seed: int, vocab: int, role: str) -> Path:
    return root / "runs" / "tokenizers" / "main" / str(seed) / f"vocab_{vocab}" / "plain_bpe" / role


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--selection", type=Path, required=True)
    parser.add_argument("--log", type=Path, required=True)
    args = parser.parse_args()
    config_path = args.config.resolve()
    selection_path = args.selection.resolve()
    config = strict_json_load(config_path)
    selection = strict_json_load(selection_path)
    if selection.get("status") != "frozen_before_main_results" or selection.get("main_results_read") is not False:
        raise RuntimeError("Main selection was not frozen exclusively from Development results")
    if selection.get("config_sha256") != sha256_file(config_path):
        raise RuntimeError("Main selection/config hash mismatch")
    result_root = PROJECT_ROOT / config["results_root"]
    baseline_state = strict_json_load(result_root / "runs" / "attack_pipeline_state.json")
    if baseline_state.get("status") != "success":
        raise RuntimeError("baseline attack pipeline must complete before Main defenses")
    downstream_data = strict_json_load(result_root / "downstream" / "ag_news_data.json")
    ag_news_test_path = PROJECT_ROOT / downstream_data["splits"]["test"]["path"]
    defense_root = result_root / "defenses" / "main"
    state_path = defense_root / "pipeline_state.json"
    logger = setup_logger("final.defenses.main", args.log.resolve())
    seeds = [int(value) for value in config["seeds"]]
    vocab = int(config["defense_main_configuration"]["vocab_size"])
    reference = config["development_search_design"]["reference"]
    configurations = [
        {"id": "private_plain_reference", "mode": "plain", **reference, "epsilon_total": None},
        {"id": "he_only_reference", "mode": "he_only", **reference, "epsilon_total": None},
    ] + [
        {
            key: row[key]
            for key in (
                "id", "mode", "epsilon_total", "clipping_percentile",
                "batch_merge_size", "candidate_pool_size",
            )
        }
        for row in selection["selected"]
    ]
    attacks = list(config["attacks"])
    expected = (
        len(configurations) * len(seeds) * (1 + len(attacks) + 1)
        + len(config["baseline_methods"]) * len(seeds)
    )
    if state_path.exists():
        state = strict_json_load(state_path)
        if state.get("selection_sha256") != sha256_file(selection_path):
            raise RuntimeError("Main defense state/selection hash mismatch")
        if state.get("status") == "success":
            logger.info("Main defenses already successful; no outputs overwritten")
            return 0
    else:
        state = {
            "schema_version": 1,
            "status": "running",
            "created_at_utc": utc_now(),
            "config_sha256": sha256_file(config_path),
            "selection_sha256": sha256_file(selection_path),
            "expected_tasks": expected,
            "completed_tasks": 0,
            "failures": 0,
            "environment": environment_metadata(),
        }
        atomic_state(state_path, state)
    completed = int(state.get("completed_tasks", 0))
    started = time.perf_counter()

    def finish(result: dict[str, Any], *, stage: str, method: str, seed: int, attack: str = "n/a", **parameters: Any) -> None:
        nonlocal completed
        if not result.get("checkpoint_reused"):
            completed += 1
        elapsed = time.perf_counter() - started
        rate = completed / elapsed if elapsed else 0.0
        logger.info(
            "stage=%s | scale=main | protocol=strict_disjoint | method=%s | attack=%s | "
            "epsilon=%s | clipping=%s | batch=%s | K=%s | vocab=%d | seed=%d | task=%d/%d | "
            "shadow=%s | elapsed=%.3fs | eta=%.3fs | successes=%d | failures=%d | log=%s",
            stage, method, attack, parameters.get("epsilon", "n/a"), parameters.get("clipping", "n/a"),
            parameters.get("batch", "n/a"), parameters.get("candidate_pool", "n/a"), vocab, seed,
            completed, expected, parameters.get("shadow", "n/a"), elapsed,
            (expected - completed) / rate if rate else 0.0, completed, state["failures"], args.log,
        )
        peak = int(peak_working_set_bytes() or 0)
        if peak > int(config["max_peak_memory_bytes"]):
            raise MemoryError(f"Main defense pipeline peak memory {peak} exceeds safety limit")
        state.update({
            "completed_tasks": completed,
            "updated_at_utc": utc_now(),
            "elapsed_seconds": elapsed,
            "peak_memory_bytes": max(peak, int(state.get("peak_memory_bytes", 0))),
        })
        atomic_state(state_path, state)

    try:
        for method in config["baseline_methods"]:
            for seed in seeds:
                manifest = (
                    PROJECT_ROOT / "data" / "final" / "manifests" / "main"
                    / "strict_disjoint" / f"seed_{seed}.json"
                )
                tokenizer_dir = (
                    result_root / "runs" / "tokenizers" / "main" / str(seed)
                    / f"vocab_{vocab}" / method["id"] / "target"
                )
                utility = evaluate_tokenizer_utility(
                    tokenizer_dir=tokenizer_dir,
                    manifest_path=manifest,
                    output_path=defense_root / "baseline_utility" / method["id"] / str(seed) / "utility.json",
                    ag_news_test_path=ag_news_test_path,
                )
                finish(utility, stage="utility", method=method["id"], seed=seed)

        for candidate in configurations:
            for seed in seeds:
                manifest = (
                    PROJECT_ROOT / "data" / "final" / "manifests" / "main"
                    / "strict_disjoint" / f"seed_{seed}.json"
                )
                directory = defense_root / "tokenizers" / candidate["id"] / str(seed)

                def round_progress(record: dict[str, Any], candidate=candidate, seed=seed) -> None:
                    elapsed = time.perf_counter() - started
                    logger.info(
                        "stage=private-bpe-round | scale=main | protocol=strict_disjoint | method=%s | "
                        "attack=n/a | epsilon=%s | clipping=%s | batch=%s | K=%s | vocab=%d | seed=%d | "
                        "task=%d/%d | shadow=n/a | elapsed=%.3fs | eta=n/a | successes=%d | failures=%d | "
                        "round=%s/%s | current_vocab=%s | log=%s",
                        candidate["id"], candidate["epsilon_total"], candidate["clipping_percentile"],
                        candidate["batch_merge_size"], candidate["candidate_pool_size"], vocab, seed,
                        completed, expected, elapsed, completed, state["failures"], record.get("round"),
                        record.get("planned_rounds"), record.get("vocab_size"), args.log,
                    )

                token_result = train_batched_private_bpe(
                    manifest_path=manifest,
                    output_dir=directory,
                    mode=candidate["mode"],
                    method_id=candidate["id"],
                    requested_vocab_size=vocab,
                    candidate_pool_size=int(candidate["candidate_pool_size"]),
                    clipping_percentile=int(candidate["clipping_percentile"]),
                    batch_size=int(candidate["batch_merge_size"]),
                    epsilon_total=(float(candidate["epsilon_total"]) if candidate["epsilon_total"] is not None else None),
                    key_bits=2048,
                    real_paillier=False,
                    progress_callback=round_progress,
                    checkpoint_every_rounds=5,
                )
                params = {
                    "epsilon": candidate["epsilon_total"], "clipping": candidate["clipping_percentile"],
                    "batch": candidate["batch_merge_size"], "candidate_pool": candidate["candidate_pool_size"],
                }
                finish(token_result, stage="private-tokenizer", method=candidate["id"], seed=seed, **params)
                shadows = [baseline_model(result_root, seed, vocab, f"shadow_{index:02d}") for index in range(int(config["main_shadow_count"]))]
                for attack in attacks:
                    chosen = shadows if attack in {"vocabulary_overlap", "merge_similarity"} else shadows[:1] if attack == "frequency_estimation" else []
                    attack_result = run_final_attack(
                        attack=attack,
                        manifest_path=manifest,
                        target_dir=directory,
                        shadow_dirs=chosen,
                        output_path=defense_root / "attacks" / candidate["id"] / str(seed) / f"{attack}.json",
                        auxiliary_group_count=int(config["auxiliary_sampling_group_count"]),
                        naive_bayes_top_k=int(config["naive_bayes_top_k"]),
                        bootstrap_iterations=int(config["bootstrap_iterations"]),
                        bootstrap_confidence=float(config["bootstrap_confidence"]),
                    )
                    finish(attack_result, stage="defense-attack", method=candidate["id"], seed=seed,
                           attack=attack, shadow=len(chosen) or "n/a", **params)
                utility = evaluate_tokenizer_utility(
                    tokenizer_dir=directory,
                    manifest_path=manifest,
                    output_path=defense_root / "utility" / candidate["id"] / str(seed) / "utility.json",
                    ag_news_test_path=ag_news_test_path,
                )
                finish(utility, stage="utility", method=candidate["id"], seed=seed, **params)

        equivalence_path = defense_root / "he_plain_equivalence.json"
        if equivalence_path.exists():
            prior_equivalence = strict_json_load(equivalence_path)
            if prior_equivalence.get("status") != "success" or not prior_equivalence.get("all_exact"):
                raise RuntimeError("existing HE/Plain equivalence result is not successful")
        else:
            equivalence = []
            for seed in seeds:
                plain = defense_root / "tokenizers" / "private_plain_reference" / str(seed) / "tokenizer.json"
                encrypted = defense_root / "tokenizers" / "he_only_reference" / str(seed) / "tokenizer.json"
                row = {
                    "seed": seed,
                    "private_plain_sha256": sha256_file(plain),
                    "he_only_sha256": sha256_file(encrypted),
                }
                row["exact_artifact_match"] = row["private_plain_sha256"] == row["he_only_sha256"]
                if not row["exact_artifact_match"]:
                    raise RuntimeError(f"HE-only and protocol-Plain tokenizers differ for seed {seed}")
                equivalence.append(row)
            write_json_exclusive(equivalence_path, {
                "schema_version": 1,
                "status": "success",
                "comparison_scope": "identical public-candidate batched-BPE protocol; aggregation differs only by Paillier",
                "full_tokenizer_he_execution": "protocol_equivalent_cleartext",
                "actual_paillier_correctness_result": "results/final/crypto/correctness_smoke_1024.json",
                "rows": equivalence,
                "all_exact": all(row["exact_artifact_match"] for row in equivalence),
                "completed_at_utc": utc_now(),
            })
        state.update({
            "status": "success", "completed_tasks": expected, "completed_at_utc": utc_now(),
            "elapsed_seconds": time.perf_counter() - started, "peak_memory_bytes": peak_working_set_bytes(),
        })
        atomic_state(state_path, state)
        return 0
    except BaseException as exc:
        failure_path = defense_root / "failures" / f"failure_{utc_now().replace(':', '').replace('+', '_')}.json"
        write_json_exclusive(failure_path, {
            "schema_version": 1,
            "status": "failed",
            "exception_type": type(exc).__name__,
            "message": str(exc),
            "traceback": traceback.format_exc(),
            "failed_at_utc": utc_now(),
            "environment": environment_metadata(),
        })
        state.update({
            "status": "failed", "failures": int(state.get("failures", 0)) + 1,
            "updated_at_utc": utc_now(), "error": f"{type(exc).__name__}: {exc}",
            "last_failure": str(failure_path.relative_to(PROJECT_ROOT)).replace("\\", "/"),
            "peak_memory_bytes": peak_working_set_bytes(),
        })
        atomic_state(state_path, state)
        logger.exception("Main defense pipeline failed")
        raise


if __name__ == "__main__":
    raise SystemExit(main())
