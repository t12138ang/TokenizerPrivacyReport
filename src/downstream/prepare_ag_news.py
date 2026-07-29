"""Download, split, globally deduplicate, and hash fixed-revision AG News."""

from __future__ import annotations

import argparse
import json
import os
import time
from collections import Counter
from pathlib import Path
from typing import Any

from datasets import load_dataset
from huggingface_hub import HfApi

from src.utils.run_metadata import (
    PROJECT_ROOT,
    environment_metadata,
    peak_working_set_bytes,
    sha256_file,
    sha256_text,
    strict_json_load,
    utc_now,
    write_json_exclusive,
)


def normalized_text_hash(text: str) -> str:
    return sha256_text(" ".join(text.split()).casefold())


def select_validation(records: list[dict[str, Any]], seed: int, fraction: float) -> set[int]:
    selected: set[int] = set()
    by_label: dict[int, list[int]] = {}
    for index, record in enumerate(records):
        by_label.setdefault(int(record["label"]), []).append(index)
    for label, indices in sorted(by_label.items()):
        count = round(len(indices) * fraction)
        ranked = sorted(indices, key=lambda index: sha256_text(
            f"ag-news-validation:{seed}:{label}:{index}:{records[index]['text']}"
        ))
        selected.update(ranked[:count])
    return selected


def write_split(path: Path, records: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    partial = path.with_suffix(path.suffix + ".partial")
    if partial.exists() or path.exists():
        raise FileExistsError(f"refusing to overwrite AG News split: {path}")
    with partial.open("x", encoding="utf-8", newline="\n") as handle:
        for record in records:
            handle.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(partial, path)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path, required=True)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    config = strict_json_load(args.config.resolve())
    metadata_path = PROJECT_ROOT / config["metadata_path"]
    if metadata_path.exists():
        metadata = strict_json_load(metadata_path)
        if (
            metadata.get("status") != "success"
            or metadata.get("dataset_id") != config["dataset_id"]
            or metadata.get("requested_revision") != config["dataset_revision"]
            or metadata.get("resolved_revision") != config["dataset_revision"]
            or int(metadata.get("split_seed", -1)) != int(config["split_seed"])
        ):
            raise RuntimeError("stale, mismatched, or non-success AG News metadata exists")
        for split, details in metadata["splits"].items():
            if sha256_file(PROJECT_ROOT / details["path"]) != details["sha256"]:
                raise RuntimeError(f"AG News {split} hash mismatch")
        print(f"checkpoint_reused=true metadata={metadata_path}")
        return 0
    started = time.perf_counter()
    dataset_id = config["dataset_id"]
    requested_revision = config["dataset_revision"]
    resolved_revision = HfApi().dataset_info(dataset_id, revision=requested_revision).sha
    if resolved_revision != requested_revision:
        raise RuntimeError(f"AG News revision mismatch requested={requested_revision} resolved={resolved_revision}")
    dataset = load_dataset(
        dataset_id,
        revision=requested_revision,
        cache_dir=str(PROJECT_ROOT / config["cache_dir"]),
    )
    raw_train = [
        {"text": str(record["text"]), "label": int(record["label"]), "source_split": "train", "source_index": index}
        for index, record in enumerate(dataset["train"])
    ]
    raw_test = [
        {"text": str(record["text"]), "label": int(record["label"]), "source_split": "test", "source_index": index}
        for index, record in enumerate(dataset["test"])
    ]
    validation_indices = select_validation(
        raw_train, int(config["split_seed"]), float(config["validation_fraction_per_class"])
    )
    proposed = {
        "test": raw_test,
        "validation": [record for index, record in enumerate(raw_train) if index in validation_indices],
        "train": [record for index, record in enumerate(raw_train) if index not in validation_indices],
    }
    seen_hashes: set[str] = set()
    retained: dict[str, list[dict[str, Any]]] = {}
    removed: dict[str, int] = {}
    # Test has first priority, then validation, then train, preventing evaluation leakage.
    for split in ("test", "validation", "train"):
        retained[split] = []
        removed[split] = 0
        for record in proposed[split]:
            digest = normalized_text_hash(record["text"])
            if digest in seen_hashes:
                removed[split] += 1
                continue
            seen_hashes.add(digest)
            retained[split].append({**record, "normalized_text_sha256": digest})
    data_dir = PROJECT_ROOT / config["data_dir"]
    split_metadata = {}
    for split in ("train", "validation", "test"):
        path = data_dir / f"{split}.jsonl"
        write_split(path, retained[split])
        split_metadata[split] = {
            "path": str(path.relative_to(PROJECT_ROOT)).replace("\\", "/"),
            "record_count": len(retained[split]),
            "byte_count": path.stat().st_size,
            "sha256": sha256_file(path),
            "class_distribution": dict(sorted(Counter(record["label"] for record in retained[split]).items())),
            "normalized_exact_duplicates_removed": removed[split],
        }
    metadata = {
        "schema_version": 1,
        "status": "success",
        "dataset_id": dataset_id,
        "requested_revision": requested_revision,
        "resolved_revision": resolved_revision,
        "raw_train_count": len(raw_train),
        "raw_test_count": len(raw_test),
        "split_seed": config["split_seed"],
        "split_policy": "stratified hash-ranked 10% validation from official train; official test retained",
        "deduplication_policy": "normalized exact text, priority test then validation then train",
        "cross_split_normalized_duplicate_count_after_filter": 0,
        "splits": split_metadata,
        "elapsed_seconds": time.perf_counter() - started,
        "peak_memory_bytes": peak_working_set_bytes(),
        "completed_at_utc": utc_now(),
        "environment": environment_metadata(),
    }
    write_json_exclusive(metadata_path, metadata)
    print(
        f"status=success revision={resolved_revision} "
        f"train={split_metadata['train']['record_count']} validation={split_metadata['validation']['record_count']} "
        f"test={split_metadata['test']['record_count']} metadata={metadata_path}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
