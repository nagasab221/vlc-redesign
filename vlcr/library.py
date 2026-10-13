"""The media library: what's in it (LibraryModel) and the two ways to browse it:
the compact sidebar next to the video and the full library window.

A location is a tuple: ("home",), ("videos",), ("videos", folder), ("music",),
("music", "songs"[, folder]), ("music", "albums"|"artists"|"genres"[, name]),
("network",), ("network", "list", id), ("network", "podcasts"[, url]), ("network", "radio"),
("playlists",), ("playlists", id), ("playlists", "favorites"), ("recent",).
"""

import json
import os
import re
import threading
import time
import xml.etree.ElementTree as ET

from PyQt5.QtCore import (QByteArray, QEvent, QMimeData, QObject, QPoint, QPointF, QRect, QRectF, QSize, Qt, QTimer,
                          QUrl, pyqtSignal)
from PyQt5.QtGui import QColor, QFontMetrics, QPainter, QPainterPath, QPixmap
from PyQt5.QtNetwork import QNetworkAccessManager, QNetworkRequest
from PyQt5.QtWidgets import (QAbstractItemView, QApplication, QFileDialog, QHBoxLayout, QInputDialog, QLineEdit,
                             QListView, QListWidget, QListWidgetItem, QMessageBox, QScrollArea, QStyle,
                             QStyledItemDelegate, QVBoxLayout, QWidget)

from . import icons, theme
from .engine import (AUDIO_EXT, MEDIA_EXT, VIDEO_EXT, display_name, ext, fmt_short, is_audio, is_url, media_in_dir,
                     natural_key)
from .theme import c
from .widgets import HLine, IconButton, Label, MenuItem, PopupMenu, elide, sep, wants_list_key
from .filedialog import FileDialog

HOME_SECTIONS = ["Continue Watching", "Listen Again", "Frequently Visited Folders", "Frequently Visited Playlists",
                 "Frequently Listened Artists"]

SECTIONS = [
    ("home", "Home", "home"),
    ("videos", "Videos", "videos"),
    ("music", "Music", "songs"),
    ("network", "Network", "streaming"),
    ("playlists", "Playlists", "playlist"),
    ("recent", "Recent", "recent"),
]

SORTS = ["Unwatched First", "Watched First", "Newest First", "Oldest First", "Title A-Z", "Title Z-A", "Artist A-Z",
         "Artist Z-A", "Album A-Z", "Album Z-A", "Track Number Ascending", "Track Number Descending", "URI Ascending",
         "URI Descending", "Disc Number Ascending", "Disc Number Descending"]
DEFAULT_SORT = "Title A-Z"

MIME = "application/x-vlcr-items"


def E(kind, name, **kw):
    d = {"kind": kind, "name": name, "sub": "", "ref": None, "icon": None, "items": None, "loc": None, "thumb": None}
    d.update(kw)
    return d



def _request(url):
    req = QNetworkRequest(QUrl(url))
    req.setAttribute(QNetworkRequest.RedirectPolicyAttribute, QNetworkRequest.NoLessSafeRedirectPolicy)
    req.setRawHeader(b"User-Agent", b"VLCRedesign/0.2 (libVLC 3)")
    return req


