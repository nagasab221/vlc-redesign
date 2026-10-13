"""Dialogs in the redesign's style: #191919 card (r=14), #090909 header with the title and
a close button, an optional tool row, and Cancel / Save in the footer."""

import difflib
import glob
import json
import os
import re
import shutil
import subprocess
import time
import webbrowser
from pathlib import Path

from PyQt5.QtCore import QPoint, QProcess, QRectF, QSize, Qt, QTimer, QUrl, pyqtSignal
from PyQt5.QtGui import QColor, QFontMetrics, QKeySequence, QPainter, QPen, QPixmap
from PyQt5.QtNetwork import QNetworkAccessManager, QNetworkRequest
from PyQt5.QtWidgets import (QApplication, QDialog, QFileDialog, QGridLayout, QHBoxLayout, QLineEdit, QPlainTextEdit,
                             QScrollArea, QVBoxLayout, QWidget)

from . import APP_NAME, __version__, icons, theme
from .engine import (AUDIO_EXT, MEDIA_EXT, SUB_EXT, VIDEO_EXT, display_name, ext, fmt_time, is_url, media_in_dir,
                     natural_key, parse_playlist_text)
from .theme import c
from .widgets import (Button, Dropdown, HLine, IconButton, Label, MenuItem, PopupMenu, Toggle, elide, paint_shadow,
                      scroll)



def _request(url):
    req = QNetworkRequest(QUrl(url))
    req.setAttribute(QNetworkRequest.RedirectPolicyAttribute, QNetworkRequest.NoLessSafeRedirectPolicy)
    req.setRawHeader(b"User-Agent", b"VLCRedesign/0.2 (libVLC 3)")
    return req


class Dialog(QDialog):
    MARGIN = 24

    def __init__(self, parent, title, width=1048, height=None, tools=None, fill="rail", save_text="Save",
                 cancel_text="Cancel", footer=True):
        super().__init__(parent, Qt.Dialog | Qt.FramelessWindowHint | Qt.NoDropShadowWindowHint)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setModal(True)
        self.fill = fill
        self._drag = None
        outer = QVBoxLayout(self)
        m = self.MARGIN
        outer.setContentsMargins(m, m, m, m)
        outer.setSpacing(0)
        self.head = QWidget()
        hv = QVBoxLayout(self.head)
        hv.setContentsMargins(9, 9, 9, 9)
        hv.setSpacing(12)
        top = QHBoxLayout()
        top.setContentsMargins(10, 0, 0, 0)
        self.title_label = Label(title, 14, "icon")
        top.addWidget(self.title_label)
        top.addStretch(1)
        close = IconButton("x", "Close (Esc)", box=(32, 32))
        close.clicked.connect(self.reject)
        top.addWidget(close)
        hv.addLayout(top)
        if tools is not None:
            hv.addWidget(tools)
        outer.addWidget(self.head)
        self.body = QWidget()
        self.body_lay = QVBoxLayout(self.body)
        self.body_lay.setContentsMargins(20, 22, 20, 22)
        self.body_lay.setSpacing(20)
        outer.addWidget(self.body, 1)
        self.footer = QWidget()
        fl = QHBoxLayout(self.footer)
        fl.setContentsMargins(20, 20, 20, 20)
        self.cancel = Button(cancel_text, "face", height=32)
        self.cancel.setFixedWidth(100)
        self.cancel.clicked.connect(self.reject)
        self.save = Button(save_text, "primary", height=32)
        self.save.setFixedWidth(max(100, self.save.sizeHint().width()))
        self.save.clicked.connect(self.accept_clicked)
        fl.addWidget(self.cancel)
        fl.addStretch(1)
        fl.addWidget(self.save)
        outer.addWidget(self.footer)
        self.footer.setVisible(footer)
        self.resize(width + 2 * m, (height or 600) + 2 * m)

    def accept_clicked(self):
        self.accept()

    def head_height(self):
        return self.head.height()

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        m = self.MARGIN
        r = QRectF(self.rect()).adjusted(m, m, -m, -m)
        paint_shadow(p, r, 14, spread=16, alpha=0.46, dy=8)
        p.setPen(Qt.NoPen)
        p.setBrush(c(self.fill, 0.985))
        p.drawRoundedRect(r, 14, 14)
        # header
        hr = QRectF(r.left(), r.top(), r.width(), self.head.height())
        from PyQt5.QtGui import QPainterPath
        path = QPainterPath()
        path.addRoundedRect(QRectF(r.left(), r.top(), r.width(), hr.height() + 20), 14, 14)
        clip = QPainterPath()
        clip.addRect(hr)
        p.setBrush(c("panel_head"))
        p.drawPath(path.intersected(clip))

    def showEvent(self, e):
        par = self.parentWidget()
        if par is not None:
            g = par.window().frameGeometry()
            self.move(g.center() - self.rect().center())
        super().showEvent(e)

    def mousePressEvent(self, e):
        if e.button() == Qt.LeftButton and e.pos().y() < self.MARGIN + 50:
            self._drag = e.globalPos() - self.frameGeometry().topLeft()

    def mouseMoveEvent(self, e):
        if self._drag is not None:
            self.move(e.globalPos() - self._drag)

    def mouseReleaseEvent(self, e):
        self._drag = None


def section(title, desc=None):
    w = QWidget()
    v = QVBoxLayout(w)
    v.setContentsMargins(0, 0, 0, 0)
    v.setSpacing(5)
    v.addWidget(Label(title, 16, "text_hi"))
    if desc:
        v.addWidget(Label(desc, 14, "text_2", wrap=True))
    return w


class Field(QLineEdit):
    """#090909 text field with a 2 px #191919 inner border (dialog inputs)."""

    def __init__(self, placeholder="", text="", height=37):
        super().__init__(text)
        self.setPlaceholderText(placeholder)
        self.setFixedHeight(height)
        self.setStyleSheet(f"QLineEdit {{ background: {theme.hexc('field')}; border: 2px solid {theme.hexc('item')}; "
                           f"border-radius: 6px; padding: 0 12px; }}")


class ToolBox(QWidget):
    """Search / path box used in dialog tool rows (#151515 r=6, 34 px)."""

    def __init__(self, icon, text="", placeholder="", editable=True):
        super().__init__()
        self.setFixedHeight(34)
        h = QHBoxLayout(self)
        h.setContentsMargins(8, 0, 8, 0)
        h.setSpacing(10)
        ic = Label("")
        ic.setPixmap(icons.pixmap(icon, 24))
        h.addWidget(ic)
        self.edit = QLineEdit(text)
        self.edit.setObjectName("Bare")
        self.edit.setPlaceholderText(placeholder)
        self.edit.setReadOnly(not editable)
        h.addWidget(self.edit, 1)

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setPen(Qt.NoPen)
        p.setBrush(c("field_2"))
        p.drawRoundedRect(QRectF(self.rect()), 6, 6)


