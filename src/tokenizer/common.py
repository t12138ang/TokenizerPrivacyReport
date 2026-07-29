"""Manifest-driven BPE training, truncation, and post-hoc Min-count filtering."""

from __future__ import annotations

import json
import os
import time
from collections import Counter
from pathlib import Path
from typing import Any, Iterator

from tokenizers import Tokenizer
from tokenizers.models import BPE
from tokenizers.pre_tokenizers import Whitespace
from tokenizers.trainers import BpeTrainer

from src.utils.run_metadata import (
    PROJECT_ROOT,
    canonical_sha256,
    environment_metadata,
    peak_working_set_bytes,
    sha256_file,
    sha256_text,
    strict_json_load,
    utc_now,
    write_json_exclusive,
)
from src.attacks.token_statistics import aggregate_substring_token_counts


SPECIAL_TOKENS = ["[UNK]", "[CLS]", "[SEP]", "[PAD]", "[MASK]"]


def iter_site_texts(corpus_path: Path, site_ids: set[str]) -> Iterator[str]:
    """Yield manifest-selected text without creating a second corpus copy."""
    with corpus_path.open("r", encoding="utf-8") as handle:
        for line in handle:
            if not line.strip():
                continue
            record = json.loads(line)
            if record["site_id"] in site_ids:
                yield record["text"]


def site_text_statistics(corpus_path: Path, site_ids: set[str]) -> dict[str, int]:
    text_count = 0
    byte_count = 0
    for text in iter_site_texts(corpus_path, site_ids):
        text_count += 1
        byte_count += len(text.encode("utf-8"))
    return {"training_text_count": text_count, "training_byte_count": byte_count}


def artifact_is_complete(artifact_path: Path, metadata_path: Path) -> bool:
    if not artifact_path.is_file() and not metadata_path.is_file():
        return False
    if not artifact_path.is_file() or not metadata_path.is_file():
        raise RuntimeError(f"partial tokenizer checkpoint exists: {artifact_path.parent}")
    metadata = strict_json_load(metadata_path)
    if metadata.get("status") != "success":
        raise RuntimeError(f"non-success tokenizer checkpoint exists: {metadata_path}")
    if metadata.get("artifact_sha256") != sha256_file(artifact_path):
        raise RuntimeError(f"tokenizer artifact hash mismatch: {artifact_path}")
    return True


def _metadata_common(
    *,
    manifest_path: Path,
    protocol: str,
    seed: int,
    role: str,
    shadow_id: int | None,
    site_set: set[str],
    corpus_path: Path,
    tokenizers_threads: int,
) -> dict[str, Any]:
    manifest = strict_json_load(manifest_path)
    return {
        "protocol": protocol,
        "seed": seed,
        "role": role,
        "shadow_id": shadow_id,
        "training_site_count": len(site_set),
        **site_text_statistics(corpus_path, site_set),
        "tokenizers_threads": tokenizers_threads,
        "logical_cpu_count": os.cpu_count(),
        "manifest_path": str(manifest_path.relative_to(PROJECT_ROOT)),
        "manifest_sha256": manifest["manifest_sha256"],
        "corpus_sha256": manifest["corpus_sha256"],
    }


