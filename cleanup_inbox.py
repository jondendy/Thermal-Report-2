#!/usr/bin/env python3
"""Delete old pictures from every group's drive folder (run from cron or a systemd timer).

    python cleanup_inbox.py             # uses INBOX_KEEP_DAYS (default 30)
    python cleanup_inbox.py --days 14 --dry-run

Reads INBOX_ROOT, NAS_MARKER and INBOX_KEEP_DAYS from the environment, or from the .env file next to
this script when they are not set (cron does not load .env by itself). A picture's age is counted from
when it was put on the drive, not from when it was taken. Only .jpg/.jpeg files in a group's folder and
its folders are touched; processed batches are never touched. Nothing happens if the drive is not mounted.
"""
from __future__ import annotations

import os
import sys

from inbox import Inbox

KEYS = ("INBOX_ROOT", "NAS_MARKER", "INBOX_KEEP_DAYS")


def env_from_file(path: str) -> dict:
    """The few settings this script needs, from a KEY=value file (comments and other keys ignored)."""
    found = {}
    try:
        with open(path, "r", encoding="utf-8") as fh:
            for line in fh:
                key, sep, value = line.strip().partition("=")
                if sep and key in KEYS:
                    found[key] = value.strip().strip('"').strip("'")
    except OSError:
        pass
    return found


def run(root: str, days: float, marker: str = "", dry_run: bool = False) -> int:
    probe = Inbox(root, "default", marker=marker)
    if not probe.available():
        print("The drive is not available (not mounted or marker file missing) - nothing deleted.")
        return 0
    total = 0
    for group in sorted(os.listdir(root)):
        if not os.path.isdir(os.path.join(root, group)):
            continue
        box = Inbox(root, group, marker=marker)
        for name in box.cleanup(days, dry_run=dry_run):
            print(("Would delete " if dry_run else "Deleting ") + f"{group}/{name}")
            total += 1
    print(f"{total} picture(s) {'would be ' if dry_run else ''}removed (older than {days:g} days).")
    return total


if __name__ == "__main__":
    args = sys.argv[1:]
    conf = env_from_file(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env"))
    conf.update({k: os.environ[k] for k in KEYS if os.environ.get(k)})          # the real environment wins
    days = float(args[args.index("--days") + 1]) if "--days" in args else float(conf.get("INBOX_KEEP_DAYS") or 30)
    if not conf.get("INBOX_ROOT"):
        raise SystemExit("INBOX_ROOT is not set (in the environment or in .env)")
    run(conf["INBOX_ROOT"], days, conf.get("NAS_MARKER", ""), "--dry-run" in args)
