"""Validate Gate 2 corpus deduplication, quality, and protocol boundaries."""

from __future__ import annotations

import argparse
import json
import logging
import statistics
import time
from collections import Counter
from pathlib import Path
from typing import Any

from src.data.build_pilot_manifest import SEEDS
from src.data.stream_c4_websites import URL_PATTERN, normalize_text, validate_text
from src.utils.run_metadata import (
    PROJECT_ROOT,
    canonical_sha256,
    environment_metadata,
    log_progress,
    setup_logger,
    sha256_file,
    strict_json_load,
    write_json_exclusive,
)


def percentile(values: list[int], q: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    position = (len(ordered) - 1) * q
    lower = int(position)
    upper = min(lower + 1, len(ordered) - 1)
    fraction = position - lower
    return ordered[lower] * (1 - fraction) + ordered[upper] * fraction


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--log", type=Path, required=True)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    config = strict_json_load(args.config.resolve())
    logger = setup_logger("gate2.data.validate", args.log.resolve())
    started = time.perf_counter()
    corpus_dir = PROJECT_ROOT / config["corpus_dir"]
    corpus_path = corpus_dir / "texts.jsonl"
    index_path = corpus_dir / "site_index.json"
    manifest_root = PROJECT_ROOT / config["manifest_dir"]
    output_path = PROJECT_ROOT / config["validation_path"]
    errors: list[str] = []
    counters: Counter[str] = Counter()
    site_counts: Counter[str] = Counter()
    exact_seen: dict[str, str] = {}
    normalized_seen: dict[str, str] = {}
    char_lengths: list[int] = []
    byte_lengths: list[int] = []
    site_ids_in_text = 0
    forbidden_url_fields = 0
    urls_in_text = 0

    try:
        if output_path.exists():
            raise FileExistsError(f"refusing to overwrite validation result: {output_path}")
        index = strict_json_load(index_path)
        with corpus_path.open("r", encoding="utf-8") as handle:
            for line_number, line in enumerate(handle, start=1):
                if not line.strip():
                    counters["blank_jsonl_lines"] += 1
                    continue
                record = json.loads(line)
                counters["text_count"] += 1
                site_id = record.get("site_id")
                text = record.get("text")
                if not site_id or not isinstance(text, str) or not text.strip():
                    counters["empty_or_invalid_text"] += 1
                    continue
                if "url" in record or "original_url" in record:
                    forbidden_url_fields += 1
                if site_id in text:
                    site_ids_in_text += 1
                if URL_PATTERN.search(text):
                    urls_in_text += 1
                cleaned, reason, stats = validate_text(text, config)
                if reason:
                    counters[reason] += 1
                assert cleaned is not None
                exact_hash = record["text_sha256"]
                normalized_hash = record["normalized_text_sha256"]
                if exact_hash in exact_seen:
                    counters[
                        "exact_duplicate_within" if exact_seen[exact_hash] == site_id else "exact_duplicate_cross"
                    ] += 1
                else:
                    exact_seen[exact_hash] = site_id
                if normalized_hash in normalized_seen:
                    counters[
                        "normalized_duplicate_within"
                        if normalized_seen[normalized_hash] == site_id
                        else "normalized_duplicate_cross"
                    ] += 1
                else:
                    normalized_seen[normalized_hash] = site_id
                if normalized_hash != __import__("hashlib").sha256(normalize_text(text).encode("utf-8")).hexdigest():
                    counters["normalized_hash_mismatch"] += 1
                site_counts[site_id] += 1
                char_lengths.append(stats["char_count"])
                byte_lengths.append(stats["byte_count"])

        if sha256_file(corpus_path) != index["corpus_sha256"]:
            errors.append("corpus SHA-256 does not match site index")
        if dict(sorted(site_counts.items())) != index["site_counts"]:
            errors.append("site counts do not match site index")
        if len(site_counts) != int(config["target_site_count"]) + int(config["auxiliary_site_count"]):
            errors.append("unexpected total site count")
        if min(site_counts.values(), default=0) < int(config["min_texts_per_site"]):
            errors.append("one or more sites are below min_texts_per_site")
        if max(site_counts.values(), default=0) > int(config["max_texts_per_site"]):
            errors.append("one or more sites exceed max_texts_per_site")
        duplicate_keys = [key for key in counters if "duplicate" in key and counters[key] > 0]
        if duplicate_keys:
            errors.append(f"retained corpus contains duplicates: {duplicate_keys}")
        if counters["empty_or_invalid_text"] or counters["encoding_anomaly"] or counters["language_anomaly"]:
            errors.append("retained corpus contains empty, encoding, or language anomalies")
        if forbidden_url_fields:
            errors.append("full URL field found in public corpus")
        if site_ids_in_text:
            errors.append("site identifier found inside text")
        if urls_in_text:
            errors.append("URL found inside retained text")

        manifest_results: dict[str, Any] = {}
        for protocol in ("paper_aligned", "strict_disjoint"):
            manifest_results[protocol] = {}
            for seed in SEEDS:
                path = manifest_root / protocol / f"seed_{seed}.json"
                manifest = strict_json_load(path)
                expected_hash = manifest.pop("manifest_sha256")
                actual_hash = canonical_sha256(manifest)
                manifest["manifest_sha256"] = expected_hash
                members = set(manifest["target_member_site_ids"])
                nonmembers = set(manifest["target_nonmember_site_ids"])
                target = set(manifest["target_pool_site_ids"])
                auxiliary = set(manifest["auxiliary_pool_site_ids"])
                protocol_errors: list[str] = []
                if expected_hash != actual_hash:
                    protocol_errors.append("manifest hash mismatch")
                if members & nonmembers or members | nonmembers != target:
                    protocol_errors.append("member/nonmember split is not an exact disjoint target partition")
                if len(target) != 128 or len(members) != 64 or len(nonmembers) != 64:
                    protocol_errors.append("target/member/nonmember sizes are invalid")
                if manifest["corpus_sha256"] != index["corpus_sha256"]:
                    protocol_errors.append("manifest corpus hash mismatch")
                if protocol == "strict_disjoint" and target & auxiliary:
                    protocol_errors.append("strict_disjoint target and auxiliary pools overlap")
                if protocol == "paper_aligned" and len(target & auxiliary) != 128:
                    protocol_errors.append("paper_aligned target pool is not within shared auxiliary universe")
                for site in target:
                    in_count = sum(site in plan["training_site_ids"] for plan in manifest["shadow_plans"])
                    if not 0 < in_count < manifest["shadow_count"]:
                        protocol_errors.append(f"target site lacks shadow in/out coverage: {site}")
                        break
                manifest_results[protocol][str(seed)] = {
                    "manifest_sha256": expected_hash,
                    "target_site_count": len(target),
                    "member_site_count": len(members),
                    "nonmember_site_count": len(nonmembers),
                    "auxiliary_site_count": len(auxiliary),
                    "target_auxiliary_overlap_count": len(target & auxiliary),
                    "errors": protocol_errors,
                }
                errors.extend(f"{protocol}/{seed}: {message}" for message in protocol_errors)

        result = {
            "schema_version": 1,
            "status": "success" if not errors else "failed",
            "dataset_id": config["dataset_id"],
            "dataset_revision": config["dataset_revision"],
            "corpus_sha256": sha256_file(corpus_path),
            "site_index_sha256": sha256_file(index_path),
            "site_count": len(site_counts),
            "text_count": counters["text_count"],
            "site_text_counts": {
                "minimum": min(site_counts.values(), default=0),
                "maximum": max(site_counts.values(), default=0),
                "mean": statistics.fmean(site_counts.values()) if site_counts else 0.0,
            },
            "text_char_length": {
                "minimum": min(char_lengths, default=0),
                "p25": percentile(char_lengths, 0.25),
                "median": percentile(char_lengths, 0.5),
                "p75": percentile(char_lengths, 0.75),
                "p95": percentile(char_lengths, 0.95),
                "maximum": max(char_lengths, default=0),
            },
            "text_byte_length": {
                "minimum": min(byte_lengths, default=0),
                "median": percentile(byte_lengths, 0.5),
                "p95": percentile(byte_lengths, 0.95),
                "maximum": max(byte_lengths, default=0),
                "total": sum(byte_lengths),
            },
            "duplicate_and_anomaly_counts": dict(counters),
            "forbidden_full_url_field_count": forbidden_url_fields,
            "site_id_in_text_count": site_ids_in_text,
            "url_in_text_count": urls_in_text,
            "protocols": manifest_results,
            "errors": errors,
            "elapsed_seconds": time.perf_counter() - started,
            "environment": environment_metadata(),
        }
        write_json_exclusive(output_path, result)
        log_progress(
            logger,
            started=started,
            stage="data-validation-complete",
            seed=config["seed"],
            method="quality-validation",
            completed=counters["text_count"],
            total=index["text_count"],
            failures=len(errors),
            result_path=output_path,
            level=logging.ERROR if errors else logging.INFO,
        )
        if errors:
            raise RuntimeError(f"Gate 2 data validation failed with {len(errors)} errors")
        return 0
    except BaseException:
        logger.exception("Gate 2 data validation failed after %.3fs", time.perf_counter() - started)
        raise


if __name__ == "__main__":
    raise SystemExit(main())