def train_base_tokenizer_artifact(
    *,
    corpus_path: Path,
    training_site_ids: list[str],
    manifest_path: Path,
    protocol: str,
    seed: int,
    max_vocab_size: int,
    role: str,
    shadow_id: int | None,
    output_dir: Path,
    tokenizers_threads: int,
) -> dict[str, Any]:
    """Train one maximum-size BPE checkpoint for later deterministic truncation."""
    artifact_path = output_dir / "tokenizer.json"
    metadata_path = output_dir / "metadata.json"
    if artifact_is_complete(artifact_path, metadata_path):
        metadata = strict_json_load(metadata_path)
        manifest = strict_json_load(manifest_path)
        expected = {
            "manifest_sha256": manifest["manifest_sha256"],
            "corpus_sha256": manifest["corpus_sha256"],
            "protocol": protocol,
            "seed": seed,
            "role": role,
            "shadow_id": shadow_id,
            "training_site_count": len(set(training_site_ids)),
            "requested_vocab_size": int(max_vocab_size),
            "tokenizers_threads": int(tokenizers_threads),
        }
        mismatches = {
            key: (metadata.get(key), value)
            for key, value in expected.items()
            if metadata.get(key) != value
        }
        if mismatches:
            raise RuntimeError(f"stale or mismatched base tokenizer artifact {output_dir}: {mismatches}")
        metadata["checkpoint_reused"] = True
        return metadata
    if output_dir.exists() and any(output_dir.iterdir()):
        raise FileExistsError(f"refusing to overwrite nonempty base-tokenizer output: {output_dir}")
    output_dir.mkdir(parents=True, exist_ok=True)

    started = time.perf_counter()
    started_at = utc_now()
    site_set = set(training_site_ids)
    common = _metadata_common(
        manifest_path=manifest_path,
        protocol=protocol,
        seed=seed,
        role=role,
        shadow_id=shadow_id,
        site_set=site_set,
        corpus_path=corpus_path,
        tokenizers_threads=tokenizers_threads,
    )
    if common["training_text_count"] == 0:
        raise RuntimeError("tokenizer training selection contains zero texts")
    os.environ["TOKENIZERS_PARALLELISM"] = "true" if tokenizers_threads > 1 else "false"
    tokenizer = Tokenizer(BPE(unk_token="[UNK]"))
    tokenizer.pre_tokenizer = Whitespace()
    trainer = BpeTrainer(
        special_tokens=SPECIAL_TOKENS,
        vocab_size=max_vocab_size,
        min_frequency=2,
        show_progress=False,
    )
    tokenizer.train_from_iterator(
        iter_site_texts(corpus_path, site_set),
        trainer=trainer,
        length=common["training_text_count"],
    )
    partial_path = output_dir / "tokenizer.json.partial"
    if partial_path.exists() or artifact_path.exists():
        raise FileExistsError(f"refusing to overwrite tokenizer file: {artifact_path}")
    tokenizer.save(str(partial_path))
    partial_path.rename(artifact_path)
    metadata = {
        "schema_version": 1,
        "status": "success",
        "kind": "maximum_vocab_training_checkpoint",
        "started_at_utc": started_at,
        "completed_at_utc": utc_now(),
        "elapsed_seconds": time.perf_counter() - started,
        **common,
        "trainer_min_frequency": 2,
        "requested_vocab_size": max_vocab_size,
        "actual_vocab_size": tokenizer.get_vocab_size(),
        "peak_memory_bytes": peak_working_set_bytes(),
        "code": environment_metadata()["git"],
        "artifact": str(artifact_path.relative_to(PROJECT_ROOT)),
        "artifact_sha256": sha256_file(artifact_path),
        "checkpoint_reused": False,
    }
    write_json_exclusive(metadata_path, metadata)
    return metadata


def _merge_pair(value: Any) -> tuple[str, str]:
    if isinstance(value, list) and len(value) == 2:
        return str(value[0]), str(value[1])
    if isinstance(value, str):
        parts = value.split(" ", 1)
        if len(parts) == 2:
            return parts[0], parts[1]
    raise ValueError(f"unsupported BPE merge representation: {value!r}")


def _tokenizer_from_vocab_and_merges(vocab: dict[str, int], raw_merges: list[Any]) -> Tokenizer:
    merges = [_merge_pair(value) for value in raw_merges]
    tokenizer = Tokenizer(BPE(vocab=vocab, merges=merges, unk_token="[UNK]"))
    tokenizer.pre_tokenizer = Whitespace()
    tokenizer.add_special_tokens([token for token in SPECIAL_TOKENS if token in vocab])
    return tokenizer


