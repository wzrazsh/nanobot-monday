#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys

from helpers import append_log, ensure_runtime_dirs, load_state, save_state, tmux_has_session, tmux_kill_session


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("task_id")
    parser.add_argument("--status", default="stopped")
    parser.add_argument("--close-session", action="store_true")
    parser.add_argument("--keep-cron", action="store_true")
    args = parser.parse_args()

    ensure_runtime_dirs()
    state = load_state(args.task_id)
    session = state.session

    if not args.keep_cron:
        state.data["cron_job_id"] = ""

    if args.close_session and tmux_has_session(session):
        tmux_kill_session(session)
        session_action = "killed"
    else:
        session_action = "kept"

    state.data["mode"] = "stop"
    state.data["status"] = args.status
    state.data["last_decision"] = "stop"
    save_state(state)
    append_log(args.task_id, f"stop task status={args.status} session_action={session_action}")

    print(json.dumps({
        "task_id": args.task_id,
        "session": session,
        "status": args.status,
        "decision": "stop",
        "session_action": session_action,
        "cron_job_id": state.data.get("cron_job_id", ""),
    }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
