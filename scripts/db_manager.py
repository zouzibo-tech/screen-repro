#!/usr/bin/env python3
"""
db_manager.py — screen-repro v3.0 SQLite数据库管理模块
=====================================================
职责: SQLite数据库的创建、读写、查询。是唯一与数据库交互的模块。

数据方案: SQLite（权威数据源） + MD文件（人类可读）

表结构:
- papers: 文献库
- screening: 筛选记录（含PICOS判定详情）
- progress: 进度追踪（单行表）
- qa_reviews: QA复核记录
- migration_log: 迁移日志
"""

import sqlite3
import json
import csv
from pathlib import Path
from datetime import datetime


class DbManager:
    """SQLite数据库管理器"""

    def __init__(self, db_path: Path):
        self.db_path = db_path
        self.conn = None

    def connect(self):
        """建立连接"""
        self.conn = sqlite3.connect(str(self.db_path))
        self.conn.row_factory = sqlite3.Row  # 返回dict而非tuple
        self.conn.execute("PRAGMA journal_mode=WAL")  # 写入性能优化
        self.conn.execute("PRAGMA foreign_keys=ON")

    def close(self):
        """关闭连接"""
        if self.conn:
            self.conn.close()
            self.conn = None

    def __enter__(self):
        self.connect()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()

    # ====== 初始化 ======

    def init_db(self):
        """创建数据库表（如果不存在）"""
        self.conn.executescript("""
            CREATE TABLE IF NOT EXISTS papers (
                key TEXT PRIMARY KEY,
                author TEXT,
                year INTEGER,
                title TEXT,
                doi TEXT,
                pdf_path TEXT,
                created_at TEXT DEFAULT (datetime('now'))
            );

            CREATE TABLE IF NOT EXISTS screening (
                key TEXT PRIMARY KEY REFERENCES papers(key),
                decision TEXT NOT NULL,
                exclusion_code TEXT,
                p_result TEXT,
                p_evidence TEXT,
                p_analysis TEXT,
                i_result TEXT,
                i_device_type TEXT,
                i_evidence TEXT,
                i_analysis TEXT,
                c_result TEXT,
                c_evidence TEXT,
                c_analysis TEXT,
                o_result TEXT,
                o_outcome_type TEXT,
                o_retention_weeks INTEGER,
                o_evidence TEXT,
                o_analysis TEXT,
                s_result TEXT,
                s_design_type TEXT,
                s_evidence TEXT,
                s_analysis TEXT,
                reason TEXT,
                text_quality TEXT,
                pdf_path TEXT,
                mining_path TEXT,
                md_path TEXT,
                screened_at TEXT,
                model TEXT,
                text_hash TEXT,
                -- 可复现性字段
                fingerprint TEXT,
                model_version TEXT,
                prompt_hash TEXT,
                extraction_method TEXT,
                temperature REAL DEFAULT 0,
                seed INTEGER DEFAULT 42,
                llm_response_raw TEXT
            );

            CREATE TABLE IF NOT EXISTS progress (
                id INTEGER PRIMARY KEY CHECK (id = 1),
                total INTEGER DEFAULT 0,
                processed INTEGER DEFAULT 0,
                remaining INTEGER DEFAULT 0,
                current_key TEXT,
                status TEXT DEFAULT 'idle',
                include_count INTEGER DEFAULT 0,
                exclude_count INTEGER DEFAULT 0,
                maybe_count INTEGER DEFAULT 0,
                skipped_count INTEGER DEFAULT 0,
                error_count INTEGER DEFAULT 0,
                started_at TEXT,
                last_updated TEXT
            );

            CREATE TABLE IF NOT EXISTS qa_reviews (
                key TEXT REFERENCES screening(key),
                action TEXT NOT NULL,
                old_decision TEXT,
                new_decision TEXT,
                reason TEXT,
                reviewed_at TEXT DEFAULT (datetime('now')),
                PRIMARY KEY (key, action)
            );

            CREATE TABLE IF NOT EXISTS migration_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                old_key TEXT,
                new_key TEXT,
                migrated_at TEXT DEFAULT (datetime('now'))
            );

            CREATE INDEX IF NOT EXISTS idx_screening_decision ON screening(decision);
            CREATE INDEX IF NOT EXISTS idx_papers_year ON papers(year);
            CREATE INDEX IF NOT EXISTS idx_screening_fingerprint ON screening(fingerprint);

            -- 初始化progress行（如果不存在）
            INSERT OR IGNORE INTO progress (id, total, processed, remaining, status)
            VALUES (1, 0, 0, 0, 'idle');
        """)
        self.conn.commit()

    # ====== 文献库操作 ======

    def add_paper(self, key: str, author: str, year: int, title: str,
                  doi: str = None, pdf_path: str = None):
        """添加文献到papers表"""
        self.conn.execute(
            "INSERT OR REPLACE INTO papers (key, author, year, title, doi, pdf_path) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (key, author, year, title, doi, pdf_path)
        )
        self.conn.commit()

    def get_paper(self, key: str) -> dict | None:
        """获取文献信息"""
        row = self.conn.execute(
            "SELECT * FROM papers WHERE key = ?", (key,)
        ).fetchone()
        return dict(row) if row else None

    def get_all_papers(self) -> list[dict]:
        """获取所有文献"""
        rows = self.conn.execute("SELECT * FROM papers ORDER BY key").fetchall()
        return [dict(r) for r in rows]

    # ====== 筛选操作 ======

    def save_screening(self, data: dict):
        """
        保存筛选结果

        参数:
            data: 包含以下字段的dict
                - key, decision, exclusion_code
                - picos: {P: {result, evidence, analysis}, I: {...}, C: {...}, O: {...}, S: {...}}
                - reason, text_quality
                - pdf_path, mining_path, md_path
                - model, text_hash
                - fingerprint, model_version, prompt_hash, extraction_method
                - temperature, seed, llm_response_raw
        """
        picos = data.get("picos", {})
        p = picos.get("P", {})
        i = picos.get("I", {})
        c = picos.get("C", {})
        o = picos.get("O", {})
        s = picos.get("S", {})

        self.conn.execute("""
            INSERT OR REPLACE INTO screening (
                key, decision, exclusion_code,
                p_result, p_evidence, p_analysis,
                i_result, i_device_type, i_evidence, i_analysis,
                c_result, c_evidence, c_analysis,
                o_result, o_outcome_type, o_retention_weeks, o_evidence, o_analysis,
                s_result, s_design_type, s_evidence, s_analysis,
                reason, text_quality,
                pdf_path, mining_path, md_path,
                screened_at, model, text_hash,
                fingerprint, model_version, prompt_hash, extraction_method,
                temperature, seed, llm_response_raw
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            data.get("key"),
            data.get("decision"),
            data.get("exclusion_code"),
            p.get("result"),
            json.dumps(p.get("evidence", []), ensure_ascii=False),
            p.get("analysis"),
            i.get("result"),
            i.get("device_type"),
            json.dumps(i.get("evidence", []), ensure_ascii=False),
            i.get("analysis"),
            c.get("result"),
            json.dumps(c.get("evidence", []), ensure_ascii=False),
            c.get("analysis"),
            o.get("result"),
            o.get("outcome_type"),
            o.get("retention_weeks"),
            json.dumps(o.get("evidence", []), ensure_ascii=False),
            o.get("analysis"),
            s.get("result"),
            s.get("design_type"),
            json.dumps(s.get("evidence", []), ensure_ascii=False),
            s.get("analysis"),
            data.get("reason"),
            data.get("text_quality"),
            data.get("pdf_path"),
            data.get("mining_path"),
            data.get("md_path"),
            datetime.now().isoformat(timespec="seconds"),
            data.get("model"),
            data.get("text_hash"),
            data.get("fingerprint"),
            data.get("model_version"),
            data.get("prompt_hash"),
            data.get("extraction_method"),
            data.get("temperature", 0),
            data.get("seed", 42),
            data.get("llm_response_raw"),
        ))
        self.conn.commit()

    def get_screening(self, key: str) -> dict | None:
        """获取筛选结果"""
        row = self.conn.execute(
            "SELECT * FROM screening WHERE key = ?", (key,)
        ).fetchone()
        if not row:
            return None

        result = dict(row)
        # 解析JSON字段
        for dim in ["p", "i", "c", "o", "s"]:
            evidence_key = f"{dim}_evidence"
            if result.get(evidence_key):
                try:
                    result[evidence_key] = json.loads(result[evidence_key])
                except json.JSONDecodeError:
                    result[evidence_key] = []
        return result

    def is_screened(self, key: str) -> bool:
        """检查文献是否已筛选"""
        row = self.conn.execute(
            "SELECT 1 FROM screening WHERE key = ?", (key,)
        ).fetchone()
        return row is not None

    def get_screening_by_fingerprint(self, fingerprint: str) -> dict | None:
        """通过指纹获取筛选结果（用于缓存检查）"""
        row = self.conn.execute(
            "SELECT * FROM screening WHERE fingerprint = ?", (fingerprint,)
        ).fetchone()
        return dict(row) if row else None

    # ====== 进度操作 ======

    def update_progress(self, key: str, decision: str):
        """
        更新进度（原子事务）

        参数:
            key: 文献key
            decision: 决策（INCLUDE/EXCLUDE/MAYBE/SKIPPED/ERROR）
        """
        col_map = {
            "INCLUDE": "include_count",
            "EXCLUDE": "exclude_count",
            "MAYBE": "maybe_count",
            "SKIPPED": "skipped_count",
            "ERROR": "error_count",
        }
        col = col_map.get(decision, "error_count")

        with self.conn:
            # 更新计数
            self.conn.execute(
                f"UPDATE progress SET {col} = {col} + 1 WHERE id = 1"
            )
            # 更新总数和状态
            self.conn.execute("""
                UPDATE progress SET
                    processed = include_count + exclude_count + maybe_count + skipped_count + error_count,
                    remaining = MAX(0, total - processed),
                    current_key = ?,
                    status = 'running',
                    last_updated = datetime('now')
                WHERE id = 1
            """, (key,))

    def get_progress(self) -> dict:
        """获取进度"""
        row = self.conn.execute(
            "SELECT * FROM progress WHERE id = 1"
        ).fetchone()
        return dict(row) if row else {}

    def set_total(self, total: int):
        """设置文献总数"""
        self.conn.execute(
            "UPDATE progress SET total = ?, remaining = MAX(0, ? - processed) WHERE id = 1",
            (total, total)
        )
        self.conn.commit()

    def reset_progress(self):
        """重置进度（慎用）"""
        self.conn.execute("""
            UPDATE progress SET
                total = 0, processed = 0, remaining = 0,
                current_key = NULL, status = 'idle',
                include_count = 0, exclude_count = 0, maybe_count = 0,
                skipped_count = 0, error_count = 0,
                started_at = NULL, last_updated = NULL
            WHERE id = 1
        """)
        self.conn.commit()

    # ====== 查询操作 ======

    def get_next_unscreened(self) -> dict | None:
        """获取下一篇待筛选文献（从papers表中找未在screening表中的）"""
        row = self.conn.execute("""
            SELECT p.* FROM papers p
            LEFT JOIN screening s ON p.key = s.key
            WHERE s.key IS NULL
            ORDER BY p.key
            LIMIT 1
        """).fetchone()
        return dict(row) if row else None

    def get_by_decision(self, decision: str) -> list[dict]:
        """按决策获取文献列表"""
        rows = self.conn.execute(
            "SELECT * FROM screening WHERE decision = ? ORDER BY key",
            (decision,)
        ).fetchall()
        return [dict(r) for r in rows]

    def get_summary(self) -> dict:
        """获取汇总统计"""
        row = self.conn.execute("""
            SELECT
                total, processed, remaining,
                include_count, exclude_count, maybe_count,
                skipped_count, error_count,
                status, current_key, started_at, last_updated
            FROM progress WHERE id = 1
        """).fetchone()
        return dict(row) if row else {}

    def count_by_decision(self) -> dict[str, int]:
        """按决策统计数量"""
        rows = self.conn.execute("""
            SELECT decision, COUNT(*) as cnt
            FROM screening
            GROUP BY decision
        """).fetchall()
        return {row["decision"]: row["cnt"] for row in rows}

    # ====== 导出操作 ======

    def export_csv(self, csv_path: Path):
        """
        导出screening_summary.csv

        参数:
            csv_path: CSV文件路径
        """
        rows = self.conn.execute("""
            SELECT
                s.key,
                p.author,
                p.year,
                p.title,
                p.doi,
                s.decision,
                s.exclusion_code,
                s.reason,
                s.screened_at,
                s.p_result,
                s.i_result,
                s.i_device_type,
                s.c_result,
                s.o_result,
                s.o_outcome_type,
                s.s_result,
                s.s_design_type,
                s.md_path
            FROM screening s
            JOIN papers p ON s.key = p.key
            ORDER BY s.key
        """).fetchall()

        with open(csv_path, "w", encoding="utf-8-sig", newline="") as f:
            writer = csv.writer(f)
            writer.writerow([
                "Key", "Author", "Year", "Title", "DOI",
                "最终决策", "排除码", "判定理由", "筛选日期",
                "P判定", "I判定", "设备类型", "C判定",
                "O判定", "结局类型", "S判定", "设计类型", "MD文件"
            ])
            for row in rows:
                writer.writerow(row)

    # ====== 迁移操作 ======

    def import_from_v2(self, csv_path: Path, progress_path: Path,
                       records_dir: Path):
        """
        从v2.3导入数据

        参数:
            csv_path: screening_summary.csv路径
            progress_path: screening_progress.json路径
            records_dir: screening_records目录路径
        """
        # 导入papers
        if csv_path.exists():
            with open(csv_path, encoding="utf-8-sig") as f:
                reader = csv.DictReader(f)
                for row in reader:
                    key = row.get("Key", "").strip()
                    if key:
                        # 提取年份（处理非数字格式如"2024（在线发表...）"）
                        year_str = row.get("Year", "0")
                        import re
                        year_match = re.search(r'(\d{4})', str(year_str))
                        year = int(year_match.group(1)) if year_match else 0

                        self.add_paper(
                            key=key,
                            author=row.get("Author", ""),
                            year=year,
                            title=row.get("Title", ""),
                            doi=row.get("DOI"),
                        )

        # 导入progress
        if progress_path.exists():
            with open(progress_path, encoding="utf-8") as f:
                prog = json.load(f)
            self.conn.execute("""
                UPDATE progress SET
                    total = ?,
                    processed = ?,
                    remaining = ?,
                    include_count = ?,
                    exclude_count = ?,
                    maybe_count = ?,
                    skipped_count = ?,
                    error_count = ?,
                    status = 'running',
                    started_at = ?,
                    last_updated = ?
                WHERE id = 1
            """, (
                prog.get("total", 0),
                prog.get("processed", 0),
                prog.get("remaining", 0),
                prog.get("results", {}).get("INCLUDE", 0),
                prog.get("results", {}).get("EXCLUDE", 0),
                prog.get("results", {}).get("MAYBE", 0),
                prog.get("results", {}).get("SKIPPED", 0),
                prog.get("results", {}).get("ERROR", 0),
                prog.get("started_at"),
                prog.get("last_updated"),
            ))
            self.conn.commit()

        # 导入screening记录（从MD文件名推断）
        for decision in ["INCLUDE", "EXCLUDE", "MAYBE"]:
            d = records_dir / decision
            if not d.exists():
                continue
            for md_file in d.glob("*.md"):
                key = md_file.stem
                if not self.is_screened(key):
                    # 确保paper存在（外键约束）
                    if not self.get_paper(key):
                        # 从key推断author和year
                        parts = key.split("_")
                        author = parts[0] if len(parts) > 0 else "Unknown"
                        year_str = parts[1] if len(parts) > 1 else "0"
                        import re
                        year_match = re.search(r'(\d{4})', year_str)
                        year = int(year_match.group(1)) if year_match else 0
                        self.add_paper(key=key, author=author, year=year, title="")

                    self.conn.execute("""
                        INSERT OR IGNORE INTO screening
                        (key, decision, md_path, screened_at)
                        VALUES (?, ?, ?, datetime('now'))
                    """, (key, decision, str(md_file.relative_to(records_dir.parent.parent))))
            self.conn.commit()

    def log_migration(self, old_key: str, new_key: str):
        """记录迁移日志"""
        self.conn.execute(
            "INSERT INTO migration_log (old_key, new_key) VALUES (?, ?)",
            (old_key, new_key)
        )
        self.conn.commit()

    # ====== QA操作 ======

    def save_qa_review(self, key: str, action: str, old_decision: str = None,
                       new_decision: str = None, reason: str = None):
        """保存QA复核记录"""
        self.conn.execute("""
            INSERT OR REPLACE INTO qa_reviews
            (key, action, old_decision, new_decision, reason, reviewed_at)
            VALUES (?, ?, ?, ?, ?, datetime('now'))
        """, (key, action, old_decision, new_decision, reason))
        self.conn.commit()

    def get_qa_reviews(self, action: str = None) -> list[dict]:
        """获取QA复核记录"""
        if action:
            rows = self.conn.execute(
                "SELECT * FROM qa_reviews WHERE action = ? ORDER BY reviewed_at",
                (action,)
            ).fetchall()
        else:
            rows = self.conn.execute(
                "SELECT * FROM qa_reviews ORDER BY reviewed_at"
            ).fetchall()
        return [dict(r) for r in rows]

    def update_decision(self, key: str, new_decision: str,
                        exclusion_code: str = None):
        """更新筛选决策（用于QA复核）"""
        self.conn.execute("""
            UPDATE screening SET
                decision = ?,
                exclusion_code = ?,
                screened_at = datetime('now')
            WHERE key = ?
        """, (new_decision, exclusion_code, key))
        self.conn.commit()

        # 更新progress计数
        with self.conn:
            # 获取旧决策
            old = self.conn.execute(
                "SELECT decision FROM screening WHERE key = ?", (key,)
            ).fetchone()
            if old:
                old_dec = old["decision"]
                old_col = {
                    "INCLUDE": "include_count",
                    "EXCLUDE": "exclude_count",
                    "MAYBE": "maybe_count",
                }.get(old_dec)
                new_col = {
                    "INCLUDE": "include_count",
                    "EXCLUDE": "exclude_count",
                    "MAYBE": "maybe_count",
                }.get(new_decision)

                if old_col and new_col and old_col != new_col:
                    self.conn.execute(
                        f"UPDATE progress SET {old_col} = MAX(0, {old_col} - 1) WHERE id = 1"
                    )
                    self.conn.execute(
                        f"UPDATE progress SET {new_col} = {new_col} + 1 WHERE id = 1"
                    )

    # ====== 验证操作 ======

    def verify_consistency(self) -> list[str]:
        """
        验证数据一致性

        返回: 问题列表（空列表=一致）
        """
        issues = []
        progress = self.get_progress()

        # 1. 检查screening表计数与progress表一致
        counts = self.count_by_decision()
        if counts.get("INCLUDE", 0) != progress.get("include_count", 0):
            issues.append(
                f"INCLUDE计数不一致: DB={counts.get('INCLUDE',0)} "
                f"progress={progress.get('include_count',0)}"
            )
        if counts.get("EXCLUDE", 0) != progress.get("exclude_count", 0):
            issues.append(
                f"EXCLUDE计数不一致: DB={counts.get('EXCLUDE',0)} "
                f"progress={progress.get('exclude_count',0)}"
            )
        if counts.get("MAYBE", 0) != progress.get("maybe_count", 0):
            issues.append(
                f"MAYBE计数不一致: DB={counts.get('MAYBE',0)} "
                f"progress={progress.get('maybe_count',0)}"
            )

        # 2. 检查processed总数
        total_counted = sum(counts.values())
        if total_counted != progress.get("processed", 0):
            issues.append(
                f"总数不一致: DB={total_counted} "
                f"progress={progress.get('processed',0)}"
            )

        # 3. 检查MD文件存在性
        rows = self.conn.execute(
            "SELECT key, md_path FROM screening WHERE md_path IS NOT NULL"
        ).fetchall()
        for row in rows:
            md_path = Path(row["md_path"])
            if not md_path.exists():
                issues.append(f"MD文件缺失: {row['key']} -> {row['md_path']}")

        return issues


# ====== CLI ======

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="screen-repro v3.0 数据库管理")
    parser.add_argument("command", choices=["init", "check", "verify", "export"],
                        help="命令")
    parser.add_argument("--db", default="screening.db", help="数据库路径")
    parser.add_argument("--output", help="导出路径")

    args = parser.parse_args()
    db_path = Path(args.db)

    with DbManager(db_path) as db:
        if args.command == "init":
            db.init_db()
            print(f"✅ 数据库已初始化: {db_path}")

        elif args.command == "check":
            summary = db.get_summary()
            print(f"{'='*50}")
            print(f"📊 筛选进度")
            print(f"{'='*50}")
            print(f"  总数: {summary.get('total', 0)}")
            print(f"  已处理: {summary.get('processed', 0)}")
            print(f"  剩余: {summary.get('remaining', 0)}")
            print(f"  INCLUDE: {summary.get('include_count', 0)}")
            print(f"  EXCLUDE: {summary.get('exclude_count', 0)}")
            print(f"  MAYBE: {summary.get('maybe_count', 0)}")
            print(f"  SKIPPED: {summary.get('skipped_count', 0)}")
            print(f"  ERROR: {summary.get('error_count', 0)}")
            print(f"  状态: {summary.get('status', 'unknown')}")
            print(f"{'='*50}")

        elif args.command == "verify":
            issues = db.verify_consistency()
            if issues:
                print(f"❌ 发现 {len(issues)} 个问题:")
                for issue in issues:
                    print(f"  - {issue}")
            else:
                print("✅ 数据一致性验证通过")

        elif args.command == "export":
            output = Path(args.output) if args.output else Path("screening_summary.csv")
            db.export_csv(output)
            print(f"✅ 已导出: {output}")
