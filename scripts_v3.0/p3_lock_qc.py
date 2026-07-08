#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
P3 lock-pool and PDF-mapping QC gate for screen-repro projects.

Run this before freezing FINAL_POOL_LOCK and before any P4/RoB/Meta step.
It validates that the lock file is machine-readable and that each included UID
maps to the expected PDF asset rather than merely to an existing file path.

Examples:
    python scripts/p3_lock_qc.py --project . --lock 03_Screening/FINAL_POOL_LOCK.yaml
    python scripts/p3_lock_qc.py --project . --expected-included 71 --admin-included ChenS_2026_a6c739 --admin-excluded ChenP_2026_31ed5c --admin-excluded JaudC_2021_5c33a0

Exit code:
    0 = no FAIL issues
    1 = at least one FAIL issue
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from collections import defaultdict
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

try:
    import yaml
except Exception as exc:
    raise SystemExit(f"PyYAML is required: {exc}")

try:
    import pymupdf
except Exception:
    pymupdf = None


@dataclass
class Issue:
    severity: str
    scope: str
    uid: str
    message: str
    evidence: str = ""


def rel(path: Path, root: Path) -> str:
    try:
        return path.resolve().relative_to(root.resolve()).as_posix()
    except Exception:
        return str(path)


def illegal_control_chars(text: str) -> list[str]:
    out = []
    for ch in text:
        code = ord(ch)
        if code < 32 and ch not in "\n\r\t":
            out.append(f"0x{code:02x}")
    return sorted(set(out))


def normalize_doi(value: Any) -> str:
    if value is None:
        return ""
    text = str(value).strip().lower()
    text = re.sub(r"^https?://(dx\.)?doi\.org/", "", text)
    return text.rstrip(".,;)")


