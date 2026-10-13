"""Title bar and control bar, built from the profile's layout so they can be customised.

A layout names elements (see ELEMENTS) for three title-bar zones and one or more
control-bar rows; "spacer" stretches, so [a, spacer, b, spacer, c] puts b exactly in
the middle. Several bars can exist at once (windowed, fullscreen overlay), each talking
to the shared window through `actions`.
"""

import os

from PyQt5.QtCore import QPoint, QRect, QRectF, QSize, Qt, QTimer, pyqtSignal
from PyQt5.QtGui import QColor, QFontMetrics, QPainter, QPixmap
from PyQt5.QtWidgets import QAbstractButton, QGridLayout, QHBoxLayout, QLabel, QSizePolicy, QVBoxLayout, QWidget

from . import icons, theme
from .engine import fmt_time, is_audio, is_url
from .theme import c
from .widgets import (ConeVolume, Customizable, Divider, FloatingCard, IconButton, PlayButton, SeekBar, TimeLabel,
                      elide)

# id: (label, icon for the customise dialog, group)
ELEMENTS = {
    "menu": ("Menu", "more", "title"),
    "sidebar": ("Library Sidebar", "sidebar-left", "title"),
    "divider": ("Divider", "divider", "divider"),
    "folder": ("Folder", "folder", "title"),
    "title": ("Title", "info", "title"),
    "minimize": ("Minimize", "minimize", "window"),
    "maximize": ("Maximize", "maximize", "window"),
    "close": ("Close", "x", "window"),
    "fullscreen": ("Fullscreen", "fullscreen", "fullscreen"),
    "prev": ("Previous", "previous", "transport"),
    "play": ("Play / Pause", "play", "transport"),
    "next": ("Next", "next", "transport"),
    "current_time": ("Current Time", "recent", "time"),
    "seek": ("Seek Bar", "playback", "time"),
    "duration": ("Duration", "recent", "time"),
    "volume": ("Volume", "vol-high", "volume"),
    "audio": ("Audio Tracks", "audio", "panels"),
    "subtitles": ("Subtitles", "subtitles", "panels"),
    "settings": ("Playback Settings", "playback settings", "panels"),
    "queue": ("Queue", "lists", "queue"),
    "spacer": ("Spacer", None, "spacer"),
    "speed": ("Playback Speed", "speed", "transport2"),
    "fwd10": ("Forward 10s", "fwd10", "transport"),
    "back10": ("Backward 10s", "back10", "transport"),
    "fwd15": ("Forward 15s", "fwd15", "transport"),
    "back15": ("Backward 15s", "back15", "transport"),
    "stop": ("Stop", "stop", "transport"),
    "repeat": ("Repeat", "repeat", "modes"),
    "shuffle": ("Shuffle", "shuffle", "modes"),
    "record": ("Record", "record", "extra"),
    "snapshot": ("Snapshot", "snapshot", "extra"),
    "pip": ("Picture in Picture", "PiP", "extra"),
    "hdr": ("HDR", "HDR", "extra"),
    "zoom": ("Zoom", "zoom", "extra"),
    "right_sidebar": ("Right Sidebar", "sidebar-right", "extra"),
    "quit": ("Quit VLC", "quit", "extra"),
    "vlc_settings": ("VLC Settings", "settings", "extra"),
    "go_home": ("Go Home", "home", "extra"),
    "thumbnail": ("Thumbnail", "thumbnail", "info"),
    "file_name": ("File Name", "info", "info"),
    "artist_name": ("Artist Name", "info", "info"),
    "folder_name": ("Folder Name", "info", "info"),
    "playlist_name": ("Playlist Name", "info", "info"),
    "properties": ("Properties", "info", "extra"),
    "replay": ("Replay", "replay", "transport"),
    "chapters": ("Chapters", "chapters", "extra"),
    "favorite": ("Favorite", "favorite", "extra"),
    "bookmark": ("Bookmark", "bookmark", "extra"),
    "bookmarks": ("Bookmarks", "bookmarks", "extra"),
    "playlists": ("Playlists", "playlist", "extra"),
}

