"""Heat loss report builder.

Pure data in, pure data out: it takes the surveyor's labelled points (plus notes) and
returns the report dictionary the template renders. It does no file I/O on survey data,
so it works the same on local disk, WebDAV or SMB (the BatchService does the storing).

What the report says about each point is built from three layers, kept apart so the
surveyor can see and change each one in the editor:
  * comment    - generated from the point definition (type, temperatures, severity); the
                 surveyor can overwrite it, or leave it blank to keep the generated text.
  * extras     - recommendations the surveyor picked from the list for that point type.
  * note       - freehand text.
Survey-wide (general_notes) and per-image (image_notes) notes are separate again.
"""
from __future__ import annotations

import json
import os
from collections import OrderedDict
from datetime import datetime
from typing import Dict, List, Optional

from vocab import (SEVERITY_COLOURS, SEVERITY_LABELS, SEVERITY_ORDER, SEVERITY_TEXT,
                   normalise_severity, severity_legend, short_id)

_HERE = os.path.dirname(os.path.abspath(__file__))
_DEFAULT_RECS = os.path.join(_HERE, "energy_recommendations.json")
_DEFAULT_CONTENT = os.path.join(_HERE, "report_content.json")
IMAGES_PER_SHEET = 4
MAX_EXTRAS = 12

SEVERITY_SENTENCE = {
    "very_high": "This is a very high heat loss point that should be addressed first.",
    "high": "This is a significant heat loss area that should be addressed promptly.",
    "medium": "This represents moderate heat loss that should be addressed.",
    "low": "This is a minor heat loss point for consideration.",
}


def labeled_image_name(image_name: str) -> str:
    stem = os.path.splitext(image_name)[0]
    return f"{stem}_labeled.jpg"


def _fill(value, org: str):
    """Put the organisation's name into {org} placeholders, in text, lists and dicts."""
    if isinstance(value, str):
        return value.replace("{org}", org)
    if isinstance(value, list):
        return [_fill(v, org) for v in value]
    if isinstance(value, dict):
        return {k: _fill(v, org) for k, v in value.items() if not k.startswith("_")}
    return value


def _safe_links(measures: List[Dict]) -> List[Dict]:
    for m in measures:
        m["links"] = [l for l in m.get("links", []) if str(l.get("url", "")).startswith("https://")]
    return measures


