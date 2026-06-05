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

import sys, os, json, csv, shutil, difflib
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
    import re

    nk = normalize(key)
    np = normalize(pdf_name)

    # 先检查 key 是否包含在 pdf 中（处理 Wang_2024 匹配 Wang_2024_VR_Education 的情况）
    if nk in np or np in nk:
        return 0.95

    # 从 Key 中提取 Author 和 Year（格式: Author_Year 或 De La Garza_2019）
    key_parts = key.rsplit('_', 1)  # 从右边分割，处理 "De La Garza_2019" 的情况
    if len(key_parts) >= 2:
        key_author = key_parts[0].strip()
        key_year = key_parts[1].strip()

        # 从 PDF 文件名中提取 Author 和 Year（格式: Author 等 - Year - ...）
        # 支持带空格、连字符、和/等的作者名
        # 如 "Al-Kadi和Donnon - 2013 - ..."、"De La Garza 等 - 2019 - ..."
        match = re.match(r'^([\w\u4e00-\u9fff]+(?:[\s\-]+[\w\u4e00-\u9fff]+)*)[\s]*(?:等|和[\w\u4e00-\u9fff]+)?\s*-\s*(\d{4})', pdf_name)
        if match:
            pdf_author = match.group(1).strip()
            pdf_year = match.group(2).strip()

            # 合著者处理：Key通常只有第一作者，PDF含"和XXX"
            # "Zapf和Ujiki" → 提取第一作者 "Zapf"
            pdf_first_author = re.split(r'[和&]', pdf_author)[0].strip()

            # 比较 Author 和 Year
            if normalize(key_author) == normalize(pdf_first_author) and key_year == pdf_year:
                return 0.98  # 高置信度匹配

            # 只 Year 匹配 + Author 部分匹配
            if key_year == pdf_year:
                author_sim = difflib.SequenceMatcher(None, normalize(key_author), normalize(pdf_first_author)).ratio()
                if author_sim >= 0.6:
                    return 0.85 + author_sim * 0.1  # 0.91 ~ 0.95

    return difflib.SequenceMatcher(None, nk, np).ratio()


