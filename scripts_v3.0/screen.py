#!/usr/bin/env python3
"""
screen.py — screen-repro v3.0 主编排器
======================================
单一入口，Python控制一切，AI只在PICOS判定时被调用。
数据存储：SQLite（权威） + MD文件（人类可读）

CLI命令:
    python screen.py init              # 初始化项目
    python screen.py run               # 执行筛选循环
    python screen.py run --batch 10    # 筛选10篇后暂停
    python screen.py check             # 查看进度
    python screen.py verify            # 验证一致性
    python screen.py summary           # 汇总报告
    python screen.py export            # 导出CSV
    python screen.py migrate           # 从v2.3迁移
    python screen.py pdf map           # PDF映射
    python screen.py qa generate       # 生成QA报告
    python screen.py qa resolve ...    # MAYBE复核
    python screen.py qa confirm ...    # 抽样确认
    python screen.py qa status         # QA进度
"""

import argparse
import json
import sys
import os
import io
import logging
import subprocess
import tempfile

# Windows兼容性：强制UTF-8编码
if sys.platform == 'win32':
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8')
import hashlib
import csv
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


# ====== 主编排器 ======

class ScreeningOrchestrator:
    """筛选编排器 — Python控制一切，SQLite存储"""

    def __init__(self, project_dir: Path):
        self.base = project_dir
        self.config = load_config(project_dir)
        self.logger = setup_logging(project_dir)

        # 导入DbManager
        sys.path.insert(0, str(SKILL_DIR))
        from db_manager import DbManager

        self.db = DbManager(project_dir / DB_FILE)
        self.db.connect()
        self.db.init_db()

    def __del__(self):
        if hasattr(self, 'db') and self.db:
            self.db.close()

    # ====== 核心循环 ======

    def run(self, batch_size=None):
        """主筛选循环"""
        self._init_check()

        # 强制PDF匹配检查
        if not self._pdf_mapping_ready():
            self.logger.info("正在匹配PDF文件与文献库...")
            self._map_pdfs()

        # 防死循环配置
        MAX_CONSECUTIVE_SKIPS = 10  # 最大连续跳过次数
        MAX_TOTAL_SKIPS = 50  # 最大总跳过次数
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
            # 1. 获取下一篇待筛选文献（从SQLite查询，跳过无效记录）
            paper = self.db.get_next_unscreened(skip_invalid=True)
            if paper is None:
                self.logger.info("全部文献筛选完成！")
                break

            progress = self.db.get_progress()
            self.logger.info(
                f"[{progress['processed']+1}/{progress['total']}] {paper['key']}"
            )

            # 2. PDF文本提取
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
                    
                    # 防死循环检查
                    if consecutive_skips >= MAX_CONSECUTIVE_SKIPS:
                        self.logger.warning(
                            f"连续跳过 {consecutive_skips} 篇，可能存在问题。"
                            f"暂停筛选，请检查PDF文件是否齐全。"
                        )
                        break
                    if total_skips >= MAX_TOTAL_SKIPS:
                        self.logger.warning(
                            f"总跳过 {total_skips} 篇，达到上限。"
                            f"暂停筛选，请检查数据质量。"
                        )
                        break
                    
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
                
                # 防死循环检查
                if consecutive_skips >= MAX_CONSECUTIVE_SKIPS:
                    self.logger.warning(
                        f"连续跳过 {consecutive_skips} 篇，可能存在问题。"
                        f"暂停筛选，请检查PDF文件是否齐全。"
                    )
                    break
                if total_skips >= MAX_TOTAL_SKIPS:
                    self.logger.warning(
                        f"总跳过 {total_skips} 篇，达到上限。"
                        f"暂停筛选，请检查数据质量。"
                    )
                    break
                
                continue

            # 3. PICOS判定（独立进程，防上下文污染）
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
                    text[:1000].encode()).hexdigest()[:8]
                md_path = write_record(data, self.db, self.base)
                self.db.update_progress(paper["key"], result["decision"])
                self.logger.info(f"  已写入: {md_path}")
            except Exception as e:
                self.logger.error(f"  写入失败: {e}")
                self.db.update_progress(paper["key"], "ERROR")

            # 5. 速率限制（rate_limiter已在picos_judge内部处理）
            processed += 1

            # 6. 批次控制
            if batch_size and processed >= batch_size:
                self.logger.info(f"已完成 {batch_size} 篇，暂停")
                break
        
        # 循环结束后的统计
        if consecutive_skips >= MAX_CONSECUTIVE_SKIPS or total_skips >= MAX_TOTAL_SKIPS:
            self.logger.warning(
                f"筛选因跳过次数过多而暂停 | "
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
                text=True,
                encoding="utf-8",
                timeout=180,
            )

            if proc.returncode == 0:
                return json.loads(proc.stdout)
            else:
                return self._make_maybe(
                    paper, f"判定进程异常: {proc.stderr[:200]}")
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
                dim: {"result": "⚠️", "evidence": [], "analysis": reason}
                for dim in ["P", "I", "C", "O", "S"]
            },
            "reason": reason,
            "text_quality": "N/A",
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

    def _map_pdfs(self):
        """PDF映射（简化版）"""
        pdf_dir = self.base / "pdfs"
        if not pdf_dir.exists():
            self.logger.warning("pdfs目录不存在")
            return

        mapping = {}
        for pdf_file in pdf_dir.glob("*.pdf"):
            # 尝试从文件名提取key
            name = pdf_file.stem
            mapping[name] = pdf_file.name

        mapping_path = self.base / "pdf_mapping.json"
        with open(mapping_path, "w", encoding="utf-8") as f:
            json.dump(mapping, f, indent=2, ensure_ascii=False)

        self.logger.info(f"PDF映射完成: {len(mapping)}个文件")

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
        print(f"📊 筛选进度")
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
            print(f"❌ 发现 {len(issues)} 个问题:")
            for issue in issues:
                print(f"  - {issue}")
        else:
            print("✅ 数据一致性验证通过")

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
        print(f"✅ 已导出: {csv_path}")


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
        print(f"✅ 已创建 {CONFIG_FILE}（请填入API key）")

    # 初始化数据库
    sys.path.insert(0, str(SKILL_DIR))
    from db_manager import DbManager

    db_path = base / DB_FILE
    with DbManager(db_path) as db:
        db.init_db()
        print(f"✅ 数据库已初始化: {db_path}")

    print(f"✅ 项目初始化完成: {base}")


