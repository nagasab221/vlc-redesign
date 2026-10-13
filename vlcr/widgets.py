"""Painted controls that follow the redesign's components.

Sizes and colours come from the Figma components (Icon Wrapper, play, Current Time,
Duration, volume, Toggle, Values, Dropdown, option, Button...).
"""

import math

from PyQt5.QtCore import (QEasingCurve, QEvent, QPoint, QPointF, QPropertyAnimation, QRect, QRectF, QSize, QTimer,
                          Qt, pyqtProperty, pyqtSignal)
from PyQt5.QtGui import (QColor, QCursor, QFontMetrics, QLinearGradient, QPainter, QPainterPath, QPen, QPixmap,
                         QPolygonF)
from PyQt5.QtWidgets import (QAbstractButton, QApplication, QColorDialog, QFrame, QHBoxLayout, QLabel, QLineEdit,
                             QSizePolicy, QToolTip, QVBoxLayout, QWidget)

from . import icons, theme
from .theme import c


def rounded(rect, r):
    path = QPainterPath()
    path.addRoundedRect(QRectF(rect), r, r)
    return path


def paint_shadow(p, rect, radius, spread=14, alpha=0.38, dy=6):
    """Cheap soft shadow: stacked translucent rounded rects."""
    p.save()
    p.setPen(Qt.NoPen)
    steps = 7
    for i in range(steps):
        t = (i + 1) / steps
        grow = spread * (1 - t)
        col = QColor(0, 0, 0)
        col.setAlphaF(alpha * t * t / steps * 2.2)
        p.setBrush(col)
        r = QRectF(rect).adjusted(-grow, -grow + dy, grow, grow + dy)
        p.drawRoundedRect(r, radius + grow, radius + grow)
    p.restore()


def elide(text, font, width, mode=Qt.ElideRight):
    return QFontMetrics(font).elidedText(text, mode, max(0, int(width)))


class Customizable:
    """Mixin: draws a dashed outline while the player is being customised."""
    customizing = False

    def paint_custom_outline(self, p):
        if self.customizing:
            pen = QPen(c("text_2"), 1, Qt.DashLine)
            pen.setDashPattern([3, 3])
            p.setPen(pen)
            p.setBrush(Qt.NoBrush)
            p.drawRect(QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5))


# ---------------------------------------------------------------------------
class IconButton(QAbstractButton, Customizable):
    """The "Icon Wrapper" component: 30×32, hover plate white 6% r=4."""

    def __init__(self, name, tip="", parent=None, size=24, box=(30, 32), variant="", checkable=False):
        super().__init__(parent)
        self.name = name
        self.variant = variant
        self.isize = size
        self.setFixedSize(*box)
        self.setToolTip(tip)
        self.setCheckable(checkable)
        self.setCursor(Qt.PointingHandCursor)
        self.setFocusPolicy(Qt.NoFocus)
        self._hover = False
        self.active = False
        self.dimmed = False
        self.color = None
        self.badge = False         # small accent dot (sort active)

    def set_icon(self, name, variant=None):
        self.name = name
        if variant is not None:
            self.variant = variant
        self.update()

    def set_active(self, on):
        self.active = bool(on)
        self.update()

    def enterEvent(self, e):
        self._hover = True
        self.update()

    def leaveEvent(self, e):
        self._hover = False
        self.update()

    def sizeHint(self):
        return self.size()

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        if (self._hover or self.isDown()) and self.isEnabled():
            p.setPen(Qt.NoPen)
            p.setBrush(QColor(0, 0, 0, 22) if theme.T.is_light() else QColor(255, 255, 255, 15))
            p.drawRoundedRect(QRectF(self.rect()), 4, 4)
        on = self.active or self.isChecked()
        variant = self.variant
        color = self.color
        if on:
            variant = ",".join(v for v in (variant, "active") if v)
            if icons.svg(self.name, variant) == icons.svg(self.name, self.variant):
                color = c("accent")
        pm = icons.pixmap(self.name, self.isize, color, variant)
        if not self.isEnabled() or self.dimmed:
            p.setOpacity(0.35)
        x = (self.width() - self.isize) / 2
        y = (self.height() - self.isize) / 2
        p.drawPixmap(QPointF(x, y), pm)
        if self.badge:
            p.setOpacity(1)
            p.setPen(Qt.NoPen)
            p.setBrush(c("accent"))
            p.drawEllipse(QPointF(x + self.isize - 3, y + 3), 2.2, 2.2)
        p.setOpacity(1)
        self.paint_custom_outline(p)


class PlayButton(QAbstractButton, Customizable):
    """54×28: orange with a play glyph while paused, grey with pause bars while playing."""

    def __init__(self, parent=None, width=54, height=28):
        super().__init__(parent)
        self.setFixedSize(width, height)
        self.setCursor(Qt.PointingHandCursor)
        self.setFocusPolicy(Qt.NoFocus)
        self.playing = False
        self._hover = False
        self.setToolTip("Play / Pause (Space)")

    def set_playing(self, on):
        self.playing = on
        self.update()

    def enterEvent(self, e):
        self._hover = True
        self.update()

    def leaveEvent(self, e):
        self._hover = False
        self.update()

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        bg = QColor("#6b6b6b") if self.playing else c("accent")
        if self._hover:
            bg = bg.lighter(112)
        if self.isDown():
            bg = bg.darker(110)
        p.setPen(Qt.NoPen)
        p.setBrush(bg)
        p.drawRoundedRect(QRectF(self.rect()), 6, 6)
        glyph = "#1b1b1b"
        pm = icons.pixmap("pause" if self.playing else "play", 24, glyph)
        p.drawPixmap(QPointF((self.width() - 24) / 2 + (0 if self.playing else 1), (self.height() - 24) / 2), pm)
        self.paint_custom_outline(p)


class TimeLabel(QWidget, Customizable):
    """Current Time / Duration (76×28, 15px). Current time turns into a jump-to field on
    click; duration toggles remaining time."""
    jump = pyqtSignal(int)
    toggled = pyqtSignal(bool)

    def __init__(self, kind="current", parent=None):
        super().__init__(parent)
        self.kind = kind
        self.ms = 0
        self.length = 0
        self.remaining = False
        self._hover = False
        self.setFixedHeight(28)
        self.setCursor(Qt.PointingHandCursor)
        self.edit = QLineEdit(self)
        self.edit.setObjectName("Bare")
        self.edit.setFont(theme.font(15))
        self.edit.hide()
        self.edit.returnPressed.connect(self._commit)
        self.edit.editingFinished.connect(self._finish)
        self.setToolTip("Click to jump to a time" if kind == "current" else "Click to show the remaining time")
        self._update_width()

    def _text(self):
        from .engine import fmt_time
        if self.kind == "duration":
            if self.remaining and self.length:
                return "-" + fmt_time(max(0, self.length - self.ms))
            return fmt_time(self.length)
        return fmt_time(self.ms)

    def _update_width(self):
        fm = QFontMetrics(theme.font(15))
        w = fm.horizontalAdvance("-00:00:00" if self.kind == "duration" else "00:00:00") + 16
        if self.length >= 36_000_000:
            w += fm.horizontalAdvance("0")
        self.setFixedWidth(max(76, w))

    def set_time(self, ms, length):
        self.ms, self.length = ms, length
        self.update()

    def set_remaining(self, on):
        self.remaining = on
        self.update()

    def enterEvent(self, e):
        self._hover = True
        self.update()

    def leaveEvent(self, e):
        self._hover = False
        self.update()

    def mousePressEvent(self, e):
        if e.button() != Qt.LeftButton or self.customizing:
            return
        if self.kind == "duration":
            self.remaining = not self.remaining
            self.toggled.emit(self.remaining)
            self.update()
        else:
            self.edit.setText(self._text())
            self.edit.setGeometry(self.rect().adjusted(8, 2, -2, -2))
            self.edit.show()
            self.edit.setFocus()
            self.edit.selectAll()
            self.update()

    def _commit(self):
        import re
        text = self.edit.text().strip().lower()
        secs = None
        m = re.fullmatch(r"(?:(\d+)\s*h)?\s*(?:(\d+)\s*m(?:in)?)?\s*(?:(\d+)\s*s)?", text)
        if text and m and any(m.groups()):
            h, mi, se = (int(g or 0) for g in m.groups())
            secs = h * 3600 + mi * 60 + se
        else:
            nums = [int(x) for x in re.findall(r"\d+", text)][:3]
            if nums:
                secs = 0
                for n in nums:
                    secs = secs * 60 + n
        if secs is not None:
            self.jump.emit(int(secs * 1000))
        self.edit.hide()
        self.update()

    def _finish(self):
        self.edit.hide()
        self.update()

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        if (self._hover or self.edit.isVisible()) and not self.customizing:
            p.setPen(Qt.NoPen)
            p.setBrush(c("field"))
            p.drawRoundedRect(QRectF(self.rect()), 6, 6)
        if not self.edit.isVisible():
            p.setFont(theme.font(15))
            p.setPen(c("icon"))
            p.drawText(self.rect(), Qt.AlignCenter, self._text())
        self.paint_custom_outline(p)


