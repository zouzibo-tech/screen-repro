#!/usr/bin/env python3
"""Focused P3 PICOS lock high-risk audit.

This is a risk-screening tool, not a decision engine. It audits the included P3
lock pool using the human-readable screening record sections and verifies that
quoted evidence can be found in the current extracted txt. It intentionally avoids
flagging generic background mentions in the full PDF unless they appear in the
C/O/S screening rationale.
"""
from __future__ import annotations

import argparse
import csv
import json
import re
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

import yaml


@dataclass
class Finding:
    severity: str
    uid: str
    category: str
    message: str
    evidence: str = ""


def rel(path: Path, root: Path) -> str:
    try:
        return path.relative_to(root).as_posix()
    except ValueError:
        return path.as_posix()


def norm_text(value: str) -> str:
    value = value.lower()
    value = value.replace("–", "-").replace("—", "-").replace("−", "-")
    value = re.sub(r"[^a-z0-9\u4e00-\u9fff]+", " ", value)
    return re.sub(r"\s+", " ", value).strip()


def read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace") if path.exists() else ""


def contains_any(text: str, terms: list[str]) -> list[str]:
    text_norm = norm_text(text)
    hits = []
    for term in terms:
        if term.startswith("re:"):
            if re.search(term[3:], text_norm):
                hits.append(term)
        elif norm_text(term) in text_norm:
            hits.append(term)
    return hits


def section(md: str, letter: str) -> str:
    """Extract a PICOS section from screening record markdown."""
    pattern = re.compile(rf"^###\s*{re.escape(letter)}\b[\s\S]*?(?=^###\s*[PICOS]\b|^##\s|\Z)", re.M)
    match = pattern.search(md)
    return match.group(0) if match else ""


def all_picos_sections(md: str) -> str:
    return "\n".join(section(md, x) for x in "PICOS")


def evidence_quotes_from_sections(md: str) -> list[str]:
    quotes: list[str] = []
    for sec in [section(md, x) for x in "PICOS"]:
        in_evidence = False
        for raw in sec.splitlines():
            line = raw.strip()
            if "原文证据" in line or "Full-Text Evidence" in line:
                in_evidence = True
                continue
            if line.startswith("**分析") or line.startswith("**判定") or line.startswith("---"):
                in_evidence = False
            if not in_evidence or not line.startswith(">"):
                continue
            q = line.lstrip(">").strip().strip(' "“”')
            q_norm = norm_text(q)
            # Skip admin annotations, JSON-like leftovers, and labels rather than verbatim evidence.
            if len(q_norm) < 35:
                continue
            if any(x in q for x in ["修正说明", "Admin", "analysis", "reason", "关键词", "{"]):
                continue
            quotes.append(q)
    return quotes


def quote_found(quote: str, txt_norm: str) -> tuple[bool, float]:
    qn = norm_text(quote)
    if not qn:
        return True, 1.0
    if qn in txt_norm:
        return True, 1.0
    tokens = [t for t in qn.split() if len(t) >= 4]
    if not tokens:
        return True, 1.0
    found = sum(1 for t in tokens if t in txt_norm)
    ratio = found / len(tokens)
    return ratio >= 0.72, ratio


NON_VR_CONTROL = [
    "control group", "non-training control", "non training control", "no training control", "traditional", "conventional",
    "standard lectures", "bedside instruction", "usual training", "usual curriculum", "box trainer", "physical simulator",
    "non-vr", "non vr", "对照组", "无训练对照", "传统", "常规", "标准讲座", "床边教学", "非vr",
]

VR_INTERNAL = [
    "vr vs vr", "both groups used vr", "both groups received vr", "both groups trained on", "both groups practiced on",
    "same vr simulator", "same simulator", "hmd vs desktop", "desktop vs immersive", "feedback frequency", "feedback-plus",
    "augmented feedback", "distributed practice", "massed practice", "same fb circumstances", "同一vr", "vr内部", "两组均使用",
    "不同反馈", "分散练习", "集中练习",
]

EXPERIENCE_VALIDATION = [
    "junior resident", "senior resident", "expert", "novice", "experienced surgeon", "experience level", "known groups",
    "known-groups", "construct validity", "validation study", "simulator validation", "messick", "proficiency-based test",
    "效度验证", "经验水平", "专家组", "新手组",
]

NO_CONTROL = [
    "no control group", "without a control group", "single group", "single-arm", "single arm", "one-group", "one group",
    "pre-post", "pre post", "before-after", "within-subject", "within subject", "within-participant", "within participant",
    "无对照组", "单组", "自身前后",
]

