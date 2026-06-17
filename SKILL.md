---
name: screen-repro
description: >
  screen-repro v3.1 — 专门用于系统综述/Meta分析中PICOS文献筛选的自动化工具。
  利用AI快速筛选，Python保证可复现性。AI筛选，Python把关，可复现为本。
triggers: >
  screen-repro, 筛选, screen, 筛选文献, 筛选PDF, 全文筛选, 逐篇筛选,
  PICOS筛选, 筛选第, screen paper, fulltext screening, 文献筛选, 可复现筛选
tools: Read, Write, Edit, Bash, Glob
model: inherit
---

# screen-repro v3.1 — 可复现的PICOS文献筛选系统

> **AI筛选，Python把关，可复现为本。**

## 项目定位

**screen-repro** 是一个专门用于 **系统综述/Meta分析** 中 **PICOS文献筛选** 的自动化工具。

**核心价值**：
- **快速筛选**：利用AI快速阅读论文全文，自动判定PICOS五维度
- **可复现性**：同一PDF + 同一规则 + 同一模型 = 必须得到同一结果
- **零幻觉**：所有判定必须引用原文，禁止推测或补全
- **无人值守**：一键启动，自动循环，断点恢复

**适用场景**：系统综述/Meta分析的全文筛选阶段，需要按PICOS标准判定文献，文献量大（100+篇），需要AI辅助加速。

---

> **核心理念**：Python是编排器，AI是一个可调用的函数。
> **数据方案**：SQLite（权威数据源） + MD文件（人类可读） + CSV（导出格式）
> **SKILL_DIR** = `~/.workbuddy/skills/screen-repro/scripts_v3.0`

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

# 步骤2：预筛选+AI复核+人机协同
python screen.py prescreen

# 步骤3：PDF映射
python screen.py pdf map

# 步骤4：正常筛选
python screen.py run
```

---

## 命令速查

| 命令 | 说明 |
|------|------|
| `screen.py init` | 初始化项目（创建目录、数据库、config模板） |
| `screen.py import --ris xxx.ris` | 导入RIS文件 |
| `screen.py prescreen` | 预筛选+AI复核+人机协同 |
| `screen.py run` | 执行筛选循环（自动从断点恢复） |
| `screen.py run --batch N` | 筛选N篇后暂停 |
| `screen.py check` | 查看进度 |
| `screen.py verify` | 验证数据一致性 |
| `screen.py summary` | 汇总报告 |
| `screen.py report` | 生成完整筛选报告（含决策分布、排除原因、年份分布、PICOS通过率、质量检查、筛选效率） |
| `screen.py export` | 导出CSV |
| `screen.py migrate` | 从v2.3迁移 |
| `screen.py pdf map` | PDF映射；执行前会校验数据库非空、创建 `_backups/*_before_pdf_map` 备份，并拒绝在错库/空库上运行 |
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
screen.py (主编排器)
    │
    ├─ 预筛选模块 (标题/摘要快速排除综述类文章)
    ├─ db_manager.py (SQLite数据库)
    ├─ record_writer.py (双写MD + SQLite)
    ├─ picos_judge.py (AI判定，子进程隔离)
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

*详细设计文档：`03_Screening/screen_repro_v3_design.md`*
