#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from helpers import (
    append_log,
    build_continue_prompt,
    build_user_notification,
    detect_status,
    ensure_runtime_dirs,
    load_state,
    save_state,
    sha256_text,
    summarize_output,
    tmux_capture,
    tmux_has_session,
    tmux_send,
)


MAX_NO_CHANGE = 3
FINAL_STATUSES = {"completed", "failed", "interrupted", "stopped"}


def cmd_monitor(task_id: str) -> int:
    ensure_runtime_dirs()
    state = load_state(task_id)
    session = state.session
    previous_status = state.data.get("status", "running")
    previous_decision = state.data.get("last_decision", "no_change")

    if previous_status in FINAL_STATUSES and previous_decision == "stop":
        message = build_user_notification(
            task_id=task_id,
            session=session,
            status=previous_status,
            excerpt=state.data.get("last_output_excerpt", ""),
            needs_user_action=False,
            reason="任务已处于最终状态，无需重复通知。",
            still_monitoring=False,
        )
        print(json.dumps({
            "task_id": task_id,
            "session": session,
            "status": previous_status,
            "decision": "stop",
            "reason": "task already finalized",
            "excerpt": state.data.get("last_output_excerpt", ""),
            "no_change_count": state.data.get("no_change_count", 0),
            "user_message": message,
        }, ensure_ascii=False))
        return 0

    if not tmux_has_session(session):
        state.data["status"] = "interrupted"
        state.data["last_decision"] = "stop"
        state.data["last_output_excerpt"] = "tmux session 不存在。"
        state.data["no_change_count"] = 0
        save_state(state)
        append_log(task_id, "session missing; mark interrupted")
        message = build_user_notification(
            task_id=task_id,
            session=session,
            status="interrupted",
            excerpt="tmux session 不存在。",
            needs_user_action=True,
            reason="任务会话已丢失，无法继续自动执行。",
            still_monitoring=False,
        )
        print(json.dumps({
            "task_id": task_id,
            "status": "interrupted",
            "decision": "stop",
            "reason": "tmux session missing",
            "no_change_count": 0,
            "user_message": message,
        }, ensure_ascii=False))
        return 2

    output = tmux_capture(session)
    excerpt = summarize_output(output)
    output_hash = sha256_text(output)

    last_hash = state.data.get("last_output_hash", "")
    status, reason = detect_status(output)
    decision = "no_change"
    no_change_count = int(state.data.get("no_change_count", 0))

    state.data["last_output_excerpt"] = excerpt
    state.data["last_output_hash"] = output_hash
    state.data["last_probe_reason"] = reason

    if status == "completed":
        state.data["status"] = "completed"
        state.data["no_change_count"] = 0
        decision = "stop"
    elif output_hash == last_hash:
        no_change_count += 1
        state.data["no_change_count"] = no_change_count

        if no_change_count >= MAX_NO_CHANGE:
            decision = "notify_user"
            state.data["status"] = "failed"
            reason = f"连续 {MAX_NO_CHANGE} 次监控结果无明显变化，默认按运行报错/卡住处理，需要用户介入。"
            state.data["last_probe_reason"] = reason
        elif status == "running":
            decision = "no_change"
            state.data["status"] = state.data.get("status", "running") or "running"
        else:
            if previous_status == status and previous_decision == "notify_user":
                decision = "no_change"
            else:
                decision = "notify_user"
            state.data["status"] = status
    else:
        state.data["no_change_count"] = 0
        if status in {"failed", "interrupted"}:
            if state.data.get("allow_auto_continue", False) and status == "interrupted":
                continue_prompt = build_continue_prompt(reason, excerpt)
                tmux_send(session, continue_prompt)
                decision = "continue"
                state.data["status"] = "continuing"
                state.data["last_continue_prompt"] = continue_prompt
                append_log(task_id, f"auto continue sent for status={status}")
            else:
                if previous_status == status and previous_decision == "notify_user":
                    decision = "no_change"
                else:
                    decision = "notify_user"
                state.data["status"] = status
        else:
            decision = "no_change"
            state.data["status"] = "running"

    state.data["last_decision"] = decision
    save_state(state)
    append_log(
        task_id,
        f"monitor status={state.data.get('status')} decision={decision} no_change_count={state.data.get('no_change_count', 0)}",
    )

    needs_user_action = decision == "notify_user"
    still_monitoring = decision not in {"stop"}
    user_message = ""
    if decision in {"notify_user", "stop", "continue"}:
        user_message = build_user_notification(
            task_id=task_id,
            session=session,
            status=state.data.get("status", "running"),
            excerpt=excerpt,
            needs_user_action=needs_user_action,
            reason=reason,
            still_monitoring=still_monitoring,
        )

    print(json.dumps({
        "task_id": task_id,
        "session": session,
        "status": state.data.get("status"),
        "decision": decision,
        "reason": reason,
        "excerpt": excerpt,
        "no_change_count": state.data.get("no_change_count", 0),
        "user_message": user_message,
    }, ensure_ascii=False))
    return 0


def cmd_continue(task_id: str, prompt: str, cron_job_id: str = "") -> int:
    ensure_runtime_dirs()
    state = load_state(task_id)
    session = state.session
    if not tmux_has_session(session):
        message = build_user_notification(
            task_id=task_id,
            session=session,
            status="interrupted",
            excerpt=state.data.get("last_output_excerpt", ""),
            needs_user_action=True,
            reason="tmux session 丢失，无法继续发送提示词。",
            still_monitoring=False,
        )
        print(json.dumps({
            "task_id": task_id,
            "status": "interrupted",
            "decision": "notify_user",
            "reason": "tmux session missing",
            "no_change_count": state.data.get("no_change_count", 0),
            "user_message": message,
        }, ensure_ascii=False))
        return 2

    tmux_send(session, prompt)
    state.data["status"] = "continuing"
    state.data["last_decision"] = "continue"
    state.data["last_continue_prompt"] = prompt
    state.data["no_change_count"] = 0
    if cron_job_id:
        state.data["cron_job_id"] = cron_job_id
    save_state(state)
    append_log(task_id, "manual continue prompt sent")
    message = build_user_notification(
        task_id=task_id,
        session=session,
        status="continuing",
        excerpt=state.data.get("last_output_excerpt", ""),
        needs_user_action=False,
        reason="已在原会话中继续发送提示词，任务继续执行。",
        still_monitoring=bool(state.data.get('cron_job_id')),
    )
    print(json.dumps({
        "task_id": task_id,
        "session": session,
        "status": "continuing",
        "decision": "continue",
        "no_change_count": 0,
        "user_message": message,
        "cron_job_id": state.data.get("cron_job_id", ""),
    }, ensure_ascii=False))
    return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)

    p_monitor = sub.add_parser("monitor")
    p_monitor.add_argument("task_id")

    p_continue = sub.add_parser("continue")
    p_continue.add_argument("task_id")
    p_continue.add_argument("prompt")
    p_continue.add_argument("--cron-job-id", default="")

    args = parser.parse_args()

    if args.command == "monitor":
        return cmd_monitor(args.task_id)
    if args.command == "continue":
        return cmd_continue(args.task_id, args.prompt, cron_job_id=args.cron_job_id)
    return 1


if __name__ == "__main__":
    sys.exit(main())
