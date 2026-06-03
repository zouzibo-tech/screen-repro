---
name: screen-repro
description: >
  screen-repro v2.1 — 可复现的逐篇全文筛选skill（AI+Python版）。
  用于系统综述/Meta分析的文献筛选阶段。
  AI负责读PDF+PICOS判定，Python负责提取/验证/写文件/计数/统计（防止AI幻觉）。
  所有判定可溯源、可复现、可审计。一键初始化，自动筛选，无人值守。
  调用方式：开新对话 → 发送"使用screen-repro筛选文献"。
triggers: >
  screen-repro, 筛选, screen, 筛选文献, 筛选PDF, 全文筛选, 逐篇筛选,
  PICOS筛选, 筛选第, screen paper, fulltext screening, 文献筛选, 可复现筛选
tools: Read, Write, Edit, Bash, Glob
model: inherit
---

# screen-repro v2.1 — 可复现的逐篇全文筛选协议

> **v2.1核心变化**：Python全面接管机械操作（提取/验证/写文件/统计），AI只做语义判断。一键初始化，自动循环，无人值守。
> **v2.0（Python引入版）** 和 **v1.0（纯AI版）** 已通过Git标签存档：`git checkout v2.0` / `v1.0`
> **SKILL_DIR** = `~/.workbuddy/skills/screen-repro/scripts`（所有Python脚本统一由此路径调用）

---

## 🛑 开始前必须执行（主agent自动完成，用户无需手动操作）

**用户只需说"开始筛选"，主agent自动检测并完成所有准备工作。**

主agent收到"开始筛选"后的自动流程：

```bash
SKILL_DIR=~/.workbuddy/skills/screen-repro/scripts

# 1. 检查项目是否已初始化
if [ ! -f "screening_progress.json" ]; then
    echo "🆕 首次使用，正在初始化项目..."
    PYTHONIOENCODING=utf-8 python $SKILL_DIR/progress_manager.py init
    # → 自动创建 progress.json + summary.csv + 从模板复制PICOS_RULES.md/RATE_LIMIT.md + 创建子目录
fi

# 2. 检查是否已设置文献总数
PROGRESS=$(PYTHONIOENCODING=utf-8 python $SKILL_DIR/progress_manager.py check 2>&1)
if echo "$PROGRESS" | grep -q "总数:0"; then
    # 自动从文献列表CSV中获取总数
    TOTAL=$(wc -l < dedup_pool_with_abstracts.csv 2>/dev/null || echo 0)
    TOTAL=$((TOTAL - 1))  # 减表头
    PYTHONIOENCODING=utf-8 python $SKILL_DIR/progress_manager.py set-total $TOTAL
    echo "📊 自动识别: 总文献数 = $TOTAL"
fi

# 3. 双重验证 + 查看进度
PYTHONIOENCODING=utf-8 python $SKILL_DIR/progress_manager.py verify
PYTHONIOENCODING=utf-8 python $SKILL_DIR/progress_manager.py check
```

**判断逻辑速查**：

| 检查项 | 文件/命令 | 不存在时 | 存在时 |
|--------|----------|---------|--------|
| 项目初始化 | `screening_progress.json` 存在？ | 执行 `init` | 跳过 |
| 文献总数 | `check` 输出 `总数:0`？ | 自动从 CSV 获取并 `set-total` | 跳过 |
| 断点恢复 | `verify` + `check` | — | 总是执行 |

