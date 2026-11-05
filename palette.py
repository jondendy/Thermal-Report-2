"""Draw the thermal picture from the temperature data, in one fixed colour scale.

Cameras bake their own palette into the JPEG (rainbow, iron, ...), so two surveys taken with
different camera settings look completely different. Drawing the picture here from the
radiometric temperatures gives every survey the same look, and lets us choose a scale that
grades smoothly from dark (cold) to light (warm) without relying on green.

PALETTE (env): inferno (default) | cividis | grey | camera (keep the camera's own picture).
"""
from __future__ import annotations

from typing import Dict, Optional

import numpy as np
from PIL import Image

PALETTES = {"inferno": "inferno", "cividis": "cividis", "grey": "gray"}
CAMERA = "camera"
DEFAULT = "inferno"


def valid(name: str) -> str:
    name = str(name or "").lower().strip()
    return name if name in PALETTES or name == CAMERA else DEFAULT


def _cmap(name: str):
    from matplotlib import colormaps
    return colormaps[PALETTES[name]]


def display_range(temp: np.ndarray, low_pct: float = 1.0, high_pct: float = 99.0):
    """Temperature range mapped onto the colour scale. Percentiles, so one hot pixel or a cold
    sky does not squash everything else into a single colour."""
    finite = np.asarray(temp, dtype=float)[np.isfinite(temp)]
    if finite.size < 2:
        return 0.0, 1.0
    lo, hi = np.percentile(finite, [low_pct, high_pct])
    if hi - lo < 2.0:                                   # an almost uniform scene: keep it calm
        mid = (hi + lo) / 2.0
        lo, hi = mid - 1.0, mid + 1.0
    return float(lo), float(hi)


def render(temp: np.ndarray, palette: str = DEFAULT, target_width: int = 640) -> Image.Image:
    palette = valid(palette)
    if palette == CAMERA:
        raise ValueError("the camera palette is not drawn here")
    temp = np.asarray(temp, dtype=float)
    lo, hi = display_range(temp)
    norm = np.clip((np.nan_to_num(temp, nan=lo) - lo) / (hi - lo), 0.0, 1.0)
    rgb = (_cmap(palette)(norm)[..., :3] * 255).astype(np.uint8)
    img = Image.fromarray(rgb, "RGB")
    scale = max(1, round(target_width / temp.shape[1]))
    if scale > 1:
        img = img.resize((temp.shape[1] * scale, temp.shape[0] * scale), Image.BICUBIC)
    return img


def legend(temp: np.ndarray, palette: str = DEFAULT) -> Optional[Dict]:
    """Scale bar description for the editor and report: lowest / highest temperature and a
    CSS gradient in the same colours. None for the camera palette (its scale is unknown)."""
    palette = valid(palette)
    if palette == CAMERA:
        return None
    import matplotlib.colors as mcolors
    lo, hi = display_range(temp)
    cmap = _cmap(palette)
    stops = ", ".join(mcolors.to_hex(cmap(x)) for x in np.linspace(0, 1, 9))
    return {"lo": round(lo, 1), "hi": round(hi, 1), "css": f"linear-gradient(to right, {stops})",
            "palette": palette}
