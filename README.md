# screen-repro v1.0 — 可复现的AI文献筛选系统

> ⚠️ **此版本已废弃**，请新项目使用 `@screen-repro`（v2.0 AI+Python版）。
> v1.0 保留仅供历史参考。

---

## 快速开始

### 1. 调用方式

```
@screen-repro-v1 筛选第X篇：{Author}_{Year}，PDF路径：{path}
```

或：

```
@screen-repro-v1 开始筛选
```

### 2. 前提条件

确保你的项目结构已建立：

```
03_Screening/
├── pdfs/                          # PDF原始文件
├── mining_output/                 # 提取的文本文件
├── screening_records/             # 筛选记录
│   ├── INCLUDE/
│   ├── EXCLUDE/
│   └── MAYBE/
├── screening_progress.json        # 进度记录
├── screening_summary.csv          # 汇总追踪表
└── PICOS_RULES.md                 # 筛选标准（必须有！）
```

---

## 核心原则

| 原则 | 含义 |
|------|------|
| **零幻觉** | 只读原文，禁止推测，所有判定必须有原文引用 |
| **上下文隔离** | 每次只处理一篇PDF，独立子agent，互不污染 |
| **可溯源** | 每条判定都引用原文页码+段落，可人工复核 |
| **拿不准就MAYBE** | 任何PICOS要素不明确 → 整篇标MAYBE |

---

## 筛选流程

### 模式A：自动批量（推荐）

用户只需发送：`开始筛选`

系统自动执行：

```
1. 读取 PICOS_RULES.md + 文献列表
2. 检查进度文件，支持断点恢复
3. 每次派发1个子agent，间隔3秒
4. 每篇完成后写入筛选记录 + 更新进度
5. 全部完成后输出汇总报告
```

### 模式B：单篇手动

用户发送：`筛选第X篇：Chen_2024，PDF路径：03_Screening/pdfs/Chen_2024.pdf`

系统直接处理该篇，输出筛选记录。

---

## PICOS判定标准

| 要素 | 纳入标准 | 排除码 |
|------|----------|--------|
| **P** | 高等教育学生/培训学员 | E1 |
| **I** | HMD头戴式VR | E2 |
| **C** | 有非VR对照组 | E5 |
| **O** | 有retention（≥1周）或transfer | E4 |
| **S** | RCT或准实验 | E6 |

**其他排除码**：
- E3: 非程序性技能
- E7: 综述/理论文章
- E8: 非英文
- E9: 其他原因

---

## 设备类型判定（重点）

| 关键词 | 判定 |
|--------|------|
| HMD / head-mounted / head-worn | ✅ HMD |
| Oculus / HTC Vive / Meta Quest / Pico | ✅ HMD |
| LapSim / EyeSi / MIST-VR / dV-Trainer | ✅ Desktop |
| VR + 手术领域 | 📌 推断 Desktop |
| 仅"VR"未说明设备 | ⚠️ 需确认 |

---

## 筛选记录模板

每篇文献生成独立 MD 文件，位于 `screening_records/{INCLUDE/EXCLUDE/MAYBE}/`

模板结构：

```markdown
# {Author}_{Year} 全文筛选记录

## 判定结果：{INCLUDE/EXCLUDE/MAYBE}
## 排除码：{E1-E9}（如EXCLUDE）

## PICOS逐项判定

### P - 人群
- **判定**：{符合/不符合/不确定}
- **原文证据**：> "..." （页码X，段落Y）

### I - 干预
- **判定**：{符合/不符合/不确定}
- **设备类型**：{HMD/桌面VR/不明}
- **原文证据**：> "..." （页码X，段落Y）

...（C/O/S同理）

## 最终判定理由
...

## 反面证据
（如有支持不符合的证据，也需列出）
```

---

## 速率限制

默认配置（可在 `RATE_LIMIT.md` 中修改）：

| 参数 | 默认值 |
|------|--------|
| 并发数 | 1（严格串行） |
| 间隔 | 3秒 |
| 重试等待 | 30秒 |

---

## 进度管理

### 进度文件：`screening_progress.json`

```json
{
  "status": "running",
  "current": "Chen_2024",
  "total": 400,
  "completed": ["Wang_2023", "Li_2022", ...],
  "started_at": "2026-06-01T10:00:00"
}
```

### 断点恢复

如果模型中断，系统会自动：

1. 读取 `screening_progress.json`
2. 跳过 `completed` 列表中的文献
3. 从 `current` 或下一篇继续

---

## 质量控制

| 检查 | 建议 |
|------|------|
| 人工复核 MAYBE | 全部复核 |
| 人工抽样 INCLUDE | ≥10% |
| 人工抽样 EXCLUDE | ≥5% |
| 一致性检查 | 相似设计应一致判定 |

---

## 常见问题

**Q：为什么每次只读一篇PDF？**

A：防止AI跨文污染——把A论文的Methods误认为B论文。每次独立上下文，保证判定的真实性。

**Q：MAYBE是什么意思？**

A：任何一个PICOS要素无法从原文明确判定 → 整篇标MAYBE，需人工复核。不是"可能纳入"，是"AI说不准"。

**Q：能不能并行处理多篇？**

A：v1.0 严格串行。如需并发，请手动创建多个 batch 目录，各运行一个独立筛选任务。

**Q：为什么没有进度条？**

A：每篇完成后立即写入 `screening_progress.json`，断点后可恢复，进度数据比可视化重要。

---

## 版本说明

| 版本 | 日期 | 说明 |
|------|------|------|
| v1.0 | 2026-05-31 | 纯AI版，已废弃 |
| v2.0 | 2026-06-01 | AI+Python混合版，当前推荐 |

---

*screen-repro v1.0 — 可复现的逐篇全文筛选协议*
