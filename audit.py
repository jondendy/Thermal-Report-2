"""Activity log: who did what, for which group, and when.

One small file per event (audit/<date>/<time>-<random>.json) inside the group's own folder, so two
people acting at once can never overwrite each other and no backend needs an 'append' feature.
Writing the log never breaks the request that triggered it.
"""
from __future__ import annotations

import logging
import secrets
from datetime import datetime, timezone
from typing import Dict, List, Optional

from storage import StorageBackend

logger = logging.getLogger("thermal_report.audit")


class AuditLog:
    def __init__(self, storage: StorageBackend, org: str = ""):
        self.storage, self.org = storage, org

    def record(self, who: str, action: str, batch_id: Optional[str] = None, ip: str = "", **detail) -> None:
        now = datetime.now(timezone.utc)
        event = {"time": now.isoformat(timespec="seconds"), "who": who, "org": self.org, "action": action,
                 "batch_id": batch_id, "ip": ip, "detail": detail}
        try:
            self.storage.write_json(f"audit/{now:%Y-%m-%d}/{now:%H%M%S}-{secrets.token_hex(3)}.json", event)
        except Exception as exc:
            logger.error("Could not write audit event %s: %s", action, exc)

    def recent(self, limit: int = 200) -> List[Dict]:
        events: List[Dict] = []
        try:
            days = sorted(self.storage.listdir("audit"), reverse=True)
        except Exception:
            return events
        for day in days:
            for name in sorted(self.storage.listdir(f"audit/{day}"), reverse=True):
                try:
                    events.append(self.storage.read_json(f"audit/{day}/{name}"))
                except Exception:
                    continue
                if len(events) >= limit:
                    return events
        return events
