"""Pluggable storage. Choose a backend with ``STORAGE_BACKEND`` (local | webdav | smb)."""
from __future__ import annotations

import os

from .base import StorageBackend, StorageError, StorageNotFound, clean_path
from .local import LocalStorage


def _truthy(value: str) -> bool:
    return str(value).strip().lower() in ("1", "true", "yes", "on")


def create_storage(env=None) -> StorageBackend:
    """Build a backend from environment variables (see .env.example)."""
    env = os.environ if env is None else env
    kind = env.get("STORAGE_BACKEND", "local").strip().lower()

    if kind == "local":
        return LocalStorage(env.get("STORAGE_ROOT", "data"))

    if kind == "webdav":
        from .webdav import WebDAVStorage
        url = env.get("WEBDAV_URL")
        if not url:
            raise StorageError("WEBDAV_URL is required when STORAGE_BACKEND=webdav")
        return WebDAVStorage(url, env.get("WEBDAV_USER", ""), env.get("WEBDAV_PASSWORD", ""),
                             verify_tls=_truthy(env.get("WEBDAV_VERIFY_TLS", "true")))

    if kind == "smb":
        from .smb import SMBStorage
        server, share = env.get("SMB_SERVER"), env.get("SMB_SHARE")
        if not server or not share:
            raise StorageError("SMB_SERVER and SMB_SHARE are required when STORAGE_BACKEND=smb")
        return SMBStorage(server, share, env.get("SMB_USER", ""), env.get("SMB_PASSWORD", ""),
                          base_dir=env.get("SMB_BASE_DIR", "thermal-report"),
                          port=int(env.get("SMB_PORT", "445")),
                          encrypt=_truthy(env.get("SMB_ENCRYPT", "false")))

    raise StorageError(f"Unknown STORAGE_BACKEND {kind!r} (use local, webdav or smb)")


__all__ = ["StorageBackend", "StorageError", "StorageNotFound", "LocalStorage",
           "create_storage", "clean_path"]
