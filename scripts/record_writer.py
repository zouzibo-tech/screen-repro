#!/usr/bin/env python3
"""
record_writer.py — screen-repro v2.0 筛选记录写入器
=====================================================
用途: 子agent返回结构化JSON → Python验证+填模板+写MD+追加CSV+更新进度。
      一条命令完成所有写入操作，杜绝AI在文件操作中的幻觉。

用法:
  python record_writer.py '<JSON>'            # JSON作为命令行参数
  echo '<JSON>' | python record_writer.py -   # JSON从stdin读取（推荐）

JSON schema:
{
  "author": "Chen",
  "year": 2026,
  "title": "...",
  "doi": "",
  "decision": "INCLUDE",
  "exclusion_code": null,
  "picos": {
    "P": {"result": "✅", "evidence": ["text (source)"], "analysis": "..."},
    "I": {"result": "✅", "device_type": "HMD_VR", "evidence": [...], "analysis": "..."},
    "C": {"result": "✅", "evidence": [...], "analysis": "..."},
    "O": {"result": "✅", "outcome_type": "Retention", "retention_weeks": 4, "evidence": [...], "analysis": "..."},
    "S": {"result": "✅", "design_type": "RCT", "evidence": [...], "analysis": "..."}
  },
  "reason": "overall reason",
  "pdf_path": "03_Screening/pdfs/Chen_2026.pdf",
  "mining_path": "03_Screening/mining_output/Chen_2026_mining.md",
  "text_quality": "正常",
  "screening_date": "2026-06-02"
}

退出码: 0=成功, 1=JSON格式错误, 2=文件写入错误
"""

import sys
import os
import json
import csv
import hashlib
from datetime import datetime
from pathlib import Path

BASE = Path.cwd()  # 数据文件在当前工作目录（项目根目录）
RECORDS = BASE / "screening_records"
PROGRESS = BASE / "screening_progress.json"
SUMMARY = BASE / "screening_summary.csv"
SUBDIRS = ["INCLUDE", "EXCLUDE", "MAYBE"]
VALID_DECISIONS = {"INCLUDE", "EXCLUDE", "MAYBE"}
VALID_CODES = {"", "E1", "E2", "E3", "E4", "E5", "E6", "E7", "E8", "E9"}
VALID_RESULTS = {"✅", "❌", "⚠️"}

TEMPLATE = """# 筛选记录 — {author}_{year}

> **筛选日期**：{screening_date}
> **筛选轮次**：Round {round_num}
> **筛选人**：AI (screen-repro v2.0)
> **筛选方法**：PDF全文阅读（自动提取）

---

## 文献信息

| 字段 | 内容 |
|------|------|
| **标题** | {title} |
| **作者** | {author} |
| **年份** | {year} |
| **DOI** | {doi} |
| **PDF路径** | {pdf_path} |
| **提取路径** | {mining_path} |
| **文本质量** | {text_quality} |

---

## PICOS 逐项判定

{P_section}

---

{I_section}

---

{C_section}

---

{O_section}

---

{S_section}

---

## 最终判定

**决策**：{decision}

**排除码**（如EXCLUDE）：{exclusion_code}

**判定理由**：

{reason}

---

## 筛选过程日志

| 时间 | 操作 | 说明 |
|------|------|------|
| {log_time} | PDF提取 | {text_quality} |
| {log_time} | PICOS判定 | P:{P_result} I:{I_result} C:{C_result} O:{O_result} S:{S_result} |
| {log_time} | 最终决策 | {decision} {exclusion_code} |
| {log_time} | MD文件写入 | 路径：screening_records/{decision}/ |
| {log_time} | 汇总表更新 | screening_summary.csv 追加一行 |
"""


def ts():
    return datetime.now().isoformat(timespec="seconds")


def load_progress():
    if PROGRESS.exists():
        with open(PROGRESS, encoding="utf-8") as f:
            return json.load(f)
    return None


def save_progress(d):
    d["last_updated"] = ts()
    with open(PROGRESS, "w", encoding="utf-8") as f:
        json.dump(d, f, indent=2, ensure_ascii=False)


