---
name: screen-repro
description: >
  screen-repro v2.0 — 可复现的逐篇全文筛选skill（AI+Python版）。
  用于系统综述/Meta分析的文献筛选阶段。
  AI负责读PDF+PICOS判定，Python负责计数/记账/验证/统计（防止AI幻觉）。
  所有判定可溯源、可复现、可审计。
  调用方式：开新对话 → 发送"使用screen-repro筛选文献"。
triggers: >
  screen-repro, 筛选, screen, 筛选文献, 筛选PDF, 全文筛选, 逐篇筛选,
  PICOS筛选, 筛选第, screen paper, fulltext screening, 文献筛选, 可复现筛选
tools: Read, Write, Edit, Bash, Glob
model: inherit
---

# screen-repro v2.0 — 可复现的逐篇全文筛选协议

> **v2.0核心变化**：Python负责记账（计数/验证/统计），AI只负责思考（读PDF/PICOS判定）
> **v1.0（纯AI版）已保留为 @screen-repro-v1**

---

## 🛑 开始前必须执行

**在派发任何子agent之前，主agent必须先运行：**

```bash
# 1. 初始化（如果第一次）
PYTHONIOENCODING=utf-8 python 03_Screening/progress_manager.py init
PYTHONIOENCODING=utf-8 python 03_Screening/progress_manager.py set-total <总数>

# 2. 查看进度（每次重新开始时）
PYTHONIOENCODING=utf-8 python 03_Screening/progress_manager.py check
# → 输出：已处理X/总数，下一篇是Y，INCLUDE Z篇

# 3. 双重验证（每次重新开始时）
PYTHONIOENCODING=utf-8 python 03_Screening/progress_manager.py verify
# → 自动修复 MD ↔ progress 不一致
```

---

## 核心原则

**AI负责思考，Python负责说实话。**
- AI: 读PDF、理解Methods、判定PICOS → 创造性的，需要语义理解
- Python: 计数、记账、验证、统计 → 确定性的，零幻觉风险

---

## 文件结构

```
03_Screening/
├── progress_manager.py            # 进度管理脚本（Python，零幻觉）
├── screening_progress.json        # 进度文件（由Python管理）
├── screening_summary.csv          # 汇总表（由Python管理）
├── PICOS_RULES.md                 # PICOS纳入/排除标准
├── RATE_LIMIT.md                  # API速率限制配置
├── pdfs/                          # PDF原始文件
├── mining_output/                 # 提取的文本文件
└── screening_records/             # 筛选记录（按判定分类）
    ├── INCLUDE/
    ├── EXCLUDE/
    └── MAYBE/
```

---

## 执行流程

### 启动阶段

```
1. 读取 PICOS_RULES.md + RATE_LIMIT.md
2. PYTHONIOENCODING=utf-8 python 03_Screening/progress_manager.py init
3. PYTHONIOENCODING=utf-8 python 03_Screening/progress_manager.py set-total 461
4. PYTHONIOENCODING=utf-8 python 03_Screening/progress_manager.py verify  ← 自动修复
5. PYTHONIOENCODING=utf-8 python 03_Screening/progress_manager.py check   ← 查看进度
```

### 循环阶段（每篇文献）

```
╔═══════════════════════════════════════════════════════╗
║ 🛑 派发前强制检查 — 每次必须逐条确认 ⚠️                ║
╠═══════════════════════════════════════════════════════╣
║ □ 当前是否已经有子agent正在运行？→ 如果有，等待       ║
║ □ 上一篇是否完成并返回结果？→ 如果未，等待            ║
║ □ 距离上次派发是否已超过间隔？→ 如果未，等待          ║
║ □ 确认：本次只派发 1 个 Agent                        ║
╚═══════════════════════════════════════════════════════╝

违反以上任何一条 → 立即停止，向用户报告违规。

1. 派发子agent（仅1个）→ 等待完成
2. 子agent返回: {Author}_{Year} | {INCLUDE/EXCLUDE/MAYBE} | {排除码} | {理由}
3. **立即用Python记录**:
   PYTHONIOENCODING=utf-8 python 03_Screening/progress_manager.py update \
       {Author} {Year} {INCLUDE/EXCLUDE/MAYBE} {排除码}
4. 等待间隔（从RATE_LIMIT.md读取）
5. 重复 CHECKPOINT → 派发 → 等待 → Python记录
```

### 子agent的工作（独立的，不知道其他文献）

```
1. 读取 PICOS_RULES.md
2. 读取 pdfs/{Author}_{Year}.pdf，用PyMuPDF提取文本
3. 按PICOS逐项判定，引用原文
4. 写入 screening_records/{判定}/{Author}_{Year}.md
   （使用 templates/SCREENING_RECORD.template.md）
5. 返回: {Author}_{Year} | {判定} | {排除码} | {简要理由}
```

### 完成阶段

```
PYTHONIOENCODING=utf-8 python 03_Screening/progress_manager.py verify
PYTHONIOENCODING=utf-8 python 03_Screening/progress_manager.py summary
→ 输出最终报告 + 标记done
```

---

## Python命令速查

| 命令 | 何时用 |
|------|--------|
| `init` | 首次运行，创建文件 |
| `set-total N` | 开始筛选前 |
| `check` | 每次启动/查看进度 |
| `verify` | 每次启动/检查一致性 |
| `update First Year Decision Code` | 每篇完成后 |
| `summary` | 全部完成时 |

---

## PICOS规则

**从 03_Screening/PICOS_RULES.md 读取。**（不在skill中硬编码）

## 速率限制

**从 03_Screening/RATE_LIMIT.md 读取。**（不在skill中硬编码）

## 筛选记录模板

**从 templates/SCREENING_RECORD.template.md 读取。**（不在skill中硬编码）

## 快速预筛记录模板

**从 templates/SCREENING_RECORD_QUICK.template.md 读取。**（不在skill中硬编码）

---

*skill版本: v2.0*
*创建日期: 2026-06-02*
*配套Python脚本: 03_Screening/progress_manager.py*