def normalize_title(value: Any) -> str:
    if value is None:
        return ""
    text = str(value).lower()
    text = re.sub(r"[^a-z0-9\u4e00-\u9fff]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def title_tokens(title: str) -> set[str]:
    stop = {
        "a", "an", "the", "of", "and", "or", "in", "on", "for", "to", "with", "by", "from", "at", "as",
        "study", "trial", "randomized", "controlled", "effects", "effect", "training", "virtual", "reality",
    }
    return {t for t in normalize_title(title).split() if len(t) >= 4 and t not in stop}


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def extract_first_pages(path: Path, max_pages: int) -> str:
    if pymupdf is None:
        return ""
    try:
        doc = pymupdf.open(str(path))
        try:
            return "\n".join(doc[i].get_text() for i in range(min(max_pages, doc.page_count)))
        finally:
            doc.close()
    except Exception:
        return ""


def read_lock(path: Path, root: Path, issues: list[Issue]) -> dict[str, Any] | None:
    scope = rel(path, root)
    if not path.exists():
        issues.append(Issue("FAIL", scope, "", "lock file missing"))
        return None
    raw = path.read_text(encoding="utf-8", errors="surrogateescape")
    bad = illegal_control_chars(raw)
    if bad:
        issues.append(Issue("FAIL", scope, "", "illegal control characters in YAML", ",".join(bad)))
    try:
        data = yaml.safe_load(raw)
    except Exception as exc:
        issues.append(Issue("FAIL", scope, "", "YAML parse failed", repr(exc)))
        return None
    if not isinstance(data, dict):
        issues.append(Issue("FAIL", scope, "", "YAML root is not a mapping"))
        return None
    return data


def validate_lock(
    path: Path,
    data: dict[str, Any],
    root: Path,
    issues: list[Issue],
    expected_included: int | None,
    expected_excluded: int | None,
    admin_included: set[str],
    admin_excluded: set[str],
    max_pages: int,
    min_title_overlap: float,
) -> dict[str, Any]:
    scope = rel(path, root)
    records = data.get("records") or []
    if not isinstance(records, list):
        issues.append(Issue("FAIL", scope, "", "records is not a list"))
        records = []

    if expected_included is not None:
        if data.get("total_included") != expected_included:
            issues.append(Issue("FAIL", scope, "", "unexpected total_included", str(data.get("total_included"))))
        if len(records) != expected_included:
            issues.append(Issue("FAIL", scope, "", "records count does not match expected_included", str(len(records))))
    if expected_excluded is not None and data.get("total_excluded") != expected_excluded:
        issues.append(Issue("WARN", scope, "", "unexpected total_excluded", str(data.get("total_excluded"))))

    uid_map: dict[str, dict[str, Any]] = {}
    by_sha: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for rec in records:
        uid = str(rec.get("uid") or "")
        if not uid:
            issues.append(Issue("FAIL", scope, "", "record without uid"))
            continue
        if uid in uid_map:
            issues.append(Issue("FAIL", scope, uid, "duplicate uid in lock records"))
        uid_map[uid] = rec
        sha = str(rec.get("sha256") or "").lower()
        if sha:
            by_sha[sha].append(rec)

        pdf_raw = rec.get("pdf_path")
        if not pdf_raw:
            issues.append(Issue("FAIL", scope, uid, "missing pdf_path"))
            continue
        pdf = Path(str(pdf_raw))
        if not pdf.is_absolute():
            pdf = root / pdf
        if not pdf.exists():
            issues.append(Issue("FAIL", scope, uid, "pdf_path does not exist", str(pdf_raw)))
            continue
        actual_sha = sha256_file(pdf)
        if sha and actual_sha != sha:
            issues.append(Issue("FAIL", scope, uid, "sha256 mismatch", f"expected={sha}; actual={actual_sha}; file={rel(pdf, root)}"))

        first_text = extract_first_pages(pdf, max_pages=max_pages)
        doi = normalize_doi(rec.get("doi"))
        if doi and first_text:
            doi_variants = {doi, doi.replace("/", ""), doi.replace("/", " ").replace("-", " ")}
            if not any(v and v in first_text.lower() for v in doi_variants):
                issues.append(Issue("WARN", scope, uid, f"DOI not found in first {max_pages} PDF pages", doi))
        elif doi and pymupdf is None:
            issues.append(Issue("WARN", scope, uid, "PyMuPDF unavailable; DOI/title PDF text checks skipped"))

        tokens = title_tokens(str(rec.get("title") or ""))
        first_norm = normalize_title(first_text)
        if tokens and first_norm:
            found = sum(1 for token in tokens if token in first_norm)
            ratio = found / max(len(tokens), 1)
            if ratio < min_title_overlap:
                issues.append(Issue("WARN", scope, uid, "low title/PDF first-pages token overlap", f"{found}/{len(tokens)}={ratio:.2f}"))

    for sha, rows in by_sha.items():
        if len(rows) <= 1:
            continue
        dois = {normalize_doi(r.get("doi")) for r in rows}
        titles = {normalize_title(r.get("title")) for r in rows}
        uids = [str(r.get("uid")) for r in rows]
        if len(dois) > 1 or len(titles) > 1:
            issues.append(Issue("FAIL", scope, ",".join(uids), "same sha256 is mapped to multiple different records", sha))
        else:
            issues.append(Issue("INFO", scope, ",".join(uids), "same sha256 shared by identical metadata", sha))

    for uid in sorted(admin_included):
        if uid not in uid_map:
            issues.append(Issue("FAIL", scope, uid, "admin-included UID missing from lock"))
    for uid in sorted(admin_excluded):
        if uid in uid_map:
            issues.append(Issue("FAIL", scope, uid, "admin-excluded UID still present in lock"))

    return {"scope": scope, "records": len(records)}


def validate_downstream(root: Path, admin_excluded: set[str], issues: list[Issue]) -> None:
    if not admin_excluded:
        return
    p4 = root / "04_Extraction"
    if p4.exists():
        for path in p4.glob("*.tsv"):
            text = path.read_text(encoding="utf-8", errors="ignore")
            for uid in admin_excluded:
                if uid in text:
                    issues.append(Issue("FAIL", "downstream", uid, "admin-excluded UID remains in active P4 TSV", rel(path, root)))
    rob = root / "05_RoB"
    if rob.exists():
        for path in rob.glob("**/*.json"):
            path_text = path.as_posix()
            if "_excluded" in path_text or "quarantine" in path_text.lower():
                continue
            for uid in admin_excluded:
                if uid in path.name:
                    issues.append(Issue("FAIL", "downstream", uid, "admin-excluded UID remains in active RoB JSON", rel(path, root)))


def validate_lock_vs_database(root: Path, locks_data: list[tuple[Path, dict[str, Any]]], issues: list[Issue], admin_excluded: set[str]) -> None:
    """检查当前 screening.db 是否与锁池关键状态冲突。

    锁池可以作为冻结权威，但如果数据库仍保留旧 INCLUDE，后续重新导出锁池时会把旧错误复活。
    因此数据库-锁池不一致至少应 WARN；admin 排除项仍为 INCLUDE 时应 FAIL。
    """
    db_candidates = [root / "03_Screening" / "screening.db", root / "screening.db"]
    db_path = next((p for p in db_candidates if p.exists()), None)
    if db_path is None:
        issues.append(Issue("WARN", "database", "", "screening.db not found; database-lock consistency check skipped"))
        return
    try:
        import sqlite3
        con = sqlite3.connect(str(db_path))
        con.row_factory = sqlite3.Row
        try:
            row = con.execute("SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name='screening'").fetchone()
            if not row or row[0] == 0:
                issues.append(Issue("WARN", "database", "", "screening table missing; database-lock consistency check skipped", rel(db_path, root)))
                return
            db_rows = {r["key"]: dict(r) for r in con.execute("SELECT key, decision, exclusion_code, pdf_path, text_hash FROM screening")}
        finally:
            con.close()
    except Exception as exc:
        issues.append(Issue("WARN", "database", "", "database-lock consistency check failed to run", repr(exc)))
        return

    for lock_path, data in locks_data:
        scope = rel(lock_path, root)
        records = data.get("records") or []
        lock_uids = {str(r.get("uid") or "") for r in records if r.get("uid")}
        for uid in lock_uids:
            db_row = db_rows.get(uid)
            if db_row and db_row.get("decision") != "INCLUDE":
                issues.append(Issue("WARN", "database", uid, "lock-included UID is not INCLUDE in screening.db", f"{scope}; db_decision={db_row.get('decision')}"))
        for uid, db_row in db_rows.items():
            if db_row.get("decision") == "INCLUDE" and uid not in lock_uids:
                severity = "FAIL" if uid in admin_excluded else "WARN"
                issues.append(Issue(severity, "database", uid, "screening.db has INCLUDE not present in lock", f"{scope}; db={rel(db_path, root)}"))


def write_reports(root: Path, report_dir: Path, issues: list[Issue], summaries: list[dict[str, Any]]) -> tuple[Path, Path]:
    report_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    payload = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "project_root": root.as_posix(),
        "summaries": summaries,
        "issue_counts": {
            "FAIL": sum(1 for i in issues if i.severity == "FAIL"),
            "WARN": sum(1 for i in issues if i.severity == "WARN"),
            "INFO": sum(1 for i in issues if i.severity == "INFO"),
        },
        "issues": [asdict(i) for i in issues],
    }
    json_path = report_dir / f"p3_lock_qc_{stamp}.json"
    md_path = report_dir / f"p3_lock_qc_{stamp}.md"
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    lines = [
        "# P3 Lock QC Report",
        "",
        f"- Generated at: `{payload['generated_at']}`",
        f"- Project: `{root.as_posix()}`",
        f"- FAIL: **{payload['issue_counts']['FAIL']}**",
        f"- WARN: **{payload['issue_counts']['WARN']}**",
        f"- INFO: **{payload['issue_counts']['INFO']}**",
        "",
        "## Lock summaries",
    ]
    for item in summaries:
        lines.append(f"- `{item['scope']}`: records={item['records']}")
    lines += ["", "## Issues"]
    if issues:
        lines += ["| Severity | Scope | UID | Message | Evidence |", "|---|---|---|---|---|"]
        for i in issues:
            lines.append(f"| {i.severity} | `{i.scope}` | `{i.uid}` | {i.message.replace('|', '\\|')} | {i.evidence.replace('|', '\\|').replace(chr(10), ' ')} |")
    else:
        lines.append("No issues detected.")
    lines += ["", "## Gate decision", "PASS" if payload["issue_counts"]["FAIL"] == 0 else "FAIL"]
    md_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return json_path, md_path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Validate P3 lock YAML files and PDF mappings.")
    parser.add_argument("--project", default=".", help="Project root directory.")
    parser.add_argument("--lock", action="append", default=[], help="Lock YAML path relative to project root or absolute. Repeatable.")
    parser.add_argument("--expected-included", type=int, default=None)
    parser.add_argument("--expected-excluded", type=int, default=None)
    parser.add_argument("--admin-included", action="append", default=[])
    parser.add_argument("--admin-excluded", action="append", default=[])
    parser.add_argument("--max-pages", type=int, default=3)
    parser.add_argument("--min-title-overlap", type=float, default=0.35)
    parser.add_argument("--report-dir", default="03_Screening/qc/lock_qc")
    args = parser.parse_args(argv)

    root = Path(args.project).resolve()
    locks = [Path(p) for p in args.lock] if args.lock else [root / "03_Screening" / "FINAL_POOL_LOCK.yaml"]
    report_dir = Path(args.report_dir)
    if not report_dir.is_absolute():
        report_dir = root / report_dir

    issues: list[Issue] = []
    summaries: list[dict[str, Any]] = []
    locks_data: list[tuple[Path, dict[str, Any]]] = []
    for lock in locks:
        if not lock.is_absolute():
            lock = root / lock
        data = read_lock(lock, root, issues)
        if data is not None:
            locks_data.append((lock, data))
            summaries.append(validate_lock(
                lock, data, root, issues,
                args.expected_included, args.expected_excluded,
                set(args.admin_included), set(args.admin_excluded),
                args.max_pages, args.min_title_overlap,
            ))
    validate_downstream(root, set(args.admin_excluded), issues)
    validate_lock_vs_database(root, locks_data, issues, set(args.admin_excluded))
    json_path, md_path = write_reports(root, report_dir, issues, summaries)
    fail_count = sum(1 for i in issues if i.severity == "FAIL")
    warn_count = sum(1 for i in issues if i.severity == "WARN")
    print(f"P3_LOCK_QC {'PASS' if fail_count == 0 else 'FAIL'} fail={fail_count} warn={warn_count}")
    print(f"REPORT_JSON {rel(json_path, root)}")
    print(f"REPORT_MD {rel(md_path, root)}")
    return 0 if fail_count == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
