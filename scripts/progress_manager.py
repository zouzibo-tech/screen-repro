#!/usr/bin/env python3
"""
progress_manager.py — screen-repro v2.0 进度管理器
====================================================
用途: 用Python替代AI执行计数、记账、验证、统计等确定性操作，防止AI幻觉。

用法:
  python progress_manager.py init                          # 初始化文件
  python progress_manager.py set-total 461                 # 设置总数
  python progress_manager.py check                         # 查看进度
  python progress_manager.py update Smith 2024 INCLUDE     # 记录一篇
  python progress_manager.py verify                        # 双重验证
  python progress_manager.py summary                       # 汇总报告
"""

import sys, os, json, csv, shutil
from datetime import datetime
from pathlib import Path

BASE = Path.cwd()  # 数据文件在当前工作目录（项目根目录）
PROGRESS = BASE / "screening_progress.json"
SUMMARY = BASE / "screening_summary.csv"
RECORDS = BASE / "screening_records"
SUBDIRS = ["INCLUDE", "EXCLUDE", "MAYBE"]
PY = "C:/Users/bo/.workbuddy/binaries/python/versions/3.13.12/python.exe"


def ts():
    return datetime.now().isoformat(timespec="seconds")


def load():
    if PROGRESS.exists():
        with open(PROGRESS, encoding="utf-8") as f:
            return json.load(f)
    return None


def save(d):
    d["last_updated"] = ts()
    with open(PROGRESS, "w", encoding="utf-8") as f:
        json.dump(d, f, indent=2, ensure_ascii=False)


# ====== Commands ======

def init():
    import shutil

    # 创建数据文件
    if PROGRESS.exists():
        print("⚠️ 进度文件已存在，跳过创建")
    else:
        save({"status":"init","total":0,"processed":0,"remaining":0,"current":None,
              "completed":[],"errors":[],
              "results":{"INCLUDE":0,"EXCLUDE":0,"MAYBE":0,"SKIPPED":0,"ERROR":0},
              "started_at":ts(),"last_updated":ts()})
        print("✅ 进度文件已创建")
    if not SUMMARY.exists():
        with open(SUMMARY, "w", encoding="utf-8-sig", newline="") as f:
            w = csv.writer(f)
            w.writerow(["序号","Author","Year","Title","DOI","PDF状态","文本提取工具","文本质量",
                        "P判定","I判定","设备类型","C判定","O判定","结局类型","S判定","设计类型",
                        "最终决策","排除码","判定理由","筛选日期","MD文件"])
        print("✅ 汇总表已创建")

    # 从skill模板复制项目配置文件
    skill_templates = Path(__file__).parent.parent / "templates"
    for tpl_file in ["PICOS_RULES.template.md", "RATE_LIMIT.template.md"]:
        tpl_path = skill_templates / tpl_file
        if not tpl_path.exists():
            continue
        dest_name = tpl_file.replace(".template", "")
        dest_path = BASE / dest_name
        if dest_path.exists():
            print(f"⚠️ {dest_name} 已存在，跳过")
        else:
            shutil.copy(tpl_path, dest_path)
            print(f"✅ {dest_name} 已从模板创建")

    # 创建必要的子目录
    for subdir in ["pdfs", "mining_output"]:
        d = BASE / subdir
        d.mkdir(exist_ok=True)
    for subdir in SUBDIRS:
        d = RECORDS / subdir
        d.mkdir(exist_ok=True)

    print("✅ 项目目录结构已就绪")


def set_total(n):
    d = load()
    if not d:
        print("❌ 请先运行 init")
        return
    d["total"] = n
    d["remaining"] = n - d["processed"]
    d["status"] = "running"
    d["started_at"] = ts()
    save(d)
    print(f"✅ 总计={n}, 剩余={d['remaining']}, 状态→running")


def check():
    d = load()
    if not d:
        print("❌ 无进度文件，请先运行 init 和 set-total")
        return
    r = d["results"]
    print("=" * 50)
    print("📊 筛选进度")
    print("=" * 50)
    print(f"  状态:{d['status']:>8}    总数:{d['total']}")
    print(f"  已处理:{d['processed']:>4}    剩余:{d['remaining']}")
    print(f"  INCLUDE:{r['INCLUDE']:>4} | EXCLUDE:{r['EXCLUDE']:>4} | MAYBE:{r['MAYBE']:>4} | SKIPPED:{r.get('SKIPPED',0):>4} | ERROR:{r['ERROR']}")
    if d["current"]:
        print(f"  当前/上篇: {d['current']}")
    print(f"  开始: {d.get('started_at','?')}")
    print("=" * 50)
    if d["status"] == "done":
        print("✅ 全部完成")


