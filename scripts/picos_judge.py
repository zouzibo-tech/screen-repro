#!/usr/bin/env python3
"""
picos_judge.py — screen-repro v3.0 PICOS AI判定模块
====================================================
职责: 接收PDF文本 + PICOS规则 → 调用LLM API → 返回结构化JSON。
关键设计: 独立可执行脚本，每次由screen.py通过subprocess.run()调用。
         每次调用都是全新进程，无共享状态，无上下文污染。

CLI接口:
    python picos_judge.py --config config.json --rules PICOS_RULES.md \
        --input /tmp/paper_text.txt \
        --meta '{"key":"Chen_2026","author":"Chen","year":"2026","title":"..."}'

输出: JSON到stdout（结构化判定结果）
退出码: 0=成功, 1=失败
"""

import json
import re
import sys
import time
import hashlib
import unicodedata
from abc import ABC, abstractmethod
from pathlib import Path


# ====== 常量 ======

PROMPT_VERSION = "v3.0.1"


# ====== 速率限制器 ======

class RateLimiter:
    """
    速率限制器 — 滑动窗口实现
    特性: RPM限制、TPM限制、安全边际、自动等待
    """

    def __init__(self, rpm: int = 60, tpm: int = 100000,
                 safety_margin: float = 0.8):
        self.rpm_limit = int(rpm * safety_margin)
        self.tpm_limit = int(tpm * safety_margin)
        self.requests = []  # (timestamp, input_tokens, output_tokens)

    def wait_if_needed(self, estimated_tokens: int = 2000):
        """请求前调用 — 如果需要等待则阻塞"""
        self._cleanup_old_records()

        # 检查RPM
        while len(self.requests) >= self.rpm_limit:
            oldest = self.requests[0]
            wait_time = 60 - (time.time() - oldest[0])
            if wait_time > 0:
                print(f"  [RateLimit] RPM限制，等待{wait_time:.1f}秒...",
                      file=sys.stderr)
                time.sleep(wait_time)
            self._cleanup_old_records()

        # 检查TPM
        current_tpm = sum(r[1] + r[2] for r in self.requests)
        while current_tpm + estimated_tokens > self.tpm_limit:
            oldest = self.requests[0]
            wait_time = 60 - (time.time() - oldest[0])
            if wait_time > 0:
                print(f"  [RateLimit] TPM限制，等待{wait_time:.1f}秒...",
                      file=sys.stderr)
                time.sleep(wait_time)
            self._cleanup_old_records()
            current_tpm = sum(r[1] + r[2] for r in self.requests)

    def record(self, input_tokens: int, output_tokens: int):
        """请求完成后调用 — 记录token使用量"""
        self.requests.append((time.time(), input_tokens, output_tokens))

    def _cleanup_old_records(self):
        """清理超过60秒的旧记录"""
        cutoff = time.time() - 60
        self.requests = [r for r in self.requests if r[0] >= cutoff]


# ====== LLM后端抽象 ======

class LLMBackend(ABC):
    """LLM后端基类"""

    @abstractmethod
    def call(self, user_prompt: str, system_prompt: str = "") -> str:
        """调用LLM，返回文本响应"""
        pass

    @property
    @abstractmethod
    def name(self) -> str:
        """后端名称"""
        pass


class OpenAIBackend(LLMBackend):
    """OpenAI API后端（兼容所有OpenAI格式API，包括MIMO）"""

    def __init__(self, config: dict):
        self.api_key = config["api_key"]
        self.model = config.get("model", "gpt-4o")
        self.base_url = config.get("base_url", "https://api.openai.com/v1")
        # 速率限制
        rate_config = config.get("rate_limit", {})
        self.rate_limiter = RateLimiter(
            rpm=config.get("rpm", rate_config.get("default_rpm", 60)),
            tpm=config.get("tpm", rate_config.get("default_tpm", 100000)),
            safety_margin=rate_config.get("safety_margin", 0.8),
        )

    @property
    def name(self):
        return f"openai/{self.model}"

    def call(self, user_prompt: str, system_prompt: str = "") -> str:
        """调用OpenAI API，支持system prompt分离，自动限速"""
        import httpx

        # 预估token数
        estimated_tokens = len(user_prompt + system_prompt) // 2
        self.rate_limiter.wait_if_needed(estimated_tokens)

        messages = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.append({"role": "user", "content": user_prompt})

        body = {
            "model": self.model,
            "messages": messages,
            "temperature": 0,
            "seed": 42,
            "response_format": {"type": "json_object"},
        }

        with httpx.Client(timeout=120) as client:
            resp = client.post(
                f"{self.base_url}/chat/completions",
                headers={"Authorization": f"Bearer {self.api_key}"},
                json=body,
            )

            # 处理429限流
            if resp.status_code == 429:
                retry_after = int(resp.headers.get("Retry-After", 60))
                print(f"  [RateLimit] 429响应，等待{retry_after}秒...",
                      file=sys.stderr)
                time.sleep(retry_after)
                return self.call(user_prompt, system_prompt)

            resp.raise_for_status()
            result = resp.json()

            # 记录实际token使用量
            usage = result.get("usage", {})
            self.rate_limiter.record(
                input_tokens=usage.get("prompt_tokens", 0),
                output_tokens=usage.get("completion_tokens", 0),
            )

            return result["choices"][0]["message"]["content"]


