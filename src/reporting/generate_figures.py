"""Generate all manuscript figures and figure-specific CSVs from final artifacts."""

from __future__ import annotations

import argparse
import csv
import math
import os
import statistics
from pathlib import Path
from typing import Any

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.ticker as mticker
import numpy as np

from src.reporting.collect_results import write_csv_exclusive
from src.utils.run_metadata import PROJECT_ROOT, environment_metadata, sha256_file, strict_json_load, utc_now, write_json_exclusive


ATTACK_LABELS = {
    "compression_rate": "Compression rate",
    "vocabulary_overlap": "Vocabulary overlap",
    "frequency_estimation": "Frequency estimation",
    "merge_similarity": "Merge similarity",
    "naive_bayes": "Naive Bayes",
}


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def save_figure(fig: plt.Figure, number: int, name: str, figure_root: Path) -> list[Path]:
    base = figure_root / f"figure_{number:02d}_{name}"
    pdf = base.with_suffix(".pdf")
    png = base.with_suffix(".png")
    for path in (pdf, png):
        if path.exists():
            raise FileExistsError(f"refusing to overwrite figure: {path}")
    fig.savefig(pdf, bbox_inches="tight")
    fig.savefig(png, dpi=220, bbox_inches="tight")
    plt.close(fig)
    return [pdf, png]