class ToolDropdown(Dropdown):
    """Dropdown in dialog tool rows: flat #151515, 34 px."""

    def __init__(self, options, value=None, width=200):
        super().__init__(options, value, height=34)
        self.setFixedWidth(width)

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setPen(Qt.NoPen)
        p.setBrush(c("field_2") if not self._hover else c("hover"))
        p.drawRoundedRect(QRectF(self.rect()), 6, 6)
        p.setFont(theme.font(14))
        p.setPen(c("text"))
        p.drawText(QRectF(12, 0, self.width() - 50, self.height()), Qt.AlignVCenter, self.label())
        p.drawPixmap(self.width() - 8 - 24, (self.height() - 24) // 2,
                     icons.pixmap("chevron-up" if self._open else "chevron-down", 24))


# --- Add Subtitles -----------------------------------------------------------------------
EP_RE = re.compile(r"(?:(?<![a-z])s(\d{1,2})[ ._-]*e(\d{1,3})(?!\d)|(?<!\d)(\d{1,2})x(\d{2,3})(?!\d)"
                   r"|(?<![a-z])e(?:p(?:isode)?)?[ ._-]?(\d{1,3})(?!\d))", re.I)


NOISE_NUMBERS = {"480", "576", "720", "1080", "2160", "264", "265", "10", "8"}


def episode_key(name):
    m = EP_RE.search(name)
    if not m:
        stem = os.path.splitext(name)[0]
        stem = re.sub(r"\[[^\]]*\]|\([^)]*\)", " ", stem)          # [1080p] [x265] (2024)
        nums = [n for n in re.findall(r"(?<![\dA-Za-z])(\d{1,3})(?![\dA-Za-z])", stem) if n not in NOISE_NUMBERS]
        return (0, int(nums[-1])) if nums else None
    if m.group(1):
        return (int(m.group(1)), int(m.group(2)))
    if m.group(3):
        return (int(m.group(3)), int(m.group(4)))
    return (0, int(m.group(5)))


def match_subtitles(videos, subs):
    """{video: subtitle} by episode number, then by name similarity."""
    out = {}
    free = list(subs)
    by_ep = {}
    for s in subs:
        k = episode_key(os.path.basename(s))
        if k:
            by_ep.setdefault(k, []).append(s)
    for v in videos:
        k = episode_key(os.path.basename(v))
        cands = by_ep.get(k) or (by_ep.get((0, k[1])) if k else None)
        if not cands and k:
            cands = [s for kk, ss in by_ep.items() if kk[1] == k[1] for s in ss]
        if cands:
            out[v] = cands[0]
            continue
        stem = os.path.splitext(os.path.basename(v))[0].lower()
        # never pair a video with a subtitle that names a different episode
        pool = [s for s in free if not (k and episode_key(os.path.basename(s)) and
                                        episode_key(os.path.basename(s)) != k)]
        best = max(pool, key=lambda s: difflib.SequenceMatcher(None, stem, os.path.basename(s).lower()).ratio(),
                   default=None)
        if best and difflib.SequenceMatcher(None, stem, os.path.basename(best).lower()).ratio() > 0.6:
            out[v] = best
    return out


class PairRow(QWidget):
    """video ─ ─ ─ ─ subtitle (click the subtitle to choose another one)."""
    pick = pyqtSignal(str)

    def __init__(self, video, sub):
        super().__init__()
        self.video, self.sub = video, sub
        self.setFixedHeight(36)
        self.setMouseTracking(True)
        self._hover_right = False

    def set_sub(self, sub):
        self.sub = sub
        self.update()

    def mouseMoveEvent(self, e):
        h = e.x() > self.width() / 2
        if h != self._hover_right:
            self._hover_right = h
            self.setCursor(Qt.PointingHandCursor if h else Qt.ArrowCursor)
            self.update()

    def leaveEvent(self, e):
        self._hover_right = False
        self.update()

    def mouseReleaseEvent(self, e):
        if e.x() > self.width() / 2:
            self.pick.emit(self.video)

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        half = self.width() / 2
        f = theme.font(14)
        fm = QFontMetrics(f)
        p.setFont(f)
        p.drawPixmap(10, 6, icons.pixmap("video", 24))
        name = os.path.basename(self.video)
        nw = min(fm.horizontalAdvance(name), int(half - 80))
        p.setPen(c("text"))
        p.drawText(QRectF(48, 0, nw + 2, 36), Qt.AlignVCenter, elide(name, f, half - 80))
        pen = QPen(c("line").lighter(150), 1, Qt.DashLine)
        pen.setDashPattern([6, 4])
        p.setPen(pen)
        p.drawLine(int(48 + nw + 14), 18, int(half - 10), 18)
        if self._hover_right:
            p.setPen(Qt.NoPen)
            p.setBrush(c("field"))
            p.drawRoundedRect(QRectF(half + 4, 0, half - 14, 36), 6, 6)
            p.drawPixmap(int(self.width() - 40), 6, icons.pixmap("swap", 24))
        p.drawPixmap(int(half + 14), 6, icons.pixmap("subtitle", 24))
        p.setPen(c("text") if self.sub else c("text_3"))
        p.drawText(QRectF(half + 48, 0, half - 100, 36), Qt.AlignVCenter,
                   elide(os.path.basename(self.sub) if self.sub else "No subtitles, click to choose", f, half - 100))


class CheckRow(QWidget):
    toggled = pyqtSignal(bool)

    def __init__(self, text, checked=False, icon="file"):
        super().__init__()
        self.text, self.checked, self.icon = text, checked, icon
        self.setFixedHeight(36)
        self.setCursor(Qt.PointingHandCursor)

    def mouseReleaseEvent(self, e):
        self.checked = not self.checked
        self.update()
        self.toggled.emit(self.checked)

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        if self.checked:
            p.setPen(Qt.NoPen)
            p.setBrush(c("field"))
            p.drawRoundedRect(QRectF(self.rect()), 6, 6)
        p.drawPixmap(10, 6, icons.pixmap(self.icon, 24))
        f = theme.font(14)
        p.setFont(f)
        p.setPen(c("text_hi") if self.checked else c("text"))
        p.drawText(QRectF(46, 0, self.width() - 90, 36), Qt.AlignVCenter, elide(self.text, f, self.width() - 90))
        if self.checked:
            p.drawPixmap(self.width() - 34, 6, icons.pixmap("check", 24, None, "active"))


