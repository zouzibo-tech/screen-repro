---
name: screen-repro-v1
description: >
  ⚠️ 已废弃。v1.0版本（纯AI版），请使用 @screen-repro（v2.0 AI+Python版）。
  v1.0保留仅供历史参考。
triggers: >
  screen-repro-v1, 旧版筛选
tools: Read, Write, Edit, Bash, Glob
model: inherit
---

# screen-repro v1.0 — 纯AI版（已废弃）

> **⚠️ 此版本已废弃，请使用 @screen-repro（v2.0 AI+Python版）**
> **保留仅供历史参考**
---

# screen-repro — 可复现的逐篇全文筛选协议 v2.3

---

## 🛑 开始前必须执行——每次对话只执行一次

**在派发任何子agent之前，主agent必须先打开并完整阅读本skill的 §7.1 中的 CHECKPOINT 检查框。**

如果跳过此步骤导致并发派发多个子agent → 违反skill协议，工作作废。

---

> **设计原则**：零幻觉、全程溯源、逐篇独立、上下文隔离、拿不准就写MAYBE
> **设计日期**：2026-05-31
> **适用阶段**：P3 Round 2-4（标题/摘要初筛后的全文验证）

---

## 一、核心规则

### 1.1 零幻觉原则

| 规则 | 说明 |
|------|------|
| **只读原文** | 所有判断必须基于PDF提取的原文文本，禁止基于记忆、推测或外部知识 |
| **逐字引用** | 每条判定依据必须是论文中的**原文逐字引用**，用 `> "..."` 格式标注 |
| **标注来源** | 每条引用必须标注出处：页码 + 段落位置（如"Methods, p.3, 第2段"） |
| **拿不准就MAYBE** | 任何一个PICOS要素无法从原文中明确判定 → 该要素标⚠️ → 整篇标MAYBE |
| **禁止推断** | 如果论文没有明确说"这是HMD"或"这是桌面VR"，不能自己推断设备类型 |
| **禁止补全** | 如果论文没有报告retention interval，不能假设"可能有延迟测试" |
| **禁止跨文污染** | 不能用A论文的信息来推断B论文的内容 |

### 1.2 上下文隔离规则（最关键）

**每篇PDF由独立的子agent处理，确保上下文完全隔离。**

**两种运行模式**：

#### 模式A：自动批量模式（推荐，用户说"开始筛选"即可）
```
1. 用户发送：开始筛选
2. 主agent读取文献列表（screening_summary.csv或dedup_pool CSV）
3. 对每篇未筛选的文献，主agent用Agent工具派发一个子agent
4. 每个子agent有独立上下文，执行完整的筛选流程（Step 0-7）
5. 子agent完成后，将结果返回主agent
6. 主agent收集所有结果，更新汇总表
7. 全部完成后，主agent输出汇总报告
```

#### 模式B：单篇手动模式（用户说"筛选第X篇"）
```
1. 用户发送：筛选第X篇：{Author}_{Year}，PDF路径：{path}
2. 主agent直接执行筛选流程（Step 0-7）
3. 输出该篇的MD文件内容 + 筛选结果
```

**为什么要上下文隔离**：
- 防止AI将A论文的Methods内容误认为B论文的内容
- 防止AI将A论文的判定结果"复制"到B论文
- 防止长上下文导致的注意力衰减和幻觉
- 每篇文献都是全新的上下文，确保判定的独立性

### 1.3 逐篇独立原则

| 规则 | 说明 |
|------|------|
| **一篇一文件** | 每篇文献生成独立的MD筛选记录文件 |
| **一次一篇** | 每次对话只处理一篇文献，处理完毕后清除上下文 |
| **完整走完** | 每篇必须走完P→I→C→O→S五个步骤，不能跳过 |
| **不参考前文** | 每篇的判定完全基于当前PDF内容，不参考之前任何文献的判定结果 |

### 1.4 溯源原则

| 规则 | 说明 |
|------|------|
| **引用格式** | `> "原文内容" （页码X，段落/表格/图注）` |
| **多证据** | 如果同一要素有多处证据，全部列出 |
| **反面证据** | 如果有证据支持不符合，也要列出（不能只列支持符合的证据） |
| **过程记录** | 记录筛选的时间、步骤、遇到的问题 |

