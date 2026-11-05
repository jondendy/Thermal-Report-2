"""Shared wording and colours: severity levels, point abbreviations, detection steps.

Severity colours are sampled from the 'inferno' colour scale (dark purple -> orange -> pale
yellow), the same scale the thermal pictures use, so a marker's colour reads the same way as
the picture behind it. Inferno stays distinguishable for all common kinds of colour blindness
(it never relies on a green/red difference).
"""
from __future__ import annotations

SEVERITY_ORDER = ["low", "medium", "high", "very_high"]
SEVERITY_LABELS = {"low": "Low", "medium": "Medium", "high": "High", "very_high": "Very high"}
SEVERITY_COLOURS = {"low": "#420A68", "medium": "#A82E5F", "high": "#F37819", "very_high": "#F4E156"}
SEVERITY_TEXT = {"low": "#FFFFFF", "medium": "#FFFFFF", "high": "#000000", "very_high": "#000000"}

# older saved surveys used a different top level name
_LEGACY = {"critical": "very_high", "very high": "very_high", "veryhigh": "very_high"}


def normalise_severity(value, default: str = "low") -> str:
    v = _LEGACY.get(str(value or "").lower().strip(), str(value or "").lower().strip())
    return v if v in SEVERITY_ORDER else default


def severity_label(value) -> str:
    return SEVERITY_LABELS[normalise_severity(value)]


def severity_legend():
    return [{"key": k, "label": SEVERITY_LABELS[k], "colour": SEVERITY_COLOURS[k],
             "text": SEVERITY_TEXT[k]} for k in SEVERITY_ORDER]


# How many automatic points are proposed. Left = fewer, right = more.
SENSITIVITY_ORDER = ["lowest", "low", "medium", "high", "highest"]
SENSITIVITY_LABELS = {"lowest": "Fewest", "low": "Fewer", "medium": "Standard",
                      "high": "More", "highest": "Most"}

SPOT_ABBR = {"Wall": "Wa", "Window": "Wi", "Door": "D", "Eaves": "E", "Vent": "V",
             "Roof": "R", "Chimney": "C", "Porch": "P"}


def short_id(spot_type: str, number) -> str:
    """'Wi1' for Window #1, 'D2' for Door #2."""
    abbr = SPOT_ABBR.get(spot_type) or (spot_type[:1].upper() if spot_type else "?")
    return f"{abbr}{number}"
