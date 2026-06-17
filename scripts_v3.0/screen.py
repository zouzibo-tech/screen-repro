#!/usr/bin/env python3
"""
screen.py — screen-repro v3.2 主编排器
======================================
程序化优先：流程控制、校验、备份、报告全部程序化；AI仅保留给全文PICOS判定。
数据存储：SQLite（权威） + MD文件（人类可读）

三层架构:
    - Orchestration Layer: 状态机、前置校验、备份、恢复、队列、批次、重试
    - Extraction Layer: RIS导入、PDF匹配、PDF文本抽取、质量指标、缓存
    - Judgment Layer: AI判定（仅负责PICOS全文判定，子进程隔离）

特性:
    - 程序化优先：流程控制、状态管理、数据校验全部由程序完成
    - 断点续跑：被中断后重新运行自动从上次位置继续
    - 信号处理：捕获 SIGINT/SIGTERM，当前文献处理完毕后安全退出
    - --base 参数：指定项目目录，不再依赖 cwd
    - 进度权威源：progress 表从 papers/screening 实时重建，永不失真
    - 防御性校验：每个关键步骤都有前置校验，空库/错库/缺文件都会被拒绝
    - 原子操作：文件写入先写临时文件，成功后再原子替换

CLI命令:
    python screen.py init              # 初始化项目
    python screen.py import --ris xxx.ris  # 导入RIS文件
    python screen.py run               # 执行筛选循环（自动续跑，含规则预筛+AI判定）
    python screen.py run --batch 10    # 筛选N篇后暂停
    python screen.py run --base D:\\project  # 指定项目目录
    python screen.py check             # 查看进度
    python screen.py verify            # 验证一致性
    python screen.py summary           # 汇总报告
    python screen.py report            # 生成完整筛选报告
    python screen.py export            # 导出CSV
    python screen.py migrate           # 从v2.3迁移
    python screen.py pdf map           # PDF映射（含数据库前置校验+备份）
    python screen.py prescreen         # 遗留模式：预筛选+AI复核+人机协同
    python screen.py workflow --ris xxx.ris  # 一键执行完整流程
"""

import argparse
import json
import sys
import os
import io
import logging
import subprocess
import tempfile
import re
import signal
import hashlib
import csv
import sqlite3
import shutil
import time
from pathlib import Path
from datetime import datetime

# ====== 常量 ======

SKILL_DIR = Path(__file__).parent
CONFIG_FILE = "config.json"
DB_FILE = "screening.db"


# ====== 日志配置 ======

def setup_logging(base: Path) -> logging.Logger:
    """配置日志系统"""
    log_file = base / "screening.log"
    logger = logging.getLogger("screen-repro")
    logger.setLevel(logging.INFO)

    # 文件处理器
    fh = logging.FileHandler(str(log_file), encoding="utf-8")
    fh.setLevel(logging.INFO)

    # 控制台处理器
    ch = logging.StreamHandler()
    ch.setLevel(logging.INFO)

    # 格式
    formatter = logging.Formatter(
        "%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S"
    )
    fh.setFormatter(formatter)
    ch.setFormatter(formatter)

    logger.addHandler(fh)
    logger.addHandler(ch)

    return logger


# ====== 辅助函数 ======

def load_config(base: Path) -> dict:
    """加载配置文件"""
    config_path = base / CONFIG_FILE
    if not config_path.exists():
        return {}
    with open(config_path, encoding="utf-8") as f:
        return json.load(f)


def make_key(author: str, year: str, title: str) -> str:
    """
    生成唯一文献标识

    格式: Author_Year_TitleHash6
    示例: Chen_2026_a3b2c1
    """
    title_clean = title.strip()[:30].lower()
    h = hashlib.md5(title_clean.encode()).hexdigest()[:6]
    return f"{author}_{year}_{h}"


class GracefulShutdown:
    """优雅退出处理器 — 捕获 SIGINT/SIGTERM，允许当前文献处理完毕后再退出"""
    def __init__(self):
        self.should_exit = False
        self._logger = None
        signal.signal(signal.SIGINT, self._handler)
        if hasattr(signal, 'SIGTERM'):
            signal.signal(signal.SIGTERM, self._handler)

    def set_logger(self, logger):
        self._logger = logger

    def _handler(self, signum, frame):
        self.should_exit = True
        if self._logger:
            self._logger.warning(
                f"收到终止信号 ({signal.Signals(signum).name})，"
                f"将在当前文献处理完毕后安全退出..."
            )

    def check(self) -> bool:
        """返回 True 表示应该退出"""
        return self.should_exit


_shutdown = GracefulShutdown()


# ====== 主编排器 ======

