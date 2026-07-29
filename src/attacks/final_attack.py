"""Manifest-driven implementations of all five final tokenizer MIA attacks."""

from __future__ import annotations

import json
import time
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

from tokenizers import Tokenizer

from src.attacks.frequency_estimation import fit_zipf_exponent, frequency_estimation_scores
from src.attacks.merge_similarity import merge_similarity_scores
from src.attacks.metrics import compute_attack_metrics
from src.attacks.naive_bayes import naive_bayes_scores
from src.attacks.token_statistics import aggregate_counts, site_token_counts
from src.attacks.vocabulary_overlap import jaccard
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


ATTACKS = {
    "compression_rate",
    "vocabulary_overlap",
    "frequency_estimation",
    "merge_similarity",
    "naive_bayes",
}


def _validated_tokenizer(directory: Path, manifest: dict[str, Any]) -> tuple[Tokenizer, dict[str, Any]]:
    artifact = directory / "tokenizer.json"
    metadata_path = directory / "metadata.json"
    metadata = strict_json_load(metadata_path)
    if metadata.get("status") != "success":
        raise RuntimeError(f"non-success tokenizer metadata: {metadata_path}")
    if metadata.get("artifact_sha256") != sha256_file(artifact):
        raise RuntimeError(f"tokenizer artifact hash mismatch: {artifact}")
    if metadata.get("manifest_sha256") != manifest["manifest_sha256"]:
        raise RuntimeError(f"tokenizer manifest hash mismatch: {metadata_path}")
    return Tokenizer.from_file(str(artifact)), metadata


def _compression_scores(
    tokenizer: Tokenizer, corpus_path: Path, target_sites: set[str]
) -> tuple[dict[str, float], dict[str, dict[str, int]]]:
    totals: dict[str, list[int]] = defaultdict(lambda: [0, 0])
    with corpus_path.open("r", encoding="utf-8") as handle:
        for line in handle:
            if not line.strip():
                continue
            record = json.loads(line)
            site = record["site_id"]
            if site not in target_sites:
                continue
            totals[site][0] += len(record["text"].encode("utf-8"))
            totals[site][1] += len(tokenizer.encode(record["text"]).ids)
    scores: dict[str, float] = {}
    supporting: dict[str, dict[str, int]] = {}
    for site in sorted(target_sites):
        byte_count, token_count = totals[site]
        if token_count <= 0:
            raise RuntimeError(f"zero tokenizer output for target site: {site}")
        scores[site] = byte_count / token_count
        supporting[site] = {"byte_count": byte_count, "token_count": token_count}
    return scores, supporting


def _vocabulary_overlap_scores(
    target_vocab: set[str],
    shadow_vocabs: list[set[str]],
    shadow_training_sites: list[set[str]],
    target_sites: list[str],
) -> tuple[dict[str, float], dict[str, dict[str, int]]]:
    scores: dict[str, float] = {}
    supporting: dict[str, dict[str, int]] = {}
    for site in target_sites:
        inside = [vocab for vocab, sites in zip(shadow_vocabs, shadow_training_sites) if site in sites]
        outside = [vocab for vocab, sites in zip(shadow_vocabs, shadow_training_sites) if site not in sites]
        if not inside or not outside:
            raise RuntimeError(f"site lacks in/out shadow coverage: {site}")
        tokens_in = set().union(*inside)
        tokens_out = set().union(*outside)
        excluded = tokens_in & tokens_out
        filtered_target = target_vocab - excluded
        in_scores = [jaccard(vocab - excluded, filtered_target) for vocab in inside]
        out_scores = [jaccard(vocab - excluded, filtered_target) for vocab in outside]
        scores[site] = (sum(in_scores) / len(in_scores) - sum(out_scores) / len(out_scores) + 1.0) / 2.0
        supporting[site] = {
            "in_shadow_count": len(inside),
            "out_shadow_count": len(outside),
            "excluded_token_count": len(excluded),
        }
    return scores, supporting


