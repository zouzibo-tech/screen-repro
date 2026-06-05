# screen-repro — 可复现的AI文献筛选系统

> **AI负责思考，Python负责说实话。**
> 用于系统综述/Meta分析的全文筛选阶段。所有判定可溯源、可复现、可审计。
> 一键初始化，自动循环，无人值守。

---

## 快速开始

```bash
# 新项目，只需说一句话
"使用screen-repro筛选文献"

# 主agent自动完成：
# 1. 初始化项目目录 + 从模板复制配置文件
# 2. 自动识别文献总数
# 3. 断点恢复（如有中断）
# 4. 逐篇派发子agent筛选
# 5. 全部完成后自动生成QA报告
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

## 设计哲学

```
AI负责思考（创造性的）          Python负责说实话（确定性的）
┌─────────────────────┐        ┌──────────────────────────┐
│ 理解PICOS规则       │        │ 提取PDF文本              │
│ 阅读论文Methods     │        │ 验证JSON格式             │
│ 逐要素语义判定      │        │ 填入模板生成MD文件       │
│ 引用原文证据        │        │ 追加CSV汇总表            │
│ 写出分析理由        │        │ 更新进度文件             │
│                     │        │ 统计INCLUDE/EXCLUDE/MAYBE│
│  拿不准就MAYBE      │        │ 管理速率退避             │
└─────────────────────┘        └──────────────────────────┘
```

**核心原则**：AI永远不直接写入文件系统（不写MD、不追加CSV、不更新进度）。所有文件操作由Python脚本完成，确保100%确定性。

---

## 工作流程

### 1. 启动阶段（自动）

```
用户: "开始筛选"
  │
  ▼
主agent检测项目状态:
  ├─ screening_progress.json 不存在？
  │   → 自动执行 init：
  │       创建 progress.json + summary.csv
  │       从模板复制 PICOS_RULES.md + RATE_LIMIT.md
  │       创建 pdfs/ mining_output/ screening_records/{INCLUDE,EXCLUDE,MAYBE}/
  │
  ├─ check 显示总数=0？
  │   → 自动从文献池CSV读取并 set-total
  │
  └─ 总是执行 verify（断点恢复）+ check（查看进度）
```

### 2. 循环筛选阶段（自动）

```
┌──────────────────────────────────┐
│ 🛑 CHECKPOINT: 每次派发前强制检查  │
│  □ 当前无子agent运行？            │
│  □ 上篇已完成返回？              │
│  □ 距上次派发已过间隔？          │
│  □ 本次只派发 1 个 Agent         │
└──────────────────────────────────┘
  │
  ▼
派发子agent（独立上下文）→ 等待返回JSON
  │
  ├─ 类型A: 正常筛选 → record_writer.py（验证→写MD→CSV→进度）
  ├─ 类型B: PDF缺失  → progress_manager.py skip
  └─ 类型C: 429限流  → progress_manager.py retry-wait（指数退避）
  │
  ▼
等待3秒 → 回到 CHECKPOINT → 直到 remaining=0
```

### 3. QA阶段（用户人工复核）

```
筛选完成后自动生成 QA_REPORT.md:
  ├─ 🔴 MAYBE: 全部需人工复核
  ├─ 🟡 INCLUDE: 随机抽样≥10%
  └─ 🟢 EXCLUDE: 随机抽样≥5%

用户阅读MD文件 → 运行 qa_report.py resolve/confirm → Python自动更新所有文件
```

**AI不参与QA阶段的任何决策。** 最终判定权始终在用户手中。

---

## 文件结构

### Skill包（跨项目复用）

```
~/.workbuddy/skills/screen-repro/
├── SKILL.md                              # 协议文件（AI执行的规则书）
├── README.md                             # 本文件
├── scripts/                              # Python引擎
│   ├── progress_manager.py               # 进度管理 + 智能初始化
│   ├── pdf_extractor.py                  # PDF文本提取
│   ├── record_writer.py                  # JSON验证 + 记录写入
│   └── qa_report.py                      # QA报告生成
└── templates/                            # 项目模板文件
    ├── PICOS_RULES.template.md           # PICOS纳入排除标准
    ├── RATE_LIMIT.template.md            # API速率限制配置
    ├── SCREENING_RECORD.template.md      # 筛选记录模板（详细版）
    ├── SCREENING_RECORD_QUICK.template.md # 筛选记录模板（快速版）
    └── PROGRESS.template.json            # 进度文件模板
