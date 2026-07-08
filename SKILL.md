---
name: screen-repro
description: >
  screen-repro v3.6 — 专门用于系统综述/Meta分析中PICOS文献筛选的自动化工具。
  程序化优先：流程控制、校验、备份、报告全部程序化；PDF映射真实性必须先通过程序化门禁，AI仅保留给全文PICOS判定。
triggers: >
  screen-repro, 筛选, screen, 筛选文献, 筛选PDF, 全文筛选, 逐篇筛选,
  PICOS筛选, 筛选第, screen paper, fulltext screening, 文献筛选, 可复现筛选
tools: Read, Write, Edit, Bash, Glob
model: inherit
---

# screen-repro v3.6 — 程序化优先的PICOS文献筛选系统

> **程序主导，AI辅助，可复现为本。**

## 项目定位

**screen-repro** 是一个专门用于 **系统综述/Meta分析** 中 **PICOS文献筛选** 的自动化工具。

**核心价值**：
- **程序化优先**：流程控制、数据校验、备份恢复、报告导出全部由程序完成
- **AI最小化**：AI仅用于全文PICOS语义判定，不参与流程控制
- **PDF映射真实性**：题录-全文映射必须程序化验收；`pdf_path` 不是证据，只有 DOI/标题/sha256/重复哈希/人工确认门禁通过后才可进入锁定池
- **可复现性**：同一 PDF / 文本 / 规则 / prompt / 模型配置 / 脚本版本必须形成可校验指纹；AI 后端若发生版本漂移，也必须能定位输入、配置、响应和程序版本差异
- **零幻觉**：所有判定必须引用原文，禁止推测或补全
- **无人值守**：一键启动，自动循环，断点恢复

**适用场景**：系统综述/Meta分析的全文筛选阶段，需要按PICOS标准判定文献，文献量大（100+篇），需要AI辅助加速。

---

> **核心理念**：Python是编排器，AI是一个可调用的函数。
> **数据方案**：SQLite（权威数据源） + PDF资产/映射验收表 + MD文件（人类可读） + CSV（导出格式）
> **SKILL_DIR** = `~/.workbuddy/skills/screen-repro/scripts_v3.0`
> **硬规则**：PDF映射 QC 未通过时，不得冻结 P3 锁定池，不得进入 P4/RoB/Meta。

---

## 设计哲学（V3.6）

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
init → import → gate → map → pdf-qc → extract → rule_prescreen → judge → lock-qc → picos-audit → report
  │       │       │      │       │          │           │            │         │            │
  │       │       │      │       │          │           │            │         │            └─ 100%程序
  │       │       │      │       │          │           │            │         └─ 71篇锁池PICOS高风险审计（100%程序，输出人工复核清单）
  │       │       │      │       │          │           │            └─ 锁池/PDF映射终检（100%程序，FAIL则禁止下游）
  │       │       │      │       │          │           └─ AI判定（唯一AI环节）
  │       │       │      │       │          └─ PDF文本抽取（PyMuPDF优先）
  │       │       │      │       └─ 题录-PDF映射真实性门禁（100%程序）
  │       │       │      └─ PDF映射+备份（100%程序）
  │       │       └─ 数据库/目录/输入前置校验（100%程序）
  │       └─ RIS解析入库（100%程序）
  └─ 目录/库初始化（100%程序）
