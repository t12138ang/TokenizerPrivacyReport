"""Deterministic, tiny end-to-end tokenizer-MIA smoke test.

This is an independent reimplementation.  It does not import or modify the
audited third-party repository.  The selected attack mirrors the upstream
compression-rate baseline: UTF-8 bytes per emitted token is the membership
signal and ROC AUC is the reported attack score.
"""

from __future__ import annotations

import argparse
import ctypes
import importlib.metadata
import json
import logging
import math
import os
import platform
import random
import subprocess
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
from sklearn.metrics import auc, roc_curve
from tokenizers import Tokenizer
from tokenizers.models import BPE
from tokenizers.pre_tokenizers import Whitespace
from tokenizers.trainers import BpeTrainer


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SPECIAL_TOKENS = ["[UNK]", "[CLS]", "[SEP]", "[PAD]", "[MASK]"]


class SeverityCounter(logging.Handler):
    """Count warnings and errors emitted by this run."""

    def __init__(self) -> None:
        super().__init__()
        self.warning_count = 0
        self.error_count = 0

    def emit(self, record: logging.LogRecord) -> None:
        if record.levelno >= logging.ERROR:
            self.error_count += 1
        elif record.levelno >= logging.WARNING:
            self.warning_count += 1


@dataclass(frozen=True)
class DatasetRecord:
    name: str
    documents: list[str]


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def elapsed_text(started: float) -> str:
    seconds = time.perf_counter() - started
    return f"{seconds:.3f}s"