---

## 二、文件管理结构

**所有筛选相关文件按以下目录结构组织：**

```
03_Screening/
├── pdfs/                          # PDF原始文件
│   └── {Author}_{Year}.pdf
├── mining_output/                 # 提取的文本文件
│   └── {Author}_{Year}_mining.txt
├── screening_records/             # 筛选记录（按判定结果分类）
│   ├── INCLUDE/                   # 纳入的文献
│   │   └── {Author}_{Year}.md
│   ├── EXCLUDE/                   # 排除的文献
│   │   └── {Author}_{Year}.md
│   └── MAYBE/                     # 待人工复核的文献
│       └── {Author}_{Year}.md
├── screening_progress.json        # 进度记录（断点恢复）
├── screening_summary.csv          # 汇总追踪表
├── dedup_pool_2026-05-31.csv      # 去重后的文献池
├── dedup_pool_with_abstracts_2026-05-31.csv  # 带摘要的文献池
└── FULLTEXT_SCREENING_PROTOCOL.md # 筛选协议
```

**文件命名规则**：
- PDF：`{Author}_{Year}.pdf`（如 `Chen_2026.pdf`）
- MinerU输出：`{Author}_{Year}_mining.md`（如 `Chen_2026_mining.md`）
- 筛选记录：`{Author}_{Year}.md`（如 `Chen_2026.md`）

**关键规则**：
- MinerU原始输出 → `mining_output/`（不得修改）
- 筛选记录 → `screening_records/{判定结果}/`（按INCLUDE/EXCLUDE/MAYBE分类）
- 汇总表 → `03_Screening/screening_summary.csv`（根目录，不在子目录中）

---

## 三、单篇筛选流程（子agent执行）

**每篇PDF由一个独立的子agent处理。子agent不知道其他文献的存在。**

子agent收到的prompt模板：
```
你是一个文献筛选子agent。你的任务是筛选一篇PDF文献，判断它是否符合PICOS纳入标准。

你的工作目录是：{project_dir}
你需要：
1. 读取 03_Screening/PICOS_RULES.md 了解纳入/排除标准
2. 读取 03_Screening/pdfs/{Author}_{Year}.pdf
3. 按PICOS标准逐一判定
4. 将筛选记录写入 03_Screening/screening_records/{判定}/{Author}_{Year}.md
5. 返回判定结果（INCLUDE/EXCLUDE/MAYBE + 排除码 + 简要理由）

注意：
- 你只能读取当前这篇PDF，不能参考任何其他文献
- 所有判定必须引用PDF原文
- 拿不准的写MAYBE
- 使用screen-repro skill的templates/SCREENING_RECORD.template.md作为记录模板
```