class ScreeningOrchestrator:
    """筛选编排器 — Python控制一切，SQLite存储"""

    def __init__(self, project_dir: Path, init_db: bool = True,
                 require_existing_db: bool = False):
        self.base = project_dir
        self.config = load_config(project_dir)
        self.logger = setup_logging(project_dir)

        db_path = project_dir / DB_FILE
        if require_existing_db and not db_path.exists():
            raise RuntimeError(f"数据库不存在: {db_path}")

        # 导入DbManager
        sys.path.insert(0, str(SKILL_DIR))
        from db_manager import DbManager

        self.db = DbManager(db_path)
        self.db.connect()
        if init_db:
            self.db.init_db()

    def __del__(self):
        if hasattr(self, 'db') and self.db:
            self.db.close()

    # ====== 核心循环 ======

    def run(self, batch_size=None):
        """主筛选循环 — 支持信号中断、断点续跑、批次控制"""
        self._init_check()
        _shutdown.set_logger(self.logger)

        # 检测中断恢复
        progress = self.db.rebuild_progress()
        if progress.get('status') == 'running' and progress.get('processed', 0) > 0:
            self.logger.info(
                f"检测到上次中断，自动续跑 | "
                f"已完成:{progress['processed']}/{progress['total']} | "
                f"剩余:{progress['remaining']}"
            )

        # 强制PDF匹配检查
        if not self._pdf_mapping_ready():
            self.logger.info("正在匹配PDF文件与文献库...")
            self._map_pdfs()

        # 跳过统计仅用于告警，不再作为暂停条件。
        # 无PDF/提取失败的文献会写入screening表，因此不会造成死循环；
        # 如果达到阈值就break，反而会破坏无人值守连续筛选。
        SKIP_WARNING_INTERVAL = 10
        consecutive_skips = 0  # 当前连续跳过次数
        total_skips = 0  # 总跳过次数
        
        # 首先清理无效记录
        invalid_count = self.db.mark_invalid_papers_as_skipped()
        if invalid_count > 0:
            self.logger.info(f"已清理 {invalid_count} 条无效记录")

        processed = 0
        progress = self.db.get_progress()
        self.logger.info(
            f"开始筛选 | 总数:{progress['total']} | "
            f"已完成:{progress['processed']} | "
            f"剩余:{progress['remaining']}"
        )

        while True:
            # 信号检查：收到终止信号后安全退出
            if _shutdown.check():
                self.logger.warning(
                    f"收到终止信号，安全退出 | 本次已处理:{processed}篇 | "
                    f"总进度:{progress['processed']}/{progress['total']}"
                )
                break

            # 1. 获取下一篇待筛选文献（从SQLite查询，跳过无效记录）
            paper = self.db.get_next_unscreened(skip_invalid=True)
            if paper is None:
                self.logger.info("全部文献筛选完成！")
                break

            progress = self.db.get_progress()
            self.logger.info(
                f"[{progress['processed']+1}/{progress['total']}] {paper['key']}"
            )

            # 2. 预筛选：检查标题和摘要，排除综述类文章
            should_exclude, reason = self._pre_screen(paper)
            if should_exclude:
                self.logger.info(f"  预筛选排除: {reason}")
                # 添加到screening表，标记为EXCLUDE（E7：综述/理论）
                self.db.conn.execute("""
                    INSERT OR IGNORE INTO screening
                    (key, decision, exclusion_code, reason, screened_at, screening_round)
                    VALUES (?, 'EXCLUDE', 'E7', ?, datetime('now'), '初筛')
                """, (paper["key"], reason))
                self.db.conn.commit()
                self.db.update_progress(paper["key"], "EXCLUDE")
                processed += 1
                continue

            # 3. PDF文本提取
            try:
                text, tool = self._extract_pdf(paper)
                if text is None:
                    self.logger.info("  无PDF，跳过")
                    # 添加到screening表，防止重复处理
                    self.db.conn.execute("""
                        INSERT OR IGNORE INTO screening
                        (key, decision, reason, screened_at)
                        VALUES (?, 'SKIPPED', '无PDF', datetime('now'))
                    """, (paper["key"],))
                    self.db.conn.commit()
                    self.db.update_progress(paper["key"], "SKIPPED")
                    processed += 1
                    
                    # 更新跳过计数
                    consecutive_skips += 1
                    total_skips += 1
                    
                    if consecutive_skips % SKIP_WARNING_INTERVAL == 0:
                        self.logger.warning(
                            f"连续跳过 {consecutive_skips} 篇，请稍后检查PDF匹配情况。"
                        )
                    if total_skips % SKIP_WARNING_INTERVAL == 0:
                        self.logger.warning(
                            f"本次运行累计跳过 {total_skips} 篇，请稍后检查数据质量。"
                        )

                    continue
                
                # 成功提取PDF，重置连续跳过计数
                consecutive_skips = 0
                
                self.logger.info(
                    f"  文本提取成功 ({tool}, {len(text)}字符)"
                )
            except Exception as e:
                self.logger.error(f"  PDF提取失败: {e}")
                # 添加到screening表，防止重复处理
                self.db.conn.execute("""
                    INSERT OR IGNORE INTO screening
                    (key, decision, reason, screened_at)
                    VALUES (?, 'ERROR', ?, datetime('now'))
                """, (paper["key"], str(e)[:200]))
                self.db.conn.commit()
                self.db.update_progress(paper["key"], "ERROR")
                processed += 1
                
                # 更新跳过计数
                consecutive_skips += 1
                total_skips += 1
                
                if consecutive_skips % SKIP_WARNING_INTERVAL == 0:
                    self.logger.warning(
                        f"连续跳过 {consecutive_skips} 篇，请稍后检查PDF匹配情况。"
                    )
                if total_skips % SKIP_WARNING_INTERVAL == 0:
                    self.logger.warning(
                        f"本次运行累计跳过 {total_skips} 篇，请稍后检查数据质量。"
                    )

                continue

            # 3. PICOS判定（独立进程，防上下文污染）
            # 检查文本长度，避免对过短文本进行AI判定
            if len(text) < 100:
                self.logger.warning(f"  文本过短({len(text)}字符)，跳过AI判定")
                result = self._make_maybe(paper, f"文本过短({len(text)}字符)")
            else:
                try:
                    result = self._judge_in_subprocess(text, paper)
                    picos = result.get("picos", {})
                    self.logger.info(
                        f"  AI判定: {result['decision']} "
                        f"(P:{picos.get('P',{}).get('result','?')} "
                        f"I:{picos.get('I',{}).get('result','?')} "
                        f"C:{picos.get('C',{}).get('result','?')} "
                        f"O:{picos.get('O',{}).get('result','?')} "
                        f"S:{picos.get('S',{}).get('result','?')})"
                    )
                except subprocess.TimeoutExpired:
                    self.logger.error("  AI判定超时(180秒)")
                    result = self._make_maybe(paper, "AI判定超时")
                except Exception as e:
                    self.logger.error(f"  AI判定失败: {e}")
                    result = self._make_maybe(paper, f"AI判定异常: {e}")

            # 4. 写入记录（双写：MD + SQLite）
            try:
                from record_writer import write_record
                data = {**paper, **result}
                data["model"] = self.config.get("llm_backend", "unknown")
                data["text_hash"] = hashlib.md5(
                    text.encode()).hexdigest()[:16]
                data["screening_round"] = "正式筛选"
                
                # 强制规则：如果没有PDF路径，不能标记为INCLUDE
                if result["decision"] == "INCLUDE" and not data.get("pdf_path"):
                    self.logger.warning(
                        f"  强制规则：AI判定为INCLUDE但无PDF路径，改为SKIPPED"
                    )
                    data["decision"] = "SKIPPED"
                    data["reason"] = "无PDF路径，不能纳入"
                    data["exclusion_code"] = None
                
                md_path = write_record(data, self.db, self.base)
                self.db.update_progress(paper["key"], data["decision"])
                self.logger.info(f"  已写入: {md_path}")
            except Exception as e:
                self.logger.error(f"  写入失败: {e}")
                self.db.conn.execute("""
                    INSERT OR REPLACE INTO screening
                    (key, decision, reason, screened_at, screening_round)
                    VALUES (?, 'ERROR', ?, datetime('now'), '写入失败')
                """, (paper["key"], str(e)[:200]))
                self.db.conn.commit()
                self.db.update_progress(paper["key"], "ERROR")

            # 5. 速率限制（rate_limiter已在picos_judge内部处理）
            processed += 1

            # 6. 批次控制
            if batch_size and processed >= batch_size:
                self.logger.info(f"已完成 {batch_size} 篇，暂停")
                break
        
        # 循环结束后的统计
        if total_skips > 0:
            self.logger.warning(
                f"本次运行存在跳过/提取失败文献 | "
                f"连续跳过: {consecutive_skips} | "
                f"总跳过: {total_skips} | "
                f"已处理: {processed}"
            )

        self._print_run_summary(processed)

    # ====== 子进程判定 ======

    def _judge_in_subprocess(self, text: str, paper: dict) -> dict:
        """PICOS判定 — 独立进程调用，防止上下文污染"""
        # 写文本到临时文件
        with tempfile.NamedTemporaryFile(
            mode='w', suffix='.txt', delete=False, encoding='utf-8'
        ) as f:
            f.write(text)
            text_file = f.name

        # 构建元数据JSON
        meta = json.dumps({
            "key": paper["key"],
            "author": paper.get("author", ""),
            "year": paper.get("year", ""),
            "title": paper.get("title", ""),
        }, ensure_ascii=False)

        try:
            # 强制子进程使用UTF-8编码（Windows默认GBK会导致中文乱码）
            env = os.environ.copy()
            env["PYTHONIOENCODING"] = "utf-8"
            env["PYTHONUTF8"] = "1"
            if sys.platform == 'win32':
                env["PYTHONLEGACYWINDOWSSTDIO"] = "1"

            # 使用bytes模式捕获，手动解码避免Windows编码问题
            proc = subprocess.run(
                [
                    sys.executable,
                    str(SKILL_DIR / "picos_judge.py"),
                    "--config", str(self.base / "config.json"),
                    "--rules", str(self.base / self.config.get(
                        "picos_rules_path", "PICOS_RULES.md")),
                    "--input", text_file,
                    "--meta", meta,
                ],
                capture_output=True,
                env=env,
                timeout=180,
            )

            # 手动解码stdout/stderr为UTF-8
            stdout = proc.stdout.decode("utf-8", errors="replace") if proc.stdout else ""
            stderr = proc.stderr.decode("utf-8", errors="replace") if proc.stderr else ""

            if proc.returncode == 0:
                return json.loads(stdout)
            else:
                return self._make_maybe(
                    paper, f"判定进程异常: {stderr[:200]}")
        except subprocess.TimeoutExpired:
            raise
        finally:
            os.unlink(text_file)

    def _make_maybe(self, paper: dict, reason: str) -> dict:
        """生成MAYBE结果"""
        return {
            "key": paper["key"],
            "decision": "MAYBE",
            "exclusion_code": None,
            "picos": {
                dim: {"result": "UNCERTAIN", "evidence": [], "analysis": reason}
                for dim in ["P", "I", "C", "O", "S"]
            },
            "reason": reason,
            "text_quality": "N/A",
            "fingerprint": None,
            "model_version": "unknown",
            "prompt_hash": None,
            "extraction_method": None,
            "temperature": 0,
            "seed": 42,
            "text_hash": None,
            "token_stats": {
                "input_tokens": 0,
                "output_tokens": 0,
                "total_tokens": 0,
                "prompt_length": 0,
                "system_prompt_length": 0,
                "text_length": 0
            }
        }

    # ====== PDF处理 ======

    def _extract_pdf(self, paper: dict) -> tuple[str, str] | tuple[None, None]:
        """
        PDF文本提取

        返回: (text, method) 或 (None, None)
        """
        # 查找PDF文件
        pdf_path = self._find_pdf(paper)
        if not pdf_path:
            return None, None

        # 检查mining缓存
        mining_dir = self.base / "mining_output"
        mining_dir.mkdir(exist_ok=True)
        mining_path = mining_dir / f"{paper['key']}_mining.md"

        if mining_path.exists():
            text = mining_path.read_text(encoding="utf-8")
            if len(text) > 100:
                return text, "cached"

        # 提取文本
        try:
            import fitz  # PyMuPDF
            doc = fitz.open(str(pdf_path))
            text = ""
            for page in doc:
                text += page.get_text()
            doc.close()

            # 保存到mining缓存
            mining_path.write_text(text, encoding="utf-8")
            return text, "PyMuPDF"
        except ImportError:
            self.logger.warning("PyMuPDF未安装，无法提取PDF")
            return None, None
        except Exception as e:
            self.logger.error(f"PDF提取失败: {e}")
            return None, None

    def _find_pdf(self, paper: dict) -> Path | None:
        """查找PDF文件"""
        # 检查pdf_mapping.json
        mapping_path = self.base / "pdf_mapping.json"
        if mapping_path.exists():
            with open(mapping_path, encoding="utf-8") as f:
                mapping = json.load(f)
            if paper["key"] in mapping:
                pdf_path = self.base / "pdfs" / mapping[paper["key"]]
                if pdf_path.exists():
                    return pdf_path

        # 尝试默认路径
        pdf_dir = self.base / "pdfs"
        if pdf_dir.exists():
            # 尝试key.pdf
            pdf_path = pdf_dir / f"{paper['key']}.pdf"
            if pdf_path.exists():
                return pdf_path

            # 尝试模糊匹配
            for pdf_file in pdf_dir.glob("*.pdf"):
                if paper["key"][:10] in pdf_file.stem:
                    return pdf_file

        return None

    def _pdf_mapping_ready(self) -> bool:
        """检查PDF映射是否就绪"""
        return (self.base / "pdf_mapping.json").exists()

    def _guard_database_ready_for_pdf_map(self) -> dict:
        """PDF映射前数据库安全检查，防止错库/空库场景。"""
        db_path = self.base / DB_FILE
        if not db_path.exists():
            raise RuntimeError(f"数据库不存在: {db_path}")

        try:
            integrity = self.db.conn.execute("PRAGMA integrity_check").fetchone()[0]
        except sqlite3.DatabaseError as exc:
            raise RuntimeError(f"数据库无法读取或已损坏: {exc}") from exc

        if integrity != "ok":
            raise RuntimeError(f"数据库完整性检查失败: {integrity}")

        tables = {
            row[0]
            for row in self.db.conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            ).fetchall()
        }
        required_tables = {"papers", "screening", "progress"}
        missing_tables = sorted(required_tables - tables)
        if missing_tables:
            raise RuntimeError(f"数据库缺少核心表: {', '.join(missing_tables)}")

        paper_count = self.db.conn.execute("SELECT COUNT(*) FROM papers").fetchone()[0]
        screening_count = self.db.conn.execute("SELECT COUNT(*) FROM screening").fetchone()[0]
        if paper_count == 0:
            raise RuntimeError(
                "拒绝执行PDF映射：papers表为空。请确认 --base 指向正确项目目录，"
                "或先运行 import 导入RIS。"
            )
        if screening_count > paper_count:
            raise RuntimeError(
                f"拒绝执行PDF映射：screening记录数({screening_count})大于"
                f"papers记录数({paper_count})，请先排查数据库。"
            )

        return {
            "db_path": db_path,
            "paper_count": paper_count,
            "screening_count": screening_count,
        }

    def _backup_pdf_map_state(self) -> Path:
        """备份screening.db和现有pdf_mapping.json。"""
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        backup_dir = self.base / "_backups" / f"{timestamp}_before_pdf_map"
        backup_dir.mkdir(parents=True, exist_ok=False)

        self.db.conn.commit()
        db_backup = backup_dir / DB_FILE
        with sqlite3.connect(str(db_backup)) as backup_conn:
            self.db.conn.backup(backup_conn)

        mapping_path = self.base / "pdf_mapping.json"
        if mapping_path.exists():
            shutil.copy2(mapping_path, backup_dir / "pdf_mapping.json")

        return backup_dir

    def _map_pdfs(self):
        """PDF映射：只写pdf_mapping.json，写入前执行数据库护栏和备份。
        
        关键修复：使用数据库中的key格式（Author_Year_Hash6），而不是PDF文件名。
        """
        try:
            db_state = self._guard_database_ready_for_pdf_map()
        except RuntimeError as exc:
            self.logger.error(str(exc))
            raise SystemExit(1) from exc

        pdf_dir = self.base / "pdfs"
        if not pdf_dir.exists():
            self.logger.warning("pdfs目录不存在")
            return

        pdf_files = sorted(pdf_dir.glob("*.pdf"))
        if not pdf_files:
            self.logger.warning("pdfs目录中没有PDF文件")
            return

        backup_dir = self._backup_pdf_map_state()
        self.logger.info(f"已创建PDF映射前备份: {backup_dir}")

        # 从数据库读取所有papers的元数据
        papers = self.db.get_all_papers()
        self.logger.info(f"从数据库读取 {len(papers)} 篇文献")

        # 构建PDF文件索引：基于作者、年份、标题的模糊匹配
        pdf_index = {}
        for pdf_file in pdf_files:
            pdf_name = pdf_file.stem.lower()
            pdf_index[pdf_name] = pdf_file

        # 为每篇paper查找匹配的PDF
        mapping = {}
        matched_count = 0
        unmatched_papers = []
        unmatched_pdfs = set(pdf_index.keys())

        for paper in papers:
            key = paper['key']
            author = paper.get('author', '').lower()
            year = str(paper.get('year', ''))
            title = paper.get('title', '').lower()

            # 尝试多种匹配策略
            matched_pdf = None

            # 策略1：精确匹配key（如果PDF文件名就是key）
            if key.lower() in pdf_index:
                matched_pdf = pdf_index[key.lower()]

            # 策略2：基于作者+年份+标题关键词匹配
            if not matched_pdf and author and year and title:
                # 提取第一作者姓氏
                first_author = author.split(',')[0].split(';')[0].strip().split()[-1].lower() if author else ''
                # 提取标题前30字符
                title_prefix = title[:30].lower()

                for pdf_name, pdf_file in pdf_index.items():
                    # 检查是否包含作者姓氏、年份、标题关键词
                    if (first_author in pdf_name and 
                        year in pdf_name and 
                        any(word in pdf_name for word in title_prefix.split()[:5])):
                        matched_pdf = pdf_file
                        break

            if matched_pdf:
                mapping[key] = matched_pdf.name
                matched_count += 1
                unmatched_pdfs.discard(matched_pdf.stem.lower())
            else:
                unmatched_papers.append(key)

        # 统计结果
        paper_count = db_state["paper_count"]
        self.logger.info(f"PDF匹配结果: {matched_count}/{paper_count} 篇文献找到对应PDF")

        if unmatched_papers:
            self.logger.warning(f"未匹配文献数: {len(unmatched_papers)}")
            # 记录前10个未匹配的文献
            for key in unmatched_papers[:10]:
                self.logger.warning(f"  未匹配: {key}")

        if unmatched_pdfs:
            self.logger.warning(f"未匹配PDF数: {len(unmatched_pdfs)}")

        # 写入mapping
        mapping_path = self.base / "pdf_mapping.json"
        temp_mapping_path = mapping_path.with_suffix(".json.tmp")
        with open(temp_mapping_path, "w", encoding="utf-8") as f:
            json.dump(mapping, f, indent=2, ensure_ascii=False)
        temp_mapping_path.replace(mapping_path)

        self.logger.info(
            f"PDF映射完成: {len(mapping)}个文件 | "
            f"papers:{paper_count} | screening:{db_state['screening_count']}"
        )

    # ====== 预筛选模块 ======

    def _pre_screen(self, paper: dict) -> tuple[bool, str]:
        """
        预筛选：通过标题和摘要排除综述类文章
        
        返回:
            (should_exclude, reason): 是否排除及原因
        """
        title = paper.get("title", "").lower()
        author = paper.get("author", "").lower()
        
        # 综述类关键词（不区分大小写）
        review_keywords = [
            # 英文关键词
            "systematic review",
            "meta-analysis",
            "meta analysis",
            "narrative review",
            "literature review",
            "scoping review",
            "umbrella review",
            "critical review",
            "comprehensive review",
            "overview of reviews",
            "review of reviews",
            "a review",
            "an update",
            "updates on",
            "current perspectives",
            "state of the art",
            "state-of-the-art",
            "recent advances",
            "recent developments",
            "recent progress",
            "a narrative",
            "a scoping",
            "a systematic",
            "a comprehensive",
            "a critical",
            "an overview",
            "an umbrella",
            # 中文关键词
            "综述",
            "系统综述",
            "荟萃分析",
            "meta分析",
            "文献综述",
            "范围综述",
            "伞状综述",
            "叙述性综述",
        ]
        
        # 标题模式匹配（正则表达式）
        review_patterns = [
            r"^a\s+systematic\s+review",
            r"^a\s+meta[\s-]analysis",
            r"^a\s+narrative\s+review",
            r"^a\s+scoping\s+review",
            r"^systematic\s+review\s+and\s+meta[\s-]analysis",
            r"^review\s+of\s+",
            r"^overview\s+of\s+",
            r"^state\s+of\s+the\s+art",
            r"^recent\s+advances\s+in",
            r"^recent\s+developments\s+in",
            r"^current\s+perspectives\s+on",
            r"^an?\s+update\s+on",
        ]
        
        # 检查标题是否匹配综述模式
        for pattern in review_patterns:
            if re.search(pattern, title, re.IGNORECASE):
                return True, f"综述类文献-标题匹配模式: {pattern}"
        
        # 检查标题是否包含综述关键词
        for keyword in review_keywords:
            if keyword in title:
                return True, f"综述类文献-标题包含关键词: {keyword}"
        
        return False, ""

    def _pre_screen_batch(self, papers: list[dict]) -> list[tuple[dict, bool, str]]:
        """
        批量预筛选
        
        返回:
            [(paper, should_exclude, reason), ...]
        """
        results = []
        for paper in papers:
            should_exclude, reason = self._pre_screen(paper)
            results.append((paper, should_exclude, reason))
        return results

    # ====== 辅助方法 ======

    def _init_check(self):
        """检查初始化状态"""
        if not (self.base / CONFIG_FILE).exists():
            self.logger.error(
                f"缺少{CONFIG_FILE}，请运行: python screen.py init")
            sys.exit(1)

    def _print_run_summary(self, processed: int):
        """打印本次运行摘要"""
        progress = self.db.get_progress()
        self.logger.info(f"{'='*50}")
        self.logger.info(f"本次运行: 处理 {processed} 篇")
        self.logger.info(
            f"总进度: {progress['processed']}/{progress['total']}")
        self.logger.info(
            f"INCLUDE:{progress['include_count']} "
            f"EXCLUDE:{progress['exclude_count']} "
            f"MAYBE:{progress['maybe_count']} "
            f"SKIPPED:{progress['skipped_count']} "
            f"ERROR:{progress['error_count']}"
        )
        self.logger.info(f"{'='*50}")

    # ====== CLI命令 ======

    def check(self):
        """查看进度"""
        progress = self.db.get_summary()
        print(f"{'='*50}")
        print(f"筛选进度")
        print(f"{'='*50}")
        print(f"  总数: {progress.get('total', 0)}")
        print(f"  已处理: {progress.get('processed', 0)}")
        print(f"  剩余: {progress.get('remaining', 0)}")
        print(f"  INCLUDE: {progress.get('include_count', 0)}")
        print(f"  EXCLUDE: {progress.get('exclude_count', 0)}")
        print(f"  MAYBE: {progress.get('maybe_count', 0)}")
        print(f"  SKIPPED: {progress.get('skipped_count', 0)}")
        print(f"  ERROR: {progress.get('error_count', 0)}")
        print(f"  状态: {progress.get('status', 'unknown')}")
        print(f"  当前: {progress.get('current_key', 'N/A')}")
        print(f"{'='*50}")

    def verify(self):
        """验证数据一致性"""
        issues = self.db.verify_consistency()
        if issues:
            print(f"[ERROR] 发现 {len(issues)} 个问题:")
            for issue in issues:
                print(f"  - {issue}")
        else:
            print("[OK] 数据一致性验证通过")

    def summary(self):
        """汇总报告"""
        self.check()

        # 按年份统计
        try:
            rows = self.db.conn.execute("""
                SELECT p.year, s.decision, COUNT(*) as cnt
                FROM screening s
                JOIN papers p ON s.key = p.key
                WHERE p.year > 0
                GROUP BY p.year, s.decision
                ORDER BY p.year
            """).fetchall()

            if rows:
                print(f"\n按年份统计:")
                for row in rows:
                    print(f"  {row['year']}: {row['decision']}={row['cnt']}")
        except Exception:
            pass

    def export_csv(self):
        """导出CSV"""
        csv_path = self.base / "screening_summary.csv"
        self.db.export_csv(csv_path)
        print(f"[OK] 已导出: {csv_path}")

    def generate_report(self) -> str:
        """
        生成完整筛选报告（Markdown格式）

        返回: 报告文件路径
        """
        progress = self.db.rebuild_progress()

        # ====== 数据采集 ======
        total = progress.get("total", 0)
        processed = progress.get("processed", 0)
        remaining = progress.get("remaining", 0)
        include_n = progress.get("include_count", 0)
        exclude_n = progress.get("exclude_count", 0)
        maybe_n = progress.get("maybe_count", 0)
        skipped_n = progress.get("skipped_count", 0)
        error_n = progress.get("error_count", 0)
        status = progress.get("status", "unknown")

        # 排除码分布
        exclusion_dist = self.db.get_exclusion_distribution()

        # PICOS维度统计（仅INCLUDE文献）
        picos_stats = self.db.get_picos_dimension_stats()

        # 年份分布（仅INCLUDE文献）
        try:
            year_rows = self.db.conn.execute("""
                SELECT p.year, COUNT(*) as cnt
                FROM screening s
                JOIN papers p ON s.key = p.key
                WHERE s.decision = 'INCLUDE' AND p.year > 0
                GROUP BY p.year
                ORDER BY p.year
            """).fetchall()
            year_dist = [(row["year"], row["cnt"]) for row in year_rows]
        except Exception:
            year_dist = []

        # INCLUDE无PDF文献
        include_no_pdf = self.db.get_include_without_pdf()

        # 时间范围
        time_range = self.db.get_screening_time_range()

        # 数据一致性检查
        issues = self.db.verify_consistency()

        # MAYBE文献列表
        maybe_papers = self.db.get_by_decision("MAYBE")

        # ERROR文献列表
        error_papers = self.db.get_by_decision("ERROR")

        # SKIPPED文献列表
        skipped_papers = self.db.get_by_decision("SKIPPED")

        # ====== 报告生成 ======
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        pct = (processed / total * 100) if total > 0 else 0
        bar_len = 30
        filled = int(bar_len * pct / 100)
        bar = "█" * filled + "░" * (bar_len - filled)

        # 状态中文映射
        status_map = {
            "idle": "未开始",
            "running": "进行中",
            "done": "已完成",
        }
        status_cn = status_map.get(status, status)

        lines = []
        lines.append("# screen-repro 筛选报告")
        lines.append("")
        lines.append(f"> **生成时间**：{now}")
        lines.append(f"> **项目目录**：`{self.base}`")
        lines.append(f"> **工具版本**：screen-repro v3.1")
        lines.append("")
        lines.append("---")
        lines.append("")

        # ====== 1. 筛选总览 ======
        lines.append("## 1. 筛选总览")
        lines.append("")
        lines.append("| 指标 | 数值 |")
        lines.append("|------|------|")
        lines.append(f"| 文献总数 | **{total}** |")
        lines.append(f"| 已筛选 | **{processed}** |")
        lines.append(f"| 未筛选 | **{remaining}** |")
        lines.append(f"| 完成率 | **{pct:.1f}%** |")
        lines.append(f"| 筛选状态 | **{status_cn}** |")
        lines.append("")
        lines.append(f"**进度**：`[{bar}] {pct:.1f}%`")
        lines.append("")
        lines.append("---")
        lines.append("")

        # ====== 2. 决策分布 ======
        lines.append("## 2. 决策分布")
        lines.append("")
        lines.append("| 决策 | 数量 | 占比 | 图示 |")
        lines.append("|------|------|------|------|")

        decisions = [
            ("INCLUDE", include_n, "纳入"),
            ("EXCLUDE", exclude_n, "排除"),
            ("MAYBE", maybe_n, "待定"),
            ("SKIPPED", skipped_n, "跳过"),
            ("ERROR", error_n, "异常"),
        ]

        # 决策分布水平柱状图
        max_cnt = max(d[1] for d in decisions) if decisions else 1
        for label, cnt, cn in decisions:
            p = (cnt / processed * 100) if processed > 0 else 0
            bar_w = int(20 * cnt / max_cnt) if max_cnt > 0 else 0
            bar_str = "█" * bar_w
            lines.append(f"| {label} ({cn}) | {cnt} | {p:.1f}% | `{bar_str}` |")

        lines.append("")
        lines.append("---")
        lines.append("")

        # ====== 3. 排除原因分析 ======
        lines.append("## 3. 排除原因分析")
        lines.append("")

        if exclusion_dist:
            lines.append("| 排除码 | 数量 | 占EXCLUDE% | 典型原因 |")
            lines.append("|--------|------|------------|----------|")
            for item in exclusion_dist:
                code = item["code"]
                cnt = item["cnt"]
                reasons = item.get("reasons", "") or ""
                # 取第一个reason作为典型原因
                typical = reasons.split(",")[0][:60] if reasons else "-"
                pct_excl = (cnt / exclude_n * 100) if exclude_n > 0 else 0
                lines.append(f"| {code} | {cnt} | {pct_excl:.1f}% | {typical} |")
            lines.append("")
        else:
            lines.append("_暂无排除记录_")
            lines.append("")

        lines.append("---")
        lines.append("")

        # ====== 4. INCLUDE 文献年份分布 ======
        lines.append("## 4. INCLUDE 文献年份分布")
        lines.append("")

        if year_dist:
            lines.append("| 年份 | 数量 | 图示 |")
            lines.append("|------|------|------|")
            max_year_cnt = max(c for _, c in year_dist) if year_dist else 1
            for year, cnt in year_dist:
                bar_w = int(15 * cnt / max_year_cnt) if max_year_cnt > 0 else 0
                bar_str = "█" * bar_w
                lines.append(f"| {year} | {cnt} | `{bar_str}` |")
            lines.append("")
        else:
            lines.append("_暂无 INCLUDE 记录_")
            lines.append("")

        lines.append("---")
        lines.append("")

        # ====== 5. PICOS 维度分析 ======
        lines.append("## 5. PICOS 维度分析（INCLUDE 文献）")
        lines.append("")

        dim_labels = {
            "P": "人群 (Population)",
            "I": "干预 (Intervention)",
            "C": "对照 (Comparator)",
            "O": "结局 (Outcomes)",
            "S": "研究设计 (Study Design)",
        }

        has_picos = any(v for v in picos_stats.values())
        if has_picos:
            lines.append("| 维度 | PASS | FAIL | UNCERTAIN | 通过率 |")
            lines.append("|------|------|------|-----------|--------|")
            for dim in ["P", "I", "C", "O", "S"]:
                d = picos_stats.get(dim, {})
                pass_n = d.get("PASS", 0) + d.get("✅", 0)
                fail_n = d.get("FAIL", 0) + d.get("❌", 0)
                uncertain_n = d.get("UNCERTAIN", 0) + d.get("⚠️", 0)
                dim_total = pass_n + fail_n + uncertain_n
                pass_rate = (pass_n / dim_total * 100) if dim_total > 0 else 0
                lines.append(
                    f"| {dim} ({dim_labels[dim]}) | {pass_n} | {fail_n} "
                    f"| {uncertain_n} | {pass_rate:.1f}% |"
                )
            lines.append("")
        else:
            lines.append("_暂无 INCLUDE 文献的 PICOS 判定数据_")
            lines.append("")

        lines.append("---")
        lines.append("")

        # ====== 6. 质量检查 ======
        lines.append("## 6. 质量检查")
        lines.append("")
        lines.append("| 检查项 | 状态 | 详情 |")
        lines.append("|--------|------|------|")

        # 无PDF的INCLUDE文献
        if include_no_pdf:
            lines.append(
                f"| INCLUDE无PDF | **⚠ 警告** | {len(include_no_pdf)} 篇 |"
            )
        else:
            lines.append("| INCLUDE无PDF | ✅ 正常 | 0 篇 |")

        # 数据一致性
        if issues:
            lines.append(
                f"| 数据一致性 | **⚠ 问题** | {len(issues)} 项 |"
            )
        else:
            lines.append("| 数据一致性 | ✅ 正常 | 无问题 |")

        # MAYBE文献
        if maybe_papers:
            lines.append(
                f"| MAYBE文献 | **⚠ 需复核** | {len(maybe_papers)} 篇 |"
            )
        else:
            lines.append("| MAYBE文献 | ✅ 正常 | 0 篇 |")

        # ERROR文献
        if error_papers:
            lines.append(
                f"| ERROR文献 | **❌ 异常** | {len(error_papers)} 篇 |"
            )
        else:
            lines.append("| ERROR文献 | ✅ 正常 | 0 篇 |")

        lines.append("")

        # 展开详情
        if include_no_pdf:
            lines.append("### INCLUDE 但无 PDF 的文献")
            lines.append("")
            for p in include_no_pdf:
                lines.append(
                    f"- `{p['key']}` — {p.get('author','')}, "
                    f"{p.get('year','')}, {p.get('title','')[:60]}"
                )
            lines.append("")

        if maybe_papers:
            lines.append("### MAYBE 文献列表（需人工复核）")
            lines.append("")
            for p in maybe_papers:
                reason = (p.get("reason", "") or "")[:50]
                lines.append(f"- `{p['key']}` — {reason}")
            lines.append("")

        if error_papers:
            lines.append("### ERROR 文献列表")
            lines.append("")
            for p in error_papers:
                reason = (p.get("reason", "") or "")[:50]
                lines.append(f"- `{p['key']}` — {reason}")
            lines.append("")

        if skipped_papers:
            lines.append(f"### SKIPPED 文献列表（共 {len(skipped_papers)} 篇）")
            lines.append("")
            for p in skipped_papers[:20]:  # 最多显示20篇
                reason = (p.get("reason", "") or "")[:50]
                lines.append(f"- `{p['key']}` — {reason}")
            if len(skipped_papers) > 20:
                lines.append(f"- _...等共 {len(skipped_papers)} 篇_")
            lines.append("")

        lines.append("---")
        lines.append("")

        # ====== 7. 筛选效率 ======
        lines.append("## 7. 筛选效率")
        lines.append("")
        first_dt = time_range.get("first_screened", "")
        last_dt = time_range.get("last_screened", "")
        days = time_range.get("screening_days", 0) or 0

        lines.append("| 指标 | 数值 |")
        lines.append("|------|------|")
        lines.append(f"| 首次筛选 | {first_dt or 'N/A'} |")
        lines.append(f"| 最近筛选 | {last_dt or 'N/A'} |")
        lines.append(f"| 筛选天数 | {days} 天 |")

        if days > 0 and processed > 0:
            daily_avg = processed / days
            lines.append(f"| 日均处理 | {daily_avg:.1f} 篇/天 |")

            # 预估剩余时间
            if remaining > 0 and daily_avg > 0:
                eta_days = remaining / daily_avg
                lines.append(f"| 预估剩余 | {eta_days:.1f} 天 |")

        lines.append("")
        lines.append("---")
        lines.append("")
        lines.append(f"*screen-repro v3.1 自动生成 | {now}*")

        # ====== 写入文件 ======
        report_content = "\n".join(lines)
        reports_dir = self.base / "reports"
        reports_dir.mkdir(exist_ok=True)

        date_str = datetime.now().strftime("%Y%m%d")
        report_path = reports_dir / f"screening_report_{date_str}.md"
        report_path.write_text(report_content, encoding="utf-8")

        # 同时打印到控制台
        print(report_content)
        print(f"\n[OK] 报告已保存: {report_path}")

        return str(report_path)