> **用户只需要在首次使用时确认文献总数的准确性，之后无需任何手动操作。**
# → 自动修复 MD ↔ progress 不一致，修复规则：
#   - MD文件存在 但 progress未标记 → 标记为completed，记录"已恢复"
#   - MD文件存在 但 progress标记为pending → 标记为completed
#   - progress标记completed 但 MD文件不存在 → 从completed中移除，标记为pending
#   - progress标记completed 但 MD文件为空(0字节) → 从completed中移除，标记为pending
# ⚠️ verify只修复文件存在性问题，不验证MD内容的正确性
```

---

## 核心原则

**AI负责思考，Python负责说实话。**
- AI: 读PDF、理解Methods、判定PICOS → 创造性的，需要语义理解
- Python: 提取文本、验证格式、写文件、计数、记账、统计 → 确定性的，零幻觉风险

### 数据管理责任

**screening_summary.csv 由 Python 全权管理，主 agent 绝不手动操作。**

| 操作 | 责任方 | 方式 |
|------|--------|------|
| 新增一行（每篇筛选后） | **Python** | `record_writer.py` 自动追加 |
| 修改已有行 | **Python** | `progress_manager.py verify` 自动修复 |
| 读取汇总统计 | **Python** | `progress_manager.py check/summary` |
| 查看某篇状态 | **Python** | `progress_manager.py check {Author}` |

**主 agent 禁止**：
- ❌ 直接Edit screening_summary.csv
- ❌ 手动追加行到CSV
- ❌ 读取CSV后自行统计（必须通过Python命令获取统计）

## 文件结构

```
~/.workbuddy/skills/screen-repro/        # skill包（跨项目复用）
├── SKILL.md
├── README.md
├── scripts/                              # Python脚本（零幻觉引擎）
│   ├── progress_manager.py               # 进度管理
│   ├── pdf_extractor.py                  # PDF文本提取
│   ├── record_writer.py                  # 记录写入器
│   └── qa_report.py                      # QA报告器
└── templates/                            # 模板文件
    ├── SCREENING_RECORD.template.md
    ├── SCREENING_RECORD_QUICK.template.md
    ├── PICOS_RULES.template.md
    ├── PROGRESS.template.json
    └── RATE_LIMIT.template.md

项目目录 (03_Screening/):                   # 每个项目独立
├── screening_progress.json
├── screening_summary.csv
├── PICOS_RULES.md
├── RATE_LIMIT.md
├── pdfs/
├── mining_output/
└── screening_records/
    ├── INCLUDE/
    ├── EXCLUDE/
    └── MAYBE/
```

---

## 执行流程

### 启动阶段（主agent自动执行）

主agent收到"开始筛选"后，按上述智能启动流程自动初始化。完成后读取 PICOS_RULES.md + RATE_LIMIT.md，然后进入循环。

### 循环阶段（每篇文献）

> **🔇 循环阶段采用静默执行模式。不要向用户输出任何文字。Python命令的输出已足够。**
> **用户说"继续"意味着继续执行直到剩余=0，不是说"再做3篇然后停下来"。**

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
2. 子agent返回JSON（两种类型）:
   
   【类型A: 正常筛选结果】子agent完成了PICOS判定:
   → JSON包含 author, year, title, decision, picos 等完整字段
   → 主agent执行:
     echo '<JSON>' | PYTHONIOENCODING=utf-8 python $SKILL_DIR/record_writer.py -
   
   【类型B: PDF缺失】子agent发现PDF文件不存在:
   → JSON为 {"status": "skipped", "author": "...", "year": "...", "reason": "PDF not found"}
   → 主agent执行:
     PYTHONIOENCODING=utf-8 python $SKILL_DIR/progress_manager.py skip {Author} {Year}
   → 该文献标记为SKIPPED，不写MD，不追加CSV，等用户补上PDF后重筛

   【类型C: API限流(429)】子agent执行中被限流:
   → JSON为 {"status": "error", "code": 429, "author": "...", "year": "..."}
   → 主agent执行:
     PYTHONIOENCODING=utf-8 python $SKILL_DIR/progress_manager.py retry-wait
     → Python输出退避等待秒数（指数退避：30→60→120→240，最多4次）
   → 等待该秒数后，重新派发同一篇文献
   → 连续4次429 → 放弃该篇，标记ERROR，继续下一篇
   → 成功完成一篇后调用: PYTHONIOENCODING=utf-8 python $SKILL_DIR/progress_manager.py retry-reset

3. 等待间隔（从RATE_LIMIT.md读取）

### 子agent异常返回速查

子agent返回非正常JSON时，主agent按以下规则处理。
所有异常最终由Python记录，主agent不得自行"修复"子agent的错误结果。

| 返回内容 | 含义 | 主agent行动 | Python命令 |
|----------|------|-------------|-----------|
| `{"status":"skipped",...}` | PDF缺失 | 跳过该篇，继续下一篇 | `skip A Y` |
| `{"status":"error","code":429,...}` | API限流 | 退避→重试同一篇 | `retry-wait` |
| `{"status":"error","code":1,...}` | PDF提取失败 | 标记MAYBE，继续下一篇 | `update A Y MAYBE` |
| 非JSON/空返回 | 子agent崩溃 | 标记ERROR，继续下一篇 | `update A Y ERROR` |
| record_writer退出码≠0 | 格式验证失败 | 标记ERROR，不重试 | `update A Y ERROR` |
| retry-wait返回-1 | 连续4次429 | 放弃，标记ERROR | `update A Y ERROR` |
| 连续3篇返回非JSON | 子agent连续崩溃 | 标记ERROR，**报告用户** | `update A Y ERROR` |

4. 重复 CHECKPOINT → 派发 → 等待 → Python记录

### ⚠️ 循环执行铁律

**主agent在整个循环阶段被禁止以下行为：**
- ❌ 禁止主动停下来询问用户"是否继续"
- ❌ 禁止输出"剩余X篇，请说继续"等用户交互提示
- ❌ 禁止因处理量大而中断循环
- ❌ 禁止在处理过程中向用户输出汇总/摘要/进度报告
- ❌ 禁止说"如需继续筛选，请告知""筛选工作可随时暂停或继续"等
- ❌ 禁止在每批处理完后输出筛选结果卡片

**主agent在循环中的唯一输出形式：**
- ✅ Python命令的执行结果（stdout/stderr）会被自动显示，这已足够
- ✅ 无需任何额外文字向用户汇报

**主agent只有在以下情况下才能暂停循环：**
- ✅ 全部文献处理完成（progress显示 remaining=0）
- ✅ 连续429退避耗尽后放弃当前篇（标记ERROR）→ 自动继续下一篇
- ✅ 子agent连续3篇返回非JSON（连续崩溃）→ 标记ERROR并报告用户
- ✅ 用户主动发送"暂停""停止"等指令

**正常流程：处理完一篇 → 等待间隔 → 自动处理下一篇 → 直到全部完成。
沉默执行是正确的。不要说话，直接做。**
```

