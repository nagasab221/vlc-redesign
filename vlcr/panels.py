"""The right-hand panels that float over the video (400 px, #0e0e0e at 95%, r=10):

  tracks    Video | Audio | Subtitles      (audio and subtitle buttons)
  settings  Playback | Video | Audio | Subtitles + presets   (playback-settings button)
  queue     Queue | Bookmarks | Chapters   (queue button)
"""

import os

from PyQt5.QtCore import QEvent, QPoint, QRect, QRectF, QSize, Qt, QTimer, pyqtSignal
from PyQt5.QtGui import QColor, QFontMetrics, QPainter, QPainterPath
from PyQt5.QtWidgets import (QAbstractItemView, QFileDialog, QToolTip, QHBoxLayout, QInputDialog, QListWidget,
                             QListWidgetItem, QScrollArea, QStackedWidget, QStyledItemDelegate, QVBoxLayout, QWidget)

from . import icons, prefs, theme
from .engine import SLEEP_OPTIONS, STEREO_MODES, display_name, fmt_ms, fmt_short, fmt_total, is_url
from .theme import c
from .widgets import (Button, ColorField, Dropdown, Expander, HLine, IconButton, Label, MenuItem, NumberField,
                      PopupMenu, Segmented, Slider, Tabs, Toggle, VSlider, clear_layout, elide, sep, wants_list_key)
from .filedialog import FileDialog

PANEL_W = 400


class Panel(QWidget):
    """Floating rounded panel (a translucent tool window over the video)."""
    closed = pyqtSignal()

    def __init__(self, window, tabs):
        super().__init__(window, Qt.Tool | Qt.FramelessWindowHint | Qt.NoDropShadowWindowHint)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.win = window
        self.engine = window.engine
        self.store = window.store
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        self.tabs = Tabs(tabs)
        self.tabs.changed.connect(self.show_tab)
        lay.addWidget(self.tabs)
        self.stack = QStackedWidget()
        lay.addWidget(self.stack, 1)
        self.pages = {}
        self.anchor_button = None

    def add_page(self, key, widget):
        self.pages[key] = widget
        self.stack.addWidget(widget)

    def show_tab(self, key):
        if key in self.pages:
            self.tabs.set_current(key)
            self.stack.setCurrentWidget(self.pages[key])
            page = self.pages[key]
            if hasattr(page, "refresh"):
                page.refresh()

    def current_tab(self):
        return self.tabs.current

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        path = QPainterPath()
        path.addRoundedRect(QRectF(self.rect()), 10, 10)
        p.setClipPath(path)
        p.fillRect(self.rect(), c("base", 0.96))
        p.fillRect(QRect(0, 0, self.width(), self.tabs.height()), c("panel_head"))

    def resizeEvent(self, e):
        path = QPainterPath()
        path.addRoundedRect(QRectF(self.rect()), 10, 10)
        from PyQt5.QtGui import QRegion
        self.setMask(QRegion(path.toFillPolygon().toPolygon()))

    def keyPressEvent(self, e):
        if e.key() == Qt.Key_Escape:
            self.hide()
            self.closed.emit()
        else:
            super().keyPressEvent(e)

    def hideEvent(self, e):
        self.closed.emit()
        super().hideEvent(e)


# --- building blocks ---------------------------------------------------------------
class Form(QWidget):
    """Vertical form with the panel's spacing: 20 px sides, sections split by lines."""

    def __init__(self, parent=None, side=20):
        super().__init__(parent)
        self.lay = QVBoxLayout(self)
        self.lay.setContentsMargins(side, 4, side, 16)
        self.lay.setSpacing(10)

    def header(self, text, buttons=(), size=16, medium=True, top=14):
        row = QHBoxLayout()
        row.setContentsMargins(0, top, 0, 2)
        lab = Label(text, size, "text" if medium else "text_2", weight=theme.MEDIUM if medium else 50)
        row.addWidget(lab)
        row.addStretch(1)
        for b in buttons:
            row.addWidget(b)
        self.lay.addLayout(row)
        return lab

    def line(self):
        self.lay.addSpacing(6)
        w = HLine()
        self.lay.addWidget(w)
        self.lay.addSpacing(2)

    def row(self, label, widget, width=140, info=""):
        row = QHBoxLayout()
        row.setContentsMargins(4, 0, 0, 0)
        lab = Label(label, 14, "text_2")
        if info:
            lab.setToolTip(info)
        row.addWidget(lab)
        row.addStretch(1)
        if width:
            widget.setFixedWidth(width)
        row.addWidget(widget)
        self.lay.addLayout(row)
        return row

    def slider_row(self, label, slider, value_widget=None, value_text=None):
        box = QVBoxLayout()
        box.setSpacing(6)
        top = QHBoxLayout()
        top.setContentsMargins(4, 0, 0, 0)
        top.addWidget(Label(label, 14, "text_2"))
        top.addStretch(1)
        vt = None
        if value_widget is not None:
            value_widget.setFixedWidth(140)
            top.addWidget(value_widget)
        elif value_text is not None:
            vt = Label(value_text, 14, "text_2")
            top.addWidget(vt)
        box.addLayout(top)
        box.addWidget(slider)
        self.lay.addLayout(box)
        return vt

    def widget(self, w):
        self.lay.addWidget(w)
        return w

    def stretch(self):
        self.lay.addStretch(1)


def scrolled(widget):
    area = QScrollArea()
    area.setWidgetResizable(True)
    area.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
    area.setStyleSheet("QScrollArea { background: transparent; border: none; }")
    area.viewport().setStyleSheet("background: transparent;")
    area.setWidget(widget)
    return area


class ChoiceList(QWidget):
    """Rows with a check on the selected one (tracks, devices)."""
    picked = pyqtSignal(object)

    ROW = 44

    def __init__(self, parent=None, elide_mode=Qt.ElideRight):
        super().__init__(parent)
        self.elide_mode = elide_mode
        self.items = []         # (value, label)
        self.value = None
        self.hover = -1
        self.setMouseTracking(True)
        self.setCursor(Qt.PointingHandCursor)

    def set_items(self, items, value):
        self.items = items
        self.value = value
        self.setFixedHeight(max(1, len(items)) * self.ROW if items else 0)
        self.update()

    def mouseMoveEvent(self, e):
        h = e.y() // self.ROW
        if h != self.hover:
            self.hover = h
            self.update()
            if 0 <= h < len(self.items):
                QToolTip.showText(e.globalPos(), self.items[h][1], self)

    def leaveEvent(self, e):
        self.hover = -1
        self.update()

    def mouseReleaseEvent(self, e):
        i = e.y() // self.ROW
        if 0 <= i < len(self.items):
            self.value = self.items[i][0]
            self.update()
            self.picked.emit(self.value)

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        f = theme.font(14)
        p.setFont(f)
        for i, (val, label) in enumerate(self.items):
            r = QRectF(0, i * self.ROW, self.width(), self.ROW)
            if i == self.hover:
                p.setPen(Qt.NoPen)
                p.setBrush(c("hover"))
                p.drawRoundedRect(r, 6, 6)
            if val == self.value:
                p.drawPixmap(QRectF(8, r.center().y() - 12, 24, 24).toRect(), icons.pixmap("check", 24, None, "active"))
            p.setPen(c("text"))
            p.drawText(r.adjusted(42, 0, -8, 0), Qt.AlignVCenter, elide(label, f, r.width() - 50, self.elide_mode))


