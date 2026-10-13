"""First-run onboarding (1090×759): welcome + media folder, theme and accent colour,
basic keyboard shortcuts."""

import os

from PyQt5.QtCore import QRectF, Qt, pyqtSignal
from PyQt5.QtGui import QColor, QFontMetrics, QPainter, QPen
from PyQt5.QtWidgets import (QDialog, QFileDialog, QGridLayout, QHBoxLayout, QLineEdit, QStackedWidget, QVBoxLayout,
                             QWidget)

from . import icons, theme
from .theme import ACCENTS, c
from .widgets import Label, paint_shadow
from .filedialog import FileDialog


class SolidButton(QWidget):
    clicked = pyqtSignal()

    def __init__(self, text, kind="accent", width=100):
        super().__init__()
        self.text, self.kind = text, kind
        self.setFixedSize(width, 32)
        self.setCursor(Qt.PointingHandCursor)

    def mouseReleaseEvent(self, e):
        self.clicked.emit()

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setPen(Qt.NoPen)
        if self.kind == "accent":
            bg = c("accent").lighter(108) if self.underMouse() else c("accent")
            fg = QColor("#1b1b1b")
        else:
            bg = c("hover") if self.underMouse() else c("bar")
            fg = c("text")
        p.setBrush(bg)
        p.drawRoundedRect(QRectF(self.rect()), 4, 4)
        p.setFont(theme.font(16, theme.SEMIBOLD))
        p.setPen(fg)
        p.drawText(self.rect(), Qt.AlignCenter, self.text)


class ThemeTile(QWidget):
    clicked = pyqtSignal(str)

    def __init__(self, key, label):
        super().__init__()
        self.key, self.label = key, label
        self.selected = False
        self.setFixedSize(124, 160)
        self.setCursor(Qt.PointingHandCursor)

    def mouseReleaseEvent(self, e):
        self.clicked.emit(self.key)

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        box = QRectF(12, 12, 100, 100)
        if self.selected:
            p.setPen(QPen(c("accent"), 4))
            p.setBrush(Qt.NoBrush)
            p.drawRoundedRect(box.adjusted(-10, -10, 10, 10), 14, 14)
        p.setPen(Qt.NoPen)
        from PyQt5.QtGui import QPainterPath, QPolygonF
        from PyQt5.QtCore import QPointF
        path = QPainterPath()
        path.addRoundedRect(box, 10, 10)
        if self.key == "light":
            p.setBrush(QColor("#b4b4b4"))
            p.drawPath(path)
        elif self.key == "dark":
            p.setBrush(QColor("#232323"))
            p.drawPath(path)
        else:
            p.setBrush(QColor("#232323"))
            p.drawPath(path)
            p.setClipPath(path)
            p.setBrush(QColor("#b4b4b4"))
            p.drawPolygon(QPolygonF([QPointF(box.left() + 40, box.top()), QPointF(box.right(), box.top()),
                                     QPointF(box.right(), box.bottom()), QPointF(box.left() + 88, box.bottom())]))
            p.setClipping(False)
        p.setFont(theme.font(16))
        p.setPen(c("text"))
        p.drawText(QRectF(0, 128, self.width(), 26), Qt.AlignCenter, self.label)


