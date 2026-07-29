# 最小可行 Smoke Test 报告

运行日期：2026-07-26
最终结果：成功
结果文件：`results/smoke/metrics.json`
日志文件：`logs/smoke_test.log`

## 真实运行结果

| 项目 | 实测值 |
|---|---:|
| 数据来源 | 固定种子生成的 smoke-only 微型语料，不是 C4 |
| 数据量 | 8 个数据集、96 篇短文（每组 12 篇） |
| 目标成员 / 非成员 | 4 / 4 个数据集 |
| 随机种子 | 20260726 |
| 目标 Tokenizer | 1 个 BPE |
| 影子 Tokenizer | 1 个 BPE |
| 请求 / 实际词表 | 256 / 256 |
| 攻击 | Compression Rate（UTF-8 bytes/token，随后 sigmoid） |
| 攻击分数 | ROC AUC = 1.000000 |
| Balanced Accuracy | 1.000000 |
| TPR @ FPR ≤ 1% | 1.000000 |
| Python 内部耗时 | 0.0682219 秒 |
| PowerShell + Conda 启动 + 校验总耗时 | 16.6748571 秒 |
| Python 进程峰值工作集 | 152,125,440 bytes = 145.078125 MiB |
| GPU | 未使用 |
| 成功运行的错误 / 警告 | 0 / 1 |

上述值均由本地脚本实际生成并保存在 JSON 中，没有复制论文表格，也没有手工填写实验值。

## 如何解读 AUC=1.0

该满分只能证明最小管线能训练 Tokenizer、计算攻击信号并生成合法 ROC 指标。微型语料使用相同模板和等量文本，但每个数据集带有不同的重复随机标记；目标 Tokenizer 很容易合并成员标记，因此成员的 bytes/token 明显更高。样本只有 4 个成员和 4 个非成员，统计分辨率极低。

因此这个 AUC 不能代表官方 C4 场景、不能与论文数值比较、不能用于论证隐私泄露强度，也不能写入课程论文的实验结论。成功日志中的唯一警告正是这一限制。

## 与官方实现的差异

本项目没有修改、导入或在原位调用 `third_party/Tokenizer-MIA`。Smoke 逻辑在 `src/smoke_test.py` 独立实现，差异包括：

- 官方使用 C4 的 4,133 个网站和 80k–200k 词表；Smoke 使用 8 个生成数据集和 256 词表。
- 官方一次完整流程有 5 个目标 Tokenizer、最多 128 轮影子训练；Smoke 仅目标/影子各 1 个。
- 本阶段只选最简单的官方 Compression Rate 攻击；影子 Tokenizer 只验证训练链路，不参与该基线打分。
- 官方分数输出会覆盖固定文件；Smoke 对数据、Tokenizer artifact 和 `metrics.json` 均采用存在即拒绝或 exclusive-create。
- Smoke 固定 Python/NumPy/数据划分种子，记录 Python、平台、CPU 数、内存、包版本、官方 commit 和全部参数。
- Smoke 输出严格 JSON；ROC 的正无穷首阈值被保存为 JSON `null`，而不是非标准 `Infinity`。
- PowerShell 与 Python 都显示阶段和已耗时；Python 异常用完整 traceback 记入日志。

## 失败保留与修复记录

首轮 Python 计算本身完成，但 PowerShell 5.1 按本地代码页解析无 BOM 的中文校验脚本，导致校验阶段语法错误；随后还发现首轮 Windows 峰值内存 API 没有显式声明 64 位句柄，结果为 `null`。首轮产物没有覆盖或删除，保留为：

- `results/smoke/metrics_attempt1.json`
- `results/smoke/artifacts_attempt1/`
- `data/smoke_attempt1/`
- `logs/smoke_test_attempt1.log`

修复方式为：校验脚本使用 ASCII 输出标签；Windows API 显式设置 ctypes 签名；严格处理非有限 ROC 阈值。修复后重新执行一次极小 Smoke，最终完整流程和结果校验均成功。

## 停止门禁

Smoke Test 成功后已立即停止实验工作。没有下载 C4、没有运行官方目标/影子全量训练、没有运行任何全量攻击、Min-count、防御、下游分类或同态加密实验，也没有撰写实验结论。
