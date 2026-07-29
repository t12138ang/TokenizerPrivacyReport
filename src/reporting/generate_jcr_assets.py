"""Generate JCR-paper assets from recorded experiment summaries.

The generator reads completed experiment summaries without modifying them.
Reported values are copied or deterministically derived, such as the
direction-adapted AUC.
"""

from __future__ import annotations

import csv
import json
import re
from pathlib import Path

import matplotlib.pyplot as plt
from matplotlib.patches import FancyArrowPatch, FancyBboxPatch
from matplotlib.ticker import NullFormatter, NullLocator, ScalarFormatter


ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "paper_jcr"
GENERATED = OUT / "generated"
TABLES = OUT / "tables"
FIGURES = OUT / "figures"
FORMAL = ROOT / "results" / "final"

METHOD_LABELS = {
    "plain_bpe": "Plain BPE",
    "Plain BPE": "Plain BPE",
    "he_only_reference": "协议匹配无 DP 基线",
    "HE-only": "协议匹配无 DP 基线",
    "local_dp_eps16p0_C75_b32_K1024": "Local-DP-BPE（ε=16，第75百分位截断）",
    "sa_dp_eps8p0_C75_b32_K1024": "SA-DP-BPE（ε=8，第75百分位截断）",
    "sa_dp_eps4p0_C50_b32_K1024": "SA-DP-BPE（ε=4，第50百分位截断）",
    "Min-count 2": "Min-count BPE（阈值2）",
    "Min-count 5": "Min-count BPE（阈值5）",
    "Min-count 10": "Min-count BPE（阈值10）",
}


def ensure_dirs() -> None:
    for path in (GENERATED, TABLES, FIGURES):
        path.mkdir(parents=True, exist_ok=True)


def tex_escape(value: str) -> str:
    return (
        value.replace("\\", r"\textbackslash{}")
        .replace("&", r"\&")
        .replace("%", r"\%")
        .replace("_", r"\_")
        .replace("#", r"\#")
    )


def generate_privacy_accounting() -> None:
    methods = [
        "local_dp_eps16p0_C75_b32_K1024",
        "sa_dp_eps8p0_C75_b32_K1024",
        "sa_dp_eps4p0_C50_b32_K1024",
    ]
    rows: list[dict[str, object]] = []
    for method in methods:
        for seed in (20260726, 20260727, 20260728):
            source = (
                FORMAL
                / "defenses"
                / "main"
                / "tokenizers"
                / method
                / str(seed)
                / "metadata.json"
            )
            data = json.loads(source.read_text(encoding="utf-8"))
            rounds = data["rounds"]
            epsilons = {float(item["epsilon_round"]) for item in rounds}
            if len(epsilons) != 1:
                raise RuntimeError(f"non-uniform per-round epsilon: {source}")
            bounds = [int(item["clipping_bound"]) for item in rounds]
            accountant = data["privacy_accountant"]
            if accountant["accountant"] != "basic_sequential_composition":
                raise RuntimeError(f"unexpected accountant: {source}")
            rows.append(
                {
                    "method_id": method,
                    "method_display": METHOD_LABELS[method],
                    "seed": seed,
                    "adjacency": data["adjacency"],
                    "epsilon_total_requested": data["epsilon_total_requested"],
                    "epsilon_total_accounted": accountant["epsilon_total"],
                    "delta": accountant["delta"],
                    "planned_rounds": data["planned_rounds"],
                    "actual_rounds": data["actual_rounds"],
                    "epsilon_per_round": next(iter(epsilons)),
                    "clipping_percentile": data["clipping_percentile"],
                    "clipping_bound_min": min(bounds),
                    "clipping_bound_max": max(bounds),
                    "candidate_pool_size_K": data["candidate_pool_size"],
                    "batch_merge_size_b": data["batch_size"],
                    "actual_merge_count": data["merge_count"],
                    "actual_vocab_size": data["actual_vocab_size"],
                    "early_stop": data["actual_rounds"] < data["planned_rounds"],
                    "budget_allocation": "uniform_over_planned_rounds",
                    "dp_mechanism": data["dp_mechanism"],
                    "training_aggregation_execution": data["aggregation_execution"],
                    "source": source.relative_to(ROOT).as_posix(),
                }
            )

    csv_path = GENERATED / "privacy_accounting.csv"
    with csv_path.open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)

    lines = [
        r"\begin{tabular}{lrrrrrr}",
        r"\toprule",
        r"方法与种子 & $\varepsilon$ & $R$ & $\varepsilon_r$ & $C_r$范围 & merges & 提前停止 \\",
        r"\midrule",
    ]
    for row in rows:
        short = str(row["method_display"]).replace("（协议匹配、无DP）", "")
        if short.startswith("Local-DP"):
            short = "Local-DP（$\\varepsilon=16$）"
        elif "ε=8" in short:
            short = "SA-DP-BPE（$\\varepsilon=8$）"
        else:
            short = "SA-DP-BPE（$\\varepsilon=4$）"
        lines.append(
            f"{short}，{row['seed']} & {float(row['epsilon_total_requested']):g} & "
            f"{row['actual_rounds']} & {float(row['epsilon_per_round']):.6f} & "
            f"[{row['clipping_bound_min']},{row['clipping_bound_max']}] & "
            f"{row['actual_merge_count']} & 否 \\\\"
        )
    lines.extend(
        [
            r"\bottomrule",
            r"\end{tabular}",
        ]
    )
    (TABLES / "privacy_accounting.tex").write_text("\n".join(lines) + "\n", encoding="utf-8")


