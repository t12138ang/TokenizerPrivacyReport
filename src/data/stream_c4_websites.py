"""Collect a bounded natural C4 pilot corpus with a fixed dataset revision."""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import os
import random
import re
import time
import unicodedata
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Iterator
from urllib.parse import urlsplit

from src.utils.run_metadata import (
    PROJECT_ROOT,
    environment_metadata,
    log_progress,
    peak_working_set_bytes,
    setup_logger,
    sha256_file,
    sha256_text,
    strict_json_load,
    utc_now,
    write_json_exclusive,
)


URL_PATTERN = re.compile(r"(?i)\b(?:https?://|www\.)\S+")


def contains_source_marker_or_url(text: str, host: str) -> bool:
    folded = unicodedata.normalize("NFKC", text).casefold()
    return bool(URL_PATTERN.search(text)) or host.casefold() in folded


def enforce_resource_limits(config: dict[str, Any], started: float) -> None:
    elapsed = time.perf_counter() - started
    if elapsed > float(config["max_elapsed_seconds"]):
        raise RuntimeError(f"Gate 2 data collection exceeded {config['max_elapsed_seconds']} seconds")
    peak = int(peak_working_set_bytes() or 0)
    if peak > int(config["max_peak_memory_bytes"]):
        raise RuntimeError(f"Gate 2 data collection exceeded {config['max_peak_memory_bytes']} peak bytes")


def normalize_host(url: str) -> str | None:
    if not isinstance(url, str) or not url.strip():
        return None
    try:
        parsed = urlsplit(url.strip())
        host = parsed.hostname
        if not host:
            return None
        host = host.rstrip(".").lower().encode("idna").decode("ascii")
        if host.startswith("www."):
            host = host[4:]
        if not host or len(host) > 253 or "." not in host:
            return None
        return host
    except (UnicodeError, ValueError):
        return None


def site_id_from_host(host: str) -> str:
    return "site_" + sha256_text(host)[:20]


def normalize_text(text: str) -> str:
    return " ".join(unicodedata.normalize("NFKC", text).casefold().split())


def validate_text(text: Any, config: dict[str, Any]) -> tuple[str | None, str | None, dict[str, Any]]:
    if not isinstance(text, str) or not text.strip():
        return None, "empty_text", {}
    cleaned = text.strip()
    if "\x00" in cleaned:
        return None, "encoding_anomaly", {}
    char_count = len(cleaned)
    if char_count < int(config["min_text_chars"]):
        return None, "too_short", {"char_count": char_count}
    if char_count > int(config["max_text_chars"]):
        return None, "too_long", {"char_count": char_count}
    try:
        encoded = cleaned.encode("utf-8", errors="strict")
    except UnicodeError:
        return None, "encoding_anomaly", {}
    letters = [character for character in cleaned if character.isalpha()]
    ascii_letters = sum(character.isascii() for character in letters)
    ratio = ascii_letters / len(letters) if letters else 0.0
    if len(letters) < int(config["min_alpha_chars"]) or ratio < float(config["min_ascii_letter_ratio"]):
        return None, "language_anomaly", {
            "alpha_chars": len(letters),
            "ascii_letter_ratio": ratio,
        }
    return cleaned, None, {
        "char_count": char_count,
        "byte_count": len(encoded),
        "alpha_chars": len(letters),
        "ascii_letter_ratio": ratio,
    }


def make_stream(config: dict[str, Any]) -> Iterator[dict[str, Any]]:
    # Keep normalization/validation importable in a dependency-light test
    # process.  The heavyweight network dependency is required only when the
    # stream is actually opened.
    from datasets import load_dataset

    dataset = load_dataset(
        path=config["dataset_id"],
        name=config["dataset_config"],
        split=config["dataset_split"],
        revision=config["dataset_revision"],
        streaming=True,
        cache_dir=str(PROJECT_ROOT / config["hf_cache_dir"]),
    )
    return iter(dataset)


