# 课程提交仓库白名单

本文件记录课程提交专用 GitHub 仓库的发布边界。公开仓库由本地研究仓库按白名单复制生成，不直接推送本地完整工作区。

## 纳入内容

- 根目录说明与环境文件：`README.md`、`environment.yml`、`requirements.txt`、`.gitignore`；
- 第三方上游代码：`.gitmodules` 与固定提交的 `third_party/Tokenizer-MIA` Git submodule；
- 可复现代码与配置：`src/`、`configs/`、`scripts/`、`tests/`；
- 数据、实验与审计说明：指定的 `docs/` 文件、`data/README.md` 及数据清单；
- 论文源码和已编译论文：`paper_jcr/`；
- 论文所需的汇总表、图表源数据与主实验元数据。

## 明确排除内容

- 原始或缓存语料、AG News 本地数据副本；
- 模型检查点、训练中间状态和运行日志；
- 除已声明 Git submodule 外的第三方仓库副本；
- 本地备份、临时渲染目录和旧版交付包；
- 仅供作者个人准备的材料；
- Conda 环境、Python 缓存及其他机器相关文件。

## 发布核验

发布前后均执行 `scripts/scan_submission_secrets.ps1`。该脚本只输出文件、行号和命中规则，不打印疑似秘密正文。最终提交还通过远端可访问性检查和全新克隆编译验证。