```
Step 0: 筛选前检查（子agent执行）
  ├─ 检查 pdfs/{Author}_{Year}.pdf 是否存在
  ├─ 检查 screening_records/ 下三个子目录中是否已有该文献的MD文件
  ├─ 已有MD → 返回"已有记录，判定为{结果}"，结束
  └─ 无MD → 继续Step 0.5

Step 0.5: 标题/摘要快速预筛（无需读PDF）
  ├─ 从 dedup_pool_with_abstracts CSV 中读取该文献的Title和Abstract
  ├─ 检查标题或摘要中是否包含以下综述类关键词：
  │    "systematic review" / "meta-analysis" / "scoping review" /
  │    "narrative review" / "umbrella review" / "critical review" /
  │    "literature review" / "review of" / "we review" / "this review"
  ├─ 如果命中 → 直接判定EXCLUDE E7，写入简要筛选记录，结束
  │    筛选记录内容：
  │    - 标题/摘要中明确标识为综述类文献
  │    - 原文证据：引用标题或摘要中包含的综述关键词
  │    - 判定理由：综合类型文献，不予纳入
  │    - 跳过PDF全文阅读
  └─ 如果未命中 → 继续Step 1

Step 1: PDF文本提取

  **提取工具优先级**（按质量从高到低）：
  1. **MinerU Agent 轻量解析 API（推荐）** — 免费，免Token，输出结构化的Markdown
  2. **PyMuPDF本地提取（回退）** — PDF≤10MB/≤20页时API优先，超过限制时本地

  **MinerU Agent API提取流程**（首选手法）：
  ├─ 条件：PDF文件≤10MB 且 ≤20页（API限制）
  ├─ Step 1a: 获取上传URL
  │    POST https://mineru.net/api/v1/agent/parse/file
  │    Body: {"file_name": "{Author}_{Year}.pdf", "language": "en", "enable_table": false, "is_ocr": false, "enable_formula": false}
  │    返回: {"task_id": "...", "file_url": "..."}  ← 签名上传URL
  ├─ Step 1b: 上传PDF
  │    PUT {file_url}  ← 将PDF文件内容PUT到该URL
  ├─ Step 1c: 轮询结果
  │    GET https://mineru.net/api/v1/agent/parse/{task_id}
  │    每5秒轮询一次，直到 state="done"或超时（最长2分钟）
  ├─ Step 1d: 下载Markdown
  │    从返回的 markdown_url 字段下载.md文件
  │    输出路径：mining_output/{Author}_{Year}_mining.md
  └─ 如果API调用失败（429/超时/文件超限） → 静默回退到PyMuPDF

  **PyMuPDF本地提取流程**（回退方案）：
  ├─ 条件：PDF > 10MB 或 > 20页，或MinerU API不可用
  ├─ 用 fitz.open() 打开PDF
  ├─ 逐页提取文本：page.get_text("text") — 纯文本模式
  ├─ 合并为完整文档文本
  ├─ 输出路径：mining_output/{Author}_{Year}_mining.txt
  └─ 优点：零依赖，稳定，快速；缺点：丢失表格/公式结构

  **文本质量控制**（无论用哪种工具，都强制执行）：
  ├─ 计算乱码率
  ├─ 乱码率>10% → 标记"文本提取异常"，写入MAYBE
  └─ 文本提取成功 → Step 2

Step 2: 读取PICOS规则
  └─ 读取 03_Screening/PICOS_RULES.md

Step 3: 阅读关键章节
  ├─ 先读Methods（确定P、I、C、S）
  ├─ 再读Results（确定O）
  └─ 必要时读Abstract和Discussion补充

Step 4: 逐项PICOS判定（按PICOS_RULES.md）
  ├─ P: 按规则文件中的"人群"标准判定
  ├─ I: 按规则文件中的"干预"标准判定
  ├─ C: 按规则文件中的"对照"标准判定
  ├─ O: 按规则文件中的"结局"标准判定
  └─ S: 按规则文件中的"研究设计"标准判定

Step 5: 最终判定
  ├─ 5项全部✅ → INCLUDE
  ├─ 任何一项❌ → EXCLUDE + 排除码
  └─ 任何一项⚠️ → MAYBE

Step 6: 写入MD文件
  ├─ 读取 templates/SCREENING_RECORD.template.md
  ├─ 填入判定结果、原文证据、分析、反面证据
  └─ 写入 screening_records/{判定}/{Author}_{Year}.md

Step 7: 返回结果
  └─ 返回：{Author}_{Year} | {INCLUDE/EXCLUDE/MAYBE} | {排除码} | {简要理由}
```

---

## 三、排除码定义

| 排除码 | 含义 | 判定标准 |
|--------|------|----------|
| **E1** | 非目标人群 | 参与者非高等教育学生/培训学员（患者、K-12、企业员工等） |
| **E2** | 非目标干预 | 未使用VR技术（纯物理模拟、纯视频教学、AR/MR等） |
| **E3** | 非程序性技能 | 测量的是认知知识（笔试）、态度、满意度等，非操作技能 |
| **E4** | 无retention/transfer | 仅有即时后测，无延迟后测（≥1周）或迁移测试 |
| **E5** | 无对照组 | 单组前后测设计，无非VR对照组 |
| **E6** | 非实验设计 | 非RCT、非准实验（质性研究、调查、案例报告等） |
| **E7** | 综述/理论 | 系统综述、Meta分析、理论文章、评论、研究方案 |
| **E8** | 非英文 | 非英文发表 |
| **E9** | 其他 | 以上均不适用的排除原因 |

