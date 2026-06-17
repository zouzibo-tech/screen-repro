#!/usr/bin/env python3
"""
launch_screening.py — 脱离终端的筛选启动器
===========================================
作用：把 screen.py run 作为完全独立的后台进程启动，脱离当前终端。
即使关掉终端/断网/session超时，筛选也会继续跑直到全部完成。

用法：
    python launch_screening.py --base D:\Myworkshop\WorkBuddy\mate分析\03_Screening

进程启动后：
    - 立即返回，不阻塞
    - 日志写入 {项目目录}/screening.log
    - 进度可通过 `python screen.py --base <dir> check` 随时查看
    - 进程ID写入 {项目目录}/screening.pid，可用于手动终止
"""

import subprocess
import sys
import os
import io
import argparse
from pathlib import Path

if sys.platform == 'win32' and hasattr(sys.stdout, 'buffer'):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8')

SKILL_DIR = Path(__file__).parent
SCREEN_PY = SKILL_DIR / "screen.py"


def launch(base_dir: Path):
    """启动 screen.py run 为独立后台进程"""
    if not base_dir.exists():
        print(f"错误: 项目目录不存在: {base_dir}")
        sys.exit(1)

    db_path = base_dir / "screening.db"
    if not db_path.exists():
        print(f"错误: 未找到数据库: {db_path}")
        print("请先运行: python screen.py --base <dir> init")
        sys.exit(1)

    log_path = base_dir / "screening.log"
    pid_path = base_dir / "screening.pid"

    # 检查是否已有进程在运行
    if pid_path.exists():
        old_pid = pid_path.read_text().strip()
        # 检查进程是否还活着
        if sys.platform == 'win32':
            import ctypes
            kernel32 = ctypes.windll.kernel32
            handle = kernel32.OpenProcess(0x1000, False, int(old_pid))  # PROCESS_QUERY_LIMITED_INFORMATION
            if handle:
                kernel32.CloseHandle(handle)
                print(f"警告: 已有筛选进程在运行 (PID={old_pid})")
                print(f"如需重新启动，请先手动终止: taskkill /PID {old_pid}")
                print(f"或删除PID文件: {pid_path}")
                sys.exit(1)

    # 启动独立进程
    cmd = [
        sys.executable,
        str(SCREEN_PY),
        "--base", str(base_dir),
        "run",
    ]

    with open(log_path, "a", encoding="utf-8") as log_f:
        log_f.write(f"\n{'='*60}\n")
        log_f.write(f"[Launcher] 启动筛选进程 @ {__import__('datetime').datetime.now().isoformat()}\n")
        log_f.write(f"[Launcher] 项目目录: {base_dir}\n")
        log_f.write(f"{'='*60}\n")
        log_f.flush()

        if sys.platform == 'win32':
            # Windows: CREATE_NEW_PROCESS_GROUP 使进程脱离当前终端组
            # DETACHED_PROCESS 使进程脱离控制台
            proc = subprocess.Popen(
                cmd,
                stdout=log_f,
                stderr=subprocess.STDOUT,
                creationflags=subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.DETACHED_PROCESS,
                cwd=str(base_dir),
                env={**os.environ, "PYTHONIOENCODING": "utf-8", "PYTHONUTF8": "1"},
            )
        else:
            # Linux/macOS: 用 start_new_session 脱离终端
            proc = subprocess.Popen(
                cmd,
                stdout=log_f,
                stderr=subprocess.STDOUT,
                start_new_session=True,
                cwd=str(base_dir),
                env={**os.environ, "PYTHONIOENCODING": "utf-8"},
            )

    # 写入PID文件
    pid_path.write_text(str(proc.pid))

    print(f"筛选进程已启动")
    print(f"  PID: {proc.pid}")
    print(f"  日志: {log_path}")
    print(f"  进度查看: python screen.py --base \"{base_dir}\" check")
    print(f"  停止进程: taskkill /PID {proc.pid} /F")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="screen-repro 筛选进程启动器（脱离终端运行）"
    )
    parser.add_argument("--base", required=True, help="项目目录路径")
    args = parser.parse_args()

    launch(Path(args.base).resolve())
