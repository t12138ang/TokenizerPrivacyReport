"""Reproducible from-scratch Transformer Encoder training on fixed AG News splits."""

from __future__ import annotations

import argparse
import json
import math
import os
import random
import re
import time
from pathlib import Path
from typing import Any

import numpy as np
import torch
from sklearn.metrics import accuracy_score, f1_score
from tokenizers import Tokenizer
from torch import nn
from torch.utils.data import DataLoader, Dataset

from src.utils.run_metadata import (
    PROJECT_ROOT,
    environment_metadata,
    peak_working_set_bytes,
    setup_logger,
    sha256_file,
    strict_json_dumps,
    strict_json_load,
    utc_now,
    write_json_exclusive,
)


class MemmapTokenDataset(Dataset):
    def __init__(self, tokens_path: Path, labels_path: Path) -> None:
        self.tokens = np.load(tokens_path, mmap_mode="r")
        self.labels = np.load(labels_path, mmap_mode="r")
        if len(self.tokens) != len(self.labels):
            raise RuntimeError("encoded token/label dimensions differ")

    def __len__(self) -> int:
        return len(self.labels)

    def __getitem__(self, index: int) -> tuple[torch.Tensor, torch.Tensor]:
        return (
            torch.tensor(np.asarray(self.tokens[index]), dtype=torch.long),
            torch.tensor(int(self.labels[index]), dtype=torch.long),
        )


class NewsTransformer(nn.Module):
    def __init__(self, *, vocab_size: int, pad_id: int, config: dict[str, Any]) -> None:
        super().__init__()
        width = int(config["embedding_dim"])
        max_length = int(config["max_sequence_length"])
        self.pad_id = pad_id
        self.token_embedding = nn.Embedding(vocab_size, width, padding_idx=pad_id)
        self.position_embedding = nn.Embedding(max_length, width)
        layer = nn.TransformerEncoderLayer(
            d_model=width,
            nhead=int(config["attention_heads"]),
            dim_feedforward=int(config["ffn_dim"]),
            dropout=float(config["dropout"]),
            activation="gelu",
            batch_first=True,
            norm_first=True,
        )
        self.encoder = nn.TransformerEncoder(layer, num_layers=int(config["transformer_layers"]))
        self.norm = nn.LayerNorm(width)
        self.classifier = nn.Linear(width, 4)

    def forward(self, token_ids: torch.Tensor) -> torch.Tensor:
        mask = token_ids.eq(self.pad_id)
        positions = torch.arange(token_ids.shape[1], device=token_ids.device).unsqueeze(0)
        encoded = self.encoder(
            self.token_embedding(token_ids) + self.position_embedding(positions),
            src_key_padding_mask=mask,
        )
        weights = (~mask).unsqueeze(-1)
        pooled = (encoded * weights).sum(dim=1) / weights.sum(dim=1).clamp_min(1)
        return self.classifier(self.norm(pooled))


def set_determinism(seed: int) -> None:
    os.environ.setdefault("CUBLAS_WORKSPACE_CONFIG", ":4096:8")
    random.seed(seed)
    np.random.seed(seed)
    torch.manual_seed(seed)
    if torch.cuda.is_available():
        torch.cuda.manual_seed_all(seed)
    torch.use_deterministic_algorithms(True)


def capture_rng_state(device: torch.device) -> dict[str, Any]:
    return {
        "python_random_state": random.getstate(),
        "numpy_random_state": np.random.get_state(),
        "torch_rng_state": torch.get_rng_state(),
        "cuda_rng_state_all": torch.cuda.get_rng_state_all() if device.type == "cuda" else None,
    }


def restore_rng_state(state: dict[str, Any], device: torch.device) -> None:
    random.setstate(state["python_random_state"])
    np.random.set_state(state["numpy_random_state"])
    torch.set_rng_state(state["torch_rng_state"].cpu())
    if device.type == "cuda":
        cuda_states = state["cuda_rng_state_all"]
        if not cuda_states:
            raise RuntimeError("CUDA checkpoint lacks CUDA RNG states")
        torch.cuda.set_rng_state_all([value.cpu() for value in cuda_states])