class LibraryModel(QObject):
    """Answers "what is at this location" from the folders, tags and saved lists."""
    changed = pyqtSignal()
    feedLoaded = pyqtSignal(str)
    _bgDone = pyqtSignal()          # emitted from worker threads, delivered on the GUI thread

    def __init__(self, store, media):
        super().__init__()
        self.store = store
        self.media = media
        self.music_files = []          # all audio files under the music folders
        self._scan_lock = threading.Lock()
        self._scanning = False
        self.feeds = {}                # url -> {"title", "episodes": [(title, url, date)]}
        self.net = QNetworkAccessManager(self)
        self._bgDone.connect(self.changed.emit)
        store.libraryChanged.connect(self._on_store)
        self.rescan()

    def _on_store(self, what):
        if what == "folders":
            self.drop_index()
            self.rescan()
        self.changed.emit()

    # --- scanning music tags in the background ---------------------------------
    def rescan(self):
        if self._scanning:
            return
        self._scanning = True
        threading.Thread(target=self._scan, daemon=True).start()

    def _scan(self):
        files = []
        for root in self.store["music_folders"]:
            files.extend(media_in_dir(root, AUDIO_EXT, recursive=True, hidden=self.store.get("show_hidden")))
        for f in files:
            self.media.info_now(f)
        self.music_files = files
        self._scanning = False
        self._bgDone.emit()

    def tags(self, path):
        info = self.media.info(path) or {}
        return info

    # --- locations ----------------------------------------------------------------
    def title(self, loc):
        return self.crumbs(loc)[-1][0]

    def crumbs(self, loc):
        """[(label, location)] for the breadcrumb."""
        sec = loc[0]
        names = {k: label for k, label, _ in SECTIONS}
        out = [(names.get(sec, sec.title()), (sec,))]
        if sec == "videos" and len(loc) > 1:
            folder = loc[1]
            root = next((r for r in self.store["video_folders"] if folder == r or folder.startswith(r + os.sep)), None)
            if root:
                if (os.path.basename(root) or root).lower() != "videos":
                    out.append((os.path.basename(root) or root, ("videos", root)))
                rel = os.path.relpath(folder, root)
                if rel != ".":
                    acc = root
                    for part in rel.split(os.sep):
                        acc = os.path.join(acc, part)
                        out.append((part, ("videos", acc)))
            else:
                out.append((os.path.basename(folder), loc))
        elif sec == "music" and len(loc) > 1:
            sub = loc[1]
            out.append((sub.title(), ("music", sub)))
            if sub == "songs" and len(loc) > 2:
                folder = loc[2]
                root = next((r for r in self.store["music_folders"] if folder == r or folder.startswith(r + os.sep)), None)
                if root:
                    if os.path.basename(root).lower() not in ("music", "songs"):
                        out.append((os.path.basename(root), ("music", "songs", root)))
                    rel = os.path.relpath(folder, root)
                    if rel != ".":
                        acc = root
                        for part in rel.split(os.sep):
                            acc = os.path.join(acc, part)
                            out.append((part, ("music", "songs", acc)))
            elif len(loc) > 2:
                out.append((loc[2] or "Unknown", loc))
        elif sec == "network" and len(loc) > 1:
            if loc[1] == "list":
                lst = self.store.network_list(loc[2])
                out.append((lst["name"] if lst else "List", loc))
            elif loc[1] == "podcasts":
                out.append(("Podcasts", ("network", "podcasts")))
                if len(loc) > 2:
                    out.append((self.feeds.get(loc[2], {}).get("title") or loc[2], loc))
            elif loc[1] == "radio":
                out.append(("Radio", loc))
        elif sec == "playlists" and len(loc) > 1:
            if loc[1] == "favorites":
                out.append(("Favorites", loc))
            else:
                pl = self.store.playlist(loc[1])
                out.append((pl["name"] if pl else "Playlist", loc))
        return out

    def entries(self, loc):
        sec = loc[0]
        fn = getattr(self, "_e_" + sec, None)
        return fn(loc) if fn else []

    def home_sections(self):
        """[(title, location for "›", entries, style)], style is video | square | folder | list.
        Sections switched off in Customize Home are left out."""
        hidden = set(self.store["home_hidden"])
        return [s for s in self._home_sections() if s[0] not in hidden]

    def _home_sections(self):
        out = []
        cw = [p for p in self.store.continue_watching(12)]
        if cw:
            out.append(("Continue Watching", ("recent",), [self._media(p) for p in cw], "video"))
        listen = [r["item"] for r in self.store["recent"] if is_audio(r["item"]) and os.path.exists(r["item"])][:12]
        if listen:
            out.append(("Listen Again", ("recent",), [self._media(p) for p in listen], "square"))
        visits = sorted(self.store["visits"].items(), key=lambda kv: -kv[1])
        folders = [f for f, _ in visits if os.path.isdir(f)][:8]
        if not folders:
            folders = [f for f in self.store["video_folders"] + self.store["music_folders"] if os.path.isdir(f)][:8]
        if folders:
            out.append(("Frequently Visited Folders", ("videos",),
                        [self._folder(f, "music" if f in self.store["music_folders"] else "videos") for f in folders],
                        "folder"))
        pv = self.store["playlist_visits"]
        pls = sorted(self.store.playlists(), key=lambda p: -pv.get(p["id"], 0))[:8]
        if pls:
            out.append(("Frequently Visited Playlists", ("playlists",), [self._playlist(p) for p in pls], "list"))
        artists = sorted(self.store["plays"].items(), key=lambda kv: -kv[1])[:8]
        if artists:
            out.append(("Frequently Listened Artists", ("music", "artists"),
                        [E("artist", a, icon="artist", loc=("music", "artists", a)) for a, _ in artists], "list"))
        return out

    def _e_home(self, loc):
        out = []
        for title, more, entries, style in self.home_sections():
            out.append(E("header", title, loc=more))
            out.extend(entries[:3])
        return out

    # videos
    def _e_videos(self, loc):
        if len(loc) == 1:
            out = [E("add", "Add", ref="videos")]
            for f in self.store["video_folders"]:
                out.append(self._folder(f, "videos", removable=True))
            return out
        return self._folder_entries(loc[1], "videos", VIDEO_EXT | AUDIO_EXT)

    def _folder_entries(self, folder, section, kinds):
        out = []
        hidden = self.store.get("show_hidden")
        try:
            entries = sorted(os.scandir(folder), key=lambda e: natural_key(e.name))
        except OSError:
            return [E("empty", "This folder can't be opened. Is the drive connected?")]
        for e in entries:
            if not hidden and e.name.startswith("."):
                continue
            try:
                if e.is_dir():
                    if e.name in ("$RECYCLE.BIN", "System Volume Information", "lost+found"):
                        continue
                    out.append(self._folder(e.path, section))
                elif e.is_file() and ext(e.name) in kinds:
                    out.append(self._media(e.path))
            except OSError:
                pass
        return out

    def _folder(self, path, section, removable=False):
        loc = ("music", "songs", path) if section == "music" else ("videos", path)
        return E("folder", os.path.basename(path.rstrip(os.sep)) or path, ref=path, icon="folder", loc=loc,
                 removable=removable)

    def _media(self, path):
        info = self.media.info(path) or {}
        name = display_name(path)
        sub = info.get("artist", "")
        if is_audio(path) and info.get("title"):
            name = info["title"]
        return E("media", name, ref=path, sub=sub, thumb=path, items=[path], duration=info.get("duration", 0),
                 square=is_audio(path))

    # music
    def _e_music(self, loc):
        if len(loc) == 1:
            return [E("add", "Add", ref="music"),
                    E("category", "Songs", icon="music", loc=("music", "songs")),
                    E("category", "Albums", icon="album", loc=("music", "albums")),
                    E("category", "Artists", icon="artist", loc=("music", "artists")),
                    E("category", "Genres", icon="genre", loc=("music", "genres"))]
        sub = loc[1]
        if sub == "songs":
            if len(loc) == 2:
                return [E("add", "Add", ref="music")] + [self._folder(f, "music", removable=True)
                                                         for f in self.store["music_folders"]]
            return self._folder_entries(loc[2], "music", AUDIO_EXT)
        key = {"albums": "album", "artists": "artist", "genres": "genre"}[sub]
        groups = {}
        for f in self.music_files:
            info = self.media.info(f) or {}
            groups.setdefault(info.get(key) or "Unknown", []).append(f)
        if len(loc) == 2:
            out = []
            for name in sorted(groups, key=natural_key):
                files = groups[name]
                icon = {"album": "album", "artist": "artist", "genre": "genre"}[key]
                sub_txt = (self.media.info(files[0]) or {}).get("artist", "") if key == "album" else \
                    f"{len(files)} song{'s' if len(files) != 1 else ''}"
                out.append(E(key, name, sub=sub_txt, icon=icon, loc=("music", sub, name), items=files,
                             thumb=files[0] if key == "album" else None, square=True))
            return out
        files = groups.get(loc[2], [])
        files.sort(key=lambda f: ((self.media.info(f) or {}).get("disc", 0), (self.media.info(f) or {}).get("track", 0),
                                  natural_key(f)))
        return [self._media(f) for f in files]

    # network
    def _e_network(self, loc):
        net = self.store.network()
        if len(loc) == 1:
            out = [E("add", "Add Network Stream", ref="network"),
                   E("category", "Podcasts", icon="streaming", loc=("network", "podcasts")),
                   E("category", "Radio", icon="streaming", loc=("network", "radio"))]
            for lst in net["lists"]:
                out.append(E("netlist", lst["name"], ref=lst["id"], icon="playlist", loc=("network", "list", lst["id"]),
                             items=[x["url"] for x in lst["items"]], sub=f"{len(lst['items'])} streams"))
            for s in net["streams"]:
                out.append(E("stream", s["name"], ref=s["url"], icon="streaming", items=[s["url"]], sub=s["url"]))
            return out
        if loc[1] == "list":
            lst = self.store.network_list(loc[2]) or {"items": []}
            return [E("stream", x["name"], ref=x["url"], icon="tv", items=[x["url"]], names={x["url"]: x["name"]})
                    for x in lst["items"]]
        if loc[1] == "podcasts":
            if len(loc) == 2:
                out = [E("add", "Add Podcast", ref="podcast")]
                for p in net["podcasts"]:
                    out.append(E("podcast", self.feeds.get(p["url"], {}).get("title") or p["name"], ref=p["url"],
                                 icon="streaming", loc=("network", "podcasts", p["url"])))
                return out
            feed = self.feeds.get(loc[2])
            if feed is None:
                self.load_feed(loc[2])
                return [E("empty", "Loading episodes…")]
            return [E("stream", t, ref=u, icon="music", items=[u], sub=d, names={u: t}) for t, u, d in feed["episodes"]]
        if loc[1] == "radio":
            out = [E("add", "Add Radio Station", ref="radio")]
            for r in net["radio"]:
                out.append(E("stream", r["name"], ref=r["url"], icon="streaming", items=[r["url"]], sub=r["url"],
                             names={r["url"]: r["name"]}, radio=True))
            return out
        return []

    def load_feed(self, url):
        if url in getattr(self, "_loading", set()):
            return
        self._loading = getattr(self, "_loading", set()) | {url}
        reply = self.net.get(_request(url))

        def done():
            self._loading.discard(url)
            data = bytes(reply.readAll())
            reply.deleteLater()
            title, eps = url, []
            try:
                root = ET.fromstring(data)
                ch = root.find("channel")
                if ch is not None:
                    title = (ch.findtext("title") or url).strip()
                    for it in ch.findall("item"):
                        enc = it.find("enclosure")
                        if enc is not None and enc.get("url"):
                            eps.append(((it.findtext("title") or "Episode").strip(), enc.get("url"),
                                        (it.findtext("pubDate") or "")[:16]))
            except ET.ParseError:
                title = "Couldn't read this feed"
            self.feeds[url] = {"title": title, "episodes": eps}
            self.feedLoaded.emit(url)
            self.changed.emit()
        reply.finished.connect(done)

    # playlists
    def _playlist(self, p):
        return E("playlist", p["name"], ref=p["id"], icon="playlist", loc=("playlists", p["id"]), items=list(p["items"]),
                 sub=f"{len(p['items'])} items", thumb=p.get("thumb") or None, image=p.get("thumb") or None)

    def _e_playlists(self, loc):
        if len(loc) == 1:
            out = [E("add", "Create playlist", ref="playlist")]
            if self.store["favorites"]:
                out.append(E("favorites", "Favorites", icon="favorite", loc=("playlists", "favorites"),
                             items=list(self.store["favorites"]), sub=f"{len(self.store['favorites'])} items"))
            out.extend(self._playlist(p) for p in self.store.playlists())
            return out
        if loc[1] == "favorites":
            return [self._media(i) if not is_url(i) else E("stream", display_name(i), ref=i, icon="streaming", items=[i])
                    for i in self.store["favorites"]]
        pl = self.store.playlist(loc[1])
        if not pl:
            return []
        return [self._media(i) if not is_url(i) else E("stream", display_name(i), ref=i, icon="streaming", items=[i],
                                                        in_playlist=pl["id"])
                for i in pl["items"]]

    def _e_recent(self, loc):
        out = []
        for r in self.store["recent"]:
            i = r["item"]
            if is_url(i):
                out.append(E("stream", display_name(i), ref=i, icon="streaming", items=[i], sub=i))
            elif os.path.exists(i):
                out.append(self._media(i))
        return out

    # --- sorting -------------------------------------------------------------------
    def sort(self, entries, how):
        fixed = [e for e in entries if e["kind"] in ("add", "category", "header", "empty")]
        rest = [e for e in entries if e not in fixed]
        folders = [e for e in rest if e["kind"] == "folder"]
        others = [e for e in rest if e["kind"] != "folder"]

        def info(e):
            return (self.media.info(e["ref"]) or {}) if e["kind"] == "media" else {}

        def mtime(e):
            try:
                return os.path.getmtime(e["ref"])
            except (OSError, TypeError):
                return 0
        keyfns = {
            "Unwatched First": (lambda e: (self.store.watched(e["ref"] or ""), natural_key(e["name"])), False),
            "Watched First": (lambda e: (not self.store.watched(e["ref"] or ""), natural_key(e["name"])), False),
            "Newest First": (mtime, True),
            "Oldest First": (mtime, False),
            "Title A-Z": (lambda e: natural_key(e["name"]), False),
            "Title Z-A": (lambda e: natural_key(e["name"]), True),
            "Artist A-Z": (lambda e: (info(e).get("artist", "").lower(), natural_key(e["name"])), False),
            "Artist Z-A": (lambda e: (info(e).get("artist", "").lower(), natural_key(e["name"])), True),
            "Album A-Z": (lambda e: (info(e).get("album", "").lower(), info(e).get("track", 0)), False),
            "Album Z-A": (lambda e: (info(e).get("album", "").lower(), info(e).get("track", 0)), True),
            "Track Number Ascending": (lambda e: info(e).get("track", 0), False),
            "Track Number Descending": (lambda e: info(e).get("track", 0), True),
            "URI Ascending": (lambda e: natural_key(str(e["ref"])), False),
            "URI Descending": (lambda e: natural_key(str(e["ref"])), True),
            "Disc Number Ascending": (lambda e: (info(e).get("disc", 0), info(e).get("track", 0)), False),
            "Disc Number Descending": (lambda e: (info(e).get("disc", 0), info(e).get("track", 0)), True),
        }
        fn, rev = keyfns.get(how, keyfns[DEFAULT_SORT])
        if how in ("Title A-Z", "Title Z-A", "Newest First", "Oldest First", "URI Ascending", "URI Descending"):
            folders.sort(key=fn, reverse=rev)
        others.sort(key=fn, reverse=rev)
        return fixed + folders + others

    def search(self, loc, query):
        q = query.lower()
        base = self.entries(loc)
        out = [e for e in base if e["kind"] not in ("add", "header", "empty") and q in e["name"].lower()]
        # also look inside folders of this location
        roots = []
        if loc[0] == "videos":
            roots = [loc[1]] if len(loc) > 1 else list(self.store["video_folders"])
            kinds = VIDEO_EXT | AUDIO_EXT
        elif loc[0] == "music":
            roots = [loc[2]] if len(loc) > 2 and loc[1] == "songs" else list(self.store["music_folders"])
            kinds = AUDIO_EXT
        seen = {e["ref"] for e in out}
        pending = False
        for r in roots:
            files = self.indexed(r, kinds)
            if files is None:
                pending = True
                continue
            for f in files:
                if q in os.path.basename(f).lower() and f not in seen:
                    out.append(self._media(f))
                    seen.add(f)
                    if len(out) > 400:
                        return out
        if pending:
            out.append(E("empty", "Searching subfolders…"))
        elif not out:
            out.append(E("empty", f"Nothing matches “{query}”."))
        return out

    def indexed(self, root, kinds):
        """All media files under root, from a background index (None while it is built)."""
        key = (root, tuple(sorted(kinds)))
        idx = getattr(self, "_index", None)
        if idx is None:
            idx = self._index = {}
        if key in idx:
            return idx[key]
        building = getattr(self, "_building", set())
        self._building = building
        if key not in building:
            building.add(key)
            hidden = self.store.get("show_hidden")

            def run():
                files = media_in_dir(root, set(kinds), recursive=True, hidden=hidden)
                idx[key] = files
                building.discard(key)
                self._bgDone.emit()
            threading.Thread(target=run, daemon=True).start()
        return None

    def drop_index(self):
        self._index = {}

    def playable(self, entry):
        """Items to play for an entry (folders expand recursively)."""
        if entry.get("items"):
            return list(entry["items"])
        if entry["kind"] == "folder":
            kinds = AUDIO_EXT if entry["loc"][0] == "music" else MEDIA_EXT
            return media_in_dir(entry["ref"], kinds, recursive=True, hidden=self.store.get("show_hidden"))
        return []


