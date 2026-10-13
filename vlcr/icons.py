"""The redesign's icon set (exported from the Figma "Icon" component, 24×24 SVG).

Icons are drawn in grey #8F8F8F with orange #D37918 / #AB6314 for active and two-tone
variants. Those colours are swapped for the current theme and accent at render time.
"""

import json

from PyQt5.QtCore import QByteArray, QRectF, Qt
from PyQt5.QtGui import QIcon, QPainter, QPixmap
from PyQt5.QtSvg import QSvgRenderer
from PyQt5.QtWidgets import QApplication

from . import theme
from .theme import ASSETS, c

with open(ASSETS / "icons.json", encoding="utf-8") as _f:
    _ICONS = json.load(_f)

# The Figma file files the speaker-level icons under "close"; give them sensible names.
ALIASES = {
    "vol-high": ("volume", "value=high"),
    "vol-medium": ("close", "value=medium"),
    "vol-low": ("close", "value=low"),
    "vol-mute": ("close", "value=none"),
    "x": ("close", ""),
    "back": ("arrow", "direction=left"),
    "forward": ("arrow", "direction=right"),
    "up": ("arrow", "direction=up"),
    "down": ("arrow", "direction=down"),
    "chevron-down": ("chevron", "direction=down"),
    "chevron-up": ("chevron", "direction=up"),
    "chevron-right": ("chevron", "direction=right"),
    "chevron-left": ("chevron", "direction=left"),
    "sidebar-left": ("sidebar", "direction=left"),
    "sidebar-right": ("sidebar", "direction=right"),
    "repeat": ("repeat", "direction=right"),
    "shuffle": ("shuffle", "direction=right"),
    "fwd10": ("forward-10-sec", ""),
    "back10": ("backward-10-sec", ""),
    "fwd15": ("forward-15-sec", ""),
    "back15": ("backward-15-sec", ""),
}

_cache = {}


def names():
    return sorted(_ICONS)


def svg(name, variant=""):
    if name in ALIASES:
        name, base_variant = ALIASES[name]
        variant = ",".join(v for v in (base_variant, variant) if v)
    variants = _ICONS.get(name)
    if not variants:
        return None
    key = _norm(variant)
    if key in variants:
        return variants[key]
    # Fall back to the closest variant: drop properties until something matches.
    parts = key.split(",") if key else []
    for i in range(len(parts) - 1, -1, -1):
        k = ",".join(parts[:i] + parts[i + 1:])
        if k in variants:
            return variants[k]
    return variants.get("") or next(iter(variants.values()))


def _norm(variant):
    if not variant:
        return ""
    short = {"active": "state=active", "selected": "state=selected", "two": "two-tone=yes"}
    parts = [short.get(p.strip(), p.strip()) for p in variant.split(",") if p.strip()]
    return ",".join(sorted(parts))


def pixmap(name, size=24, color=None, variant="", dpr=None, accent=None):
    """Render an icon. color replaces the grey stroke; accent replaces the orange."""
    if dpr is None:
        app = QApplication.instance()
        dpr = app.devicePixelRatio() if app else 1.0
    col = (color if isinstance(color, str) else color.name(color.HexArgb)) if color is not None else theme.hexc("icon")
    acc = accent or theme.T.accent
    key = (name, variant, size, col, acc, dpr, theme.T.is_light())
    pm = _cache.get(key)
    if pm is not None:
        return pm
    data = svg(name, variant)
    pm = QPixmap(int(size * dpr), int(size * dpr))
    pm.fill(Qt.transparent)
    if data:
        alpha = None
        if col.startswith("#") and len(col) == 9:          # #AARRGGBB from QColor.HexArgb
            alpha = int(col[1:3], 16) / 255
            col = "#" + col[3:]
        data = (data.replace("#8F8F8F", col).replace("#8f8f8f", col)
                .replace("#D37918", acc).replace("#d37918", acc)
                .replace("#AB6314", c("accent_2").name()).replace("#ab6314", c("accent_2").name())
                .replace("#303030", theme.hexc("thumb")).replace("#373737", theme.hexc("line")))
        r = QSvgRenderer(QByteArray(data.encode()))
        p = QPainter(pm)
        p.setRenderHint(QPainter.Antialiasing)
        if alpha is not None:
            p.setOpacity(alpha)
        r.render(p, QRectF(0, 0, size * dpr, size * dpr))
        p.end()
    pm.setDevicePixelRatio(dpr)
    if len(_cache) > 2000:
        _cache.clear()
    _cache[key] = pm
    return pm


def icon(name, size=24, color=None, variant=""):
    return QIcon(pixmap(name, size, color, variant))


def clear_cache():
    _cache.clear()


theme.T.changed.connect(clear_cache)
