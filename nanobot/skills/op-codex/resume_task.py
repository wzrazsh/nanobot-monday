#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys

from helpers import (
    append_log,
    ensure_runtime_dirs,
    load_state,
    save_state,
    tmux_has_session,
    tmux_send,
)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("task_id")
    parser.add_argument("prompt")
    parser.add_argument("--cron-job-id", default="")
    parser.add_argument("--allow-auto-continue", action="store_true")
    args = parser.parse_args()

    ensure_runtime_dirs()
    state = load_state(args.task_id)
    session = state.session

    if not tmux_has_session(session):
        print(json.dumps({
            "task_id": args.task_id,
            "session": session,
            "status": "interrupted",
            "decision": "notify_user",
            "reason": "tmux session missing",
            "cron_job_id": state.data.get("cron_job_id", ""),
        }, ensure_ascii=False))
        return 2

    tmux_send(session, args.prompt)
    state.data["mode"] = "resume"
    state.data["status"] = "running"
    state.data["last_decision"] = "continue"
    state.data["last_resume_prompt"] = args.prompt
    state.data["no_change_count"] = 0
    state.data["last_output_hash"] = ""
    state.data["last_probe_reason"] = "任务已恢复并重新进入监控。"
    if args.cron_job_id:
        state.data["cron_job_id"] = args.cron_job_id
    if args.allow_auto_continue:
        state.data["allow_auto_continue"] = True
    save_state(state)
    append_log(args.task_id, f"resume task session={session} cron_job_id={state.data.get('cron_job_id', '')}")

    print(json.dumps({
        "task_id": args.task_id,
        "session": session,
        "status": state.data.get("status", "running"),
        "decision": "resume",
        "reason": "existing session resumed and monitoring metadata refreshed",
        "cron_job_id": state.data.get("cron_job_id", ""),
    }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
