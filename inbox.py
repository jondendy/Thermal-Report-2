"""The "drive": a staging folder of FLIR pictures for each group, on a drive mounted on the server
(e.g. a Synology share). People put pictures there with "Upload to drive" and later pick them with
"Select from drive" to make the next batch.

Safety rules kept in one place:
  * a group only ever sees its own sub-folder (<INBOX_ROOT>/<group>/), with one level of folders
    inside it (usually one per property) and nothing deeper;
  * names are cleaned, must be .jpg/.jpeg, and must really be pictures;
  * checking whether the drive is there can never hang the web server (a drive that is asleep or
    unmounted makes network folders block for a long time): it is asked on a side thread with a time limit.
"""
from __future__ import annotations

import io
import os
import threading
import time
from typing import Callable, Dict, List, Optional, Tuple

from PIL import Image
from werkzeug.utils import secure_filename

EXTENSIONS = (".jpg", ".jpeg")
MAX_NAME = 100
MAX_FOLDER = 80
FOLDER_CHARS = " -_,.'()&"          # besides letters and digits
THUMB = (240, 180)


class InboxError(Exception):
    """Something the person can fix (bad name, not a picture, drive full of files...)."""


def _within(seconds: float, fn: Callable, default=None):
    """Run fn on a daemon thread and give up after ``seconds`` (a blocked network folder leaves the
    thread behind, but never the request)."""
    box: Dict = {}

    def run():
        try:
            box["value"] = fn()
        except Exception as exc:                       # noqa: BLE001 - any failure means 'not usable'
            box["error"] = exc

    t = threading.Thread(target=run, daemon=True)
    t.start()
    t.join(seconds)
    return box.get("value", default) if "value" in box else default