def read_method_summary() -> list[dict[str, str]]:
    source = ROOT / "paper" / "tables" / "main_methods_summary.csv"
    with source.open(encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def generate_human_tables() -> None:
    rows = read_method_summary()
    include = {
        "Plain BPE",
        "HE-only",
        "local_dp_eps16p0_C75_b32_K1024",
        "sa_dp_eps8p0_C75_b32_K1024",
        "sa_dp_eps4p0_C50_b32_K1024",
    }
    selected = [row for row in rows if row["method"] in include]
    ordered_names = [
        "Plain BPE",
        "HE-only",
        "local_dp_eps16p0_C75_b32_K1024",
        "sa_dp_eps8p0_C75_b32_K1024",
        "sa_dp_eps4p0_C50_b32_K1024",
    ]
    selected.sort(key=lambda row: ordered_names.index(row["method"]))

    attack_source = FORMAL / "tables" / "attack_aggregates.csv"
    with attack_source.open(encoding="utf-8-sig", newline="") as handle:
        attack_rows = list(csv.DictReader(handle))
    summary_id = {
        "Plain BPE": "plain_bpe",
        "HE-only": "he_only_reference",
        "local_dp_eps16p0_C75_b32_K1024": "local_dp_eps16p0_C75_b32_K1024",
        "sa_dp_eps8p0_C75_b32_K1024": "sa_dp_eps8p0_C75_b32_K1024",
        "sa_dp_eps4p0_C50_b32_K1024": "sa_dp_eps4p0_C50_b32_K1024",
    }
    attacks_by_method: dict[str, list[float]] = {}
    for item in attack_rows:
        attacks_by_method.setdefault(item["method_id"], []).append(float(item["roc_auc_mean"]))

    lines = [
        r"\begin{tabular}{lrrrr}",
        r"\toprule",
        r"方法 & 平均原始AUC & 平均$\mathrm{AUC}_{\rm adapt}$ & $D_{\rm AUC}$ & Macro-F1 \\",
        r"\midrule",
    ]
    derived_rows: list[dict[str, object]] = []
    for row in selected:
        method_id = summary_id[row["method"]]
        attack_aucs = attacks_by_method[method_id]
        if len(attack_aucs) != 5:
            raise RuntimeError(f"expected five attacks for {method_id}, got {len(attack_aucs)}")
        auc = sum(attack_aucs) / len(attack_aucs)
        adapt = sum(max(value, 1.0 - value) for value in attack_aucs) / len(attack_aucs)
        auc_deviation = sum(2.0 * abs(value - 0.5) for value in attack_aucs) / len(attack_aucs)
        concise_labels = {
            "Plain BPE": "Plain BPE",
            "HE-only": "协议匹配无 DP 基线",
            "local_dp_eps16p0_C75_b32_K1024": r"Local-DP-BPE（$\varepsilon=16$）",
            "sa_dp_eps8p0_C75_b32_K1024": r"SA-DP-BPE（$\varepsilon=8$）",
            "sa_dp_eps4p0_C50_b32_K1024": r"SA-DP-BPE（$\varepsilon=4$）",
        }
        label = concise_labels[row["method"]]
        lines.append(
            f"{label} & {auc:.4f} & {adapt:.4f} & {auc_deviation:.4f} & "
            f"{float(row['macro_f1']):.4f} \\\\"
        )
        derived_rows.append(
            {
                "method_id": method_id,
                "method_display": METHOD_LABELS[row["method"]],
                "attack_count": len(attack_aucs),
                "mean_raw_auc_measured": auc,
                "mean_direction_adapted_auc_derived": adapt,
                "mean_auc_deviation_derived": auc_deviation,
                "macro_f1_measured": float(row["macro_f1"]),
                "derivation": "mean_j max(AUC_j,1-AUC_j); D_AUC=mean_j 2*abs(AUC_j-0.5)",
                "source": "固定主评估攻击汇总与方法效用汇总",
            }
        )
    lines.extend(
        [
            r"\bottomrule",
            r"\end{tabular}",
        ]
    )
    (TABLES / "main_comparison_jcr.tex").write_text("\n".join(lines) + "\n", encoding="utf-8")
    with (GENERATED / "adaptive_auc.csv").open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(derived_rows[0]))
        writer.writeheader()
        writer.writerows(derived_rows)

    macro_names = {
        "plain_bpe": "Plain",
        "he_only_reference": "ProtocolBaseline",
        "local_dp_eps16p0_C75_b32_K1024": "Local",
        "sa_dp_eps8p0_C75_b32_K1024": "Primary",
        "sa_dp_eps4p0_C50_b32_K1024": "Secondary",
    }
    macro_lines = [
        "% Generated from recorded experiment summaries.",
        "% AUC_adapt = mean_j max(AUC_j,1-AUC_j); D_AUC = mean_j 2|AUC_j-0.5|.",
    ]
    for item in derived_rows:
        stem = macro_names[str(item["method_id"])]
        macro_lines.extend(
            [
                f"\\newcommand{{\\{stem}AdaptedAUC}}{{{float(item['mean_direction_adapted_auc_derived']):.4f}}}",
                f"\\newcommand{{\\{stem}AUCDeviation}}{{{float(item['mean_auc_deviation_derived']):.4f}}}",
            ]
        )
    (GENERATED / "adaptive_auc.tex").write_text("\n".join(macro_lines) + "\n", encoding="utf-8")

    complexity = r"""\setlength{\tabcolsep}{3pt}
\begin{tabular}{p{0.20\linewidth}p{0.14\linewidth}p{0.24\linewidth}p{0.24\linewidth}}
\toprule
阶段 & 执行方 & 计算复杂度 & 通信复杂度 \\
\midrule
本地截断与编码 & 客户端 & $O(K)$ & 无 \\
Paillier 加密 & 客户端 & $O(K)$ 次加密 & $K$ 个密文 \\
密文聚合 & 聚合服务器 $A$ & $O(NK)$ 次模乘 & 无额外客户端通信 \\
噪声采样与加密 & 聚合服务器 $A$ & $O(K)$ 次采样和加密 & 无 \\
聚合密文发送 & $A\rightarrow D$ & 无 & $K$ 个密文 \\
解密 & 解密服务器 $D$ & $O(K)$ 次解密 & 无 \\
top-$b$ 选择 & 解密服务器 $D$ & 排序实现为 $O(K\log K)$ & $b$ 个 merge IDs \\
\bottomrule
\end{tabular}
\par\smallskip\footnotesize 来源：协议的循环与排序路径；$N$ 为客户端数，$K$ 为候选维度。KeyGen 是每个密钥对的一次性离线操作，表中聚焦在线阶段。
"""
    (TABLES / "participant_costs.tex").write_text(complexity, encoding="utf-8")

    attack_labels = {
        "compression_rate": "压缩率",
        "frequency_estimation": "频率估计",
        "merge_similarity": "合并相似度",
        "naive_bayes": "朴素贝叶斯",
        "vocabulary_overlap": "词表重叠",
    }
    with (FORMAL / "tables" / "attack_aggregates.csv").open(encoding="utf-8-sig", newline="") as handle:
        attack_rows = [row for row in csv.DictReader(handle) if row["method_id"] == "plain_bpe"]
    attack_lines = [
        r"\begin{tabular}{lrrrrr}",
        r"\toprule",
        r"攻击 & ROC AUC & AP & 平衡准确率 & TPR@1\% & TPR@0.1\% \\",
        r"\midrule",
    ]
    for row in attack_rows:
        attack_lines.append(
            f"{attack_labels[row['attack']]} & {float(row['roc_auc_mean']):.4f} $\\pm$ "
            f"{float(row['roc_auc_sample_std']):.4f} & {float(row['average_precision_mean']):.4f} & "
            f"{float(row['balanced_accuracy_mean']):.4f} & "
            f"{float(row['tpr_at_fpr_le_0_01_mean']):.4f} & "
            f"{float(row['tpr_at_fpr_le_0_001_mean']):.4f} \\\\"
        )
    attack_lines.extend([r"\bottomrule", r"\end{tabular}"])
    (TABLES / "attack_results_jcr.tex").write_text("\n".join(attack_lines) + "\n", encoding="utf-8")

    dataset_path = FORMAL / "tables" / "dataset_statistics.csv"
    with dataset_path.open(encoding="utf-8-sig", newline="") as handle:
        dataset_rows = list(csv.DictReader(handle))
    dataset_lines = [
        r"\begin{tabular}{lrrrrrr}",
        r"\toprule",
        r"规模 & 种子 & 目标 & 成员 & 非成员 & 影子辅助 & 公开候选 \\",
        r"\midrule",
    ]
    scale_labels = {"development": "开发集", "main": "主评估集"}
    for item in dataset_rows:
        dataset_lines.append(
            f"{scale_labels[item['scale']]} & {item['seed']} & {item['target_site_count']} & "
            f"{item['member_site_count']} & {item['nonmember_site_count']} & "
            f"{item['shadow_auxiliary_site_count']} & {item['public_candidate_site_count']} \\\\"
        )
    dataset_lines.extend([r"\bottomrule", r"\end{tabular}"])
    (GENERATED / "table_dataset.tex").write_text("\n".join(dataset_lines) + "\n", encoding="utf-8")

    with (FORMAL / "tables" / "crypto_benchmark.csv").open(encoding="utf-8-sig", newline="") as handle:
        crypto_rows = list(csv.DictReader(handle))
    representative = {(2, 1024), (16, 1024), (16, 4096), (32, 1024), (32, 4096), (64, 1024), (64, 4096)}
    chosen = [
        item for item in crypto_rows
        if (int(item["client_count"]), int(item["candidate_dimension"])) in representative
    ]
    chosen.sort(key=lambda item: (int(item["client_count"]), int(item["candidate_dimension"])))
    crypto_lines = [
        r"\begin{tabular}{lrrrrr}",
        r"\toprule",
        r"来源 & 客户端数 & $K$ & 平均耗时/s & 95\% 区间/s & 上行/MiB \\",
        r"\midrule",
    ]
    for item in chosen:
        source_label = "实测" if item["data_source"] == "measured" else "模型预测"
        crypto_lines.append(
            f"{source_label} & {item['client_count']} & {item['candidate_dimension']} & "
            f"{float(item['round_mean_seconds']):.3f} & "
            f"[{float(item['round_ci95_lower_seconds']):.3f}, {float(item['round_ci95_upper_seconds']):.3f}] & "
            f"{float(item['total_client_upstream_bytes']) / (1024**2):.2f} \\\\"
        )
    crypto_lines.extend([r"\bottomrule", r"\end{tabular}"])
    (GENERATED / "table_crypto.tex").write_text("\n".join(crypto_lines) + "\n", encoding="utf-8")