SELF_KNOWLEDGE = [
    "self-confidence", "motivation", "satisfaction", "perceived", "readiness", "questionnaire", "survey", "knowledge test",
    "knowledge retention", "mcq", "multiple choice", "attitude", "cognitive load", "自信", "满意度", "动机", "自评", "感知", "问卷", "知识", "态度",
]

OBJECTIVE_SKILL = [
    "osats", "dops", "global rating", "checklist", "completion time", "time to completion", "accuracy", "error", "errors",
    "score", "performance score", "procedure completion", "skill retention", "transfer test", "manual dexterity", "technical skill",
    "操作评分", "完成时间", "准确", "错误", "技能保持", "技能迁移",
]

DELAY_SIGNAL = ["retention", "follow-up", "follow up", "month", "week", "transfer test", "3 months", "4 months", "6 months", "12 months", "保持", "随访", "迁移"]
IMMEDIATE_ONLY = ["immediate post", "immediately after", "same day", "no follow-up", "without follow-up", "即时", "立即", "无随访"]
DESIGN_RISK = ["cross-sectional", "feasibility", "pilot study", "validation study", "construct validity", "correlation", "observational", "横断面", "可行性", "相关性", "观察性"]
CONTROLLED_SIGNAL = ["randomized", "randomised", "randomly assigned", "control group", "controlled trial", "quasi-experimental", "对照组", "随机"]


def audit_record(root: Path, rec: dict[str, Any]) -> list[Finding]:
    uid = str(rec.get("uid", ""))
    findings: list[Finding] = []
    txt_path = root / "03_Screening" / "txt" / f"{uid}.txt"
    md_candidates = [
        root / "03_Screening" / "screening_records" / "INCLUDE" / f"{uid}.md",
        root / "03_Screening" / "screening_records" / "EXCLUDE" / f"{uid}.md",
    ]
    md_path = next((p for p in md_candidates if p.exists()), None)
    txt = read_text(txt_path)
    md = read_text(md_path) if md_path else ""
    txt_norm = norm_text(txt)

    if not txt:
        findings.append(Finding("HIGH", uid, "missing_txt", "included lock record has no txt file", rel(txt_path, root)))
    if not md_path:
        findings.append(Finding("MEDIUM", uid, "missing_record", "included lock record has no screening record markdown"))
        return findings

    quotes = evidence_quotes_from_sections(md)
    missing = []
    for q in quotes:
        ok, ratio = quote_found(q, txt_norm)
        if not ok:
            missing.append((q, ratio))
    if missing:
        severity = "HIGH" if len(missing) >= 2 else "MEDIUM"
        sample = "; ".join(f"{r:.2f}: {q[:120]}" for q, r in missing[:3])
        findings.append(Finding(severity, uid, "evidence_not_found", f"{len(missing)}/{len(quotes)} PICOS evidence quotes not found in txt", sample))

    c_sec = section(md, "C")
    o_sec = section(md, "O")
    s_sec = section(md, "S")
    c_hits_internal = contains_any(c_sec, VR_INTERNAL)
    c_hits_nonvr = contains_any(c_sec, NON_VR_CONTROL)
    c_hits_exp = contains_any(c_sec + "\n" + s_sec, EXPERIENCE_VALIDATION)
    c_hits_nocontrol = contains_any(c_sec + "\n" + s_sec, NO_CONTROL)

    if c_hits_exp and not c_hits_nonvr:
        findings.append(Finding("HIGH", uid, "C/S_risk", "screening rationale suggests experience-level/validation comparator without explicit non-VR control", ", ".join(c_hits_exp[:8])))
    if c_hits_nocontrol and not c_hits_nonvr:
        findings.append(Finding("HIGH", uid, "C/S_risk", "screening rationale contains no-control/single-group/pre-post language without explicit non-VR control", ", ".join(c_hits_nocontrol[:8])))
    if c_hits_internal and not c_hits_nonvr:
        findings.append(Finding("HIGH", uid, "C_risk", "screening rationale suggests VR-internal comparator without explicit non-VR control", ", ".join(c_hits_internal[:8])))

    o_hits_self = contains_any(o_sec, SELF_KNOWLEDGE)
    o_hits_objective = contains_any(o_sec, OBJECTIVE_SKILL)
    o_hits_immediate = contains_any(o_sec, IMMEDIATE_ONLY)
    o_hits_delay = contains_any(o_sec, DELAY_SIGNAL)
    if o_hits_self and len(o_hits_objective) < 2:
        findings.append(Finding("MEDIUM", uid, "O_risk", "O rationale emphasizes self-report/knowledge terms with weak objective skill signal", f"self={o_hits_self[:8]}; objective={o_hits_objective[:5]}"))
    if o_hits_immediate and not o_hits_delay:
        findings.append(Finding("MEDIUM", uid, "O_risk", "O rationale contains immediate/no-follow-up language without clear retention/transfer signal", ", ".join(o_hits_immediate[:8])))

    s_hits_risk = contains_any(s_sec, DESIGN_RISK)
    s_hits_controlled = contains_any(s_sec, CONTROLLED_SIGNAL)
    if s_hits_risk and not s_hits_controlled:
        findings.append(Finding("MEDIUM", uid, "S_risk", "S rationale contains design-risk terms without clear randomized/controlled signal", ", ".join(s_hits_risk[:8])))

    return findings