class AddSubtitlesDialog(Dialog):
    def __init__(self, win, target_path=None, scope="file", items=None):
        self.win = win
        tools = QWidget()
        th = QHBoxLayout(tools)
        th.setContentsMargins(0, 0, 0, 0)
        th.setSpacing(10)
        self.scope = ToolDropdown([("file", "To This File"), ("folder", "To This Folder"), ("playlist", "To This Playlist")],
                                  scope)
        self.origin = ToolDropdown([("computer", "From Computer"), ("opensubtitles", "From OpenSubtitles"),
                                    ("link", "From Link …")], "computer")
        self.where = ToolBox("folder", editable=False)
        self.where.setCursor(Qt.PointingHandCursor)
        self.where.mousePressEvent = lambda e: self._choose_folder()
        self.query = ToolBox("search", placeholder="Search OpenSubtitles")
        self.lang = ToolDropdown(["English", "Arabic", "Chinese", "French", "German", "Hindi", "Italian", "Japanese",
                                  "Korean", "Portuguese", "Russian", "Spanish", "Turkish", "Urdu"], "English")
        self.link = ToolBox("link", placeholder="https://… link to a .srt / .vtt / .ass file")
        for w in (self.scope, self.origin):
            th.addWidget(w)
        th.addWidget(self.where, 1)
        th.addWidget(self.query, 1)
        th.addWidget(self.lang)
        th.addWidget(self.link, 1)
        super().__init__(win, "Add Subtitles", 1048, 704, tools=tools, fill="base")
        self.target_path = target_path or win.engine.current
        self.items = items
        if scope == "file" and self.target_path and os.path.isdir(self.target_path):
            self.scope.set_value("folder")
        self.folder = None
        if self.target_path and not is_url(self.target_path):
            self.folder = self.target_path if os.path.isdir(self.target_path) else os.path.dirname(self.target_path)
        self.list_host = QWidget()
        self.list_lay = QVBoxLayout(self.list_host)
        self.list_lay.setContentsMargins(10, 10, 10, 10)
        self.list_lay.setSpacing(4)
        self.body_lay.setContentsMargins(20, 10, 20, 0)
        self.body_lay.addWidget(scroll(self.list_host), 1)
        self.scope.changed.connect(lambda _v: self.rebuild())
        self.origin.changed.connect(lambda _v: self.rebuild())
        self.query.edit.returnPressed.connect(self._search_online)
        self.link.edit.returnPressed.connect(self._download_link)
        self.pairs = {}
        self.checked = set()
        self.net = QNetworkAccessManager(self)
        self.rebuild()

    def _videos(self):
        scope = self.scope.value
        if scope == "file":
            return [self.target_path] if self.target_path and not is_url(self.target_path) else []
        if scope == "playlist":
            if self.items:
                return [i for i in self.items if not is_url(i) and ext(i) in VIDEO_EXT]
            return [i for i in self.win.engine.items if not is_url(i) and ext(i) in VIDEO_EXT]
        return media_in_dir(self.folder, VIDEO_EXT) if self.folder else []

    def _subs(self):
        if not self.folder:
            return []
        subs = media_in_dir(self.folder, SUB_EXT)
        for d in ("Subs", "subs", "Subtitles", "subtitles"):
            subs += media_in_dir(os.path.join(self.folder, d), SUB_EXT)
        return subs

    def _choose_folder(self):
        d = QFileDialog.getExistingDirectory(self, "Folder with the subtitle files", self.folder or os.path.expanduser("~"),
                                             options=self.win.dialog_options())
        if d:
            self.folder = d
            self.rebuild()

    def rebuild(self):
        origin = self.origin.value
        self.where.setVisible(origin == "computer")
        self.query.setVisible(origin == "opensubtitles")
        self.lang.setVisible(origin == "opensubtitles")
        self.link.setVisible(origin == "link")
        self.where.edit.setText(self._pretty(self.folder) if self.folder else "Choose a folder…")
        while self.list_lay.count():
            w = self.list_lay.takeAt(0).widget()
            if w:
                w.deleteLater()
        if origin == "opensubtitles":
            name = os.path.splitext(os.path.basename(self.target_path or ""))[0].replace("_", " ")
            if not self.query.edit.text():
                self.query.edit.setText(name)
            self.list_lay.addWidget(Label("Press Enter to search OpenSubtitles.com in your browser. Save the files you "
                                          "download into the video's folder, then switch back to “From Computer”, "
                                          "they will be matched to the episodes automatically.", 14, "text_2", wrap=True))
            self.list_lay.addStretch(1)
            return
        if origin == "link":
            self.list_lay.addWidget(Label("Paste a direct link to a subtitle file and press Enter. It is saved next to "
                                          "the video and added.", 14, "text_2", wrap=True))
            self.list_lay.addStretch(1)
            return
        subs = self._subs()
        if self.scope.value == "file":
            existing = set(self.win.store.external_subs(self.target_path)) if self.target_path else set()
            stem = os.path.splitext(os.path.basename(self.target_path or ""))[0].lower()
            subs.sort(key=lambda s: (not os.path.basename(s).lower().startswith(stem[:8]), natural_key(s)))
            self.checked = set(existing)
            if not subs:
                self.list_lay.addWidget(Label("No subtitle files in this folder.", 14, "text_3"))
            for s in subs:
                row = CheckRow(os.path.basename(s), s in self.checked)
                row.toggled.connect(lambda on, s=s: self.checked.add(s) if on else self.checked.discard(s))
                self.list_lay.addWidget(row)
        else:
            videos = self._videos()
            current = {v: (self.win.store.external_subs(v) or [None])[0] for v in videos}
            auto = match_subtitles(videos, subs)
            self.pairs = {v: current.get(v) or auto.get(v) for v in videos}
            self._subs_cache = subs
            if not videos:
                self.list_lay.addWidget(Label("No videos to match.", 14, "text_3"))
            for v in videos:
                row = PairRow(v, self.pairs[v])
                row.pick.connect(self._pick_for)
                self.list_lay.addWidget(row)
        self.list_lay.addStretch(1)

    def _pretty(self, path):
        home = os.path.expanduser("~")
        p = path.replace(home, "~", 1) if path.startswith(home) else path
        return " / ".join(x for x in p.split(os.sep) if x)

    def _pick_for(self, video):
        items = [MenuItem(os.path.basename(s), lambda s=s, v=video: self._set_pair(v, s),
                          checked=(self.pairs.get(video) == s)) for s in self._subs_cache]
        items.append(MenuItem("No subtitles", lambda v=video: self._set_pair(v, None)))
        items.append(MenuItem("Other file…", lambda v=video: self._pick_file(v)))
        PopupMenu(items, self, width=420, row=40).popup(QCursorPos())

    def _pick_file(self, video):
        path, _ = QFileDialog.getOpenFileName(self, "Subtitle file", self.folder or "", "Subtitles (*.srt *.ass *.ssa *.vtt *.sub)",
                                              options=self.win.dialog_options())
        if path:
            self._set_pair(video, path)

    def _set_pair(self, video, sub):
        self.pairs[video] = sub
        for i in range(self.list_lay.count()):
            w = self.list_lay.itemAt(i).widget()
            if isinstance(w, PairRow) and w.video == video:
                w.set_sub(sub)

    def _search_online(self):
        q = self.query.edit.text().strip()
        if q:
            lang = {"English": "en", "Arabic": "ar", "Chinese": "zh-CN", "French": "fr", "German": "de", "Hindi": "hi",
                    "Italian": "it", "Japanese": "ja", "Korean": "ko", "Portuguese": "pt-PT", "Russian": "ru",
                    "Spanish": "es", "Turkish": "tr", "Urdu": "ur"}.get(self.lang.value, "en")
            webbrowser.open(f"https://www.opensubtitles.com/{lang}/search-all/q-{QUrl.toPercentEncoding(q).data().decode()}")

    def _download_link(self):
        url = self.link.edit.text().strip()
        if not url or not self.target_path or is_url(self.target_path):
            return
        reply = self.net.get(_request(url))

        def done():
            data = bytes(reply.readAll())
            reply.deleteLater()
            name = os.path.basename(QUrl(url).path()) or "subtitles.srt"
            if ext(name) not in SUB_EXT:
                name += ".srt"
            dest = os.path.join(self.folder or os.path.dirname(self.target_path), name)
            try:
                Path(dest).write_bytes(data)
            except OSError:
                self.win.notify("Couldn't save the subtitle file")
                return
            self.checked.add(dest)
            self.origin.set_value("computer")
            self.rebuild()
        reply.finished.connect(done)

    def accept_clicked(self):
        store, engine = self.win.store, self.win.engine
        if self.scope.value == "file":
            if self.target_path and not is_url(self.target_path):
                old = set(store.external_subs(self.target_path))
                store.set_external_subs(self.target_path, sorted(self.checked))
                if engine.current == self.target_path:
                    for s in sorted(self.checked - old):
                        engine.add_subtitle(s)
        else:
            n = 0
            for v, s in self.pairs.items():
                if s:
                    store.set_external_subs(v, [s])
                    n += 1
                    if engine.current == v:
                        engine.add_subtitle(s)
            self.win.notify(f"Subtitles saved for {n} video{'s' if n != 1 else ''}")
        self.accept()