# --- tracks panel ---------------------------------------------------------------------
class TracksPanel(Panel):
    def __init__(self, window):
        super().__init__(window, [("video", "Video", "video"), ("audio", "Audio", "audio"),
                                  ("subtitles", "Subtitles", "subtitles")])
        self.video_page = self._video_page()
        self.audio_page = self._audio_page()
        self.sub_page = self._sub_page()
        for key, page in (("video", self.video_page), ("audio", self.audio_page), ("subtitles", self.sub_page)):
            area = scrolled(page)
            area.refresh = self.refresh
            self.add_page(key, area)
        self.engine.tracksChanged.connect(self.refresh)
        self.engine.mediaChanged.connect(self.refresh)

    def _footer(self, form):
        form.stretch()
        b = Button("Show Preferences", "dark", height=32, bold=False)
        b.clicked.connect(lambda: self.win.toggle_panel("settings", self.current_tab(), None, force=True))
        form.widget(b)

    def _video_page(self):
        f = Form()
        f.header("Embedded Tracks", size=14, medium=False)
        self.vlist = ChoiceList()
        self.vlist.picked.connect(self.engine.set_video_track)
        f.widget(self.vlist)
        self._footer(f)
        f.refresh = self.refresh
        return f

    def _audio_page(self):
        f = Form()
        f.header("Embedded Tracks", size=14, medium=False)
        self.alist = ChoiceList()
        self.alist.picked.connect(self.engine.set_audio_track)
        f.widget(self.alist)
        f.line()
        refresh = IconButton("refresh", "Refresh the device list", box=(30, 30))
        refresh.clicked.connect(self.refresh)
        f.header("Output Device", [refresh], size=14, medium=False, top=4)
        self.dlist = ChoiceList(elide_mode=Qt.ElideMiddle)
        self.dlist.picked.connect(self._pick_device)
        f.widget(self.dlist)
        self._footer(f)
        f.refresh = self.refresh
        return f

    def _sub_page(self):
        f = Form()
        f.header("Embedded Tracks", size=14, medium=False)
        self.slist = ChoiceList()
        self.slist.picked.connect(self.engine.set_spu)
        f.widget(self.slist)
        f.line()
        edit = IconButton("edit", "Match subtitle files to episodes…", box=(30, 30))
        edit.clicked.connect(lambda: self.win.open_add_subtitles())
        add = IconButton("add", "Add a subtitle file…", box=(30, 30))
        add.clicked.connect(self._add_sub)
        f.header("External Tracks", [edit, add], size=14, medium=False, top=4)
        self.xlist = ChoiceList()
        self.xlist.picked.connect(self.engine.set_spu)
        f.widget(self.xlist)
        self.xempty = Label("Add a subtitle file with +, or drop one on the video.", 13, "text_3", wrap=True)
        f.widget(self.xempty)
        self._footer(f)
        f.refresh = self.refresh
        return f

    def _add_sub(self):
        start = os.path.dirname(self.engine.current) if self.engine.current and not is_url(self.engine.current) else ""
        path, _ = FileDialog.getOpenFileName(self.win, "Add subtitles", start,
                                              "Subtitles (*.srt *.ass *.ssa *.vtt *.sub *.smi *.idx);;All files (*)",
                                              options=self.win.dialog_options())
        if path:
            self.engine.add_subtitle(path)

    def _pick_device(self, dev):
        self.engine.set_audio_device(dev)
        self.store.set("audio_device", dev or "System default")

    def refresh(self):
        e = self.engine
        if not e.current:
            for lst in (self.vlist, self.alist, self.slist, self.xlist):
                lst.set_items([], None)
            return
        vt = [(-1, "None")] + [(i, n) for i, n in e.video_tracks() if i != -1]
        self.vlist.set_items(vt, e.video_track())
        at = [(-1, "None")] + [(i, n) for i, n in e.audio_tracks() if i != -1]
        self.alist.set_items(at, e.audio_track())
        devs = [(None, "System default")] + [(d, desc) for d, desc in e.audio_devices()]
        cur = e.audio_device() or None
        if cur not in [d for d, _ in devs]:
            cur = None
        self.dlist.set_items(devs, cur)
        tracks = [(i, n) for i, n in e.spu_tracks() if i != -1]
        ext = e.external_subtitles()
        n_ext = min(len(ext), len(tracks))
        embedded = tracks[:len(tracks) - n_ext] if n_ext else tracks
        external = list(zip([t[0] for t in tracks[len(tracks) - n_ext:]], [os.path.basename(x) for x in ext[-n_ext:]])) \
            if n_ext else []
        self.slist.set_items([(-1, "None")] + embedded, e.spu())
        self.xlist.set_items(external, e.spu())
        self.xempty.setVisible(not external)


