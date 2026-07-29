"""Fast course-report audit with explicit measured/extrapolated provenance checks."""

from __future__ import annotations

import argparse
import csv
import math
from pathlib import Path
from typing import Any

from src.utils.run_metadata import PROJECT_ROOT, sha256_file, strict_json_load, utc_now, write_text_exclusive


TIMING_METRICS = {
    "encryption_seconds", "aggregation_seconds", "noise_encryption_seconds",
    "decryption_seconds", "top_b_selection_seconds", "round_total_seconds",
}


def rows(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def finite_tree(value: Any) -> bool:
    if isinstance(value, float):
        return math.isfinite(value)
    if isinstance(value, dict):
        return all(finite_tree(child) for child in value.values())
    if isinstance(value, list):
        return all(finite_tree(child) for child in value)
    return True


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--require-pdf", action="store_true")
    args = parser.parse_args()
    config = strict_json_load(args.config.resolve())
    result_root = PROJECT_ROOT / config["results_root"]
    fast_root = result_root / "report_fast"
    checks: list[tuple[str, str]] = []

    for label, path in (
        ("Main defenses", result_root / "defenses" / "main" / "pipeline_state.json"),
        ("Development downstream", result_root / "downstream" / "development_state.json"),
        ("Main downstream", result_root / "downstream" / "main_state.json"),
    ):
        state = strict_json_load(path)
        if state.get("status") != "success" or int(state.get("failures", 0)) != 0:
            raise RuntimeError(f"{label} is incomplete or failed: {path}")
        checks.append((label, "PASS"))

    cell_files = sorted((result_root / "crypto" / "cells").glob("clients_*_K_*.json"))
    if len(cell_files) != 24:
        raise RuntimeError(f"expected 24 complete Paillier cells after cutoff, found {len(cell_files)}")
    complete_combinations = set()
    for path in cell_files:
        cell = strict_json_load(path)
        if (
            cell.get("status") != "success"
            or not cell.get("formal_real_paillier")
            or int(cell.get("measured_repetitions", 0)) != 20
            or len(cell.get("raw_measurements", [])) != 20
            or not all(row.get("equality") for row in cell.get("raw_measurements", []))
        ):
            raise RuntimeError(f"invalid complete crypto cell: {path}")
        complete_combinations.add((int(cell["client_count"]), int(cell["candidate_dimension"])))
    checks.append(("24 readable complete Paillier cells", "PASS"))

    source_hashes = strict_json_load(fast_root / "source_hashes.json")
    for relative, expected_hash in source_hashes["source_hashes"].items():
        path = PROJECT_ROOT / relative
        if sha256_file(path) != expected_hash:
            raise RuntimeError(f"source changed after fast-report extraction: {relative}")
    checks.append(("Source hashes unchanged after cutoff", "PASS"))

    raw = rows(fast_root / "crypto_measured_raw.csv")
    benchmark_rows = [row for row in raw if row["record_type"] == "benchmark_repetition"]
    key_rows = [row for row in raw if row["record_type"] == "key_generation_repetition"]
    if len(benchmark_rows) != 480 or len(key_rows) != 20:
        raise RuntimeError(f"unexpected measured raw row counts: benchmark={len(benchmark_rows)}, keygen={len(key_rows)}")
    if sum(int(row["failure"]) for row in raw) != 0 or sum(int(row["success"]) for row in benchmark_rows) != 480:
        raise AssertionError("measured raw correctness counts failed")
    checks.append(("480 benchmark repetitions + 20 key-generation repetitions", "PASS"))

    combined = rows(fast_root / "crypto_combined.csv")
    if len(combined) != 30 * len(TIMING_METRICS):
        raise RuntimeError(f"combined crypto row count mismatch: {len(combined)}")
    if set(row["metric"] for row in combined) != TIMING_METRICS:
        raise RuntimeError("combined crypto metric set mismatch")
    measured = [row for row in combined if row["data_source"] == "measured"]
    extrapolated = [row for row in combined if row["data_source"] == "extrapolated"]
    if len(measured) != 24 * len(TIMING_METRICS) or len(extrapolated) != 6 * len(TIMING_METRICS):
        raise RuntimeError("measured/extrapolated combined counts mismatch")
    missing = {
        (32, 4096), (64, 128), (64, 512), (64, 1024), (64, 2048), (64, 4096),
    }
    if {(int(row["clients"]), int(row["K"])) for row in extrapolated} != missing:
        raise RuntimeError("extrapolated combination set differs from cutoff record")
    if {(int(row["clients"]), int(row["K"])) for row in measured} != complete_combinations:
        raise RuntimeError("measured combined rows differ from complete cell artifacts")
    for row in combined:
        values = [float(row[key]) for key in ("value", "lower_bound", "upper_bound")]
        if not all(math.isfinite(value) for value in values) or not values[0] >= 0:
            raise ValueError(f"invalid combined numeric value: {row}")
        if not values[1] <= values[0] <= values[2]:
            raise AssertionError(f"interval excludes point value: {row}")
        if row["data_source"] == "extrapolated" and (not row["model_name"] or row["measured_n"] != "0"):
            raise RuntimeError(f"extrapolated row lacks provenance: {row}")
        if row["data_source"] == "measured" and int(row["measured_n"]) != 20:
            raise RuntimeError(f"measured row lacks 20 repetitions: {row}")
    checks.append(("M/E labels, intervals, finite values and missing combinations", "PASS"))

    models = strict_json_load(fast_root / "crypto_scaling_models.json")
    if models.get("status") != "success" or models.get("training_cell_count") != 24 or not finite_tree(models):
        raise RuntimeError("scaling model artifact failed structural or finite-value audit")
    for metric in TIMING_METRICS:
        block = models["metrics"].get(metric)
        if not block or block["selected_model"] not in {"M1", "M2", "M3", "M4"} or len(block["models"]) != 4:
            raise RuntimeError(f"model comparison incomplete for {metric}")
    checks.append(("M1-M4 comparison and selected scaling models", "PASS"))

    figure_manifest = strict_json_load(result_root / "figures" / "figure_manifest.json")
    if figure_manifest.get("status") != "success" or int(figure_manifest.get("figure_count", 0)) < 20:
        raise RuntimeError("figure manifest is missing core report figures")
    for figure in figure_manifest["figures"]:
        source_path = PROJECT_ROOT / figure["source_csv"]
        if sha256_file(source_path) != figure["source_sha256"]:
            raise RuntimeError(f"figure source hash mismatch: {source_path}")
        for item in figure["files"]:
            path = PROJECT_ROOT / item["path"]
            if sha256_file(path) != item["sha256"]:
                raise RuntimeError(f"figure file hash mismatch: {path}")
    checks.append(("Figure files and source CSV hashes", "PASS"))

    crypto_table = (PROJECT_ROOT / "paper" / "generated" / "table_crypto.tex").read_text(encoding="utf-8")
    macros = (PROJECT_ROOT / "paper" / "generated" / "results_macros.tex").read_text(encoding="utf-8")
    if "E$^{*}$" not in crypto_table or "带 $^{*}$" not in (PROJECT_ROOT / "paper" / "sections" / "results.tex").read_text(encoding="utf-8"):
        raise RuntimeError("LaTeX table or note does not visibly distinguish extrapolation")
    if "\\newcommand{\\MeasuredCryptoCells}{24}" not in macros or "\\newcommand{\\ExtrapolatedCryptoCells}{6}" not in macros:
        raise RuntimeError("generated LaTeX macros disagree with fast cutoff")
    checks.append(("LaTeX numerical macros and M/E table note", "PASS"))

    manuscript = "\n".join(
        path.read_text(encoding="utf-8")
        for path in [PROJECT_ROOT / "paper" / "main.tex", *sorted((PROJECT_ROOT / "paper" / "sections").glob("*.tex"))]
    )
    forbidden = (
        "30 个单元均完成实测", "全部配置重复运行 25 次", "以下数据全部来自正式实验",
        "实验严格验证了最大规模运行时间", "矩阵每个点来自 20 次实测",
    )
    found = [phrase for phrase in forbidden if phrase in manuscript]
    if found:
        raise RuntimeError(f"manuscript overclaims incomplete crypto measurements: {found}")
    required_phrases = ("外推", "预测区间", "不作为完整实测结果", "两服务器", "非串通")
    missing_phrases = [phrase for phrase in required_phrases if phrase not in manuscript]
    if missing_phrases:
        raise RuntimeError(f"manuscript is missing required limitations: {missing_phrases}")
    checks.append(("Manuscript provenance wording and threat-model limits", "PASS"))

    pdf_path = PROJECT_ROOT / "paper" / "main.pdf"
    if args.require_pdf:
        if not pdf_path.exists() or pdf_path.stat().st_size < 100_000 or pdf_path.read_bytes()[:4] != b"%PDF":
            raise RuntimeError(f"paper PDF is missing or invalid: {pdf_path}")
        checks.append(("paper/main.pdf header and size", "PASS"))

    audit_lines = [
        "# FAST_REPORT_AUDIT",
        "",
        f"- Generated at (UTC): `{utc_now()}`",
        f"- Complete measured Paillier cells: **{len(cell_files)}/30**",
        "- Extrapolated Paillier cells: **6/30**",
        "- Measured benchmark failures: **0**",
        "",
        "| Check | Status |",
        "|---|---|",
        *(f"| {label} | {status} |" for label, status in checks),
        "",
        "## Conclusion",
        "",
        "Fast course-report audit passed. Measured and extrapolated cryptographic values remain explicitly separated; no missing cell is represented as a completed formal measurement.",
        "",
    ]
    write_text_exclusive(fast_root / "FAST_REPORT_AUDIT.md", "\n".join(audit_lines))
    print(f"status=success checks={len(checks)} measured_cells=24 extrapolated_cells=6 failures=0")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
