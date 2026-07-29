"""Train one manifest-selected target BPE tokenizer."""

from __future__ import annotations

import argparse
from pathlib import Path

from src.tokenizer.common import train_tokenizer_artifact
from src.utils.run_metadata import PROJECT_ROOT, strict_json_load


def train_target(
    manifest_path: Path,
    vocab_size: int,
    method: dict,
    output_dir: Path,
    tokenizers_threads: int,
) -> dict:
    manifest = strict_json_load(manifest_path)
    return train_tokenizer_artifact(
        corpus_path=PROJECT_ROOT / manifest["corpus_path"],
        training_site_ids=manifest["target_training_site_ids"],
        manifest_path=manifest_path,
        protocol=manifest["protocol"],
        seed=int(manifest["seed"]),
        vocab_size=vocab_size,
        method=method,
        role="target",
        shadow_id=None,
        output_dir=output_dir,
        tokenizers_threads=tokenizers_threads,
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--vocab-size", type=int, required=True)
    parser.add_argument("--method-id", required=True)
    parser.add_argument("--defense", required=True)
    parser.add_argument("--min-frequency", type=int, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--tokenizers-threads", type=int, default=1)
    args = parser.parse_args()
    train_target(
        args.manifest.resolve(),
        args.vocab_size,
        {"id": args.method_id, "defense": args.defense, "min_count_threshold": args.min_frequency},
        args.output_dir.resolve(),
        args.tokenizers_threads,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
