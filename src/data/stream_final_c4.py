"""Build disjoint Development/Main C4 corpora with a resumable two-pass scan."""

from __future__ import annotations

import argparse
import json
import logging
import math
import os
import shutil
import time
from collections import Counter
from pathlib import Path
from typing import Any

from src.data.stream_c4_websites import (
    contains_source_marker_or_url,
    make_stream,
    normalize_host,
    normalize_text,
    site_id_from_host,
    validate_text,
)
from src.utils.run_metadata import (
    PROJECT_ROOT,
    environment_metadata,
    peak_working_set_bytes,
    setup_logger,
    sha256_file,
    sha256_text,
    strict_json_load,
    utc_now,
    write_json_exclusive,
)


def check_resources(config: dict[str, Any], started: float) -> None:
    elapsed = time.perf_counter() - started
    peak = int(peak_working_set_bytes() or 0)
    free = int(shutil.disk_usage(PROJECT_ROOT).free)
    if elapsed > int(config["max_elapsed_seconds"]):
        raise RuntimeError(f"final data elapsed limit exceeded: {elapsed:.1f}s")
    if peak > int(config["max_peak_memory_bytes"]):
        raise MemoryError(f"final data peak-memory limit exceeded: {peak} bytes")
    if free < int(config["min_free_disk_bytes"]):
        raise OSError(f"final data free-disk gate crossed: {free} bytes")


def progress(
    logger: logging.Logger,
    *,
    started: float,
    stage: str,
    completed: int,
    total: int,
    successes: int,
    failures: int,
    log_path: Path,
    details: str = "",
) -> None:
    elapsed = time.perf_counter() - started
    rate = completed / elapsed if elapsed > 0 else 0.0
    eta = (total - completed) / rate if rate > 0 else None
    logger.info(
        "stage=%s | scale=development+main | protocol=strict_disjoint | method=c4-streaming | "
        "attack=n/a | epsilon=n/a | clipping=n/a | batch=n/a | vocab=n/a | seed=20260726 | "
        "task=%d/%d | shadow=n/a | elapsed=%.3fs | eta=%s | successes=%d | failures=%d | "
        "log=%s | %s",
        stage,
        completed,
        total,
        elapsed,
        "unknown" if eta is None else f"{eta:.1f}s",
        successes,
        failures,
        log_path,
        details,
    )


def scale_required(scale: dict[str, Any]) -> int:
    return (
        int(scale["target_site_count"])
        + int(scale["shadow_auxiliary_site_count"])
        + int(scale["public_candidate_site_count"])
    )


def rank_sites(sites: list[str] | set[str], namespace: str, seed: int) -> list[str]:
    return sorted(sites, key=lambda site: sha256_text(f"{namespace}:{seed}:{site}"))


