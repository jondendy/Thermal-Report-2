"""Heat loss report builder.

Pure data in, pure data out: it takes the surveyor's labelled points (plus notes) and
returns the report dictionary the template renders. It does no file I/O on survey data,
so it works the same on local disk, WebDAV or SMB (the BatchService does the storing).

Two kinds of commentary end up in the report, deliberately kept apart so the page can
show both:
  * AUTO comments  - generated from the point definition (type, number, temperatures,
                     severity) plus the recommendation text for that type.
  * SURVEYOR notes - freehand text typed by the user, at three levels:
                     whole survey (general_notes), per image (image_notes) and per
                     point (spot['note']).
"""
from __future__ import annotations

import json
import os
from collections import OrderedDict
from datetime import datetime
from typing import Dict, List, Optional

SEVERITY_ORDER = ["low", "medium", "high", "critical"]
_DEFAULT_RECS = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                             "energy_recommendations.json")


def labeled_image_name(image_name: str) -> str:
    stem = os.path.splitext(image_name)[0]
    return f"{stem}_labeled.jpg"


class HeatLossReporter:
    def __init__(self, recommendations_file: Optional[str] = None):
        path = recommendations_file or _DEFAULT_RECS
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        # keys starting with "_" (e.g. "_metadata") are not spot types
        self.recommendations: Dict[str, Dict] = OrderedDict(
            (k, v) for k, v in data.items() if not k.startswith("_"))

    @property
    def spot_types(self) -> List[str]:
        return list(self.recommendations.keys())

    # ------------------------------------------------------------------ findings
    @staticmethod
    def labelled_spots(spots: List[Dict]) -> List[Dict]:
        """Only points the surveyor has given both a type and a number."""
        return [s for s in spots if s.get("type") and s.get("spot_number")]

    def group_findings(self, spots: List[Dict]) -> "OrderedDict[tuple, List[Dict]]":
        """Group by (type, number): 'Wall #1' and 'Window #1' are different findings,
        while the same Wall #1 seen in several images is a single finding."""
        groups: "OrderedDict[tuple, List[Dict]]" = OrderedDict()
        for s in sorted(self.labelled_spots(spots),
                        key=lambda x: (int(x["spot_number"]), x["type"], x["image_name"])):
            groups.setdefault((s["type"], int(s["spot_number"])), []).append(s)
        return groups

    def build_finding(self, spot_type: str, number: int, group: List[Dict],
                      image_notes: Dict[str, str]) -> Dict:
        temps = [float(s["temperature"]) for s in group]
        max_t, min_t, avg_t = max(temps), min(temps), sum(temps) / len(temps)
        severity = max((s.get("severity", "low") for s in group),
                       key=lambda v: SEVERITY_ORDER.index(v) if v in SEVERITY_ORDER else 0)

        where = (f"at {spot_type.lower()} location" if len(group) == 1
                 else f"at {spot_type.lower()} visible in {len(group)} images")
        excesses = [float(s["excess"]) for s in group if s.get("excess") is not None]
        max_excess = max(excesses) if excesses else None
        text = (f"Heat loss detected {where}. Temperature readings range from "
                f"{min_t:.1f}°C to {max_t:.1f}°C, averaging {avg_t:.1f}°C")
        if max_excess is not None and max_excess > 0:
            text += f", up to {max_excess:.1f}°C warmer than the surrounding wall surface"
        text += ". "
        text += {
            "critical": "This represents a critical heat loss point requiring immediate attention.",
            "high": "This is a significant heat loss area that should be addressed promptly.",
            "medium": "This represents moderate heat loss that should be addressed.",
        }.get(severity, "This is a minor heat loss point for consideration.")

        images, notes, seen = [], [], set()
        for s in group:
            name = s["image_name"]
            images.append({
                "image_name": name,
                "labeled_image": labeled_image_name(name),
                "location": list(s.get("location", [0, 0])),
                "temperature": float(s["temperature"]),
                "severity": s.get("severity", "low"),
                "image_note": image_notes.get(name, "") if name not in seen else "",
            })
            seen.add(name)
            if (s.get("note") or "").strip():
                notes.append({"image_name": name, "note": s["note"].strip()})

        return {
            "spot_number": number,
            "type": spot_type,
            "title": f"Heat loss at {spot_type} #{number}",
            "description": text,                       # AUTO comment
            "surveyor_notes": notes,                   # freehand, per point
            "max_temp": max_t, "min_temp": min_t, "avg_temp": avg_t, "max_excess": max_excess,
            "severity": severity,
            "image_count": len(set(s["image_name"] for s in group)),
            "images": images,
            "recommendations": self.recommendations.get(spot_type, {}),
        }

    # ------------------------------------------------------------------ summary
    @staticmethod
    def executive_summary(findings: List[Dict]) -> Dict:
        counts = {s: 0 for s in SEVERITY_ORDER}
        for f in findings:
            counts[f["severity"]] = counts.get(f["severity"], 0) + 1
        highest = next((s for s in reversed(SEVERITY_ORDER) if counts[s]), "low")
        return {
            "total_findings": len(findings),
            "highest_severity": highest.capitalize(),
            "severity_breakdown": counts,
            "critical_count": counts["critical"],
            "high_count": counts["high"],
        }

    @staticmethod
    def recommendations_list(findings: List[Dict]) -> List[Dict]:
        prio = {"high": 0, "medium": 1, "low": 2}
        recs = []
        for f in findings:
            r = f.get("recommendations") or {}
            if r:
                recs.append({
                    "spot_number": f["spot_number"], "type": f["type"],
                    "advice": r.get("advice", []), "savings": r.get("savings", ""),
                    "priority": r.get("priority", "medium"), "severity": f["severity"],
                })
        recs.sort(key=lambda x: (prio.get(x["priority"], 2),
                                 -SEVERITY_ORDER.index(x["severity"])))
        return recs

    # ------------------------------------------------------------------ report
    def build_report(self, batch_id: str, labels: Dict, property_address: str = "",
                     inspector_name: str = "", now: Optional[datetime] = None) -> Dict:
        now = now or datetime.now()
        spots = labels.get("spots", [])
        image_notes = {k: v for k, v in (labels.get("image_notes") or {}).items()}

        findings = [self.build_finding(t, n, g, image_notes)
                    for (t, n), g in self.group_findings(spots).items()]

        return {
            "batch_id": batch_id,
            "property_address": property_address or labels.get("property_address") or "Not specified",
            "inspector_name": inspector_name or labels.get("inspector_name") or "Not specified",
            "survey_date": now.strftime("%Y-%m-%d"),
            "survey_time": now.strftime("%H:%M"),
            "general_notes": (labels.get("general_notes") or "").strip(),   # freehand, survey-wide
            "image_notes": [
                {"image_name": k, "labeled_image": labeled_image_name(k), "note": v.strip()}
                for k, v in image_notes.items() if (v or "").strip()
            ],
            "summary": self.executive_summary(findings),
            "findings": findings,
            "recommendations": self.recommendations_list(findings),
        }
