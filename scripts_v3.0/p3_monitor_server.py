#!/usr/bin/env python3
"""Portable P3 runtime monitor server.

Design goals:
- Reusable outside WorkBuddy: only Python standard library is required.
- Project data stays inside the target project: 03_Screening/qc/runtime/.
- The monitor serves the reusable HTML from this script directory.
- One monitor instance per project is enforced by a cross-platform lock file.
- One resume worker is enforced by a project-local worker lock plus best-effort process scan.
- The monitor itself does not modify screening.db, lock YAML, PDFs, txt files, or screening records.
"""
from __future__ import annotations

import argparse
import contextlib
import hashlib
import json
import os
import platform
import socket
import subprocess
import sys
import threading
import time
import webbrowser
from datetime import datetime
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

SKILL_SCRIPT_DIR = Path(__file__).resolve().parent
STATUS_SCRIPT = SKILL_SCRIPT_DIR / "p3_runtime_status.py"
MONITOR_HTML = SKILL_SCRIPT_DIR / "p3_monitor.html"

PROJECT = Path.cwd().resolve()
P3 = PROJECT / "03_Screening"
RUN_SCRIPT = P3 / "qc" / "full_reaudit" / "run_dual_model_disputed_review_resume_20260706.py"
STAGE_DIR = P3 / "qc" / "full_reaudit" / "dual_model_disputed_review"
CHECKPOINT_DIR = STAGE_DIR / "checkpoints"
RUNTIME_DIR = P3 / "qc" / "runtime"
SERVER_STATUS = RUNTIME_DIR / "p3_monitor_server.json"
SERVER_LOG = RUNTIME_DIR / "p3_monitor_server.log"
SUPERVISOR_EVENTS = RUNTIME_DIR / "p3_supervisor_events.jsonl"
SUPERVISOR_STATE = RUNTIME_DIR / "p3_supervisor_state.json"
MONITOR_LOCK = RUNTIME_DIR / "p3_monitor_server.lock"
RESUME_LOCK = RUNTIME_DIR / "p3_resume_worker.lock"
AUTO_RESUME_LOG = RUNTIME_DIR / "p3_auto_resume_worker.log"
MANUAL_RESUME_LOG = RUNTIME_DIR / "p3_manual_resume_worker.log"

MANUAL_CHILD: subprocess.Popen[str] | None = None
MANUAL_CHILD_LOCK = threading.Lock()
MONITOR_LOCK_HANDLE: Any = None
RESUME_LOCK_HANDLE: Any = None


def configure_project(project: Path) -> None:
    """Configure project-local runtime paths."""
    global PROJECT, P3, RUN_SCRIPT, STAGE_DIR, CHECKPOINT_DIR, RUNTIME_DIR, SERVER_STATUS, SERVER_LOG
    global SUPERVISOR_EVENTS, SUPERVISOR_STATE, MONITOR_LOCK, RESUME_LOCK, AUTO_RESUME_LOG, MANUAL_RESUME_LOG
    PROJECT = project.resolve()
    P3 = PROJECT / "03_Screening"
    RUN_SCRIPT = P3 / "qc" / "full_reaudit" / "run_dual_model_disputed_review_resume_20260706.py"
    STAGE_DIR = P3 / "qc" / "full_reaudit" / "dual_model_disputed_review"
    CHECKPOINT_DIR = STAGE_DIR / "checkpoints"
    RUNTIME_DIR = P3 / "qc" / "runtime"
    SERVER_STATUS = RUNTIME_DIR / "p3_monitor_server.json"
    SERVER_LOG = RUNTIME_DIR / "p3_monitor_server.log"
    SUPERVISOR_EVENTS = RUNTIME_DIR / "p3_supervisor_events.jsonl"
    SUPERVISOR_STATE = RUNTIME_DIR / "p3_supervisor_state.json"
    MONITOR_LOCK = RUNTIME_DIR / "p3_monitor_server.lock"
    RESUME_LOCK = RUNTIME_DIR / "p3_resume_worker.lock"
    AUTO_RESUME_LOG = RUNTIME_DIR / "p3_auto_resume_worker.log"
    MANUAL_RESUME_LOG = RUNTIME_DIR / "p3_manual_resume_worker.log"


