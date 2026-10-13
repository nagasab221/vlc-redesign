"""The main window: a frameless shell with the redesign's title bar, the player (video or
music view + control bar, with the library sidebar floating over it) and the full library
with its mini player. It also owns the floating panels, overlays, menus and shortcuts,
and is the `actions` object every bar element talks to."""

import json
import os
import re
import shutil
import subprocess
import webbrowser

from PyQt5.QtCore import QByteArray, QEvent, QObject, QPoint, QRect, QSize, Qt, QTimer, QUrl, pyqtSignal
from PyQt5.QtGui import QColor, QCursor, QIcon, QKeySequence, QPainter, QPixmap
from PyQt5.QtWidgets import (QApplication, QFileDialog, QHBoxLayout, QInputDialog, QMenu, QShortcut,
                             QStackedWidget, QSystemTrayIcon, QVBoxLayout, QWidget)

from . import APP_NAME, icons, prefs, theme, wm
from .actions import ACTION, ACTIONS, WINDOW_WIDE
from .controls import SeekPreview, TitleBar
from .dialogs import (AboutDialog, AddSubtitlesDialog, CaptureDialog, ConvertDialog, DiscDialog, MessageDialog,
                      NetworkDialog, PlaylistDialog, PropertiesDialog, ShortcutsDialog, StreamDialog)
from .engine import (AUDIO_EXT, MEDIA_EXT, SUB_EXT, VIDEO_EXT, display_name, ext, fmt_time, is_audio, is_url,
                     media_in_dir)
from .library import CompactLibrary, FullLibrary, Library
from .media import MediaService, find_subtitles
from .miniplayer import MiniPlayer, PipWindow
from .panels import QueuePanel, SettingsPanel, TracksPanel
from .player import OverlayWindow, PlayerArea, StatsBox
from .theme import c
from .widgets import OSD, MenuItem, PopupMenu, Toast, sep
from .filedialog import FileDialog

FRAME = 5