```

### PDF映射事故防复发硬规则

- 将 `pdf_path` 视为待验证字段，而不是可信证据。
- 在 PDF 映射后、全文筛选前，运行程序化 `pdf-qc` 门禁；在生成最终锁定池后，运行 `lock-qc` 终检。
- 只允许 `PASS` 或 `admin_verified=true` 的题录-PDF映射进入全文 PICOS 判定和最终锁定池。
- 遇到同一 `sha256` 对应多个不同 DOI/标题时，直接判为 FAIL，除非人工明确标记为同一论文重复记录。
- 遇到 YAML 不能解析、非法控制字符、PDF 不存在、sha256 不一致、admin 排除文献残留于锁定池/P4/RoB 时，直接判为 FAIL。
- DOI 不在 PDF 前若干页、标题相似度低、PDF 文本提取失败时，至少判为 WARN，并进入人工复核队列。
- 不允许用“文件存在”“hash 可计算”“数据库有记录”替代题录-全文一致性验证。

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

# 步骤2：PDF映射（可自动/半自动，但不得直接信任结果）
python screen.py pdf map

# 步骤3：PDF映射/锁池门禁（必须程序化运行；FAIL则停止）
python scripts/p3_lock_qc.py --project . --lock 03_Screening/FINAL_POOL_LOCK.yaml

# 步骤4：正式筛选（含AI判定；仅对PDF映射门禁通过的记录运行）
python screen.py run

# 步骤5：最终锁定池终检（进入P4/RoB/Meta前必须运行）
python scripts/p3_lock_qc.py --project . --lock 03_Screening/FINAL_POOL_LOCK.yaml

# 步骤6：PICOS 高风险审计（锁池不是绝对真理；输出人工复核清单）
python scripts/p3_picos_lock_audit.py --project . --lock 03_Screening/FINAL_POOL_LOCK.yaml
```

**执行纪律**：`p3_lock_qc.py` 返回非 0 时，停止后续 P4/RoB/Meta；先修复 FAIL，再重跑 QC。WARN 不自动阻断，但必须在报告中解释或人工裁决。

**QC 存放纪律**：阶段内 QC 跟着阶段走。P3 的 PDF 映射 QC、锁池 QC、PICOS 高风险审计和全量二次复核报告默认写入 `03_Screening/qc/`；`08_QC/` 仅用于跨阶段总审计、最终提交前总 QC 或阶段 QC 索引。

---

## 命令速查

| 命令 | 说明 |
|------|------|
| `screen.py init` | 初始化项目（创建目录、数据库、config模板） |
| `screen.py import --ris xxx.ris` | 导入RIS文件 |
| `screen.py run` | 执行筛选循环（自动从断点恢复，含规则预筛+AI判定） |
| `screen.py run --batch N` | 筛选N篇后暂停 |
| `screen.py check` | 查看进度 |
| `screen.py verify` | 验证数据库、进度缓存与MD文件一致性 |
| `screen.py audit-rules` | 只读规则审计：扫描VR内部比较误纳入、E4误排transfer/retention、knowledge retention误纳入等高风险候选 |
| `screen.py qa generate --include-rate 0.1 --exclude-rate 0.1 --seed 20260629` | 按比例生成INCLUDE/EXCLUDE质量复核样本；只输出CSV和报告，不修改数据库 |
| `screen.py qa status` | 查看已生成的QA样本文件 |
| `screen.py summary` | 汇总报告 |
| `screen.py report` | 生成完整筛选报告（含决策分布、排除原因、年份分布、PICOS通过率、质量检查、筛选效率） |
| `screen.py export` | 导出CSV |
| `screen.py migrate` | 从v2.3迁移 |
| `screen.py pdf map` | PDF映射；执行前会校验数据库非空、创建 `_backups/*_before_pdf_map` 备份，并拒绝在错库/空库上运行；映射结果仍必须通过 `p3_lock_qc.py` 验收 |
| `scripts/p3_lock_qc.py --project . --lock 03_Screening/FINAL_POOL_LOCK.yaml` | P3锁定池/PDF映射程序化门禁：检查YAML可解析性、非法控制字符、PDF存在、sha256、重复哈希、DOI/标题匹配、admin纳入/排除状态、P4/RoB残留；FAIL则禁止进入下游 |
| `scripts/p3_picos_lock_audit.py --project . --lock 03_Screening/FINAL_POOL_LOCK.yaml` | P3纳入锁池PICOS高风险审计：基于筛选记录C/O/S分析段和证据句反查，识别疑似VR内部比较、经验组/效度验证、无对照、证据不在当前txt、缺筛选记录等风险；只输出CSV/MD/JSON人工复核清单，不自动改判定 |
| `screen.py prescreen` | 遗留模式：预筛选+AI复核+人机协同（兼容旧流程） |
| `screen.py workflow --ris xxx.ris` | 一键执行完整流程 |

