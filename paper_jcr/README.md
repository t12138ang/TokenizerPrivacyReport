# 《密码学报》模板版现代密码学课程报告

正文使用《密码学报（中英文）》中文 LaTeX 模板。根目录中的 `jcr.cls`、`jcr.cfg` 与 `mfirstuc.sty` 是官方文件的未修改副本，使源码无需额外设置 `TEXINPUTS` 即可编译；来源与校验值见 `SOURCE.md`。PDF 不含独立课程封面，首页直接显示中英文题名、作者、单位和摘要。兼容层仅调整课程报告不需要的期刊出版栏，不修改官方类文件。

作者元数据统一保存在 `metadata.tex`：唐志凯，广东外语外贸大学信息科学与技术学院（网络空间安全学院），广州 510006，`20251010016@mail.gdufs.edu.cn`。

## 编译

在本目录直接执行三遍 XeLaTeX：

```powershell
xelatex main.tex
xelatex main.tex
xelatex main.tex
```

Windows + MiKTeX：

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\build.ps1
```

Linux + TeX Live：

```bash
sh ./build.sh
```

安装了 latexmk 时：

```text
latexmk main.tex
```

Overleaf 中上传源码包全部内容并将编译器选择为 XeLaTeX，主文件选择 `main.tex`。

也可在完整项目根目录执行：

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\scripts\build_paper_jcr.ps1
```

官方模板没有提供 `.bst`，因此数字型参考文献列表保存在 `generated/references.tex`，其条目与 `references.bib` 对应。

## 结果口径

- 攻击、分词器效用与 AG News 结果均来自既有实验记录，论文润色过程不改变原始数值。
- 五种攻击分别计算方向适配后的 AUC，再取宏平均；`D_AUC` 表示 AUC 偏离度，不等同于成员推断优势。
- 隐私风险、分词器效用和下游分类采用协议等价的清晰聚合实现；真实 Paillier 实验独立验证 2048-bit 参数下的正确性与计算开销。
- 密码学开销表明确区分 24 组实测配置与 6 组模型预测配置。

## 提交版说明

提交版以学术问题、协议设计、形式分析和实验结果为叙事主线。中文摘要、引言贡献、威胁模型、分层验证策略、综合结果分析、讨论和结论均已完成学术化修订；图表中的实验分组与计量标签已统一为中文。
