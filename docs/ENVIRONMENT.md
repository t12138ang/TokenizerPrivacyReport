# 独立环境说明

记录日期：2026-07-26
项目根：仓库根目录

## 结论

项目环境成功建立在仓库内的独立前缀：

```text
<repository-root>\.conda\envs\tokenizer-privacy-report
```

没有激活、删除或安装包到 base 及其他已有 Conda 环境。官方要求的 Python 3.12 可用，实测为 Python 3.12.13，因此没有创建降级兼容环境。2026-07-27 最近一次 `pip check` 为 `No broken requirements found.`，43 项项目单元测试通过。

## 软件版本实测

| 组件 | 实际版本 |
|---|---:|
| Python | 3.12.13 |
| datasets | 2.21.0 |
| joblib | 1.4.2 |
| mpmath | 1.3.0 |
| numpy | 2.3.2 |
| powerlaw | 1.5 |
| scikit-learn | 1.7.1 |
| tokenizers | 0.21.4 |
| tqdm | 4.66.4 |
| phe | 1.5.0 |
| gmpy2 | 2.3.1 |
| PyTorch | 2.13.0+cu126 |
| PyTorch CUDA runtime | 12.6 |
| matplotlib | 3.11.1 |
| pandas | 3.0.5 |
| scipy | 1.18.0 |

`requirements.txt` 的第一段逐项固定官方仓库依赖，第二段固定 Gate 3 密码学、PyTorch、统计和绘图依赖。`environment.yml` 固定 Python 主次版本并通过 pip 引用根目录 requirements。

## 硬件与资源

| 项目 | 审计值 |
|---|---:|
| 操作系统 | Windows 11 10.0.22631, AMD64 |
| 逻辑 CPU | 12 |
| 物理内存 | 17,064,644,608 bytes（约 15.89 GiB） |
| GPU | NVIDIA GeForce GTX 1050 Ti |
| GPU 显存 | 4096 MiB |
| Compute Capability | 6.1（Pascal） |
| NVIDIA 驱动 | 581.57 |
| D 盘 CUDA 安装后剩余空间 | 约 348.54 GiB |
| 当前环境大小 | 4,967,840,323 bytes（约 4.63 GiB） |

通过官方索引 `https://download.pytorch.org/whl/cu126` 安装 `torch==2.13.0+cu126`，没有从第三方镜像获取 wheel。安装后 `torch.cuda.is_available()` 为 `True`，设备名为 `NVIDIA GeForce GTX 1050 Ti`，Compute Capability 为 6.1；wheel 自报支持列表包含 `sm_61`。真实验证以 batch 16、序列长 256、词表 32,000 的两层 Transformer 完成一次 CUDA 前向与反向传播，loss 为 `1.393704891204834`，该验证进程峰值已分配显存为 156,424,704 bytes。该数值只证明代码和硬件路径连通，不作为 AG News 结果。

实际安装与验证命令为：

```powershell
& '.\.conda\envs\tokenizer-privacy-report\python.exe' -m pip install `
  --force-reinstall --no-deps torch==2.13.0+cu126 `
  --index-url https://download.pytorch.org/whl/cu126
& '.\.conda\envs\tokenizer-privacy-report\python.exe' -m pip check
```

AG News 总配置的预检 revision 曾填写为一个不可解析候选值；该值没有用于下载。权威的 `configs/downstream.json`、`configs/final_study.json` 和实际数据元数据现均固定到 `eb185aade064a813bc0b7f42de02595523103ca4`。同步发生在任何 Development 下游训练和 Main 选择之前，随后重新哈希冻结计划；完整历史见 `docs/CONFIGURATION_ERRATA.md`。

## 环境建立异常与处置

第一次长时 `conda env create` 已成功求解 Python 3.12，但事务校验报告用户级 Conda 缓存中的 `ucrt-10.0.26100.0-h57928b3_0` 缺少 DLL，触发 `CondaVerificationError`。这不是 Python 3.12 或官方 requirements 的版本冲突。

为避免改变共享缓存或其他环境，采用以下隔离处置：

1. 仅检查项目内未完成前缀；`conda env remove` 因其尚不是合法环境而安全拒绝。
2. 仅对创建进程把 `CONDA_PKGS_DIRS` 设为项目内 `.conda\pkgs`。
3. 在项目前缀以 `--force` 创建 Python 3.12 与 pip，再安装根目录固定依赖。
4. 共享损坏缓存保留原状，没有删除或修复。

另一次初始命令只因执行工具的短超时窗口中止，当时环境前缀尚未建立；该记录不是依赖冲突。

## Windows 调用方式

若普通 PowerShell 中 `conda` 不在 `PATH`，可从 Anaconda Prompt 执行，或使用本机 Anaconda 安装目录中的 `conda.exe`。创建完成后直接调用环境内的 `python.exe`。系统 ExecutionPolicy 可能阻止直接启动 `.ps1`，因此使用进程级旁路，不永久更改系统策略：

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\scripts\run_everything.ps1
```

从空目录重建环境时建议继续使用项目私有包缓存：

```powershell
$env:CONDA_PKGS_DIRS = "$PWD\.conda\pkgs"
conda env create `
  --prefix "$PWD\.conda\envs\tokenizer-privacy-report" `
  --file .\environment.yml
```

该命令必须在项目根目录执行，因为 `environment.yml` 的 pip 段引用相对路径 `requirements.txt`。

## 依赖许可证边界

官方代码和各 Python 依赖受各自许可证约束。`phe==1.5.0`（python-paillier）以外部运行依赖方式使用，其源代码没有复制到本仓库。仓库本身尚未声明统一开源许可证，因此在公开分发或复用前需要单独完成许可证决策。