def truncate_tokenizer(base_artifact: Path, requested_vocab_size: int) -> Tokenizer:
    """Truncate by learned merge order, matching the official training strategy."""
    payload = strict_json_load(base_artifact)
    full_vocab = {str(token): int(index) for token, index in payload["model"]["vocab"].items()}
    raw_merges = list(payload["model"]["merges"])
    if requested_vocab_size > len(full_vocab):
        requested_vocab_size = len(full_vocab)
    selected_tokens = [
        token for token, _ in sorted(full_vocab.items(), key=lambda item: item[1])[:requested_vocab_size]
    ]
    vocab = {token: index for index, token in enumerate(selected_tokens)}
    selected_merges = []
    for raw_merge in raw_merges:
        left, right = _merge_pair(raw_merge)
        if left in vocab and right in vocab and left + right in vocab:
            selected_merges.append(raw_merge)
    return _tokenizer_from_vocab_and_merges(vocab, selected_merges)


def apply_posthoc_min_count(
    tokenizer: Tokenizer,
    *,
    corpus_path: Path,
    site_set: set[str],
    threshold: int,
    feature_cache_path: Path | None = None,
    corpus_sha256: str | None = None,
) -> tuple[Tokenizer, dict[str, Any]]:
    """Filter with the official overlapping substring-frequency semantics."""
    tokenizer_hash = sha256_text(tokenizer.to_str())
    sites_hash = sha256_text("\n".join(sorted(site_set)))
    corpus_hash = corpus_sha256 or sha256_file(corpus_path)
    counts: Counter[str]
    feature_cache_reused = False
    feature_counting_seconds = 0.0
    if feature_cache_path is not None and feature_cache_path.exists():
        cached = strict_json_load(feature_cache_path)
        cached_counts = cached.get("counts")
        cached_seconds = cached.get("feature_counting_seconds")
        if (
            cached.get("status") != "success"
            or cached.get("tokenizer_sha256") != tokenizer_hash
            or cached.get("selected_sites_sha256") != sites_hash
            or cached.get("selected_site_count") != len(site_set)
            or cached.get("corpus_sha256") != corpus_hash
            or cached.get("feature_definition") != "official_overlapping_regex_unit_substrings"
            or not isinstance(cached_counts, dict)
            or cached.get("counts_sha256") != canonical_sha256(cached_counts)
            or not isinstance(cached_seconds, (int, float))
            or not 0 <= float(cached_seconds) < float("inf")
        ):
            raise RuntimeError(f"invalid Min-count feature cache: {feature_cache_path}")
        counts = Counter({str(token): int(value) for token, value in cached_counts.items()})
        feature_counting_seconds = float(cached_seconds)
        feature_cache_reused = True
        if any(value < 0 for value in counts.values()):
            raise RuntimeError(f"negative Min-count cache value: {feature_cache_path}")
    else:
        feature_started = time.perf_counter()
        counts = aggregate_substring_token_counts(tokenizer, corpus_path, site_set)
        feature_counting_seconds = time.perf_counter() - feature_started
        if feature_cache_path is not None:
            serialized_counts = dict(sorted(counts.items()))
            write_json_exclusive(feature_cache_path, {
                "schema_version": 1,
                "status": "success",
                "feature_definition": "official_overlapping_regex_unit_substrings",
                "tokenizer_sha256": tokenizer_hash,
                "selected_sites_sha256": sites_hash,
                "corpus_sha256": corpus_hash,
                "selected_site_count": len(site_set),
                "counts": serialized_counts,
                "counts_sha256": canonical_sha256(serialized_counts),
                "feature_counting_seconds": feature_counting_seconds,
            })
    if not counts or any(value < 0 for value in counts.values()):
        raise RuntimeError("Min-count substring feature counts must be nonempty and nonnegative")
    counts_hash = canonical_sha256(dict(sorted(counts.items())))
    cache_path_value = None
    cache_hash = None
    if feature_cache_path is not None:
        cache_path_value = str(feature_cache_path.resolve().relative_to(PROJECT_ROOT)).replace("\\", "/")
        cache_hash = sha256_file(feature_cache_path)
    payload = json.loads(tokenizer.to_str())
    old_vocab = {str(token): int(index) for token, index in payload["model"]["vocab"].items()}
    selected = [
        token
        for token, _ in sorted(old_vocab.items(), key=lambda item: item[1])
        if token in SPECIAL_TOKENS or counts[token] >= threshold
    ]
    if "[UNK]" not in selected:
        selected.insert(0, "[UNK]")
    vocab = {token: index for index, token in enumerate(dict.fromkeys(selected))}
    selected_merges = []
    for raw_merge in payload["model"]["merges"]:
        left, right = _merge_pair(raw_merge)
        if left in vocab and right in vocab and left + right in vocab:
            selected_merges.append(raw_merge)
    filtered = _tokenizer_from_vocab_and_merges(vocab, selected_merges)
    return filtered, {
        "observed_token_occurrences": int(sum(counts.values())),
        "observed_distinct_tokens": len(counts),
        "removed_token_count": len(old_vocab) - len(vocab),
        "feature_definition": "official_overlapping_regex_unit_substrings",
        "feature_counts_sha256": counts_hash,
        "feature_selected_sites_sha256": sites_hash,
        "feature_cache_path": cache_path_value,
        "feature_cache_sha256": cache_hash,
        "feature_cache_reused": feature_cache_reused,
        "feature_counting_seconds": feature_counting_seconds,
    }


