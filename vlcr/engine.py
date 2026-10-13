"""Playback engine: a libVLC media player, the play queue and the session's effects.

libVLC events arrive on its own threads, so state is polled from the Qt event loop
instead; calling back into libVLC from an event callback can deadlock.

Effects ("fx") are the values edited in the playback panel: picture adjustments,
filters, equalizer, delays, subtitle appearance. Some apply at once through libVLC's
API; the rest (video/audio filters, subtitle rendering) only exist as libVLC instance
options. Changing those rebuilds the instance and resumes where playback was.
On this libVLC, CPU video filters also need software decoding, which is switched on
per media only while such a filter is active.
"""

import copy
import ctypes
import hashlib
import os
import random
import re
import shutil
import struct
import subprocess
import tempfile
import threading
import time
import wave
from datetime import datetime
from pathlib import Path
from urllib.parse import unquote, urlparse

from PyQt5.QtCore import QObject, QProcess, QTimer, pyqtSignal

import vlc

from . import prefs
from .store import CACHE_DIR

VIDEO_EXT = {
    "mp4", "mkv", "avi", "mov", "wmv", "flv", "webm", "m4v", "mpg", "mpeg", "ts", "m2ts", "mts",
    "3gp", "ogv", "vob", "divx", "rm", "rmvb", "asf", "f4v",
}
AUDIO_EXT = {
    "mp3", "flac", "wav", "ogg", "oga", "opus", "m4a", "aac", "wma", "aiff", "aif", "ape", "mka",
    "mid", "midi", "wv",
}
SUB_EXT = {"srt", "ass", "ssa", "sub", "vtt", "smi", "idx"}
PLAYLIST_EXT = {"m3u", "m3u8", "pls", "xspf"}
MEDIA_EXT = VIDEO_EXT | AUDIO_EXT

STATES = {
    vlc.State.NothingSpecial: "stopped",
    vlc.State.Opening: "opening",
    vlc.State.Buffering: "opening",
    vlc.State.Playing: "playing",
    vlc.State.Paused: "paused",
    vlc.State.Stopped: "stopped",
    vlc.State.Ended: "ended",
    vlc.State.Error: "error",
}

LANG_CODES = {
    "English": "en", "Arabic": "ar", "Chinese": "zh", "French": "fr", "German": "de", "Hindi": "hi",
    "Italian": "it", "Japanese": "ja", "Korean": "ko", "Portuguese": "pt", "Russian": "ru", "Spanish": "es",
    "Turkish": "tr", "Urdu": "ur",
}

SLEEP_OPTIONS = ["Off", "15 minutes", "30 minutes", "45 minutes", "1 hour", "1.5 hours", "2 hours",
                 "End of current item"]

STEREO_MODES = ["Stereo", "Mono", "Left", "Right", "Reverse stereo", "Dolby Surround"]
CHANNELS = {"Stereo": 1, "Reverse stereo": 2, "Left": 3, "Right": 4, "Dolby Surround": 5}

FX_DEFAULTS = {
    # video
    "flip": "none", "rotate": 0, "zoom": "Fit", "hdr": "Auto", "resolution": "Best Available",
    "aspect": "Automatic", "crop": "None", "render": "Local",
    "hue": 0, "brightness": 1.0, "contrast": 1.0, "saturation": 1.0, "gamma": 1.0,
    "sharpen": 0.0, "banding": 0, "grain": 0.0,
    "crop_top": 0, "crop_bottom": 0, "crop_left": 0, "crop_right": 0, "crop_link_v": False, "crop_link_h": False,
    "extract": False, "extract_color": "#FF0000", "thres": False, "thres_color": "#FF0000",
    "thres_saturation": 20, "thres_similarity": 15, "negate": False, "posterize": False,
    "gradient": False, "gradient_mode": "Gradient", "gradient_color": False, "cartoon": False, "sepia": 0,
    # audio
    "audio_track_default": 1, "audio_lang": "English", "stereo_mode": "Stereo", "audio_delay": 0.0,
    "eq_preset": "None", "eq_bands": [12.0] + [0.0] * 10, "eq_2pass": False,
    "compressor": False, "comp_rms_peak": 0.2, "comp_attack": 25.0, "comp_release": 100.0,
    "comp_threshold": -11.0, "comp_ratio": 4.0, "comp_knee": 5.0, "comp_makeup": 7.0,
    "spatializer": False, "spat_size": 8.0, "spat_width": 10.0, "spat_wet": 4.0, "spat_dry": 5.0, "spat_damp": 5.0,
    "widener": False, "wide_delay": 20.0, "wide_feedback": 0.2, "wide_crossfeed": 0.3, "wide_dry": 0.8,
    "pitch": 0.0,
    # subtitles
    "sub_track_default": 1, "sub_lang": "English", "sub_delay": 0.0, "sub_fps": 1.0, "sub_duration": 0.0,
    "sub_embedded_format": True, "sub_font": "Inter", "sub_color": "#FFFFFF", "sub_outline_color": "#000000",
    "sub_offset": 0, "size_mode": "relative", "sub_size": "Automatic", "sub_px": 32, "outline_px": 2,
    "outline": "Normal", "bg_opacity": 0,
    # playback
    "sub_timeline": False, "waveform": False, "statistics": False, "reverse": False, "sleep": "Off",
    "visualizer": "Disabled", "speed": 1.0,
}

# Settings that set the starting value of an effect.
SETTING_TO_FX = {
    "pic_hue": "hue", "pic_brightness": "brightness", "pic_contrast": "contrast", "pic_saturation": "saturation",
    "pic_gamma": "gamma", "aspect": "aspect", "crop": "crop", "zoom": "zoom",
    "eq_2pass": "eq_2pass", "compressor": "compressor", "stereo_widen": "widener", "pitch_shift": "pitch",
    "sub_font": "sub_font", "sub_color": "sub_color", "sub_outline_color": "sub_outline_color",
    "sub_size_mode": "size_mode", "sub_size": "sub_size", "sub_px": "sub_px", "sub_outline": "outline",
    "sub_bg_opacity": "bg_opacity", "sub_delay_default": "sub_delay", "audio_delay_default": "audio_delay",
    "visualizer": "visualizer", "default_speed": "speed",
}

# Effects that only exist as libVLC instance options.
INSTANCE_FX = {
    "flip", "rotate", "sharpen", "banding", "grain", "extract", "extract_color", "thres", "thres_color",
    "thres_saturation", "thres_similarity", "negate", "posterize", "gradient", "gradient_mode", "gradient_color",
    "cartoon", "sepia", "eq_2pass", "compressor", "comp_rms_peak", "comp_attack", "comp_release", "comp_threshold",
    "comp_ratio", "comp_knee", "comp_makeup", "spatializer", "spat_size", "spat_width", "spat_wet", "spat_dry",
    "spat_damp", "widener", "wide_delay", "wide_feedback", "wide_crossfeed", "wide_dry", "pitch", "sub_fps",
    "sub_duration", "sub_font", "sub_color", "sub_outline_color", "sub_offset", "size_mode", "sub_size", "sub_px",
    "outline", "outline_px", "bg_opacity", "visualizer", "resolution", "stereo_mode",
}

# Subtitle style settings (freetype options) and the track kinds they can't reach.
SUB_STYLE_FX = {"sub_font", "sub_color", "sub_outline_color", "size_mode", "sub_size", "sub_px", "outline",
                "outline_px", "bg_opacity", "sub_offset"}
STYLED_SUBS = {"ssa "}                                                  # drawn by libass in the file's style
PICTURE_SUBS = {"spu ", "bdpg", "dvbs", "cvd ", "ogt ", "xsub", "scte"}  # DVD / Blu-ray / DVB bitmaps
PLAIN_SUBS = CACHE_DIR / "plain-subs"


def plain_srt(text):
    r"""ffmpeg's .srt from an .ass track keeps the file's fonts, sizes and colours as tags;
    drop them (keeping italics and {\anN} positions) so our style settings apply."""
    text = re.sub(r"</?(?:font|b|u|s)\b[^>]*>", "", text, flags=re.I)
    return re.sub(r"\{\\(?!an\d\})[^}]*\}", "", text)


def subtitle_stream(src, tid, embedded):
    """ffmpeg stream spec for libVLC subtitle track tid of src (embedded: libVLC's ids, in order)."""
    if not embedded:
        return "0:s:0"                        # a separate subtitle file
    r = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "s", "-show_entries", "stream=index",
                        "-of", "csv=p=0", src], capture_output=True, text=True, timeout=30)
    indexes = [int(x) for x in r.stdout.split() if x.strip().isdigit()]
    if set(embedded) <= set(indexes):
        return f"0:{tid}"                     # Matroska: libVLC's track ids are the stream numbers
    if len(indexes) == len(embedded):
        return f"0:s:{embedded.index(tid)}"
    return None


def make_plain_subs(src, tid, embedded, dest):
    """Extract one subtitle track of src as plain .srt into dest. Runs in a thread."""
    tmp = str(dest) + ".part"
    try:
        stream = subtitle_stream(src, tid, embedded)
        if not stream:
            return False
        r = subprocess.run(["ffmpeg", "-nostdin", "-v", "error", "-y", "-i", src, "-map", stream, "-f", "srt", tmp],
                           capture_output=True, timeout=120)
        if r.returncode != 0 or not os.path.getsize(tmp):
            return False
        with open(tmp, encoding="utf-8", errors="replace") as f:
            text = plain_srt(f.read())
        with open(tmp, "w", encoding="utf-8") as f:
            f.write(text)
        os.replace(tmp, dest)
        return True
    except (OSError, subprocess.SubprocessError):
        return False
    finally:
        if os.path.exists(tmp):
            os.remove(tmp)

PRESET_KEYS = [k for k in FX_DEFAULTS if k not in ("sub_timeline", "waveform", "statistics", "reverse", "sleep")]


def ext(path):
    return os.path.splitext(path)[1][1:].lower()


def is_url(item):
    return bool(re.match(r"^[a-zA-Z][a-zA-Z0-9+.-]*://", item)) and not item.startswith("file://")


def is_audio(item):
    return not is_url(item) and ext(item) in AUDIO_EXT


def is_video(item):
    return not is_url(item) and ext(item) in VIDEO_EXT


