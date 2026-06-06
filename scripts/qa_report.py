#!/usr/bin/env python3
"""
qa_report.py — screen-repro v2.3 质量保证报告器
=================================================
用途: 筛选完成后，自动生成QA报告（MAYBE清单+随机抽样INCLUDE/EXCLUDE），
      用户人工复核后记录结果。

用法:
  python qa_report.py generate                     # 生成 QA_REPORT.md
  python qa_report.py status                       # 查看QA进度
  python qa_report.py resolve Chen 2026 INCLUDE "复核确认HMD VR"  # MAYBE→最终判定
  python qa_report.py confirm Chen 2026 INCLUDE    # 抽样复核确认

设计原则:
  - Python负责统计、抽样、文件操作（确定性，零幻觉）
  - 用户（人）负责最终判定（需要学术判断）
  - AI不参与QA阶段的任何决策
"""

import sys
import os
import io
import json
import csv
import random
import shutil
from datetime import datetime
from pathlib import Path

# Windows兼容性：强制UTF-8编码
if sys.platform == 'win32':
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8')

BASE = Path.cwd()  # 数据文件在当前工作目录（项目根目录）
RECORDS = BASE / "screening_records"
PROGRESS = BASE / "screening_progress.json"
SUMMARY = BASE / "screening_summary.csv"
QA_REPORT = BASE / "QA_REPORT.md"
QA_STATE = BASE / "qa_state.json"
SUBDIRS = ["INCLUDE", "EXCLUDE", "MAYBE"]
PY = "C:/Users/bo/.workbuddy/binaries/python/versions/3.13.12/python.exe"


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


def load_qa_state():
    if QA_STATE.exists():
        with open(QA_STATE, encoding="utf-8") as f:
            return json.load(f)
    return {"resolved": {}, "confirmed": {}, "generated_at": None}


def save_qa_state(s):
    s["last_updated"] = ts()
    with open(QA_STATE, "w", encoding="utf-8") as f:
        json.dump(s, f, indent=2, ensure_ascii=False)


def read_md_key(md_path: Path) -> dict:
    """从筛选记录MD中提取关键信息"""
    info = {"title": "", "uncertain_dims": [], "key_question": "", "exclusion_code": ""}
    if not md_path.exists():
        return info
    try:
        txt = md_path.read_text(encoding="utf-8")
    except Exception:
        return info

    for line in txt.split("\n"):
        line = line.strip()
        if line.startswith("**标题**"):
            info["title"] = line.split("|")[-1].strip() if "|" in line else line.replace("**标题**", "").strip()
        if "⚠️ 不确定" in line and line.startswith("**判定**"):
            # 从上下文判断维度
            context = txt[:txt.index(line)] if line in txt else ""
            for dim in ["P —", "I —", "C —", "O —", "S —"]:
                if dim in context[-200:]:
                    dim_letter = dim[0]
                    info["uncertain_dims"].append(dim_letter)
        if line.startswith("**判定理由**："):
            info["key_question"] = line.replace("**判定理由**：", "").strip()[:100]
        if "**排除码**" in line:
            info["exclusion_code"] = line.split("**排除码**")[-1].strip().replace("：", "").replace(":", "").strip()[:10]
    return info


