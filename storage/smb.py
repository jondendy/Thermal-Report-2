"""SMB/CIFS backend using ``smbprotocol`` (pure Python; Linux, Mac and Windows).

Connects straight to ``\\\\server\\share`` with no OS-level mount. (If the share is
already mounted on the server you can instead use the local backend on the mount.)
"""
from __future__ import annotations

import posixpath
from typing import List

from .base import StorageBackend, StorageError, StorageNotFound, clean_path


class SMBStorage(StorageBackend):
    def __init__(self, server: str, share: str, username: str = "", password: str = "",
                 base_dir: str = "", port: int = 445, encrypt: bool = False):
        try:
            import smbclient  # type: ignore
        except ImportError as exc:  # pragma: no cover
            raise StorageError("SMB backend needs 'smbprotocol' (pip install smbprotocol)") from exc
        self._smb = smbclient
        self.server, self.share, self.base_dir = server, share, clean_path(base_dir)
        try:
            smbclient.register_session(server, username=username or None,
                                       password=password or None, port=port,
                                       encrypt=encrypt)
        except Exception as exc:
            raise StorageError(f"Could not connect to SMB server {server}: {exc}") from exc
        self.makedirs("")

    def _unc(self, path: str) -> str:
        rel = clean_path(path)
        parts = [p for p in (self.base_dir, rel) if p]
        tail = "\\".join("/".join(parts).split("/")) if parts else ""
        return f"\\\\{self.server}\\{self.share}" + (f"\\{tail}" if tail else "")

    def exists(self, path: str) -> bool:
        return self._smb.path.exists(self._unc(path))

    def is_dir(self, path: str) -> bool:
        return self._smb.path.isdir(self._unc(path))

    def read_bytes(self, path: str) -> bytes:
        try:
            with self._smb.open_file(self._unc(path), mode="rb") as fh:
                return fh.read()
        except FileNotFoundError as exc:
            raise StorageNotFound(path) from exc
        except Exception as exc:
            raise StorageError(f"SMB read {path} failed: {exc}") from exc

    def write_bytes(self, path: str, data: bytes) -> None:
        self.makedirs(posixpath.dirname(clean_path(path)))
        try:
            with self._smb.open_file(self._unc(path), mode="wb") as fh:
                fh.write(data)
        except Exception as exc:
            raise StorageError(f"SMB write {path} failed: {exc}") from exc

    def listdir(self, path: str) -> List[str]:
        if not self.is_dir(path):
            return []
        return sorted(self._smb.listdir(self._unc(path)))

    def makedirs(self, path: str) -> None:
        try:
            self._smb.makedirs(self._unc(path), exist_ok=True)
        except Exception as exc:
            raise StorageError(f"SMB makedirs {path!r} failed: {exc}") from exc

    def delete(self, path: str) -> None:
        unc = self._unc(path)
        try:
            if self._smb.path.isdir(unc):
                self._smb.rmtree(unc)
            elif self._smb.path.exists(unc):
                self._smb.remove(unc)
        except Exception as exc:
            raise StorageError(f"SMB delete {path} failed: {exc}") from exc

    def describe(self) -> str:
        return f"smb://{self.server}/{self.share}/{self.base_dir}"
