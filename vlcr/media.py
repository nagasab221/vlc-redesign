"""Background media services: thumbnails, tags, durations, cover art, waveforms,
subtitle cue times and seek-preview frames. Everything is cached under
~/.cache/vlc-redesign and produced on a thread pool; widgets ask, get None the first
time, and repaint when the matching *Ready signal arrives.
"""

import hashlib
import json
import os
import re
import shutil
import struct
import subprocess
import threading
from collections import OrderedDict

from PyQt5.QtCore import QObject, QRunnable, QThreadPool, QTimer, Qt, pyqtSignal
from PyQt5.QtGui import QImage, QPixmap

from .engine import AUDIO_EXT, SUB_EXT, VIDEO_EXT, ext, is_url
from .store import CACHE_DIR

try:
    import mutagen
except ImportError:          # tags fall back to ffprobe
    mutagen = None

FFMPEG = shutil.which("ffmpeg")
FFPROBE = shutil.which("ffprobe")
THUMBS = CACHE_DIR / "thumbs"
COVERS = CACHE_DIR / "covers"
WAVES = CACHE_DIR / "waves"
PREVIEWS = CACHE_DIR / "previews"


def _key(path):
    try:
        st = os.stat(path)
        raw = f"{path}|{st.st_mtime_ns}|{st.st_size}"
    except OSError:
        raw = path
    return hashlib.sha1(raw.encode("utf-8", "surrogateescape")).hexdigest()


def _run(args, timeout=30):
    try:
        return subprocess.run(args, capture_output=True, timeout=timeout)
    except (OSError, subprocess.TimeoutExpired):
        return None


class _Job(QRunnable):
    def __init__(self, fn, *args):
        super().__init__()
        self.fn, self.args = fn, args
        self.setAutoDelete(True)

    def run(self):
        try:
            self.fn(*self.args)
        except Exception:
            pass


