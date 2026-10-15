"""Automatic detection of distinctly warmer areas in a thermal image.

Why this replaces ``ThermalAnalyzer.detect_hot_spots`` for the app
------------------------------------------------------------------
The old detector flagged pixels above ``mean + k * std`` of the *whole* picture. In outdoor
shots a large cold sky / ground stretches that spread to 10-17 degC, so the threshold ended up
above every real warm area and most images found nothing.

This detector instead asks "is this area clearly warmer than the building surface around it?":

1. Cold pixels (sky, ground) are identified as far below the image median and ignored.
2. The *typical building temperature* (median of what is left) and its natural scatter
   (robust, MAD based) are measured from the picture itself.
3. Pixels that exceed that baseline by a clear margin form warm areas, which are
   grouped, filtered by size, ranked and capped.

Severity is based on how many degrees warmer than the baseline the area is, which is what a
surveyor actually cares about, rather than on statistics distorted by the sky.
"""
from __future__ import annotations

from typing import Dict, List, Tuple

import numpy as np
from scipy import ndimage

from thermal_analyzer import HotSpot

# Anything this much colder than the image median is treated as sky / ground, not building.
SKY_DROP_C = 12.0
MAX_SPOTS_PER_IMAGE = 8
EDGE_MARGIN = 2          # pixels

# min_excess : never flag an area less than this many degC above the building baseline
# k          : ... nor less than k x the wall's own warm-side scatter (so a naturally patchy
#              wall needs a bigger step than a smooth one)
# min_area   : smallest area, in thermal pixels, worth reporting
PRESETS: Dict[str, Dict[str, float]] = {
    "low":    {"min_excess": 2.0, "k": 3.0, "min_area": 6},
    "medium": {"min_excess": 1.3, "k": 1.5, "min_area": 4},
    "high":   {"min_excess": 0.9, "k": 1.0, "min_area": 3},
}

# degrees above the building baseline -> severity
SEVERITY_STEPS: List[Tuple[float, str]] = [(8.0, "critical"), (4.0, "high"), (2.0, "medium")]


def classify_excess(excess_c: float) -> str:
    for limit, name in SEVERITY_STEPS:
        if excess_c >= limit:
            return name
    return "low"


def building_baseline(temp: np.ndarray) -> Tuple[float, float, np.ndarray]:
    """Return (baseline degC, warm-side sigma degC, mask of non-sky pixels).

    baseline = the most common temperature among non-sky pixels (the histogram peak), so a
    cold foreground (ground, parked cars) cannot drag it down the way a median would.
    sigma    = natural scatter of the wall, measured only on the warm side of the baseline.
    Returns (nan, nan, mask) when the image has too little usable data.
    """
    temp = np.asarray(temp, dtype=float)
    finite = np.isfinite(temp)
    if finite.sum() < 20:
        return float("nan"), float("nan"), finite
    median = np.median(temp[finite])
    ground = finite & (temp > median - SKY_DROP_C)
    vals = temp[ground]

    edges = np.arange(vals.min(), vals.max() + 0.2, 0.2)
    if len(edges) < 3:
        base = float(np.median(vals))
    else:
        hist, edges = np.histogram(vals, bins=edges)
        hist = np.convolve(hist, np.ones(3) / 3.0, mode="same")
        base = float((edges[int(np.argmax(hist))] + edges[int(np.argmax(hist)) + 1]) / 2.0)

    near_warm = vals[(vals > base) & (vals <= base + 3.0)] - base
    sigma = float(np.median(near_warm) / 0.6745) if near_warm.size >= 10 else 0.0
    return base, sigma, ground


def detect_warm_areas(temp: np.ndarray, sensitivity: str = "medium",
                      max_spots: int = MAX_SPOTS_PER_IMAGE) -> List[HotSpot]:
    """Find areas distinctly warmer than the building surface. Strongest first."""
    preset = PRESETS.get(sensitivity, PRESETS["medium"])
    temp = np.asarray(temp, dtype=float)
    base, sigma, _ = building_baseline(temp)
    if not np.isfinite(base):
        return []

    threshold = max(preset["min_excess"], preset["k"] * sigma)
    filled = np.where(np.isfinite(temp), temp, base)
    smooth = ndimage.gaussian_filter(filled, 1.0)          # tames single-pixel noise

    mask = (smooth - base) >= threshold
    mask = ndimage.binary_closing(mask, structure=np.ones((3, 3)))   # join fragments of one area
    labels, count = ndimage.label(mask, structure=np.ones((3, 3)))

    # Pixels in the outer frame are unreliable (lens vignetting, scaling), so a point is never
    # placed there - a warm area running off the picture is marked at its hottest inner pixel.
    interior = np.zeros(temp.shape, dtype=bool)
    interior[EDGE_MARGIN:-EDGE_MARGIN, EDGE_MARGIN:-EDGE_MARGIN] = True

    spots: List[HotSpot] = []
    for lab in range(1, count + 1):
        region = labels == lab
        area = int(region.sum())
        if area < preset["min_area"]:
            continue
        values = np.where(region & interior, filled, -np.inf)
        if not np.isfinite(values.max()):
            continue
        flat = int(np.argmax(values))
        row, col = np.unravel_index(flat, temp.shape)
        peak = float(filled[row, col])
        excess = peak - base
        spot = HotSpot(location=(int(row), int(col)), temperature=peak, area_size=area,
                       severity=classify_excess(excess), excess=round(excess, 2))
        spots.append(spot)

    spots.sort(key=lambda s: -s.excess)
    return spots[:max_spots]


def severity_at(temp: np.ndarray, row: int, col: int) -> Tuple[float, str]:
    """(excess degC, severity) for one chosen pixel, on the same scale as automatic detection."""
    base, _, _ = building_baseline(temp)
    value = float(temp[row, col])
    if not np.isfinite(base) or not np.isfinite(value):
        return 0.0, "low"
    excess = value - base
    return round(excess, 2), classify_excess(excess)