# Order of the extra elements in the Customize Player dialog (as in the design).
CUSTOMIZE_ORDER = ["spacer", "divider", "speed", "fwd10", "back10", "fwd15", "back15", "stop", "repeat", "shuffle",
                   "record", "snapshot", "pip", "hdr", "zoom", "right_sidebar", "quit", "vlc_settings", "go_home",
                   "thumbnail", "file_name", "artist_name", "folder_name", "playlist_name", "properties", "replay",
                   "chapters", "favorite", "bookmark", "bookmarks", "playlists",
                   "menu", "sidebar", "folder", "title", "fullscreen", "prev", "play", "next", "current_time", "seek",
                   "duration", "volume", "audio", "subtitles", "settings", "queue"]
REPEATABLE = {"spacer", "divider"}
LOCKED = {"minimize", "maximize", "close"}


def gap_between(a, b):
    ga, gb = ELEMENTS.get(a, ("", "", a))[2], ELEMENTS.get(b, ("", "", b))[2]
    if "divider" in (ga, gb) or "spacer" in (ga, gb):
        return 4
    if ga == gb == "window":
        return 0
    if ga == gb == "panels":
        return 12
    if ga == gb == "title":
        return 8
    if ga == gb:
        return 8
    return 20


class FolderChip(QWidget, Customizable):
    """Folder icon + the playing item's folder name; opens the library there."""
    clicked = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.text = ""
        self.setFixedHeight(28)
        self.setCursor(Qt.PointingHandCursor)
        self._hover = False
        self.setToolTip("Show this folder in the library")
        self.icon = "folder"

    def set_text(self, text, icon="folder"):
        self.text = text
        self.icon = icon
        fm = QFontMetrics(theme.font(14))
        self.setFixedWidth(min(220, 28 + 4 + fm.horizontalAdvance(text) + 6) if text else 0)
        self.setVisible(bool(text) or self.customizing)
        self.update()

    def enterEvent(self, e):
        self._hover = True
        self.update()

    def leaveEvent(self, e):
        self._hover = False
        self.update()

    def mouseReleaseEvent(self, e):
        if e.button() == Qt.LeftButton and self.rect().contains(e.pos()) and not self.customizing:
            self.clicked.emit()

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        if self._hover:
            p.setPen(Qt.NoPen)
            p.setBrush(QColor(255, 255, 255, 15) if not theme.T.is_light() else QColor(0, 0, 0, 20))
            p.drawRoundedRect(QRectF(self.rect()), 6, 6)
        p.drawPixmap(2, 2, icons.pixmap(self.icon, 24))
        f = theme.font(14)
        p.setFont(f)
        p.setPen(c("icon"))
        p.drawText(QRectF(32, 0, self.width() - 34, self.height()), Qt.AlignVCenter,
                   elide(self.text, f, self.width() - 34))
        self.paint_custom_outline(p)


class TextElement(QWidget, Customizable):
    """Title / file name / artist / folder / playlist name in a bar."""
    clicked = pyqtSignal()

    def __init__(self, parent=None, center=False, max_width=420):
        super().__init__(parent)
        self.text = ""
        self.center = center
        self.max_width = max_width
        self.setFixedHeight(28)
        self.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Fixed)

    def set_text(self, text):
        self.text = text or ""
        fm = QFontMetrics(theme.font(14))
        self.setMinimumWidth(40 if self.customizing else 0)
        self.setFixedWidth(min(self.max_width, max(60 if self.customizing else 0, fm.horizontalAdvance(self.text) + 8)))
        self.update()

    def mouseReleaseEvent(self, e):
        if e.button() == Qt.LeftButton and not self.customizing:
            self.clicked.emit()

    def paintEvent(self, e):
        p = QPainter(self)
        f = theme.font(14)
        p.setFont(f)
        p.setPen(c("icon"))
        p.drawText(self.rect(), Qt.AlignVCenter | (Qt.AlignHCenter if self.center else Qt.AlignLeft),
                   elide(self.text, f, self.width()))
        self.paint_custom_outline(p)


class ThumbElement(QWidget, Customizable):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedSize(50, 28)
        self.pm = None

    def set_pixmap(self, pm):
        self.pm = pm
        self.update()

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        r = QRectF(self.rect())
        p.setPen(Qt.NoPen)
        p.setBrush(c("thumb"))
        p.drawRoundedRect(r, 3, 3)
        if self.pm:
            from PyQt5.QtGui import QPainterPath
            path = QPainterPath()
            path.addRoundedRect(r, 3, 3)
            p.setClipPath(path)
            p.drawPixmap(self.rect(), self.pm)
        self.paint_custom_outline(p)


