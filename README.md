# TokenizerPrivacyReport

## 项目简介

本仓库对应现代密码学课程研究《面向分词器成员推断的站点级差分隐私与同态安全聚合方法》。项目在固定版本的 C4 网站语料上复现五类 Tokenizer 成员推断攻击，实现 Plain BPE、Min-count、Local-DP-BPE 与协议匹配基线，并评估融合站点级差分隐私和 Paillier 加法同态聚合的 SA-DP-BPE。下游效用使用 AG News 四分类任务，密码学实现使用真实 2048-bit Paillier 参数。

Tokenizer-MIA 官方实现以 Git submodule 保存在 `third_party/Tokenizer-MIA/`，固定提交为 `eeb0d83b34dd13f203bf578814463d0654295798`；本项目代码位于 `src/`，不直接修改第三方源码。

课程提交专用公开仓库为：<https://github.com/t12138ang/TokenizerPrivacyReport>。公开仓库由白名单生成，不包含原始语料、模型检查点、运行日志或作者个人准备材料。

完整克隆仓库及固定版本的上游代码：

```bash
git clone --recurse-submodules https://github.com/t12138ang/TokenizerPrivacyReport.git
cd TokenizerPrivacyReport
git submodule status -- third_party/Tokenizer-MIA
```

对于已经克隆的工作区，初始化第三方 submodule：

```bash
git submodule update --init --recursive
```

## 目录结构

```text
TokenizerPrivacyReport/
├── configs/                    # 实验配置
├── data/                       # 已跟踪的划分清单与验证记录
├── docs/                       # 环境、上游仓库、数据和协议说明
├── paper/                      # 课程报告的精简结果表
├── paper_jcr/                  # 《密码学报》模板版论文源码
├── results/                    # 已公开的汇总结果、图表源数据和主方法元数据
├── scripts/                    # 分阶段运行、检查和构建入口
├── src/                        # 攻击、防御、下游与密码学实现
├── tests/                      # 单元测试
├── third_party/
│   └── Tokenizer-MIA/          # 固定提交的官方 Git submodule
├── .gitignore
├── .gitmodules
├── environment.yml
├── requirements.txt
└── README.md
```

- `data/` 中只提交划分 manifest 和验证记录；原始 C4、AG News 与缓存数据在运行后本地生成，不进入 Git。
- `logs/` 由运行脚本在本地生成，并由 `.gitignore` 排除。
- `paper_jcr/build/` 是论文编译时生成的本地目录，不进入 Git。
- `deliverables/` 中的课程提交文件单独提供，不进入公开代码仓库。

## 环境要求

- Windows 11 + Anaconda/Miniconda，或能够运行 Python 3.12 的 Linux 环境。
- Python 依赖由根目录 `requirements.txt` 给出；Conda 入口为 `environment.yml`。
- 论文需要 XeLaTeX。Windows 已验证 MiKTeX；源码同时提供 TeX Live/Overleaf 所需的相对文件结构。

Windows 上从空环境创建独立前缀：

```powershell
$env:CONDA_PKGS_DIRS = "$PWD\.conda\pkgs"
conda env create `
  --prefix "$PWD\.conda\envs\tokenizer-privacy-report" `
  --file .\environment.yml
```

检查环境和单元测试：

```powershell
& '.\.conda\envs\tokenizer-privacy-report\python.exe' -m unittest discover -s tests -v
```

更详细的版本、CUDA 验证和 Windows 注意事项见 [环境说明](docs/ENVIRONMENT.md)。

## 数据获取

- C4：`allenai/c4`，固定 revision `1588ec454efa1a09f29cd18ddd04fe05fc8653a2`。
- AG News：`fancyzhx/ag_news`，固定 revision `eb185aade064a813bc0b7f42de02595523103ca4`。
- 数据划分采用网站级严格互斥，并由三个固定随机种子生成。

下载和构建正式数据：

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\scripts\run_final_data.ps1
```

该命令实时输出阶段、进度与耗时，主日志为 `logs/final/data.log`。完成后查看：

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\scripts\check_final_data.ps1
Get-Content .\logs\final\data.log -Tail 40
```

## 一键复现入口

统一入口为：

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass `
  -File .\scripts\reproduce_report.ps1 -FiguresOnly
```

入口支持 `-Attacks`、`-Defense`、`-Downstream`、`-Crypto`、`-FiguresOnly` 和 `-PaperOnly`，可组合多个阶段。未提供开关时脚本只显示用法并退出，不会默认启动高耗时实验。统一入口显示当前阶段和累计耗时，将调度记录写入 `logs/reproduce_report_*.log`，并在每个阶段结束时显示结果检查命令。

## 攻击复现

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\scripts\reproduce_report.ps1 -Attacks
```

运行时显示 3982 个任务的进度、恢复状态和耗时；日志为 `logs/final/attacks.log`。结果查看：

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\scripts\check_attacks.ps1
Get-Content .\logs\final\check_attacks.log -Tail 80
```