def generate_references() -> None:
    target = GENERATED / "references.tex"
    if not target.exists():
        raise RuntimeError(f"missing curated bibliography: {target}")
    count = len(re.findall(r"\\bibitem\{", target.read_text(encoding="utf-8")))
    if count < 30:
        raise RuntimeError(f"curated bibliography contains only {count} entries")


def rounded_box(ax, xy, width, height, text, face, edge="#334155", fontsize=13.2):
    box = FancyBboxPatch(
        xy,
        width,
        height,
        boxstyle="round,pad=0.018,rounding_size=0.025",
        linewidth=1.2,
        edgecolor=edge,
        facecolor=face,
    )
    ax.add_patch(box)
    ax.text(xy[0] + width / 2, xy[1] + height / 2, text, ha="center", va="center", fontsize=fontsize)


def arrow(ax, start, end, color="#475569", style="-|>"):
    ax.add_patch(FancyArrowPatch(start, end, arrowstyle=style, mutation_scale=13, lw=1.3, color=color))


def generate_protocol_figure() -> None:
    plt.rcParams.update(
        {
            "font.sans-serif": ["Microsoft YaHei", "SimHei", "DejaVu Sans"],
            "axes.unicode_minus": False,
            "pdf.fonttype": 42,
        }
    )
    fig, ax = plt.subplots(figsize=(13.5, 6.5))
    ax.set_xlim(0, 1)
    ax.set_ylim(0, 1)
    ax.axis("off")

    public, plaintext, ciphertext, secret, output = "#dbeafe", "#dcfce7", "#ede9fe", "#fee2e2", "#fef3c7"
    rounded_box(ax, (0.37, 0.82), 0.26, 0.12, "解密服务器 D\n① (pk, sk) ← KeyGen(1^κ)\n仅 D 持有 sk", secret)
    rounded_box(ax, (0.04, 0.57), 0.25, 0.17, "站点客户端 P_i\n② 接收 pk 与公共候选 Q_r\n③ 截断 xbar_i,r 后上传\nE_pk(xbar_i,r)", ciphertext)
    rounded_box(ax, (0.38, 0.48), 0.27, 0.25, "聚合服务器 A\n② 接收 pk（无 sk）\n④ 密文逐坐标聚合\n⑤ 采样双边几何噪声 z_r\n⑥ 加密 z_r 并同态加入", ciphertext)
    rounded_box(ax, (0.72, 0.55), 0.25, 0.18, "解密服务器 D\n⑧ 解密带噪聚合\n⑧ 兼容 top-b 选择\n仅保留 merge IDs", plaintext)
    rounded_box(ax, (0.70, 0.22), 0.27, 0.15, "协调端 / 聚合服务器 A\n⑨ 仅接收 merge IDs\n更新公开 Tokenizer 状态", public)
    rounded_box(ax, (0.37, 0.08), 0.27, 0.14, "⑩ 最终发布 Tokenizer\n词表与有序 merge 列表\n受站点级 DP 约束", output)

    arrow(ax, (0.43, 0.82), (0.25, 0.74), "#2563eb")
    arrow(ax, (0.50, 0.82), (0.51, 0.73), "#2563eb")
    ax.text(0.30, 0.80, "pk（公共）", fontsize=11.5, color="#1d4ed8")
    arrow(ax, (0.29, 0.655), (0.38, 0.61), "#7c3aed")
    ax.text(0.30, 0.67, "单客户端密文", fontsize=11.5, color="#6d28d9")
    arrow(ax, (0.65, 0.61), (0.72, 0.64), "#7c3aed")
    ax.text(0.68, 0.77, "⑦ 密文带噪聚合\nE_pk(sum_i xbar_i,r + z_r)", fontsize=10.5, color="#6d28d9", ha="center")
    arrow(ax, (0.845, 0.55), (0.835, 0.37), "#2563eb")
    ax.text(0.85, 0.45, "merge IDs", fontsize=11.5, color="#1d4ed8", rotation=90, va="center")
    arrow(ax, (0.70, 0.27), (0.64, 0.18), "#2563eb")
    arrow(ax, (0.46, 0.48), (0.29, 0.40), "#2563eb")
    arrow(ax, (0.29, 0.40), (0.15, 0.57), "#2563eb")
    ax.text(0.14, 0.38, "下一轮 Q_(r+1) / Tokenizer\n（公共协议信息）", fontsize=11.5, color="#1d4ed8", ha="center")

    ax.text(0.04, 0.08, "图例", fontsize=12.5, weight="bold")
    legends = [(public, "公共信息"), (plaintext, "明文/带噪聚合"), (ciphertext, "密文"), (secret, "私钥"), (output, "发布输出")]
    for idx, (color, label) in enumerate(legends):
        x = 0.04 + idx * 0.125
        ax.add_patch(FancyBboxPatch((x, 0.025), 0.025, 0.025, boxstyle="round,pad=0.005", facecolor=color, edgecolor="#64748b"))
        ax.text(x + 0.032, 0.0375, label, va="center", fontsize=10.8)
    ax.text(0.98, 0.02, "HE 保护中间统计；DP 约束最终发布", ha="right", fontsize=12.5, weight="bold", color="#7c2d12")
    fig.tight_layout(pad=0.1)
    fig.savefig(FIGURES / "protocol_jcr.pdf", bbox_inches="tight", pad_inches=0.01)
    fig.savefig(FIGURES / "protocol_jcr.png", dpi=220, bbox_inches="tight", pad_inches=0.01)
    plt.close(fig)