def pass1(
    config: dict[str, Any], logger: logging.Logger, log_path: Path, started: float
) -> dict[str, Any]:
    scales = config["scales"]
    dev = scales["development"]
    main = scales["main"]
    multiplier = float(config["candidate_buffer_multiplier"])
    main_candidate_count = math.ceil(scale_required(main) * multiplier)
    dev_candidate_count = math.ceil(scale_required(dev) * multiplier)
    dev_min = int(dev["min_texts_per_site"])
    main_min = int(main["min_texts_per_site"])
    cap = max(int(dev["max_texts_per_site"]), int(main["max_texts_per_site"]))
    counts: Counter[str] = Counter()
    stats: Counter[str] = Counter()
    dev_qualified: set[str] = set()
    main_qualified: set[str] = set()
    exceptions: list[dict[str, Any]] = []
    max_records = int(config["max_stream_records"])
    every = int(config["progress_every_records"])

    for index, sample in enumerate(make_stream(config)):
        if index >= max_records:
            break
        stats["scanned_records"] += 1
        try:
            host = normalize_host(sample.get("url"))
            if host is None:
                stats["invalid_url"] += 1
                continue
            site_id = site_id_from_host(host)
            if counts[site_id] >= cap:
                stats["site_cap"] += 1
                continue
            text, reason, _ = validate_text(sample.get("text"), config)
            if reason:
                stats[reason] += 1
                continue
            assert text is not None
            if contains_source_marker_or_url(text, host):
                stats["url_or_source_marker"] += 1
                continue
            counts[site_id] += 1
            current = counts[site_id]
            stats["accepted_for_counting"] += 1
            if current == dev_min:
                dev_qualified.add(site_id)
            if current == main_min:
                main_qualified.add(site_id)
        except Exception as exc:
            stats["record_exceptions"] += 1
            if len(exceptions) < 20:
                exceptions.append(
                    {"record_index": index, "type": type(exc).__name__, "message": str(exc)}
                )
            logger.exception("final C4 pass1 record failure index=%d", index)

        if stats["scanned_records"] % every == 0:
            check_resources(config, started)
            progress(
                logger,
                started=started,
                stage="final-data-pass1",
                completed=stats["scanned_records"],
                total=max_records,
                successes=len(main_qualified),
                failures=stats["record_exceptions"],
                log_path=log_path,
                details=(
                    f"qualified_dev={len(dev_qualified)}/{main_candidate_count + dev_candidate_count} "
                    f"qualified_main={len(main_qualified)}/{main_candidate_count} "
                    f"observed_sites={len(counts)}"
                ),
            )
        if (
            len(main_qualified) >= main_candidate_count
            and len(dev_qualified) >= main_candidate_count + dev_candidate_count
        ):
            stats["stopped_after_candidate_buffer"] = 1
            break

    if len(main_qualified) < main_candidate_count:
        raise RuntimeError(
            f"scan ended with {len(main_qualified)} main-qualified sites; "
            f"candidate requirement={main_candidate_count}"
        )
    main_candidates = rank_sites(main_qualified, "main-candidates", int(config["seed"]))[
        :main_candidate_count
    ]
    dev_pool = dev_qualified - set(main_candidates)
    if len(dev_pool) < dev_candidate_count:
        raise RuntimeError(
            f"only {len(dev_pool)} disjoint development candidates; required={dev_candidate_count}"
        )
    dev_candidates = rank_sites(dev_pool, "development-candidates", int(config["seed"]))[
        :dev_candidate_count
    ]
    return {
        "schema_version": 1,
        "status": "pass1_success",
        "dataset_revision": config["dataset_revision"],
        "scan_limit": int(stats["scanned_records"]),
        "main_candidates": main_candidates,
        "development_candidates": dev_candidates,
        "statistics": dict(stats),
        "qualified_counts": {
            "development": len(dev_qualified),
            "main": len(main_qualified),
        },
        "exception_samples": exceptions,
        "environment": environment_metadata(),
    }


def archive_partial(path: Path) -> None:
    if not path.exists():
        return
    timestamp = time.strftime("%Y%m%d_%H%M%S")
    archived = path.with_name(f"{path.name}.failed_{timestamp}")
    if archived.exists():
        raise FileExistsError(f"refusing to overwrite archived partial: {archived}")
    path.rename(archived)


