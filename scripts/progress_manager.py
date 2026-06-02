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

import sys, os, json, csv
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
    missing = [k for k in d["completed"]
               if not any((RECORDS / sd / f"{k}.md").exists() for sd in SUBDIRS)]
    # MD存在但progress未标记 → 补标记
    unmarked = []
    for sd in SUBDIRS:
        md_dir = RECORDS / sd
        if md_dir.exists():
            for m in md_dir.glob("*.md"):
                k = m.stem
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
    else:
        print("用法: python progress_manager.py <命令>")
        print("  init         初始化进度文件")
        print("  set-total N  设置总文献数")
        print("  check        查看进度")
        print("  update First Year Decision ExclCode  记录一篇")
        print("  verify       双重验证MD↔进度")
        print("  summary      汇总报告+标记完成")
