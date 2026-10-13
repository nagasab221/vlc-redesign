"""The player area: video surface (or the music view), the control bar, and the
overlay bars used in fullscreen / hide-UI / always-auto-hide modes.

Gotcha: libVLC draws into a native X window, and widgets beside it become native too;
native children can't be translucent. Overlays are therefore separate translucent tool
windows with their own bar instances.
"""

from PyQt5.QtCore import QEvent, QPoint, QPointF, QRect, QRectF, Qt, QTimer, pyqtSignal
from PyQt5.QtGui import QColor, QCursor, QFontMetrics, QImage, QPainter, QPainterPath, QPixmap, QRegion
from PyQt5.QtWidgets import QApplication, QFrame, QHBoxLayout, QStackedWidget, QVBoxLayout, QWidget

from . import icons, theme
from .controls import ControlBar, TitleBar
from .theme import c


class Surface(QFrame):
    """Mouse handling shared by the video and the music view."""
    clicked = pyqtSignal()
    doubleClicked = pyqtSignal()
    wheeled = pyqtSignal(int, int)        # dx, dy steps
    moved = pyqtSignal(QPoint)            # global position
    contextMenu = pyqtSignal(QPoint)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.ClickFocus)
        self._click = QTimer(self, singleShot=True, timeout=self.clicked.emit)
        self._press = None

    def mousePressEvent(self, e):
        if e.button() == Qt.LeftButton:
            self._press = e.globalPos()
        elif e.button() == Qt.RightButton:
            self.contextMenu.emit(e.globalPos())

    def mouseReleaseEvent(self, e):
        if e.button() == Qt.LeftButton and self._press is not None:
            if (e.globalPos() - self._press).manhattanLength() < 5:
                self._click.start(QApplication.doubleClickInterval())
            self._press = None

    def mouseDoubleClickEvent(self, e):
        if e.button() == Qt.LeftButton:
            self._click.stop()
            self.doubleClicked.emit()

    def mouseMoveEvent(self, e):
        self.moved.emit(e.globalPos())
        if self._press is not None and (e.globalPos() - self._press).manhattanLength() > 8:
            self._press = None
            self._click.stop()
            w = self.window()
            if hasattr(w, "start_move") and not w.isFullScreen():
                w.start_move()

    def wheelEvent(self, e):
        d = e.angleDelta()
        self.wheeled.emit(int(d.x() / 120) if abs(d.x()) >= 120 else (1 if d.x() > 0 else -1 if d.x() < 0 else 0),
                          int(d.y() / 120) if abs(d.y()) >= 120 else (1 if d.y() > 0 else -1 if d.y() < 0 else 0))


