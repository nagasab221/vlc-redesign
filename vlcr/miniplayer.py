"""The mini player shown over the library (391 px card, r=14) and the picture-in-picture
window. Both borrow the player's native video frame by re-parenting it, so playback
never restarts."""

import os

from PyQt5.QtCore import QPoint, QRect, QRectF, Qt, QTimer, pyqtSignal
from PyQt5.QtGui import QColor, QCursor, QPainter
from PyQt5.QtWidgets import QApplication, QHBoxLayout, QVBoxLayout, QWidget

from . import icons, theme
from .engine import display_name, is_url
from .panels import QueueList, ROLE_INDEX, ROLE_KIND
from .theme import c
from .widgets import IconButton, Label, elide, paint_shadow


class ProgressStrip(QWidget):
    seek = pyqtSignal(float)

    def __init__(self):
        super().__init__()
        self.setFixedHeight(16)
        self.value = 0.0
        self.setCursor(Qt.PointingHandCursor)

    def set_value(self, v):
        self.value = v
        self.update()

    def mousePressEvent(self, e):
        self.seek.emit(max(0.0, min(1.0, (e.x() - 2) / max(1, self.width() - 4))))

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setPen(Qt.NoPen)
        p.setBrush(c("field"))
        p.drawRoundedRect(QRectF(self.rect()), 6, 6)
        inner = QRectF(self.rect()).adjusted(2, 2, -2, -2)
        if self.value > 0:
            p.setBrush(c("accent_bg"))
            p.drawRoundedRect(QRectF(inner.left(), inner.top(), max(8, inner.width() * self.value), inner.height()), 4, 4)


class MetaChip(QWidget):
    def __init__(self, icon):
        super().__init__()
        self.icon, self.text = icon, ""
        self.setFixedHeight(24)

    def set_text(self, t):
        self.text = t
        self.setVisible(bool(t))
        self.setFixedWidth(min(150, 34 + self.fontMetrics().horizontalAdvance(t) + 8))
        self.update()

    def paintEvent(self, e):
        p = QPainter(self)
        p.drawPixmap(0, 0, icons.pixmap(self.icon, 24))
        f = theme.font(16)
        p.setFont(f)
        p.setPen(c("text"))
        p.drawText(QRectF(32, 0, self.width() - 32, 24), Qt.AlignVCenter, elide(self.text, f, self.width() - 32))


class MiniOverlay(QWidget):
    """Hover controls drawn over the mini player's video."""

    def __init__(self, mini):
        super().__init__(mini.window(), Qt.Tool | Qt.FramelessWindowHint | Qt.NoDropShadowWindowHint
                         | Qt.WindowDoesNotAcceptFocus)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_ShowWithoutActivating)
        self.mini = mini
        self.setMouseTracking(True)
        self._timer = QTimer(self, interval=150, timeout=self._check)

    def show_over(self, rect):
        self.setGeometry(rect)
        self.show()
        self.raise_()
        self._timer.start()

    def _check(self):
        if not self.geometry().adjusted(-2, -2, 2, 2).contains(QCursor.pos()):
            self._timer.stop()
            self.hide()

    def _buttons(self):
        w, h = self.width(), self.height()
        cy = h / 2
        return {"close": QRectF(10, 10, 28, 28), "open": QRectF(w - 38, 10, 28, 28),
                "prev": QRectF(w / 2 - 70, cy - 16, 32, 32), "play": QRectF(w / 2 - 16, cy - 16, 32, 32),
                "next": QRectF(w / 2 + 38, cy - 16, 32, 32)}

    def mouseReleaseEvent(self, e):
        m, eng = self.mini, self.mini.win.engine
        for key, r in self._buttons().items():
            if r.contains(e.pos()):
                if key == "close":
                    m.win.close_mini()
                elif key == "open":
                    m.win.show_player()
                elif key == "prev":
                    eng.prev()
                elif key == "next":
                    eng.next()
                elif key == "play":
                    if eng.state == "ended" or (not eng.is_active() and eng.current):
                        eng.restart()
                    else:
                        eng.toggle()
                self.update()
                return
        m.win.show_player()

    def mousePressEvent(self, e):
        self._press = e.globalPos()

    def mouseMoveEvent(self, e):
        if e.buttons() & Qt.LeftButton and (e.globalPos() - getattr(self, "_press", e.globalPos())).manhattanLength() > 6:
            self.hide()
            self.mini.begin_drag(self._press)

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(0, 0, 0, 130))
        p.drawRoundedRect(QRectF(self.rect()), 6, 6)
        eng = self.mini.win.engine
        b = self._buttons()
        white = "#ffffff"
        p.drawPixmap(b["close"].adjusted(2, 2, -2, -2).toRect(), icons.pixmap("x", 24, white))
        p.drawPixmap(b["open"].adjusted(2, 2, -2, -2).toRect(), icons.pixmap("open", 24, white))
        p.drawPixmap(b["prev"].toRect(), icons.pixmap("previous", 32, white))
        p.drawPixmap(b["next"].toRect(), icons.pixmap("next", 32, white))
        ended = eng.state in ("ended", "stopped") and eng.current
        name = "replay" if ended else ("pause" if eng.is_playing() else "play")
        p.drawPixmap(b["play"].toRect(), icons.pixmap(name, 32, white))