```

### 项目目录（每个项目独立）

```
{项目}/03_Screening/
├── screening_progress.json        # 进度记录（由Python管理）
├── screening_summary.csv          # 汇总追踪表（由Python管理）
├── QA_REPORT.md                   # QA报告（筛选完成后生成）
├── qa_state.json                  # QA状态（复核进度）
├── PICOS_RULES.md                 # 本项目的纳入排除标准
├── RATE_LIMIT.md                  # 本项目的速率限制配置
├── pdfs/                          # PDF原始文件
│   ├── Chen_2024.pdf
│   └── Wang_2023.pdf
├── mining_output/                 # 提取的文本文件
│   ├── Chen_2024_mining.md
│   └── Wang_2023_mining.txt
└── screening_records/             # 筛选记录（按判定分类）
    ├── INCLUDE/
    │   └── Chen_2024_a3f2.md
    ├── EXCLUDE/
    │   └── Li_2022_b7d8.md
    └── MAYBE/
        └── Katz_2023_c8d1.md
```

---

## Python脚本详解

### `progress_manager.py` — 进度管理器

```bash
SKILL_DIR=~/.workbuddy/skills/screen-repro/scripts

# 首次使用：一键初始化整个项目
python $SKILL_DIR/progress_manager.py init
# → 创建 progress.json + summary.csv
# → 从模板复制 PICOS_RULES.md + RATE_LIMIT.md
# → 创建 pdfs/ mining_output/ screening_records/{INCLUDE,EXCLUDE,MAYBE}/

# 设置文献总数
python $SKILL_DIR/progress_manager.py set-total 200

# 查看进度
python $SKILL_DIR/progress_manager.py check
# 输出: 已处理 50/200 | INCLUDE: 8 | EXCLUDE: 38 | MAYBE: 3 | SKIPPED: 1 | ERROR: 0

# 手动记录一篇（通常由record_writer.py自动完成）
python $SKILL_DIR/progress_manager.py update Chen 2024 INCLUDE

# PDF缺失时跳过
python $SKILL_DIR/progress_manager.py skip Zhang 2023

# API限流退避
python $SKILL_DIR/progress_manager.py retry-wait
# → 输出等待秒数（30→60→120→240，每次递增）

# 成功完成一篇后重置退避计数
python $SKILL_DIR/progress_manager.py retry-reset

# 双重验证（断点恢复）
python $SKILL_DIR/progress_manager.py verify
# → 自动修复 MD ↔ progress 不一致

# 汇总报告
python $SKILL_DIR/progress_manager.py summary
# → 输出最终统计 + 标记 done
```

### `pdf_extractor.py` — PDF文本提取器

```bash
# 自动选择最佳提取方式
python $SKILL_DIR/pdf_extractor.py pdfs/Chen_2024.pdf mining_output/Chen_2024_mining.md

# 内部逻辑:
#   PDF ≤ 10MB 且 ≤ 20页 → MinerU API（免费，输出Markdown）
#   PDF > 10MB 或 > 20页 → PyMuPDF本地提取（输出纯文本）
#   任意方式失败 → 自动回退另一种
#   全部失败 → 退出码1