def pass2(
    config: dict[str, Any],
    checkpoint: dict[str, Any],
    candidate_path: Path,
    logger: logging.Logger,
    log_path: Path,
    started: float,
) -> dict[str, Any]:
    candidates = set(checkpoint["main_candidates"]) | set(checkpoint["development_candidates"])
    cap = max(
        int(scale["max_texts_per_site"]) for scale in config["scales"].values()
    )
    counts: Counter[str] = Counter()
    stats: Counter[str] = Counter()
    exact_seen: dict[str, str] = {}
    normalized_seen: dict[str, str] = {}
    exceptions: list[dict[str, Any]] = []
    scan_limit = int(checkpoint["scan_limit"])
    every = int(config["progress_every_records"])

    with candidate_path.open("x", encoding="utf-8", newline="\n") as output:
        for index, sample in enumerate(make_stream(config)):
            if index >= scan_limit:
                break
            stats["scanned_records"] += 1
            try:
                host = normalize_host(sample.get("url"))
                if host is None:
                    stats["invalid_url"] += 1
                    continue
                site_id = site_id_from_host(host)
                if site_id not in candidates:
                    stats["unselected_site"] += 1
                    continue
                if counts[site_id] >= cap:
                    stats["site_cap"] += 1
                    continue
                text, reason, text_stats = validate_text(sample.get("text"), config)
                if reason:
                    stats[reason] += 1
                    continue
                assert text is not None
                if contains_source_marker_or_url(text, host):
                    stats["url_or_source_marker"] += 1
                    continue
                exact_hash = sha256_text(text)
                normalized_hash = sha256_text(normalize_text(text))
                if exact_hash in exact_seen:
                    stats[
                        "exact_duplicate_within"
                        if exact_seen[exact_hash] == site_id
                        else "exact_duplicate_cross"
                    ] += 1
                    continue
                if normalized_hash in normalized_seen:
                    stats[
                        "normalized_duplicate_within"
                        if normalized_seen[normalized_hash] == site_id
                        else "normalized_duplicate_cross"
                    ] += 1
                    continue
                exact_seen[exact_hash] = site_id
                normalized_seen[normalized_hash] = site_id
                record = {
                    "site_id": site_id,
                    "text_sha256": exact_hash,
                    "normalized_text_sha256": normalized_hash,
                    "url_sha256": sha256_text(str(sample.get("url", "")).strip()),
                    "source_record_index": index,
                    "char_count": text_stats["char_count"],
                    "byte_count": text_stats["byte_count"],
                    "ascii_letter_ratio": text_stats["ascii_letter_ratio"],
                    "text": text,
                }
                output.write(json.dumps(record, ensure_ascii=False, sort_keys=True, allow_nan=False))
                output.write("\n")
                counts[site_id] += 1
                stats["retained_candidate_texts"] += 1
            except Exception as exc:
                stats["record_exceptions"] += 1
                if len(exceptions) < 20:
                    exceptions.append(
                        {"record_index": index, "type": type(exc).__name__, "message": str(exc)}
                    )
                logger.exception("final C4 pass2 record failure index=%d", index)
            if stats["scanned_records"] % every == 0:
                check_resources(config, started)
                progress(
                    logger,
                    started=started,
                    stage="final-data-pass2",
                    completed=stats["scanned_records"],
                    total=scan_limit,
                    successes=len(counts),
                    failures=stats["record_exceptions"],
                    log_path=log_path,
                    details=f"retained_candidate_texts={stats['retained_candidate_texts']}",
                )
    return {
        "statistics": dict(stats),
        "site_counts": dict(sorted(counts.items())),
        "exception_samples": exceptions,
    }


def choose_final_sites(
    config: dict[str, Any], checkpoint: dict[str, Any], pass2_result: dict[str, Any]
) -> dict[str, list[str]]:
    counts = pass2_result["site_counts"]
    main_config = config["scales"]["main"]
    dev_config = config["scales"]["development"]
    main_eligible = {
        site
        for site in checkpoint["main_candidates"]
        if int(counts.get(site, 0)) >= int(main_config["min_texts_per_site"])
    }
    main_required = scale_required(main_config)
    if len(main_eligible) < main_required:
        raise RuntimeError(
            f"pass2 retained only {len(main_eligible)} main sites; required={main_required}"
        )
    main_sites = rank_sites(main_eligible, "main-final", int(config["seed"]))[:main_required]
    dev_pool = (
        set(checkpoint["development_candidates"])
        | (set(checkpoint["main_candidates"]) - set(main_sites))
    )
    dev_eligible = {
        site
        for site in dev_pool
        if int(counts.get(site, 0)) >= int(dev_config["min_texts_per_site"])
    }
    dev_required = scale_required(dev_config)
    if len(dev_eligible) < dev_required:
        raise RuntimeError(
            f"pass2 retained only {len(dev_eligible)} development sites; required={dev_required}"
        )
    development_sites = rank_sites(
        dev_eligible, "development-final", int(config["seed"])
    )[:dev_required]
    if set(main_sites) & set(development_sites):
        raise AssertionError("Development and Main site sets overlap")
    return {"development": development_sites, "main": main_sites}