def validate(data: dict) -> list[str]:
    """验证JSON格式，返回错误列表"""
    errors = []

    # 必填顶层字段
    for field in ["author", "year", "title", "decision"]:
        if not data.get(field):
            errors.append(f"缺少必填字段: {field}")

    if data.get("decision") not in VALID_DECISIONS:
        errors.append(f"无效的决策: {data['decision']}，合法值: {VALID_DECISIONS}")

    excl = data.get("exclusion_code", "")
    if excl and excl not in VALID_CODES:
        errors.append(f"无效的排除码: {excl}，合法值: {VALID_CODES}")

    # EXCLUDE 必须有排除码
    if data.get("decision") == "EXCLUDE" and not excl:
        errors.append("EXCLUDE决策必须提供排除码")

    # INCLUDE/MAYBE 不应有排除码
    if data.get("decision") != "EXCLUDE" and excl:
        errors.append(f"{data['decision']}决策不应有排除码")

    # picos 字段
    picos = data.get("picos", {})
    for dim in ["P", "I", "C", "O", "S"]:
        d = picos.get(dim, {})
        if not d:
            errors.append(f"缺少PICOS.{dim}判定")
            continue
        if d.get("result") not in VALID_RESULTS:
            errors.append(f"PICOS.{dim}.result 无效: {d.get('result')}，合法值: {VALID_RESULTS}")
        if not d.get("evidence") or not isinstance(d.get("evidence"), list):
            errors.append(f"PICOS.{dim}.evidence 缺失或不是数组")
        if "analysis" not in d:
            errors.append(f"PICOS.{dim}.analysis 缺失")

    return errors


def build_picos_section(dim: str, data: dict) -> str:
    """构建单个PICOS维度的MD段落"""
    result_text = {"✅": "符合", "❌": "不符合", "⚠️": "不确定"}

    dim_labels = {"P": "人群 (Population)", "I": "干预 (Intervention)",
                   "C": "对照 (Comparator)", "O": "结局 (Outcomes)", "S": "研究设计 (Study Design)"}

    lines = [f"### {dim} — {dim_labels.get(dim, dim)}", "",
             f"**判定**：{data.get('result', '?')} {result_text.get(data.get('result', ''), '')}", ""]

    # 特殊字段
    if dim == "I" and data.get("device_type"):
        lines.append(f"**设备类型**：{data['device_type']}")
        lines.append("")
    if dim == "O":
        if data.get("outcome_type"):
            lines.append(f"**结局类型**：{data['outcome_type']}")
            lines.append("")
        if data.get("retention_weeks") is not None:
            lines.append(f"**Retention详情**：延迟时间 = {data['retention_weeks']}周")
            lines.append("")
    if dim == "S" and data.get("design_type"):
        lines.append(f"**设计类型**：{data['design_type']}")
        lines.append("")

    # 原文证据
    lines.append("**原文证据**：")
    lines.append("")
    for ev in data.get("evidence", []):
        lines.append(f"> \"{ev}\"")
    lines.append("")

    # 分析
    lines.append(f"**分析**：{data.get('analysis', '')}")
    lines.append("")

    return "\n".join(lines)


def fill_template(data: dict) -> str:
    """填入模板生成MD内容"""
    picos = data.get("picos", {})
    now = ts()

    p_sections = []
    for dim in ["P", "I", "C", "O", "S"]:
        p_sections.append(build_picos_section(dim, picos.get(dim, {})))

    return TEMPLATE.format(
        author=data.get("author", ""),
        year=data.get("year", ""),
        screening_date=data.get("screening_date", now[:10]),
        round_num=data.get("round_num", "?"),
        title=data.get("title", ""),
        doi=data.get("doi", ""),
        pdf_path=data.get("pdf_path", ""),
        mining_path=data.get("mining_path", ""),
        text_quality=data.get("text_quality", "未知"),
        P_section=p_sections[0],
        I_section=p_sections[1],
        C_section=p_sections[2],
        O_section=p_sections[3],
        S_section=p_sections[4],
        decision=data.get("decision", ""),
        exclusion_code=data.get("exclusion_code", ""),
        reason=data.get("reason", ""),
        log_time=now[:19],
        P_result=picos.get("P", {}).get("result", "?"),
        I_result=picos.get("I", {}).get("result", "?"),
        C_result=picos.get("C", {}).get("result", "?"),
        O_result=picos.get("O", {}).get("result", "?"),
        S_result=picos.get("S", {}).get("result", "?"),
    )