class SpeedElement(QWidget, Customizable):
    clicked = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedSize(60, 28)
        self.rate = 1.0
        self.setCursor(Qt.PointingHandCursor)
        self.setToolTip("Playback speed")

    def set_rate(self, r):
        self.rate = r
        self.update()

    def mouseReleaseEvent(self, e):
        if not self.customizing:
            self.clicked.emit()

    def paintEvent(self, e):
        p = QPainter(self)
        p.drawPixmap(0, 2, icons.pixmap("speed", 24, c("accent") if abs(self.rate - 1) > 0.01 else None))
        p.setFont(theme.font(13))
        p.setPen(c("icon"))
        p.drawText(QRectF(26, 0, 34, 28), Qt.AlignVCenter, f"{self.rate:g}x")
        self.paint_custom_outline(p)


class SpacerElement(QWidget, Customizable):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedHeight(28)
        self.setMinimumWidth(0)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)

    def paintEvent(self, e):
        if self.customizing:
            p = QPainter(self)
            self.paint_custom_outline(p)


class Bar(QWidget):
    """Common background: #1b1b1b at 95% (or transparent in overlays)."""

    def __init__(self, parent=None, overlay=False, top_line=False):
        super().__init__(parent)
        self.overlay = overlay
        self.top_line = top_line
        self.setAttribute(Qt.WA_StyledBackground, False)

    def paintEvent(self, e):
        p = QPainter(self)
        if not self.overlay:
            p.fillRect(self.rect(), c("bar"))
            if self.top_line:
                p.fillRect(QRect(0, 0, self.width(), 1), c("line"))