class AnthropicBackend(LLMBackend):
    """Anthropic Claude API后端"""

    def __init__(self, config: dict):
        self.api_key = config["api_key"]
        self.model = config.get("model", "claude-sonnet-4-20250514")
        # 速率限制
        rate_config = config.get("rate_limit", {})
        self.rate_limiter = RateLimiter(
            rpm=config.get("rpm", rate_config.get("default_rpm", 60)),
            tpm=config.get("tpm", rate_config.get("default_tpm", 80000)),
            safety_margin=rate_config.get("safety_margin", 0.8),
        )

    @property
    def name(self):
        return f"anthropic/{self.model}"

    def call(self, user_prompt: str, system_prompt: str = "") -> str:
        """调用Claude API，system prompt通过system参数传递"""
        import httpx

        # 预估token数
        estimated_tokens = len(user_prompt + system_prompt) // 2
        self.rate_limiter.wait_if_needed(estimated_tokens)

        body = {
            "model": self.model,
            "max_tokens": 4096,
            "temperature": 0,
            "messages": [{"role": "user", "content": user_prompt}],
        }
        if system_prompt:
            body["system"] = system_prompt

        with httpx.Client(timeout=120) as client:
            resp = client.post(
                "https://api.anthropic.com/v1/messages",
                headers={
                    "x-api-key": self.api_key,
                    "anthropic-version": "2023-06-01",
                },
                json=body,
            )

            # 处理429限流
            if resp.status_code == 429:
                retry_after = int(resp.headers.get("Retry-After", 60))
                print(f"  [RateLimit] 429响应，等待{retry_after}秒...",
                      file=sys.stderr)
                time.sleep(retry_after)
                return self.call(user_prompt, system_prompt)

            resp.raise_for_status()
            result = resp.json()

            # 记录token使用量
            usage = result.get("usage", {})
            self.rate_limiter.record(
                input_tokens=usage.get("input_tokens", 0),
                output_tokens=usage.get("output_tokens", 0),
            )

            return result["content"][0]["text"]


class OllamaBackend(LLMBackend):
    """Ollama本地模型后端"""

    def __init__(self, config: dict):
        self.model = config.get("model", "qwen2.5:72b")
        self.base_url = config.get("base_url", "http://localhost:11434")
        # 本地模型通常无限制
        self.rate_limiter = RateLimiter(rpm=9999, tpm=999999999)

    @property
    def name(self):
        return f"ollama/{self.model}"

    def call(self, user_prompt: str, system_prompt: str = "") -> str:
        """调用Ollama本地模型"""
        import httpx

        body = {
            "model": self.model,
            "prompt": user_prompt,
            "stream": False,
            "options": {
                "temperature": 0,
                "seed": 42,
            },
        }
        if system_prompt:
            body["system"] = system_prompt

        with httpx.Client(timeout=300) as client:
            resp = client.post(f"{self.base_url}/api/generate", json=body)
            resp.raise_for_status()
            return resp.json()["response"]


# ====== 异常定义 ======

class RateLimitError(Exception):
    pass


class JudgeError(Exception):
    pass


# ====== 文本处理 ======

def normalize_whitespace(text: str) -> str:
    """空白规范化 — 保证同一文本始终相同"""
    text = unicodedata.normalize("NFKC", text)
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r'[ \t]+', ' ', text)
    text = re.sub(r'\n{3,}', '\n\n', text)
    text = "\n".join(line.rstrip() for line in text.split("\n"))
    return text.strip()