def read_records(path: Path) -> list[dict[str, Any]]:
    with path.open("r", encoding="utf-8") as handle:
        return [json.loads(line) for line in handle if line.strip()]


def prepare_encoded_split(
    *,
    tokenizer: Tokenizer,
    tokenizer_hash: str,
    split: str,
    source_path: Path,
    source_hash: str,
    cache_root: Path,
    max_length: int,
    logger: Any,
) -> tuple[Path, Path, dict[str, Any]]:
    directory = cache_root / tokenizer_hash / split
    metadata_path = directory / "metadata.json"
    token_path = directory / "tokens.npy"
    label_path = directory / "labels.npy"
    if metadata_path.exists():
        metadata = strict_json_load(metadata_path)
        if (
            metadata.get("status") == "success"
            and metadata.get("source_sha256") == source_hash
            and metadata.get("tokenizer_sha256") == tokenizer_hash
            and metadata.get("max_length") == max_length
            and token_path.exists()
            and label_path.exists()
            and metadata.get("tokens_sha256") == sha256_file(token_path)
            and metadata.get("labels_sha256") == sha256_file(label_path)
        ):
            return token_path, label_path, metadata
        raise RuntimeError(f"invalid encoded AG News cache: {directory}")
    if directory.exists() and any(directory.iterdir()):
        raise FileExistsError(f"refusing to overwrite encoded cache: {directory}")
    directory.mkdir(parents=True, exist_ok=True)
    records = read_records(source_path)
    vocab = tokenizer.get_vocab()
    unk = int(vocab["[UNK]"])
    pad = int(vocab["[PAD]"])
    cls = int(vocab["[CLS]"])
    sep = int(vocab["[SEP]"])
    token_partial = token_path.with_suffix(".npy.partial")
    label_partial = label_path.with_suffix(".npy.partial")
    token_array = np.lib.format.open_memmap(
        token_partial, mode="w+", dtype=np.uint32, shape=(len(records), max_length)
    )
    label_array = np.lib.format.open_memmap(
        label_partial, mode="w+", dtype=np.int64, shape=(len(records),)
    )
    token_array[:] = pad
    lengths = []
    truncated = 0
    started = time.perf_counter()
    for begin in range(0, len(records), 512):
        batch = records[begin : begin + 512]
        encodings = tokenizer.encode_batch([record["text"] for record in batch])
        for offset, (record, encoding) in enumerate(zip(batch, encodings)):
            ids = [cls] + [int(value) if 0 <= int(value) < len(vocab) else unk for value in encoding.ids] + [sep]
            if len(ids) > max_length:
                truncated += 1
                ids = ids[:max_length]
                ids[-1] = sep
            token_array[begin + offset, : len(ids)] = ids
            label_array[begin + offset] = int(record["label"])
            lengths.append(len(ids))
        logger.info(
            "stage=ag-news-encode | scale=downstream | protocol=n/a | method=%s | attack=n/a | "
            "epsilon=n/a | clipping=n/a | batch=512 | vocab=%d | seed=n/a | task=%d/%d | shadow=n/a | "
            "elapsed=%.3fs | eta=n/a | successes=%d | failures=0 | split=%s",
            tokenizer_hash[:12], len(vocab), min(begin + len(batch), len(records)), len(records),
            time.perf_counter() - started, min(begin + len(batch), len(records)), split,
        )
    token_array.flush()
    label_array.flush()
    del token_array, label_array
    os.replace(token_partial, token_path)
    os.replace(label_partial, label_path)
    metadata = {
        "schema_version": 1, "status": "success", "split": split,
        "source_sha256": source_hash, "tokenizer_sha256": tokenizer_hash,
        "record_count": len(records), "max_length": max_length,
        "mean_encoded_length": float(np.mean(lengths)),
        "truncated_record_count": truncated,
        "truncated_fraction": truncated / len(records),
        "tokens_sha256": sha256_file(token_path), "labels_sha256": sha256_file(label_path),
        "completed_at_utc": utc_now(),
    }
    write_json_exclusive(metadata_path, metadata)
    return token_path, label_path, metadata