def generate_attack_figures() -> None:
    """Regenerate the reader-facing attack figures with Chinese labels."""
    plt.rcParams.update(
        {
            "font.sans-serif": ["Microsoft YaHei", "SimHei", "DejaVu Sans"],
            "axes.unicode_minus": False,
            "pdf.fonttype": 42,
        }
    )
    fig, ax = plt.subplots(figsize=(8.4, 3.4))
    ax.set_xlim(-0.01, 0.95)
    ax.set_ylim(0.08, 0.82)
    ax.axis("off")
    nodes = {
        "目标分词器": (0.12, 0.70),
        "候选网站": (0.12, 0.30),
        "固定评分函数": (0.43, 0.50),
        "成员/非成员标签": (0.82, 0.20),
        "ROC / PR 指标": (0.82, 0.50),
    }
    for label, (x, y) in nodes.items():
        rounded_box(ax, (x - 0.105, y - 0.072), 0.21, 0.144, label, "#e7f0fa", fontsize=11.2)
    edges = [
        ("目标分词器", "固定评分函数", "查询分词器特征"),
        ("候选网站", "固定评分函数", "网站文本"),
        ("固定评分函数", "ROC / PR 指标", "成员得分"),
        ("成员/非成员标签", "ROC / PR 指标", "仅用于指标计算"),
    ]
    for left, right, edge_label in edges:
        x1, y1 = nodes[left]
        x2, y2 = nodes[right]
        if abs(x1 - x2) < 0.02:
            arrow(ax, (x1, y1 + 0.075), (x2, y2 - 0.075), "#334155")
            ax.text(x1 + 0.025, (y1 + y2) / 2, edge_label, fontsize=9.4, ha="left", va="center", backgroundcolor="white")
        else:
            arrow(ax, (x1 + 0.108, y1), (x2 - 0.108, y2), "#334155")
            offset = 0.09 if abs(y1 - y2) < 0.02 else 0.035
            ax.text((x1 + x2) / 2, (y1 + y2) / 2 + offset, edge_label, fontsize=9.4, ha="center", backgroundcolor="white")
    fig.tight_layout(pad=0.05)
    fig.savefig(FIGURES / "figure_02_attack_pipeline.pdf", bbox_inches="tight", pad_inches=0.02)
    fig.savefig(FIGURES / "figure_02_attack_pipeline.png", dpi=220, bbox_inches="tight")
    plt.close(fig)

    attack_labels = {
        "compression_rate": "压缩率",
        "vocabulary_overlap": "词表重叠",
        "frequency_estimation": "频率估计",
        "merge_similarity": "合并相似度",
        "naive_bayes": "朴素贝叶斯",
    }
    with (FORMAL / "figures" / "source_data" / "figure_03.csv").open(encoding="utf-8-sig", newline="") as handle:
        roc_rows = list(csv.DictReader(handle))
    fig, ax = plt.subplots(figsize=(6.4, 4.5))
    for attack_id, display in attack_labels.items():
        items = [item for item in roc_rows if item["attack"] == attack_id]
        fpr = [float(item["fpr"]) for item in items]
        mean = [float(item["mean_tpr"]) for item in items]
        std = [float(item["sample_std_tpr"]) for item in items]
        line = ax.plot(fpr, mean, label=display)[0]
        ax.fill_between(
            fpr,
            [max(0.0, value - spread) for value, spread in zip(mean, std)],
            [min(1.0, value + spread) for value, spread in zip(mean, std)],
            alpha=0.12,
            color=line.get_color(),
        )
    ax.plot([0, 1], [0, 1], "k--", lw=1, label="随机猜测")
    ax.set(xlabel="假阳性率", ylabel="真阳性率", xlim=(0, 1), ylim=(0, 1), title="Plain BPE 五类成员推断（主评估集，16k）")
    ax.grid(alpha=0.25)
    ax.legend(fontsize=8)
    fig.tight_layout()
    fig.savefig(FIGURES / "figure_03_plain_roc.pdf", bbox_inches="tight")
    fig.savefig(FIGURES / "figure_03_plain_roc.png", dpi=220, bbox_inches="tight")
    plt.close(fig)

    with (FORMAL / "figures" / "source_data" / "figure_04.csv").open(encoding="utf-8-sig", newline="") as handle:
        vocab_rows = list(csv.DictReader(handle))
    fig, ax = plt.subplots(figsize=(6.6, 4.5))
    for attack_id, display in attack_labels.items():
        items = sorted((item for item in vocab_rows if item["attack"] == attack_id), key=lambda item: int(item["vocab_size"]))
        ax.errorbar(
            [int(item["vocab_size"]) for item in items],
            [float(item["auc_mean"]) for item in items],
            yerr=[float(item["auc_sample_std"]) for item in items],
            marker="o",
            capsize=3,
            label=display,
        )
    x = [4000, 8000, 16000, 32000]
    ax.set_xticks(x)
    ax.set_xticklabels(["4k", "8k", "16k", "32k"])
    ax.minorticks_off()
    ax.xaxis.set_major_formatter(ScalarFormatter())
    ax.ticklabel_format(style="plain", axis="x", useOffset=False)
    ax.set_xticklabels(["4k", "8k", "16k", "32k"])
    ax.tick_params(axis="both", labelsize=9)
    ax.set_xlim(2500, 33500)
    ax.set(xlabel="目标词表规模", ylabel="ROC AUC", title="词表规模与攻击性能")
    ax.grid(alpha=0.25)
    ax.legend(fontsize=8, ncol=2, loc="best")
    fig.tight_layout()
    fig.savefig(FIGURES / "figure_04_vocab_auc.pdf", bbox_inches="tight")
    fig.savefig(FIGURES / "figure_04_vocab_auc.png", dpi=220, bbox_inches="tight")
    plt.close(fig)


