"""Local-disk backend (also works for an SMB/NFS share that is already mounted)."""
from __future__ import annotations

import os
import shutil
from typing import List

from .base import StorageBackend, StorageError, StorageNotFound, clean_path


class LocalStorage(StorageBackend):
    def __init__(self, root: str):
        self.root = os.path.abspath(root)
        os.makedirs(self.root, exist_ok=True)

    def _full(self, path: str) -> str:
        rel = clean_path(path)
        full = os.path.abspath(os.path.join(self.root, rel))
        if full != self.root and not full.startswith(self.root + os.sep):
            raise StorageError(f"Path escapes storage root: {path!r}")
        return full

    def exists(self, path: str) -> bool:
        return os.path.exists(self._full(path))

    def is_dir(self, path: str) -> bool:
        return os.path.isdir(self._full(path))

    def read_bytes(self, path: str) -> bytes:
        full = self._full(path)
        if not os.path.isfile(full):
            raise StorageNotFound(path)
        with open(full, "rb") as fh:
            return fh.read()

    def write_bytes(self, path: str, data: bytes) -> None:
        full = self._full(path)
        os.makedirs(os.path.dirname(full), exist_ok=True)
        tmp = full + ".part"
        with open(tmp, "wb") as fh:
            fh.write(data)
        os.replace(tmp, full)

    def listdir(self, path: str) -> List[str]:
        full = self._full(path)
        if not os.path.isdir(full):
            return []
        return sorted(os.listdir(full))

    def makedirs(self, path: str) -> None:
        os.makedirs(self._full(path), exist_ok=True)

    def delete(self, path: str) -> None:
        full = self._full(path)
        if os.path.isdir(full):
            shutil.rmtree(full)
        elif os.path.exists(full):
            os.remove(full)

    def describe(self) -> str:
        return f"local:{self.root}"
