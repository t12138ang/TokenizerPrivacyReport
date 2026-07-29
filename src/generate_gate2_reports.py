"""Generate Chinese Gate 2 reports strictly from validated machine outputs."""

from __future__ import annotations

import argparse
import csv
import statistics
from collections import defaultdict
from pathlib import Path
from typing import Any

from src.utils.run_metadata import (
    PROJECT_ROOT,
    strict_json_load,
    write_json_exclusive,
    write_text_exclusive,
)


def fmt(value: float) -> str:
    return f"{float(value):.6f}"


def mib(value: int | float) -> str:
    return f"{float(value) / 1024 / 1024:.2f} MiB"


def counter(payload: dict[str, Any], name: str) -> int:
    return int(payload.get(name, 0))


def artifact_directory_bytes(metadata_path: Path) -> int:
    """Return the actual size of one immutable tokenizer artifact directory."""
    if not metadata_path.is_file():
        raise FileNotFoundError(f"missing tokenizer metadata: {metadata_path}")
    files = [path for path in metadata_path.parent.iterdir() if path.is_file()]
    if not files:
        raise RuntimeError(f"empty tokenizer artifact directory: {metadata_path.parent}")
    return sum(path.stat().st_size for path in files)


def resource_extrapolation(
    *,
    profiles: list[dict[str, str]],
    summary: dict[str, Any],
    observed_shadow_count: int,
    target_shadow_count: int,
) -> dict[str, Any]:
    """Linearly extrapolate only the components that scale with shadow count.

    This intentionally leaves target-tokenizer work, Compression Rate, bootstrap,
    and orchestration in the fixed component.  The whole measured Vocabulary
    Overlap elapsed time is treated as scalable, making the wall-clock estimate
    conservative for its fixed bootstrap component.
    """
    if observed_shadow_count <= 0 or target_shadow_count < observed_shadow_count:
        raise ValueError("invalid shadow-count extrapolation")
    scale_factor = target_shadow_count / observed_shadow_count
    target_rows = [row for row in profiles if row["role"] == "target"]
    shadow_rows = [row for row in profiles if row["role"] == "shadow"]
    if not target_rows or not shadow_rows:
        raise RuntimeError("target and shadow resource rows are both required")

    target_seconds = sum(float(row["elapsed_seconds"]) for row in target_rows)
    shadow_seconds = sum(float(row["elapsed_seconds"]) for row in shadow_rows)
    vocabulary_overlap_seconds = sum(
        float(row["elapsed_seconds"])
        for row in summary["rows"]
        if row["attack"] == "vocabulary_overlap"
    )
    pipeline_seconds = float(summary["run_state"]["accumulated_elapsed_seconds"])
    scalable_seconds = shadow_seconds + vocabulary_overlap_seconds
    fixed_seconds = pipeline_seconds - scalable_seconds
    if fixed_seconds < 0:
        raise RuntimeError("measured scalable time exceeds total pipeline time")

    def artifact_bytes(rows: list[dict[str, str]]) -> int:
        return sum(
            artifact_directory_bytes(PROJECT_ROOT / row["metadata_path"])
            for row in rows
        )

    target_artifact_bytes = artifact_bytes(target_rows)
    shadow_artifact_bytes = artifact_bytes(shadow_rows)
    estimated_tokenizer_seconds = target_seconds + shadow_seconds * scale_factor
    estimated_pipeline_seconds = fixed_seconds + scalable_seconds * scale_factor
    estimated_artifact_bytes = target_artifact_bytes + shadow_artifact_bytes * scale_factor
    peak_memory_bytes = int(summary["run_state"]["peak_memory_bytes"])

    return {
        "schema_version": 1,
        "status": "estimate_from_gate2_measurements",
        "scope": "same Gate 2 matrix with only shadow_count changed",
        "observed_shadow_count": observed_shadow_count,
        "target_shadow_count": target_shadow_count,
        "linear_scale_factor": scale_factor,
        "observed": {
            "target_tokenizer_seconds": target_seconds,
            "shadow_tokenizer_seconds": shadow_seconds,
            "vocabulary_overlap_seconds": vocabulary_overlap_seconds,
            "fixed_pipeline_seconds": fixed_seconds,
            "pipeline_seconds": pipeline_seconds,
            "target_tokenizer_artifact_bytes": target_artifact_bytes,
            "shadow_tokenizer_artifact_bytes": shadow_artifact_bytes,
            "sequential_peak_memory_bytes": peak_memory_bytes,
        },
        "estimated_for_target_shadow_count": {
            "tokenizer_processing_seconds": estimated_tokenizer_seconds,
            "pipeline_seconds": estimated_pipeline_seconds,
            "tokenizer_artifact_bytes": int(round(estimated_artifact_bytes)),
            "sequential_peak_memory_reference_bytes": peak_memory_bytes,
        },
        "assumptions": [
            "target tokenizer, Compression Rate, bootstrap, and orchestration remain fixed",
            "shadow tokenizer work and the full Vocabulary Overlap time scale linearly",
            "tokenizers remain sequential; parallel-worker memory is not estimated",
            "this is a one-point engineering extrapolation, not a measured 96-shadow result",
        ],
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path, required=True)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    config = strict_json_load(args.config.resolve())
    data_config = strict_json_load(PROJECT_ROOT / "configs/gate2_data.json")
    validation = strict_json_load(PROJECT_ROOT / "data/gate2/validation.json")
    collection = strict_json_load(PROJECT_ROOT / "data/gate2/corpus/collection_stats.json")
    summary = strict_json_load(PROJECT_ROOT / config["results_root"] / "summary.json")
    if validation.get("status") != "success" or summary.get("status") != "success":
        raise RuntimeError("validated Gate 2 data and successful attack summary are required")

    manifest_lines = []
    for protocol in ("paper_aligned", "strict_disjoint"):
        for seed in summary["seeds"]:
            manifest_hash = validation["protocols"][protocol][str(seed)]["manifest_sha256"]
            manifest_lines.append(f"- `{protocol}` / `{seed}`：`{manifest_hash}`")
    data_report = f"""# Gate 2 自然语料报告

本报告由 `src/generate_gate2_reports.py` 从不可变清单和验证 JSON 自动生成，未手工填写实验数值。

## 固定数据源与边界

- 数据集：`{validation['dataset_id']}`，配置 `en`，训练切分，固定 revision `{validation['dataset_revision']}`（https://huggingface.co/datasets/allenai/c4/commit/{validation['dataset_revision']}）。
- 两遍流式扫描的第一遍实际检查 `{collection['pass1']['scanned_records']}` 条记录；配置硬上限为 `{data_config['max_stream_records']}`。
- 最终保留站点 `{validation['site_count']}` 个、文本 `{validation['text_count']}` 条；每站最少/最多 `{validation['site_text_counts']['minimum']}/{validation['site_text_counts']['maximum']}` 条。
- 语料 SHA-256：`{validation['corpus_sha256']}`。
- 文本 UTF-8 总量：{mib(validation['text_byte_length']['total'])}；字符长度中位数/p95：{validation['text_char_length']['median']:.1f}/{validation['text_char_length']['p95']:.1f}。

## 去重与质量验证

- 完整 URL 字段数：{validation['forbidden_full_url_field_count']}；正文 URL 次数：{validation['url_in_text_count']}；站点 ID 出现在正文的次数：{validation['site_id_in_text_count']}。
- 保留语料的精确重复（站内/站间）为 {counter(validation['duplicate_and_anomaly_counts'], 'exact_duplicate_within')}/{counter(validation['duplicate_and_anomaly_counts'], 'exact_duplicate_cross')}；规范化重复（站内/站间）为 {counter(validation['duplicate_and_anomaly_counts'], 'normalized_duplicate_within')}/{counter(validation['duplicate_and_anomaly_counts'], 'normalized_duplicate_cross')}；验证错误数为 {len(validation['errors'])}。
- 第一遍丢弃：过短 {counter(collection['pass1']['statistics'], 'too_short')}、过长 {counter(collection['pass1']['statistics'], 'too_long')}、语言异常 {counter(collection['pass1']['statistics'], 'language_anomaly')}、编码异常 {counter(collection['pass1']['statistics'], 'encoding_anomaly')}、URL/源站标记 {counter(collection['pass1']['statistics'], 'url_or_source_marker')}、记录异常 {counter(collection['pass1']['statistics'], 'record_exceptions')}。
- 目标评估集合固定为 128 个站点（成员/非成员 64/64），另有 64 个额外辅助站点。
- `paper_aligned` 使用共享 192 站点宇宙；`strict_disjoint` 的 64 站点辅助池与 128 个目标评估站点严格不交叉，目标站点仅作为显式 shadow in/out 探针。

## 可复现性说明

三个种子为 {', '.join(map(str, summary['seeds']))}。所有训练只按 manifest 的站点 ID 从同一份 JSONL 流式读取，没有复制成员文本。原始 hostname 与 URL 不写入公开语料，站点仅以 SHA-256 派生标识出现。

本阶段最初将“每站最多 100”误设为“必须 100”，随后依据原始要求改为显式最低 5、最高 100；两次未产出语料的受控终止完整保留在 `logs/gate2/`，最终成功数据没有覆盖先前结果。固定 manifest 内容哈希如下：

{chr(10).join(manifest_lines)}
"""

    grouped: dict[tuple[str, str, int, str], list[dict[str, Any]]] = defaultdict(list)
    for row in summary["rows"]:
        grouped[(row["protocol"], row["attack"], int(row["vocab_size"]), row["method_id"])].append(row)
    attack_lines = [
        "# Gate 2 攻击结果报告",
        "",
        "本报告仅汇总本地 108 个真实结果 JSON。分数方向在运行前固定为 `higher_is_more_member`，没有按标签翻转分数。置信区间来自 1000 次按网站类别分层的 percentile bootstrap。",
        "",
        "| 协议 | 攻击 | 词表 | 方法 | AUC（均值±SD） | BA | TPR@FPR≤1% | AP |",
        "|---|---|---:|---|---:|---:|---:|---:|",
    ]
    for key in sorted(grouped):
        protocol, attack, vocab, method = key
        values = grouped[key]
        means = {
            metric: sum(float(row[metric]) for row in values) / len(values)
            for metric in ("roc_auc", "balanced_accuracy", "tpr_at_fpr_le_0_01", "average_precision")
        }
        deviations = {
            metric: statistics.stdev(float(row[metric]) for row in values)
            for metric in ("roc_auc", "balanced_accuracy", "tpr_at_fpr_le_0_01", "average_precision")
        }
        attack_lines.append(
            f"| {protocol} | {attack} | {vocab} | {method} | {fmt(means['roc_auc'])}±{fmt(deviations['roc_auc'])} | "
            f"{fmt(means['balanced_accuracy'])}±{fmt(deviations['balanced_accuracy'])} | "
            f"{fmt(means['tpr_at_fpr_le_0_01'])}±{fmt(deviations['tpr_at_fpr_le_0_01'])} | "
            f"{fmt(means['average_precision'])}±{fmt(deviations['average_precision'])} |"
        )
    attack_lines.extend(
        [
            "",
            "上表均值仅用于浏览，不替代逐运行结果。",
            "",
            "## 逐运行真实指标",
            "",
            "下表 108 行直接来自逐运行结果 JSON，没有从论文表格补数。四项指标的 95% 区间保存在 `results/gate2/summary.csv`；ROC 数组、128 个站点标签与分数保存在对应的 `results/gate2/runs/attacks/...` JSON。",
            "",
            "| 协议 | 种子 | 词表请求/实际 | 方法 | 攻击 | AUC | BA | TPR@FPR≤1% | AP |",
            "|---|---:|---:|---|---|---:|---:|---:|---:|",
        ]
    )
    for row in sorted(
        summary["rows"],
        key=lambda item: (
            item["protocol"],
            int(item["seed"]),
            int(item["vocab_size"]),
            item["method_id"],
            item["attack"],
        ),
    ):
        attack_lines.append(
            f"| {row['protocol']} | {row['seed']} | {row['vocab_size']}/{row['actual_vocab_size']} | "
            f"{row['method_id']} | {row['attack']} | {fmt(row['roc_auc'])} | "
            f"{fmt(row['balanced_accuracy'])} | {fmt(row['tpr_at_fpr_le_0_01'])} | "
            f"{fmt(row['average_precision'])} |"
        )

    profile_path = PROJECT_ROOT / config["results_root"] / "resource_profile.csv"
    with profile_path.open("r", encoding="utf-8-sig", newline="") as handle:
        profiles = list(csv.DictReader(handle))
    max_profile_peak = max(int(row["peak_memory_bytes"]) for row in profiles)
    total_training_seconds = sum(float(row["elapsed_seconds"]) for row in profiles)
    run_state = summary["run_state"]
    extrapolation = resource_extrapolation(
        profiles=profiles,
        summary=summary,
        observed_shadow_count=int(config["shadow_count"]),
        target_shadow_count=96,
    )
    observed = extrapolation["observed"]
    estimated = extrapolation["estimated_for_target_shadow_count"]
    resource_report = f"""# Gate 2 资源报告

本报告由实际 checkpoint 元数据自动生成。

- 完成状态：`{run_state['status']}`；完成任务 `{run_state['completed_tasks']}/{run_state['expected_tasks']}`。
- Tokenizer 资源记录：{len(profiles)}；其中最大词表真实训练 54 次，其余由 merge 顺序截断并按需执行 Min-count。
- 各 Tokenizer 记录耗时之和：{total_training_seconds:.3f} 秒（不是并行墙钟时间）。
- Tokenizer 元数据记录的最大进程峰值内存：{mib(max_profile_peak)}。
- 总管线累计墙钟时间：{run_state['accumulated_elapsed_seconds']:.3f} 秒；总管线峰值内存：{mib(run_state['peak_memory_bytes'])}。
- 硬停止阈值：{config['max_elapsed_seconds']} 秒与 {mib(config['max_peak_memory_bytes'])}。停止原因：`{run_state.get('stop_reason')}`。
- 逻辑 CPU 与线程数、训练文本/字节数、artifact SHA-256 均逐条保存在 `results/gate2/resource_profile.csv`。

GPU 未被 Tokenizers、Compression Rate 或 Vocabulary Overlap 使用；本阶段为 CPU 工作负载。

## 从 8 个影子 Tokenizer 外推到 96 个

这是工程预算估算，不是实测结果，也不是论文全量配置。估算只把同一 Gate 2 矩阵的影子数量从 {extrapolation['observed_shadow_count']} 改为 {extrapolation['target_shadow_count']}，线性倍率为 {extrapolation['linear_scale_factor']:.1f}；数据量、词表规模、协议、种子和方法均保持 Gate 2 不变。

- 实测目标 Tokenizer 处理耗时：{observed['target_tokenizer_seconds']:.3f} 秒；实测影子 Tokenizer 处理耗时：{observed['shadow_tokenizer_seconds']:.3f} 秒。
- 96-shadow Tokenizer 串行处理估算：{estimated['tokenizer_processing_seconds']:.3f} 秒（{estimated['tokenizer_processing_seconds'] / 3600:.2f} 小时）。
- 将全部 Vocabulary Overlap 实测耗时也按影子数线性放大后，总攻击管线估算：{estimated['pipeline_seconds']:.3f} 秒（{estimated['pipeline_seconds'] / 3600:.2f} 小时）。这对其固定 bootstrap 成分偏保守。
- 当前目标/影子 Tokenizer 工件实测大小：{mib(observed['target_tokenizer_artifact_bytes'])}/{mib(observed['shadow_tokenizer_artifact_bytes'])}；96-shadow 工件估算：{mib(estimated['tokenizer_artifact_bytes'])}。
- 串行执行不把内存乘以 12；只能把本次实测峰值 {mib(estimated['sequential_peak_memory_reference_bytes'])} 作为参考，不能据此保证 96-shadow 峰值。若并行训练，内存必须另行实测。

完整公式、分量和假设保存于 `results/gate2/resource_extrapolation_96.json`。单点线性外推不包含更大 C4 语料、论文 80k--200k 词表或其它攻击的成本，因此不能当作全量复现预算。
"""

    difference_report = f"""# 论文、官方代码与 Gate 2 协议差异

论文版本为 arXiv v4（https://arxiv.org/html/2510.05699）。论文 §4.2 Algorithm 1 后的说明和 §5.1 `Tokenizer Training` 均明确使用 96 个 shadow Tokenizer；论文 Table 5 另列 1/32/64/96/128 的训练成本，128 是资源消融点，不是主攻击设置。

本项目固定的官方代码 commit `eeb0d83b34dd13f203bf578814463d0654295798` 在 `third_party/Tokenizer-MIA/mia_via_vocabulary_overlap.py:57` 设置 `shadow_num = 128`，并在第 67 行循环加载 128 个 shadow Tokenizer。因此“论文 96”和“当前官方 commit 128”均属实，但位置与语义不同。

## 保持一致的核心定义

- Compression Rate 使用每站点 UTF-8 字节数除以目标 Tokenizer token 数，高分固定解释为更可能是成员。
- Vocabulary Overlap 对每个被测站点按 8 个 shadow 的 in/out 关系分组，排除同时出现在 in/out 并集的 token，再计算与目标词表的 Jaccard 差分。
- Min-count 遵循官方防御的核心规则：先以训练语料上的 token 出现次数计数，再做训练后过滤。

## 有意缩小或加固的部分

- 论文主实验使用 96 个 shadow，当前官方 commit 的 Vocabulary Overlap 脚本使用 128 个；Gate 2 因用户规定的 6 小时/12 GiB 门禁固定为 8 个 shadow 和 2k/4k/8k 词表，只用于自然语料中规模 pilot。
- 官方目标 Tokenizer 各词表分别训练、shadow 从最大词表按 merge 顺序截断；Gate 2 对目标和 shadow 都统一采用一次 8k 训练后确定性截断，以降低重复计算，并记录完整派生哈希。
- 官方 Min-count 示例仅处理目标 Tokenizer 且阈值固定为 48；Gate 2 对目标与全部 shadow 对称应用阈值 16、64，以便攻击比较保持同分布。
- Gate 2 保留五个特殊 token；官方 Min-count 过滤器只显式保留 `[UNK]`。这会影响实际词表大小，逐运行元数据已记录。
- 官方脚本依赖目录名充当网站身份并含未固定 shuffle；Gate 2 使用固定 revision、哈希站点 ID、不可变 manifest 与三个显式种子。
- `paper_aligned` 保留共享站点宇宙的论文代码风格；`strict_disjoint` 额外给出辅助池严格不交叉版本。两种协议必须分开解读，不能混合汇总为单一实验结论。
- 官方仅报告 AUC、BA、低 FPR TPR；Gate 2 另报告 AP 和四项指标的网站级 95% bootstrap 区间。

本阶段没有运行 Merge Similarity、Naive Bayes、Frequency Estimation、Signal F，也没有实现或运行 Paillier、LDP、L1 截断、下游分类或全量实验。

由于数据量、每站文本数、词表规模、shadow 数、划分协议、Min-count 对称应用方式均不同，Gate 2 数值不能与论文全量表格作直接数值比较，也不能作为论文结论的复现确认。
"""

    write_text_exclusive(PROJECT_ROOT / "docs/GATE2_DATA_REPORT.md", data_report)
    write_text_exclusive(PROJECT_ROOT / "docs/GATE2_ATTACK_REPORT.md", "\n".join(attack_lines))
    write_text_exclusive(PROJECT_ROOT / "docs/GATE2_RESOURCE_REPORT.md", resource_report)
    write_text_exclusive(PROJECT_ROOT / "docs/PAPER_CODE_PROTOCOL_DIFFERENCES.md", difference_report)
    write_json_exclusive(
        PROJECT_ROOT / config["results_root"] / "resource_extrapolation_96.json",
        extrapolation,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