def update(first, year, decision, excl=""):
    d = load()
    if not d:
        print("❌ 无进度文件")
        return
    key = f"{first}_{year}"
    d["current"] = key
    if key not in d["completed"]:
        d["completed"].append(key)
        d["processed"] += 1
        d["remaining"] = max(0, d["total"] - d["processed"])
    if decision in d["results"]:
        d["results"][decision] += 1
    d["status"] = "running"
    save(d)
    print(f"✅ {key} → {decision}  "
          f"({d['processed']}/{d['total']}  "
          f"I:{d['results']['INCLUDE']} E:{d['results']['EXCLUDE']} M:{d['results']['MAYBE']})")


def skip(first, year, reason=""):
    """记录一篇无PDF跳过的文献"""
    d = load()
    if not d:
        print("❌ 无进度文件")
        return
    key = f"{first}_{year}"
    d["current"] = key
    if key not in d["completed"]:
        d["completed"].append(key)
        d["processed"] += 1
        d["remaining"] = max(0, d["total"] - d["processed"])
    d["results"]["SKIPPED"] = d["results"].get("SKIPPED", 0) + 1
    d["status"] = "running"
    save(d)
    print(f"⏭️ {key} → SKIPPED ({reason or '无PDF'})  "
          f"({d['processed']}/{d['total']}  跳过:{d['results']['SKIPPED']})")


RETRY_SEQUENCE = [30, 60, 120, 240]  # 指数退避秒数
MAX_RETRIES = 4


def backoff():
    """计算退避等待秒数。每次调用递增，超过4次返回-1表示放弃"""
    d = load()
    if not d:
        print("❌ 无进度文件")
        return
    count = d.get("retry_count", 0)
    if count >= MAX_RETRIES:
        d["retry_count"] = 0
        save(d)
        print("-1")
        return
    wait = RETRY_SEQUENCE[count]
    d["retry_count"] = count + 1
    save(d)
    print(f"{wait}")


def reset_retry():
    """重置退避计数器（成功完成一篇后调用）"""
    d = load()
    if d:
        d["retry_count"] = 0
        save(d)


def verify():
    d = load()
    if not d:
        print("❌ 无进度文件")
        return
    # progress标记done但MD不存在 → 移除
    # 使用glob匹配，支持新的 {Author}_{Year}_{hash}.md 格式
    missing = []
    for k in d["completed"]:
        found = False
        for sd in SUBDIRS:
            md_dir = RECORDS / sd
            if md_dir.exists():
                # 匹配旧格式 {key}.md 或新格式 {key}_*.md
                if list(md_dir.glob(f"{k}.md")) or list(md_dir.glob(f"{k}_*.md")):
                    found = True
                    break
        if not found:
            missing.append(k)
    # MD存在但progress未标记 → 补标记
    unmarked = []
    for sd in SUBDIRS:
        md_dir = RECORDS / sd
        if md_dir.exists():
            for m in md_dir.glob("*.md"):
                stem = m.stem
                # 从文件名提取key：可能是 Author_Year 或 Author_Year_hash
                parts = stem.rsplit('_', 1)
                if len(parts) >= 2:
                    # 尝试提取 Author_Year 部分（去掉hash后缀）
                    # 如果最后一部分是6位hex，则认为是hash，取前面部分
                    if len(parts[-1]) == 6 and all(c in '0123456789abcdef' for c in parts[-1]):
                        k = parts[0]
                    else:
                        k = stem
                else:
                    k = stem
                if k not in d["completed"] and k != "TEMPLATE":
                    unmarked.append((sd, k))

    dirty = False
    print("=" * 50)
    print("🔍 双重验证")
    print("=" * 50)

    if missing:
        print(f"\n❌ 进度标记done但MD不存在 ({len(missing)}篇)，将移除并重做:")
        for k in missing:
            print(f"   {k}")
            d["completed"].remove(k)
            d["processed"] -= 1
            d["remaining"] += 1
        dirty = True
    else:
        print("✅ 进度↔MD 一致性: OK")

    if unmarked:
        print(f"\n⚠️ MD存在但进度未标记 ({len(unmarked)}篇)，将补标记:")
        for sd, k in unmarked:
            mp = RECORDS / sd / f"{k}.md"
            dec = "MAYBE"
            if mp.exists():
                txt = mp.read_text(encoding="utf-8")
                if "**决策**：INCLUDE" in txt:
                    dec = "INCLUDE"
                elif "**决策**：EXCLUDE" in txt:
                    dec = "EXCLUDE"
            d["completed"].append(k)
            d["processed"] += 1
            d["remaining"] = max(0, d["total"] - d["processed"])
            d["results"][dec] += 1
            print(f"   {sd}/{k} → {dec}")
        dirty = True
    else:
        print("✅ MD↔进度 一致性: OK")

    if not missing and not unmarked:
        print("\n✅ 完全一致，无需修复")

    if dirty:
        save(d)
        print("\n修复结果已保存。")


