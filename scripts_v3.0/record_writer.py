#!/usr/bin/env python3
"""
record_writer.py — screen-repro v3.0 筛选记录写入器
====================================================
职责: 双写MD文件 + SQLite数据库
方案B: SQLite为权威数据源，MD为人类可读记录

接口:
    write_record(data: dict, db: DbManager, base: Path) -> str

流程:
    1. 验证data格式
    2. 生成MD文件名: {key}.md
    3. 填充MD模板 → 写入 screening_records/{decision}/{key}.md
    4. 写入SQLite screening表
"""

import sys
import io
import json
from datetime import datetime
from pathlib import Path

# Windows兼容性：强制UTF-8编码（仅在直接运行时执行）
if sys.platform == 'win32' and __name__ == '__main__':
    if hasattr(sys.stdout, 'buffer'):
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
    if hasattr(sys.stderr, 'buffer'):
        sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8')

# 常量
VALID_DECISIONS = {"INCLUDE", "EXCLUDE", "MAYBE"}
VALID_CODES = {None, "", "E1", "E2", "E3", "E4", "E5", "E6", "E7", "E8", "E9"}
# 同时接受emoji和文本格式（picos_judge.py返回文本格式）
VALID_RESULTS_EMOJI = {"✅", "❌", "⚠️"}
VALID_RESULTS_TEXT = {"PASS", "FAIL", "UNCERTAIN"}
VALID_RESULTS = VALID_RESULTS_EMOJI | VALID_RESULTS_TEXT

# MD模板
TEMPLATE = """# 筛选记录 — {key}

> **筛选日期**：{screening_date}
> **筛选版本**：screen-repro v3.1
> **筛选轮次**：{screening_round}
> **AI模型**：{model}
> **筛选方法**：PDF全文阅读（AI自动判定）

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

## 可复现性信息

| 字段 | 值 |
|------|-----|
| **指纹** | {fingerprint} |
| **模型版本** | {model_version} |
| **Prompt Hash** | {prompt_hash} |
| **提取方法** | {extraction_method} |
| **温度** | {temperature} |
| **Seed** | {seed} |

---

*screen-repro v3.0 自动生成 | {screening_date}*
"""


def ts() -> str:
    """当前时间戳"""
    return datetime.now().isoformat(timespec="seconds")


def validate(data: dict) -> list[str]:
    """
    验证数据格式

    返回: 错误列表（空列表=验证通过）
    """
    errors = []

    # 必填字段
    for field in ["key", "decision"]:
        if not data.get(field):
            errors.append(f"缺少必填字段: {field}")

    # 决策合法性
    if data.get("decision") not in VALID_DECISIONS:
        errors.append(f"无效的决策: {data.get('decision')}")

    # 排除码
    excl = data.get("exclusion_code")
    if excl and excl not in VALID_CODES:
        errors.append(f"无效的排除码: {excl}")

    # EXCLUDE必须有排除码
    if data.get("decision") == "EXCLUDE" and not excl:
        errors.append("EXCLUDE决策必须提供排除码")

    # PICOS维度
    picos = data.get("picos", {})
    for dim in ["P", "I", "C", "O", "S"]:
        d = picos.get(dim, {})
        if not d:
            errors.append(f"缺少PICOS.{dim}判定")
            continue
        if d.get("result") not in VALID_RESULTS:
            errors.append(f"PICOS.{dim}.result 无效: {d.get('result')}")
        if not isinstance(d.get("evidence"), list):
            errors.append(f"PICOS.{dim}.evidence 不是数组")
        if "analysis" not in d:
            errors.append(f"PICOS.{dim}.analysis 缺失")

    return errors