class Swatches(QWidget):
    picked = pyqtSignal(str)

    def __init__(self, value):
        super().__init__()
        self.value = value
        self.setFixedSize(9 * 80, 64)
        self.setCursor(Qt.PointingHandCursor)

    def mouseReleaseEvent(self, e):
        i = int(e.x() // 80)
        if 0 <= i < len(ACCENTS):
            self.value = ACCENTS[i]
            self.update()
            self.picked.emit(self.value)

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        for i, col in enumerate(ACCENTS):
            cx = i * 80 + 32
            cy = 32
            sel = col.lower() == self.value.lower()
            q = QColor(col)
            if not sel:
                q = theme.mix(col, "#0e0e0e", 0.4)
            else:
                ring = QColor(col)
                ring.setAlphaF(0.35)
                p.setPen(QPen(ring, 4))
                p.setBrush(Qt.NoBrush)
                p.drawEllipse(QRectF(cx - 30, cy - 30, 60, 60))
            p.setPen(Qt.NoPen)
            p.setBrush(q)
            p.drawEllipse(QRectF(cx - 20, cy - 20, 40, 40))


class KeyCap(QWidget):
    def __init__(self, text):
        super().__init__()
        self.text = text
        fm = QFontMetrics(theme.font(16))
        self.setFixedSize(max(56, fm.horizontalAdvance(text) + 44), 50)

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        r = QRectF(1, 1, self.width() - 2, self.height() - 8)
        p.setPen(Qt.NoPen)
        p.setBrush(QColor("#151515"))
        p.drawRoundedRect(r.translated(0, 5), 8, 8)
        p.setPen(QPen(QColor("#3a3a3a"), 2))
        p.setBrush(QColor("#232323"))
        p.drawRoundedRect(r, 8, 8)
        p.setFont(theme.font(16))
        p.setPen(QColor("#cfcfcf"))
        p.drawText(r, Qt.AlignCenter, self.text)


class PreviewBars(QWidget):
    """Step 2's backdrop: a miniature player (title bar + control bar) in the chosen colours."""

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        w, h = self.width(), self.height()
        p.fillRect(self.rect(), c("video") if not theme.T.is_light() else QColor("#0a0a0a"))
        p.fillRect(QRectF(0, 0, w, 22), c("bar"))
        p.drawPixmap(8, 3, icons.pixmap("vlc", 16))
        for i, name in enumerate(("x", "fullscreen", "maximize", "minimize")):
            p.drawPixmap(w - 24 - i * 20, 3, icons.pixmap(name, 16))
        y = h - 30
        p.fillRect(QRectF(0, y, w, 30), c("bar"))
        p.drawPixmap(10, y + 7, icons.pixmap("previous", 16))
        p.setPen(Qt.NoPen)
        p.setBrush(c("accent"))
        p.drawRoundedRect(QRectF(32, y + 7, 30, 16), 3, 3)
        p.drawPixmap(39, y + 7, icons.pixmap("play", 16, "#1b1b1b"))
        p.drawPixmap(68, y + 7, icons.pixmap("next", 16))
        p.setFont(theme.font(9))
        p.setPen(c("icon"))
        p.drawText(QRectF(92, y, 50, 30), Qt.AlignVCenter, "00:02:10")
        p.setBrush(c("field"))
        p.drawRoundedRect(QRectF(140, y + 7, w - 360, 16), 3, 3)
        p.setBrush(c("accent_bg"))
        p.drawRoundedRect(QRectF(141, y + 8, 50, 14), 2, 2)
        p.setBrush(c("accent"))
        p.drawRoundedRect(QRectF(187, y + 8, 4, 14), 2, 2)
        p.setPen(c("icon"))
        p.drawText(QRectF(w - 214, y, 50, 30), Qt.AlignVCenter, "00:25:40")
        p.drawPixmap(w - 160, y + 7, icons.pixmap("vol-high", 16))
        p.setBrush(QColor("#95d318"))
        p.drawRect(QRectF(w - 140, y + 12, 20, 6))
        for i, name in enumerate(("lists", "playback settings", "subtitles", "audio")):
            p.drawPixmap(w - 24 - i * 22, y + 7, icons.pixmap(name, 16))


class Onboarding(QDialog):
    MARGIN = 24

    def __init__(self, win):
        super().__init__(win, Qt.Dialog | Qt.FramelessWindowHint | Qt.NoDropShadowWindowHint)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.win = win
        self.store = win.store
        m = self.MARGIN
        self.resize(1090 + 2 * m, 759 + 2 * m)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(m, m, m, m)
        self.stack = QStackedWidget()
        outer.addWidget(self.stack)
        self.stack.addWidget(self._welcome())
        self.stack.addWidget(self._theme())
        self.stack.addWidget(self._shortcuts())
        self._drag = None

    def _buttons(self, back=True, next_text="Next"):
        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.addStretch(1)
        if back:
            b = SolidButton("Back", "dark")
            b.clicked.connect(lambda: self.stack.setCurrentIndex(self.stack.currentIndex() - 1))
            row.addWidget(b)
            row.addSpacing(8)
        n = SolidButton(next_text)
        n.clicked.connect(self._next)
        row.addWidget(n)
        return row

    def _welcome(self):
        w = QWidget()
        v = QVBoxLayout(w)
        v.setContentsMargins(248, 110, 50, 56)
        v.setSpacing(0)
        cone = Label("")
        cone.setPixmap(icons.pixmap("vlc", 128))
        cone.setAlignment(Qt.AlignCenter)
        cone.setContentsMargins(0, 0, 198, 0)
        v.addWidget(cone)
        v.addSpacing(40)
        t = Label("Welcome to VLC!", 32, "text_hi", weight=300)
        t.setAlignment(Qt.AlignCenter)
        t.setContentsMargins(0, 0, 198, 0)
        v.addWidget(t)
        v.addSpacing(40)
        body = Label("VLC is a free and open source cross-platform multimedia player and framework that plays most "
                     "multimedia files as well as DVDs, Audio CDs, VCDs, and various streaming protocols.", 16, "text",
                     wrap=True)
        body.setFixedWidth(594)
        v.addWidget(body)
        v.addSpacing(50)
        v.addWidget(Label("Choose a Media Folder to get started with", 14, "icon"))
        v.addSpacing(14)
        row = QHBoxLayout()
        row.setSpacing(10)
        default = os.path.expanduser("~/Videos")
        if self.store["video_folders"]:
            default = self.store["video_folders"][0]
        self.folder = QLineEdit(default.replace(os.path.expanduser("~"), "~", 1))
        self.folder.setFixedSize(470, 36)
        self.folder.setStyleSheet(f"QLineEdit {{ background: #090909; border: 1px solid #222222; border-radius: 6px; "
                                  f"padding: 0 12px; font-size: 16px; color: {theme.hexc('text_hi')}; }}")
        row.addWidget(self.folder)
        browse = SolidButton("Browse", "dark", width=114)
        browse.clicked.connect(self._browse)
        row.addWidget(browse)
        row.addStretch(1)
        v.addLayout(row)
        v.addStretch(1)
        v.addLayout(self._buttons(back=False))
        return w

    def _browse(self):
        d = FileDialog.getExistingDirectory(self, "Choose a media folder", os.path.expanduser(self.folder.text()),
                                             options=self.win.dialog_options())
        if d:
            self.folder.setText(d.replace(os.path.expanduser("~"), "~", 1))

    def _theme(self):
        w = QWidget()
        v = QVBoxLayout(w)
        v.setContentsMargins(16, 16, 16, 0)
        v.setSpacing(0)
        self.preview = PreviewBars()
        self.preview.setFixedHeight(594)
        pv = QVBoxLayout(self.preview)
        pv.setContentsMargins(0, 160, 0, 0)
        t = Label("Select your preferred theme and color.", 24, "text", weight=300)
        t.set_color("text")
        t.setAlignment(Qt.AlignCenter)
        pv.addWidget(t)
        pv.addSpacing(70)
        tiles = QHBoxLayout()
        tiles.addStretch(1)
        self.tiles = {}
        for key, label in (("light", "Light"), ("dark", "Dark"), ("system", "System")):
            tile = ThemeTile(key, label)
            tile.clicked.connect(self._pick_theme)
            tiles.addWidget(tile)
            tiles.addSpacing(32)
            self.tiles[key] = tile
        tiles.addStretch(1)
        pv.addLayout(tiles)
        pv.addStretch(1)
        v.addWidget(self.preview)
        bottom = QHBoxLayout()
        bottom.setContentsMargins(36, 34, 34, 56)
        self.swatches = Swatches(self.store.get("accent"))
        self.swatches.picked.connect(self._pick_accent)
        bottom.addWidget(self.swatches)
        bottom.addStretch(1)
        bottom.addLayout(self._buttons())
        v.addLayout(bottom)
        self._pick_theme(self.store.get("theme"), apply=False)
        return w

    def _pick_theme(self, key, apply=True):
        for k, t in self.tiles.items():
            t.selected = k == key
            t.update()
        if apply:
            self.store.set("theme", key)
            self.win.apply_theme()
            self.update()

    def _pick_accent(self, col):
        self.store.set("accent", col)
        self.win.apply_theme()
        self.update()
        for t in self.tiles.values():
            t.update()
        self.preview.update()

    def _shortcuts(self):
        w = QWidget()
        v = QVBoxLayout(w)
        v.setContentsMargins(62, 70, 50, 56)
        t = Label("Learn the basic keyboard shortcuts.", 24, "text", weight=300)
        t.setAlignment(Qt.AlignCenter)
        v.addWidget(t)
        v.addSpacing(50)
        grid = QGridLayout()
        grid.setVerticalSpacing(30)
        grid.setHorizontalSpacing(0)
        keys = self.win.hotkeys()
        left = [("Play / Pause", [keys.get("play_pause", "Space")]),
                ("Seek Backward / Forward", ["←", "→"]),
                ("Previous / Next Item", ["<", ">"]),
                ("Previous / Next Frame", [",", "."]),
                ("Cycle Subtitle Track", [keys.get("sub_track", "V")]),
                ("Cycle Audio Track", [keys.get("audio_track", "B")])]
        right = [("Toggle Fullscreen", [keys.get("fullscreen", "F")]),
                 ("Increase / Decrease Volume", ["↑", "↓"]),
                 ("Show / Hide Queue", [keys.get("queue", "Q")]),
                 ("Show / Hide File Browser", [keys.get("library", "L")]),
                 ("Take a Snapshot", keys.get("snapshot", "Shift+S").upper().split("+")),
                 ("Record", keys.get("record", "Shift+R").upper().split("+"))]
        for col, rows in ((0, left), (2, right)):
            for r, (label, caps) in enumerate(rows):
                grid.addWidget(Label(label, 16, "text"), r, col)
                h = QHBoxLayout()
                h.setSpacing(8)
                for i, cap in enumerate(caps):
                    if i and len(caps) == 2 and cap not in ("S", "R"):
                        h.addWidget(Label("/", 16, "text_2"))
                    h.addWidget(KeyCap(cap))
                h.addStretch(1)
                cw = QWidget()
                cw.setLayout(h)
                grid.addWidget(cw, r, col + 1)
        grid.setColumnMinimumWidth(0, 240)
        grid.setColumnMinimumWidth(2, 240)
        grid.setColumnStretch(1, 1)
        grid.setColumnStretch(3, 1)
        v.addLayout(grid)
        v.addStretch(1)
        bottom = QHBoxLayout()
        bottom.addWidget(Label("Find the full list in the menu.", 16, "text"))
        bottom.addStretch(1)
        bottom.addLayout(self._buttons(next_text="Finish"))
        v.addLayout(bottom)
        return w

    def _next(self):
        i = self.stack.currentIndex()
        if i == 0:
            folder = os.path.expanduser(self.folder.text().strip())
            if folder and os.path.isdir(folder):
                music = os.path.basename(folder.rstrip("/")).lower() in ("music", "songs", "audio")
                self.store.add_folder("music" if music else "video", folder)
        if i < self.stack.count() - 1:
            self.stack.setCurrentIndex(i + 1)
        else:
            self.store["onboarded"] = True
            self.accept()

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        m = self.MARGIN
        r = QRectF(self.rect()).adjusted(m, m, -m, -m)
        paint_shadow(p, r, 14, spread=16, alpha=0.5, dy=8)
        p.setPen(Qt.NoPen)
        p.setBrush(QColor("#0e0e0e") if not theme.T.is_light() else c("base"))
        p.drawRoundedRect(r, 14, 14)

    def showEvent(self, e):
        par = self.parentWidget()
        if par is not None:
            g = par.window().frameGeometry()
            self.move(g.center() - self.rect().center())
        super().showEvent(e)

    def mousePressEvent(self, e):
        self._drag = e.globalPos() - self.frameGeometry().topLeft()

    def mouseMoveEvent(self, e):
        if self._drag is not None and e.buttons() & Qt.LeftButton:
            self.move(e.globalPos() - self._drag)

    def mouseReleaseEvent(self, e):
        self._drag = None

    def keyPressEvent(self, e):
        if e.key() == Qt.Key_Escape:
            self.store["onboarded"] = True
            self.reject()
        else:
            super().keyPressEvent(e)