def first_pass(config: dict[str, Any], logger: logging.Logger, started: float) -> tuple[list[str], dict[str, Any]]:
    site_counts: Counter[str] = Counter()
    exact_seen: dict[str, str] = {}
    normalized_seen: dict[str, str] = {}
    stats: Counter[str] = Counter()
    exception_samples: list[dict[str, Any]] = []
    max_records = int(config["max_stream_records"])
    cap = int(config["max_texts_per_site"])
    minimum = int(config["min_texts_per_site"])
    buffer_count = int(config["qualified_site_buffer"])
    progress_every = int(config["progress_every_records"])

    for index, sample in enumerate(make_stream(config)):
        if index >= max_records:
            break
        if index % 250 == 0:
            enforce_resource_limits(config, started)
        stats["scanned_records"] += 1
        try:
            host = normalize_host(sample.get("url"))
            if host is None:
                stats["invalid_url"] += 1
                continue
            site_id = site_id_from_host(host)
            if site_counts[site_id] >= cap:
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
            exact_hash = sha256_text(text)
            normalized_hash = sha256_text(normalize_text(text))
            if exact_hash in exact_seen:
                stats["exact_duplicate_within" if exact_seen[exact_hash] == site_id else "exact_duplicate_cross"] += 1
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
            site_counts[site_id] += 1
            stats["accepted_for_counting"] += 1
        except Exception as exc:
            stats["record_exceptions"] += 1
            if len(exception_samples) < 20:
                exception_samples.append({"record_index": index, "type": type(exc).__name__, "message": str(exc)})
            logger.exception("C4 first-pass record failure at index=%d", index)

        qualified = sum(count >= minimum for count in site_counts.values())
        if stats["scanned_records"] % progress_every == 0:
            log_progress(
                logger,
                started=started,
                stage="data-pass1-count-sites",
                seed=config["seed"],
                method="c4-streaming",
                completed=stats["scanned_records"],
                total=max_records,
                failures=stats["record_exceptions"],
                result_path=PROJECT_ROOT / config["corpus_dir"],
            )
            logger.info("qualified_sites=%d/%d observed_sites=%d", qualified, buffer_count, len(site_counts))
        if qualified >= buffer_count:
            stats["stopped_after_qualified_buffer"] = 1
            break

    qualified_sites = [site for site, count in site_counts.items() if count >= minimum]
    required = int(config["target_site_count"]) + int(config["auxiliary_site_count"])
    if len(qualified_sites) < required:
        raise RuntimeError(
            f"max_stream_records reached with only {len(qualified_sites)} qualified sites; "
            f"required={required}; quality constraints were not relaxed"
        )
    seed = int(config["seed"])
    ranked = sorted(qualified_sites, key=lambda site: sha256_text(f"{seed}:{site}"))
    selected = ranked[:required]
    return selected, {
        "statistics": dict(stats),
        "observed_site_count": len(site_counts),
        "qualified_site_count": len(qualified_sites),
        "selected_site_count": len(selected),
        "scanned_records": stats["scanned_records"],
        "exception_samples": exception_samples,
    }


