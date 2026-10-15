"""Runtime settings, read from environment variables (see .env.example).

Nothing here is specific to one machine, so the app runs unchanged on any server.
Storage selection (local / WebDAV / SMB) lives in storage.create_storage().
"""
from __future__ import annotations

import os
from dataclasses import dataclass


def _int(env, name: str, default: int) -> int:
    try:
        return int(env.get(name, default))
    except (TypeError, ValueError):
        return default


@dataclass(frozen=True)
class Settings:
    host: str = "0.0.0.0"
    port: int = 5000
    debug: bool = False
    max_upload_mb: int = 200
    batch_size: int = 8
    sensitivity: str = "medium"          # low | medium | high
    max_spots: int = 8                   # automatic points kept per image (strongest first)
    secret_key: str = "change-me"
    org_name: str = ""


def load_settings(env=None) -> Settings:
    env = os.environ if env is None else env
    sensitivity = str(env.get("SENSITIVITY", "medium")).lower()
    if sensitivity not in ("low", "medium", "high"):
        sensitivity = "medium"
    return Settings(
        host=env.get("HOST", "0.0.0.0"),
        port=_int(env, "PORT", 5000),
        debug=str(env.get("FLASK_DEBUG", "0")).lower() in ("1", "true", "yes"),
        max_upload_mb=_int(env, "MAX_UPLOAD_MB", 200),
        batch_size=_int(env, "BATCH_SIZE", 8),
        sensitivity=sensitivity,
        max_spots=max(1, min(_int(env, "MAX_SPOTS_PER_IMAGE", 8), 30)),
        secret_key=env.get("SECRET_KEY", "change-me"),
        org_name=env.get("ORG_NAME", ""),
    )