**排除码优先级规则**：如果同时满足多个排除条件，按E1→E9顺序检查，标**第一个命中的排除码**，在判定理由中列出所有符合的排除码。

---

## 四、设备类型判定规则

**这是最容易出错的环节，必须严格按原文判定。**

| 原文描述 | 设备类型判定 | 说明 |
|----------|-------------|------|
| "head-mounted display" / "HMD" / "head-worn" | ✅ HMD_VR | 明确HMD |
| "Oculus" / "HTC Vive" / "Meta Quest" / "Pico" | ✅ HMD_VR | 已知HMD品牌 |
| "LapSim" / "LapMentor" / "Simbionix" / "EyeSi" | ✅ Desktop_VR | 已知桌面VR品牌 |
| "FLS trainer" / "Fundamentals of Laparoscopic Surgery" | ✅ Desktop_VR | 已知桌面VR系统 |
| "da Vinci Skills Simulator" / "dV-Trainer" | ✅ Desktop_VR | 机器人VR模拟器 |
| "virtual reality simulator" + 屏幕显示 | ✅ Desktop_VR | 需确认是屏幕而非HMD |
| "VR" / "virtual reality" 但未说明设备 | ⚠️ 需确认 | 无法判定HMD还是桌面 |
| "VR simulator" + 手术领域（腹腔镜/眼科/关节镜） | 📌 推断Desktop_VR | 推断：手术领域VR模拟器通常为桌面VR，标注"推断" |
| "simulation" / "simulator" 但未提VR | ⚠️ 需确认 | 可能是物理模拟器 |
| "box trainer" / "bench-top" / "physical model" | ❌ 非VR | 纯物理模拟器 |
| "augmented reality" / "AR" / "mixed reality" / "MR" | ❌ 非VR | AR/MR不是VR |
| "360° video" / "360-degree video" 无交互 | ❌ 非VR | 非交互式视频 |

---

## 五、Retention/Transfer判定规则

### Retention判定

| 原文描述 | 判定 | 说明 |
|----------|------|------|
| "delayed post-test at X weeks/months" | ✅ Retention | 有明确延迟时间 |
| "follow-up assessment at X weeks/months" | ✅ Retention | 有明确随访时间 |
| "retention test" / "retention assessment" | ✅ Retention | 有明确retention测试 |
| "skill maintenance" / "skill decay" | ✅ Retention | 有保持/衰减测量 |
| 延迟时间 < 1周 | ⚠️ 需确认 | 严格来说不符合≥1周标准 |
| 仅"post-test" / "posttest" 无延迟说明 | ❌ 无Retention | 可能是即时后测 |
| 仅"immediate assessment" | ❌ 无Retention | 明确即时 |

### Transfer判定

| 原文描述 | 判定 | 说明 |
|----------|------|------|
| "transfer test" / "transfer assessment" | ✅ Transfer | 有明确迁移测试 |
| "performance in the OR" / "real-world performance" | ✅ Transfer (far) | 迁移到真实环境 |
| "novel task" / "unfamiliar task" | ✅ Transfer (near/far) | 迁移到新任务 |
| "crossover" / "crossover design" | ⚠️ 需确认 | 可能涉及迁移 |
| 仅在同一任务上的重复测试 | ❌ 无Transfer | 不是迁移 |

---

## 六、MD文件模板

**模板来源**：读取本skill目录下的 `templates/SCREENING_RECORD.template.md` 文件。
如果找不到 → 使用内置的默认结构（见下方）。

**使用方式**：
1. 读取模板文件
2. 替换 `{Author}_{Year}` 为实际作者和年份
3. 填入PICOS判定结果、原文证据、分析、反面证据
4. 保留模板中的所有字段，不得删减

---

## 七、执行策略

### 7.1 自动批量模式（推荐）

**主agent协调，子agent执行。用户只需说"开始筛选"。**

