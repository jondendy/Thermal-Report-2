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
    sensitivity: str = "medium"          # lowest | low | medium | high | highest
    max_spots: int = 8                   # automatic points kept per image (strongest first)
    secret_key: str = "change-me"
    org_name: str = ""
    palette: str = "inferno"             # inferno | cividis | grey | camera
    report_content_file: str = ""        # optional replacement for report_content.json
    orgs_file: str = ""                  # groups file (orgs/orgs.json); blank = one group, no sign-in check
    auth_mode: str = ""                  # cloudflare | dev (required when orgs_file is set)
    cf_team_domain: str = ""             # e.g. yourteam.cloudflareaccess.com
    cf_aud: str = ""                     # the Access application's "Application Audience (AUD) Tag"
    dev_user_email: str = ""             # only with AUTH_MODE=dev
    inbox_root: str = ""                 # folder (e.g. a mounted Synology share) for the "drive" buttons; blank = off
    inbox_delete_after: bool = False     # remove a picture from the drive once it has been processed
    inbox_keep_days: int = 30            # used by cleanup_inbox.py
    inbox_max_files: int = 500           # per group
    wake_mac: str = ""                   # Wake-on-LAN for a sleeping NAS; off until MAC and host are set
    wake_broadcast: str = ""             # e.g. 192.168.1.255 (blank = 255.255.255.255)
    nas_host: str = ""                   # NAS name or IP, probed to see whether it is awake
    nas_port: int = 445                  # 445 = SMB file sharing
    nas_marker: str = ""                 # optional file that must exist on the share, e.g. .thermal-report-share


def load_settings(env=None) -> Settings:
    env = os.environ if env is None else env
    sensitivity = str(env.get("SENSITIVITY", "medium")).lower()
    if sensitivity not in ("lowest", "low", "medium", "high", "highest"):
        sensitivity = "medium"
    palette = str(env.get("PALETTE", "inferno")).lower().strip()
    if palette not in ("inferno", "cividis", "grey", "camera"):
        palette = "inferno"
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
        palette=palette,
        report_content_file=env.get("REPORT_CONTENT_FILE", ""),
        orgs_file=env.get("ORGS_FILE", "").strip(),
        auth_mode=env.get("AUTH_MODE", "").strip().lower(),
        cf_team_domain=env.get("CF_ACCESS_TEAM_DOMAIN", "").strip(),
        cf_aud=env.get("CF_ACCESS_AUD", "").strip(),
        dev_user_email=env.get("DEV_USER_EMAIL", "").strip(),
        inbox_root=env.get("INBOX_ROOT", "").strip(),
        inbox_delete_after=str(env.get("INBOX_DELETE_AFTER_PROCESS", "0")).lower() in ("1", "true", "yes"),
        inbox_keep_days=max(1, _int(env, "INBOX_KEEP_DAYS", 30)),
        inbox_max_files=max(1, _int(env, "INBOX_MAX_FILES", 500)),
        wake_mac=env.get("WAKE_MAC", "").strip(),
        wake_broadcast=env.get("WAKE_BROADCAST", "").strip(),
        nas_host=env.get("NAS_HOST", "").strip(),
        nas_port=_int(env, "NAS_PORT", 445),
        nas_marker=env.get("NAS_MARKER", "").strip(),
    )
