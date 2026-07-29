"""Generate README_REPORT.md from machine-readable fast-report artifacts."""

from __future__ import annotations

import argparse
import csv
from pathlib import Path

from src.utils.run_metadata import PROJECT_ROOT, strict_json_load, write_text_exclusive


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path, required=True)
    args = parser.parse_args()
    config = strict_json_load(args.config.resolve())
    root = PROJECT_ROOT / config["results_root"] / "report_fast"
    state = strict_json_load(root / "fast_crypto_state.json")
    models = strict_json_load(root / "crypto_scaling_models.json")
    with (root / "crypto_extrapolated.csv").open("r", encoding="utf-8-sig", newline="") as handle:
        extrapolated = list(csv.DictReader(handle))
    total_rows = [row for row in extrapolated if row["metric"] == "round_total_seconds"]

    model_lines = []
    labels = {
        "encryption_seconds": "Encryption",
        "aggregation_seconds": "Aggregation",
        "noise_encryption_seconds": "Noise encryption",
        "decryption_seconds": "Decryption",
        "top_b_selection_seconds": "Selection",
        "round_total_seconds": "Total",
    }
    for metric, block in models["metrics"].items():
        selected = next(row for row in block["models"] if row["model_name"] == block["selected_model"])
        model_lines.append(
            f"| {labels[metric]} | {selected['model_name']} | {selected['formula']} | "
            f"{selected['r_squared']:.4f} | {selected['loocv_mae']:.4f} | "
            f"{selected['loocv_mape_percent']:.2f}% |"
        )
    missing_lines = "\n".join(
        f"- clients={row['clients']}, K={row['K']}: total {float(row['value']):.3f} s, "
        f"prediction interval [{float(row['lower_bound']):.3f}, {float(row['upper_bound']):.3f}] s "
        f"({row['model_name']}, E)"
        for row in total_rows
    )
    text = f"""# TokenizerPrivacyReport 课程报告交付说明

本文件由正式 JSON/CSV 自动生成。M 表示完整实测，E 表示基于 M 单元缩放模型的外推；E 不作为完整实测结果。

## 已完成阶段

- 正式成员推断：3982/3982，failures=0。
- Development 防御：508/508，failures=0；方法选择只读取 Development 验证结果。
- Main 防御：117/117，failures=0。
- Development AG News：36/36，failures=0。
- Main AG News：24/24，failures=0。
- Paillier-2048：{state['measured_complete_cells']}/{state['expected_cells']} 个参数单元完成完整实测，{state['extrapolated_cells']} 个单元透明外推。

Paillier 的每个 M 单元包含 5 次预热和 20 次正式重复。逐次解密结果与明文聚合完全一致；不完整的 clients=32、K=4096 运行只保留在原日志，未进入 M 数据和模型训练。

## Paillier 实测覆盖与缺失组合

实测覆盖 clients={{2,4,8,16}} 的全部 K={{128,512,1024,2048,4096}}，以及 clients=32、K={{128,512,1024,2048}}，共 {state['measured_complete_cells']} 个单元。缺失组合的总耗时估算如下：

{missing_lines}

详细逐次原始值、统计量和全部阶段的区间见：

- `results/final/report_fast/crypto_measured_raw.csv`
- `results/final/report_fast/crypto_measured_summary.csv`
- `results/final/report_fast/crypto_extrapolated.csv`
- `results/final/report_fast/crypto_combined.csv`

## 外推模型与误差

候选模型为 M1: `T=a+b*clients+c*K`、M2: `T=a+b*clients*K`、M3: `log(T)=a+b*log(clients)+c*log(K)`、M4: `T=a+b*K+c*clients*K`。每个阶段只用 {models['training_cell_count']} 个完整单元训练，以留一组合 MAE 为主；在最优 MAE 的 5% 内优先参数更少的模型。

| 阶段 | 选中模型 | 公式 | R² | LOOCV MAE/s | LOOCV MAPE |
|---|---|---|---:|---:|---:|
{chr(10).join(model_lines)}

预测区间使用 {models['bootstrap_iterations']} 次固定种子单元 bootstrap 加残差重采样，并扩宽到覆盖所有有效候选模型的点预测。完整系数、AIC/BIC、训练残差和候选模型比较见 `crypto_scaling_models.json`。

## 复现已测部分与快速报告

在项目根目录使用独立 Conda 环境：

```powershell
conda env create -f .\\environment.yml -p .\\.conda\\envs\\tokenizer-privacy-report
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\\scripts\\generate_fast_report.ps1
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\\scripts\\build_paper.ps1
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\\scripts\\check_fast_report.ps1
```

生成器拒绝覆盖现有结果。若需要从原始正式产物完全重建派生文件，应先把现有 `results/final/report_fast`、`results/final/tables`、`results/final/figures`、`paper/figures`、`paper/generated` 和 `paper/tables` 移到单独归档目录，再运行上述命令。不得删除 `results/final/crypto/cells` 或原始日志。

## 继续补跑完整密码学矩阵

当前 `benchmark_state.json` 保留 completed_cells={state['measured_complete_cells']}。确认没有另一条 benchmark 进程后，可执行：

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\\scripts\\run_crypto_bench.ps1
```

正式 runner 会保留已完成 cell JSON，从 clients=32、K=4096 重新开始完整单元，然后运行 clients=64 的五个组合。补跑完成后应重新生成标准 benchmark summary，并归档本次 E 版报告；不要把旧 E 行直接改名为 M。

## 论文与数据来源

- 中文论文：`paper/main.tex`、`paper/main.pdf`
- 图：`paper/figures/`；每图源 CSV 在 `results/final/figures/source_data/`
- 表：`paper/tables/` 与 `paper/generated/`
- 快速审计：`results/final/report_fast/FAST_REPORT_AUDIT.md`
- cutoff 与原始哈希：`logs/final/FAST_REPORT_CUTOFF.txt`、`results/final/report_fast/source_hashes.json`

论文中的 M/E 说明、模型误差与局限性是结论的一部分；不得在引用外推值时省略数据来源。
"""
    write_text_exclusive(PROJECT_ROOT / "README_REPORT.md", text)
    print(f"status=success measured_cells={state['measured_complete_cells']} extrapolated_cells={state['extrapolated_cells']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