class SeekBar(QWidget, Customizable):
    """The thick seek bar (28 px): track #090909 r=6, played part accent-dark, handle 8×24.

    Modes: plain, waveform (peaks list) and subtitle timeline (cue intervals)."""
    seekRequested = pyqtSignal(float)   # fraction, on release / click
    scrubbed = pyqtSignal(float)        # fraction while dragging
    hovered = pyqtSignal(float, QPoint) # fraction, global position of the cursor on the bar
    left = pyqtSignal()

    def __init__(self, parent=None, height=28):
        super().__init__(parent)
        self.setFixedHeight(height)
        self.setMinimumWidth(80)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.setMouseTracking(True)
        self.setCursor(Qt.PointingHandCursor)
        self.value = 0.0
        self.enabled_seek = True
        self.peaks = None       # waveform: list of 0..1
        self.cues = None        # [(start_ms, end_ms)]
        self.length = 0
        self.marks = []         # fractions (bookmarks)
        self.chapters = []      # fractions
        self._drag = False
        self._hover_x = None

    def set_value(self, frac):
        if not self._drag:
            self.value = max(0.0, min(1.0, frac))
            self.update()

    def _frac(self, x):
        inner = self.width() - 4
        return max(0.0, min(1.0, (x - 2) / max(1, inner)))

    def mousePressEvent(self, e):
        if e.button() == Qt.LeftButton and self.enabled_seek and not self.customizing:
            self._drag = True
            self.value = self._frac(e.x())
            self.scrubbed.emit(self.value)
            self.update()

    def mouseMoveEvent(self, e):
        self._hover_x = e.x()
        if self._drag:
            self.value = self._frac(e.x())
            self.scrubbed.emit(self.value)
        self.hovered.emit(self._frac(e.x()), self.mapToGlobal(QPoint(e.x(), 0)))
        self.update()

    def mouseReleaseEvent(self, e):
        if self._drag:
            self._drag = False
            self.value = self._frac(e.x())
            self.seekRequested.emit(self.value)
            self.update()

    def leaveEvent(self, e):
        # Qt sends a synthetic leave when the hover card appears; ignore it while the cursor is still here.
        if self.rect().contains(self.mapFromGlobal(QCursor.pos())):
            return
        self._hover_x = None
        self.left.emit()
        self.update()

    def wheelEvent(self, e):
        e.ignore()

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        r = QRectF(self.rect())
        p.setPen(Qt.NoPen)
        p.setBrush(c("field"))
        p.drawRoundedRect(r, 6, 6)
        inner = r.adjusted(2, 2, -2, -2)
        x = inner.left() + inner.width() * self.value
        if self.peaks:
            self._paint_wave(p, inner, x)
        elif self.cues and self.length > 0:
            self._paint_cues(p, inner, x)
        else:
            if self.value > 0:
                played = QRectF(inner.left(), inner.top(), max(8.0, x - inner.left()), inner.height())
                p.setBrush(c("accent_bg"))
                p.drawRoundedRect(played, 4, 4)
        # chapter ticks and bookmarks
        if self.chapters:
            p.setBrush(c("text_3"))
            for f in self.chapters:
                cx = inner.left() + inner.width() * f
                p.drawRect(QRectF(cx - 0.5, inner.top() + 4, 1, inner.height() - 8))
        if self.marks:
            p.setBrush(c("accent"))
            for f in self.marks:
                cx = inner.left() + inner.width() * f
                p.drawEllipse(QPointF(cx, inner.top() + 4), 2.2, 2.2)
        if self._hover_x is not None and not self._drag and self.enabled_seek:
            p.setBrush(QColor(255, 255, 255, 40) if not theme.T.is_light() else QColor(0, 0, 0, 40))
            p.drawRect(QRectF(self._hover_x - 0.5, inner.top(), 1, inner.height()))
        # handle
        if self.enabled_seek:
            hx = max(inner.left(), min(inner.right() - 8, x - 8))
            p.setBrush(c("accent"))
            p.drawRoundedRect(QRectF(hx, inner.top(), 8, inner.height()), 4, 4)
        self.paint_custom_outline(p)

    def _paint_wave(self, p, inner, x):
        n = len(self.peaks)
        step = 3.0
        count = int(inner.width() / step)
        mid = inner.center().y()
        for i in range(count):
            px = inner.left() + i * step + 1
            v = self.peaks[min(n - 1, int(i / max(1, count) * n))]
            h = max(2.0, v * (inner.height() - 4))
            p.setBrush(c("accent") if px < x else c("text_3"))
            p.drawRoundedRect(QRectF(px, mid - h / 2, 1.5, h), 0.75, 0.75)

    def _paint_cues(self, p, inner, x):
        # played area tinted, cues drawn as blocks (lighter where already played)
        if x > inner.left():
            p.setBrush(c("accent_bg"))
            p.drawRoundedRect(QRectF(inner.left(), inner.top(), x - inner.left(), inner.height()), 4, 4)
        for start, end in self.cues:
            a = inner.left() + inner.width() * (start / self.length)
            b = inner.left() + inner.width() * (end / self.length)
            if b < inner.left() or a > inner.right():
                continue
            rect = QRectF(a, inner.top() + 2, max(1.5, b - a), inner.height() - 4)
            p.setBrush(theme.mix(c("accent"), c("field"), 0.55) if a < x else c("item"))
            p.drawRect(rect)