def natural_key(text):
    return [int(t) if t.isdigit() else t.lower() for t in re.split(r"(\d+)", text)]


def fmt_time(ms, always_hours=True):
    s = max(0, int((ms or 0) // 1000))
    h, s = divmod(s, 3600)
    m, s = divmod(s, 60)
    return f"{h:02d}:{m:02d}:{s:02d}" if (h or always_hours) else f"{m:02d}:{s:02d}"


def fmt_short(ms):
    """23:06 or 1:02:03, for durations in lists."""
    s = max(0, int((ms or 0) // 1000))
    h, s = divmod(s, 3600)
    m, s = divmod(s, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m:02d}:{s:02d}"


def fmt_ms(ms):
    """00:00:42:023, for bookmarks."""
    ms = max(0, int(ms))
    s, milli = divmod(ms, 1000)
    h, s = divmod(s, 3600)
    m, s = divmod(s, 60)
    return f"{h:02d}:{m:02d}:{s:02d}:{milli:03d}"


def fmt_total(ms):
    """2h 3m, for queue totals."""
    m = int((ms or 0) // 60000)
    h, m = divmod(m, 60)
    return f"{h}h {m}m" if h else f"{m}m"


def display_name(item):
    if is_url(item):
        u = urlparse(item)
        return unquote(os.path.basename(u.path.rstrip("/"))) or u.netloc or item
    return os.path.basename(item)


def media_in_dir(folder, kinds=MEDIA_EXT, recursive=False, hidden=False):
    out = []
    try:
        entries = sorted(os.scandir(folder), key=lambda e: natural_key(e.name))
    except OSError:
        return []
    for e in entries:
        if not hidden and e.name.startswith("."):
            continue
        try:
            if e.is_file() and ext(e.name) in kinds:
                out.append(e.path)
            elif recursive and e.is_dir():
                out.extend(media_in_dir(e.path, kinds, True, hidden))
        except OSError:
            pass
    return out


def read_playlist(path):
    """Entries of an m3u/pls file as (name, location)."""
    out = []
    try:
        text = Path(path).read_text(encoding="utf-8", errors="replace") if not is_url(path) else ""
    except OSError:
        return out
    return parse_playlist_text(text, os.path.dirname(path))


def parse_playlist_text(text, base=""):
    out = []
    name = None
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        if line.startswith("#EXTINF"):
            name = line.split(",", 1)[1].strip() if "," in line else None
            continue
        if line.startswith("#"):
            continue
        m = re.match(r"File\d+=(.*)", line)
        if m:
            line = m.group(1)
        elif "=" in line and not is_url(line) and line.split("=")[0].lower().startswith(("title", "length", "numberofentries", "version", "[")):
            t = re.match(r"Title\d+=(.*)", line)
            if t and out:
                out[-1] = (t.group(1), out[-1][1])
            continue
        if line.startswith("["):
            continue
        loc = line if is_url(line) or os.path.isabs(line) else os.path.join(base, line)
        out.append((name or display_name(loc), loc))
        name = None
    return out


def normalize(item, recursive=True, hidden=False):
    """Turn a path, file:// URI, folder, playlist file or URL into playable items."""
    item = item.strip()
    if item.startswith("file://"):
        item = unquote(urlparse(item).path)
    if is_url(item):
        return [item]
    item = os.path.abspath(os.path.expanduser(item))
    if os.path.isdir(item):
        return media_in_dir(item, recursive=recursive, hidden=hidden)
    if os.path.isfile(item):
        if ext(item) in PLAYLIST_EXT and ext(item) != "xspf":
            return [loc for _, loc in read_playlist(item)]
        return [item]
    return []


def hex_to_int(color):
    try:
        return int(color.lstrip("#")[:6], 16)
    except ValueError:
        return 0xFFFFFF


def fx_from_settings(store):
    fx = copy.deepcopy(FX_DEFAULTS)
    for key, fkey in SETTING_TO_FX.items():
        fx[fkey] = copy.deepcopy(store.get(key))
    if store.get("eq_enabled"):
        fx["eq_preset"] = store.get("eq_preset") if store.get("eq_preset") != "None" else "Flat"
        fx["eq_bands"] = list(store.get("eq_bands"))
    setup = store.get("speaker_setup")
    fx["stereo_mode"] = setup if setup in CHANNELS else "Stereo"
    if fx["sub_font"] in ("", None):
        fx["sub_font"] = ""
    return fx


def video_filters(fx):
    """(filter names, extra options) for the active CPU video filters."""
    filters, opts = [], []
    rot = int(fx["rotate"]) % 360
    if fx["flip"] in ("horizontal", "vertical"):
        filters.append("transform")
        opts.append("--transform-type=" + ("hflip" if fx["flip"] == "horizontal" else "vflip"))
        if rot:
            filters.append("rotate")
            opts.append(f"--rotate-angle={rot}")
    elif rot in (90, 180, 270):
        filters.append("transform")
        opts.append(f"--transform-type={rot}")
    elif rot:
        filters.append("rotate")
        opts.append(f"--rotate-angle={rot}")
    if fx["sharpen"] > 0:
        filters.append("sharpen")
        opts.append(f"--sharpen-sigma={fx['sharpen']:.2f}")
    if fx["banding"] > 0:
        filters.append("gradfun")
        opts.append(f"--gradfun-radius={int(fx['banding'])}")
    if fx["grain"] > 0:
        filters.append("grain")
        opts.append(f"--grain-variance={fx['grain']:.2f}")
    if fx["extract"]:
        filters.append("extract")
        opts.append(f"--extract-component={hex_to_int(fx['extract_color'])}")
    if fx["thres"]:
        filters.append("colorthres")
        opts += [f"--colorthres-color={hex_to_int(fx['thres_color'])}",
                 f"--colorthres-saturationthres={int(fx['thres_saturation'])}",
                 f"--colorthres-similaritythres={int(fx['thres_similarity'])}"]
    if fx["negate"]:
        filters.append("invert")
    if fx["posterize"]:
        filters.append("posterize")
        opts.append("--posterize-level=6")
    if fx["gradient"] or fx["cartoon"]:
        filters.append("gradient")
        mode = {"Gradient": "gradient", "Edge": "edge", "Hough": "hough"}.get(fx["gradient_mode"], "gradient")
        opts += [f"--gradient-mode={mode}", f"--gradient-type={1 if fx['gradient_color'] else 0}",
                 "--gradient-cartoon" if fx["cartoon"] else "--no-gradient-cartoon"]
    if fx["sepia"] > 0:
        filters.append("sepia")
        opts.append(f"--sepia-intensity={int(fx['sepia'])}")
    return filters, opts


def instance_args(store, fx):
    s = store.get
    args = ["--no-video-title-show", "--no-osd", "--no-snapshot-preview", "--no-mouse-events", "--no-keyboard-events"]
    # diagnostics
    verbosity = {"Errors only": 0, "Warnings": 1, "Everything": 2}.get(s("log_details"), 0)
    args.append(f"--verbose={verbosity}")
    if s("save_log"):
        log = s("log_file") or str(CACHE_DIR / "vlc-log.txt")
        try:
            Path(log).parent.mkdir(parents=True, exist_ok=True)
        except OSError:
            pass
        args += ["--file-logging", f"--logfile={log}", "--logmode=" + ("html" if s("log_format") == "HTML" else "text")]
    if s("syslog"):
        args += ["--syslog", f"--syslog-ident={s('syslog_ident') or 'vlc'}", f"--syslog-facility={s('syslog_facility')}"]
        if s("syslog_debug"):
            args.append("--syslog-debug")
    args.append("--stats" if s("collect_stats") else "--no-stats")
    # interfaces
    extra = []
    if s("web_remote") and s("web_password"):
        extra.append("http")
        args += [f"--http-port={int(s('web_port'))}", f"--http-password={s('web_password')}"]
    if s("lastfm") and s("lastfm_user"):
        extra.append("audioscrobbler")
        args += [f"--lastfm-username={s('lastfm_user')}", f"--lastfm-password={s('lastfm_pass')}",
                 f"--scrobbler-url={s('lastfm_url') or 'post.audioscrobbler.com'}"]
    if extra:
        args.append("--extraintf=" + ":".join(extra))
    args.append("--metadata-network-access" if s("fetch_online") or s("fetch_art") != "From the file only"
                else "--no-metadata-network-access")
    # tracks
    res = fx.get("resolution") or s("preferred_quality")
    m = re.match(r"(\d+)", str(res))
    q = re.match(r"(\d+)", str(s("preferred_quality")))
    if res == "Best Available":
        res_val = int(q.group(1)) if q else -1
    else:
        res_val = int(m.group(1)) if m else -1
    args.append(f"--preferred-resolution={res_val}")
    args.append(f"--menu-language={LANG_CODES.get(s('menu_language'), 'en')}")
    if int(s("default_audio_track")) > 1:
        args.append(f"--audio-track={int(s('default_audio_track')) - 1}")
    if int(s("default_sub_track")) > 0:
        args.append(f"--sub-track={int(s('default_sub_track')) - 1}")
    if s("audio_language") in LANG_CODES:
        args.append(f"--audio-language={LANG_CODES[s('audio_language')]}")
    if s("sub_language") in LANG_CODES:
        args.append(f"--sub-language={LANG_CODES[s('sub_language')]}")
    args.append("--no-input-fast-seek" if s("seek_precise") else "--input-fast-seek")
    args.append("--avi-index=" + {"When needed": "3", "Always": "1", "Never": "2"}.get(s("repair_avi"), "3"))
    args.append("--mkv-preload-local-dir" if s("mkv_local") else "--no-mkv-preload-local-dir")
    if s("timeshift_dir"):
        args.append(f"--input-timeshift-path={s('timeshift_dir')}")
    args.append("--input-timeshift-granularity=" + str({"Small": 10, "Medium": 50, "Large": 200}.get(s("timeshift_size"), 50) * 1024 * 1024))
    # video
    if not s("enable_video"):
        args.append("--no-video")
    args.append("--disable-screensaver" if s("stop_screensaver") else "--no-disable-screensaver")
    args.append("--align=" + str({"Center": 0, "Left": 1, "Right": 2, "Top": 4, "Bottom": 8, "Top left": 5,
                                  "Top right": 6, "Bottom left": 9, "Bottom right": 10}.get(s("align"), 0)))
    args.append(f"--monitor-par={s('monitor_par')}")
    filters, fopts = video_filters(fx)
    if filters:
        args.append("--video-filter=" + ":".join(filters))
        args += fopts
    # audio
    if not s("enable_audio"):
        args.append("--no-audio")
    args.append("--audio-replay-gain-mode=" + {"Off": "none", "Track": "track", "Album": "album"}[s("replay_gain")])
    args.append(f"--audio-replay-gain-preamp={float(s('replay_preamp')):.1f}")
    afilters = []
    if s("normalize"):
        afilters.append("normvol")
        args.append(f"--norm-max-level={float(s('norm_level')):.1f}")
    if fx["compressor"] or s("night_mode"):
        afilters.append("compressor")
        if s("night_mode") and not fx["compressor"]:
            args += ["--compressor-threshold=-30", "--compressor-ratio=8", "--compressor-makeup-gain=14",
                     "--compressor-attack=10", "--compressor-release=200"]
        else:
            args += [f"--compressor-rms-peak={fx['comp_rms_peak']:.2f}", f"--compressor-attack={fx['comp_attack']:.1f}",
                     f"--compressor-release={fx['comp_release']:.1f}", f"--compressor-threshold={fx['comp_threshold']:.1f}",
                     f"--compressor-ratio={fx['comp_ratio']:.1f}", f"--compressor-knee={fx['comp_knee']:.1f}",
                     f"--compressor-makeup-gain={fx['comp_makeup']:.1f}"]
    if fx["spatializer"] or (s("spatial") and s("spatial_mode").startswith("Room")):
        afilters.append("spatializer")
        args += [f"--spatializer-roomsize={fx['spat_size'] / 10:.2f}", f"--spatializer-width={fx['spat_width'] / 10:.2f}",
                 f"--spatializer-wet={fx['spat_wet'] / 10:.2f}", f"--spatializer-dry={fx['spat_dry'] / 10:.2f}",
                 f"--spatializer-damp={fx['spat_damp'] / 10:.2f}"]
    if s("spatial") and s("spatial_mode").startswith("Headphone"):
        afilters.append("headphone")
    if fx["widener"]:
        afilters.append("stereo_widen")
        args += [f"--stereowiden-delay={fx['wide_delay']:.1f}", f"--stereowiden-feedback={fx['wide_feedback']:.2f}",
                 f"--stereowiden-crossfeed={fx['wide_crossfeed']:.2f}", f"--stereowiden-dry-mix={fx['wide_dry']:.2f}"]
    if abs(fx["pitch"]) > 0.01:
        afilters.append("scaletempo_pitch")
        args.append(f"--pitch-shift={fx['pitch']:.2f}")
    if s("chorus"):
        afilters.append("chorus_flanger")
    if s("karaoke"):
        afilters.append("karaoke")
    if fx["stereo_mode"] == "Mono":
        afilters.append("mono")
    if afilters:
        args.append("--audio-filter=" + ":".join(afilters))
    if fx["eq_2pass"]:
        args.append("--equalizer-2pass")
    if s("spdif"):
        args.append("--spdif")
    args.append("--force-dolby-surround=" + {"Automatic": "0", "On": "1", "Off": "2"}[s("dolby_detect")])
    args.append("--audio-resampler=" + {"Fast": "ugly", "Balanced": "speex_resampler", "Best": "soxr"}[s("resampler")])
    args.append(f"--role={s('media_role').lower()}")
    viz = fx["visualizer"]
    if viz in ("Spectometer", "Scope", "Spectrum"):
        args += ["--audio-visual=visual", "--effect-list=" + {"Spectometer": "spectrometer", "Scope": "scope",
                                                               "Spectrum": "spectrum"}[viz]]
        if s("viz_size") != "Match window":
            w, h = s("viz_size").split("×")
            args += [f"--effect-width={w}", f"--effect-height={h}"]
    # subtitles
    if not s("enable_subs"):
        args.append("--no-spu")
    enc = s("sub_encoding")
    if enc != "Automatic":
        args.append("--subsdec-encoding=" + enc.split(" ")[0])
    args.append("--sub-autodetect-file" if s("sub_autoload") else "--no-sub-autodetect-file")
    args.append(f"--sub-autodetect-fuzzy={prefs.FUZZY.index(s('sub_fuzzy')) if s('sub_fuzzy') in prefs.FUZZY else 3}")
    paths = ",".join(p.strip() for p in (s("sub_paths") or "").split(",") if p.strip())
    if paths:
        args.append(f"--sub-autodetect-path={paths}")
    if fx["sub_font"]:
        args.append(f"--freetype-font={fx['sub_font']}")
    if s("sub_monofont"):
        args.append(f"--freetype-monofont={s('sub_monofont')}")
    if fx["size_mode"] == "pixels":
        args.append(f"--freetype-fontsize={int(fx['sub_px'])}")
    else:
        rel = {"Automatic": 0, "Smaller": 20, "Small": 18, "Normal": 16, "Large": 12, "Larger": 6}.get(fx["sub_size"], 0)
        if rel:
            args.append(f"--freetype-rel-fontsize={rel}")
    args.append("--freetype-bold" if s("sub_bold") else "--no-freetype-bold")
    args.append(f"--freetype-color={hex_to_int(fx['sub_color'])}")
    args.append(f"--freetype-opacity={round(int(s('sub_opacity')) * 2.55)}")
    if fx["size_mode"] == "pixels":
        thick = min(6, max(0, int(fx["outline_px"]) * 2)) if fx["outline_px"] else 0
    else:
        thick = {"None": 0, "Thin": 2, "Normal": 4, "Thick": 6}.get(fx["outline"], 4)
    args.append(f"--freetype-outline-thickness={thick}")
    args.append(f"--freetype-outline-color={hex_to_int(fx['sub_outline_color'])}")
    args.append(f"--freetype-shadow-opacity={128 if s('sub_shadow') else 0}")
    args.append(f"--freetype-shadow-color={hex_to_int(s('sub_shadow_color'))}")
    args.append("--freetype-shadow-distance=" + {"Small": "0.04", "Medium": "0.06", "Large": "0.1"}[s("sub_shadow_distance")])
    args.append(f"--freetype-shadow-angle={int(s('sub_shadow_angle'))}")
    bg = int(fx["bg_opacity"])
    if s("sub_background") == "Box" and not bg:
        bg = int(s("sub_bg_opacity"))
    args.append(f"--freetype-background-opacity={round(bg * 2.55)}")
    args.append(f"--freetype-background-color={hex_to_int(s('sub_bg_color'))}")
    args.append("--freetype-text-direction=" + {"Left to right": "0", "Right to left": "1", "Automatic": "2"}[s("sub_direction")])
    margin = int(fx["sub_offset"]) or {"Bottom": 0, "Raised": 50, "High": 150}.get(s("sub_position"), 0)
    if margin:
        args.append(f"--sub-margin={margin}")
    if abs(fx["sub_fps"] - 1.0) > 0.001:
        args.append(f"--sub-fps={25 * fx['sub_fps']:.3f}")
    if abs(fx["sub_duration"]) > 0.001:
        args += ["--sub-filter=subsdelay", "--subsdelay-mode=1", f"--subsdelay-factor={max(0.0, 1 + fx['sub_duration']):.2f}"]
    # network
    caching = {"Low latency": (100, 300, 300, 100), "Normal": (300, 300, 1000, 300), "High": (1000, 1000, 3000, 1000)}
    if s("buffering") == "Custom":
        cf, cd, cn, cl = (int(s("cache_file")), int(s("cache_disc")), int(s("cache_net")), int(s("cache_live")))
    else:
        cf, cd, cn, cl = caching.get(s("buffering"), caching["Normal"])
    args += [f"--file-caching={cf}", f"--disc-caching={cd}", f"--network-caching={cn}", f"--live-caching={cl}"]
    args.append("--gnutls-system-trust" if s("tls_system_trust") else "--no-gnutls-system-trust")
    args.append("--gnutls-priorities=" + {"Balanced": "NORMAL", "Performance": "PERFORMANCE", "Secure": "SECURE256"}[s("tls_ciphers")])
    # performance
    args.append(f"--postproc-q={ {'Off': 0, 'Low': 2, 'Medium': 4, 'High': 6}[s('postproc')] }")
    args.append(f"--avcodec-skiploopfilter={['Never', 'Non-reference frames', 'B-frames', 'Non-key frames', 'All frames'].index(s('skip_loop'))}")
    args.append(f"--avcodec-threads={int(s('dec_threads'))}")
    args.append("--drop-late-frames" if s("drop_late") else "--no-drop-late-frames")
    if s("rt_priority"):
        args += ["--rt-priority", f"--rt-offset={int(s('rt_offset'))}"]
    if s("codec") != "Automatic":
        args.append(f"--codec={s('codec')},any")
    if s("encoder") != "Automatic":
        args.append(f"--encoder={s('encoder')},any")
    args.append("--clock-synchro=" + {"Default": "-1", "Disabled": "0", "Enabled": "1"}[s("clock_sync")])
    args.append(f"--clock-jitter={int(s('clock_jitter'))}")
    args.append(f"--cr-average={int(s('cr_average'))}")
    if s("extra_args"):
        args += [a for a in s("extra_args").split() if a.startswith("-")]
    return args


def media_options(store, fx):
    opts = []
    filters, _ = video_filters(fx)
    hw = store.get("hwdec")
    if filters or hw == "Off":
        opts.append(":avcodec-hw=none")
    elif hw == "VDPAU":
        opts.append(":avcodec-hw=vdpau_avcodec")
    return opts


def trim_memory():
    """Hand memory that decoders freed back to the system (glibc keeps it otherwise)."""
    try:
        ctypes.CDLL("libc.so.6").malloc_trim(0)
    except (OSError, AttributeError):
        pass


class Engine(QObject):
    stateChanged = pyqtSignal(str)
    tick = pyqtSignal(int, int)            # time ms, length ms
    mediaChanged = pyqtSignal()            # a new item started (or the queue emptied)
    metaChanged = pyqtSignal()
    videoChanged = pyqtSignal(bool)
    playlistChanged = pyqtSignal()
    volumeChanged = pyqtSignal(int, bool)
    modesChanged = pyqtSignal()            # repeat / shuffle / stop-after
    message = pyqtSignal(str, str)         # text, key that caused it ("" if none)
    error = pyqtSignal(str)
    resumed = pyqtSignal(int, str)         # saved position ms, "auto" | "ask"
    tracksChanged = pyqtSignal()
    fxChanged = pyqtSignal(str)            # fx key ("" = many)
    rebuilt = pyqtSignal()
    recordingChanged = pyqtSignal(bool)
    sleepChanged = pyqtSignal(str)
    rateChanged = pyqtSignal(float)
    _plainReady = pyqtSignal(object, str)   # (item, track id), .srt path or "" (from a worker thread)

    def __init__(self, store):
        super().__init__()
        self.store = store
        self.items = []
        self.names = {}                     # item -> display name (network channels etc.)
        self.index = -1
        self.repeat = {"Off": "off", "All": "all", "One": "one"}[store.get("repeat")]
        self.shuffle = bool(store.get("shuffle"))
        self.stop_after = bool(store.get("stop_after_each"))
        self.meta = {}                      # item -> {title, artist, album, art}
        self.fx = fx_from_settings(store)
        self._xwindow = 0
        self._volume = self._startup_volume()
        self._muted = False
        self._state = "stopped"
        self._has_video = True
        self._video_checked = False
        self._meta_checked = False
        self._tracks_checked = 0
        self._last = (0, 0)
        self._last_save = 0.0
        self._history = []
        self._pause_on_start = False
        self._restore = None                # (audio track, spu track) to restore after a rebuild
        # .ass tracks shown as plain text so the subtitle style applies ("Embedded Sub Format" off)
        self._plain_es = {}                 # (item, track id) -> id of its plain copy in the player
        self._plain_on = None               # track id whose plain copy is showing
        self._plain_want = None             # track id that should show as plain text
        self._plain_busy = set()            # (item, track id) being converted / loaded
        self._codecs = {}                   # item -> {subtitle track id: codec}, kept through restarts
        self._hold = None                   # {"audio", "spu", "until"}: tracks to keep after a rebuild
        self._plainReady.connect(self._plain_ready)
        self._sleep_timer = QTimer(self, singleShot=True, timeout=self._sleep_fire)
        self._sleep_end_of_item = False
        self._record = None                 # {"start": ms, "item": ..., "proc": QProcess}
        self._eq = None
        self._args = None
        self.instance = None
        self.mp = None
        self._build()

        self._rebuild_timer = QTimer(self, singleShot=True, interval=350, timeout=self.rebuild)
        self._trim_timer = QTimer(self, singleShot=True, interval=4000, timeout=trim_memory)
        self._timer = QTimer(self, interval=200, timeout=self._poll)
        self._timer.start()
        store.settingChanged.connect(self._on_setting)

    # --- libVLC instance -------------------------------------------------
    def _startup_volume(self):
        v = self.store.get("startup_volume")
        return int(self.store["volume"]) if v is None or v < 0 else int(v)

    def _build(self):
        args = instance_args(self.store, self.fx)
        once = getattr(self, "extra_once", None)
        if once:
            self.extra_once = None
            args = args + once
        proxy = (self.store.get("http_proxy") or "").strip()
        if proxy:
            if "://" not in proxy:
                proxy = "http://" + proxy
            pwd = self.store.get("http_proxy_pwd")
            if pwd and "@" not in proxy:
                proxy = proxy.replace("://", f"://:{pwd}@", 1)
            os.environ["http_proxy"] = os.environ["https_proxy"] = proxy
        self.instance = vlc.Instance(args)
        if self.instance is None:
            # An option was rejected (usually from "Additional command line options"): fall back.
            args = [a for a in args if not any(a.startswith(x) for x in (self.store.get("extra_args") or "-@").split())]
            self.instance = vlc.Instance(args) or vlc.Instance(["--no-video-title-show"])
            self.error.emit("libVLC rejected some options; they were ignored.")
        self._args = args
        try:
            self.instance.set_user_agent("VLC", "VLCRedesign/0.2 LibVLC/3.0")
            self.instance.set_app_id("vlc-redesign", "0.2", "vlc")
        except Exception:
            pass
        self.mp = self.instance.media_player_new()
        self.mp.video_set_mouse_input(False)
        self.mp.video_set_key_input(False)
        if self._xwindow:
            self.mp.set_xwindow(self._xwindow)
        self.mp.audio_set_volume(self._volume)
        dev = self.store.get("audio_device")
        self._device = "" if dev in (None, "", "System default") else dev
        if self._device:
            self.mp.audio_output_device_set(None, self._device)
        self._apply_eq()

    def set_window(self, winid):
        self._xwindow = int(winid)
        if self.mp:
            self.mp.set_xwindow(self._xwindow)

    def schedule_rebuild(self):
        self._rebuild_timer.start()

    def rebuild(self, force=False):
        args = instance_args(self.store, self.fx)
        if not force and args == [a for a in self._args if a != "--reset-plugins-cache"]:
            return
        item = self.current
        was_paused = self._state == "paused"
        active = self.is_active()
        t = self.mp.get_time() if active else 0
        restore = self._wanted_tracks() if active else None
        rate = self.mp.get_rate() or 1.0
        self._save_progress()
        self.mp.stop()
        self.mp.release()
        self.instance.release()
        self._build()
        if item and active:
            self._restore = restore
            self._start_item(self.index, start_ms=max(0, t), resume=False, quiet=True)
            self._hold = {"audio": restore[0], "spu": restore[1], "until": time.monotonic() + 6}
            self._pause_on_start = was_paused
            if abs(rate - 1.0) > 0.01:
                self.mp.set_rate(rate)
        self.rebuilt.emit()
        self._trim_timer.start()

    def _on_setting(self, key, value):
        row = prefs.ROWS.get(key)
        if key in SETTING_TO_FX:
            self.set_fx(SETTING_TO_FX[key], copy.deepcopy(value), announce=False)
        if key in ("eq_enabled", "eq_preset", "eq_bands"):
            if self.store.get("eq_enabled"):
                p = self.store.get("eq_preset")
                self.fx["eq_preset"] = p if p != "None" else "Flat"
                self.fx["eq_bands"] = list(self.store.get("eq_bands"))
            else:
                self.fx["eq_preset"] = "None"
            self._apply_eq()
            self.fxChanged.emit("eq_preset")
        elif key == "speaker_setup":
            self.set_fx("stereo_mode", value if value in CHANNELS else "Stereo", announce=False)
        elif key == "repeat":
            self.set_repeat({"Off": "off", "All": "all", "One": "one"}[value])
        elif key == "shuffle":
            if bool(value) != self.shuffle:
                self.toggle_shuffle(announce=False)
        elif key == "stop_after_each":
            self.stop_after = bool(value)
            self.modesChanged.emit()
        elif key == "audio_device":
            self.set_audio_device(None if value == "System default" else value)
        elif key in ("deinterlace", "deinterlace_mode", "autoscale"):
            self._apply_runtime()
        elif key == "max_volume":
            if self._volume > int(value):
                self.set_volume(int(value), notify=False)
        elif key == "hwdec":
            self.rebuild(force=True)
        elif row is not None and row.apply == "instance":
            self.schedule_rebuild()

    # --- effects -------------------------------------------------------------
    def set_fx(self, key, value, announce=True):
        if self.fx.get(key) == value:
            return
        self.fx[key] = value
        self.fxChanged.emit(key)
        if key in SUB_STYLE_FX:
            self._style_touched(key)
        if key == "sub_embedded_format":
            self._set_embedded_format(bool(value))
        elif key in INSTANCE_FX:
            self.schedule_rebuild()
            if key == "stereo_mode":
                self._apply_runtime()       # Left/Right/… are runtime; only Mono needs the rebuild
        elif key.startswith("eq_"):
            self._apply_eq()
        elif key == "audio_delay":
            self.mp.audio_set_delay(int(value * 1_000_000))
        elif key == "sub_delay":
            self.mp.video_set_spu_delay(int(value * 1_000_000))
        elif key == "speed":
            self.set_rate(value, announce=announce)
        elif key == "sleep":
            self.set_sleep(value)
        else:
            self._apply_runtime()

    def reset_fx(self, keys=None):
        base = fx_from_settings(self.store)
        for k in keys or list(FX_DEFAULTS):
            if k in ("sleep",):
                continue
            self.fx[k] = copy.deepcopy(base[k])
        self.fxChanged.emit("")
        self._apply_runtime()
        self._apply_eq()
        self.mp.audio_set_delay(int(self.fx["audio_delay"] * 1_000_000))
        self.mp.video_set_spu_delay(int(self.fx["sub_delay"] * 1_000_000))
        self.schedule_rebuild()

    def preset_values(self):
        return {k: copy.deepcopy(self.fx[k]) for k in PRESET_KEYS}

    def apply_preset(self, name):
        """name None/"Default Preset" resets to the settings' defaults."""
        if not name or name == "Default Preset":
            self.reset_fx(PRESET_KEYS)
            return
        values = self.store.presets().get(name)
        if not values:
            return
        for k, v in values.items():
            if k in FX_DEFAULTS:
                self.fx[k] = copy.deepcopy(v)
        self.fxChanged.emit("")
        self._apply_runtime()
        self._apply_eq()
        self.mp.audio_set_delay(int(self.fx["audio_delay"] * 1_000_000))
        self.mp.video_set_spu_delay(int(self.fx["sub_delay"] * 1_000_000))
        if abs((self.mp.get_rate() or 1.0) - self.fx["speed"]) > 0.01:
            self.mp.set_rate(self.fx["speed"])
        self.schedule_rebuild()

    def _apply_eq(self):
        if not self.mp:
            return
        preset = self.fx["eq_preset"]
        if preset == "None":
            self.mp.set_equalizer(None)
            self._eq = None
            return
        eq = vlc.AudioEqualizer()
        bands = self.fx["eq_bands"]
        eq.set_preamp(float(bands[0]))
        for i, amp in enumerate(bands[1:11]):
            eq.set_amp_at_index(float(amp), i)
        self.mp.set_equalizer(eq)     # libVLC copies the settings
        eq.release()
        self._eq = preset

    def eq_preset_bands(self, name):
        """Preamp + 10 band values of a libVLC preset."""
        if name not in prefs.EQ_PRESETS[1:]:
            return [12.0] + [0.0] * 10
        idx = prefs.EQ_PRESETS.index(name) - 1
        eq = vlc.libvlc_audio_equalizer_new_from_preset(idx)
        try:
            vals = [vlc.libvlc_audio_equalizer_get_preamp(eq)]
            vals += [vlc.libvlc_audio_equalizer_get_amp_at_index(eq, i) for i in range(10)]
        finally:
            vlc.libvlc_audio_equalizer_release(eq)
        return [round(v, 1) for v in vals]

    def _apply_runtime(self):
        """Effects libVLC can change while playing."""
        mp = self.mp
        if not mp:
            return
        fx = self.fx
        # picture
        if (fx["hue"], fx["brightness"], fx["contrast"], fx["saturation"], fx["gamma"]) != (0, 1.0, 1.0, 1.0, 1.0):
            mp.video_set_adjust_int(vlc.VideoAdjustOption.Enable, 1)
            mp.video_set_adjust_float(vlc.VideoAdjustOption.Hue, float(fx["hue"]))
            mp.video_set_adjust_float(vlc.VideoAdjustOption.Brightness, float(fx["brightness"]))
            mp.video_set_adjust_float(vlc.VideoAdjustOption.Contrast, float(fx["contrast"]))
            mp.video_set_adjust_float(vlc.VideoAdjustOption.Saturation, float(fx["saturation"]))
            mp.video_set_adjust_float(vlc.VideoAdjustOption.Gamma, float(fx["gamma"]))
        else:
            mp.video_set_adjust_int(vlc.VideoAdjustOption.Enable, 0)
        # framing
        aspect = fx["aspect"]
        mp.video_set_aspect_ratio(None if aspect in ("Automatic", "Default") else aspect)
        crop = fx["crop"]
        px = (fx["crop_top"], fx["crop_bottom"], fx["crop_left"], fx["crop_right"])
        if any(px):
            try:
                w, h = mp.video_get_size(0)
            except Exception:
                w = h = 0
            if w and h:
                t, b, l, r = px
                mp.video_set_crop_geometry(f"{max(16, w - l - r)}x{max(16, h - t - b)}+{l}+{t}")
        else:
            mp.video_set_crop_geometry(None if crop in ("None", "Default") else crop)
        zoom = fx["zoom"]
        scale = {"Fit": 0.0, "1:4 Quarter": 0.25, "1:2 Half": 0.5, "1:1 Original": 1.0, "2:1 Double": 2.0}.get(zoom, 0.0)
        if not self.store.get("autoscale") and scale == 0.0:
            scale = 1.0
        mp.video_set_scale(scale)
        # deinterlace
        d = self.store.get("deinterlace")
        mode = self.store.get("deinterlace_mode")
        if d == "Off":
            mp.video_set_deinterlace(None)
        elif d == "On" or mode != "Automatic":
            name = {"Automatic": "yadif", "Blend": "blend", "Bob": "bob", "Discard": "discard", "Linear": "linear",
                    "Mean": "mean", "X": "x", "Yadif": "yadif", "Yadif (2x)": "yadif2x", "Phosphor": "phosphor",
                    "Film NTSC (IVTC)": "ivtc"}.get(mode, "yadif")
            mp.video_set_deinterlace(name)
        # audio channels: only when it actually changes. Setting the stereo mode, even to the
        # value it already has, makes libVLC rebuild the audio output (a new PulseAudio/PipeWire
        # stream); doing that as a movie starts could leave it silent. "Stereo" = as the file is.
        ch = CHANNELS.get(fx["stereo_mode"], CHANNELS["Stereo"])     # Mono is an audio filter instead
        if self.is_active():
            cur = mp.audio_get_channel()
            if cur != ch and not (ch == CHANNELS["Stereo"] and cur in (0, CHANNELS["Stereo"])):
                mp.audio_set_channel(ch)

    # --- queries ---------------------------------------------------------------
    @property
    def current(self):
        return self.items[self.index] if 0 <= self.index < len(self.items) else None

    @property
    def state(self):
        return self._state

    @property
    def has_video(self):
        return self._has_video

    @property
    def volume(self):
        return self._volume

    @property
    def muted(self):
        return self._muted

    def title(self, item=None):
        item = item if item is not None else self.current
        if item is None:
            return ""
        return self.names.get(item) or self.meta.get(item, {}).get("title") or display_name(item)

    def file_title(self, item=None):
        """Title for the title bar: the name without extension."""
        item = item if item is not None else self.current
        if item is None:
            return ""
        t = self.names.get(item) or self.meta.get(item, {}).get("title")
        if t:
            return t
        name = display_name(item)
        return os.path.splitext(name)[0] if not is_url(item) else name

    def artist(self, item=None):
        item = item if item is not None else self.current
        return self.meta.get(item, {}).get("artist", "") if item else ""

    def album(self, item=None):
        item = item if item is not None else self.current
        return self.meta.get(item, {}).get("album", "") if item else ""

    def artwork(self):
        return self.meta.get(self.current, {}).get("art", "") if self.current else ""

    def time(self):
        return self._last

    def is_active(self):
        return self._state in ("playing", "paused", "opening")

    def is_playing(self):
        return self._state in ("playing", "opening")

    def length_of(self, item):
        return self.meta.get(item, {}).get("length", 0)

    # --- queue -----------------------------------------------------------------
    def _expand(self, items):
        rec = self.store.get("folder_mode") == "Include subfolders"
        hidden = self.store.get("show_hidden")
        return [i for item in items for i in normalize(item, rec, hidden)]

    def load(self, items, index=0, names=None):
        items = self._expand(items)
        if not items:
            self.message.emit("Nothing playable there", "")
            return False
        if names:
            self.names.update(names)
        # Save the old item's position *before* the queue changes, then forget it:
        # play_index() would otherwise file it under the new queue's item.
        self._save_progress()
        self.index = -1
        self._last = (0, 0)
        self.items = items
        self._history = []
        self.playlistChanged.emit()
        self.play_index(max(0, min(index, len(items) - 1)))
        return True

    def enqueue(self, items, names=None, play_next=False):
        items = self._expand(items)
        if not items:
            return 0
        if names:
            self.names.update(names)
        if play_next and self.index >= 0:
            pos = self.index + 1
            self.items[pos:pos] = items
        else:
            self.items.extend(items)
        self.playlistChanged.emit()
        if not self.is_active() and self.index < 0:
            self.play_index(len(self.items) - len(items) if not play_next else 0)
        return len(items)

    def remove(self, rows):
        rows = sorted(set(rows), reverse=True)
        playing_removed = self.index in rows
        if playing_removed:
            self._save_progress()
            self._last = (0, 0)
        for r in rows:
            if 0 <= r < len(self.items):
                del self.items[r]
                if r < self.index:
                    self.index -= 1
        if playing_removed:
            self.mp.stop()
            self.index = min(self.index, len(self.items) - 1)
            if self.items and self.index >= 0:
                self.play_index(self.index)
            else:
                self.index = -1
                self.mediaChanged.emit()
        self.playlistChanged.emit()

    def move(self, src, dst):
        if not (0 <= src < len(self.items)) or not (0 <= dst < len(self.items)) or src == dst:
            return
        self.items.insert(dst, self.items.pop(src))
        if src == self.index:
            self.index = dst
        elif src < self.index <= dst:
            self.index -= 1
        elif dst <= self.index < src:
            self.index += 1
        self.playlistChanged.emit()

    def sort_queue(self, key="name", reverse=False):
        cur = self.current
        if key == "name":
            self.items.sort(key=lambda i: natural_key(display_name(i)), reverse=reverse)
        elif key == "length":
            self.items.sort(key=lambda i: self.length_of(i), reverse=reverse)
        elif key == "folder":
            self.items.sort(key=lambda i: natural_key(i), reverse=reverse)
        self.index = self.items.index(cur) if cur in self.items else -1
        self.playlistChanged.emit()

    def clear(self):
        self._save_progress()
        self.mp.stop()
        self.items, self.index = [], -1
        self.playlistChanged.emit()
        self.mediaChanged.emit()

    def queue_length(self):
        return sum(self.length_of(i) for i in self.items)

    # --- transport ---------------------------------------------------------------
    def play_index(self, i, resume=True):
        if not (0 <= i < len(self.items)):
            return
        self._save_progress()
        self._start_item(i, resume=resume)

    def _start_item(self, i, start_ms=0, resume=True, quiet=False):
        self._plain_es, self._plain_on, self._plain_busy = {}, None, set()
        if not quiet:
            self._plain_want = self._hold = None
            self._codecs = {}
        self.index = i
        item = self.items[i]
        media = self.instance.media_new(item) if is_url(item) else self.instance.media_new_path(item)
        for opt in media_options(self.store, self.fx):
            media.add_option(opt)
        if item.startswith(("v4l2://", "screen://")):
            for opt in getattr(self, "capture_options", []):
                media.add_option(opt)
        saved = self.store["resume"].get(item) if not is_url(item) else None
        mode = self.store.get("continue")
        ask = 0
        if resume and saved and not start_ms:
            if mode == "Always":
                start_ms = saved["time"]
            elif mode == "Ask":
                ask = saved["time"]
        if start_ms:
            media.add_option(f":start-time={start_ms / 1000:.2f}")
        self._start_ms = start_ms
        if not is_url(item):
            auto = self._autodetected(item)
            for sub in [s for s in self.store.external_subs(item) if s not in auto]:
                try:
                    media.slaves_add(vlc.MediaSlaveType.subtitle, 4, Path(sub).as_uri())
                except Exception:
                    pass
        self.mp.stop()
        self.mp.set_media(media)
        media.release()      # the player keeps its own reference; ours would leak the media (and its attachments)
        self._has_video = not is_audio(item)
        self._video_checked = False
        self._meta_checked = False
        self._tracks_checked = 0
        self._last = (start_ms, saved["length"] if saved else 0)
        self._pause_on_start = not self.store.get("autoplay") and not quiet
        self.mp.play()
        if self._state != "opening":
            # A restart can finish between two polls; without this the poll never sees it start
            # and skips _on_started (track restore after a rebuild, delays, speed).
            self._state = "opening"
            self.stateChanged.emit("opening")
        if self.shuffle and not quiet:
            self._history.append(i)
        if not quiet:
            self.store.add_recent(item)
            self._apply_folder_preset(item)
        self.mediaChanged.emit()
        self.videoChanged.emit(self._has_video)
        if not quiet:
            if start_ms and mode == "Always":
                self.resumed.emit(start_ms, "auto")
            elif ask:
                self.resumed.emit(ask, "ask")

    def _apply_folder_preset(self, item):
        if is_url(item):
            return
        folder = os.path.dirname(item)
        name = self.store.folder_preset(folder)
        if name and name in self.store.presets():
            self.apply_preset(name)
            self.current_preset = name
        else:
            self.current_preset = getattr(self, "current_preset", "Default Preset")

    def restart(self):
        if self.current:
            self.store.forget_progress(self.current)
            if self.is_active():
                self.mp.set_time(0)
            else:
                self.play_index(self.index, resume=False)

    def continue_from(self, ms):
        if self.is_active():
            self.mp.set_time(int(ms))

    def toggle(self):
        if self._state in ("playing", "opening"):
            self.mp.set_pause(1)
        elif self._state == "paused":
            self.mp.set_pause(0)
        elif self.items:
            self.play_index(self.index if self.index >= 0 else 0)

    def pause(self):
        if self._state in ("playing", "opening"):
            self.mp.set_pause(1)

    def play(self):
        if self._state == "paused":
            self.mp.set_pause(0)
        elif not self.is_active() and self.items:
            self.play_index(max(0, self.index))

    def stop(self):
        self._save_progress()
        self.mp.stop()
        self.stop_record()

    def next(self, auto=False):
        if not self.items:
            return
        if self.shuffle and len(self.items) > 1:
            choices = [i for i in range(len(self.items)) if i != self.index]
            self.play_index(random.choice(choices))
            return
        nxt = self.index + 1
        if nxt >= len(self.items):
            nxt = 0 if self.repeat == "all" else None
        if nxt is None:
            if not auto:
                self.message.emit("End of the queue", "")
            return
        self.play_index(nxt)

    def prev(self):
        if not self.items:
            return
        if self._last[0] > 3000 and self._state in ("playing", "paused"):
            self.mp.set_time(0)
            return
        if self.shuffle and len(self._history) > 1:
            self._history.pop()
            self.play_index(self._history.pop())
            return
        prv = self.index - 1
        if prv < 0:
            prv = len(self.items) - 1 if self.repeat == "all" else 0
        self.play_index(prv)

    def seek_rel(self, ms):
        if not self.mp.is_seekable():
            return
        t, length = self.mp.get_time(), self.mp.get_length()
        target = max(0, t + ms)
        if length > 0:
            target = min(target, length - 500)
        self.mp.set_time(int(target))
        self._last = (int(target), length)
        self.tick.emit(*self._last)

    def seek_to(self, ms):
        if self.mp.is_seekable():
            self.mp.set_time(int(ms))
            self._last = (int(ms), self._last[1])
            self.tick.emit(*self._last)

    def set_position(self, frac):
        if self.mp.is_seekable():
            frac = max(0.0, min(1.0, frac))
            length = self.mp.get_length()
            if length > 1000:
                # libVLC ignores a seek to the very end of some files (MKV stays put), so land just before it
                frac = min(frac, (length - 400) / length)
            self.mp.set_position(frac)

    def next_frame(self):
        if self.is_active():
            self.mp.next_frame()

    def prev_frame(self):
        if not self.is_active():
            return
        self.pause()
        fps = self.mp.get_fps() or 25
        self.seek_rel(-int(1000 / fps))

    # --- audio --------------------------------------------------------------------
    def max_volume(self):
        return int(self.store.get("max_volume"))

    def set_volume(self, v, notify=True):
        self._volume = max(0, min(self.max_volume(), int(round(v))))
        if self._muted and v > 0:
            self._muted = False
            self.mp.audio_set_mute(False)
        self.mp.audio_set_volume(self._volume)
        self.store["volume"] = self._volume
        self.volumeChanged.emit(self._volume, self._muted)
        if notify:
            self.message.emit(f"Volume: {self._volume}%", "")

    def change_volume(self, steps):
        self.set_volume(self._volume + steps * int(self.store.get("volume_step")))

    def toggle_mute(self):
        self._muted = not self._muted
        self.mp.audio_set_mute(self._muted)
        self.volumeChanged.emit(self._volume, self._muted)
        self.message.emit("Muted" if self._muted else f"Volume: {self._volume}%", "")

    def rate(self):
        return self.mp.get_rate() or 1.0

    def set_rate(self, r, announce=True):
        r = round(max(0.25, min(4.0, r)), 2)
        self.mp.set_rate(r)
        self.fx["speed"] = r
        self.rateChanged.emit(r)
        if announce:
            self.message.emit(f"Speed: {r:g}×", "")

    def audio_tracks(self):
        return [(i, _dec(n)) for i, n in (self.mp.audio_get_track_description() or [])]

    def audio_track(self):
        return self.mp.audio_get_track()

    def set_audio_track(self, tid):
        if self._hold:
            self._hold["audio"] = tid
        self.mp.audio_set_track(tid)
        self.tracksChanged.emit()

    def audio_devices(self):
        out = []
        devs = self.mp.audio_output_device_enum()
        if devs:
            d = devs
            while d:
                dev = d.contents
                out.append((_dec(dev.device), _dec(dev.description)))
                d = dev.next
            vlc.libvlc_audio_output_device_list_release(devs)
        return out

    def audio_device(self):
        """The output picked in the app ("" = system default). libVLC only reports a new
        device once the stream has moved over, so asking it right after a pick gave the old one."""
        return self._device

    def set_audio_device(self, device):
        self._device = device or ""
        self.mp.audio_output_device_set(None, self._device)
        self.store.set("audio_device", self._device or "System default")
        self.tracksChanged.emit()

    def audio_delay(self):
        return self.fx["audio_delay"]

    def test_stereo(self):
        """Play a short tone in the left, then the right channel through the current setup."""
        path = CACHE_DIR / "stereo-test.wav"
        if not path.exists():
            path.parent.mkdir(parents=True, exist_ok=True)
            rate = 44100
            frames = []
            import math
            for side in (0, 1):
                for n in range(int(rate * 0.45)):
                    v = int(9000 * math.sin(2 * math.pi * 660 * n / rate) * min(1, n / 800, (rate * 0.45 - n) / 800))
                    frames.append(struct.pack("<hh", v if side == 0 else 0, v if side == 1 else 0))
                frames.append(b"\0\0\0\0" * int(rate * 0.15))
            with wave.open(str(path), "wb") as w:
                w.setnchannels(2)
                w.setsampwidth(2)
                w.setframerate(rate)
                w.writeframes(b"".join(frames))
        tp = self.instance.media_player_new()
        tmedia = self.instance.media_new_path(str(path))
        tp.set_media(tmedia)
        tmedia.release()
        tp.audio_set_volume(min(100, self._volume or 60))
        tp.play()
        ch = CHANNELS.get(self.fx["stereo_mode"])
        if ch:
            QTimer.singleShot(150, lambda: tp.audio_set_channel(ch))
        QTimer.singleShot(1800, lambda: (tp.stop(), tp.release()))
        self.message.emit("Testing: left, then right", "")

    # --- video / subtitles --------------------------------------------------------
    def video_tracks(self):
        return [(i, _dec(n)) for i, n in (self.mp.video_get_track_description() or [])]

    def video_track(self):
        return self.mp.video_get_track()

    def set_video_track(self, tid):
        self.mp.video_set_track(tid)
        self.tracksChanged.emit()

    def spu_tracks(self):
        hidden = set(self._plain_es.values())       # our plain-text copies stand in for their originals
        return [(i, _dec(n)) for i, n in (self.mp.video_get_spu_description() or []) if i not in hidden]

    def spu(self):
        cur = self.mp.video_get_spu()
        if self._plain_on is not None and cur == self._plain_es.get((self.current, self._plain_on)):
            return self._plain_on
        return cur

    def _wanted_tracks(self):
        """(audio, subtitle) the user has, including while a rebuild is still starting up."""
        if self._restore:
            return self._restore
        if self._hold:
            return self._hold["audio"], self._hold["spu"]
        if self._state == "opening":
            return None, None       # nothing chosen yet (-1 would mean "off"); let libVLC pick
        return self.mp.audio_get_track(), self.spu()

    def _keep_tracks(self):
        """After a rebuild libVLC picks tracks again (an .srt next to the file wins as soon as
        it loads); keep putting back the ones that were showing until playback settles."""
        h = self._hold
        if not h or self._restore:
            return
        if time.monotonic() > h["until"]:
            self._hold = None
            return
        if h["audio"] is not None and h["audio"] != self.mp.audio_get_track():
            self.mp.audio_set_track(h["audio"])
        tid = h["spu"]
        if tid is None:
            return
        plain = None
        if self._plain_want == tid and tid >= 0:
            plain = self._plain_es.get((self.current, tid))
            if plain is None:
                self._show_plain(tid)
        target = plain if plain is not None else tid
        if self.mp.video_get_spu() != target:
            self.mp.video_set_spu(target)
            self._plain_on = tid if plain is not None else None
            self.tracksChanged.emit()

    def set_spu(self, tid):
        if self._hold:
            self._hold["spu"] = tid
        self._plain_on = None
        self.mp.video_set_spu(tid)
        self._plain_want = tid if tid >= 0 and not self.fx["sub_embedded_format"] and \
            self.spu_kind(tid) == "styled" else None
        if self._plain_want is not None:
            self._show_plain(tid)
        self.tracksChanged.emit()

    # --- subtitle styling ----------------------------------------------------------
    # libVLC draws .ass/.ssa tracks with libass in the file's own style and ignores the
    # font/colour/size options, and picture subtitles (DVD, Blu-ray) can't be restyled at
    # all. With "Embedded Sub Format" off, an .ass track is converted to plain text once
    # (ffmpeg, cached) and shown in its place, so the style settings reach it.
    def _spu_codecs(self):
        """Subtitle track id -> codec for the current item (remembered while a rebuild restarts it)."""
        known = self._codecs.setdefault(self.current, {})
        media = self.mp.get_media()
        if media:
            try:
                known.update({t.id: t.codec.to_bytes(4, "little").decode("latin1") for t in (media.tracks_get() or [])
                              if getattr(t.type, "value", t.type) == 2})
            except Exception:
                pass
            finally:
                media.release()
        return known

    def spu_kind(self, tid=None):
        """"text", "styled" (.ass) or "picture" for a subtitle track, "" if none/unknown."""
        tid = self._wanted_tracks()[1] if tid is None else tid
        if tid is None or tid < 0:
            return ""
        codec = self._spu_codecs().get(tid, "")
        if codec in STYLED_SUBS:
            return "styled"
        if codec in PICTURE_SUBS:
            return "picture"
        return "text" if codec else ""

    def sub_style_note(self):
        kind = self.spu_kind() if self.is_active() else ""
        if kind == "picture":
            return ("This subtitle track is pictures (from a DVD or Blu-ray), so its font, colour and size can't change. "
                    "Add an .srt file to use your style.")
        if kind == "styled" and self.fx["sub_embedded_format"]:
            return "This track has its own styling. Changing the style below shows it as plain text with yours."
        return ""

    def _plain_source(self, tid):
        """(file, embedded track ids or [] for a separate subtitle file) holding track tid."""
        visible = [i for i, _ in self.spu_tracks() if i >= 0]
        ext = self.external_subtitles()
        n_ext = min(len(ext), len(visible))
        embedded = visible[:len(visible) - n_ext] if n_ext else visible
        if tid in embedded:
            return self.current, embedded
        if tid in visible:
            return ext[n_ext - (len(visible) - visible.index(tid))], []
        return None

    def _show_plain(self, tid):
        item = self.current
        key = (item, tid)
        es = self._plain_es.get(key)
        if es is not None:
            self.mp.video_set_spu(es)
            self._plain_on = tid
            self.tracksChanged.emit()
            return
        if key in self._plain_busy:
            return
        src = self._plain_source(tid)
        try:
            st = os.stat(src[0]) if src else None
        except OSError:
            st = None
        if not st:
            self.message.emit("Couldn't convert these subtitles to plain text", "")
            return
        self._plain_busy.add(key)
        name = hashlib.sha1(f"{src[0]}|{st.st_size}|{st.st_mtime}|{tid}|{src[1]}".encode()).hexdigest() + ".srt"
        dest = PLAIN_SUBS / name
        if dest.exists():
            self._plain_ready(key, str(dest))
            return
        PLAIN_SUBS.mkdir(parents=True, exist_ok=True)

        def work():
            self._plainReady.emit(key, str(dest) if make_plain_subs(src[0], tid, src[1], dest) else "")
        threading.Thread(target=work, daemon=True).start()

    def _plain_ready(self, key, path):
        item, tid = key
        if item != self.current or self._plain_want != tid or not path:
            self._plain_busy.discard(key)
            if path == "" and item == self.current:
                self.message.emit("Couldn't convert these subtitles to plain text", "")
            return
        before = {i for i, _ in (self.mp.video_get_spu_description() or [])}
        if self.mp.add_slave(vlc.MediaSlaveType.subtitle, Path(path).as_uri(), True) != 0:
            self._plain_busy.discard(key)
            return
        tries = [0]

        def find():
            # the new track appears a moment after it's added
            if item != self.current:
                return
            new = {i for i, _ in (self.mp.video_get_spu_description() or [])} - before
            if new:
                self._plain_busy.discard(key)
                self._plain_es[key] = max(new)
                if self._plain_want == tid:
                    self.mp.video_set_spu(self._plain_es[key])
                    self._plain_on = tid
                else:
                    self.mp.video_set_spu(self._plain_want if self._plain_want is not None else tid)
                self.tracksChanged.emit()
            elif tries[0] < 100:
                tries[0] += 1
                QTimer.singleShot(100, find)
            else:
                self._plain_busy.discard(key)
        QTimer.singleShot(100, find)

    def _style_touched(self, key):
        """A style setting changed: make sure it shows, or say why it can't."""
        if not self.is_active():
            return
        kind = self.spu_kind()
        if kind == "picture" and key != "sub_offset":
            self.message.emit("These subtitles are pictures, so their style can't change", "")
        elif kind == "styled" and self.fx["sub_embedded_format"]:
            self.fx["sub_embedded_format"] = False
            self.fxChanged.emit("sub_embedded_format")
            self._plain_want = self._wanted_tracks()[1]
            self.message.emit("Showing these subtitles in your style", "")

    def _set_embedded_format(self, on):
        if on:
            orig, self._plain_on, self._plain_want = self._plain_on, None, None
            if orig is not None:
                self.mp.video_set_spu(orig)
        elif self.spu_kind() == "styled":
            self._plain_want = self._wanted_tracks()[1]
            self._show_plain(self._plain_want)
        self.tracksChanged.emit()

    def cycle_spu(self, key=""):
        tracks = self.spu_tracks()
        if len(tracks) < 2:
            self.message.emit("No subtitles", key)
            return
        ids = [t for t, _ in tracks]
        cur = self.spu()
        nxt = ids[(ids.index(cur) + 1) % len(ids)] if cur in ids else ids[0]
        self.set_spu(nxt)
        self.message.emit("Subtitles: " + dict(tracks)[nxt], key)

    def cycle_audio(self, key=""):
        tracks = [(i, n) for i, n in self.audio_tracks() if i != -1]
        if len(tracks) < 2:
            self.message.emit("Only one audio track", key)
            return
        ids = [t for t, _ in tracks]
        cur = self.audio_track()
        nxt = ids[(ids.index(cur) + 1) % len(ids)] if cur in ids else ids[0]
        self.set_audio_track(nxt)
        self.message.emit("Audio: " + dict(tracks)[nxt], key)

    def add_subtitle(self, path, remember=True):
        if not self.current:
            return False
        ok = self.mp.add_slave(vlc.MediaSlaveType.subtitle, Path(path).as_uri(), True) == 0
        if ok and remember and not is_url(self.current):
            self.store.add_external_sub(self.current, path)
        self.message.emit(f"Subtitles: {Path(path).name}" if ok else "Couldn't load that subtitle file", "")
        QTimer.singleShot(600, self.tracksChanged.emit)
        return ok

    def add_audio_file(self, path):
        if not self.current:
            return False
        ok = self.mp.add_slave(vlc.MediaSlaveType.audio, Path(path).as_uri(), True) == 0
        self.message.emit(f"Audio track: {Path(path).name}" if ok else "Couldn't load that audio file", "")
        QTimer.singleShot(600, self.tracksChanged.emit)
        return ok

    def external_subtitles(self):
        """Subtitle files loaded for the current item, in the order libVLC adds them."""
        item = self.current
        if not item or is_url(item):
            return []
        auto = self._autodetected(item)
        return auto + [s for s in self.store.external_subs(item) if s not in auto]

    def _autodetected(self, item):
        if not self.store.get("sub_autoload") or is_url(item):
            return []
        from .media import find_subtitles
        return find_subtitles(item)

    def video_size(self):
        try:
            return self.mp.video_get_size(0)
        except Exception:
            return (0, 0)

    def chapters(self):
        out = []
        try:
            descr = self.mp.get_full_chapter_descriptions(-1)
        except Exception:
            descr = None
        if descr:
            for i, ch in enumerate(descr):
                out.append((i, _dec(ch.name) or f"Chapter {i + 1}", int(ch.time_offset)))
        return out

    def set_chapter(self, i):
        self.mp.set_chapter(i)

    def stats(self):
        media = self.mp.get_media()
        if not media:
            return {}
        st = vlc.MediaStats()
        try:
            media.get_stats(st)
        except Exception:
            return {}
        finally:
            media.release()
        w, h = self.video_size()
        return {
            "Input bitrate": f"{st.input_bitrate * 8000:.0f} kb/s",
            "Demux bitrate": f"{st.demux_bitrate * 8000:.0f} kb/s",
            "Read": f"{st.read_bytes / 1048576:.1f} MiB",
            "Decoded video": f"{st.decoded_video} blocks",
            "Displayed frames": str(st.displayed_pictures),
            "Lost frames": str(st.lost_pictures),
            "Decoded audio": f"{st.decoded_audio} blocks",
            "Played audio buffers": str(st.played_abuffers),
            "Lost audio buffers": str(st.lost_abuffers),
            "Corrupted": str(st.demux_corrupted),
            "Resolution": f"{w}×{h}" if w else "-",
            "Frame rate": f"{self.mp.get_fps():.3g} fps" if self.mp.get_fps() else "-",
        }

    def snapshot(self):
        if not self._has_video or not self.is_active():
            return None
        s = self.store.get
        folder = Path(os.path.expanduser(s("snap_dir") or "~/Pictures"))
        folder.mkdir(parents=True, exist_ok=True)
        extn = "jpg" if s("snap_format") == "JPG" else "png"
        prefix = s("snap_prefix") or ""
        if s("snap_sequential"):
            n = 1
            while (folder / f"{prefix}{n:04d}.{extn}").exists():
                n += 1
            name = f"{prefix}{n:04d}.{extn}"
        else:
            name = f"{prefix}{datetime.now():%Y-%m-%d-%Hh%Mm%Ss}.{extn}"
        path = folder / name
        width = {"Same as video": 0, "1920 wide": 1920, "1280 wide": 1280, "640 wide": 640}.get(s("snap_size"), 0)
        if self.mp.video_take_snapshot(0, str(path), width, 0) == 0:
            self.message.emit(f"Snapshot saved: {name}", "")
            return str(path)
        self.message.emit("Couldn't take a snapshot", "")
        return None

    def info(self):
        item = self.current
        if not item:
            return {}
        w, h = self.video_size()
        fps = 0
        try:
            fps = self.mp.get_fps()
        except Exception:
            pass
        return {
            "Title": self.title(),
            "Artist": self.artist(),
            "Album": self.album(),
            "Location": item,
            "Duration": fmt_time(self.mp.get_length()),
            "Resolution": f"{w} × {h}" if w and h else "",
            "Frame rate": f"{fps:.3g} fps" if fps else "",
            "Audio tracks": str(len([t for t in self.audio_tracks() if t[0] != -1])),
            "Subtitle tracks": str(len([t for t in self.spu_tracks() if t[0] != -1])),
        }

    # --- modes ---------------------------------------------------------------------
    def cycle_repeat(self):
        self.set_repeat({"off": "all", "all": "one", "one": "off"}[self.repeat])
        self.message.emit({"off": "Repeat: off", "all": "Repeat: all", "one": "Repeat: this item"}[self.repeat], "")

    def set_repeat(self, mode):
        if mode != self.repeat:
            self.repeat = mode
            self.modesChanged.emit()

    def toggle_shuffle(self, announce=True):
        self.shuffle = not self.shuffle
        self._history = [self.index] if self.shuffle and self.index >= 0 else []
        self.modesChanged.emit()
        if announce:
            self.message.emit("Shuffle: on" if self.shuffle else "Shuffle: off", "")

    def toggle_stop_after(self):
        self.stop_after = not self.stop_after
        self.modesChanged.emit()

    # --- sleep timer -------------------------------------------------------------
    def set_sleep(self, option):
        self.fx["sleep"] = option
        self._sleep_timer.stop()
        self._sleep_end_of_item = False
        minutes = {"15 minutes": 15, "30 minutes": 30, "45 minutes": 45, "1 hour": 60, "1.5 hours": 90,
                   "2 hours": 120}.get(option)
        if minutes:
            self._sleep_timer.start(minutes * 60_000)
            self.message.emit(f"Sleep timer: {option}", "")
        elif option == "End of current item":
            self._sleep_end_of_item = True
            self.message.emit("Playback stops after this item", "")
        self.sleepChanged.emit(option)
        self.fxChanged.emit("sleep")

    def _sleep_fire(self):
        self.pause()
        self.fx["sleep"] = "Off"
        self.sleepChanged.emit("Off")
        self.fxChanged.emit("sleep")
        self.message.emit("Sleep timer: paused", "")

    # --- recording (ffmpeg) ----------------------------------------------------------
    def is_recording(self):
        return self._record is not None

    def toggle_record(self):
        if self._record:
            self.stop_record()
        else:
            self.start_record()

    def start_record(self):
        item = self.current
        if not item or not self.is_active():
            self.message.emit("Nothing to record", "")
            return
        if not shutil.which("ffmpeg"):
            self.message.emit("Recording needs ffmpeg", "")
            return
        rec = {"item": item, "start": self._last[0], "proc": None, "out": None}
        if is_url(item):
            # Live streams: capture from now on, in parallel with playback.
            out = self._record_path(item, "ts")
            proc = QProcess(self)
            proc.start("ffmpeg", ["-nostdin", "-loglevel", "error", "-y", "-i", item, "-c", "copy", out])
            rec.update(proc=proc, out=out)
        self._record = rec
        self.recordingChanged.emit(True)
        self.message.emit("Recording", "")

    def stop_record(self):
        rec = self._record
        if not rec:
            return
        self._record = None
        self.recordingChanged.emit(False)
        if rec["proc"]:
            rec["proc"].write(b"q")
            rec["proc"].terminate()
            self.message.emit(f"Recording saved: {os.path.basename(rec['out'])}", "")
            return
        item = rec["item"]
        start, end = rec["start"], self._last[0] if self.current == item else self._last[1]
        if end - start < 500:
            self.message.emit("Recording too short", "")
            return
        copy_streams = self.store.get("record_copy")
        out = self._record_path(item, ext(item) if copy_streams else "mp4")
        args = ["-nostdin", "-loglevel", "error", "-y", "-ss", f"{start / 1000:.3f}", "-to", f"{end / 1000:.3f}",
                "-i", item]
        args += ["-map", "0", "-c", "copy"] if copy_streams else ["-c:v", "libx264", "-preset", "veryfast", "-c:a", "aac"]
        args.append(out)
        proc = QProcess(self)
        proc.finished.connect(lambda code, _s, o=out: self.message.emit(
            f"Recording saved: {os.path.basename(o)}" if code == 0 else "Couldn't save the recording", ""))
        proc.start("ffmpeg", args)
        self.message.emit("Saving recording…", "")

    def _record_path(self, item, extension):
        folder = Path(os.path.expanduser(self.store.get("record_dir") or "~/Videos"))
        folder.mkdir(parents=True, exist_ok=True)
        base = re.sub(r"[^\w .-]+", "_", os.path.splitext(display_name(item))[0])[:60] or "stream"
        return str(folder / f"vlc-record-{datetime.now():%Y-%m-%d-%Hh%Mm%Ss}-{base}.{extension or 'mkv'}")

    # --- internals ---------------------------------------------------------------------
    def _save_progress(self):
        item = self.current
        if item and not is_url(item) and self._has_video:
            t, length = self._last
            self.store.set_progress(item, t, length)

    def _poll(self):
        state = STATES.get(self.mp.get_state(), "stopped")
        if state != self._state:
            prev = self._state
            self._state = state
            if state == "playing":
                self.mp.audio_set_volume(self._volume)
                self.mp.audio_set_mute(self._muted)
                if prev == "opening" or prev in ("stopped", "ended", "error"):
                    self._on_started()
                if self._pause_on_start:
                    self._pause_on_start = False
                    self.mp.set_pause(1)
            self.stateChanged.emit(state)
            if state == "ended":
                self._on_ended()
                return
            if state == "error":
                msg = f"Couldn't play “{self.title()}”"
                self.error.emit(msg)
                if self.index + 1 < len(self.items):
                    QTimer.singleShot(400, lambda: self.next(auto=True))
                return

        if state in ("playing", "paused"):
            self._keep_tracks()
            t, length = self.mp.get_time(), self.mp.get_length()
            if t >= 0:
                self._last = (t, length)
                self.tick.emit(t, length)
                if length > 0 and self.current:
                    self.meta.setdefault(self.current, {})["length"] = length
            if not self._video_checked and t > 0:
                self._video_checked = True
                has_video = self.mp.video_get_track_count() > 0
                if has_video != self._has_video:
                    self._has_video = has_video
                    self.videoChanged.emit(has_video)
                self._apply_runtime()
            if not self._meta_checked and t > 0:
                self._meta_checked = True
                self._read_meta()
            if self._tracks_checked < 3 and t > 0:
                self._tracks_checked += 1
                if self._tracks_checked == 2 and not self.fx["sub_embedded_format"] and self._plain_want is None \
                        and self._plain_on is None and self.spu_kind() == "styled":
                    self._plain_want = self.spu()       # the track libVLC picked is .ass: show it in our style
                    self._show_plain(self._plain_want)
                self.tracksChanged.emit()
            now = time.monotonic()
            if now - self._last_save > 5:
                self._last_save = now
                self._save_progress()
            if now - getattr(self, "_last_trim", 0) > 45:
                self._last_trim = now
                trim_memory()

    def _on_started(self):
        self._trim_timer.start()
        start = getattr(self, "_start_ms", 0)
        length = self.mp.get_length()
        if start and length > 0 and start > length - 5000:
            # A saved position past the end (stale or from another file): start over.
            self._start_ms = 0
            if self.current:
                self.store.forget_progress(self.current)
            self.mp.set_time(0)
        self.mp.audio_set_delay(int(self.fx["audio_delay"] * 1_000_000))
        self.mp.video_set_spu_delay(int(self.fx["sub_delay"] * 1_000_000))
        if abs(self.fx["speed"] - 1.0) > 0.01:
            self.mp.set_rate(self.fx["speed"])
        if self._restore:
            atrack, strack = self._restore
            self._restore = None
            QTimer.singleShot(300, lambda: self._restore_tracks(atrack, strack))
        QTimer.singleShot(200, self._apply_runtime)

    def _restore_tracks(self, atrack, strack):
        if atrack is not None:
            self.mp.audio_set_track(atrack)
        if strack is not None:
            self.mp.video_set_spu(strack)
            if self._plain_want == strack:
                self._show_plain(strack)

    def _on_ended(self):
        item = self.current
        if item and self._has_video:
            self.store.forget_progress(item)
            self.store.mark_watched(item, True)
        if self._record:
            self.stop_record()
        if self._sleep_end_of_item:
            self._sleep_end_of_item = False
            self.fx["sleep"] = "Off"
            self.sleepChanged.emit("Off")
            self.mediaChanged.emit()
            return
        if self.repeat == "one":
            self.play_index(self.index, resume=False)
            return
        if self.stop_after:
            self.mediaChanged.emit()
            return
        last = self.index + 1 >= len(self.items) and self.repeat != "all" and not (self.shuffle and len(self.items) > 1)
        if last:
            self.finished_all = True
            self.mediaChanged.emit()
            return
        self.next(auto=True)

    def _read_meta(self):
        media = self.mp.get_media()
        item = self.current
        if not media:
            return
        if not item:
            media.release()
            return
        title = _dec(media.get_meta(vlc.Meta.Title))
        artist = _dec(media.get_meta(vlc.Meta.Artist))
        album = _dec(media.get_meta(vlc.Meta.Album))
        art = _dec(media.get_meta(vlc.Meta.ArtworkURL))
        media.release()
        if art.startswith("file://"):
            art = unquote(urlparse(art).path)
        else:
            art = ""
        if title and not is_url(item) and title in (os.path.basename(item), Path(item).stem):
            title = ""
        if title and is_url(item) and (title == display_name(item) or title.startswith(display_name(item) + "?")
                                       or title.startswith(("http://", "https://"))):
            title = ""
        entry = self.meta.setdefault(item, {})
        entry.update({"title": title, "artist": artist, "album": album, "art": art})
        if artist:
            self.store.count_artist(artist)
        self.metaChanged.emit()

    def shutdown(self):
        self._save_progress()
        self._timer.stop()
        self.stop_record()
        self.mp.stop()
        self.store.save()


def _dec(value):
    if isinstance(value, bytes):
        return value.decode("utf-8", "replace")
    return value or ""