### 进度监控与断点可见性（最低优先级必须实现）

P3 的首要 UI 诉求不是复杂裁决系统，而是让用户实时知道程序是否仍在运行、运行到哪一篇、是否断掉、断在哪里。任何长任务入口都应写入机器可读的进度心跳文件，例如 `03_Screening/qc/runtime/p3_runtime_status.json` 与追加式 `03_Screening/qc/runtime/p3_runtime_events.jsonl`。

最低字段应包括：

```json
{
  "run_id": "20260706_183500",
  "stage": "full_reaudit",
  "status": "running",
  "current_uid": "Example_2024_xxxxxx",
  "done": 37,
  "total": 71,
  "last_success_uid": "Previous_2023_xxxxxx",
  "last_error": null,
  "started_at": "2026-07-06T18:35:00+08:00",
  "updated_at": "2026-07-06T18:42:10+08:00",
  "heartbeat_age_seconds": 8,
  "can_resume": true
}
```

监控规则：

- 每完成 1 篇、每次 API 调用前后、每次写库/写报告后，都必须更新 `updated_at` 和进度计数。
- 若 `status=running` 但 `updated_at` 超过阈值未变化，例如 5-10 分钟，应在 UI 中显示为 `可能卡住`，而不是继续显示正常运行。
- 若进程异常退出，下一次启动 `screen.py check` 或 `p3 monitor` 应能根据最后心跳、最后成功 UID、错误日志判断是否断掉以及从何处恢复。
- UI 第一版只需要读取这些 JSON/JSONL 和数据库，不直接执行筛选逻辑；核心执行仍由 CLI 状态机负责。
- 每次开始筛选、复核或对比这类长任务时，应自动启动本地监控服务并打开页面，避免用户记忆网址。标准实现统一放在 `scripts_v3.0/p3_monitor_server.py`、`scripts_v3.0/p3_runtime_status.py`、`scripts_v3.0/p3_monitor.html`；项目内只保留轻量 wrapper/入口和 `03_Screening/qc/runtime/` 运行数据，避免脚本散落。
- 监控模块必须可迁移：除 Python 标准库外不依赖 WorkBuddy 专有 API；迁移到其他 agent 软件时，可复制 `screen-repro/` 到项目根目录，或设置 `SCREEN_REPRO_DIR` 指向 skill 根目录；标准脚本支持 `--project`、`--run-script`、`--stage-dir`、`--checkpoint-dir` 参数，不得写死用户本机路径。
- 监控服务必须单实例运行：启动时先获取本地锁或检查旧监控服务；若已有监控服务运行，默认拒绝启动第二个并返回已有页面地址，不得自动顺延端口创建多个监控页。
- 监控服务不能只显示卡住，必须具备 supervisor：当状态为 `stale`、心跳超过阈值、任务未完成且可恢复时，自动调用断点恢复入口，并把 `auto_resume_started`、`child_finished`、`progress_observed` 写入 `03_Screening/qc/runtime/p3_supervisor_events.jsonl`；当前监督器状态写入 `p3_supervisor_state.json`；自动/手动续跑子进程输出必须写入日志文件，不得用未消费的 PIPE 长时间捕获 stdout/stderr，以免堵塞子进程。
- 监控 UI 应提供“继续跑/断点续跑”手动按钮，调用本地监控服务的受控恢复接口（如 `POST /api/resume`），用于用户确认卡住后立即启动 resume；接口必须先扫描已有续跑工作进程，若已有进程则拒绝启动第二个，并记录 `manual_resume_started` / `manual_resume_ignored` / `manual_child_finished`，不得由前端直接改数据库、锁池、PDF、txt 或 screening records。
- 用户最小可接受界面应优先显示：当前阶段、完成数/总数、百分比、最后心跳时间、当前 UID、最后成功 UID、最近错误、是否可断点续跑、auto-resume 是否开启、恢复子进程是否运行、下一步建议。

