"""Persistent state: settings (per profile), library, playlists, bookmarks, presets, history.

Everything lives in one JSON file. Settings belong to a profile ("Movies", "Shows", ...),
so switching profile swaps the whole configuration including the control layout.
"""

import copy
import fnmatch
import json
import os
import time
from pathlib import Path

from PyQt5.QtCore import QObject, QTimer, pyqtSignal

from . import APP_ID, prefs

CONFIG_DIR = Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config") / APP_ID
CACHE_DIR = Path(os.environ.get("XDG_CACHE_HOME") or Path.home() / ".cache") / APP_ID

# The default control layout, as in the design's main Player frame.
DEFAULT_LAYOUT = {
    "title_left": ["menu", "sidebar", "divider", "folder"],
    "title_center": ["title"],
    "title_right": ["minimize", "maximize", "fullscreen", "close"],
    "rows": [["prev", "play", "next", "current_time", "seek", "duration", "volume", "audio", "subtitles",
              "settings", "queue"]],
}

# The alternative layout from the "Player - customize" frames.
ALT_LAYOUT = {
    "title_left": [],
    "title_center": [],
    "title_right": ["minimize", "maximize", "close"],
    "rows": [["current_time", "seek", "duration"],
             ["menu", "sidebar", "divider", "folder", "title", "favorite", "spacer", "prev", "play", "next", "spacer",
              "volume", "audio", "subtitles", "settings", "queue", "fullscreen"]],
}

DEFAULT_PROFILES = ["Movies", "Shows", "Music", "Work"]

STATE_DEFAULTS = {
    "version": 2,
    "onboarded": False,
    "profile": "Movies",
    "profiles": {name: {} for name in DEFAULT_PROFILES},
    "volume": 80,
    "geometry": "",
    "video_folders": [],
    "music_folders": [],
    "resume": {},          # path -> {time, length, updated}
    "watched": {},         # path -> epoch seconds
    "favorites": [],       # paths / urls
    "playlists": [],       # [{id, name, thumb, items: [...], created}]
    "network": {           # saved network streams
        "streams": [],     # [{name, url}]
        "lists": [],       # [{id, name, items: [{name, url}]}]
        "podcasts": [],    # [{name, url}]
        "radio": [],       # [{name, url}]
    },
    "recent": [],          # [{item, time}]  most recent first
    "recent_open": [],     # items opened through Open File/Folder/Stream (Open Recent menu)
    "pinned": [],          # [{kind: folder|playlist|item, ref, name}]
    "visits": {},          # folder -> count
    "plays": {},           # artist -> count
    "playlist_visits": {}, # playlist id -> count
    "bookmarks": {},       # path -> [{ms, name}]
    "presets": {},         # name -> {...}
    "folder_presets": {},  # folder -> preset name
    "external_subs": {},   # video path -> [subtitle paths]
    "hotkeys": {},         # action -> key sequence string (overrides)
    "global_hotkeys": {},  # action -> key sequence string
    "views": {},           # page key -> {"view": grid|list, "sort": ...}
    "mini_corner": "br",
    "lib_size": "Large",
    "home_hidden": [],     # titles of the home sections switched off   # library item size: Large | Medium | Small (every page and window)
}

RESUME_LIMIT = 500
RECENT_LIMIT = 200


