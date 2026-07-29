"""Build immutable paper-aligned and strict-disjoint Gate 2 manifests."""

from __future__ import annotations

import argparse
import random
import time
from pathlib import Path
from typing import Any

from src.utils.run_metadata import (
    PROJECT_ROOT,
    canonical_sha256,
    environment_metadata,
    log_progress,
    setup_logger,
    sha256_file,
    strict_json_load,
    utc_now,
    write_json_exclusive,
)


SEEDS = [20260726, 20260727, 20260728]
SHADOW_COUNT = 8


def with_manifest_hash(payload: dict[str, Any]) -> dict[str, Any]:
    result = dict(payload)
    result["manifest_sha256"] = canonical_sha256(payload)
    return result


def paper_shadow_plan(all_sites: list[str], target_sites: list[str], seed: int) -> tuple[list[dict[str, Any]], int]:
    sample_size = len(all_sites) // 2
    for attempt in range(1, 1001):
        rng = random.Random(seed * 1009 + attempt)
        plans = []
        for shadow_id in range(SHADOW_COUNT):
            training = sorted(rng.sample(all_sites, sample_size))
            plans.append(
                {
                    "shadow_id": shadow_id,
                    "auxiliary_site_ids": training,
                    "target_probe_site_ids": [],
                    "training_site_ids": training,
                }
            )
        valid = all(
            0 < sum(site in plan["training_site_ids"] for plan in plans) < SHADOW_COUNT
            for site in target_sites
        )
        if valid:
            return plans, attempt
    raise RuntimeError("unable to construct paper-aligned shadow plan with in/out coverage")


def strict_shadow_plan(target_sites: list[str], auxiliary_sites: list[str], seed: int) -> list[dict[str, Any]]:
    rng = random.Random(seed * 2003)
    probes_by_shadow: list[list[str]] = [[] for _ in range(SHADOW_COUNT)]
    for site in target_sites:
        for shadow_id in rng.sample(range(SHADOW_COUNT), SHADOW_COUNT // 2):
            probes_by_shadow[shadow_id].append(site)
    plans = []
    for shadow_id in range(SHADOW_COUNT):
        auxiliary_sample = sorted(rng.sample(auxiliary_sites, len(auxiliary_sites) // 2))
        probes = sorted(probes_by_shadow[shadow_id])
        plans.append(
            {
                "shadow_id": shadow_id,
                "auxiliary_site_ids": auxiliary_sample,
                "target_probe_site_ids": probes,
                "training_site_ids": sorted(auxiliary_sample + probes),
            }
        )
    return plans


def build_manifest(
    *,
    protocol: str,
    seed: int,
    target_sites: list[str],
    extra_auxiliary_sites: list[str],
    corpus_index: dict[str, Any],
    corpus_index_path: Path,
) -> dict[str, Any]:
    rng = random.Random(seed)
    member_sites = sorted(rng.sample(target_sites, len(target_sites) // 2))
    nonmember_sites = sorted(set(target_sites) - set(member_sites))
    all_sites = sorted(target_sites + extra_auxiliary_sites)

    if protocol == "paper_aligned":
        auxiliary_pool = all_sites
        shadow_plans, plan_attempt = paper_shadow_plan(all_sites, target_sites, seed)
        definition = (
            "Shared-universe protocol: each shadow independently samples half of all 192 sites; "
            "the target sites may also occur in the auxiliary universe, matching the audited code's pattern."
        )
    elif protocol == "strict_disjoint":
        auxiliary_pool = sorted(extra_auxiliary_sites)
        shadow_plans = strict_shadow_plan(target_sites, auxiliary_pool, seed)
        plan_attempt = 1
        definition = (
            "The 64-site auxiliary base pool is disjoint from all 128 target evaluation sites. "
            "Target sites are added only as explicit in/out probes (four of eight shadows each), "
            "not treated as auxiliary sites."
        )
    else:
        raise ValueError(f"unknown protocol: {protocol}")

    payload = {
        "schema_version": 1,
        "created_at_utc": utc_now(),
        "protocol": protocol,
        "protocol_definition": definition,
        "seed": seed,
        "dataset_id": corpus_index["dataset_id"],
        "dataset_revision": corpus_index["dataset_revision"],
        "corpus_path": str((corpus_index_path.parent / "texts.jsonl").relative_to(PROJECT_ROOT)),
        "corpus_sha256": corpus_index["corpus_sha256"],
        "site_index_sha256": sha256_file(corpus_index_path),
        "texts_per_site": min(corpus_index["site_counts"].values()),
        "target_pool_site_ids": sorted(target_sites),
        "target_member_site_ids": member_sites,
        "target_nonmember_site_ids": nonmember_sites,
        "target_training_site_ids": member_sites,
        "extra_auxiliary_site_ids": sorted(extra_auxiliary_sites),
        "auxiliary_pool_site_ids": auxiliary_pool,
        "target_auxiliary_overlap_site_ids": sorted(set(target_sites) & set(auxiliary_pool)),
        "shadow_count": SHADOW_COUNT,
        "shadow_plan_generation_attempt": plan_attempt,
        "shadow_plans": shadow_plans,
        "positive_class": "target tokenizer training member website",
        "environment": environment_metadata(),
    }
    return with_manifest_hash(payload)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--log", type=Path, required=True)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    config = strict_json_load(args.config.resolve())
    logger = setup_logger("gate2.data.manifest", args.log.resolve())
    started = time.perf_counter()
    manifest_root = PROJECT_ROOT / config["manifest_dir"]
    index_path = PROJECT_ROOT / config["corpus_dir"] / "site_index.json"
    try:
        if manifest_root.exists():
            raise FileExistsError(f"refusing to overwrite manifest directory: {manifest_root}")
        corpus_index = strict_json_load(index_path)
        site_ids = sorted(corpus_index["site_counts"])
        expected = int(config["target_site_count"]) + int(config["auxiliary_site_count"])
        if len(site_ids) != expected:
            raise RuntimeError(f"expected {expected} sites, found {len(site_ids)}")
        target_sites = site_ids[: int(config["target_site_count"])]
        extra_auxiliary_sites = site_ids[int(config["target_site_count"]) :]
        total = len(SEEDS) * 2
        completed = 0
        for protocol in ("paper_aligned", "strict_disjoint"):
            protocol_dir = manifest_root / protocol
            protocol_dir.mkdir(parents=True, exist_ok=True)
            for seed in SEEDS:
                manifest = build_manifest(
                    protocol=protocol,
                    seed=seed,
                    target_sites=target_sites,
                    extra_auxiliary_sites=extra_auxiliary_sites,
                    corpus_index=corpus_index,
                    corpus_index_path=index_path,
                )
                output = protocol_dir / f"seed_{seed}.json"
                write_json_exclusive(output, manifest)
                completed += 1
                log_progress(
                    logger,
                    started=started,
                    stage="manifest-build",
                    protocol=protocol,
                    seed=seed,
                    method="split-manifest",
                    completed=completed,
                    total=total,
                    result_path=output,
                )
        logger.info("manifest_generation_complete count=%d", completed)
        return 0
    except BaseException:
        logger.exception("Gate 2 manifest generation failed after %.3fs", time.perf_counter() - started)
        raise


if __name__ == "__main__":
    raise SystemExit(main())