---

## 配置

编辑项目目录下的 `config.json`：

```json
{
  "llm_backend": "openai",
  "active_profile": "primary_gpt55",
  "openai": {
    "api_key": "sk-...",
    "model": "gpt-5.5",
    "base_url": "https://api.example.com/v1",
    "rpm": 100,
    "tpm": 10000000
  },
  "review_profiles": {
    "primary_gpt55": {
      "llm_backend": "openai",
      "api_key": "sk-...",
      "model": "gpt-5.5",
      "base_url": "https://api.example.com/v1",
      "role": "main_judge"
    },
    "review_claude_opus_4_8": {
      "llm_backend": "openai",
      "api_key": "sk-...",
      "model": "claude-opus-4-8",
      "base_url": "https://api.example.com/v1",
      "role": "reviewer_or_adjudicator"
    }
  },
  "rate_limit": {
    "safety_margin": 0.8
  },
  "picos_rules_path": "PICOS_RULES.md"
}
```

`review_profiles` 用于双模型复核/仲裁。默认 `screen.py run` 仍读取顶层 `openai` 配置；如需单篇或抽样用备用模型复核，可直接运行 `picos_judge.py --profile review_claude_opus_4_8 ...`。OpenAI-compatible 中转地址必须写到 `/v1`，脚本会请求 `{base_url}/chat/completions`。配置文件中可以保存 API key，但报告、日志和对用户回复中不得明文泄露密钥。

支持的后端：`openai`、`anthropic`、`ollama`

---

## PDF映射与数据库门禁（V3.6 必须执行）

### 为什么数据库仍需门禁

SQLite 是权威数据源，但数据库只能稳定保存已写入的数据，不能天然证明 `pdf_path` 对应的 PDF 就是该题录的正确全文。若错误 PDF 映射被写入数据库，后续可复现流程会稳定复现错误。因此，PDF 映射必须作为独立资产和独立状态进行程序化验收。

### 推荐数据库结构

在新项目或升级项目中，优先将 PDF 从 `papers.pdf_path` 的普通字符串升级为独立资产：

```sql
CREATE TABLE IF NOT EXISTS pdf_assets (
    pdf_id INTEGER PRIMARY KEY AUTOINCREMENT,
    file_path TEXT NOT NULL,
    sha256 TEXT NOT NULL UNIQUE,
    page_count INTEGER,
    extracted_title TEXT,
    extracted_doi TEXT,
    first_pages_text TEXT,
    created_at TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS paper_pdf_links (
    key TEXT NOT NULL REFERENCES papers(key),
    pdf_id INTEGER NOT NULL REFERENCES pdf_assets(pdf_id),
    match_method TEXT,
    doi_match TEXT,              -- PASS/WARN/FAIL/NA
    title_similarity REAL,
    duplicate_sha_risk TEXT,     -- PASS/WARN/FAIL
    evidence_quote_check TEXT,   -- PASS/WARN/FAIL/NA
    status TEXT NOT NULL,        -- PASS/WARN/FAIL/ADMIN_REVIEW
    admin_verified INTEGER DEFAULT 0,
    admin_note TEXT,
    checked_at TEXT DEFAULT (datetime('now')),
    PRIMARY KEY (key, pdf_id)
);
```

### 锁池生成规则

- 从数据库生成 `FINAL_POOL_LOCK.yaml` 前，必须确认每条纳入记录满足：`paper_pdf_links.status='PASS' OR admin_verified=1`。
- `paper_pdf_links.status='FAIL'` 的记录不得进入全文筛选、最终锁定池、P4、RoB 或 Meta。
- `paper_pdf_links.status='WARN'` 的记录必须有人工复核说明；无说明时不得冻结锁定池。
- 生成锁定池后再次运行 `scripts/p3_lock_qc.py`，以文件级门禁反查数据库导出的结果。

