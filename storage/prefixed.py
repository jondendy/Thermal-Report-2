"""A view of one folder of a storage backend, used to keep each group's data apart.

Every path is validated and then placed under ``<prefix>/``, so code that runs inside one
group cannot name, read, write or delete anything belonging to another group.
"""
from __future__ import annotations

from typing import List

from .base import StorageBackend, StorageError, clean_path


class PrefixedStorage(StorageBackend):
    def __init__(self, inner: StorageBackend, prefix: str):
        self.inner = inner
        self.prefix = clean_path(prefix)
        if not self.prefix:
            raise StorageError("A prefix is required")

    def _p(self, path: str) -> str:
        rel = clean_path(path)
        return f"{self.prefix}/{rel}" if rel else self.prefix

    def exists(self, path: str) -> bool:
        return self.inner.exists(self._p(path))

    def is_dir(self, path: str) -> bool:
        return self.inner.is_dir(self._p(path))

    def read_bytes(self, path: str) -> bytes:
        return self.inner.read_bytes(self._p(path))

    def write_bytes(self, path: str, data: bytes) -> None:
        self.inner.write_bytes(self._p(path), data)

    def listdir(self, path: str) -> List[str]:
        return self.inner.listdir(self._p(path))

    def makedirs(self, path: str) -> None:
        self.inner.makedirs(self._p(path))

    def delete(self, path: str) -> None:
        if not clean_path(path):
            raise StorageError("Refusing to delete a whole group folder")
        self.inner.delete(self._p(path))

    def describe(self) -> str:
        return f"{self.inner.describe()}/{self.prefix}"