## 防御训练

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\scripts\reproduce_report.ps1 -Defense
```

入口依次执行开发集筛选、配置选择和独立主评估，显示任务计数、耗时和恢复信息；日志为 `logs/final/defenses.log`、`logs/final/defenses_main.log` 和 `logs/final/defenses_wrapper.log`。结果查看：

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\scripts\check_defenses.ps1 -Stage All
Get-Content .\logs\final\check_defenses.log -Tail 80
```

## 下游任务

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\scripts\reproduce_report.ps1 -Downstream
```

AG News 训练逐 epoch 显示进度、验证指标和累计耗时；日志为 `logs/final/downstream_main.log` 与 `logs/final/downstream_wrapper.log`。结果查看：

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\scripts\check_downstream.ps1 -Stage Main
Get-Content .\logs\final\downstream_main.log -Tail 60
```

## Paillier 开销实验

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\scripts\reproduce_report.ps1 -Crypto
```

脚本显示参数组合、预热/正式重复、耗时和内存，日志为 `logs/final/crypto_bench.log` 与 `logs/final/crypto_wrapper.log`。结果查看：

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\scripts\check_crypto_bench.ps1
Get-Content .\logs\final\check_crypto_bench.log -Tail 80
```

## 论文图表生成

仅使用已有正式结果重新生成图表，不启动训练或攻击：

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\scripts\reproduce_report.ps1 -FiguresOnly
```

生成过程显示阶段和耗时，日志为 `logs/reproduce_report_*.log`；输出位于 `paper_jcr/figures/`、`paper_jcr/tables/` 和 `paper_jcr/generated/`。查看命令：

```powershell
Get-ChildItem .\paper_jcr\figures -Filter '*.pdf'
Get-ChildItem .\paper_jcr\tables, .\paper_jcr\generated -Filter '*.tex'
```

仅编译论文：

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\scripts\reproduce_report.ps1 -PaperOnly
```

论文构建显示三遍 XeLaTeX 进度和总耗时，日志在运行后生成于 `paper_jcr/build/build.log`，结果为 `paper_jcr/main.pdf`。[论文源码说明](paper_jcr/README.md)另列直接 XeLaTeX、PowerShell、Bash、latexmk 和 Overleaf 编译方式。

## 已公开结果与本地运行产物

- `results/final/tables/`：论文使用的汇总表。
- `results/final/figures/source_data/`：图表对应的源数据。
- `results/final/defenses/main/tokenizers/`：已选主要防御配置的元数据。
- `paper/tables/main_methods_summary.csv`：主要方法的精简结果表。
- `paper_jcr/`：论文源码及由记录结果生成的图表。

完整运行会在本地生成攻击状态、模型检查点、下游训练产物、Paillier 单元结果和日志；这些大体积或机器相关文件按 `.gitignore` 规则不进入公开仓库。结果文件采用不覆盖写入和可恢复状态记录。模型预测值在表格与图中单独标记，不作为真实测量。

## 随机种子

正式研究固定三个种子：

```text
20260726
20260727
20260728
```

数据划分使用 `20260726`；Python、NumPy、PyTorch 和 DataLoader 的随机性由配置及运行脚本统一设置。配置文件为 `configs/final_study.json` 和 `configs/downstream.json`。

## 预计运行时间

以下为当前 12 逻辑 CPU、15.89 GiB 内存和 GTX 1050 Ti 4 GiB 机器上的既有运行记录，用于估算而非性能承诺：

| 阶段 | 已记录耗时 |
|---|---:|
| 五类攻击，3982 项 | 约 6.8 小时 |
| 防御开发集，508 项 | 约 3.6 小时 |
| 防御主评估，117 项 | 约 3.5 小时 |
| AG News 开发集，36 项 | 约 9.8 小时（当前调用累计） |
| AG News 主评估，24 项 | 约 9.6 小时（当前调用累计） |
| Paillier 24 组实测 | 约 8.6 小时 |

数据下载受网络和 Hugging Face 缓存影响。完整复现通常需要数十小时；图表生成和论文编译通常为分钟级。各入口支持复用已完成结果或从状态文件恢复，但运行前仍应检查日志和剩余磁盘。

## 硬件要求

- 攻击、分词器防御和 Paillier 基准主要使用 CPU；建议至少 8 个逻辑核心和 16 GiB 内存。
- AG News 两层 Transformer 支持 CPU 回退；建议使用至少 4 GiB 显存的 CUDA GPU。当前 GTX 1050 Ti 4 GiB 已完成所报告任务。
- 正式配置要求运行前至少保留 40 GiB 磁盘空间。若复现官方仓库原始的大规模影子分词器流程，磁盘和内存需求明显更高，详见 [Tokenizer-MIA 上游仓库说明](docs/UPSTREAM_TOKENIZER_MIA.md)。

## 许可证与引用

本仓库用于课程报告的代码与结果复现。仓库目前未设置统一开源许可证；第三方代码与依赖分别遵循其原始许可证。
