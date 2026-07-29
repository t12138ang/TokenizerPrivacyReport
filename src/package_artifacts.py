"""Create deterministic final paper, source, reproducibility, CSV, and figure bundles."""

from __future__ import annotations

import argparse
import os
import zipfile
from pathlib import Path
from typing import Iterable

from src.utils.run_metadata import (
    PROJECT_ROOT,
    environment_metadata,
    sha256_file,
    strict_json_load,
    utc_now,
    write_json_exclusive,
    write_text_exclusive,
)


ZIP_TIMESTAMP = (2026, 7, 26, 0, 0, 0)


def unique_files(paths: Iterable[Path]) -> list[Path]:
    result = []
    seen = set()
    for path in paths:
        resolved = path.resolve()
        if not path.is_file() or resolved in seen:
            continue
        if PROJECT_ROOT not in resolved.parents:
            raise RuntimeError(f"bundle input escaped project root: {resolved}")
        seen.add(resolved)
        result.append(resolved)
    return sorted(result, key=lambda value: value.relative_to(PROJECT_ROOT).as_posix())


def make_zip(path: Path, files: Iterable[Path]) -> dict[str, object]:
    if path.exists():
        raise FileExistsError(f"refusing to overwrite final artifact: {path}")
    selected = unique_files(files)
    if not selected:
        raise RuntimeError(f"bundle would be empty: {path}")
    partial = path.with_suffix(path.suffix + ".partial")
    if partial.exists():
        raise FileExistsError(f"partial artifact requires audit: {partial}")
    with zipfile.ZipFile(partial, "x", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for source in selected:
            relative = source.relative_to(PROJECT_ROOT).as_posix()
            info = zipfile.ZipInfo(relative, ZIP_TIMESTAMP)
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o644 << 16
            archive.writestr(info, source.read_bytes())
    try:
        os.link(partial, path)
    except BaseException:
        raise
    else:
        partial.unlink()
    return {"path": path, "file_count": len(selected), "bytes": path.stat().st_size, "sha256": sha256_file(path)}


def glob_files(pattern: str) -> list[Path]:
    return [path for path in PROJECT_ROOT.glob(pattern) if path.is_file()]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--fast-report", action="store_true")
    args = parser.parse_args()
    output = args.output_dir.resolve()
    if output != (PROJECT_ROOT / "artifacts").resolve():
        raise RuntimeError("final artifacts must be written to the project artifacts directory")
    output.mkdir(parents=True, exist_ok=True)
    pdf = output / "Tokenizer_Privacy_Course_Report_Draft.pdf"
    if not pdf.is_file() or pdf.read_bytes()[:4] != b"%PDF":
        raise RuntimeError("paper PDF must exist before packaging")
    if args.fast_report:
        final_audit = PROJECT_ROOT / "results" / "final" / "report_fast" / "FAST_REPORT_AUDIT.md"
        if not final_audit.is_file() or "Fast course-report audit passed." not in final_audit.read_text(encoding="utf-8"):
            raise RuntimeError("successful fast-report audit is required before packaging")
        package_manifest = PROJECT_ROOT / "results" / "final" / "report_fast" / "package_manifest.json"
    else:
        final_audit = PROJECT_ROOT / "results" / "final" / "audit" / "final_audit.json"
        if not final_audit.is_file():
            raise RuntimeError("successful final audit is required before packaging")
        final_audit_payload = strict_json_load(final_audit)
        if final_audit_payload.get("status") != "success":
            raise RuntimeError("final audit manifest is not successful")
        package_manifest = PROJECT_ROOT / "results" / "final" / "audit" / "package_manifest.json"
    if package_manifest.exists():
        prior = strict_json_load(package_manifest)
        if prior.get("status") != "success":
            raise RuntimeError("existing package manifest is not successful")
        for item in prior.get("artifacts", []):
            path = PROJECT_ROOT / item["path"]
            if not path.is_file() or path.stat().st_size != int(item["bytes"]) or sha256_file(path) != item["sha256"]:
                raise RuntimeError(f"existing packaged artifact failed verification: {path}")
        checksum = PROJECT_ROOT / prior["sha256sums"]
        expected_checksum = "".join(
            f"{item['sha256']}  {Path(item['path']).name}\n" for item in prior["artifacts"]
        )
        if (
            not checksum.is_file()
            or checksum.read_text(encoding="ascii") != expected_checksum
            or prior.get("sha256sums_sha256") != sha256_file(checksum)
            or prior.get("audit_source_sha256", prior.get("final_audit_sha256")) != sha256_file(final_audit)
        ):
            raise RuntimeError("existing package checksum/audit provenance is invalid")
        print(f"status=success verified_existing=true artifacts={len(prior['artifacts'])} checksum={checksum}")
        return 0

    paper_files = [
        path for path in (PROJECT_ROOT / "paper").rglob("*")
        if path.is_file() and "build" not in path.relative_to(PROJECT_ROOT / "paper").parts
    ]
    source_bundle = make_zip(output / "Tokenizer_Privacy_Course_Report_Source.zip", paper_files)

    csv_files = glob_files("results/final/tables/*.csv") + glob_files("results/final/figures/source_data/*.csv")
    csv_files += [PROJECT_ROOT / "docs" / "LITERATURE_AUDIT.csv", PROJECT_ROOT / "results" / "final" / "result_registry.json"]
    if args.fast_report:
        csv_files += glob_files("results/final/report_fast/*.csv") + glob_files("results/final/report_fast/*.json")
    csv_bundle = make_zip(output / "Final_Results_csv_bundle.zip", csv_files)

    figure_files = glob_files("paper/figures/*.pdf") + glob_files("paper/figures/*.png")
    figure_files += [PROJECT_ROOT / "results" / "final" / "figures" / "figure_manifest.json"]
    figure_bundle = make_zip(output / "Final_Figures.zip", figure_files)

    reproduction_files: list[Path] = []
    for directory in ("src", "configs", "scripts", "tests", "docs"):
        reproduction_files.extend(path for path in (PROJECT_ROOT / directory).rglob("*") if path.is_file() and "__pycache__" not in path.parts)
    reproduction_files.extend([
        PROJECT_ROOT / "README.md", PROJECT_ROOT / "requirements.txt", PROJECT_ROOT / "environment.yml",
        PROJECT_ROOT / ".gitmodules", PROJECT_ROOT / ".gitignore",
    ])
    if args.fast_report:
        reproduction_files.append(PROJECT_ROOT / "README_REPORT.md")
    reproduction_files.extend(path for path in (PROJECT_ROOT / "data" / "final" / "manifests").rglob("*.json"))
    reproduction_files.extend([
        PROJECT_ROOT / "data" / "final" / "validation.json",
        PROJECT_ROOT / "data" / "final" / "corpus" / "collection_stats.json",
        PROJECT_ROOT / "data" / "final" / "corpus" / "site_index.json",
    ])
    reproduction_files.extend(glob_files("results/final/tables/*"))
    reproduction_files.extend(glob_files("results/final/figures/source_data/*.csv"))
    reproduction_files.extend(glob_files("results/final/figures/*.json"))
    reproduction_files.extend(glob_files("results/final/audit/*.json"))
    if args.fast_report:
        reproduction_files.extend(glob_files("results/final/report_fast/*"))
    reproduction_files.extend(glob_files("results/final/crypto/*.json"))
    reproduction_files.extend(glob_files("results/final/crypto/*.csv"))
    reproduction_files.extend(glob_files("results/final/crypto/cells/*.json"))
    reproduction_files.extend(glob_files("results/final/crypto/full_tokenizer_2048/*.json"))
    reproduction_files.extend(glob_files("results/final/runs/attacks/**/*.json"))
    reproduction_files.extend(glob_files("results/final/runs/shadow_sensitivity/**/*.json"))
    reproduction_files.extend(glob_files("results/final/defenses/**/*.json"))
    reproduction_files.extend(glob_files("results/final/downstream/**/*.json"))
    reproduction_files.extend([
        PROJECT_ROOT / "results" / "final" / "attack_summary.json",
        PROJECT_ROOT / "results" / "final" / "attack_summary.csv",
        PROJECT_ROOT / "results" / "final" / "result_registry.json",
    ])
    reproduction_bundle = make_zip(output / "Tokenizer_Privacy_Reproducibility_Bundle.zip", reproduction_files)

    artifacts = [
        {"path": pdf, "file_count": 1, "bytes": pdf.stat().st_size, "sha256": sha256_file(pdf)},
        source_bundle, reproduction_bundle, csv_bundle, figure_bundle,
    ]
    checksum_path = output / "SHA256SUMS"
    if checksum_path.exists():
        raise FileExistsError(f"refusing to overwrite checksum file: {checksum_path}")
    write_text_exclusive(
        checksum_path,
        "".join(f"{item['sha256']}  {Path(item['path']).name}\n" for item in artifacts),
    )
    write_json_exclusive(package_manifest, {
        "schema_version": 1, "status": "success", "created_at_utc": utc_now(),
        "artifacts": [
            {**item, "path": str(Path(item["path"]).relative_to(PROJECT_ROOT)).replace("\\", "/")}
            for item in artifacts
        ],
        "sha256sums": "artifacts/SHA256SUMS",
        "sha256sums_sha256": sha256_file(checksum_path),
        "audit_mode": "fast_report" if args.fast_report else "full",
        "audit_source": str(final_audit.relative_to(PROJECT_ROOT)).replace("\\", "/"),
        "audit_source_sha256": sha256_file(final_audit),
        "environment": environment_metadata(),
    })
    print(f"status=success artifacts={len(artifacts)} checksum={checksum_path}")
    for item in artifacts:
        print(f"{Path(item['path']).name} bytes={item['bytes']} sha256={item['sha256']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
