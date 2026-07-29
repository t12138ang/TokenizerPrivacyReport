# 冻结配置勘误

## AG News revision

`configs/final_study.json` 在 Gate 3 攻击矩阵与 Development 搜索计划冻结时，`downstream.dataset_revision` 记录了预检阶段的候选值：

```text
b78c6568d2d15d88a2e1295f9cb3a2a5304a9d0d
```

该对象无法作为 `fancyzhx/ag_news` 的可解析 revision，且从未用于生成下游数据。为保留已经运行的攻击状态和预声明搜索计划的配置 SHA-256，未事后改写这份总配置，也未修改任何原始结果 JSON。

AG News 的唯一权威运行配置是 `configs/downstream.json`。它通过 Hugging Face 官方 API 解析并固定：

```text
eb185aade064a813bc0b7f42de02595523103ca4
```

实际数据元数据 `results/final/downstream/ag_news_data.json` 同时记录 `requested_revision` 与 `resolved_revision` 为该值；原始 train/test 数分别为 120,000/7,600，去重后的固定 train/validation/test 数为 107,839/12,000/7,600，跨划分规范化精确重复为 0。在任何 Development 下游训练或 Main 选择发生前，`configs/final_study.json` 的伞形字段也已同步为该权威 revision，并重新哈希冻结搜索计划；旧值只作为本勘误的历史失败记录保留，不再作为可执行配置或最终审计警告。

## 完整 Tokenizer 的真实 Paillier 运行

冻结总配置中的 `he_execution_for_full_tokenizer` 描述适用于 Development/Main 参数搜索网格：这些组合使用协议等价明文路径，并由独立 Paillier 矩阵测量密码学开销。为满足完整 Tokenizer 训练时间的实测要求，后续新增且不改变原搜索计划的 `configs/crypto_full.json`，固定执行一个 Development 4k、$\varepsilon=4$、P75、$b=32$、$K=1024$、2048-bit、6 进程的真实 Paillier 端到端运行。最终审计要求其 artifact、逐轮候选池哈希与 merge 序列完全等于同噪声明文协议参考；这项独立正式运行不用于调参。
