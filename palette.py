"""Draw the thermal picture from the temperature data, in one fixed colour scale.

Cameras bake their own palette into the JPEG (rainbow, iron, ...), so two surveys taken with
different camera settings look completely different. Drawing the picture here from the
radiometric temperatures gives every survey the same look, and lets us choose a scale that
grades smoothly from dark (cold) to light (warm) without relying on green.

PALETTE (env) is the starting style: inferno (default) | ironbow | cividis | grey | camera (keep the
camera's own picture). Each batch can then be switched to another style, and to another temperature
range, on its points page.
"""
from __future__ import annotations

from typing import Dict, Optional

import numpy as np
from PIL import Image

PALETTES = {"inferno": "inferno", "ironbow": "ironbow", "cividis": "cividis", "grey": "gray"}
CAMERA = "camera"
DEFAULT = "inferno"
# what people see in the list (all of ours run dark = cold to light = warm, with no green)
LABELS = {"inferno": "Inferno (black, purple, orange, yellow)", "ironbow": "Ironbow (black, blue, magenta, orange, white)",
          "cividis": "Cividis (dark blue to yellow)", "grey": "Grey (white is warm)",
          CAMERA: "As it came off the camera"}
# the classic thermal-camera "iron" look: black, deep blue, magenta, orange, yellow, white
IRONBOW = [(0.00, "#000004"), (0.14, "#1c0a6e"), (0.30, "#6a0a9a"), (0.46, "#b8148a"), (0.60, "#e8431f"),
           (0.74, "#f98e09"), (0.88, "#fbd324"), (1.00, "#fffbe8")]
_custom: Dict = {}


def choices() -> list:
    return [{"key": k, "label": LABELS[k]} for k in list(PALETTES) + [CAMERA]]


def valid(name: str) -> str:
    name = str(name or "").lower().strip()
    return name if name in PALETTES or name == CAMERA else DEFAULT


def _cmap(name: str):
    if name == "ironbow":
        if name not in _custom:
            from matplotlib.colors import LinearSegmentedColormap
            _custom[name] = LinearSegmentedColormap.from_list("ironbow", IRONBOW)
        return _custom[name]
    from matplotlib import colormaps
    return colormaps[PALETTES[name]]


def shared_range(temps) -> tuple:
    """One range for several pictures, so the same colour means the same temperature on all of them."""
    arrays = [np.asarray(t, dtype=float).ravel() for t in temps]
    return display_range(np.concatenate(arrays)) if arrays else (0.0, 1.0)


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


def _range(temp, lo, hi):
    """The given range when it is usable, else the picture's own."""
    try:
        if lo is not None and hi is not None and float(hi) > float(lo):
            return float(lo), float(hi)
    except (TypeError, ValueError):
        pass
    return display_range(temp)


def render(temp: np.ndarray, palette: str = DEFAULT, target_width: int = 640,
           lo: Optional[float] = None, hi: Optional[float] = None) -> Image.Image:
    """lo/hi: the temperatures at the two ends of the colour scale (default: chosen from the picture)."""
    palette = valid(palette)
    if palette == CAMERA:
        raise ValueError("the camera palette is not drawn here")
    temp = np.asarray(temp, dtype=float)
    lo, hi = _range(temp, lo, hi)
    norm = np.clip((np.nan_to_num(temp, nan=lo) - lo) / (hi - lo), 0.0, 1.0)
    rgb = (_cmap(palette)(norm)[..., :3] * 255).astype(np.uint8)
    img = Image.fromarray(rgb, "RGB")
    scale = max(1, round(target_width / temp.shape[1]))
    if scale > 1:
        img = img.resize((temp.shape[1] * scale, temp.shape[0] * scale), Image.BICUBIC)
    return img


def legend(temp: np.ndarray, palette: str = DEFAULT, lo: Optional[float] = None,
           hi: Optional[float] = None) -> Optional[Dict]:
    """Scale bar description for the editor and report: lowest / highest temperature and a
    CSS gradient in the same colours. None for the camera palette (its scale is unknown)."""
    palette = valid(palette)
    if palette == CAMERA:
        return None
    import matplotlib.colors as mcolors
    lo, hi = _range(temp, lo, hi)
    cmap = _cmap(palette)
    stops = ", ".join(mcolors.to_hex(cmap(x)) for x in np.linspace(0, 1, 9))
    return {"lo": round(lo, 1), "hi": round(hi, 1), "css": f"linear-gradient(to right, {stops})",
            "palette": palette}
