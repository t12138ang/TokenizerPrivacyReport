# Independent implementation boundary

本目录是课程项目的独立实现，不从 `third_party/Tokenizer-MIA` 导入代码，也不修改第三方 submodule。固定官方实现只作为公式、输入输出和复现口径的审计对象。

主要模块：

- `attacks/`：五种成员推断、固定分数方向、ROC/PR 与网站级 bootstrap；
- `tokenizer/`：Plain/Min-count 资源以及边界感知的增量批量私有 BPE；
- `privacy/`：站点 L1 截断、确定性整数化、双边几何机制与基本组合；
- `crypto/`：两服务器 Paillier 正确性和 2048-bit 正式矩阵；
- `defenses/`：Development 搜索、冻结选择与 Main 防御；
- `downstream/`：固定 AG News 划分、编码缓存和可恢复 PyTorch Transformer；
- `reporting/`：统一结果表、配对统计、12 幅图与 LaTeX 生成；
- `audit_final.py`：跨数据、结果、密码学、论文、引用和 Git 的最终检查。

论文、官方代码与当前协议的逐项差异见 `docs/PAPER_CODE_PROTOCOL_DIFFERENCES.md`；已知冻结配置勘误见 `docs/CONFIGURATION_ERRATA.md`。