def QCursorPos():
    from PyQt5.QtGui import QCursor
    return QCursor.pos()


# --- Keyboard shortcuts --------------------------------------------------------------------
class KeyField(QWidget):
    """270×33 hotkey box with a pencil; click and press keys to change it."""
    changed = pyqtSignal(str)

    def __init__(self, seq=""):
        super().__init__()
        self.seq = seq
        self.capturing = False
        self.setFixedHeight(33)
        self.setCursor(Qt.PointingHandCursor)
        self.setFocusPolicy(Qt.ClickFocus)

    def mousePressEvent(self, e):
        if e.x() > self.width() - 40 and self.seq and not self.capturing:
            pass
        self.capturing = True
        self.setFocus()
        self.update()

    def focusOutEvent(self, e):
        self.capturing = False
        self.update()

    def keyPressEvent(self, e):
        if not self.capturing:
            return super().keyPressEvent(e)
        k = e.key()
        if k in (Qt.Key_Shift, Qt.Key_Control, Qt.Key_Alt, Qt.Key_Meta):
            return
        if k == Qt.Key_Escape:
            self.capturing = False
            self.clearFocus()
            self.update()
            return
        if k in (Qt.Key_Backspace, Qt.Key_Delete) and not e.modifiers():
            self.seq = ""
        else:
            mods = int(e.modifiers()) & (Qt.ShiftModifier | Qt.ControlModifier | Qt.AltModifier | Qt.MetaModifier)
            self.seq = QKeySequence(mods | k).toString()
        self.capturing = False
        self.clearFocus()
        self.update()
        self.changed.emit(self.seq)

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setPen(QPen(c("accent"), 1) if self.capturing else Qt.NoPen)
        p.setBrush(c("field"))
        p.drawRoundedRect(QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5), 6, 6)
        f = theme.font(14)
        f.setFamily("DejaVu Sans Mono") if False else None
        p.setFont(f)
        p.setPen(c("text_hi") if self.seq else c("text_3"))
        text = "Press a key…" if self.capturing else (self.seq or "")
        p.drawText(QRectF(20, 0, self.width() - 60, self.height()), Qt.AlignCenter, text)
        p.drawPixmap(self.width() - 20 - 24 + 4, 4, icons.pixmap("edit", 24))


class ShortcutsDialog(Dialog):
    def __init__(self, win):
        from .actions import ACTIONS
        tools = QWidget()
        th = QHBoxLayout(tools)
        th.setContentsMargins(0, 0, 0, 0)
        th.setSpacing(10)
        self.search = ToolBox("search", placeholder="Search Keyboard Shortcuts...")
        self.fields = ToolDropdown(["All Fields", "Action", "Hotkey", "Global Hotkey"], "All Fields")
        th.addWidget(self.search, 1)
        th.addWidget(self.fields)
        super().__init__(win, "Keyboard Shortcuts", 1048, 704, tools=tools)
        self.win = win
        self.keys = dict(win.hotkeys())
        self.globals = dict(win.store["global_hotkeys"])
        self.save.setEnabled(False)
        hdr = QHBoxLayout()
        for text, stretch in (("Action", 488), ("Hotkey", 332), ("Global Hotkey", 188)):
            l = Label(text, 14, "text_2")
            l.setFixedWidth(stretch)
            hdr.addWidget(l)
        self.body_lay.setSpacing(12)
        self.body_lay.addLayout(hdr)
        self.body_lay.addWidget(HLine())
        host = QWidget()
        self.rows_lay = QVBoxLayout(host)
        self.rows_lay.setContentsMargins(0, 0, 8, 0)
        self.rows_lay.setSpacing(14)
        self.rows = []
        cat = None
        for aid, category, label, default, _ in ACTIONS:
            if category != cat:
                if cat is not None:
                    self.rows_lay.addWidget(HLine())
                cl = Label(category, 16, "text")
                self.rows_lay.addWidget(cl)
                cat = category
                self.rows.append(("header", category, cl))
            row = QWidget()
            h = QHBoxLayout(row)
            h.setContentsMargins(0, 0, 0, 0)
            name = Label(label, 14, "text_hi")
            h.addWidget(name, 1)
            k = KeyField(self.keys.get(aid, ""))
            k.setFixedWidth(270)
            k.changed.connect(lambda s, a=aid: self._set(a, s))
            h.addWidget(k)
            h.addSpacing(89)
            g = KeyField(self.globals.get(aid, ""))
            g.setFixedWidth(270)
            g.changed.connect(lambda s, a=aid: self._set_global(a, s))
            h.addWidget(g)
            self.rows_lay.addWidget(row)
            self.rows.append((aid, label, row, k, g, category))
        self.rows_lay.addStretch(1)
        self.body_lay.addWidget(scroll(host), 1)
        self.search.edit.textChanged.connect(self._filter)
        self.fields.changed.connect(lambda _v: self._filter())

    def _set(self, aid, seq):
        if seq:
            for other, s in list(self.keys.items()):
                if other != aid and s == seq:
                    self.keys[other] = ""
                    for r in self.rows:
                        if r[0] == other:
                            r[3].seq = ""
                            r[3].update()
                    self.win.notify(f"{seq} was moved from another action")
        self.keys[aid] = seq
        self.save.setEnabled(True)

    def _set_global(self, aid, seq):
        self.globals[aid] = seq
        self.save.setEnabled(True)

    def _filter(self):
        q = self.search.edit.text().lower().strip()
        field = self.fields.value
        for r in self.rows:
            if r[0] == "header":
                continue
            aid, label, row, k, g, cat = r
            hay = {"Action": label, "Hotkey": k.seq, "Global Hotkey": g.seq}.get(field, f"{label} {k.seq} {g.seq}")
            row.setVisible(not q or q in hay.lower())

    def accept_clicked(self):
        from .actions import ACTION
        overrides = {a: s for a, s in self.keys.items() if s != ACTION[a][3]}
        self.win.store["hotkeys"] = overrides
        self.win.store["global_hotkeys"] = {a: s for a, s in self.globals.items() if s}
        self.win.apply_shortcuts()
        self.accept()


