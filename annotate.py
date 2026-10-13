"""Draw labelled heat points onto a thermal image.

Replaces ThermalAnalyzer.label_hot_spots for the merged app because that method
(a) assumed a 120x160 sensor, (b) used a font (arial.ttf) that does not exist on most
Linux servers and (c) could only draw automatic spots, with no type / number labels.
"""
from __future__ import annotations

import io
import os
from typing import Dict, Iterable, Sequence, Tuple

from PIL import Image, ImageDraw, ImageFont

SEVERITY_COLOURS = {
    "low": "#FFFF00",
    "medium": "#FFA500",
    "high": "#FF4500",
    "critical": "#FF0000",
}


def _font(size: int):
    """DejaVu ships with matplotlib, so it exists wherever the app's requirements do."""
    try:
        import matplotlib
        path = os.path.join(matplotlib.get_data_path(), "fonts", "ttf", "DejaVuSans.ttf")
        return ImageFont.truetype(path, size)
    except Exception:
        return ImageFont.load_default()


def spot_label(spot: Dict) -> str:
    """'Wall #2  14.3°C' for a labelled point, '? 14.3°C' while still unlabelled."""
    temp = spot.get("temperature")
    temp_txt = f"{float(temp):.1f}°C" if temp is not None else ""
    if spot.get("type") and spot.get("spot_number"):
        return f"{spot['type']} #{spot['spot_number']}  {temp_txt}".strip()
    return f"? {temp_txt}".strip()


def render_labeled_image(image_path: str, array_shape: Tuple[int, int],
                         spots: Iterable[Dict]) -> Image.Image:
    """Return a copy of the image with every spot marked.

    ``spot['location']`` is (row, col) in the temperature array, whose shape is
    ``array_shape``; it is scaled up to the pixel size of the picture.
    """
    img = Image.open(image_path).convert("RGB")
    spots = list(spots)
    draw = ImageDraw.Draw(img)
    rows, cols = array_shape
    sx, sy = img.width / float(cols), img.height / float(rows)
    font = _font(max(14, img.width // 40))
    small = max(6, img.width // 80)
    placed = []                                   # label rectangles already drawn

    for spot in spots:
        row, col = spot["location"]
        x, y = int(col * sx), int(row * sy)
        colour = SEVERITY_COLOURS.get(spot.get("severity", "medium"), "#FF0000")
        arm = max(8, img.width // 60)
        draw.line([(x - arm, y), (x + arm, y)], fill=colour, width=2)
        draw.line([(x, y - arm), (x, y + arm)], fill=colour, width=2)
        radius = arm + small
        draw.ellipse([(x - radius, y - radius), (x + radius, y + radius)],
                     outline=colour, width=2)

        text = spot_label(spot)
        box = draw.textbbox((0, 0), text, font=font)
        w, h = box[2] - box[0], box[3] - box[1]
        tx = min(max(2, x + radius + 4), max(2, img.width - w - 6))
        ty = min(max(2, y - h // 2), max(2, img.height - h - 6))
        # nudge the label down (or up, at the bottom edge) until it no longer covers another label
        step = h + 8 if ty + 2 * (h + 8) < img.height else -(h + 8)
        for _ in range(len(spots)):
            rect = (tx - 3, ty - 3, tx + w + 3, ty + h + 5)
            if not any(rect[0] < o[2] and rect[2] > o[0] and rect[1] < o[3] and rect[3] > o[1]
                       for o in placed):
                break
            ty = min(max(2, ty + step), max(2, img.height - h - 6))
        placed.append((tx - 3, ty - 3, tx + w + 3, ty + h + 5))
        draw.rectangle([(tx - 3, ty - 3), (tx + w + 3, ty + h + 5)], fill="black")
        draw.text((tx, ty), text, fill=colour, font=font)

    banner = f"Heat points: {len(spots)}"
    box = draw.textbbox((8, 8), banner, font=font)
    draw.rectangle([(4, 4), (box[2] + 4, box[3] + 4)], fill="black", outline="white")
    draw.text((8, 8), banner, fill="white", font=font)
    return img


def to_jpeg_bytes(img: Image.Image, quality: int = 88) -> bytes:
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=quality)
    return buf.getvalue()
