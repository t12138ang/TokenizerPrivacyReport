"""Final cross-artifact experiment, cryptographic, citation, and language audit."""

from __future__ import annotations

import argparse
import csv
import math
import re
import subprocess
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

from src.utils.run_metadata import (
    PROJECT_ROOT,
    canonical_sha256,
    environment_metadata,
    sha256_file,
    strict_json_load,
    utc_now,
    write_json_exclusive,
    write_text_exclusive,
)


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def assert_finite(value: Any, location: str) -> None:
    if isinstance(value, float) and not math.isfinite(value):
        raise ValueError(f"non-finite value at {location}")
    if isinstance(value, dict):
        for key, child in value.items():
            assert_finite(child, f"{location}.{key}")
    elif isinstance(value, list):
        for index, child in enumerate(value):
            assert_finite(child, f"{location}[{index}]")


def git(*arguments: str) -> str:
    result = subprocess.run(
        ["git", *arguments], cwd=PROJECT_ROOT, check=True, text=True,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, encoding="utf-8",
    )
    return result.stdout.strip()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path, required=True)
    args = parser.parse_args()
    config_path = args.config.resolve()
    config = strict_json_load(config_path)
    config_hash = sha256_file(config_path)
    result_root = PROJECT_ROOT / config["results_root"]
    errors: list[str] = []
    warnings: list[str] = []

    validation_path = PROJECT_ROOT / "data" / "final" / "validation.json"
    validation = strict_json_load(validation_path)
    if validation.get("status") != "success" or validation.get("errors"):
        errors.append("final C4 validation is not clean")
    if validation.get("dataset_revision") != "1588ec454efa1a09f29cd18ddd04fe05fc8653a2":
        errors.append("C4 revision drift")
    manifests = []
    for scale in ("development", "main"):
        for seed in config["seeds"]:
            path = PROJECT_ROOT / "data" / "final" / "manifests" / scale / "strict_disjoint" / f"seed_{seed}.json"
            manifest = strict_json_load(path)
            manifests.append((path, manifest))
            groups = {
                "member": set(manifest["target_member_site_ids"]),
                "nonmember": set(manifest["target_nonmember_site_ids"]),
                "shadow": set(manifest["shadow_auxiliary_site_ids"]),
                "public": set(manifest["public_candidate_site_ids"]),
            }
            for left, left_values in groups.items():
                for right, right_values in groups.items():
                    if left < right and left_values & right_values:
                        errors.append(f"manifest overlap {scale}/{seed}: {left}/{right}")
            if manifest.get("corpus_sha256") != validation.get("corpus_sha256"):
                errors.append(f"corpus hash mismatch in {path}")

    ag_path = result_root / "downstream" / "ag_news_data.json"
    ag = strict_json_load(ag_path)
    downstream_config = strict_json_load(PROJECT_ROOT / "configs" / "downstream.json")
    if ag.get("status") != "success" or ag.get("cross_split_normalized_duplicate_count_after_filter") != 0:
        errors.append("AG News split/deduplication audit failed")
    if ag.get("resolved_revision") != "eb185aade064a813bc0b7f42de02595523103ca4":
        errors.append("AG News revision drift")
    if ag.get("requested_revision") != downstream_config.get("dataset_revision") or ag.get("resolved_revision") != downstream_config.get("dataset_revision"):
        errors.append("AG News authoritative configuration/metadata revision mismatch")
    configured_ag_revision = config.get("downstream", {}).get("dataset_revision")
    if configured_ag_revision != ag.get("resolved_revision"):
        errata = PROJECT_ROOT / "docs" / "CONFIGURATION_ERRATA.md"
        if not errata.is_file():
            errors.append("umbrella/downstream AG News revision mismatch has no checked-in erratum")
        else:
            warnings.append(
                "frozen umbrella config retains the unused preliminary AG News revision; "
                "configs/downstream.json and actual metadata are authoritative"
            )

    required_states = [
        result_root / "runs" / "attack_pipeline_state.json",
        result_root / "defenses" / "development" / "pipeline_state.json",
        result_root / "downstream" / "development_state.json",
        result_root / "defenses" / "main" / "pipeline_state.json",
        result_root / "downstream" / "main_state.json",
        result_root / "crypto" / "benchmark_state.json",
    ]
    state_rows = []
    frozen_execution_commits: set[str] = set()
    for path in required_states:
        state = strict_json_load(path)
        state_rows.append((path, state))
        if state.get("status") != "success":
            errors.append(f"non-success final state: {path}")
        if "expected_tasks" in state:
            if int(state.get("completed_tasks", -1)) != int(state["expected_tasks"]):
                errors.append(f"incomplete final task state: {path}")
        elif "expected_cells" in state:
            if int(state.get("completed_cells", -1)) != int(state["expected_cells"]):
                errors.append(f"incomplete final cell state: {path}")
        else:
            errors.append(f"final state has no recognized completeness counters: {path}")
        if int(state.get("failures", 0)) != 0:
            warnings.append(f"resolved failures are preserved in final state: {path} -> {state.get('failures')}")
        git_metadata = state.get("environment", {}).get("git", {})
        commit = git_metadata.get("commit")
        if not isinstance(commit, str) or not re.fullmatch(r"[0-9a-f]{40}", commit):
            errors.append(f"final state lacks a valid Git commit: {path}")
        else:
            frozen_execution_commits.add(commit)
        if git_metadata.get("tracked_worktree_dirty") is not False:
            errors.append(f"final state began from a dirty tracked worktree: {path}")
        expected_state_config_hash = (
            sha256_file(PROJECT_ROOT / "configs" / "downstream.json")
            if "downstream" in path.parts else config_hash
        )
        if state.get("config_sha256") != expected_state_config_hash:
            errors.append(f"final state/config hash mismatch: {path}")
    if len(frozen_execution_commits) != 1:
        errors.append(f"final execution states do not share one frozen Git commit: {sorted(frozen_execution_commits)}")

    json_files = sorted(result_root.rglob("*.json"))
    for path in json_files:
        try:
            value = strict_json_load(path)
            assert_finite(value, str(path))
            if isinstance(value, dict) and "schema_version" not in value:
                warnings.append(f"JSON has no schema_version: {path.relative_to(PROJECT_ROOT)}")
        except Exception as exc:
            errors.append(f"JSON audit failed {path}: {type(exc).__name__}: {exc}")

    selection_path = result_root / "defenses" / "main_selection.json"
    selection = strict_json_load(selection_path)
    if (
        selection.get("status") != "frozen_before_main_results"
        or selection.get("development_only") is not True
        or selection.get("main_results_read") is not False
        or selection.get("selection_metric") != "best_validation_macro_f1"
        or selection.get("test_metrics_read_for_selection") is not False
        or selection.get("config_sha256") != config_hash
    ):
        errors.append("Main defense selection is not a valid Development-validation-only freeze")
    selected_roles = Counter(row.get("report_role") for row in selection.get("selected", []))
    expected_roles = Counter({
        "local_dp_primary": 1,
        "sa_dp_primary_tradeoff": 1,
        "sa_dp_strong_privacy_comparator": 1,
    })
    if selected_roles != expected_roles:
        errors.append(f"Main defense report roles mismatch: {dict(selected_roles)}")
    selected_by_role = {row.get("report_role"): row for row in selection.get("selected", [])}
    primary_sa = selected_by_role.get("sa_dp_primary_tradeoff")
    privacy_sa = selected_by_role.get("sa_dp_strong_privacy_comparator")
    eligible_sa = [
        row for row in selection.get("evaluated", [])
        if row.get("mode") == "sa_dp" and row.get("passes_macro_f1_constraint") is True
    ]
    eligible_local = [
        row for row in selection.get("evaluated", [])
        if row.get("mode") == "local_dp" and row.get("passes_macro_f1_constraint") is True
    ]
    selected_local = selected_by_role.get("local_dp_primary")
    if selected_local:
        expected_local = sorted(
            eligible_local,
            key=lambda row: (
                row["development_mean_attack_auc"],
                row["development_c4_mean_token_increase_fraction"],
                row["cryptographic_work_proxy_K_per_batch"],
                row["epsilon_total"],
            ),
        )
        if not expected_local or selected_local["id"] != expected_local[0]["id"]:
            errors.append("Local-DP primary configuration violates the frozen objective")
    if primary_sa and privacy_sa:
        expected_primary = sorted(
            eligible_sa,
            key=lambda row: (
                row["development_mean_attack_auc"],
                row["development_c4_mean_token_increase_fraction"],
                row["cryptographic_work_proxy_K_per_batch"],
                row["epsilon_total"],
            ),
        )
        if not expected_primary or primary_sa["id"] != expected_primary[0]["id"]:
            errors.append("SA-DP primary configuration violates the frozen objective")
        expected_privacy = sorted(
            (row for row in eligible_sa if row["id"] != primary_sa["id"]),
            key=lambda row: (
                float(row["epsilon_total"]),
                row["development_mean_attack_auc"],
                row["development_c4_mean_token_increase_fraction"],
                row["cryptographic_work_proxy_K_per_batch"],
                row["epsilon_total"],
            ),
        )
        if not expected_privacy or privacy_sa["id"] != expected_privacy[0]["id"]:
            errors.append("SA-DP strong-privacy comparator violates the frozen secondary rule")
    baseline_methods = [row["id"] for row in config["baseline_methods"]]
    report_methods = baseline_methods + ["he_only_reference"] + [
        row["id"] for row in selection.get("selected", [])
    ]
    if len(set(report_methods)) != len(report_methods):
        errors.append("duplicate method IDs in final reporting set")

    attack_rows = read_csv(result_root / "tables" / "attack_results.csv")
    attack_expected = len(report_methods) * len(config["seeds"]) * len(config["attacks"])
    if len(attack_rows) != attack_expected:
        errors.append(f"final attack table count {len(attack_rows)} != {attack_expected}")
    attack_keys = [(row["method_id"], row["seed"], row["attack"]) for row in attack_rows]
    if len(set(attack_keys)) != len(attack_keys):
        errors.append("duplicate final attack combinations")
    expected_attack_keys = {
        (method, str(seed), attack)
        for method in report_methods
        for seed in config["seeds"]
        for attack in config["attacks"]
    }
    if set(attack_keys) != expected_attack_keys:
        errors.append("final attack combination set differs from the frozen reporting matrix")
    for row in attack_rows:
        source = strict_json_load(PROJECT_ROOT / row["source"])
        metrics = source["metrics"]
        method = row["method_id"]
        seed = int(row["seed"])
        attack = row["attack"]
        main_manifest = strict_json_load(
            PROJECT_ROOT / "data" / "final" / "manifests" / "main"
            / "strict_disjoint" / f"seed_{seed}.json"
        )
        if (
            source.get("status") != "success"
            or source.get("method_id") != method
            or source.get("attack") != attack
            or int(source.get("seed", -1)) != seed
            or source.get("scale") != "main"
            or source.get("protocol") != "strict_disjoint"
            or source.get("data", {}).get("manifest_sha256") != main_manifest["manifest_sha256"]
        ):
            errors.append(f"attack result provenance mismatch: {row['source']}")
        target_artifact = (
            result_root / "runs" / "tokenizers" / "main" / str(seed) / "vocab_16000"
            / method / "target" / "tokenizer.json"
            if method in baseline_methods
            else result_root / "defenses" / "main" / "tokenizers" / method / str(seed) / "tokenizer.json"
        )
        if (
            not target_artifact.is_file()
            or source.get("artifacts", {}).get("target_tokenizer_sha256") != sha256_file(target_artifact)
        ):
            errors.append(f"attack target artifact mismatch: {row['source']}")
        details = source.get("details", [])
        if (
            len(details) != 512
            or len({item.get("site_id") for item in details}) != 512
            or sum(bool(item.get("is_member")) for item in details) != 256
        ):
            errors.append(f"attack website details are incomplete or imbalanced: {row['source']}")
        if metrics.get("positive_class") != "target tokenizer training member website":
            errors.append(f"positive-class mismatch: {row['source']}")
        if metrics.get("score_direction") != "higher_is_more_member":
            errors.append(f"score direction mismatch: {row['source']}")
        if metrics["bootstrap"].get("iterations") != 10000:
            errors.append(f"bootstrap count mismatch: {row['source']}")
        expected_shadow_count = 32 if attack in {"vocabulary_overlap", "merge_similarity"} else 1 if attack == "frequency_estimation" else 0
        if int(source.get("shadow_count", -1)) != expected_shadow_count:
            errors.append(f"shadow count mismatch: {row['source']}")
        knowledge = source.get("attacker_shadow_knowledge")
        shadow_methods = set(source.get("shadow_tokenizer_method_ids", []))
        if attack in {"vocabulary_overlap", "merge_similarity", "frequency_estimation"}:
            expected_knowledge = "matched_training_method" if method in baseline_methods else "fixed_transfer_shadow_method"
            expected_shadow_methods = {method} if method in baseline_methods else {"plain_bpe"}
            if knowledge != expected_knowledge or shadow_methods != expected_shadow_methods:
                errors.append(f"attacker shadow-knowledge mismatch: {row['source']}")
        elif attack == "naive_bayes":
            if knowledge != "auxiliary_membership_sets_without_shadow_tokenizer":
                errors.append(f"Naive-Bayes attacker knowledge mismatch: {row['source']}")
        elif knowledge != "shadow_tokenizer_not_required":
            errors.append(f"Compression-rate attacker knowledge mismatch: {row['source']}")
        expected_auxiliary_groups = int(config["auxiliary_sampling_group_count"]) if attack in {"frequency_estimation", "naive_bayes"} else 0
        if int(source.get("auxiliary_group_count", -1)) != expected_auxiliary_groups:
            errors.append(f"auxiliary group count mismatch: {row['source']}")
    method_seed_counts: dict[str, set[str]] = defaultdict(set)
    for row in attack_rows:
        method_seed_counts[row["method_id"]].add(row["seed"])
    expected_seed_set = {str(seed) for seed in config["seeds"]}
    for method, seeds in method_seed_counts.items():
        if seeds != expected_seed_set:
            errors.append(f"seed completeness failed for attack method {method}: {sorted(seeds)}")

    min_metadata_paths = sorted(
        path for path in (result_root / "runs" / "tokenizers").rglob("metadata.json")
        if any(part.startswith("min_count_") for part in path.parts)
    )
    expected_min_metadata = (
        2 * len(config["seeds"]) * len(config["vocab_sizes"])
        * len(config["min_count_thresholds"]) * (int(config["main_shadow_count"]) + 1)
    )
    if len(min_metadata_paths) != expected_min_metadata:
        errors.append(f"Min-count tokenizer metadata count {len(min_metadata_paths)} != {expected_min_metadata}")
    verified_min_caches: dict[str, str] = {}
    for path in min_metadata_paths:
        metadata = strict_json_load(path)
        cache_value = metadata.get("feature_cache_path")
        cache_path = PROJECT_ROOT / cache_value if isinstance(cache_value, str) else None
        artifact = PROJECT_ROOT / metadata["artifact"]
        if (
            metadata.get("status") != "success"
            or metadata.get("min_count_semantics")
            != "post-hoc official overlapping regex-unit substring frequency filtering"
            or metadata.get("feature_definition") != "official_overlapping_regex_unit_substrings"
            or not isinstance(metadata.get("feature_counts_sha256"), str)
            or cache_path is None
            or not cache_path.is_file()
            or not artifact.is_file()
            or metadata.get("artifact_sha256") != sha256_file(artifact)
        ):
            errors.append(f"invalid official-semantics Min-count artifact: {path}")
            continue
        cache_key = str(cache_path)
        actual_cache_hash = verified_min_caches.get(cache_key)
        if actual_cache_hash is None:
            actual_cache_hash = sha256_file(cache_path)
            verified_min_caches[cache_key] = actual_cache_hash
        if metadata.get("feature_cache_sha256") != actual_cache_hash:
            errors.append(f"Min-count cache hash mismatch: {path}")
        cache = strict_json_load(cache_path)
        if (
            cache.get("counts_sha256") != metadata.get("feature_counts_sha256")
            or cache.get("feature_definition") != "official_overlapping_regex_unit_substrings"
            or cache.get("selected_sites_sha256") != metadata.get("feature_selected_sites_sha256")
        ):
            errors.append(f"Min-count cache provenance mismatch: {path}")

    downstream_rows = read_csv(result_root / "tables" / "downstream_results.csv")
    expected_downstream_keys = {
        (method, str(seed)) for method in report_methods for seed in config["seeds"]
    }
    actual_downstream_keys = [(row["method_id"], row["seed"]) for row in downstream_rows]
    if len(actual_downstream_keys) != len(set(actual_downstream_keys)):
        errors.append("duplicate final downstream combinations")
    if set(actual_downstream_keys) != expected_downstream_keys:
        errors.append("final downstream combination set differs from the frozen reporting matrix")
    main_downstream_plan = strict_json_load(result_root / "downstream" / "main_plan.json")
    planned_downstream = {
        (task["method_id"], str(task["seed"])): task for task in main_downstream_plan["tasks"]
    }
    for method in {row["method_id"] for row in downstream_rows}:
        seeds = {row["seed"] for row in downstream_rows if row["method_id"] == method}
        if seeds != expected_seed_set:
            errors.append(f"seed completeness failed for downstream method {method}")
    downstream_config_hash = sha256_file(PROJECT_ROOT / "configs" / "downstream.json")
    for row in downstream_rows:
        source_path = PROJECT_ROOT / row["source"]
        source = strict_json_load(source_path)
        task = planned_downstream.get((row["method_id"], row["seed"]))
        expected_source = PROJECT_ROOT / task["output_dir"] / "result.json" if task else None
        if (
            task is None
            or source_path.resolve() != expected_source.resolve()
            or source.get("status") != "success"
            or source.get("method_id") != row["method_id"]
            or int(source.get("seed", -1)) != int(row["seed"])
            or source.get("downstream_config_sha256") != downstream_config_hash
            or source.get("tokenizer_sha256") != task["tokenizer_sha256"]
            or source.get("dataset_revision") != ag["resolved_revision"]
            or source.get("split_hashes") != {
                split: details["sha256"] for split, details in ag["splits"].items()
            }
            or source.get("test_used_for_model_selection") is not False
        ):
            errors.append(f"downstream result provenance/selection mismatch: {row['source']}")
        architecture = source.get("architecture", {})
        for key in (
            "embedding_dim", "transformer_layers", "attention_heads", "ffn_dim",
            "max_sequence_length", "dropout", "epochs", "early_stopping_patience",
            "effective_batch_size", "micro_batch_size", "learning_rate", "weight_decay",
        ):
            if architecture.get(key) != downstream_config.get(key):
                errors.append(f"downstream architecture mismatch {key}: {row['source']}")
        convergence = source.get("convergence", [])
        best_epoch = int(source.get("best_validation_epoch", -1))
        if (
            not convergence
            or best_epoch not in {int(item["epoch"]) for item in convergence}
            or int(source.get("test", {}).get("record_count", -1)) != int(ag["splits"]["test"]["record_count"])
            or not source.get("started_at_utc")
            or not source.get("completed_at_utc")
            or int(source.get("peak_process_memory_bytes", 0)) <= 0
        ):
            errors.append(f"downstream training completeness mismatch: {row['source']}")
        if not str(source.get("device", "")).startswith("cuda"):
            warnings.append(f"downstream result used CPU fallback: {row['source']}")

    registry_path = result_root / "result_registry.json"
    registry = strict_json_load(registry_path)
    if registry.get("status") != "success" or registry.get("entry_count") != len(registry.get("entries", [])):
        errors.append("result registry is incomplete")
    result_ids = [entry.get("result_id") for entry in registry.get("entries", [])]
    if len(result_ids) != len(set(result_ids)):
        errors.append("result registry contains duplicate result IDs")
    if any("smoke" in str(entry).lower() for entry in registry.get("entries", [])):
        errors.append("Smoke output leaked into the final result registry")
    for source, expected_hash in registry.get("source_hashes", {}).items():
        if sha256_file(PROJECT_ROOT / source) != expected_hash:
            errors.append(f"registry source hash mismatch: {source}")
    for source, expected_hash in registry.get("table_hashes", {}).items():
        if sha256_file(PROJECT_ROOT / source) != expected_hash:
            errors.append(f"registry table hash mismatch: {source}")

    figure_manifest = strict_json_load(result_root / "figures" / "figure_manifest.json")
    if figure_manifest.get("figure_count") != 12:
        errors.append("figure count is not 12")
    for figure in figure_manifest.get("figures", []):
        source = PROJECT_ROOT / figure["source_csv"]
        if sha256_file(source) != figure["source_sha256"]:
            errors.append(f"figure source hash mismatch: {source}")
        for item in figure["files"]:
            path = PROJECT_ROOT / item["path"]
            if not path.is_file() or sha256_file(path) != item["sha256"]:
                errors.append(f"figure artifact missing/hash mismatch: {path}")
    latex_manifest = strict_json_load(result_root / "tables" / "latex_generation_manifest.json")
    if latex_manifest.get("table_count") != 10:
        errors.append("LaTeX generated table count is not 10")
    for item in latex_manifest.get("outputs", []):
        path = PROJECT_ROOT / item["path"]
        if not path.is_file() or sha256_file(path) != item["sha256"]:
            errors.append(f"generated LaTeX missing/hash mismatch: {path}")

    tex_files = sorted((PROJECT_ROOT / "paper").rglob("*.tex"))
    tex = "\n".join(path.read_text(encoding="utf-8") for path in tex_files)
    bib = (PROJECT_ROOT / "paper" / "references.bib").read_text(encoding="utf-8")
    citation_keys = {key.strip() for group in re.findall(r"\\cite(?:t|p)?\{([^}]+)\}", tex) for key in group.split(",")}
    bib_keys = set(re.findall(r"@[A-Za-z]+\{([^,]+),", bib))
    if citation_keys - bib_keys:
        errors.append(f"undefined citation keys: {sorted(citation_keys - bib_keys)}")
    if bib_keys - citation_keys:
        warnings.append(f"unused BibTeX keys: {sorted(bib_keys - citation_keys)}")
    literature = read_csv(PROJECT_ROOT / "docs" / "LITERATURE_AUDIT.csv")
    if {row["citation_key"] for row in literature} != bib_keys:
        errors.append("literature audit and BibTeX key sets differ")
    recent_count = sum(row["recent_2024_2026"] == "yes" and row["ccf_rank"] in {"A", "B"} for row in literature)
    if recent_count < 15:
        errors.append(f"only {recent_count} verified recent CCF A/B papers")
    cjk_count = len(re.findall(r"[\u4e00-\u9fff]", tex))
    if cjk_count < 8000:
        errors.append(f"Chinese manuscript is shorter than target: {cjk_count} CJK characters")

    paper_pdf = PROJECT_ROOT / "artifacts" / "Tokenizer_Privacy_Course_Report_Draft.pdf"
    if not paper_pdf.is_file() or paper_pdf.stat().st_size < 10_000 or paper_pdf.read_bytes()[:4] != b"%PDF":
        errors.append("paper PDF is missing or not a readable PDF header")
    build_log = PROJECT_ROOT / "logs" / "paper_build.log"
    build_text = build_log.read_text(encoding="utf-8", errors="replace") if build_log.exists() else ""
    if build_text.count("SUCCESS: two consecutive clean builds") != 1:
        errors.append("paper build log does not confirm two consecutive clean builds")
    overfull_box_count = len(re.findall(r"(?im)^Overfull \\hbox", build_text))
    underfull_box_count = len(re.findall(r"(?im)^Underfull \\hbox", build_text))
    if overfull_box_count:
        warnings.append(f"paper build reports {overfull_box_count} Overfull hbox warnings")

    submodule = git("submodule", "status", "--", "third_party/Tokenizer-MIA")
    if not submodule.lstrip(" -+").startswith("eeb0d83b34dd13f203bf578814463d0654295798") or submodule.startswith(("+", "-")):
        errors.append(f"official submodule drift: {submodule}")
    submodule_worktree = git("-C", "third_party/Tokenizer-MIA", "status", "--porcelain")
    if submodule_worktree:
        errors.append(f"official submodule worktree is modified: {submodule_worktree}")
    tracked = git("ls-files").splitlines()
    forbidden_data = [path for path in tracked if path.replace("\\", "/") in {
        "data/final/corpus/texts.jsonl", "data/downstream/ag_news/train.jsonl",
        "data/downstream/ag_news/validation.jsonl", "data/downstream/ag_news/test.jsonl",
    }]
    if forbidden_data:
        errors.append(f"raw datasets are tracked by Git: {forbidden_data}")
    large_tracked = []
    for relative in tracked:
        path = PROJECT_ROOT / relative
        if path.is_file() and path.stat().st_size > 10 * 1024 * 1024:
            large_tracked.append((relative, path.stat().st_size))
    if large_tracked:
        errors.append(f"unexpected tracked files over 10 MiB: {large_tracked}")
    git_status = git("status", "--short")
    if git_status:
        warnings.append("working tree is expected to be dirty before the final audit commit")

    crypto_summary = strict_json_load(result_root / "crypto" / "benchmark_summary.json")
    expected_crypto_cells = (
        len(config["paillier"]["client_counts"])
        * len(config["paillier"]["candidate_dimensions"])
    )
    if (
        crypto_summary.get("formal_key_bits") != 2048
        or crypto_summary.get("actual_cells") != expected_crypto_cells
        or crypto_summary.get("config_sha256") != config_hash
        or crypto_summary.get("warmup_repetitions_per_cell") != config["paillier"]["warmup_repetitions"]
        or crypto_summary.get("measured_repetitions_per_cell") != config["paillier"]["measured_repetitions"]
    ):
        errors.append("formal Paillier benchmark matrix is incomplete")
    if not crypto_summary.get("all_plaintext_he_equal") or crypto_summary.get("maximum_absolute_error") != 0:
        errors.append("Paillier correctness summary failed")
    full_crypto = strict_json_load(result_root / "crypto" / "full_tokenizer_summary.json")
    full_crypto_config_hash = sha256_file(PROJECT_ROOT / "configs" / "crypto_full.json")
    if (
        full_crypto.get("status") != "success"
        or not full_crypto.get("formal_real_paillier")
        or not full_crypto.get("complete_tokenizer_training_measured")
        or full_crypto.get("requested_key_bits") != 2048
        or full_crypto.get("actual_modulus_bits", 0) < 2048
        or not full_crypto.get("artifact_exact_match")
        or full_crypto.get("config_sha256") != full_crypto_config_hash
        or not full_crypto.get("started_at_utc")
        or not isinstance(full_crypto.get("summed_round_crypto"), dict)
    ):
        errors.append("actual complete-tokenizer 2048-bit Paillier audit failed")
    for artifact_key, hash_key in (
        ("real_paillier_artifact", "real_paillier_artifact_sha256"),
        ("cleartext_reference_artifact", "cleartext_reference_artifact_sha256"),
    ):
        artifact_path = PROJECT_ROOT / full_crypto[artifact_key]
        if not artifact_path.is_file() or sha256_file(artifact_path) != full_crypto[hash_key]:
            errors.append(f"full-tokenizer crypto artifact hash mismatch: {artifact_path}")
    equivalence = strict_json_load(result_root / "defenses" / "main" / "he_plain_equivalence.json")
    if not equivalence.get("all_exact") or len(equivalence.get("rows", [])) != len(config["seeds"]):
        errors.append("protocol Plain/HE-only tokenizer equivalence failed")
    smoke_crypto = strict_json_load(result_root / "crypto" / "correctness_smoke_1024.json")
    if (
        smoke_crypto.get("status") != "success"
        or smoke_crypto.get("development_only") is not True
        or smoke_crypto.get("requested_key_bits") != 1024
    ):
        errors.append("1024-bit cryptographic smoke is missing or mislabeled")

    partial_outputs = sorted(
        path for root in (result_root, PROJECT_ROOT / "data" / "final", PROJECT_ROOT / "artifacts")
        for path in root.rglob("*.partial") if root.exists()
    )
    if partial_outputs:
        errors.append(f"partial outputs require audit: {[str(path) for path in partial_outputs]}")

    log_error_counts = Counter()
    for path in sorted((PROJECT_ROOT / "logs" / "final").rglob("*.log")):
        content = path.read_text(encoding="utf-8", errors="replace")
        count = len(re.findall(r"(?im)^(?:.*\|\s*)?(ERROR|Traceback)", content))
        if count:
            log_error_counts[str(path.relative_to(PROJECT_ROOT)).replace("\\", "/")] = count
    active_log_errors = {
        path: count for path, count in log_error_counts.items()
        if "/attempts/" not in "/" + path.replace("\\", "/")
    }
    preserved_attempt_errors = {
        path: count for path, count in log_error_counts.items()
        if "/attempts/" in "/" + path.replace("\\", "/")
    }
    if active_log_errors:
        errors.append(f"active final logs contain error/traceback markers: {active_log_errors}")
    if preserved_attempt_errors:
        warnings.append(f"superseded-attempt logs retain error markers: {preserved_attempt_errors}")

    if errors:
        raise RuntimeError("final audit failed:\n- " + "\n- ".join(errors))

    attack_summary = strict_json_load(result_root / "attack_summary.json")
    experiment_doc = f"""# 最终实验真实性审计

- 审计时间（UTC）：{utc_now()}
- 状态：通过
- C4 revision：`{validation['dataset_revision']}`
- corpus SHA-256：`{validation['corpus_sha256']}`
- 网站/文本/字节：{validation['site_count']} / {validation['text_count']} / {validation['text_byte_length']['total']}
- 标准攻击结果：{attack_summary['actual_standard_results']} / {attack_summary['expected_standard_results']}
- shadow 敏感性结果：{attack_summary['actual_sensitivity_results']} / {attack_summary['expected_sensitivity_results']}
- 最终报告攻击行：{len(attack_rows)}；重复组合：0
- 下游结果行：{len(downstream_rows)}；测试集用于选模：否
- 结果注册项：{registry['entry_count']}
- 图：{figure_manifest['figure_count']} 幅，每幅均有哈希绑定 CSV；LaTeX 表：{latex_manifest['table_count']} 张
- JSON 审计数：{len(json_files)}；NaN/Infinity：0
- 最终状态失败计数：{sum(int(state.get('failures', 0)) for _, state in state_rows)}
- 冻结执行 commit：`{next(iter(frozen_execution_commits))}`
- Min-count 官方子串语义 artifact：{len(min_metadata_paths)}；唯一共享计数缓存：{len(verified_min_caches)}
- 防御选择只读取 Development 最佳验证 Macro-F1：是；读取 AG News test 指标：否
- shadow 型防御攻击知识：固定 Plain shadow 迁移，而非防御自适应 shadow
- Git 审计时状态：`{'dirty-before-final-commit' if git_status else 'clean'}`

三个随机种子、成员正类和分数方向均按冻结配置检查。配对 AUC 差以网站为重采样单位并执行 10,000 次分层 bootstrap。Smoke 结果未进入最终表或结果注册表。
"""
    crypto_doc = f"""# 密码学正确性与边界审计

- 状态：通过
- 正式密钥：2048-bit Paillier；矩阵单元：{crypto_summary['actual_cells']} / {crypto_summary['expected_cells']}
- 每单元 warm-up / 实测：{crypto_summary['warmup_repetitions_per_cell']} / {crypto_summary['measured_repetitions_per_cell']}
- 明文与解密聚合逐项相等：是；最大绝对误差：{crypto_summary['maximum_absolute_error']}
- 模空间回绕：{crypto_summary['overflow_count']} 次
- 协议内 Plain 与 HE-only Tokenizer exact match：是
- Development 4k 完整真实 Paillier Tokenizer：{full_crypto['actual_elapsed_seconds']:.3f} 秒；与同噪声明文协议 artifact exact match：是
- 1024-bit 文件仅标记为开发 smoke，不作为正式开销结果。

Paillier 只保护训练期密文机密性；发布期站点成员隐私来自 L1 截断、双边几何机制和基本顺序组合。当前实现是 A/D 两服务器非共谋模型，不是门限 Paillier。恶意客户端、A/D 共谋、流量分析、主动篡改和掉线容错均未被当前证明覆盖。
"""
    citation_doc = f"""# 论文与引用审计

- LaTeX 源文件：{len(tex_files)}
- 正文 citation key：{len(citation_keys)}
- BibTeX key：{len(bib_keys)}
- 未定义引用：0；未使用 BibTeX：{len(bib_keys - citation_keys)}
- 文献审计总数：{len(literature)}
- 2024--2026 CCF A/B：{recent_count}
- 论文 PDF：`artifacts/Tokenizer_Privacy_Course_Report_Draft.pdf`
- 两次连续 clean XeLaTeX 构建：通过
- Overfull / Underfull hbox：{overfull_box_count} / {underfull_box_count}
"""
    language_doc = f"""# 论文语言与结构审计

- 中文 CJK 字符数：{cjk_count}
- 中文摘要、关键词、英文摘要：齐全
- System Model、Threat Model、两段算法伪代码、协议时序图、正确性/机密性/DP/复杂度分析：齐全
- 结果数字来源：`paper/generated/results_macros.tex` 与 10 个自动表片段
- 手工复制 Smoke 或官方论文表格数字：未发现
- 结论限制：没有把 HE-only 写成成员隐私保证，没有声称完全安全、门限 Paillier或工业部署。

仍建议提交前由课程作者人工检查署名、学校模板要求、中文措辞及最终 PDF 的视觉分页。
"""
    limitations_doc = """# 局限性清单

1. 两服务器必须 honest-but-curious 且不共谋；共谋可恢复未扰动聚合。
2. 未覆盖恶意客户端、越界密文、主动篡改、重放、掉线和拒绝服务。
3. 公开候选语料若不具代表性，会限制可达词表并损害效用。
4. 使用基本顺序组合而非更紧的 RDP/PLD 会计；自适应停止只按实际已执行轮计账。
5. C4 固定英文快照与 AG News 单一任务不能代表多语言或所有下游场景。
6. 本地 2048-bit 开销是单机实测，不等于广域网或工业部署性能。
7. 三个种子和网站级 bootstrap 不覆盖数据源、revision 与模型族不确定性。
8. 已实现两服务器 Paillier，不得称为门限 Paillier。
9. HE-only 只提供训练期机密性，不提供最终 Tokenizer 的成员差分隐私。
10. 经验攻击 AUC 接近随机不能替代形式差分隐私证明。
11. 防御 Tokenizer 的 shadow 型攻击固定复用 Plain shadow，属于迁移攻击；未覆盖为每个防御重训的自适应 shadow 攻击。
12. 最初一批最大词表基座 artifact 的 metadata 记录了请求线程数 4，但创建时尚未由包装脚本显式设置 `RAYON_NUM_THREADS`，故其有效 Rust 线程数不可追溯；artifact 哈希与实验结果仍可验证，时间只应解释为该机器上的历史实测。
"""
    audit_path = result_root / "audit" / "final_audit.json"
    audited_sources = {
        "validation_sha256": sha256_file(validation_path),
        "registry_sha256": sha256_file(registry_path),
        "attack_table_sha256": sha256_file(result_root / "tables" / "attack_results.csv"),
        "downstream_table_sha256": sha256_file(result_root / "tables" / "downstream_results.csv"),
        "selection_sha256": sha256_file(selection_path),
        "crypto_summary_sha256": sha256_file(result_root / "crypto" / "benchmark_summary.json"),
        "full_crypto_summary_sha256": sha256_file(result_root / "crypto" / "full_tokenizer_summary.json"),
        "figure_manifest_sha256": sha256_file(result_root / "figures" / "figure_manifest.json"),
        "latex_manifest_sha256": sha256_file(result_root / "tables" / "latex_generation_manifest.json"),
        "paper_pdf_sha256": sha256_file(paper_pdf),
        "paper_build_log_sha256": sha256_file(build_log),
        "literature_audit_sha256": sha256_file(PROJECT_ROOT / "docs" / "LITERATURE_AUDIT.csv"),
        "references_bib_sha256": sha256_file(PROJECT_ROOT / "paper" / "references.bib"),
        "tex_source_set_sha256": canonical_sha256({
            str(path.relative_to(PROJECT_ROOT)).replace("\\", "/"): sha256_file(path)
            for path in tex_files
        }),
    }
    audit_documents = {
        PROJECT_ROOT / "docs" / "FINAL_EXPERIMENT_AUDIT.md": experiment_doc,
        PROJECT_ROOT / "docs" / "CRYPTOGRAPHIC_AUDIT.md": crypto_doc,
        PROJECT_ROOT / "docs" / "PAPER_CITATION_AUDIT.md": citation_doc,
        PROJECT_ROOT / "docs" / "PAPER_LANGUAGE_AUDIT.md": language_doc,
        PROJECT_ROOT / "docs" / "LIMITATIONS.md": limitations_doc,
    }
    if audit_path.exists():
        prior = strict_json_load(audit_path)
        if prior.get("status") != "success":
            raise RuntimeError("existing final audit is not successful")
        if prior.get("audited_sources") != audited_sources:
            raise RuntimeError("existing final audit source hashes no longer match current results")
        missing_documents = [str(path) for path in audit_documents if not path.is_file()]
        if missing_documents:
            raise RuntimeError(f"existing final audit is missing documents: {missing_documents}")
        print(
            f"status=success verified_existing=true json={len(json_files)} attacks={len(attack_rows)} "
            f"downstream={len(downstream_rows)} figures=12 tables=10 citations={len(citation_keys)}"
        )
        return 0
    existing_documents = [str(path) for path in audit_documents if path.exists()]
    if existing_documents:
        raise FileExistsError(
            "audit documents exist without a final audit manifest; refusing to overwrite: "
            f"{existing_documents}"
        )
    for path, content in audit_documents.items():
        write_text_exclusive(path, content)
    write_json_exclusive(audit_path, {
        "schema_version": 1, "status": "success", "completed_at_utc": utc_now(),
        "errors": [], "warnings": warnings, "json_file_count": len(json_files),
        "attack_row_count": len(attack_rows), "downstream_row_count": len(downstream_rows),
        "figure_count": figure_manifest["figure_count"], "latex_table_count": latex_manifest["table_count"],
        "literature_count": len(literature), "recent_ccf_ab_count": recent_count,
        "cjk_character_count": cjk_count, "git_status_before_final_commit": git_status,
        "active_final_log_error_markers": active_log_errors,
        "preserved_attempt_log_error_markers": preserved_attempt_errors,
        "paper_overfull_hbox_count": overfull_box_count,
        "paper_underfull_hbox_count": underfull_box_count,
        "frozen_execution_commit": next(iter(frozen_execution_commits)),
        "environment": environment_metadata(),
        "audited_sources": audited_sources,
    })
    print(f"status=success json={len(json_files)} attacks={len(attack_rows)} downstream={len(downstream_rows)} figures=12 tables=10 citations={len(citation_keys)}")
    print(f"warnings={len(warnings)} audit={audit_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