# --- Open network stream ---------------------------------------------------------------------
class NetworkDialog(Dialog):
    def __init__(self, win, enqueue=False):
        super().__init__(win, "Open Network Stream", 1048, 581, save_text="Open")
        self.win = win
        self.enqueue = enqueue
        self.body_lay.addWidget(section("Network URL", "You can enter multiple links to load."))
        self.urls = QPlainTextEdit()
        self.urls.setPlaceholderText("e.g., https://www.example.com/stream.avi")
        self.urls.setFixedHeight(194)
        self.urls.setStyleSheet(f"QPlainTextEdit {{ background: {theme.hexc('field')}; border: 2px solid "
                                f"{theme.hexc('item')}; border-radius: 6px; padding: 8px 12px; }}")
        clip = QApplication.clipboard().text().strip()
        if re.match(r"^[a-z]+://\S+$", clip):
            self.urls.setPlainText(clip)
        self.body_lay.addWidget(self.urls)
        self.body_lay.addWidget(HLine())
        self.body_lay.addWidget(section("Playlist Name (optional)", "The playlist will be saved and displayed with this title."))
        self.name = Field("e.g., Favorite TV Channels")
        self.body_lay.addWidget(self.name)
        self.body_lay.addStretch(1)
        self.urls.textChanged.connect(self._update)
        self._update()
        self.net = QNetworkAccessManager(self)

    def _update(self):
        self.save.setEnabled(bool(self.urls.toPlainText().strip()))

    def accept_clicked(self):
        urls = [u.strip() for u in self.urls.toPlainText().split() if u.strip()]
        urls = [u if "://" in u else "http://" + u for u in urls]
        name = self.name.text().strip()
        playlist_like = [u for u in urls if re.search(r"\.(m3u8?|pls)(\?|$)", u, re.I) and not u.lower().endswith(".m3u8")]
        if len(urls) == 1 and playlist_like:
            self._fetch_list(urls[0], name or display_name(urls[0]).rsplit(".", 1)[0].title())
            return
        if name or len(urls) > 1:
            lid = self.win.store.add_network_list(name or f"Streams {time.strftime('%Y-%m-%d %H:%M')}",
                                                  [{"name": display_name(u), "url": u} for u in urls])
            self.win.library.navigate(("network", "list", lid))
        else:
            self.win.store.add_stream(urls[0])
        self.win.store.add_recent_open(urls[0])
        if self.enqueue:
            self.win.enqueue(urls)
        else:
            self.win.play(urls)
        self.accept()

    def _fetch_list(self, url, name):
        self.save.setEnabled(False)
        self.save.setText("Loading…")
        reply = self.net.get(_request(url))

        def done():
            text = bytes(reply.readAll()).decode("utf-8", "replace")
            reply.deleteLater()
            entries = parse_playlist_text(text, "")
            if not entries:
                self.win.notify("That link isn't a playlist; opening it directly")
                self.win.store.add_stream(url)
                self.win.play([url])
                self.accept()
                return
            lid = self.win.store.add_network_list(name, [{"name": n, "url": u} for n, u in entries])
            self.win.notify(f"Saved “{name}” with {len(entries)} streams")
            self.win.show_library_window(("network", "list", lid))
            self.accept()
        reply.finished.connect(done)


# --- New playlist ------------------------------------------------------------------------------
class DropZone(QWidget):
    filesAdded = pyqtSignal(list)

    def __init__(self):
        super().__init__()
        self.setAcceptDrops(True)
        self.setMinimumHeight(186)
        self.setCursor(Qt.PointingHandCursor)
        self.files = []

    def dragEnterEvent(self, e):
        if e.mimeData().hasUrls() or e.mimeData().hasFormat("application/x-vlcr-items"):
            e.acceptProposedAction()

    def dropEvent(self, e):
        md = e.mimeData()
        if md.hasFormat("application/x-vlcr-items"):
            files = json.loads(bytes(md.data("application/x-vlcr-items")).decode())
        else:
            files = [u.toLocalFile() or u.toString() for u in md.urls()]
        self._add(files)

    def mouseReleaseEvent(self, e):
        files, _ = QFileDialog.getOpenFileNames(self, "Select files", os.path.expanduser("~"),
                                                "Media (" + " ".join(f"*.{x}" for x in sorted(MEDIA_EXT)) + ");;All files (*)",
                                                options=self.window().parent().dialog_options() if self.window().parent() else
                                                QFileDialog.Options())
        if files:
            self._add(files)

    def _add(self, files):
        out = []
        for f in files:
            if os.path.isdir(f):
                out.extend(media_in_dir(f, recursive=True))
            else:
                out.append(f)
        self.files.extend(x for x in out if x not in self.files)
        self.update()
        self.filesAdded.emit(self.files)

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setBrush(c("field"))
        pen = QPen(c("text_3"), 1, Qt.DashLine)
        pen.setDashPattern([4, 4])
        p.setPen(pen)
        p.drawRoundedRect(QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5), 6, 6)
        p.setFont(theme.font(14))
        p.setPen(c("text_2"))
        if not self.files:
            p.drawText(QRectF(0, self.height() / 2 - 40, self.width(), 30), Qt.AlignCenter,
                       "Drag files into the box or click here to select files.")
            p.setPen(Qt.NoPen)
            p.setBrush(c("item"))
            br = QRectF(self.width() / 2 - 42, self.height() / 2, 84, 30)
            p.drawRoundedRect(br, 4, 4)
            p.drawPixmap(int(br.center().x() - 12), int(br.center().y() - 12), icons.pixmap("add", 24))
        else:
            y = 12
            for f in self.files[:6]:
                p.drawText(QRectF(16, y, self.width() - 32, 22), Qt.AlignVCenter,
                           elide(display_name(f), theme.font(14), self.width() - 32))
                y += 24
            if len(self.files) > 6:
                p.drawText(QRectF(16, y, self.width() - 32, 22), Qt.AlignVCenter, f"… and {len(self.files) - 6} more")
            p.setPen(c("text_3"))
            p.drawText(QRectF(0, self.height() - 30, self.width() - 16, 22), Qt.AlignRight | Qt.AlignVCenter,
                       f"{len(self.files)} file{'s' if len(self.files) != 1 else ''} · click or drop to add more")