def read_csv_rows() -> list[dict]:
    """读取screening_summary.csv所有行"""
    if not SUMMARY.exists():
        return []
    rows = []
    with open(SUMMARY, encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        for r in reader:
            rows.append(r)
    return rows


def generate():
    """生成QA报告"""
    d = load_progress()
    if not d:
        print("❌ 无进度文件")
        return

    qa = load_qa_state()
    now = ts()

    # 收集MAYBE列表
    maybe_dir = RECORDS / "MAYBE"
    maybes = []
    if maybe_dir.exists():
        for m in sorted(maybe_dir.glob("*.md")):
            if m.stem == "TEMPLATE":
                continue
            info = read_md_key(m)
            key = m.stem
            maybes.append({
                "key": key,
                "title": info["title"],
                "uncertain_dims": info["uncertain_dims"],
                "key_question": info["key_question"] or "见MD文件",
                "resolved": key in qa.get("resolved", {})
            })

    # 收集INCLUDE/EXCLUDE列表供抽样
    includes = []
    excludes = []
    for sd in ["INCLUDE", "EXCLUDE"]:
        sd_dir = RECORDS / sd
        if sd_dir.exists():
            for m in sorted(sd_dir.glob("*.md")):
                if m.stem == "TEMPLATE":
                    continue
                info = read_md_key(m)
                key = m.stem
                entry = {
                    "key": key,
                    "title": info["title"],
                    "decision": sd,
                    "exclusion_code": info.get("exclusion_code", ""),
                    "confirmed": key in qa.get("confirmed", {})
                }
                if sd == "INCLUDE":
                    includes.append(entry)
                else:
                    excludes.append(entry)

    # 随机抽样（种子基于日期，可复现）
    seed = int(datetime.now().strftime("%Y%m%d"))
    random.seed(seed)

    inc_sample_n = max(1, int(len(includes) * 0.10))
    exc_sample_n = max(1, int(len(excludes) * 0.05))
    inc_samples = random.sample(includes, min(inc_sample_n, len(includes)))
    exc_samples = random.sample(excludes, min(exc_sample_n, len(excludes)))

    # 生成报告
    r = d["results"]
    resolved_count = len(qa.get("resolved", {}))
    confirmed_count = len(qa.get("confirmed", {}))

    lines = []
    lines.append("# 筛选QA报告 — " + now[:10])
    lines.append("")
    lines.append(f"> **生成时间**：{now}")
    lines.append(f"> **抽样种子**：{seed}（可复现，同一天生成结果相同）")
    lines.append(f"> **QA进度**：MAYBE已解决 {resolved_count}/{r.get('MAYBE', 0)} | 抽样已确认 {confirmed_count}")
    lines.append("")
    lines.append("---")
    lines.append("")
    lines.append("## 📊 总体情况")
    lines.append("")
    lines.append(f"| | INCLUDE | EXCLUDE | MAYBE | SKIPPED | 总计 |")
    lines.append(f"|--|---------|---------|-------|---------|------|")
    lines.append(f"| 数量 | {r['INCLUDE']} | {r['EXCLUDE']} | {r.get('MAYBE', 0)} | {r.get('SKIPPED', 0)} | {d['total']} |")
    lines.append("")
    lines.append("---")
    lines.append("")

    # 第一优先级：MAYBE
    lines.append("## 🔴 第一优先级：MAYBE 人工复核")
    lines.append("")
    if maybes:
        lines.append(f"共 {len(maybes)} 篇，需逐篇阅读MD文件后做出最终判定。")
        lines.append("")
        lines.append("| # | 文献 | 不确定维度 | 关键疑问 | 状态 |")
        lines.append("|---|------|-----------|----------|------|")
        for i, m in enumerate(maybes, 1):
            dims = ",".join(m["uncertain_dims"]) if m["uncertain_dims"] else "?"
            status = "✅ 已解决" if m["resolved"] else "⏳ 待复核"
            lines.append(f"| {i} | {m['key']} | {dims} | {m['key_question'][:60]} | {status} |")
    else:
        lines.append("✅ 无MAYBE文献。")
    lines.append("")
    lines.append("**操作方式**：阅读 `screening_records/MAYBE/{文献}.md`，确定最终判定后执行：")
    lines.append("```")
    lines.append("python 03_Screening/qa_report.py resolve {Author} {Year} {INCLUDE/EXCLUDE} \"复核理由\"")
    lines.append("```")
    lines.append("")

    # 第二优先级：INCLUDE抽样
    lines.append("---")
    lines.append("")
    lines.append(f"## 🟡 第二优先级：INCLUDE 抽样复核（{len(inc_samples)}篇，≥10%）")
    lines.append("")
    lines.append("| # | 文献 | 状态 |")
    lines.append("|---|------|------|")
    for i, s in enumerate(inc_samples, 1):
        status = "✅ 已确认" if s["confirmed"] else "⏳ 待确认"
        lines.append(f"| {i} | {s['key']} | {status} |")
    lines.append("")
    lines.append("**操作方式**：阅读MD文件确认判定无误后执行：")
    lines.append("```")
    lines.append("python 03_Screening/qa_report.py confirm {Author} {Year} INCLUDE")
    lines.append("```")
    lines.append("")

    # 第三优先级：EXCLUDE抽样
    lines.append("---")
    lines.append("")
    lines.append(f"## 🟢 第三优先级：EXCLUDE 抽样复核（{len(exc_samples)}篇，≥5%）")
    lines.append("")
    lines.append("| # | 文献 | 排除码 | 状态 |")
    lines.append("|---|------|--------|------|")
    for i, s in enumerate(exc_samples, 1):
        code = s.get("exclusion_code", "")
        status = "✅ 已确认" if s["confirmed"] else "⏳ 待确认"
        lines.append(f"| {i} | {s['key']} | {code} | {status} |")
    lines.append("")
    lines.append("**操作方式**：阅读MD文件确认排除理由充分后执行：")
    lines.append("```")
    lines.append("python 03_Screening/qa_report.py confirm {Author} {Year} EXCLUDE")
    lines.append("```")
    lines.append("")

    lines.append("---")
    lines.append("")
    lines.append(f"*QA报告由 screen-repro v2.0 自动生成 | {now}*")

    with open(QA_REPORT, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))

    # 保存QA状态
    qa["generated_at"] = now
    qa["seed"] = seed
    save_qa_state(qa)

    print(f"✅ QA报告已生成: {QA_REPORT}")
    print(f"   MAYBE待复核: {len(maybes)}篇")
    print(f"   INCLUDE抽样: {len(inc_samples)}篇")
    print(f"   EXCLUDE抽样: {len(exc_samples)}篇")


