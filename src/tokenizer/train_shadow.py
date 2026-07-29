"""Train one of the eight manifest-selected shadow BPE tokenizers."""

from __future__ import annotations

import argparse
from pathlib import Path

from src.tokenizer.common import train_tokenizer_artifact
from src.utils.run_metadata import PROJECT_ROOT, strict_json_load


def train_shadow(
    manifest_path: Path,
    shadow_id: int,
    vocab_size: int,
    method: dict,
    output_dir: Path,
    tokenizers_threads: int,
) -> dict:
    manifest = strict_json_load(manifest_path)
    plans = {int(plan["shadow_id"]): plan for plan in manifest["shadow_plans"]}
    if shadow_id not in plans:
        raise ValueError(f"shadow_id not in manifest: {shadow_id}")
    return train_tokenizer_artifact(
        corpus_path=PROJECT_ROOT / manifest["corpus_path"],
        training_site_ids=plans[shadow_id]["training_site_ids"],
        manifest_path=manifest_path,
        protocol=manifest["protocol"],
        seed=int(manifest["seed"]),
        vocab_size=vocab_size,
        method=method,
        role="shadow",
        shadow_id=shadow_id,
        output_dir=output_dir,
        tokenizers_threads=tokenizers_threads,
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--shadow-id", type=int, required=True)
    parser.add_argument("--vocab-size", type=int, required=True)
    parser.add_argument("--method-id", required=True)
    parser.add_argument("--defense", required=True)
    parser.add_argument("--min-frequency", type=int, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--tokenizers-threads", type=int, default=1)
    args = parser.parse_args()
    train_shadow(
        args.manifest.resolve(),
        args.shadow_id,
        args.vocab_size,
        {"id": args.method_id, "defense": args.defense, "min_count_threshold": args.min_frequency},
        args.output_dir.resolve(),
        args.tokenizers_threads,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
