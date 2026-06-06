#!/usr/bin/env python3
"""
pdf_extractor.py — screen-repro v2.3 PDF文本提取器（MinerU API优先 + PyMuPDF回退）

用于 screen-repro 筛选流程的子agent。
AI只需调用一条命令，Python负责选工具、提文本、查质量。

用法：
    python pdf_extractor.py {pdf_path} {output_path}

示例：
    python pdf_extractor.py 03_Screening/pdfs/Chen_2026.pdf 03_Screening/mining_output/Chen_2026_mining.md

输出：
    - 成功: mining_output/{Author}_{Year}_mining.md (Markdown) 或 .txt (纯文本)
    - 失败: 返回非0退出码，错误信息输出到stderr
"""

import sys
import os
import io
import json
import time
import requests

# Windows兼容性：强制UTF-8编码
if sys.platform == 'win32':
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8')

# ============ 配置 ============
MINERU_API = "https://mineru.net/api/v1/agent/parse/file"
POLL_INTERVAL = 5       # 轮询间隔（秒）
POLL_TIMEOUT = 120       # 超时（秒）
MAX_SIZE_MB = 10         # MinerU API文件大小限制
MAX_PAGES = 20           # MinerU API页数限制


def get_pdf_info(pdf_path: str) -> tuple[int, int, bool]:
    """获取PDF页数和大小，返回 (页数, 大小MB, 是否可用PyMuPDF)"""
    size_mb = os.path.getsize(pdf_path) / (1024 * 1024)
    try:
        import fitz
        doc = fitz.open(pdf_path)
        pages = len(doc)
        doc.close()
        return pages, size_mb, True
    except ImportError:
        # PyMuPDF不可用
        return 0, size_mb, False
    except Exception:
        return 0, size_mb, False


def can_use_mineru(pages: int, size_mb: float) -> bool:
    """判断是否满足MinerU API条件"""
    return pages <= MAX_PAGES and size_mb <= MAX_SIZE_MB


def extract_via_mineru(pdf_path: str, output_path: str) -> bool:
    """
    MinerU Agent API提取流程：
    1. 获取上传URL
    2. 上传PDF
    3. 轮询直到完成
    4. 下载Markdown
    """
    print(f"[MinerU] 开始API提取: {os.path.basename(pdf_path)}")

    # Step 1: 获取上传URL
    try:
        resp = requests.post(
            MINERU_API,
            json={
                "file_name": os.path.basename(pdf_path),
                "language": "en",
                "enable_table": False,
                "is_ocr": False,
                "enable_formula": False,
            },
            timeout=30,
        )
        if resp.status_code != 200:
            raise Exception(f"HTTP {resp.status_code}: {resp.text[:200]}")
        data = resp.json()
        task_id = data.get("task_id")
        file_url = data.get("file_url")
        if not task_id or not file_url:
            raise Exception(f"响应缺少task_id或file_url: {json.dumps(data)[:300]}")
        print(f"[MinerU] task_id={task_id}")
    except Exception as e:
        print(f"[MinerU] 获取上传URL失败: {e}", file=sys.stderr)
        return False

    # Step 2: 上传PDF
    try:
        with open(pdf_path, "rb") as f:
            put_resp = requests.put(file_url, data=f, timeout=60)
        if put_resp.status_code not in (200, 201, 204):
            raise Exception(f"上传失败 HTTP {put_resp.status_code}")
        print("[MinerU] 上传成功，等待解析...")
    except Exception as e:
        print(f"[MinerU] 上传失败: {e}", file=sys.stderr)
        return False

    # Step 3: 轮询结果
    poll_url = f"https://mineru.net/api/v1/agent/parse/{task_id}"
    elapsed = 0
    while elapsed < POLL_TIMEOUT:
        time.sleep(POLL_INTERVAL)
        elapsed += POLL_INTERVAL
        try:
            poll = requests.get(poll_url, timeout=10)
            if poll.status_code != 200:
                continue
            state = poll.json().get("state", "")
            if state == "done":
                print(f"[MinerU] 解析完成 ({elapsed}s)")
                break
            elif state in ("failed", "error"):
                raise Exception(f"解析失败: state={state}")
            print(f"[MinerU] 轮询中... state={state} ({elapsed}s)")
        except Exception as e:
            print(f"[MinerU] 轮询失败: {e}", file=sys.stderr)
            return False
    else:
        print(f"[MinerU] 超时 ({POLL_TIMEOUT}s)", file=sys.stderr)
        return False

    # Step 4: 下载Markdown
    try:
        markdown_url = poll.json().get("markdown_url", "")
        if not markdown_url:
            raise Exception("响应中无markdown_url")
        md_resp = requests.get(markdown_url, timeout=30)
        if md_resp.status_code != 200:
            raise Exception(f"下载失败 HTTP {md_resp.status_code}")
        with open(output_path, "w", encoding="utf-8") as f:
            f.write(md_resp.text)
        print(f"[MinerU] 输出: {output_path} ({len(md_resp.text)} 字符)")
        return True
    except Exception as e:
        print(f"[MinerU] 下载失败: {e}", file=sys.stderr)
        return False