class ThumbPicker(QWidget):
    def __init__(self, path=""):
        super().__init__()
        self.path = path
        self.setFixedSize(112, 112)
        self.setCursor(Qt.PointingHandCursor)
        self.setToolTip("Choose a thumbnail image")

    def mouseReleaseEvent(self, e):
        path, _ = QFileDialog.getOpenFileName(self, "Thumbnail", os.path.expanduser("~/Pictures"),
                                              "Images (*.png *.jpg *.jpeg *.webp *.bmp)")
        if path:
            self.path = path
            self.update()

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setPen(QPen(c("item"), 2))
        p.setBrush(c("field"))
        r = QRectF(self.rect()).adjusted(1, 1, -1, -1)
        p.drawRoundedRect(r, 6, 6)
        pm = QPixmap(self.path) if self.path else QPixmap()
        if not pm.isNull():
            from PyQt5.QtGui import QPainterPath
            path = QPainterPath()
            path.addRoundedRect(r.adjusted(2, 2, -2, -2), 5, 5)
            p.setClipPath(path)
            p.drawPixmap(r.toRect(), pm.scaled(110, 110, Qt.KeepAspectRatioByExpanding, Qt.SmoothTransformation))
        else:
            p.setPen(Qt.NoPen)
            p.setBrush(c("field_2"))
            p.drawRoundedRect(QRectF(41, 41, 30, 30), 5, 5)
            p.drawPixmap(44, 44, icons.pixmap("add", 24))


class PlaylistDialog(Dialog):
    def __init__(self, win, pid=None, items=None):
        pl = win.store.playlist(pid) if pid else None
        super().__init__(win, "Playlist Properties" if pl else "New Playlist", 713, 517)
        self.win, self.pid = win, pid
        top = QHBoxLayout()
        top.setSpacing(20)
        left = QVBoxLayout()
        left.setSpacing(20)
        left.addWidget(section("Playlist Details", "The playlist will be saved and displayed with this title and thumbnail."))
        self.name = Field("e.g., Favorite Videos", pl["name"] if pl else "")
        left.addWidget(self.name)
        top.addLayout(left, 1)
        self.thumb = ThumbPicker(pl.get("thumb", "") if pl else "")
        top.addWidget(self.thumb, 0, Qt.AlignBottom)
        self.body_lay.addLayout(top)
        self.body_lay.addWidget(HLine())
        self.body_lay.addWidget(section("Select Files", "These files will be added to the playlist." if not pl else
                                        f"{len(pl['items'])} items. Files you add here are appended."))
        self.drop = DropZone()
        if items:
            self.drop._add(items)
        self.body_lay.addWidget(self.drop, 1)
        self.name.textChanged.connect(lambda t: self.save.setEnabled(bool(t.strip())))
        self.save.setEnabled(bool(self.name.text().strip()))

    def accept_clicked(self):
        name = self.name.text().strip()
        if not name:
            return
        if self.pid:
            self.win.store.update_playlist(self.pid, name=name, thumb=self.thumb.path)
            if self.drop.files:
                self.win.store.add_to_playlist(self.pid, self.drop.files)
        else:
            self.pid = self.win.store.add_playlist(name, self.drop.files, self.thumb.path)
            self.win.notify(f"Created “{name}”")
        self.accept()


# --- Open disc / capture -------------------------------------------------------------------------
class DiscDialog(Dialog):
    def __init__(self, win):
        super().__init__(win, "Open Disc", 640, 380, save_text="Play")
        self.win = win
        drives = sorted(glob.glob("/dev/sr*")) or [win.store.get("optical_drive")]
        self.kind = Dropdown([("dvd", "DVD"), ("bluray", "Blu-ray"), ("cdda", "Audio CD"), ("vcd", "Video CD")], "dvd",
                             height=44)
        self.drive = Dropdown(drives, win.store.get("optical_drive") if win.store.get("optical_drive") in drives else drives[0],
                              height=44)
        self.menus = Toggle()
        self.menus.setChecked(True)
        grid = QGridLayout()
        grid.setVerticalSpacing(14)
        for row, (label, w) in enumerate((("Disc type", self.kind), ("Drive", self.drive), ("Disc menus", self.menus))):
            grid.addWidget(Label(label, 14, "text_2"), row, 0)
            grid.addWidget(w, row, 1, Qt.AlignRight if isinstance(w, Toggle) else Qt.Alignment())
        grid.setColumnStretch(1, 1)
        self.body_lay.addLayout(grid)
        self.body_lay.addStretch(1)

    def accept_clicked(self):
        kind, drive = self.kind.value, self.drive.value
        if kind == "dvd":
            mrl = f"{'dvd' if self.menus.isChecked() else 'dvdsimple'}://{drive}"
        elif kind == "bluray":
            mrl = f"bluray://{drive}"
        elif kind == "vcd":
            mrl = f"vcd://{drive}"
        else:
            mrl = f"cdda://{drive}"
        self.win.play([mrl], names={mrl: f"{self.kind.label()} ({drive})"})
        self.accept()


class CaptureDialog(Dialog):
    def __init__(self, win):
        super().__init__(win, "Open Capture Device", 640, 400, save_text="Play")
        self.win = win
        vids = sorted(glob.glob("/dev/video*"))
        self.mode = Dropdown([("v4l2", "Camera / capture card"), ("screen", "Desktop (screen)")], "v4l2" if vids else "screen",
                             height=44)
        self.device = Dropdown(vids or ["(no video devices)"], vids[0] if vids else None, height=44)
        sources = [("", "No audio")]
        try:
            r = subprocess.run(["pactl", "list", "short", "sources"], capture_output=True, text=True, timeout=3)
            for line in r.stdout.splitlines():
                parts = line.split("\t")
                if len(parts) > 1:
                    sources.append((parts[1], parts[1].replace("alsa_input.", "").replace("alsa_output.", "")[:60]))
        except (OSError, subprocess.TimeoutExpired):
            pass
        self.audio = Dropdown(sources, "", height=44)
        self.fps = Dropdown(["10", "15", "24", "30", "60"], "30", height=44)
        grid = QGridLayout()
        grid.setVerticalSpacing(14)
        for row, (label, w) in enumerate((("Capture", self.mode), ("Video device", self.device),
                                          ("Audio", self.audio), ("Frame rate (screen)", self.fps))):
            grid.addWidget(Label(label, 14, "text_2"), row, 0)
            grid.addWidget(w, row, 1)
        grid.setColumnStretch(1, 1)
        self.body_lay.addLayout(grid)
        self.body_lay.addStretch(1)

    def accept_clicked(self):
        if self.mode.value == "screen":
            mrl = "screen://"
            opts = f":screen-fps={self.fps.value}"
            name = "Desktop"
        else:
            mrl = f"v4l2://{self.device.value}"
            opts = ""
            name = os.path.basename(self.device.value or "camera")
        self.win.engine.capture_options = [opts] if opts else []
        if self.audio.value:
            self.win.engine.capture_options.append(f":input-slave=pulse://{self.audio.value}")
        self.win.play([mrl], names={mrl: name})
        self.accept()