def extract_key_sections(text: str) -> dict:
    """
    从全文中提取关键章节（Methods和Results优先）

    返回:
        {
            "full": 完整文本,
            "abstract": Abstract（最多1500字符）,
            "methods": Methods（最多4000字符）,
            "results": Results（最多3000字符）,
        }
    """
    sections = {"full": text, "abstract": "", "methods": "", "results": ""}

    # 提取Abstract
    abstract_match = re.search(
        r'(?:abstract|摘要)[:\s]*\n?(.*?)(?=\n(?:introduction|keywords|1\.|I\.))',
        text, re.IGNORECASE | re.DOTALL
    )
    if abstract_match:
        sections["abstract"] = abstract_match.group(1).strip()[:1500]

    # 提取Methods/Methodology
    methods_match = re.search(
        r'(?:methods?|methodology|materials?\s+(?:and|&)\s+methods?|实验方法|研究方法)'
        r'[:\s]*\n?(.*?)(?=\n(?:results?|findings?|discussion|conclusion|3\.|III\.))',
        text, re.IGNORECASE | re.DOTALL
    )
    if methods_match:
        sections["methods"] = methods_match.group(1).strip()[:4000]

    # 提取Results
    results_match = re.search(
        r'(?:results?|findings?)[:\s]*\n?(.*?)(?=\n(?:discussion|conclusion|4\.|IV\.))',
        text, re.IGNORECASE | re.DOTALL
    )
    if results_match:
        sections["results"] = results_match.group(1).strip()[:3000]

    return sections


# ====== 质量检查器 ======

class QualityChecker:
    """判定结果质量检查器"""

    def check(self, result: dict, text: str = "") -> list[str]:
        """
        检查判定结果质量
        返回问题列表（空列表=质量合格）
        """
        issues = []

        # 1. 结构完整性
        if "decision" not in result:
            issues.append("缺少decision字段")
        if "picos" not in result:
            issues.append("缺少picos字段")
            return issues

        # 2. 决策合法性
        if result.get("decision") not in ("INCLUDE", "EXCLUDE", "MAYBE"):
            issues.append(f"无效decision: {result.get('decision')}")

        # 3. EXCLUDE必须有排除码
        if result.get("decision") == "EXCLUDE":
            code = result.get("exclusion_code")
            if not code or code not in [f"E{i}" for i in range(1, 10)]:
                issues.append(f"EXCLUDE但排除码无效: {code}")

        # 4. 每个PICOS维度完整性
        for dim in ["P", "I", "C", "O", "S"]:
            dim_data = result.get("picos", {}).get(dim, {})
            if "result" not in dim_data:
                issues.append(f"{dim}维度缺少result")
            elif dim_data["result"] not in ("✅", "❌", "⚠️"):
                issues.append(f"{dim}维度result无效: {dim_data['result']}")
            if not dim_data.get("evidence"):
                issues.append(f"{dim}维度缺少evidence")
            if not dim_data.get("analysis"):
                issues.append(f"{dim}维度缺少analysis")

        # 5. 一致性检查
        picos = result.get("picos", {})
        has_fail = any(picos.get(d, {}).get("result") == "❌" for d in "PICOS")
        has_uncertain = any(picos.get(d, {}).get("result") == "⚠️" for d in "PICOS")
        all_pass = all(picos.get(d, {}).get("result") == "✅" for d in "PICOS")

        decision = result.get("decision")
        if all_pass and decision != "INCLUDE":
            issues.append(f"五维度全✅但decision={decision}（应为INCLUDE）")
        if has_fail and decision != "EXCLUDE":
            issues.append(f"存在❌但decision={decision}（应为EXCLUDE）")
        if has_uncertain and not has_fail and decision == "INCLUDE":
            issues.append(f"存在⚠️但decision=INCLUDE（应为MAYBE）")

        # 6. evidence是否引用了原文
        for dim in "PICOS":
            evidence_list = picos.get(dim, {}).get("evidence", [])
            for ev in evidence_list:
                if ev and len(ev) < 10:
                    issues.append(f"{dim}维度evidence过短: '{ev}'")

        return issues


# ====== PICOS判定器 ======

