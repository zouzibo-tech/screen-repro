---
name: screen-repro
description: >
  screen-repro v3.2 — 专门用于系统综述/Meta分析中PICOS文献筛选的自动化工具。
  程序化优先：流程控制、校验、备份、报告全部程序化；AI仅保留给全文PICOS判定。
triggers: >
  screen-repro, 筛选, screen, 筛选文献, 筛选PDF, 全文筛选, 逐篇筛选,
  PICOS筛选, 筛选第, screen paper, fulltext screening, 文献筛选, 可复现筛选
tools: Read, Write, Edit, Bash, Glob
model: inherit
---

# screen-repro v3.2 — 程序化优先的PICOS文献筛选系统

> **程序主导，AI辅助，可复现为本。**

## 项目定位

**screen-repro** 是一个专门用于 **系统综述/Meta分析** 中 **PICOS文献筛选** 的自动化工具。

**核心价值**：
- **程序化优先**：流程控制、数据校验、备份恢复、报告导出全部由程序完成
- **AI最小化**：AI仅用于全文PICOS语义判定，不参与流程控制
- **可复现性**：同一PDF + 同一规则 + 同一模型 = 必须得到同一结果
- **零幻觉**：所有判定必须引用原文，禁止推测或补全
- **无人值守**：一键启动，自动循环，断点恢复

**适用场景**：系统综述/Meta分析的全文筛选阶段，需要按PICOS标准判定文献，文献量大（100+篇），需要AI辅助加速。

---

> **核心理念**：Python是编排器，AI是一个可调用的函数。
> **数据方案**：SQLite（权威数据源） + MD文件（人类可读） + CSV（导出格式）
> **SKILL_DIR** = `~/.workbuddy/skills/screen-repro/scripts_v3.0`

---

## 设计哲学（V3.2）

### 三层架构

```
┌─────────────────────────────────────────────────────┐
│  Orchestration Layer（纯程序）                        │
│  状态机、前置校验、备份、恢复、队列、批次、重试          │
├─────────────────────────────────────────────────────┤
│  Extraction Layer（纯程序）                           │
│  RIS导入、PDF匹配、PDF文本抽取、质量指标、缓存           │
├─────────────────────────────────────────────────────┤
│  Judgment Layer（AI最小化）                           │
│  仅负责PICOS全文判定，输入输出schema固定，调用隔离        │
└─────────────────────────────────────────────────────┘
```

### 程序化优先原则

| 原则 | 说明 |
|------|------|
| **程序主导** | 流程控制、状态管理、数据校验、备份恢复全部由程序完成 |
| **AI最小化** | AI仅承担"阅读+判断"，不承担流程控制、文件管理、数据校验 |
| **数据库优先** | SQLite是权威数据源，MD是人类可读副本，先DB commit再落MD |
| **防御性校验** | 每个关键步骤都有前置校验，空库/错库/缺文件都会被拒绝 |
| **原子操作** | 文件写入先写临时文件，成功后再原子替换 |
| **可审计** | 每个操作都有日志，每次修改都有备份 |

### 状态机式工作流

```
init → import → gate → map → extract → rule_prescreen → judge → report
  │       │       │      │       │           │            │        │
  │       │       │      │       │           │            │        └─ 100%程序
  │       │       │      │       │           │            └─ AI判定（唯一AI环节）
  │       │       │      │       │           └─ 100%程序（规则预筛）
  │       │       │      │       └─ 100%程序（PDF文本抽取）
  │       │       │      └─ 100%程序（PDF映射+备份）
  │       │       └─ 100%程序（前置校验门禁）
  │       └─ 100%程序（RIS解析入库）
  └─ 100%程序（目录/库初始化）
```

---

## 工作流程

### 完整流程（一键执行）

```bash
cd {项目目录}
python screen.py workflow --ris xxx.ris
```

### 分步执行

```bash
# 步骤1：RIS导入
python screen.py import --ris xxx.ris

# 步骤2：规则预筛（程序自动，无需人工）
python screen.py run
# 注：run命令内部已集成规则预筛选，会自动排除综述类文献

# 步骤3：PDF映射（如需手动映射）
python screen.py pdf map

# 步骤4：正式筛选（含AI判定）
python screen.py run
```

---

## 命令速查

