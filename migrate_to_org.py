#!/usr/bin/env python3
"""Move the surveys you already have into one group's folder.

    python migrate_to_org.py chesham --dry-run     # shows what would move
    python migrate_to_org.py chesham               # does it

Run it once, with the app stopped, after ORGS_FILE is set up and BEFORE restarting with groups
switched on. It works on a local (or already-mounted) STORAGE_ROOT. Nothing is copied or deleted:
the 'images' and 'reports' folders are renamed into <STORAGE_ROOT>/<group>/.
"""
from __future__ import annotations

import os
import shutil
import sys

from orgs import SLUG

FOLDERS = ("images", "reports")


def migrate(root: str, slug: str, dry_run: bool = False):
    if not SLUG.match(slug):
        raise SystemExit(f"{slug!r} is not a valid group id")
    moves = [(os.path.join(root, f), os.path.join(root, slug, f)) for f in FOLDERS
             if os.path.isdir(os.path.join(root, f))]
    for _, dest in moves:
        if os.path.exists(dest) and os.listdir(dest):
            raise SystemExit(f"{dest} already has files in it - nothing was moved")
    if not moves:
        print("Nothing to move (no 'images' or 'reports' folder directly under the storage root).")
        return []
    for src, dest in moves:
        print(("Would move " if dry_run else "Moving ") + f"{src}  ->  {dest}")
        if not dry_run:
            os.makedirs(os.path.dirname(dest), exist_ok=True)
            if os.path.isdir(dest):
                os.rmdir(dest)
            shutil.move(src, dest)
    return moves


if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    if len(args) != 1:
        raise SystemExit(__doc__)
    storage_root = os.environ.get("STORAGE_ROOT", "data")
    if os.environ.get("STORAGE_BACKEND", "local").lower() != "local":
        raise SystemExit("This helper only works with STORAGE_BACKEND=local (a mounted share counts as local).")
    migrate(storage_root, args[0], dry_run="--dry-run" in sys.argv)
