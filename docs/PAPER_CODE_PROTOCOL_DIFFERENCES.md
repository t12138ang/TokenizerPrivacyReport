# 论文、官方代码与本项目协议差异

审计对象：

- Tokenizer MIA 论文的可核验公开版本；
- 官方仓库 `mengtong0110/Tokenizer-MIA` 固定 commit `eeb0d83b34dd13f203bf578814463d0654295798`；
- 本项目 Gate 3 的 `strict_disjoint` 最终研究协议。

本项目不修改 `third_party/Tokenizer-MIA`。所有差异均在 `src/` 独立实现，并通过配置、manifest、状态文件和测试记录。

## 攻击复现差异

| 项目 | 论文/官方实现 | Gate 3 实现与理由 |
|---|---|---|
| 数据身份 | 官方目录名承担网站身份，数据下载与路径组织依赖脚本约定 | 固定 C4 revision，用规范化 host 的 SHA-256 作为站点 ID；成员标签只存在于 manifest，不写入正文或文件名 |
| 数据划分 | 论文主设置与公开脚本的辅助数据复用方式不完全一致 | 只采用 `strict_disjoint`：目标评估、shadow auxiliary、public candidate 三池互斥，逐 seed 保存不可变 manifest |
| 随机性 | 部分官方 shuffle/采样未显式固定全部随机源 | 固定 Python/NumPy/PyTorch/tokenizers 可控随机源，三个预声明种子；分数方向在运行前固定 |
| shadow 数 | 论文主攻击描述使用 96；官方 Vocabulary Overlap 脚本硬编码 128 | 主实验用 32；只在 Main/16k/Plain/seed 20260726 上运行 8/16/32/64/96 敏感性，避免扩展为无意义笛卡尔积 |
| Frequency Estimation | 官方先按 `\w+|[^\w\s]+` 切分文本，统计词表项作为切分单元子串的重叠特征；再按目录布局采样辅助集合，并把归一化 token 频次展开成 1000 万 rank 样本交给 `powerlaw.Fit` | 用确定性 Aho--Corasick 复现相同子串计数口径；使用 1 个 shadow tokenizer 加 10 组固定辅助采样；排除单字符/特殊 token，以原频次为权重执行确定性离散 MLE，并在至多 128 个 `xmin` 上按 KS 选尾部，避免展开数组；alpha、xmin、KS 与样本来源进入结果 |
| Naive Bayes | 使用相同的重叠子串特征、10 组辅助成员并对尾部 top-k 词表项计算生存概率连乘 | 保持官方分母和 top-k 构造；用 Aho--Corasick 代替大型词项映射缓存，并在对数域累计 `-log(1-p)` 以避免连乘下溢；零计数跳过、有限数检查与正类方向均预先固定 |
| Compression Rate | 字节/token；高压缩率代表更可能为成员 | 保持相同定义，明确零 token 防护，并保存原始站点分数 |
| Vocabulary Overlap | 对 shadow in/out 差异词表与目标词表计算差分 | 保持核心定义，显式处理空集合、并列分数和 shadow 前缀复用 |
| Merge Similarity | 依据目标与 shadow merge 信息构造相似度 | 独立解析 merge 序列，固定方向与数值边界，保存逐站分数 |
| 指标 | 主要报告 ROC AUC、BA 与低 FPR TPR | 额外报告 AP、TPR@1%、TPR@0.1%、完整 ROC/PR、网站级 95% bootstrap CI、时间和峰值内存 |
| 平台 | 多处使用 POSIX 路径/硬编码路径 | 使用 `pathlib` 与 PowerShell 包装器，支持 Windows、原子 JSON、失败 traceback 和 checkpoint |

攻击的 perfect/random/reversed/constant/class-imbalance 方向测试位于 `tests/test_attack_direction.py`。结果不会根据观察到的真实标签事后取负。

## Tokenizer 基线差异

- Plain BPE 采用本项目统一的固定特殊 token、normalizer、pre-tokenizer 与训练元数据。
- Min-count 是训练后按官方重叠子串特征在训练语料中的出现计数进行确定性过滤，阈值为 2、5、10；目标与攻击所需 shadow 使用同一规则。项目用带来源哈希的共享计数缓存避免三个阈值重复扫描，缓存不进入论文结果；官方示例只处理目标 Tokenizer 且使用固定阈值 48，本项目按课程要求扩展为目标/shadow 一致的 2/5/10 可审计矩阵。
- 4k/8k/16k/32k 资源由最大词表的 merge 顺序确定性截断，保存父资源与派生哈希；这种工程复用与逐词表独立训练不完全等价，因此报告中必须明确。

