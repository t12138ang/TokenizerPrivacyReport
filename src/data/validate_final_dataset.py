"""Strictly validate final corpus isolation, deduplication, and manifests."""

from __future__ import annotations

import argparse
import json
import statistics
import time
from collections import Counter
from pathlib import Path
from typing import Any

from src.data.build_final_manifests import SEEDS
from src.data.stream_c4_websites import URL_PATTERN, normalize_text, validate_text
from src.data.stream_final_c4 import scale_required
from src.utils.run_metadata import (
    PROJECT_ROOT,
    canonical_sha256,
    environment_metadata,
    setup_logger,
    sha256_file,
    sha256_text,
    strict_json_load,
    write_json_exclusive,
)


def percentile(values: list[int], q: float) -> float:
    ordered = sorted(values)
    if not ordered:
        return 0.0
    position = (len(ordered) - 1) * q
    lower = int(position)
    upper = min(lower + 1, len(ordered) - 1)
    fraction = position - lower
    return ordered[lower] * (1 - fraction) + ordered[upper] * fraction


def validate_manifest(
    manifest: dict[str, Any], config: dict[str, Any], index: dict[str, Any]
) -> list[str]:
    errors: list[str] = []
    scale_name = manifest["scale"]
    scale = config["scales"][scale_name]
    stored_hash = manifest["manifest_sha256"]
    unhashed = dict(manifest)
    unhashed.pop("manifest_sha256")
    if stored_hash != canonical_sha256(unhashed):
        errors.append("manifest hash mismatch")
    target = set(manifest["target_pool_site_ids"])
    members = set(manifest["target_member_site_ids"])
    nonmembers = set(manifest["target_nonmember_site_ids"])
    shadow_aux = set(manifest["shadow_auxiliary_site_ids"])
    public = set(manifest["public_candidate_site_ids"])
    if members & nonmembers or members | nonmembers != target:
        errors.append("member/nonmember is not an exact disjoint target partition")
    if target & shadow_aux or target & public or shadow_aux & public:
        errors.append("target, shadow auxiliary, and public candidate sets are not pairwise disjoint")
    if len(target) != int(scale["target_site_count"]):
        errors.append("target site count mismatch")
    if len(shadow_aux) != int(scale["shadow_auxiliary_site_count"]):
        errors.append("shadow auxiliary site count mismatch")
    if len(public) != int(scale["public_candidate_site_count"]):
        errors.append("public candidate site count mismatch")
    if target | shadow_aux | public != set(manifest["all_scale_site_ids"]):
        errors.append("role partitions do not cover scale site set")
    if manifest["corpus_sha256"] != index["corpus_sha256"]:
        errors.append("manifest corpus hash mismatch")
    if len(manifest["shadow_plans"]) != int(scale["max_shadow_count"]):
        errors.append("shadow plan count mismatch")
    for plan in manifest["shadow_plans"]:
        training = set(plan["training_site_ids"])
        if training & public:
            errors.append("public candidate site appears in shadow training")
            break
        if set(plan["auxiliary_site_ids"]) - shadow_aux:
            errors.append("shadow plan uses a non-auxiliary site as auxiliary data")
            break
    for prefix in manifest["shadow_prefixes"]:
        for site in target:
            in_count = sum(
                site in plan["target_probe_site_ids"]
                for plan in manifest["shadow_plans"][: int(prefix)]
            )
            if in_count != int(prefix) // 2:
                errors.append(f"target probe is not half-in at shadow prefix {prefix}")
                return errors
    return errors


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--log", type=Path, required=True)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    config = strict_json_load(args.config.resolve())
    logger = setup_logger("final.data.validate", args.log.resolve())
    started = time.perf_counter()
    corpus_dir = PROJECT_ROOT / config["corpus_dir"]
    corpus_path = corpus_dir / "texts.jsonl"
    index_path = corpus_dir / "site_index.json"
    index = strict_json_load(index_path)
    output = PROJECT_ROOT / config["validation_path"]
    if output.exists():
        raise FileExistsError(f"refusing to overwrite final validation: {output}")

    errors: list[str] = []
    counts: Counter[str] = Counter()
    anomalies: Counter[str] = Counter()
    exact_seen: dict[str, str] = {}
    normalized_seen: dict[str, str] = {}
    lengths: list[int] = []
    byte_lengths: list[int] = []
    with corpus_path.open("r", encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            if not line.strip():
                anomalies["blank_line"] += 1
                continue
            record = json.loads(line)
            site = record.get("site_id")
            text = record.get("text")
            if not site or not isinstance(text, str) or not text.strip():
                anomalies["empty_or_invalid_text"] += 1
                continue
            if "url" in record or "original_url" in record:
                anomalies["forbidden_full_url_field"] += 1
            if site in text:
                anomalies["site_id_in_text"] += 1
            if URL_PATTERN.search(text):
                anomalies["url_in_text"] += 1
            cleaned, reason, stats = validate_text(text, config)
            if reason:
                anomalies[reason] += 1
                continue
            assert cleaned is not None
            exact_hash = sha256_text(text)
            normalized_hash = sha256_text(normalize_text(text))
            if exact_hash != record.get("text_sha256"):
                anomalies["exact_hash_mismatch"] += 1
            if normalized_hash != record.get("normalized_text_sha256"):
                anomalies["normalized_hash_mismatch"] += 1
            if exact_hash in exact_seen:
                anomalies[
                    "exact_duplicate_within"
                    if exact_seen[exact_hash] == site
                    else "exact_duplicate_cross"
                ] += 1
            else:
                exact_seen[exact_hash] = site
            if normalized_hash in normalized_seen:
                anomalies[
                    "normalized_duplicate_within"
                    if normalized_seen[normalized_hash] == site
                    else "normalized_duplicate_cross"
                ] += 1
            else:
                normalized_seen[normalized_hash] = site
            counts[site] += 1
            lengths.append(stats["char_count"])
            byte_lengths.append(stats["byte_count"])

    if sha256_file(corpus_path) != index["corpus_sha256"]:
        errors.append("corpus SHA-256 mismatch")
    if dict(sorted(counts.items())) != index["site_counts"]:
        errors.append("site counts mismatch index")
    duplicate_or_content_anomalies = {
        key: value for key, value in anomalies.items() if value > 0
    }
    if duplicate_or_content_anomalies:
        errors.append(f"retained corpus anomalies: {duplicate_or_content_anomalies}")
    scale_sets = {name: set(sites) for name, sites in index["scale_site_ids"].items()}
    if scale_sets["development"] & scale_sets["main"]:
        errors.append("Development and Main overlap")
    manifest_results: dict[str, Any] = {}
    manifest_root = PROJECT_ROOT / config["manifest_dir"]
    for scale_name, scale in config["scales"].items():
        sites = scale_sets[scale_name]
        if len(sites) != scale_required(scale):
            errors.append(f"{scale_name}: site count mismatch")
        values = [counts[site] for site in sites]
        if min(values, default=0) < int(scale["min_texts_per_site"]):
            errors.append(f"{scale_name}: site below minimum")
        if max(values, default=0) > int(scale["max_texts_per_site"]):
            errors.append(f"{scale_name}: site exceeds maximum")
        manifest_results[scale_name] = {}
        for seed in SEEDS:
            path = manifest_root / scale_name / "strict_disjoint" / f"seed_{seed}.json"
            manifest = strict_json_load(path)
            manifest_errors = validate_manifest(manifest, config, index)
            errors.extend(f"{scale_name}/{seed}: {item}" for item in manifest_errors)
            manifest_results[scale_name][str(seed)] = {
                "manifest_sha256": manifest["manifest_sha256"],
                "target_site_count": len(manifest["target_pool_site_ids"]),
                "member_site_count": len(manifest["target_member_site_ids"]),
                "nonmember_site_count": len(manifest["target_nonmember_site_ids"]),
                "shadow_auxiliary_site_count": len(manifest["shadow_auxiliary_site_ids"]),
                "public_candidate_site_count": len(manifest["public_candidate_site_ids"]),
                "max_shadow_count": manifest["max_shadow_count"],
                "errors": manifest_errors,
            }
    result = {
        "schema_version": 1,
        "status": "success" if not errors else "failed",
        "dataset_id": index["dataset_id"],
        "dataset_revision": index["dataset_revision"],
        "corpus_sha256": index["corpus_sha256"],
        "site_index_sha256": sha256_file(index_path),
        "site_count": len(counts),
        "text_count": sum(counts.values()),
        "text_char_length": {
            "minimum": min(lengths, default=0),
            "median": percentile(lengths, 0.5),
            "p95": percentile(lengths, 0.95),
            "maximum": max(lengths, default=0),
        },
        "text_byte_length": {
            "minimum": min(byte_lengths, default=0),
            "median": percentile(byte_lengths, 0.5),
            "p95": percentile(byte_lengths, 0.95),
            "maximum": max(byte_lengths, default=0),
            "total": sum(byte_lengths),
        },
        "texts_per_site": {
            "minimum": min(counts.values(), default=0),
            "mean": statistics.fmean(counts.values()) if counts else 0.0,
            "maximum": max(counts.values(), default=0),
        },
        "duplicate_and_anomaly_counts": dict(anomalies),
        "scale_site_counts": {name: len(sites) for name, sites in scale_sets.items()},
        "protocols": manifest_results,
        "errors": errors,
        "elapsed_seconds": time.perf_counter() - started,
        "environment": environment_metadata(),
    }
    write_json_exclusive(output, result)
    logger.info(
        "stage=final-data-validation | scale=development+main | protocol=strict_disjoint | "
        "method=validation | attack=n/a | epsilon=n/a | clipping=n/a | batch=n/a | vocab=n/a | "
        "seed=all | task=%d/%d | shadow=n/a | elapsed=%.3fs | eta=0s | successes=%d | failures=%d | "
        "log=%s | result=%s",
        result["text_count"],
        result["text_count"],
        time.perf_counter() - started,
        result["text_count"],
        len(errors),
        args.log,
        output,
    )
    if errors:
        raise RuntimeError(f"final data validation failed with {len(errors)} errors")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