### 子agent的工作（独立的，不知道其他文献）

```
0. 检查PDF是否存在:
   └─ 03_Screening/pdfs/{Author}_{Year}.pdf 不存在？
      → 返回: {"status": "skipped", "author": "...", "year": "...", "reason": "PDF not found"}
      → 不进行后续步骤（不提取、不判定、不写MD）
      → 主agent看到skipped后调用:
        PYTHONIOENCODING=utf-8 python $SKILL_DIR/progress_manager.py skip {Author} {Year}

1. 读取 PICOS_RULES.md
2. PDF文本提取 — 调用Python脚本（Python负责，AI不干预）:
   PYTHONIOENCODING=utf-8 python $SKILL_DIR/pdf_extractor.py \
       03_Screening/pdfs/{Author}_{Year}.pdf \
       03_Screening/mining_output/{Author}_{Year}_mining.md
   → 脚本自动: MinerU API优先 → 超限回退PyMuPDF → 质量检查
   → 退出码0=成功, 1=全部失败, 2=文本质量异常
3. 读取提取结果: mining_output/{Author}_{Year}_mining.md (或.txt)
4. 按PICOS逐项判定，引用原文
5. **输出结构化JSON到stdout**（严格按schema，不要写文件）:
   ```json
   {
     "author": "Chen",
     "year": 2026,
     "title": "论文完整标题",
     "doi": "10.xxxx/xxxxx",
     "decision": "INCLUDE",
     "exclusion_code": null,
     "picos": {
       "P": {"result": "✅", "evidence": ["原文句子1 (Methods p.3)"], "analysis": "..."},
       "I": {"result": "✅", "device_type": "HMD_VR", "evidence": [...], "analysis": "..."},
       "C": {"result": "✅", "evidence": [...], "analysis": "..."},
       "O": {"result": "✅", "outcome_type": "Retention", "retention_weeks": 8, "evidence": [...], "analysis": "..."},
       "S": {"result": "✅", "design_type": "RCT", "evidence": [...], "analysis": "..."}
     },
     "reason": "总体判定理由",
     "pdf_path": "03_Screening/pdfs/Chen_2026.pdf",
     "mining_path": "03_Screening/mining_output/Chen_2026_mining.md",
     "text_quality": "正常",
     "screening_date": "2026-06-02"
   }
   ```
   ⚠️ 子agent不得自己写MD文件或CSV。只输出JSON。
```