def append_csv(data: dict, md_filename: str = ""):
    """追加到screening_summary.csv"""
    exists = SUMMARY.exists()
    with open(SUMMARY, "a", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        if not exists:
            w.writerow(["序号", "Author", "Year", "Title", "DOI", "PDF状态", "文本提取工具", "文本质量",
                        "P判定", "I判定", "设备类型", "C判定", "O判定", "结局类型", "S判定", "设计类型",
                        "最终决策", "排除码", "判定理由", "筛选日期", "MD文件"])
        d = load_progress()
        seq = (d["processed"] + 1) if d else 1
        picos = data.get("picos", {})
        w.writerow([
            seq,
            data.get("author", ""),
            data.get("year", ""),
            data.get("title", ""),
            data.get("doi", ""),
            "有PDF" if data.get("pdf_path") else "无PDF",
            "MinerU/PyMuPDF",
            data.get("text_quality", ""),
            picos.get("P", {}).get("result", ""),
            picos.get("I", {}).get("result", ""),
            picos.get("I", {}).get("device_type", ""),
            picos.get("C", {}).get("result", ""),
            picos.get("O", {}).get("result", ""),
            picos.get("O", {}).get("outcome_type", ""),
            picos.get("S", {}).get("result", ""),
            picos.get("S", {}).get("design_type", ""),
            data.get("decision", ""),
            data.get("exclusion_code", ""),
            data.get("reason", ""),
            data.get("screening_date", ts()[:10]),
            f"screening_records/{data.get('decision', '')}/{md_filename}",
        ])
    print(f"[CSV] 已追加: {data.get('author')}_{data.get('year')}")


def update_progress(data: dict):
    """更新screening_progress.json"""
    d = load_progress()
    if not d:
        print("[Progress] ⚠️ 无进度文件，跳过更新")
        return
    key = f"{data.get('author')}_{data.get('year')}"
    d["current"] = key
    if key not in d["completed"]:
        d["completed"].append(key)
        d["processed"] += 1
        d["remaining"] = max(0, d["total"] - d["processed"])
    decision = data.get("decision", "MAYBE")
    if decision in d["results"]:
        d["results"][decision] += 1
    d["status"] = "running"
    save_progress(d)
    r = d["results"]
    print(f"[Progress] {key} → {decision}  "
          f"({d['processed']}/{d['total']}  "
          f"I:{r['INCLUDE']} E:{r['EXCLUDE']} M:{r['MAYBE']})")


def main():
    # 读取JSON
    if len(sys.argv) >= 2:
        if sys.argv[1] == "-":
            raw = sys.stdin.read()
        else:
            raw = sys.argv[1]
    else:
        raw = sys.stdin.read()

    if not raw.strip():
        print("❌ 未提供JSON数据", file=sys.stderr)
        print("用法: python record_writer.py '<JSON>' 或 echo '<JSON>' | python record_writer.py -", file=sys.stderr)
        sys.exit(1)

    # 解析JSON
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as e:
        print(f"❌ JSON解析失败: {e}", file=sys.stderr)
        sys.exit(1)

    # 验证格式
    errors = validate(data)
    if errors:
        print("❌ JSON格式验证失败:", file=sys.stderr)
        for err in errors:
            print(f"   - {err}", file=sys.stderr)
        sys.exit(1)

    print(f"✅ JSON验证通过: {data.get('author')}_{data.get('year')} → {data.get('decision')}")

    # 生成MD内容
    md_content = fill_template(data)

    # 生成唯一文件名（解决同作者同年文献命名冲突）
    decision = data.get("decision", "MAYBE")
    author = data.get("author", "unknown")
    year = data.get("year", "0000")
    title = data.get("title", "")
    # 标题前20字符的MD5前6位作为唯一标识
    title_part = title[:20] if title else f"{author}_{year}"
    hash_part = hashlib.md5(title_part.encode('utf-8')).hexdigest()[:6]
    filename = f"{author}_{year}_{hash_part}.md"
    write_dir = RECORDS / decision
    os.makedirs(write_dir, exist_ok=True)
    write_path = write_dir / filename
    # 极端冲突兜底：如果文件已存在，追加序号
    counter = 2
    while write_path.exists():
        filename = f"{author}_{year}_{hash_part}_{counter}.md"
        write_path = write_dir / filename
        counter += 1

    # 写入MD
    try:
        with open(write_path, "w", encoding="utf-8") as f:
            f.write(md_content)
        print(f"[MD] 已写入: {write_path}")
    except Exception as e:
        print(f"❌ MD写入失败: {e}", file=sys.stderr)
        sys.exit(2)

    # 追加CSV
    try:
        append_csv(data, filename)
    except Exception as e:
        print(f"❌ CSV追加失败: {e}", file=sys.stderr)
        sys.exit(2)

    # 更新进度
    try:
        update_progress(data)
    except Exception as e:
        print(f"❌ 进度更新失败: {e}", file=sys.stderr)
        sys.exit(2)

    print(f"✅ 全流程完成: {author}_{year} → {decision}")
    sys.exit(0)


if __name__ == "__main__":
    main()