def fix_pdf_names():
    """
    PDF文件名标题匹配：扫描 pdfs/ 目录，与文献池 CSV 匹配。
    输出 pdf_mapping.json，后续筛选时查表。

    匹配策略（四级）：
    1. 精确文件名匹配（Key.pdf）
    2. PDF文件名标题匹配（从文件名提取标题，匹配CSV标题）
    3. 作者+年份匹配（宽松匹配）
    4. 无法匹配 → 列出给用户确认

    核心思路：PDF文件名格式为 "Author 等 - Year - Title.pdf"，
    提取 Year 后面的标题部分，直接去文献库匹配文献标题。
    """
    import re
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
    csv_data = {}  # key -> {title, author, year}
    with open(csv_path, encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        for row in reader:
            k = row.get("Key", "").strip()
            if k:
                csv_data[k] = {
                    "title": row.get("Title", "").strip(),
                    "author": row.get("Author", "").strip(),
                    "year": row.get("Year", "").strip(),
                }
    print(f"[CSV] 读取到 {len(csv_data)} 个 Key")

    # 3. 从PDF文件名提取标题
    def extract_title_from_filename(name):
        """从 'Author 等 - Year - Title.pdf' 提取标题"""
        m = re.match(r'^.*?\s*-\s*\d{4}\s*-\s*(.+)\.pdf$', name)
        return m.group(1).strip() if m else ''

    def norm(t):
        """标准化文本：小写、去标点、压缩空格"""
        t = re.sub(r'[^a-z0-9\s]', '', t.lower())
        return re.sub(r'\s+', ' ', t).strip()

    # 4. 匹配
    mapping = {}
    auto_matched = []
    unmatched_keys = list(csv_data.keys())
    unmatched_pdfs = list(pdf_files)
    used_pdfs = set()

    # 4a. 精确文件名匹配
    for key in list(unmatched_keys):
        exact = f"{key}.pdf"
        if exact in pdf_files:
            mapping[key] = exact
            used_pdfs.add(exact)
            unmatched_keys.remove(key)
            if exact in unmatched_pdfs:
                unmatched_pdfs.remove(exact)

    # 4b. PDF文件名标题匹配（核心方法）
    # 建立 PDF文件名标题 -> PDF文件名 的索引
    pdf_title_index = {}  # norm_title -> pdf_filename
    for pdf in pdf_files:
        if pdf in used_pdfs:
            continue
        title = extract_title_from_filename(pdf)
        if title:
            pdf_title_index[norm(title)] = pdf

    # 建立 CSV标题 -> Key 的索引
    csv_title_index = {}  # norm_title -> key
    for key, info in csv_data.items():
        if key in mapping:
            continue
        csv_title_index[norm(info["title"])] = key

    # 匹配：用CSV标题去PDF标题索引中查找
    title_matched = []
    for norm_csv_title, key in csv_title_index.items():
        if not norm_csv_title:
            continue

        # 精确匹配
        if norm_csv_title in pdf_title_index:
            pdf = pdf_title_index[norm_csv_title]
            if pdf not in used_pdfs:
                mapping[key] = pdf
                used_pdfs.add(pdf)
                title_matched.append((key, pdf, 1.0))
                if key in unmatched_keys:
                    unmatched_keys.remove(key)
                if pdf in unmatched_pdfs:
                    unmatched_pdfs.remove(pdf)
                continue

        # 模糊匹配（相似度≥0.8）
        best_score = 0.0
        best_pdf = None
        for norm_pdf_title, pdf in pdf_title_index.items():
            if pdf in used_pdfs:
                continue
            sim = difflib.SequenceMatcher(None, norm_csv_title[:80], norm_pdf_title[:80]).ratio()
            if sim > best_score:
                best_score = sim
                best_pdf = pdf

        if best_pdf and best_score >= 0.8:
            mapping[key] = best_pdf
            used_pdfs.add(best_pdf)
            title_matched.append((key, best_pdf, best_score))
            if key in unmatched_keys:
                unmatched_keys.remove(key)
            if best_pdf in unmatched_pdfs:
                unmatched_pdfs.remove(best_pdf)

    auto_matched.extend(title_matched)

    # 4c. 作者+年份匹配（宽松匹配，处理未匹配的Key）
    still_unmatched = []
    for key in unmatched_keys:
        info = csv_data.get(key, {})
        parts = key.rsplit('_', 1)
        if len(parts) < 2:
            still_unmatched.append(key)
            continue

        author_last = parts[0].split(',')[0].split(' ')[0].lower()
        year = info.get("year", "")

        found = False
        for pdf in pdf_files:
            if pdf in used_pdfs:
                continue
            pdf_lower = pdf.lower()
            if author_last in pdf_lower and year in pdf_lower:
                mapping[key] = pdf
                used_pdfs.add(pdf)
                auto_matched.append((key, pdf, 1.0))
                if pdf in unmatched_pdfs:
                    unmatched_pdfs.remove(pdf)
                found = True
                break

        if not found:
            still_unmatched.append(key)

    unmatched_keys = still_unmatched

    # 5. 输出结果
    save_mapping(mapping)
    print(f"\n{'='*60}")
    print(f"📊 PDF 匹配结果")
    print(f"{'='*60}")
    print(f"  总 Key 数: {len(csv_data)}")
    print(f"  精确匹配: {len(mapping) - len(auto_matched)}")
    print(f"  PDF文件名标题匹配: {len(title_matched)}")
    print(f"  作者+年份匹配: {len(auto_matched) - len(title_matched)}")
    print(f"  总匹配: {len(mapping)}")
    print(f"  未匹配 Key: {len(unmatched_keys)}")
    print(f"  未匹配 PDF: {len(unmatched_pdfs)}")

    if title_matched:
        print(f"\n📋 PDF文件名标题匹配详情:")
        for key, pdf, score in sorted(title_matched, key=lambda x: -x[2])[:20]:
            csv_t = csv_data.get(key, {}).get("title", "")[:40]
            pdf_t = extract_title_from_filename(pdf)[:40]
            print(f"   {key:30s} ↔ {pdf_t}")
            if score < 1.0:
                print(f"     相似度: {score:.0%}")

    if unmatched_keys:
        print(f"\n⚠️ 未匹配的 Key ({len(unmatched_keys)}篇):")
        for k in unmatched_keys:
            info = csv_data.get(k, {})
            has_pdf = any(info.get("author", "").split(",")[0].split(" ")[0].lower() in f.lower() for f in pdf_files)
            status = "有PDF(错误)" if has_pdf else "无PDF"
            print(f"   ❓ {k:30s} [{status}]")

    print(f"\n✅ 映射已保存: {MAPPING_FILE}")
    print(f"   共 {len(mapping)} 条映射")

    return mapping, unmatched_keys, unmatched_pdfs


def fix_pdf_names_ai():
    """
    AI辅助匹配：对未匹配的Key，读取PDF第一页内容进行二次匹配。
    Python负责提取文本，AI负责判断语义匹配。

    流程：
    1. 读取已有映射，找出未匹配的Key和PDF
    2. 对每个未匹配Key，找相似度≥0.3的候选PDF
    3. 用PyMuPDF提取候选PDF第一页文本
    4. 输出候选列表（Key → 候选PDF → 第一页摘要）
    5. 主agent派发子agent判断哪个候选正确
    """
    import re

    # 1. 读取已有映射
    mapping = load_mapping()
    matched_pdfs = set(mapping.values())

    # 2. 读取文献池CSV
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

    # 3. 找到未匹配的Key和PDF
    unmatched_keys = [k for k in keys if k not in mapping]
    pdfs_dir = BASE / "pdfs"
    unmatched_pdfs = [f.name for f in pdfs_dir.glob("*.pdf") if f.name not in matched_pdfs]

    print(f"[AI匹配] 未匹配Key: {len(unmatched_keys)}, 未匹配PDF: {len(unmatched_pdfs)}")

    if not unmatched_keys or not unmatched_pdfs:
        print("✅ 无未匹配项，无需AI辅助")
        return

    # 4. 对每个未匹配Key，找候选PDF
    candidates = {}
    for key in unmatched_keys:
        key_candidates = []
        for pdf in unmatched_pdfs:
            score = fuzzy_score(key, pdf)
            if score >= 0.3:  # 低阈值，扩大候选范围
                key_candidates.append((pdf, score))
        if key_candidates:
            key_candidates.sort(key=lambda x: -x[1])
            candidates[key] = key_candidates[:5]  # 最多5个候选

    print(f"[AI匹配] {len(candidates)} 个Key有候选PDF")

    # 5. 提取候选PDF第一页文本
    try:
        import fitz
    except ImportError:
        print("[AI匹配] PyMuPDF未安装，尝试安装...")
        os.system(f"{sys.executable} -m pip install PyMuPDF -q")
        import fitz

    output_lines = []
    output_lines.append("# PDF 匹配候选列表")
    output_lines.append("")
    output_lines.append("> 对于每个未匹配的Key，列出相似度最高的候选PDF及第一页摘要。")
    output_lines.append("> 请判断哪个候选PDF是正确的匹配。")
    output_lines.append("")
    output_lines.append("---")

    for key, cands in candidates.items():
        output_lines.append("")
        output_lines.append(f"## Key: {key}")
        output_lines.append("")
        output_lines.append("| # | 候选PDF | 相似度 | 第一页摘要 |")
        output_lines.append("|---|---------|--------|-----------|")

        for i, (pdf, score) in enumerate(cands, 1):
            pdf_path = pdfs_dir / pdf
            first_page_text = ""
            try:
                doc = fitz.open(str(pdf_path))
                if len(doc) > 0:
                    first_page_text = doc[0].get_text("text")[:500].replace("\n", " ").strip()
                doc.close()
            except Exception as e:
                first_page_text = f"[提取失败: {e}]"

            # 截断过长的摘要
            if len(first_page_text) > 200:
                first_page_text = first_page_text[:200] + "..."

            output_lines.append(f"| {i} | `{pdf}` | {score:.0%} | {first_page_text} |")

        output_lines.append("")
        output_lines.append(f"**判定**：填入正确的PDF编号（1-{len(cands)}），或填0表示都不匹配。")
        output_lines.append("")

    output_lines.append("---")
    output_lines.append("")
    output_lines.append("## 输出格式")
    output_lines.append("")
    output_lines.append("```json")
    output_lines.append('{"Key1": 1, "Key2": 2, "Key3": 0}')
    output_lines.append("```")
    output_lines.append("")
    output_lines.append("其中数字是候选PDF的编号，0表示都不匹配。")

    # 6. 写入候选列表
    candidates_file = BASE / "pdf_candidates_for_ai.md"
    with open(candidates_file, "w", encoding="utf-8") as f:
        f.write("\n".join(output_lines))

    print(f"\n✅ 候选列表已生成: {candidates_file}")
    print(f"   共 {len(candidates)} 个Key需要AI判断")
    print(f"\n💡 下一步：")
    print(f"   1. 主agent读取 {candidates_file}")
    print(f"   2. 对每个Key，读取候选PDF第一页，判断正确匹配")
    print(f"   3. 输出JSON: {{\"Key\": 候选编号, ...}}")
    print(f"   4. 调用 progress_manager.py apply-ai-matches '<JSON>' 更新映射")


def apply_ai_matches(matches_json: str):
    """
    应用AI匹配结果。
    输入格式: {"Key1": 1, "Key2": 2, "Key3": 0}
    其中数字是候选PDF的编号，0表示都不匹配。
    """
    import re

    try:
        matches = json.loads(matches_json)
    except json.JSONDecodeError as e:
        print(f"❌ JSON解析失败: {e}")
        return

    # 读取候选列表
    candidates_file = BASE / "pdf_candidates_for_ai.md"
    if not candidates_file.exists():
        print("❌ 候选列表不存在，请先运行 fix-pdf-names-ai")
        return

    # 解析候选列表
    content = candidates_file.read_text(encoding="utf-8")
    current_key = None
    candidates = {}

    for line in content.split("\n"):
        # 找到Key行
        if line.startswith("## Key: "):
            current_key = line.replace("## Key: ", "").strip()
            candidates[current_key] = []
        # 找到候选PDF行
        elif current_key and line.startswith("| ") and "候选PDF" not in line and "---" not in line:
            parts = line.split("|")
            if len(parts) >= 4:
                try:
                    idx = int(parts[1].strip())
                    pdf_name = parts[2].strip().strip("`")
                    candidates[current_key].append((idx, pdf_name))
                except ValueError:
                    pass

    # 应用匹配
    mapping = load_mapping()
    applied = 0
    for key, choice in matches.items():
        if choice == 0:
            print(f"⏭️ {key} → 跳过（都不匹配）")
            continue

        if key not in candidates:
            print(f"⚠️ {key} → 候选列表中不存在")
            continue

        # 找到对应的PDF
        for idx, pdf_name in candidates[key]:
            if idx == choice:
                mapping[key] = pdf_name
                applied += 1
                print(f"✅ {key} → {pdf_name}")
                break
        else:
            print(f"⚠️ {key} → 编号 {choice} 不存在")

    save_mapping(mapping)
    print(f"\n✅ 已应用 {applied} 条AI匹配，总映射: {len(mapping)} 条")


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
    elif cmd == "fix-pdf-names-ai":
        fix_pdf_names_ai()
    elif cmd == "apply-ai-matches":
        apply_ai_matches(sys.argv[2] if len(sys.argv) > 2 else "")
    else:
        print("用法: python progress_manager.py <命令>")
        print("  init         初始化进度文件")
        print("  set-total N  设置总文献数")
        print("  check        查看进度")
        print("  update First Year Decision ExclCode  记录一篇")
        print("  verify       双重验证MD↔进度")
        print("  summary      汇总报告+标记完成")
        print("  fix-pdf-names      PDF文件名纠偏（模糊匹配）")
        print("  fix-pdf-names-ai   AI辅助匹配（读取PDF内容）")
        print("  apply-ai-matches '<JSON>'  应用AI匹配结果")