# --- Convert / stream ----------------------------------------------------------------------------
PROFILES = [
    ("mp4", "Video – H.264 + AAC (MP4)", ["-c:v", "libx264", "-preset", "medium", "-crf", "21", "-c:a", "aac", "-b:a", "160k"], "mp4"),
    ("mkv", "Video – H.265 + AAC (MKV)", ["-c:v", "libx265", "-crf", "24", "-c:a", "aac", "-b:a", "160k"], "mkv"),
    ("webm", "Video – VP9 + Opus (WebM)", ["-c:v", "libvpx-vp9", "-b:v", "0", "-crf", "32", "-c:a", "libopus"], "webm"),
    ("copy", "Remux only – keep streams (MKV)", ["-map", "0", "-c", "copy"], "mkv"),
    ("mp3", "Audio – MP3", ["-vn", "-c:a", "libmp3lame", "-q:a", "2"], "mp3"),
    ("flac", "Audio – FLAC", ["-vn", "-c:a", "flac"], "flac"),
    ("opus", "Audio – Opus (OGG)", ["-vn", "-c:a", "libopus", "-b:a", "128k"], "opus"),
]


class ConvertDialog(Dialog):
    def __init__(self, win, items):
        super().__init__(win, "Convert", 760, 440, save_text="Start")
        self.win = win
        self.items = [i for i in items if not is_url(i)] or []
        self.body_lay.addWidget(section("Files", "\n".join(display_name(i) for i in self.items[:4]) +
                                        (f"\n… and {len(self.items) - 4} more" if len(self.items) > 4 else "") or
                                        "No local files selected."))
        self.profile = Dropdown([(k, label) for k, label, _, _ in PROFILES], "mp4", height=44)
        self.dest = Field("Same folder as each file")
        browse = Button("Browse", "dark", height=37)
        browse.setFixedWidth(100)
        browse.clicked.connect(self._browse)
        row = QHBoxLayout()
        row.addWidget(self.dest, 1)
        row.addWidget(browse)
        self.body_lay.addWidget(Label("Profile", 14, "text_2"))
        self.body_lay.addWidget(self.profile)
        self.body_lay.addWidget(Label("Save to", 14, "text_2"))
        self.body_lay.addLayout(row)
        self.status = Label("", 14, "text_2")
        self.body_lay.addWidget(self.status)
        self.body_lay.addStretch(1)
        self.proc = None
        self.queue = []
        self.save.setEnabled(bool(self.items) and bool(shutil.which("ffmpeg")))
        if not shutil.which("ffmpeg"):
            self.status.setText("Converting needs ffmpeg (sudo apt install ffmpeg).")

    def _browse(self):
        d = QFileDialog.getExistingDirectory(self, "Save converted files to", os.path.expanduser("~/Videos"),
                                             options=self.win.dialog_options())
        if d:
            self.dest.setText(d)

    def accept_clicked(self):
        prof = next(p for p in PROFILES if p[0] == self.profile.value)
        self.queue = list(self.items)
        self.args, self.extn = prof[2], prof[3]
        self.save.setEnabled(False)
        self.cancel.setText("Stop")
        self._next()

    def _next(self):
        if not self.queue:
            self.status.setText("Done.")
            self.cancel.setText("Close")
            self.win.notify("Conversion finished")
            return
        src = self.queue.pop(0)
        folder = self.dest.text().strip() or os.path.dirname(src)
        out = os.path.join(folder, os.path.splitext(os.path.basename(src))[0] + "." + self.extn)
        if os.path.abspath(out) == os.path.abspath(src):
            out = os.path.splitext(out)[0] + "-converted." + self.extn
        self._status = f"Converting {display_name(src)}…"
        self.status.setText(self._status)
        self.proc = QProcess(self)
        self.proc.finished.connect(lambda code, _s: (self.status.setText("Failed." if code else ""), self._next()))
        self.proc.readyReadStandardError.connect(self._progress)
        self.duration = (self.win.media.info_now(src) or {}).get("duration", 0)
        self.proc.start("ffmpeg", ["-nostdin", "-y", "-i", src] + self.args + [out])

    def _progress(self):
        text = bytes(self.proc.readAllStandardError()).decode(errors="replace")
        m = re.findall(r"time=(\d+):(\d+):(\d+\.\d+)", text)
        if m and self.duration:
            h, mi, s = m[-1]
            t = (int(h) * 3600 + int(mi) * 60 + float(s)) * 1000
            self.status.setText(f"{self._status} ({min(100, t / self.duration * 100):.0f}%)")

    def reject(self):
        if self.proc and self.proc.state() != QProcess.NotRunning:
            self.queue = []
            self.proc.kill()
        super().reject()


class StreamDialog(Dialog):
    """Stream out over HTTP with libVLC's stream output (a separate player)."""

    def __init__(self, win, items):
        super().__init__(win, "Stream", 760, 420, save_text="Start Streaming")
        self.win = win
        self.items = items
        s = win.store.get
        self.body_lay.addWidget(section("Stream over HTTP", "Other devices on your network can open the address below "
                                        "in VLC or a browser."))
        self.port = Field("8080", "8080")
        self.path = Field("/stream", "/stream")
        grid = QGridLayout()
        grid.addWidget(Label("Port", 14, "text_2"), 0, 0)
        grid.addWidget(self.port, 0, 1)
        grid.addWidget(Label("Path", 14, "text_2"), 1, 0)
        grid.addWidget(self.path, 1, 1)
        self.body_lay.addLayout(grid)
        self.address = Label("", 14, "accent")
        self.address.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.body_lay.addWidget(self.address)
        self.body_lay.addStretch(1)
        self.player = None

    def accept_clicked(self):
        import socket
        import vlc
        s = self.win.store.get
        port = int(self.port.text() or 8080)
        path = self.path.text() or "/stream"
        venc = f"vcodec=h264,venc=x264{{preset={s('stream_preset').lower()}" + \
            (f",tune={s('stream_tune').lower().replace(' ', '')}" if s("stream_tune") != "None" else "") + "}," \
            if s("stream_video") else "vcodec=none,"
        aenc = "acodec=mp4a,ab=160,channels=2," if s("stream_audio") else "acodec=none,"
        senc = "scodec=dvbs,soverlay," if s("stream_subs") else ""
        chain = f"#transcode{{{venc}{aenc}{senc}}}:http{{mux=ts,dst=:{port}{path}}}"
        if s("stream_local"):
            chain = f"#transcode{{{venc}{aenc}{senc}}}:duplicate{{dst=http{{mux=ts,dst=:{port}{path}}},dst=display}}"
        self.inst = vlc.Instance(["--no-video-title-show"])
        self.player = self.inst.media_list_player_new()
        ml = self.inst.media_list_new()
        for i in self.items:
            m = self.inst.media_new(i) if is_url(i) else self.inst.media_new_path(i)
            m.add_option(":sout=" + chain)
            m.add_option(":sout-keep")
            ml.add_media(m)
            m.release()
        self.player.set_media_list(ml)
        self.player.play()
        try:
            host = socket.gethostbyname(socket.gethostname())
        except OSError:
            host = "this-computer"
        self.address.setText(f"http://{host}:{port}{path}")
        self.save.setEnabled(False)
        self.cancel.setText("Stop")
        self.win.notify("Streaming started")

    def reject(self):
        if self.player:
            self.player.stop()
            self.player.release()
            self.inst.release()
            self.player = None
        super().reject()