class Elements:
    """Creates element widgets wired to the window's actions."""

    def __init__(self, bar, actions):
        self.bar = bar
        self.a = actions

    def make(self, eid, where="bar"):
        a = self.a
        if eid == "menu":
            w = IconButton("more", "Menu", box=(28, 28))
            w.clicked.connect(lambda: a.open_main_menu(w))
        elif eid == "sidebar":
            w = IconButton("sidebar-left", "Library sidebar", box=(28, 28))
            w.clicked.connect(a.toggle_sidebar)
        elif eid == "divider":
            w = Divider()
        elif eid == "folder":
            w = FolderChip()
            w.clicked.connect(a.open_current_folder)
        elif eid in ("title", "file_name", "artist_name", "folder_name", "playlist_name"):
            w = TextElement(center=(where == "title_center"), max_width=900 if where == "title_center" else 360)
            if eid == "title" and where != "title_center":
                w.clicked.connect(a.show_properties)
                w.setCursor(Qt.PointingHandCursor)
        elif eid == "minimize":
            w = IconButton("minimize", "Minimize", box=(36, 24))
            w.clicked.connect(a.minimize)
        elif eid == "maximize":
            w = IconButton("maximize", "Maximize", box=(36, 24))
            w.clicked.connect(a.toggle_maximized)
        elif eid == "close":
            w = IconButton("x", "Close", box=(36, 24))
            w.clicked.connect(a.close_window)
        elif eid == "fullscreen":
            box = (36, 24) if where.startswith("title") else (30, 32)
            w = IconButton("fullscreen", "Fullscreen (F)", box=box)
            w.clicked.connect(a.toggle_fullscreen)
        elif eid == "prev":
            w = IconButton("previous", "Previous (P)", box=(30, 28))
            w.clicked.connect(a.engine.prev)
        elif eid == "next":
            w = IconButton("next", "Next (N)", box=(30, 28))
            w.clicked.connect(lambda: a.engine.next())
        elif eid == "play":
            w = PlayButton()
            w.clicked.connect(a.engine.toggle)
        elif eid == "current_time":
            w = TimeLabel("current")
            w.jump.connect(a.engine.seek_to)
        elif eid == "duration":
            w = TimeLabel("duration")
            w.set_remaining(a.store.view("player", "remaining", False))
            w.toggled.connect(lambda on: a.store.set_view("player", "remaining", on))
        elif eid == "seek":
            w = SeekRow(self.bar, a)
        elif eid == "volume":
            w = VolumeGroup(a)
        elif eid == "audio":
            w = IconButton("audio", "Audio tracks", box=(30, 32))
            w.clicked.connect(lambda: a.toggle_panel("tracks", "audio", w))
        elif eid == "subtitles":
            w = IconButton("subtitles", "Subtitles", box=(30, 32))
            w.clicked.connect(lambda: a.toggle_panel("tracks", "subtitles", w))
        elif eid == "settings":
            w = IconButton("playback settings", "Playback settings", box=(30, 32))
            w.clicked.connect(lambda: a.toggle_panel("settings", None, w))
        elif eid == "queue":
            w = IconButton("lists", "Queue, bookmarks and chapters (Q)", box=(30, 32))
            w.clicked.connect(lambda: a.toggle_panel("queue", None, w))
        elif eid == "spacer":
            w = SpacerElement()
        elif eid == "speed":
            w = SpeedElement()
            w.clicked.connect(lambda: a.toggle_panel("settings", "playback", w))
        elif eid in ("fwd10", "back10", "fwd15", "back15"):
            secs = int(eid[-2:]) * (1 if eid.startswith("fwd") else -1)
            w = IconButton(eid, f"{'Forward' if secs > 0 else 'Back'} {abs(secs)} seconds", box=(30, 28))
            w.clicked.connect(lambda _=False, s=secs: a.engine.seek_rel(s * 1000))
        elif eid == "stop":
            w = IconButton("stop", "Stop (S)", box=(30, 28))
            w.clicked.connect(a.engine.stop)
        elif eid == "repeat":
            w = IconButton("repeat", "Repeat", box=(30, 32))
            w.clicked.connect(a.engine.cycle_repeat)
        elif eid == "shuffle":
            w = IconButton("shuffle", "Shuffle", box=(30, 32))
            w.clicked.connect(lambda: a.engine.toggle_shuffle())
        elif eid == "record":
            w = IconButton("record", "Record (Shift+R)", box=(30, 32))
            w.clicked.connect(a.engine.toggle_record)
        elif eid == "snapshot":
            w = IconButton("snapshot", "Snapshot (Shift+S)", box=(30, 32))
            w.clicked.connect(a.take_snapshot)
        elif eid == "pip":
            w = IconButton("PiP", "Picture in picture", box=(30, 32))
            w.clicked.connect(a.toggle_pip)
        elif eid == "hdr":
            w = IconButton("HDR", "Video settings", box=(30, 32))
            w.clicked.connect(lambda: a.toggle_panel("settings", "video", w))
        elif eid == "zoom":
            w = IconButton("zoom", "Zoom (Z)", box=(30, 32))
            w.clicked.connect(lambda: a.cycle_zoom())
        elif eid == "right_sidebar":
            w = IconButton("sidebar-right", "Right sidebar", box=(30, 32))
            w.clicked.connect(a.toggle_right_panel)
        elif eid == "quit":
            w = IconButton("quit", "Quit (Ctrl+Q)", box=(30, 32))
            w.clicked.connect(a.quit)
        elif eid == "vlc_settings":
            w = IconButton("settings", "Settings (Ctrl+P)", box=(30, 32))
            w.clicked.connect(a.open_settings)
        elif eid == "go_home":
            w = IconButton("home", "Library home", box=(30, 32))
            w.clicked.connect(a.go_home)
        elif eid == "thumbnail":
            w = ThumbElement()
        elif eid == "properties":
            w = IconButton("info", "Properties & Metadata (Ctrl+I)", box=(30, 32))
            w.clicked.connect(a.show_properties)
        elif eid == "replay":
            w = IconButton("replay", "Play from the start", box=(30, 32))
            w.clicked.connect(a.engine.restart)
        elif eid == "chapters":
            w = IconButton("chapters", "Chapters", box=(30, 32))
            w.clicked.connect(lambda: a.toggle_panel("queue", "chapters", w))
        elif eid == "favorite":
            w = IconButton("favorite", "Favorite", box=(30, 32))
            w.clicked.connect(a.toggle_favorite_current)
        elif eid == "bookmark":
            w = IconButton("bookmark", "Add a bookmark (Ctrl+B)", box=(30, 32))
            w.clicked.connect(a.add_bookmark)
        elif eid == "bookmarks":
            w = IconButton("bookmarks", "Bookmarks", box=(30, 32))
            w.clicked.connect(lambda: a.toggle_panel("queue", "bookmarks", w))
        elif eid == "playlists":
            w = IconButton("playlist", "Playlists", box=(30, 32))
            w.clicked.connect(lambda: a.show_library_page("playlists"))
        else:
            w = SpacerElement()
        w.element_id = eid
        w.zone = where
        return w