# 退出码:
#   0 = 成功
#   1 = 全部提取方式失败
#   2 = 文本质量异常（乱码率>10%）
```

### `record_writer.py` — 记录写入器

```bash
# 子agent返回JSON后，主agent通过管道传入
echo '{
  "author": "Chen",
  "year": 2024,
  "title": "论文标题",
  "doi": "10.xxxx/xxxxx",
  "decision": "INCLUDE",
  "picos": {
    "P": {"result": "✅", "evidence": ["原文 (Methods p.3)"], "analysis": "undergraduate nursing students"},
    "I": {"result": "✅", "device_type": "HMD_VR", "evidence": ["Oculus Quest 2 (Methods p.4)"], "analysis": ""},
    "C": {"result": "✅", "evidence": ["traditional training (Methods p.5)"], "analysis": ""},
    "O": {"result": "✅", "outcome_type": "Retention", "retention_weeks": 8, "evidence": ["8-week delayed post-test (Results p.8)"], "analysis": ""},
    "S": {"result": "✅", "design_type": "RCT", "evidence": ["randomized controlled trial (Methods p.2)"], "analysis": ""}
  },
  "reason": "HMD VR RCT with nursing students and 8-week retention",
  "pdf_path": "pdfs/Chen_2024.pdf",
  "mining_path": "mining_output/Chen_2024_mining.md",
  "text_quality": "正常",
  "screening_date": "2026-06-02"
}' | python $SKILL_DIR/record_writer.py -

# 自动完成:
#   1. 验证JSON格式（必填字段、决策合法性、排除码合法性）
#   2. 生成唯一文件名（标题哈希解决同作者同年冲突）
#   3. 填入模板生成MD内容
#   4. 写入 screening_records/INCLUDE/Chen_2024_a3f2.md
#   5. 追加 screening_summary.csv
#   6. 更新 screening_progress.json
# → 一条命令完成所有写入
```

### `qa_report.py` — QA报告器

```bash
# 筛选完成后生成QA报告
python $SKILL_DIR/qa_report.py generate
# → 生成 QA_REPORT.md:
#     - MAYBE全部需复核（含不确定维度+关键疑问）
#     - INCLUDE随机抽样≥10%
#     - EXCLUDE随机抽样≥5%
#     - 抽样种子基于日期，同一天可复现

# MAYBE文献由用户复核后确定最终判定
python $SKILL_DIR/qa_report.py resolve Chen 2024 INCLUDE "确认HMD VR，retention≥1周"
# → Python: 移动MD→INCLUDE/ | 更新CSV | 更新progress

# 抽样文献由用户确认
python $SKILL_DIR/qa_report.py confirm Wang 2023 INCLUDE
# → Python: 标记MD末尾"已确认" | 更新qa_state.json