def iso_now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def resolve_project_path(path: Path | None) -> Path | None:
    if path is None:
        return None
    expanded = path.expanduser()
    if expanded.is_absolute():
        return expanded.resolve()
    return (PROJECT / expanded).resolve()


def rel_or_abs(path: Path) -> str:
    try:
        return str(path.relative_to(PROJECT)).replace("\\", "/")
    except ValueError:
        return str(path).replace("\\", "/")


def sha256_file(path: Path | None) -> str | None:
    if path is None or not path.exists() or not path.is_file():
        return None
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def file_manifest(path: Path | None) -> dict[str, Any] | None:
    if path is None:
        return None
    if not path.exists() or not path.is_file():
        return {"path": str(path).replace("\\", "/"), "exists": False}
    stat = path.stat()
    return {
        "path": str(path).replace("\\", "/"),
        "exists": True,
        "size_bytes": stat.st_size,
        "mtime": datetime.fromtimestamp(stat.st_mtime).astimezone().isoformat(timespec="seconds"),
        "sha256": sha256_file(path),
    }


def atomic_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


def append_log(message: str) -> None:
    RUNTIME_DIR.mkdir(parents=True, exist_ok=True)
    with SERVER_LOG.open("a", encoding="utf-8") as f:
        f.write(f"[{iso_now()}] {message}\n")


def append_supervisor_event(event: str, **payload: Any) -> None:
    RUNTIME_DIR.mkdir(parents=True, exist_ok=True)
    row = {"time": iso_now(), "event": event, **payload}
    with SUPERVISOR_EVENTS.open("a", encoding="utf-8") as f:
        f.write(json.dumps(row, ensure_ascii=False) + "\n")


def is_port_free(host: str, port: int) -> bool:
    with contextlib.closing(socket.socket(socket.AF_INET, socket.SOCK_STREAM)) as sock:
        sock.settimeout(0.5)
        return sock.connect_ex((host, port)) != 0


def choose_port(host: str, preferred: int, *, allow_next_port: bool, max_tries: int = 20) -> int:
    if is_port_free(host, preferred):
        return preferred
    if not allow_next_port:
        raise RuntimeError(
            f"Port {preferred} is already in use. Refusing to start a second monitor. "
            "Stop the old monitor or pass --allow-next-port explicitly."
        )
    for port in range(preferred + 1, preferred + max_tries):
        if is_port_free(host, port):
            return port
    raise RuntimeError(f"No free port found from {preferred} to {preferred + max_tries - 1}")


def _lock_file_windows(handle: Any, blocking: bool) -> bool:
    import msvcrt

    try:
        mode = msvcrt.LK_LOCK if blocking else msvcrt.LK_NBLCK
        msvcrt.locking(handle.fileno(), mode, 1)
        return True
    except OSError:
        return False


def _unlock_file_windows(handle: Any) -> None:
    import msvcrt

    with contextlib.suppress(OSError):
        msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)


def _lock_file_posix(handle: Any, blocking: bool) -> bool:
    import fcntl

    try:
        flags = fcntl.LOCK_EX if blocking else fcntl.LOCK_EX | fcntl.LOCK_NB
        fcntl.flock(handle.fileno(), flags)
        return True
    except OSError:
        return False