def summary():
    d = load()
    if not d:
        print("❌ 无进度文件")
        return
    r = d["results"]
    print("=" * 50)
    print("📊 最终汇总")
    print("=" * 50)
    print(f"  总文献: {d['total']}   已处理: {d['processed']}")
    print(f"  INCLUDE: {r['INCLUDE']}    EXCLUDE: {r['EXCLUDE']}")
    print(f"  MAYBE(需人工复核): {r['MAYBE']}    SKIPPED(无PDF): {r.get('SKIPPED',0)}    ERROR: {r['ERROR']}")
    print(f"  开始: {d.get('started_at','?')}")
    print("=" * 50)

    maybe_n = r.get("MAYBE", 0)
    if maybe_n > 0:
        print(f"\n⚠️ {maybe_n}篇MAYBE需人工复核:")
        md = RECORDS / "MAYBE"
        if md.exists():
            for m in sorted(md.glob("*.md")):
                if m.stem != "TEMPLATE":
                    print(f"   📄 {m.name}")
    d["status"] = "done"
    save(d)
    print("\n✅ 已标记为 done")


# ====== PDF 文件名纠偏 ======

MAPPING_FILE = BASE / "pdf_mapping.json"


def load_mapping():
    if MAPPING_FILE.exists():
        with open(MAPPING_FILE, encoding="utf-8") as f:
            return json.load(f)
    return {}


def save_mapping(m):
    with open(MAPPING_FILE, "w", encoding="utf-8") as f:
        json.dump(m, f, indent=2, ensure_ascii=False)


def normalize(s: str) -> str:
    """标准化文件名：小写、去空格/连字符/下划线/括号等"""
    import re
    s = s.lower().strip()
    s = re.sub(r'[\s_\-]+', '', s)          # 去空格/连字符/下划线
    s = re.sub(r'[()[\]{}]', '', s)          # 去括号
    s = re.sub(r'\.pdf$', '', s)             # 去扩展名
    s = re.sub(r'[^a-z0-9\u4e00-\u9fff]', '', s)  # 只保留字母数字中文
    return s


def fuzzy_score(key: str, pdf_name: str) -> float:
    """计算 Key 与 PDF 文件名的相似度（0~1）"""
    nk = normalize(key)
    np = normalize(pdf_name)
    # 先检查 key 是否包含在 pdf 中（处理 Wang_2024 匹配 Wang_2024_VR_Education 的情况）
    if nk in np or np in nk:
        return 0.95
    return difflib.SequenceMatcher(None, nk, np).ratio()


