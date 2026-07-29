"""Build immutable disjoint Development/Main manifests for the final study."""

from __future__ import annotations

import argparse
import random
import time
from pathlib import Path
from typing import Any

from src.data.stream_final_c4 import rank_sites, scale_required
from src.utils.run_metadata import (
    PROJECT_ROOT,
    canonical_sha256,
    environment_metadata,
    setup_logger,
    sha256_file,
    strict_json_load,
    utc_now,
    write_json_exclusive,
)


SEEDS = (20260726, 20260727, 20260728)
SHADOW_PREFIXES = (8, 16, 32, 64, 96)


def add_hash(payload: dict[str, Any]) -> dict[str, Any]:
    result = dict(payload)
    result["manifest_sha256"] = canonical_sha256(payload)
    return result


def prefix_balanced_probe_assignments(
    target_sites: list[str], max_shadow_count: int, seed: int
) -> list[list[str]]:
    valid_prefixes = [value for value in SHADOW_PREFIXES if value <= max_shadow_count]
    if not valid_prefixes or valid_prefixes[-1] != max_shadow_count:
        valid_prefixes.append(max_shadow_count)
    segments: list[range] = []
    start = 0
    for end in valid_prefixes:
        if (end - start) % 2:
            raise ValueError("shadow prefix segments must have even length")
        segments.append(range(start, end))
        start = end
    assignments: list[list[str]] = [[] for _ in range(max_shadow_count)]
    for site_index, site in enumerate(target_sites):
        rng = random.Random(seed * 1000003 + site_index)
        for segment in segments:
            chosen = rng.sample(list(segment), len(segment) // 2)
            for shadow_id in chosen:
                assignments[shadow_id].append(site)
    return [sorted(sites) for sites in assignments]


def build_manifest(
    *,
    scale_name: str,
    scale: dict[str, Any],
    scale_sites: list[str],
    seed: int,
    index: dict[str, Any],
    index_path: Path,
) -> dict[str, Any]:
    expected = scale_required(scale)
    if len(scale_sites) != expected:
        raise ValueError(f"{scale_name}: expected {expected} sites, found {len(scale_sites)}")
    ranked = rank_sites(scale_sites, f"{scale_name}-roles", seed)
    target_count = int(scale["target_site_count"])
    shadow_count = int(scale["shadow_auxiliary_site_count"])
    public_count = int(scale["public_candidate_site_count"])
    target_sites = sorted(ranked[:target_count])
    shadow_auxiliary = sorted(ranked[target_count : target_count + shadow_count])
    public_candidates = sorted(ranked[target_count + shadow_count :])
    if len(public_candidates) != public_count:
        raise AssertionError("public candidate site count mismatch")

    members = sorted(
        random.Random(seed).sample(target_sites, len(target_sites) // 2)
    )
    nonmembers = sorted(set(target_sites) - set(members))
    max_shadows = int(scale["max_shadow_count"])
    probes = prefix_balanced_probe_assignments(target_sites, max_shadows, seed)
    shadow_plans = []
    for shadow_id in range(max_shadows):
        rng = random.Random(seed * 2000003 + shadow_id)
        auxiliary = sorted(
            rng.sample(shadow_auxiliary, max(1, len(shadow_auxiliary) // 2))
        )
        shadow_plans.append(
            {
                "shadow_id": shadow_id,
                "auxiliary_site_ids": auxiliary,
                "target_probe_site_ids": probes[shadow_id],
                "training_site_ids": sorted(auxiliary + probes[shadow_id]),
            }
        )

    prefixes = [value for value in SHADOW_PREFIXES if value <= max_shadows]
    if max_shadows not in prefixes:
        prefixes.append(max_shadows)
    payload = {
        "schema_version": 1,
        "created_at_utc": utc_now(),
        "scale": scale_name,
        "protocol": "strict_disjoint",
        "protocol_definition": (
            "Target evaluation, shadow-only auxiliary, and public-candidate sites are pairwise disjoint. "
            "Target sites enter shadows only as explicit prefix-balanced in/out probes."
        ),
        "seed": seed,
        "dataset_id": index["dataset_id"],
        "dataset_revision": index["dataset_revision"],
        "corpus_path": str((index_path.parent / "texts.jsonl").relative_to(PROJECT_ROOT)),
        "corpus_sha256": index["corpus_sha256"],
        "site_index_sha256": sha256_file(index_path),
        "minimum_texts_per_site": int(scale["min_texts_per_site"]),
        "maximum_texts_per_site": int(scale["max_texts_per_site"]),
        "all_scale_site_ids": sorted(scale_sites),
        "target_pool_site_ids": target_sites,
        "target_member_site_ids": members,
        "target_nonmember_site_ids": nonmembers,
        "target_training_site_ids": members,
        "shadow_auxiliary_site_ids": shadow_auxiliary,
        "public_candidate_site_ids": public_candidates,
        "public_candidate_source": "fixed disjoint C4 auxiliary sites at the same dataset revision",
        "max_shadow_count": max_shadows,
        "shadow_prefixes": prefixes,
        "shadow_plans": shadow_plans,
        "positive_class": "target tokenizer training member website",
        "environment": environment_metadata(),
    }
    return add_hash(payload)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--log", type=Path, required=True)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    config = strict_json_load(args.config.resolve())
    logger = setup_logger("final.manifests", args.log.resolve())
    started = time.perf_counter()
    index_path = PROJECT_ROOT / config["corpus_dir"] / "site_index.json"
    index = strict_json_load(index_path)
    root = PROJECT_ROOT / config["manifest_dir"]
    if root.exists():
        raise FileExistsError(f"refusing to overwrite final manifests: {root}")
    total = len(config["scales"]) * len(SEEDS)
    completed = 0
    for scale_name, scale in config["scales"].items():
        directory = root / scale_name / "strict_disjoint"
        directory.mkdir(parents=True, exist_ok=True)
        scale_sites = index["scale_site_ids"][scale_name]
        for seed in SEEDS:
            manifest = build_manifest(
                scale_name=scale_name,
                scale=scale,
                scale_sites=scale_sites,
                seed=seed,
                index=index,
                index_path=index_path,
            )
            output = directory / f"seed_{seed}.json"
            write_json_exclusive(output, manifest)
            completed += 1
            logger.info(
                "stage=final-manifest | scale=%s | protocol=strict_disjoint | method=n/a | "
                "attack=n/a | epsilon=n/a | clipping=n/a | batch=n/a | vocab=n/a | seed=%d | "
                "task=%d/%d | shadow=n/a | elapsed=%.3fs | eta=n/a | successes=%d | failures=0 | "
                "log=%s | result=%s",
                scale_name,
                seed,
                completed,
                total,
                time.perf_counter() - started,
                completed,
                args.log,
                output,
            )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
