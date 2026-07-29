"""Strict output, progress logging, hashing, and runtime metadata helpers."""

from __future__ import annotations

import ctypes
import hashlib
import importlib.metadata
import json
import logging
import os
import platform
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable


PROJECT_ROOT = Path(__file__).resolve().parents[2]


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def strict_json_load(path: Path) -> Any:
    def reject_constant(value: str) -> None:
        raise ValueError(f"non-standard JSON constant in {path}: {value}")

    return json.loads(path.read_text(encoding="utf-8"), parse_constant=reject_constant)


def strict_json_dumps(payload: Any, *, indent: int | None = 2) -> str:
    return json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        indent=indent,
        allow_nan=False,
        separators=None if indent else (",", ":"),
    )


def write_json_exclusive(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        raise FileExistsError(f"refusing to overwrite JSON output: {path}")
    partial = path.with_suffix(path.suffix + ".partial")
    if partial.exists():
        raise FileExistsError(f"partial JSON output requires audit: {partial}")
    with partial.open("x", encoding="utf-8", newline="\n") as handle:
        handle.write(strict_json_dumps(payload))
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())
    try:
        os.link(partial, path)
    except BaseException:
        # Preserve the complete partial file for forensic inspection.
        raise
    else:
        partial.unlink()


def write_text_exclusive(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        raise FileExistsError(f"refusing to overwrite text output: {path}")
    partial = path.with_suffix(path.suffix + ".partial")
    if partial.exists():
        raise FileExistsError(f"partial text output requires audit: {partial}")
    with partial.open("x", encoding="utf-8", newline="\n") as handle:
        handle.write(content.rstrip("\r\n"))
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())
    try:
        os.link(partial, path)
    except BaseException:
        raise
    else:
        partial.unlink()


def write_json_atomic_replace(path: Path, payload: Any) -> None:
    """Atomically replace a mutable checkpoint while preserving crash residue."""
    path.parent.mkdir(parents=True, exist_ok=True)
    partial = path.with_suffix(path.suffix + ".partial")
    if partial.exists():
        raise FileExistsError(f"partial JSON checkpoint requires audit: {partial}")
    with partial.open("x", encoding="utf-8", newline="\n") as handle:
        handle.write(strict_json_dumps(payload))
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(partial, path)


def canonical_sha256(payload: Any) -> str:
    encoded = strict_json_dumps(payload, indent=None).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def sha256_file(path: Path, chunk_size: int = 1024 * 1024) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(chunk_size):
            digest.update(chunk)
    return digest.hexdigest()


def sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def git_state() -> dict[str, Any]:
    def run(*args: str) -> str:
        completed = subprocess.run(
            ["git", "-C", str(PROJECT_ROOT), *args],
            check=True,
            capture_output=True,
            text=True,
        )
        return completed.stdout.strip()

    try:
        commit = run("rev-parse", "HEAD")
        status = run("status", "--porcelain=v1", "--untracked-files=no")
        branch = run("branch", "--show-current")
        return {"commit": commit, "branch": branch, "tracked_worktree_dirty": bool(status)}
    except (OSError, subprocess.CalledProcessError) as exc:
        return {"commit": None, "branch": None, "tracked_worktree_dirty": None, "error": str(exc)}


def package_versions(
    names: Iterable[str] = (
        "datasets", "numpy", "scikit-learn", "tokenizers", "powerlaw",
        "phe", "gmpy2", "torch", "matplotlib", "pandas", "scipy",
    ),
) -> dict[str, str]:
    result: dict[str, str] = {}
    for name in names:
        try:
            result[name] = importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:
            result[name] = "not-installed"
    return result


def total_physical_memory_bytes() -> int | None:
    if os.name != "nt":
        try:
            return int(os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES"))
        except (AttributeError, OSError, ValueError):
            return None

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


def peak_working_set_bytes() -> int | None:
    if os.name != "nt":
        try:
            import resource

            peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
            return int(peak * 1024 if sys.platform != "darwin" else peak)
        except (ImportError, OSError):
            return None

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
    get_info = psapi.GetProcessMemoryInfo
    get_info.argtypes = [ctypes.c_void_p, ctypes.POINTER(ProcessMemoryCounters), ctypes.c_ulong]
    get_info.restype = ctypes.c_int
    counters = ProcessMemoryCounters()
    counters.cb = ctypes.sizeof(counters)
    if get_info(kernel32.GetCurrentProcess(), ctypes.byref(counters), counters.cb):
        return int(counters.PeakWorkingSetSize)
    return None


def environment_metadata() -> dict[str, Any]:
    return {
        "generated_at_utc": utc_now(),
        "python_version": platform.python_version(),
        "python_executable": sys.executable,
        "platform": platform.platform(),
        "machine": platform.machine(),
        "processor": platform.processor(),
        "logical_cpu_count": os.cpu_count(),
        "total_physical_memory_bytes": total_physical_memory_bytes(),
        "packages": package_versions(),
        "git": git_state(),
    }


def setup_logger(name: str, log_path: Path) -> logging.Logger:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    logger = logging.getLogger(name)
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
    logger.addHandler(stream)
    logger.addHandler(file_handler)
    return logger


def log_progress(
    logger: logging.Logger,
    *,
    started: float,
    stage: str,
    protocol: str = "n/a",
    seed: int | str = "n/a",
    vocab_size: int | str = "n/a",
    method: str = "n/a",
    shadow: int | str = "n/a",
    completed: int | str = "n/a",
    total: int | str = "n/a",
    failures: int = 0,
    result_path: str | Path = "n/a",
    level: int = logging.INFO,
) -> None:
    logger.log(
        level,
        "stage=%s | protocol=%s | seed=%s | vocab=%s | method=%s | shadow=%s | "
        "progress=%s/%s | elapsed=%.3fs | failures=%d | result=%s",
        stage,
        protocol,
        seed,
        vocab_size,
        method,
        shadow,
        completed,
        total,
        time.perf_counter() - started,
        failures,
        result_path,
    )