def write_json_exclusive(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8", newline="\n") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2, sort_keys=True, allow_nan=False)
        handle.write("\n")


def setup_logger(log_path: Path) -> tuple[logging.Logger, SeverityCounter]:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    logger = logging.getLogger("tokenizer_privacy_smoke")
    logger.setLevel(logging.INFO)
    logger.handlers.clear()
    formatter = logging.Formatter(
        fmt="%(asctime)s | %(levelname)s | %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%S%z",
    )
    stream = logging.StreamHandler(sys.stdout)
    stream.setFormatter(formatter)
    file_handler = logging.FileHandler(log_path, mode="a", encoding="utf-8")
    file_handler.setFormatter(formatter)
    counter = SeverityCounter()
    logger.addHandler(stream)
    logger.addHandler(file_handler)
    logger.addHandler(counter)
    return logger, counter


def make_micro_corpus(seed: int, dataset_count: int, documents_per_dataset: int) -> list[DatasetRecord]:
    """Create a balanced, deterministic site-like micro corpus.

    Each dataset has the same template and length distribution.  Only its
    randomly generated signature terms differ, so this validates plumbing
    without downloading C4 or claiming a distribution-matched evaluation.
    """

    rng = random.Random(seed)
    themes = [
        ("astronomy", "orbit", "telescope", "nebula"),
        ("botany", "canopy", "pollen", "rhizome"),
        ("ceramics", "kiln", "glaze", "clay"),
        ("navigation", "harbor", "compass", "latitude"),
        ("acoustics", "resonance", "timbre", "waveform"),
        ("geology", "basalt", "stratum", "mineral"),
        ("robotics", "actuator", "sensor", "kinematics"),
        ("ecology", "wetland", "habitat", "watershed"),
    ]
    if dataset_count > len(themes):
        raise ValueError(f"dataset_count must be <= {len(themes)} for this smoke corpus")

    alphabet = "abcdefghjkmnpqrstuvwxyz"
    records: list[DatasetRecord] = []
    for dataset_index, theme in enumerate(themes[:dataset_count]):
        signature = "".join(rng.choice(alphabet) for _ in range(18))
        documents: list[str] = []
        for document_index in range(documents_per_dataset):
            rotation = document_index % 3
            ordered_terms = theme[rotation:] + theme[:rotation]
            repetition = 3 + (document_index % 4)
            signature_phrase = " ".join([signature] * repetition)
            document = (
                f"Field note {document_index:02d} reports a careful reproducible study of "
                f"{' '.join(ordered_terms)}. Shared methods compare evidence, measurements, "
                f"and uncertainty. Dataset marker {signature_phrase}. "
                f"The observation cycle is {document_index % 5} and the protocol remains stable."
            )
            documents.append(document)
        records.append(DatasetRecord(name=f"site_{dataset_index:02d}", documents=documents))
    return records


def train_bpe(texts: list[str], vocab_size: int, min_frequency: int) -> Tokenizer:
    tokenizer = Tokenizer(BPE(unk_token="[UNK]"))
    tokenizer.pre_tokenizer = Whitespace()
    trainer = BpeTrainer(
        vocab_size=vocab_size,
        min_frequency=min_frequency,
        special_tokens=SPECIAL_TOKENS,
        show_progress=False,
    )
    tokenizer.train_from_iterator(texts, trainer=trainer)
    return tokenizer


def compression_rate(tokenizer: Tokenizer, documents: list[str]) -> tuple[float, int, int]:
    total_bytes = sum(len(text.encode("utf-8")) for text in documents)
    total_tokens = sum(len(tokenizer.encode(text).ids) for text in documents)
    if total_tokens == 0:
        raise RuntimeError("tokenizer emitted zero tokens")
    return total_bytes / total_tokens, total_bytes, total_tokens


def sigmoid(value: float) -> float:
    return 1.0 / (1.0 + math.exp(-value))


def peak_working_set_bytes() -> int | None:
    if os.name == "nt":
        class ProcessMemoryCounters(ctypes.Structure):
            _fields_ = [
                ("cb", ctypes.c_ulong),
                ("PageFaultCount", ctypes.c_ulong),
                ("PeakWorkingSetSize", ctypes.c_size_t),
                ("WorkingSetSize", ctypes.c_size_t),
                ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
                ("QuotaPagedPoolUsage", ctypes.c_size_t),
                ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
                ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                ("PagefileUsage", ctypes.c_size_t),
                ("PeakPagefileUsage", ctypes.c_size_t),
            ]

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        psapi = ctypes.WinDLL("psapi", use_last_error=True)
        kernel32.GetCurrentProcess.restype = ctypes.c_void_p
        get_process_memory_info = psapi.GetProcessMemoryInfo
        get_process_memory_info.argtypes = [
            ctypes.c_void_p,
            ctypes.POINTER(ProcessMemoryCounters),
            ctypes.c_ulong,
        ]
        get_process_memory_info.restype = ctypes.c_int

        counters = ProcessMemoryCounters()
        counters.cb = ctypes.sizeof(counters)
        process = kernel32.GetCurrentProcess()
        ok = get_process_memory_info(
            process, ctypes.byref(counters), counters.cb
        )
        if ok:
            return int(counters.PeakWorkingSetSize)
        return None

    try:
        import resource

        peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        return int(peak * 1024 if sys.platform != "darwin" else peak)
    except (ImportError, OSError):
        return None


def total_physical_memory_bytes() -> int | None:
    if os.name == "nt":
        class MemoryStatusEx(ctypes.Structure):
            _fields_ = [
                ("dwLength", ctypes.c_ulong),
                ("dwMemoryLoad", ctypes.c_ulong),
                ("ullTotalPhys", ctypes.c_ulonglong),
                ("ullAvailPhys", ctypes.c_ulonglong),
                ("ullTotalPageFile", ctypes.c_ulonglong),
                ("ullAvailPageFile", ctypes.c_ulonglong),
                ("ullTotalVirtual", ctypes.c_ulonglong),
                ("ullAvailVirtual", ctypes.c_ulonglong),
                ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
            ]

        status = MemoryStatusEx()
        status.dwLength = ctypes.sizeof(status)
        if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):
            return int(status.ullTotalPhys)
    return None


def package_versions() -> dict[str, str]:
    names = ["datasets", "joblib", "mpmath", "numpy", "powerlaw", "scikit-learn", "tokenizers", "tqdm"]
    versions: dict[str, str] = {}
    for name in names:
        try:
            versions[name] = importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:
            versions[name] = "not-installed"
    return versions


