# screen-repro — 可复现的AI文献筛选系统

> **AI负责思考，Python负责说实话。**
> 用于系统综述/Meta分析的全文筛选阶段。所有判定可溯源、可复现、可审计。

---

## 快速开始

```bash
# 新项目，只需说一句话
"使用screen-repro筛选文献"

# 主agent自动完成：
# 1. 初始化项目目录 + 复制配置文件
# 2. 自动识别文献总数
# 3. 断点恢复（如有中断）
# 4. 逐篇派发子agent筛选
# 5. 生成QA报告（MAYBE清单 + 随机抽样）
```

**用户全程无需手动操作文件。**

---

## 版本历史

| 版本 | 标签 | 状态 | 核心变化 |
|------|------|:--:|----------|
| **v2.1** | `v2.1` | ✅ 当前 | Python全面接管机械操作，一键初始化，自动循环 |
| v2.0 | `v2.0` | 📦 存档 | AI+Python混合，Python负责计数/记账 |
| v1.0 | `v1.0` | ⚠️ 废弃 | 纯AI版，仅保留历史参考 |

---

## v2.1 vs v2.0 变更清单

### 🆕 新增功能
| # | 功能 | 说明 |
|---|------|------|
| 1 | **一键初始化** | `progress_manager.py init` 自动创建所有目录+从模板复制 PICOS_RULES.md / RATE_LIMIT.md |
| 2 | **智能启动** | 用户只需说"开始筛选"，主agent自动检测项目状态、初始化、断点恢复 |
| 3 | **QA报告** | `qa_report.py` 筛选完成后自动生成 QA_REPORT.md（MAYBE清单+随机抽样≥10%/5%） |
| 4 | **PDF自动提取** | `pdf_extractor.py` MinerU API优先→超限PyMuPDF回退→自动质量检查 |
| 5 | **速率退避** | `progress_manager.py retry-wait` 指数退避30→60→120→240秒 |

### 🔧 重构改进
| # | 改进 | 旧方案 | 新方案 |
|---|------|--------|--------|
| 6 | **JSON返回协议** | 子agent返回自由文本 | 子agent返回结构化JSON，Python验证schema |
| 7 | **MD文件写入** | 子agent用Write工具写文件 | `record_writer.py` 统一写入（验证→填模板→MD→CSV→进度） |
| 8 | **CSV管理** | 主agent手动追加CSV | Python全权管理，主agent禁止操作CSV |
| 9 | **verify修复逻辑** | 未定义 | 明确4条修复规则（MD↔progress自动修复） |
| 10 | **包结构** | Python脚本放在项目目录 | 迁移到 `~/.workbuddy/skills/screen-repro/scripts/`，跨项目复用 |
| 11 | **PDF缺失处理** | 未定义 | 子agent返回skipped JSON，标记SKIPPED，等用户补PDF |
| 12 | **错误处理** | 未定义 | 异常速查表，6种异常各有标准处理流程 |
| 13 | **循环铁律** | 可能中途暂停 | 禁止中途询问用户，全程自动直到完成 |
| 14 | **RATE_LIMIT模板** | 占位符 | 预填mimo/mimo-v2.5默认参数（RPM 100, TPM 10M） |

### 📁 目录结构

```
~/.workbuddy/skills/screen-repro/        # skill包（跨项目复用）
├── SKILL.md
├── README.md
├── scripts/                              # Python引擎
│   ├── progress_manager.py               # 进度管理 + 智能初始化
│   ├── pdf_extractor.py                  # PDF文本提取
│   ├── record_writer.py                  # JSON验证 + 记录写入
│   └── qa_report.py                      # QA报告生成
└── templates/                            # 模板文件
    ├── PICOS_RULES.template.md
    ├── RATE_LIMIT.template.md
    ├── SCREENING_RECORD.template.md
    ├── SCREENING_RECORD_QUICK.template.md
    └── PROGRESS.template.json
```

---

## 核心原则

| 原则 | AI做的事 | Python做的事 |
|------|---------|-------------|
| **语义判断** | 读PDF、理解Methods、PICOS判定 | — |
| **机械操作** | — | 提取文本、验证JSON、写MD、追加CSV、更新进度 |
| **不可出错** | 拿不准就MAYBE | 确定性操作，零幻觉 |

---

## 回退指南

```bash
git clone https://github.com/zouzibo-tech/screen-repro.git
cd screen-repro

# 查看所有版本
git tag

# 回退到任意版本
git checkout v1.0   # 纯AI版
git checkout v2.0   # AI+Python初版
git checkout v2.1   # 最新版
```

---

## 仓库

https://github.com/zouzibo-tech/screen-repro

---

*screen-repro v2.1 — 可复现的全文筛选 | 2026-06-02*