# --- Properties & metadata --------------------------------------------------------------------------
class PropertiesDialog(Dialog):
    FIELDS = [("Title", "Title"), ("Artist", "Artist"), ("Album", "Album"), ("Genre", "Genre"), ("Date", "Date"),
              ("TrackNumber", "Track number"), ("Description", "Comment")]

    def __init__(self, win, item):
        super().__init__(win, "Properties & Metadata", 760, 640)
        import vlc
        self.win, self.item = win, item
        self.media = None
        self.edits = {}
        grid = QGridLayout()
        grid.setVerticalSpacing(10)
        grid.setHorizontalSpacing(16)
        editable = not is_url(item)
        if editable:
            inst = win.engine.instance
            self.media = inst.media_new_path(item)
            self.media.parse_with_options(vlc.MediaParseFlag.local, 3000)
            t0 = time.time()
            while self.media.get_parsed_status() == 0 and time.time() - t0 < 3:
                QApplication.processEvents()
                time.sleep(0.02)
        for row, (key, label) in enumerate(self.FIELDS):
            val = ""
            if self.media is not None:
                v = self.media.get_meta(getattr(vlc.Meta, key))
                val = v.decode() if isinstance(v, bytes) else (v or "")
            f = Field("", val, height=34)
            f.setReadOnly(not editable)
            grid.addWidget(Label(label, 14, "text_2"), row, 0)
            grid.addWidget(f, row, 1)
            self.edits[key] = f
        self.body_lay.addLayout(grid)
        self.body_lay.addWidget(HLine())
        info = self._technical()
        text = "\n".join(f"{k}:  {v}" for k, v in info.items() if v)
        lab = Label(text, 13, "text_2", wrap=True)
        lab.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.body_lay.addWidget(scroll(lab), 1)
        self.save.setEnabled(editable)

    def _technical(self):
        out = {"Location": self.item}
        if not is_url(self.item):
            try:
                out["Size"] = f"{os.path.getsize(self.item) / 1048576:.1f} MiB"
            except OSError:
                pass
            if shutil.which("ffprobe"):
                r = subprocess.run(["ffprobe", "-v", "error", "-show_entries",
                                    "format=duration,bit_rate:stream=codec_type,codec_long_name,width,height,"
                                    "r_frame_rate,channels,sample_rate:stream_tags=language,title", "-of", "json",
                                    self.item], capture_output=True, timeout=10)
                try:
                    data = json.loads(r.stdout or b"{}")
                    fmt = data.get("format", {})
                    if fmt.get("duration"):
                        out["Duration"] = fmt_time(float(fmt["duration"]) * 1000)
                    if fmt.get("bit_rate"):
                        out["Bitrate"] = f"{int(fmt['bit_rate']) // 1000} kb/s"
                    for n, st in enumerate(data.get("streams", [])):
                        t = st.get("codec_type", "")
                        desc = st.get("codec_long_name", "")
                        if t == "video":
                            fr = st.get("r_frame_rate", "0/1").split("/")
                            fps = float(fr[0]) / max(1, float(fr[1])) if len(fr) == 2 else 0
                            desc += f", {st.get('width')}×{st.get('height')}, {fps:.3g} fps"
                        elif t == "audio":
                            desc += f", {st.get('channels')} ch, {st.get('sample_rate')} Hz"
                        lang = (st.get("tags") or {}).get("language")
                        if lang:
                            desc += f" [{lang}]"
                        out[f"Stream {n} ({t})"] = desc
                except ValueError:
                    pass
        if self.item == self.win.engine.current and self.win.store.get("developer_mode"):
            out.update(self.win.engine.stats())
        return out

    def accept_clicked(self):
        import vlc
        if self.media is not None:
            for key, f in self.edits.items():
                self.media.set_meta(getattr(vlc.Meta, key), f.text())
            ok = self.media.save_meta()
            self.win.notify("Metadata saved" if ok else "Couldn't write the tags to this file")
            from .media import _key
            self.win.media._info.pop(_key(self.item), None)
        self.accept()

    def done(self, r):
        if self.media is not None:
            self.media.release()
            self.media = None
        super().done(r)


class AboutDialog(Dialog):
    def __init__(self, win):
        super().__init__(win, "About VLC", 560, 430, footer=False)
        v = self.body_lay
        v.setSpacing(10)
        cone = Label("")
        cone.setPixmap(icons.pixmap("vlc", 72))
        cone.setAlignment(Qt.AlignCenter)
        v.addWidget(cone)
        t = Label(f"{APP_NAME} {__version__}", 20, "text_hi")
        t.setAlignment(Qt.AlignCenter)
        v.addWidget(t)
        import vlc
        ver = vlc.libvlc_get_version()
        ver = ver.decode() if isinstance(ver, bytes) else ver
        for text in (f"Plays everything through libVLC {ver}.",
                     "Interface design: “VLC Redesign” by Juxtopposed, CC BY 4.0.",
                     "Icons from the same design. Font: Inter (SIL Open Font License).",
                     "Not affiliated with VideoLAN. VLC and the cone are trademarks of VideoLAN."):
            l = Label(text, 14, "text_2", wrap=True)
            l.setAlignment(Qt.AlignCenter)
            v.addWidget(l)
        row = QHBoxLayout()
        row.addStretch(1)
        for label, url in (("The redesign video", "https://youtu.be/ERFeOgqk324"),
                           ("Figma file", "https://www.figma.com/community/file/1691465999134370980"),
                           ("Donate to VideoLAN", "https://www.videolan.org/contribute.html")):
            b = Button(label, "dark", height=32, bold=False)
            b.clicked.connect(lambda _=False, u=url: webbrowser.open(u))
            row.addWidget(b)
        row.addStretch(1)
        v.addStretch(1)
        v.addLayout(row)


class MessageDialog(Dialog):
    def __init__(self, win, title, text, detail="", ok="OK", cancel="Cancel"):
        super().__init__(win, title, 520, 220, save_text=ok, cancel_text=cancel)
        self.body_lay.addWidget(Label(text, 16, "text_hi", wrap=True))
        if detail:
            self.body_lay.addWidget(Label(detail, 14, "text_2", wrap=True))
        self.body_lay.addStretch(1)
        if not cancel:
            self.cancel.hide()