# --- settings panel ---------------------------------------------------------------------
class SettingsPanel(Panel):
    def __init__(self, window):
        super().__init__(window, [("playback", "Playback", "playback"), ("video", "Video", "video"),
                                  ("audio", "Audio", "audio"), ("subtitles", "Subtitles", "subtitles")])
        self.fx = self.engine.fx
        self._binders = []          # (fx key, setter) for refreshing
        for key, build in (("playback", self._playback), ("video", self._video), ("audio", self._audio),
                           ("subtitles", self._subtitles)):
            page = QWidget()
            v = QVBoxLayout(page)
            v.setContentsMargins(0, 0, 0, 0)
            v.setSpacing(0)
            form = Form()
            build(form)
            form.stretch()
            v.addWidget(scrolled(form), 1)
            if key != "playback":
                v.addWidget(self._footer())
            page.refresh = self.refresh
            self.add_page(key, page)
        self.engine.fxChanged.connect(self._fx_changed)
        self.engine.rateChanged.connect(lambda r: self._fx_changed("speed"))
        self.store.libraryChanged.connect(lambda what: what == "presets" and self._update_presets())

    # binding helpers --------------------------------------------------------
    def _bind(self, key, setter):
        self._binders.append((key, setter))
        setter(self.engine.fx[key])

    def _fx_changed(self, key):
        for k, setter in self._binders:
            if not key or k == key:
                setter(self.engine.fx[k])

    def refresh(self):
        self._fx_changed("")
        self._update_presets()

    def _set(self, key, value):
        self.engine.set_fx(key, value)

    def _update_sub_note(self, *_):
        note = self.engine.sub_style_note()
        self.sub_note.setText(note)
        self.sub_note.setVisible(bool(note))

    def toggle_row(self, form, label, key):
        t = Toggle()
        t.toggled.connect(lambda on, k=key: self._set(k, on))
        self._bind(key, lambda v, t=t: (t.blockSignals(True), t.setChecked(bool(v)), t.blockSignals(False), t.update()))
        form.row(label, t, width=68)
        return t

    def dropdown_row(self, form, label, key, options, width=220, icon_for=None):
        d = Dropdown(options, self.engine.fx[key], height=48, icon_for=icon_for)
        d.changed.connect(lambda v, k=key: self._set(k, v))
        self._bind(key, d.set_value)
        form.row(label, d, width=width)
        return d

    def number_row(self, form, label, key, minimum, maximum, step, decimals, suffix, width=140, scale=1.0):
        n = NumberField(self.engine.fx[key] * scale, minimum, maximum, step, decimals, suffix)
        n.valueChanged.connect(lambda v, k=key: self._set(k, v / scale))
        self._bind(key, lambda v, n=n: n.set_value(v * scale))
        form.row(label, n, width=width)
        return n

    def slider(self, key, minimum, maximum, step, ticks=0, labels=None, scale=1.0, live=True, mark=None):
        s = Slider(minimum, maximum, self.engine.fx[key] * scale, step, ticks, labels, thumb="accent_2", mark=mark,
                   centered=True)
        if live:
            s.changed.connect(lambda v, k=key: self._set(k, v / scale))
        else:
            s.released.connect(lambda v, k=key: self._set(k, v / scale))
        self._bind(key, lambda v, s=s: s.set_value(v * scale))
        return s

    def num_slider(self, form, label, key, minimum, maximum, step, decimals, suffix, labels=None, ticks=0, live=True):
        n = NumberField(self.engine.fx[key], minimum, maximum, step, decimals, suffix)
        n.valueChanged.connect(lambda v, k=key: self._set(k, v))
        self._bind(key, n.set_value)
        s = self.slider(key, minimum, maximum, step, ticks, labels, live=live)
        form.slider_row(label, s, value_widget=n)

    def text_slider(self, form, label, key, minimum, maximum, step, fmt, live=False, ticks=0):
        s = self.slider(key, minimum, maximum, step, ticks, live=live)
        vt = form.slider_row(label, s, value_text=fmt(self.engine.fx[key]))
        self._bind(key, lambda v, vt=vt: vt.setText(fmt(v)))

    # pages ------------------------------------------------------------------
    def _playback(self, f):
        f.lay.addSpacing(6)
        self.toggle_row(f, "Show Subtitle Timeline on Seekbar", "sub_timeline")
        self.toggle_row(f, "Show Waveform on Seekbar", "waveform")
        self.toggle_row(f, "Show Statistics", "statistics")
        rev = self.toggle_row(f, "Reverse", "reverse")
        rev.setEnabled(False)
        rev.setToolTip("libVLC 3 can't play backwards.")
        self.dropdown_row(f, "Sleep Timer", "sleep", SLEEP_OPTIONS)
        viz_icons = {"Spectometer": "spectometer", "Scope": "scope", "Spectrum": "spectrum", "Waveform": "waveform"}
        self.dropdown_row(f, "Audio Visualizer", "visualizer", prefs.VISUALIZERS, icon_for=lambda v: viz_icons.get(v))
        speed = NumberField(self.engine.fx["speed"], 0.25, 4.0, 0.05, 2, "x")
        speed._fmt = lambda v: f"{v:g}x"
        speed.set_value(self.engine.fx["speed"])
        speed.valueChanged.connect(lambda v: self.engine.set_rate(v))
        self._bind("speed", speed.set_value)
        s = self.slider("speed", 0.25, 2.0, 0.05, ticks=8, labels=["0.25", "0.5", "0.75", "1", "1.25", "1.5", "1.75", "2"])
        f.slider_row("Speed", s, value_widget=speed)

    def _video(self, f):
        f.header("Defaults")
        seg = Segmented([("none", "None"), ("horizontal", "Horizontal"), ("vertical", "Vertical")], height=44)
        seg.changed.connect(lambda v: self._set("flip", v))
        self._bind("flip", seg.set_value)
        box = QVBoxLayout()
        box.setSpacing(8)
        box.addWidget(Label("Flip", 14, "text_2"))
        box.addWidget(seg)
        f.lay.addLayout(box)
        self.number_row(f, "Rotate", "rotate", 0, 359, 90, 0, " deg")
        self.dropdown_row(f, "Zoom", "zoom", prefs.ZOOMS)
        hdr = self.dropdown_row(f, "HDR", "hdr", ["Auto", "Off", "HDR10", "HDR10+", "Dolby Vision", "HLG"])
        hdr.setEnabled(False)
        hdr.setToolTip("HDR tone mapping needs VLC 4 (libplacebo); libVLC 3 shows HDR video as it is.")
        self.dropdown_row(f, "Resolution", "resolution", ["Best Available", "2160p", "1440p", "1080p", "720p", "576p",
                                                          "480p", "360p", "240p"])
        self.dropdown_row(f, "Aspect Ratio", "aspect", self.win.aspect_options())
        self.dropdown_row(f, "Crop", "crop", self.win.crop_options())
        render = self.dropdown_row(f, "Render", "render", ["Local"])
        render.setToolTip("Casting needs renderer discovery, which this libVLC build doesn't include.")
        f.line()
        f.header("Filters")
        f.lay.addWidget(Label("Essential", 14, "text_2"))
        self._expandable(f, self._essential)
        f.lay.addWidget(Label("Crop", 14, "text_2"))
        self._expandable(f, self._crop_px)
        f.lay.addWidget(Label("Colors", 14, "text_2"))
        self._expandable(f, self._colors)

    def _expandable(self, form, build):
        exp = Expander("Expand Settings", centered=False, open_text="Collapse Settings", dark=True, height=40)
        body = Form(side=12)
        body.lay.setContentsMargins(12, 0, 12, 12)
        build(body)
        holder = QWidget()
        hv = QVBoxLayout(holder)
        hv.setContentsMargins(0, 0, 0, 0)
        hv.setSpacing(0)
        hv.addWidget(exp)
        hv.addWidget(body)
        body.hide()
        exp.toggled.connect(body.setVisible)
        form.widget(holder)

    def _essential(self, f):
        self.text_slider(f, "Hue", "hue", -180, 180, 1, lambda v: f"{v:+.0f}°", live=True)
        self.text_slider(f, "Brightness", "brightness", 0, 2, 0.01, lambda v: f"{v:.2f}", live=True)
        self.text_slider(f, "Contrast", "contrast", 0, 2, 0.01, lambda v: f"{v:.2f}", live=True)
        self.text_slider(f, "Saturation", "saturation", 0, 3, 0.01, lambda v: f"{v:.2f}", live=True)
        self.text_slider(f, "Gamma", "gamma", 0.01, 10, 0.01, lambda v: f"{v:.2f}", live=True, ticks=10)
        self.text_slider(f, "Sharpen Sigma", "sharpen", 0, 2, 0.05, lambda v: "Off" if v <= 0 else f"{v:.2f}", ticks=5)
        self.text_slider(f, "Banding Removal Radius", "banding", 0, 20, 1, lambda v: "Off" if v <= 0 else f"{v:.0f}",
                         ticks=11)
        self.text_slider(f, "Film Grain Variance", "grain", 0, 10, 0.5, lambda v: "Off" if v <= 0 else f"{v:.1f}",
                         ticks=11)
        reset = Button("Reset", "dark", height=32)
        reset.clicked.connect(lambda: self.engine.reset_fx(["hue", "brightness", "contrast", "saturation", "gamma",
                                                            "sharpen", "banding", "grain"]))
        f.widget(reset)

    def _crop_px(self, f):
        grid = QWidget()
        g = QVBoxLayout(grid)
        g.setContentsMargins(0, 0, 0, 0)
        g.setSpacing(8)
        for pair, link_key in ((("crop_top", "Top"), ("crop_bottom", "Bottom")), "crop_link_v"), \
                              ((("crop_left", "Left"), ("crop_right", "Right")), "crop_link_h"):
            row = QHBoxLayout()
            row.setSpacing(8)
            fields = []
            for key, label in pair:
                n = NumberField(self.engine.fx[key], 0, 4000, 2, 0, " px")
                n._fmt = lambda v, label=label: f"{label}   {v:.0f} px"
                n.set_value(self.engine.fx[key])
                fields.append((key, n))
                row.addWidget(n, 1)
            link = IconButton("link", "Link both sides", box=(30, 32), checkable=True)
            link.setChecked(self.engine.fx[link_key])

            def changed(v, key, others=fields, link=link):
                self._set(key, int(v))
                if link.isChecked():
                    for k2, n2 in others:
                        if k2 != key:
                            self._set(k2, int(v))
            for key, n in fields:
                n.valueChanged.connect(lambda v, key=key, ch=changed: ch(v, key))
                self._bind(key, n.set_value)
            link.toggled.connect(lambda on, k=link_key: self.engine.fx.__setitem__(k, on))
            row.addWidget(link)
            g.addLayout(row)
        f.widget(grid)

    def _colors(self, f):
        def color_toggle(label, on_key, color_key):
            row = QHBoxLayout()
            row.addWidget(Label(label, 14, "text_2"))
            row.addStretch(1)
            col = Dropdown([("#FF0000", "Red"), ("#00FF00", "Green"), ("#0000FF", "Blue"), ("#FFFF00", "Yellow"),
                            ("#FF00FF", "Fuchsia"), ("#00FFFF", "Aqua")], self.engine.fx[color_key], height=32)
            col.setFixedWidth(110)
            col.changed.connect(lambda v: self._set(color_key, v))
            self._bind(color_key, col.set_value)
            row.addWidget(col)
            t = Toggle()
            t.toggled.connect(lambda on: self._set(on_key, on))
            self._bind(on_key, lambda v, t=t: (t.blockSignals(True), t.setChecked(bool(v)), t.blockSignals(False), t.update()))
            row.addWidget(t)
            f.lay.addLayout(row)
        color_toggle("Color Extraction", "extract", "extract_color")
        color_toggle("Color Threshold", "thres", "thres_color")
        self.text_slider(f, "Saturation", "thres_saturation", 0, 255, 1, lambda v: f"{v:.0f}")
        self.text_slider(f, "Similarity", "thres_similarity", 0, 255, 1, lambda v: f"{v:.0f}")
        self.toggle_row(f, "Negate Colors", "negate")
        self.toggle_row(f, "Posterize", "posterize")
        self.toggle_row(f, "Gradient", "gradient")
        d = Dropdown(["Gradient", "Edge", "Hough"], self.engine.fx["gradient_mode"], height=44)
        d.changed.connect(lambda v: self._set("gradient_mode", v))
        self._bind("gradient_mode", d.set_value)
        f.widget(d)
        self.toggle_row(f, "Color", "gradient_color")
        self.toggle_row(f, "Cartoon", "cartoon")
        self.text_slider(f, "Sepia Intensity", "sepia", 0, 255, 5, lambda v: "Off" if v <= 0 else f"{v:.0f}", ticks=3)

    def _audio(self, f):
        f.header("Defaults")
        n = NumberField(self.engine.fx["audio_track_default"], 1, 32, 1, 0, "")
        n.valueChanged.connect(lambda v: (self._set("audio_track_default", int(v)), self._select_audio(int(v))))
        self._bind("audio_track_default", n.set_value)
        f.row("Audio Track", n)
        lang = Dropdown(["English", "Arabic", "Chinese", "French", "German", "Hindi", "Italian", "Japanese", "Korean",
                         "Portuguese", "Russian", "Spanish", "Turkish", "Urdu"], self.engine.fx["audio_lang"], height=32)
        lang.changed.connect(lambda v: (self._set("audio_lang", v), self._select_audio_lang(v)))
        self._bind("audio_lang", lang.set_value)
        f.row("Audio Language", lang)
        test = Button("Test", "primary", height=32, bold=False)
        test.setFixedWidth(68)
        test.clicked.connect(self.engine.test_stereo)
        f.row("Stereo Mode", test, width=68)
        d = Dropdown(STEREO_MODES, self.engine.fx["stereo_mode"], height=48)
        d.changed.connect(lambda v: self._set("stereo_mode", v))
        self._bind("stereo_mode", d.set_value)
        f.widget(d)
        f.line()
        f.header("Synchronization")
        self.num_slider(f, "Audio Track Delay", "audio_delay", -10, 10, 0.05, 3, " s", labels=["-10 s", "0 s", "+10 s"])
        f.line()
        f.header("Filters")
        f.lay.addWidget(Label("Equalizer", 14, "text_2"))
        eq_icons = {p: ("EQ", "EQ=" + p.lower().replace("full bass and treble", "full bass and treble"))
                    for p in prefs.EQ_PRESETS[1:]}
        eq = Dropdown(prefs.EQ_PRESETS, self.engine.fx["eq_preset"], height=48,
                      icon_for=lambda v: "EQ" if v in eq_icons else None)
        eq.icon_for = None
        eq.changed.connect(self._eq_preset)
        self._bind("eq_preset", eq.set_value)
        f.widget(eq)
        self._expandable(f, self._eq_bands)
        self.toggle_row(f, "2 Pass", "eq_2pass")
        f.lay.addSpacing(4)
        self.toggle_row(f, "Compressor", "compressor")
        self._expandable(f, self._compressor)
        self.toggle_row(f, "Spatializer", "spatializer")
        self._expandable(f, self._spatializer)
        self.toggle_row(f, "Stereo Widener", "widener")
        self._expandable(f, self._widener)
        self.text_slider(f, "Adjust Pitch", "pitch", -12, 12, 0.5,
                         lambda v: "0 semitones" if abs(v) < 0.01 else f"{v:+.1f} semitones", ticks=5)

    def _select_audio(self, n):
        tracks = [t for t, _ in self.engine.audio_tracks() if t != -1]
        if 0 < n <= len(tracks):
            self.engine.set_audio_track(tracks[n - 1])

    def _select_audio_lang(self, lang):
        for tid, name in self.engine.audio_tracks():
            if lang.lower() in name.lower() or name.lower().endswith(f"[{lang[:2].lower()}]"):
                self.engine.set_audio_track(tid)
                return

    def _eq_preset(self, name):
        if name != "None":
            self.engine.fx["eq_bands"] = self.engine.eq_preset_bands(name)
        self._set("eq_preset", name)
        self._fx_changed("eq_bands")

    def _eq_bands(self, f):
        names = ["Preamp"] + prefs.EQ_BANDS
        for i, name in enumerate(names):
            n = NumberField(self.engine.fx["eq_bands"][i], -20, 20, 0.5, 1, " dB")
            s = Slider(-20, 20, self.engine.fx["eq_bands"][i], 0.1, thumb="accent_2", centered=True, mark=0)

            def changed(v, i=i):
                bands = list(self.engine.fx["eq_bands"])
                bands[i] = round(v, 1)
                if self.engine.fx["eq_preset"] == "None":
                    self.engine.fx["eq_preset"] = "Flat"
                    self._fx_changed("eq_preset")
                self._set("eq_bands", bands)
            n.valueChanged.connect(changed)
            s.changed.connect(changed)
            self._bind("eq_bands", lambda v, n=n, s=s, i=i: (n.set_value(v[i]), s.set_value(v[i])))
            f.slider_row(name, s, value_widget=n)

    def _compressor(self, f):
        self.text_slider(f, "RMS / Peak", "comp_rms_peak", 0, 1, 0.1, lambda v: f"{v:.1f}")
        self.text_slider(f, "Attack", "comp_attack", 1.5, 400, 0.5, lambda v: f"{v:.1f} ms")
        self.text_slider(f, "Release", "comp_release", 2, 800, 1, lambda v: f"{v:.1f} ms")
        self.text_slider(f, "Threshold", "comp_threshold", -30, 0, 0.5, lambda v: f"{v:.1f} dB")
        self.text_slider(f, "Ratio", "comp_ratio", 1, 20, 0.5, lambda v: f"{v:.1f} : 1")
        self.text_slider(f, "Knee Radius", "comp_knee", 1, 10, 0.5, lambda v: f"{v:.1f} dB")
        self.text_slider(f, "Makeup Gain", "comp_makeup", 0, 24, 0.5, lambda v: f"{v:.1f} dB")

    def _spatializer(self, f):
        for key, label in (("spat_size", "Size"), ("spat_width", "Width"), ("spat_wet", "Wet"), ("spat_dry", "Dry"),
                           ("spat_damp", "Damp")):
            self.text_slider(f, label, key, 0, 11 if key == "spat_size" else 10, 0.1, lambda v: f"{v:.1f}", ticks=11)

    def _widener(self, f):
        self.text_slider(f, "Delay Time", "wide_delay", 1, 100, 1, lambda v: f"{v:.1f} ms")
        self.text_slider(f, "Feedback Gain", "wide_feedback", 0, 0.9, 0.05, lambda v: f"{v:.1f} %", ticks=10)
        self.text_slider(f, "Crossfeed", "wide_crossfeed", 0, 0.8, 0.05, lambda v: f"{v:.1f} %", ticks=9)
        self.text_slider(f, "Dry Mix", "wide_dry", 0, 1, 0.05, lambda v: f"{v:.1f} %", ticks=11)

    def _subtitles(self, f):
        f.header("Defaults")
        n = NumberField(self.engine.fx["sub_track_default"], 0, 32, 1, 0, "")
        n.valueChanged.connect(lambda v: (self._set("sub_track_default", int(v)), self._select_sub(int(v))))
        self._bind("sub_track_default", n.set_value)
        f.row("Subtitle Track", n)
        lang = Dropdown(["English", "Arabic", "Chinese", "French", "German", "Hindi", "Italian", "Japanese", "Korean",
                         "Portuguese", "Russian", "Spanish", "Turkish", "Urdu"], self.engine.fx["sub_lang"], height=32)
        lang.changed.connect(lambda v: (self._set("sub_lang", v), self._select_sub_lang(v)))
        self._bind("sub_lang", lang.set_value)
        f.row("Subtitle Language", lang)
        f.line()
        f.header("Synchronization")
        self.num_slider(f, "Subtitle Track Delay", "sub_delay", -10, 10, 0.05, 3, " s", labels=["-10 s", "0 s", "+10 s"])
        self.num_slider(f, "Subtitle Speed", "sub_fps", 0.5, 2.0, 0.01, 3, " fps", live=False)
        self.num_slider(f, "Subtitle Duration Factor", "sub_duration", 0, 5, 0.1, 3, "", live=False)
        f.line()
        f.header("Appearance")
        self.toggle_row(f, "Embedded Sub Format", "sub_embedded_format")
        self.sub_note = f.widget(Label("", 13, "text_3", wrap=True))
        self.sub_note.hide()
        for sig in (self.engine.tracksChanged, self.engine.mediaChanged, self.engine.fxChanged):
            sig.connect(self._update_sub_note)
        font = Dropdown(self.win.font_families(), self.engine.fx["sub_font"] or "", height=32, placeholder="System")
        font.changed.connect(lambda v: self._set("sub_font", v))
        self._bind("sub_font", font.set_value)
        f.row("Subtitle Font", font)
        for key, label in (("sub_color", "Text Color"), ("sub_outline_color", "Outline Color")):
            cf = ColorField(self.engine.fx[key])
            cf.changed.connect(lambda v, k=key: self._set(k, v))
            self._bind(key, cf.set_color)
            f.row(label, cf)
        self.number_row(f, "Subtitle Offset", "sub_offset", 0, 500, 5, 0, "px")
        seg = Segmented([("relative", "Relative"), ("pixels", "Pixels")], height=38)
        seg.changed.connect(lambda v: self._set("size_mode", v))
        self._bind("size_mode", seg.set_value)
        f.row("Size Values", seg, width=180)
        # relative sizes
        self.rel_box = QWidget()
        rb = Form(side=0)
        rb.lay.setContentsMargins(0, 0, 0, 0)
        sizes = ["Automatic", "Smaller", "Small", "Normal", "Large", "Larger"]
        s = Slider(0, len(sizes) - 1, sizes.index(self.engine.fx["sub_size"]) if self.engine.fx["sub_size"] in sizes else 0,
                   1, ticks=len(sizes), thumb="accent_2")
        vt = rb.slider_row("Font Size", s, value_text=self.engine.fx["sub_size"].replace("Automatic", "Auto"))
        s.released.connect(lambda v: self._set("sub_size", sizes[int(v)]))
        s.changed.connect(lambda v: vt.setText(sizes[int(v)].replace("Automatic", "Auto")))
        self._bind("sub_size", lambda v, s=s, vt=vt: (s.set_value(sizes.index(v) if v in sizes else 0),
                                                      vt.setText(v.replace("Automatic", "Auto"))))
        outs = ["None", "Thin", "Normal", "Thick"]
        s2 = Slider(0, 3, outs.index(self.engine.fx["outline"]), 1, ticks=4, thumb="accent_2")
        vt2 = rb.slider_row("Outline Thickness", s2, value_text=self.engine.fx["outline"])
        s2.released.connect(lambda v: self._set("outline", outs[int(v)]))
        s2.changed.connect(lambda v: vt2.setText(outs[int(v)]))
        self._bind("outline", lambda v, s2=s2, vt2=vt2: (s2.set_value(outs.index(v)), vt2.setText(v)))
        self.rel_box = rb
        f.widget(rb)
        # pixel sizes
        pb = Form(side=0)
        pb.lay.setContentsMargins(0, 0, 0, 0)
        self.number_row(pb, "Font Size", "sub_px", 8, 200, 1, 0, "px")
        self.number_row(pb, "Outline Thickness", "outline_px", 0, 3, 1, 0, "px")
        self.px_box = pb
        f.widget(pb)
        self._bind("size_mode", lambda v: (self.rel_box.setVisible(v != "pixels"), self.px_box.setVisible(v == "pixels")))
        self.text_slider(f, "Background Opacity", "bg_opacity", 0, 100, 5, lambda v: f"{v:.0f} %", ticks=11)

    def _select_sub(self, n):
        tracks = [t for t, _ in self.engine.spu_tracks() if t != -1]
        if n == 0:
            self.engine.set_spu(-1)
        elif 0 < n <= len(tracks):
            self.engine.set_spu(tracks[n - 1])

    def _select_sub_lang(self, lang):
        for tid, name in self.engine.spu_tracks():
            if tid != -1 and lang.lower() in name.lower():
                self.engine.set_spu(tid)
                return

    # footer: presets + advanced settings ---------------------------------------
    def _footer(self):
        w = QWidget()
        v = QVBoxLayout(w)
        v.setContentsMargins(20, 10, 20, 14)
        v.setSpacing(10)
        d = PresetDropdown(self)
        v.addWidget(d)
        self._preset_dropdowns = getattr(self, "_preset_dropdowns", []) + [d]
        b = Button("Open Advanced Settings", "dark", height=32, icon="settings", bold=False)
        b.clicked.connect(lambda: self.win.open_settings(self.current_tab()))
        v.addWidget(b)
        return w

    def _update_presets(self):
        for d in getattr(self, "_preset_dropdowns", []):
            d.update()