### 活跃筛选入口内置门禁

`screen.py run` 不得只因为 `pdf_mapping.json` 存在就继续筛选。活跃入口 `scripts_v3.0/screen.py` 必须在运行前执行题录-PDF 一致性检查：

- `_pdf_mapping_ready()` 必须验证 mapping 文件存在、每个映射 PDF 存在、候选 PDF 通过 DOI 或标题前页重叠检查、同一 sha256 未映射到不同 DOI/标题。
- `_find_pdf()` 只把 mapping/key/fuzzy 命中当作候选来源，候选 PDF 未通过一致性验证时不得返回给 AI 判定。
- mining/text 缓存必须绑定当前 PDF sha256；改挂 PDF 后，如果缓存无 sha256 元数据或 hash 不一致，必须重新提取全文。
- 旧兼容入口 `scripts/screen.py` 应转发到 `scripts_v3.0/screen.py`，避免误调用没有 PDF 映射门禁的历史实现。

### `scripts/p3_lock_qc.py` 用法

将脚本复制到项目或直接从 skill 路径运行：

```bash
python ~/.workbuddy/skills/screen-repro/scripts/p3_lock_qc.py \
  --project . \
  --lock 03_Screening/FINAL_POOL_LOCK.yaml \
  --expected-included 71 \
  --admin-included ChenS_2026_a6c739 \
  --admin-excluded ChenP_2026_31ed5c \
  --admin-excluded JaudC_2021_5c33a0
```

脚本默认输出到 `03_Screening/qc/lock_qc/*.md` 与 `*.json` 报告，并以退出码表达门禁结果：`0=PASS`，`1=FAIL`。如需跨阶段总审计副本，可显式传入 `--report-dir 08_QC/...`。

### `scripts/p3_picos_lock_audit.py` 用法

锁池/PDF 映射 QC 通过后，仍不能假定所有 PICOS 学术判断完全正确。必须再运行聚焦式高风险审计：

```bash
python ~/.workbuddy/skills/screen-repro/scripts/p3_picos_lock_audit.py \
  --project . \
  --lock 03_Screening/FINAL_POOL_LOCK.yaml
```

该脚本只读取锁池、`txt/` 和 `screening_records/`，不修改数据库或筛选结论。它重点检查：

- C/S：筛选记录的 C/S 证据与分析段是否出现经验组比较、效度验证、无对照、单组前后测、VR 内部比较等高风险信号。
- O：O 维度是否以自评、知识、满意度、即时后测为主，缺少客观技能保持/迁移信号。
- Evidence grounding：筛选记录中的 PICOS 原文证据句是否能在当前 `txt` 中反查。
- Record completeness：锁池纳入文献是否缺少人类可读筛选记录。

输出：`03_Screening/qc/picos_audit/*.csv`、`*.json`、`*.md`。HIGH 项不是自动排除结论，但必须进入 admin review；MEDIUM 项应抽查或在报告中解释。

### FAIL 项处理规范

出现以下任一 FAIL 时，立即停止后续阶段：

- YAML 无法解析或含非法控制字符。
- `records` 数量与 `total_included` 不一致。
- `pdf_path` 不存在。
- 文件实际 sha256 与锁定池 sha256 不一致。
- 同一 sha256 对应多个不同 DOI/标题。
- admin 已排除 UID 仍在锁定池、P4 TSV 或 RoB 活跃目录。
- admin 应纳入 UID 不在锁定池。

### WARN 项处理规范

出现以下 WARN 时，不自动阻断，但必须进入人工复核或报告解释：

- DOI 未在 PDF 前 3 页检出。
- 标题关键词与 PDF 前 3 页重叠率低。
- PDF 文本无法提取。
- 旧论文、扫描版或出版商格式导致 DOI 不显示。

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

**排除码**：E7（综述/理论类文献）

---

## 可复现性保证

