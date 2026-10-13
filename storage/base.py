"""Storage abstraction: one interface, several backends (local disk, WebDAV, SMB).

All paths passed to a backend are POSIX-style *relative* strings such as
``reports/batches/batch_1/results.json``. They are validated so a caller can never
escape the storage root with ``..`` or absolute paths.
"""
from __future__ import annotations

import json
import os
import posixpath
import tempfile
from abc import ABC, abstractmethod
from contextlib import contextmanager
from typing import Any, Iterator, List


class StorageError(Exception):
    """Raised for storage failures (connection, permission, bad path)."""


class StorageNotFound(StorageError, FileNotFoundError):
    """Raised when a path does not exist."""


def clean_path(path: str) -> str:
    """Normalise and validate a relative storage path."""
    if path is None:
        raise StorageError("Path is required")
    p = str(path).replace("\\", "/").strip("/")
    if p == "":
        return ""
    parts = p.split("/")
    for part in parts:
        if part in ("..", ".") or part == "" or "\x00" in part:
            raise StorageError(f"Illegal path: {path!r}")
    return posixpath.join(*parts)


class StorageBackend(ABC):
    """Interface every backend implements."""

    @abstractmethod
    def exists(self, path: str) -> bool: ...

    @abstractmethod
    def is_dir(self, path: str) -> bool: ...

    @abstractmethod
    def read_bytes(self, path: str) -> bytes: ...

    @abstractmethod
    def write_bytes(self, path: str, data: bytes) -> None: ...

    @abstractmethod
    def listdir(self, path: str) -> List[str]:
        """Return the names (not full paths) of entries directly inside ``path``."""

    @abstractmethod
    def makedirs(self, path: str) -> None: ...

    @abstractmethod
    def delete(self, path: str) -> None:
        """Delete a file or an entire directory tree. Missing path is not an error."""

    # -- helpers built on the primitives --------------------------------------
    def read_text(self, path: str, encoding: str = "utf-8") -> str:
        return self.read_bytes(path).decode(encoding)

    def write_text(self, path: str, text: str, encoding: str = "utf-8") -> None:
        self.write_bytes(path, text.encode(encoding, errors="replace"))

    def read_json(self, path: str) -> Any:
        return json.loads(self.read_text(path))

    def write_json(self, path: str, data: Any) -> None:
        self.write_text(path, json.dumps(data, indent=2, default=str))

    def upload_file(self, local_path: str, path: str) -> None:
        with open(local_path, "rb") as fh:
            self.write_bytes(path, fh.read())

    def download_file(self, path: str, local_path: str) -> None:
        with open(local_path, "wb") as fh:
            fh.write(self.read_bytes(path))

    @contextmanager
    def local_copy(self, path: str) -> Iterator[str]:
        """Yield a temporary *local* file holding the contents of ``path``.

        Needed for libraries (FLIR extractor, PIL, ...) that only accept real files.
        """
        suffix = os.path.splitext(path)[1]
        fd, tmp = tempfile.mkstemp(suffix=suffix)
        os.close(fd)
        try:
            self.download_file(path, tmp)
            yield tmp
        finally:
            try:
                os.remove(tmp)
            except OSError:
                pass

    def describe(self) -> str:
        return self.__class__.__name__
