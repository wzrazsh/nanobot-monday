from __future__ import annotations

import hashlib
import json
import re
import subprocess
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

BASE_DIR = Path(__file__).resolve().parent
RUNTIME_DIR = BASE_DIR / "runtime"
LOG_DIR = RUNTIME_DIR / "logs"


@dataclass
class TaskState:
    data: dict[str, Any]

    @property
    def task_id(self) -> str:
        return self.data["task_id"]

    @property
    def session(self) -> str:
        return self.data["session"]

    @property
    def path(self) -> Path:
        return state_path(self.task_id)


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def ensure_runtime_dirs() -> None:
    RUNTIME_DIR.mkdir(parents=True, exist_ok=True)
    LOG_DIR.mkdir(parents=True, exist_ok=True)


def slugify(value: str) -> str:
    value = value.strip().lower()
    value = re.sub(r"[^a-z0-9_-]+", "-", value)
    value = re.sub(r"-+", "-", value).strip("-")
    return value or "task"


def state_path(task_id: str) -> Path:
    return RUNTIME_DIR / f"{task_id}.json"


def load_state(task_id: str) -> TaskState:
    path = state_path(task_id)
    data = json.loads(path.read_text(encoding="utf-8"))
    return TaskState(data)


def save_state(state: TaskState) -> None:
    state.data["updated_at"] = utc_now_iso()
    state.path.write_text(
        json.dumps(state.data, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def append_log(task_id: str, message: str) -> None:
    ensure_runtime_dirs()
    path = LOG_DIR / f"{task_id}.log"
    with path.open("a", encoding="utf-8") as f:
        f.write(f"[{utc_now_iso()}] {message}\n")


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def run_cmd(cmd: list[str], check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(cmd, check=check, text=True, capture_output=True)


def tmux_has_session(session: str) -> bool:
    result = subprocess.run(
        ["tmux", "has-session", "-t", session],
        text=True,
        capture_output=True,
    )
    return result.returncode == 0


def tmux_new_session(session: str, workdir: str | None = None) -> None:
    run_cmd(["tmux", "new-session", "-d", "-s", session])
    if workdir:
        tmux_send(session, f"cd {workdir}")


def tmux_kill_session(session: str) -> None:
    run_cmd(["tmux", "kill-session", "-t", session], check=False)


def tmux_capture(session: str, pane: str | None = None) -> str:
    target = session if pane is None else f"{session}:{pane}"
    result = run_cmd(["tmux", "capture-pane", "-pt", target], check=False)
    if result.returncode != 0:
        return ""
    return result.stdout


def tmux_send(session: str, text: str, enter: bool = True) -> None:
    run_cmd(["tmux", "set-buffer", "--", text])
    run_cmd(["tmux", "paste-buffer", "-t", session])
    if enter:
        run_cmd(["tmux", "send-keys", "-t", session, "C-m"])


def start_opencode_if_needed(session: str, workdir: str) -> str:
    existing = tmux_capture(session)
    if "opencode" in existing.lower():
        return "reused"
    tmux_send(session, f"cd {workdir} && /root/.opencode/bin/opencode")
    return "started"


def summarize_output(text: str, max_lines: int = 20) -> str:
    lines = [line.rstrip() for line in text.splitlines() if line.strip()]
    return "\n".join(lines[-max_lines:])


def detect_status(output: str) -> tuple[str, str]:
    low = output.lower()
    if not output.strip():
        return "interrupted", "tmux 输出为空，可能会话失效、进程退出或尚未正常运行。"

    strong_completion_markers = [
        "保存成功",
        "已保存到",
        "保存路径",
        "task completed",
        "completed successfully",
        "任务已完成",
        "执行完成",
    ]
    completion_markers = [
        "done",
        "已完成",
        "完成",
    ]
    error_markers = [
        "traceback",
        "exception",
        "error",
        "failed",
        "permission denied",
        "not found",
        "syntax error",
    ]
    waiting_markers = [
        "press enter",
        "waiting for input",
        "awaiting input",
        "请输入",
        "等待输入",
    ]

    if any(marker in output for marker in strong_completion_markers) or any(marker in low for marker in strong_completion_markers):
        return "completed", "检测到强完成信号，任务已完成或结果已保存。"
    if any(marker in low for marker in error_markers):
        return "failed", "检测到错误关键词，疑似任务报错。"
    if any(marker in low for marker in waiting_markers):
        return "interrupted", "检测到等待输入或卡住信号，疑似任务中断。"
    if any(marker in low for marker in completion_markers):
        return "completed", "检测到完成信号，疑似任务已完成。"
    return "running", "任务仍在执行或尚未达到完成条件。"


def build_continue_prompt(reason: str, excerpt: str) -> str:
    return (
        "请基于当前上下文继续执行原任务，不要重新开始整个流程。"
        f" 当前需要处理的问题：{reason}。"
        " 请先分析现状，再做安全修复或继续剩余步骤。"
        " 若无法继续，请明确说明阻塞点。"
        f"\n\n最近输出摘要：\n{excerpt}"
    )


def build_initial_prompt(goal: str, context: str, constraints: str, output: str, completion: str) -> str:
    return (
        f"目标：{goal}\n"
        f"上下文：{context}\n"
        f"约束：{constraints}\n"
        f"输出：{output}\n"
        f"完成标准：{completion}\n"
        "若遇到错误：先分析原因，再尝试安全修复；若无法继续，请明确说明阻塞点。"
    )


def build_user_notification(task_id: str, session: str, status: str, excerpt: str, needs_user_action: bool, reason: str, still_monitoring: bool) -> str:
    lines = [
        f"任务：{task_id}",
        f"会话：{session}",
        f"当前状态：{status}",
        f"监控状态：{'继续监控中' if still_monitoring else '已停止监控'}",
        f"说明：{reason}",
    ]
    if excerpt.strip():
        lines.extend([
            "最新摘要：",
            excerpt,
        ])
    if needs_user_action:
        lines.extend([
            "是否需要你介入：需要",
            "建议你下一步：请确认是继续当前任务、调整提示词，还是终止任务。若有新的约束或目标，请直接告诉我。",
        ])
    else:
        lines.extend([
            "是否需要你介入：暂时不需要",
            "当前进度说明：任务已有明确状态变化，我会在必要时继续同步。",
        ])
    return "\n".join(lines)


def new_state(
    task_id: str,
    session: str,
    workdir: str,
    prompt_goal: str,
    completion_criteria: str,
    allow_auto_continue: bool,
) -> TaskState:
    now = utc_now_iso()
    return TaskState(
        {
            "task_id": task_id,
            "session": session,
            "workdir": workdir,
            "mode": "start",
            "status": "running",
            "cron_job_id": "",
            "created_at": now,
            "updated_at": now,
            "last_output_hash": "",
            "last_output_excerpt": "",
            "last_decision": "no_change",
            "no_change_count": 0,
            "allow_auto_continue": allow_auto_continue,
            "prompt_goal": prompt_goal,
            "completion_criteria": completion_criteria,
        }
    )
