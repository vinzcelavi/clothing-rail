"""Config loading and color helpers shared by the Blender scripts."""

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BUILD = ROOT / "build"
TEXTURES = BUILD / "textures"


def load(path=None):
    path = Path(path) if path else ROOT / "config" / "rail.json"
    return json.loads(path.read_text())


def _layout(cfg):
    """(garment, featured?) for every slot, left to right: fillers, then the
    featured items with `fillers_between` (one list per pair of consecutive
    items) between them, then fillers."""
    items = cfg["items"]
    between = cfg.get("fillers_between", [[] for _ in items[1:]])
    if len(between) != len(items) - 1:
        raise ValueError(f"fillers_between needs {len(items) - 1} lists, one per pair of consecutive items")
    layout = [(garment, False) for garment in cfg["fillers_before"]]
    for i, item in enumerate(items):
        if i:
            layout += [(garment, False) for garment in between[i - 1]]
        layout.append((item, True))
    return layout + [(garment, False) for garment in cfg["fillers_after"]]


def slots(cfg):
    """Every garment on the rail, left to right."""
    return [garment for garment, _ in _layout(cfg)]


def featured(cfg):
    """Slot indices of the featured items, in the order they face the camera."""
    return [k for k, (_, is_featured) in enumerate(_layout(cfg)) if is_featured]


def _srgb_to_linear(c):
    return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4


def hex_to_linear(hex_color):
    """'#RRGGBB' (sRGB) to a linear RGBA tuple, as Blender color sockets expect."""
    h = hex_color.lstrip("#")
    r, g, b = (int(h[i : i + 2], 16) / 255.0 for i in (0, 2, 4))
    return (_srgb_to_linear(r), _srgb_to_linear(g), _srgb_to_linear(b), 1.0)