def materialize_tokenizer_artifact(
    *,
    base_artifact: Path,
    base_metadata_path: Path,
    corpus_path: Path,
    training_site_ids: list[str],
    manifest_path: Path,
    vocab_size: int,
    method: dict[str, Any],
    output_dir: Path,
) -> dict[str, Any]:
    """Derive one immutable Plain-BPE or official-style Min-count artifact."""
    artifact_path = output_dir / "tokenizer.json"
    metadata_path = output_dir / "metadata.json"
    if artifact_is_complete(artifact_path, metadata_path):
        metadata = strict_json_load(metadata_path)
        manifest = strict_json_load(manifest_path)
        expected = {
            "manifest_sha256": manifest["manifest_sha256"],
            "method_id": method["id"],
            "defense": method["defense"],
            "min_count_threshold": int(method["min_count_threshold"]),
            "requested_vocab_size": int(vocab_size),
            "base_artifact_sha256": sha256_file(base_artifact),
        }
        mismatches = {
            key: (metadata.get(key), value)
            for key, value in expected.items()
            if metadata.get(key) != value
        }
        if method["defense"] == "min_count":
            cache_value = metadata.get("feature_cache_path")
            cache_path = PROJECT_ROOT / cache_value if isinstance(cache_value, str) else None
            if (
                metadata.get("min_count_semantics")
                != "post-hoc official overlapping regex-unit substring frequency filtering"
                or metadata.get("feature_definition")
                != "official_overlapping_regex_unit_substrings"
                or not metadata.get("feature_counts_sha256")
                or cache_path is None
                or not cache_path.is_file()
                or metadata.get("feature_cache_sha256") != sha256_file(cache_path)
            ):
                mismatches["min_count_feature_provenance"] = ("invalid", "official substring cache")
        if mismatches:
            raise RuntimeError(f"stale or mismatched derived tokenizer artifact {output_dir}: {mismatches}")
        metadata["checkpoint_reused"] = True
        return metadata
    if output_dir.exists() and any(output_dir.iterdir()):
        raise FileExistsError(f"refusing to overwrite nonempty tokenizer output: {output_dir}")
    output_dir.mkdir(parents=True, exist_ok=True)
    base_metadata = strict_json_load(base_metadata_path)
    if base_metadata["artifact_sha256"] != sha256_file(base_artifact):
        raise RuntimeError(f"base tokenizer artifact hash mismatch: {base_artifact}")

    started = time.perf_counter()
    tokenizer = truncate_tokenizer(base_artifact, vocab_size)
    before_filter_size = tokenizer.get_vocab_size()
    filter_stats = {
        "observed_token_occurrences": 0,
        "observed_distinct_tokens": 0,
        "removed_token_count": 0,
        "feature_definition": "not_applied",
        "feature_counts_sha256": None,
        "feature_selected_sites_sha256": None,
        "feature_cache_path": None,
        "feature_cache_sha256": None,
        "feature_cache_reused": False,
        "feature_counting_seconds": 0.0,
    }
    threshold = int(method["min_count_threshold"])
    if method["defense"] == "min_count":
        tokenizer, filter_stats = apply_posthoc_min_count(
            tokenizer,
            corpus_path=corpus_path,
            site_set=set(training_site_ids),
            threshold=threshold,
            feature_cache_path=(
                output_dir.parent.parent / "_min_count_feature_cache" / f"{output_dir.name}.json"
            ),
            corpus_sha256=base_metadata["corpus_sha256"],
        )
    elif method["defense"] != "plain_bpe":
        raise ValueError(f"unsupported tokenizer method: {method}")

    partial_path = output_dir / "tokenizer.json.partial"
    tokenizer.save(str(partial_path))
    partial_path.rename(artifact_path)
    actual_elapsed_seconds = time.perf_counter() - started
    standalone_elapsed_seconds = actual_elapsed_seconds + (
        float(filter_stats["feature_counting_seconds"])
        if filter_stats["feature_cache_reused"] else 0.0
    )
    metadata = {
        "schema_version": 1,
        "status": "success",
        "kind": "derived_tokenizer",
        "completed_at_utc": utc_now(),
        "elapsed_seconds": actual_elapsed_seconds,
        "standalone_elapsed_seconds": standalone_elapsed_seconds,
        "base_training_elapsed_seconds": base_metadata["elapsed_seconds"],
        "protocol": base_metadata["protocol"],
        "seed": base_metadata["seed"],
        "role": base_metadata["role"],
        "shadow_id": base_metadata["shadow_id"],
        "method_id": method["id"],
        "defense": method["defense"],
        "min_count_threshold": threshold,
        "min_count_semantics": (
            "post-hoc official overlapping regex-unit substring frequency filtering"
            if threshold else "not applied"
        ),
        "requested_vocab_size": vocab_size,
        "pre_filter_vocab_size": before_filter_size,
        "actual_vocab_size": tokenizer.get_vocab_size(),
        "training_site_count": base_metadata["training_site_count"],
        "training_text_count": base_metadata["training_text_count"],
        "training_byte_count": base_metadata["training_byte_count"],
        **filter_stats,
        "tokenizers_threads": base_metadata["tokenizers_threads"],
        "logical_cpu_count": base_metadata["logical_cpu_count"],
        "peak_memory_bytes": peak_working_set_bytes(),
        "manifest_path": base_metadata["manifest_path"],
        "manifest_sha256": base_metadata["manifest_sha256"],
        "corpus_sha256": base_metadata["corpus_sha256"],
        "base_artifact": str(base_artifact.relative_to(PROJECT_ROOT)),
        "base_artifact_sha256": base_metadata["artifact_sha256"],
        "artifact": str(artifact_path.relative_to(PROJECT_ROOT)),
        "artifact_sha256": sha256_file(artifact_path),
        "checkpoint_reused": False,
    }
    write_json_exclusive(metadata_path, metadata)
    return metadata


def train_tokenizer_artifact(
    *,
    corpus_path: Path,
    training_site_ids: list[str],
    manifest_path: Path,
    protocol: str,
    seed: int,
    vocab_size: int,
    method: dict[str, Any],
    role: str,
    shadow_id: int | None,
    output_dir: Path,
    tokenizers_threads: int,
) -> dict[str, Any]:
    """Standalone compatibility entry point used by the target/shadow CLIs."""
    base_dir = output_dir.parent / f"{output_dir.name}_base"
    train_base_tokenizer_artifact(
        corpus_path=corpus_path,
        training_site_ids=training_site_ids,
        manifest_path=manifest_path,
        protocol=protocol,
        seed=seed,
        max_vocab_size=vocab_size,
        role=role,
        shadow_id=shadow_id,
        output_dir=base_dir,
        tokenizers_threads=tokenizers_threads,
    )
    return materialize_tokenizer_artifact(
        base_artifact=base_dir / "tokenizer.json",
        base_metadata_path=base_dir / "metadata.json",
        corpus_path=corpus_path,
        training_site_ids=training_site_ids,
        manifest_path=manifest_path,
        vocab_size=vocab_size,
        method=method,
        output_dir=output_dir,
    )
