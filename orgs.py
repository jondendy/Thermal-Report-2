"""Groups ("organisations"): who belongs to which, and each group's own settings.

Configured in one file (ORGS_FILE), e.g. orgs/orgs.json:

    {
      "admins": ["jon@example.org"],
      "organisations": {
        "chesham":  {"name": "Sustainable Chesham Energy Group", "colour": "#1f3a5f",
                     "sensitivity": "medium", "palette": "inferno",
                     "members": ["a@example.org", "b@example.org"]},
        "amersham": {"name": "Amersham Energy Group", "colour": "#6b2c5f", "members": ["c@example.org"]}
      }
    }

The same email may appear in several groups (shared operators). Admins can open every group.
A group may also have orgs/<slug>/report_content.json, which replaces the matching parts of
the standard report wording.
"""
from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from typing import Dict, FrozenSet, List, Optional

import palette as palette_mod
from warm_areas import PRESETS

SLUG = re.compile(r"^[a-z0-9][a-z0-9_-]{0,39}$")
COLOUR = re.compile(r"^#[0-9a-fA-F]{6}$")
EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
DEFAULT_COLOUR = "#1f3a5f"


class OrgConfigError(Exception):
    """The groups file is wrong. The message says what to fix."""


@dataclass(frozen=True)
class Org:
    slug: str
    name: str
    colour: str = DEFAULT_COLOUR
    sensitivity: Optional[str] = None
    palette: Optional[str] = None
    members: FrozenSet[str] = field(default_factory=frozenset)
    content_file: Optional[str] = None

    @property
    def text_colour(self) -> str:
        r, g, b = (int(self.colour[i:i + 2], 16) for i in (1, 3, 5))
        return "#000000" if 0.299 * r + 0.587 * g + 0.114 * b > 150 else "#ffffff"


def _email(value, where: str) -> str:
    e = str(value).strip().lower()
    if not EMAIL.match(e):
        raise OrgConfigError(f"{where}: {value!r} is not an email address")
    return e


class OrgDirectory:
    def __init__(self, orgs: Dict[str, Org], admins: FrozenSet[str]):
        if not orgs:
            raise OrgConfigError("No organisations are defined")
        self.orgs, self.admins = orgs, admins

    @classmethod
    def load(cls, path: str) -> "OrgDirectory":
        try:
            with open(path, "r", encoding="utf-8") as fh:
                data = json.load(fh)
        except FileNotFoundError:
            raise OrgConfigError(f"Groups file not found: {path}")
        except ValueError as exc:
            raise OrgConfigError(f"{path} is not valid JSON: {exc}")
        base = os.path.dirname(os.path.abspath(path))
        raw = data.get("organisations")
        if not isinstance(raw, dict) or not raw:
            raise OrgConfigError(f"{path}: 'organisations' must list at least one group")
        admins = frozenset(_email(e, "admins") for e in data.get("admins", []))
        orgs: Dict[str, Org] = {}
        for slug, o in raw.items():
            if not SLUG.match(slug):
                raise OrgConfigError(f"Group id {slug!r}: use lower-case letters, digits, - or _ (max 40)")
            if not str(o.get("name", "")).strip():
                raise OrgConfigError(f"Group {slug!r} needs a 'name'")
            colour = o.get("colour", DEFAULT_COLOUR)
            if not COLOUR.match(colour):
                raise OrgConfigError(f"Group {slug!r}: colour must look like #1f3a5f")
            sens = o.get("sensitivity")
            if sens is not None and sens not in PRESETS:
                raise OrgConfigError(f"Group {slug!r}: sensitivity must be one of {', '.join(PRESETS)}")
            pal = o.get("palette")
            if pal is not None and palette_mod.valid(pal) != str(pal).lower().strip():
                raise OrgConfigError(f"Group {slug!r}: unknown palette {pal!r}")
            content = os.path.join(base, slug, "report_content.json")
            orgs[slug] = Org(slug=slug, name=str(o["name"]).strip(), colour=colour, sensitivity=sens,
                             palette=pal, content_file=content if os.path.isfile(content) else None,
                             members=frozenset(_email(e, f"group {slug!r} members") for e in o.get("members", [])))
        return cls(orgs, admins)

    def is_admin(self, email: str) -> bool:
        return (email or "").strip().lower() in self.admins

    def for_user(self, email: str) -> List[Org]:
        """Groups this person may work for, in file order. Admins may open all of them."""
        e = (email or "").strip().lower()
        if not e:
            return []
        if e in self.admins:
            return list(self.orgs.values())
        return [o for o in self.orgs.values() if e in o.members]