class PicosJudge:
    """
    PICOS判定器 — 可插拔LLM后端

    特性:
    1. 智能截取：优先保留Methods/Results章节
    2. System/User分离：提高LLM遵循度
    3. 分步判定流程：引导LLM逐项检查
    4. 质量检查：自动验证判定结果
    5. 可复现性：temperature=0 + seed=42
    """

    def __init__(self, config: dict):
        self.config = config
        self.backend = self._create_backend(config)
        self.picos_rules = self._load_rules(config)
        self.max_text = config.get("max_text_length", 8000)
        self.max_retries = 3
        self.checker = QualityChecker()

    def _create_backend(self, config: dict) -> LLMBackend:
        """根据配置创建LLM后端"""
        backend_type = config.get("llm_backend", "openai")
        backends = {
            "openai": OpenAIBackend,
            "anthropic": AnthropicBackend,
            "ollama": OllamaBackend,
        }
        if backend_type not in backends:
            raise ValueError(f"未知后端: {backend_type}，可选: {list(backends.keys())}")

        # 合并rate_limit配置到后端配置
        backend_config = dict(config.get(backend_type, {}))
        backend_config["rate_limit"] = config.get("rate_limit", {})
        return backends[backend_type](backend_config)

    def _load_rules(self, config: dict) -> str:
        """加载PICOS规则"""
        rules_path = config.get("picos_rules_path", "PICOS_RULES.md")
        p = Path(rules_path)
        if not p.is_absolute():
            p = Path.cwd() / p
        if p.exists():
            return p.read_text(encoding="utf-8")
        return "（未找到PICOS_RULES.md，请配置picos_rules_path）"

    def judge(self, text: str, paper_meta: dict) -> dict:
        """
        PICOS判定

        输入:
            text: PDF提取的全文文本
            paper_meta: {"key", "author", "year", "title"}

        输出:
            {
                "key": "...",
                "decision": "INCLUDE/EXCLUDE/MAYBE",
                "exclusion_code": "E1-E9" or null,
                "picos": { P: {...}, I: {...}, C: {...}, O: {...}, S: {...} },
                "reason": "...",
                "text_quality": "正常/异常",
                "fingerprint": "...",
                "model_version": "...",
                "prompt_hash": "...",
                "extraction_method": "...",
                "temperature": 0,
                "seed": 42,
            }
        """
        system_prompt = self._build_system_prompt()
        user_prompt = self._build_prompt(text, paper_meta)
        prompt_hash = self._compute_prompt_hash(user_prompt)

        for attempt in range(self.max_retries):
            try:
                response = self.backend.call(user_prompt, system_prompt)
                result = self._parse_response(response)

                # 质量检查
                issues = self.checker.check(result, text)
                if issues:
                    print(f"  [Judge] 质量问题: {', '.join(issues)}",
                          file=sys.stderr)
                    # 如果有严重问题，重试
                    if any("缺少" in i or "无效" in i for i in issues):
                        continue

                # 填充元数据
                result["key"] = paper_meta.get("key",
                    f"{paper_meta.get('author', 'unknown')}_{paper_meta.get('year', '0')}")
                result["text_quality"] = "正常"

                # 可复现性字段
                text_hash = hashlib.md5(text.encode()).hexdigest()[:16]
                result["fingerprint"] = self._compute_fingerprint(
                    text, prompt_hash)
                result["model_version"] = self.backend.name
                result["prompt_hash"] = prompt_hash
                result["extraction_method"] = self.config.get(
                    "pdf_extractor", "pymupdf")
                result["temperature"] = 0
                result["seed"] = 42
                result["text_hash"] = text_hash

                return result

            except RateLimitError:
                wait = 30 * (2 ** attempt)
                print(f"  [Judge] 429限流，等待{wait}秒 "
                      f"({attempt+1}/{self.max_retries})", file=sys.stderr)
                time.sleep(wait)
            except json.JSONDecodeError:
                # 尝试修复JSON
                try:
                    repaired = self._repair_json(response)
                    result = self._parse_response(repaired)
                    result["key"] = paper_meta.get("key",
                        f"{paper_meta.get('author', 'unknown')}_{paper_meta.get('year', '0')}")
                    result["text_quality"] = "正常(JSON已修复)"
                    return result
                except Exception:
                    print(f"  [Judge] JSON解析失败 "
                          f"({attempt+1}/{self.max_retries})", file=sys.stderr)
            except Exception as e:
                print(f"  [Judge] 调用异常: {e} "
                      f"({attempt+1}/{self.max_retries})", file=sys.stderr)
                if attempt < self.max_retries - 1:
                    time.sleep(5)

        # 全部失败 → MAYBE
        return self.make_maybe(paper_meta,
            f"AI判定失败（{self.max_retries}次重试后放弃）")

    def make_maybe(self, paper_meta: dict, reason: str) -> dict:
        """生成MAYBE结果"""
        return {
            "key": paper_meta.get("key",
                f"{paper_meta.get('author', 'unknown')}_{paper_meta.get('year', '0')}"),
            "decision": "MAYBE",
            "exclusion_code": None,
            "picos": {
                dim: {"result": "⚠️", "evidence": [], "analysis": reason}
                for dim in ["P", "I", "C", "O", "S"]
            },
            "reason": reason,
            "text_quality": "N/A",
            "fingerprint": None,
            "model_version": self.backend.name if self.backend else "unknown",
            "prompt_hash": None,
            "extraction_method": None,
            "temperature": 0,
            "seed": 42,
            "text_hash": None,
        }

    def _build_system_prompt(self) -> str:
        """系统提示词 — 定义角色和行为规范"""
        return """你是一名系统综述文献筛选专家。你的任务是根据PICOS标准判定文献是否纳入。

## 行为规范
1. 逐项检查P、I、C、O、S五个维度，不要跳过任何维度
2. 每个维度的判定必须引用原文证据（标注具体段落或页码）
3. 如果信息不足以判定某个维度，用⚠️标记并说明缺少什么信息
4. 最终决策基于五个维度的综合判断
5. 排除码按E1→E9顺序检查，标第一个命中的
6. 保持客观，不要推测原文未明确说明的内容"""

    def _build_prompt(self, text: str, paper_meta: dict) -> str:
        """用户提示词 — 包含PICOS规则、文献内容和输出要求"""

        # 智能截断：优先保留Methods/Results部分
        sections = extract_key_sections(text)
        if sections["methods"]:
            content = sections["methods"]
            if sections["results"]:
                content += "\n\n" + sections["results"]
            if len(content) < 2000 and sections["full"]:
                content = sections["full"][:self.max_text]
        else:
            content = text[:self.max_text]

        return f"""<!-- prompt_version: {PROMPT_VERSION} -->
## PICOS筛选规则

{self.picos_rules}

## 待筛选文献元数据
- 标题: {paper_meta.get('title', '未知')}
- 作者: {paper_meta.get('author', '未知')}
- 年份: {paper_meta.get('year', '未知')}

## 文献内容（PDF提取，已智能截取关键章节）
{content}

## 判定流程

请按以下顺序逐项判定：

### 第1步：P（人群）
- 参与者是否为高等教育阶段学习者或培训学员？
- 如果无法确定 → ⚠️

### 第2步：I（干预）
- 是否使用VR模拟器？设备类型是什么？
- HMD_VR: Oculus/HTC Vive/Meta Quest/Pico等头戴设备
- Desktop_VR: LapSim/LapMentor/EyeSi/FLS/da Vinci等桌面模拟器
- 如果仅说"VR"未说明设备 → ⚠️，设备类型标注"需确认"

### 第3步：C（对照）
- 是否有非VR对照组？
- 仅比较VR内部条件 → ❌

### 第4步：O（结局）
- 是否报告了技能保持（retention，延迟≥7天）或技能迁移（transfer）？
- 仅有即时后测 → ❌
- 如果报告了但延迟<7天 → ⚠️

### 第5步：S（研究设计）
- 是否为RCT或准实验设计？
- 单组前后测 → ❌

### 第6步：综合决策
- 五个维度全部✅ → INCLUDE
- 任一维度❌ → EXCLUDE（标排除码E1-E9）
- 存在⚠️但无❌ → MAYBE

## 输出要求

输出严格的JSON格式，不要包含任何其他文字或markdown标记：

{{
  "decision": "INCLUDE 或 EXCLUDE 或 MAYBE",
  "exclusion_code": "E1-E9（仅EXCLUDE时填写，其他为null）",
  "picos": {{
    "P": {{
      "result": "✅ 或 ❌ 或 ⚠️",
      "evidence": ["原文引用1", "原文引用2"],
      "analysis": "简要分析"
    }},
    "I": {{
      "result": "✅ 或 ❌ 或 ⚠️",
      "device_type": "HMD_VR 或 Desktop_VR 或 非VR 或 ⚠️需确认",
      "evidence": ["原文引用"],
      "analysis": "简要分析"
    }},
    "C": {{
      "result": "✅ 或 ❌ 或 ⚠️",
      "evidence": ["原文引用"],
      "analysis": "简要分析"
    }},
    "O": {{
      "result": "✅ 或 ❌ 或 ⚠️",
      "outcome_type": "Retention 或 Transfer 或 Both 或 无",
      "retention_weeks": null,
      "evidence": ["原文引用"],
      "analysis": "简要分析"
    }},
    "S": {{
      "result": "✅ 或 ❌ 或 ⚠️",
      "design_type": "RCT 或 准实验 或 其他",
      "evidence": ["原文引用"],
      "analysis": "简要分析"
    }}
  }},
  "reason": "一句话总结判定理由"
}}"""

    def _compute_prompt_hash(self, prompt: str) -> str:
        """计算prompt模板的hash"""
        return hashlib.md5(prompt.encode()).hexdigest()[:8]

    def _compute_fingerprint(self, text: str, prompt_hash: str) -> str:
        """
        计算五维指纹 — 任一维度变化则缓存失效

        五维:
        1. text_hash: PDF提取文本的hash
        2. rules_hash: PICOS规则的hash
        3. model: 精确模型版本字符串
        4. prompt_hash: prompt模板的hash
        5. extraction_method: PDF提取方法
        """
        rules_hash = hashlib.md5(
            self.picos_rules.encode()).hexdigest()[:8]
        extraction_method = self.config.get("pdf_extractor", "pymupdf")
        text_hash = hashlib.md5(text.encode()).hexdigest()[:16]

        combined = "|".join([
            text_hash,
            rules_hash,
            self.backend.name,
            prompt_hash,
            extraction_method,
        ])
        return hashlib.md5(combined.encode()).hexdigest()[:16]

    def _parse_response(self, response: str) -> dict:
        """解析LLM响应为结构化dict"""
        try:
            data = json.loads(response)
        except json.JSONDecodeError:
            match = re.search(r'\{[\s\S]*\}', response)
            if match:
                data = json.loads(match.group())
            else:
                raise json.JSONDecodeError(
                    "无法从响应中提取JSON", response, 0)

        # 验证必需字段
        if "decision" not in data:
            raise ValueError("缺少decision字段")
        if data["decision"] not in ("INCLUDE", "EXCLUDE", "MAYBE"):
            raise ValueError(f"无效decision: {data['decision']}")
        if "picos" not in data:
            raise ValueError("缺少picos字段")

        # 填充默认值
        data.setdefault("exclusion_code", None)
        data.setdefault("reason", "")
        for dim in ["P", "I", "C", "O", "S"]:
            data["picos"].setdefault(dim, {
                "result": "⚠️", "evidence": [], "analysis": "未判定"})
            data["picos"][dim].setdefault("evidence", [])
            data["picos"][dim].setdefault("analysis", "")

        return data

    def _repair_json(self, text: str) -> str:
        """尝试修复LLM输出的JSON"""
        try:
            from json_repair import repair_json
            return repair_json(text)
        except ImportError:
            match = re.search(r'\{[\s\S]*\}', text)
            if match:
                return match.group()
            raise


# ====== CLI入口（独立进程调用时执行）======

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="PICOS AI判定（独立进程）")
    parser.add_argument("--config", required=True, help="config.json路径")
    parser.add_argument("--rules", required=True, help="PICOS_RULES.md路径")
    parser.add_argument("--input", required=True, help="文献文本文件路径")
    parser.add_argument("--meta", required=True, help="文献元数据JSON字符串")
    args = parser.parse_args()

    try:
        # 加载配置
        with open(args.config, encoding="utf-8") as f:
            config = json.load(f)
        config["picos_rules_path"] = args.rules

        # 加载文本
        with open(args.input, encoding="utf-8") as f:
            text = f.read()

        # 文本规范化
        text = normalize_whitespace(text)

        # 解析元数据
        paper_meta = json.loads(args.meta)

        # 执行判定
        judge = PicosJudge(config)
        result = judge.judge(text, paper_meta)

        # 输出JSON到stdout
        print(json.dumps(result, ensure_ascii=False, indent=2))
        sys.exit(0)

    except Exception as e:
        print(f"ERROR: {e}", file=sys.stderr)
        sys.exit(1)
