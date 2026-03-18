#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys

from helpers import (
    append_log,
    build_initial_prompt,
    ensure_runtime_dirs,
    load_state,
    new_state,
    save_state,
    slugify,
    state_path,
    start_codex_if_needed,
    tmux_has_session,
    tmux_new_session,
    tmux_send,
)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--task-id", default="")
    parser.add_argument("--session", default="")
    parser.add_argument("--workdir", default="/home/work/nanobot")
    parser.add_argument("--goal", required=True)
    parser.add_argument("--context", default="")
    parser.add_argument("--constraints", default="尽量保持变更最小，避免高风险不可逆操作。")
    parser.add_argument("--output", default="请输出执行结果与必要说明。")
    parser.add_argument("--completion", default="任务目标达成，并给出最终结果总结。")
    parser.add_argument("--allow-auto-continue", action="store_true")
    parser.add_argument("--cron-job-id", default="")
    args = parser.parse_args()

    ensure_runtime_dirs()
    task_id = slugify(args.task_id or args.goal)[:64]
    session = args.session or f"codex-{task_id}"

    path = state_path(task_id)
    if path.exists():
        state = load_state(task_id)
        print(json.dumps({
            "task_id": task_id,
            "session": state.session,
            "status": state.data.get("status"),
            "decision": "reuse_existing",
            "reason": "state file already exists"
        }, ensure_ascii=False))
        return 0

    if not tmux_has_session(session):
        tmux_new_session(session, args.workdir)
        session_action = "created"
    else:
        session_action = "reused"

    prompt = build_initial_prompt(
        goal=args.goal,
        context=args.context,
        constraints=args.constraints,
        output=args.output,
        completion=args.completion,
    )
    codex_action = start_codex_if_needed(session, args.workdir, prompt=prompt)
    # prompt already sent inside start_codex_if_needed

    state = new_state(
        task_id=task_id,
        session=session,
        workdir=args.workdir,
        prompt_goal=args.goal,
        completion_criteria=args.completion,
        allow_auto_continue=args.allow_auto_continue,
    )
    state.data["mode"] = "start"
    state.data["last_initial_prompt"] = prompt
    state.data["cron_job_id"] = args.cron_job_id
    state.data["no_change_count"] = 0
    save_state(state)
    append_log(task_id, f"start task session={session} session_action={session_action} codex_action={codex_action}")

    print(json.dumps({
        "task_id": task_id,
        "session": session,
        "status": "running",
        "decision": "started",
        "session_action": session_action,
        "codex_action": codex_action,
        "cron_job_id": args.cron_job_id,
    }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
