"""Resumable Main/Development baseline training and five-attack pipeline."""

from __future__ import annotations

import argparse
import os
import time
import traceback
from pathlib import Path
from typing import Any, Callable

from src.attacks.final_attack import run_final_attack
from src.tokenizer.common import materialize_tokenizer_artifact, train_base_tokenizer_artifact
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


def manifest_path(scale: str, seed: int) -> Path:
    return PROJECT_ROOT / "data" / "final" / "manifests" / scale / "strict_disjoint" / f"seed_{seed}.json"


def base_directory(root: Path, scale: str, seed: int, role: str) -> Path:
    return root / "tokenizers" / scale / str(seed) / "base_vocab_32000" / role


def model_directory(root: Path, scale: str, seed: int, vocab: int, method: str, role: str) -> Path:
    return root / "tokenizers" / scale / str(seed) / f"vocab_{vocab}" / method / role


def attack_path(root: Path, scale: str, seed: int, vocab: int, method: str, attack: str) -> Path:
    return root / "attacks" / scale / str(seed) / f"vocab_{vocab}" / method / f"{attack}.json"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--log", type=Path, required=True)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    config_path = args.config.resolve()
    config = strict_json_load(config_path)
    logger = setup_logger("final.attacks", args.log.resolve())
    started = time.perf_counter()
    results_root = PROJECT_ROOT / config["results_root"] / "runs"
    state_path = results_root / "attack_pipeline_state.json"
    config_hash = sha256_file(config_path)
    validation = strict_json_load(PROJECT_ROOT / "data" / "final" / "validation.json")
    if validation.get("status") != "success":
        raise RuntimeError("final dataset validation is not successful")

    seeds = [int(value) for value in config["seeds"]]
    vocabs = [int(value) for value in config["vocab_sizes"]]
    methods = list(config["baseline_methods"])
    standard_shadows = int(config["main_shadow_count"])
    tasks: list[tuple[dict[str, Any], Callable[[], dict[str, Any]]]] = []

    for scale in ("development", "main"):
        for seed in seeds:
            manifest_file = manifest_path(scale, seed)
            manifest = strict_json_load(manifest_file)
            corpus_path = PROJECT_ROOT / manifest["corpus_path"]
            maximum_shadows = 96 if scale == "main" and seed == seeds[0] else standard_shadows
            roles: list[tuple[str, list[str], int | None]] = [
                ("target", manifest["target_training_site_ids"], None)
            ] + [
                (f"shadow_{index:02d}", manifest["shadow_plans"][index]["training_site_ids"], index)
                for index in range(maximum_shadows)
            ]
            for role, sites, shadow_id in roles:
                directory = base_directory(results_root, scale, seed, role)
                meta = {"stage": "base-tokenizer", "scale": scale, "seed": seed, "vocab": 32000,
                        "method": "plain_bpe_base", "attack": "n/a", "shadow": role}
                tasks.append((meta, lambda directory=directory, sites=sites, shadow_id=shadow_id,
                    manifest_file=manifest_file, corpus_path=corpus_path, seed=seed:
                    train_base_tokenizer_artifact(
                        corpus_path=corpus_path,
                        training_site_ids=sites,
                        manifest_path=manifest_file,
                        protocol="strict_disjoint",
                        seed=seed,
                        max_vocab_size=32000,
                        role="target" if shadow_id is None else "shadow",
                        shadow_id=shadow_id,
                        output_dir=directory,
                        tokenizers_threads=int(config["tokenizers_threads"]),
                    )))

            standard_roles = roles[: standard_shadows + 1]
            for vocab in vocabs:
                for method in methods:
                    for role, sites, shadow_id in standard_roles:
                        source = base_directory(results_root, scale, seed, role)
                        directory = model_directory(results_root, scale, seed, vocab, method["id"], role)
                        meta = {"stage": "derived-tokenizer", "scale": scale, "seed": seed,
                                "vocab": vocab, "method": method["id"], "attack": "n/a", "shadow": role}
                        tasks.append((meta, lambda source=source, directory=directory, sites=sites,
                            manifest_file=manifest_file, corpus_path=corpus_path, vocab=vocab, method=method:
                            materialize_tokenizer_artifact(
                                base_artifact=source / "tokenizer.json",
                                base_metadata_path=source / "metadata.json",
                                corpus_path=corpus_path,
                                training_site_ids=sites,
                                manifest_path=manifest_file,
                                vocab_size=vocab,
                                method=method,
                                output_dir=directory,
                            )))

            if scale == "main" and seed == seeds[0]:
                plain = methods[0]
                for role, sites, _ in roles[standard_shadows + 1 :]:
                    source = base_directory(results_root, scale, seed, role)
                    directory = model_directory(results_root, scale, seed, 16000, plain["id"], role)
                    meta = {"stage": "shadow-sensitivity-tokenizer", "scale": scale, "seed": seed,
                            "vocab": 16000, "method": plain["id"], "attack": "n/a", "shadow": role}
                    tasks.append((meta, lambda source=source, directory=directory, sites=sites,
                        manifest_file=manifest_file, corpus_path=corpus_path, plain=plain:
                        materialize_tokenizer_artifact(
                            base_artifact=source / "tokenizer.json",
                            base_metadata_path=source / "metadata.json",
                            corpus_path=corpus_path,
                            training_site_ids=sites,
                            manifest_path=manifest_file,
                            vocab_size=16000,
                            method=plain,
                            output_dir=directory,
                        )))

            for vocab in vocabs:
                for method in methods:
                    target = model_directory(results_root, scale, seed, vocab, method["id"], "target")
                    shadows = [
                        model_directory(results_root, scale, seed, vocab, method["id"], f"shadow_{index:02d}")
                        for index in range(standard_shadows)
                    ]
                    for attack in config["attacks"]:
                        selected_shadows = (
                            shadows if attack in {"vocabulary_overlap", "merge_similarity"}
                            else shadows[:1] if attack == "frequency_estimation" else []
                        )
                        output = attack_path(results_root, scale, seed, vocab, method["id"], attack)
                        meta = {"stage": "attack", "scale": scale, "seed": seed, "vocab": vocab,
                                "method": method["id"], "attack": attack,
                                "shadow": len(selected_shadows) if selected_shadows else "n/a"}
                        tasks.append((meta, lambda attack=attack, manifest_file=manifest_file,
                            target=target, selected_shadows=selected_shadows, output=output:
                            run_final_attack(
                                attack=attack,
                                manifest_path=manifest_file,
                                target_dir=target,
                                shadow_dirs=selected_shadows,
                                output_path=output,
                                auxiliary_group_count=int(config["auxiliary_sampling_group_count"]),
                                naive_bayes_top_k=int(config["naive_bayes_top_k"]),
                                bootstrap_iterations=int(config["bootstrap_iterations"]),
                                bootstrap_confidence=float(config["bootstrap_confidence"]),
                            )))

    # Only this declared Main/16k/Plain/seed configuration expands to 96 shadows.
    scale, seed, vocab, method_id = "main", seeds[0], 16000, "plain_bpe"
    target = model_directory(results_root, scale, seed, vocab, method_id, "target")
    manifest_file = manifest_path(scale, seed)
    for shadow_count in config["shadow_sensitivity_counts"]:
        if int(shadow_count) == standard_shadows:
            # The standard Main result is the canonical 32-shadow sensitivity point.
            continue
        shadows = [
            model_directory(results_root, scale, seed, vocab, method_id, f"shadow_{index:02d}")
            for index in range(int(shadow_count))
        ]
        for attack in ("vocabulary_overlap", "merge_similarity"):
            output = results_root / "shadow_sensitivity" / f"shadows_{shadow_count}" / f"{attack}.json"
            meta = {"stage": "shadow-sensitivity-attack", "scale": scale, "seed": seed,
                    "vocab": vocab, "method": method_id, "attack": attack, "shadow": shadow_count}
            tasks.append((meta, lambda attack=attack, shadows=shadows, output=output:
                run_final_attack(
                    attack=attack,
                    manifest_path=manifest_file,
                    target_dir=target,
                    shadow_dirs=shadows,
                    output_path=output,
                    auxiliary_group_count=int(config["auxiliary_sampling_group_count"]),
                    naive_bayes_top_k=int(config["naive_bayes_top_k"]),
                    bootstrap_iterations=int(config["bootstrap_iterations"]),
                    bootstrap_confidence=float(config["bootstrap_confidence"]),
                )))

    if state_path.exists():
        state = strict_json_load(state_path)
        if state.get("config_sha256") != config_hash:
            raise RuntimeError("attack checkpoint config hash mismatch")
        if state.get("status") == "success":
            logger.info("final attack pipeline already successful; outputs were not overwritten")
            return 0
    else:
        state = {
            "schema_version": 1,
            "status": "running",
            "created_at_utc": utc_now(),
            "config_path": str(config_path.relative_to(PROJECT_ROOT)),
            "config_sha256": config_hash,
            "data_validation_sha256": sha256_file(PROJECT_ROOT / "data" / "final" / "validation.json"),
            "expected_tasks": len(tasks),
            "completed_tasks": 0,
            "checkpoint_reuses": 0,
            "failures": 0,
            "environment": environment_metadata(),
        }
        atomic_state(state_path, state)

    completed = int(state.get("completed_tasks", 0))
    try:
        for task_index, (meta, operation) in enumerate(tasks, start=1):
            task_started = time.perf_counter()
            result = operation()
            if result.get("checkpoint_reused"):
                state["checkpoint_reuses"] = int(state.get("checkpoint_reuses", 0)) + 1
            completed = max(completed, task_index)
            elapsed = time.perf_counter() - started
            rate = completed / elapsed if elapsed > 0 else 0.0
            remaining = max(0, len(tasks) - completed)
            logger.info(
                "stage=%s | scale=%s | protocol=strict_disjoint | method=%s | attack=%s | "
                "epsilon=n/a | clipping=n/a | batch=n/a | vocab=%s | seed=%s | task=%d/%d | "
                "shadow=%s | elapsed=%.3fs | eta=%.3fs | successes=%d | failures=%d | log=%s | task_seconds=%.3f",
                meta["stage"], meta["scale"], meta["method"], meta["attack"], meta["vocab"],
                meta["seed"], completed, len(tasks), meta["shadow"], elapsed,
                remaining / rate if rate else 0.0, completed, state["failures"], args.log,
                time.perf_counter() - task_started,
            )
            peak = int(peak_working_set_bytes() or 0)
            if peak > int(config["max_peak_memory_bytes"]):
                raise MemoryError(f"peak memory {peak} exceeds configured limit")
            state.update({"completed_tasks": completed, "updated_at_utc": utc_now(),
                          "peak_memory_bytes": max(peak, int(state.get("peak_memory_bytes", 0))),
                          "accumulated_elapsed_seconds": elapsed})
            atomic_state(state_path, state)
        state.update({"status": "success", "completed_tasks": len(tasks), "completed_at_utc": utc_now(),
                      "accumulated_elapsed_seconds": time.perf_counter() - started})
        atomic_state(state_path, state)
        return 0
    except BaseException as exc:
        failure_path = results_root / "failures" / f"failure_{utc_now().replace(':', '').replace('+', '_')}.json"
        write_json_exclusive(failure_path, {
            "schema_version": 1,
            "status": "failed",
            "exception_type": type(exc).__name__,
            "message": str(exc),
            "traceback": traceback.format_exc(),
            "completed_tasks_before_failure": completed,
            "failed_at_utc": utc_now(),
            "environment": environment_metadata(),
        })
        state.update({"status": "failed", "failures": int(state.get("failures", 0)) + 1,
                      "updated_at_utc": utc_now(), "error": f"{type(exc).__name__}: {exc}",
                      "last_failure": str(failure_path.relative_to(PROJECT_ROOT)).replace("\\", "/")})
        atomic_state(state_path, state)
        logger.exception("final attack pipeline failed")
        raise


if __name__ == "__main__":
    raise SystemExit(main())
