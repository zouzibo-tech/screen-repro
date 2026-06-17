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

        # 添加screening_round列（如果不存在）
        try:
            self.conn.execute("ALTER TABLE screening ADD COLUMN screening_round TEXT")
        except sqlite3.OperationalError:
            pass  # 列已存在

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
                temperature, seed, llm_response_raw, screening_round
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
            data.get("screening_round", "正式筛选"),
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
        同步进度缓存。

        注意：progress 表不是权威数据源，只能由 papers/screening 实时重建。
        这样可避免重复运行、覆盖写入、跳过记录等场景造成 processed > total。
        """
        self.rebuild_progress(current_key=key, status="running")

    def _stored_progress(self) -> dict:
        """读取 progress 原始缓存行，不做修正。"""
        row = self.conn.execute(
            "SELECT * FROM progress WHERE id = 1"
        ).fetchone()
        return dict(row) if row else {}

    def rebuild_progress(self, current_key: str = None,
                         status: str = None) -> dict:
        """从 papers/screening 权威表重建 progress 缓存并返回真实进度。"""
        counts = self.count_by_decision()
        total = self.conn.execute(
            "SELECT COUNT(*) FROM papers"
        ).fetchone()[0]
        processed = self.conn.execute(
            "SELECT COUNT(*) FROM screening"
        ).fetchone()[0]
        remaining = self.conn.execute("""
            SELECT COUNT(*)
            FROM papers p
            LEFT JOIN screening s ON p.key = s.key
            WHERE s.key IS NULL
        """).fetchone()[0]

        stored = self._stored_progress()
        effective_status = status
        if effective_status is None:
            if total == 0:
                effective_status = "idle"
            elif remaining == 0:
                effective_status = "done"
            else:
                effective_status = stored.get("status") or "running"
                if effective_status == "done":
                    effective_status = "running"

        effective_current = current_key if current_key is not None else stored.get("current_key")

        self.conn.execute("""
            UPDATE progress SET
                total = ?,
                processed = ?,
                remaining = ?,
                current_key = ?,
                status = ?,
                include_count = ?,
                exclude_count = ?,
                maybe_count = ?,
                skipped_count = ?,
                error_count = ?,
                started_at = COALESCE(started_at, datetime('now')),
                last_updated = datetime('now')
            WHERE id = 1
        """, (
            total,
            processed,
            remaining,
            effective_current,
            effective_status,
            counts.get("INCLUDE", 0),
            counts.get("EXCLUDE", 0),
            counts.get("MAYBE", 0),
            counts.get("SKIPPED", 0),
            counts.get("ERROR", 0),
        ))
        self.conn.commit()
        return {
            "id": 1,
            "total": total,
            "processed": processed,
            "remaining": remaining,
            "current_key": effective_current,
            "status": effective_status,
            "include_count": counts.get("INCLUDE", 0),
            "exclude_count": counts.get("EXCLUDE", 0),
            "maybe_count": counts.get("MAYBE", 0),
            "skipped_count": counts.get("SKIPPED", 0),
            "error_count": counts.get("ERROR", 0),
            "started_at": stored.get("started_at"),
            "last_updated": datetime.now().isoformat(timespec="seconds"),
        }

    def get_progress(self) -> dict:
        """获取真实进度，并自动修复 progress 缓存。"""
        return self.rebuild_progress()

    def set_total(self, total: int):
        """兼容旧接口：total 不再手工设置，而是从 papers 表重建。"""
        self.rebuild_progress()

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

    def get_next_unscreened(self, skip_invalid: bool = True) -> dict | None:
        """
        获取下一篇待筛选文献（从papers表中找未在screening表中的）
        
        参数:
            skip_invalid: 是否跳过明显无效的记录（author为空且year为0）
        """
        if skip_invalid:
            # 过滤掉明显无效的记录（author为空且year为0，可能是迁移产生的脏数据）
            row = self.conn.execute("""
                SELECT p.* FROM papers p
                LEFT JOIN screening s ON p.key = s.key
                WHERE s.key IS NULL
                    AND NOT (p.author = '' AND p.year = 0 AND p.title = '')
                ORDER BY p.key
                LIMIT 1
            """).fetchone()
        else:
            row = self.conn.execute("""
                SELECT p.* FROM papers p
                LEFT JOIN screening s ON p.key = s.key
                WHERE s.key IS NULL
                ORDER BY p.key
                LIMIT 1
            """).fetchone()
        return dict(row) if row else None
    
    def get_invalid_papers(self) -> list[dict]:
        """获取明显无效的文献记录（author为空且year为0）"""
        rows = self.conn.execute("""
            SELECT p.* FROM papers p
            LEFT JOIN screening s ON p.key = s.key
            WHERE s.key IS NULL
                AND p.author = '' AND p.year = 0 AND p.title = ''
            ORDER BY p.key
        """).fetchall()
        return [dict(r) for r in rows]
    
    def mark_invalid_papers_as_skipped(self, reason: str = "无效记录-无元数据") -> int:
        """将明显无效的文献记录标记为SKIPPED"""
        # 获取无效记录
        invalid_papers = self.get_invalid_papers()
        count = 0
        
        for paper in invalid_papers:
            try:
                self.conn.execute("""
                    INSERT OR IGNORE INTO screening
                    (key, decision, reason, screened_at)
                    VALUES (?, 'SKIPPED', ?, datetime('now'))
                """, (paper["key"], reason))
                count += 1
            except Exception as e:
                # 如果插入失败，记录错误但继续处理
                print(f"警告: 无法标记 {paper['key']} 为 SKIPPED: {e}")
        
        self.conn.commit()
        return count

    def get_by_decision(self, decision: str) -> list[dict]:
        """按决策获取文献列表"""
        rows = self.conn.execute(
            "SELECT * FROM screening WHERE decision = ? ORDER BY key",
            (decision,)
        ).fetchall()
        return [dict(r) for r in rows]

    def get_exclusion_distribution(self) -> list[dict]:
        """按排除码分组统计 EXCLUDE 文献数量"""
        rows = self.conn.execute("""
            SELECT COALESCE(exclusion_code, '未分类') as code,
                   COUNT(*) as cnt,
                   GROUP_CONCAT(DISTINCT reason) as reasons
            FROM screening
            WHERE decision = 'EXCLUDE'
            GROUP BY exclusion_code
            ORDER BY cnt DESC
        """).fetchall()
        return [dict(r) for r in rows]

    def get_picos_dimension_stats(self) -> dict:
        """统计 INCLUDE 文献各 PICOS 维度 PASS/FAIL/UNCERTAIN 数量"""
        stats = {}
        for dim in ["p", "i", "c", "o", "s"]:
            col = f"{dim}_result"
            rows = self.conn.execute(f"""
                SELECT
                    {col} as result,
                    COUNT(*) as cnt
                FROM screening
                WHERE decision = 'INCLUDE'
                GROUP BY {col}
            """).fetchall()
            stats[dim.upper()] = {row["result"]: row["cnt"] for row in rows}
        return stats

    def get_include_without_pdf(self) -> list[dict]:
        """获取 INCLUDE 但无 PDF 路径的文献"""
        rows = self.conn.execute("""
            SELECT s.key, p.author, p.year, p.title
            FROM screening s
            JOIN papers p ON s.key = p.key
            WHERE s.decision = 'INCLUDE'
              AND (s.pdf_path IS NULL OR s.pdf_path = '')
        """).fetchall()
        return [dict(r) for r in rows]

    def get_screening_time_range(self) -> dict:
        """获取筛选记录的时间范围"""
        row = self.conn.execute("""
            SELECT
                MIN(screened_at) as first_screened,
                MAX(screened_at) as last_screened,
                COUNT(DISTINCT date(screened_at)) as screening_days
            FROM screening
            WHERE screened_at IS NOT NULL
        """).fetchone()
        return dict(row) if row else {}

    def get_summary(self) -> dict:
        """获取汇总统计（实时重建，避免读取陈旧 progress 缓存）"""
        return self.rebuild_progress()

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
        """更新筛选决策（用于QA复核），并从权威表重建进度缓存。"""
        self.conn.execute("""
            UPDATE screening SET
                decision = ?,
                exclusion_code = ?,
                screened_at = datetime('now')
            WHERE key = ?
        """, (new_decision, exclusion_code, key))
        self.conn.commit()
        self.rebuild_progress(current_key=key)

    # ====== 验证操作 ======

    def verify_consistency(self) -> list[str]:
        """
        验证数据一致性

        返回: 问题列表（空列表=一致）
        """
        issues = []
        stored_before = self._stored_progress()
        progress = self.rebuild_progress()

        # 1. 检查重建前 progress 缓存是否曾经失真；verify 会自动修复缓存
        cache_fields = [
            "total", "processed", "remaining", "include_count", "exclude_count",
            "maybe_count", "skipped_count", "error_count"
        ]
        for field in cache_fields:
            if stored_before.get(field, 0) != progress.get(field, 0):
                issues.append(
                    f"progress缓存已自动修复: {field} "
                    f"{stored_before.get(field, 0)} -> {progress.get(field, 0)}"
                )

        # 2. 检查screening表计数与重建后的progress一致
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
        if counts.get("SKIPPED", 0) != progress.get("skipped_count", 0):
            issues.append(
                f"SKIPPED计数不一致: DB={counts.get('SKIPPED',0)} "
                f"progress={progress.get('skipped_count',0)}"
            )
        if counts.get("ERROR", 0) != progress.get("error_count", 0):
            issues.append(
                f"ERROR计数不一致: DB={counts.get('ERROR',0)} "
                f"progress={progress.get('error_count',0)}"
            )

        # 3. 检查processed与remaining总数
        total_counted = sum(counts.values())
        if total_counted != progress.get("processed", 0):
            issues.append(
                f"总数不一致: DB={total_counted} "
                f"progress={progress.get('processed',0)}"
            )
        if progress.get("processed", 0) + progress.get("remaining", 0) != progress.get("total", 0):
            issues.append(
                f"进度守恒不一致: processed+remaining="
                f"{progress.get('processed',0) + progress.get('remaining',0)} "
                f"total={progress.get('total',0)}"
            )

        # 4. 检查MD文件存在性
        rows = self.conn.execute(
            "SELECT key, md_path FROM screening WHERE md_path IS NOT NULL"
        ).fetchall()
        for row in rows:
            md_path = Path(row["md_path"])
            if not md_path.is_absolute():
                md_path = self.db_path.parent / md_path
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
            print(f"[OK] 数据库已初始化: {db_path}")

        elif args.command == "check":
            summary = db.get_summary()
            print(f"{'='*50}")
            print(f"筛选进度")
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
                print(f"[ERROR] 发现 {len(issues)} 个问题:")
                for issue in issues:
                    print(f"  - {issue}")
            else:
                print("[OK] 数据一致性验证通过")

        elif args.command == "export":
            output = Path(args.output) if args.output else Path("screening_summary.csv")
            db.export_csv(output)
            print(f"[OK] 已导出: {output}")