class SeekRow(QWidget, Customizable):
    """The seek bar plus, in subtitle-timeline mode, ‹ − and + › buttons at its ends."""

    def __init__(self, bar, actions):
        super().__init__()
        self.a = actions
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.setFixedHeight(28)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(4)
        self.nudges = []
        for sym, tip, fn in (("‹", "Previous subtitle", lambda: actions.jump_cue(-1)),
                             ("−", "Subtitles earlier (100 ms)", lambda: actions.nudge_sub(-0.1))):
            b = NudgeButton(sym, tip)
            b.clicked.connect(fn)
            lay.addWidget(b)
            self.nudges.append(b)
        self.seek = SeekBar()
        lay.addWidget(self.seek, 1)
        for sym, tip, fn in (("+", "Subtitles later (100 ms)", lambda: actions.nudge_sub(0.1)),
                             ("›", "Next subtitle", lambda: actions.jump_cue(1))):
            b = NudgeButton(sym, tip)
            b.clicked.connect(fn)
            lay.addWidget(b)
            self.nudges.append(b)
        self.set_timeline(False)

    def set_timeline(self, on):
        for b in self.nudges:
            b.setVisible(on)

    def paintEvent(self, e):
        p = QPainter(self)
        self.paint_custom_outline(p)


class NudgeButton(QWidget):
    clicked = pyqtSignal()

    def __init__(self, sym, tip):
        super().__init__()
        self.sym = sym
        self.setFixedSize(18, 24)
        self.setToolTip(tip)
        self.setCursor(Qt.PointingHandCursor)

    def mouseReleaseEvent(self, e):
        if self.rect().contains(e.pos()):
            self.clicked.emit()

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setPen(Qt.NoPen)
        p.setBrush(c("accent") if self.underMouse() else c("accent_2"))
        p.drawRoundedRect(QRectF(self.rect()), 3, 3)
        p.setFont(theme.font(15, theme.SEMIBOLD))
        p.setPen(QColor("#1b1b1b"))
        p.drawText(self.rect(), Qt.AlignCenter, self.sym)


class VolumeGroup(QWidget, Customizable):
    """Speaker icon (mute) + cone volume, 120×28."""

    def __init__(self, actions):
        super().__init__()
        self.a = actions
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(8)
        self.icon = IconButton("vol-high", "Mute (M)", box=(24, 28))
        self.icon.clicked.connect(actions.engine.toggle_mute)
        lay.addWidget(self.icon)
        self.cone = ConeVolume()
        self.cone.changed.connect(lambda v: actions.engine.set_volume(v, notify=False))
        lay.addWidget(self.cone)
        self.setFixedHeight(28)

    def set_volume(self, v, muted, maximum):
        self.cone.maximum = maximum
        self.cone.set_value(v, muted)
        name = "vol-mute" if muted or v == 0 else ("vol-low" if v < 34 else "vol-medium" if v < 67 else "vol-high")
        self.icon.set_icon(name)

    def paintEvent(self, e):
        p = QPainter(self)
        self.paint_custom_outline(p)