class MiniArt(QWidget):
    """Cover art (or a music note) shown in the mini player while audio plays."""

    def __init__(self, mini):
        super().__init__()
        self.mini = mini
        self.pm = None
        self.setMouseTracking(True)

    def set_cover(self, path):
        from PyQt5.QtGui import QPixmap
        pm = QPixmap(path) if path else QPixmap()
        self.pm = None if pm.isNull() else pm
        self.update()

    def mouseMoveEvent(self, e):
        self.mini._hover(e.globalPos(), force=True)

    def paintEvent(self, e):
        from PyQt5.QtGui import QPainterPath
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setRenderHint(QPainter.SmoothPixmapTransform)
        path = QPainterPath()
        path.addRoundedRect(QRectF(self.rect()), 6, 6)
        p.setClipPath(path)
        p.fillRect(self.rect(), QColor("#000000"))
        if self.pm is not None:
            side = self.height()
            r = QRect((self.width() - side) // 2, 0, side, side)
            p.setOpacity(0.35)
            p.drawPixmap(self.rect(), self.pm.scaled(self.size(), Qt.KeepAspectRatioByExpanding, Qt.SmoothTransformation))
            p.setOpacity(1)
            p.drawPixmap(r, self.pm.scaled(r.size(), Qt.KeepAspectRatioByExpanding, Qt.SmoothTransformation))
        else:
            p.drawPixmap((self.width() - 64) // 2, (self.height() - 64) // 2, icons.pixmap("music", 64, "#5a5a5a"))


class MiniPlayer(QWidget):
    """391×339 card (690 with the queue open) in a corner of the library."""

    def __init__(self, win, parent):
        super().__init__(parent)
        self.win = win
        self.engine = win.engine
        self.store = win.store
        self.media = win.media
        self.setFixedWidth(391 + 40)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(20 + 8, 20 + 8, 20 + 8, 20 + 8)
        lay.setSpacing(0)
        # queue (hidden by default)
        self.queue_box = QWidget()
        qv = QVBoxLayout(self.queue_box)
        qv.setContentsMargins(0, 8, 0, 12)
        qv.setSpacing(8)
        title = Label("Queue", 14, "text")
        title.setAlignment(Qt.AlignCenter)
        qv.addWidget(title)
        head = QHBoxLayout()
        head.setContentsMargins(12, 10, 4, 4)
        self.qcount = Label("", 14, "text_2")
        head.addWidget(self.qcount)
        head.addStretch(1)
        sort = IconButton("sort", "Sort", box=(24, 28))
        sort.clicked.connect(lambda: self.win.panels_queue()._sort_menu())
        rem = IconButton("remove", "Remove", box=(24, 28))
        rem.clicked.connect(self._remove)
        add = IconButton("add", "Add", box=(24, 28))
        add.clicked.connect(lambda: self.win.panels_queue()._add_menu(add))
        for b in (sort, rem, add):
            head.addSpacing(10)
            head.addWidget(b)
        qv.addLayout(head)
        self.qlist = QueueList(self)
        self.qlist.setFixedHeight(260)
        self.qlist.itemDoubleClicked.connect(lambda it: it.data(ROLE_KIND) == "item" and
                                             self.engine.play_index(it.data(ROLE_INDEX)))
        qv.addWidget(self.qlist)
        self.queue_box.hide()
        lay.addWidget(self.queue_box)
        # video
        self.holder = QWidget()
        self.holder.setFixedSize(375, 211)
        self.hl = QVBoxLayout(self.holder)
        self.hl.setContentsMargins(0, 0, 0, 0)
        self.art = MiniArt(self)
        self.art.hide()
        self.hl.addWidget(self.art)
        lay.addWidget(self.holder)
        lay.addSpacing(4)
        self.progress = ProgressStrip()
        self.progress.seek.connect(self.engine.set_position)
        lay.addWidget(self.progress)
        lay.addSpacing(24)
        self.name = Label("", 16, "text")
        self.name.setContentsMargins(8, 0, 8, 0)
        lay.addWidget(self.name)
        lay.addSpacing(14)
        row = QHBoxLayout()
        row.setContentsMargins(8, 0, 8, 4)
        row.setSpacing(20)
        self.folder = MetaChip("folder")
        self.artist = MetaChip("artist")
        row.addWidget(self.folder)
        row.addWidget(self.artist)
        row.addStretch(1)
        self.qbtn = IconButton("lists", "Queue", box=(30, 32), checkable=True)
        self.qbtn.toggled.connect(self._toggle_queue)
        row.addWidget(self.qbtn)
        lay.addLayout(row)
        self.overlay = MiniOverlay(self)
        self._drag = None
        self.engine.tick.connect(self._tick)
        self.engine.mediaChanged.connect(self.refresh)
        self.engine.metaChanged.connect(self.refresh)
        self.engine.videoChanged.connect(lambda _v: self.refresh())
        self.video = None
        self.engine.playlistChanged.connect(self._rebuild_queue)
        self.engine.stateChanged.connect(lambda s: self.overlay.update())
        self.adjustSize()

    # QueueList expects a panel-like object
    def move_rows(self, rows, dst):
        self.win.panels_queue().move_rows(rows, dst)

    def remove_selected(self):
        self._remove()

    def _remove(self):
        rows = [i.data(ROLE_INDEX) for i in self.qlist.selectedItems() if i.data(ROLE_KIND) == "item"]
        if rows:
            self.engine.remove(rows)

    def _toggle_queue(self, on):
        self.queue_box.setVisible(on)
        self._rebuild_queue()
        self.adjustSize()
        self.win.place_mini()

    def _rebuild_queue(self):
        from PyQt5.QtWidgets import QListWidgetItem
        self.qlist.clear()
        for i, item in enumerate(self.engine.items):
            it = QListWidgetItem(display_name(item))
            it.setData(ROLE_KIND, "item")
            it.setData(ROLE_INDEX, i)
            it.setFlags(Qt.ItemIsEnabled | Qt.ItemIsSelectable | Qt.ItemIsDragEnabled)
            self.qlist.addItem(it)
        from .engine import fmt_total
        total = self.engine.queue_length()
        n = len(self.engine.items)
        self.qcount.setText(f"{n} item{'s' if n != 1 else ''}" + (f" - {fmt_total(total)}" if total else ""))

    def attach(self, video):
        video.setParent(self.holder)
        self.hl.addWidget(video)
        video.set_radius(6)
        video.show()
        self.video = video
        try:
            video.moved.disconnect(self._hover)
        except TypeError:
            pass
        video.moved.connect(self._hover)

    def detach(self):
        v = getattr(self, "video", None)
        if v is not None:
            try:
                v.moved.disconnect(self._hover)
            except TypeError:
                pass
        self.video = None
        self.overlay.hide()

    def _hover(self, gpos, force=False):
        if self.isVisible() and (self.video is not None or force):
            r = QRect(self.holder.mapToGlobal(QPoint(0, 0)), self.holder.size())
            if r.contains(gpos) and not self.overlay.isVisible():
                self.overlay.show_over(r)

    def refresh(self):
        e = self.engine
        item = e.current
        self.name.setText(elide(e.file_title() if item else "", theme.font(16), 360))
        if item and not is_url(item):
            self.folder.set_text(os.path.basename(os.path.dirname(item)))
        else:
            self.folder.set_text("")
        self.artist.set_text(e.artist() or (os.path.basename(os.path.dirname(item)) if item and not is_url(item) else ""))
        audio_only = bool(item) and not e.has_video
        if audio_only:
            cover = e.artwork() or (self.win.media.cover_path(item) if not is_url(item) else "")
            self.art.set_cover(cover)
        self.art.setVisible(audio_only)
        if self.video is not None:
            self.video.setVisible(not audio_only)

    def _tick(self, t, length):
        if self.isVisible():
            self.progress.set_value(t / length if length > 0 else 0)

    # dragging to another corner
    def mousePressEvent(self, e):
        if e.button() == Qt.LeftButton:
            self._drag = (e.globalPos(), self.pos())

    def begin_drag(self, gpos):
        self._drag = (gpos, self.pos())
        self.grabMouse()

    def mouseMoveEvent(self, e):
        if self._drag:
            start, pos = self._drag
            self.move(pos + e.globalPos() - start)
            if self.overlay.isVisible():
                self.overlay.hide()

    def mouseReleaseEvent(self, e):
        if self._drag:
            self.releaseMouse()
            start, pos = self._drag
            self._drag = None
            if (e.globalPos() - start).manhattanLength() < 4:
                return
            par = self.parentWidget().rect()
            center = self.geometry().center()
            corner = ("t" if center.y() < par.center().y() else "b") + ("l" if center.x() < par.center().x() else "r")
            self.store["mini_corner"] = corner
            self.win.place_mini()

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        r = QRectF(self.rect()).adjusted(20, 20, -20, -20)
        paint_shadow(p, r, 14, spread=14, alpha=0.38, dy=0)
        p.setPen(Qt.NoPen)
        p.setBrush(c("bar"))
        p.drawRoundedRect(r, 14, 14)


class PipWindow(QWidget):
    """Picture in picture: a small always-on-top window with the video."""

    def __init__(self, win):
        super().__init__(None, Qt.Window | Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint)
        self.win = win
        self.setWindowTitle("Picture in Picture")
        self.lay = QVBoxLayout(self)
        self.lay.setContentsMargins(0, 0, 0, 0)
        self.setStyleSheet("background: #000000;")
        self.video = None
        screen = QApplication.primaryScreen().availableGeometry()
        w = 480
        h = 270
        self.setGeometry(screen.right() - w - 24, screen.bottom() - h - 24, w, h)
        self._resize = None

    def attach(self, video):
        video.setParent(self)
        self.lay.addWidget(video)
        video.set_radius(0)
        video.show()
        self.video = video
        video.doubleClicked.connect(self.win.toggle_pip)
        self.show()
        self.win.set_above(self, True)

    def detach(self):
        if self.video is not None:
            try:
                self.video.doubleClicked.disconnect(self.win.toggle_pip)
            except TypeError:
                pass
        self.video = None
        self.hide()

    def keyPressEvent(self, e):
        if e.key() == Qt.Key_Escape:
            self.win.toggle_pip()
        elif e.key() == Qt.Key_Space:
            self.win.engine.toggle()

    def closeEvent(self, e):
        e.ignore()
        self.win.toggle_pip()