def fix_pdf_names():
    """
    一次性纠偏：扫描 pdfs/ 目录，与文献池 CSV 中的 Key 匹配。
    输出 pdf_mapping.json，后续筛选时查表。

    匹配策略：
    1. 精确匹配（忽略大小写）→ 直接记录
    2. 模糊匹配（相似度 ≥ 0.7）→ 自动记录
    3. 无法匹配 → 列出给用户确认

    AI不参与此过程，全部由Python确定性操作。
    """
    import difflib

    pdfs_dir = BASE / "pdfs"
    if not pdfs_dir.exists():
        print("❌ pdfs/ 目录不存在")
        return

    # 1. 扫描 pdfs/ 目录
    pdf_files = [f.name for f in pdfs_dir.glob("*.pdf")]
    if not pdf_files:
        print("❌ pdfs/ 目录中没有 PDF 文件")
        return
    print(f"[PDF] 扫描到 {len(pdf_files)} 个 PDF 文件")

    # 2. 读取文献池 CSV
    csv_candidates = list(BASE.glob("dedup_pool_with_abstracts*.csv"))
    if not csv_candidates:
        print("❌ 未找到 dedup_pool_with_abstracts*.csv")
        return
    csv_path = csv_candidates[0]
    keys = []
    with open(csv_path, encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        for row in reader:
            k = row.get("Key", "").strip()
            if k:
                keys.append(k)
    print(f"[CSV] 读取到 {len(keys)} 个 Key")

    # 3. 匹配
    mapping = {}       # key → pdf_filename
    auto_matched = []  # (key, pdf, score)
    unmatched_keys = []
    unmatched_pdfs = list(pdf_files)  # 未被匹配的 PDF
    used_pdfs = set()

    for key in keys:
        # 精确匹配
        exact = f"{key}.pdf"
        if exact in pdf_files:
            mapping[key] = exact
            used_pdfs.add(exact)
            if exact in unmatched_pdfs:
                unmatched_pdfs.remove(exact)
            continue

        # 模糊匹配
        best_score = 0.0
        best_pdf = None
        for pdf in pdf_files:
            if pdf in used_pdfs:
                continue
            score = fuzzy_score(key, pdf)
            if score > best_score:
                best_score = score
                best_pdf = pdf

        if best_pdf and best_score >= 0.7:
            mapping[key] = best_pdf
            used_pdfs.add(best_pdf)
            auto_matched.append((key, best_pdf, best_score))
            if best_pdf in unmatched_pdfs:
                unmatched_pdfs.remove(best_pdf)
        else:
            unmatched_keys.append(key)

    # 4. 输出结果
    save_mapping(mapping)
    print(f"\n{'='*60}")
    print(f"📊 PDF 文件名匹配结果")
    print(f"{'='*60}")
    print(f"  总 Key 数: {len(keys)}")
    print(f"  精确匹配: {len(mapping) - len(auto_matched)}")
    print(f"  模糊匹配: {len(auto_matched)}")
    print(f"  未匹配 Key: {len(unmatched_keys)}")
    print(f"  未匹配 PDF: {len(unmatched_pdfs)}")

    if auto_matched:
        print(f"\n📋 模糊匹配详情（已自动记录）:")
        for key, pdf, score in sorted(auto_matched, key=lambda x: -x[2]):
            print(f"   {key:40s} → {pdf:50s} ({score:.0%})")

    if unmatched_keys:
        print(f"\n⚠️ 未匹配的 Key（需人工确认 PDF）:")
        for k in unmatched_keys:
            print(f"   ❓ {k}")

    if unmatched_pdfs:
        print(f"\n⚠️ 未匹配的 PDF（可能在 CSV 中没有对应 Key）:")
        for p in unmatched_pdfs:
            print(f"   📄 {p}")

    print(f"\n✅ 映射已保存: {MAPPING_FILE}")
    print(f"   共 {len(mapping)} 条映射")

    if unmatched_keys or unmatched_pdfs:
        print(f"\n💡 如需手动补充映射，可编辑 {MAPPING_FILE}")
        print(f"   格式: {{\"Key\": \"actual_filename.pdf\", ...}}")


# ====== CLI ======
if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else ""
    if cmd == "init":
        init()
    elif cmd == "set-total":
        set_total(int(sys.argv[2]))
    elif cmd == "check":
        check()
    elif cmd == "update":
        update(sys.argv[2], sys.argv[3], sys.argv[4],
               sys.argv[5] if len(sys.argv) > 5 else "")
    elif cmd == "skip":
        skip(sys.argv[2], sys.argv[3],
             sys.argv[4] if len(sys.argv) > 4 else "")
    elif cmd == "retry-wait":
        backoff()
    elif cmd == "retry-reset":
        reset_retry()
    elif cmd == "verify":
        verify()
    elif cmd == "summary":
        summary()
    elif cmd == "fix-pdf-names":
        fix_pdf_names()
    else:
        print("用法: python progress_manager.py <命令>")
        print("  init         初始化进度文件")
        print("  set-total N  设置总文献数")
        print("  check        查看进度")
        print("  update First Year Decision ExclCode  记录一篇")
        print("  verify       双重验证MD↔进度")
        print("  summary      汇总报告+标记完成")
