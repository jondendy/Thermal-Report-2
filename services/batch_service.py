"""Batch processing and survey data, all routed through a StorageBackend.

Layout inside the storage root (identical for local, WebDAV and SMB):

    images/batches/<batch_id>/<original>.jpg
    reports/batches/<batch_id>/results.json
    reports/batches/<batch_id>/thermal_analysis.json
    reports/batches/<batch_id>/hotspot_labels.json          (points + notes)
    reports/batches/<batch_id>/heat_loss_report_data.json
    reports/batches/<batch_id>/<stem>_temperatures.csv
    reports/batches/<batch_id>/<stem>_labeled.jpg
    reports/batches/<batch_id>/<stem>_thermal_report.html
"""
from __future__ import annotations

import hashlib
import io
import logging
import os
import re
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
from werkzeug.utils import secure_filename

import annotate
from storage import StorageBackend, StorageError, StorageNotFound

logger = logging.getLogger(__name__)

BATCH_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,80}$")
ALLOWED_EXT = {".jpg", ".jpeg"}
SEVERITIES = ["low", "medium", "high", "critical"]
MAX_NOTE = 5000


class BatchError(Exception):
    """User-facing problem (bad id, bad input). Mapped to HTTP 4xx by the app."""


def valid_batch_id(batch_id: str) -> str:
    if not isinstance(batch_id, str) or not BATCH_ID_RE.match(batch_id):
        raise BatchError("Invalid batch id")
    return batch_id


def new_batch_id(filenames: List[str]) -> str:
    digest = hashlib.md5("".join(sorted(filenames)).encode()).hexdigest()[:8]
    return f"batch_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{digest}"


def _clean_text(value: Any, limit: int = MAX_NOTE) -> str:
    return str(value or "").replace("\x00", "")[:limit]