def write_reports(root: Path, findings: list[Finding], audited: int, report_dir: Path) -> tuple[Path, Path, Path]:
    report_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    csv_path = report_dir / f"p3_picos_lock_audit_{stamp}.csv"
    json_path = report_dir / f"p3_picos_lock_audit_{stamp}.json"
    md_path = report_dir / f"p3_picos_lock_audit_{stamp}.md"
    with csv_path.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=["severity", "uid", "category", "message", "evidence"])
        writer.writeheader()
        for item in findings:
            writer.writerow(asdict(item))
    payload = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "audited_records": audited,
        "counts": {
            "HIGH": sum(1 for f in findings if f.severity == "HIGH"),
            "MEDIUM": sum(1 for f in findings if f.severity == "MEDIUM"),
            "LOW": sum(1 for f in findings if f.severity == "LOW"),
        },
        "findings": [asdict(f) for f in findings],
    }
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    lines = [
        "# P3 PICOS Lock Focused High-risk Audit",
        "",
        f"- Generated at: `{payload['generated_at']}`",
        f"- Audited included records: **{audited}**",
        f"- HIGH: **{payload['counts']['HIGH']}**",
        f"- MEDIUM: **{payload['counts']['MEDIUM']}**",
        f"- LOW: **{payload['counts']['LOW']}**",
        "",
        "## Interpretation",
        "",
        "This report is a focused risk screen based on screening-record C/O/S/O rationales and evidence quote back-checks. It is not a final PICOS decision. HIGH findings should be reviewed before treating the P3 lock as final.",
        "",
        "## Findings",
    ]
    if findings:
        lines += ["| Severity | UID | Category | Message | Evidence |", "|---|---|---|---|---|"]
        for item in findings:
            ev = item.evidence.replace("|", "\\|").replace("\n", " ")
            msg = item.message.replace("|", "\\|")
            lines.append(f"| {item.severity} | `{item.uid}` | {item.category} | {msg} | {ev} |")
    else:
        lines.append("No focused high-risk patterns detected.")
    md_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return csv_path, json_path, md_path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Focused audit for included P3 lock records.")
    parser.add_argument("--project", default=".")
    parser.add_argument("--lock", default="03_Screening/P3_FINAL_POOL_LOCK_v2_after_admin_review_20260705.yaml")
    parser.add_argument("--report-dir", default="03_Screening/qc/picos_audit")
    args = parser.parse_args(argv)
    root = Path(args.project).resolve()
    lock_path = Path(args.lock)
    if not lock_path.is_absolute():
        lock_path = root / lock_path
    data = yaml.safe_load(lock_path.read_text(encoding="utf-8"))
    records = data.get("records") or []
    findings: list[Finding] = []
    for rec in records:
        findings.extend(audit_record(root, rec))
    findings.sort(key=lambda f: ({"HIGH": 0, "MEDIUM": 1, "LOW": 2}.get(f.severity, 9), f.uid, f.category))
    report_dir = Path(args.report_dir)
    if not report_dir.is_absolute():
        report_dir = root / report_dir
    csv_path, json_path, md_path = write_reports(root, findings, len(records), report_dir)
    high = sum(1 for f in findings if f.severity == "HIGH")
    medium = sum(1 for f in findings if f.severity == "MEDIUM")
    print(f"P3_PICOS_LOCK_AUDIT high={high} medium={medium} audited={len(records)}")
    print(f"REPORT_CSV {rel(csv_path, root)}")
    print(f"REPORT_JSON {rel(json_path, root)}")
    print(f"REPORT_MD {rel(md_path, root)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
