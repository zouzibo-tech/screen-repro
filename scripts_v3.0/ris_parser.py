#!/usr/bin/env python3
"""
ris_parser.py — RIS文件解析器
============================
解析RIS文件，提取文献元数据（TI/AU/PY/AB/DO等字段）
用于screen-repro v3.1的文献池导入

用法:
    python ris_parser.py input.ris                    # 解析并输出统计
    python ris_parser.py input.ris --db screening.db  # 解析并写入数据库
"""

import re
import sys
import json
import sqlite3
from pathlib import Path
from datetime import datetime


class RisParser:
    """RIS文件解析器"""

    # RIS字段映射
    FIELD_MAP = {
        'TY': 'type',
        'TI': 'title',
        'T1': 'title',  # 备选标题字段
        'AU': 'authors',
        'A1': 'authors',  # 备选作者字段
        'PY': 'year',
        'Y1': 'year',  # 备选年份字段
        'DA': 'date',
        'AB': 'abstract',
        'N2': 'abstract',  # 备选摘要字段
        'DO': 'doi',
        'UR': 'url',
        'KW': 'keywords',
        'T2': 'journal',
        'JO': 'journal',  # 备选期刊字段
        'VL': 'volume',
        'IS': 'issue',
        'SP': 'start_page',
        'EP': 'end_page',
        'SN': 'issn',
        'LA': 'language',
        'AN': 'accession_number',
        'ER': 'end',  # 记录结束标记
    }

    def __init__(self):
        self.records = []
        self.current_record = {}
        self.current_field = None
        self.current_value = []

    def parse(self, ris_path: str) -> list[dict]:
        """
        解析RIS文件

        参数:
            ris_path: RIS文件路径

        返回:
            解析后的文献记录列表
        """
        ris_path = Path(ris_path)
        if not ris_path.exists():
            raise FileNotFoundError(f"RIS文件不存在: {ris_path}")

        self.records = []
        self.current_record = {}
        self.current_field = None
        self.current_value = []

        # 读取文件，支持多种编码
        for encoding in ['utf-8', 'utf-8-sig', 'latin-1', 'gbk']:
            try:
                with open(ris_path, 'r', encoding=encoding) as f:
                    lines = f.readlines()
                break
            except UnicodeDecodeError:
                continue
        else:
            raise UnicodeDecodeError(f"无法解码RIS文件: {ris_path}")

        # 解析每一行
        for line in lines:
            line = line.rstrip('\n\r')
            self._parse_line(line)

        # 处理最后一条记录
        if self.current_record:
            self._finalize_record()

        return self.records

    def _parse_line(self, line: str):
        """解析单行"""
        # 空行跳过
        if not line.strip():
            return

        # 检查是否是新字段（格式：XX  - value）
        match = re.match(r'^([A-Z][A-Z0-9])\s{2}-\s(.*)$', line)
        if match:
            field_code = match.group(1)
            field_value = match.group(2).strip()

            # 处理记录结束标记
            if field_code == 'ER':
                self._finalize_record()
                return

            # 保存当前字段值
            if self.current_field and self.current_value:
                self._save_field_value()

            # 开始新字段
            self.current_field = field_code
            self.current_value = [field_value] if field_value else []

        elif line.startswith('  ') or line.startswith('\t'):
            # 续行（以空格或Tab开头）
            if self.current_field:
                self.current_value.append(line.strip())

    def _save_field_value(self):
        """保存当前字段值到记录"""
        if not self.current_field or self.current_field not in self.FIELD_MAP:
            return

        field_name = self.FIELD_MAP[self.current_field]
        value = ' '.join(self.current_value).strip()

        if not value:
            return

        # 处理多值字段（如作者、关键词）
        if field_name in ['authors', 'keywords']:
            if field_name not in self.current_record:
                self.current_record[field_name] = []
            self.current_record[field_name].append(value)
        else:
            # 单值字段，如果已存在则追加
            if field_name in self.current_record:
                self.current_record[field_name] += ' ' + value
            else:
                self.current_record[field_name] = value

    def _finalize_record(self):
        """完成当前记录"""
        # 保存最后一个字段
        if self.current_field and self.current_value:
            self._save_field_value()

        # 如果有标题，添加到记录列表
        if 'title' in self.current_record and self.current_record['title']:
            # 处理年份
            if 'year' in self.current_record:
                year_str = self.current_record['year']
                match = re.search(r'(\d{4})', str(year_str))
                if match:
                    self.current_record['year'] = int(match.group(1))
                else:
                    self.current_record['year'] = 0
            else:
                self.current_record['year'] = 0

            # 处理作者列表
            if 'authors' in self.current_record:
                self.current_record['authors_str'] = '; '.join(
                    self.current_record['authors'])

            # 生成key（Author_Year格式）
            first_author = ''
            if 'authors' in self.current_record and self.current_record['authors']:
                first_author = self.current_record['authors'][0].split(',')[0].strip()
                # 清理作者名中的特殊字符
                first_author = re.sub(r'[^a-zA-Z\s-]', '', first_author).strip()

            year = self.current_record.get('year', 0)
            self.current_record['key'] = f"{first_author}_{year}"

            self.records.append(self.current_record)

        # 重置
        self.current_record = {}
        self.current_field = None
        self.current_value = []

    def get_statistics(self) -> dict:
        """获取解析统计"""
        if not self.records:
            return {'total': 0}

        years = [r.get('year', 0) for r in self.records if r.get('year', 0) > 0]
        has_abstract = sum(1 for r in self.records if r.get('abstract'))
        has_doi = sum(1 for r in self.records if r.get('doi'))

        return {
            'total': len(self.records),
            'has_abstract': has_abstract,
            'has_doi': has_doi,
            'year_min': min(years) if years else 0,
            'year_max': max(years) if years else 0,
            'year_median': sorted(years)[len(years) // 2] if years else 0,
        }


def import_ris_to_db(ris_path: str, db_path: str) -> dict:
    """
    导入RIS文件到数据库

    参数:
        ris_path: RIS文件路径
        db_path: 数据库路径

    返回:
        导入统计
    """
    # 解析RIS文件
    parser = RisParser()
    records = parser.parse(ris_path)

    # 连接数据库
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row

    # 确保papers表有abstract字段
    try:
        conn.execute("ALTER TABLE papers ADD COLUMN abstract TEXT")
    except sqlite3.OperationalError:
        pass  # 字段已存在

    try:
        conn.execute("ALTER TABLE papers ADD COLUMN source TEXT")
    except sqlite3.OperationalError:
        pass  # 字段已存在

    # 导入记录
    imported = 0
    skipped = 0
    for record in records:
        key = record.get('key', '')
        if not key:
            skipped += 1
            continue

        # 检查是否已存在
        existing = conn.execute(
            "SELECT key FROM papers WHERE key = ?", (key,)
        ).fetchone()

        if existing:
            skipped += 1
            continue

        # 插入新记录
        try:
            conn.execute("""
                INSERT INTO papers (key, author, year, title, doi, abstract, source, pdf_path)
                VALUES (?, ?, ?, ?, ?, ?, 'RIS', NULL)
            """, (
                key,
                record.get('authors_str', ''),
                record.get('year', 0),
                record.get('title', ''),
                record.get('doi'),
                record.get('abstract'),
            ))
            imported += 1
        except Exception as e:
            print(f"警告: 无法导入 {key}: {e}")
            skipped += 1

    conn.commit()

    # 更新progress表
    total = conn.execute("SELECT COUNT(*) FROM papers").fetchone()[0]
    conn.execute("""
        UPDATE progress SET
            total = ?,
            remaining = MAX(0, ? - processed),
            last_updated = datetime('now')
        WHERE id = 1
    """, (total, total))
    conn.commit()

    conn.close()

    return {
        'total_records': len(records),
        'imported': imported,
        'skipped': skipped,
        'total_in_db': total,
    }


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="RIS文件解析器")
    parser.add_argument("ris_file", help="RIS文件路径")
    parser.add_argument("--db", help="数据库路径（可选，不指定则只解析不导入）")
    parser.add_argument("--output", help="输出JSON文件路径（可选）")

    args = parser.parse_args()

    # 解析RIS文件
    ris_parser = RisParser()
    records = ris_parser.parse(args.ris_file)
    stats = ris_parser.get_statistics()

    print(f"=== RIS解析结果 ===")
    print(f"总记录数: {stats['total']}")
    print(f"有摘要: {stats['has_abstract']}")
    print(f"有DOI: {stats['has_doi']}")
    print(f"年份范围: {stats['year_min']} - {stats['year_max']}")

    # 如果指定了数据库，导入记录
    if args.db:
        print(f"\n=== 导入数据库 ===")
        result = import_ris_to_db(args.ris_file, args.db)
        print(f"导入: {result['imported']}")
        print(f"跳过: {result['skipped']}")
        print(f"数据库总数: {result['total_in_db']}")

    # 如果指定了输出文件，保存解析结果
    if args.output:
        with open(args.output, 'w', encoding='utf-8') as f:
            json.dump(records, f, ensure_ascii=False, indent=2)
        print(f"\n解析结果已保存到: {args.output}")
