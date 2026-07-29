"""Resumable Development defense screening, attacks, utility, and shortlist."""

from __future__ import annotations

import argparse
import os
import statistics
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
    strict_json_dumps,
    strict_json_load,
    utc_now,
    write_json_atomic_replace,
    write_json_exclusive,
)


def atomic_state(path: Path, payload: dict[str, Any]) -> None:
    write_json_atomic_replace(path, payload)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--plan", type=Path, required=True)
    parser.add_argument("--log", type=Path, required=True)
    return parser.parse_args()


def baseline_model(root: Path, seed: int, vocab: int, role: str) -> Path:
    return root / "runs" / "tokenizers" / "development" / str(seed) / f"vocab_{vocab}" / "plain_bpe" / role


def main() -> int:
    args = parse_args()
    config_path = args.config.resolve()
    plan_path = args.plan.resolve()
    config = strict_json_load(config_path)
    plan = strict_json_load(plan_path)
    if plan.get("status") != "frozen_before_development_results":
        raise RuntimeError("Development search plan is not frozen")
    if plan.get("config_sha256") != sha256_file(config_path):
        raise RuntimeError("Development plan/config hash mismatch")
    baseline_state = strict_json_load(PROJECT_ROOT / config["results_root"] / "runs" / "attack_pipeline_state.json")
    if baseline_state.get("status") != "success":
        raise RuntimeError("baseline attack pipeline must complete before defense screening")
    logger = setup_logger("final.defenses", args.log.resolve())
    started = time.perf_counter()
    result_root = PROJECT_ROOT / config["results_root"]
    defense_root = result_root / "defenses" / "development"
    downstream_data = strict_json_load(result_root / "downstream" / "ag_news_data.json")
    ag_news_test_path = PROJECT_ROOT / downstream_data["splits"]["test"]["path"]
    state_path = defense_root / "pipeline_state.json"
    seeds = [int(value) for value in plan["seeds"]]
    vocab = int(plan["vocab_size"])
    configurations = list(plan["configurations"])
    reference = plan["search_design"]["reference"]
    reference_configs = [
        {"id": "private_plain_reference", "mode": "plain", **reference, "epsilon_total": None},
        {"id": "he_only_reference", "mode": "he_only", **reference, "epsilon_total": None},
    ]
    all_configs = reference_configs + configurations
    attacks = list(config["attacks"])
    expected_tasks = len(seeds) + len(seeds) * len(all_configs) * (1 + len(attacks) + 1) + 1
    config_hash = sha256_file(config_path)
    if state_path.exists():
        state = strict_json_load(state_path)
        if state.get("config_sha256") != config_hash or state.get("plan_sha256") != sha256_file(plan_path):
            raise RuntimeError("defense checkpoint configuration mismatch")
        if state.get("status") == "success":
            logger.info("Development defense pipeline already successful; no output overwritten")
            return 0
    else:
        state = {
            "schema_version": 1, "status": "running", "created_at_utc": utc_now(),
            "config_sha256": config_hash, "plan_sha256": sha256_file(plan_path),
            "expected_tasks": expected_tasks, "completed_tasks": 0, "failures": 0,
            "environment": environment_metadata(),
        }
        atomic_state(state_path, state)
    completed = int(state.get("completed_tasks", 0))

    def progress(meta: dict[str, Any], *, stage: str, method: str, seed: int,
                 attack: str = "n/a", epsilon: Any = "n/a", clipping: Any = "n/a",
                 batch: Any = "n/a", candidate_pool: Any = "n/a", shadow: Any = "n/a") -> None:
        elapsed = time.perf_counter() - started
        rate = completed / elapsed if elapsed > 0 else 0.0
        logger.info(
            "stage=%s | scale=development | protocol=strict_disjoint | method=%s | attack=%s | "
            "epsilon=%s | clipping=%s | batch=%s | K=%s | vocab=%d | seed=%d | task=%d/%d | "
            "shadow=%s | elapsed=%.3fs | eta=%.3fs | successes=%d | failures=%d | log=%s | detail=%s",
            stage, method, attack, epsilon, clipping, batch, candidate_pool, vocab, seed,
            completed, expected_tasks, shadow, elapsed,
            (expected_tasks - completed) / rate if rate else 0.0,
            completed, state["failures"], args.log, strict_json_dumps(meta, indent=None),
        )

    def finish_task(result: dict[str, Any], **meta: Any) -> None:
        nonlocal completed
        if not result.get("checkpoint_reused"):
            completed += 1
        compact = {
            "status": result.get("status"),
            "checkpoint_reused": result.get("checkpoint_reused"),
            "elapsed_seconds": result.get("elapsed_seconds"),
            "actual_vocab_size": result.get("actual_vocab_size"),
            "roc_auc": result.get("metrics", {}).get("roc_auc"),
        }
        progress(compact, **meta)
        peak = int(peak_working_set_bytes() or 0)
        if peak > int(config["max_peak_memory_bytes"]):
            raise MemoryError(f"defense pipeline peak memory {peak} exceeds limit")
        state.update({"completed_tasks": completed, "updated_at_utc": utc_now(),
                      "elapsed_seconds": time.perf_counter() - started,
                      "peak_memory_bytes": max(peak, int(state.get("peak_memory_bytes", 0)))})
        atomic_state(state_path, state)

    try:
        # Canonical Plain utility is measured on the identical held-out C4 sites.
        for seed in seeds:
            manifest = PROJECT_ROOT / "data" / "final" / "manifests" / "development" / "strict_disjoint" / f"seed_{seed}.json"
            output = defense_root / "plain_utility" / str(seed) / "utility.json"
            result = evaluate_tokenizer_utility(
                tokenizer_dir=baseline_model(result_root, seed, vocab, "target"),
                manifest_path=manifest,
                output_path=output,
                ag_news_test_path=ag_news_test_path,
            )
            finish_task(result, stage="utility", method="plain_bpe", seed=seed)

        for candidate in all_configs:
            for seed in seeds:
                manifest = PROJECT_ROOT / "data" / "final" / "manifests" / "development" / "strict_disjoint" / f"seed_{seed}.json"
                directory = defense_root / "tokenizers" / candidate["id"] / str(seed)

                def round_progress(record: dict[str, Any], candidate=candidate, seed=seed) -> None:
                    progress(record, stage="private-bpe-round", method=candidate["id"], seed=seed,
                             epsilon=candidate["epsilon_total"], clipping=candidate["clipping_percentile"],
                             batch=candidate["batch_merge_size"], candidate_pool=candidate["candidate_pool_size"])

                result = train_batched_private_bpe(
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
                finish_task(result, stage="private-tokenizer", method=candidate["id"], seed=seed,
                            epsilon=candidate["epsilon_total"], clipping=candidate["clipping_percentile"],
                            batch=candidate["batch_merge_size"], candidate_pool=candidate["candidate_pool_size"])
                shadows = [baseline_model(result_root, seed, vocab, f"shadow_{index:02d}") for index in range(int(config["main_shadow_count"]))]
                for attack in attacks:
                    selected = shadows if attack in {"vocabulary_overlap", "merge_similarity"} else shadows[:1] if attack == "frequency_estimation" else []
                    output = defense_root / "attacks" / candidate["id"] / str(seed) / f"{attack}.json"
                    attack_result = run_final_attack(
                        attack=attack,
                        manifest_path=manifest,
                        target_dir=directory,
                        shadow_dirs=selected,
                        output_path=output,
                        auxiliary_group_count=int(config["auxiliary_sampling_group_count"]),
                        naive_bayes_top_k=int(config["naive_bayes_top_k"]),
                        bootstrap_iterations=int(config["bootstrap_iterations"]),
                        bootstrap_confidence=float(config["bootstrap_confidence"]),
                    )
                    finish_task(attack_result, stage="defense-attack", method=candidate["id"], seed=seed,
                                attack=attack, epsilon=candidate["epsilon_total"],
                                clipping=candidate["clipping_percentile"], batch=candidate["batch_merge_size"],
                                candidate_pool=candidate["candidate_pool_size"], shadow=len(selected) or "n/a")
                utility_result = evaluate_tokenizer_utility(
                    tokenizer_dir=directory,
                    manifest_path=manifest,
                    output_path=defense_root / "utility" / candidate["id"] / str(seed) / "utility.json",
                    ag_news_test_path=ag_news_test_path,
                )
                finish_task(utility_result, stage="utility", method=candidate["id"], seed=seed,
                            epsilon=candidate["epsilon_total"], clipping=candidate["clipping_percentile"],
                            batch=candidate["batch_merge_size"], candidate_pool=candidate["candidate_pool_size"])

        # Shortlisting uses Development attack and C4 utility only; downstream F1 is still pending.
        plain_tokens = statistics.fmean(
            strict_json_load(defense_root / "plain_utility" / str(seed) / "utility.json")["sources"]["c4_heldout"]["mean_tokens_per_document"]
            for seed in seeds
        )
        ranking = []
        for candidate in configurations:
            aucs = [
                strict_json_load(defense_root / "attacks" / candidate["id"] / str(seed) / f"{attack}.json")["metrics"]["roc_auc"]
                for seed in seeds for attack in attacks
            ]
            token_mean = statistics.fmean(
                strict_json_load(defense_root / "utility" / candidate["id"] / str(seed) / "utility.json")["sources"]["c4_heldout"]["mean_tokens_per_document"]
                for seed in seeds
            )
            ranking.append({
                **candidate,
                "mean_attack_auc": statistics.fmean(aucs),
                "sample_standard_deviation_attack_auc": statistics.stdev(aucs),
                "c4_mean_token_increase_fraction": token_mean / plain_tokens - 1.0,
                "passes_token_constraint": token_mean / plain_tokens - 1.0 <= float(config["selection_rule"]["max_mean_token_increase_fraction"]),
                "cryptographic_work_proxy_K_per_batch": (
                    float(candidate["candidate_pool_size"]) / float(candidate["batch_merge_size"])
                ),
            })
        shortlist = {}
        limit = int(plan["search_design"]["maximum_downstream_shortlist_per_private_method"])
        for mode in ("local_dp", "sa_dp"):
            eligible = [row for row in ranking if row["mode"] == mode and row["passes_token_constraint"]]
            shortlist[mode] = sorted(eligible, key=lambda row: (
                row["mean_attack_auc"], row["c4_mean_token_increase_fraction"],
                row["cryptographic_work_proxy_K_per_batch"], row["epsilon_total"]
            ))[:limit]
        write_json_exclusive(result_root / "defenses" / "development_shortlist.json", {
            "schema_version": 1,
            "status": "awaiting_downstream_macro_f1_constraint",
            "created_at_utc": utc_now(),
            "development_only": True,
            "main_results_read": False,
            "selection_rule": config["selection_rule"],
            "ranking": ranking,
            "shortlist": shortlist,
        })
        completed += 1
        state.update({"status": "success", "completed_tasks": expected_tasks,
                      "completed_at_utc": utc_now(), "elapsed_seconds": time.perf_counter() - started})
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
        state.update({"status": "failed", "failures": int(state.get("failures", 0)) + 1,
                      "updated_at_utc": utc_now(), "error": f"{type(exc).__name__}: {exc}",
                      "last_failure": str(failure_path.relative_to(PROJECT_ROOT)).replace("\\", "/")})
        atomic_state(state_path, state)
        logger.exception("Development defense pipeline failed")
        raise


if __name__ == "__main__":
    raise SystemExit(main())
