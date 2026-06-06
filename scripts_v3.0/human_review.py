#!/usr/bin/env python3
"""
human_review.py — 人机协同交互模块
===================================
在预筛选验证报告环节停顿，让人复核误杀和漏网文献，修正规则
用于screen-repro v3.1的人机协同流程

用法:
    python human_review.py --db screening.db --review-result review_result.json
"""

import json
import sys
import sqlite3
from pathlib import Path
from datetime import datetime


class HumanReviewer:
    """人机协同复核器"""

    def __init__(self, db_path: str):
        self.db_path = db_path
        self.conn = sqlite3.connect(db_path)
        self.conn.row_factory = sqlite3.Row

    def generate_report(self, review_result: dict) -> str:
        """
        生成验证报告

        参数:
            review_result: AI复核结果

        返回:
            格式化的报告字符串
        """
        stats = review_result['stats']
        details = review_result['details']

        report = []
        report.append("=" * 70)
        report.append("预筛选规则验证报告")
        report.append("=" * 70)
        report.append("")

        # 总体统计
        report.append("【总体统计】")
        report.append(f"总文献数：{stats['total']}篇")
        report.append(f"已复核：{stats['reviewed']}篇")
        report.append("")

        report.append("【AI复核结果】")
        report.append(f"• 确认排除（规则正确）：{stats['confirmed_exclude']}篇")
        report.append(f"• 确认纳入（规则正确）：{stats['confirmed_include']}篇")
        report.append(f"• ⚠️ 误杀（规则错误）：{stats['false_positive']}篇")
        report.append(f"• ⚠️ 漏网（规则遗漏）：{stats['missed_review']}篇")
        report.append(f"• 不确定（需人工判断）：{stats['uncertain']}篇")
        report.append("")

        # 计算准确率
        total_rule_decisions = stats['confirmed_exclude'] + stats['false_positive']
        if total_rule_decisions > 0:
            accuracy = stats['confirmed_exclude'] / total_rule_decisions * 100
            report.append("【规则准确性】")
            report.append(f"• 准确率：{accuracy:.1f}% ({stats['confirmed_exclude']}/{total_rule_decisions})")
            report.append(f"• 误杀率：{100-accuracy:.1f}% ({stats['false_positive']}/{total_rule_decisions})")
            report.append("")

        # 误杀文献详情
        if details['false_positives']:
            report.append("=" * 70)
            report.append("【误杀文献】规则标记EXCLUDE但AI判断不是综述")
            report.append("=" * 70)
            report.append("")

            for i, paper in enumerate(details['false_positives'], 1):
                report.append(f"{i}. {paper['key']}")
                report.append(f"   标题：{paper['title']}")
                report.append(f"   作者：{paper['author']}")
                report.append(f"   年份：{paper['year']}")
                report.append(f"   AI判断：NOT_REVIEW (confidence: {paper['confidence']})")
                report.append(f"   原因：{paper['ai_reason']}")

                # 获取文件位置信息
                location = self._get_paper_location(paper['key'])
                report.append(f"   📁 文件位置：")
                report.append(f"   - 数据库：papers表 key={paper['key']}")
                if location.get('pdf_path'):
                    report.append(f"   - PDF文件：{location['pdf_path']}")
                else:
                    report.append(f"   - PDF文件：未找到PDF文件")
                if location.get('doi'):
                    report.append(f"   - DOI：{location['doi']}")
                report.append("")

        # 漏网文献详情
        if details['missed_reviews']:
            report.append("=" * 70)
            report.append("【漏网文献】规则未匹配但AI判断是综述")
            report.append("=" * 70)
            report.append("")

            for i, paper in enumerate(details['missed_reviews'], 1):
                report.append(f"{i}. {paper['key']}")
                report.append(f"   标题：{paper['title']}")
                report.append(f"   作者：{paper['author']}")
                report.append(f"   年份：{paper['year']}")
                report.append(f"   AI判断：REVIEW (confidence: {paper['confidence']})")
                report.append(f"   原因：{paper['ai_reason']}")

                # 获取文件位置信息
                location = self._get_paper_location(paper['key'])
                report.append(f"   📁 文件位置：")
                report.append(f"   - 数据库：papers表 key={paper['key']}")
                if location.get('pdf_path'):
                    report.append(f"   - PDF文件：{location['pdf_path']}")
                else:
                    report.append(f"   - PDF文件：未找到PDF文件")
                if location.get('doi'):
                    report.append(f"   - DOI：{location['doi']}")
                report.append("")

        # 不确定文献详情
        if details['uncertains']:
            report.append("=" * 70)
            report.append("【不确定文献】AI无法确定是否为综述")
            report.append("=" * 70)
            report.append("")

            for i, paper in enumerate(details['uncertains'], 1):
                report.append(f"{i}. {paper['key']}")
                report.append(f"   标题：{paper['title']}")
                report.append(f"   当前决策：{paper['current_decision']}")
                report.append(f"   AI判断：UNCERTAIN (confidence: {paper['confidence']})")
                report.append(f"   原因：{paper['ai_reason']}")

                # 获取文件位置信息
                location = self._get_paper_location(paper['key'])
                report.append(f"   📁 文件位置：")
                report.append(f"   - 数据库：papers表 key={paper['key']}")
                if location.get('pdf_path'):
                    report.append(f"   - PDF文件：{location['pdf_path']}")
                else:
                    report.append(f"   - PDF文件：未找到PDF文件")
                report.append("")

        # 操作建议
        report.append("=" * 70)
        report.append("【操作建议】")
        report.append("=" * 70)
        report.append("")

        if details['false_positives']:
            report.append("1. 误杀文献处理：")
            report.append(f"   • 将 {len(details['false_positives'])} 篇误杀文献移出EXCLUDE")
            report.append("   • 更新预筛选规则，避免类似误杀")
            report.append("")

        if details['missed_reviews']:
            report.append("2. 漏网文献处理：")
            report.append(f"   • 将 {len(details['missed_reviews'])} 篇漏网文献标记为EXCLUDE")
            report.append("   • 更新预筛选规则，捕获类似漏网")
            report.append("")

        if details['uncertains']:
            report.append("3. 不确定文献处理：")
            report.append(f"   • 人工复核 {len(details['uncertains'])} 篇不确定文献")
            report.append("")

        return "\n".join(report)

    def _get_paper_location(self, key: str) -> dict:
        """获取文献的文件位置信息"""
        row = self.conn.execute("""
            SELECT key, title, author, year, doi, pdf_path, abstract
            FROM papers WHERE key = ?
        """, (key,)).fetchone()

        if not row:
            return {}

        return {
            'key': row['key'],
            'title': row['title'],
            'author': row['author'],
            'year': row['year'],
            'doi': row['doi'],
            'pdf_path': row['pdf_path'],
            'has_abstract': bool(row['abstract']),
        }

    def interactive_review(self, review_result: dict):
        """
        交互式复核

        参数:
            review_result: AI复核结果
        """
        # 生成报告
        report = self.generate_report(review_result)
        print(report)

        # 交互循环
        while True:
            print("\n" + "=" * 70)
            print("【操作选项】")
            print("=" * 70)
            print("[A] 接受当前结果，继续下一步")
            print("[B] 修正误杀文献（移出EXCLUDE）")
            print("[C] 修正漏网文献（标记为EXCLUDE）")
            print("[D] 查看特定文献详情")
            print("[E] 导出验证报告")
            print("[Q] 退出")
            print("")

            choice = input("请选择操作：").strip().upper()

            if choice == 'A':
                print("\n接受当前结果，继续下一步...")
                return {
                    'action': 'accept',
                    'review_result': review_result
                }

            elif choice == 'B':
                result = self._fix_false_positives(review_result['details']['false_positives'])
                if result:
                    return result

            elif choice == 'C':
                result = self._fix_missed_reviews(review_result['details']['missed_reviews'])
                if result:
                    return result

            elif choice == 'D':
                self._view_paper_details()

            elif choice == 'E':
                self._export_report(report)

            elif choice == 'Q':
                print("\n退出复核...")
                return {'action': 'quit'}

            else:
                print("\n无效选择，请重新输入")

    def _fix_false_positives(self, false_positives: list) -> dict:
        """修正误杀文献"""
        if not false_positives:
            print("\n没有误杀文献需要修正")
            return None

        print(f"\n=== 修正误杀文献 ({len(false_positives)}篇) ===")
        print("以下文献被规则误判为综述，但AI判断不是综述：")
        print("")

        for i, paper in enumerate(false_positives, 1):
            print(f"{i}. {paper['key']}: {paper['title'][:50]}...")
            print(f"   AI判断：NOT_REVIEW ({paper['confidence']})")
            print(f"   原因：{paper['ai_reason']}")
            print("")

        confirm = input("是否将这些文献移出EXCLUDE？[Y/N]: ").strip().upper()
        if confirm != 'Y':
            return None

        # 执行修正
        fixed = 0
        for paper in false_positives:
            try:
                # 删除screening表中的记录
                self.conn.execute("""
                    DELETE FROM screening WHERE key = ? AND decision = 'EXCLUDE'
                """, (paper['key'],))
                fixed += 1
            except Exception as e:
                print(f"警告: 无法修正 {paper['key']}: {e}")

        self.conn.commit()
        print(f"\n已将 {fixed} 篇误杀文献移出EXCLUDE")

        return {
            'action': 'fixed_false_positives',
            'fixed_count': fixed,
            'review_result': None  # 需要重新运行复核
        }

    def _fix_missed_reviews(self, missed_reviews: list) -> dict:
        """修正漏网文献"""
        if not missed_reviews:
            print("\n没有漏网文献需要修正")
            return None

        print(f"\n=== 修正漏网文献 ({len(missed_reviews)}篇) ===")
        print("以下文献未被规则匹配，但AI判断是综述：")
        print("")

        for i, paper in enumerate(missed_reviews, 1):
            print(f"{i}. {paper['key']}: {paper['title'][:50]}...")
            print(f"   AI判断：REVIEW ({paper['confidence']})")
            print(f"   原因：{paper['ai_reason']}")
            print("")

        confirm = input("是否将这些文献标记为EXCLUDE？[Y/N]: ").strip().upper()
        if confirm != 'Y':
            return None

        # 执行修正
        fixed = 0
        for paper in missed_reviews:
            try:
                # 插入或更新screening表
                self.conn.execute("""
                    INSERT OR REPLACE INTO screening 
                    (key, decision, exclusion_code, reason, screened_at)
                    VALUES (?, 'EXCLUDE', 'E1', ?, datetime('now'))
                """, (paper['key'], f"AI复核确认：{paper['ai_reason']}"))
                fixed += 1
            except Exception as e:
                print(f"警告: 无法修正 {paper['key']}: {e}")

        self.conn.commit()
        print(f"\n已将 {fixed} 篇漏网文献标记为EXCLUDE")

        return {
            'action': 'fixed_missed_reviews',
            'fixed_count': fixed,
            'review_result': None  # 需要重新运行复核
        }

    def _view_paper_details(self):
        """查看特定文献详情"""
        print("\n=== 查看文献详情 ===")
        key = input("请输入文献Key：").strip()

        location = self._get_paper_location(key)
        if not location:
            print(f"未找到文献：{key}")
            return

        print(f"\n文献详情：{key}")
        print(f"标题：{location.get('title', '未知')}")
        print(f"作者：{location.get('author', '未知')}")
        print(f"年份：{location.get('year', '未知')}")
        print(f"DOI：{location.get('doi', '无')}")
        print(f"PDF文件：{location.get('pdf_path', '未找到')}")
        print(f"有摘要：{'是' if location.get('has_abstract') else '否'}")

    def _export_report(self, report: str):
        """导出报告"""
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = f"prescreen_report_{timestamp}.txt"

        with open(filename, 'w', encoding='utf-8') as f:
            f.write(report)

        print(f"\n报告已导出到：{filename}")

    def close(self):
        """关闭数据库连接"""
        self.conn.close()


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="人机协同复核模块")
    parser.add_argument("--db", required=True, help="数据库路径")
    parser.add_argument("--review-result", required=True, help="AI复核结果JSON文件")

    args = parser.parse_args()

    # 加载AI复核结果
    with open(args.review_result, 'r', encoding='utf-8') as f:
        review_result = json.load(f)

    # 运行人机协同复核
    reviewer = HumanReviewer(args.db)
    result = reviewer.interactive_review(review_result)
    reviewer.close()

    # 输出结果
    print(f"\n操作结果：{result.get('action', 'unknown')}")