def resolve(first, year, decision, reason):
    """MAYBE文献 → 最终判定"""
    if decision not in ("INCLUDE", "EXCLUDE"):
        print(f"❌ 无效决策: {decision}，只支持 INCLUDE 或 EXCLUDE")
        return

    key = f"{first}_{year}"
    src = RECORDS / "MAYBE" / f"{key}.md"
    dst_dir = RECORDS / decision
    dst = dst_dir / f"{key}.md"

    if not src.exists():
        print(f"❌ MAYBE/{key}.md 不存在")
        return

    # 更新MD文件中的决策
    txt = src.read_text(encoding="utf-8")
    txt = txt.replace("**决策**：MAYBE", f"**决策**：{decision}")
    if decision == "EXCLUDE":
        txt = txt.replace("**排除码**（如EXCLUDE）：", "**排除码**（如EXCLUDE）：E9")
    # 追加复核记录
    txt += f"\n\n---\n## QA复核记录\n\n- **复核日期**: {ts()[:10]}\n- **复核结果**: {decision}\n- **理由**: {reason}\n"

    os.makedirs(dst_dir, exist_ok=True)
    dst.write_text(txt, encoding="utf-8")
    src.unlink()
    print(f"[MD] {key} 已从 MAYBE → {decision}")

    # 更新CSV
    if SUMMARY.exists():
        rows = []
        with open(SUMMARY, encoding="utf-8-sig") as f:
            reader = csv.DictReader(f)
            fieldnames = reader.fieldnames
            for r in reader:
                if r.get("Author") == first and r.get("Year") == year:
                    r["最终决策"] = decision
                    r["MD文件"] = f"screening_records/{decision}/{key}.md"
                rows.append(r)
        with open(SUMMARY, "w", encoding="utf-8-sig", newline="") as f:
            w = csv.DictWriter(f, fieldnames=fieldnames)
            w.writeheader()
            w.writerows(rows)
        print(f"[CSV] {key} 决策已更新 → {decision}")

    # 更新progress
    d = load_progress()
    if d:
        d["results"]["MAYBE"] = max(0, d["results"].get("MAYBE", 0) - 1)
        d["results"][decision] = d["results"].get(decision, 0) + 1
        save_progress(d)
        print(f"[Progress] MAYBE-1 → {decision}+1")

    # 更新QA状态
    qa = load_qa_state()
    qa["resolved"][key] = {"decision": decision, "reason": reason, "date": ts()[:10]}
    save_qa_state(qa)

    print(f"✅ {key} → {decision} ({reason})")


def confirm(first, year, decision):
    """抽样复核确认"""
    key = f"{first}_{year}"
    md = RECORDS / decision / f"{key}.md"

    if not md.exists():
        print(f"❌ {decision}/{key}.md 不存在")
        return

    # 在MD文件末尾追加确认标记
    txt = md.read_text(encoding="utf-8")
    if "## QA复核确认" not in txt:
        txt += f"\n\n---\n## QA复核确认\n\n- **复核日期**: {ts()[:10]}\n- **结果**: 确认，判定无误\n"
        md.write_text(txt, encoding="utf-8")
        print(f"[MD] {key} 已标记QA确认")

    # 更新QA状态
    qa = load_qa_state()
    qa["confirmed"][key] = {"decision": decision, "date": ts()[:10]}
    save_qa_state(qa)

    print(f"✅ {key} → 已确认 ({decision})")


def status():
    """查看QA进度"""
    qa = load_qa_state()
    d = load_progress()
    r = d["results"] if d else {}

    resolved = len(qa.get("resolved", {}))
    confirmed = len(qa.get("confirmed", {}))

    print("=" * 50)
    print("📋 QA 进度")
    print("=" * 50)
    print(f"  生成时间: {qa.get('generated_at', '尚未生成')}")
    print(f"  MAYBE 已解决: {resolved}/{r.get('MAYBE', 0) + resolved}")
    print(f"  抽样 已确认: {confirmed}")
    print("=" * 50)

    pending_maybe = r.get("MAYBE", 0)
    if pending_maybe > 0:
        print(f"\n⏳ 还有 {pending_maybe} 篇MAYBE待复核")
    elif resolved > 0:
        print("\n✅ MAYBE全部复核完成")
    else:
        print("\n✅ 无MAYBE文献")


# ====== CLI ======
if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else ""
    if cmd == "generate":
        generate()
    elif cmd == "resolve":
        if len(sys.argv) < 5:
            print("用法: python qa_report.py resolve <First> <Year> <INCLUDE/EXCLUDE> <理由>")
        else:
            resolve(sys.argv[2], sys.argv[3], sys.argv[4],
                    " ".join(sys.argv[5:]) if len(sys.argv) > 5 else "人工复核")
    elif cmd == "confirm":
        if len(sys.argv) < 5:
            print("用法: python qa_report.py confirm <First> <Year> <INCLUDE/EXCLUDE>")
        else:
            confirm(sys.argv[2], sys.argv[3], sys.argv[4])
    elif cmd == "status":
        status()
    else:
        print("用法: python qa_report.py <命令>")
        print("  generate    生成 QA_REPORT.md")
        print("  status      查看QA进度")
        print("  resolve First Year INCLUDE/EXCLUDE <理由>  MAYBE→最终判定")
        print("  confirm First Year INCLUDE/EXCLUDE  抽样复核确认")