# ====== 初始化项目 ======

def init_project(base: Path):
    """初始化项目"""
    # 创建目录
    for d in ["screening_records/INCLUDE", "screening_records/EXCLUDE",
              "screening_records/MAYBE", "pdfs", "mining_output"]:
        (base / d).mkdir(parents=True, exist_ok=True)

    # 创建config.json（如果不存在）
    config_path = base / CONFIG_FILE
    if not config_path.exists():
        config = {
            "llm_backend": "openai",
            "openai": {
                "api_key": "YOUR_API_KEY_HERE",
                "model": "gpt-4o-2024-08-06",
                "base_url": "https://api.openai.com/v1",
                "rpm": 100,
                "tpm": 10000000,
            },
            "anthropic": {
                "api_key": "YOUR_API_KEY_HERE",
                "model": "claude-sonnet-4-20250514",
                "rpm": 60,
                "tpm": 80000,
            },
            "ollama": {
                "model": "qwen2.5:72b",
                "base_url": "http://localhost:11434",
                "rpm": 9999,
                "tpm": 999999999,
            },
            "rate_limit": {
                "enabled": True,
                "default_rpm": 60,
                "default_tpm": 100000,
                "safety_margin": 0.8,
            },
            "picos_rules_path": "PICOS_RULES.md",
            "pdf_extractor": "pymupdf",
            "max_text_length": 8000,
            "cache_enabled": True,
        }
        with open(config_path, "w", encoding="utf-8") as f:
            json.dump(config, f, indent=2, ensure_ascii=False)
        print(f"[OK] 已创建 {CONFIG_FILE}（请填入API key）")

    # 初始化数据库
    sys.path.insert(0, str(SKILL_DIR))
    from db_manager import DbManager

    db_path = base / DB_FILE
    with DbManager(db_path) as db:
        db.init_db()
        print(f"[OK] 数据库已初始化: {db_path}")

    print(f"[OK] 项目初始化完成: {base}")