class VideoFrame(Surface):
    """Native child window that libVLC draws into (rounded with an X shape mask).

    libVLC's OpenGL output renders straight into this window, so Qt must never paint it:
    any Qt repaint (focus change, resize, palette change) would flash over the video.
    While nothing plays, PlayerArea shows a plain black page instead.
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WA_NativeWindow)
        self.setAttribute(Qt.WA_DontCreateNativeAncestors)
        self.setAttribute(Qt.WA_PaintOnScreen)
        self.setAttribute(Qt.WA_OpaquePaintEvent)
        self.setAttribute(Qt.WA_NoSystemBackground)
        self.setAutoFillBackground(False)
        self.radius = 9

    def paintEngine(self):
        return None

    def paintEvent(self, e):
        pass

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self._mask()

    def set_radius(self, r):
        self.radius = r
        self._mask()

    def _mask(self):
        if self.radius <= 0:
            self.clearMask()
            return
        path = QPainterPath()
        path.addRoundedRect(QRectF(self.rect()), self.radius, self.radius)
        self.setMask(QRegion(path.toFillPolygon().toPolygon()))


class MusicView(Surface):
    """Audio: a large cover over a blurred copy of it, "Title - Artist" underneath.
    With the Waveform visualizer it draws the track's waveform scrolling past instead."""

    def __init__(self, win, parent=None):
        super().__init__(parent)
        self.win = win
        self.cover = None
        self.blur = None
        self.text = ""
        self.peaks = None
        self.pos = 0.0
        self.waveform = False

    def set_media(self, cover_path, text):
        self.text = text
        pm = QPixmap(cover_path) if cover_path else QPixmap()
        self.cover = pm if not pm.isNull() else None
        self.blur = _blurred(self.cover) if self.cover else None
        self.update()

    def set_wave(self, peaks, pos, on):
        self.peaks, self.pos, self.waveform = peaks, pos, on
        if on:
            self.update()

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setRenderHint(QPainter.SmoothPixmapTransform)
        p.fillRect(self.rect(), QColor("#000000"))
        if self.blur is not None:
            s = self.blur.size().scaled(self.size(), Qt.KeepAspectRatioByExpanding)
            r = QRect(0, 0, s.width(), s.height())
            r.moveCenter(self.rect().center())
            p.setOpacity(0.55)
            p.drawPixmap(r, self.blur)
            p.setOpacity(1)
            p.fillRect(self.rect(), QColor(0, 0, 0, 120))
        h = self.height()
        side = int(min(self.width() - 80, h - 200, 738))
        text_y = None
        if self.waveform and self.peaks:
            self._paint_wave(p)
            text_y = int(h * 0.78)
        elif side > 60:
            box = QRectF((self.width() - side) / 2, max(30, (h - side - 90) / 2), side, side)
            path = QPainterPath()
            path.addRoundedRect(box, 16, 16)
            if self.cover is not None and self.win.store.get("cover_art"):
                p.setClipPath(path)
                pm = self.cover.scaled(int(side * self.devicePixelRatioF()), int(side * self.devicePixelRatioF()),
                                       Qt.KeepAspectRatioByExpanding, Qt.SmoothTransformation)
                pm.setDevicePixelRatio(self.devicePixelRatioF())
                p.drawPixmap(box.topLeft(), pm)
                p.setClipping(False)
            else:
                p.setPen(Qt.NoPen)
                p.setBrush(QColor("#000000"))
                p.drawPath(path)
                p.drawPixmap(QRectF(box.center().x() - 48, box.center().y() - 48, 96, 96).toRect(),
                             icons.pixmap("music", 96, "#3a3a3a"))
            text_y = int(box.bottom() + 70)
        if text_y is not None:
            f = theme.font(24)
            p.setFont(f)
            p.setPen(QColor("#969696"))
            p.drawText(QRectF(20, text_y - 18, self.width() - 40, 36), Qt.AlignCenter,
                       QFontMetrics(f).elidedText(self.text, Qt.ElideRight, self.width() - 40))

    def _paint_wave(self, p):
        n = len(self.peaks)
        w, h = self.width(), self.height()
        mid = h * 0.45
        span = 160                       # bins visible
        center = self.pos * n
        step = w / span
        for i in range(span):
            idx = int(center - span / 2 + i)
            if not 0 <= idx < n:
                continue
            v = self.peaks[idx]
            bh = max(3.0, v * h * 0.5)
            x = i * step
            p.setPen(Qt.NoPen)
            p.setBrush(c("accent") if idx <= center else QColor("#3a3a3a"))
            p.drawRoundedRect(QRectF(x + step * 0.2, mid - bh / 2, step * 0.6, bh), 2, 2)


def _blurred(pm, radius=8):
    img = pm.toImage().scaled(48, 48, Qt.KeepAspectRatioByExpanding, Qt.SmoothTransformation)
    for _ in range(2):
        img = img.scaled(24, 24, Qt.IgnoreAspectRatio, Qt.SmoothTransformation)
        img = img.scaled(64, 64, Qt.IgnoreAspectRatio, Qt.SmoothTransformation)
    return QPixmap.fromImage(img.scaled(640, 640, Qt.IgnoreAspectRatio, Qt.SmoothTransformation))