## SA-DP-BPE 与现有官方代码的关系

官方仓库没有实现本文防御。Gate 3 新增：

1. 站点级 add/remove adjacency；一个完整网站是一个隐私单位。
2. 由严格隔离 public candidate 语料按当前公开 tokenizer 生成 top-K 候选。
3. 客户端候选频率向量执行 L1 截断，并用确定性最大余数法整数化，保证非负、维度不变且 L1 总和不超过 C。
4. 聚合值使用双边几何噪声；每批预算为 `epsilon_total/R`，对自适应 R 轮采用基本顺序组合。
5. 每轮按预定义兼容规则选取最多 b 个不冲突 merge：禁止右端到左端的链式重叠，并拒绝产生同名新符号的不同分解，以减少隐私查询和密码学交互轮次而不破坏 Tokenizer 状态一致性。
6. 聚合服务器 A 只持 Paillier 公钥并看到单客户端密文；解密服务器 D 持私钥，只接收加入噪声后的聚合密文并返回 merge ID。两者均 honest-but-curious 且假定不共谋。

私有 BPE 以 Whitespace 预分词单元为边界维护增量词对计数，不把相邻单词末尾/开头误当作候选 merge。批内兼容规则禁止某条规则的右符号成为另一条规则的左符号，并拒绝两个不同词对产生同名新符号，但允许只共享左符号或只共享右符号；这保证确定性从左到右应用时不存在链式重叠或词表状态歧义，同时避免过度保守地把实际每批 merge 数压低到远小于 b。计划轮数固定为 `ceil((目标词表-初始词表)/b)`，实际执行轮数和未达到目标词表的情况均如实记录。

当前实现不是 local DP、不是门限 Paillier，也不覆盖恶意客户端、主动篡改、访问模式或 A/D 共谋。Paillier 保护训练阶段频率的机密性；站点级 DP 才针对发布后成员隐私提供形式化机制。HE-only 原则上与 Plain 得到完全相同的明文聚合与 tokenizer，因此不能仅凭加密声称发布后泄露下降。

## 密码学实现口径

- `phe==1.5.0` 作为外部依赖；1024-bit 只用于正确性 smoke，正式开销统一使用 2048-bit。
- D 生成并持有私钥；A 的对象不包含私钥或解密方法。
- 负噪声使用库的 signed encoding；运行前检查可编码范围，结果逐项与明文聚合比较，正常条件下期望绝对误差为 0。
- 正确性测试覆盖客户端顺序不变性、负噪声、密钥复用、明文/HE 一致性以及 A 无法解密。
- Development/Main 搜索网格采用协议等价的清晰计数路径，避免把同一 Python Paillier 模指数运算重复嵌入全部参数组合。真实 Paillier 正确性和 2–64 客户端、K=128–4096 的正式开销由独立矩阵测量；此外在 Development 4k 参考配置上执行一次完整 2048-bit、6 进程真实 Paillier SA-DP-BPE，并要求其 artifact、候选池哈希和逐轮 merge 与同噪声明文协议完全一致。只有这一个完整运行可称为全程密文实测，其余防御搜索的密码学时间不得伪称实跑。

## 数据和调参口径

- Gate 2 的 192 网站 pilot 与 Gate 3 结果严格分开。
- Development：128 个目标评估网站、64 个辅助网站、每站 10–100 篇。
- Main：512 个目标评估网站、256 个辅助网站、每站 20–100 篇。
- Development 只执行预声明的 one-factor screen 与 shortlist，不做 288 单元的笛卡尔爆炸。
- Local-DP 与两组 SA-DP Main 配置必须在读取 Main 攻击或下游结果前，依据 Development 的固定约束和排序规则冻结。
- 参数选择的密码开销并列项使用预声明的静态代理 $K/b$，表示每新增 merge 的近似密文坐标工作量；它排在 Development AUC 与 token 增长之后，避免使用稍后才产生的 Main 或正式密码计时反向调参。
- AG News 的 epoch 只由验证集选择；测试集只在最终选择后使用一次。所有方法共享划分、结构、优化器、最大 epoch 和 early stopping 规则。

## 可比性限制

本地 C4 规模、严格隔离规则、shadow 数、词表派生方式、Min-count 处理、攻击数值加固和硬件均与论文可能不同。因此 Gate 3 可以验证代码路径、攻击趋势和方法间本地比较，但不能把数值差异直接解释为对官方表格的逐格复现，也不能声称达到原论文规模或结论，除非最终审计后的真实数据明确支持。