class MainWindow(QWidget):
    def __init__(self, engine, store):
        super().__init__(None, Qt.Window | Qt.FramelessWindowHint)
        self.engine = engine
        self.store = store
        self.media = MediaService()
        self.setWindowTitle(APP_NAME)
        self.setWindowIcon(QIcon(icons.pixmap("vlc", 128, dpr=1.0)))
        self.setAcceptDrops(True)
        self.setMouseTracking(True)
        self.setMinimumSize(560, 360)
        self.mode = "player"
        self.hide_ui = False
        self.pip = None
        self.panels = {}
        self.open_panel = None
        self._fs_bars = {}
        self._scrub_last = 0
        self._was_max = False
        self._cursor_hidden = False
        self._last_move = 0
        self.library = Library(self)

        root = QVBoxLayout(self)
        root.setContentsMargins(FRAME, FRAME, FRAME, FRAME)
        root.setSpacing(0)
        self.titlebar = TitleBar(self)
        root.addWidget(self.titlebar)
        self.pages = QStackedWidget()
        root.addWidget(self.pages, 1)

        # player page: the player area (the library sidebar floats over it)
        self.player_page = QWidget()
        ph = QHBoxLayout(self.player_page)
        ph.setContentsMargins(0, 0, 0, 0)
        ph.setSpacing(0)
        # The library sidebar floats over the left of the video (like the right-hand panels),
        # so opening it never resizes the video.
        self.compact = CompactLibrary(self.library)
        self.compact.setParent(self, Qt.Tool | Qt.FramelessWindowHint | Qt.NoDropShadowWindowHint)
        self.compact.setAttribute(Qt.WA_TranslucentBackground)
        self.compact.hide()
        self.sidebar_open = False
        self.player = PlayerArea(self)
        ph.addWidget(self.player, 1)
        self.pages.addWidget(self.player_page)
        # library page
        self.library_page = QWidget()
        lv = QVBoxLayout(self.library_page)
        lv.setContentsMargins(0, 0, 0, 0)
        self.full = FullLibrary(self.library)
        lv.addWidget(self.full)
        self.pages.addWidget(self.library_page)
        self.mini = MiniPlayer(self, self.library_page)
        self.mini.hide()

        self.video = self.player.video
        self.engine.set_window(int(self.video.winId()))

        self.toast = Toast(self)
        self.toast.anchor = self._toast_anchor
        self.osd = OSD(self)
        self.osd.anchor = self._osd_anchor
        self.preview = SeekPreview()
        self.stats = StatsBox(self)
        self._stats_timer = QTimer(self, interval=1000, timeout=self._update_stats)
        self._overlay_timer = QTimer(self, interval=80, timeout=self._overlay_tick)
        self._hide_timer = QTimer(self, singleShot=True, timeout=self._hide_overlays)
        self._cursor_timer = QTimer(self, singleShot=True, interval=2200, timeout=self._hide_cursor)

        self._wire_surfaces()
        self._wire_engine()
        self.store.layoutChanged.connect(self.rebuild_bars)
        self.store.settingChanged.connect(self._on_setting)
        self.store.libraryChanged.connect(self._on_library)
        self.media.waveReady.connect(lambda p: p == self.engine.current and self._update_seek_modes())
        self.media.cuesReady.connect(lambda _k: self._update_seek_modes())
        self.media.previewReady.connect(self._preview_ready)
        self.media.thumbReady.connect(lambda p: p == self.engine.current and self._update_title())
        self.library.locationChanged.connect(self._on_location)

        self.rebuild_bars()
        self._restore_geometry()
        self.apply_shortcuts()
        self._setup_tray()
        self.pulse = None
        self._apply_window_settings()
        self.show_library_window() if not engine.items else self.show_player()
        self._update_title()
        self._sync_all()
        if store.get("compact_start"):
            QTimer.singleShot(0, self.toggle_hide_ui)

    # ------------------------------------------------------------------ wiring
    def _wire_surfaces(self):
        for surf in (self.player.video, self.player.music, self.player.blank):
            surf.clicked.connect(self._surface_click)
            surf.doubleClicked.connect(self._surface_double)
            surf.wheeled.connect(self._surface_wheel)
            surf.middleClicked.connect(self._surface_middle)
            surf.moved.connect(self._surface_moved)
            surf.contextMenu.connect(self.open_context_menu)

    def _wire_engine(self):
        e = self.engine
        e.stateChanged.connect(self._on_state)
        e.tick.connect(self._on_tick)
        e.mediaChanged.connect(self._on_media)
        e.metaChanged.connect(self._on_meta)
        e.videoChanged.connect(self._on_video)
        e.volumeChanged.connect(self._on_volume)
        e.modesChanged.connect(self._sync_all)
        e.message.connect(self._on_message)
        e.error.connect(self.error)
        e.resumed.connect(self._on_resumed)
        e.fxChanged.connect(self._on_fx)
        e.rebuilt.connect(self._on_rebuilt)
        e.recordingChanged.connect(lambda on: self._sync_all())
        e.rateChanged.connect(lambda r: self._sync_all())
        e.playlistChanged.connect(self._sync_all)

    # ------------------------------------------------------------------ bars
    def bars(self):
        out = [self.titlebar, self.player.bar]
        out += [w.bar for w in self._fs_bars.values()]
        return out

    def rebuild_bars(self):
        layout = self.store.layout()
        if self.mode == "library":
            lib_layout = {"title_left": ["menu"], "title_center": ["title"],
                          "title_right": [e for e in layout.get("title_right", []) if e in
                                          ("minimize", "maximize", "fullscreen", "close")] or
                                         ["minimize", "maximize", "fullscreen", "close"]}
            self.titlebar.build(lib_layout)
        else:
            self.titlebar.build(layout)
        rows = self._rows_for_media(layout["rows"])
        self.player.bar.build(rows)
        for kind, ow in self._fs_bars.items():
            if kind == "top":
                ow.bar.build(layout)
            else:
                ow.bar.build(rows)
            ow.adjustSize()
        self._sync_all()
        self._update_title()
        self._update_seek_modes()
        self._layout_bars_visibility()

    def _rows_for_media(self, rows):
        """Music: back/forward 15 s around play, no subtitles button (design's music bar)."""
        cur = self.engine.current
        if not cur or not is_audio(cur) or self.engine.has_video:
            return rows
        out = []
        for row in rows:
            row = [x for x in row if x != "subtitles"]
            if "play" in row and not any(x in row for x in ("back10", "fwd10", "back15", "fwd15")):
                i = row.index("play")
                row = row[:i] + ["back15", "play", "fwd15"] + row[i + 1:]
            out.append(row)
        return out

    def _elements(self, eid):
        out = []
        for b in self.bars():
            try:
                out.extend(b.elements(eid))
            except RuntimeError:
                pass
        return out

    def _sync_all(self):
        e = self.engine
        playing = e.is_playing()
        for w in self._elements("play"):
            w.set_playing(playing)
        for w in self._elements("volume"):
            w.set_volume(e.volume, e.muted, e.max_volume())
        cur = e.current
        fav = bool(cur and self.store.is_favorite(cur))
        for w in self._elements("favorite"):
            w.set_active(fav)
        for w in self._elements("repeat"):
            w.set_active(e.repeat != "off")
            w.setToolTip({"off": "Repeat: off", "all": "Repeat: all", "one": "Repeat: this item"}[e.repeat])
        for w in self._elements("shuffle"):
            w.set_active(e.shuffle)
        for w in self._elements("record"):
            w.set_active(e.is_recording())
        for w in self._elements("speed"):
            w.set_rate(e.fx["speed"])
        for w in self._elements("sidebar"):
            w.set_active(self.sidebar_open)
        for w in self._elements("pip"):
            w.set_active(self.pip is not None)
        for w in self._elements("fullscreen"):
            w.set_icon("fullscreen", "active" if self.isFullScreen() else "")
        for w in self._elements("maximize"):
            w.setToolTip("Restore" if self.isMaximized() else "Maximize")
        panel_state = {"audio": ("tracks", "audio"), "subtitles": ("tracks", "subtitles"), "settings": ("settings", None),
                       "queue": ("queue", None), "chapters": ("queue", "chapters"), "bookmarks": ("queue", "bookmarks")}
        for eid, (kind, tab) in panel_state.items():
            p = self.panels.get(kind)
            on = bool(p and p.isVisible() and (tab is None or p.current_tab() == tab))
            for w in self._elements(eid):
                w.set_active(on)
        folder = os.path.basename(os.path.dirname(cur)) if cur and not is_url(cur) else ""
        for w in self._elements("folder"):
            w.set_text(folder or ("Videos" if not cur else "Network"), "folder")
        for w in self._elements("folder_name"):
            w.set_text(folder)
        for w in self._elements("file_name"):
            w.set_text(display_name(cur) if cur else "")
        for w in self._elements("artist_name"):
            w.set_text(e.artist())
        pl = next((p for p in self.store.playlists() if cur in p["items"]), None) if cur else None
        for w in self._elements("playlist_name"):
            w.set_text(pl["name"] if pl else "")
        for w in self._elements("thumbnail"):
            pm = self.media.thumb(cur, 50, 28, self.devicePixelRatioF()) if cur else None
            w.set_pixmap(pm)
        t, length = e.time()
        self._update_time(t, length)
        self._update_title()

    def _update_time(self, t, length):
        for w in self._elements("current_time"):
            w.set_time(t, length)
        for w in self._elements("duration"):
            w.set_time(t, length)
        if not self.engine._last or length <= 0:
            frac = 0
        else:
            frac = t / length
        for w in self._elements("seek"):
            w.seek.set_value(frac)
            w.seek.length = length

    def _update_title(self):
        e = self.engine
        show = self.store.get("title_filename")
        text = e.file_title() if (e.current and show) else "VLC Media Player"
        for w in self._elements("title"):
            w.set_text(text)
        self.setWindowTitle(text if e.current else APP_NAME)
        if self.mode == "library" and not e.current:
            for w in self._elements("title"):
                w.set_text("VLC Media Player")

    def _update_seek_modes(self):
        e = self.engine
        cur = e.current
        peaks = None
        cues = None
        if cur and not is_url(cur):
            wave_on = e.fx["waveform"] or (is_audio(cur) and not e.has_video)
            if wave_on:
                peaks = self.media.waveform(cur)
            if e.fx["sub_timeline"] and e.has_video:
                ext_subs = self.store.external_subs(cur) or find_subtitles(cur)
                cues = self.media.cues(cur, ext_subs[0] if ext_subs else None)
        shift = int(e.fx["sub_delay"] * 1000)
        for w in self._elements("seek"):
            w.seek.peaks = peaks
            w.seek.cues = [(a + shift, b + shift) for a, b in cues] if cues else None
            w.seek.length = e.time()[1]
            w.set_timeline(bool(e.fx["sub_timeline"] and e.has_video))
            marks = []
            length = e.time()[1]
            if cur and length > 0:
                marks = [b["ms"] / length for b in self.store.bookmarks(cur)]
                chs = getattr(self, "_chapters", [])
                w.seek.chapters = [t / length for _, _, t in chs if t > 0]
            w.seek.marks = marks
            w.seek.update()
        if self.player.music.isVisible() or e.fx["visualizer"] == "Waveform":
            t, length = e.time()
            self.player.music.set_wave(self.media.waveform(cur) if cur and not is_url(cur) else None,
                                       t / length if length else 0, e.fx["visualizer"] == "Waveform")

    # ------------------------------------------------------------------ engine events
    def _on_state(self, state):
        self._sync_all()
        if state in ("stopped", "error", "opening", "playing"):
            self._update_surface()
        if self.store.get("icon_change"):
            self._update_icon(state)
        if state in ("stopped", "ended") and getattr(self.engine, "finished_all", False):
            self.engine.finished_all = False
            what = self.store.get("when_finished")
            if what == "Quit":
                QTimer.singleShot(300, self.quit)
            elif what == "Show the library":
                self.show_library_window()

    def _on_tick(self, t, length):
        self._update_time(t, length)
        if self.engine.fx["visualizer"] == "Waveform" and self.player.music.isVisible():
            self.player.music.set_wave(self.player.music.peaks, t / length if length else 0, True)

    def _on_media(self):
        e = self.engine
        cur = e.current
        self._chapters = []
        self.rebuild_bars() if cur and is_audio(cur) != getattr(self, "_bar_audio", None) else self._sync_all()
        self._bar_audio = bool(cur and is_audio(cur))
        self._update_surface()
        self._update_seek_modes()
        self._update_title()
        if not cur:
            return
        if self.mode != "library" and e.has_video and self.store.get("start_fullscreen") and not self.isFullScreen():
            QTimer.singleShot(300, self.enter_fullscreen)
        raise_on = self.store.get("raise_on_play")
        kind = "Audio" if is_audio(cur) else "Video"
        if raise_on == "Both" or raise_on == kind:
            if self.isMinimized():
                self.showNormal()
            self.raise_()
        if self.store.get("osd_filename") and not is_audio(cur):
            QTimer.singleShot(400, self._show_filename)
        self._track_popup()
        QTimer.singleShot(1500, self._load_chapters)
        self._resize_to_video_pending = True

    def _show_filename(self):
        e = self.engine
        if self.mode == "player" and e.current and e.has_video and self.pip is None:
            self.osd.show_text(e.file_title(), "", self.store.get("osd_filename_duration"), big=False)

    def _load_chapters(self):
        self._chapters = self.engine.chapters()
        self._update_seek_modes()

    def _on_meta(self):
        self._update_title()
        self._update_surface()
        self._sync_all()

    def _on_video(self, has_video):
        if self.engine.current and is_audio(self.engine.current):
            self.rebuild_bars()
        self._update_surface()
        if has_video and getattr(self, "_resize_to_video_pending", False):
            self._resize_to_video_pending = False
            QTimer.singleShot(600, self._resize_to_video)

    def _on_volume(self, v, muted):
        for w in self._elements("volume"):
            w.set_volume(v, muted, self.engine.max_volume())

    def _on_message(self, text, key=""):
        if self.store.get("osd_feedback") and self.mode == "player" and self.isVisible():
            self.osd.show_text(text, key or getattr(self, "_last_key", ""), self.store.get("osd_duration"))
        self._last_key = ""

    def _on_resumed(self, ms, mode):
        if mode == "auto":
            self.toast.show_message(f"Continued from <b>{fmt_time(ms)}</b>", "Start Over", 6000)
            self.toast.action.connect(self.engine.restart)
        else:
            self.toast.show_message(f"You already watched till <b>{fmt_time(ms)}</b>", "Continue Watching", 8000)
            self.toast.action.connect(lambda: self.engine.continue_from(ms))

    def _on_fx(self, key):
        if key in ("sub_timeline", "waveform", "sub_delay", ""):
            self._update_seek_modes()
        if key in ("statistics", ""):
            self._toggle_stats(self.engine.fx["statistics"])
        if key in ("visualizer", ""):
            self._update_surface()
        if key in ("speed", ""):
            self._sync_all()

    def _on_rebuilt(self):
        self.engine.set_window(int(self.video.winId()))

    def _on_library(self, what):
        if what in ("favorites", "bookmarks"):
            self._sync_all()
            self._update_seek_modes()

    def _on_location(self):
        pass

    def _on_setting(self, key, value):
        if key in ("theme", "accent"):
            self.apply_theme()
        elif key in ("title_filename",):
            self._update_title()
        elif key in ("on_top", "window_opacity", "decorations", "tray_icon", "pause_other_audio"):
            self._apply_window_settings()
        elif key in ("autohide", "fs_controls"):
            self._layout_bars_visibility()
        elif key == "max_volume":
            self._sync_all()
        elif key in ("use_library",):
            self.sidebar_open = False
            self._sync_compact()
        elif key in ("custom_aspects", "custom_crops"):
            for p in self.panels.values():
                p.deleteLater()
            self.panels = {}

    # ------------------------------------------------------------------ surfaces / video placement
    def _update_surface(self):
        e = self.engine
        cur = e.current
        show_music = bool(cur) and not e.has_video and e.fx["visualizer"] not in ("Spectometer", "Scope", "Spectrum")
        if self.pip is not None and cur:
            self.player.music.set_media("", "Playing in picture in picture. Double-click it to come back.")
            self.player.stack.setCurrentWidget(self.player.music)
        elif show_music:
            cover = e.artwork() or (self.media.cover_path(cur) if not is_url(cur) else "")
            text = e.file_title()
            if e.artist():
                text = f"{e.file_title()} - {e.artist()}"
            self.player.music.set_media(cover, text)
            self.player.stack.setCurrentWidget(self.player.music)
        elif self.player.video.parentWidget() is self.player.stack:
            idle = not cur or e.state in ("stopped", "error")
            self.player.stack.setCurrentWidget(self.player.blank if idle else self.player.video)
        self._place_video()

    def _place_video(self):
        v = self.video
        e = self.engine
        target = "player"
        if self.pip is not None:
            target = "pip"
        elif self.mode == "library" and e.current and (e.is_active() or e.state == "paused"):
            target = "mini"
        cur_parent = v.parentWidget()
        if target == "pip":
            if cur_parent is not self.pip:
                self.mini.detach()
                self.pip.attach(v)
        elif target == "mini":
            if cur_parent is not self.mini.holder:
                self.mini.attach(v)
        else:
            if cur_parent is not self.player.stack:
                self.mini.detach()
                self.player.stack.insertWidget(0, v)
                if self.engine.has_video or not self.engine.current:
                    self.player.stack.setCurrentWidget(v)
                v.set_radius(9 if self.player.frame_lay.contentsMargins().left() else 0)
        self.place_mini()

    def place_mini(self):
        e = self.engine
        show = self.mode == "library" and bool(e.current) and self.pip is None
        self.mini.setVisible(show)
        if not show:
            return
        self.mini.refresh()
        self.mini.adjustSize()
        par = self.library_page.rect()
        corner = self.store["mini_corner"]
        w, h = self.mini.width(), self.mini.height()
        x = 0 if "l" in corner else par.width() - w
        y = 0 if "t" in corner else par.height() - h
        self.mini.move(x, y)
        self.mini.raise_()

    def close_mini(self):
        self.engine.stop()
        self.engine.clear()
        self.place_mini()

    # ------------------------------------------------------------------ modes
    def show_player(self):
        if self.mode != "player":
            self.mode = "player"
            self.pages.setCurrentWidget(self.player_page)
            self.rebuild_bars()
        self._place_video()
        self._layout_bars_visibility()
        self._sync_compact()

    def show_library_window(self, loc=None):
        if loc:
            self.library.navigate(loc)
        if self.isFullScreen():
            self.exit_fullscreen()
        if self.mode != "library":
            self.mode = "library"
            self.pages.setCurrentWidget(self.library_page)
            self.osd.hide()
            self.stats.hide()
            self.close_panels()
            self.rebuild_bars()
        self._place_video()
        self._layout_bars_visibility()
        self._sync_compact()

    def go_home(self):
        self.show_library_window(("home",))

    def show_library_page(self, key):
        self.show_library_window((key,))

    def toggle_sidebar(self):
        if self.mode == "library":
            self.show_player()
            return
        if not self.store.get("use_library"):
            return
        self.sidebar_open = not self.sidebar_open
        self._sync_compact()
        self._sync_all()

    def _sync_compact(self):
        show = (self.sidebar_open and self.mode == "player" and not self.isFullScreen() and self.isVisible()
                and not self.isMinimized() and self.pip is None)
        if show:
            self._place_compact()
            if not self.compact.isVisible():
                self.compact.show()
            self.compact.raise_()
        elif self.compact.isVisible():
            self.compact.hide()

    def _place_compact(self):
        page = self.player_page
        top_left = page.mapToGlobal(QPoint(0, 0))
        h = page.height() - (self.player.bar.height() if self.player.bar.isVisible() else 0)
        self.compact.setGeometry(top_left.x(), top_left.y(), self.compact.width(), max(200, h))

    def open_current_folder(self):
        cur = self.engine.current
        if cur and not is_url(cur):
            self.library.show_folder(cur)
        elif cur:
            self.library.navigate(("network",))
        else:
            self.library.navigate(("videos",))
        if self.mode == "player" and not self.sidebar_open:
            self.toggle_sidebar()

    def _layout_bars_visibility(self):
        """Windowed bars vs overlay bars, depending on fullscreen / hide UI / auto-hide."""
        overlay = self.overlay_mode()
        decorations = self.store.get("decorations")
        in_library = self.mode == "library"
        self.titlebar.setVisible(not self.isFullScreen() and (in_library or (not overlay and decorations)))
        self.player.bar.setVisible(not overlay and not in_library)
        if self.isFullScreen():
            self.player.set_inset(0)
        elif overlay and not in_library:
            self.player.set_inset(10, top=10)
        else:
            self.player.set_inset(10, top=1)
        if overlay and not in_library:
            self._overlay_timer.start()
            if self.store.get("autohide") == "Never" and self.isFullScreen():
                self._show_overlays()
        else:
            self._overlay_timer.stop()
            self._hide_overlays(force=True)
            self._show_cursor()
        QTimer.singleShot(0, self._reposition_floating)

    def overlay_mode(self):
        if self.mode != "player":
            return False
        if self.isFullScreen():
            return True
        return self.hide_ui or self.store.get("autohide") == "Always"

    # overlays ------------------------------------------------------------------
    def _ensure_overlays(self):
        for kind in ("top", "bottom"):
            if kind not in self._fs_bars:
                ow = OverlayWindow(self, kind)
                if kind == "top":
                    ow.bar.build(self.store.layout())
                else:
                    ow.bar.build(self._rows_for_media(self.store.layout()["rows"]))
                self._fs_bars[kind] = ow
        self._sync_all()
        self._update_seek_modes()

    def _overlay_geometry(self, kind):
        vr = self.player.video_rect_global()
        if self.isFullScreen():
            vr = self.windowHandle().screen().geometry() if self.windowHandle() else vr
        ow = self._fs_bars[kind]
        h = ow.bar.height() if kind == "top" else ow.bar.height()
        if kind == "top":
            return QRect(vr.left(), vr.top(), vr.width(), 40)
        return QRect(vr.left(), vr.bottom() - h + 1, vr.width(), h)

    def _show_overlays(self, which=("top", "bottom")):
        if not (self.store.get("fs_controls") or not self.isFullScreen()):
            return
        self._ensure_overlays()
        for kind in which:
            ow = self._fs_bars[kind]
            ow.setGeometry(self._overlay_geometry(kind))
            if self.isFullScreen():
                ow.setWindowFlags(ow.windowFlags() | Qt.WindowStaysOnTopHint)
            ow.show()
            ow.raise_()
        self._show_cursor()

    def _hide_overlays(self, force=False):
        if not force:
            if self.open_panel is not None or QApplication.activePopupWidget() is not None:
                self._hide_timer.start(800)
                return
            if self.store.get("autohide") == "Never" and self.isFullScreen():
                return
            pos = QCursor.pos()
            for ow in self._fs_bars.values():
                if ow.isVisible() and ow.geometry().adjusted(0, -20, 0, 20).contains(pos):
                    self._hide_timer.start(400)
                    return
        for ow in self._fs_bars.values():
            ow.hide()
        self.preview.hide()

    def _overlay_tick(self):
        if not self.isActiveWindow() and not any(o.isVisible() for o in self._fs_bars.values()) \
                and self.open_panel is None:
            return
        pos = QCursor.pos()
        vr = self.player.video_rect_global()
        if self.isFullScreen() and self.windowHandle():
            vr = self.windowHandle().screen().geometry()
        if not vr.adjusted(-2, -2, 2, 2).contains(pos):
            if any(o.isVisible() for o in self._fs_bars.values()) and not self._hide_timer.isActive():
                self._hide_timer.start(int(self.store.get("hide_after") * 1000) + 50)
            return
        zone = {0: 40, 1: 90, 2: 160}.get(int(self.store.get("fs_sensitivity")), 90)
        near_top = pos.y() - vr.top() < zone
        near_bottom = vr.bottom() - pos.y() < zone + 54
        if near_top or near_bottom:
            self._hide_timer.stop()
            self._show_overlays([k for k, near in (("top", near_top), ("bottom", near_bottom)) if near])
        elif any(o.isVisible() for o in self._fs_bars.values()):
            if not self._hide_timer.isActive():
                self._hide_timer.start(int(self.store.get("hide_after") * 1000) + 50)

    def show_overlay_bars(self):
        if self.overlay_mode():
            self._show_overlays()
            self._hide_timer.start(2500)

    def _surface_moved(self, gpos):
        if self.isFullScreen() or self.overlay_mode():
            self._show_cursor()
            self._cursor_timer.start()

    def _hide_cursor(self):
        if self.isFullScreen() and not any(o.isVisible() for o in self._fs_bars.values()) and self.open_panel is None:
            QApplication.setOverrideCursor(Qt.BlankCursor)
            self._cursor_hidden = True

    def _show_cursor(self):
        if self._cursor_hidden:
            QApplication.restoreOverrideCursor()
            self._cursor_hidden = False

    # fullscreen / hide UI / pip ------------------------------------------------------
    def toggle_fullscreen(self):
        if self.isFullScreen():
            self.exit_fullscreen()
        else:
            self.enter_fullscreen()

    def enter_fullscreen(self):
        if self.pip is not None:
            self.toggle_pip()
        if self.mode != "player":
            self.show_player()
        self._was_max = self.isMaximized()
        target = self.store.get("fullscreen_screen")
        if target != "Current screen":
            for sc in QApplication.screens():
                if target.startswith(sc.name()) and self.windowHandle():
                    self.windowHandle().setScreen(sc)
                    self.move(sc.geometry().topLeft())
        self.layout().setContentsMargins(0, 0, 0, 0)
        self.showFullScreen()
        self._layout_bars_visibility()
        self._sync_all()

    def exit_fullscreen(self):
        self._show_cursor()
        if self._was_max:
            self.showMaximized()
        else:
            self.showNormal()
        self._layout_bars_visibility()
        self._sync_all()

    def toggle_hide_ui(self):
        if self.mode != "player":
            self.show_player()
        self.hide_ui = not self.hide_ui
        self._layout_bars_visibility()
        self.osd.show_text("Interface hidden" if self.hide_ui else "Interface shown",
                           self.key_label("hide_ui") if self.hide_ui else "", 1.5)

    def toggle_pip(self):
        if self.pip is None:
            if not self.engine.current or not self.engine.has_video:
                self.notify("Picture in picture needs a video")
                return
            if self.isFullScreen():
                self.exit_fullscreen()
            self.pip = PipWindow(self)
            self.pip.start_move = lambda p=self.pip: wm.start(p)
            self._place_video()
            self._update_surface()
            self.showMinimized() if self.mode == "player" else None
        else:
            pip = self.pip
            self.pip = None
            pip.detach()
            pip.deleteLater()
            if self.isMinimized():
                self.showNormal()
            self._place_video()
            self._update_surface()
        self._sync_all()
        self._sync_compact()

    def escape(self):
        if self.open_panel is not None:
            self.close_panels()
        elif self.isFullScreen():
            self.exit_fullscreen()
        elif self.pip is not None:
            self.toggle_pip()
        elif self.hide_ui:
            self.toggle_hide_ui()

    # ------------------------------------------------------------------ panels
    def panels_queue(self):
        return self._panel("queue")

    def _panel(self, kind):
        p = self.panels.get(kind)
        if p is None:
            p = {"tracks": TracksPanel, "settings": SettingsPanel, "queue": QueuePanel}[kind](self)
            p.closed.connect(lambda k=kind: self._panel_closed(k))
            self.panels[kind] = p
        return p

    def toggle_panel(self, kind, tab=None, anchor=None, force=False):
        p = self._panel(kind)
        if p.isVisible() and not force and (tab is None or p.current_tab() == tab):
            p.hide()
            return
        for k, other in self.panels.items():
            if other is not p and other.isVisible():
                other.hide()
        if tab:
            p.show_tab(tab)
        else:
            p.show_tab(p.current_tab())
        self.open_panel = p
        self._place_panel(p)
        if self.isFullScreen():
            p.setWindowFlags(p.windowFlags() | Qt.WindowStaysOnTopHint)
        p.show()
        p.raise_()
        if self.overlay_mode():
            self._show_overlays(["bottom"])
        self._sync_all()

    def toggle_queue(self):
        self.toggle_panel("queue", "queue")

    def toggle_right_panel(self):
        if self.open_panel is not None:
            self.close_panels()
        else:
            self.toggle_panel(getattr(self, "_last_panel", "queue"))

    def close_panels(self):
        for p in self.panels.values():
            if p.isVisible():
                p.hide()

    def _panel_closed(self, kind):
        if self.open_panel is self.panels.get(kind):
            self._last_panel = kind
            self.open_panel = None
        self._sync_all()

    def _place_panel(self, p):
        vr = self.player.video_rect_global()
        if self.isFullScreen() and self.windowHandle():
            vr = self.windowHandle().screen().geometry()
        bottom = vr.bottom()
        if self.overlay_mode() and "bottom" in self._fs_bars:
            bottom -= self._fs_bars["bottom"].bar.height()
        h = max(260, bottom - vr.top() - 40)
        p.setGeometry(vr.right() - 20 - 400 + 1, vr.top() + 20, 400, h)

    def _reposition_floating(self):
        self._sync_compact()
        for p in self.panels.values():
            if p.isVisible():
                self._place_panel(p)
        if self.toast.isVisible():
            self.toast.reposition()
        if self.osd.isVisible():
            self.osd.reposition()
        for kind, ow in self._fs_bars.items():
            if ow.isVisible():
                ow.setGeometry(self._overlay_geometry(kind))
        if self.stats.isVisible():
            vr = self.player.video_rect_global()
            self.stats.move(vr.left() + 20, vr.top() + 20)
        self.place_mini()

    def _toast_anchor(self):
        if self.mode == "library":
            r = self.library_page.rect()
            return self.library_page.mapToGlobal(QPoint(r.center().x(), r.bottom() - 20))
        vr = self.player.video_rect_global()
        bottom = vr.bottom() + 1
        if self.overlay_mode() and any(o.isVisible() for o in self._fs_bars.values()):
            bottom -= 54
        return QPoint(vr.center().x(), bottom)

    def _osd_anchor(self):
        vr = self.player.video_rect_global()
        if self.isFullScreen() and self.windowHandle():
            vr = self.windowHandle().screen().geometry()
        return QPoint(vr.right() - 24, vr.top() + 24 + (40 if self.overlay_mode() and
                                                         self._fs_bars.get("top") and self._fs_bars["top"].isVisible() else 0))

    # stats -----------------------------------------------------------------------------
    def _toggle_stats(self, on):
        if on:
            self._update_stats()
            vr = self.player.video_rect_global()
            self.stats.move(vr.left() + 20, vr.top() + 20)
            self.stats.show()
            self._stats_timer.start()
        else:
            self._stats_timer.stop()
            self.stats.hide()

    def _update_stats(self):
        if self.mode != "player":
            self.stats.hide()
            return
        self.stats.set_rows(self.engine.stats() or {"Statistics": "Nothing playing"})
        if not self.stats.isVisible():
            self.stats.show()

    # ------------------------------------------------------------------ seek bar
    def scrub(self, frac):
        import time as _t
        now = _t.monotonic()
        length = self.engine.time()[1]
        self._update_time(int(frac * length), length)
        if now - self._scrub_last > 0.09:
            self._scrub_last = now
            self.engine.set_position(frac)

    def seek_fraction(self, frac):
        self.engine.set_position(frac)

    def seek_hover(self, frac, gpos, bar):
        e = self.engine
        length = e.time()[1]
        if not e.current or length <= 0:
            return
        ms = int(frac * length)
        pm = None
        if self.store.get("seek_thumbs") and e.has_video and not is_url(e.current):
            pm = self.media.preview(e.current, ms, length)
            self._preview_key = (e.current, int(ms / length * 240))
        top = bar.mapToGlobal(QPoint(0, 0)).y()
        self._preview_args = (gpos.x(), top, fmt_time(ms))
        self.preview.show_at(gpos.x(), top, fmt_time(ms), pm)

    def _preview_ready(self, path, bucket):
        if self.preview.isVisible() and getattr(self, "_preview_key", None) == (path, bucket):
            x, top, text = self._preview_args
            length = self.engine.time()[1]
            pm = self.media.preview(path, int((bucket + 0.5) / 240 * length), length)
            self.preview.show_at(x, top, text, pm)

    def seek_hover_end(self):
        self.preview.hide()

    def jump_cue(self, direction):
        e = self.engine
        cur = e.current
        if not cur:
            return
        subs = self.store.external_subs(cur) or find_subtitles(cur)
        cues = self.media.cues(cur, subs[0] if subs else None) or []
        shift = int(e.fx["sub_delay"] * 1000)
        t = e.time()[0]
        starts = sorted(a + shift for a, _ in cues)
        if direction > 0:
            nxt = next((s for s in starts if s > t + 300), None)
        else:
            nxt = next((s for s in reversed(starts) if s < t - 800), None)
        if nxt is not None:
            e.seek_to(max(0, nxt - 200))

    def nudge_sub(self, delta):
        e = self.engine
        e.set_fx("sub_delay", round(e.fx["sub_delay"] + delta, 3))
        self.osd.show_text(f"Subtitle delay: {e.fx['sub_delay'] * 1000:+.0f} ms", "", self.store.get("osd_duration"))

    # ------------------------------------------------------------------ surface actions
    def _surface_click(self):
        a = self.store.get("click_action")
        if a == "Play / Pause":
            self.engine.toggle()
        elif a == "Toggle fullscreen":
            self.toggle_fullscreen()
        elif a == "Show controls":
            self.show_overlay_bars()
        self.close_panels() if self.open_panel is not None and a == "Nothing" else None

    def _surface_double(self):
        if self.pip is not None:
            return                      # in picture in picture a double click just goes back (PipWindow)
        a = self.store.get("dblclick_action")
        if a == "Toggle fullscreen":
            self.toggle_fullscreen()
        elif a == "Play / Pause":
            self.engine.toggle()

    def _surface_middle(self):
        # VLC: the middle button shows or hides the fullscreen controls
        if self.pip is None and self.overlay_mode():
            if any(o.isVisible() for o in self._fs_bars.values()):
                self._hide_overlays(force=True)
            else:
                self.show_overlay_bars()

    def video_drags_window(self):
        return not self.titlebar.isVisible()

    def _surface_wheel(self, dx, dy):
        rev = -1 if self.store.get("reverse_scroll") else 1
        for amount, action in ((dy, self.store.get("wheel_action")), (dx, self.store.get("hwheel_action"))):
            if not amount:
                continue
            amount *= rev
            if action == "Volume":
                self.engine.change_volume(amount)
            elif action == "Seek":
                self.engine.seek_rel(amount * int(self.store.get("skip_vshort")) * 1000)
                self.engine.message.emit(f"{'+' if amount > 0 else '-'}{int(self.store.get('skip_vshort'))} s", "")

    # ------------------------------------------------------------------ simple actions
    def minimize(self):
        self.showMinimized()

    def toggle_maximized(self):
        if self.isFullScreen():
            return
        self.showNormal() if self.isMaximized() else self.showMaximized()

    def close_window(self):
        self.close()

    def quit(self):
        self.close()

    def start_move(self):
        if self.windowHandle() and not self.isFullScreen():
            wm.start(self)

    def take_snapshot(self):
        path = self.engine.snapshot()
        if path and self.store.get("snap_preview"):
            QTimer.singleShot(300, lambda: self._snapshot_preview(path))

    def _snapshot_preview(self, path):
        pm = QPixmap(path)
        if pm.isNull():
            return
        self.toast.show_message(f"<img src='{path}' width='160'><br>{os.path.basename(path)}", "Open folder", 5000)
        self.toast.action.connect(lambda: subprocess.Popen(["xdg-open", os.path.dirname(path)]))

    def show_time(self):
        t, length = self.engine.time()
        self.osd.show_text(f"{fmt_time(t)} / {fmt_time(length)}", self.key_label("show_time"), 2.0)

    def volume_up(self):
        self.engine.change_volume(1)

    def volume_down(self):
        self.engine.change_volume(-1)

    def faster(self):
        self.engine.set_rate(self._next_rate(1))

    def slower(self):
        self.engine.set_rate(self._next_rate(-1))

    def normal_speed(self):
        self.engine.set_rate(1.0)

    def _next_rate(self, d):
        steps = [0.25, 0.5, 0.75, 0.9, 1.0, 1.1, 1.25, 1.5, 1.75, 2.0, 2.5, 3.0, 4.0]
        r = self.engine.rate()
        if d > 0:
            return next((s for s in steps if s > r + 0.01), steps[-1])
        return next((s for s in reversed(steps) if s < r - 0.01), steps[0])

    def jump(self, size, direction):
        secs = int(self.store.get({"vshort": "skip_vshort", "short": "skip_short", "medium": "skip_medium",
                                   "long": "skip_long"}[size]))
        self.engine.seek_rel(direction * secs * 1000)
        t, length = self.engine.time()
        self.engine.message.emit(f"{'+' if direction > 0 else '-'}{secs} s  ({fmt_time(t)} / {fmt_time(length)})", "")

    def cycle_audio(self):
        self.engine.cycle_audio(self._last_key)

    def cycle_spu(self):
        self.engine.cycle_spu(self._last_key)

    def audio_delay(self, delta):
        e = self.engine
        e.set_fx("audio_delay", round(e.fx["audio_delay"] + float(delta), 3))
        e.message.emit(f"Audio delay: {e.fx['audio_delay'] * 1000:+.0f} ms", "")

    def sub_delay(self, delta):
        e = self.engine
        e.set_fx("sub_delay", round(e.fx["sub_delay"] + float(delta), 3))
        e.message.emit(f"Subtitle delay: {e.fx['sub_delay'] * 1000:+.0f} ms", "")

    def font_families(self):
        from PyQt5.QtGui import QFontDatabase
        fams = sorted(set(QFontDatabase().families()))
        return [("", "System")] + [(f, f) for f in fams]

    def aspect_options(self):
        extra = [x.strip() for x in (self.store.get("custom_aspects") or "").split(",") if x.strip()]
        return prefs.ASPECTS + [x for x in extra if x not in prefs.ASPECTS]

    def crop_options(self):
        extra = [x.strip() for x in (self.store.get("custom_crops") or "").split(",") if x.strip()]
        return prefs.CROPS + [x for x in extra if x not in prefs.CROPS]

    def _cycle(self, key, options, label):
        cur = self.engine.fx[key]
        nxt = options[(options.index(cur) + 1) % len(options)] if cur in options else options[0]
        self.engine.set_fx(key, nxt, announce=False)
        self.engine.message.emit(f"{label}: {nxt}", "")

    def cycle_aspect(self):
        self._cycle("aspect", self.aspect_options(), "Aspect ratio")

    def cycle_crop(self):
        self._cycle("crop", self.crop_options(), "Crop")

    def cycle_zoom(self):
        self._cycle("zoom", prefs.ZOOMS, "Zoom")

    def toggle_deinterlace(self):
        cur = self.store.get("deinterlace")
        nxt = "Off" if cur != "Off" else "On"
        self.store.set("deinterlace", nxt)
        self.engine.message.emit(f"Deinterlacing: {nxt.lower()}", "")

    def toggle_favorite_current(self):
        cur = self.engine.current
        if cur:
            on = self.store.toggle_favorite(cur)
            self.notify("Added to Favorites" if on else "Removed from Favorites")

    def add_bookmark(self):
        e = self.engine
        cur = e.current
        if not cur or not e.is_active():
            self.notify("Play something to add a bookmark")
            return
        t = e.mp.get_time()
        n = len(self.store.bookmarks(cur)) + 1
        name = f"{e.file_title().upper()} {n}"
        if self.store.add_bookmark(cur, t, name):
            e.message.emit(f"Bookmark added: {fmt_time(t)}", self.key_label("bookmark"))
            self._update_seek_modes()

    def key_label(self, aid):
        return self.hotkeys().get(aid, "")

    # ------------------------------------------------------------------ playing things
    def play(self, items, index=0, names=None, shuffle=False):
        items = list(items)
        if not items:
            return
        if shuffle:
            import random
            random.shuffle(items)
        if self.engine.load(items, index, names=names):
            if not self.store.get("use_library") or self.mode == "player" or not self.engine.has_video or \
                    not is_audio(self.engine.current):
                self.show_player()
            for i in items[:1]:
                self.store.add_recent_open(i) if is_url(i) else None

    def enqueue(self, items, names=None):
        n = self.engine.enqueue(items, names=names)
        if n:
            self.notify(f"Added {n} to the queue" if n > 1 else "Added to the queue")

    def handle_args(self, items):
        items = [i for i in items if i]
        if not items:
            self.raise_()
            self.activateWindow()
            return
        subs = [i for i in items if ext(i) in SUB_EXT]
        media = [i for i in items if ext(i) not in SUB_EXT]
        if media:
            if self.store.get("enqueue_opened") and self.engine.items:
                self.enqueue(media)
            else:
                self.play(media)
        for s in subs:
            self.engine.add_subtitle(s)
        if self.isMinimized():
            self.showNormal()
        self.raise_()
        self.activateWindow()

    # ------------------------------------------------------------------ dialogs
    def dialog_options(self):
        return QFileDialog.DontUseNativeDialog if self.store.get("inapp_dialogs") else QFileDialog.Options()

    def open_dialog(self, kind="file", enqueue=False, start=None):
        start = start or (os.path.dirname(self.engine.current) if self.engine.current and not is_url(self.engine.current)
                          else os.path.expanduser("~/Videos"))
        if kind == "folder":
            folder = FileDialog.getExistingDirectory(self, "Open Folder", start, options=self.dialog_options())
            items = [folder] if folder else []
        else:
            exts = " ".join(f"*.{x}" for x in sorted(MEDIA_EXT | {"m3u", "m3u8", "pls"}))
            items, _ = FileDialog.getOpenFileNames(self, "Open File(s)", start, f"Media ({exts});;All files (*)",
                                                    options=self.dialog_options())
        if not items:
            return
        for i in items:
            self.store.add_recent_open(i)
        if enqueue:
            self.enqueue(items)
        else:
            self.play(items)

    def open_network(self, enqueue=False):
        NetworkDialog(self, enqueue).exec_()

    def open_disc(self):
        DiscDialog(self).exec_()

    def open_capture(self):
        CaptureDialog(self).exec_()

    def open_convert(self, items=None):
        items = items or ([self.engine.current] if self.engine.current else [])
        if not items:
            files, _ = FileDialog.getOpenFileNames(self, "Convert", os.path.expanduser("~/Videos"),
                                                    options=self.dialog_options())
            items = files
        if items:
            ConvertDialog(self, items).exec_()

    def open_stream_out(self, items=None):
        items = items or ([self.engine.current] if self.engine.current else [])
        if items:
            StreamDialog(self, items).exec_()

    def open_add_subtitles(self, path=None, scope="file", items=None):
        if path is None and items is None and not self.engine.current:
            self.notify("Play a video or choose a folder first")
            return
        if scope == "folder" and path is None:
            return
        d = AddSubtitlesDialog(self, path, scope, items)
        d.exec_()
        for p in self.panels.values():
            if hasattr(p, "refresh"):
                p.refresh()

    def add_audio_tracks(self, path=None, folder=None):
        start = folder or (os.path.dirname(path) if path and not is_url(path) else os.path.expanduser("~"))
        f, _ = FileDialog.getOpenFileName(self, "Add an audio track", start,
                                           "Audio (" + " ".join(f"*.{x}" for x in sorted(AUDIO_EXT)) + ")",
                                           options=self.dialog_options())
        if not f:
            return
        if path and path != self.engine.current:
            self.play([path])
            QTimer.singleShot(1200, lambda: self.engine.add_audio_file(f))
        else:
            self.engine.add_audio_file(f)

    def new_playlist(self, items=None):
        d = PlaylistDialog(self, items=items)
        if d.exec_() and d.pid:
            self.library.navigate(("playlists", d.pid))

    def playlist_properties(self, pid):
        PlaylistDialog(self, pid=pid).exec_()

    def export_playlist(self, pid):
        pl = self.store.playlist(pid)
        if not pl:
            return
        path, _ = FileDialog.getSaveFileName(self, "Export Playlist", os.path.expanduser(f"~/{pl['name']}.m3u"),
                                              "M3U playlist (*.m3u)", options=self.dialog_options())
        if path:
            lines = ["#EXTM3U"]
            for i in pl["items"]:
                lines.append(f"#EXTINF:-1,{display_name(i)}")
                lines.append(i)
            try:
                with open(path, "w", encoding="utf-8") as f:
                    f.write("\n".join(lines) + "\n")
                self.notify(f"Exported {os.path.basename(path)}")
            except OSError:
                self.error("Couldn't write the playlist file")

    def playlist_submenu(self, items):
        out = [MenuItem("New Playlist…", lambda: self.new_playlist(items), icon="add", icon_variant="")]
        for pl in self.store.playlists():
            out.append(MenuItem(pl["name"], lambda pid=pl["id"]: (self.store.add_to_playlist(pid, items),
                                                                  self.notify(f"Added to {self.store.playlist(pid)['name']}")),
                                icon="playlist"))
        return out

    def show_properties(self, item=None):
        item = item or self.engine.current
        if item:
            PropertiesDialog(self, item).exec_()

    def folder_info(self, target):
        if isinstance(target, tuple):
            self.notify(f"{self.library.model.title(target)}: {self.library.count(self.library.entries())} items")
            return
        files = media_in_dir(target, recursive=True)
        size = sum(os.path.getsize(f) for f in files if os.path.exists(f))
        dur = sum((self.media.info(f) or {}).get("duration", 0) for f in files)
        text = f"{len(files)} files · {size / 1073741824:.2f} GiB"
        if dur:
            text += f" · {fmt_time(dur)}"
        MessageDialog(self, os.path.basename(target), text, target, ok="Close", cancel="").exec_()

    def show_in_folder(self, path):
        if path and not is_url(path):
            try:
                subprocess.Popen(["nemo", path] if shutil.which("nemo") else ["xdg-open", os.path.dirname(path)])
            except OSError:
                subprocess.Popen(["xdg-open", os.path.dirname(path)])

    def open_settings(self, tab=None):
        from .settings import SettingsDialog
        cat = {"playback": "playback", "video": "video", "audio": "audio", "subtitles": "subtitles"}.get(tab or "", "general")
        self.close_panels()
        SettingsDialog(self, cat).exec_()

    def open_shortcuts(self):
        ShortcutsDialog(self).exec_()

    def show_about(self):
        AboutDialog(self).exec_()

    def customize_player(self):
        from .customize import CustomizeDialog
        if self.mode != "player":
            self.show_player()
        if self.isFullScreen():
            self.exit_fullscreen()
        if self.hide_ui:
            self.toggle_hide_ui()
        CustomizeDialog(self).exec_()

    def confirm(self, title, detail=""):
        return MessageDialog(self, title, title, detail, ok="OK").exec_() == 1

    def error(self, text):
        if self.store.get("show_dialogs"):
            MessageDialog(self, "Error", text, ok="OK", cancel="").exec_()
        else:
            self.notify(text, timeout=4000)

    def notify(self, text, timeout=2200):
        self.toast.show_message(text, "", timeout)

    # ------------------------------------------------------------------ menus
    def open_main_menu(self, anchor):
        k = self.key_label
        recent = self.store["recent_open"]
        profiles = self.store.profiles()
        items = [
            MenuItem("Open File(s)", lambda: self.open_dialog("file"), icon="file", shortcut=k("open_file")),
            MenuItem("Open Folder", lambda: self.open_dialog("folder"), icon="folder", shortcut=k("open_folder")),
            MenuItem("Open Network Stream", self.open_network, icon="streaming", shortcut=k("open_network")),
            MenuItem("Open Disc", self.open_disc, icon="disc", shortcut=k("open_disc")),
            MenuItem("Open Capture Device", self.open_capture, icon="capture-device", shortcut=k("open_capture")),
            MenuItem("Convert", self.open_convert, icon="swap", shortcut=k("convert")),
            MenuItem("Open Recent", icon="recent", enabled=bool(recent),
                     submenu=lambda: [MenuItem(display_name(r), lambda r=r: self.play([r])) for r in recent] +
                     [sep(), MenuItem("Clear", lambda: self.store.__setitem__("recent_open", []))]),
            sep(),
            MenuItem(f"Profile ({self.store.profile})", icon="profile", submenu=self._profile_menu),
            MenuItem("Keyboard Shortcuts", self.open_shortcuts, icon="hotkeys", shortcut=k("shortcuts")),
            MenuItem("Customize Player", self.customize_player, icon="paint"),
            MenuItem("Settings", self.open_settings, icon="settings", shortcut=k("settings")),
            MenuItem("About VLC", self.show_about, icon="info", shortcut=k("about")),
        ]
        m = PopupMenu(items, self, width=367)
        m.popup_below(anchor, gap=6)

    def _profile_menu(self):
        items = [MenuItem(p, lambda p=p: self._switch_profile(p), checked=(p == self.store.profile))
                 for p in self.store.profiles()]
        items.append(MenuItem("New", self._new_profile, icon="add", icon_variant=""))
        if len(self.store.profiles()) > 1:
            items.append(MenuItem(f"Delete “{self.store.profile}”", lambda: self.store.remove_profile(self.store.profile)))
        return items

    def _switch_profile(self, name):
        self.store.switch_profile(name)
        self.apply_theme()
        self.apply_shortcuts()
        self.notify(f"Profile: {name}")

    def _new_profile(self):
        name, ok = QInputDialog.getText(self, "New Profile", "Name (starts as a copy of the current profile):")
        if ok and name.strip():
            if self.store.add_profile(name.strip()):
                self.notify(f"Profile: {name.strip()}")

    def open_context_menu(self, gpos):
        e = self.engine
        k = self.key_label
        active = e.is_active()
        items = [
            MenuItem("Pause" if e.is_playing() else "Play", e.toggle, icon="playback", shortcut=k("play_pause")),
            MenuItem("Stop", e.stop, icon="stop", shortcut=k("stop"), enabled=active),
            MenuItem("Previous Item", e.prev, icon="previous", shortcut=k("prev")),
            MenuItem("Next Item", e.next, icon="next", shortcut=k("next")),
            MenuItem("Stop Recording" if e.is_recording() else "Record", e.toggle_record, icon="record",
                     shortcut=k("record"), enabled=active),
            MenuItem("Show UI" if self.hide_ui else "Hide UI", self.toggle_hide_ui, icon="ui", shortcut=k("hide_ui")),
            MenuItem("Playback", icon="playback", submenu=self._playback_menu),
            MenuItem("Video", icon="video", submenu=self._video_menu),
            MenuItem("Audio", icon="audio", submenu=self._audio_menu),
            MenuItem("Subtitle", icon="subtitles", submenu=self._subtitle_menu),
            MenuItem("More Settings", icon="settings", submenu=lambda: [
                MenuItem("Playback Settings", lambda: self.toggle_panel("settings", "playback", force=True)),
                MenuItem("Video Settings", lambda: self.toggle_panel("settings", "video", force=True)),
                MenuItem("Audio Settings", lambda: self.toggle_panel("settings", "audio", force=True)),
                MenuItem("Subtitle Settings", lambda: self.toggle_panel("settings", "subtitles", force=True)),
                MenuItem("Show Statistics", lambda: e.set_fx("statistics", not e.fx["statistics"]),
                         checked=e.fx["statistics"]),
                MenuItem("Properties & Metadata", self.show_properties, shortcut=k("properties"), enabled=bool(e.current)),
                sep(),
                MenuItem("Always on Top", lambda: self.store.set("on_top", not self.store.get("on_top")),
                         checked=self.store.get("on_top")),
                MenuItem("All Settings…", self.open_settings, shortcut=k("settings"))]),
            MenuItem("Playlist", icon="playlist", submenu=lambda: [
                MenuItem("Queue", lambda: self.toggle_panel("queue", "queue", force=True), shortcut=k("queue")),
                MenuItem("Bookmarks", lambda: self.toggle_panel("queue", "bookmarks", force=True)),
                MenuItem("Chapters", lambda: self.toggle_panel("queue", "chapters", force=True)),
                MenuItem("Add Bookmark", self.add_bookmark, shortcut=k("bookmark"), enabled=active),
                sep(),
                MenuItem("Save Queue as Playlist…", lambda: self.new_playlist(list(e.items)), enabled=bool(e.items)),
                MenuItem("Shuffle", e.toggle_shuffle, checked=e.shuffle),
                MenuItem("Repeat", e.cycle_repeat, checked=e.repeat != "off"),
                MenuItem("Clear Queue", e.clear, enabled=bool(e.items))]),
            MenuItem("Open Media", icon="file", submenu=lambda: [
                MenuItem("Open File(s)", lambda: self.open_dialog("file"), shortcut=k("open_file")),
                MenuItem("Open Folder", lambda: self.open_dialog("folder"), shortcut=k("open_folder")),
                MenuItem("Open Network Stream", self.open_network, shortcut=k("open_network")),
                MenuItem("Open Disc", self.open_disc, shortcut=k("open_disc")),
                MenuItem("Open Capture Device", self.open_capture, shortcut=k("open_capture")),
                MenuItem("Library", self.show_library_window, shortcut=k("home"))]),
            MenuItem("Quit VLC", self.quit, icon="quit", shortcut=k("quit")),
        ]
        PopupMenu(items, self, width=292).popup(gpos)

    def _playback_menu(self):
        e = self.engine
        k = self.key_label
        speeds = [0.25, 0.5, 0.75, 1.0, 1.25, 1.5, 1.75, 2.0]
        return [
            MenuItem("Speed", submenu=lambda: [MenuItem(f"{s:g}×", lambda s=s: e.set_rate(s),
                                                        checked=abs(e.rate() - s) < 0.01) for s in speeds]),
            MenuItem("Faster", self.faster, shortcut=k("faster")),
            MenuItem("Slower", self.slower, shortcut=k("slower")),
            MenuItem("Normal Speed", self.normal_speed, shortcut=k("normal_speed")),
            sep(),
            MenuItem("Jump Forward", lambda: self.jump("short", 1), shortcut=k("fwd_short")),
            MenuItem("Jump Backward", lambda: self.jump("short", -1), shortcut=k("back_short")),
            MenuItem("Jump to Specific Time…", self._jump_to_time),
            MenuItem("Next Frame", e.next_frame, shortcut=k("next_frame")),
            sep(),
            MenuItem("Sleep Timer", submenu=lambda: [MenuItem(o, lambda o=o: e.set_sleep(o), checked=e.fx["sleep"] == o)
                                                     for o in ("Off", "15 minutes", "30 minutes", "45 minutes", "1 hour",
                                                               "1.5 hours", "2 hours", "End of current item")]),
            MenuItem("Stop After Each Item", e.toggle_stop_after, checked=e.stop_after),
            MenuItem("Chapter", enabled=bool(getattr(self, "_chapters", [])),
                     submenu=lambda: [MenuItem(n, lambda i=i: e.set_chapter(i)) for i, n, _ in self._chapters]),
            MenuItem("Bookmarks", enabled=bool(e.current and self.store.bookmarks(e.current)),
                     submenu=lambda: [MenuItem(b["name"] or fmt_time(b["ms"]), lambda b=b: e.seek_to(b["ms"]),
                                               shortcut=fmt_time(b["ms"])) for b in self.store.bookmarks(e.current)]),
        ]

    def _jump_to_time(self):
        t, length = self.engine.time()
        text, ok = QInputDialog.getText(self, "Jump to Time", "Time (hh:mm:ss):", text=fmt_time(t))
        if ok:
            nums = [float(x) for x in re.split(r"[:.]", text.strip()) if x.strip().replace(".", "").isdigit()]
            secs = 0
            for n in nums[:3]:
                secs = secs * 60 + n
            self.engine.seek_to(int(secs * 1000))

    def _video_menu(self):
        e = self.engine
        k = self.key_label
        return [
            MenuItem("Video Track", submenu=lambda: [MenuItem(n, lambda i=i: e.set_video_track(i), checked=e.video_track() == i)
                                                     for i, n in e.video_tracks()] or [MenuItem("None", enabled=False)]),
            MenuItem("Fullscreen", self.toggle_fullscreen, shortcut=k("fullscreen"), checked=self.isFullScreen()),
            MenuItem("Picture in Picture", self.toggle_pip, shortcut=k("mini"), checked=self.pip is not None),
            MenuItem("Always on Top", lambda: self.store.set("on_top", not self.store.get("on_top")),
                     checked=self.store.get("on_top")),
            sep(),
            MenuItem("Zoom", submenu=lambda: [MenuItem(z, lambda z=z: e.set_fx("zoom", z), checked=e.fx["zoom"] == z)
                                              for z in prefs.ZOOMS]),
            MenuItem("Aspect Ratio", submenu=lambda: [MenuItem(a, lambda a=a: e.set_fx("aspect", a),
                                                               checked=e.fx["aspect"] == a) for a in self.aspect_options()]),
            MenuItem("Crop", submenu=lambda: [MenuItem(c_, lambda c_=c_: e.set_fx("crop", c_), checked=e.fx["crop"] == c_)
                                              for c_ in self.crop_options()]),
            MenuItem("Deinterlace", submenu=lambda: [MenuItem(o, lambda o=o: self.store.set("deinterlace", o),
                                                              checked=self.store.get("deinterlace") == o)
                                                     for o in ("Automatic", "On", "Off")]),
            MenuItem("Flip", submenu=lambda: [MenuItem(l, lambda v=v: e.set_fx("flip", v), checked=e.fx["flip"] == v)
                                              for v, l in (("none", "None"), ("horizontal", "Horizontal"),
                                                           ("vertical", "Vertical"))]),
            MenuItem("Rotate", submenu=lambda: [MenuItem(f"{d}°", lambda d=d: e.set_fx("rotate", d), checked=e.fx["rotate"] == d)
                                                for d in (0, 90, 180, 270)]),
            sep(),
            MenuItem("Take Snapshot", self.take_snapshot, shortcut=k("snapshot"), enabled=e.has_video and e.is_active()),
            MenuItem("Video Settings…", lambda: self.toggle_panel("settings", "video", force=True)),
        ]

    def _audio_menu(self):
        e = self.engine
        k = self.key_label
        from .engine import STEREO_MODES
        return [
            MenuItem("Audio Track", submenu=lambda: [MenuItem(n, lambda i=i: e.set_audio_track(i), checked=e.audio_track() == i)
                                                     for i, n in e.audio_tracks()] or [MenuItem("None", enabled=False)]),
            MenuItem("Audio Device", submenu=lambda: [MenuItem("System default", lambda: e.set_audio_device(None))] +
                     [MenuItem(desc, lambda d=d: e.set_audio_device(d), checked=e.audio_device() == d)
                      for d, desc in e.audio_devices()]),
            MenuItem("Stereo Mode", submenu=lambda: [MenuItem(m, lambda m=m: e.set_fx("stereo_mode", m),
                                                              checked=e.fx["stereo_mode"] == m) for m in STEREO_MODES]),
            MenuItem("Equalizer", submenu=lambda: [MenuItem(pn, lambda pn=pn: self._eq(pn), checked=e.fx["eq_preset"] == pn)
                                                   for pn in prefs.EQ_PRESETS]),
            sep(),
            MenuItem("Volume Up", self.volume_up, shortcut=k("vol_up")),
            MenuItem("Volume Down", self.volume_down, shortcut=k("vol_down")),
            MenuItem("Mute", e.toggle_mute, shortcut=k("mute"), checked=e.muted),
            sep(),
            MenuItem("Add Audio Track…", lambda: self.add_audio_tracks(e.current), enabled=bool(e.current)),
            MenuItem("Audio Settings…", lambda: self.toggle_panel("settings", "audio", force=True)),
        ]

    def _eq(self, name):
        e = self.engine
        if name != "None":
            e.fx["eq_bands"] = e.eq_preset_bands(name)
        e.set_fx("eq_preset", name)

    def _subtitle_menu(self):
        e = self.engine
        k = self.key_label
        return [
            MenuItem("Add Subtitle File…", self._add_sub_file, enabled=bool(e.current)),
            MenuItem("Add Subtitles to Folder / Playlist…", lambda: self.open_add_subtitles(
                os.path.dirname(e.current) if e.current and not is_url(e.current) else None, "folder"),
                     enabled=bool(e.current) and not is_url(e.current or "")),
            MenuItem("Sub Track", submenu=lambda: [MenuItem(n, lambda i=i: e.set_spu(i), checked=e.spu() == i)
                                                   for i, n in e.spu_tracks()] or [MenuItem("None", enabled=False)]),
            sep(),
            MenuItem("Delay −100 ms", lambda: self.sub_delay(-0.1), shortcut=k("sub_delay_down")),
            MenuItem("Delay +100 ms", lambda: self.sub_delay(0.1), shortcut=k("sub_delay_up")),
            MenuItem("Reset Delay", lambda: e.set_fx("sub_delay", 0.0)),
            MenuItem("Show Subtitle Timeline", lambda: e.set_fx("sub_timeline", not e.fx["sub_timeline"]),
                     checked=e.fx["sub_timeline"]),
            MenuItem("Subtitle Settings…", lambda: self.toggle_panel("settings", "subtitles", force=True)),
        ]

    def _add_sub_file(self):
        cur = self.engine.current
        start = os.path.dirname(cur) if cur and not is_url(cur) else ""
        path, _ = FileDialog.getOpenFileName(self, "Add subtitles", start,
                                              "Subtitles (*.srt *.ass *.ssa *.vtt *.sub *.smi *.idx);;All files (*)",
                                              options=self.dialog_options())
        if path:
            self.engine.add_subtitle(path)

    # ------------------------------------------------------------------ shortcuts
    def hotkeys(self):
        over = self.store["hotkeys"]
        return {aid: over.get(aid, default) for aid, _c, _l, default, _m in ACTIONS}

    def apply_shortcuts(self):
        for sc in getattr(self, "_shortcuts", []):
            sc.setEnabled(False)
            sc.deleteLater()
        self._shortcuts = []
        # Qt routes keys from the floating sidebar and panels (Qt.Tool windows) to these too.
        self._add_shortcuts(self)
        self._apply_global_hotkeys()

    def _add_shortcuts(self, target):
        for aid, key in self.hotkeys().items():
            if not key:
                continue
            sc = QShortcut(QKeySequence(key), target)
            sc.setContext(Qt.WindowShortcut)
            sc.activated.connect(lambda a=aid, k=key: self._run_action(a, k))
            self._shortcuts.append(sc)

    def _run_action(self, aid, key=""):
        if self.mode == "library" and key in ("Alt+Left", "Alt+Right"):
            self.library.back() if key == "Alt+Left" else self.library.forward()
            return
        method = ACTION[aid][4]
        self._last_key = key
        target, _, arg = method.partition(":")
        if target.startswith("engine."):
            getattr(self.engine, target[7:])()
        elif target == "jump":
            size, direction = arg.split(":")
            self.jump(size, int(direction))
        elif arg:
            getattr(self, target)(float(arg) if re.match(r"^-?\d", arg) else arg)
        else:
            getattr(self, target)()
        if aid in ("play_pause",):
            self.engine.message.emit("Pause" if self.engine.is_playing() else "Play", "") if False else None

    def _apply_global_hotkeys(self):
        mapping = {aid: k for aid, k in self.store["global_hotkeys"].items() if k}
        if not mapping:
            if getattr(self, "_global", None):
                self._global.stop()
                self._global = None
            return
        from .hotkeys import GlobalHotkeys
        if getattr(self, "_global", None):
            self._global.stop()
        self._global = GlobalHotkeys(mapping)
        self._global.triggered.connect(lambda aid: self._run_action(aid))
        self._global.start()

    # ------------------------------------------------------------------ theme / window settings
    def apply_theme(self):
        theme.apply(self.store.get("theme"), self.store.get("accent"))
        self.update()
        for w in self.findChildren(QWidget):
            w.update()
        for b in self.bars():
            b.update()

    def _apply_window_settings(self):
        s = self.store
        self.setWindowOpacity(max(0.1, s.get("window_opacity") / 100))
        self.set_above(self, s.get("on_top"))
        self._layout_bars_visibility()
        if hasattr(self, "tray") and self.tray is not None:
            self.tray.setVisible(bool(s.get("tray_icon")))
        if s.get("pause_other_audio"):
            self._start_pulse_watch()
        elif self.pulse is not None:
            self.pulse.kill()
            self.pulse = None

    def set_above(self, widget, on):
        if shutil.which("wmctrl"):
            QTimer.singleShot(200, lambda: subprocess.run(
                ["wmctrl", "-i", "-r", hex(int(widget.winId())), "-b", ("add" if on else "remove") + ",above"],
                capture_output=True, timeout=3))

    def _update_icon(self, state):
        base = icons.pixmap("vlc", 64, dpr=1.0).copy()
        if state in ("playing", "paused"):
            p = QPainter(base)
            p.setRenderHint(QPainter.Antialiasing)
            p.setPen(Qt.NoPen)
            p.setBrush(QColor("#1b1b1b"))
            p.drawEllipse(36, 36, 28, 28)
            p.drawPixmap(38, 38, icons.pixmap("pause" if state == "playing" else "play", 24, "#ffffff", dpr=1.0))
            p.end()
        self.setWindowIcon(QIcon(base))

    def _resize_to_video(self):
        mode = self.store.get("resize_to_video")
        if mode == "Never" or self.isFullScreen() or self.isMaximized() or self.mode != "player":
            return
        if mode == "First video only" and getattr(self, "_resized_once", False):
            return
        w, h = self.engine.video_size()
        if not w or not h:
            return
        self._resized_once = True
        screen = self.screen().availableGeometry()
        chrome_w = self.width() - self.player.stack.width()
        chrome_h = self.height() - self.player.stack.height()
        scale = min(1.0, (screen.width() - chrome_w - 40) / w, (screen.height() - chrome_h - 40) / h)
        self.resize(int(w * scale) + chrome_w, int(h * scale) + chrome_h)

    def _start_pulse_watch(self):
        if self.pulse is not None or not shutil.which("pactl"):
            return
        from PyQt5.QtCore import QProcess
        self.pulse = QProcess(self)
        self.pulse.readyReadStandardOutput.connect(self._pulse_event)
        self.pulse.start("pactl", ["subscribe"])

    def _pulse_event(self):
        data = bytes(self.pulse.readAllStandardOutput()).decode(errors="replace")
        if "'new' on sink-input" not in data or not self.engine.is_playing():
            return
        try:
            out = subprocess.run(["pactl", "list", "sink-inputs"], capture_output=True, text=True, timeout=2).stdout
        except (OSError, subprocess.TimeoutExpired):
            return
        pids = re.findall(r'application\.process\.id = "(\d+)"', out)
        if any(int(p) != os.getpid() for p in pids):
            self.engine.pause()
            self.notify("Paused because another app is playing sound")

    def _setup_tray(self):
        self.tray = None
        if not QSystemTrayIcon.isSystemTrayAvailable():
            return
        self.tray = QSystemTrayIcon(QIcon(icons.pixmap("vlc", 64, dpr=1.0)), self)
        self.tray.setToolTip(APP_NAME)
        menu = QMenu()
        menu.addAction("Play / Pause", self.engine.toggle)
        menu.addAction("Previous", self.engine.prev)
        menu.addAction("Next", lambda: self.engine.next())
        menu.addSeparator()
        menu.addAction("Show / Hide", self._toggle_visible)
        menu.addAction("Quit", self.quit)
        self.tray.setContextMenu(menu)
        self._tray_menu = menu
        self.tray.activated.connect(lambda reason: reason == QSystemTrayIcon.Trigger and self._toggle_visible())
        self.tray.setVisible(bool(self.store.get("tray_icon")))

    def _toggle_visible(self):
        if self.isVisible() and not self.isMinimized():
            self.hide()
        else:
            self.showNormal()
            self.raise_()
            self.activateWindow()

    def _track_popup(self):
        mode = self.store.get("track_popup")
        hidden = self.isMinimized() or not self.isVisible() or not self.isActiveWindow()
        if mode == "Never" or (mode == "When minimized" and not hidden):
            return
        title = self.engine.title()
        artist = self.engine.artist()
        dur = int(self.store.get("popup_duration") * 1000)
        if dur <= 0:
            return
        if self.tray is not None and self.tray.isVisible():
            self.tray.showMessage(title, artist or "Now playing", QIcon(icons.pixmap("vlc", 64, dpr=1.0)), dur)
        elif shutil.which("notify-send"):
            subprocess.Popen(["notify-send", "-t", str(dur), "-i", "vlc", title, artist or "Now playing"])

    # ------------------------------------------------------------------ drag & drop
    def dragEnterEvent(self, e):
        if e.mimeData().hasUrls() or e.mimeData().hasFormat("application/x-vlcr-items"):
            e.acceptProposedAction()

    def dropEvent(self, e):
        md = e.mimeData()
        if md.hasFormat("application/x-vlcr-items"):
            items = json.loads(bytes(md.data("application/x-vlcr-items")).decode())
        else:
            items = [u.toLocalFile() or u.toString() for u in md.urls()]
        subs = [i for i in items if ext(i) in SUB_EXT]
        media = [i for i in items if ext(i) not in SUB_EXT]
        if subs and self.engine.current:
            for s in subs:
                self.engine.add_subtitle(s)
        if media:
            if e.keyboardModifiers() & Qt.ShiftModifier or (self.engine.is_active() and
                                                            QApplication.keyboardModifiers() & Qt.ControlModifier):
                self.enqueue(media)
            else:
                self.play(media)
        e.acceptProposedAction()

    # ------------------------------------------------------------------ window chrome
    def changeEvent(self, e):
        if e.type() == QEvent.WindowStateChange:
            m = 0 if (self.isMaximized() or self.isFullScreen()) else FRAME
            self.layout().setContentsMargins(m, m, m, m)
            self._sync_all()
            if self.isMinimized():
                if self.store.get("pause_minimized") and self.engine.is_playing() and self.engine.has_video:
                    self.engine.pause()
                self.close_panels()
                for ow in self._fs_bars.values():
                    ow.hide()
            QTimer.singleShot(50, self._reposition_floating)
        super().changeEvent(e)

    def hideEvent(self, e):
        super().hideEvent(e)
        self._sync_compact()

    def showEvent(self, e):
        super().showEvent(e)
        QTimer.singleShot(0, self._sync_compact)

    def moveEvent(self, e):
        super().moveEvent(e)
        self._reposition_floating()

    def resizeEvent(self, e):
        super().resizeEvent(e)
        QTimer.singleShot(0, self._reposition_floating)

    def _edges(self, pos):
        edges = Qt.Edges()
        if self.isMaximized() or self.isFullScreen():
            return edges
        r, m = self.rect(), FRAME + 1
        if pos.x() <= m:
            edges |= Qt.LeftEdge
        if pos.x() >= r.width() - m:
            edges |= Qt.RightEdge
        if pos.y() <= m:
            edges |= Qt.TopEdge
        if pos.y() >= r.height() - m:
            edges |= Qt.BottomEdge
        return edges

    def mouseMoveEvent(self, e):
        edges = self._edges(e.pos())
        if edges in (Qt.LeftEdge | Qt.TopEdge, Qt.RightEdge | Qt.BottomEdge):
            self.setCursor(Qt.SizeFDiagCursor)
        elif edges in (Qt.RightEdge | Qt.TopEdge, Qt.LeftEdge | Qt.BottomEdge):
            self.setCursor(Qt.SizeBDiagCursor)
        elif edges & (Qt.LeftEdge | Qt.RightEdge):
            self.setCursor(Qt.SizeHorCursor)
        elif edges & (Qt.TopEdge | Qt.BottomEdge):
            self.setCursor(Qt.SizeVerCursor)
        else:
            self.unsetCursor()

    def mousePressEvent(self, e):
        edges = self._edges(e.pos())
        if e.button() == Qt.LeftButton and edges and self.windowHandle():
            wm.start(self, edges)

    def leaveEvent(self, e):
        self.unsetCursor()

    def paintEvent(self, e):
        p = QPainter(self)
        p.fillRect(self.rect(), c("bar"))
        if not (self.isMaximized() or self.isFullScreen()):
            p.setPen(c("line"))
            p.drawRect(self.rect().adjusted(0, 0, -1, -1))

    def _restore_geometry(self):
        geo = self.store["geometry"]
        if not (self.store.get("remember_geometry") and geo and
                self.restoreGeometry(QByteArray.fromBase64(geo.encode()))):
            screen = QApplication.primaryScreen().availableGeometry()
            w, h = min(1280, screen.width() - 80), min(800, screen.height() - 80)
            self.setGeometry(screen.x() + (screen.width() - w) // 2, screen.y() + (screen.height() - h) // 2, w, h)

    def closeEvent(self, e):
        if self.isFullScreen():
            self.exit_fullscreen()
        if self.pip is not None:
            self.toggle_pip()
        self.store["geometry"] = bytes(self.saveGeometry().toBase64()).decode()
        for w in (self.toast, self.osd, self.preview, self.stats, *self.panels.values(), *self._fs_bars.values()):
            w.hide()
        if getattr(self, "_global", None):
            self._global.stop()
        if self.pulse is not None:
            self.pulse.kill()
        if self.tray is not None:
            self.tray.hide()
        self.engine.shutdown()
        self.media.shutdown()
        super().closeEvent(e)
        QApplication.quit()