# --- painting entries ---------------------------------------------------------------------
LIB_SIZES = {"Large": 1.0, "Medium": 0.8, "Small": 0.64}


class EntryDelegate(QStyledItemDelegate):
    """Paints entries as compact rows (32 px thumbs) or as cards (grid)."""

    def __init__(self, view, mode="row"):
        super().__init__(view)
        self.view = view
        self.mode = mode          # row | card | square | tile | listcol

    @staticmethod
    def is_square(e):
        return bool(e and (e.get("square") or e["kind"] in ("folder", "category", "album", "artist", "genre",
                                                            "playlist", "favorites", "add", "netlist", "podcast")))

    @staticmethod
    def card_size(scale, square):
        """(thumb width, thumb height, text block height) of a card at a library size."""
        if square:
            return round(200 * scale), round(200 * scale), round(68 * scale)
        return round(316 * scale), round(178 * scale), round(59 * scale)

    @staticmethod
    def row_height(scale):
        return round(42 * (1 + scale) / 2)     # rows shrink less than cards

    def sizeHint(self, option, index):
        e = index.data(Qt.UserRole)
        s = self.view.lib.scale()
        if self.mode == "card":
            tw, th, text_h = self.card_size(s, self.is_square(e))
            return QSize(tw + 20, th + 20 + text_h)
        if self.mode == "listcol":
            return QSize(self.view.col_width(), self.row_height(s))
        if e and e["kind"] == "header":
            return QSize(option.rect.width(), self.row_height(s) + 2)
        return QSize(option.rect.width(), self.row_height(s))

    def paint(self, p, option, index):
        e = index.data(Qt.UserRole)
        if not e:
            return
        p.save()
        p.setRenderHint(QPainter.Antialiasing)
        hover = bool(option.state & QStyle.State_MouseOver)
        sel = bool(option.state & QStyle.State_Selected)
        if self.mode == "card":
            self._card(p, QRectF(option.rect), e, hover, sel)
        else:
            self._row(p, QRectF(option.rect), e, hover, sel)
        p.restore()

    # -- helpers
    def _state(self, e):
        lib = self.view.lib
        ref = e.get("ref") or ""
        playing = e["kind"] in ("media", "stream") and ref == lib.engine.current
        watched = e["kind"] == "media" and lib.store.watched(ref)
        prog = lib.store.progress(ref) if e["kind"] == "media" else None
        return playing, watched, prog

    def _thumb(self, e, w, h):
        lib = self.view.lib
        src = e.get("image") or e.get("thumb")
        if not src:
            return None
        if e.get("image"):
            pm = QPixmap(e["image"])
            if pm.isNull():
                return None
            return pm.scaled(w, h, Qt.KeepAspectRatioByExpanding, Qt.SmoothTransformation)
        dpr = self.view.devicePixelRatioF()
        return lib.media.thumb(src, w, h, dpr)

    def _row(self, p, r, e, hover, sel):
        f = theme.font(14)
        p.setFont(f)
        if e["kind"] == "header":
            p.setFont(theme.font(16))
            p.setPen(c("text"))
            p.drawText(r.adjusted(0, 10, -30, 0), Qt.AlignVCenter | Qt.AlignLeft, e["name"])
            p.drawPixmap(QRectF(r.right() - 24, r.center().y() - 7, 24, 24).toRect(), icons.pixmap("chevron-right", 24))
            return
        if e["kind"] == "empty":
            p.setPen(c("text_3"))
            p.drawText(r, Qt.AlignVCenter | Qt.AlignLeft | Qt.TextWordWrap, e["name"])
            return
        if hover or sel:
            p.setPen(Qt.NoPen)
            p.setBrush(c("hover"))
            p.drawRoundedRect(r.adjusted(-4, 1, 4, -1), 6, 6)
        playing, watched, prog = self._state(e)
        box = QRectF(r.left(), r.center().y() - 16, 32, 32)
        if e["kind"] in ("media",):
            pm = self._thumb(e, 32, 32)
            path = QPainterPath()
            path.addRoundedRect(box, 2, 2)
            p.setPen(Qt.NoPen)
            p.setBrush(c("thumb"))
            p.drawPath(path)
            if pm:
                p.setClipPath(path)
                p.drawPixmap(box.toRect(), pm)
                p.setClipping(False)
            elif e.get("square"):
                p.drawPixmap(box.adjusted(4, 4, -4, -4).toRect(), icons.pixmap("music", 24))
            if playing:
                p.setBrush(QColor(0, 0, 0, 140))
                p.drawRoundedRect(box, 2, 2)
                p.drawPixmap(box.adjusted(4, 4, -4, -4).toRect(), icons.pixmap("playback", 24, None, "active"))
            frac = 1.0 if watched else prog
            if frac:
                p.setBrush(c("accent"))
                p.drawRect(QRectF(box.left(), box.bottom() - 3, max(3.0, 32 * frac), 3))
        elif e["kind"] == "add":
            p.drawPixmap(QRectF(r.left() + 4, r.center().y() - 12, 24, 24).toRect(), icons.pixmap("add", 24))
        else:
            name = e.get("icon") or "folder"
            if e.get("image"):
                pm = self._thumb(e, 32, 32)
                if pm:
                    path = QPainterPath()
                    path.addRoundedRect(box, 2, 2)
                    p.setClipPath(path)
                    p.drawPixmap(box.toRect(), pm)
                    p.setClipping(False)
            else:
                p.drawPixmap(QRectF(r.left() + 4, r.center().y() - 12, 24, 24).toRect(), icons.pixmap(name, 24))
        x = r.left() + 42
        dim = watched and not playing
        p.setPen(c("accent") if playing else (c("text_2") if dim or e["kind"] == "add" else c("text")))
        if dim:
            p.setOpacity(theme.T.p["dim"] + 0.2)
        text = e["name"]
        if e.get("sub") and e["kind"] == "media" and e.get("square"):
            text = f"{e['name']} - {e['sub']}"
        right = r.right() - 4
        if e.get("duration") and self.mode == "row" and self.view.show_durations:
            dur = fmt_short(e["duration"])
            dw = QFontMetrics(f).horizontalAdvance(dur)
            p.save()
            p.setPen(c("text_2"))
            p.drawText(QRectF(right - dw, r.top(), dw + 2, r.height()), Qt.AlignVCenter, dur)
            p.restore()
            right -= dw + 12
        p.drawText(QRectF(x, r.top(), right - x, r.height()), Qt.AlignVCenter | Qt.AlignLeft, elide(text, f, right - x))
        p.setOpacity(1)

    def _card(self, p, r, e, hover, sel):
        square = self.is_square(e)
        s = self.view.lib.scale()
        tw, th, _ = self.card_size(s, square)
        box = QRectF(r.left() + 10, r.top() + 10, tw, th)
        playing, watched, prog = self._state(e)
        path = QPainterPath()
        path.addRoundedRect(box, 12, 12)
        p.setPen(Qt.NoPen)
        p.setBrush(c("card") if e["kind"] == "media" else c("hover"))
        p.drawPath(path)
        pm = self._thumb(e, tw, th) if e["kind"] in ("media", "album", "playlist") else None
        if pm:
            p.setClipPath(path)
            p.drawPixmap(box.toRect(), pm)
            p.setClipping(False)
        else:
            name = {"add": "add", "folder": "folder", "category": e.get("icon")}.get(e["kind"]) or e.get("icon") or \
                ("music" if e.get("square") else "video")
            p.drawPixmap(QRectF(box.center().x() - 24, box.center().y() - 24, 48, 48).toRect(), icons.pixmap(name, 48))
        if hover and e["kind"] not in ("media", "stream"):
            p.setBrush(QColor(255, 255, 255, 18))      # folders, albums… open on click: just light up
            p.drawPath(path)
        elif hover:
            p.setBrush(QColor(0, 0, 0, 90))
            p.drawPath(path)
            p.setBrush(c("accent"))
            p.drawRoundedRect(QRectF(box.center().x() - 27, box.center().y() - 14, 54, 28), 6, 6)
            p.drawPixmap(QRectF(box.center().x() - 12, box.center().y() - 12, 24, 24).toRect(),
                         icons.pixmap("play", 24, "#1b1b1b"))
        frac = 1.0 if watched else prog
        if frac and e["kind"] == "media":
            p.setClipPath(path)
            p.setBrush(c("accent"))
            p.drawRect(QRectF(box.left(), box.bottom() - 4, max(8.0, tw * frac), 4))
            p.setClipping(False)
        if watched and not playing:
            p.setOpacity(theme.T.p["dim"] + 0.15)
        f = theme.font(max(12, round(16 * (1 + s) / 2)))
        p.setFont(f)
        p.setPen(c("accent") if playing else c("text"))
        ty = box.bottom() + round(20 * s)
        lh = max(18, round(20 * s))
        p.drawText(QRectF(box.left(), ty, tw, lh), Qt.AlignLeft | Qt.AlignVCenter, elide(e["name"], f, tw))
        if e.get("sub") and square:
            p.setPen(c("text_2"))
            p.drawText(QRectF(box.left(), ty + round(29 * s), tw, lh), Qt.AlignLeft | Qt.AlignVCenter,
                       elide(e["sub"], f, tw))
        p.setOpacity(1)