- **确定性配置**：AI 调用默认 `temperature=0`，如后端支持 `seed` 则固定并记录；若后端不支持 seed 或模型版本可能漂移，报告必须标注为“可审计复跑”而非数学意义的位级确定。
- **多维指纹缓存**：至少记录 `pdf_sha256 + extracted_text_sha256 + rules_sha256 + prompt_sha256 + model_id + model_version_or_endpoint + script_sha256 + extraction_method`。
- **原始输入/输出归档**：每次 AI 判定必须保存请求摘要、脱敏配置、原始响应、解析后的 JSON、质量检查结果和重试记录；不得只保存最终 INCLUDE/EXCLUDE。
- **PDF映射指纹**：锁定池必须记录 `pdf_path` 与 `sha256`；程序化核验实际文件 hash、重复 hash、DOI/标题匹配情况，防止错误全文被稳定复现。
- **运行清单 manifest**：每次筛选/复核/监控快照应写入机器可读 manifest，记录 Python 版本、平台、脚本 hash、checkpoint/progress log hash、配置路径和关键参数；迁移到其他 agent 软件后先比对 manifest 再解释结果差异。
- **运行产物配对**：checkpoint 与 progress log 必须按同一 `run_id` 配对；不得分别取“最新 checkpoint”和“最新 progress log”后混用。无法配对时必须在 manifest 中标记 `artifact_pairing_status=UNPAIRED` 或 `MISSING`，禁止把混配状态显示为正常。
- **关键输入指纹**：监控/复核状态 manifest 必须纳入 `config.json`、项目/阶段 `PICOS_RULES.md`、`FINAL_POOL_LOCK.yaml` 等关键输入文件的路径、存在性和 sha256；缺失文件也要显式记录，不得静默忽略。
- **子进程隔离**：每次 AI 判定在独立进程，无上下文污染；长任务 stdout/stderr 写入日志文件，不用未消费的 PIPE 长时间捕获，避免子进程阻塞。
- **写库前硬门禁**：`record_writer.py` 会根据 P/I/C/O/S 程序化重算最终决策和排除码；若发现模型输出与重算结果冲突，或命中 VR 内部比较、E4/transfer、knowledge retention 等高风险语义冲突，自动转为 `MAYBE`，禁止直接写入 `INCLUDE/EXCLUDE`。
- **PDF文本规范化**：NFKC + 空白规范化；文本提取工具、版本、页数、字符数、文本 hash 必须记录，换工具或 PDF hash 变化时必须重提取并使缓存失效。
- **人工裁决可追溯**：任何 `admin_verified`、MAYBE 裁决、WARN 放行都必须记录裁决人/时间/理由/证据句/受影响 UID，不允许只改最终状态。

---

## v2.3兼容性

```bash
python screen.py migrate
```

自动导入v2.3的CSV、progress.json、screening_records到SQLite。

---

### 二次程序审计补丁（2026-07-06）

- `picos_judge.py` 的质量检查器必须接受 `E10`；evidence 长句能在当前全文 txt 中反查是质量 warning，而不是自动把整篇判为 MAYBE 的严重错误。反查失败应写入 `evidence_grounding_warning` / `evidence_grounding_issues`，进入后续 QC 或 admin review。
- `record_writer.py` 的 VR 内部比较硬门禁必须覆盖 junior/senior/expert、novice/expert、known-groups、validation study、Messick 等经验分层/效度验证信号，防止 JaudC 类研究被当成非 VR 对照。
- `p3_lock_qc.py` 必须检查当前 `screening.db` 与锁池的一致性；若 admin-excluded UID 在数据库中仍为 INCLUDE，应判为 FAIL，而不是仅 WARN。旧数据库未同步时，不得从数据库重新导出锁池。
- `scripts/p3_lock_qc.py` 应作为兼容包装器转发到 `scripts_v3.0/p3_lock_qc.py`，避免双副本漂移。
- OpenAI-compatible API 调用必须记录可诊断但不泄露密钥的错误信息：HTTP status code、JSON mode 是否启用、响应前缀（脱敏后）。如果中转不支持 `response_format={"type":"json_object"}`，应记录 JSON mode 失败并自动去掉 `response_format` 重试一次。
- PICOS prompt 必须对 E10/E5 设置硬规则：两组均使用 VR/virtual simulator/LapSim/LapMentor/MIST-VR/dVSS/EyeSi/Simodont/VirtaMed/ANGIO Mentor 等虚拟训练而仅反馈、haptic、2D/3D、训练目标或协议不同 → EXCLUDE/E10；无独立对照、baseline vs post-test、单组前后测或只比较保留/迁移时间点 → EXCLUDE/E5。