class BatchService:
    def __init__(self, storage: StorageBackend, spot_types: List[str],
                 sensitivity: str = "medium", processor_factory=None):
        self.storage = storage
        self.spot_types = list(spot_types)
        self.sensitivity = sensitivity
        self._processor_factory = processor_factory
        self._temp_cache: Dict[Tuple[str, str], np.ndarray] = {}

    # ------------------------------------------------------------------ paths
    @staticmethod
    def rdir(batch_id: str) -> str:
        return f"reports/batches/{valid_batch_id(batch_id)}"

    @staticmethod
    def idir(batch_id: str) -> str:
        return f"images/batches/{valid_batch_id(batch_id)}"

    def _make_processor(self):
        if self._processor_factory:
            return self._processor_factory()
        from flir_processor import SimpleFLIRProcessor  # heavy import, keep lazy
        return SimpleFLIRProcessor()

    # ------------------------------------------------------------------ processing
    def process_batch(self, batch_id: str, files: List[Tuple[str, bytes]]) -> Dict:
        """files: [(original_filename, raw_bytes)]. Returns the results dict."""
        from thermal_analyzer import ThermalAnalyzer

        processor = self._make_processor()
        analyzer = ThermalAnalyzer(sensitivity=self.sensitivity)
        rdir, idir = self.rdir(batch_id), self.idir(batch_id)
        self.storage.makedirs(rdir)
        self.storage.makedirs(idir)

        results: Dict[str, Any] = {
            "batch_id": batch_id, "timestamp": datetime.now().isoformat(),
            "images": [], "summary": {"total_images": 0},
        }
        analysis_images: List[Dict] = []
        all_stats: List[Dict] = []
        used_names = set()

        for original_name, raw in files:
            name = secure_filename(original_name or "")
            if os.path.splitext(name)[1].lower() not in ALLOWED_EXT:
                continue
            base, ext = os.path.splitext(name)
            n = 2
            while name in used_names:                      # two uploads with the same name
                name, n = f"{base}_{n}{ext}", n + 1
            used_names.add(name)
            stem = os.path.splitext(name)[0]

            try:
                self.storage.write_bytes(f"{idir}/{name}", raw)
                with self.storage.local_copy(f"{idir}/{name}") as local:
                    temp, stats = processor.process_single_image(local, display=False)
                    hot_spots = analyzer.detect_hot_spots(temp)
                    spots = []
                    for i, hs in enumerate(hot_spots, 1):
                        d = hs.to_dict()
                        spots.append({
                            "spot_id": f"{name}_auto{i}", "source": "auto",
                            "location": [int(d["location"][0]), int(d["location"][1])],
                            "temperature": round(float(d["temperature"]), 2),
                            "severity": d["severity"], "area_size": int(d["area_size"]),
                        })
                    labeled = annotate.render_labeled_image(local, temp.shape, spots)
                    self.storage.write_bytes(f"{rdir}/{stem}_labeled.jpg",
                                             annotate.to_jpeg_bytes(labeled))

                self.storage.write_text(
                    f"{rdir}/{stem}_thermal_report.html",
                    analyzer.generate_report(name, hot_spots, stats))
                buf = io.StringIO()
                np.savetxt(buf, temp, delimiter=",", fmt="%.2f")
                self.storage.write_text(f"{rdir}/{stem}_temperatures.csv", buf.getvalue())

                clean_stats = {k: float(stats[k]) for k in ("min", "max", "mean", "median", "std")}
                results["images"].append({
                    "filename": name, "image_name": name, "stats": clean_stats,
                    "shape": list(temp.shape), "hot_spots": spots,
                    "hot_spot_count": len(spots),
                    "thermal_report": f"{stem}_thermal_report.html",
                    "labeled_image": f"{stem}_labeled.jpg",
                    "csv": f"{stem}_temperatures.csv",
                })
                analysis_images.append({
                    "image_name": name, "shape": list(temp.shape), "stats": clean_stats,
                    "hot_spots": spots, "labeled_image": f"{stem}_labeled.jpg",
                })
                all_stats.append(clean_stats)
            except Exception as exc:                       # one bad image must not sink the batch
                logger.exception("Failed processing %s", name)
                results["images"].append({"filename": name, "image_name": name, "error": str(exc)})

        if all_stats:
            means = [s["mean"] for s in all_stats]
            results["summary"] = {
                "total_images": len(all_stats),
                "avg_temperature": sum(means) / len(means),
                "min_temperature": min(s["min"] for s in all_stats),
                "max_temperature": max(s["max"] for s in all_stats),
            }
        self.storage.write_json(f"{rdir}/results.json", results)
        self.storage.write_json(f"{rdir}/thermal_analysis.json", {
            "batch_id": batch_id, "timestamp": results["timestamp"], "images": analysis_images})
        return results

    # ------------------------------------------------------------------ reading
    def list_batches(self) -> List[Dict]:
        out = []
        for name in sorted(self.storage.listdir("reports/batches"), reverse=True):
            if not BATCH_ID_RE.match(name):
                continue
            try:
                data = self.storage.read_json(f"reports/batches/{name}/results.json")
            except (StorageError, ValueError):
                continue
            out.append({"batch_id": name, "timestamp": data.get("timestamp"),
                        "image_count": len(data.get("images", [])),
                        "summary": data.get("summary", {})})
        return out

    def get_results(self, batch_id: str) -> Dict:
        return self.storage.read_json(f"{self.rdir(batch_id)}/results.json")

    def get_analysis(self, batch_id: str) -> Dict:
        return self.storage.read_json(f"{self.rdir(batch_id)}/thermal_analysis.json")

    def batch_exists(self, batch_id: str) -> bool:
        return self.storage.exists(f"{self.rdir(batch_id)}/results.json")

    def delete_batch(self, batch_id: str) -> None:
        self.storage.delete(self.rdir(batch_id))
        self.storage.delete(self.idir(batch_id))
        for key in [k for k in self._temp_cache if k[0] == batch_id]:
            del self._temp_cache[key]

    # ------------------------------------------------------------------ temperatures
    def temperatures(self, batch_id: str, image_name: str) -> np.ndarray:
        key = (batch_id, image_name)
        if key not in self._temp_cache:
            stem = os.path.splitext(secure_filename(image_name))[0]
            text = self.storage.read_text(f"{self.rdir(batch_id)}/{stem}_temperatures.csv")
            self._temp_cache[key] = np.loadtxt(io.StringIO(text), delimiter=",", ndmin=2)
        return self._temp_cache[key]

    def probe(self, batch_id: str, image_name: str, row: int, col: int) -> Dict:
        """Temperature + suggested severity at one point (used when adding a manual point)."""
        from thermal_analyzer import SKY_REFLECTION_CUTOFF_C, ThermalAnalyzer
        arr = self.temperatures(batch_id, image_name)
        r = int(min(max(row, 0), arr.shape[0] - 1))
        c = int(min(max(col, 0), arr.shape[1] - 1))
        temp = float(arr[r, c])
        valid = np.where(np.isfinite(arr) & (arr > SKY_REFLECTION_CUTOFF_C), arr, np.nan)
        if np.isfinite(valid).any():
            severity = ThermalAnalyzer(sensitivity=self.sensitivity)._classify_severity(
                temp, valid[np.isfinite(valid)])
        else:
            severity = "low"
        return {"row": r, "col": c, "temperature": round(temp, 2), "severity": severity}

    # ------------------------------------------------------------------ labels & notes
    def get_labels(self, batch_id: str) -> Dict:
        try:
            return self.storage.read_json(f"{self.rdir(batch_id)}/hotspot_labels.json")
        except StorageNotFound:
            return {"batch_id": batch_id, "spots": [], "image_notes": {},
                    "general_notes": "", "property_address": "", "inspector_name": ""}

    def build_edit_state(self, batch_id: str) -> Dict:
        """Everything the editing page needs: detected points merged with saved labels/notes."""
        analysis = self.get_analysis(batch_id)
        labels = self.get_labels(batch_id)
        saved = {s["spot_id"]: s for s in labels.get("spots", [])}
        images = []
        for img in analysis.get("images", []):
            name = img["image_name"]
            spots = []
            for s in img.get("hot_spots", []):
                m = saved.get(s["spot_id"], {})
                spots.append({**s, "auto_severity": s["severity"],
                              "severity": m.get("severity", s["severity"]),
                              "type": m.get("type", ""), "spot_number": m.get("spot_number"),
                              "note": m.get("note", "")})
            for m in labels.get("spots", []):
                if m.get("source") == "manual" and m.get("image_name") == name:
                    spots.append({**m, "auto_severity": m.get("auto_severity", m.get("severity", "low"))})
            images.append({"image_name": name, "shape": img["shape"], "stats": img.get("stats", {}),
                           "image_note": (labels.get("image_notes") or {}).get(name, ""),
                           "spots": spots})
        return {"batch_id": batch_id, "images": images,
                "general_notes": labels.get("general_notes", ""),
                "property_address": labels.get("property_address", ""),
                "inspector_name": labels.get("inspector_name", "")}

    def save_labels(self, batch_id: str, payload: Dict) -> Dict:
        """Validate what the browser sent and persist it. Never trusts client temperatures."""
        if not isinstance(payload, dict):
            raise BatchError("Expected a JSON object")
        analysis = self.get_analysis(batch_id)
        known = {i["image_name"]: i for i in analysis.get("images", [])}

        spots_out, image_notes = [], {}
        for img in payload.get("images", []) or []:
            name = img.get("image_name")
            if name not in known:
                continue
            note = _clean_text(img.get("image_note"))
            if note.strip():
                image_notes[name] = note
            shape = known[name]["shape"]
            auto_by_id = {s["spot_id"]: s for s in known[name].get("hot_spots", [])}
            for s in img.get("spots", []) or []:
                spot = self._clean_spot(batch_id, name, shape, s, auto_by_id)
                if spot:
                    spots_out.append(spot)

        labels = {
            "batch_id": batch_id, "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "property_address": _clean_text(payload.get("property_address"), 300),
            "inspector_name": _clean_text(payload.get("inspector_name"), 200),
            "general_notes": _clean_text(payload.get("general_notes"), 20000),
            "image_notes": image_notes, "spots": spots_out,
        }
        self.storage.write_json(f"{self.rdir(batch_id)}/hotspot_labels.json", labels)
        try:
            self.regenerate_labeled_images(batch_id, labels, analysis)
        except Exception:                                   # labels are saved; picture refresh is best-effort
            logger.exception("Could not regenerate labeled images for %s", batch_id)
        labelled = sum(1 for s in spots_out if s.get("type") and s.get("spot_number"))
        return {"total_spots": len(spots_out), "labeled_spots": labelled}

    def _clean_spot(self, batch_id, image_name, shape, s, auto_by_id) -> Optional[Dict]:
        if not isinstance(s, dict):
            return None
        source = "manual" if s.get("source") == "manual" else "auto"
        spot_id = _clean_text(s.get("spot_id"), 120)
        if source == "auto":
            base = auto_by_id.get(spot_id)
            if not base:
                return None
            location, temp, area = base["location"], base["temperature"], base.get("area_size", 0)
            auto_sev = base["severity"]
        else:
            try:
                row, col = int(s["location"][0]), int(s["location"][1])
            except (KeyError, TypeError, ValueError, IndexError):
                return None
            p = self.probe(batch_id, image_name, row, col)      # temperature comes from the data
            location, temp, area, auto_sev = [p["row"], p["col"]], p["temperature"], 0, p["severity"]
            if not spot_id:
                spot_id = f"{image_name}_manual{int(datetime.now().timestamp() * 1000)}"

        sev = s.get("severity") if s.get("severity") in SEVERITIES else auto_sev
        stype = s.get("type") if s.get("type") in self.spot_types else ""
        try:
            number = int(s.get("spot_number"))
            number = number if 1 <= number <= 99 else None
        except (TypeError, ValueError):
            number = None
        return {"spot_id": spot_id, "image_name": image_name, "source": source,
                "location": [int(location[0]), int(location[1])], "temperature": float(temp),
                "area_size": int(area), "auto_severity": auto_sev, "severity": sev,
                "type": stype, "spot_number": number, "note": _clean_text(s.get("note"), 2000)}

    def regenerate_labeled_images(self, batch_id: str, labels: Dict, analysis: Dict) -> None:
        """Redraw each *_labeled.jpg with every point (auto + manual) and its type/number."""
        rdir, idir = self.rdir(batch_id), self.idir(batch_id)
        by_image: Dict[str, List[Dict]] = {}
        for s in labels.get("spots", []):
            by_image.setdefault(s["image_name"], []).append(s)
        for img in analysis.get("images", []):
            name = img["image_name"]
            spots = by_image.get(name, img.get("hot_spots", []))
            stem = os.path.splitext(name)[0]
            with self.storage.local_copy(f"{idir}/{name}") as local:
                picture = annotate.render_labeled_image(local, tuple(img["shape"]), spots)
            self.storage.write_bytes(f"{rdir}/{stem}_labeled.jpg", annotate.to_jpeg_bytes(picture))

    # ------------------------------------------------------------------ report data
    def save_report(self, batch_id: str, report: Dict) -> None:
        self.storage.write_json(f"{self.rdir(batch_id)}/heat_loss_report_data.json", report)

    def get_report(self, batch_id: str) -> Dict:
        return self.storage.read_json(f"{self.rdir(batch_id)}/heat_loss_report_data.json")