def build_picos_section(dim: str, data: dict) -> str:
    """构建单个PICOS维度的MD段落"""
    # 纯文本到emoji的映射（支持两种格式输入）
    result_emoji = {"PASS": "✅", "FAIL": "❌", "UNCERTAIN": "⚠️",
                    "✅": "✅", "❌": "❌", "⚠️": "⚠️"}
    result_text = {"PASS": "符合", "FAIL": "不符合", "UNCERTAIN": "不确定",
                   "✅": "符合", "❌": "不符合", "⚠️": "不确定"}

    dim_labels = {
        "P": "人群 (Population)",
        "I": "干预 (Intervention)",
        "C": "对照 (Comparator)",
        "O": "结局 (Outcomes)",
        "S": "研究设计 (Study Design)",
    }

    result = data.get('result', '?')
    emoji = result_emoji.get(result, '')
    text = result_text.get(result, '')

    lines = [
        f"### {dim} — {dim_labels.get(dim, dim)}",
        "",
        f"**判定**：{emoji} {text}",
        "",
    ]

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
        lines.append(f'> "{ev}"')
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

    # 确定筛选轮次
    screening_round = data.get("screening_round", "正式筛选")

    return TEMPLATE.format(
        key=data.get("key", ""),
        title=data.get("title", ""),
        author=data.get("author", ""),
        year=data.get("year", ""),
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
        exclusion_code=data.get("exclusion_code", "") or "无",
        reason=data.get("reason", ""),
        model=data.get("model", "unknown"),
        screening_date=data.get("screened_at", now[:10]),
        screening_round=screening_round,
        fingerprint=data.get("fingerprint", ""),
        model_version=data.get("model_version", ""),
        prompt_hash=data.get("prompt_hash", ""),
        extraction_method=data.get("extraction_method", ""),
        temperature=data.get("temperature", 0),
        seed=data.get("seed", 42),
    )


def write_md(content: str, data: dict, base: Path) -> str:
    """
    写入MD文件

    参数:
        content: MD文件内容
        data: 筛选数据
        base: 项目根目录

    返回: 写入的MD文件名
    """
    key = data.get("key", "unknown")
    decision = data.get("decision", "MAYBE")

    # 创建目录
    records_dir = base / "screening_records" / decision
    records_dir.mkdir(parents=True, exist_ok=True)

    # 写入文件
    md_path = records_dir / f"{key}.md"
    md_path.write_text(content, encoding="utf-8")

    return f"screening_records/{decision}/{key}.md"


def write_record(data: dict, db, base) -> str:
    """
    写入筛选记录（双写：MD + SQLite）

    参数:
        data: 完整的判定结果dict
        db: DbManager实例
        base: 项目根目录（str或Path）

    返回: 写入的MD文件路径

    异常:
        ValueError: 数据验证失败
    """
    # 确保base是Path对象
    if isinstance(base, str):
        base = Path(base)

    # 1. 验证数据
    errors = validate(data)
    if errors:
        raise ValueError(f"数据验证失败: {'; '.join(errors)}")

    # 2. 生成MD内容并写入
    md_content = fill_template(data)
    md_path = write_md(md_content, data, base)

    # 3. 更新data中的md_path
    data["md_path"] = md_path

    # 4. 写入SQLite
    db.save_screening(data)

    print(f"[Record] 已写入: {md_path}")
    return md_path


# ====== CLI入口（兼容v2.3）======

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(
        description="screen-repro v3.0 筛选记录写入器")
    parser.add_argument("json_input", nargs="?", default="-",
                        help="JSON字符串或'-'从stdin读取")
    parser.add_argument("--db", default="screening.db",
                        help="SQLite数据库路径")

    args = parser.parse_args()

    try:
        # 读取JSON
        if args.json_input == "-":
            json_str = sys.stdin.read()
        else:
            json_str = args.json_input

        data = json.loads(json_str)

        # 导入DbManager
        sys.path.insert(0, str(Path(__file__).parent))
        from db_manager import DbManager

        # 写入记录
        base = Path.cwd()
        db_path = base / args.db

        with DbManager(db_path) as db:
            md_path = write_record(data, db, base)
            print(f"✅ 写入成功: {md_path}")

        sys.exit(0)

    except json.JSONDecodeError as e:
        print(f"❌ JSON解析失败: {e}", file=sys.stderr)
        sys.exit(1)
    except ValueError as e:
        print(f"❌ {e}", file=sys.stderr)
        sys.exit(1)
    except Exception as e:
        print(f"❌ 写入失败: {e}", file=sys.stderr)
        sys.exit(2)