---

## V3.6 更新说明（2026-07-08）

**版本号**：v3.5 → v3.6

**背景**：P3 长任务监控与双模型复核进入可迁移封装后，单纯显示“最新进度”不足以支撑可复现审计。若 checkpoint 与 progress log 来自不同历史运行，或者关键输入规则/锁池没有 hash，系统会稳定展示一个看似正常但证据链混配的状态。因此 v3.6 将运行监控从“进度可见”升级为“证据链可审计”。

### 修复 1：运行产物按 `run_id` 配对

- `p3_runtime_status.py` 按同一 `run_id` 选择 checkpoint 与 progress log。
- 禁止分别取“最新 checkpoint”和“最新 progress log”后混用。
- 无法配对时显式输出 `artifact_pairing_status=UNPAIRED` 或 `MISSING`，不得显示为正常审计链。

### 修复 2：运行清单纳入关键输入指纹

- `reproducibility_manifest` 新增 `config.json`、项目/阶段 `PICOS_RULES.md`、`FINAL_POOL_LOCK.yaml` 的路径、存在性、mtime、size 与 sha256。
- 缺失文件也必须以 `exists=false` 记录，不能静默忽略。
- 迁移到其他 agent 软件或其他机器后，应先比较 manifest，再解释进度或结论差异。

### 修复 3：监控与状态脚本可迁移

- `p3_monitor_server.py` 与 `p3_runtime_status.py` 支持 `--project`、`--run-script`、`--stage-dir`、`--checkpoint-dir`。
- 相对路径统一按 `--project` 解析，避免因当前 shell 工作目录不同导致指向错误文件。
- 项目内只保留轻量 wrapper；可通过复制 `screen-repro/` 或设置 `SCREEN_REPRO_DIR` 迁移到其他环境。

### 修复 4：命令行可直接审计配对状态

- `p3_runtime_status.py` 终端摘要新增 `run_id` 与 `artifact_pairing_status`。
- 当前项目验证输出为 `completed`、`PAIRED`、52/52，可作为完整复核运行状态的可审计快照。

---

## V3.5 更新说明（2026-07-06）

**版本号**：v3.4 → v3.5

**背景**：P3 admin review 发现 ChenP/ChenS 题录-全文错配，且锁定池修复后程序化 QC 又发现 Wierinck 两条不同题录共用同一 PDF 哈希。该事故说明：数据库和可复现流程只能稳定保存输入，不能自动证明 PDF 映射正确。必须将题录-全文一致性改为 P3 的硬门禁。

### 修复 1：新增 PDF 映射真实性原则

- `pdf_path` 仅是待验证字段，不是可信证据。
- 文件存在、hash 可计算、数据库有记录，均不等同于题录-全文一致。
- 同一 `sha256` 对应多个不同 DOI/标题时，必须 FAIL 或进入人工确认的重复记录流程。

### 修复 2：新增 `scripts/p3_lock_qc.py`

新增程序化锁池/PDF映射 QC 脚本，检查：

- YAML 可解析性与非法控制字符。
- `records` 数量与 `total_included` 一致性。
- PDF 文件存在性。
- 实际 sha256 与锁定池 sha256 一致性。
- 同一 sha256 是否映射到多个不同题录。
- DOI 是否出现在 PDF 前若干页。
- 标题关键词与 PDF 前若干页文本的重叠率。
- admin 纳入/排除 UID 是否符合锁定池。
- admin 排除 UID 是否残留于 P4 TSV 或 RoB 活跃目录。