class Inbox:
    def __init__(self, root: str, group: str = "default", marker: str = "", waker=None,
                 wait: float = 3.0, max_files: int = 500):
        self.root = os.path.abspath(root)
        self.group = secure_filename(group) or "default"
        self.folder = os.path.join(self.root, self.group)
        self.marker, self.waker, self.wait, self.max_files = marker.strip(), waker, wait, max_files

    # ------------------------------------------------------------------ is it there?
    def available(self) -> bool:
        """True when the drive can be used right now. Never raises and never blocks for long."""
        if self.waker is not None and self.waker.configured and not self.waker.status()["up"]:
            return False                               # asleep: do not even touch the folder
        def check():
            if not os.path.isdir(self.root):
                return False
            if self.marker and not os.path.exists(os.path.join(self.root, self.marker)):
                return False                           # the share is not mounted (we would be writing to the bare mount point)
            os.makedirs(self.folder, exist_ok=True)
            return True
        return bool(_within(self.wait, check, default=False))

    # ------------------------------------------------------------------ names
    def _name(self, name: str) -> str:
        clean = secure_filename(os.path.basename(str(name or "")))
        if not clean or len(clean) > MAX_NAME or os.path.splitext(clean)[1].lower() not in EXTENSIONS:
            raise InboxError(f"{name!r} is not a .jpg picture name")
        return clean

    @staticmethod
    def folder_name(name: str) -> str:
        """A tidy folder name ('' = the main folder). Folders are one level deep and are usually a
        property address, so spaces and commas are kept; anything that could leave the folder is refused."""
        raw = str(name or "").strip()
        if not raw:
            return ""
        if any(c in raw for c in "/\\\x00") or raw.startswith("."):
            raise InboxError("Folder names cannot contain slashes or start with a dot")
        clean = "".join(c for c in raw if c.isalnum() or c in FOLDER_CHARS)
        clean = " ".join(clean.split()).rstrip(". ")[:MAX_FOLDER].rstrip(". ")
        if not clean or clean.startswith("."):
            raise InboxError(f"{name!r} is not a usable folder name")
        return clean

    def _dir(self, folder: str = "") -> str:
        clean = self.folder_name(folder)
        path = os.path.realpath(os.path.join(self.folder, clean)) if clean else os.path.realpath(self.folder)
        if clean and os.path.dirname(path) != os.path.realpath(self.folder):
            raise InboxError("Not a folder on this drive")
        return path

    def _path(self, name: str, folder: str = "") -> str:
        base = self._dir(folder)
        path = os.path.realpath(os.path.join(base, self._name(name)))
        if os.path.dirname(path) != base:
            raise InboxError("Not a file in this drive folder")
        return path

    # ------------------------------------------------------------------ folders
    def folders(self) -> List[Dict]:
        """Folders in the group's drive folder, newest first."""
        if not os.path.isdir(self.folder):
            return []
        out = []
        for entry in os.scandir(self.folder):
            if entry.is_dir() and not entry.name.startswith((".", "@", "#")):     # skip NAS housekeeping folders
                try:
                    if self.folder_name(entry.name) != entry.name:
                        continue                                  # made by hand with a name we would not accept
                except InboxError:
                    continue
                files = self.list(entry.name)
                newest = max([f["modified"] for f in files] or [int(entry.stat().st_mtime)])
                out.append({"name": entry.name, "count": len(files), "modified": newest})
        return sorted(out, key=lambda f: (-f["modified"], f["name"].lower()))

    def _count(self) -> int:
        return len(self.list()) + sum(f["count"] for f in self.folders())

    # ------------------------------------------------------------------ files
    def list(self, folder: str = "") -> List[Dict]:
        base = self._dir(folder)
        if not os.path.isdir(base):
            return []
        files = []
        for entry in os.scandir(base):
            if entry.is_file() and os.path.splitext(entry.name)[1].lower() in EXTENSIONS:
                st = entry.stat()
                files.append({"name": entry.name, "size": st.st_size, "modified": int(st.st_mtime)})
        return sorted(files, key=lambda f: (-f["modified"], f["name"]))

    def save(self, name: str, data: bytes, modified: Optional[float] = None, folder: str = "") -> str:
        clean = self._name(name)
        base = self._dir(folder)
        if self._count() >= self.max_files:
            raise InboxError(f"The drive already holds {self.max_files} pictures for this group - delete some first")
        try:
            Image.open(io.BytesIO(data)).verify()
        except Exception:
            raise InboxError(f"{clean} is not a readable JPEG picture")
        os.makedirs(base, exist_ok=True)
        stem, ext = os.path.splitext(clean)
        final, n = clean, 1
        while os.path.exists(os.path.join(base, final)):             # never overwrite someone else's picture
            final, n = f"{stem}-{n}{ext}", n + 1
        path = os.path.join(base, final)
        with open(path, "wb") as fh:
            fh.write(data)
        if modified and 946684800 <= modified <= time.time() + 86400:   # keep the picture's own date (2000-01-01 .. tomorrow)
            os.utime(path, (modified, modified))
        return final

    def modified(self, name: str, folder: str = "") -> Optional[float]:
        path = self._path(name, folder)
        return os.path.getmtime(path) if os.path.isfile(path) else None

    def read(self, name: str, folder: str = "") -> bytes:
        path = self._path(name, folder)
        if not os.path.isfile(path):
            raise InboxError(f"{name} is not on the drive any more")
        with open(path, "rb") as fh:
            return fh.read()

    def thumb(self, name: str, folder: str = "") -> bytes:
        path = self._path(name, folder)
        if not os.path.isfile(path):
            raise InboxError(f"{name} is not on the drive any more")
        with Image.open(path) as img:
            img.draft("RGB", THUMB)                    # decode a shrunk version: fast even for big files
            img = img.convert("RGB")
            img.thumbnail(THUMB)
            buf = io.BytesIO()
            img.save(buf, "JPEG", quality=72)
        return buf.getvalue()

    def delete(self, name: str, folder: str = "") -> bool:
        path = self._path(name, folder)
        if not os.path.isfile(path):
            return False
        os.remove(path)
        self._drop_if_empty(folder)
        return True

    def _drop_if_empty(self, folder: str) -> None:
        """Remove a folder once its last picture has gone (never the main folder, never one with other files)."""
        if not self.folder_name(folder):
            return
        try:
            os.rmdir(self._dir(folder))                # fails, harmlessly, if anything is still inside
        except OSError:
            pass

    def cleanup(self, older_than_days: float, dry_run: bool = False) -> List[str]:
        """Delete pictures not modified for this many days, in the main folder and every folder.
        Returns what was (or with dry_run, would be) removed."""
        cutoff, removed = time.time() - older_than_days * 86400, []
        for folder in [""] + [f["name"] for f in self.folders()]:
            for f in self.list(folder):
                if f["modified"] < cutoff:
                    if dry_run or self.delete(f["name"], folder):
                        removed.append(f"{folder}/{f['name']}" if folder else f["name"])
            if not dry_run:
                self._drop_if_empty(folder)
        return removed