class PresetDropdown(QWidget):
    """The "Default Preset" dropdown. Opens upwards: New Preset, saved presets, Default Preset."""

    def __init__(self, panel):
        super().__init__()
        self.panel = panel
        self.engine = panel.engine
        self.store = panel.store
        self.setFixedHeight(42)
        self.setCursor(Qt.PointingHandCursor)

    def current(self):
        return getattr(self.engine, "current_preset", "Default Preset")

    def mousePressEvent(self, e):
        items = [MenuItem("New Preset", self._new, icon="add", icon_variant="")]
        for name in sorted(self.store.presets()):
            items.append(MenuItem(name, lambda n=name: self._apply(n), icon="more", icon_variant="",
                                  checked=(name == self.current()),
                                  submenu=lambda n=name: [
                                      MenuItem("Apply", lambda: self._apply(n)),
                                      MenuItem("Save current settings here", lambda: self._overwrite(n)),
                                      MenuItem("Rename…", lambda: self._rename(n)),
                                      MenuItem("Delete", lambda: self._delete(n))]))
        items.append(MenuItem("Default Preset", lambda: self._apply("Default Preset"),
                              checked=(self.current() == "Default Preset")))
        m = PopupMenu(items, self, width=self.width() - 4, row=40)
        m.popup_above(self, gap=4)

    def _folder(self):
        cur = self.engine.current
        return os.path.dirname(cur) if cur and not is_url(cur) else None

    def _apply(self, name):
        self.engine.apply_preset(name)
        self.engine.current_preset = name
        folder = self._folder()
        if folder:
            self.store.set_folder_preset(folder, None if name == "Default Preset" else name)
        self.update()
        self.panel.win.notify(f"Preset: {name}")

    def _new(self):
        base = os.path.basename(self._folder() or "") or "Custom"
        name, ok = QInputDialog.getText(self.panel.win, "New Preset", "Save the current video, audio and subtitle "
                                        "settings as:", text=f"{base} Preset")
        if ok and name.strip():
            name = name.strip()
            self.store.save_preset(name, self.engine.preset_values())
            self._apply(name)

    def _overwrite(self, name):
        self.store.save_preset(name, self.engine.preset_values())
        self.panel.win.notify(f"Saved to {name}")

    def _rename(self, name):
        new, ok = QInputDialog.getText(self.panel.win, "Rename Preset", "Name:", text=name)
        if ok and new.strip() and new.strip() != name:
            vals = self.store.presets().get(name)
            self.store.remove_preset(name)
            self.store.save_preset(new.strip(), vals)
            if self.current() == name:
                self.engine.current_preset = new.strip()

    def _delete(self, name):
        self.store.remove_preset(name)
        if self.current() == name:
            self.engine.current_preset = "Default Preset"
        self.update()

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setPen(Qt.NoPen)
        p.setBrush(c("field"))
        p.drawRoundedRect(QRectF(self.rect()), 6, 6)
        p.setBrush(c("item"))
        p.drawRoundedRect(QRectF(self.rect()).adjusted(2, 2, -2, -2), 4, 4)
        p.setFont(theme.font(14))
        p.setPen(c("text"))
        p.drawText(QRectF(16, 0, self.width() - 60, self.height()), Qt.AlignVCenter, self.current())
        p.drawPixmap(self.width() - 16 - 24, (self.height() - 24) // 2, icons.pixmap("chevron-up", 24))


# --- queue panel ----------------------------------------------------------------------------
ROLE_KIND = Qt.UserRole + 1
ROLE_INDEX = Qt.UserRole + 2


class QueueDelegate(QStyledItemDelegate):
    def __init__(self, panel):
        super().__init__(panel)
        self.panel = panel

    def sizeHint(self, option, index):
        return QSize(option.rect.width(), 32 if index.data(ROLE_KIND) == "item" else 28)

    def paint(self, p, option, index):
        e = self.panel.engine
        r = QRectF(option.rect)
        p.save()
        p.setRenderHint(QPainter.Antialiasing)
        f = theme.font(14)
        p.setFont(f)
        if index.data(ROLE_KIND) == "header":
            p.setFont(theme.font(12))
            p.setPen(c("text_2"))
            p.drawText(r.adjusted(4, 0, 0, 0), Qt.AlignVCenter | Qt.AlignLeft,
                       elide(index.data(Qt.DisplayRole), theme.font(12), r.width() - 8))
            p.restore()
            return
        i = index.data(ROLE_INDEX)
        item = e.items[i] if 0 <= i < len(e.items) else ""
        current = i == e.index
        if option.state & 0x8000 or option.state & 0x2000:     # selected / mouse over
            p.setPen(Qt.NoPen)
            p.setBrush(c("hover"))
            p.drawRoundedRect(r, 6, 6)
        icon = icons.pixmap("playback", 24, None, "active") if current else icons.pixmap("move", 24)
        p.drawPixmap(QRectF(r.left() + 4, r.center().y() - 12, 24, 24).toRect(), icon)
        length = e.length_of(item) or (self.panel.win.media.info(item) or {}).get("duration", 0)
        dur = fmt_short(length) if length else ""
        fm = QFontMetrics(f)
        dw = fm.horizontalAdvance(dur) if dur else 0
        p.setPen(c("text_2"))
        p.drawText(r.adjusted(0, 0, -4, 0), Qt.AlignVCenter | Qt.AlignRight, dur)
        p.setPen(c("accent") if current else c("text"))
        name = e.names.get(item) or display_name(item)
        p.drawText(QRectF(r.left() + 42, r.top(), r.width() - 42 - dw - 14, r.height()), Qt.AlignVCenter,
                   elide(name, f, r.width() - 42 - dw - 14))
        p.restore()


class QueueList(QListWidget):
    def __init__(self, panel):
        super().__init__()
        self.panel = panel
        self.setItemDelegate(QueueDelegate(panel))
        self.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.setDragDropMode(QAbstractItemView.DragDrop)
        self.setDefaultDropAction(Qt.MoveAction)
        self.setAcceptDrops(True)
        self.setMouseTracking(True)
        self.setSpacing(0)
        self.setVerticalScrollMode(QAbstractItemView.ScrollPerPixel)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.viewport().setAutoFillBackground(False)

    def event(self, e):
        if e.type() == QEvent.ShortcutOverride and wants_list_key(e):
            e.accept()
            return True
        return super().event(e)

    def dragEnterEvent(self, e):
        if e.source() is self or e.mimeData().hasUrls() or e.mimeData().hasFormat("application/x-vlcr-items"):
            e.acceptProposedAction()
        else:
            super().dragEnterEvent(e)

    def dragMoveEvent(self, e):
        if e.source() is self:
            super().dragMoveEvent(e)
        else:
            e.acceptProposedAction()

    def dropEvent(self, e):
        md = e.mimeData()
        if e.source() is self:
            src_rows = sorted(i.data(ROLE_INDEX) for i in self.selectedItems() if i.data(ROLE_KIND) == "item")
            target = self.itemAt(e.pos())
            dst = target.data(ROLE_INDEX) if target is not None and target.data(ROLE_KIND) == "item" else \
                len(self.panel.engine.items) - 1
            if target is not None:
                rect = self.visualItemRect(target)
                if e.pos().y() > rect.center().y() and dst < len(self.panel.engine.items) - 1:
                    dst = dst + (0 if src_rows and src_rows[0] < dst else 1)
            e.setDropAction(Qt.IgnoreAction)
            e.accept()
            self.panel.move_rows(src_rows, dst)
            return
        items = []
        if md.hasFormat("application/x-vlcr-items"):
            import json
            items = json.loads(bytes(md.data("application/x-vlcr-items")).decode())
        elif md.hasUrls():
            items = [u.toLocalFile() or u.toString() for u in md.urls()]
        if items:
            n = self.panel.engine.enqueue(items)
            if n:
                self.panel.win.notify(f"Added {n} to the queue" if n > 1 else "Added to the queue")
        e.acceptProposedAction()

    def keyPressEvent(self, e):
        if e.key() in (Qt.Key_Delete, Qt.Key_Backspace):
            self.panel.remove_selected()
        elif e.key() in (Qt.Key_Return, Qt.Key_Enter):
            it = self.currentItem()
            if it and it.data(ROLE_KIND) == "item":
                self.panel.engine.play_index(it.data(ROLE_INDEX))
        else:
            super().keyPressEvent(e)


class MarkList(QWidget):
    """Bookmark / chapter rows: name left, time right (chapters numbered in accent)."""
    activated = pyqtSignal(int)
    renamed = pyqtSignal(int)
    menu = pyqtSignal(int, QPoint)

    ROW = 44

    def __init__(self, numbered=False):
        super().__init__()
        self.rows = []            # (key, name, time text)
        self.numbered = numbered
        self.hover = -1
        self.selected = -1
        self.setMouseTracking(True)
        self.setCursor(Qt.PointingHandCursor)

    def set_rows(self, rows):
        self.rows = rows
        self.selected = -1
        self.setFixedHeight(max(1, len(rows)) * self.ROW)
        self.update()

    def _at(self, y):
        i = y // self.ROW
        return i if 0 <= i < len(self.rows) else -1

    def mouseMoveEvent(self, e):
        h = self._at(e.y())
        if h != self.hover:
            self.hover = h
            self.update()

    def leaveEvent(self, e):
        self.hover = -1
        self.update()

    def mousePressEvent(self, e):
        i = self._at(e.y())
        self.selected = i
        self.update()
        if i >= 0 and e.button() == Qt.RightButton:
            self.menu.emit(i, e.globalPos())

    def mouseReleaseEvent(self, e):
        i = self._at(e.y())
        if i >= 0 and e.button() == Qt.LeftButton:
            self.activated.emit(i)

    def mouseDoubleClickEvent(self, e):
        i = self._at(e.y())
        if i >= 0:
            self.renamed.emit(i)

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        f = theme.font(14)
        fm = QFontMetrics(f)
        p.setFont(f)
        for i, (key, name, t) in enumerate(self.rows):
            r = QRectF(0, i * self.ROW, self.width(), self.ROW)
            if i in (self.hover, self.selected):
                p.setPen(Qt.NoPen)
                p.setBrush(c("hover"))
                p.drawRoundedRect(r, 6, 6)
            x = r.left() + 8
            if self.numbered:
                p.setPen(c("accent"))
                p.drawText(QRectF(x + 8, r.top(), 30, r.height()), Qt.AlignVCenter, str(i + 1))
                x += 48
            tw = fm.horizontalAdvance(t)
            p.setPen(c("text_2"))
            p.drawText(r.adjusted(0, 0, -8, 0), Qt.AlignVCenter | Qt.AlignRight, t)
            p.setPen(c("text"))
            p.drawText(QRectF(x, r.top(), r.right() - x - tw - 20, r.height()), Qt.AlignVCenter,
                       elide(name, f, r.right() - x - tw - 20))


class QueuePanel(Panel):
    def __init__(self, window):
        super().__init__(window, [("queue", "Queue", "queue"), ("bookmarks", "Bookmarks", "bookmarks"),
                                  ("chapters", "Chapters", "chapters")])
        # queue
        q = QWidget()
        qv = QVBoxLayout(q)
        qv.setContentsMargins(20, 0, 20, 12)
        qv.setSpacing(0)
        head = QHBoxLayout()
        head.setContentsMargins(8, 20, 0, 12)
        head.setSpacing(12)
        self.count = Label("", 14, "text_2")
        head.addWidget(self.count)
        head.addStretch(1)
        self.sort_btn = IconButton("sort", "Sort the queue", box=(24, 28))
        self.sort_btn.clicked.connect(self._sort_menu)
        self.repeat_btn = IconButton("repeat", "Repeat", box=(30, 28))
        self.repeat_btn.clicked.connect(self.engine.cycle_repeat)
        self.shuffle_btn = IconButton("shuffle", "Shuffle", box=(30, 28))
        self.shuffle_btn.clicked.connect(lambda: self.engine.toggle_shuffle())
        rem = IconButton("remove", "Remove the selected items (Delete)", box=(24, 28))
        rem.clicked.connect(self.remove_selected)
        add = IconButton("add", "Add to the queue", box=(24, 28))
        add.clicked.connect(lambda: self._add_menu(add))
        for b in (self.sort_btn, self.repeat_btn, self.shuffle_btn, rem, add):
            head.addWidget(b)
        qv.addLayout(head)
        self.list = QueueList(self)
        self.list.itemDoubleClicked.connect(self._activate)
        self.list.setContextMenuPolicy(Qt.CustomContextMenu)
        self.list.customContextMenuRequested.connect(self._context)
        qv.addWidget(self.list, 1)
        self.empty = Label("The queue is empty. Drag files here from the library or your file manager.", 13,
                           "text_3", wrap=True)
        self.empty.setAlignment(Qt.AlignCenter)
        qv.addWidget(self.empty)
        self.add_page("queue", q)
        q.refresh = self.rebuild
        # bookmarks
        b = QWidget()
        bv = QVBoxLayout(b)
        bv.setContentsMargins(20, 0, 20, 12)
        bh = QHBoxLayout()
        bh.setContentsMargins(8, 20, 0, 12)
        self.bcount = Label("", 14, "text_2")
        bh.addWidget(self.bcount)
        bh.addStretch(1)
        brem = IconButton("remove", "Remove the selected bookmark", box=(24, 28))
        brem.clicked.connect(self._remove_bookmark)
        badd = IconButton("add", "Add a bookmark here (Ctrl+B)", box=(24, 28))
        badd.clicked.connect(self.win.add_bookmark)
        bh.addSpacing(12)
        bh.addWidget(brem)
        bh.addSpacing(12)
        bh.addWidget(badd)
        bv.addLayout(bh)
        self.blist = MarkList()
        self.blist.activated.connect(self._goto_bookmark)
        self.blist.renamed.connect(self._rename_bookmark)
        bv.addWidget(scrolled(self.blist), 1)
        self.add_page("bookmarks", b)
        b.refresh = self.rebuild_bookmarks
        # chapters
        ch = QWidget()
        cv = QVBoxLayout(ch)
        cv.setContentsMargins(20, 16, 20, 12)
        self.clist = MarkList(numbered=True)
        self.clist.activated.connect(self.engine.set_chapter)
        cv.addWidget(scrolled(self.clist), 1)
        self.cempty = Label("This file has no chapters.", 13, "text_3")
        cv.addWidget(self.cempty)
        self.add_page("chapters", ch)
        ch.refresh = self.rebuild_chapters

        self.engine.playlistChanged.connect(self.rebuild)
        self.engine.mediaChanged.connect(self.rebuild)
        self.engine.mediaChanged.connect(lambda: QTimer.singleShot(1500, self.rebuild_chapters))
        self.engine.metaChanged.connect(self.list.viewport().update)
        self.engine.modesChanged.connect(self._modes)
        self.store.libraryChanged.connect(lambda w: w == "bookmarks" and self.rebuild_bookmarks())
        self.engine.mediaChanged.connect(self.rebuild_bookmarks)
        self.win.media.infoReady.connect(lambda _p: self.list.viewport().update())
        self.store.settingChanged.connect(lambda k, v: k == "queue_tree" and self.rebuild())
        self.rebuild()
        self._modes()

    # queue ----------------------------------------------------------------------
    def rebuild(self):
        e = self.engine
        self.list.clear()
        tree = self.store.get("queue_tree")
        folders = {os.path.dirname(i) for i in e.items if not is_url(i)}
        last_folder = None
        for i, item in enumerate(e.items):
            if tree and len(folders) > 1:
                folder = os.path.dirname(item) if not is_url(item) else "Network"
                if folder != last_folder:
                    h = QListWidgetItem(os.path.basename(folder) or folder)
                    h.setData(ROLE_KIND, "header")
                    h.setFlags(Qt.ItemIsEnabled)
                    self.list.addItem(h)
                    last_folder = folder
            it = QListWidgetItem(e.names.get(item) or display_name(item))
            it.setData(ROLE_KIND, "item")
            it.setData(ROLE_INDEX, i)
            it.setFlags(Qt.ItemIsEnabled | Qt.ItemIsSelectable | Qt.ItemIsDragEnabled)
            self.list.addItem(it)
        total = e.queue_length()
        n = len(e.items)
        self.count.setText(f"{n} item{'s' if n != 1 else ''}" + (f" - {fmt_total(total)}" if total else ""))
        self.empty.setVisible(n == 0)
        self.list.setVisible(n > 0)

    def _modes(self):
        e = self.engine
        self.repeat_btn.set_icon("repeat")
        self.repeat_btn.set_active(e.repeat != "off")
        self.repeat_btn.setToolTip({"off": "Repeat: off", "all": "Repeat: all", "one": "Repeat: this item"}[e.repeat])
        self.shuffle_btn.set_active(e.shuffle)

    def move_rows(self, rows, dst):
        e = self.engine
        if not rows:
            return
        items = [e.items[r] for r in rows]
        cur = e.current
        keep = [it for k, it in enumerate(e.items) if k not in rows]
        anchor = e.items[dst] if 0 <= dst < len(e.items) and dst not in rows else None
        pos = keep.index(anchor) if anchor in keep else len(keep)
        if anchor is not None and rows[0] < dst:
            pos += 1
        e.items = keep[:pos] + items + keep[pos:]
        e.index = e.items.index(cur) if cur in e.items else -1
        e.playlistChanged.emit()

    def remove_selected(self):
        rows = [i.data(ROLE_INDEX) for i in self.list.selectedItems() if i.data(ROLE_KIND) == "item"]
        if rows:
            self.engine.remove(rows)

    def _activate(self, it):
        if it.data(ROLE_KIND) == "item":
            self.engine.play_index(it.data(ROLE_INDEX))

    def _sort_menu(self):
        items = [MenuItem("Name (A-Z)", lambda: self.engine.sort_queue("name")),
                 MenuItem("Name (Z-A)", lambda: self.engine.sort_queue("name", True)),
                 MenuItem("Duration", lambda: self.engine.sort_queue("length")),
                 MenuItem("Folder", lambda: self.engine.sort_queue("folder")),
                 sep(),
                 MenuItem("Clear the queue", self.engine.clear)]
        PopupMenu(items, self).popup_below(self.sort_btn)

    def _add_menu(self, btn):
        w = self.win
        items = [MenuItem("Add File", lambda: w.open_dialog("file", enqueue=True), icon="add", icon_variant=""),
                 MenuItem("Add Folder", lambda: w.open_dialog("folder", enqueue=True), icon="new folder", icon_variant="two"),
                 MenuItem("Add Network Stream", lambda: w.open_network(enqueue=True), icon="streaming", icon_variant="two")]
        PopupMenu(items, self).popup_below(btn, align_right=True)

    def _context(self, pos):
        it = self.list.itemAt(pos)
        if not it or it.data(ROLE_KIND) != "item":
            return
        i = it.data(ROLE_INDEX)
        item = self.engine.items[i]
        w = self.win
        items = [MenuItem("Play", lambda: self.engine.play_index(i), icon="playback", icon_variant="two"),
                 MenuItem("Play Next", lambda: self.move_rows([i], self.engine.index + (0 if i < self.engine.index else 1))),
                 MenuItem("Add to Playlist", icon="playlist", submenu=lambda: w.playlist_submenu([item])),
                 MenuItem("Show in File Explorer", lambda: w.show_in_folder(item), icon="folder",
                          enabled=not is_url(item)),
                 MenuItem("Properties & Metadata", lambda: w.show_properties(item), icon="info"),
                 sep(),
                 MenuItem("Remove", lambda: self.engine.remove([i]), icon="remove", icon_variant="")]
        PopupMenu(items, self).popup(self.list.viewport().mapToGlobal(pos))

    # bookmarks ------------------------------------------------------------------------
    def rebuild_bookmarks(self):
        item = self.engine.current
        marks = self.store.bookmarks(item) if item else []
        self._marks = marks
        rows = [(b["ms"], b["name"] or f"Bookmark {n + 1}", fmt_ms(b["ms"])) for n, b in enumerate(marks)]
        self.blist.set_rows(rows)
        self.bcount.setText(f"{len(marks)} Bookmark{'s' if len(marks) != 1 else ''}")

    def _goto_bookmark(self, i):
        self.engine.seek_to(self._marks[i]["ms"])

    def _rename_bookmark(self, i):
        b = self._marks[i]
        name, ok = QInputDialog.getText(self.win, "Rename Bookmark", "Name:", text=b["name"])
        if ok:
            self.store.rename_bookmark(self.engine.current, b["ms"], name.strip())

    def _remove_bookmark(self):
        i = self.blist.selected
        if 0 <= i < len(self._marks):
            self.store.remove_bookmark(self.engine.current, self._marks[i]["ms"])

    # chapters ---------------------------------------------------------------------------
    def rebuild_chapters(self):
        chs = self.engine.chapters() if self.engine.current else []
        self.clist.set_rows([(i, name, fmt_short(t)) for i, name, t in chs])
        self.cempty.setVisible(not chs)