脚本以退出码实现门禁：`0=PASS`，`1=FAIL`。FAIL 未清零前，不得进入 P4/RoB/Meta。

### 修复 3：推荐数据库资产化设计

新增 `pdf_assets` 与 `paper_pdf_links` 推荐结构，将 PDF 从 `papers.pdf_path` 字符串升级为可审计资产。锁定池只能从 `paper_pdf_links.status='PASS' OR admin_verified=1` 的记录生成。

### 修复 4：锁池冻结纪律

P3 锁池冻结必须执行两次程序化 QC 和一次高风险审计：

1. PDF 映射后、AI 全文筛选前，运行 PDF 映射 QC。
2. `FINAL_POOL_LOCK.yaml` 生成后、进入 P4/RoB/Meta 前，运行锁池终检。
3. 锁池终检通过后，运行 `p3_picos_lock_audit.py`，生成 C/O/S 与 evidence grounding 高风险人工复核清单。

任何 FAIL 都必须先修复、备份、重跑 QC；WARN 必须解释或人工裁决。`p3_picos_lock_audit.py` 的 HIGH 项不是自动改判定，但不得忽略，必须由 admin 复核或形成说明。

---

## V3.4 更新说明（2026-06-30）

**版本号**：v3.3 → v3.4

**背景**：2026-06-30 全量人工复核发现 `INCLUDE` 集中有 10 条（12%）确认误纳入（8 条 VR 内部比较、2 条知识保持误当技能保持）。下文为修复内容。

### 修复 1：Prompt 增强（picos_judge.py）

**C维度**：增加具体示例（两组都在同一VR/模拟训练设备上的场景、HMD vs Desktop比较场景），明确"即使有对照组但对照也用了VR训练→FAIL"。

**O维度**：明确区分知识保持（knowledge retention，笔试/选择题/MCQ）和程序性技能保持（skill retention，OSATS/DOPS/操作评估），并给出具体示例。

### 修复 2：硬门禁去循环依赖（record_writer.py）

**`_has_internal_vr_comparator()`**：
- 接受可选的 `i_data` 参数进行结构性检测
- 当 C=PASS 但 I 维度文本中同时出现多个模拟器/VR术语（≥2次）时，触发VR内部比较警告
- 不再仅依赖AI自述文本中的关键词

**`_knowledge_retention_risk()`**：
- 删除 `procedural_terms` 负向覆盖机制（"skill"、"performance" 等词过于宽泛，AI在无关上下文中也会使用，导致门禁被错误抑制）
- 改为正向匹配特定评估工具术语（OSATS/DOPS/GRS/completion time/accuracy等）
- 只有知识术语匹配 + 没有特定评估工具术语 → 触发知识保持风险

### 修复 3：新增排除码 E10

| 排除码 | 含义 | 判定标准 |
|--------|------|----------|
| **E10** | VR内部比较 | 两组均使用VR/模拟训练，无非VR对照 |

`derive_decision()` 中 C=FAIL 时：
- 如果 `_has_internal_vr_comparator()` 触发 → E10（VR内部比较）
- 否则 → E5（无对照组，原行为）

**兼容性**：新增代码不影响旧数据，E10 仅用于新筛选轮次。

### 修复 4：API URL 拼接修复

**问题**：config.json 中 `base_url` 末尾包含斜杠（`https://balabalabala.top/`），与代码中 `f"{self.base_url}/chat/completions"` 拼接后产生双斜杠 URL（`//chat/completions`），Nginx 归一化为 `/chat/completions` 后路由不存在，API 返回管理后台 HTML 页面而非 JSON 响应，导致全部 LLM 调用 JSON 解析失败。

**修复**：
- config.json：`base_url` 末尾 `/` 改为 `/v1`
- picos_judge.py：`OpenAIBackend.__init__()` 中 `.rstrip("/")` 防止未来配置末尾误带斜杠

---

*详细设计文档：`03_Screening/screen_repro_v3_design.md`*
