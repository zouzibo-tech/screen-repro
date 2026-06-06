#!/usr/bin/env python3
"""
ai_reviewer.py — AI复核模块
============================
用标题和摘要让AI判断文献是否为综述类文章
用于screen-repro v3.1的预筛选复核

用法:
    python ai_reviewer.py --db screening.db               # 复核所有文献
    python ai_reviewer.py --db screening.db --batch 10     # 复核10篇后暂停
"""

import json
import sys
import sqlite3
from pathlib import Path
from datetime import datetime

# 添加当前目录到path
sys.path.insert(0, str(Path(__file__).parent))
from picos_judge import OpenAIBackend, AnthropicBackend, OllamaBackend


class AIReviewer:
    """AI复核器 - 判断文献是否为综述类文章"""

    def __init__(self, config: dict):
        self.config = config
        self.backend = self._create_backend(config)

    def _create_backend(self, config: dict):
        """创建LLM后端"""
        backend_type = config.get("llm_backend", "openai")
        backends = {
            "openai": OpenAIBackend,
            "anthropic": AnthropicBackend,
            "ollama": OllamaBackend,
        }
        if backend_type not in backends:
            raise ValueError(f"未知后端: {backend_type}")

        backend_config = dict(config.get(backend_type, {}))
        backend_config["rate_limit"] = config.get("rate_limit", {})
        return backends[backend_type](backend_config)

    def review_paper(self, title: str, abstract: str = None) -> dict:
        """
        复核单篇文献

        参数:
            title: 文献标题
            abstract: 文献摘要（可选）

        返回:
            {
                "decision": "REVIEW/NOT_REVIEW/UNCERTAIN",
                "reason": "判断依据",
                "confidence": "high/medium/low"
            }
        """
        # 构建prompt
        prompt = self._build_prompt(title, abstract)
        system_prompt = self._build_system_prompt()

        try:
            # 调用AI
            response = self.backend.call(prompt, system_prompt)

            # 解析响应
            result = self._parse_response(response)
            return result

        except Exception as e:
            return {
                "decision": "UNCERTAIN",
                "reason": f"AI调用失败: {str(e)}",
                "confidence": "low"
            }

    def _build_system_prompt(self) -> str:
        """构建系统提示词"""
        return """你是一名系统综述筛选专家。你的任务是判断文献是否为综述类文章。

判断标准：
1. 系统综述（Systematic Review）：有明确的检索策略、纳入排除标准、质量评价
2. Meta分析（Meta-Analysis）：对多个研究进行定量合并分析
3. 叙述性综述（Narrative Review）：对某一主题进行总结性描述
4. 范围综述（Scoping Review）：对某一领域的研究范围进行梳理
5. 伞状综述（Umbrella Review）：对多个系统综述进行综合
6. 其他综述类型：综述、评论、展望等

注意：
- 只要标题或摘要明确表明是综述类文章，就判断为REVIEW
- 如果标题暗示是综述（如"where are we", "current state"），但摘要表明是原始研究，判断为NOT_REVIEW
- 如果无法确定，判断为UNCERTAIN"""

    def _build_prompt(self, title: str, abstract: str = None) -> str:
        """构建用户提示词"""
        prompt = f"""请判断以下文献是否为综述类文章。

文献信息：
- 标题：{title}
"""
        if abstract:
            # 截取摘要前2000字符
            abstract_truncated = abstract[:2000] if len(abstract) > 2000 else abstract
            prompt += f"- 摘要：{abstract_truncated}\n"

        prompt += """
请严格按照以下JSON格式输出，不要包含任何其他文字：

{
  "decision": "REVIEW 或 NOT_REVIEW 或 UNCERTAIN",
  "reason": "判断依据（简要说明）",
  "confidence": "high 或 medium 或 low"
}"""

        return prompt

    def _parse_response(self, response: str) -> dict:
        """解析AI响应"""
        try:
            # 尝试直接解析JSON
            result = json.loads(response)
        except json.JSONDecodeError:
            # 尝试提取JSON
            import re
            match = re.search(r'\{[\s\S]*\}', response)
            if match:
                try:
                    result = json.loads(match.group())
                except json.JSONDecodeError:
                    return {
                        "decision": "UNCERTAIN",
                        "reason": "无法解析AI响应",
                        "confidence": "low"
                    }
            else:
                return {
                    "decision": "UNCERTAIN",
                    "reason": "无法解析AI响应",
                    "confidence": "low"
                }

        # 验证字段
        decision = result.get("decision", "UNCERTAIN")
        if decision not in ["REVIEW", "NOT_REVIEW", "UNCERTAIN"]:
            decision = "UNCERTAIN"

        confidence = result.get("confidence", "low")
        if confidence not in ["high", "medium", "low"]:
            confidence = "low"

        return {
            "decision": decision,
            "reason": result.get("reason", ""),
            "confidence": confidence
        }