# ====== 迁移脚本 ======

def migrate_from_v2(base: Path):
    """从v2.3迁移到v3.0"""
    sys.path.insert(0, str(SKILL_DIR))
    from db_manager import DbManager

    # 检查v2.3文件
    csv_path = base / "screening_summary.csv"
    progress_path = base / "screening_progress.json"

    if not csv_path.exists():
        print("[ERROR] 未找到screening_summary.csv")
        return

    print("[INFO] 开始从v2.3迁移...")

    # 初始化数据库
    db_path = base / DB_FILE
    with DbManager(db_path) as db:
        db.init_db()

        # 导入数据
        db.import_from_v2(csv_path, progress_path,
                          base / "screening_records")

        # 验证
        issues = db.verify_consistency()
        if issues:
            print(f"[WARN] 迁移后发现 {len(issues)} 个问题:")
            for issue in issues:
                print(f"  - {issue}")
        else:
            print("[OK] 迁移后数据一致性验证通过")

    # 备份原文件
    for f in ["screening_progress.json", "screening_summary.csv",
              "screening_cache.json"]:
        src = base / f
        if src.exists():
            backup = base / f"{f}.v2.3_backup"
            if not backup.exists():
                src.rename(backup)
                print(f"  备份: {f} -> {backup.name}")

    print("[OK] 迁移完成")


