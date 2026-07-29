"""Compression-rate membership attack for one Gate 2 combination."""

from __future__ import annotations

import json
import time
from collections import defaultdict
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


def run_compression_rate(
    *,
    manifest_path: Path,
    target_artifact: Path,
    target_metadata_path: Path,
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
    target_sites = set(manifest["target_pool_site_ids"])
    members = set(manifest["target_member_site_ids"])
    totals: dict[str, list[int]] = defaultdict(lambda: [0, 0])
    tokenizer = Tokenizer.from_file(str(target_artifact))
    corpus_path = PROJECT_ROOT / manifest["corpus_path"]
    with corpus_path.open("r", encoding="utf-8") as handle:
        for line in handle:
            if not line.strip():
                continue
            record = json.loads(line)
            site = record["site_id"]
            if site not in target_sites:
                continue
            totals[site][0] += len(record["text"].encode("utf-8"))
            totals[site][1] += len(tokenizer.encode(record["text"]).ids)

    details = []
    labels: list[int] = []
    scores: list[float] = []
    for site in sorted(target_sites):
        byte_count, token_count = totals[site]
        if token_count <= 0:
            raise RuntimeError(f"zero tokens for site {site}")
        score = byte_count / token_count
        label = int(site in members)
        labels.append(label)
        scores.append(score)
        details.append(
            {
                "site_id": site,
                "is_member": bool(label),
                "score": score,
                "byte_count": byte_count,
                "token_count": token_count,
            }
        )
    metrics = compute_attack_metrics(
        labels,
        scores,
        seed=int(manifest["seed"]) + vocab_size + int(method["min_count_threshold"]) * 17,
        bootstrap_iterations=bootstrap_iterations,
        bootstrap_confidence=bootstrap_confidence,
    )
    result = {
        "schema_version": 1,
        "status": "success",
        "attack": "compression_rate",
        "score_definition": "UTF-8 bytes divided by target-tokenizer token count",
        "protocol": manifest["protocol"],
        "seed": int(manifest["seed"]),
        "vocab_size": vocab_size,
        "actual_vocab_size": target_metadata["actual_vocab_size"],
        "method_id": method["id"],
        "defense": method["defense"],
        "min_count_threshold": int(method["min_count_threshold"]),
        "data": {
            "site_count": len(target_sites),
            "member_site_count": len(members),
            "nonmember_site_count": len(target_sites - members),
            "texts_per_site": manifest["texts_per_site"],
            "dataset_revision": manifest["dataset_revision"],
            "manifest_sha256": manifest["manifest_sha256"],
        },
        "artifacts": {
            "target_tokenizer_sha256": target_metadata["artifact_sha256"],
            "target_tokenizer_metadata": str(target_metadata_path.relative_to(PROJECT_ROOT)),
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
