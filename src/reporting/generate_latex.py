"""Generate manuscript macros and ten LaTeX tables from final CSV/JSON outputs."""

from __future__ import annotations

import argparse
import csv
import statistics
from pathlib import Path
from typing import Any

from src.reporting.collect_results import write_csv_exclusive
from src.utils.run_metadata import (
    PROJECT_ROOT,
    sha256_file,
    strict_json_load,
    utc_now,
    write_json_exclusive,
    write_text_exclusive,
)


class Raw(str):
    """A small marker for generated LaTeX fragments rather than user/data text."""


def rows(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def esc(value: Any) -> str:
    if isinstance(value, Raw):
        return str(value)
    mapping = {
        "\\": r"\textbackslash{}", "_": r"\_", "%": r"\%", "&": r"\&",
        "#": r"\#", "{": r"\{", "}": r"\}",
    }
    return "".join(mapping.get(character, character) for character in str(value))


def method_name(value: str) -> str:
    names = {
        "plain_bpe": "Plain BPE", "min_count_2": "Min-count 2", "min_count_5": "Min-count 5",
        "min_count_10": "Min-count 10", "he_only_reference": "HE-only",
    }
    return names.get(value, value)


def tex_table(headers: list[str], body: list[list[Any]], *, column_spec: str | None = None) -> str:
    spec = column_spec or ("l" + "r" * (len(headers) - 1))
    lines = [r"\begin{tabular}{" + spec + "}", r"\toprule", " & ".join(map(esc, headers)) + r" \\", r"\midrule"]
    lines.extend(" & ".join(esc(value) for value in row) + r" \\" for row in body)
    lines.extend([r"\bottomrule", r"\end{tabular}", ""])
    return "\n".join(lines)


def mean_std(values: list[float], digits: int = 4) -> Raw:
    return Raw(f"{statistics.fmean(values):.{digits}f} $\\pm$ {statistics.stdev(values):.{digits}f}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--fast-report", action="store_true")
    args = parser.parse_args()
    config = strict_json_load(args.config.resolve())
    result_root = PROJECT_ROOT / config["results_root"]
    table_root = result_root / "tables"
    generated = PROJECT_ROOT / "paper" / "generated"
    presentation_tables = PROJECT_ROOT / "paper" / "tables"
    if generated.exists() and any(generated.iterdir()):
        raise FileExistsError(f"generated LaTeX directory is not empty: {generated}")
    generated.mkdir(parents=True, exist_ok=True)
    if presentation_tables.exists() and any(presentation_tables.iterdir()):
        raise FileExistsError(f"paper table directory is not empty: {presentation_tables}")
    presentation_tables.mkdir(parents=True, exist_ok=True)
    outputs: list[Path] = []

    selection = strict_json_load(result_root / "defenses" / "main_selection.json")
    data = strict_json_load(PROJECT_ROOT / "data" / "final" / "validation.json")
    attack = rows(table_root / "attack_results.csv")
    attack_agg = rows(table_root / "attack_aggregates.csv")
    utility = rows(table_root / "tokenizer_utility.csv")
    downstream = rows(table_root / "downstream_results.csv")
    downstream_agg = rows(table_root / "downstream_aggregates.csv")
    crypto = rows(table_root / "crypto_benchmark.csv")
    full_crypto_path = table_root / "full_tokenizer_crypto.csv"
    full_crypto = rows(full_crypto_path)[0] if full_crypto_path.exists() else None
    paired = rows(table_root / "paired_auc_differences.csv")
    dataset_rows = rows(table_root / "dataset_statistics.csv")
    ablation = rows(result_root / "figures" / "source_data" / "figure_12.csv")
    selected_sa = [row for row in selection["selected"] if row["mode"] == "sa_dp"]
    strongest_plain = max(
        (row for row in attack_agg if row["method_id"] == "plain_bpe"),
        key=lambda row: float(row["roc_auc_mean"]),
    )
    primary_sa = next(
        row for row in selected_sa if row.get("report_role") == "sa_dp_primary_tradeoff"
    )
    plain_downstream = next(row for row in downstream_agg if row["method_id"] == "plain_bpe")
    primary_downstream = next(row for row in downstream_agg if row["method_id"] == primary_sa["id"])
    primary_auc = statistics.fmean(float(row["roc_auc"]) for row in attack if row["method_id"] == primary_sa["id"])
    plain_auc = statistics.fmean(float(row["roc_auc"]) for row in attack if row["method_id"] == "plain_bpe")
    local_method = next(row["method_id"] for row in downstream_agg if row["method_id"].startswith("local_dp_"))
    local_auc = statistics.fmean(float(row["roc_auc"]) for row in attack if row["method_id"] == local_method)
    he_auc = statistics.fmean(float(row["roc_auc"]) for row in attack if row["method_id"] == "he_only_reference")
    local_downstream = next(row for row in downstream_agg if row["method_id"] == local_method)
    he_downstream = next(row for row in downstream_agg if row["method_id"] == "he_only_reference")
    primary_utility = [row for row in utility if row["method_id"] == primary_sa["id"] and row["source_corpus"] == "c4_heldout"]
    plain_utility = [row for row in utility if row["method_id"] == "plain_bpe" and row["source_corpus"] == "c4_heldout"]
    macro_lines = [
        r"% Automatically generated from results/final; do not edit numerical values by hand.",
        rf"\newcommand{{\FinalCfourRevision}}{{\texttt{{{esc(data['dataset_revision'][:12])}}}}}",
        rf"\newcommand{{\FinalCorpusSites}}{{{data['site_count']}}}",
        rf"\newcommand{{\FinalCorpusTexts}}{{{data['text_count']}}}",
        rf"\newcommand{{\FinalCorpusBytes}}{{{data['text_byte_length']['total']}}}",
        rf"\newcommand{{\MainTargetSites}}{{{data['protocols']['main'][str(config['seeds'][0])]['target_site_count']}}}",
        rf"\newcommand{{\StrongestPlainAttack}}{{{esc(strongest_plain['attack'])}}}",
        rf"\newcommand{{\StrongestPlainAUC}}{{{float(strongest_plain['roc_auc_mean']):.4f}}}",
        rf"\newcommand{{\PrimarySAMethod}}{{\texttt{{{esc(primary_sa['id'])}}}}}",
        rf"\newcommand{{\PrimaryEpsilon}}{{{float(primary_sa['epsilon_total']):g}}}",
        rf"\newcommand{{\PrimaryClipPercentile}}{{{int(primary_sa['clipping_percentile'])}}}",
        rf"\newcommand{{\PrimaryBatchSize}}{{{int(primary_sa['batch_merge_size'])}}}",
        rf"\newcommand{{\PrimaryCandidateK}}{{{int(primary_sa['candidate_pool_size'])}}}",
        rf"\newcommand{{\PlainMeanAttackAUC}}{{{plain_auc:.4f}}}",
        rf"\newcommand{{\PrimaryMeanAttackAUC}}{{{primary_auc:.4f}}}",
        rf"\newcommand{{\PrimaryAttackAUCDelta}}{{{primary_auc - plain_auc:+.4f}}}",
        rf"\newcommand{{\LocalMeanAttackAUC}}{{{local_auc:.4f}}}",
        rf"\newcommand{{\HEMeanAttackAUC}}{{{he_auc:.4f}}}",
        rf"\newcommand{{\PlainMacroFOne}}{{{float(plain_downstream['macro_f1_mean']):.4f}}}",
        rf"\newcommand{{\PrimaryMacroFOne}}{{{float(primary_downstream['macro_f1_mean']):.4f}}}",
        rf"\newcommand{{\PrimaryMacroFOneDelta}}{{{float(primary_downstream['macro_f1_mean']) - float(plain_downstream['macro_f1_mean']):+.4f}}}",
        rf"\newcommand{{\LocalMacroFOne}}{{{float(local_downstream['macro_f1_mean']):.4f}}}",
        rf"\newcommand{{\HEMacroFOne}}{{{float(he_downstream['macro_f1_mean']):.4f}}}",
        rf"\newcommand{{\PlainAccuracy}}{{{float(plain_downstream['accuracy_mean']):.4f}}}",
        rf"\newcommand{{\PrimaryAccuracy}}{{{float(primary_downstream['accuracy_mean']):.4f}}}",
        rf"\newcommand{{\PlainBytesPerToken}}{{{statistics.fmean(float(row['bytes_per_token']) for row in plain_utility):.4f}}}",
        rf"\newcommand{{\PrimaryBytesPerToken}}{{{statistics.fmean(float(row['bytes_per_token']) for row in primary_utility):.4f}}}",
        rf"\newcommand{{\PrimaryBytesPerTokenChangePercent}}{{{100.0 * (statistics.fmean(float(row['bytes_per_token']) for row in primary_utility) / statistics.fmean(float(row['bytes_per_token']) for row in plain_utility) - 1.0):+.2f}\%}}",
    ]
    if full_crypto is not None:
        macro_lines.extend([
            rf"\newcommand{{\FullCryptoSeconds}}{{{float(full_crypto['actual_elapsed_seconds']):.3f}}}",
            rf"\newcommand{{\FullCryptoRatio}}{{{float(full_crypto['actual_to_cleartext_time_ratio']):.2f}}}",
        ])
    if args.fast_report:
        fast_state = strict_json_load(result_root / "report_fast" / "fast_crypto_state.json")
        models = strict_json_load(result_root / "report_fast" / "crypto_scaling_models.json")
        total_block = models["metrics"]["round_total_seconds"]
        total_model = next(row for row in total_block["models"] if row["model_name"] == total_block["selected_model"])
        combined = rows(result_root / "report_fast" / "crypto_combined.csv")
        largest = next(row for row in combined if row["clients"] == "64" and row["K"] == "4096" and row["metric"] == "round_total_seconds")
        macro_lines.extend([
            rf"\newcommand{{\MeasuredCryptoCells}}{{{fast_state['measured_complete_cells']}}}",
            rf"\newcommand{{\ExpectedCryptoCells}}{{{fast_state['expected_cells']}}}",
            rf"\newcommand{{\ExtrapolatedCryptoCells}}{{{fast_state['extrapolated_cells']}}}",
            rf"\newcommand{{\CryptoTotalModel}}{{{esc(total_block['selected_model'])}}}",
            rf"\newcommand{{\CryptoTotalModelRtwo}}{{{float(total_model['r_squared']):.4f}}}",
            rf"\newcommand{{\CryptoTotalModelLOOMAPE}}{{{float(total_model['loocv_mape_percent']):.2f}\%}}",
            rf"\newcommand{{\CryptoLargestEstimate}}{{{float(largest['value']):.2f}}}",
            rf"\newcommand{{\CryptoLargestLower}}{{{float(largest['lower_bound']):.2f}}}",
            rf"\newcommand{{\CryptoLargestUpper}}{{{float(largest['upper_bound']):.2f}}}",
        ])
    macro_lines.append("")
    macros = "\n".join(macro_lines)
    path = generated / "results_macros.tex"
    write_text_exclusive(path, macros)
    outputs.append(path)

    tables: list[tuple[str, str]] = []
    tables.append(("table_symbols.tex", tex_table(
        ["符号", "含义"],
        [[Raw("$P_i$"), "第 i 个站点客户端"], [Raw("$A$"), "无私钥的聚合服务器"],
         [Raw("$D$"), "持私钥的解密与选择服务器"], [Raw("$C_r$"), "第 r 轮站点级 L1 截断上界"],
         [Raw("$K$"), "公开候选词对维度"], [Raw("$b$"), "每轮最大兼容合并数"],
         [Raw("$R$"), "批量 BPE 实际轮数"], [Raw("$\\varepsilon_r$"), "第 r 轮隐私预算"]],
        column_spec="lp{0.70\\linewidth}",
    )))
    tables.append(("table_parties.tex", tex_table(
        ["实体", "可见信息", "不可见/不保证"],
        [[Raw("客户端 $P_i$"), "自身站点文本、公开候选、Paillier 公钥", "其他站点明文"],
         [Raw("服务器 $A$"), "单客户端密文、加密噪声", "私钥、明文频率"],
         [Raw("服务器 $D$"), "噪声后聚合、top-b 结果", "单客户端密文、独立噪声值"],
         ["外部攻击者 $M$", "发布 Tokenizer、候选网站文本", "训练期私有统计"]],
        column_spec="lp{0.31\\linewidth}p{0.35\\linewidth}",
    )))
    tables.append(("table_dataset.tex", tex_table(
        ["规模", "种子", "目标", "成员", "非成员", "影子辅助", "公开候选"],
        [[row["scale"], row["seed"], row["target_site_count"], row["member_site_count"],
          row["nonmember_site_count"], row["shadow_auxiliary_site_count"], row["public_candidate_site_count"]]
         for row in dataset_rows],
    )))
    plain_rows = [row for row in attack_agg if row["method_id"] == "plain_bpe"]
    tables.append(("table_attack_results.tex", tex_table(
        ["攻击", "ROC AUC", "AP", "Balanced Acc.", "TPR@1%", "TPR@0.1%"],
        [[row["attack"], Raw(f"{float(row['roc_auc_mean']):.4f} $\\pm$ {float(row['roc_auc_sample_std']):.4f}"),
          f"{float(row['average_precision_mean']):.4f}", f"{float(row['balanced_accuracy_mean']):.4f}",
          f"{float(row['tpr_at_fpr_le_0_01_mean']):.4f}", f"{float(row['tpr_at_fpr_le_0_001_mean']):.4f}"]
         for row in plain_rows],
    )))
    methods = sorted({row["method_id"] for row in attack})
    defense_body = []
    for method in methods:
        values = [float(row["roc_auc"]) for row in attack if row["method_id"] == method]
        paired_values = [float(row["candidate_minus_plain_auc"]) for row in paired if row["method_id"] == method]
        defense_body.append([method_name(method), f"{statistics.fmean(values):.4f}",
                             "--" if method == "plain_bpe" else f"{statistics.fmean(paired_values):+.4f}"])
    tables.append(("table_defense_results.tex", tex_table(["方法", "五攻击平均 AUC", "相对 Plain 的配对 AUC 差"], defense_body)))
    utility_body = []
    for method in methods:
        selected = [row for row in utility if row["method_id"] == method and row["source_corpus"] == "c4_heldout"]
        utility_body.append([method_name(method), mean_std([float(row["bytes_per_token"]) for row in selected]),
                             mean_std([float(row["mean_tokens_per_document"]) for row in selected], 2),
                             f"{statistics.fmean(float(row['actual_vocab_size']) for row in selected):.0f}",
                             f"{statistics.fmean(float(row['tokenizer_training_seconds']) for row in selected):.2f}"])
    tables.append(("table_tokenizer_utility.tex", tex_table(
        ["方法", "bytes/token", "tokens/document", "实际词表", "训练秒"], utility_body)))
    downstream_body = [[method_name(row["method_id"]),
                        Raw(f"{float(row['accuracy_mean']):.4f} $\\pm$ {float(row['accuracy_sample_std']):.4f}"),
                        Raw(f"{float(row['macro_f1_mean']):.4f} $\\pm$ {float(row['macro_f1_sample_std']):.4f}"),
                        f"{float(row['training_seconds_mean']):.1f}"] for row in downstream_agg]
    tables.append(("table_downstream.tex", tex_table(["方法", "Accuracy", "Macro-F1", "平均训练秒"], downstream_body)))
    crypto_selected = [row for row in crypto if
                       (int(row["candidate_dimension"]) == 1024 and int(row["client_count"]) in {2, 16, 32, 64})
                       or (int(row["client_count"]) in {16, 32, 64} and int(row["candidate_dimension"]) == 4096)]
    tables.append(("table_crypto.tex", tex_table(
        ["来源", "clients", "$K$", "平均耗时/s", Raw("95\\% 区间/s"), "上行/MiB"],
        [["M" if row.get("data_source", "measured") == "measured" else Raw("E$^{*}$"),
          row["client_count"], row["candidate_dimension"], f"{float(row['round_mean_seconds']):.3f}",
          Raw(f"[{float(row['round_ci95_lower_seconds']):.3f}, {float(row['round_ci95_upper_seconds']):.3f}]"),
          f"{float(row['total_client_upstream_bytes']) / (1024**2):.2f}"]
         for row in crypto_selected])))
    tables.append(("table_ablation.tex", tex_table(
        ["$b$", "平均攻击 AUC", "bytes/token"],
        [[row["batch_merge_size"], f"{float(row['mean_attack_auc']):.4f}", f"{float(row['bytes_per_token_mean']):.4f}"] for row in ablation])))
    tables.append(("table_security_scope.tex", tex_table(
        ["目标/威胁", "当前覆盖", "边界"],
        [["单客户端上传机密性", "Paillier 语义安全", "诚实但好奇，未覆盖恶意密文"],
         ["发布后站点成员隐私", "站点级纯 DP", "依赖正确截断与基本组合"],
         ["A 与 D 非共谋", "当前原型假设", "共谋可恢复未扰动聚合"],
         ["恶意客户端", "未覆盖", "需范围证明/鲁棒聚合"],
         ["流量分析与主动篡改", "未覆盖", "需认证信道及侧信道防护"]],
        column_spec="lp{0.30\\linewidth}p{0.40\\linewidth}",
    )))
    main_summary_rows = []
    for method in methods:
        method_attack = [float(row["roc_auc"]) for row in attack if row["method_id"] == method]
        method_utility = [float(row["bytes_per_token"]) for row in utility if row["method_id"] == method and row["source_corpus"] == "c4_heldout"]
        method_downstream = next(row for row in downstream_agg if row["method_id"] == method)
        main_summary_rows.append([
            method_name(method), f"{statistics.fmean(method_attack):.4f}", f"{statistics.fmean(method_utility):.4f}",
            f"{float(method_downstream['accuracy_mean']):.4f}", f"{float(method_downstream['macro_f1_mean']):.4f}",
        ])
    tables.append(("table_main_summary.tex", tex_table(
        ["方法", "五攻击平均 AUC", "bytes/token", "Accuracy", "Macro-F1"], main_summary_rows)))
    for filename, content in tables:
        path = generated / filename
        write_text_exclusive(path, content)
        outputs.append(path)
    expected_table_count = 11
    if len(tables) != expected_table_count:
        raise AssertionError(f"exactly {expected_table_count} generated manuscript tables are required")
    write_csv_exclusive(presentation_tables / "main_methods_summary.csv", [
        {"method": row[0], "mean_attack_auc": row[1], "bytes_per_token": row[2], "accuracy": row[3], "macro_f1": row[4]}
        for row in main_summary_rows
    ])
    write_csv_exclusive(presentation_tables / "crypto_measured_extrapolated.csv", crypto)
    write_json_exclusive(result_root / "tables" / "latex_generation_manifest.json", {
        "schema_version": 1, "status": "success", "generated_at_utc": utc_now(),
        "macro_file": str(outputs[0].relative_to(PROJECT_ROOT)).replace("\\", "/"),
        "table_count": len(tables),
        "outputs": [{"path": str(path.relative_to(PROJECT_ROOT)).replace("\\", "/"), "sha256": sha256_file(path)} for path in outputs],
        "sources": {
            str(path.relative_to(PROJECT_ROOT)).replace("\\", "/"): sha256_file(path)
            for path in sorted(table_root.glob("*.csv"))
        },
    })
    print(f"status=success macros=1 latex_tables={len(tables)} presentation_csv_tables=2")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