def run_final_attack(
    *,
    attack: str,
    manifest_path: Path,
    target_dir: Path,
    shadow_dirs: list[Path],
    output_path: Path,
    auxiliary_group_count: int,
    naive_bayes_top_k: int,
    bootstrap_iterations: int,
    bootstrap_confidence: float,
) -> dict[str, Any]:
    if attack not in ATTACKS:
        raise ValueError(f"unsupported attack: {attack}")
    if output_path.exists():
        existing = strict_json_load(output_path)
        if existing.get("status") == "success":
            return {**existing, "checkpoint_reused": True}
        raise FileExistsError(f"refusing to overwrite attack result: {output_path}")
    started = time.perf_counter()
    manifest = strict_json_load(manifest_path)
    target_tokenizer, target_metadata = _validated_tokenizer(target_dir, manifest)
    target_sites = sorted(manifest["target_pool_site_ids"])
    target_site_set = set(target_sites)
    members = set(manifest["target_member_site_ids"])
    corpus_path = PROJECT_ROOT / manifest["corpus_path"]
    plans = {int(plan["shadow_id"]): plan for plan in manifest["shadow_plans"]}
    score_definition = ""
    implementation_difference = ""
    supporting: dict[str, dict[str, Any]] = {site: {} for site in target_sites}
    shadow_hashes: list[str] = []
    shadow_method_ids: list[str] = []
    token_statistics = "not_applicable"

    if attack == "compression_rate":
        scores, supporting = _compression_scores(target_tokenizer, corpus_path, target_site_set)
        score_definition = "UTF-8 byte count divided by target-tokenizer token count"
        implementation_difference = "Sigmoid omitted because it is monotone and can lose floating-point resolution."
    elif attack in {"vocabulary_overlap", "merge_similarity"}:
        if not shadow_dirs:
            raise ValueError(f"{attack} requires shadow tokenizers")
        shadow_vocabs_as_dict: list[dict[str, int]] = []
        shadow_training_sites: list[set[str]] = []
        for shadow_id, directory in enumerate(shadow_dirs):
            tokenizer, metadata = _validated_tokenizer(directory, manifest)
            if metadata.get("shadow_id") != shadow_id:
                raise RuntimeError(f"shadow ID mismatch in {directory}")
            shadow_vocabs_as_dict.append(tokenizer.get_vocab())
            shadow_training_sites.append(set(plans[shadow_id]["training_site_ids"]))
            shadow_hashes.append(metadata["artifact_sha256"])
            shadow_method_ids.append(str(metadata.get("method_id", metadata.get("method"))))
        if attack == "vocabulary_overlap":
            scores, supporting = _vocabulary_overlap_scores(
                set(target_tokenizer.get_vocab()),
                [set(vocab) for vocab in shadow_vocabs_as_dict],
                shadow_training_sites,
                target_sites,
            )
            score_definition = "official filtered in/out-shadow Jaccard difference, mapped to [0,1]"
            implementation_difference = "Deterministic ordering and explicit in/out coverage checks added."
        else:
            scores = merge_similarity_scores(
                target_vocab=target_tokenizer.get_vocab(),
                shadow_vocabs=shadow_vocabs_as_dict,
                shadow_training_sites=shadow_training_sites,
                target_sites=target_sites,
            )
            score_definition = "official mean in-shadow minus out-shadow shared-vocabulary rank correlation"
            implementation_difference = "Undefined or fewer-than-two-token correlations are fixed to zero."
    else:
        token_statistics = (
            "official regex units (\\w+|[^\\w\\s]+); each vocabulary token is counted once "
            "per unit occurrence when it is a substring, including overlapping vocabulary features"
        )
        if auxiliary_group_count <= 0 or auxiliary_group_count > len(plans):
            raise ValueError("invalid auxiliary group count")
        auxiliary_plans = [plans[index] for index in range(auxiliary_group_count)]
        involved_sites = set().union(
            *(set(plan["training_site_ids"]) for plan in auxiliary_plans)
        )
        counts = site_token_counts(target_tokenizer, corpus_path, target_site_set | involved_sites)
        background_counts = aggregate_counts(counts, sorted(involved_sites))
        target_counts = {site: counts[site] for site in target_sites}
        if attack == "naive_bayes":
            scores = naive_bayes_scores(
                target_vocab=target_tokenizer.get_vocab(),
                site_counts=target_counts,
                background_counts=background_counts,
                involved_sites=involved_sites,
                top_k=min(naive_bayes_top_k, target_tokenizer.get_vocab_size()),
            )
            score_definition = "non-saturating negative log of official Naive-Bayes survival product"
            implementation_difference = (
                "Official overlapping substring features are matched with Aho-Corasick; the log-domain "
                "score preserves non-saturated ranking while avoiding product underflow, and exact "
                "zero-survival cases share one fixed finite maximal sentinel."
            )
        else:
            if not shadow_dirs:
                raise ValueError("frequency estimation requires one shadow tokenizer")
            shadow_tokenizer, shadow_metadata = _validated_tokenizer(shadow_dirs[0], manifest)
            shadow_hashes.append(shadow_metadata["artifact_sha256"])
            shadow_method_ids.append(str(shadow_metadata.get("method_id", shadow_metadata.get("method"))))
            fit_sites = set(plans[0]["training_site_ids"])
            shadow_counts_by_site = site_token_counts(shadow_tokenizer, corpus_path, fit_sites)
            fit = fit_zipf_exponent(
                shadow_tokenizer.get_vocab(), aggregate_counts(shadow_counts_by_site, fit_sites)
            )
            scores = frequency_estimation_scores(
                target_vocab=target_tokenizer.get_vocab(),
                site_counts=target_counts,
                background_counts=background_counts,
                involved_sites=involved_sites,
                zipf_alpha=float(fit["alpha"]),
                xmin=int(fit["xmin"]),
            )
            score_definition = "maximum official-style relative token frequency times fitted Zipf rarity"
            implementation_difference = "Deterministic weighted discrete MLE/KS replaces the official 10M expanded powerlaw sample."
            supporting = {site: {"zipf_fit": fit} for site in target_sites}

    labels = [int(site in members) for site in target_sites]
    score_values = [float(scores[site]) for site in target_sites]
    metric_seed = int(sha256_text(f"{manifest['seed']}:{attack}:{target_metadata['artifact_sha256']}")[:8], 16)
    metrics = compute_attack_metrics(
        labels,
        score_values,
        seed=metric_seed,
        bootstrap_iterations=bootstrap_iterations,
        bootstrap_confidence=bootstrap_confidence,
    )
    details = [
        {
            "site_id": site,
            "is_member": site in members,
            "score": float(scores[site]),
            **supporting.get(site, {}),
        }
        for site in target_sites
    ]
    target_method_id = str(target_metadata.get("method_id", target_metadata.get("method")))
    if shadow_method_ids:
        shadow_knowledge = (
            "matched_training_method"
            if set(shadow_method_ids) == {target_method_id}
            else "fixed_transfer_shadow_method"
        )
    elif attack == "naive_bayes":
        shadow_knowledge = "auxiliary_membership_sets_without_shadow_tokenizer"
    else:
        shadow_knowledge = "shadow_tokenizer_not_required"
    result = {
        "schema_version": 1,
        "status": "success",
        "attack": attack,
        "score_definition": score_definition,
        "token_statistics": token_statistics,
        "official_code_difference": implementation_difference,
        "protocol": manifest["protocol"],
        "scale": manifest["scale"],
        "seed": int(manifest["seed"]),
        "method_id": target_method_id,
        "defense": target_metadata.get("defense", target_metadata.get("method")),
        "requested_vocab_size": target_metadata["requested_vocab_size"],
        "actual_vocab_size": target_metadata["actual_vocab_size"],
        "shadow_count": len(shadow_dirs) if attack in {"vocabulary_overlap", "merge_similarity"} else (1 if attack == "frequency_estimation" else 0),
        "shadow_tokenizer_method_ids": sorted(set(shadow_method_ids)),
        "attacker_shadow_knowledge": shadow_knowledge,
        "auxiliary_group_count": auxiliary_group_count if attack in {"frequency_estimation", "naive_bayes"} else 0,
        "data": {
            "dataset_id": manifest["dataset_id"],
            "dataset_revision": manifest["dataset_revision"],
            "site_count": len(target_sites),
            "member_site_count": len(members),
            "nonmember_site_count": len(target_site_set - members),
            "minimum_texts_per_site": manifest["minimum_texts_per_site"],
            "maximum_texts_per_site": manifest["maximum_texts_per_site"],
            "manifest_sha256": manifest["manifest_sha256"],
        },
        "artifacts": {
            "target_tokenizer_sha256": target_metadata["artifact_sha256"],
            "shadow_tokenizer_sha256": shadow_hashes,
        },
        "metrics": metrics,
        "details": details,
        "elapsed_seconds": time.perf_counter() - started,
        "peak_memory_bytes": peak_working_set_bytes(),
        "completed_at_utc": utc_now(),
        "environment": environment_metadata(),
        "checkpoint_reused": False,
    }
    write_json_exclusive(output_path, result)
    return result