def _unlock_file_posix(handle: Any) -> None:
    import fcntl

    with contextlib.suppress(OSError):
        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def acquire_file_lock(path: Path, payload: dict[str, Any], *, blocking: bool = False) -> Any | None:
    """Acquire a cross-platform advisory lock and write metadata to the lock file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    handle = path.open("a+", encoding="utf-8")
    handle.seek(0)
    if sys.platform.startswith("win"):
        ok = _lock_file_windows(handle, blocking)
    else:
        ok = _lock_file_posix(handle, blocking)
    if not ok:
        handle.close()
        return None
    handle.seek(0)
    handle.truncate()
    handle.write(json.dumps({"pid": os.getpid(), "started_at": iso_now(), **payload}, ensure_ascii=False))
    handle.flush()
    return handle


def release_file_lock(handle: Any | None) -> None:
    if handle is None:
        return
    try:
        handle.seek(0)
        handle.truncate()
        handle.flush()
        if sys.platform.startswith("win"):
            _unlock_file_windows(handle)
        else:
            _unlock_file_posix(handle)
    finally:
        handle.close()


def refresh_once(timeout: int = 60) -> tuple[bool, str]:
    cmd = [
        sys.executable,
        str(STATUS_SCRIPT),
        "--project",
        str(PROJECT),
        "--stage-dir",
        str(STAGE_DIR),
        "--checkpoint-dir",
        str(CHECKPOINT_DIR),
    ]
    try:
        proc = subprocess.run(
            cmd,
            cwd=str(PROJECT),
            text=True,
            encoding="utf-8",
            errors="replace",
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=timeout,
        )
        if proc.returncode == 0:
            return True, proc.stdout.strip()
        return False, (proc.stderr or proc.stdout or f"returncode={proc.returncode}").strip()
    except Exception as exc:
        return False, str(exc)


def load_runtime_status() -> dict[str, Any]:
    path = RUNTIME_DIR / "p3_runtime_status.json"
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        return {"_load_error": str(exc)}


def _process_scan_windows(pattern: str, *, python_only: bool = True) -> list[dict[str, Any]]:
    safe_pattern = pattern.replace("'", "''")
    name_filter = " -and $_.Name -match 'python'" if python_only else ""
    command = (
        f"$pattern = '{safe_pattern}'; "
        "$items = Get-CimInstance Win32_Process | Where-Object { "
        "$_.CommandLine -like \"*$pattern*\""
        f"{name_filter}"
        " } | Select-Object ProcessId, CreationDate, CommandLine; "
        "$items | ConvertTo-Json -Compress"
    )
    try:
        proc = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", command],
            text=True,
            encoding="utf-8",
            errors="replace",
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=10,
        )
        text = (proc.stdout or "").strip()
        if proc.returncode != 0 or not text:
            return []
        data = json.loads(text)
        if isinstance(data, dict):
            data = [data]
        return [x for x in data if isinstance(x, dict)] if isinstance(data, list) else []
    except Exception as exc:
        append_supervisor_event("process_scan_failed", method="windows", pattern=pattern, error=str(exc))
        return []


def _process_scan_posix(pattern: str, *, python_only: bool = True) -> list[dict[str, Any]]:
    try:
        proc = subprocess.run(
            ["ps", "-eo", "pid=,args="],
            text=True,
            encoding="utf-8",
            errors="replace",
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=10,
        )
        if proc.returncode != 0:
            return []
        rows: list[dict[str, Any]] = []
        for line in proc.stdout.splitlines():
            text = line.strip()
            if not text or pattern not in text:
                continue
            if python_only and "python" not in text.lower():
                continue
            try:
                pid_text, cmdline = text.split(None, 1)
                pid = int(pid_text)
            except ValueError:
                continue
            rows.append({"ProcessId": pid, "CommandLine": cmdline})
        return rows
    except Exception as exc:
        append_supervisor_event("process_scan_failed", method="posix", pattern=pattern, error=str(exc))
        return []


def process_scan(pattern: str, *, python_only: bool = True) -> list[dict[str, Any]]:
    if sys.platform.startswith("win"):
        return _process_scan_windows(pattern, python_only=python_only)
    return _process_scan_posix(pattern, python_only=python_only)


def existing_resume_processes() -> list[dict[str, Any]]:
    current_pid = os.getpid()
    rows = process_scan(RUN_SCRIPT.name, python_only=True)
    return [x for x in rows if x.get("ProcessId") != current_pid]


def stop_processes(processes: list[dict[str, Any]]) -> list[int]:
    stopped: list[int] = []
    for item in processes:
        pid = item.get("ProcessId")
        if not isinstance(pid, int) or pid == os.getpid():
            continue
        try:
            if sys.platform.startswith("win"):
                subprocess.run(
                    ["powershell", "-NoProfile", "-NonInteractive", "-Command", f"Stop-Process -Id {pid} -Force -Confirm:$false"],
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                    timeout=10,
                )
            else:
                os.kill(pid, 15)
            stopped.append(pid)
        except Exception as exc:
            append_supervisor_event("process_stop_failed", pid=pid, error=str(exc))
    return stopped


def refresh_loop(interval: int, stop_event: threading.Event) -> None:
    while not stop_event.is_set():
        ok, output = refresh_once()
        append_log(f"refresh ok={ok} output={output[:1000]}")
        stop_event.wait(interval)


def tail_text(path: Path, limit: int = 2000) -> str:
    if not path.exists():
        return ""
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except Exception:
        return ""
    return text[-limit:]


def start_resume_process(source: str) -> subprocess.Popen[str]:
    if not RUN_SCRIPT.exists():
        raise FileNotFoundError(f"Missing resume script: {RUN_SCRIPT}")
    log_path = MANUAL_RESUME_LOG if source == "manual" else AUTO_RESUME_LOG
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_file = log_path.open("a", encoding="utf-8")
    log_file.write(f"\n[{iso_now()}] start source={source} script={RUN_SCRIPT}\n")
    log_file.flush()
    cmd = [sys.executable, str(RUN_SCRIPT)]
    proc = subprocess.Popen(
        cmd,
        cwd=str(PROJECT),
        stdout=log_file,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    setattr(proc, "_p3_log_file", log_file)
    setattr(proc, "_p3_log_path", log_path)
    return proc


def collect_finished_child(child: subprocess.Popen[str]) -> dict[str, Any]:
    log_file = getattr(child, "_p3_log_file", None)
    log_path = getattr(child, "_p3_log_path", None)
    if log_file is not None:
        with contextlib.suppress(Exception):
            log_file.write(f"[{iso_now()}] finished returncode={child.returncode}\n")
            log_file.flush()
            log_file.close()
    return {
        "returncode": child.returncode,
        "log_path": str(log_path).replace("\\", "/") if log_path else None,
        "log_tail": tail_text(log_path) if isinstance(log_path, Path) else "",
    }


def try_acquire_resume_lock(source: str) -> Any | None:
    return acquire_file_lock(RESUME_LOCK, {"source": source, "run_script": str(RUN_SCRIPT).replace("\\", "/")}, blocking=False)


def supervisor_loop(
    interval: int,
    stale_seconds: int,
    cooldown_seconds: int,
    auto_resume: bool,
    stop_event: threading.Event,
) -> None:
    global MANUAL_CHILD, RESUME_LOCK_HANDLE
    child: subprocess.Popen[str] | None = None
    child_lock: Any | None = None
    last_launch = 0.0
    last_progress: tuple[Any, Any] | None = None
    while not stop_event.is_set():
        status = load_runtime_status()
        done = status.get("done")
        total = status.get("total")
        runtime_status = status.get("status")
        heartbeat_age = status.get("heartbeat_age_seconds")
        now_ts = time.time()

        if child is not None and child.poll() is not None:
            payload = collect_finished_child(child)
            append_supervisor_event("child_finished", **payload)
            release_file_lock(child_lock)
            child_lock = None
            child = None
            refresh_once()
            status = load_runtime_status()
            done = status.get("done")
            total = status.get("total")
            runtime_status = status.get("status")
            heartbeat_age = status.get("heartbeat_age_seconds")

        child_running = child is not None and child.poll() is None
        progress = (done, total)
        if progress != last_progress:
            append_supervisor_event(
                "progress_observed",
                runtime_status=runtime_status,
                done=done,
                total=total,
                heartbeat_age_seconds=heartbeat_age,
            )
            last_progress = progress

        existing_resume = existing_resume_processes()
        should_resume = bool(
            auto_resume
            and runtime_status == "stale"
            and status.get("can_resume")
            and isinstance(heartbeat_age, int)
            and heartbeat_age >= stale_seconds
            and not child_running
            and not existing_resume
            and (now_ts - last_launch) >= cooldown_seconds
            and (not isinstance(done, int) or not isinstance(total, int) or done < total)
        )
        if existing_resume and runtime_status == "stale":
            append_supervisor_event(
                "auto_resume_ignored",
                reason="existing_resume_process",
                count=len(existing_resume),
                done=done,
                total=total,
                heartbeat_age_seconds=heartbeat_age,
            )
        if should_resume:
            child_lock = try_acquire_resume_lock("auto")
            if child_lock is None:
                append_supervisor_event("auto_resume_ignored", reason="resume_lock_held", done=done, total=total)
            else:
                try:
                    child = start_resume_process("auto")
                    last_launch = now_ts
                    append_supervisor_event(
                        "auto_resume_started",
                        pid=child.pid,
                        done=done,
                        total=total,
                        heartbeat_age_seconds=heartbeat_age,
                    )
                except Exception as exc:
                    release_file_lock(child_lock)
                    child_lock = None
                    append_supervisor_event(
                        "auto_resume_failed_to_start",
                        error=str(exc),
                        done=done,
                        total=total,
                        heartbeat_age_seconds=heartbeat_age,
                    )

        with MANUAL_CHILD_LOCK:
            if MANUAL_CHILD is not None and MANUAL_CHILD.poll() is not None:
                payload = collect_finished_child(MANUAL_CHILD)
                append_supervisor_event("manual_child_finished", **payload)
                MANUAL_CHILD = None
                release_file_lock(RESUME_LOCK_HANDLE)
                RESUME_LOCK_HANDLE = None

        atomic_json(SUPERVISOR_STATE, {
            "updated_at": iso_now(),
            "auto_resume": auto_resume,
            "stale_seconds": stale_seconds,
            "cooldown_seconds": cooldown_seconds,
            "child_running": child is not None and child.poll() is None,
            "child_pid": child.pid if child is not None and child.poll() is None else None,
            "manual_child_running": MANUAL_CHILD is not None and MANUAL_CHILD.poll() is None,
            "manual_child_pid": MANUAL_CHILD.pid if MANUAL_CHILD is not None and MANUAL_CHILD.poll() is None else None,
            "last_runtime_status": runtime_status,
            "done": done,
            "total": total,
            "heartbeat_age_seconds": heartbeat_age,
            "last_launch_ts": last_launch,
            "resume_lock_path": str(RESUME_LOCK).replace("\\", "/"),
        })
        stop_event.wait(interval)


def manual_resume_request() -> tuple[int, dict[str, Any]]:
    global MANUAL_CHILD, RESUME_LOCK_HANDLE
    with MANUAL_CHILD_LOCK:
        if MANUAL_CHILD is not None and MANUAL_CHILD.poll() is None:
            payload = {
                "ok": False,
                "status": "already_running",
                "message": "已有一个继续运行子进程正在执行，不重复启动。",
                "pid": MANUAL_CHILD.pid,
                "time": iso_now(),
            }
            append_supervisor_event("manual_resume_ignored", reason="child_already_running", pid=MANUAL_CHILD.pid)
            return 409, payload

        existing = existing_resume_processes()
        if existing:
            payload = {
                "ok": False,
                "status": "already_running",
                "message": "检测到已有续跑进程正在执行，不重复启动。",
                "processes": existing[:5],
                "time": iso_now(),
            }
            append_supervisor_event("manual_resume_ignored", reason="existing_resume_process", count=len(existing))
            return 409, payload

        lock_handle = try_acquire_resume_lock("manual")
        if lock_handle is None:
            payload = {
                "ok": False,
                "status": "already_running",
                "message": "续跑工作锁已被占用，不重复启动。",
                "time": iso_now(),
            }
            append_supervisor_event("manual_resume_ignored", reason="resume_lock_held")
            return 409, payload

        status = load_runtime_status()
        done = status.get("done")
        total = status.get("total")
        runtime_status = status.get("status")
        if isinstance(done, int) and isinstance(total, int) and total > 0 and done >= total:
            release_file_lock(lock_handle)
            payload = {
                "ok": False,
                "status": "completed",
                "message": "当前任务已经完成，不需要继续运行。",
                "done": done,
                "total": total,
                "time": iso_now(),
            }
            append_supervisor_event("manual_resume_ignored", reason="already_completed", done=done, total=total)
            return 409, payload

        try:
            MANUAL_CHILD = start_resume_process("manual")
            RESUME_LOCK_HANDLE = lock_handle
            payload = {
                "ok": True,
                "status": "started",
                "message": "已启动断点续跑。页面会继续自动刷新进度。",
                "pid": MANUAL_CHILD.pid,
                "runtime_status": runtime_status,
                "done": done,
                "total": total,
                "time": iso_now(),
            }
            append_supervisor_event(
                "manual_resume_started",
                pid=MANUAL_CHILD.pid,
                runtime_status=runtime_status,
                done=done,
                total=total,
            )
            return 200, payload
        except Exception as exc:
            release_file_lock(lock_handle)
            payload = {
                "ok": False,
                "status": "failed_to_start",
                "message": str(exc),
                "time": iso_now(),
            }
            append_supervisor_event("manual_resume_failed_to_start", error=str(exc), done=done, total=total)
            return 500, payload


def make_handler():
    class Handler(SimpleHTTPRequestHandler):
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            super().__init__(*args, directory=str(P3), **kwargs)

        def translate_path(self, path: str) -> str:
            parsed = urlparse(path)
            clean_path = parsed.path
            if clean_path in {"/", "/p3_monitor.html"}:
                return str(MONITOR_HTML)
            return super().translate_path(path)

        def log_message(self, fmt: str, *args: Any) -> None:
            append_log("http " + (fmt % args))

        def do_POST(self) -> None:
            if self.path.split("?", 1)[0] != "/api/resume":
                self.send_error(404, "Not found")
                return
            status_code, payload = manual_resume_request()
            body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            self.send_response(status_code)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    return Handler


def main() -> int:
    parser = argparse.ArgumentParser(description="Start local P3 monitor and auto-open the page.")
    parser.add_argument("--project", type=Path, default=Path.cwd(), help="Project root containing 03_Screening.")
    parser.add_argument("--run-script", type=Path, default=None, help="Resume script to launch from the button/supervisor. Defaults to the current P3 dual-model resume script.")
    parser.add_argument("--stage-dir", type=Path, default=None, help="Directory containing *.progress.log. Defaults to current P3 dual-model review directory.")
    parser.add_argument("--checkpoint-dir", type=Path, default=None, help="Directory containing *.checkpoint.json. Defaults to <stage-dir>/checkpoints.")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--allow-next-port", action="store_true", help="Allow using the next free port if --port is occupied.")
    parser.add_argument("--refresh-seconds", type=int, default=30)
    parser.add_argument("--supervisor-seconds", type=int, default=30)
    parser.add_argument("--stale-seconds", type=int, default=600)
    parser.add_argument("--cooldown-seconds", type=int, default=180)
    parser.add_argument("--no-open", action="store_true", help="Do not open the browser automatically.")
    parser.add_argument("--no-auto-resume", action="store_true", help="Disable supervisor auto-resume.")
    args = parser.parse_args()
    configure_project(args.project)
    global RUN_SCRIPT, STAGE_DIR, CHECKPOINT_DIR
    if args.run_script is not None:
        RUN_SCRIPT = resolve_project_path(args.run_script)
    if args.stage_dir is not None:
        STAGE_DIR = resolve_project_path(args.stage_dir)
    if args.checkpoint_dir is not None:
        CHECKPOINT_DIR = resolve_project_path(args.checkpoint_dir)
    else:
        CHECKPOINT_DIR = (STAGE_DIR / "checkpoints").resolve()

    if not P3.exists():
        raise FileNotFoundError(f"Project does not contain 03_Screening: {P3}")
    if not STATUS_SCRIPT.exists():
        raise FileNotFoundError(f"Missing status script: {STATUS_SCRIPT}")
    if not MONITOR_HTML.exists():
        raise FileNotFoundError(f"Missing monitor HTML: {MONITOR_HTML}")

    global MONITOR_LOCK_HANDLE, RESUME_LOCK_HANDLE
    MONITOR_LOCK_HANDLE = acquire_file_lock(MONITOR_LOCK, {"kind": "monitor", "project": str(PROJECT).replace("\\", "/")}, blocking=False)
    if MONITOR_LOCK_HANDLE is None:
        try:
            old_status = json.loads(SERVER_STATUS.read_text(encoding="utf-8")) if SERVER_STATUS.exists() else {}
        except Exception:
            old_status = {}
        url = old_status.get("url") or "existing P3 monitor page"
        append_supervisor_event("monitor_start_rejected", reason="lock_held", url=url)
        print("P3 monitor is already running; refusing to start a second monitor instance.", flush=True)
        print(f"Existing monitor: {url}", flush=True)
        return 2

    try:
        ok, output = refresh_once()
        append_log(f"initial refresh ok={ok} output={output[:1000]}")

        port = choose_port(args.host, args.port, allow_next_port=args.allow_next_port)
        url = f"http://{args.host}:{port}/p3_monitor.html"
        atomic_json(SERVER_STATUS, {
            "started_at": iso_now(),
            "pid": os.getpid(),
            "host": args.host,
            "port": port,
            "url": url,
            "refresh_seconds": args.refresh_seconds,
            "supervisor_seconds": args.supervisor_seconds,
            "auto_resume": not args.no_auto_resume,
            "stale_seconds": args.stale_seconds,
            "cooldown_seconds": args.cooldown_seconds,
            "auto_open": not args.no_open,
            "standard_code_dir": str(SKILL_SCRIPT_DIR).replace("\\", "/"),
            "status_script": str(STATUS_SCRIPT).replace("\\", "/"),
            "monitor_html": str(MONITOR_HTML).replace("\\", "/"),
            "run_script": rel_or_abs(RUN_SCRIPT),
            "stage_dir": rel_or_abs(STAGE_DIR),
            "checkpoint_dir": rel_or_abs(CHECKPOINT_DIR),
            "monitor_lock_path": str(MONITOR_LOCK).replace("\\", "/"),
            "resume_lock_path": str(RESUME_LOCK).replace("\\", "/"),
            "auto_resume_log": str(AUTO_RESUME_LOG).replace("\\", "/"),
            "manual_resume_log": str(MANUAL_RESUME_LOG).replace("\\", "/"),
            "safe_note": "Monitor server refreshes status and may launch the configured resume script; it does not directly modify screening.db or locks.",
            "reproducibility_manifest": {
                "schema_version": "p3_monitor_server.v1",
                "python_executable": sys.executable,
                "python_version": sys.version.split()[0],
                "platform": platform.platform(),
                "project_root": str(PROJECT).replace("\\", "/"),
                "server_script": file_manifest(Path(__file__).resolve()),
                "status_script": file_manifest(STATUS_SCRIPT),
                "monitor_html": file_manifest(MONITOR_HTML),
                "project_server_wrapper": file_manifest(P3 / "p3_monitor_server.py"),
                "project_status_wrapper": file_manifest(P3 / "p3_runtime_status.py"),
                "run_script": file_manifest(RUN_SCRIPT),
            },
        })

        stop_event = threading.Event()
        refresher = threading.Thread(target=refresh_loop, args=(args.refresh_seconds, stop_event), daemon=True)
        supervisor = threading.Thread(
            target=supervisor_loop,
            args=(args.supervisor_seconds, args.stale_seconds, args.cooldown_seconds, not args.no_auto_resume, stop_event),
            daemon=True,
        )
        refresher.start()
        supervisor.start()

        server = ThreadingHTTPServer((args.host, port), make_handler())
        print(f"P3 monitor: {url}", flush=True)
        print(f"Auto-resume: {not args.no_auto_resume}", flush=True)
        print("Press Ctrl+C to stop.", flush=True)
        if not args.no_open:
            try:
                webbrowser.open(url)
            except Exception as exc:
                append_log(f"browser open failed: {exc}")
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass
        finally:
            stop_event.set()
            server.server_close()
            append_log("server stopped")
        return 0
    finally:
        release_file_lock(RESUME_LOCK_HANDLE)
        RESUME_LOCK_HANDLE = None
        release_file_lock(MONITOR_LOCK_HANDLE)
        MONITOR_LOCK_HANDLE = None


if __name__ == "__main__":
    raise SystemExit(main())
