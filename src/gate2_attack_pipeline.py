"""Bounded, resumable Gate 2 tokenizer-training and attack orchestrator."""

from __future__ import annotations

import argparse
import json
import os
import time
from pathlib import Path
from typing import Any

from src.attacks.compression_rate import run_compression_rate
from src.attacks.vocabulary_overlap import run_vocabulary_overlap
from src.tokenizer.common import materialize_tokenizer_artifact, train_base_tokenizer_artifact
from src.utils.run_metadata import (
    PROJECT_ROOT,
    log_progress,
    peak_working_set_bytes,
    setup_logger,
    sha256_file,
    strict_json_dumps,
    strict_json_load,
    utc_now,
)


class ResourceLimitExceeded(RuntimeError):
    """Raised after checkpointing when the declared Gate 2 limit is reached."""


def atomic_write_state(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    partial = path.with_suffix(path.suffix + ".partial")
    partial.write_text(strict_json_dumps(payload) + "\n", encoding="utf-8")
    os.replace(partial, path)


def model_dir(root: Path, protocol: str, seed: int, vocab: int, method: str, role: str) -> Path:
    return root / "tokenizers" / protocol / str(seed) / f"vocab_{vocab}" / method / role


def base_dir(root: Path, protocol: str, seed: int, role: str) -> Path:
    return root / "tokenizers" / protocol / str(seed) / "base_vocab_max" / role


def result_path(root: Path, protocol: str, seed: int, vocab: int, method: str, attack: str) -> Path:
    return root / "attacks" / protocol / str(seed) / f"vocab_{vocab}" / method / f"{attack}.json"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--log", type=Path, required=True)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    config_path = args.config.resolve()
    config = strict_json_load(config_path)
    logger = setup_logger("gate2.attack.pipeline", args.log.resolve())
    session_started = time.perf_counter()
    results_root = PROJECT_ROOT / config["results_root"]
    run_root = results_root / "runs"
    state_path = run_root / "run_state.json"
    config_hash = sha256_file(config_path)
    expected_base = len(config["protocols"]) * len(config["seeds"]) * (1 + int(config["shadow_count"]))
    expected_derived = expected_base * len(config["vocab_sizes"]) * len(config["methods"])
    expected_attacks = (
        len(config["protocols"])
        * len(config["seeds"])
        * len(config["vocab_sizes"])
        * len(config["methods"])
        * len(config["attacks"])
    )
    expected_tasks = expected_base + expected_derived + expected_attacks

    if state_path.exists():
        state = strict_json_load(state_path)
        if state.get("config_sha256") != config_hash:
            raise RuntimeError("Gate 2 checkpoint config hash differs; refusing mixed run")
        if state.get("status") == "success":
            logger.info("Gate 2 checkpoint is already complete; no outputs were overwritten")
            return 0
    else:
        state = {
            "schema_version": 1,
            "status": "running",
            "created_at_utc": utc_now(),
            "config_path": str(config_path.relative_to(PROJECT_ROOT)),
            "config_sha256": config_hash,
            "expected_base_tokenizers": expected_base,
            "expected_derived_tokenizers": expected_derived,
            "expected_attack_results": expected_attacks,
            "expected_tasks": expected_tasks,
            "completed_tasks": 0,
            "checkpoint_reuses": 0,
            "failures": 0,
            "accumulated_elapsed_seconds": 0.0,
            "peak_memory_bytes": 0,
        }
        atomic_write_state(state_path, state)

    previous_elapsed = float(state.get("accumulated_elapsed_seconds", 0.0))
    session_completed = 0
    failures = int(state.get("failures", 0))
    max_elapsed = float(config["max_elapsed_seconds"])
    max_memory = int(config["max_peak_memory_bytes"])
    max_vocab = max(int(value) for value in config["vocab_sizes"])

    def elapsed_total() -> float:
        return previous_elapsed + (time.perf_counter() - session_started)

    def checkpoint(status: str = "running", stop_reason: str | None = None) -> None:
        peak = int(peak_working_set_bytes() or 0)
        state.update(
            {
                "status": status,
                "updated_at_utc": utc_now(),
                "completed_tasks": int(state.get("completed_tasks", 0)) + session_completed,
                "checkpoint_reuses": int(state.get("checkpoint_reuses", 0)),
                "failures": failures,
                "accumulated_elapsed_seconds": elapsed_total(),
                "peak_memory_bytes": max(int(state.get("peak_memory_bytes", 0)), peak),
                "stop_reason": stop_reason,
            }
        )
        atomic_write_state(state_path, state)

    def after_task(metadata: dict[str, Any], *, stage: str, protocol: str, seed: int, vocab: Any,
                   method: str, shadow: Any, output: Path) -> None:
        nonlocal session_completed
        if metadata.get("checkpoint_reused"):
            state["checkpoint_reuses"] = int(state.get("checkpoint_reuses", 0)) + 1
        else:
            session_completed += 1
        log_progress(
            logger,
            started=session_started,
            stage=stage,
            protocol=protocol,
            seed=seed,
            vocab_size=vocab,
            method=method,
            shadow=shadow,
            completed=int(state.get("completed_tasks", 0)) + session_completed,
            total=expected_tasks,
            failures=failures,
            result_path=output,
        )
        peak = int(peak_working_set_bytes() or 0)
        if elapsed_total() > max_elapsed:
            checkpoint("resource_limit_stop", f"elapsed_seconds>{max_elapsed}")
            raise ResourceLimitExceeded(f"Gate 2 stopped after exceeding {max_elapsed} seconds")
        if peak > max_memory:
            checkpoint("resource_limit_stop", f"peak_memory_bytes>{max_memory}")
            raise ResourceLimitExceeded(f"Gate 2 stopped after exceeding {max_memory} peak bytes")
        checkpoint()
        # checkpoint() moves the current session count into the persistent count.
        session_completed = 0

    try:
        validation = strict_json_load(PROJECT_ROOT / "data" / "gate2" / "validation.json")
        if validation.get("status") != "success":
            raise RuntimeError("Gate 2 natural corpus validation is not successful")
        for protocol in config["protocols"]:
            for seed_value in config["seeds"]:
                seed = int(seed_value)
                manifest_path = PROJECT_ROOT / config["manifest_dir"] / protocol / f"seed_{seed}.json"
                manifest = strict_json_load(manifest_path)
                corpus_path = PROJECT_ROOT / manifest["corpus_path"]
                roles: list[tuple[str, list[str], int | None]] = [
                    ("target", manifest["target_training_site_ids"], None)
                ]
                roles.extend(
                    (
                        f"shadow_{int(plan['shadow_id']):02d}",
                        plan["training_site_ids"],
                        int(plan["shadow_id"]),
                    )
                    for plan in manifest["shadow_plans"]
                )
                for role, sites, shadow_id in roles:
                    directory = base_dir(run_root, protocol, seed, role)
                    metadata = train_base_tokenizer_artifact(
                        corpus_path=corpus_path,
                        training_site_ids=sites,
                        manifest_path=manifest_path,
                        protocol=protocol,
                        seed=seed,
                        max_vocab_size=max_vocab,
                        role="target" if shadow_id is None else "shadow",
                        shadow_id=shadow_id,
                        output_dir=directory,
                        tokenizers_threads=int(config["tokenizers_threads"]),
                    )
                    after_task(
                        metadata,
                        stage="base-tokenizer",
                        protocol=protocol,
                        seed=seed,
                        vocab=max_vocab,
                        method="plain_bpe_base",
                        shadow="target" if shadow_id is None else shadow_id,
                        output=directory / "metadata.json",
                    )

                for vocab_value in config["vocab_sizes"]:
                    vocab = int(vocab_value)
                    for method in config["methods"]:
                        for role, sites, shadow_id in roles:
                            base = base_dir(run_root, protocol, seed, role)
                            directory = model_dir(run_root, protocol, seed, vocab, method["id"], role)
                            metadata = materialize_tokenizer_artifact(
                                base_artifact=base / "tokenizer.json",
                                base_metadata_path=base / "metadata.json",
                                corpus_path=corpus_path,
                                training_site_ids=sites,
                                manifest_path=manifest_path,
                                vocab_size=vocab,
                                method=method,
                                output_dir=directory,
                            )
                            after_task(
                                metadata,
                                stage="derived-tokenizer",
                                protocol=protocol,
                                seed=seed,
                                vocab=vocab,
                                method=method["id"],
                                shadow="target" if shadow_id is None else shadow_id,
                                output=directory / "metadata.json",
                            )

                        target_dir = model_dir(run_root, protocol, seed, vocab, method["id"], "target")
                        shadows = [
                            model_dir(run_root, protocol, seed, vocab, method["id"], f"shadow_{index:02d}")
                            for index in range(int(config["shadow_count"]))
                        ]
                        for attack in config["attacks"]:
                            output = result_path(run_root, protocol, seed, vocab, method["id"], attack)
                            if attack == "compression_rate":
                                metadata = run_compression_rate(
                                    manifest_path=manifest_path,
                                    target_artifact=target_dir / "tokenizer.json",
                                    target_metadata_path=target_dir / "metadata.json",
                                    output_path=output,
                                    vocab_size=vocab,
                                    method=method,
                                    bootstrap_iterations=int(config["bootstrap_iterations"]),
                                    bootstrap_confidence=float(config["bootstrap_confidence"]),
                                )
                            elif attack == "vocabulary_overlap":
                                metadata = run_vocabulary_overlap(
                                    manifest_path=manifest_path,
                                    target_artifact=target_dir / "tokenizer.json",
                                    target_metadata_path=target_dir / "metadata.json",
                                    shadow_dirs=shadows,
                                    output_path=output,
                                    vocab_size=vocab,
                                    method=method,
                                    bootstrap_iterations=int(config["bootstrap_iterations"]),
                                    bootstrap_confidence=float(config["bootstrap_confidence"]),
                                )
                            else:
                                raise ValueError(f"unsupported Gate 2 attack: {attack}")
                            after_task(
                                metadata,
                                stage="attack",
                                protocol=protocol,
                                seed=seed,
                                vocab=vocab,
                                method=f"{method['id']}:{attack}",
                                shadow="8" if attack == "vocabulary_overlap" else "n/a",
                                output=output,
                            )
        # The artifact/result cardinality has just been exhaustively traversed.
        # Normal resumptions do not double-count reused checkpoints.
        state["completed_tasks"] = expected_tasks
        checkpoint("success")
        logger.info("Gate 2 attack pipeline completed all %d bounded tasks", expected_tasks)
        return 0
    except BaseException as exc:
        failures += 1
        if not isinstance(exc, ResourceLimitExceeded):
            checkpoint("failed", f"{type(exc).__name__}: {exc}")
        logger.exception("Gate 2 attack pipeline failed after %.3fs", elapsed_total())
        raise


if __name__ == "__main__":
    raise SystemExit(main())