class MediaService(QObject):
    thumbReady = pyqtSignal(str)
    infoReady = pyqtSignal(str)
    waveReady = pyqtSignal(str)
    cuesReady = pyqtSignal(str)
    previewReady = pyqtSignal(str, int)

    def __init__(self):
        super().__init__()
        for d in (THUMBS, COVERS, WAVES, PREVIEWS):
            d.mkdir(parents=True, exist_ok=True)
        self.pool = QThreadPool(self)
        self.pool.setMaxThreadCount(3)
        self.preview_pool = QThreadPool(self)
        self.preview_pool.setMaxThreadCount(1)
        self._lock = threading.Lock()
        self._pending = set()
        self._pix = OrderedDict()          # (path, w, h) -> QPixmap
        self._info_path = CACHE_DIR / "info.json"
        try:
            self._info = json.loads(self._info_path.read_text())
        except (OSError, ValueError):
            self._info = {}
        self._info_dirty = False
        self._save_timer = QTimer(self, singleShot=True, interval=3000, timeout=self._save_info)
        self._waves = {}
        self._cues = {}
        self._preview_want = None
        self._preview_cache = OrderedDict()
        self.thumbReady.connect(self._drop_pix)
        self.infoReady.connect(lambda _p: self._save_timer.start())

    # --- info (tags, duration) -----------------------------------------------
    def info(self, path):
        """{"duration": ms, "title", "artist", "album", "genre", "track", "disc", "date", "cover": bool} or None."""
        if is_url(path):
            return {}
        k = _key(path)
        rec = self._info.get(k)
        if rec is not None:
            return rec
        self._queue(("info", path), self._make_info, path, k)
        return None

    def info_now(self, path):
        """Blocking variant (used by background scans)."""
        if is_url(path):
            return {}
        k = _key(path)
        rec = self._info.get(k)
        if rec is None:
            self._make_info(path, k, emit=False)
            rec = self._info.get(k, {})
        return rec

    def _make_info(self, path, k, emit=True):
        rec = {"duration": 0, "title": "", "artist": "", "album": "", "genre": "", "track": 0, "disc": 0,
               "date": "", "cover": False}
        if mutagen is not None and ext(path) in AUDIO_EXT:
            try:
                f = mutagen.File(path, easy=True)
                if f is not None:
                    rec["duration"] = int((getattr(f.info, "length", 0) or 0) * 1000)
                    tags = f.tags or {}

                    def first(name):
                        v = tags.get(name)
                        return str(v[0]) if v else ""
                    rec["title"] = first("title")
                    rec["artist"] = first("artist") or first("albumartist")
                    rec["album"] = first("album")
                    rec["genre"] = first("genre")
                    rec["date"] = first("date")[:4]
                    for name, key in (("tracknumber", "track"), ("discnumber", "disc")):
                        m = re.match(r"(\d+)", first(name))
                        rec[key] = int(m.group(1)) if m else 0
                rec["cover"] = self._extract_cover(path, k)
            except Exception:
                pass
        if not rec["duration"] and FFPROBE:
            r = _run([FFPROBE, "-v", "error", "-show_entries", "format=duration:format_tags=title,artist,album,genre",
                      "-of", "json", path], timeout=15)
            if r and r.returncode == 0:
                try:
                    fmt = json.loads(r.stdout or b"{}").get("format", {})
                    rec["duration"] = int(float(fmt.get("duration", 0)) * 1000)
                    tags = {k2.lower(): v for k2, v in (fmt.get("tags") or {}).items()}
                    for t in ("title", "artist", "album", "genre"):
                        rec[t] = rec[t] or tags.get(t, "")
                except (ValueError, TypeError):
                    pass
        with self._lock:
            self._info[k] = rec
            self._info_dirty = True
            self._pending.discard(("info", path))
        if emit:
            self.infoReady.emit(path)

    def _extract_cover(self, path, k):
        out = COVERS / f"{k}.jpg"
        if out.exists():
            return True
        data = None
        try:
            f = mutagen.File(path)
            if f is None:
                return False
            tags = f.tags
            if hasattr(f, "pictures") and f.pictures:            # FLAC
                data = f.pictures[0].data
            elif tags is not None:
                for key in list(tags.keys()):
                    if key.startswith("APIC"):                   # ID3
                        data = tags[key].data
                        break
                    if key == "covr":                            # MP4
                        data = bytes(tags["covr"][0])
                        break
                    if key.lower() == "metadata_block_picture":  # Ogg
                        import base64
                        from mutagen.flac import Picture
                        data = Picture(base64.b64decode(tags[key][0])).data
                        break
        except Exception:
            data = None
        if not data:
            return False
        img = QImage.fromData(data)
        if img.isNull():
            return False
        img = img.scaled(600, 600, Qt.KeepAspectRatio, Qt.SmoothTransformation)
        return img.save(str(out), "JPG", 88)

    def cover_path(self, path):
        p = COVERS / f"{_key(path)}.jpg"
        return str(p) if p.exists() else ""

    def _save_info(self):
        if not self._info_dirty:
            return
        with self._lock:
            data = dict(self._info)
            self._info_dirty = False
        try:
            tmp = self._info_path.with_suffix(".tmp")
            tmp.write_text(json.dumps(data))
            os.replace(tmp, self._info_path)
        except OSError:
            pass

    # --- thumbnails ------------------------------------------------------------
    def thumb(self, path, w, h, dpr=1.0):
        """Centre-cropped thumbnail pixmap, or None while it is being made."""
        if is_url(path):
            return None
        key = (path, w, h, dpr)
        pm = self._pix.get(key)
        if pm is not None:
            self._pix.move_to_end(key)
            return pm
        k = _key(path)
        src = None
        if ext(path) in AUDIO_EXT:
            cp = COVERS / f"{k}.jpg"
            if cp.exists():
                src = str(cp)
            elif self._info.get(k) is None:
                self.info(path)
                return None
            else:
                return None
        else:
            tp = THUMBS / f"{k}.jpg"
            if tp.exists():
                src = str(tp)
            else:
                if FFMPEG and ext(path) in VIDEO_EXT:
                    self._queue(("thumb", path), self._make_thumb, path, str(tp))
                return None
        img = QImage(src)
        if img.isNull():
            return None
        pm = QPixmap.fromImage(_crop(img, int(w * dpr), int(h * dpr)))
        pm.setDevicePixelRatio(dpr)
        self._pix[key] = pm
        if len(self._pix) > 600:
            self._pix.popitem(last=False)
        return pm

    def _drop_pix(self, path):
        for key in [k for k in self._pix if k[0] == path]:
            self._pix.pop(key, None)

    def _make_thumb(self, path, out):
        dur = 0
        info = self._info.get(_key(path))
        if info:
            dur = info.get("duration", 0) / 1000
        if not dur and FFPROBE:
            r = _run([FFPROBE, "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", path], 15)
            try:
                dur = float(r.stdout.strip()) if r and r.returncode == 0 else 0
            except ValueError:
                dur = 0
        at = max(0.0, min(dur * 0.2, dur - 1)) if dur > 2 else 0
        r = _run([FFMPEG, "-nostdin", "-v", "error", "-y", "-ss", f"{at:.2f}", "-i", path, "-frames:v", "1",
                  "-vf", "scale=480:-2", "-q:v", "4", out], 40)
        with self._lock:
            self._pending.discard(("thumb", path))
        if r and r.returncode == 0 and os.path.exists(out):
            self.thumbReady.emit(path)

    # --- waveform ----------------------------------------------------------------
    def waveform(self, path, bins=600):
        if is_url(path) or not FFMPEG:
            return None
        if path in self._waves:
            return self._waves[path]
        f = WAVES / f"{_key(path)}.json"
        if f.exists():
            try:
                self._waves[path] = json.loads(f.read_text())
                return self._waves[path]
            except (OSError, ValueError):
                pass
        self._queue(("wave", path), self._make_wave, path, str(f), bins)
        return None

    def _make_wave(self, path, out, bins):
        try:
            proc = subprocess.Popen([FFMPEG, "-nostdin", "-v", "error", "-i", path, "-vn", "-ac", "1", "-ar", "2000",
                                     "-f", "s16le", "-"], stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
            raw = proc.stdout.read()
            proc.wait(timeout=120)
        except (OSError, subprocess.TimeoutExpired):
            raw = b""
        n = len(raw) // 2
        peaks = []
        if n:
            samples = struct.unpack(f"<{n}h", raw[: n * 2])
            per = max(1, n // bins)
            for i in range(0, n, per):
                chunk = samples[i:i + per]
                peaks.append(max(abs(min(chunk)), abs(max(chunk))) / 32768)
            top = max(peaks) or 1
            peaks = [round(min(1.0, (v / top) ** 0.8), 3) for v in peaks]
        with self._lock:
            self._pending.discard(("wave", path))
        if peaks:
            self._waves[path] = peaks
            try:
                open(out, "w").write(json.dumps(peaks))
            except OSError:
                pass
            self.waveReady.emit(path)

    # --- subtitle cues ----------------------------------------------------------
    def cues(self, path, subfile=None):
        """[(start_ms, end_ms)] for the given subtitle file, or the first embedded track."""
        key = subfile or path
        if key in self._cues:
            return self._cues[key]
        if subfile:
            self._cues[key] = parse_cues(subfile)
            return self._cues[key]
        if is_url(path) or not FFMPEG:
            return None
        self._queue(("cues", key), self._make_cues, path, key)
        return None

    def _make_cues(self, path, key):
        r = _run([FFMPEG, "-nostdin", "-v", "error", "-i", path, "-map", "0:s:0", "-f", "srt", "-"], 60)
        cues = parse_srt(r.stdout.decode("utf-8", "replace")) if r and r.returncode == 0 else []
        self._cues[key] = cues
        with self._lock:
            self._pending.discard(("cues", key))
        self.cuesReady.emit(key)

    # --- seek previews -----------------------------------------------------------
    def preview(self, path, ms, length, buckets=240):
        """A small frame near ms for the seek-bar hover card, or None while loading."""
        if is_url(path) or not FFMPEG or length <= 0:
            return None
        b = int(ms / length * buckets)
        key = (path, b)
        pm = self._preview_cache.get(key)
        if pm is not None:
            return pm
        f = PREVIEWS / f"{_key(path)}-{b}.jpg"
        if f.exists():
            pm = QPixmap(str(f))
            self._preview_cache[key] = pm
            if len(self._preview_cache) > 300:
                self._preview_cache.popitem(last=False)
            return pm
        self._preview_want = key
        if ("preview", path, b) not in self._pending:
            self._pending.add(("preview", path, b))
            self.preview_pool.start(_Job(self._make_preview, path, b, buckets, length, str(f)))
        return None

    def _make_preview(self, path, b, buckets, length, out):
        if self._preview_want != (path, b):
            with self._lock:
                self._pending.discard(("preview", path, b))
            return
        at = (b + 0.5) / buckets * length / 1000
        r = _run([FFMPEG, "-nostdin", "-v", "error", "-y", "-ss", f"{at:.2f}", "-i", path, "-frames:v", "1",
                  "-vf", "scale=256:-2", "-q:v", "5", out], 10)
        with self._lock:
            self._pending.discard(("preview", path, b))
        if r and r.returncode == 0 and os.path.exists(out):
            self.previewReady.emit(path, b)

    # --- internals ----------------------------------------------------------------
    def _queue(self, key, fn, *args):
        with self._lock:
            if key in self._pending:
                return
            self._pending.add(key)
        self.pool.start(_Job(fn, *args))

    def shutdown(self):
        self._save_info()
        self.pool.clear()
        self.preview_pool.clear()


def _crop(img, w, h):
    scaled = img.scaled(w, h, Qt.KeepAspectRatioByExpanding, Qt.SmoothTransformation)
    x = (scaled.width() - w) // 2
    y = (scaled.height() - h) // 2
    return scaled.copy(x, y, w, h)


def _ts(text):
    text = text.strip().replace(",", ".")
    parts = text.split(":")
    try:
        if len(parts) == 3:
            h, m, s = parts
        elif len(parts) == 2:
            h, (m, s) = 0, parts
        else:
            return 0
        return int((int(h) * 3600 + int(m) * 60 + float(s)) * 1000)
    except ValueError:
        return 0


def parse_srt(text):
    cues = []
    for m in re.finditer(r"(\d+:\d{2}:\d{2}[.,]\d{1,3})\s*-->\s*(\d+:\d{2}:\d{2}[.,]\d{1,3})", text):
        cues.append((_ts(m.group(1)), _ts(m.group(2))))
    return cues


def parse_cues(path):
    try:
        raw = open(path, "rb").read()
    except OSError:
        return []
    text = raw.decode("utf-8", "replace")
    e = ext(path)
    if e in ("ass", "ssa"):
        cues = []
        for line in text.splitlines():
            if line.startswith("Dialogue:"):
                parts = line.split(",", 3)
                if len(parts) >= 3:
                    cues.append((_ts(parts[1]), _ts(parts[2])))
        return sorted(cues)
    if e == "vtt":
        text = re.sub(r"(\n|^)(\d{2}:\d{2}\.\d{3})", r"\g<1>00:\2", text)
    return parse_srt(text)


def find_subtitles(video):
    """Subtitle files next to a video whose name starts with the video's name."""
    folder = os.path.dirname(video)
    stem = os.path.splitext(os.path.basename(video))[0].lower()
    out = []
    try:
        for e in os.scandir(folder):
            if e.is_file() and ext(e.name) in SUB_EXT and e.name.lower().startswith(stem):
                out.append(e.path)
    except OSError:
        pass
    return sorted(out)
