"""Held-out C4 and AG News tokenizer utility measurements."""

from __future__ import annotations

import json
import re
import time
from collections import Counter
from pathlib import Path
from typing import Any

import numpy as np
from tokenizers import Tokenizer

from src.utils.run_metadata import (
    PROJECT_ROOT,
    canonical_sha256,
    environment_metadata,
    peak_working_set_bytes,
    sha256_file,
    strict_json_load,
    utc_now,
    write_json_exclusive,
)


WORD_RE = re.compile(r"\b\w+\b", flags=re.UNICODE)
SENTENCE_RE = re.compile(r"[^.!?\n]+[.!?]?", flags=re.UNICODE)


def load_c4_heldout(manifest: dict[str, Any]) -> list[str]:
    selected = set(manifest["target_nonmember_site_ids"])
    texts: list[str] = []
    with (PROJECT_ROOT / manifest["corpus_path"]).open("r", encoding="utf-8") as handle:
        for line in handle:
            if line.strip():
                record = json.loads(line)
                if record["site_id"] in selected:
                    texts.append(record["text"])
    if not texts:
        raise RuntimeError("held-out C4 selection is empty")
    return texts


def load_jsonl_text(path: Path) -> list[str]:
    texts = []
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            if line.strip():
                texts.append(json.loads(line)["text"])
    if not texts:
        raise RuntimeError(f"evaluation text file is empty: {path}")
    return texts


def corpus_metrics(tokenizer: Tokenizer, texts: list[str]) -> dict[str, Any]:
    byte_count = sum(len(text.encode("utf-8")) for text in texts)
    character_count = sum(len(text) for text in texts)
    token_lengths: list[int] = []
    for start in range(0, len(texts), 256):
        token_lengths.extend(len(encoding.ids) for encoding in tokenizer.encode_batch(texts[start : start + 256]))
    total_tokens = sum(token_lengths)
    if total_tokens <= 0:
        raise RuntimeError("tokenizer produced zero evaluation tokens")
    sentences = [piece.group(0) for text in texts for piece in SENTENCE_RE.finditer(text) if piece.group(0).strip()]
    sentence_lengths: list[int] = []
    for start in range(0, len(sentences), 256):
        sentence_lengths.extend(len(encoding.ids) for encoding in tokenizer.encode_batch(sentences[start : start + 256]))
    word_counts = Counter(word.casefold() for text in texts for word in WORD_RE.findall(text))
    rare_words = sorted(word for word, count in word_counts.items() if count <= 2)
    rare_splits: list[int] = []
    rare_single_token_covered = 0
    for start in range(0, len(rare_words), 512):
        encodings = tokenizer.encode_batch(rare_words[start : start + 512])
        rare_splits.extend(len(encoding.ids) for encoding in encodings)
        rare_single_token_covered += sum(
            len(encoding.tokens) == 1 and encoding.tokens[0] != "[UNK]" for encoding in encodings
        )
    return {
        "document_count": len(texts),
        "sentence_count": len(sentences),
        "byte_count": byte_count,
        "character_count": character_count,
        "token_count": total_tokens,
        "bytes_per_token": byte_count / total_tokens,
        "characters_per_token": character_count / total_tokens,
        "mean_tokens_per_sentence": float(np.mean(sentence_lengths)),
        "mean_tokens_per_document": float(np.mean(token_lengths)),
        "median_tokens_per_document": float(np.median(token_lengths)),
        "rare_word_definition": "case-folded Unicode word with held-out corpus frequency <= 2",
        "rare_word_type_count": len(rare_words),
        "rare_word_mean_split_length": float(np.mean(rare_splits)) if rare_splits else 0.0,
        "long_tail_single_token_coverage": (
            rare_single_token_covered / len(rare_splits) if rare_splits else 0.0
        ),
    }


def evaluate_tokenizer_utility(
    *,
    tokenizer_dir: Path,
    manifest_path: Path,
    output_path: Path,
    ag_news_test_path: Path | None = None,
) -> dict[str, Any]:
    manifest = strict_json_load(manifest_path)
    artifact = tokenizer_dir / "tokenizer.json"
    metadata_path = tokenizer_dir / "metadata.json"
    metadata = strict_json_load(metadata_path)
    artifact_hash = sha256_file(artifact)
    ag_news_hash = sha256_file(ag_news_test_path) if ag_news_test_path is not None else None
    if output_path.exists():
        result = strict_json_load(output_path)
        if (
            result.get("status") == "success"
            and result.get("tokenizer_artifact_sha256") == artifact_hash
            and result.get("manifest_sha256") == manifest["manifest_sha256"]
            and result.get("evaluation_source_sha256", {}).get("ag_news_test") == ag_news_hash
        ):
            return {**result, "checkpoint_reused": True}
        raise RuntimeError(f"stale, mismatched, or non-success utility result: {output_path}")
    started = time.perf_counter()
    if metadata.get("artifact_sha256") != artifact_hash:
        raise RuntimeError(f"tokenizer artifact mismatch: {artifact}")
    if metadata.get("manifest_sha256") != manifest["manifest_sha256"]:
        raise RuntimeError("tokenizer/utility manifest mismatch")
    tokenizer = Tokenizer.from_file(str(artifact))
    payload = strict_json_load(artifact)
    sources = {"c4_heldout": corpus_metrics(tokenizer, load_c4_heldout(manifest))}
    if ag_news_test_path is not None:
        sources["ag_news_test"] = corpus_metrics(tokenizer, load_jsonl_text(ag_news_test_path))
    if metadata.get("kind") == "derived_tokenizer":
        base_training_seconds = metadata.get("base_training_elapsed_seconds")
        if base_training_seconds is None:
            base_metadata_path = PROJECT_ROOT / metadata["base_artifact"]
            base_training_seconds = strict_json_load(base_metadata_path.parent / "metadata.json")["elapsed_seconds"]
        tokenizer_training_seconds = (
            float(base_training_seconds)
            + float(metadata.get("standalone_elapsed_seconds", metadata["elapsed_seconds"]))
        )
        timing_semantics = "maximum-vocabulary base training plus standalone derivation/filtering"
    else:
        tokenizer_training_seconds = float(metadata["elapsed_seconds"])
        timing_semantics = "complete tokenizer training wall time"
    result = {
        "schema_version": 1,
        "status": "success",
        "scale": manifest["scale"],
        "protocol": manifest["protocol"],
        "seed": manifest["seed"],
        "method_id": metadata.get("method_id", metadata.get("method")),
        "requested_vocab_size": metadata["requested_vocab_size"],
        "actual_vocab_size": metadata["actual_vocab_size"],
        "merge_count": len(payload["model"]["merges"]),
        "tokenizer_training_seconds": tokenizer_training_seconds,
        "tokenizer_training_time_semantics": timing_semantics,
        "tokenizer_artifact_bytes": artifact.stat().st_size,
        "tokenizer_artifact_sha256": metadata["artifact_sha256"],
        "manifest_sha256": manifest["manifest_sha256"],
        "evaluation_source_sha256": {
            "c4_corpus": manifest["corpus_sha256"],
            "c4_heldout_site_ids": canonical_sha256(sorted(manifest["target_nonmember_site_ids"])),
            "ag_news_test": ag_news_hash,
        },
        "evaluation_is_disjoint_from_target_training": True,
        "sources": sources,
        "elapsed_seconds": time.perf_counter() - started,
        "peak_memory_bytes": peak_working_set_bytes(),
        "completed_at_utc": utc_now(),
        "environment": environment_metadata(),
        "checkpoint_reused": False,
    }
    write_json_exclusive(output_path, result)
    return result