主agent的协调流程：
```
Step 0: 读取配置文件 + 进度恢复
  ├─ 读取 03_Screening/PICOS_RULES.md（纳入/排除标准）
  ├─ 读取 03_Screening/RATE_LIMIT.md（速率限制参数）
  │    ├─ 如果不存在 → 使用默认值：并发1，间隔3秒，重试等待30秒
  │    └─ 如果存在 → 按配置控制派发速率
  ├─ 读取 templates/SCREENING_RECORD.template.md（筛选记录模板）
  └─ 读取 03_Screening/screening_progress.json（进度文件）
       ├─ 不存在 → 新建，从第1篇开始
       ├─ status=done → 提示"已全部完成"，询问是否重新筛选
       ├─ status=running → 提示"上次在{current}处中断，从断点恢复"
       └─ 恢复逻辑：跳过 completed 列表中的文献，从 current 或下一篇继续

Step 1: 读取文献列表
  └─ 读取 03_Screening/dedup_pool_with_abstracts_YYYY-MM-DD.csv

Step 2: 筛选待处理文献
  ├─ 检查 screening_records/ 下已有哪些MD文件
  ├─ 排除已在 completed 列表中的文献
  ├─ （双重验证）progress标记done但MD不存在 → 从completed中移除，重新处理
  ├─ （双重验证）MD存在但progress未标记 → 补充到completed列表
  └─ 生成待处理列表

Step 3: 严格串行派发子agent（每次仅1个）

  ╔══════════════════════════════════════════════════════════════╗
  ║  🛑 派发前强制检查 — 每次派发前必须逐条确认，违反则任务作废 ⚠️   ║
  ╠══════════════════════════════════════════════════════════════╣
  ║  □ 当前是否已经有子agent正在运行？                            ║
  ║     → 如果有 → 等待，绝不派发新的                             ║
  ║  □ 上一篇子agent是否已经完成 + 返回结果？                     ║
  ║     → 如果未完成 → 等待，绝不派发新的                         ║
  ║  □ 距离上一次派发是否已超过 {batch_wait_sec} 秒？             ║
  ║     → 如果未超过 → 等待，绝不派发新的                         ║
  ║  □ 确认：本次只派发 1 个 Agent，不是 2 个，不是 3 个          ║
  ║     → 必须是 1 个，不能多                                    ║
  ╚══════════════════════════════════════════════════════════════╝

  **违反以上任何一条 → 立即停止，向用户报告违规，不得继续。**

  具体流程：
  ├─ 从待处理列表取第1篇，派发子agent（仅1个）
  ├─ **等待该子agent完成并返回结果**（阻塞等待，不做其他事）
  ├─ 收到结果后：
  │    ├─ 追加到screening_summary.csv
  │    └─ **立即更新 screening_progress.json**（每篇完成后强制写入）
  ├─ 等待 {batch_wait_sec} 秒（从RATE_LIMIT.md读取）
  ├─ 重复上述 CHECKPOINT → 派发 → 等待 → 收集 → 更新进度 循环
  └─ 全部完成后输出汇总报告

Step 4: 收集结果
  ├─ 接收每个子agent的返回结果
  ├─ 追加到 screening_summary.csv
  └─ 统计：INCLUDE X篇, EXCLUDE Y篇, MAYBE Z篇

Step 5: 输出汇总报告 + 标记完成
  ├─ 输出筛选结果统计 + 待人工复核的MAYBE列表
  └─ 更新 screening_progress.json 中 status 为 "done"
```

### 7.2 单篇手动模式

**用户直接指定一篇文献，主agent执行筛选。**

用户发送：`筛选第X篇：{Author}_{Year}，PDF路径：{path}`
主agent直接执行Step 0-7（不需要派发子agent）。

### 7.3 执行顺序

1. 先处理**已有PDF的文献**（无需下载）
2. 再处理**可下载PDF的文献**（OA/PMC/scihub）
3. 最后处理**无PDF的文献**（仅基于摘要，标MAYBE）

---

## 八、质量控制

### 8.1 人工复核

- AI完成筛选后，用户对所有MAYBE进行人工复核
- 用户对INCLUDE进行抽样验证（至少10%）
- 用户对EXCLUDE进行抽样验证（至少5%）

### 8.2 一致性检查

- 同一篇文献的判定在不同时间应一致
- 相似设计的文献应有一致的判定逻辑
- 如果发现不一致，重新审查并记录修正原因

---

*skill创建于 2026-05-31，基于 fulltext-screening protocol v2.3*
*skill名称：screen-repro（可复现的全文筛选）*
