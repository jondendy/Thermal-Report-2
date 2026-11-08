#!/usr/bin/env python3
"""Delete old pictures from every group's drive folder (run from cron or a systemd timer).

    python cleanup_inbox.py             # uses INBOX_KEEP_DAYS (default 30)
    python cleanup_inbox.py --days 14 --dry-run

Reads INBOX_ROOT (and NAS_MARKER) from the environment, like the app. Only .jpg/.jpeg files directly
inside a group's folder are touched. Nothing happens if the drive is not mounted.
"""
from __future__ import annotations

import os
import sys
import time

from inbox import EXTENSIONS, Inbox


def run(root: str, days: float, marker: str = "", dry_run: bool = False) -> int:
    probe = Inbox(root, "default", marker=marker)
    if not probe.available():
        print("The drive is not available (not mounted or marker file missing) - nothing deleted.")
        return 0
    total, cutoff = 0, time.time() - days * 86400
    for group in sorted(os.listdir(root)):
        if not os.path.isdir(os.path.join(root, group)):
            continue
        box = Inbox(root, group, marker=marker)
        for f in box.list():
            if f["modified"] < cutoff:
                print(("Would delete " if dry_run else "Deleting ") + f"{group}/{f['name']}")
                if not dry_run:
                    box.delete(f["name"])
                total += 1
    print(f"{total} picture(s) {'would be ' if dry_run else ''}removed (older than {days:g} days).")
    return total


if __name__ == "__main__":
    args = sys.argv[1:]
    days = float(args[args.index("--days") + 1]) if "--days" in args else float(os.environ.get("INBOX_KEEP_DAYS", "30"))
    root = os.environ.get("INBOX_ROOT", "")
    if not root:
        raise SystemExit("INBOX_ROOT is not set")
    run(root, days, os.environ.get("NAS_MARKER", ""), "--dry-run" in args)