class StatsBox(QWidget):
    """Show Statistics: a translucent box over the top-left of the video."""

    def __init__(self, parent=None):
        super().__init__(parent, Qt.Tool | Qt.FramelessWindowHint | Qt.WindowDoesNotAcceptFocus
                         | Qt.NoDropShadowWindowHint | Qt.WindowTransparentForInput)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_ShowWithoutActivating)
        self.rows = {}
        self.resize(300, 300)

    def set_rows(self, rows):
        self.rows = rows
        fm = QFontMetrics(theme.font(12))
        self.resize(300, 24 + len(rows) * (fm.height() + 4))
        self.update()

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(0, 0, 0, 170))
        p.drawRoundedRect(QRectF(self.rect()), 8, 8)
        f = theme.font(12)
        fm = QFontMetrics(f)
        p.setFont(f)
        y = 12
        for k, v in self.rows.items():
            p.setPen(QColor("#9a9a9a"))
            p.drawText(QRectF(12, y, 150, fm.height()), Qt.AlignLeft | Qt.AlignVCenter, k)
            p.setPen(QColor("#e6e6e6"))
            p.drawText(QRectF(160, y, self.width() - 172, fm.height()), Qt.AlignRight | Qt.AlignVCenter, v)
            y += fm.height() + 4


class OverlayWindow(QWidget):
    """A translucent tool window holding a title bar or a control bar over the video."""

    def __init__(self, win, kind):
        super().__init__(win, Qt.Tool | Qt.FramelessWindowHint | Qt.NoDropShadowWindowHint | Qt.WindowDoesNotAcceptFocus)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_ShowWithoutActivating)
        self.win = win
        self.kind = kind
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        self.bar = TitleBar(win, overlay=True) if kind == "top" else ControlBar(win, overlay=True)
        lay.addWidget(self.bar)
        self.setMouseTracking(True)

    def paintEvent(self, e):
        p = QPainter(self)
        col = c("bar")
        col.setAlphaF(max(0.1, min(1.0, self.win.store.get("fs_opacity") / 100)))
        p.fillRect(self.rect(), col)
        if self.kind == "bottom":
            p.fillRect(QRect(0, 0, self.width(), 1), c("line"))


class BlankView(Surface):
    """Black, rounded area shown while nothing is playing."""

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.fillRect(self.rect(), c("bar"))
        path = QPainterPath()
        path.addRoundedRect(QRectF(self.rect()), 9, 9)
        p.fillPath(path, QColor("#000000"))


class PlayerArea(QWidget):
    """Video (or music view) above the control bar."""

    def __init__(self, win):
        super().__init__()
        self.win = win
        self.setAttribute(Qt.WA_StyledBackground, False)
        v = QVBoxLayout(self)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(0)
        self.frame = QWidget()
        self.frame_lay = QVBoxLayout(self.frame)
        self.frame_lay.setContentsMargins(10, 1, 10, 0)
        self.stack = QStackedWidget()
        self.video = VideoFrame()
        self.music = MusicView(win)
        self.blank = BlankView()
        self.stack.addWidget(self.video)
        self.stack.addWidget(self.music)
        self.stack.addWidget(self.blank)
        self.stack.setCurrentWidget(self.blank)
        self.frame_lay.addWidget(self.stack)
        v.addWidget(self.frame, 1)
        self.bar = ControlBar(win)
        v.addWidget(self.bar)

    def set_inset(self, inset, top=1):
        self.frame_lay.setContentsMargins(inset, top if inset else 0, inset, 0 if self.bar.isVisible() else inset)
        self.video.set_radius(9 if inset else 0)

    def video_rect_global(self):
        w = self.stack
        return QRect(w.mapToGlobal(QPoint(0, 0)), w.size())

    def paintEvent(self, e):
        p = QPainter(self)
        p.fillRect(self.rect(), c("bar"))