def review_papers_in_db(config: dict, db_path: str, batch_size: int = None) -> dict:
    """
    复核数据库中的文献

    参数:
        config: 配置文件
        db_path: 数据库路径
        batch_size: 批次大小（可选）

    返回:
        复核统计
    """
    # 连接数据库
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row

    # 创建复核器
    reviewer = AIReviewer(config)

    # 获取待复核的文献
    # 包括：1. 规则预筛选标记为EXCLUDE的 2. 未筛选的
    papers = conn.execute("""
        SELECT p.key, p.title, p.abstract, p.author, p.year,
               COALESCE(s.decision, 'PENDING') as current_decision
        FROM papers p
        LEFT JOIN screening s ON p.key = s.key
        WHERE s.key IS NULL 
           OR s.decision = 'EXCLUDE'
        ORDER BY p.key
    """).fetchall()

    print(f"待复核文献数: {len(papers)}")

    # 复核统计
    stats = {
        'total': len(papers),
        'reviewed': 0,
        'confirmed_exclude': 0,  # 规则EXCLUDE + AI REVIEW
        'false_positive': 0,     # 规则EXCLUDE + AI NOT_REVIEW（误杀）
        'missed_review': 0,      # 规则未匹配 + AI REVIEW（漏网）
        'confirmed_include': 0,  # 规则未匹配 + AI NOT_REVIEW
        'uncertain': 0,          # AI UNCERTAIN
        'errors': 0,
    }

    # 详细记录
    details = {
        'false_positives': [],  # 误杀列表
        'missed_reviews': [],   # 漏网列表
        'uncertains': [],       # 不确定列表
    }

    # 逐篇复核
    for i, paper in enumerate(papers):
        if batch_size and stats['reviewed'] >= batch_size:
            print(f"已达到批次限制 {batch_size}，暂停")
            break

        key = paper['key']
        title = paper['title']
        abstract = paper['abstract']
        current_decision = paper['current_decision']

        print(f"[{i+1}/{len(papers)}] {key}: {title[:50]}...")

        # AI复核
        result = reviewer.review_paper(title, abstract)
        ai_decision = result['decision']
        reason = result['reason']
        confidence = result['confidence']

        # 更新统计
        stats['reviewed'] += 1

        if current_decision == 'EXCLUDE':
            if ai_decision == 'REVIEW':
                stats['confirmed_exclude'] += 1
                print(f"  ✓ 确认排除: {reason}")
            elif ai_decision == 'NOT_REVIEW':
                stats['false_positive'] += 1
                details['false_positives'].append({
                    'key': key,
                    'title': title,
                    'abstract': abstract[:500] if abstract else '',
                    'author': paper['author'],
                    'year': paper['year'],
                    'ai_reason': reason,
                    'confidence': confidence,
                })
                print(f"  ⚠️ 误杀: {reason}")
            else:
                stats['uncertain'] += 1
                details['uncertains'].append({
                    'key': key,
                    'title': title,
                    'current_decision': current_decision,
                    'ai_reason': reason,
                    'confidence': confidence,
                })
                print(f"  ? 不确定: {reason}")
        else:
            if ai_decision == 'REVIEW':
                stats['missed_review'] += 1
                details['missed_reviews'].append({
                    'key': key,
                    'title': title,
                    'abstract': abstract[:500] if abstract else '',
                    'author': paper['author'],
                    'year': paper['year'],
                    'ai_reason': reason,
                    'confidence': confidence,
                })
                print(f"  ⚠️ 漏网: {reason}")
            elif ai_decision == 'NOT_REVIEW':
                stats['confirmed_include'] += 1
                print(f"  ✓ 确认纳入: {reason}")
            else:
                stats['uncertain'] += 1
                details['uncertains'].append({
                    'key': key,
                    'title': title,
                    'current_decision': current_decision,
                    'ai_reason': reason,
                    'confidence': confidence,
                })
                print(f"  ? 不确定: {reason}")

    conn.close()

    return {
        'stats': stats,
        'details': details,
    }


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="AI复核模块")
    parser.add_argument("--db", required=True, help="数据库路径")
    parser.add_argument("--config", default="config.json", help="配置文件路径")
    parser.add_argument("--batch", type=int, help="批次大小")

    args = parser.parse_args()

    # 加载配置
    config_path = Path(args.config)
    if not config_path.exists():
        print(f"错误: 配置文件不存在: {config_path}")
        sys.exit(1)

    with open(config_path, 'r', encoding='utf-8') as f:
        config = json.load(f)

    # 运行复核
    result = review_papers_in_db(config, args.db, args.batch)

    # 输出统计
    stats = result['stats']
    print(f"\n=== 复核统计 ===")
    print(f"总文献数: {stats['total']}")
    print(f"已复核: {stats['reviewed']}")
    print(f"确认排除: {stats['confirmed_exclude']}")
    print(f"误杀: {stats['false_positive']}")
    print(f"漏网: {stats['missed_review']}")
    print(f"确认纳入: {stats['confirmed_include']}")
    print(f"不确定: {stats['uncertain']}")