class HeatLossReporter:
    def __init__(self, recommendations_file: Optional[str] = None, content_file: Optional[str] = None):
        with open(recommendations_file or _DEFAULT_RECS, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        # keys starting with "_" (e.g. "_metadata") are not spot types
        self.recommendations: Dict[str, Dict] = OrderedDict(
            (k, v) for k, v in data.items() if not k.startswith("_"))
        with open(content_file or _DEFAULT_CONTENT, "r", encoding="utf-8") as fh:
            self._content = json.load(fh)

    @property
    def spot_types(self) -> List[str]:
        return list(self.recommendations.keys())

    def content(self, org_name: str = "") -> Dict:
        """The fixed wording of the report, with the organisation's name filled in."""
        filled = _fill(self._content, org_name.strip() or "your local energy group")
        filled["general_measures"] = _safe_links(filled.get("general_measures", []))
        return filled

    # ------------------------------------------------------------------ point text
    @staticmethod
    def auto_comment(spot_type: str, temperature, excess=None, severity: str = "low") -> str:
        """The generated sentence(s) for one point."""
        if not spot_type:
            return ""
        text = f"Heat loss detected at {spot_type.lower()} location"
        try:
            text += f": {float(temperature):.1f}°C"
        except (TypeError, ValueError):
            pass
        try:
            if excess is not None and float(excess) > 0:
                text += f", {float(excess):.1f}°C warmer than the surrounding wall surface"
        except (TypeError, ValueError):
            pass
        return text + ". " + SEVERITY_SENTENCE[normalise_severity(severity)]

    def options_for(self, spot_type: str) -> List[str]:
        """Recommendations the surveyor can add to a point of this type."""
        rec = self.recommendations.get(spot_type) or {}
        options = list(rec.get("advice", []))
        if rec.get("savings"):
            options.append(f"Typical saving: {rec['savings']}.")
        return options

    def options_by_type(self) -> Dict[str, List[str]]:
        return {t: self.options_for(t) for t in self.recommendations}

    @staticmethod
    def labelled_spots(spots: List[Dict]) -> List[Dict]:
        """Only points the surveyor has given both a type and a number."""
        return [s for s in spots if s.get("type") and s.get("spot_number")]

    def build_point(self, s: Dict) -> Dict:
        sev = normalise_severity(s.get("severity"))
        number = int(s["spot_number"])
        temp = float(s["temperature"])
        excess = s.get("excess")
        comment = (s.get("comment") or "").strip() or self.auto_comment(s["type"], temp, excess, sev)
        extras = [str(e).strip() for e in (s.get("extras") or []) if str(e).strip()][:MAX_EXTRAS]
        return {
            "id": short_id(s["type"], number), "type": s["type"], "number": number,
            "temperature": temp, "excess": None if excess is None else float(excess),
            "severity": sev, "severity_label": SEVERITY_LABELS[sev],
            "colour": SEVERITY_COLOURS[sev], "text_colour": SEVERITY_TEXT[sev],
            "comment": comment, "extras": extras, "note": (s.get("note") or "").strip(),
            "location": list(s.get("location", [0, 0])),
        }

    # ------------------------------------------------------------------ report
    def build_report(self, batch_id: str, labels: Dict, property_address: str = "",
                     inspector_name: str = "", images_meta: Optional[List[Dict]] = None,
                     org_name: str = "", now: Optional[datetime] = None) -> Dict:
        """images_meta: [{'image_name': ..., 'scale': {...}}] in survey order. When missing,
        the images are taken from the points, in order of first appearance."""
        now = now or datetime.now()
        spots = self.labelled_spots(labels.get("spots", []))
        image_notes = {k: (v or "").strip() for k, v in (labels.get("image_notes") or {}).items()}

        meta = list(images_meta or [])
        known = {m["image_name"] for m in meta}
        for s in spots:
            if s["image_name"] not in known:
                meta.append({"image_name": s["image_name"]})
                known.add(s["image_name"])

        images, counts = [], {k: 0 for k in SEVERITY_ORDER}
        for n, m in enumerate(meta, 1):
            name = m["image_name"]
            points = [self.build_point(s) for s in spots if s["image_name"] == name]
            points.sort(key=lambda p: (p["number"], p["type"]))
            for p in points:
                counts[p["severity"]] += 1
            images.append({
                "number": n, "image_name": name, "labeled_image": labeled_image_name(name),
                "scale": m.get("scale"), "image_note": image_notes.get(name, ""), "points": points,
            })

        total = sum(counts.values())
        highest = next((k for k in reversed(SEVERITY_ORDER) if counts[k]), "low")
        return {
            "batch_id": batch_id, "org_name": org_name,
            "property_address": property_address or labels.get("property_address") or "Not specified",
            "inspector_name": inspector_name or labels.get("inspector_name") or "Not specified",
            "survey_date": now.strftime("%Y-%m-%d"), "survey_time": now.strftime("%H:%M"),
            "general_notes": (labels.get("general_notes") or "").strip(),
            "summary": {
                "total_points": total, "image_count": len(images),
                "highest_severity": SEVERITY_LABELS[highest] if total else "None",
                "breakdown": [{"key": k, "label": SEVERITY_LABELS[k], "count": counts[k],
                               "colour": SEVERITY_COLOURS[k], "text": SEVERITY_TEXT[k]}
                              for k in reversed(SEVERITY_ORDER)],
            },
            "legend": severity_legend(),
            "images": images,
            "images_per_sheet": IMAGES_PER_SHEET,
            "content": self.content(org_name),
        }