# ====== CLI ======

def main():
    parser = argparse.ArgumentParser(
        description="screen-repro v3.2 — 程序化优先的PICOS文献筛选系统",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
screen-repro v3.2 — 程序化优先的PICOS文献筛选系统

设计哲学：程序主导，AI辅助，可复现为本。
三层架构：Orchestration（纯程序） → Extraction（纯程序） → Judgment（AI最小化）

示例:
  python screen.py init              初始化项目
  python screen.py import --ris xxx.ris  导入RIS文件
  python screen.py run               执行筛选（含规则预筛+AI判定）
  python screen.py run --batch 10    筛选10篇后暂停
  python screen.py run --base D:\\project  指定项目目录运行
  python screen.py check             查看进度
  python screen.py verify            验证一致性
  python screen.py report            生成完整筛选报告
  python screen.py export            导出CSV
  python screen.py pdf map           PDF映射（含数据库前置校验+备份）
  python screen.py prescreen         遗留模式：预筛选+AI复核+人机协同
  python screen.py workflow --ris xxx.ris  一键执行完整流程
        """
    )
    parser.add_argument("--base", type=str, default=None,
                        help="项目目录路径（默认使用当前目录）")
    sub = parser.add_subparsers(dest="command")

    # init
    sub.add_parser("init", help="初始化项目")

    # import
    import_p = sub.add_parser("import", help="导入RIS文件")
    import_p.add_argument("--ris", required=True, help="RIS文件路径")

    # prescreen (遗留模式)
    prescreen_p = sub.add_parser("prescreen", help="遗留模式：预筛选+AI复核+人机协同")
    prescreen_p.add_argument("--batch", type=int, help="AI复核批次大小")

    # run
    run_p = sub.add_parser("run", help="执行筛选")
    run_p.add_argument("--batch", type=int, help="筛选N篇后暂停")

    # check / verify / summary / export / report
    sub.add_parser("check", help="查看进度")
    sub.add_parser("verify", help="验证一致性")
    sub.add_parser("summary", help="汇总报告")
    sub.add_parser("export", help="导出CSV")
    sub.add_parser("report", help="生成完整筛选报告")

    # migrate
    sub.add_parser("migrate", help="从v2.3迁移")

    # pdf
    pdf_p = sub.add_parser("pdf", help="PDF管理")
    pdf_p.add_argument("action", choices=["map", "map-update"])

    # qa
    qa_p = sub.add_parser("qa", help="QA管理")
    qa_p.add_argument("action",
                      choices=["generate", "resolve", "confirm", "status"])
    qa_p.add_argument("args", nargs="*")

    # workflow
    workflow_p = sub.add_parser("workflow", help="一键执行完整流程")
    workflow_p.add_argument("--ris", required=True, help="RIS文件路径")
    workflow_p.add_argument("--batch", type=int, help="AI复核批次大小")

    args = parser.parse_args()

    # Windows兼容性：强制UTF-8编码
    if sys.platform == 'win32' and hasattr(sys.stdout, 'buffer'):
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
        sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8')

    # 确定项目目录
    if args.base:
        base = Path(args.base).resolve()
        if not base.exists():
            print(f"错误: 项目目录不存在: {base}")
            sys.exit(1)
    else:
        base = Path.cwd()

    if args.command == "init":
        init_project(base)

    elif args.command == "import":
        import_ris(base, args.ris)

    elif args.command == "prescreen":
        run_prescreen(base, batch_size=args.batch)

    elif args.command == "run":
        orch = ScreeningOrchestrator(base)
        orch.run(batch_size=args.batch)

    elif args.command == "check":
        orch = ScreeningOrchestrator(base)
        orch.check()

    elif args.command == "verify":
        orch = ScreeningOrchestrator(base)
        orch.verify()

    elif args.command == "summary":
        orch = ScreeningOrchestrator(base)
        orch.summary()

    elif args.command == "report":
        orch = ScreeningOrchestrator(base)
        orch.generate_report()

    elif args.command == "export":
        orch = ScreeningOrchestrator(base)
        orch.export_csv()

    elif args.command == "migrate":
        migrate_from_v2(base)

    elif args.command == "pdf":
        try:
            orch = ScreeningOrchestrator(
                base, init_db=False, require_existing_db=True)
        except RuntimeError as exc:
            print(f"错误: {exc}")
            sys.exit(1)
        if args.action == "map":
            orch._map_pdfs()

    elif args.command == "qa":
        print("QA功能尚未实现")

    elif args.command == "workflow":
        run_workflow(base, args.ris, batch_size=args.batch)

    else:
        parser.print_help()


def import_ris(base: Path, ris_path: str):
    """导入RIS文件"""
    from ris_parser import import_ris_to_db

    # 检查RIS文件是否存在
    ris_file = Path(ris_path)
    if not ris_file.exists():
        # 尝试在项目目录中查找
        ris_file = base / ris_path
        if not ris_file.exists():
            print(f"错误: RIS文件不存在: {ris_path}")
            sys.exit(1)

    # 检查数据库是否存在
    db_path = base / DB_FILE
    if not db_path.exists():
        print("错误: 数据库不存在，请先运行 init")
        sys.exit(1)

    # 导入RIS文件
    print(f"导入RIS文件: {ris_file}")
    result = import_ris_to_db(str(ris_file), str(db_path))

    print(f"\n=== 导入结果 ===")
    print(f"RIS记录数: {result['total_records']}")
    print(f"导入: {result['imported']}")
    print(f"跳过: {result['skipped']}")
    print(f"数据库总数: {result['total_in_db']}")


def run_prescreen(base: Path, batch_size: int = None):
    """运行预筛选+AI复核+人机协同"""
    from ai_reviewer import review_papers_in_db
    from human_review import HumanReviewer

    # 检查数据库是否存在
    db_path = base / DB_FILE
    if not db_path.exists():
        print("错误: 数据库不存在，请先运行 init")
        sys.exit(1)

    # 检查配置文件是否存在
    config_path = base / CONFIG_FILE
    if not config_path.exists():
        print("错误: 配置文件不存在，请先运行 init")
        sys.exit(1)

    # 加载配置
    with open(config_path, 'r', encoding='utf-8') as f:
        config = json.load(f)

    # 运行AI复核
    print("开始AI复核...")
    review_result = review_papers_in_db(config, str(db_path), batch_size)

    # 保存AI复核结果
    review_result_path = base / "review_result.json"
    with open(review_result_path, 'w', encoding='utf-8') as f:
        json.dump(review_result, f, ensure_ascii=False, indent=2)
    print(f"AI复核结果已保存到: {review_result_path}")

    # 运行人机协同复核
    print("\n开始人机协同复核...")
    reviewer = HumanReviewer(str(db_path))
    result = reviewer.interactive_review(review_result)
    reviewer.close()

    # 输出结果
    print(f"\n操作结果：{result.get('action', 'unknown')}")


def run_workflow(base: Path, ris_path: str, batch_size: int = None):
    """一键执行完整流程"""
    print("=" * 70)
    print("screen-repro v3.1 完整工作流程")
    print("=" * 70)
    print()

    # 步骤1：RIS导入
    print("【步骤1】RIS导入")
    print("-" * 70)
    import_ris(base, ris_path)
    print()

    # 步骤2：预筛选+AI复核+人机协同
    print("【步骤2】预筛选+AI复核+人机协同")
    print("-" * 70)
    run_prescreen(base, batch_size)
    print()

    # 步骤3：PDF映射
    print("【步骤3】PDF映射")
    print("-" * 70)
    orch = ScreeningOrchestrator(base)
    orch._map_pdfs()
    print()

    # 步骤4：正常筛选
    print("【步骤4】正常筛选")
    print("-" * 70)
    print("预筛选完成，可以运行 python screen.py run 开始筛选")
    print()


if __name__ == "__main__":
    main()
