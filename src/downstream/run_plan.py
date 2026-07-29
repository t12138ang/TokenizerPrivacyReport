"""Run an immutable downstream plan with resumable per-epoch training tasks."""

from __future__ import annotations

import argparse
import os
import time
import traceback
from pathlib import Path
from typing import Any

from src.downstream.train_ag_news import train_one
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


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--downstream-config", type=Path, required=True)
    parser.add_argument("--plan", type=Path, required=True)
    parser.add_argument("--state", type=Path, required=True)
    parser.add_argument("--log", type=Path, required=True)
    args = parser.parse_args()
    config_path = args.downstream_config.resolve()
    plan_path = args.plan.resolve()
    state_path = args.state.resolve()
    plan = strict_json_load(plan_path)
    if plan.get("status") != "frozen":
        raise RuntimeError("downstream plan is not frozen")
    if plan.get("downstream_config_sha256") != sha256_file(config_path):
        raise RuntimeError("downstream plan/config hash mismatch")
    logger = setup_logger(f"downstream.plan.{plan['stage']}", args.log.resolve())
    tasks = list(plan["tasks"])
    if state_path.exists():
        state = strict_json_load(state_path)
        if state.get("plan_sha256") != sha256_file(plan_path):
            raise RuntimeError("downstream state/plan hash mismatch")
        if state.get("status") == "success":
            logger.info("stage=downstream | status=already-successful | no outputs overwritten")
            return 0
    else:
        state = {
            "schema_version": 1,
            "status": "running",
            "stage": plan["stage"],
            "created_at_utc": utc_now(),
            "plan_sha256": sha256_file(plan_path),
            "config_sha256": sha256_file(config_path),
            "expected_tasks": len(tasks),
            "completed_tasks": 0,
            "failures": 0,
            "environment": environment_metadata(),
        }
        atomic_state(state_path, state)
    started = time.perf_counter()
    try:
        for index, task in enumerate(tasks, start=1):
            output_dir = PROJECT_ROOT / task["output_dir"]
            tokenizer_artifact = PROJECT_ROOT / task["tokenizer_dir"] / "tokenizer.json"
            if sha256_file(tokenizer_artifact) != task["tokenizer_sha256"]:
                raise RuntimeError(f"downstream plan tokenizer hash mismatch: {tokenizer_artifact}")
            result_path = output_dir / "result.json"
            if result_path.exists():
                existing = strict_json_load(result_path)
                if (
                    existing.get("status") != "success"
                    or existing.get("downstream_config_sha256") != sha256_file(config_path)
                    or existing.get("tokenizer_sha256") != task["tokenizer_sha256"]
                    or int(existing.get("seed", -1)) != int(task["seed"])
                ):
                    raise RuntimeError(f"mismatched existing downstream result: {result_path}")
                logger.info(
                    "stage=ag-news | scale=%s | method=%s | seed=%d | task=%d/%d | "
                    "elapsed=%.3fs | status=checkpoint-reused",
                    plan["stage"], task["method_id"], task["seed"], index, len(tasks),
                    time.perf_counter() - started,
                )
                continue
            task_started = time.perf_counter()
            task_log = PROJECT_ROOT / "logs" / "final" / "downstream" / plan["stage"] / f"{task['task_id']}.log"
            result = train_one(
                config_path=config_path,
                tokenizer_dir=PROJECT_ROOT / task["tokenizer_dir"],
                output_dir=output_dir,
                seed=int(task["seed"]),
                log_path=task_log,
            )
            completed = sum(
                (PROJECT_ROOT / row["output_dir"] / "result.json").is_file() for row in tasks
            )
            state.update({
                "status": "running",
                "completed_tasks": completed,
                "updated_at_utc": utc_now(),
                "elapsed_seconds_current_invocation": time.perf_counter() - started,
                "peak_memory_bytes": peak_working_set_bytes(),
            })
            atomic_state(state_path, state)
            elapsed = time.perf_counter() - started
            rate = completed / elapsed if elapsed else 0.0
            logger.info(
                "stage=ag-news | scale=%s | protocol=strict_disjoint | method=%s | attack=n/a | "
                "epsilon=n/a | clipping=n/a | batch=%s | vocab=%d | seed=%d | task=%d/%d | "
                "shadow=n/a | elapsed=%.3fs | eta=%.3fs | successes=%d | failures=%d | "
                "task_seconds=%.3f | accuracy=%.8f | macro_f1=%.8f | log=%s",
                plan["stage"], task["method_id"], result["architecture"]["effective_batch_size"],
                result["tokenizer_actual_vocab_size"], task["seed"], index, len(tasks), elapsed,
                (len(tasks) - completed) / rate if rate else 0.0, completed, state["failures"],
                time.perf_counter() - task_started, result["test"]["accuracy"],
                result["test"]["macro_f1"], task_log,
            )
        state.update({
            "status": "success",
            "completed_tasks": len(tasks),
            "completed_at_utc": utc_now(),
            "elapsed_seconds_current_invocation": time.perf_counter() - started,
            "peak_memory_bytes": peak_working_set_bytes(),
        })
        atomic_state(state_path, state)
        return 0
    except BaseException as exc:
        failure_dir = state_path.parent / "failures"
        failure_dir.mkdir(parents=True, exist_ok=True)
        failure_path = failure_dir / f"failure_{utc_now().replace(':', '').replace('+', '_')}.json"
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
            "status": "failed",
            "failures": int(state.get("failures", 0)) + 1,
            "updated_at_utc": utc_now(),
            "last_failure": str(failure_path.relative_to(PROJECT_ROOT)).replace("\\", "/"),
            "peak_memory_bytes": peak_working_set_bytes(),
        })
        atomic_state(state_path, state)
        logger.exception("downstream plan failed; traceback preserved at %s", failure_path)
        raise


if __name__ == "__main__":
    raise SystemExit(main())