# ====== 迁移脚本 ======

def migrate_from_v2(base: Path):
    """从v2.3迁移到v3.0"""
    sys.path.insert(0, str(SKILL_DIR))
    from db_manager import DbManager

    # 检查v2.3文件
    csv_path = base / "screening_summary.csv"
    progress_path = base / "screening_progress.json"

    if not csv_path.exists():
        print("❌ 未找到screening_summary.csv")
        return

    print("🔄 开始从v2.3迁移...")

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
            print(f"⚠️ 迁移后发现 {len(issues)} 个问题:")
            for issue in issues:
                print(f"  - {issue}")
        else:
            print("✅ 迁移后数据一致性验证通过")

    # 备份原文件
    for f in ["screening_progress.json", "screening_summary.csv",
              "screening_cache.json"]:
        src = base / f
        if src.exists():
            backup = base / f"{f}.v2.3_backup"
            if not backup.exists():
                src.rename(backup)
                print(f"  备份: {f} → {backup.name}")

    print("✅ 迁移完成")


# ====== CLI ======

def main():
    parser = argparse.ArgumentParser(
        description="screen-repro v3.0 — 可复现的文献筛选系统",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  python screen.py init              初始化项目
  python screen.py run               执行筛选
  python screen.py run --batch 10    筛选10篇后暂停
  python screen.py check             查看进度
  python screen.py verify            验证一致性
  python screen.py export            导出CSV
  python screen.py migrate           从v2.3迁移
        """
    )
    sub = parser.add_subparsers(dest="command")

    # init
    sub.add_parser("init", help="初始化项目")

    # run
    run_p = sub.add_parser("run", help="执行筛选")
    run_p.add_argument("--batch", type=int, help="筛选N篇后暂停")

    # check / verify / summary / export
    sub.add_parser("check", help="查看进度")
    sub.add_parser("verify", help="验证一致性")
    sub.add_parser("summary", help="汇总报告")
    sub.add_parser("export", help="导出CSV")

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

    args = parser.parse_args()
    base = Path.cwd()

    if args.command == "init":
        init_project(base)

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

    elif args.command == "export":
        orch = ScreeningOrchestrator(base)
        orch.export_csv()

    elif args.command == "migrate":
        migrate_from_v2(base)

    elif args.command == "pdf":
        orch = ScreeningOrchestrator(base)
        if args.action == "map":
            orch._map_pdfs()

    elif args.command == "qa":
        print("QA功能尚未实现")

    else:
        parser.print_help()


if __name__ == "__main__":
    main()