def extract_via_pymupdf(pdf_path: str, output_path: str) -> bool:
    """PyMuPDF本地提取纯文本"""
    print(f"[PyMuPDF] 开始本地提取: {os.path.basename(pdf_path)}")
    try:
        import fitz
    except ImportError:
        print("[PyMuPDF] 未安装，尝试安装...", file=sys.stderr)
        os.system(f"{sys.executable} -m pip install PyMuPDF -q")
        import fitz

    try:
        doc = fitz.open(pdf_path)
        total_pages = len(doc)
        with open(output_path, "w", encoding="utf-8") as f:
            for i, page in enumerate(doc):
                text = page.get_text("text")
                f.write(text)
                if i < total_pages - 1:
                    f.write("\n---PAGE BREAK---\n")
        doc.close()
        size = os.path.getsize(output_path)
        print(f"[PyMuPDF] 输出: {output_path} (共{total_pages}页, {size/1024:.1f}KB)")
        return True
    except Exception as e:
        print(f"[PyMuPDF] 提取失败: {e}", file=sys.stderr)
        return False


def check_text_quality(text: str) -> float:
    """计算乱码率（0~1），>0.10标记异常"""
    if not text or len(text) < 100:
        return 1.0
    # 简单版：统计非ASCII+非中文字符的比例
    total = len(text)
    readable = sum(1 for c in text if c.isalpha() or c.isdigit() or c in " .,;:!?()[]{}-\n\r\t<>/@#$%^&*+=|~'\"" or '\u4e00' <= c <= '\u9fff')
    return 1.0 - readable / total


def main():
    if len(sys.argv) < 3:
        print("用法: python pdf_extractor.py <pdf_path> <output_path>", file=sys.stderr)
        sys.exit(1)

    pdf_path = sys.argv[1]
    output_path = sys.argv[2]

    # 确保输出目录存在
    os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)

    if not os.path.exists(pdf_path):
        print(f"PDF文件不存在: {pdf_path}", file=sys.stderr)
        sys.exit(1)

    # 获取PDF信息
    pages, size_mb, has_pymupdf = get_pdf_info(pdf_path)
    print(f"[PDF] {os.path.basename(pdf_path)}: {pages}页, {size_mb:.1f}MB")

    # 尝试MinerU
    extracted = False
    if can_use_mineru(pages, size_mb):
        extracted = extract_via_mineru(pdf_path, output_path)
        if extracted:
            # 质量检查
            try:
                with open(output_path, "r", encoding="utf-8") as f:
                    content = f.read()
                q = check_text_quality(content)
                print(f"[质检] 文本质量: 乱码率={q:.1%}")
                if q > 0.10:
                    print(f"[质检] ⚠️ 乱码率过高(>{10}%)，标记为提取异常", file=sys.stderr)
                    sys.exit(2)  # 退出码2 = 文本质量异常
                sys.exit(0)  # 退出码0 = 正常
            except Exception as e:
                print(f"[质检] 检查失败: {e}", file=sys.stderr)
    else:
        print(f"[跳过] MinerU限制: ≤{MAX_PAGES}页, ≤{MAX_SIZE_MB}MB (当前{size_mb:.1f}MB/{pages}页)")

    # 回退PyMuPDF
    if not extracted and has_pymupdf:
        extracted = extract_via_pymupdf(pdf_path, output_path)
        if extracted:
            # 质量检查（可选，PyMuPDF提取的通常质量较好）
            try:
                with open(output_path, "r", encoding="utf-8") as f:
                    content = f.read()
                q = check_text_quality(content)
                print(f"[质检] 文本质量: 乱码率={q:.1%}")
            except Exception:
                pass
            sys.exit(0)

    # 全部失败
    print("[错误] 所有提取方式均失败", file=sys.stderr)
    sys.exit(1)


if __name__ == "__main__":
    main()