def write_final_corpus(
    config: dict[str, Any],
    candidate_path: Path,
    final_sites: dict[str, list[str]],
    output_path: Path,
) -> dict[str, Any]:
    selected = set(final_sites["development"]) | set(final_sites["main"])
    development_set = set(final_sites["development"])
    counts: Counter[str] = Counter()
    byte_total = 0
    with candidate_path.open("r", encoding="utf-8") as source, output_path.open(
        "x", encoding="utf-8", newline="\n"
    ) as output:
        for line in source:
            record = json.loads(line)
            site = record["site_id"]
            if site not in selected:
                continue
            scale_name = "development" if site in development_set else "main"
            cap = int(config["scales"][scale_name]["max_texts_per_site"])
            if counts[site] >= cap:
                continue
            output.write(json.dumps(record, ensure_ascii=False, sort_keys=True, allow_nan=False))
            output.write("\n")
            counts[site] += 1
            byte_total += int(record["byte_count"])
    return {
        "site_counts": dict(sorted(counts.items())),
        "text_count": sum(counts.values()),
        "text_byte_count": byte_total,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--log", type=Path, required=True)
    parser.add_argument("--resume", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    config = strict_json_load(args.config.resolve())
    log_path = args.log.resolve()
    logger = setup_logger("final.data", log_path)
    started = time.perf_counter()
    started_at = utc_now()
    corpus_dir = PROJECT_ROOT / config["corpus_dir"]
    checkpoint_path = corpus_dir / "pass1_checkpoint.json"
    candidate_partial = corpus_dir / "candidates.jsonl.partial"
    final_partial = corpus_dir / "texts.jsonl.partial"
    final_path = corpus_dir / "texts.jsonl"
    try:
        if args.resume:
            if not checkpoint_path.is_file():
                raise FileNotFoundError("resume requested but pass1 checkpoint is missing")
            checkpoint = strict_json_load(checkpoint_path)
            archive_partial(candidate_partial)
            archive_partial(final_partial)
        else:
            if corpus_dir.exists():
                raise FileExistsError(f"refusing to overwrite final corpus directory: {corpus_dir}")
            corpus_dir.mkdir(parents=True)
            progress(
                logger,
                started=started,
                stage="final-data-pass1-start",
                completed=0,
                total=int(config["max_stream_records"]),
                successes=0,
                failures=0,
                log_path=log_path,
            )
            checkpoint = pass1(config, logger, log_path, started)
            write_json_exclusive(checkpoint_path, checkpoint)

        progress(
            logger,
            started=started,
            stage="final-data-pass2-start",
            completed=0,
            total=int(checkpoint["scan_limit"]),
            successes=0,
            failures=0,
            log_path=log_path,
        )
        pass2_result = pass2(
            config, checkpoint, candidate_partial, logger, log_path, started
        )
        final_sites = choose_final_sites(config, checkpoint, pass2_result)
        corpus_result = write_final_corpus(
            config, candidate_partial, final_sites, final_partial
        )
        final_partial.rename(final_path)
        candidate_path = corpus_dir / "candidates.jsonl"
        candidate_partial.rename(candidate_path)
        index = {
            "schema_version": 1,
            "status": "success",
            "dataset_id": config["dataset_id"],
            "dataset_revision": config["dataset_revision"],
            "corpus_sha256": sha256_file(final_path),
            "site_count": len(corpus_result["site_counts"]),
            "text_count": corpus_result["text_count"],
            "text_byte_count": corpus_result["text_byte_count"],
            "site_counts": corpus_result["site_counts"],
            "scale_site_ids": final_sites,
        }
        write_json_exclusive(corpus_dir / "site_index.json", index)
        stats = {
            "schema_version": 1,
            "status": "success",
            "started_at_utc": started_at,
            "completed_at_utc": utc_now(),
            "elapsed_seconds": time.perf_counter() - started,
            "config": config,
            "pass1": checkpoint,
            "pass2": pass2_result,
            "corpus": corpus_result,
            "corpus_sha256": index["corpus_sha256"],
            "environment": environment_metadata(),
        }
        write_json_exclusive(corpus_dir / "collection_stats.json", stats)
        progress(
            logger,
            started=started,
            stage="final-data-complete",
            completed=corpus_result["text_count"],
            total=corpus_result["text_count"],
            successes=corpus_result["text_count"],
            failures=int(pass2_result["statistics"].get("record_exceptions", 0)),
            log_path=log_path,
            details=f"corpus_sha256={index['corpus_sha256']}",
        )
        return 0
    except BaseException:
        logger.exception("final data pipeline failed elapsed=%.3fs", time.perf_counter() - started)
        raise


if __name__ == "__main__":
    raise SystemExit(main())