# 查看QA进度
python $SKILL_DIR/qa_report.py status
```

---

## PICOS规则

PICOS纳入/排除标准**不在skill中硬编码**，而是由每个项目独立配置。

- 模板：`templates/PICOS_RULES.template.md`
- 项目文件：`{项目}/03_Screening/PICOS_RULES.md`
- `init` 时自动从模板复制到项目目录
- 子agent筛选前读取项目目录下的 `PICOS_RULES.md`
- 换项目/换研究问题 → 修改项目的 `PICOS_RULES.md` 即可

---

## 子agent返回JSON Schema

子agent完成PICOS判定后，必须按以下schema返回JSON（不得自己写文件）：

```json
{
  "author": "Chen",
  "year": 2024,
  "title": "论文完整标题（从PDF提取）",
  "doi": "10.xxxx/xxxxx",
  "decision": "INCLUDE",
  "exclusion_code": null,
  "picos": {
    "P": {
      "result": "✅",
      "evidence": ["undergraduate nursing students (Methods p.3)"],
      "analysis": "本科护理学生，符合高等教育人群标准"
    },
    "I": {
      "result": "✅",
      "device_type": "HMD_VR",
      "evidence": ["Oculus Quest 2 head-mounted display (Methods p.4)"],
      "analysis": "明确使用HMD VR设备"
    },
    "C": {
      "result": "✅",
      "evidence": ["control group received traditional lecture-based training (Methods p.5)"],
      "analysis": "传统教学对照组"
    },
    "O": {
      "result": "✅",
      "outcome_type": "Retention",
      "retention_weeks": 8,
      "evidence": ["delayed post-test conducted 8 weeks after training (Results p.8)"],
      "analysis": "有8周延迟后测，符合≥1周标准"
    },
    "S": {
      "result": "✅",
      "design_type": "RCT",
      "evidence": ["participants were randomly assigned to either VR or control group (Methods p.2)"],
      "analysis": "随机对照试验"
    }
  },
  "reason": "HMD VR RCT with undergraduate nursing students, 8-week retention test, traditional control group",
  "pdf_path": "03_Screening/pdfs/Chen_2024.pdf",
  "mining_path": "03_Screening/mining_output/Chen_2024_mining.md",
  "text_quality": "正常",
  "screening_date": "2026-06-02"
}
```

例外情况（子agent返回非标准JSON）：
- `{"status": "skipped", "author": "Zhang", "year": 2023, "reason": "PDF not found"}` — PDF缺失
- `{"status": "error", "code": 429, "author": "Li", "year": 2022}` — API限流

---

## 错误处理

| 异常 | 返回内容 | 主agent行动 | 是否继续 |
|------|----------|-------------|:--:|
| PDF缺失 | `{"status":"skipped",...}` | `skip` 跳过，等用户补PDF | ✅ |
| API限流(429) | `{"status":"error","code":429,...}` | `retry-wait` 退避→重试 | ✅ (重试) |
| PDF提取失败 | `{"status":"error","code":1,...}` | `update ... MAYBE` | ✅ |
| 子agent崩溃 | 非JSON/空返回 | `update ... ERROR` | ✅ |
| JSON格式验证失败 | record_writer退出码≠0 | `update ... ERROR` | ✅ |
| 连续4次429 | retry-wait返回-1 | `update ... ERROR` | ✅ |
| 连续3篇崩溃 | 连续3次非JSON | 报告用户 | ⚠️ |

**主agent退出码速查**：
| 退出码 | 含义 |
|--------|------|
| 0 | 正常 |
| 1 | JSON解析/格式验证失败 |
| 2 | 文件写入错误 |

---

## v2.1 vs v2.0 完整变更清单

### 🆕 新增功能
| # | 功能 | 脚本 | 说明 |
|---|------|------|------|
| 1 | **一键初始化** | `progress_manager.py init` | 自动创建所有目录 + 从模板复制 PICOS_RULES.md / RATE_LIMIT.md |
| 2 | **智能启动** | SKILL.md | 用户只需说"开始筛选"，主agent自动检测/初始化/断点恢复 |
| 3 | **QA报告** | `qa_report.py` | 筛选完成后自动生成 QA_REPORT.md（MAYBE清单 + 随机抽样INCLUDE≥10%/EXCLUDE≥5%），种子可复现 |
| 4 | **PDF自动提取** | `pdf_extractor.py` | MinerU API优先（≤10MB/≤20页）→ PyMuPDF回退 → 自动质量检查 |
| 5 | **速率退避** | `progress_manager.py retry-wait` | 指数退避30→60→120→240秒，4次后放弃 |

### 🔧 重构改进
| # | 改进点 | v2.0 | v2.1 |
|---|--------|------|------|
| 6 | **子agent返回格式** | 自由文本 (Author \| Decision \| Code \| Reason) | 结构化JSON，Python验证schema后拒收 |
| 7 | **MD文件写入** | 子agent用Write工具写文件 | `record_writer.py` 统一写入（验证→填模板→MD→CSV→进度），一条命令 |
| 8 | **CSV管理** | 主agent手动追加CSV | Python全权管理，主agent被禁止操作CSV |
| 9 | **verify修复逻辑** | "自动修复"未定义细则 | 明确4条修复规则 + 标注"不验证MD内容正确性" |
| 10 | **包结构** | Python脚本放在项目 03_Screening/ | 迁移到 `~/.workbuddy/skills/screen-repro/scripts/`，跨项目复用 |
| 11 | **PDF缺失处理** | 未定义 | 子agent返回 skipped JSON → skip命令 → 标记SKIPPED → 等用户补PDF |
| 12 | **错误处理** | 未定义 | 异常速查表：6种异常各有标准处理流程，Python记录 |
| 13 | **循环执行** | 主agent可能中途暂停问用户 | 循环铁律：禁止中途询问，全程自动直到完成/异常 |
| 14 | **RATE_LIMIT模板** | 占位符 {model_name} {rpm} | 预填 mimo/mimo-v2.5 默认参数（RPM 100, TPM 10M） |
| 15 | **init自动创建目录** | 需手动创建 pdfs/等子目录 | init一键创建所有子目录 |
| 16 | **唯一文件名** | {Author}_{Year}.md（同作者同年冲突） | {Author}_{Year}_{hash}.md（标题MD5前6位） |

---

## 使用场景示例

### 场景1：全新项目

```bash
# 只需说一句话
"使用screen-repro筛选文献"