def schematic(
    *, number: int, name: str, nodes: list[tuple[str, float, float]],
    edges: list[tuple[str, str, str]], source_root: Path, figure_root: Path,
) -> tuple[list[Path], Path]:
    positions = {label: (x, y) for label, x, y in nodes}
    rows = [
        {"kind": "node", "source": label, "target": "", "label": label, "x": x, "y": y}
        for label, x, y in nodes
    ] + [
        {"kind": "edge", "source": left, "target": right, "label": label, "x": "", "y": ""}
        for left, right, label in edges
    ]
    source = source_root / f"figure_{number:02d}.csv"
    write_csv_exclusive(source, rows)
    fig, ax = plt.subplots(figsize=(10, 4.8))
    for label, x, y in nodes:
        ax.text(x, y, label, ha="center", va="center", fontsize=10,
                bbox={"boxstyle": "round,pad=0.5", "facecolor": "#e7f0fa", "edgecolor": "#34699a"})
    for left, right, edge_label in edges:
        x1, y1 = positions[left]
        x2, y2 = positions[right]
        length = math.hypot(x2 - x1, y2 - y1)
        unit_x, unit_y = (x2 - x1) / length, (y2 - y1) / length
        pad = 0.075
        start = (x1 + pad * unit_x, y1 + pad * unit_y)
        end = (x2 - pad * unit_x, y2 - pad * unit_y)
        ax.annotate("", xy=end, xytext=start,
                    arrowprops={"arrowstyle": "->", "color": "#333333", "lw": 1.3,
                                "shrinkA": 0, "shrinkB": 0})
        label_offset = 0.13 if abs(y1 - y2) < 0.02 else 0.045
        ax.text((x1 + x2) / 2, (y1 + y2) / 2 + label_offset, edge_label, fontsize=8,
                ha="center", va="center", backgroundcolor="white")
    ax.set_xlim(0, 1)
    ax.set_ylim(0, 1)
    ax.axis("off")
    return save_figure(fig, number, name, figure_root), source


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--fast-report", action="store_true")
    args = parser.parse_args()
    config = strict_json_load(args.config.resolve())
    result_root = PROJECT_ROOT / config["results_root"]
    table_root = result_root / "tables"
    registry = strict_json_load(result_root / "result_registry.json")
    if registry.get("status") != "success":
        raise RuntimeError("result registry must be successful before figure generation")
    figure_root = PROJECT_ROOT / "paper" / "figures"
    source_root = result_root / "figures" / "source_data"
    if figure_root.exists() and any(figure_root.iterdir()):
        raise FileExistsError(f"paper figure directory is not empty: {figure_root}")
    if source_root.exists() and any(source_root.iterdir()):
        raise FileExistsError(f"figure source directory is not empty: {source_root}")
    figure_root.mkdir(parents=True, exist_ok=True)
    source_root.mkdir(parents=True, exist_ok=True)
    plt.rcParams.update({"font.size": 9, "axes.grid": True, "grid.alpha": 0.25, "pdf.fonttype": 42})
    manifest_rows: list[dict[str, Any]] = []

    files, source = schematic(
        number=1, name="system_protocol",
        nodes=[("Public corpus", .12, .78), ("Clients P_i", .12, .25), ("Server A\n(no secret key)", .40, .50),
               ("Server D\n(secret key)", .65, .50), ("Tokenizer", .90, .50)],
        edges=[("Public corpus", "Server A\n(no secret key)", "public top-K pairs"),
               ("Clients P_i", "Server A\n(no secret key)", "clipped Paillier ciphertexts"),
               ("Server A\n(no secret key)", "Server D\n(secret key)", "Paillier aggregate"),
               ("Server D\n(secret key)", "Tokenizer", "top-b IDs")],
        source_root=source_root, figure_root=figure_root,
    )
    manifest_rows.append({"figure": 1, "title": "System model and protocol", "source": source, "files": files})

    files, source = schematic(
        number=2, name="attack_pipeline",
        nodes=[("Target tokenizer", .10, .68), ("Candidate sites", .10, .25), ("Fixed score", .38, .46),
               ("Member/nonmember labels", .64, .25), ("ROC / PR metrics", .90, .46)],
        edges=[("Target tokenizer", "Fixed score", "query/artifact"),
               ("Candidate sites", "Fixed score", "site text"),
               ("Fixed score", "ROC / PR metrics", "membership score"),
               ("Member/nonmember labels", "ROC / PR metrics", "metrics only")],
        source_root=source_root, figure_root=figure_root,
    )
    manifest_rows.append({"figure": 2, "title": "Tokenizer membership inference workflow", "source": source, "files": files})

    seeds = [int(value) for value in config["seeds"]]
    grid = np.linspace(0.0, 1.0, 501)
    roc_rows = []
    fig, ax = plt.subplots(figsize=(6.4, 4.5))
    for attack in config["attacks"]:
        curves = []
        for seed in seeds:
            path = result_root / "runs" / "attacks" / "main" / str(seed) / "vocab_16000" / "plain_bpe" / f"{attack}.json"
            result = strict_json_load(path)
            curves.append(np.interp(grid, result["metrics"]["fpr"], result["metrics"]["tpr"]))
        matrix = np.vstack(curves)
        mean = matrix.mean(axis=0)
        std = matrix.std(axis=0, ddof=1)
        ax.plot(grid, mean, label=ATTACK_LABELS[attack])
        ax.fill_between(grid, np.maximum(0, mean - std), np.minimum(1, mean + std), alpha=.12)
        roc_rows.extend({"attack": attack, "fpr": x, "mean_tpr": y, "sample_std_tpr": s}
                        for x, y, s in zip(grid, mean, std))
    ax.plot([0, 1], [0, 1], "k--", lw=1, label="Random")
    ax.set(xlabel="False positive rate", ylabel="True positive rate", xlim=(0, 1), ylim=(0, 1),
           title="Five tokenizer attacks on Plain BPE (Main, 16k)")
    ax.legend(fontsize=8)
    source = source_root / "figure_03.csv"
    write_csv_exclusive(source, roc_rows)
    files = save_figure(fig, 3, "plain_roc", figure_root)
    manifest_rows.append({"figure": 3, "title": "Five attacks ROC", "source": source, "files": files})

    vocab_rows = []
    fig, ax = plt.subplots(figsize=(6.6, 4.5))
    for attack in config["attacks"]:
        means, stds = [], []
        for vocab in config["vocab_sizes"]:
            values = [
                float(strict_json_load(result_root / "runs" / "attacks" / "main" / str(seed) / f"vocab_{vocab}" / "plain_bpe" / f"{attack}.json")["metrics"]["roc_auc"])
                for seed in seeds
            ]
            means.append(statistics.fmean(values))
            stds.append(statistics.stdev(values))
            vocab_rows.append({"attack": attack, "vocab_size": vocab, "auc_mean": means[-1], "auc_sample_std": stds[-1]})
        ax.errorbar(config["vocab_sizes"], means, yerr=stds, marker="o", capsize=3, label=ATTACK_LABELS[attack])
    ax.set(xscale="log", xlabel="Requested vocabulary size", ylabel="ROC AUC",
           title="Vocabulary size and attack performance")
    ax.set_xticks(config["vocab_sizes"], labels=[str(value) for value in config["vocab_sizes"]])
    ax.xaxis.set_minor_formatter(mticker.NullFormatter())
    ax.legend(fontsize=8)
    source = source_root / "figure_04.csv"
    write_csv_exclusive(source, vocab_rows)
    files = save_figure(fig, 4, "vocab_auc", figure_root)
    manifest_rows.append({"figure": 4, "title": "Vocabulary size sensitivity", "source": source, "files": files})

    sensitivity = read_csv(table_root / "shadow_sensitivity.csv")
    source = source_root / "figure_05.csv"
    write_csv_exclusive(source, sensitivity)
    fig, ax = plt.subplots(figsize=(6.4, 4.3))
    for attack in sorted({row["attack"] for row in sensitivity}):
        rows = sorted((row for row in sensitivity if row["attack"] == attack), key=lambda row: int(row["shadow_count"]))
        ax.plot([int(row["shadow_count"]) for row in rows], [float(row["roc_auc"]) for row in rows], marker="o", label=ATTACK_LABELS[attack])
    ax.set(xlabel="Number of shadow tokenizers", ylabel="ROC AUC", title="Shadow-tokenizer sensitivity")
    ax.set_xticks(config["shadow_sensitivity_counts"])
    ax.legend()
    files = save_figure(fig, 5, "shadow_sensitivity", figure_root)
    manifest_rows.append({"figure": 5, "title": "Shadow count sensitivity", "source": source, "files": files})

    dev_attacks = read_csv(table_root / "development_defense_attacks.csv")
    reference = config["development_search_design"]["reference"]
    epsilon_attack_rows = []
    fig, ax = plt.subplots(figsize=(6.6, 4.5))
    for attack in config["attacks"]:
        points = []
        for epsilon in config["development_grid"]["epsilon_total"]:
            rows = [row for row in dev_attacks if row["mode"] == "sa_dp" and float(row["epsilon_total"]) == float(epsilon)
                    and int(row["clipping_percentile"]) == int(reference["clipping_percentile"])
                    and int(row["batch_merge_size"]) == int(reference["batch_merge_size"])
                    and int(row["candidate_pool_size"]) == int(reference["candidate_pool_size"])
                    and row["attack"] == attack]
            values = [float(row["roc_auc"]) for row in rows]
            points.append((epsilon, statistics.fmean(values), statistics.stdev(values)))
            epsilon_attack_rows.append({"attack": attack, "epsilon_total": epsilon, "auc_mean": points[-1][1], "auc_sample_std": points[-1][2]})
        ax.errorbar([p[0] for p in points], [p[1] for p in points], yerr=[p[2] for p in points], marker="o", capsize=3, label=ATTACK_LABELS[attack])
    ax.set(xscale="log", xlabel="Total privacy budget epsilon", ylabel="ROC AUC", title="Privacy budget and Development attack performance")
    ax.set_xticks(config["development_grid"]["epsilon_total"], labels=[str(x) for x in config["development_grid"]["epsilon_total"]])
    ax.legend(fontsize=8)
    source = source_root / "figure_06.csv"
    write_csv_exclusive(source, epsilon_attack_rows)
    files = save_figure(fig, 6, "epsilon_auc", figure_root)
    manifest_rows.append({"figure": 6, "title": "Epsilon and attack AUC", "source": source, "files": files})

    dev_utility = read_csv(table_root / "development_tokenizer_utility.csv")
    epsilon_utility_rows = []
    for epsilon in config["development_grid"]["epsilon_total"]:
        rows = [row for row in dev_utility if row["mode"] == "sa_dp" and float(row["epsilon_total"]) == float(epsilon)
                and int(row["clipping_percentile"]) == int(reference["clipping_percentile"])
                and int(row["batch_merge_size"]) == int(reference["batch_merge_size"])
                and int(row["candidate_pool_size"]) == int(reference["candidate_pool_size"])
                and row["source_corpus"] == "c4_heldout"]
        values = [float(row["bytes_per_token"]) for row in rows]
        epsilon_utility_rows.append({"epsilon_total": epsilon, "bytes_per_token_mean": statistics.fmean(values), "bytes_per_token_sample_std": statistics.stdev(values)})
    fig, ax = plt.subplots(figsize=(6.2, 4.2))
    ax.errorbar([row["epsilon_total"] for row in epsilon_utility_rows], [row["bytes_per_token_mean"] for row in epsilon_utility_rows],
                yerr=[row["bytes_per_token_sample_std"] for row in epsilon_utility_rows], marker="o", capsize=3)
    ax.set(xscale="log", xlabel="Total privacy budget epsilon", ylabel="C4 held-out bytes per token",
           title="Privacy budget and tokenizer compression")
    ax.set_xticks(config["development_grid"]["epsilon_total"], labels=[str(x) for x in config["development_grid"]["epsilon_total"]])
    source = source_root / "figure_07.csv"
    write_csv_exclusive(source, epsilon_utility_rows)
    files = save_figure(fig, 7, "epsilon_compression", figure_root)
    manifest_rows.append({"figure": 7, "title": "Epsilon and compression", "source": source, "files": files})

    dev_downstream = read_csv(table_root / "development_downstream.csv")
    method_epsilon = {row["method_id"]: float(row["epsilon_total"]) for row in dev_attacks if row["mode"] == "sa_dp" and row["epsilon_total"] != ""
                      and int(row["clipping_percentile"]) == int(reference["clipping_percentile"])
                      and int(row["batch_merge_size"]) == int(reference["batch_merge_size"])
                      and int(row["candidate_pool_size"]) == int(reference["candidate_pool_size"])}
    epsilon_downstream_rows = []
    for method, epsilon in sorted(method_epsilon.items(), key=lambda item: item[1]):
        values = [float(row["macro_f1"]) for row in dev_downstream if row["method_id"] == method]
        if len(values) != len(seeds):
            raise RuntimeError(f"epsilon downstream sweep incomplete for {method}")
        epsilon_downstream_rows.append({"method_id": method, "epsilon_total": epsilon, "macro_f1_mean": statistics.fmean(values), "macro_f1_sample_std": statistics.stdev(values)})
    fig, ax = plt.subplots(figsize=(6.2, 4.2))
    ax.errorbar([row["epsilon_total"] for row in epsilon_downstream_rows], [row["macro_f1_mean"] for row in epsilon_downstream_rows],
                yerr=[row["macro_f1_sample_std"] for row in epsilon_downstream_rows], marker="o", capsize=3)
    ax.set(xscale="log", xlabel="Total privacy budget epsilon", ylabel="AG News Macro-F1", title="Privacy budget and downstream utility")
    ax.set_xticks(config["development_grid"]["epsilon_total"], labels=[str(x) for x in config["development_grid"]["epsilon_total"]])
    source = source_root / "figure_08.csv"
    write_csv_exclusive(source, epsilon_downstream_rows)
    files = save_figure(fig, 8, "epsilon_macro_f1", figure_root)
    manifest_rows.append({"figure": 8, "title": "Epsilon and AG News Macro-F1", "source": source, "files": files})

    crypto = read_csv(table_root / "crypto_benchmark.csv")
    clients_rows = sorted((row for row in crypto if int(row["candidate_dimension"]) == 1024), key=lambda row: int(row["client_count"]))
    source = source_root / "figure_09.csv"
    write_csv_exclusive(source, clients_rows)
    fig, ax = plt.subplots(figsize=(6.2, 4.2))
    crypto_time_field = "round_mean_seconds" if args.fast_report else "round_median_seconds"
    ax.plot([int(row["client_count"]) for row in clients_rows], [float(row[crypto_time_field]) for row in clients_rows], color="#557799", lw=1)
    for source_kind, marker in (("measured", "o"), ("extrapolated", "*")):
        selected_rows = [row for row in clients_rows if row.get("data_source", "measured") == source_kind]
        if selected_rows:
            ax.scatter([int(row["client_count"]) for row in selected_rows], [float(row[crypto_time_field]) for row in selected_rows],
                       marker=marker, s=70 if marker == "*" else 35, label="M: measured" if source_kind == "measured" else "E: extrapolated")
    ax.set(xlabel="Client count (K=1024)", ylabel="Mean round time (s)" if args.fast_report else "Median round time (s)", title="2048-bit Paillier cost versus clients")
    if args.fast_report:
        ax.legend()
    files = save_figure(fig, 9, "crypto_clients", figure_root)
    manifest_rows.append({"figure": 9, "title": "Paillier overhead by clients", "source": source, "files": files})

    dimension_rows = sorted((row for row in crypto if int(row["client_count"]) == 16), key=lambda row: int(row["candidate_dimension"]))
    source = source_root / "figure_10.csv"
    write_csv_exclusive(source, dimension_rows)
    fig, ax = plt.subplots(figsize=(6.2, 4.2))
    ax.plot([int(row["candidate_dimension"]) for row in dimension_rows], [float(row[crypto_time_field]) for row in dimension_rows], marker="o")
    ax.set(xlabel="Candidate dimension K (16 clients)", ylabel="Mean round time (s)" if args.fast_report else "Median round time (s)", title="2048-bit Paillier cost versus dimension")
    files = save_figure(fig, 10, "crypto_dimension", figure_root)
    manifest_rows.append({"figure": 10, "title": "Paillier overhead by K", "source": source, "files": files})

    attacks = read_csv(table_root / "attack_results.csv")
    downstream = read_csv(table_root / "downstream_results.csv")
    utility = read_csv(table_root / "tokenizer_utility.csv")
    methods = sorted({row["method_id"] for row in downstream})
    plain_time = statistics.fmean(float(row["tokenizer_training_seconds"]) for row in utility if row["method_id"] == "plain_bpe" and row["source_corpus"] == "c4_heldout")
    pareto_rows = []
    for method in methods:
        mean_auc = statistics.fmean(float(row["roc_auc"]) for row in attacks if row["method_id"] == method)
        macro = statistics.fmean(float(row["macro_f1"]) for row in downstream if row["method_id"] == method)
        training = statistics.fmean(float(row["tokenizer_training_seconds"]) for row in utility if row["method_id"] == method and row["source_corpus"] == "c4_heldout")
        pareto_rows.append({"method_id": method, "mean_attack_auc": mean_auc, "macro_f1_mean": macro, "tokenizer_training_time_ratio": training / plain_time})
    for row in pareto_rows:
        dominated = any(other["mean_attack_auc"] <= row["mean_attack_auc"] and other["macro_f1_mean"] >= row["macro_f1_mean"]
                        and (other["mean_attack_auc"] < row["mean_attack_auc"] or other["macro_f1_mean"] > row["macro_f1_mean"])
                        for other in pareto_rows)
        row["pareto_nondominated_privacy_utility"] = not dominated
    source = source_root / "figure_11.csv"
    write_csv_exclusive(source, pareto_rows)
    fig, ax = plt.subplots(figsize=(7.0, 4.8))
    for row in pareto_rows:
        ax.scatter(row["mean_attack_auc"], row["macro_f1_mean"], s=35 + 12 * np.log1p(row["tokenizer_training_time_ratio"]),
                   marker="o" if row["pareto_nondominated_privacy_utility"] else "x")
        ax.annotate(row["method_id"], (row["mean_attack_auc"], row["macro_f1_mean"]), xytext=(4, 3), textcoords="offset points", fontsize=7)
    ax.set(xlabel="Mean attack ROC AUC (lower is better)", ylabel="AG News Macro-F1 (higher is better)", title="Privacy-utility Pareto view")
    files = save_figure(fig, 11, "pareto", figure_root)
    manifest_rows.append({"figure": 11, "title": "Privacy utility Pareto", "source": source, "files": files})

    ablation_rows = []
    for batch in config["development_grid"]["batch_merge_size"]:
        attack_values = [float(row["roc_auc"]) for row in dev_attacks if row["mode"] == "sa_dp" and float(row["epsilon_total"]) == float(reference["epsilon_total"])
                         and int(row["clipping_percentile"]) == int(reference["clipping_percentile"])
                         and int(row["batch_merge_size"]) == int(batch) and int(row["candidate_pool_size"]) == int(reference["candidate_pool_size"])]
        utility_values = [float(row["bytes_per_token"]) for row in dev_utility if row["mode"] == "sa_dp" and float(row["epsilon_total"]) == float(reference["epsilon_total"])
                          and int(row["clipping_percentile"]) == int(reference["clipping_percentile"])
                          and int(row["batch_merge_size"]) == int(batch) and int(row["candidate_pool_size"]) == int(reference["candidate_pool_size"])
                          and row["source_corpus"] == "c4_heldout"]
        ablation_rows.append({"batch_merge_size": batch, "mean_attack_auc": statistics.fmean(attack_values),
                              "attack_auc_sample_std": statistics.stdev(attack_values),
                              "bytes_per_token_mean": statistics.fmean(utility_values),
                              "bytes_per_token_sample_std": statistics.stdev(utility_values)})
    source = source_root / "figure_12.csv"
    write_csv_exclusive(source, ablation_rows)
    fig, ax = plt.subplots(figsize=(6.4, 4.3))
    second = ax.twinx()
    ax.plot([row["batch_merge_size"] for row in ablation_rows], [row["mean_attack_auc"] for row in ablation_rows], "o-", color="#b33", label="Mean attack AUC")
    second.plot([row["batch_merge_size"] for row in ablation_rows], [row["bytes_per_token_mean"] for row in ablation_rows], "s-", color="#26734d", label="Bytes/token")
    ax.set(xlabel="Batch merge size b", ylabel="Mean ROC AUC", title="Batch-merge ablation")
    second.set_ylabel("C4 held-out bytes per token")
    ax.set_xticks(config["development_grid"]["batch_merge_size"])
    lines = ax.lines + second.lines
    ax.legend(lines, [line.get_label() for line in lines], fontsize=8)
    files = save_figure(fig, 12, "batch_ablation", figure_root)
    manifest_rows.append({"figure": 12, "title": "Batch merge ablation", "source": source, "files": files})

    if args.fast_report:
        method_order = list(dict.fromkeys(row["method_id"] for row in attacks))
        attack_order = list(config["attacks"])

        risk_rows = []
        risk_matrix = np.empty((len(method_order), len(attack_order)), dtype=np.float64)
        for method_index, method in enumerate(method_order):
            for attack_index, attack in enumerate(attack_order):
                values = [float(row["roc_auc"]) for row in attacks if row["method_id"] == method and row["attack"] == attack]
                value = statistics.fmean(values)
                risk_matrix[method_index, attack_index] = value
                risk_rows.append({"method_id": method, "attack": attack, "roc_auc_mean": value,
                                  "roc_auc_sample_std": statistics.stdev(values), "seed_count": len(values)})
        source = source_root / "figure_13.csv"
        write_csv_exclusive(source, risk_rows)
        fig, ax = plt.subplots(figsize=(9.2, 5.4))
        image = ax.imshow(risk_matrix, aspect="auto", cmap="viridis", vmin=min(.5, float(risk_matrix.min())), vmax=max(.75, float(risk_matrix.max())))
        ax.set_xticks(np.arange(len(attack_order)), labels=[ATTACK_LABELS[value] for value in attack_order], rotation=24, ha="right")
        ax.set_yticks(np.arange(len(method_order)), labels=method_order)
        for row_index in range(risk_matrix.shape[0]):
            for column_index in range(risk_matrix.shape[1]):
                ax.text(column_index, row_index, f"{risk_matrix[row_index, column_index]:.3f}", ha="center", va="center", fontsize=7,
                        color="white" if risk_matrix[row_index, column_index] < risk_matrix.mean() else "black")
        ax.set_title("Membership-inference risk across defenses and attacks")
        fig.colorbar(image, ax=ax, label="Mean ROC AUC")
        files = save_figure(fig, 13, "defense_attack_risk", figure_root)
        manifest_rows.append({"figure": 13, "title": "Five-attack defense risk comparison", "source": source, "files": files})

        utility_comparison = []
        for method in method_order:
            values = [float(row["bytes_per_token"]) for row in utility if row["method_id"] == method and row["source_corpus"] == "c4_heldout"]
            utility_comparison.append({"method_id": method, "bytes_per_token_mean": statistics.fmean(values),
                                       "bytes_per_token_sample_std": statistics.stdev(values), "seed_count": len(values)})
        source = source_root / "figure_14.csv"
        write_csv_exclusive(source, utility_comparison)
        fig, ax = plt.subplots(figsize=(8.2, 4.6))
        x = np.arange(len(utility_comparison))
        ax.bar(x, [row["bytes_per_token_mean"] for row in utility_comparison],
               yerr=[row["bytes_per_token_sample_std"] for row in utility_comparison], capsize=3, color="#4c78a8")
        ax.set_xticks(x, labels=[row["method_id"] for row in utility_comparison], rotation=28, ha="right")
        ax.set(ylabel="C4 held-out bytes per token", title="Tokenizer compression utility (higher is better)")
        files = save_figure(fig, 14, "tokenizer_utility", figure_root)
        manifest_rows.append({"figure": 14, "title": "Tokenizer utility comparison", "source": source, "files": files})

        downstream_aggregates = read_csv(table_root / "downstream_aggregates.csv")
        for number, metric, label, name in (
            (15, "accuracy", "AG News Accuracy", "ag_news_accuracy"),
            (16, "macro_f1", "AG News Macro-F1", "ag_news_macro_f1"),
        ):
            chart_rows = [{"method_id": row["method_id"], "mean": row[f"{metric}_mean"],
                           "sample_standard_deviation": row[f"{metric}_sample_std"], "seed_count": row["seed_count"]}
                          for row in downstream_aggregates]
            source = source_root / f"figure_{number:02d}.csv"
            write_csv_exclusive(source, chart_rows)
            fig, ax = plt.subplots(figsize=(8.2, 4.6))
            x = np.arange(len(chart_rows))
            ax.bar(x, [float(row["mean"]) for row in chart_rows],
                   yerr=[float(row["sample_standard_deviation"]) for row in chart_rows], capsize=3, color="#59a14f")
            ax.set_xticks(x, labels=[row["method_id"] for row in chart_rows], rotation=28, ha="right")
            lower = min(float(row["mean"]) - float(row["sample_standard_deviation"]) for row in chart_rows)
            ax.set_ylim(max(0.0, lower - .01), 1.0)
            ax.set(ylabel=label, title=f"{label} across Main tokenizers (three seeds)")
            files = save_figure(fig, number, name, figure_root)
            manifest_rows.append({"figure": number, "title": label + " comparison", "source": source, "files": files})

        source = source_root / "figure_17.csv"
        write_csv_exclusive(source, crypto)
        fig, ax = plt.subplots(figsize=(7.4, 4.8))
        for clients in (2, 8, 16, 32, 64):
            chart_rows = sorted((row for row in crypto if int(row["client_count"]) == clients), key=lambda row: int(row["candidate_dimension"]))
            ax.plot([int(row["candidate_dimension"]) for row in chart_rows], [float(row["round_mean_seconds"]) for row in chart_rows], lw=1, alpha=.65)
            measured_rows = [row for row in chart_rows if row["data_source"] == "measured"]
            extrapolated_rows = [row for row in chart_rows if row["data_source"] == "extrapolated"]
            if measured_rows:
                ax.scatter([int(row["candidate_dimension"]) for row in measured_rows], [float(row["round_mean_seconds"]) for row in measured_rows], marker="o", s=26, label=f"{clients} clients (M)")
            if extrapolated_rows:
                ax.scatter([int(row["candidate_dimension"]) for row in extrapolated_rows], [float(row["round_mean_seconds"]) for row in extrapolated_rows], marker="*", s=75, label=f"{clients} clients (E)")
        ax.set(xscale="log", yscale="log", xlabel="Candidate dimension K", ylabel="Mean round time (s)", title="Paillier total time versus K: measured and extrapolated")
        ax.set_xticks(config["paillier"]["candidate_dimensions"], labels=[str(value) for value in config["paillier"]["candidate_dimensions"]])
        ax.xaxis.set_minor_formatter(mticker.NullFormatter())
        ax.legend(fontsize=7, ncol=2)
        files = save_figure(fig, 17, "crypto_total_vs_k_me", figure_root)
        manifest_rows.append({"figure": 17, "title": "Paillier total time versus K with M/E labels", "source": source, "files": files})

        source = source_root / "figure_18.csv"
        write_csv_exclusive(source, crypto)
        fig, ax = plt.subplots(figsize=(7.4, 4.8))
        for dimension in (128, 1024, 4096):
            chart_rows = sorted((row for row in crypto if int(row["candidate_dimension"]) == dimension), key=lambda row: int(row["client_count"]))
            ax.plot([int(row["client_count"]) for row in chart_rows], [float(row["round_mean_seconds"]) for row in chart_rows], lw=1, alpha=.65)
            measured_rows = [row for row in chart_rows if row["data_source"] == "measured"]
            extrapolated_rows = [row for row in chart_rows if row["data_source"] == "extrapolated"]
            if measured_rows:
                ax.scatter([int(row["client_count"]) for row in measured_rows], [float(row["round_mean_seconds"]) for row in measured_rows], marker="o", s=28, label=f"K={dimension} (M)")
            if extrapolated_rows:
                ax.scatter([int(row["client_count"]) for row in extrapolated_rows], [float(row["round_mean_seconds"]) for row in extrapolated_rows], marker="*", s=75, label=f"K={dimension} (E)")
        ax.set(xscale="log", yscale="log", xlabel="Client count", ylabel="Mean round time (s)", title="Paillier total time versus clients: measured and extrapolated")
        ax.set_xticks(config["paillier"]["client_counts"], labels=[str(value) for value in config["paillier"]["client_counts"]])
        ax.xaxis.set_minor_formatter(mticker.NullFormatter())
        ax.legend(fontsize=7, ncol=2)
        files = save_figure(fig, 18, "crypto_total_vs_clients_me", figure_root)
        manifest_rows.append({"figure": 18, "title": "Paillier total time versus clients with M/E labels", "source": source, "files": files})

        models = strict_json_load(result_root / "report_fast" / "crypto_scaling_models.json")
        total_model_block = models["metrics"]["round_total_seconds"]
        selected_model = next(row for row in total_model_block["models"] if row["model_name"] == total_model_block["selected_model"])
        coefficients = np.asarray(selected_model["coefficients"], dtype=np.float64)
        fit_rows = []
        for row in crypto:
            if row["data_source"] != "measured":
                continue
            clients = float(row["client_count"])
            dimension = float(row["candidate_dimension"])
            if selected_model["model_name"] == "M1": transformed = coefficients @ np.asarray([1.0, clients, dimension])
            elif selected_model["model_name"] == "M2": transformed = coefficients @ np.asarray([1.0, clients * dimension])
            elif selected_model["model_name"] == "M3": transformed = coefficients @ np.asarray([1.0, math.log(clients), math.log(dimension)])
            else: transformed = coefficients @ np.asarray([1.0, dimension, clients * dimension])
            fitted = math.exp(float(transformed)) if selected_model["log_target"] else float(transformed)
            fit_rows.append({"clients": int(clients), "K": int(dimension), "measured_seconds": row["round_mean_seconds"],
                             "fitted_seconds": fitted, "model_name": selected_model["model_name"], "data_source": "measured"})
        source = source_root / "figure_19.csv"
        write_csv_exclusive(source, fit_rows)
        fig, ax = plt.subplots(figsize=(5.4, 5.0))
        measured_values = np.asarray([float(row["measured_seconds"]) for row in fit_rows])
        fitted_values = np.asarray([float(row["fitted_seconds"]) for row in fit_rows])
        ax.scatter(measured_values, fitted_values, color="#e15759")
        limit = max(float(measured_values.max()), float(fitted_values.max())) * 1.05
        ax.plot([0, limit], [0, limit], "k--", lw=1, label="ideal fit")
        ax.set(xlabel="Measured mean time (s)", ylabel="Model-fitted time (s)", title=f"Measured versus fitted total time ({selected_model['model_name']})", xlim=(0, limit), ylim=(0, limit))
        ax.legend()
        files = save_figure(fig, 19, "crypto_measured_vs_fitted", figure_root)
        manifest_rows.append({"figure": 19, "title": "Measured versus fitted Paillier time", "source": source, "files": files})

        representative = [(16, 1024), (32, 2048), (32, 4096), (64, 4096)]
        stage_fields = [
            ("encryption_mean_seconds", "Encryption"),
            ("aggregation_mean_seconds", "Aggregation"),
            ("noise_encryption_mean_seconds", "Noise encryption"),
            ("decryption_mean_seconds", "Decryption"),
            ("selection_mean_seconds", "Selection"),
        ]
        stack_rows = []
        selected_crypto = []
        for clients, dimension in representative:
            row = next(item for item in crypto if int(item["client_count"]) == clients and int(item["candidate_dimension"]) == dimension)
            selected_crypto.append(row)
            for field, stage in stage_fields:
                stack_rows.append({"clients": clients, "K": dimension, "stage": stage, "seconds": row[field], "data_source": row["data_source"]})
        source = source_root / "figure_20.csv"
        write_csv_exclusive(source, stack_rows)
        fig, ax = plt.subplots(figsize=(7.2, 4.8))
        x = np.arange(len(selected_crypto))
        bottoms = np.zeros(len(selected_crypto))
        colors = ["#4e79a7", "#f28e2b", "#e15759", "#76b7b2", "#59a14f"]
        for (field, stage), color in zip(stage_fields, colors):
            values = np.asarray([float(row[field]) for row in selected_crypto])
            bars = ax.bar(x, values, bottom=bottoms, label=stage, color=color)
            for index, bar in enumerate(bars):
                if selected_crypto[index]["data_source"] == "extrapolated":
                    bar.set_hatch("//")
            bottoms += values
        labels = [f"c={row['client_count']}, K={row['candidate_dimension']}\n({'M' if row['data_source']=='measured' else 'E'})" for row in selected_crypto]
        ax.set_xticks(x, labels=labels)
        ax.set(ylabel="Mean round time (s)", title="Paillier stage-cost composition; hatched bars are extrapolated")
        ax.legend(fontsize=7)
        files = save_figure(fig, 20, "crypto_stage_stack", figure_root)
        manifest_rows.append({"figure": 20, "title": "Paillier stage-cost stack with M/E labels", "source": source, "files": files})

    payload = {
        "schema_version": 1, "status": "success", "generated_at_utc": utc_now(),
        "figure_count": len(manifest_rows),
        "figures": [
            {"figure": row["figure"], "title": row["title"],
             "source_csv": str(row["source"].relative_to(PROJECT_ROOT)).replace("\\", "/"),
             "source_sha256": sha256_file(row["source"]),
             "files": [{"path": str(path.relative_to(PROJECT_ROOT)).replace("\\", "/"), "sha256": sha256_file(path)} for path in row["files"]]}
            for row in manifest_rows
        ],
        "environment": environment_metadata(),
    }
    write_json_exclusive(result_root / "figures" / "figure_manifest.json", payload)
    print(f"status=success figures={len(manifest_rows)} pdf_png_files={2*len(manifest_rows)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