class ConeButton(QAbstractButton):
    """The VLC cone at the top left: takes you to the library's Home. Being a button, a click
    on it isn't taken as a title-bar drag or double click."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedSize(38, 28)
        self.setCursor(Qt.PointingHandCursor)
        self.setToolTip("Home")
        self.setFocusPolicy(Qt.NoFocus)

    def paintEvent(self, e):
        p = QPainter(self)
        p.drawPixmap((self.width() - 24) // 2, (self.height() - 24) // 2, icons.pixmap("vlc", 24))


class TitleBar(Bar):
    """40 px: VLC cone, left zone, centred title zone, right zone (window buttons)."""

    def __init__(self, actions, parent=None, overlay=False):
        super().__init__(parent, overlay)
        self.a = actions
        self.setFixedHeight(40)
        self.widgets = []
        self._press = None
        self.cone = ConeButton(self)
        self.cone.clicked.connect(actions.go_home)
        self.left = QHBoxLayout()
        self.center = QHBoxLayout()
        self.right = QHBoxLayout()
        grid = QGridLayout(self)
        grid.setContentsMargins(6, 6, 6, 6)
        grid.setHorizontalSpacing(10)
        lw = QWidget()
        lh = QHBoxLayout(lw)
        lh.setContentsMargins(0, 0, 0, 0)
        lh.setSpacing(7)
        lh.addWidget(self.cone)
        lh.addLayout(self.left)
        lh.addStretch(1)
        rw = QWidget()
        rh = QHBoxLayout(rw)
        rh.setContentsMargins(0, 0, 0, 0)
        rh.addStretch(1)
        rh.addLayout(self.right)
        cw = QWidget()
        ch = QHBoxLayout(cw)
        ch.setContentsMargins(0, 0, 0, 0)
        ch.addStretch(1)
        ch.addLayout(self.center)
        ch.addStretch(1)
        grid.addWidget(lw, 0, 0)
        grid.addWidget(cw, 0, 1)
        grid.addWidget(rw, 0, 2)
        grid.setColumnStretch(0, 1)
        grid.setColumnStretch(2, 1)
        self.zones = {"title_left": self.left, "title_center": self.center, "title_right": self.right}
        self.zone_widgets = {"title_left": lw, "title_center": cw, "title_right": rw}
        self.factory = Elements(self, actions)

    def build(self, layout):
        for w in self.widgets:
            w.hide()
            w.deleteLater()
        self.widgets = []
        for zone, lay in self.zones.items():
            while lay.count():
                lay.takeAt(0)
            ids = layout.get(zone, [])
            prev = None
            for eid in ids:
                if prev is not None:
                    lay.addSpacing(gap_between(prev, eid) if zone != "title_right" else 0)
                w = self.factory.make(eid, zone)
                lay.addWidget(w, 1 if eid == "spacer" else 0, Qt.AlignVCenter)
                self.widgets.append(w)
                prev = eid

    def elements(self, eid):
        return [w for w in self.widgets if getattr(w, "element_id", None) == eid]

    # window dragging
    def mousePressEvent(self, e):
        if e.button() == Qt.LeftButton:
            self._press = e.globalPos()

    def mouseMoveEvent(self, e):
        if self._press is not None and (e.globalPos() - self._press).manhattanLength() > 4:
            self._press = None
            self.a.start_move()

    def mouseReleaseEvent(self, e):
        self._press = None

    def mouseDoubleClickEvent(self, e):
        if e.button() == Qt.LeftButton:
            self.a.toggle_maximized()


class ControlBar(Bar):
    """54 px per row: pad 11/20, the design's main control bar."""

    def __init__(self, actions, parent=None, overlay=False):
        super().__init__(parent, overlay, top_line=not overlay)
        self.a = actions
        self.widgets = []
        self.rows_lay = QVBoxLayout(self)
        self.rows_lay.setContentsMargins(20, 0, 20, 0)
        self.rows_lay.setSpacing(0)
        self.factory = Elements(self, actions)
        self.preview = None

    def build(self, rows):
        for w in self.widgets:
            w.hide()
            w.deleteLater()
        self.widgets = []
        while self.rows_lay.count():
            item = self.rows_lay.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        for row in rows:
            rw = QWidget()
            rw.setFixedHeight(54)
            self._build_row(rw, row)
            self.rows_lay.addWidget(rw)
        self.setFixedHeight(54 * max(1, len(rows)))

    def _build_row(self, host, ids):
        segments = [[]]
        for eid in ids:
            if eid == "spacer":
                segments.append([])
            else:
                segments[-1].append(eid)
        has_seek = "seek" in ids
        grid = QGridLayout(host)
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setHorizontalSpacing(0)

        def seg_widget(eids, align):
            w = QWidget()
            h = QHBoxLayout(w)
            h.setContentsMargins(0, 0, 0, 0)
            h.setSpacing(0)
            if align == "right":
                h.addStretch(1)
            prev = None
            for eid in eids:
                if prev is not None:
                    h.addSpacing(gap_between(prev, eid))
                el = self.factory.make(eid, "bar")
                stretch = 1 if eid in ("seek",) else 0
                h.addWidget(el, stretch, Qt.AlignVCenter)
                self.widgets.append(el)
                prev = eid
            if align == "left" and "seek" not in eids:
                h.addStretch(1)
            return w

        n = len(segments)
        if n == 1:
            grid.addWidget(seg_widget(segments[0], "left"), 0, 0)
        elif n == 2:
            grid.addWidget(seg_widget(segments[0], "left"), 0, 0)
            grid.addWidget(seg_widget(segments[1], "right"), 0, 1)
            grid.setColumnStretch(0, 1)
            grid.setColumnStretch(1, 1)
        else:
            left = segments[0]
            center = [e for seg in segments[1:-1] for e in seg]
            right = segments[-1]
            grid.addWidget(seg_widget(left, "left"), 0, 0)
            grid.addWidget(seg_widget(center, "center"), 0, 1)
            grid.addWidget(seg_widget(right, "right"), 0, 2)
            grid.setColumnStretch(0, 1)
            grid.setColumnStretch(1, 0 if "seek" not in center else 3)
            grid.setColumnStretch(2, 1)
        for w in self.widgets:
            if getattr(w, "element_id", None) == "seek":
                sb = w.seek
                sb.hovered.connect(self._seek_hover)
                sb.left.connect(self._seek_leave)
                sb.scrubbed.connect(self.a.scrub)
                sb.seekRequested.connect(self.a.seek_fraction)

    def elements(self, eid):
        return [w for w in self.widgets if getattr(w, "element_id", None) == eid]

    # seek-bar hover preview
    def _seek_hover(self, frac, gpos):
        self.a.seek_hover(frac, gpos, self)

    def _seek_leave(self):
        self.a.seek_hover_end()

    def mousePressEvent(self, e):
        # dragging an empty part of the bar moves the window (like the title bar)
        if e.button() == Qt.LeftButton:
            self._press = e.globalPos()

    def mouseMoveEvent(self, e):
        p = getattr(self, "_press", None)
        if p is not None and (e.globalPos() - p).manhattanLength() > 6:
            self._press = None
            self.a.start_move()

    def mouseReleaseEvent(self, e):
        self._press = None


