# Tokenizer-MIA 官方仓库审计

审计日期：2026-07-26（Asia/Shanghai）
审计对象：[mengtong0110/Tokenizer-MIA](https://github.com/mengtong0110/Tokenizer-MIA)
本地只读副本：`third_party/Tokenizer-MIA`
论文：[Membership Inference Attacks on Tokenizers of Large Language Models](https://arxiv.org/abs/2510.05699)

## 1. 版本与许可

- 审计 commit：`eeb0d83b34dd13f203bf578814463d0654295798`。
- 分支：`main`；该 commit 的提交时间为 2026-05-22 15:03:28 +0800，提交说明为 `Delete dp_defense directory`。
- 代码 License：Apache License 2.0，见官方仓库 `LICENSE`。
- 数据集 License：Hugging Face 的 [allenai/c4 数据卡](https://huggingface.co/datasets/allenai/c4)标注为 ODC-BY；网页原文仍可能带有各自权利和使用约束，全量下载前需再次做数据合规确认。
- 第三方目录在本阶段保持干净，`git status --porcelain` 无输出。没有修改任何官方源码。

## 2. Python 与依赖

官方 README 指定 `conda create -n MIA python=3.12`。官方 `requirements.txt` 内容如下：

| 包 | 固定版本 |
|---|---:|
| datasets | 2.21.0 |
| joblib | 1.4.2 |
| mpmath | 1.3.0 |
| numpy | 2.3.2 |
| powerlaw | 1.5 |
| scikit_learn | 1.7.1 |
| tokenizers | 0.21.4 |
| tqdm | 4.66.4 |

当前官方依赖在 Windows + Python 3.12.13 上成功安装、导入并通过 `pip check`，没有出现 Python 3.12 依赖冲突，因此本阶段没有建立降级兼容环境。

## 3. 数据来源与规模

`download_datasets.py` 通过 `datasets.load_dataset("allenai/c4", name="en", split="train", streaming=True)` 顺序读取最多 5,000,000 条 C4 英文训练样本，用 URL 的 `netloc` 聚合为网站 JSON，并只把至少有 200 个页面的网站复制到 `website_data`。

论文报告最终评估集包含 1,681,296 个网页、4,133 个网站，每个网站作为一个待判断的数据集；目标 Tokenizer 随机使用其中一半网站训练。论文数据规模说明见 [arXiv HTML 第 5.1 节](https://arxiv.org/html/2510.05699#S5.SS1)。

下载与磁盘估算（本阶段未下载）：

- [C4 English 文件目录](https://huggingface.co/datasets/allenai/c4/tree/main/en)当前总压缩大小约 327 GB，共 1,024 个训练分片，每片约 318–320 MB。
- C4 English 训练集约 364,868,892 行；按比例读取 5,000,000 行约对应 4.5 GB 压缩数据，考虑分片边界预计网络传输约 4–6 GB。
- 解压文本、`downloaded_data` 聚合副本及 `website_data` 二次复制预计占 15–30 GB；这是静态估计，不是本地测量。
- 更严重的磁盘项来自 `train_shadow_tokenizer.py`：128 个 `training_data_<iteration>.json` 都保存半数网站的完整训练文本。按每轮约数 GB 估算，仅这些副本就可能达到 200–500 GB；再加 640 个 Tokenizer JSON 和攻击缓存，建议按 0.5–1 TB SSD 规划。
- 下载脚本对同一网站的每条新样本都执行“读整个 JSON—追加—重写整个 JSON”，总 I/O 远大于净数据量，可能成为首要瓶颈。

## 4. 五种攻击脚本的输入与输出

所有最终结果都包含 `roc_auc`、`balanced_accuracy`、`tpr_at_low_fpr`、ROC 数组和成员/非成员明细。脚本以写模式打开固定结果名，会覆盖同名旧结果；全量阶段必须在 `src` 中重实现防覆盖逻辑。

| 攻击 | 主要输入 | 主要输出 | 影子/辅助需求与备注 |
|---|---|---|---|
| Compression Rate | `website_data/*.json`；5 个 `trained_tokenizer/target_tokenizer-<vocab>.json`；相应目标成员清单 | 每个词表一个 `infer_results/MIA via Compression Rate - v_size_<vocab>.json` | 无影子 Tokenizer；以 UTF-8 bytes/token 经 sigmoid 后作为分数，是最简单基线 |
| Vocabulary Overlap | 上述目标文件；每个词表 128 个影子 Tokenizer 和 128 个成员清单 | 每个词表一个 `MIA via Vocabulary Overlap - v_size_<vocab>.json` | 当前代码 `shadow_num=128`，用 `ProcessPoolExecutor(max_workers=7)` |
| Frequency Estimation | 网站数据、目标 Tokenizer；影子 Tokenizer `..._0.json`；前 10 轮影子成员清单；多组词频/词映射/幂律缓存 | 每个词表一个 `MIA via Signal F - v_size_<vocab>.json`，并在 `trained_tokenizer` 写大量中间 JSON | 实际训练使用 1 个影子 Tokenizer，使用 10 轮辅助数据采样信息；最终文件名与脚本名不一致 |
| Merge Similarity | 目标 Tokenizer；每个词表前 96 个影子 Tokenizer及其成员清单 | 每个词表一个 `MIA via Merge Similarity - v_size_<vocab>.json` | `range(96)`；Joblib `n_jobs=10` |
| Naive Bayes | 网站数据、目标 Tokenizer；前 10 轮影子成员清单；共享词频/词映射缓存 | 每个词表、每个 `k∈{60000,40000,20000}` 一个 `MIA via Naive Bayes - K_<k> - v_size_<vocab>.json`，合计 15 个最终文件 | 不加载影子 Tokenizer 本体，但依赖 10 轮辅助成员集合；对大量 token 做乘积计算 |

## 5. Tokenizer 数量

- 目标 Tokenizer：当前训练脚本为 80k、110k、140k、170k、200k 五种词表各训练一个，共 5 个。它对相同训练数据独立训练五次。
- 影子 Tokenizer：`train_shadow_tokenizer.py` 执行 128 轮；每轮先训练一个最大 200k 的 BPE，再截断生成五种词表，最终共 640 个 JSON，但核心 BPE 训练次数为 128。
- 论文主实验写明 Merge Similarity 与 Vocabulary Overlap 使用 96 个影子 Tokenizer；当前 Merge 脚本确实读取 96 个，但当前 Vocabulary Overlap 脚本读取 128 个。这是论文描述与当前 commit 的代码差异，复现时不能混用口径。
- Frequency Estimation 论文描述为一个影子 Tokenizer、10 次辅助数据采样；当前代码与此大体一致。

## 6. GPU、CPU 与并行

- 仓库没有导入 PyTorch、CUDA 或 GPU 库，也没有设备选择代码；BPE、NumPy、线程池、进程池和 Joblib 都走 CPU。GPU 不是运行条件。
- Vocabulary Overlap 固定 7 个进程，Merge Similarity 固定 10 个 Joblib worker；其余多处使用默认大小的 `ThreadPoolExecutor`。这些固定值没有按机器资源自适应。
- Hugging Face `tokenizers` 的 Rust 后端可能自行使用多线程，因此并行脚本可能发生 CPU 过度订阅。

## 7. 路径、随机性和失败处理

### 路径

- 未发现硬编码的 Windows 盘符、Linux `/home/...` 或 `/root/...` 绝对文件路径；大多数路径从 `Path(__file__).parent` 派生。
- `mia_via_frequency_estimation.py` 硬编码 `http_proxy` 和 `https_proxy` 为 `http://127.0.0.1:10809`。这不是文件绝对路径，但会污染该进程网络配置，应在独立实现中删除或配置化。
- `min_defense/min_defense.py` 两处以当前工作目录解析 `../tokenizer_info/...` 和 `../trained_tokenizer/...`，而不是始终相对脚本目录；从官方 README 所示目录执行时很可能找错路径。

### 随机种子

- `train_target_tokenizer.py` 在未设种子的情况下调用 `random.shuffle`。
- `train_shadow_tokenizer.py` 在未设种子的情况下调用 `random.shuffle` 和 `random.sample`。
- `download_datasets.py` 未固定 Hugging Face dataset revision；上游数据版本变化会影响结果。
- 只有 `mia_via_frequency_estimation.py` 显式调用 `random.seed(42)`；其余训练/攻击没有统一保存 Python、NumPy、哈希种子和运行环境。
- 结论：官方流程不能按当前代码直接保证可复现，必须在 `src` 重实现固定种子、数据 manifest 和环境记录。

### 错误处理与覆盖

- `download_datasets.py` 在两个主循环中使用裸 `except: pass`，会静默吞掉下载、Windows 文件名、编码、I/O 和 JSON 错误。
- 训练和攻击脚本以固定文件名写入，普遍会覆盖旧结果或中间文件。
- 没有统一日志、阶段计时、峰值内存记录和失败 traceback 文件。

## 8. Windows 可运行性结论

结论为“核心算法可以运行，但官方全流程不是 Windows 即开即用”。正面因素是路径大多使用 `pathlib`，依赖存在 Windows/Python 3.12 wheel，进程池入口也有 `if __name__ == '__main__'` 保护。风险包括：

- URL `netloc` 直接作为文件名，带端口的域名包含 `:`，在 Windows 文件名中非法；该错误会被裸 `except` 静默吞掉。
- PowerShell 默认 ExecutionPolicy 可能阻止 `.ps1`，本项目使用进程级 `-ExecutionPolicy Bypass`，不修改系统策略。
- Min-count 的当前工作目录相对路径问题。
- 固定本地代理、默认系统编码、Windows 路径长度和大量小文件/重复重写带来的兼容与性能风险。
- 16 GiB 内存下，全量构造包含数十万网页字符串的 Python 列表存在明显 OOM 风险。

## 9. 本机全量资源估算与服务器建议

审计机器：Intel i7-8750H（6 核/12 线程）、约 16 GiB RAM、Windows 11；D 盘为 SSD/NVMe 存储体系，审计时剩余约 360 GiB。

论文只给出时间、不披露硬件：训练 128 个 200k 影子 Tokenizer 为 3.054 小时；对 4,133 个网站做 Vocabulary Overlap 的五种词表推理时间合计约 10.893 小时，Frequency Estimation 合计约 1.513 小时。详见论文[表 5 和表 6](https://arxiv.org/html/2510.05699#S5.SS2)。这些不能直接当作本机时间。

结合当前代码的数据重写、128 份训练数据复制、五个目标训练和五类攻击，本机全流程粗估为 2–7 天，I/O 或内存抖动时可能更久或直接失败；峰值内存保守估计 24–64 GiB，当前 16 GiB 不足以稳妥完成；总磁盘建议 0.5–1 TB 可用空间，当前约 360 GiB 余量偏紧。以上均为静态工程估算，不是本地全量实测。

建议转 CPU/内存型服务器：至少 16–32 个物理/高性能 CPU 核、64 GiB RAM（128 GiB 更稳妥）、1 TB 本地 NVMe。GPU 对官方攻击流程没有直接收益；后续下游神经文本分类若采用 Transformer，再单独规划 GPU。