def second_pass(
    config: dict[str, Any],
    selected_sites: list[str],
    scan_limit: int,
    output_path: Path,
    logger: logging.Logger,
    started: float,
) -> dict[str, Any]:
    selected = set(selected_sites)
    counts: Counter[str] = Counter()
    exact_seen: dict[str, str] = {}
    normalized_seen: dict[str, str] = {}
    stats: Counter[str] = Counter()
    exception_samples: list[dict[str, Any]] = []
    cap = int(config["max_texts_per_site"])
    minimum = int(config["min_texts_per_site"])
    progress_every = int(config["progress_every_records"])

    with output_path.open("x", encoding="utf-8", newline="\n") as output:
        for index, sample in enumerate(make_stream(config)):
            if index >= scan_limit:
                break
            if index % 250 == 0:
                enforce_resource_limits(config, started)
            stats["scanned_records"] += 1
            try:
                host = normalize_host(sample.get("url"))
                if host is None:
                    stats["invalid_url"] += 1
                    continue
                site_id = site_id_from_host(host)
                if site_id not in selected:
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
                    stats["exact_duplicate_within" if exact_seen[exact_hash] == site_id else "exact_duplicate_cross"] += 1
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
                stats["retained_texts"] += 1
            except Exception as exc:
                stats["record_exceptions"] += 1
                if len(exception_samples) < 20:
                    exception_samples.append({"record_index": index, "type": type(exc).__name__, "message": str(exc)})
                logger.exception("C4 second-pass record failure at index=%d", index)

            if stats["scanned_records"] % progress_every == 0:
                log_progress(
                    logger,
                    started=started,
                    stage="data-pass2-write-jsonl",
                    seed=config["seed"],
                    method="c4-streaming",
                    completed=stats["scanned_records"],
                    total=scan_limit,
                    failures=stats["record_exceptions"],
                    result_path=output_path,
                )
                complete_sites = sum(count >= cap for count in counts.values())
                logger.info("complete_sites=%d/%d retained_texts=%d", complete_sites, len(selected), stats["retained_texts"])
            if len(counts) == len(selected) and all(counts[site] >= cap for site in selected):
                stats["stopped_after_all_selected_complete"] = 1
                break

    incomplete = {site: counts[site] for site in selected_sites if counts[site] < minimum}
    if incomplete:
        raise RuntimeError(f"selected sites fell below min_texts_per_site during deterministic second pass: {incomplete}")
    return {
        "statistics": dict(stats),
        "site_counts": dict(sorted(counts.items())),
        "exception_samples": exception_samples,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--log", type=Path, required=True)
    parser.add_argument("--probe-records", type=int, default=0)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    config = strict_json_load(args.config.resolve())
    logger = setup_logger("gate2.data.collect", args.log.resolve())
    started = time.perf_counter()
    corpus_dir = PROJECT_ROOT / config["corpus_dir"]

    try:
        if args.probe_records:
            log_progress(
                logger,
                started=started,
                stage="data-network-probe",
                seed=config["seed"],
                method="c4-streaming",
                completed=0,
                total=args.probe_records,
                result_path="probe-only-no-output",
            )
            seen = 0
            for sample in make_stream(config):
                if not isinstance(sample, dict) or "text" not in sample or "url" not in sample:
                    raise RuntimeError("unexpected C4 streaming schema")
                seen += 1
                if seen >= args.probe_records:
                    break
            logger.info("probe_success records=%d revision=%s", seen, config["dataset_revision"])
            return 0

        if corpus_dir.exists():
            raise FileExistsError(f"refusing to overwrite existing corpus directory: {corpus_dir}")
        corpus_dir.mkdir(parents=True)
        partial_path = corpus_dir / "texts.jsonl.partial"
        final_path = corpus_dir / "texts.jsonl"
        stats_path = corpus_dir / "collection_stats.json"
        index_path = corpus_dir / "site_index.json"

        random.seed(int(config["seed"]))
        os.environ["TOKENIZERS_PARALLELISM"] = "false"
        log_progress(
            logger,
            started=started,
            stage="data-start",
            seed=config["seed"],
            method="c4-streaming",
            completed=0,
            total=config["max_stream_records"],
            result_path=corpus_dir,
        )
        selected_sites, pass1 = first_pass(config, logger, started)
        pass2 = second_pass(
            config,
            selected_sites,
            int(pass1["scanned_records"]),
            partial_path,
            logger,
            started,
        )
        partial_path.rename(final_path)
        corpus_hash = sha256_file(final_path)
        index_payload = {
            "schema_version": 1,
            "dataset_id": config["dataset_id"],
            "dataset_revision": config["dataset_revision"],
            "corpus_sha256": corpus_hash,
            "site_count": len(selected_sites),
            "text_count": sum(pass2["site_counts"].values()),
            "site_counts": pass2["site_counts"],
        }
        write_json_exclusive(index_path, index_payload)
        stats_payload = {
            "schema_version": 1,
            "status": "success",
            "started_at_utc": utc_now(),
            "completed_at_utc": utc_now(),
            "elapsed_seconds": time.perf_counter() - started,
            "config": config,
            "dataset_revision": config["dataset_revision"],
            "corpus_sha256": corpus_hash,
            "site_index_sha256": sha256_file(index_path),
            "pass1": pass1,
            "pass2": pass2,
            "environment": environment_metadata(),
        }
        write_json_exclusive(stats_path, stats_payload)
        log_progress(
            logger,
            started=started,
            stage="data-complete",
            seed=config["seed"],
            method="c4-streaming",
            completed=pass2["statistics"]["retained_texts"],
            total=len(selected_sites) * int(config["max_texts_per_site"]),
            failures=pass1["statistics"].get("record_exceptions", 0)
            + pass2["statistics"].get("record_exceptions", 0),
            result_path=final_path,
        )
        return 0
    except BaseException:
        logger.exception("Gate 2 C4 collection failed after %.3fs", time.perf_counter() - started)
        raise


if __name__ == "__main__":
    raise SystemExit(main())