def official_commit() -> str | None:
    repository = PROJECT_ROOT / "third_party" / "Tokenizer-MIA"
    try:
        completed = subprocess.run(
            ["git", "-C", str(repository), "rev-parse", "HEAD"],
            check=True,
            capture_output=True,
            text=True,
        )
        return completed.stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return None


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--log", type=Path, required=True)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    started_perf = time.perf_counter()
    started_at = utc_now()
    logger, severity = setup_logger(args.log.resolve())

    try:
        output_path = args.output.resolve()
        if output_path.exists():
            raise FileExistsError(f"refusing to overwrite existing result: {output_path}")

        config = json.loads(args.config.resolve().read_text(encoding="utf-8"))
        seed = int(config["seed"])
        random.seed(seed)
        np.random.seed(seed)
        os.environ["PYTHONHASHSEED"] = str(seed)
        os.environ["TOKENIZERS_PARALLELISM"] = "false"

        logger.info("[stage 1/5][elapsed %s] preflight and deterministic seed=%d", elapsed_text(started_perf), seed)
        logger.warning("This tiny generated corpus validates execution only; it is not a paper-level reproduction dataset.")

        data_dir = PROJECT_ROOT / "data" / "smoke"
        artifacts_dir = output_path.parent / "artifacts"
        if data_dir.exists():
            raise FileExistsError(f"refusing to overwrite existing smoke data: {data_dir}")
        if artifacts_dir.exists():
            raise FileExistsError(f"refusing to overwrite existing artifacts: {artifacts_dir}")
        data_dir.mkdir(parents=True)
        artifacts_dir.mkdir(parents=True)

        corpus = make_micro_corpus(
            seed=seed,
            dataset_count=int(config["dataset_count"]),
            documents_per_dataset=int(config["documents_per_dataset"]),
        )
        for record in corpus:
            write_json_exclusive(data_dir / f"{record.name}.json", record.documents)

        dataset_names = [record.name for record in corpus]
        target_rng = random.Random(seed)
        shadow_rng = random.Random(seed + 1)
        target_member_count = round(len(corpus) * float(config["target_member_fraction"]))
        shadow_member_count = round(len(corpus) * float(config["shadow_member_fraction"]))
        target_members = sorted(target_rng.sample(dataset_names, target_member_count))
        target_non_members = sorted(set(dataset_names) - set(target_members))
        shadow_members = sorted(shadow_rng.sample(dataset_names, shadow_member_count))
        corpus_by_name = {record.name: record.documents for record in corpus}
        manifest = {
            "source": "deterministically generated smoke-only micro corpus",
            "seed": seed,
            "member_datasets": target_members,
            "non_member_datasets": target_non_members,
            "shadow_member_datasets": shadow_members,
        }
        write_json_exclusive(data_dir / "manifest.json", manifest)
        logger.info(
            "[stage 2/5][elapsed %s] generated %d datasets / %d documents",
            elapsed_text(started_perf),
            len(corpus),
            sum(len(record.documents) for record in corpus),
        )

        target_texts = [text for name in target_members for text in corpus_by_name[name]]
        target_tokenizer = train_bpe(
            target_texts,
            vocab_size=int(config["requested_vocab_size"]),
            min_frequency=int(config["min_frequency"]),
        )
        target_path = artifacts_dir / "target_tokenizer.json"
        target_tokenizer.save(str(target_path))
        logger.info(
            "[stage 3/5][elapsed %s] trained 1 target tokenizer (actual vocab=%d)",
            elapsed_text(started_perf),
            target_tokenizer.get_vocab_size(),
        )

        shadow_texts = [text for name in shadow_members for text in corpus_by_name[name]]
        shadow_tokenizer = train_bpe(
            shadow_texts,
            vocab_size=int(config["requested_vocab_size"]),
            min_frequency=int(config["min_frequency"]),
        )
        shadow_path = artifacts_dir / "shadow_tokenizer.json"
        shadow_tokenizer.save(str(shadow_path))
        logger.info(
            "[stage 4/5][elapsed %s] trained 1 shadow tokenizer (actual vocab=%d)",
            elapsed_text(started_perf),
            shadow_tokenizer.get_vocab_size(),
        )

        labels: list[int] = []
        predictions: list[float] = []
        details: list[dict[str, Any]] = []
        for name in dataset_names:
            bpt, byte_count, token_count = compression_rate(target_tokenizer, corpus_by_name[name])
            probability_signal = sigmoid(bpt)
            is_member = int(name in target_members)
            labels.append(is_member)
            predictions.append(probability_signal)
            details.append(
                {
                    "dataset": name,
                    "is_member": bool(is_member),
                    "bytes": byte_count,
                    "tokens": token_count,
                    "bytes_per_token": bpt,
                    "membership_signal": probability_signal,
                }
            )

        fpr, tpr, thresholds = roc_curve(labels, predictions)
        roc_auc = float(auc(fpr, tpr))
        balanced_accuracy = float(np.max(1.0 - (fpr + (1.0 - tpr)) / 2.0))
        valid_low_fpr = np.where(fpr <= 0.01)[0]
        tpr_at_low_fpr = float(np.max(tpr[valid_low_fpr])) if valid_low_fpr.size else 0.0
        logger.info(
            "[stage 5/5][elapsed %s] attack complete: ROC AUC=%.6f",
            elapsed_text(started_perf),
            roc_auc,
        )

        peak_bytes = peak_working_set_bytes()
        elapsed_seconds = time.perf_counter() - started_perf
        result = {
            "schema_version": 1,
            "status": "success",
            "phase": "smoke_test",
            "research_title": "面向大模型分词器成员泄露的截断局部差分隐私与同态聚合防御研究",
            "started_at_utc": started_at,
            "completed_at_utc": utc_now(),
            "parameters": config,
            "data": {
                "source": manifest["source"],
                "dataset_count": len(corpus),
                "document_count": sum(len(record.documents) for record in corpus),
                "member_dataset_count": len(target_members),
                "non_member_dataset_count": len(target_non_members),
            },
            "tokenizers": {
                "target": {
                    "count": 1,
                    "requested_vocab_size": int(config["requested_vocab_size"]),
                    "actual_vocab_size": target_tokenizer.get_vocab_size(),
                    "member_dataset_count": len(target_members),
                    "artifact": str(target_path.relative_to(PROJECT_ROOT)),
                },
                "shadow": {
                    "count": 1,
                    "requested_vocab_size": int(config["requested_vocab_size"]),
                    "actual_vocab_size": shadow_tokenizer.get_vocab_size(),
                    "member_dataset_count": len(shadow_members),
                    "artifact": str(shadow_path.relative_to(PROJECT_ROOT)),
                },
            },
            "attack": {
                "method": "compression_rate",
                "definition": "UTF-8 bytes per target-tokenizer token; sigmoid is applied as in upstream and is monotonic",
                "score_name": "roc_auc",
                "score": roc_auc,
                "balanced_accuracy": balanced_accuracy,
                "tpr_at_fpr_le_0_01": tpr_at_low_fpr,
                "fpr": fpr.tolist(),
                "tpr": tpr.tolist(),
                "thresholds": [float(value) if np.isfinite(value) else None for value in thresholds],
                "details": details,
            },
            "performance": {
                "elapsed_seconds": elapsed_seconds,
                "peak_memory_bytes": peak_bytes,
                "peak_memory_mib": (peak_bytes / 1024**2) if peak_bytes is not None else None,
                "gpu_used": False,
            },
            "environment": {
                "python_version": platform.python_version(),
                "python_executable": sys.executable,
                "platform": platform.platform(),
                "machine": platform.machine(),
                "processor": platform.processor(),
                "logical_cpu_count": os.cpu_count(),
                "total_physical_memory_bytes": total_physical_memory_bytes(),
                "packages": package_versions(),
                "official_repo_commit": official_commit(),
            },
            "logging": {
                "log_file": str(args.log.resolve().relative_to(PROJECT_ROOT)),
                "warning_count": severity.warning_count,
                "error_count": severity.error_count,
            },
            "limitations": [
                "Generated micro corpus, not C4.",
                "Eight dataset groups and one shadow tokenizer are insufficient for a paper-level estimate.",
                "The score validates the pipeline only and must not be cited as an experimental conclusion.",
            ],
        }
        write_json_exclusive(output_path, result)
        logger.info("Smoke test succeeded; result written without overwrite: %s", output_path)
        return 0
    except BaseException:
        logger.exception("Smoke test failed after %s; full traceback follows", elapsed_text(started_perf))
        raise


if __name__ == "__main__":
    raise SystemExit(main())