class EntryView(QListWidget):
    """A list or grid of entries with drag-to-queue, context menus and activation."""

    def __init__(self, lib, mode="row", horizontal=False):
        super().__init__()
        self.lib = lib
        self.mode = mode
        self.show_durations = False
        self.setMouseTracking(True)
        self.setItemDelegate(EntryDelegate(self, mode))
        self.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.setDragEnabled(True)
        self.setDragDropMode(QAbstractItemView.DragOnly)
        self.setVerticalScrollMode(QAbstractItemView.ScrollPerPixel)
        self.setHorizontalScrollMode(QAbstractItemView.ScrollPerPixel)
        self.setUniformItemSizes(mode != "row")
        self.viewport().setAutoFillBackground(False)
        self.setFrameShape(QListWidget.NoFrame)
        if mode in ("card", "listcol"):
            self.setViewMode(QListView.IconMode if mode == "card" else QListView.ListMode)
            self.setFlow(QListView.LeftToRight)
            self.setWrapping(not horizontal)
            self.setResizeMode(QListView.Adjust)
            self.setMovement(QListView.Static)
            self.setSpacing(0)
        if horizontal:
            self.setWrapping(False)
            self.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded)
            self.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        else:
            self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.itemClicked.connect(self._clicked)
        self.itemDoubleClicked.connect(self._double)
        self.setContextMenuPolicy(Qt.CustomContextMenu)
        self.customContextMenuRequested.connect(self._menu)

    def repaint_items(self, *_):
        try:
            self.viewport().update()
        except RuntimeError:
            pass

    def col_width(self):
        cols = max(1, self.viewport().width() // 400)
        return self.viewport().width() // cols - 4

    def resizeEvent(self, e):
        super().resizeEvent(e)
        if self.mode == "listcol":
            self.doItemsLayout()

    def set_entries(self, entries):
        self.clear()
        if self.mode == "card":
            # Equal cell sizes only when every card has the same shape: on pages mixing videos
            # and songs (Recent, Favorites, search) Qt would squeeze them all into one size.
            self.setUniformItemSizes(len({EntryDelegate.is_square(e) for e in entries}) <= 1)
        for e in entries:
            it = QListWidgetItem(e["name"])
            it.setData(Qt.UserRole, e)
            flags = Qt.ItemIsEnabled
            if e["kind"] not in ("header", "empty", "add", "category"):
                flags |= Qt.ItemIsSelectable | Qt.ItemIsDragEnabled
            it.setFlags(flags)
            self.addItem(it)

    def mimeData(self, items):
        md = QMimeData()
        out = []
        for it in items:
            e = it.data(Qt.UserRole)
            out.extend(self.lib.model.playable(e))
        md.setData(MIME, QByteArray(json.dumps(out).encode()))
        md.setUrls([QUrl.fromLocalFile(i) if not is_url(i) else QUrl(i) for i in out[:200]])
        return md

    def event(self, e):
        if e.type() == QEvent.ShortcutOverride and wants_list_key(e):
            e.accept()
            return True
        return super().event(e)

    def mouseMoveEvent(self, e):
        # the hand over anything that opens or plays on click
        it = self.itemAt(e.pos())
        hand = it is not None and (it.data(Qt.UserRole) or {}).get("kind") != "empty"
        if hand != getattr(self, "_hand", False):
            self._hand = hand
            self.viewport().setCursor(Qt.PointingHandCursor) if hand else self.viewport().unsetCursor()
        super().mouseMoveEvent(e)

    def leaveEvent(self, e):
        self._hand = False
        self.viewport().unsetCursor()
        super().leaveEvent(e)

    def updateGeometries(self):
        super().updateGeometries()
        # Qt steps a grid by a whole row of cards per wheel line (3 rows a notch); use ~120 px
        self.verticalScrollBar().setSingleStep(40)
        self.horizontalScrollBar().setSingleStep(40)

    def wheelEvent(self, e):
        d = e.angleDelta()
        if e.modifiers() & Qt.ControlModifier:
            if d.y():
                self.lib.zoom(1 if d.y() > 0 else -1)
            e.accept()
            return
        if self.flow() == QListView.LeftToRight and not self.isWrapping():
            # A home-page row: the wheel scrolls the page; sideways scrolling (or Shift+wheel) moves the row.
            dx = d.x() or (d.y() if e.modifiers() & Qt.ShiftModifier else 0)
            if not dx:
                e.ignore()
                return
            bar = self.horizontalScrollBar()
            bar.setValue(bar.value() - dx)
            e.accept()
            return
        super().wheelEvent(e)

    def _clicked(self, it):
        e = it.data(Qt.UserRole)
        if QApplication.keyboardModifiers() & (Qt.ControlModifier | Qt.ShiftModifier):
            return
        # One click opens or plays. A double click out of habit must not open whatever the
        # second click lands on once the first one has changed the page.
        if time.monotonic() - self.lib.acted < QApplication.doubleClickInterval() / 1000:
            return
        self.lib.acted = time.monotonic()
        if e["kind"] in ("header",):
            self.lib.navigate(e["loc"])
        elif e["kind"] == "add":
            self.lib.add_action(e["ref"], self)
        elif e.get("loc") and self.lib.single_click_opens:
            self.lib.navigate(e["loc"])
        elif self.lib.single_click_plays and e["kind"] in ("media", "stream"):
            self.lib.activate(e, self)

    def _double(self, it):
        e = it.data(Qt.UserRole)
        if e.get("loc"):
            if not self.lib.single_click_opens:
                self.lib.navigate(e["loc"])
        elif e["kind"] in ("media", "stream"):
            if not self.lib.single_click_plays:
                self.lib.activate(e, self)

    def keyPressEvent(self, e):
        if e.key() in (Qt.Key_Return, Qt.Key_Enter) and self.currentItem():
            ent = self.currentItem().data(Qt.UserRole)
            if ent.get("loc"):
                self.lib.navigate(ent["loc"])
            else:
                self.lib.activate(ent, self)
        elif e.key() == Qt.Key_Backspace:
            self.lib.go_up()
        else:
            super().keyPressEvent(e)

    def _menu(self, pos):
        it = self.itemAt(pos)
        if not it:
            return
        if not it.isSelected():
            self.clearSelection()
            it.setSelected(True)
        entries = [i.data(Qt.UserRole) for i in self.selectedItems()]
        self.lib.context_menu(entries, self.viewport().mapToGlobal(pos), self)


# --- shared navigation state + actions ----------------------------------------------------
class Library(QObject):
    """Navigation state shared by the compact sidebar and the full library."""
    locationChanged = pyqtSignal()

    def __init__(self, window):
        super().__init__()
        self.win = window
        self.store = window.store
        self.engine = window.engine
        self.media = window.media
        self.model = LibraryModel(self.store, self.media)
        self.loc = ("home",)
        self.back_stack, self.fwd_stack = [], []
        self.query = ""
        self.single_click_opens = True
        self.single_click_plays = True
        self.acted = 0.0                    # when a click last opened or played something
        self.model.changed.connect(self.locationChanged.emit)
        self.store.libraryChanged.connect(lambda _w: self.locationChanged.emit())

    def page_key(self):
        return "/".join(str(x) for x in self.loc[:2])

    def set_home_section(self, title, shown):
        hidden = [t for t in self.store["home_hidden"] if t != title] + ([] if shown else [title])
        self.store["home_hidden"] = hidden
        self.locationChanged.emit()

    def customize_home(self):
        from .dialogs import HomeDialog
        HomeDialog(self).exec_()

    def home_menu(self, title=None):
        items = [MenuItem(f"Hide “{title}”", lambda: self.set_home_section(title, False), icon="ui")] if title else []
        return items + [MenuItem("Customize Home…", self.customize_home, icon="paint")]

    def size_name(self):
        name = self.store["lib_size"]
        return name if name in LIB_SIZES else "Large"

    def scale(self):
        return LIB_SIZES[self.size_name()]

    def set_size(self, name):
        if name in LIB_SIZES and name != self.size_name():
            self.store["lib_size"] = name
            self.locationChanged.emit()

    def zoom(self, step):
        """Ctrl+wheel in the library: one size bigger (step 1) or smaller (-1)."""
        names = list(LIB_SIZES)
        i = names.index(self.size_name()) - step
        self.set_size(names[max(0, min(len(names) - 1, i))])

    def view_mode(self, default="grid"):
        return self.store.view(self.page_key(), "view", default)

    def set_view_mode(self, mode):
        self.store.set_view(self.page_key(), "view", mode)
        self.locationChanged.emit()

    def sort_mode(self):
        return self.store.view(self.page_key(), "sort", DEFAULT_SORT)

    def set_sort(self, how):
        self.store.set_view(self.page_key(), "sort", how)
        self.locationChanged.emit()

    def navigate(self, loc, record=True):
        loc = tuple(loc)
        if loc == self.loc:
            return
        if record:
            self.back_stack.append(self.loc)
            self.fwd_stack.clear()
        self.loc = loc
        self.query = ""
        if loc[:2] == ("network", "podcasts") and len(loc) == 3 and loc[2] not in self.model.feeds:
            self.model.load_feed(loc[2])
        if loc[0] in ("videos", "music") and len(loc) >= 2 and os.path.isdir(str(loc[-1])):
            self.store.visit_folder(loc[-1])
        if loc[0] == "playlists" and len(loc) == 2 and loc[1] != "favorites":
            self.store.visit_playlist(loc[1])
        self.locationChanged.emit()

    def back(self):
        if self.back_stack:
            self.fwd_stack.append(self.loc)
            self.loc = self.back_stack.pop()
            self.query = ""
            self.locationChanged.emit()

    def forward(self):
        if self.fwd_stack:
            self.back_stack.append(self.loc)
            self.loc = self.fwd_stack.pop()
            self.query = ""
            self.locationChanged.emit()

    def go_up(self):
        crumbs = self.model.crumbs(self.loc)
        if len(crumbs) > 1:
            self.navigate(crumbs[-2][1])

    def set_query(self, q):
        self.query = q
        self.locationChanged.emit()

    def entries(self):
        if self.query:
            return self.model.search(self.loc, self.query)
        ents = self.model.entries(self.loc)
        if self.loc[0] in ("videos", "music", "playlists", "recent") and self.loc != ("recent",):
            ents = self.model.sort(ents, self.sort_mode())
        return ents

    def count(self, entries):
        return len([e for e in entries if e["kind"] not in ("add", "header", "empty")])

    def show_folder(self, path):
        """Open the library at the folder of a file (title-bar folder chip)."""
        folder = path if os.path.isdir(path) else os.path.dirname(path)
        for root in self.store["video_folders"]:
            if folder == root or folder.startswith(root + os.sep):
                self.navigate(("videos", folder))
                return
        for root in self.store["music_folders"]:
            if folder == root or folder.startswith(root + os.sep):
                self.navigate(("music", "songs", folder))
                return
        # not in the library yet: show it anyway under Videos
        self.navigate(("videos", folder))

    # --- actions -----------------------------------------------------------------
    def activate(self, entry, view=None):
        """Play an entry: the clicked file plus its siblings so "next" continues."""
        items = []
        index = 0
        names = {}
        if view is not None and entry["kind"] in ("media", "stream"):
            for i in range(view.count()):
                e = view.item(i).data(Qt.UserRole)
                if e["kind"] in ("media", "stream") and e.get("items"):
                    if e is entry or e["ref"] == entry["ref"]:
                        index = len(items)
                    items.append(e["ref"])
                    names.update(e.get("names") or {})
        if not items:
            items = self.model.playable(entry)
            names = entry.get("names") or {}
        if items:
            self.win.play(items, index, names=names)

    def add_action(self, what, anchor):
        w = self.win
        if what in ("videos", "music"):
            folder = FileDialog.getExistingDirectory(w, f"Add a folder to {what.title()}", os.path.expanduser("~"),
                                                      options=w.dialog_options())
            if folder:
                self.store.add_folder("music" if what == "music" else "video", folder)
                self.navigate(("music", "songs") if what == "music" else ("videos",))
        elif what == "network":
            w.open_network()
        elif what == "playlist":
            w.new_playlist()
        elif what in ("podcast", "radio"):
            label = "Podcast feed URL (RSS):" if what == "podcast" else "Stream URL:"
            url, ok = QInputDialog.getText(w, "Add Podcast" if what == "podcast" else "Add Radio Station", label)
            if ok and url.strip():
                name = url.strip()
                if what == "radio":
                    n, ok2 = QInputDialog.getText(w, "Add Radio Station", "Name:", text=display_name(name))
                    name = n.strip() if ok2 and n.strip() else name
                self.store.add_feed("podcasts" if what == "podcast" else "radio", url.strip(), name)
                if what == "podcast":
                    self.model.load_feed(url.strip())

    def context_menu(self, entries, gpos, view):
        w = self.win
        e = entries[0]
        items = [i for en in entries for i in self.model.playable(en)]
        kind = e["kind"]
        if kind == "add" or kind in ("header", "empty", "category"):
            return
        menu = []
        if kind in ("media", "stream"):
            ref = e["ref"]
            watched = self.store.watched(ref)
            menu = [MenuItem("Play", lambda: self.activate(e, view), icon="playback"),
                    MenuItem("Mark as Unwatched" if watched else "Mark as Watched",
                             lambda: [self.store.mark_watched(r, not watched) for r in items], icon="check",
                             enabled=kind == "media"),
                    MenuItem("Remove from Favorites" if self.store.is_favorite(ref) else "Favorite",
                             lambda: [self.store.toggle_favorite(r) for r in items], icon="favorite"),
                    MenuItem("Stream", lambda: w.open_stream_out(items), icon="streaming"),
                    MenuItem("Convert", lambda: w.open_convert(items), icon="swap"),
                    MenuItem("Add to Queue", lambda: w.enqueue(items), icon="queue"),
                    MenuItem("Add to Playlist", icon="playlist", submenu=lambda: w.playlist_submenu(items)),
                    MenuItem("Show in File Explorer", lambda: w.show_in_folder(ref), icon="folder",
                             enabled=not is_url(ref)),
                    MenuItem("Go to Source", lambda: self.show_folder(ref), icon="arrow",
                             icon_variant="direction=right,two-tone=yes", enabled=not is_url(ref)),
                    MenuItem("Add Subtitle Tracks", lambda: w.open_add_subtitles(ref), icon="subtitles",
                             enabled=kind == "media" and not is_audio(ref)),
                    MenuItem("Add Audio Tracks", lambda: w.add_audio_tracks(ref), icon="audio", enabled=kind == "media"),
                    MenuItem("Properties & Metadata", lambda: w.show_properties(ref), icon="info")]
            if e.get("in_playlist") or (self.loc[0] == "playlists" and len(self.loc) == 2):
                pid = self.loc[1]
                menu.append(sep())
                if pid == "favorites":
                    menu.append(MenuItem("Remove from Favorites", lambda: [self.store.toggle_favorite(r) for r in items]))
                else:
                    menu.append(MenuItem("Remove from Playlist", lambda: self._remove_from_playlist(pid, items)))
            if self.loc[0] == "network":
                menu.append(sep())
                menu.append(MenuItem("Remove", lambda: self._remove_network(e)))
        elif kind == "folder":
            path = e["ref"]
            pinned = self.store.is_pinned("folder", path)
            menu = [MenuItem("Play", lambda: w.play(items), icon="playback"),
                    MenuItem("Shuffle", lambda: w.play(items, shuffle=True), icon="shuffle"),
                    MenuItem("Add to Queue", lambda: w.enqueue(items), icon="queue"),
                    MenuItem("Add to Playlist", icon="playlist", submenu=lambda: w.playlist_submenu(items)),
                    MenuItem("Unpin" if pinned else "Pin to Sidebar",
                             lambda: self.store.unpin("folder", path) if pinned else
                             self.store.pin("folder", path, e["name"]), icon="link"),
                    MenuItem("Add Subtitle Tracks", lambda: w.open_add_subtitles(path, scope="folder"),
                             icon="subtitles"),
                    MenuItem("Show in File Explorer", lambda: w.show_in_folder(path), icon="folder")]
            if e.get("removable"):
                section = "music" if self.loc[0] == "music" else "video"
                menu += [sep(), MenuItem("Remove from Library", lambda: self.store.remove_folder(section, path),
                                         icon="remove", icon_variant="")]
        elif kind in ("playlist", "favorites", "netlist", "album", "artist", "genre", "podcast"):
            pid = e.get("ref")
            menu = [MenuItem("Play", lambda: w.play(items), icon="playback"),
                    MenuItem("Shuffle", lambda: w.play(items, shuffle=True), icon="shuffle"),
                    MenuItem("Add to Queue", lambda: w.enqueue(items), icon="queue")]
            if kind == "playlist":
                pinned = self.store.is_pinned("playlist", pid)
                menu = [MenuItem("Play", lambda: w.play(items), icon="playback"),
                        MenuItem("Remove from Favorites" if self.store.is_favorite("playlist:" + pid) else "Favorite",
                                 lambda: self.store.toggle_favorite("playlist:" + pid), icon="favorite"),
                        MenuItem("Shuffle", lambda: w.play(items, shuffle=True), icon="shuffle"),
                        MenuItem("Export Playlist", lambda: w.export_playlist(pid), icon="save"),
                        MenuItem("Add to Queue", lambda: w.enqueue(items), icon="queue"),
                        MenuItem("Add Subtitle Tracks", lambda: w.open_add_subtitles(None, scope="playlist", items=items),
                                 icon="subtitles"),
                        MenuItem("Add Audio Tracks", lambda: w.add_audio_tracks(items[0] if items else None),
                                 icon="audio", enabled=bool(items)),
                        MenuItem("Properties & Metadata", lambda: w.playlist_properties(pid), icon="info"),
                        sep(),
                        MenuItem("Unpin" if pinned else "Pin to Sidebar",
                                 lambda: self.store.unpin("playlist", pid) if pinned else
                                 self.store.pin("playlist", pid, e["name"])),
                        MenuItem("Rename…", lambda: self._rename_playlist(pid)),
                        MenuItem("Delete", lambda: self._delete_playlist(pid))]
            elif kind in ("netlist", "podcast"):
                menu += [sep(), MenuItem("Remove", lambda: self._remove_network(e))]
        if menu:
            PopupMenu(menu, w).popup(gpos)

    def _remove_from_playlist(self, pid, items):
        pl = self.store.playlist(pid)
        if pl:
            self.store.update_playlist(pid, items=[i for i in pl["items"] if i not in items])

    def _rename_playlist(self, pid):
        pl = self.store.playlist(pid)
        name, ok = QInputDialog.getText(self.win, "Rename Playlist", "Name:", text=pl["name"])
        if ok and name.strip():
            self.store.update_playlist(pid, name=name.strip())

    def _delete_playlist(self, pid):
        pl = self.store.playlist(pid)
        if self.win.confirm(f"Delete the playlist “{pl['name']}”?", "The files themselves are not deleted."):
            self.store.remove_playlist(pid)
            if self.loc[:2] == ("playlists", pid):
                self.navigate(("playlists",))

    def _remove_network(self, e):
        if e["kind"] == "netlist":
            self.store.remove_network("lists", e["ref"])
        elif e["kind"] == "podcast":
            self.store.remove_network("podcasts", e["ref"])
        elif e.get("radio"):
            self.store.remove_network("radio", e["ref"])
        else:
            self.store.remove_network("streams", e["ref"])

    def folder_menu(self, anchor):
        """The header "⋯" menu: Add File, Add Folder, Edit Subtitle/Audio Tracks, Resync, Info."""
        w = self.win
        loc = self.loc
        folder = None
        if loc[0] == "videos" and len(loc) > 1:
            folder = loc[1]
        elif loc[0] == "music" and len(loc) > 2 and loc[1] == "songs":
            folder = loc[2]
        menu = [MenuItem("Add File", lambda: self._add_file(folder), icon="add", icon_variant=""),
                MenuItem("Add Folder", lambda: self.add_action("music" if loc[0] == "music" else "videos", anchor),
                         icon="new folder"),
                MenuItem("Edit Subtitle Tracks", lambda: w.open_add_subtitles(folder, scope="folder"),
                         icon="subtitles", enabled=bool(folder)),
                MenuItem("Edit Audio Tracks", lambda: w.add_audio_tracks(None, folder=folder), icon="audio",
                         enabled=bool(folder)),
                MenuItem("Resync", self.resync, icon="refresh"),
                MenuItem("Info", lambda: w.folder_info(folder or loc), icon="info")]
        if loc[0] == "recent":
            menu.append(sep())
            menu.append(MenuItem("Clear Recent", self.store.clear_history))
        if loc == ("home",):
            menu = self.home_menu()
        return menu

    def _add_file(self, folder):
        w = self.win
        if self.loc[0] == "playlists" and len(self.loc) == 2 and self.loc[1] != "favorites":
            paths, _ = FileDialog.getOpenFileNames(w, "Add to playlist", os.path.expanduser("~"),
                                                    options=w.dialog_options())
            if paths:
                self.store.add_to_playlist(self.loc[1], paths)
            return
        w.open_dialog("file", enqueue=True, start=folder)

    def resync(self):
        self.model.drop_index()
        self.media._pix.clear()
        self.model.rescan()
        self.locationChanged.emit()
        self.win.notify("Library refreshed")

    def sort_menu(self, anchor):
        cur = self.sort_mode()
        items = [MenuItem(s, lambda s=s: self.set_sort(s), checked=(s == cur)) for s in SORTS]
        m = PopupMenu(items, self.win, width=257)
        m.popup_below(anchor)

    def view_menu(self, anchor, default="grid"):
        cur = self.view_mode(default)
        size = self.size_name()
        items = [MenuItem("Grid View", lambda: self.set_view_mode("grid"), icon="grid", icon_variant="",
                          checked=cur == "grid"),
                 MenuItem("List View", lambda: self.set_view_mode("list"), icon="list", icon_variant="",
                          checked=cur == "list"),
                 sep()]
        items += [MenuItem(f"{n} Items", lambda n=n: self.set_size(n), checked=n == size,
                           shortcut="Ctrl+Scroll" if n == "Large" else "") for n in LIB_SIZES]
        PopupMenu(items, self.win, width=257).popup_below(anchor)

    def add_item_menu(self, anchor, below=False):
        """The rail's "+" / "Add item": pin this page or add things."""
        w = self.win
        loc = self.loc
        can_pin = (loc[0] in ("videos", "music") and len(loc) > 1) or (loc[0] == "playlists" and len(loc) == 2)
        items = [MenuItem("Pin This Page", self.pin_current, icon="link", enabled=can_pin),
                 sep(),
                 MenuItem("Add a Video Folder", lambda: self.add_action("videos", anchor), icon="new folder"),
                 MenuItem("Add a Music Folder", lambda: self.add_action("music", anchor), icon="new folder"),
                 MenuItem("Add Network Stream", w.open_network, icon="streaming"),
                 MenuItem("Create Playlist", w.new_playlist, icon="playlist")]
        m = PopupMenu(items, self.win)
        if below:
            m.popup_below(anchor)
        else:
            m.popup(anchor.mapToGlobal(QPoint(anchor.width() + 6, 0)))

    def pin_current(self):
        loc = self.loc
        if loc[0] == "videos" and len(loc) > 1:
            self.store.pin("folder", loc[1], os.path.basename(loc[1]))
        elif loc[0] == "music" and len(loc) > 2:
            self.store.pin("folder", loc[-1], os.path.basename(str(loc[-1])))
        elif loc[0] == "playlists" and len(loc) == 2:
            pl = self.store.playlist(loc[1])
            if pl:
                self.store.pin("playlist", loc[1], pl["name"])

    def open_pin(self, pin):
        if pin["kind"] == "folder":
            self.show_folder(pin["ref"])
        elif pin["kind"] == "playlist":
            self.navigate(("playlists", pin["ref"]))


# --- header pieces --------------------------------------------------------------------------
class Breadcrumb(QWidget):
    """Clickable "Videos > Shows > The Wand > S01" path. Long paths collapse to "... > last two"."""

    def __init__(self, lib, size=16, compact=False):
        super().__init__()
        self.lib = lib
        self.size = size
        self.compact = compact
        self.setFixedHeight(28)
        self.setSizePolicy(self.sizePolicy().Expanding, self.sizePolicy().Fixed)
        self.setMouseTracking(True)
        self._hits = []
        self._hover = -1

    def _parts(self):
        crumbs = self.lib.model.crumbs(self.lib.loc)
        if self.lib.query:
            crumbs = crumbs + [(f"Search: {self.lib.query}", self.lib.loc)]
        f = theme.font(self.size)
        fm = QFontMetrics(f)
        sep_w = 20
        total = sum(fm.horizontalAdvance(t) for t, _ in crumbs) + sep_w * (len(crumbs) - 1)
        if (self.compact and len(crumbs) > 2) or total > self.width():
            keep = crumbs[-2:] if len(crumbs) > 2 else crumbs
            parents = crumbs[:-2]
            return [("…", parents[-1][1] if parents else None)] + keep
        return crumbs

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        f = theme.font(self.size)
        fm = QFontMetrics(f)
        p.setFont(f)
        x = 0
        self._hits = []
        parts = self._parts()
        for i, (text, loc) in enumerate(parts):
            last = i == len(parts) - 1
            font = theme.font(self.size, theme.SEMIBOLD) if last and not self.compact and len(parts) > 1 else f
            ffm = QFontMetrics(font)
            w = ffm.horizontalAdvance(text) + 2
            avail = self.width() - x
            if last and w > avail:
                text = elide(text, font, avail - 2)
                w = ffm.horizontalAdvance(text) + 2
            rect = QRectF(x, 0, w, self.height())
            if last:
                p.setFont(font)
                p.setPen(c("text_hi") if len(parts) > 1 else c("text"))
            else:
                p.setFont(f)
                p.setPen(c("text_hi") if i == self._hover else c("text"))
            p.drawText(rect, Qt.AlignVCenter | Qt.AlignLeft, text)
            self._hits.append((rect, loc))
            x += w
            if not last:
                # small ▸ separator
                p.setPen(Qt.NoPen)
                p.setBrush(c("text_2"))
                cy = self.height() / 2
                from PyQt5.QtGui import QPolygonF
                from PyQt5.QtCore import QPointF
                tri = QPolygonF([QPointF(x + 7, cy - 3.5), QPointF(x + 12, cy), QPointF(x + 7, cy + 3.5)])
                p.drawPolygon(tri)
                x += 20

    def mouseMoveEvent(self, e):
        h = -1
        for i, (r, loc) in enumerate(self._hits[:-1]):
            if r.contains(QPointF(e.pos())) and loc:
                h = i
        if h != self._hover:
            self._hover = h
            self.setCursor(Qt.PointingHandCursor if h >= 0 else Qt.ArrowCursor)
            self.update()

    def leaveEvent(self, e):
        self._hover = -1
        self.update()

    def mouseReleaseEvent(self, e):
        for r, loc in self._hits[:-1]:
            if r.contains(QPointF(e.pos())) and loc:
                self.lib.navigate(loc)
                return


class SearchBox(QLineEdit):
    def __init__(self, lib, width=325):
        super().__init__()
        self.lib = lib
        self.setPlaceholderText("Search")
        self.setFixedHeight(34)
        if width:
            self.setFixedWidth(width)
        self.setTextMargins(30, 0, 0, 0)
        self._timer = QTimer(self, singleShot=True, interval=250, timeout=lambda: self.lib.set_query(self.text()))
        self.textChanged.connect(lambda _t: self._timer.start())
        self.setFocusPolicy(Qt.ClickFocus)
        self.setStyleSheet("QLineEdit { padding-left: 8px; }")

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setPen(Qt.NoPen)
        p.setBrush(c("field_2"))
        p.drawRoundedRect(QRectF(self.rect()), 6, 6)
        p.end()
        super().paintEvent(e)
        p = QPainter(self)
        p.drawPixmap(8, (self.height() - 24) // 2, icons.pixmap("search", 24))

    def event(self, e):
        # Esc clears the search; without this the window's Esc shortcut would take it
        if e.type() == QEvent.ShortcutOverride and e.key() == Qt.Key_Escape:
            e.accept()
            return True
        return super().event(e)

    def keyPressEvent(self, e):
        if e.key() == Qt.Key_Escape:
            self.clear()
            self.clearFocus()
        else:
            super().keyPressEvent(e)


# --- compact sidebar (next to the video) -------------------------------------------------------
class Rail(QWidget):
    """50 px icon rail: home, videos, music, network, playlists, recent, pins, +."""

    def __init__(self, lib, with_labels=False):
        super().__init__()
        self.lib = lib
        self.setFixedWidth(50)
        self.lay = QVBoxLayout(self)
        self.lay.setContentsMargins(8, 8, 8, 8)
        self.lay.setSpacing(14)
        self.buttons = {}
        self.pin_buttons = []
        for key, label, icon in SECTIONS:
            b = IconButton(icon, label, box=(32, 32))
            b.clicked.connect(lambda _=False, k=key: lib.navigate((k,)))
            self.lay.addWidget(b, 0, Qt.AlignHCenter)
            self.buttons[key] = b
        self.pins_lay = QVBoxLayout()
        self.pins_lay.setSpacing(14)
        self.lay.addLayout(self.pins_lay)
        self.add = IconButton("add", "Pin this page or add to the library", box=(32, 32))
        self.add.clicked.connect(lambda: lib.add_item_menu(self.add))
        self.lay.addWidget(self.add, 0, Qt.AlignHCenter)
        self.lay.addStretch(1)
        lib.locationChanged.connect(self.sync)
        lib.store.libraryChanged.connect(lambda w: w == "pinned" and self.rebuild_pins())
        self.rebuild_pins()
        self.sync()

    def rebuild_pins(self):
        for b in self.pin_buttons:
            b.deleteLater()
        self.pin_buttons = []
        for pin in self.lib.store["pinned"]:
            icon = "folder" if pin["kind"] == "folder" else "playlist"
            b = IconButton(icon, pin["name"], box=(32, 32))
            if pin["kind"] == "playlist":
                pl = self.lib.store.playlist(pin["ref"])
                if pl and pl.get("thumb"):
                    b.thumb = pl["thumb"]
            b.clicked.connect(lambda _=False, p=pin: self.lib.open_pin(p))
            b.setContextMenuPolicy(Qt.CustomContextMenu)
            b.customContextMenuRequested.connect(
                lambda pos, p=pin, b=b: PopupMenu([MenuItem("Unpin", lambda: self.lib.store.unpin(p["kind"], p["ref"]))],
                                                  self).popup(b.mapToGlobal(pos)))
            self.pins_lay.addWidget(b, 0, Qt.AlignHCenter)
            self.pin_buttons.append(b)

    def sync(self):
        sec = self.lib.loc[0]
        for key, b in self.buttons.items():
            b.set_active(key == sec)

    def paintEvent(self, e):
        p = QPainter(self)
        p.fillRect(self.rect(), c("rail"))


class CompactLibrary(QWidget):
    """The library sidebar beside the video (rail 50 + panel 377, #0e0e0e at 95%)."""

    def __init__(self, lib):
        super().__init__()
        self.lib = lib
        self.setFixedWidth(427)
        h = QHBoxLayout(self)
        h.setContentsMargins(0, 0, 0, 0)
        h.setSpacing(0)
        self.rail = Rail(lib)
        h.addWidget(self.rail)
        panel = QWidget()
        h.addWidget(panel, 1)
        v = QVBoxLayout(panel)
        v.setContentsMargins(20, 20, 20, 0)
        v.setSpacing(0)
        top = QHBoxLayout()
        top.setSpacing(12)
        self.back = IconButton("back", "Back", box=(24, 24))
        self.back.clicked.connect(lib.back)
        self.fwd = IconButton("forward", "Forward", box=(24, 24))
        self.fwd.clicked.connect(lib.forward)
        top.addWidget(self.back)
        top.addWidget(self.fwd)
        top.addSpacing(-2)
        self.crumb = Breadcrumb(lib, 16, compact=True)
        top.addWidget(self.crumb, 1)
        self.more = IconButton("more", "More", box=(24, 24))
        self.more.clicked.connect(lambda: PopupMenu(lib.folder_menu(self.more), lib.win).popup_below(self.more))
        self.open = IconButton("open", "Open the full library", box=(24, 24))
        self.open.clicked.connect(lib.win.show_library_window)
        top.addWidget(self.more)
        top.addWidget(self.open)
        v.addLayout(top)
        v.addSpacing(20)
        row = QHBoxLayout()
        row.setSpacing(12)
        self.count = Label("", 14, "text_2")
        row.addWidget(self.count)
        row.addStretch(1)
        self.sort = IconButton("sort", "Sort", box=(24, 24))
        self.sort.clicked.connect(lambda: lib.sort_menu(self.sort))
        self.view = IconButton("list", "View options", box=(24, 24))
        self.view.clicked.connect(lambda: lib.view_menu(self.view, "list"))
        self.search_btn = IconButton("search", "Search", box=(24, 24), checkable=True)
        row.addWidget(self.sort)
        row.addWidget(self.view)
        row.addWidget(self.search_btn)
        v.addLayout(row)
        self.search = SearchBox(lib, width=0)
        self.search.hide()
        self.search_btn.toggled.connect(self._toggle_search)
        v.addSpacing(8)
        v.addWidget(self.search)
        v.addSpacing(9)
        v.addWidget(HLine())
        self.list = EntryView(lib, "row")
        self.list.setSpacing(0)
        v.addSpacing(14)
        v.addWidget(self.list, 1)
        lib.locationChanged.connect(self.refresh)
        lib.media.thumbReady.connect(lambda _p: self.list.viewport().update())
        lib.media.infoReady.connect(lambda _p: self.list.viewport().update())
        lib.engine.mediaChanged.connect(self.list.viewport().update)
        self.refresh()

    def _toggle_search(self, on):
        self.search.setVisible(on)
        if on:
            self.search.setFocus()
        else:
            self.search.clear()
            self.lib.set_query("")

    def refresh(self):
        if not self.isVisible() and self.list.count():
            self._stale = True
            return
        self._stale = False
        ents = self.lib.entries()
        self.list.set_entries(ents)
        n = self.lib.count(ents)
        self.count.setText(f"{n} item{'s' if n != 1 else ''}")
        self.back.setEnabled(bool(self.lib.back_stack))
        self.fwd.setEnabled(bool(self.lib.fwd_stack))
        self.sort.badge = self.lib.sort_mode() != DEFAULT_SORT
        self.sort.update()
        self.crumb.update()

    def showEvent(self, e):
        if getattr(self, "_stale", True):
            self.refresh()
        super().showEvent(e)

    def paintEvent(self, e):
        p = QPainter(self)
        p.fillRect(self.rect().adjusted(50, 0, 0, 0), c("base", 0.97))


# --- full library window content ------------------------------------------------------------------
class NavItem(QWidget):
    clicked = pyqtSignal()

    def __init__(self, label, icon):
        super().__init__()
        self.label, self.icon = label, icon
        self.active = False
        self.setFixedHeight(32)
        self.setCursor(Qt.PointingHandCursor)
        self._hover = False

    def enterEvent(self, e):
        self._hover = True
        self.update()

    def leaveEvent(self, e):
        self._hover = False
        self.update()

    def mouseReleaseEvent(self, e):
        if e.button() == Qt.LeftButton:
            self.clicked.emit()

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        if self._hover:
            p.setPen(Qt.NoPen)
            p.setBrush(c("hover"))
            p.drawRoundedRect(QRectF(self.rect()), 4, 4)
        p.drawPixmap(3, 4, icons.pixmap(self.icon, 24, None, "active" if self.active else ""))
        p.setFont(theme.font(14))
        p.setPen(c("text_hi") if self.active else c("icon"))
        p.drawText(QRectF(38, 0, self.width() - 40, 32), Qt.AlignVCenter, self.label)


class LibrarySidebar(QWidget):
    """Full library navigation: a #191919 card (r=6) inside an 8 px #0e0e0e margin."""

    def __init__(self, lib):
        super().__init__()
        self.lib = lib
        self.setFixedWidth(217)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(8, 8, 8, 8)
        self.card = QWidget()
        outer.addWidget(self.card)
        v = QVBoxLayout(self.card)
        v.setContentsMargins(16, 12, 16, 12)
        v.setSpacing(14)
        self.items = {}
        for key, label, icon in SECTIONS:
            n = NavItem(label, icon)
            n.clicked.connect(lambda k=key: lib.navigate((k,)))
            v.addWidget(n)
            self.items[key] = n
        self.pins = QVBoxLayout()
        self.pins.setSpacing(14)
        v.addLayout(self.pins)
        add = NavItem("Add item", "add")
        add.clicked.connect(lambda: lib.add_item_menu(add, below=True))
        v.addWidget(add)
        v.addStretch(1)
        lib.locationChanged.connect(self.sync)
        lib.store.libraryChanged.connect(lambda w: w == "pinned" and self.rebuild_pins())
        self.rebuild_pins()
        self.sync()

    def rebuild_pins(self):
        while self.pins.count():
            w = self.pins.takeAt(0).widget()
            if w:
                w.deleteLater()
        for pin in self.lib.store["pinned"]:
            n = NavItem(pin["name"], "folder" if pin["kind"] == "folder" else "playlist")
            n.clicked.connect(lambda p=pin: self.lib.open_pin(p))
            n.setContextMenuPolicy(Qt.CustomContextMenu)
            n.customContextMenuRequested.connect(
                lambda pos, p=pin, n=n: PopupMenu([MenuItem("Unpin", lambda: self.lib.store.unpin(p["kind"], p["ref"]))],
                                                  self).popup(n.mapToGlobal(pos)))
            self.pins.addWidget(n)

    def sync(self):
        for k, n in self.items.items():
            n.active = k == self.lib.loc[0]
            n.update()

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.fillRect(self.rect(), c("base"))
        p.setPen(Qt.NoPen)
        p.setBrush(c("rail"))
        p.drawRoundedRect(QRectF(self.card.geometry()), 6, 6)


class HomeScroll(QScrollArea):
    def __init__(self, lib):
        super().__init__()
        self.lib = lib
        self.verticalScrollBar().setSingleStep(40)

    def wheelEvent(self, e):
        if e.modifiers() & Qt.ControlModifier:
            if e.angleDelta().y():
                self.lib.zoom(1 if e.angleDelta().y() > 0 else -1)
            e.accept()
            return
        super().wheelEvent(e)


class HomeSection(QWidget):
    def __init__(self, lib, title, more_loc, entries, style):
        super().__init__()
        self.lib = lib
        self.title = title
        v = QVBoxLayout(self)
        v.setContentsMargins(0, 20, 0, 0)
        v.setSpacing(0)
        head = QHBoxLayout()
        head.setSpacing(5)
        s = lib.scale()
        t = Label(title, round(20 * (1 + s) / 2), "text")
        t.setCursor(Qt.PointingHandCursor)
        t.mousePressEvent = lambda e: lib.navigate(more_loc)
        head.addWidget(t)
        chev = IconButton("chevron-right", "Show all", box=(24, 24))
        chev.clicked.connect(lambda: lib.navigate(more_loc))
        head.addWidget(chev)
        head.addStretch(1)
        v.addLayout(head)
        v.addSpacing(10)
        if style == "list":
            view = EntryView(lib, "listcol")
            view.setFixedHeight(EntryDelegate.row_height(s) * ((len(entries) + 3) // 4) + 8)
        else:
            view = EntryView(lib, "card", horizontal=True)
            _, th, text_h = EntryDelegate.card_size(s, style in ("square", "folder"))
            view.setFixedHeight(th + 20 + text_h + 12)
        view.set_entries(entries)
        lib.media.thumbReady.connect(view.repaint_items)
        lib.media.infoReady.connect(view.repaint_items)
        v.addWidget(view)
        self.view = view

    def contextMenuEvent(self, e):
        # the title row (the cards have their own menus)
        PopupMenu(self.lib.home_menu(self.title), self.lib.win).popup(e.globalPos())


class HomePage(QWidget):
    """The home page's background: right click gives the player's usual menu (open a file,
    folder, stream…) plus Customize Home."""

    def __init__(self, lib):
        super().__init__()
        self.lib = lib

    def contextMenuEvent(self, e):
        self.lib.win.open_context_menu(e.globalPos(), extra=self.lib.home_menu())


class HomeLogo(HomePage):
    """Every section switched off: just a big VLC cone."""

    def paintEvent(self, e):
        p = QPainter(self)
        side = min(260, self.width() // 3, self.height() // 2)
        pm = icons.pixmap("vlc", side)
        p.drawPixmap((self.width() - side) // 2, (self.height() - side) // 2 - 20, pm)


class LibraryContent(QWidget):
    """Header (← → breadcrumb · N items · sort · view · search · + · ⋯) and the entries."""

    def __init__(self, lib):
        super().__init__()
        self.lib = lib
        v = QVBoxLayout(self)
        v.setContentsMargins(20, 20, 20, 0)
        v.setSpacing(0)
        head = QHBoxLayout()
        head.setSpacing(20)
        nav = QHBoxLayout()
        nav.setSpacing(12)
        self.back = IconButton("back", "Back (Alt+Left)", box=(24, 24))
        self.back.clicked.connect(lib.back)
        self.fwd = IconButton("forward", "Forward (Alt+Right)", box=(24, 24))
        self.fwd.clicked.connect(lib.forward)
        nav.addWidget(self.back)
        nav.addWidget(self.fwd)
        head.addLayout(nav)
        self.crumb = Breadcrumb(lib, 16)
        head.addWidget(self.crumb, 1)
        self.count = Label("", 14, "text_2")
        head.addWidget(self.count)
        self.sort = IconButton("sort", "Sort", box=(24, 24))
        self.sort.clicked.connect(lambda: lib.sort_menu(self.sort))
        self.viewbtn = IconButton("grid", "View options", box=(24, 24))
        self.viewbtn.clicked.connect(lambda: lib.view_menu(self.viewbtn))
        head.addWidget(self.sort)
        head.addWidget(self.viewbtn)
        self.search = SearchBox(lib)
        head.addWidget(self.search)
        self.addbtn = IconButton("add", "Add", box=(24, 24))
        self.addbtn.clicked.connect(self._add)
        head.addWidget(self.addbtn)
        self.more = IconButton("more", "More", box=(24, 24))
        self.more.clicked.connect(lambda: PopupMenu(lib.folder_menu(self.more), lib.win).popup_below(self.more, True))
        head.addWidget(self.more)
        v.addLayout(head)
        v.addSpacing(17)
        v.addWidget(HLine())
        self.body = QWidget()
        self.body_lay = QVBoxLayout(self.body)
        self.body_lay.setContentsMargins(0, 0, 0, 0)
        v.addWidget(self.body, 1)
        self.view = None
        self.home_area = None
        lib.locationChanged.connect(self.refresh)
        lib.media.thumbReady.connect(self._repaint)
        lib.media.infoReady.connect(self._repaint)
        lib.engine.mediaChanged.connect(self._repaint)
        self.refresh()

    def _repaint(self, *_):
        if self.view:
            self.view.viewport().update()

    def _add(self):
        loc = self.lib.loc
        what = {"videos": "videos", "music": "music", "network": "network", "playlists": "playlist"}.get(loc[0])
        if what:
            self.lib.add_action(what, self.addbtn)
        else:
            self.lib.add_item_menu(self.addbtn, below=True)

    def refresh(self):
        if not self.isVisible() and self.view is not None:
            self._stale = True
            return
        self._stale = False
        lib = self.lib
        while self.body_lay.count():
            w = self.body_lay.takeAt(0).widget()
            if w:
                w.hide()
                w.deleteLater()
        self.view = None
        loc = lib.loc
        if loc == ("home",) and not lib.query:
            sections = lib.model.home_sections()
            st = lib.store
            empty_library = not (st["video_folders"] or st["music_folders"] or st["recent"] or st.playlists())
            if not sections and not empty_library:
                # everything switched off (or nothing to show yet): the big cone
                self.body_lay.addWidget(HomeLogo(lib), 1)
                self.count.setText("")
                self.back.setEnabled(bool(lib.back_stack))
                self.fwd.setEnabled(bool(lib.fwd_stack))
                self.crumb.update()
                return
            inner = HomePage(lib)
            iv = QVBoxLayout(inner)
            iv.setContentsMargins(0, 0, 0, 30)
            iv.setSpacing(17)
            if not sections:
                iv.addWidget(self._welcome())
            for title, more, entries, style in sections:
                iv.addWidget(HomeSection(lib, title, more, entries, style))
            iv.addStretch(1)
            area = HomeScroll(lib)
            area.setWidgetResizable(True)
            area.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
            area.setWidget(inner)
            area.viewport().setAutoFillBackground(False)
            inner.setAutoFillBackground(False)
            self.body_lay.addWidget(area)
            n = sum(len(s[2]) for s in sections)
        else:
            ents = lib.entries()
            mode = lib.view_mode("grid")
            view = EntryView(lib, "card" if mode == "grid" else "listcol")
            view.setContentsMargins(0, 0, 0, 0)
            view.setViewportMargins(0, 20, 0, 0)
            view.set_entries(ents)
            self.body_lay.addWidget(view)
            self.view = view
            n = lib.count(ents)
            if not ents:
                self.body_lay.addWidget(self._empty_note())
        self.count.setText(f"{n} item{'s' if n != 1 else ''}")
        self.back.setEnabled(bool(lib.back_stack))
        self.fwd.setEnabled(bool(lib.fwd_stack))
        self.viewbtn.set_icon("grid" if lib.view_mode("grid") == "grid" else "list")
        self.sort.badge = lib.sort_mode() != DEFAULT_SORT
        self.sort.update()
        self.crumb.update()
        if self.search.text() != lib.query:
            self.search.blockSignals(True)
            self.search.setText(lib.query)
            self.search.blockSignals(False)

    def showEvent(self, e):
        if getattr(self, "_stale", False):
            self.refresh()
        super().showEvent(e)

    def _empty_note(self):
        lab = Label("Nothing here yet.", 14, "text_3")
        lab.setAlignment(Qt.AlignCenter)
        return lab

    def _welcome(self):
        w = QWidget()
        v = QVBoxLayout(w)
        v.setContentsMargins(0, 60, 0, 0)
        v.setSpacing(14)
        cone = Label("")
        cone.setPixmap(icons.pixmap("vlc", 96))
        cone.setAlignment(Qt.AlignCenter)
        v.addWidget(cone)
        t = Label("Your library is empty", 20, "text")
        t.setAlignment(Qt.AlignCenter)
        v.addWidget(t)
        s = Label("Add a folder to Videos or Music, open a file (Ctrl+O), or drop files on this window.", 14, "text_2")
        s.setAlignment(Qt.AlignCenter)
        v.addWidget(s)
        return w

    def paintEvent(self, e):
        p = QPainter(self)
        p.fillRect(self.rect(), c("base"))


class FullLibrary(QWidget):
    def __init__(self, lib):
        super().__init__()
        self.lib = lib
        h = QHBoxLayout(self)
        h.setContentsMargins(0, 0, 0, 0)
        h.setSpacing(0)
        self.sidebar = LibrarySidebar(lib)
        h.addWidget(self.sidebar)
        self.content = LibraryContent(lib)
        h.addWidget(self.content, 1)
