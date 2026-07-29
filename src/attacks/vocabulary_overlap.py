"""Eight-shadow vocabulary-overlap membership attack."""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

from tokenizers import Tokenizer

from src.attacks.metrics import compute_attack_metrics
from src.utils.run_metadata import (
    PROJECT_ROOT,
    environment_metadata,
    peak_working_set_bytes,
    sha256_file,
    strict_json_load,
    utc_now,
    write_json_exclusive,
)


def jaccard(left: set[str], right: set[str]) -> float:
    union = left | right
    return len(left & right) / len(union) if union else 0.0


def run_vocabulary_overlap(
    *,
    manifest_path: Path,
    target_artifact: Path,
    target_metadata_path: Path,
    shadow_dirs: list[Path],
    output_path: Path,
    vocab_size: int,
    method: dict[str, Any],
    bootstrap_iterations: int,
    bootstrap_confidence: float,
) -> dict[str, Any]:
    if output_path.exists():
        existing = strict_json_load(output_path)
        if existing.get("status") == "success":
            existing["checkpoint_reused"] = True
            return existing
        raise FileExistsError(f"refusing to overwrite attack result: {output_path}")
    started = time.perf_counter()
    manifest = strict_json_load(manifest_path)
    target_metadata = strict_json_load(target_metadata_path)
    if target_metadata["artifact_sha256"] != sha256_file(target_artifact):
        raise RuntimeError("target tokenizer artifact hash mismatch")
    expected = {
        "manifest_sha256": manifest["manifest_sha256"],
        "method_id": method["id"],
        "requested_vocab_size": vocab_size,
        "role": "target",
    }
    for key, value in expected.items():
        if target_metadata.get(key) != value:
            raise RuntimeError(f"target tokenizer {key} mismatch: expected={value!r}")
    target_vocab = set(Tokenizer.from_file(str(target_artifact)).get_vocab())
    plans = {int(plan["shadow_id"]): plan for plan in manifest["shadow_plans"]}
    shadow_vocabs: list[set[str]] = []
    shadow_training_sites: list[set[str]] = []
    shadow_artifact_hashes: list[str] = []
    for shadow_id, shadow_dir in enumerate(shadow_dirs):
        artifact = shadow_dir / "tokenizer.json"
        metadata = strict_json_load(shadow_dir / "metadata.json")
        if metadata["artifact_sha256"] != sha256_file(artifact):
            raise RuntimeError(f"shadow artifact hash mismatch: {artifact}")
        expected_shadow = {
            "manifest_sha256": manifest["manifest_sha256"],
            "method_id": method["id"],
            "requested_vocab_size": vocab_size,
            "role": "shadow",
            "shadow_id": shadow_id,
        }
        for key, value in expected_shadow.items():
            if metadata.get(key) != value:
                raise RuntimeError(f"shadow {shadow_id} tokenizer {key} mismatch: expected={value!r}")
        shadow_vocabs.append(set(Tokenizer.from_file(str(artifact)).get_vocab()))
        shadow_training_sites.append(set(plans[shadow_id]["training_site_ids"]))
        shadow_artifact_hashes.append(metadata["artifact_sha256"])

    members = set(manifest["target_member_site_ids"])
    details = []
    labels: list[int] = []
    scores: list[float] = []
    for site in sorted(manifest["target_pool_site_ids"]):
        in_vocabs = [vocab for vocab, sites in zip(shadow_vocabs, shadow_training_sites) if site in sites]
        out_vocabs = [vocab for vocab, sites in zip(shadow_vocabs, shadow_training_sites) if site not in sites]
        if not in_vocabs or not out_vocabs:
            raise RuntimeError(f"site lacks shadow in/out groups: {site}")
        tokens_in = set().union(*in_vocabs)
        tokens_out = set().union(*out_vocabs)
        nondistinctive = tokens_in & tokens_out
        filtered_target = target_vocab - nondistinctive
        in_scores = [jaccard(vocab - nondistinctive, filtered_target) for vocab in in_vocabs]
        out_scores = [jaccard(vocab - nondistinctive, filtered_target) for vocab in out_vocabs]
        score = 0.5 + sum(in_scores) / (2 * len(in_scores)) - sum(out_scores) / (2 * len(out_scores))
        label = int(site in members)
        labels.append(label)
        scores.append(score)
        details.append(
            {
                "site_id": site,
                "is_member": bool(label),
                "score": score,
                "in_shadow_count": len(in_vocabs),
                "out_shadow_count": len(out_vocabs),
                "nondistinctive_token_count": len(nondistinctive),
            }
        )
    metrics = compute_attack_metrics(
        labels,
        scores,
        seed=int(manifest["seed"]) + vocab_size + int(method["min_count_threshold"]) * 31 + 1,
        bootstrap_iterations=bootstrap_iterations,
        bootstrap_confidence=bootstrap_confidence,
    )
    result = {
        "schema_version": 1,
        "status": "success",
        "attack": "vocabulary_overlap",
        "score_definition": "fixed higher-is-member difference between mean in-shadow and out-shadow filtered Jaccard overlap",
        "protocol": manifest["protocol"],
        "seed": int(manifest["seed"]),
        "vocab_size": vocab_size,
        "actual_vocab_size": target_metadata["actual_vocab_size"],
        "method_id": method["id"],
        "defense": method["defense"],
        "min_count_threshold": int(method["min_count_threshold"]),
        "shadow_count": len(shadow_dirs),
        "data": {
            "site_count": len(manifest["target_pool_site_ids"]),
            "member_site_count": len(members),
            "nonmember_site_count": len(manifest["target_nonmember_site_ids"]),
            "texts_per_site": manifest["texts_per_site"],
            "dataset_revision": manifest["dataset_revision"],
            "manifest_sha256": manifest["manifest_sha256"],
        },
        "artifacts": {
            "target_tokenizer_sha256": target_metadata["artifact_sha256"],
            "target_tokenizer_metadata": str(target_metadata_path.relative_to(PROJECT_ROOT)),
            "shadow_tokenizer_sha256": shadow_artifact_hashes,
        },
        "metrics": metrics,
        "details": details,
        "elapsed_seconds": time.perf_counter() - started,
        "peak_memory_bytes": peak_working_set_bytes(),
        "completed_at_utc": utc_now(),
        "environment": environment_metadata(),
        "checkpoint_reused": False,
    }
    write_json_exclusive(output_path, result)
    return result