| 命令 | 说明 |
|------|------|
| `screen.py init` | 初始化项目（创建目录、数据库、config模板） |
| `screen.py import --ris xxx.ris` | 导入RIS文件 |
| `screen.py run` | 执行筛选循环（自动从断点恢复，含规则预筛+AI判定） |
| `screen.py run --batch N` | 筛选N篇后暂停 |
| `screen.py check` | 查看进度 |
| `screen.py verify` | 验证数据一致性 |
| `screen.py summary` | 汇总报告 |
| `screen.py report` | 生成完整筛选报告（含决策分布、排除原因、年份分布、PICOS通过率、质量检查、筛选效率） |
| `screen.py export` | 导出CSV |
| `screen.py migrate` | 从v2.3迁移 |
| `screen.py pdf map` | PDF映射；执行前会校验数据库非空、创建 `_backups/*_before_pdf_map` 备份，并拒绝在错库/空库上运行 |
| `screen.py prescreen` | 遗留模式：预筛选+AI复核+人机协同（兼容旧流程） |
| `screen.py workflow --ris xxx.ris` | 一键执行完整流程 |

---

## 配置

编辑项目目录下的 `config.json`：

```json
{
  "llm_backend": "openai",
  "openai": {
    "api_key": "sk-...",
    "model": "gpt-4o-2024-08-06",
    "base_url": "https://api.openai.com/v1",
    "rpm": 100,
    "tpm": 10000000
  },
  "rate_limit": {
    "safety_margin": 0.8
  },
  "picos_rules_path": "PICOS_RULES.md"
}
```

支持的后端：`openai`、`anthropic`、`ollama`

---

## 架构

```
screen.py (主编排器 — 状态机式流程控制)
    │
    ├─ Orchestration Layer
    │   ├─ 前置校验门禁（数据库完整性、表结构、记录数）
    │   ├─ 备份管理（自动备份、时间戳目录）
    │   ├─ 原子文件写入（临时文件→替换）
    │   └─ 进度管理（断点恢复、批次控制）
    │
    ├─ Extraction Layer
    │   ├─ ris_parser.py (RIS解析入库)
    │   ├─ PDF匹配（映射策略链：精确→模糊→人工队列）
    │   ├─ PDF文本抽取（PyMuPDF + 缓存）
    │   └─ 文本质量检查（乱码率、长度、章节识别）
    │
    └─ Judgment Layer
        ├─ rule_prescreen (规则预筛：综述类、会议论文等)
        ├─ picos_judge.py (AI判定，子进程隔离)
        ├─ record_writer.py (双写MD + SQLite)
        └─ qa_report.py (QA报告)
```

---

## 预筛选模块

**功能**：在PDF提取和AI判定之前，通过标题和摘要快速排除综述类文章。

**触发条件**：标题包含以下关键词或匹配模式：
- **关键词**：systematic review, meta-analysis, narrative review, scoping review, 综述, 荟萃分析等
- **模式匹配**：标题以"A systematic review..."、"Recent advances in..."等开头

**优势**：
- 节省AI调用成本（无需提取PDF和调用LLM）
- 加速筛选流程（毫秒级判定）
- 100%确定性（基于规则，无幻觉）

**排除码**：E1（综述类文献）

---

## 可复现性保证

- **temperature=0 + seed=42**：最大确定性
- **五维指纹缓存**：text + rules + model + prompt + extraction
- **子进程隔离**：每次AI判定在独立进程，无上下文污染
- **PDF文本规范化**：NFKC + 空白规范化

---

## v2.3兼容性

```bash
python screen.py migrate
```

自动导入v2.3的CSV、progress.json、screening_records到SQLite。

---

## V3.2 更新说明

**版本号**：v3.1 → v3.2

**更新内容**：
1. 补充程序化优先设计原则和三层架构说明
2. 明确状态机式工作流（init→import→gate→map→extract→rule_prescreen→judge→report）
3. `pdf map` 命令增加数据库前置校验、自动备份、原子写入
4. `prescreen` 标记为遗留模式，保留兼容
5. 文档结构优化，区分程序化层和AI层

**兼容性**：完全向后兼容，现有命令和流程不受影响。

---

*详细设计文档：`03_Screening/screen_repro_v3_design.md`*