class ConeVolume(QWidget, Customizable):
    """The cone-shaped volume control (88×28): a wedge that widens to the right, filled
    with a green→yellow→orange→red gradient up to the current level."""
    changed = pyqtSignal(int)

    def __init__(self, parent=None, width=88, height=28):
        super().__init__(parent)
        self.setFixedSize(width, height)
        self.setCursor(Qt.PointingHandCursor)
        self.setMouseTracking(True)
        self.value = 80
        self.maximum = 200
        self.muted = False
        self._drag = False
        self.bubble = Bubble()

    def set_value(self, v, muted=False):
        self.value, self.muted = v, muted
        self.update()

    def _shape(self, r):
        # left edge is ~30% of the height, right edge full height
        left_h = r.height() * 0.30
        return QPolygonF([QPointF(r.left(), r.bottom() - left_h), QPointF(r.right(), r.top()),
                          QPointF(r.right(), r.bottom()), QPointF(r.left(), r.bottom())])

    def _value_at(self, x):
        return int(round(max(0.0, min(1.0, x / self.width())) * self.maximum))

    def mousePressEvent(self, e):
        if e.button() == Qt.LeftButton and not self.customizing:
            self._drag = True
            self.changed.emit(self._value_at(e.x()))
            self._show_bubble()

    def mouseMoveEvent(self, e):
        if self._drag:
            self.changed.emit(self._value_at(e.x()))
        self._show_bubble()

    def mouseReleaseEvent(self, e):
        self._drag = False

    def wheelEvent(self, e):
        steps = e.angleDelta().y() / 120 or e.angleDelta().x() / 120
        self.changed.emit(max(0, min(self.maximum, int(self.value + steps * 5))))
        self._show_bubble()

    def enterEvent(self, e):
        self._show_bubble()

    def leaveEvent(self, e):
        if self.rect().contains(self.mapFromGlobal(QCursor.pos())):
            return
        if not self._drag:
            self.bubble.hide_later(300)

    def _show_bubble(self):
        if self.customizing:
            return
        self.bubble.show_text("Muted" if self.muted else f"{self.value} %", self.mapToGlobal(QPoint(self.width() // 2, 0)))

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        r = QRectF(self.rect()).adjusted(0, 1, 0, -1)
        shape = self._shape(r)
        p.setPen(Qt.NoPen)
        p.setBrush(c("field"))
        p.drawPolygon(shape)
        frac = 0 if self.muted else max(0.0, min(1.0, self.value / 200.0))
        if frac > 0:
            inner = r.adjusted(2, 2, -2, -2)
            g = QLinearGradient(inner.left(), 0, inner.right(), 0)
            g.setColorAt(0.0, QColor("#95d318"))
            g.setColorAt(0.58, QColor("#b2a918"))
            g.setColorAt(0.79, QColor("#d37918"))
            g.setColorAt(1.0, QColor("#ff3737"))
            clip = QPainterPath()
            clip.addPolygon(self._shape(inner))
            p.setClipPath(clip)
            p.setBrush(g)
            p.drawRoundedRect(QRectF(inner.left(), inner.top(), inner.width() * frac, inner.height()), 2, 2)
            p.setClipping(False)
        if self.maximum < 200:
            # mark the configured maximum
            mx = r.left() + r.width() * self.maximum / 200
            p.setBrush(c("text_3"))
            p.drawRect(QRectF(mx - 0.5, r.top() + 2, 1, r.height() - 4))
        self.paint_custom_outline(p)


class Bubble(QWidget):
    """The small value bubble ("100 %") shown above the volume cone."""

    def __init__(self):
        super().__init__(None, Qt.ToolTip | Qt.FramelessWindowHint | Qt.WindowDoesNotAcceptFocus
                         | Qt.WindowTransparentForInput)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_ShowWithoutActivating)
        self.setAttribute(Qt.WA_TransparentForMouseEvents)
        self.text = ""
        self._timer = QTimer(self, singleShot=True, timeout=self.hide)

    def show_text(self, text, anchor):
        self.text = text
        fm = QFontMetrics(theme.font(14))
        w = fm.horizontalAdvance(text) + 52
        h = 37 + 24
        self.resize(w, h)
        self.move(anchor.x() - w // 2, anchor.y() - h - 2)
        self.show()
        self.raise_()
        self.update()
        self._timer.start(1500)

    def hide_later(self, ms):
        self._timer.start(ms)

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        r = QRectF(12, 4, self.width() - 24, 37)
        paint_shadow(p, r, 6, spread=10, alpha=0.4, dy=5)
        p.setPen(Qt.NoPen)
        p.setBrush(c("base", 0.97))
        p.drawRoundedRect(r, 6, 6)
        p.setFont(theme.font(14))
        p.setPen(c("text"))
        p.drawText(r, Qt.AlignCenter, self.text)


class Toggle(QAbstractButton):
    """ON/OFF switch, 68×32."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setCheckable(True)
        self.setFixedSize(68, 32)
        self.setCursor(Qt.PointingHandCursor)
        self.setFocusPolicy(Qt.TabFocus)

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        if not self.isEnabled():
            p.setOpacity(0.35)
        p.setPen(Qt.NoPen)
        p.setBrush(c("field"))
        p.drawRoundedRect(QRectF(self.rect()), 6, 6)
        p.setFont(theme.font(12))
        if self.isChecked():
            p.setBrush(c("accent_bg"))
            p.drawRoundedRect(QRectF(3, 3, 33, 26), 3, 3)
            p.setBrush(c("accent_2"))
            p.drawRoundedRect(QRectF(39, 3, 26, 26), 3, 3)
            p.setPen(c("accent"))
            p.drawText(QRectF(3, 3, 33, 26), Qt.AlignCenter, "ON")
        else:
            p.setBrush(c("item"))
            p.drawRoundedRect(QRectF(32, 3, 33, 26), 3, 3)
            p.setBrush(c("knob_off"))
            p.drawRoundedRect(QRectF(3, 3, 26, 26), 3, 3)
            p.setPen(c("text_2"))
            p.drawText(QRectF(32, 3, 33, 26), Qt.AlignCenter, "OFF")


class Segmented(QWidget):
    """The "Values" component: options side by side, the selected one accent-tinted."""
    changed = pyqtSignal(object)

    def __init__(self, options, value=None, parent=None, height=44, vertical=False):
        super().__init__(parent)
        self.options = [(o, o) if not isinstance(o, tuple) else o for o in options]
        self.value = value if value is not None else self.options[0][0]
        self.vertical = vertical
        self.setFixedHeight(height)
        self.setCursor(Qt.PointingHandCursor)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)

    def set_value(self, v):
        self.value = v
        self.update()

    def _rects(self):
        r = QRectF(self.rect()).adjusted(2, 2, -2, -2)
        n = len(self.options)
        w = (r.width() - 2 * (n - 1)) / n
        return [QRectF(r.left() + i * (w + 2), r.top(), w, r.height()) for i in range(n)]

    def mousePressEvent(self, e):
        if not self.isEnabled():
            return
        for (val, _), rect in zip(self.options, self._rects()):
            if rect.contains(e.pos()):
                if val != self.value:
                    self.value = val
                    self.update()
                    self.changed.emit(val)
                return

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        if not self.isEnabled():
            p.setOpacity(0.4)
        p.setPen(Qt.NoPen)
        p.setBrush(c("field"))
        p.drawRoundedRect(QRectF(self.rect()), 6, 6)
        p.setFont(theme.font(14))
        for (val, label), rect in zip(self.options, self._rects()):
            sel = val == self.value
            p.setPen(Qt.NoPen)
            p.setBrush(c("accent_bg") if sel else c("item"))
            p.drawRoundedRect(rect, 4, 4)
            p.setPen(c("accent") if sel else c("text_2"))
            p.drawText(rect, Qt.AlignCenter, label)

    def sizeHint(self):
        fm = QFontMetrics(theme.font(14))
        w = sum(fm.horizontalAdvance(l) + 40 for _, l in self.options)
        return QSize(w, self.height())


class StartVolume(QWidget):
    """Startup volume: "Remember last volume" or "Always starts at: N%"."""
    changed = pyqtSignal(int)        # -1 = remember

    def __init__(self, value=-1, parent=None):
        super().__init__(parent)
        self.value = value
        self.setFixedHeight(78)
        self.setCursor(Qt.PointingHandCursor)
        self.field = ValueField("", self, suffix="%")
        self.field.setFixedSize(182, 32)
        self.field.editingFinished.connect(self._typed)
        self._layout_field()

    def resizeEvent(self, e):
        self._layout_field()

    def _layout_field(self):
        self.field.move(self.width() - 2 - 182, 40 + 2)
        self.field.setText(f"{self.value if self.value >= 0 else 100}%")

    def _typed(self):
        try:
            v = int(self.field.text().strip().rstrip("%"))
        except ValueError:
            return
        self.value = max(0, min(200, v))
        self.changed.emit(self.value)
        self.update()

    def mousePressEvent(self, e):
        if e.y() < 39:
            self.value = -1
        elif self.value < 0:
            self.value = 100
        self._layout_field()
        self.changed.emit(self.value)
        self.update()

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setPen(Qt.NoPen)
        p.setBrush(c("field"))
        p.drawRoundedRect(QRectF(self.rect()), 6, 6)
        top = QRectF(2, 2, self.width() - 4, 36)
        bot = QRectF(2, 40, self.width() - 4, 36)
        remember = self.value < 0
        p.setBrush(c("accent_bg") if remember else c("item"))
        p.drawRoundedRect(top, 4, 4)
        p.setBrush(c("item") if remember else c("accent_bg"))
        p.drawRoundedRect(bot, 4, 4)
        p.setFont(theme.font(14))
        p.setPen(c("accent") if remember else c("text_2"))
        p.drawText(top.adjusted(12, 0, 0, 0), Qt.AlignVCenter | Qt.AlignLeft, "Remember last volume")
        p.setPen(c("text_2") if remember else c("accent"))
        p.drawText(bot.adjusted(12, 0, 0, 0), Qt.AlignVCenter | Qt.AlignLeft, "Always starts at:")
        self.field.setEnabled(not remember)


class ValueField(QLineEdit):
    """Dark value box (#090909 r=6) used for numbers and short text."""

    def __init__(self, text="", parent=None, suffix="", height=32):
        super().__init__(text, parent)
        self.suffix = suffix
        self.setFixedHeight(height)
        self.setFont(theme.font(14))


class NumberField(ValueField):
    """A ValueField holding a number with a unit ("-4.000 s", "1x", "0 deg")."""
    valueChanged = pyqtSignal(float)

    def __init__(self, value=0.0, minimum=-1e9, maximum=1e9, step=1.0, decimals=0, suffix="", parent=None, height=32):
        super().__init__("", parent, suffix, height)
        self.minimum, self.maximum, self.step, self.decimals = minimum, maximum, step, decimals
        self.value = value
        self._show()
        self.editingFinished.connect(self._commit)

    def _fmt(self, v):
        return f"{v:.{self.decimals}f}{self.suffix}"

    def _show(self):
        if not self.hasFocus():
            self.setText(self._fmt(self.value))

    def set_value(self, v):
        self.value = v
        self._show()

    def _commit(self):
        txt = self.text().replace(self.suffix.strip(), "").strip().replace(",", ".")
        try:
            v = float(txt)
        except ValueError:
            self._show()
            return
        v = max(self.minimum, min(self.maximum, v))
        changed = abs(v - self.value) > 1e-9
        self.value = v
        self.setText(self._fmt(v))
        if changed:
            self.valueChanged.emit(v)

    def wheelEvent(self, e):
        if not self.hasFocus():
            e.ignore()
            return
        d = 1 if e.angleDelta().y() > 0 else -1
        v = max(self.minimum, min(self.maximum, self.value + d * self.step))
        if v != self.value:
            self.value = v
            self.setText(self._fmt(v))
            self.valueChanged.emit(v)


class _Swatch(QAbstractButton):
    def __init__(self, field):
        super().__init__(field)
        self.field = field

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setPen(QPen(QColor(255, 255, 255, 54), 1.5))
        p.setBrush(QColor(self.field.color))
        p.drawRoundedRect(QRectF(0.75, 0.75, self.width() - 1.5, self.height() - 1.5), 2, 2)


class ColorField(QWidget):
    """Hex colour field with a swatch (subtitle colours)."""
    changed = pyqtSignal(str)

    def __init__(self, color="#FFFFFF", parent=None, height=32):
        super().__init__(parent)
        self.setFixedHeight(height)
        self.color = color.upper()
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        self.edit = ValueField(self.color, self, height=height)
        self.edit.editingFinished.connect(self._typed)
        lay.addWidget(self.edit)
        self.swatch = _Swatch(self)
        self.swatch.setCursor(Qt.PointingHandCursor)
        self.swatch.setFixedSize(22, 22)
        self.swatch.clicked.connect(self._pick)

    def resizeEvent(self, e):
        self.swatch.move(self.width() - 29, (self.height() - 22) // 2)
        self.swatch.raise_()

    def set_color(self, col):
        self.color = col.upper()
        self.edit.setText(self.color)
        self.swatch.update()

    def _typed(self):
        t = self.edit.text().strip()
        if not t.startswith("#"):
            t = "#" + t
        if QColor(t).isValid() and len(t) in (4, 7):
            self.set_color(QColor(t).name())
            self.changed.emit(self.color)
        else:
            self.edit.setText(self.color)

    def _pick(self):
        col = QColorDialog.getColor(QColor(self.color), self.window(), "Choose a colour")
        if col.isValid():
            self.set_color(col.name())
            self.changed.emit(self.color)


class Slider(QWidget):
    """Horizontal range slider: 16 px track (#090909, 2 px inset), accent-dark fill,
    tick marks, 8×24 thumb and optional labels underneath."""
    changed = pyqtSignal(float)          # while dragging
    released = pyqtSignal(float)

    def __init__(self, minimum=0.0, maximum=1.0, value=0.0, step=0.0, ticks=0, labels=None, parent=None,
                 centered=False, thumb="accent", track=16, mark=None):
        super().__init__(parent)
        self.minimum, self.maximum, self.step = float(minimum), float(maximum), float(step)
        self.value = float(value)
        self.ticks = ticks
        self.labels = labels or []
        self.centered = centered
        self.thumb_color = thumb
        self.track_h = track
        self.mark = mark
        self._drag = False
        self.setFixedHeight(24 + (19 if self.labels else 0))
        self.setCursor(Qt.PointingHandCursor)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)

    def set_value(self, v):
        if not self._drag:
            self.value = float(v)
            self.update()

    def _frac(self):
        span = self.maximum - self.minimum
        return 0 if span <= 0 else (self.value - self.minimum) / span

    def _val(self, x):
        f = max(0.0, min(1.0, (x - 4) / max(1, self.width() - 8)))
        v = self.minimum + f * (self.maximum - self.minimum)
        if self.step:
            v = round((v - self.minimum) / self.step) * self.step + self.minimum
        return max(self.minimum, min(self.maximum, round(v, 6)))

    def mousePressEvent(self, e):
        if e.button() == Qt.LeftButton and self.isEnabled():
            self._drag = True
            self.value = self._val(e.x())
            self.changed.emit(self.value)
            self.update()

    def mouseMoveEvent(self, e):
        if self._drag:
            v = self._val(e.x())
            if v != self.value:
                self.value = v
                self.changed.emit(v)
                self.update()

    def mouseReleaseEvent(self, e):
        if self._drag:
            self._drag = False
            self.released.emit(self.value)

    def mouseDoubleClickEvent(self, e):
        if self.centered:
            mid = (self.minimum + self.maximum) / 2 if self.mark is None else self.mark
            self.value = mid
            self.changed.emit(mid)
            self.released.emit(mid)
            self.update()

    def wheelEvent(self, e):
        e.ignore()

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        if not self.isEnabled():
            p.setOpacity(0.35)
        w = self.width()
        ty = (24 - self.track_h) / 2
        track = QRectF(0, ty, w, self.track_h)
        p.setPen(Qt.NoPen)
        p.setBrush(c("field"))
        p.drawRoundedRect(track, 6, 6)
        inner = track.adjusted(2, 2, -2, -2)
        x = inner.left() + inner.width() * self._frac()
        if x > inner.left():
            p.setBrush(c("accent_bg"))
            p.drawRoundedRect(QRectF(inner.left(), inner.top(), x - inner.left(), inner.height()), 4, 4)
        if self.ticks and self.ticks > 2:
            for i in range(1, self.ticks - 1):
                tx = inner.left() + inner.width() * i / (self.ticks - 1)
                p.setBrush(theme.mix(c("accent_bg"), "#000000", 0.45) if tx < x else c("item"))
                p.drawRect(QRectF(tx - 0.5, inner.center().y() - 3, 1.2, 6))
        if self.mark is not None and not self.ticks:
            mf = (self.mark - self.minimum) / (self.maximum - self.minimum)
            mx = inner.left() + inner.width() * mf
            p.setBrush(c("item"))
            p.drawRect(QRectF(mx - 0.5, inner.center().y() - 3, 1.2, 6))
        p.setBrush(c(self.thumb_color))
        p.drawRoundedRect(QRectF(max(0, min(w - 8, x - 4)), 0, 8, 24), 4, 4)
        if self.labels:
            p.setFont(theme.font(12))
            p.setPen(c("text_2"))
            n = len(self.labels)
            for i, lab in enumerate(self.labels):
                lx = w * i / (n - 1) if n > 1 else 0
                fm = QFontMetrics(p.font())
                tw = fm.horizontalAdvance(lab)
                if i == 0:
                    rx = 0
                elif i == n - 1:
                    rx = w - tw
                else:
                    rx = lx - tw / 2
                p.drawText(QRectF(rx, 28, tw + 2, 16), Qt.AlignLeft | Qt.AlignVCenter, lab)


class VSlider(QWidget):
    """Vertical equalizer band (16 px wide track, horizontal thumb)."""
    changed = pyqtSignal(float)

    def __init__(self, minimum=-20.0, maximum=20.0, value=0.0, parent=None):
        super().__init__(parent)
        self.minimum, self.maximum, self.value = minimum, maximum, value
        self.setFixedWidth(28)
        self.setMinimumHeight(120)
        self.setCursor(Qt.PointingHandCursor)
        self._drag = False

    def set_value(self, v):
        if not self._drag:
            self.value = v
            self.update()

    def _val(self, y):
        f = 1 - max(0.0, min(1.0, (y - 4) / max(1, self.height() - 8)))
        return round(self.minimum + f * (self.maximum - self.minimum), 1)

    def mousePressEvent(self, e):
        self._drag = True
        self.value = self._val(e.y())
        self.changed.emit(self.value)
        self.update()

    def mouseMoveEvent(self, e):
        if self._drag:
            v = self._val(e.y())
            if v != self.value:
                self.value = v
                self.changed.emit(v)
                self.update()

    def mouseReleaseEvent(self, e):
        self._drag = False

    def mouseDoubleClickEvent(self, e):
        self.value = 0.0
        self.changed.emit(0.0)
        self.update()

    def wheelEvent(self, e):
        e.ignore()

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        w, h = self.width(), self.height()
        track = QRectF((w - 16) / 2, 0, 16, h)
        p.setPen(Qt.NoPen)
        p.setBrush(c("field"))
        p.drawRoundedRect(track, 6, 6)
        f = (self.value - self.minimum) / (self.maximum - self.minimum)
        y = 4 + (h - 8) * (1 - f)
        inner = track.adjusted(2, 2, -2, -2)
        if y < inner.bottom():
            p.setBrush(c("accent_bg"))
            p.drawRoundedRect(QRectF(inner.left(), y, inner.width(), inner.bottom() - y), 4, 4)
        p.setBrush(c("accent"))
        p.drawRoundedRect(QRectF(0, y - 4, w, 8), 4, 4)


# --- dropdowns and menus ------------------------------------------------------
class MenuItem:
    def __init__(self, text="", callback=None, icon=None, shortcut="", checked=None, submenu=None,
                 enabled=True, separator=False, icon_variant="two", data=None, color=None):
        self.text = text
        self.callback = callback
        self.icon = icon
        self.shortcut = shortcut
        self.checked = checked          # None = not checkable
        self.submenu = submenu          # list of MenuItem or a callable returning one
        self.enabled = enabled
        self.separator = separator
        self.icon_variant = icon_variant
        self.data = data
        self.color = color


def sep():
    return MenuItem(separator=True)


class PopupMenu(QWidget):
    """The redesign's menu: #191919 at 95%, r=10, 48 px options, two-tone icons on the
    left, shortcuts in grey on the right, submenus with a chevron."""
    ROW = 48
    PAD = 4
    MARGIN = 18      # room for the shadow

    def __init__(self, items, parent=None, width=None, row=48, check_right=True, min_width=220):
        super().__init__(parent, Qt.Popup | Qt.FramelessWindowHint | Qt.NoDropShadowWindowHint)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_DeleteOnClose)
        self.setMouseTracking(True)
        self.items = items
        self.ROW = row
        self.check_right = check_right
        self.hover = -1
        self.child = None
        self.parent_menu = None
        self.font = theme.font(14)
        fm = QFontMetrics(self.font)
        has_icon = any(i.icon for i in items)
        text_w = max([fm.horizontalAdvance(i.text) for i in items] + [0])
        sc_w = max([fm.horizontalAdvance(i.shortcut) for i in items if i.shortcut] + [0])
        has_sub = any(i.submenu for i in items)
        has_check = any(i.checked is not None for i in items)
        content = 14 + (34 if has_icon else 0) + text_w + (24 + sc_w if sc_w else 0) + (34 if has_sub else 0) \
            + (34 if has_check else 0) + 14
        self.menu_w = max(min_width, width or 0, content)
        h = self.PAD * 2 + sum(9 if i.separator else self.ROW for i in items)
        self.resize(self.menu_w + 2 * self.MARGIN, h + 2 * self.MARGIN)
        self.closed_callback = None

    # geometry helpers
    def card(self):
        return QRectF(self.MARGIN, self.MARGIN, self.menu_w, self.height() - 2 * self.MARGIN)

    def _row_rects(self):
        y = self.MARGIN + self.PAD
        out = []
        for it in self.items:
            hgt = 9 if it.separator else self.ROW
            out.append(QRectF(self.MARGIN + self.PAD, y, self.menu_w - 2 * self.PAD, hgt))
            y += hgt
        return out

    def popup(self, pos, align_right=False, above=False):
        """Show with the card's top-left (or top-right) corner at pos (global)."""
        x = pos.x() - self.MARGIN - (self.menu_w if align_right else 0)
        y = pos.y() - self.MARGIN - (self.height() - 2 * self.MARGIN if above else 0)
        screen = QApplication.screenAt(pos) or QApplication.primaryScreen()
        g = screen.availableGeometry()
        x = max(g.left() - self.MARGIN, min(x, g.right() - self.width() + self.MARGIN))
        if y + self.height() - self.MARGIN > g.bottom():
            y = g.bottom() - self.height() + self.MARGIN
        y = max(g.top() - self.MARGIN, y)
        self.move(int(x), int(y))
        self.show()
        self.setFocus()

    def popup_below(self, widget, align_right=False, gap=4):
        r = widget.rect()
        pt = widget.mapToGlobal(QPoint(r.right() if align_right else r.left(), r.bottom() + gap))
        self.popup(pt, align_right=align_right)

    def popup_above(self, widget, align_right=False, gap=6):
        r = widget.rect()
        pt = widget.mapToGlobal(QPoint(r.right() if align_right else r.left(), r.top() - gap))
        self.popup(pt, align_right=align_right, above=True)

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        card = self.card()
        paint_shadow(p, card, 10, spread=15, alpha=0.46, dy=8)
        p.setPen(Qt.NoPen)
        p.setBrush(c("rail", 0.97))
        p.drawRoundedRect(card, 10, 10)
        fm = QFontMetrics(self.font)
        p.setFont(self.font)
        for i, (it, r) in enumerate(zip(self.items, self._row_rects())):
            if it.separator:
                p.setPen(Qt.NoPen)
                p.setBrush(c("line"))
                p.drawRect(QRectF(r.left() + 8, r.center().y(), r.width() - 16, 1))
                continue
            if (i == self.hover and it.enabled) or (it.checked and self.check_right and False):
                p.setPen(Qt.NoPen)
                p.setBrush(c("hover"))
                p.drawRoundedRect(r, 6, 6)
            x = r.left() + 14
            p.setOpacity(1.0 if it.enabled else 0.4)
            if it.icon:
                pm = it.icon if isinstance(it.icon, QPixmap) else icons.pixmap(it.icon, 24, it.color, it.icon_variant)
                p.drawPixmap(QPointF(x, r.center().y() - 12), pm)
                x += 34
            elif any(m.icon for m in self.items):
                x += 34
            right = r.right() - 14
            if it.submenu:
                p.drawPixmap(QPointF(right - 24, r.center().y() - 12), icons.pixmap("chevron-right", 24))
                right -= 34
            if it.checked is not None and self.check_right:
                if it.checked:
                    p.drawPixmap(QPointF(right - 24, r.center().y() - 12), icons.pixmap("check", 24, None, "active"))
                right -= 34
            if it.shortcut:
                p.setPen(c("text_2"))
                sw = fm.horizontalAdvance(it.shortcut)
                p.drawText(QRectF(right - sw, r.top(), sw + 1, r.height()), Qt.AlignVCenter | Qt.AlignRight, it.shortcut)
                right -= sw + 16
            p.setPen(c("text"))
            p.drawText(QRectF(x, r.top(), right - x, r.height()), Qt.AlignVCenter | Qt.AlignLeft,
                       elide(it.text, self.font, right - x))
        p.setOpacity(1)

    def _index_at(self, pos):
        for i, r in enumerate(self._row_rects()):
            if r.contains(QPointF(pos)) and not self.items[i].separator:
                return i
        return -1

    def mouseMoveEvent(self, e):
        i = self._index_at(e.pos())
        if i != self.hover:
            self.hover = i
            self.update()
            self._open_sub(i)

    def _open_sub(self, i):
        if self.child is not None:
            try:
                self.child.close()
            except RuntimeError:
                pass
            self.child = None
        if i < 0:
            return
        it = self.items[i]
        if it.submenu and it.enabled:
            items = it.submenu() if callable(it.submenu) else it.submenu
            sub = PopupMenu(items, self.parentWidget(), check_right=True)
            sub.parent_menu = self
            r = self._row_rects()[i]
            gp = self.mapToGlobal(QPoint(int(r.right()) + 10, int(r.top()) - self.PAD))
            sub.setWindowFlags(Qt.ToolTip | Qt.FramelessWindowHint)
            sub.setAttribute(Qt.WA_TranslucentBackground)
            sub.popup(gp)
            self.child = sub

    def mouseReleaseEvent(self, e):
        if not self.rect().contains(e.pos()):
            return
        i = self._index_at(e.pos())
        if i < 0:
            return
        it = self.items[i]
        if not it.enabled or it.submenu:
            return
        self._activate(it)

    def _activate(self, it):
        root = self
        while root.parent_menu is not None:
            root = root.parent_menu
        root.close()
        if it.callback:
            QTimer.singleShot(0, it.callback)

    def keyPressEvent(self, e):
        k = e.key()
        selectable = [i for i, it in enumerate(self.items) if not it.separator and it.enabled]
        if not selectable:
            return super().keyPressEvent(e)
        if k in (Qt.Key_Down, Qt.Key_Up):
            if self.hover not in selectable:
                self.hover = selectable[0] if k == Qt.Key_Down else selectable[-1]
            else:
                idx = selectable.index(self.hover) + (1 if k == Qt.Key_Down else -1)
                self.hover = selectable[idx % len(selectable)]
            self.update()
        elif k in (Qt.Key_Return, Qt.Key_Enter, Qt.Key_Space) and self.hover >= 0:
            it = self.items[self.hover]
            if it.submenu:
                self._open_sub(self.hover)
            else:
                self._activate(it)
        elif k == Qt.Key_Right and self.hover >= 0 and self.items[self.hover].submenu:
            self._open_sub(self.hover)
        elif k in (Qt.Key_Escape, Qt.Key_Left):
            self.close()
        else:
            super().keyPressEvent(e)

    def closeEvent(self, e):
        if self.child is not None:
            try:
                self.child.close()
            except RuntimeError:
                pass
        if self.closed_callback:
            cb, self.closed_callback = self.closed_callback, None
            QTimer.singleShot(0, cb)
        super().closeEvent(e)

    def mousePressEvent(self, e):
        if not self.card().contains(QPointF(e.pos())):
            # click outside: Qt.Popup closes itself; also close the chain
            root = self
            while root.parent_menu is not None:
                root = root.parent_menu
            root.close()
            return
        super().mousePressEvent(e)


class Dropdown(QWidget):
    """Dropdown field: #090909 frame with a #191919 face (2 px inset), text 14 px,
    chevron on the right. Options open in a list with a check on the selected one."""
    changed = pyqtSignal(object)

    def __init__(self, options, value=None, parent=None, height=48, icon_for=None, placeholder=""):
        super().__init__(parent)
        self.options = [(o, o) if not isinstance(o, tuple) else o for o in options]
        self.value = value if value is not None else (self.options[0][0] if self.options else None)
        self.icon_for = icon_for          # callable(value) -> icon name or None
        self.placeholder = placeholder
        self.setFixedHeight(height)
        self.setCursor(Qt.PointingHandCursor)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self._open = False
        self._hover = False

    def set_options(self, options, value=None):
        self.options = [(o, o) if not isinstance(o, tuple) else o for o in options]
        if value is not None:
            self.value = value
        self.update()

    def set_value(self, v):
        self.value = v
        self.update()

    def label(self):
        for val, lab in self.options:
            if val == self.value:
                return lab
        return str(self.value) if self.value not in (None, "") else self.placeholder

    def enterEvent(self, e):
        self._hover = True
        self.update()

    def leaveEvent(self, e):
        self._hover = False
        self.update()

    def mousePressEvent(self, e):
        if e.button() == Qt.LeftButton and self.isEnabled():
            self.open_menu()

    def open_menu(self):
        items = []
        for val, lab in self.options:
            ic = self.icon_for(val) if self.icon_for else None
            items.append(MenuItem(lab, lambda v=val: self._pick(v), icon=ic, checked=(val == self.value),
                                  icon_variant="two"))
        menu = PopupMenu(items, self, width=self.width() - 4, row=44)
        menu.closed_callback = self._closed
        self._open = True
        self.update()
        menu.popup(self.mapToGlobal(QPoint(2, self.height() + 2)))

    def _closed(self):
        self._open = False
        self.update()

    def _pick(self, v):
        if v != self.value:
            self.value = v
            self.update()
            self.changed.emit(v)

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        if not self.isEnabled():
            p.setOpacity(0.4)
        p.setPen(Qt.NoPen)
        p.setBrush(c("field"))
        p.drawRoundedRect(QRectF(self.rect()), 6, 6)
        face = QRectF(self.rect()).adjusted(2, 2, -2, -2)
        p.setBrush(c("hover") if self._hover else c("item"))
        p.drawRoundedRect(face, 4, 4)
        x = face.left() + 14
        if self.icon_for:
            ic = self.icon_for(self.value)
            if ic:
                p.drawPixmap(QPointF(x, face.center().y() - 12), icons.pixmap(ic, 24, None, "two"))
                x += 32
        p.setFont(theme.font(14))
        known = any(val == self.value for val, _ in self.options)
        p.setPen(c("text") if known or self.value not in (None, "") else c("text_3"))
        p.drawText(QRectF(x, face.top(), face.right() - x - 40, face.height()), Qt.AlignVCenter | Qt.AlignLeft,
                   elide(self.label(), theme.font(14), face.right() - x - 40))
        p.drawPixmap(QPointF(face.right() - 14 - 24, face.center().y() - 12),
                     icons.pixmap("chevron-up" if self._open else "chevron-down", 24))

    def sizeHint(self):
        fm = QFontMetrics(theme.font(14))
        w = max([fm.horizontalAdvance(l) for _, l in self.options] + [80]) + 80
        return QSize(w, self.height())


class Tabs(QWidget):
    """Panel tab strip: 50 px, #090909, icon (20) + 12 px label, 2 px accent underline."""
    changed = pyqtSignal(str)

    def __init__(self, tabs, parent=None, height=50):
        super().__init__(parent)
        self.tabs = tabs                  # [(key, label, icon)]
        self.current = tabs[0][0]
        self.setFixedHeight(height)
        self.setCursor(Qt.PointingHandCursor)
        self._hover = None
        self.setMouseTracking(True)

    def set_current(self, key):
        self.current = key
        self.update()

    def _rects(self):
        n = len(self.tabs)
        inner = QRectF(self.rect()).adjusted(6, 0, -6, 0)
        w = inner.width() / n
        return [QRectF(inner.left() + i * w, 0, w, self.height()) for i in range(n)]

    def mousePressEvent(self, e):
        for (key, _, _), r in zip(self.tabs, self._rects()):
            if r.contains(QPointF(e.pos())) and key != self.current:
                self.current = key
                self.update()
                self.changed.emit(key)

    def mouseMoveEvent(self, e):
        h = None
        for (key, _, _), r in zip(self.tabs, self._rects()):
            if r.contains(QPointF(e.pos())):
                h = key
        if h != self._hover:
            self._hover = h
            self.update()

    def leaveEvent(self, e):
        self._hover = None
        self.update()

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setPen(Qt.NoPen)
        p.setBrush(c("panel_head"))
        p.drawRect(self.rect())
        f = theme.font(12)
        fm = QFontMetrics(f)
        p.setFont(f)
        for (key, label, icon), r in zip(self.tabs, self._rects()):
            sel = key == self.current
            tw = 20 + 8 + fm.horizontalAdvance(label)
            x = r.center().x() - tw / 2
            variant = "two" if sel else ""
            if sel and icon in ("playback",):
                variant = "selected,two"
            p.drawPixmap(QPointF(x, 14), icons.pixmap(icon, 20, None, variant))
            p.setPen(c("text") if (sel or key == self._hover) else c("text_2"))
            p.drawText(QRectF(x + 28, 14, tw, 20), Qt.AlignVCenter | Qt.AlignLeft, label)
            if sel:
                p.setPen(Qt.NoPen)
                p.setBrush(c("accent"))
                p.drawRect(QRectF(r.left() + 8, self.height() - 2, r.width() - 16, 2))


class Button(QAbstractButton):
    """Flat buttons: kind "dark" (#090909, used for Clear/Browse/Reset/Cancel),
    "primary" (accent text on accent-dark), "ghost" (text only)."""

    def __init__(self, text, kind="dark", parent=None, height=32, icon=None, bold=True):
        super().__init__(parent)
        self.setText(text)
        self.kind = kind
        self.icon_name = icon
        self.bold = bold
        self.setFixedHeight(height)
        self.setCursor(Qt.PointingHandCursor)
        self._hover = False

    def enterEvent(self, e):
        self._hover = True
        self.update()

    def leaveEvent(self, e):
        self._hover = False
        self.update()

    def sizeHint(self):
        fm = QFontMetrics(theme.font(14, theme.SEMIBOLD if self.bold else 50))
        return QSize(fm.horizontalAdvance(self.text()) + 52 + (28 if self.icon_name else 0), self.height())

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        enabled = self.isEnabled()
        p.setPen(Qt.NoPen)
        if self.kind == "primary":
            bg = c("accent_bg") if enabled else c("item")
            if self._hover and enabled:
                bg = theme.mix(bg, c("accent"), 0.15)
            fg = c("accent") if enabled else c("text_3")
        elif self.kind == "dark":
            bg = c("field") if not self._hover else c("hover")
            fg = c("text") if enabled else c("text_3")
        elif self.kind == "face":
            bg = c("item") if not self._hover else c("hover")
            fg = c("text") if enabled else c("text_3")
        else:  # ghost
            bg = c("hover") if self._hover else QColor(0, 0, 0, 0)
            fg = c("text") if enabled else c("text_3")
        p.setBrush(bg)
        p.drawRoundedRect(QRectF(self.rect()), 4 if self.height() <= 34 else 6, 4 if self.height() <= 34 else 6)
        f = theme.font(14, theme.SEMIBOLD if self.bold else 50)
        p.setFont(f)
        fm = QFontMetrics(f)
        tw = fm.horizontalAdvance(self.text())
        x = (self.width() - tw - (26 if self.icon_name else 0)) / 2
        if self.icon_name:
            p.drawPixmap(QPointF(x, (self.height() - 20) / 2), icons.pixmap(self.icon_name, 20))
            x += 26
        p.setPen(fg)
        p.drawText(QRectF(x, 0, tw + 2, self.height()), Qt.AlignVCenter | Qt.AlignLeft, self.text())


class Expander(QAbstractButton):
    """"Advanced Options ›" / "Expand Settings ›" bars."""

    def __init__(self, text, parent=None, height=48, centered=True, open_text=None, dark=False):
        super().__init__(parent)
        self.setCheckable(True)
        self.label = text
        self.open_text = open_text or text
        self.centered = centered
        self.dark = dark
        self.setFixedHeight(height)
        self.setCursor(Qt.PointingHandCursor)

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setPen(Qt.NoPen)
        p.setBrush(c("field") if self.dark else c("rail", 0.6))
        p.drawRoundedRect(QRectF(self.rect()), 6, 6)
        f = theme.font(14)
        p.setFont(f)
        text = self.open_text if self.isChecked() else self.label
        tw = QFontMetrics(f).horizontalAdvance(text)
        chev = icons.pixmap("chevron-down" if self.isChecked() else "chevron-right", 24)
        p.setPen(c("text_2"))
        if self.centered:
            x = (self.width() - tw - 32) / 2
            p.drawText(QRectF(x, 0, tw + 2, self.height()), Qt.AlignVCenter, text)
            p.drawPixmap(QPointF(x + tw + 12, (self.height() - 24) / 2), chev)
        else:
            p.drawText(QRectF(16, 0, self.width() - 60, self.height()), Qt.AlignVCenter, text)
            p.drawPixmap(QPointF(self.width() - 16 - 24, (self.height() - 24) / 2), chev)


class InfoIcon(QLabel):
    """The small (i) next to setting labels; hover shows the explanation."""

    def __init__(self, text, parent=None):
        super().__init__(parent)
        self.setPixmap(icons.pixmap("info", 16, c("text_3")))
        self.setFixedSize(16, 16)
        self.setToolTip(f"<div style='max-width:320px'>{text}</div>")
        self.setCursor(Qt.WhatsThisCursor)


class Label(QLabel):
    def __init__(self, text="", size=14, color="text", parent=None, weight=None, wrap=False):
        super().__init__(text, parent)
        self.setFont(theme.font(size, weight or 50))
        self._color = color
        self.setWordWrap(wrap)
        self._apply()
        theme.T.changed.connect(self._apply)

    def _apply(self):
        try:
            self.setStyleSheet(f"color: {theme.hexc(self._color)}; background: transparent;")
        except RuntimeError:
            pass

    def set_color(self, color):
        self._color = color
        self._apply()


class Divider(QWidget, Customizable):
    def __init__(self, parent=None, vertical=True, length=24):
        super().__init__(parent)
        if vertical:
            self.setFixedSize(24, 28)
        else:
            self.setFixedHeight(1)
        self.vertical = vertical

    def paintEvent(self, e):
        p = QPainter(self)
        p.setPen(Qt.NoPen)
        p.setBrush(theme.mix(c("line"), "#ffffff", 0.05))
        if self.vertical:
            p.drawRect(QRectF(self.width() / 2 - 0.75, 4, 1.5, self.height() - 8))
        else:
            p.drawRect(self.rect())
        self.paint_custom_outline(p)


class HLine(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedHeight(1)

    def paintEvent(self, e):
        p = QPainter(self)
        p.fillRect(self.rect(), c("line"))


# --- floating windows --------------------------------------------------------------
class FloatingCard(QWidget):
    """Translucent, frameless tool window with a painted rounded card and shadow.
    Content goes in self.body (inset by the shadow margin)."""
    MARGIN = 20

    def __init__(self, parent=None, radius=10, fill="base", alpha=0.97, popup=False, shadow=True):
        flags = Qt.FramelessWindowHint | Qt.NoDropShadowWindowHint
        flags |= Qt.Popup if popup else Qt.Tool
        super().__init__(parent, flags)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.radius = radius
        self.fill = fill
        self.alpha = alpha
        self.shadow = shadow
        self.body = QWidget(self)
        self._lay = QVBoxLayout(self)
        m = self.MARGIN
        self._lay.setContentsMargins(m, m, m, m)
        self._lay.addWidget(self.body)

    def card_rect(self):
        m = self.MARGIN
        return QRectF(self.rect()).adjusted(m, m, -m, -m)

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        r = self.card_rect()
        if self.shadow:
            paint_shadow(p, r, self.radius, spread=15, alpha=0.46, dy=8)
        p.setPen(Qt.NoPen)
        p.setBrush(c(self.fill, self.alpha))
        p.drawRoundedRect(r, self.radius, self.radius)

    def place(self, card_global_rect):
        """Position so that the card occupies card_global_rect (QRect, global)."""
        m = self.MARGIN
        self.setGeometry(card_global_rect.adjusted(-m, -m, m, m))


class Toast(QWidget):
    """Bottom-centre notice: "You already watched till 00:12:30 [Continue Watching] ✕"."""
    action = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent, Qt.Tool | Qt.FramelessWindowHint | Qt.WindowDoesNotAcceptFocus
                         | Qt.NoDropShadowWindowHint)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_ShowWithoutActivating)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(18 + 14, 18 + 8, 18 + 8, 18 + 8)
        lay.setSpacing(14)
        self.text = QLabel()
        self.text.setFont(theme.font(14))
        self.text.setTextFormat(Qt.RichText)
        lay.addWidget(self.text)
        self.button = Button("", "accent", height=26)
        self.button.paintEvent = self._paint_action
        self.button.clicked.connect(self._on_action)
        lay.addWidget(self.button)
        self.close_btn = IconButton("x", "Dismiss", box=(26, 26), size=20)
        self.close_btn.clicked.connect(self.hide)
        lay.addWidget(self.close_btn)
        self._timer = QTimer(self, singleShot=True, timeout=self.hide)
        self.anchor = None

    def _paint_action(self, e):
        b = self.button
        p = QPainter(b)
        p.setRenderHint(QPainter.Antialiasing)
        p.setPen(Qt.NoPen)
        p.setBrush(c("accent").lighter(108) if b.underMouse() else c("accent"))
        p.drawRoundedRect(QRectF(b.rect()), 4, 4)
        p.setFont(theme.font(13, theme.SEMIBOLD))
        p.setPen(QColor("#1b1b1b"))
        p.drawText(b.rect(), Qt.AlignCenter, b.text())

    def _on_action(self):
        self.hide()
        self.action.emit()

    def show_message(self, html, action_text="", timeout=5000, anchor=None):
        try:
            self.action.disconnect()
        except TypeError:
            pass
        self.text.setStyleSheet(f"color: {theme.hexc('text')};")
        self.text.setText(html)
        self.button.setText(action_text)
        fm = QFontMetrics(theme.font(13, theme.SEMIBOLD))
        self.button.setFixedWidth(fm.horizontalAdvance(action_text) + 22)
        self.button.setVisible(bool(action_text))
        self.adjustSize()
        if anchor is not None:
            self.anchor = anchor
        self.reposition()
        self.show()
        self.raise_()
        if timeout:
            self._timer.start(timeout)
        else:
            self._timer.stop()

    def reposition(self):
        if callable(self.anchor):
            pt = self.anchor()
            self.move(pt.x() - self.width() // 2, pt.y() - self.height())

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        r = QRectF(self.rect()).adjusted(18, 18, -18, -18)
        paint_shadow(p, r, 8, spread=12, alpha=0.4, dy=6)
        p.setPen(QPen(c("line"), 1))
        p.setBrush(c("bar", 0.98))
        p.drawRoundedRect(r, 8, 8)


class OSD(QWidget):
    """On-screen feedback in the corner of the video: "Crop: 2.35:1" / "Pressed C"."""

    def __init__(self, parent=None):
        super().__init__(parent, Qt.Tool | Qt.FramelessWindowHint | Qt.WindowDoesNotAcceptFocus
                         | Qt.NoDropShadowWindowHint | Qt.WindowTransparentForInput)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_ShowWithoutActivating)
        self.setAttribute(Qt.WA_TransparentForMouseEvents)
        self.lines = []
        self._timer = QTimer(self, singleShot=True, timeout=self.hide)
        self.anchor = None

    def show_text(self, text, key="", duration=1.5, big=True):
        self.lines = [(text, 30 if big else 22, True)]
        if key:
            self.lines.append((f"Pressed {key}", 22, False))
        self.big = big
        f1 = theme.font(30, theme.SEMIBOLD)
        w = max(QFontMetrics(theme.font(size, theme.SEMIBOLD if b else 50)).horizontalAdvance(t)
                for t, size, b in self.lines) + 40
        h = sum(size + 14 for _, size, _ in self.lines) + 24
        self.resize(w, h)
        self.reposition()
        self.show()
        self.raise_()
        self.update()
        self._timer.start(int(duration * 1000))

    def reposition(self):
        if callable(self.anchor):
            pt = self.anchor()
            self.move(pt.x() - self.width(), pt.y())

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setRenderHint(QPainter.TextAntialiasing)
        y = 12
        for text, size, bold in self.lines:
            f = theme.font(size, theme.SEMIBOLD if bold else 50)
            p.setFont(f)
            rect = QRectF(0, y, self.width() - 20, size + 14)
            p.setPen(QColor(0, 0, 0, 160))
            for dx, dy in ((1, 1), (2, 2), (-1, 1), (1, -1)):
                p.drawText(rect.translated(dx, dy), Qt.AlignRight | Qt.AlignVCenter, text)
            p.setPen(QColor("#ffffff") if bold else QColor("#dddddd"))
            p.drawText(rect, Qt.AlignRight | Qt.AlignVCenter, text)
            y += size + 14


class ScrollArea(QWidget):
    """Thin wrapper so pages can call ScrollArea(widget)."""


def scroll(widget):
    from PyQt5.QtWidgets import QScrollArea
    area = QScrollArea()
    area.setWidgetResizable(True)
    area.setFrameShape(QFrame.NoFrame)
    area.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
    area.setWidget(widget)
    area.viewport().setAutoFillBackground(False)
    widget.setAutoFillBackground(False)
    return area


def clear_layout(lay):
    while lay.count():
        item = lay.takeAt(0)
        w = item.widget()
        if w is not None:
            w.hide()
            w.deleteLater()
        elif item.layout() is not None:
            clear_layout(item.layout())