def generate_crypto_figures() -> None:
    """Regenerate the two crypto panels with measured/predicted Chinese labels."""
    plt.rcParams.update(
        {
            "font.sans-serif": ["Microsoft YaHei", "SimHei", "DejaVu Sans"],
            "axes.unicode_minus": False,
            "pdf.fonttype": 42,
        }
    )
    with (FORMAL / "tables" / "crypto_benchmark.csv").open(encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.DictReader(handle))
    fig, ax = plt.subplots(figsize=(7.4, 4.8))
    colors = {2: "#2563eb", 8: "#f59e0b", 16: "#16a34a", 32: "#dc2626", 64: "#7c3aed"}
    for clients in (2, 8, 16, 32, 64):
        items = sorted((item for item in rows if int(item["client_count"]) == clients), key=lambda item: int(item["candidate_dimension"]))
        ax.plot(
            [int(item["candidate_dimension"]) for item in items],
            [float(item["round_mean_seconds"]) for item in items],
            lw=1.1,
            alpha=0.65,
            color=colors[clients],
        )
        measured = [item for item in items if item["data_source"] == "measured"]
        predicted = [item for item in items if item["data_source"] == "extrapolated"]
        if measured:
            ax.scatter(
                [int(item["candidate_dimension"]) for item in measured],
                [float(item["round_mean_seconds"]) for item in measured],
                marker="o", s=28, color=colors[clients], label=f"{clients} 客户端（实测）",
            )
        if predicted:
            ax.scatter(
                [int(item["candidate_dimension"]) for item in predicted],
                [float(item["round_mean_seconds"]) for item in predicted],
                marker="*", s=80, color=colors[clients], label=f"{clients} 客户端（模型预测）",
            )
    dimensions = [128, 512, 1024, 2048, 4096]
    ax.set_xscale("log")
    ax.set_yscale("log")
    ax.set_xticks(dimensions, labels=[str(value) for value in dimensions])
    ax.xaxis.set_minor_locator(NullLocator())
    ax.xaxis.set_minor_formatter(NullFormatter())
    ax.tick_params(axis="x", which="major", labelsize=8)
    ax.set(xlabel="候选维度 K", ylabel="平均单轮耗时（秒）", title="Paillier 总耗时随候选维度的变化")
    ax.grid(alpha=0.25)
    ax.legend(fontsize=8, ncol=2)
    fig.tight_layout()
    fig.savefig(FIGURES / "figure_17_crypto_total_vs_k_me.pdf", bbox_inches="tight")
    fig.savefig(FIGURES / "figure_17_crypto_total_vs_k_me.png", dpi=220, bbox_inches="tight")
    plt.close(fig)

    with (FORMAL / "figures" / "source_data" / "figure_19.csv").open(encoding="utf-8-sig", newline="") as handle:
        fit_rows = list(csv.DictReader(handle))
    measured = [float(item["measured_seconds"]) for item in fit_rows]
    fitted = [float(item["fitted_seconds"]) for item in fit_rows]
    limit = max(max(measured), max(fitted)) * 1.05
    fig, ax = plt.subplots(figsize=(5.4, 5.0))
    ax.scatter(measured, fitted, color="#e15759", label="实测配置")
    ax.plot([0, limit], [0, limit], "k--", lw=1, label="理想拟合")
    ax.set(
        xlabel="实测平均耗时（秒）",
        ylabel="模型拟合耗时（秒）",
        title=f"Paillier 总耗时的实测--拟合关系（{fit_rows[0]['model_name']}）",
        xlim=(0, limit),
        ylim=(0, limit),
    )
    ax.grid(alpha=0.25)
    ax.legend()
    fig.tight_layout()
    fig.savefig(FIGURES / "figure_19_crypto_measured_vs_fitted.pdf", bbox_inches="tight")
    fig.savefig(FIGURES / "figure_19_crypto_measured_vs_fitted.png", dpi=220, bbox_inches="tight")
    plt.close(fig)