class SeekPreview(FloatingCard):
    """Hover card above the seek bar: a frame from that moment and its time."""

    def __init__(self):
        super().__init__(None, radius=8, fill="bar", alpha=0.98)
        self.setAttribute(Qt.WA_ShowWithoutActivating)
        self.setWindowFlags(Qt.ToolTip | Qt.FramelessWindowHint | Qt.NoDropShadowWindowHint
                            | Qt.WindowTransparentForInput | Qt.WindowDoesNotAcceptFocus)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_TransparentForMouseEvents)
        self.pm = None
        self.text = ""
        self.MARGIN = 14
        self._lay.setContentsMargins(14, 14, 14, 14)

    def show_at(self, gx, gy_top, text, pm):
        self.text = text
        self.pm = pm
        if pm is not None:
            iw = 192
            ih = int(iw * pm.height() / max(1, pm.width())) if pm.width() else 108
            ih = min(ih, 140)
            cw, ch = iw + 12, ih + 12 + 26
        else:
            fm = QFontMetrics(theme.font(14))
            cw, ch = fm.horizontalAdvance(text) + 24, 34
        m = self.MARGIN
        self.resize(cw + 2 * m, ch + 2 * m)
        self.move(int(gx - self.width() / 2), int(gy_top - self.height() + m - 8))
        if not self.isVisible():
            self.show()
        self.update()

    def paintEvent(self, e):
        super().paintEvent(e)
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        r = self.card_rect()
        p.setFont(theme.font(14))
        p.setPen(c("text"))
        if self.pm is not None:
            img = QRectF(r.left() + 6, r.top() + 6, r.width() - 12, r.height() - 12 - 26)
            from PyQt5.QtGui import QPainterPath
            path = QPainterPath()
            path.addRoundedRect(img, 6, 6)
            p.save()
            p.setClipPath(path)
            p.drawPixmap(img.toRect(), self.pm)
            p.restore()
            p.drawText(QRectF(r.left(), img.bottom(), r.width(), 26), Qt.AlignCenter, self.text)
        else:
            p.drawText(r, Qt.AlignCenter, self.text)