### record_writer.py 的工作流程（一条命令完成）

收到子agent JSON后，主agent将JSON通过管道传给record_writer.py：

```
echo '<JSON>' | PYTHONIOENCODING=utf-8 python $SKILL_DIR/record_writer.py -
```

脚本自动完成以下步骤（Python负责，AI不参与）：

```
1. 验证JSON:
   ├─ 必填字段检查 (author, year, title, decision, picos)
   ├─ 决策合法性检查 (INCLUDE/EXCLUDE/MAYBE)
   ├─ 排除码合法性检查 (E1~E9 或空)
   ├─ EXCLUDE必须有排除码, INCLUDE/MAYBE不应有排除码
   └─ PICOS五维度 result/evidence/analysis 完整性检查
   → 任一不通过 → 退出码1，拒绝写入

2. 填入模板:
   └─ 读取内置模板 → 替换占位符 → 生成MD内容

3. 写入MD:
   └─ screening_records/{decision}/{Author}_{Year}.md

4. 追加CSV:
   └─ screening_summary.csv 追加一行

5. 更新进度:
   └─ screening_progress.json 更新 processed/remaining/results
```

### 完成阶段

```
PYTHONIOENCODING=utf-8 python $SKILL_DIR/progress_manager.py summary
→ 输出最终报告 + 标记done

PYTHONIOENCODING=utf-8 python $SKILL_DIR/qa_report.py generate
→ 生成 QA_REPORT.md（MAYBE清单+随机抽样INCLUDE/EXCLUDE）
→ 提醒用户进行人工复核
```

### QA阶段（用户操作，AI不参与判定）

```
用户阅读 QA_REPORT.md
  │
  ├─ MAYBE复核:
  │   → 用户阅读 screening_records/MAYBE/{文献}.md
  │   → PYTHONIOENCODING=utf-8 python $SKILL_DIR/qa_report.py resolve Smith 2024 INCLUDE "复核理由"
  │   → Python: 移动MD→INCLUDE/ | 更新CSV | 更新progress
  │
  ├─ INCLUDE抽样:
  │   → 用户阅读 screening_records/INCLUDE/{文献}.md
  │   → PYTHONIOENCODING=utf-8 python $SKILL_DIR/qa_report.py confirm Chen 2026 INCLUDE
  │   → Python: 标记MD末尾QA确认 | 更新qa_state.json
  │
  └─ EXCLUDE抽样:
      → 用户阅读 screening_records/EXCLUDE/{文献}.md
      → PYTHONIOENCODING=utf-8 python $SKILL_DIR/qa_report.py confirm Li 2022 EXCLUDE
      → Python: 标记MD末尾QA确认 | 更新qa_state.json

随时查看QA进度:
  PYTHONIOENCODING=utf-8 python $SKILL_DIR/qa_report.py status
```

---

## Python命令速查

| 命令 | 何时用 |
|------|--------|
| `pdf_extractor.py <pdf> <out>` | 每篇PDF提取（子agent执行） |
| `record_writer.py '<JSON>'` | 子agent完成后写入全部记录 |
| `skip First Year` | PDF缺失时跳过文献 |
| `qa_report.py generate` | 筛选完成后生成QA报告 |
| `qa_report.py resolve First Year Dec Reason` | MAYBE→最终判定 |
| `qa_report.py confirm First Year Dec` | 抽样复核确认 |
| `qa_report.py status` | 查看QA进度 |
| `init` | 首次运行，创建文件 |
| `set-total N` | 开始筛选前 |
| `check` | 每次启动/查看进度 |
| `verify` | 每次启动/检查一致性 |
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

*skill版本: v2.1*
*创建日期: 2026-06-02*
*配套Python脚本: ~/.workbuddy/skills/screen-repro/scripts/*