def generate_result_figures() -> None:
    plt.rcParams.update(
        {
            "font.sans-serif": ["Microsoft YaHei", "SimHei", "DejaVu Sans"],
            "axes.unicode_minus": False,
            "pdf.fonttype": 42,
        }
    )
    selected_ids = [
        "plain_bpe",
        "he_only_reference",
        "local_dp_eps16p0_C75_b32_K1024",
        "sa_dp_eps8p0_C75_b32_K1024",
        "sa_dp_eps4p0_C50_b32_K1024",
    ]
    short_labels = ["Plain BPE", "协议匹配无 DP", "Local-DP-BPE\n（ε=16）", "SA-DP-BPE\n（ε=8）", "SA-DP-BPE\n（ε=4）"]
    attack_order = ["compression_rate", "vocabulary_overlap", "frequency_estimation", "merge_similarity", "naive_bayes"]
    attack_labels = ["压缩率", "词表重叠", "频率估计", "合并相似度", "朴素贝叶斯"]
    with (FORMAL / "tables" / "attack_aggregates.csv").open(encoding="utf-8-sig", newline="") as handle:
        source_rows = list(csv.DictReader(handle))
    lookup = {(row["method_id"], row["attack"]): float(row["roc_auc_mean"]) for row in source_rows}
    matrix = [[lookup[(method, attack)] for attack in attack_order] for method in selected_ids]

    adapted_matrix = [[max(value, 1.0 - value) for value in row] for row in matrix]
    fig, axes = plt.subplots(1, 2, figsize=(14.2, 4.9), gridspec_kw={"width_ratios": [1, 1]})
    for ax, values, title, limits in [
        (axes[0], matrix, "原始 ROC AUC（固定方向）", (0.45, 0.72)),
        (axes[1], adapted_matrix, "逐攻击方向适配 ROC AUC", (0.50, 0.70)),
    ]:
        image_obj = ax.imshow(values, cmap="YlGnBu", vmin=limits[0], vmax=limits[1], aspect="auto")
        ax.set_xticks(range(len(attack_labels)), attack_labels, rotation=20, ha="right", fontsize=8)
        ax.set_yticks(range(len(short_labels)), short_labels, fontsize=8)
        for i, value_row in enumerate(values):
            for j, value in enumerate(value_row):
                normalized = (value - limits[0]) / (limits[1] - limits[0])
                ax.text(j, i, f"{value:.3f}", ha="center", va="center", fontsize=8.3, color="white" if normalized > 0.55 else "#172554")
        ax.set_title(title)
        fig.colorbar(image_obj, ax=ax, pad=0.015, fraction=0.045)
    fig.tight_layout()
    fig.savefig(FIGURES / "defense_attack_risk_jcr.pdf", bbox_inches="tight")
    fig.savefig(FIGURES / "defense_attack_risk_jcr.png", dpi=220, bbox_inches="tight")
    plt.close(fig)

    summary_by_id = {}
    for row in read_method_summary():
        mapped = "he_only_reference" if row["method"] == "HE-only" else row["method"]
        mapped = "plain_bpe" if mapped == "Plain BPE" else mapped
        summary_by_id[mapped] = row
    bytes_per_token = [float(summary_by_id[method]["bytes_per_token"]) for method in selected_ids]
    macro_f1 = [float(summary_by_id[method]["macro_f1"]) for method in selected_ids]
    with (FORMAL / "figures" / "source_data" / "figure_14.csv").open(encoding="utf-8-sig", newline="") as handle:
        bpt_std = {row["method_id"]: float(row["bytes_per_token_sample_std"]) for row in csv.DictReader(handle)}
    with (FORMAL / "figures" / "source_data" / "figure_16.csv").open(encoding="utf-8-sig", newline="") as handle:
        f1_std = {row["method_id"]: float(row["sample_standard_deviation"]) for row in csv.DictReader(handle)}
    bytes_per_token_std = [bpt_std[method] for method in selected_ids]
    macro_f1_std = [f1_std[method] for method in selected_ids]
    fig, axes = plt.subplots(1, 2, figsize=(11.0, 4.5))
    colors = ["#64748b", "#8b5cf6", "#14b8a6", "#2563eb", "#f59e0b"]
    for ax, values, standard_deviations, title, ylabel, ylim in [
        (axes[0], bytes_per_token, bytes_per_token_std, "留出 C4 编码效率", "bytes/token（越高越好）", (4.18, 4.36)),
        (axes[1], macro_f1, macro_f1_std, "AG News 下游效用", "Macro-F1", (0.89, 0.912)),
    ]:
        positions = list(range(len(values)))
        for position, value, spread, color in zip(positions, values, standard_deviations, colors):
            ax.errorbar(
                position,
                value,
                yerr=spread,
                fmt="o",
                markersize=7,
                color=color,
                markeredgecolor="#334155",
                markeredgewidth=0.7,
                capsize=4,
                elinewidth=1.2,
                capthick=1.2,
            )
        ax.set_xticks(positions, short_labels, rotation=20, ha="right", fontsize=8)
        ax.set_ylim(*ylim)
        ax.set_ylabel(ylabel)
        ax.set_title(title)
        ax.grid(axis="y", alpha=0.25)
        for position, value, spread in zip(positions, values, standard_deviations):
            ax.text(position, value + spread + (ylim[1] - ylim[0]) * 0.012, f"{value:.4f}", ha="center", va="bottom", fontsize=8)
    fig.tight_layout()
    fig.savefig(FIGURES / "utility_downstream_jcr.pdf", bbox_inches="tight")
    fig.savefig(FIGURES / "utility_downstream_jcr.png", dpi=220, bbox_inches="tight")
    plt.close(fig)


def main() -> None:
    ensure_dirs()
    generate_privacy_accounting()
    generate_human_tables()
    generate_references()
    generate_protocol_figure()
    generate_attack_figures()
    generate_crypto_figures()
    generate_result_figures()


if __name__ == "__main__":
    main()
