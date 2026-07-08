#!/usr/bin/env python3
"""Build a read-only P3 runtime status snapshot.

This script does not modify screening.db, lock files, PDFs, txt files, or screening
records. It only reads existing checkpoint/progress artifacts and writes a small
runtime status JSON plus a normalized JSONL event stream for monitoring.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import platform
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

PROJECT = Path.cwd().resolve()
P3 = PROJECT / "03_Screening"
RUNTIME_DIR = P3 / "qc" / "runtime"
DISPUTED_DIR = P3 / "qc" / "full_reaudit" / "dual_model_disputed_review"
CHECKPOINT_DIR = DISPUTED_DIR / "checkpoints"
STATUS_PATH = RUNTIME_DIR / "p3_runtime_status.json"
EVENTS_PATH = RUNTIME_DIR / "p3_runtime_events.jsonl"


def configure_project(project: Path, stage_dir: Path | None = None, checkpoint_dir: Path | None = None) -> None:
    """Configure all project-relative paths.

    Reusable skill copy lives outside the project; callers should pass the project
    root, not the 03_Screening directory. Runtime outputs still stay inside
    03_Screening/qc/runtime of that project.
    """
    global PROJECT, P3, RUNTIME_DIR, DISPUTED_DIR, CHECKPOINT_DIR, STATUS_PATH, EVENTS_PATH
    PROJECT = project.resolve()
    P3 = PROJECT / "03_Screening"
    RUNTIME_DIR = P3 / "qc" / "runtime"
    default_stage = P3 / "qc" / "full_reaudit" / "dual_model_disputed_review"
    DISPUTED_DIR = resolve_project_path(stage_dir) if stage_dir is not None else default_stage.resolve()
    CHECKPOINT_DIR = resolve_project_path(checkpoint_dir) if checkpoint_dir is not None else (DISPUTED_DIR / "checkpoints").resolve()
    STATUS_PATH = RUNTIME_DIR / "p3_runtime_status.json"
    EVENTS_PATH = RUNTIME_DIR / "p3_runtime_events.jsonl"

def resolve_project_path(path: Path | None) -> Path | None:
    if path is None:
        return None
    expanded = path.expanduser()
    if expanded.is_absolute():
        return expanded.resolve()
    return (PROJECT / expanded).resolve()


LINE_RE = re.compile(
    r"^\[(?P<time>[^\]]+)\]\s+(?P<index>\d+)/(?:\s*)?(?P<total>\d+)\s+(?P<uid>\S+)\s+(?P<profile>\S+)\s+(?P<state>.+)$"
)


def parse_dt(value: str | None) -> datetime | None:
    if not value:
        return None
    text = str(value).strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(text)
    except ValueError:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=now_local().tzinfo)
    return dt.astimezone()


def now_local() -> datetime:
    return datetime.now().astimezone()


def iso(dt: datetime | None) -> str | None:
    return dt.isoformat(timespec="seconds") if dt else None


def rel_or_abs(path: Path | None) -> str | None:
    if path is None:
        return None
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
        return {"path": rel_or_abs(path), "exists": False}
    stat = path.stat()
    return {
        "path": rel_or_abs(path),
        "exists": True,
        "size_bytes": stat.st_size,
        "mtime": iso(datetime.fromtimestamp(stat.st_mtime).astimezone()),
        "sha256": sha256_file(path),
    }


def latest_file(folder: Path, pattern: str) -> Path | None:
    files = [p for p in folder.glob(pattern) if p.is_file()]
    if not files:
        return None
    return max(files, key=lambda p: p.stat().st_mtime)


def run_id_from_path(path: Path | None) -> str | None:
    if path is None:
        return None
    name = path.name
    for suffix in (".checkpoint.json", ".progress.log"):
        if name.endswith(suffix):
            return name[: -len(suffix)]
    return path.stem


def latest_paired_artifacts(stage_dir: Path, checkpoint_dir: Path) -> tuple[Path | None, Path | None, str | None, str]:
    """Select checkpoint/log artifacts from the same run_id when possible.

    Reproducibility requires that checkpoint and progress log describe the same
    run. If no complete pair exists, return the best available artifacts and an
    explicit pairing status instead of silently mixing unrelated runs.
    """
    checkpoints = {run_id_from_path(p): p for p in checkpoint_dir.glob("*.checkpoint.json") if p.is_file()}
    logs = {run_id_from_path(p): p for p in stage_dir.glob("*.progress.log") if p.is_file()}
    paired_ids = sorted(set(checkpoints) & set(logs), key=lambda rid: max(checkpoints[rid].stat().st_mtime, logs[rid].stat().st_mtime), reverse=True)
    if paired_ids:
        rid = paired_ids[0]
        return checkpoints[rid], logs[rid], rid, "PAIRED"

    checkpoint = latest_file(checkpoint_dir, "*.checkpoint.json")
    log = latest_file(stage_dir, "*.progress.log")
    rid = run_id_from_path(checkpoint) or run_id_from_path(log)
    if checkpoint is None and log is None:
        return None, None, None, "MISSING"
    return checkpoint, log, rid, "UNPAIRED"


def load_json(path: Path | None) -> dict[str, Any]:
    if not path or not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        return {"_load_error": str(exc), "_path": str(path)}


def parse_progress_log(path: Path | None) -> list[dict[str, Any]]:
    if not path or not path.exists():
        return []
    events: list[dict[str, Any]] = []
    for raw in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = raw.strip()
        if not line:
            continue
        m = LINE_RE.match(line)
        if not m:
            events.append({"time": None, "event": "raw_log", "message": line})
            continue
        gd = m.groupdict()
        state = gd["state"].strip()
        if state.startswith("reused_success"):
            event = "reused_success"
        elif state.startswith("attempt="):
            event = "attempt_started"
        else:
            event = "progress"
        events.append({
            "time": gd["time"],
            "event": event,
            "index": int(gd["index"]),
            "total": int(gd["total"]),
            "uid": gd["uid"],
            "profile": gd["profile"],
            "state": state,
            "message": line,
        })
    return events


def call_success(call: dict[str, Any]) -> bool:
    return bool(call.get("ok") and call.get("json_valid"))


def summarize(
    checkpoint: dict[str, Any],
    checkpoint_path: Path | None,
    log_path: Path | None,
    events: list[dict[str, Any]],
    heartbeat_threshold_min: int,
    run_id: str | None,
    artifact_pairing_status: str,
) -> dict[str, Any]:
    now = now_local()
    calls = checkpoint.get("calls") if isinstance(checkpoint.get("calls"), list) else []
    comparisons = checkpoint.get("comparisons") if isinstance(checkpoint.get("comparisons"), list) else []
    total_calls = int(checkpoint.get("total_calls") or (events[-1].get("total") if events and events[-1].get("total") else len(calls) or 0))
    completed_calls = int(checkpoint.get("completed_calls") or len(calls))
    ok_calls = sum(1 for c in calls if call_success(c))
    failed_calls = [c for c in calls if not call_success(c)]

    event_times = [parse_dt(e.get("time")) for e in events if e.get("time")]
    event_times = [t for t in event_times if t]
    checkpoint_dt = parse_dt(checkpoint.get("generated_at"))
    mtime_candidates: list[datetime] = []
    for p in [checkpoint_path, log_path]:
        if p and p.exists():
            mtime_candidates.append(datetime.fromtimestamp(p.stat().st_mtime).astimezone())
    last_heartbeat = max([*event_times, checkpoint_dt, *mtime_candidates], default=None)
    heartbeat_age = int((now - last_heartbeat).total_seconds()) if last_heartbeat else None

    last_event = events[-1] if events else {}
    last_success_call = next((c for c in reversed(calls) if call_success(c)), {})
    last_failed_call = next((c for c in reversed(calls) if not call_success(c)), {})

    if total_calls and completed_calls >= total_calls:
        status = "completed"
        health = "completed"
        next_action = "任务已完成；可以查看复核报告或进入 admin 裁决。"
    elif heartbeat_age is None:
        status = "unknown"
        health = "no_runtime_signal"
        next_action = "未找到运行心跳；请确认是否已启动 P3 长任务。"
    elif heartbeat_age > heartbeat_threshold_min * 60:
        status = "stale"
        health = "possibly_interrupted"
        next_action = "心跳长时间未更新；建议检查任务进程，必要时执行断点续跑。"
    else:
        status = "running_or_recent"
        health = "recent_heartbeat"
        next_action = "最近有心跳记录；如命令行仍在运行，继续等待即可。"

    percent = round((completed_calls / total_calls * 100), 1) if total_calls else 0
    status_script = Path(__file__).resolve()
    project_status_wrapper = P3 / "p3_runtime_status.py"
    return {
        "generated_at": iso(now),
        "project": str(PROJECT).replace("\\", "/"),
        "stage": DISPUTED_DIR.name,
        "run_id": run_id,
        "artifact_pairing_status": artifact_pairing_status,
        "reproducibility_manifest": {
            "schema_version": "p3_runtime_status.v1",
            "run_id": run_id,
            "artifact_pairing_status": artifact_pairing_status,
            "python_executable": sys.executable,
            "python_version": sys.version.split()[0],
            "platform": platform.platform(),
            "project_root": str(PROJECT).replace("\\", "/"),
            "script": file_manifest(status_script),
            "project_wrapper": file_manifest(project_status_wrapper),
            "checkpoint": file_manifest(checkpoint_path),
            "progress_log": file_manifest(log_path),
            "project_config": file_manifest(PROJECT / "config.json"),
            "picos_rules_project": file_manifest(PROJECT / "PICOS_RULES.md"),
            "picos_rules_screening": file_manifest(P3 / "PICOS_RULES.md"),
            "final_pool_lock": file_manifest(P3 / "FINAL_POOL_LOCK.yaml"),
        },
        "status": status,
        "health": health,
        "done": completed_calls,
        "total": total_calls,
        "percent": percent,
        "ok_calls": ok_calls,
        "failed_calls": len(failed_calls),
        "total_disputed": checkpoint.get("total_disputed"),
        "comparison_count": len(comparisons),
        "current_uid": last_event.get("uid"),
        "current_profile": last_event.get("profile"),
        "last_success_uid": last_success_call.get("uid"),
        "last_success_profile": last_success_call.get("profile"),
        "last_error_uid": last_failed_call.get("uid"),
        "last_error_profile": last_failed_call.get("profile"),
        "last_error_reason": (last_failed_call.get("reason") or last_failed_call.get("stderr") or "")[:600] if last_failed_call else None,
        "last_heartbeat_at": iso(last_heartbeat),
        "heartbeat_age_seconds": heartbeat_age,
        "heartbeat_threshold_minutes": heartbeat_threshold_min,
        "can_resume": status in {"stale", "unknown", "running_or_recent"} and (not total_calls or completed_calls < total_calls),
        "next_action": next_action,
        "checkpoint_path": rel_or_abs(checkpoint_path),
        "progress_log_path": rel_or_abs(log_path),
        "status_path": rel_or_abs(STATUS_PATH),
        "events_path": rel_or_abs(EVENTS_PATH),
        "recent_events": events[-20:],
        "safe_note": "Read-only runtime monitor. It does not modify screening.db, lock YAML, PDFs, txt, or screening records.",
    }


def write_outputs(status: dict[str, Any], events: list[dict[str, Any]]) -> None:
    RUNTIME_DIR.mkdir(parents=True, exist_ok=True)
    tmp_status = STATUS_PATH.with_suffix(".json.tmp")
    tmp_events = EVENTS_PATH.with_suffix(".jsonl.tmp")
    tmp_status.write_text(json.dumps(status, ensure_ascii=False, indent=2), encoding="utf-8")
    with tmp_events.open("w", encoding="utf-8") as f:
        for event in events:
            f.write(json.dumps(event, ensure_ascii=False) + "\n")
    tmp_status.replace(STATUS_PATH)
    tmp_events.replace(EVENTS_PATH)


def main() -> int:
    parser = argparse.ArgumentParser(description="Build read-only P3 runtime status snapshot.")
    parser.add_argument("--project", type=Path, default=Path.cwd(), help="Project root containing 03_Screening.")
    parser.add_argument("--stage-dir", type=Path, default=None, help="Directory containing *.progress.log. Defaults to current P3 dual-model review directory.")
    parser.add_argument("--checkpoint-dir", type=Path, default=None, help="Directory containing *.checkpoint.json. Defaults to <stage-dir>/checkpoints.")
    parser.add_argument("--heartbeat-threshold-min", type=int, default=10)
    args = parser.parse_args()
    configure_project(args.project, args.stage_dir, args.checkpoint_dir)
    checkpoint_path, log_path, run_id, pairing_status = latest_paired_artifacts(DISPUTED_DIR, CHECKPOINT_DIR)
    checkpoint = load_json(checkpoint_path)
    events = parse_progress_log(log_path)
    status = summarize(checkpoint, checkpoint_path, log_path, events, args.heartbeat_threshold_min, run_id, pairing_status)
    write_outputs(status, events)
    print(json.dumps({
        "status": status["status"],
        "health": status["health"],
        "run_id": status["run_id"],
        "artifact_pairing_status": status["artifact_pairing_status"],
        "done": status["done"],
        "total": status["total"],
        "percent": status["percent"],
        "heartbeat_age_seconds": status["heartbeat_age_seconds"],
        "status_path": status["status_path"],
        "events_path": status["events_path"],
        "next_action": status["next_action"],
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