# 主agent:
# - 检测到未初始化 → 自动 init
# - 检测到总数=0 → 从文献池CSV自动识别
# - 开始逐篇筛选
```

### 场景2：中断恢复

```bash
# 模型崩溃/电脑死机后重新启动
"使用screen-repro筛选文献"

# 主agent:
# - verify: 自动修复MD↔progress不一致
# - check: 显示"已处理50/200，下一篇Wang_2023"
# - 从断点继续筛选
```

### 场景3：换项目

```bash
# skill已经安装在 ~/.workbuddy/skills/screen-repro/
cd 新项目目录
"使用screen-repro筛选文献"
# → 自动为新项目初始化，Python脚本复用
```

### 场景4：版本回退

```bash
git clone https://github.com/zouzibo-tech/screen-repro.git
git checkout v2.0   # 回到 v2.0
# 或
git checkout v1.0   # 回到 v1.0（纯AI版）
```

---

## 统计示例

```
📊 筛选进度
状态: running    总数: 200
已处理: 50       剩余: 150
INCLUDE:   8 | EXCLUDE:  38 | MAYBE:   3 | SKIPPED:   1 | ERROR:   0
当前/上篇: Chen_2024
```

```
📊 最终汇总
总文献: 200      已处理: 200
INCLUDE: 24       EXCLUDE: 156
MAYBE(需人工复核): 12    SKIPPED(无PDF): 8    ERROR: 0
```

---

## 常见问题

**Q：为什么每篇只读一篇PDF？**

A：防止AI跨文污染——把A论文的Methods误认为B论文的内容。每篇独立子agent处理，独立上下文。

**Q：MAYBE是什么意思？**

A：任何一个PICOS要素无法从原文明确判定 → 整篇标MAYBE，需人工复核。不是"可能纳入"，是"AI说不准"。

**Q：能不能并行处理多篇？**

A：v2.1严格串行（模型RPM限制）。如需并发，手动创建多个batch目录，各跑一个独立筛选任务。

**Q：为什么AI不写MD文件？**

A：AI写文件可能出错（路径、格式）。v2.1中AI只返回JSON，Python负责所有文件操作——确定性，零幻觉。

**Q：换模型怎么调整速率？**

A：修改项目目录下的 `RATE_LIMIT.md` 中的 RPM 和间隔秒数。模板已有mimo/mimo-v2.5默认值。

**Q：新项目怎么开始？**

A：`progress_manager.py init` 一键搞定。自动创建所有目录、复制配置文件。然后说"开始筛选"即可。

---

## 回退指南

```bash
git clone https://github.com/zouzibo-tech/screen-repro.git
cd screen-repro

# 查看所有版本
git tag
# 输出:
# v1.0
# v2.0
# v2.1

# 回退到任意版本
git checkout v1.0   # 纯AI版
git checkout v2.0   # AI+Python初版
git checkout v2.1   # 最新版（Python全面接管）
```

---

## 仓库

https://github.com/zouzibo-tech/screen-repro

---

*screen-repro v2.1 — 可复现的全文筛选 | 2026-06-02*