def evaluate(
    model: nn.Module, loader: DataLoader, device: torch.device
) -> tuple[dict[str, Any], float]:
    model.eval()
    labels: list[int] = []
    predictions: list[int] = []
    started = time.perf_counter()
    with torch.no_grad():
        for token_ids, targets in loader:
            logits = model(token_ids.to(device, non_blocking=True))
            predictions.extend(logits.argmax(dim=-1).cpu().tolist())
            labels.extend(targets.tolist())
    if device.type == "cuda":
        torch.cuda.synchronize(device)
    elapsed = time.perf_counter() - started
    return {
        "accuracy": float(accuracy_score(labels, predictions)),
        "macro_f1": float(f1_score(labels, predictions, average="macro")),
        "per_class_f1": [float(value) for value in f1_score(labels, predictions, average=None, labels=[0, 1, 2, 3])],
        "record_count": len(labels),
    }, elapsed


def train_one(
    *,
    config_path: Path,
    tokenizer_dir: Path,
    output_dir: Path,
    seed: int,
    log_path: Path,
) -> dict[str, Any]:
    config_path = config_path.resolve()
    config = strict_json_load(config_path)
    config_hash = sha256_file(config_path)
    tokenizer_path = tokenizer_dir / "tokenizer.json"
    tokenizer_metadata_path = tokenizer_dir / "metadata.json"
    tokenizer_meta = strict_json_load(tokenizer_metadata_path)
    tokenizer_hash = sha256_file(tokenizer_path)
    if tokenizer_meta.get("artifact_sha256") != tokenizer_hash:
        raise RuntimeError("downstream tokenizer artifact hash mismatch")
    result_path = output_dir / "result.json"
    if result_path.exists():
        result = strict_json_load(result_path)
        if (
            result.get("status") == "success"
            and result.get("downstream_config_sha256") == config_hash
            and result.get("tokenizer_sha256") == tokenizer_hash
            and int(result.get("seed", -1)) == seed
        ):
            return {**result, "checkpoint_reused": True}
        raise RuntimeError(f"stale, mismatched, or non-success downstream result exists: {result_path}")
    output_dir.mkdir(parents=True, exist_ok=True)
    run_started_at_utc = utc_now()
    data_meta = strict_json_load(PROJECT_ROOT / config["metadata_path"])
    logger = setup_logger(f"downstream.{tokenizer_meta.get('method_id')}.{seed}", log_path)
    set_determinism(seed)
    preferred = config["preferred_device"]
    if preferred == "cuda" and torch.cuda.is_available():
        device = torch.device("cuda")
    elif config["allow_cpu_fallback"]:
        device = torch.device("cpu")
    else:
        raise RuntimeError("CUDA requested but unavailable and CPU fallback disabled")
    tokenizer = Tokenizer.from_file(str(tokenizer_path))
    cache_root = PROJECT_ROOT / "data" / "downstream" / "encoded"
    encoded = {}
    for split in ("train", "validation", "test"):
        details = data_meta["splits"][split]
        encoded[split] = prepare_encoded_split(
            tokenizer=tokenizer,
            tokenizer_hash=tokenizer_meta["artifact_sha256"],
            split=split,
            source_path=PROJECT_ROOT / details["path"],
            source_hash=details["sha256"],
            cache_root=cache_root,
            max_length=int(config["max_sequence_length"]),
            logger=logger,
        )
    datasets = {
        split: MemmapTokenDataset(values[0], values[1]) for split, values in encoded.items()
    }
    generator = torch.Generator().manual_seed(seed)
    loaders = {
        "train": DataLoader(
            datasets["train"], batch_size=int(config["micro_batch_size"]), shuffle=True,
            num_workers=int(config["data_loader_workers"]), generator=generator,
            pin_memory=device.type == "cuda",
        ),
        "validation": DataLoader(
            datasets["validation"], batch_size=int(config["micro_batch_size"]), shuffle=False,
            num_workers=int(config["data_loader_workers"]), pin_memory=device.type == "cuda",
        ),
        "test": DataLoader(
            datasets["test"], batch_size=int(config["micro_batch_size"]), shuffle=False,
            num_workers=int(config["data_loader_workers"]), pin_memory=device.type == "cuda",
        ),
    }
    vocab = tokenizer.get_vocab()
    model = NewsTransformer(vocab_size=len(vocab), pad_id=int(vocab["[PAD]"]), config=config).to(device)
    optimizer = torch.optim.AdamW(
        model.parameters(), lr=float(config["learning_rate"]), weight_decay=float(config["weight_decay"])
    )
    criterion = nn.CrossEntropyLoss()
    accumulation = int(config["effective_batch_size"]) // int(config["micro_batch_size"])
    if accumulation <= 0 or int(config["effective_batch_size"]) % int(config["micro_batch_size"]):
        raise ValueError("effective batch size must be a multiple of micro batch size")
    convergence = []
    best_macro = -math.inf
    best_epoch = 0
    best_path: Path | None = None
    patience = 0
    start_epoch = 1
    prior_elapsed_seconds = 0.0
    checkpoint_pattern = re.compile(r"^checkpoint_epoch_(\d+)\.pt$")
    completed_checkpoints = sorted(
        (
            (int(match.group(1)), path)
            for path in output_dir.glob("checkpoint_epoch_*.pt")
            if (match := checkpoint_pattern.match(path.name)) is not None
        ),
        key=lambda item: item[0],
    )
    if completed_checkpoints:
        latest_epoch, latest_path = completed_checkpoints[-1]
        checkpoint = torch.load(latest_path, map_location="cpu", weights_only=False)
        required = {
            "model_state", "optimizer_state", "epoch", "convergence", "best_macro",
            "best_epoch", "best_checkpoint_name", "patience", "generator_state",
            "elapsed_seconds", "config_sha256", "tokenizer_sha256", "seed", "device_type",
            "python_random_state", "numpy_random_state", "torch_rng_state", "cuda_rng_state_all",
            "started_at_utc", "peak_gpu_memory_bytes", "peak_process_memory_bytes",
        }
        missing = required.difference(checkpoint)
        if missing:
            raise RuntimeError(
                f"cannot resume legacy/incomplete checkpoint {latest_path}: missing {sorted(missing)}"
            )
        if int(checkpoint["epoch"]) != latest_epoch:
            raise RuntimeError(f"checkpoint epoch/name mismatch: {latest_path}")
        if (
            checkpoint["config_sha256"] != config_hash
            or checkpoint["tokenizer_sha256"] != tokenizer_hash
            or int(checkpoint["seed"]) != seed
            or checkpoint["device_type"] != device.type
        ):
            raise RuntimeError(f"checkpoint provenance/device mismatch: {latest_path}")
        model.load_state_dict(checkpoint["model_state"])
        optimizer.load_state_dict(checkpoint["optimizer_state"])
        convergence = list(checkpoint["convergence"])
        best_macro = float(checkpoint["best_macro"])
        best_epoch = int(checkpoint["best_epoch"])
        best_path = output_dir / str(checkpoint["best_checkpoint_name"])
        patience = int(checkpoint["patience"])
        generator.set_state(checkpoint["generator_state"])
        restore_rng_state(checkpoint, device)
        prior_elapsed_seconds = float(checkpoint["elapsed_seconds"])
        run_started_at_utc = str(checkpoint["started_at_utc"])
        prior_peak_gpu_memory_bytes = checkpoint["peak_gpu_memory_bytes"]
        prior_peak_process_memory_bytes = int(checkpoint["peak_process_memory_bytes"] or 0)
        start_epoch = latest_epoch + 1
        if not best_path.exists():
            raise FileNotFoundError(f"best checkpoint referenced by resume state is missing: {best_path}")
        logger.info(
            "stage=ag-news-resume | method=%s | seed=%d | completed_epoch=%d | next_epoch=%d | "
            "best_epoch=%d | elapsed=%.3fs",
            tokenizer_meta.get("method_id", tokenizer_meta.get("method")), seed, latest_epoch,
            start_epoch, best_epoch, prior_elapsed_seconds,
        )
    started = time.perf_counter()
    if not completed_checkpoints:
        prior_peak_gpu_memory_bytes = None
        prior_peak_process_memory_bytes = 0
    if device.type == "cuda":
        torch.cuda.reset_peak_memory_stats(device)
    epoch_range = (
        range(start_epoch, int(config["epochs"]) + 1)
        if patience < int(config["early_stopping_patience"])
        else range(0)
    )
    for epoch in epoch_range:
        model.train()
        optimizer.zero_grad(set_to_none=True)
        loss_sum = 0.0
        examples = 0
        for batch_index, (token_ids, targets) in enumerate(loaders["train"], start=1):
            token_ids = token_ids.to(device, non_blocking=True)
            targets = targets.to(device, non_blocking=True)
            loss = criterion(model(token_ids), targets)
            (loss / accumulation).backward()
            if batch_index % accumulation == 0 or batch_index == len(loaders["train"]):
                optimizer.step()
                optimizer.zero_grad(set_to_none=True)
            loss_sum += float(loss.detach().cpu()) * len(targets)
            examples += len(targets)
            if batch_index % 200 == 0:
                logger.info(
                    "stage=ag-news-train | scale=downstream | protocol=n/a | method=%s | attack=n/a | "
                    "epsilon=n/a | clipping=n/a | batch=%d | vocab=%d | seed=%d | task=%d/%d | shadow=n/a | "
                    "elapsed=%.3fs | eta=n/a | successes=%d | failures=0 | epoch=%d/%d",
                    tokenizer_meta.get("method_id", tokenizer_meta.get("method")), config["effective_batch_size"],
                    len(vocab), seed, batch_index, len(loaders["train"]), time.perf_counter() - started,
                    batch_index, epoch, config["epochs"],
                )
        validation, validation_seconds = evaluate(model, loaders["validation"], device)
        row = {"epoch": epoch, "training_loss": loss_sum / examples,
               "validation_accuracy": validation["accuracy"],
               "validation_macro_f1": validation["macro_f1"],
               "validation_seconds": validation_seconds}
        convergence.append(row)
        if validation["macro_f1"] > best_macro:
            best_macro = validation["macro_f1"]
            best_epoch = epoch
            best_path = output_dir / f"checkpoint_epoch_{epoch}.pt"
            patience = 0
        else:
            patience += 1
        epoch_path = output_dir / f"checkpoint_epoch_{epoch}.pt"
        if epoch_path.exists():
            raise FileExistsError(f"refusing to overwrite completed checkpoint: {epoch_path}")
        checkpoint_payload = {
            "model_state": model.state_dict(),
            "optimizer_state": optimizer.state_dict(),
            "epoch": epoch,
            "validation": validation,
            "convergence": convergence,
            "best_macro": best_macro,
            "best_epoch": best_epoch,
            "best_checkpoint_name": best_path.name if best_path is not None else epoch_path.name,
            "patience": patience,
            "generator_state": generator.get_state(),
            **capture_rng_state(device),
            "config_sha256": config_hash,
            "tokenizer_sha256": tokenizer_hash,
            "seed": seed,
            "device_type": device.type,
            "started_at_utc": run_started_at_utc,
            "peak_gpu_memory_bytes": (
                max(
                    int(prior_peak_gpu_memory_bytes or 0),
                    int(torch.cuda.max_memory_allocated(device)),
                )
                if device.type == "cuda" else None
            ),
            "peak_process_memory_bytes": max(
                prior_peak_process_memory_bytes,
                int(peak_working_set_bytes() or 0),
            ),
            "elapsed_seconds": prior_elapsed_seconds + time.perf_counter() - started,
        }
        partial_path = epoch_path.with_suffix(".pt.partial")
        if partial_path.exists():
            raise FileExistsError(f"incomplete checkpoint requires manual audit: {partial_path}")
        torch.save(checkpoint_payload, partial_path)
        os.replace(partial_path, epoch_path)
        logger.info("stage=ag-news-validation | method=%s | seed=%d | epoch=%d | accuracy=%.8f | macro_f1=%.8f | best_epoch=%d",
                    tokenizer_meta.get("method_id", tokenizer_meta.get("method")), seed, epoch,
                    validation["accuracy"], validation["macro_f1"], best_epoch)
        if patience >= int(config["early_stopping_patience"]):
            break
    assert best_path is not None
    best = torch.load(best_path, map_location="cpu", weights_only=False)
    model.load_state_dict(best["model_state"])
    test_metrics, inference_seconds = evaluate(model, loaders["test"], device)
    gpu_peak = (
        max(int(prior_peak_gpu_memory_bytes or 0), int(torch.cuda.max_memory_allocated(device)))
        if device.type == "cuda" else None
    )
    process_peak = max(prior_peak_process_memory_bytes, int(peak_working_set_bytes() or 0))
    result = {
        "schema_version": 1, "status": "success",
        "started_at_utc": run_started_at_utc,
        "downstream_config_sha256": config_hash,
        "tokenizer_metadata_sha256": sha256_file(tokenizer_metadata_path),
        "dataset_id": data_meta["dataset_id"], "dataset_revision": data_meta["resolved_revision"],
        "split_hashes": {split: data_meta["splits"][split]["sha256"] for split in data_meta["splits"]},
        "seed": seed, "method_id": tokenizer_meta.get("method_id", tokenizer_meta.get("method")),
        "tokenizer_sha256": tokenizer_meta["artifact_sha256"],
        "tokenizer_actual_vocab_size": tokenizer_meta["actual_vocab_size"],
        "architecture": {
            key: config[key] for key in (
                "embedding_dim", "transformer_layers", "attention_heads", "ffn_dim",
                "max_sequence_length", "dropout", "epochs", "early_stopping_patience",
                "effective_batch_size", "micro_batch_size", "learning_rate", "weight_decay",
            )
        },
        "device": str(device), "torch_version": torch.__version__,
        "cuda_version": torch.version.cuda, "cuda_device_name": torch.cuda.get_device_name(device) if device.type == "cuda" else None,
        "convergence": convergence, "best_validation_epoch": best_epoch,
        "best_validation_macro_f1": best_macro,
        "test": test_metrics,
        "test_inference_seconds": inference_seconds,
        "mean_sequence_length": encoded["test"][2]["mean_encoded_length"],
        "test_truncated_fraction": encoded["test"][2]["truncated_fraction"],
        "training_elapsed_seconds": prior_elapsed_seconds + time.perf_counter() - started,
        "peak_gpu_memory_bytes": gpu_peak, "peak_process_memory_bytes": process_peak,
        "checkpoint_path": str(best_path.relative_to(PROJECT_ROOT)).replace("\\", "/"),
        "completed_at_utc": utc_now(), "environment": environment_metadata(),
        "test_used_for_model_selection": False, "checkpoint_reused": False,
    }
    write_json_exclusive(result_path, result)
    return result


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--tokenizer-dir", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--seed", type=int, required=True)
    parser.add_argument("--log", type=Path, required=True)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    result = train_one(
        config_path=args.config.resolve(), tokenizer_dir=args.tokenizer_dir.resolve(),
        output_dir=args.output_dir.resolve(), seed=args.seed, log_path=args.log.resolve(),
    )
    print(f"status={result['status']} method={result['method_id']} seed={result['seed']} accuracy={result['test']['accuracy']:.8f} macro_f1={result['test']['macro_f1']:.8f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