class Store(QObject):
    settingChanged = pyqtSignal(str, object)
    profileChanged = pyqtSignal()
    libraryChanged = pyqtSignal(str)      # what: folders|playlists|favorites|network|recent|watched|pinned|bookmarks|presets
    layoutChanged = pyqtSignal()

    def __init__(self):
        super().__init__()
        self.path = CONFIG_DIR / "config.json"
        self.data = copy.deepcopy(STATE_DEFAULTS)
        try:
            with open(self.path, encoding="utf-8") as f:
                saved = json.load(f)
            if isinstance(saved, dict):
                if saved.get("version") != 2:
                    saved = self._migrate(saved)
                for k, v in saved.items():
                    if k in STATE_DEFAULTS:
                        self.data[k] = v
        except (OSError, ValueError):
            pass
        for k, v in STATE_DEFAULTS["network"].items():
            self.data["network"].setdefault(k, copy.deepcopy(v))
        if self.data["profile"] not in self.data["profiles"]:
            self.data["profiles"].setdefault(self.data["profile"], {})
        self._timer = QTimer(self, singleShot=True, interval=1200, timeout=self.save)

    def _migrate(self, old):
        """Carry over what the first version stored."""
        new = {"onboarded": old.get("onboarded", False), "volume": old.get("volume", 80),
               "geometry": old.get("geometry", ""), "resume": old.get("resume", {})}
        folders = old.get("media_folders") or []
        new["video_folders"] = list(folders)
        new["bookmarks"] = {p: [{"ms": ms, "name": ""} for ms in marks]
                            for p, marks in (old.get("bookmarks") or {}).items()}
        new["network"] = {"streams": [{"name": u, "url": u} for u in old.get("recent_urls", [])],
                          "lists": [], "podcasts": [], "radio": []}
        return new

    # --- raw state -----------------------------------------------------
    def __getitem__(self, key):
        return self.data[key]

    def __setitem__(self, key, value):
        self.data[key] = value
        self.save_later()

    def save_later(self):
        self._timer.start()

    def save(self):
        self._timer.stop()
        try:
            CONFIG_DIR.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(".tmp")
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(self.data, f, indent=1)
            os.replace(tmp, self.path)
        except OSError:
            pass

    # --- settings (per profile) ------------------------------------------
    @property
    def profile(self):
        return self.data["profile"]

    def _prof(self):
        return self.data["profiles"].setdefault(self.data["profile"], {})

    def get(self, key):
        prof = self._prof()
        if key in prof:
            return prof[key]
        return copy.deepcopy(prefs.DEFAULTS.get(key))

    def set(self, key, value):
        if self.get(key) == value:
            return
        self._prof()[key] = value
        self.save_later()
        self.settingChanged.emit(key, value)

    def reset_settings(self):
        prof = self._prof()
        layout = prof.get("layout")
        old = {k: self.get(k) for k in prefs.DEFAULTS}
        prof.clear()
        if layout is not None:
            prof["layout"] = layout
        self.save_later()
        for k, v in old.items():
            if self.get(k) != v:
                self.settingChanged.emit(k, self.get(k))

    def profiles(self):
        return list(self.data["profiles"])

    def switch_profile(self, name):
        if name == self.profile or name not in self.data["profiles"]:
            return
        old = {k: self.get(k) for k in prefs.DEFAULTS}
        self.data["profile"] = name
        self.save_later()
        for k, v in old.items():
            if self.get(k) != v:
                self.settingChanged.emit(k, self.get(k))
        self.profileChanged.emit()
        self.layoutChanged.emit()

    def add_profile(self, name, copy_current=True):
        name = name.strip()
        if not name or name in self.data["profiles"]:
            return False
        self.data["profiles"][name] = copy.deepcopy(self._prof()) if copy_current else {}
        self.save_later()
        self.switch_profile(name)
        return True

    def remove_profile(self, name):
        if name not in self.data["profiles"] or len(self.data["profiles"]) < 2:
            return
        if name == self.profile:
            self.switch_profile(next(n for n in self.data["profiles"] if n != name))
        self.data["profiles"].pop(name, None)
        self.save_later()
        self.profileChanged.emit()

    # --- layout ----------------------------------------------------------
    def layout(self):
        lay = self._prof().get("layout")
        if not lay:
            return copy.deepcopy(DEFAULT_LAYOUT)
        out = copy.deepcopy(DEFAULT_LAYOUT)
        out.update(copy.deepcopy(lay))
        return out

    def set_layout(self, layout):
        self._prof()["layout"] = copy.deepcopy(layout)
        self.save_later()
        self.layoutChanged.emit()

    # --- history -----------------------------------------------------------
    def private(self, item):
        pats = [p.strip() for p in (self.get("never_remember") or "").split(",") if p.strip()]
        for p in pats:
            if any(ch in p for ch in "*?["):
                if fnmatch.fnmatch(item, p) or fnmatch.fnmatch(os.path.basename(item), p):
                    return True
            elif p.lower() in item.lower():
                return True
        return False

    def add_recent(self, item):
        if not self.get("save_recent") or self.private(item):
            return
        rec = [r for r in self.data["recent"] if r["item"] != item]
        rec.insert(0, {"item": item, "time": time.time()})
        self.data["recent"] = rec[:RECENT_LIMIT]
        self.save_later()
        self.libraryChanged.emit("recent")

    def add_recent_open(self, item):
        rec = [r for r in self.data["recent_open"] if r != item]
        self.data["recent_open"] = [item] + rec[:14]
        self.save_later()

    def clear_history(self):
        self.data["recent"] = []
        self.data["recent_open"] = []
        self.data["resume"] = {}
        self.data["visits"] = {}
        self.data["plays"] = {}
        self.data["playlist_visits"] = {}
        self.save_later()
        self.libraryChanged.emit("recent")

    def visit_folder(self, folder):
        v = self.data["visits"]
        v[folder] = v.get(folder, 0) + 1
        self.save_later()

    def count_artist(self, artist):
        if artist:
            p = self.data["plays"]
            p[artist] = p.get(artist, 0) + 1
            self.save_later()

    # --- resume points / watched -------------------------------------------
    def set_progress(self, path, time_ms, length_ms):
        if not path or length_ms <= 0 or not self.get("remember_position") or self.private(path):
            return
        resume = self.data["resume"]
        if time_ms >= length_ms * 0.95 or length_ms - time_ms < 15_000:
            if resume.pop(path, None) is not None:
                self.save_later()
            self.mark_watched(path, True)
            return
        if time_ms < 10_000:
            return
        resume[path] = {"time": int(time_ms), "length": int(length_ms), "updated": time.time()}
        if len(resume) > RESUME_LIMIT:
            oldest = sorted(resume, key=lambda p: resume[p]["updated"])[: len(resume) - RESUME_LIMIT]
            for p in oldest:
                resume.pop(p, None)
        self.save_later()

    def progress(self, path):
        r = self.data["resume"].get(path)
        if not r or r["length"] <= 0:
            return None
        return r["time"] / r["length"]

    def forget_progress(self, path):
        if self.data["resume"].pop(path, None) is not None:
            self.save_later()

    def continue_watching(self, limit=20):
        items = sorted(self.data["resume"].items(), key=lambda kv: kv[1]["updated"], reverse=True)
        return [p for p, r in items if os.path.exists(p)][:limit]

    def watched(self, path):
        return path in self.data["watched"]

    def mark_watched(self, path, value=True):
        w = self.data["watched"]
        if value:
            if path in w:
                return
            w[path] = time.time()
        else:
            if w.pop(path, None) is None:
                return
        self.save_later()
        self.libraryChanged.emit("watched")

    # --- favorites -----------------------------------------------------------
    def is_favorite(self, item):
        return item in self.data["favorites"]

    def toggle_favorite(self, item):
        fav = self.data["favorites"]
        if item in fav:
            fav.remove(item)
        else:
            fav.insert(0, item)
        self.save_later()
        self.libraryChanged.emit("favorites")
        return item in fav

    # --- playlists -----------------------------------------------------------
    def playlists(self):
        return self.data["playlists"]

    def playlist(self, pid):
        return next((p for p in self.data["playlists"] if p["id"] == pid), None)

    def add_playlist(self, name, items=(), thumb=""):
        pid = f"pl{int(time.time() * 1000)}"
        self.data["playlists"].append({"id": pid, "name": name, "thumb": thumb, "items": list(items),
                                       "created": time.time()})
        self.save_later()
        self.libraryChanged.emit("playlists")
        return pid

    def update_playlist(self, pid, **kw):
        pl = self.playlist(pid)
        if pl:
            pl.update(kw)
            self.save_later()
            self.libraryChanged.emit("playlists")

    def add_to_playlist(self, pid, items):
        pl = self.playlist(pid)
        if pl:
            pl["items"].extend(i for i in items if i not in pl["items"])
            self.save_later()
            self.libraryChanged.emit("playlists")

    def remove_playlist(self, pid):
        self.data["playlists"] = [p for p in self.data["playlists"] if p["id"] != pid]
        self.data["pinned"] = [p for p in self.data["pinned"] if not (p["kind"] == "playlist" and p["ref"] == pid)]
        self.save_later()
        self.libraryChanged.emit("playlists")

    def visit_playlist(self, pid):
        v = self.data["playlist_visits"]
        v[pid] = v.get(pid, 0) + 1
        self.save_later()

    # --- network -------------------------------------------------------------
    def network(self):
        return self.data["network"]

    def add_stream(self, url, name=None):
        streams = [s for s in self.data["network"]["streams"] if s["url"] != url]
        streams.insert(0, {"name": name or url, "url": url})
        self.data["network"]["streams"] = streams[:100]
        self.save_later()
        self.libraryChanged.emit("network")

    def add_network_list(self, name, items):
        lid = f"nl{int(time.time() * 1000)}"
        self.data["network"]["lists"].append({"id": lid, "name": name, "items": items})
        self.save_later()
        self.libraryChanged.emit("network")
        return lid

    def network_list(self, lid):
        return next((p for p in self.data["network"]["lists"] if p["id"] == lid), None)

    def remove_network(self, kind, key):
        net = self.data["network"]
        if kind == "lists":
            net["lists"] = [x for x in net["lists"] if x["id"] != key]
        else:
            net[kind] = [x for x in net[kind] if x["url"] != key]
        self.save_later()
        self.libraryChanged.emit("network")

    def add_feed(self, kind, url, name):
        lst = [x for x in self.data["network"][kind] if x["url"] != url]
        lst.append({"name": name or url, "url": url})
        self.data["network"][kind] = lst
        self.save_later()
        self.libraryChanged.emit("network")

    # --- pinned ----------------------------------------------------------------
    def pin(self, kind, ref, name):
        if any(p["kind"] == kind and p["ref"] == ref for p in self.data["pinned"]):
            return
        self.data["pinned"].append({"kind": kind, "ref": ref, "name": name})
        self.save_later()
        self.libraryChanged.emit("pinned")

    def unpin(self, kind, ref):
        self.data["pinned"] = [p for p in self.data["pinned"] if not (p["kind"] == kind and p["ref"] == ref)]
        self.save_later()
        self.libraryChanged.emit("pinned")

    def is_pinned(self, kind, ref):
        return any(p["kind"] == kind and p["ref"] == ref for p in self.data["pinned"])

    # --- folders ---------------------------------------------------------------
    def add_folder(self, kind, folder):
        key = "music_folders" if kind == "music" else "video_folders"
        folder = os.path.abspath(os.path.expanduser(folder))
        if folder not in self.data[key]:
            self.data[key].append(folder)
            self.save_later()
            self.libraryChanged.emit("folders")

    def remove_folder(self, kind, folder):
        key = "music_folders" if kind == "music" else "video_folders"
        if folder in self.data[key]:
            self.data[key].remove(folder)
            self.save_later()
            self.libraryChanged.emit("folders")

    # --- bookmarks ---------------------------------------------------------------
    def bookmarks(self, path):
        return sorted(self.data["bookmarks"].get(path, []), key=lambda b: b["ms"])

    def add_bookmark(self, path, ms, name=None):
        marks = self.data["bookmarks"].setdefault(path, [])
        if any(abs(b["ms"] - ms) < 500 for b in marks):
            return False
        marks.append({"ms": int(ms), "name": name or ""})
        self.save_later()
        self.libraryChanged.emit("bookmarks")
        return True

    def rename_bookmark(self, path, ms, name):
        for b in self.data["bookmarks"].get(path, []):
            if b["ms"] == ms:
                b["name"] = name
        self.save_later()
        self.libraryChanged.emit("bookmarks")

    def remove_bookmark(self, path, ms):
        marks = self.data["bookmarks"].get(path, [])
        self.data["bookmarks"][path] = [b for b in marks if b["ms"] != ms]
        if not self.data["bookmarks"][path]:
            self.data["bookmarks"].pop(path)
        self.save_later()
        self.libraryChanged.emit("bookmarks")

    # --- presets -------------------------------------------------------------------
    def presets(self):
        return self.data["presets"]

    def save_preset(self, name, values):
        self.data["presets"][name] = copy.deepcopy(values)
        self.save_later()
        self.libraryChanged.emit("presets")

    def remove_preset(self, name):
        self.data["presets"].pop(name, None)
        for f, p in list(self.data["folder_presets"].items()):
            if p == name:
                self.data["folder_presets"].pop(f)
        self.save_later()
        self.libraryChanged.emit("presets")

    def folder_preset(self, folder):
        return self.data["folder_presets"].get(folder)

    def set_folder_preset(self, folder, name):
        if name:
            self.data["folder_presets"][folder] = name
        else:
            self.data["folder_presets"].pop(folder, None)
        self.save_later()

    # --- external subtitles per video ------------------------------------------------
    def external_subs(self, path):
        return [s for s in self.data["external_subs"].get(path, []) if os.path.exists(s)]

    def add_external_sub(self, path, sub):
        lst = self.data["external_subs"].setdefault(path, [])
        if sub not in lst:
            lst.append(sub)
            self.save_later()

    def set_external_subs(self, path, subs):
        if subs:
            self.data["external_subs"][path] = list(subs)
        else:
            self.data["external_subs"].pop(path, None)
        self.save_later()

    # --- view preferences --------------------------------------------------------------
    def view(self, page, key, default):
        return self.data["views"].get(page, {}).get(key, default)

    def set_view(self, page, key, value):
        self.data["views"].setdefault(page, {})[key] = value
        self.save_later()
