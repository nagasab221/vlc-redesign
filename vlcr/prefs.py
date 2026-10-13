"""Every preference in the Settings dialog, in the order the design lists them.

Each row knows its default, how it is edited and how it takes effect:
  apply="ui"        the window reacts to the change
  apply="runtime"   the engine applies it to the running player
  apply="instance"  libVLC must be restarted with new options (playback resumes where it was)
  apply="none"      stored only (used the next time it matters)
Rows the player cannot honour are kept for completeness but disabled, with the reason
shown in their info tip.
"""

from pathlib import Path

HOME = str(Path.home())


class Row:
    def __init__(self, key, label, kind, default=None, info="", options=None, apply="none",
                 advanced=False, disabled="", **extra):
        self.key = key
        self.label = label
        self.kind = kind            # toggle dropdown segmented slider number text path button accent color eq startvol
        self.default = default
        self.info = info
        self.options = options or []
        self.apply = apply
        self.advanced = advanced
        self.disabled = disabled    # non-empty: why the row can't be used
        self.extra = extra
        self.category = None
        self.section = None


class Section:
    def __init__(self, title, rows, advanced=False):
        self.title = title
        self.rows = rows
        self.advanced = advanced


class Category:
    def __init__(self, key, group, label, icon, title, sections):
        self.key = key
        self.group = group
        self.label = label
        self.icon = icon
        self.title = title
        self.sections = sections
        for s in sections:
            for r in s.rows:
                r.category = self
                r.section = s
                if s.advanced:
                    r.advanced = True


NOT_IN_LIBVLC = "Not available when VLC is used as a library (libVLC 3)."

EQ_PRESETS = ["None", "Flat", "Classical", "Club", "Dance", "Full bass", "Full bass and treble", "Full treble",
              "Headphones", "Large hall", "Live", "Party", "Pop", "Reggae", "Rock", "Ska", "Soft", "Soft rock", "Techno"]
EQ_BANDS = ["60 Hz", "170 Hz", "310 Hz", "600 Hz", "1 KHz", "3 KHz", "6 KHz", "12 KHz", "14 KHz", "16 KHz"]
VISUALIZERS = ["Disabled", "Spectometer", "Scope", "Spectrum", "Waveform"]
DEINTERLACE_METHODS = ["Automatic", "Blend", "Bob", "Discard", "Linear", "Mean", "X", "Yadif", "Yadif (2x)",
                       "Phosphor", "Film NTSC (IVTC)"]
ASPECTS = ["Automatic", "16:9", "4:3", "1:1", "16:10", "2.21:1", "2.35:1", "2.39:1", "5:4"]
CROPS = ["None", "16:9", "4:3", "1:1", "16:10", "2.21:1", "2.35:1", "2.39:1", "5:4"]
ZOOMS = ["Fit", "1:4 Quarter", "1:2 Half", "1:1 Original", "2:1 Double"]
FUZZY = ["Off", "Any title", "Contains title", "Title with extra characters", "Exact title match"]
CLICK_ACTIONS = ["Play / Pause", "Show controls", "Toggle fullscreen", "Nothing"]
DBL_ACTIONS = ["Toggle fullscreen", "Play / Pause", "Nothing"]
WHEEL_ACTIONS = ["Volume", "Seek", "Nothing"]

CATEGORIES = [
    Category("general", "App", "General", "preferences", "Global Settings", [
        Section("Appearance", [
            Row("language", "Interface language", "dropdown", "English", options=["English"],
                info="The language of menus and dialogs. This player is available in English only."),
            Row("theme", "Theme", "segmented", "dark", options=[("dark", "Dark"), ("light", "Light"), ("system", "System")],
                apply="ui", info="Dark or light interface. System follows your desktop theme."),
            Row("accent", "Accent color", "accent", "#d37918", apply="ui",
                info="The highlight colour used for the play button, seek bar and active icons."),
        ]),
        Section("Startup", [
            Row("show_dialogs", "Show confirmation and error dialogs", "toggle", False, apply="ui",
                info="Show errors in a dialog that waits for you instead of a short notice at the bottom."),
            Row("check_updates", "Check for updates automatically", "toggle", False,
                disabled="This player is updated with its project folder; there is nothing to check online.",
                info="Look for a newer version when the app starts."),
        ]),
        Section("Diagnostics", [
            Row("developer_mode", "Developer mode", "toggle", False, apply="ui",
                info="Shows the Developer Options below and adds technical details to Properties & Metadata."),
            Row("save_log", "Save a log", "toggle", True, apply="instance",
                info="Write libVLC's messages to a log file. Useful when a file won't play."),
            Row("log_file", "Log file", "path", "", apply="instance", mode="save",
                info="Where the log is written. Empty means ~/.cache/vlc-redesign/vlc-log.txt."),
            Row("log_details", "Log details", "dropdown", "Errors only", options=["Errors only", "Warnings", "Everything"],
                apply="instance", info="How much libVLC writes to the log."),
        ]),
        Section("Advanced Options", [
            Row("minor_warnings", "Show minor warnings", "toggle", False, apply="ui",
                info="Also show notices for problems that don't stop playback."),
            Row("extra_args", "Additional command line options", "text", "", apply="instance",
                info="Extra libVLC options, as you would pass them to vlc on the command line (for example --no-osd)."),
            Row("log_format", "Log format", "dropdown", "Plain text", options=["Plain text", "HTML"], apply="instance",
                info="File format of the log."),
            Row("collect_stats", "Collect playback statistics", "toggle", True, apply="instance",
                info="Needed for Show Statistics in the playback panel and for Properties & Metadata."),
            Row("open_config", "Open configurations folder", "button", "Open",
                info="Opens the folder that holds this player's settings, playlists and resume points."),
            Row("reset_all", "Reset all settings", "button", "Reset",
                info="Restore every setting of the current profile to its default. Your library, playlists and bookmarks are kept."),
        ], advanced=True),
        Section("Developer Options", [
            Row("syslog", "Write to the system log", "toggle", False, apply="instance",
                info="Send libVLC's messages to syslog as well."),
            Row("syslog_debug", "Include debug messages in the system log", "toggle", False, apply="instance",
                info="Also send debug-level messages to syslog."),
            Row("syslog_ident", "System log identity", "text", "vlc", apply="instance",
                info="The name messages appear under in the system log."),
            Row("syslog_facility", "System log facility", "dropdown", "user",
                options=["user", "daemon", "local0", "local1", "local2", "local3", "local4", "local5", "local6", "local7"],
                apply="instance", info="The syslog facility used for messages."),
        ], advanced=True),
    ]),
    Category("interface", "App", "Interface", "interface", "Interface Settings", [
        Section("Skin", [
            Row("custom_skin", "Use a custom skin", "toggle", False, disabled=NOT_IN_LIBVLC + " Skins only work in VLC's skins2 interface.",
                info="Use a VLC skin file (.vlt) instead of this interface."),
            Row("skin_file", "Skin file", "path", "", disabled=NOT_IN_LIBVLC, info="The .vlt skin to use."),
        ]),
        Section("Window", [
            Row("video_in_window", "Play video inside main window", "toggle", True, apply="ui",
                info="When off, video opens in its own window and the main window stays on the library."),
            Row("resize_to_video", "Resize window to fit video", "dropdown", "Never",
                options=["Never", "First video only", "Every video"], apply="ui",
                info="Change the window size to match the video's resolution when it starts."),
            Row("remember_geometry", "Remember window size and position", "toggle", True, apply="ui"),
            Row("on_top", "Keep window above other windows", "toggle", False, apply="ui",
                info="Keep the player above other windows."),
            Row("decorations", "Show window decorations", "toggle", True, apply="ui",
                info="Show the title bar with the window buttons. When off, move the window by dragging the video."),
            Row("title_filename", "Show the file name in the title bar", "toggle", True, apply="ui",
                info="Show what's playing in the middle of the title bar."),
            Row("fullscreen_screen", "Open fullscreen on", "dropdown", "Current screen", options=["Current screen"],
                apply="ui", dynamic="screens", info="Which screen fullscreen uses."),
        ]),
        Section("Playback Controls", [
            Row("compact_start", "Start in compact mode", "toggle", False, apply="ui",
                info="Start with the interface hidden (Ctrl+\\). Hover the top or bottom edge to bring the bars back."),
            Row("autohide", "Auto-hide the controls", "dropdown", "In fullscreen only",
                options=["In fullscreen only", "Always", "Never"], apply="ui",
                info="Always also hides the bars in a window: they appear when you hover the top or bottom edge."),
            Row("hide_after", "Hide after", "slider", 0.5, apply="ui", min=0.0, max=10.0, step=0.5, ticks=21,
                labels=["0 s", "5 s", "10s"], suffix=" s",
                info="How long the bars stay after the mouse leaves them."),
            Row("fs_controls", "Show controls in fullscreen", "toggle", True, apply="ui",
                info="Show the title bar and control bar when you hover the edges in fullscreen."),
        ]),
        Section("Notifications and System Tray", [
            Row("tray_icon", "Show a system tray icon", "toggle", True, apply="ui",
                info="An icon in the panel tray with playback controls."),
            Row("start_in_tray", "Start minimized to the tray", "toggle", False, apply="ui",
                info="Start hidden; click the tray icon to show the window."),
            Row("track_popup", "Show a popup when the track changes", "dropdown", "When minimized",
                options=["When minimized", "Always", "Never"], apply="ui",
                info="A desktop notification with the title of the new track."),
            Row("popup_duration", "Popup duration", "slider", 3.0, apply="ui", min=0.0, max=10.0, step=0.5, ticks=21,
                labels=["0 s", "5 s", "10s"], suffix=" s"),
        ]),
        Section("Playlist", [
            Row("queue_tree", "Show the playlist as a tree", "toggle", True, apply="ui",
                info="Group the queue by folder."),
        ]),
        Section("Advanced Options", [
            Row("window_opacity", "Window opacity", "slider", 100, apply="ui", min=10, max=100, step=5, ticks=19,
                labels=["10%", "100%"], suffix="%"),
            Row("pause_minimized", "Pause video when window is minimized", "toggle", False, apply="ui"),
            Row("raise_on_play", "Bring VLC to the front when playback starts", "dropdown", "Video",
                options=["Video", "Audio", "Both", "Never"], apply="ui",
                info="Raise the window when this kind of media starts playing."),
            Row("cover_art", "Show cover art in the window when playing audio", "toggle", True, apply="ui"),
            Row("inapp_dialogs", "Show the file browser inside the open dialog", "toggle", True, apply="ui",
                info="Use this app's own file dialogs instead of the desktop's."),
            Row("icon_change", "Allow the app icon to change", "toggle", True, apply="ui",
                info="Show a small play/pause badge on the window icon."),
            Row("fs_opacity", "Fullscreen controls opacity", "slider", 95, apply="ui", min=10, max=100, step=5, ticks=19,
                labels=["10%", "100%"], suffix="%"),
            Row("fs_sensitivity", "Fullscreen controls sensitivity", "slider", 1, apply="ui", min=0, max=2, step=1, ticks=3,
                labels=["Low", "Medium", "High"], info="How close to the edge the mouse has to be to show the bars."),
        ], advanced=True),
    ]),
    Category("privacy", "App", "Privacy", "privacy", "Privacy Settings", [
        Section("History", [
            Row("save_recent", "Save recently played items", "toggle", True, apply="ui",
                info="Keep a list of what you played for Recent, Continue Watching and Listen Again."),
            Row("remember_position", "Remember the playback position", "toggle", True,
                info="Remember where you stopped in each video."),
            Row("never_remember", "Never remember items matching", "text", "",
                info="Comma-separated words or wildcards (e.g. *private*, /tmp/*). Matching files are left out of history."),
            Row("clear_history", "Clear history now", "button", "Clear",
                info="Forget recently played items and saved positions."),
        ]),
        Section("Network", [
            Row("fetch_online", "Fetch titles and cover art from the internet", "toggle", False, apply="instance",
                info="Let libVLC look up missing album art online."),
            Row("lastfm", "Submit played tracks to last.fm", "toggle", False, apply="instance",
                info="Scrobble the music you play to your last.fm account."),
            Row("lastfm_user", "Last.fm username", "text", "", apply="instance"),
            Row("lastfm_pass", "Last.fm password", "text", "", apply="instance", password=True),
            Row("lastfm_url", "Last.fm submission URL", "text", "post.audioscrobbler.com", apply="instance",
                info="Change this only for a compatible service such as libre.fm."),
        ]),
    ]),
    Category("controls", "App", "Controls", "hotkeys", "Mouse, Keyboard & Remote Controls", [
        Section("Keyboard Shortcuts", [
            Row("edit_shortcuts", "Defaults", "button", "Edit Keyboard Shortcuts", wide=True),
            Row("reset_shortcuts", "Reset all shortcuts", "button", "Reset", wide=True),
        ]),
        Section("Skip Amounts", [
            Row("skip_vshort", "Very short skip", "number", 3, min=1, max=60, suffix=" s",
                info="Shift+Left / Shift+Right."),
            Row("skip_short", "Short skip", "number", 10, min=1, max=600, suffix=" s",
                info="Left / Right, and the 10-second buttons."),
            Row("skip_medium", "Medium skip", "number", 60, min=1, max=3600, suffix=" s", info="Ctrl+Left / Ctrl+Right."),
            Row("skip_long", "Long skip", "number", 300, min=1, max=7200, suffix=" s", info="Alt+Left / Alt+Right."),
        ]),
        Section("Mouse", [
            Row("click_action", "Single click on the video", "dropdown", "Play / Pause", options=CLICK_ACTIONS),
            Row("dblclick_action", "Double click on the video", "dropdown", "Toggle fullscreen", options=DBL_ACTIONS),
            Row("wheel_action", "Scroll wheel", "dropdown", "Volume", options=WHEEL_ACTIONS),
            Row("hwheel_action", "Horizontal scroll", "dropdown", "Seek", options=WHEEL_ACTIONS),
            Row("reverse_scroll", "Reverse scroll direction", "toggle", False),
        ]),
        Section("Remotes", [
            Row("web_remote", "Web remote control", "toggle", False, apply="instance",
                info="Control playback from a browser on your network at http://<this computer>:<port>. Needs a password."),
            Row("web_port", "Port", "number", 8080, min=1024, max=65535, apply="instance"),
            Row("web_password", "Password", "text", "", apply="instance", password=True,
                info="The web remote asks for this password (leave the user name empty)."),
        ]),
    ]),
    Category("playback", "Media", "Playback", "playback", "Playback Preferences", [
        Section("Startup", [
            Row("continue", "Continue where you left off", "dropdown", "Always", options=["Always", "Ask", "Never"],
                info="Always resumes and offers Start Over; Ask offers Continue Watching; Never starts from the beginning."),
            Row("autoplay", "Start playing as soon as a file opens", "toggle", True,
                info="When off, files open paused on their first frame."),
            Row("default_speed", "Default playback speed", "slider", 1.0, min=0.25, max=2.0, step=0.25, ticks=8,
                labels=["0.25", "0.5", "0.75", "1", "1.25", "1.5", "1.75", "2"], suffix="×"),
            Row("pause_other_audio", "Pause when another app uses audio", "toggle", False, apply="ui",
                info="Pause while another application starts playing sound (PulseAudio / PipeWire)."),
        ]),
        Section("Tracks", [
            Row("preferred_quality", "Preferred video quality", "dropdown", "Best available",
                options=["Best available", "2160p", "1440p", "1080p", "720p", "576p", "480p", "360p", "240p"],
                apply="instance", info="For adaptive streams (HLS, DASH) and YouTube links."),
            Row("menu_language", "Preferred menu language", "dropdown", "English",
                options=["English", "Arabic", "Chinese", "French", "German", "Hindi", "Italian", "Japanese", "Korean",
                         "Portuguese", "Russian", "Spanish", "Turkish", "Urdu"],
                apply="instance", info="Language for DVD and Blu-ray menus."),
            Row("default_audio_track", "Default audio track", "number", 1, min=1, max=32, apply="instance",
                info="The audio track selected when a file starts (1 is the first)."),
            Row("default_sub_track", "Default subtitle track", "number", 0, min=0, max=32, apply="instance",
                info="The subtitle track selected when a file starts. 0 means the file's own choice."),
        ]),
        Section("Order", [
            Row("repeat", "Repeat", "dropdown", "Off", options=["Off", "All", "One"], apply="runtime",
                info="Repeat the queue (All) or the current item (One)."),
            Row("shuffle", "Shuffle", "toggle", False, apply="runtime"),
            Row("stop_after_each", "Stop after each item", "toggle", False, apply="runtime",
                info="Don't continue to the next item in the queue."),
            Row("when_finished", "When playback finishes", "dropdown", "Stop",
                options=["Stop", "Show the library", "Quit"], info="What happens after the last item."),
        ]),
        Section("Seeking", [
            Row("seek_precise", "Seek precisely", "toggle", True, apply="instance",
                info="Seek to the exact frame. Off is faster but lands on the nearest keyframe."),
            Row("seek_thumbs", "Show thumbnail while scrubbing", "toggle", True, apply="ui",
                info="Preview frames above the seek bar (needs ffmpeg)."),
        ]),
        Section("Files & Discs", [
            Row("optical_drive", "Default optical drive", "dropdown", "/dev/sr0", options=["/dev/sr0"], dynamic="drives",
                info="The drive used by Open Disc."),
            Row("repair_avi", "Repair damaged AVI files", "dropdown", "When needed",
                options=["When needed", "Always", "Never"], apply="instance",
                info="Rebuild the index of broken AVI files so they can be seeked."),
            Row("mkv_local", "Load matching .mkv files from the same folder", "toggle", True, apply="instance",
                info="Load MKV segments that belong to the same film (ordered chapters)."),
        ]),
        Section("Advanced Options", [
            Row("timeshift_dir", "Time-shift buffer folder", "path", "", mode="dir", apply="instance",
                info="Where paused live streams are buffered. Empty means the system temporary folder."),
            Row("timeshift_size", "Time-shift buffer size", "dropdown", "Medium", options=["Small", "Medium", "Large"],
                apply="instance"),
        ], advanced=True),
    ]),
    Category("video", "Media", "Video", "video", "Video Defaults", [
        Section("Output", [
            Row("enable_video", "Enable video", "toggle", True, apply="instance", info="When off, only sound is played."),
            Row("start_fullscreen", "Start videos in fullscreen", "toggle", False, apply="ui"),
            Row("stop_screensaver", "Stop the screensaver during playback", "toggle", True, apply="instance"),
            Row("wallpaper", "Play video as desktop wallpaper", "toggle", False,
                disabled="VLC supports wallpaper mode on Windows only.", info="Draw the video on the desktop background."),
        ]),
        Section("Picture", [
            Row("pic_hue", "Hue", "slider", 0, apply="runtime", min=-180, max=180, step=1, centered=True),
            Row("pic_brightness", "Brightness", "slider", 1.0, apply="runtime", min=0.0, max=2.0, step=0.01, centered=True),
            Row("pic_contrast", "Contrast", "slider", 1.0, apply="runtime", min=0.0, max=2.0, step=0.01, centered=True),
            Row("pic_saturation", "Saturation", "slider", 1.0, apply="runtime", min=0.0, max=3.0, step=0.01, mark=1.0),
            Row("pic_gamma", "Gamma", "slider", 1.0, apply="runtime", min=0.01, max=10.0, step=0.01, ticks=10),
            Row("pic_reset", "Reset picture", "button", "Reset"),
        ]),
        Section("Deinterlacing", [
            Row("deinterlace", "Deinterlacing", "dropdown", "Automatic", options=["Automatic", "On", "Off"], apply="runtime",
                info="Remove the comb lines of interlaced video (TV recordings, DVDs)."),
            Row("deinterlace_mode", "Method", "dropdown", "Automatic", options=DEINTERLACE_METHODS, apply="runtime",
                info="Yadif gives the best quality; Blend and Linear are cheaper."),
        ]),
        Section("Framing", [
            Row("aspect", "Aspect ratio", "dropdown", "Automatic", options=ASPECTS, apply="runtime"),
            Row("crop", "Crop", "dropdown", "None", options=CROPS, apply="runtime"),
            Row("autoscale", "Scale video to the window", "toggle", True, apply="runtime",
                info="When off, video is shown at its own size."),
            Row("zoom", "Zoom", "dropdown", "Fit", options=ZOOMS, apply="runtime"),
            Row("align", "Position in the window", "dropdown", "Center",
                options=["Center", "Top", "Bottom", "Left", "Right", "Top left", "Top right", "Bottom left", "Bottom right"],
                apply="instance", info="Where the video sits when it doesn't fill the window."),
        ]),
        Section("Snapshots", [
            Row("snap_dir", "Save to", "path", HOME + "/Pictures", mode="dir"),
            Row("snap_prefix", "File name prefix", "text", "vlcsnap-"),
            Row("snap_format", "Format", "dropdown", "PNG", options=["PNG", "JPG"]),
            Row("snap_size", "Size", "dropdown", "Same as video", options=["Same as video", "1920 wide", "1280 wide", "640 wide"]),
            Row("snap_sequential", "Number files sequentially", "toggle", False,
                info="Name snapshots 0001, 0002… instead of with the date and time."),
            Row("snap_preview", "Show a preview after capture", "toggle", True),
        ]),
        Section("Advanced Options", [
            Row("custom_aspects", "Custom aspect ratios", "text", "", apply="ui",
                info="Extra ratios for the aspect ratio menus, comma separated (e.g. 1.85:1, 3:2)."),
            Row("custom_crops", "Custom crop ratios", "text", "", apply="ui",
                info="Extra ratios for the crop menus, comma separated."),
            Row("monitor_par", "Monitor pixel aspect ratio", "dropdown", "1:1", options=["1:1", "4:3", "16:9", "16:10"],
                apply="instance", info="Only change this for displays with non-square pixels."),
        ], advanced=True),
    ]),
    Category("audio", "Media", "Audio", "audio", "Audio Defaults", [
        Section("Output", [
            Row("enable_audio", "Enable audio", "toggle", True, apply="instance"),
            Row("audio_device", "Output device", "dropdown", "System default", options=["System default"],
                dynamic="audio_devices", apply="runtime"),
            Row("speaker_setup", "Speaker setup", "dropdown", "Automatic",
                options=["Automatic", "Stereo", "Reverse stereo", "Left", "Right", "Dolby Surround"], apply="runtime",
                info="Remix the sound for your speakers."),
        ]),
        Section("Volume", [
            Row("max_volume", "Maximum volume", "slider", 200, min=100, max=200, step=10, ticks=11,
                labels=["100%", "150%", "200%"], suffix="%", apply="ui",
                info="How far the volume control goes. libVLC amplifies up to 200%."),
            Row("startup_volume", "Startup volume", "startvol", -1,
                info="Remember last volume, or always start at a fixed level."),
            Row("volume_step", "Volume step", "number", 5, min=1, max=25, suffix="%",
                info="How much the arrow keys and the scroll wheel change the volume."),
        ]),
        Section("Loudness", [
            Row("normalize", "Normalize loudness", "toggle", False, apply="instance",
                info="Keep quiet and loud parts closer together."),
            Row("norm_level", "Target level", "number", 2.0, min=0.5, max=10.0, step=0.5, apply="instance",
                info="Higher values allow more dynamic range."),
            Row("night_mode", "Night mode", "toggle", False, apply="instance",
                info="Compresses loud sounds so dialogue stays audible at low volume."),
            Row("replay_gain", "Replay gain", "dropdown", "Off", options=["Off", "Track", "Album"], apply="instance",
                info="Use ReplayGain tags to even out volume between songs."),
            Row("replay_preamp", "Replay gain preamp", "number", 0.0, min=-20.0, max=20.0, step=0.5, suffix=" dB",
                apply="instance"),
        ]),
        Section("Effects", [
            Row("eq_enabled", "Equalizer", "toggle", False, apply="runtime"),
            Row("eq_preset", "Equalizer preset", "dropdown", "None", options=EQ_PRESETS, apply="runtime", eqicons=True),
            Row("eq_bands", "", "eq", [12.0] + [0.0] * 10, apply="runtime"),
            Row("eq_2pass", "Two-pass", "toggle", False, apply="instance", info="Apply the equalizer twice for a stronger effect."),
            Row("spatial", "Spatial audio", "toggle", False, apply="instance",
                info="Simulate speakers around you."),
            Row("spatial_mode", "Spatial audio mode", "dropdown", "Headphone virtualization",
                options=["Headphone virtualization", "Room (spatializer)"], apply="instance"),
            Row("stereo_widen", "Stereo widening", "toggle", False, apply="instance"),
            Row("compressor", "Compressor", "toggle", False, apply="instance"),
            Row("pitch_shift", "Pitch shift", "number", 0.0, min=-12.0, max=12.0, step=0.5, suffix=" st", apply="instance",
                info="Change the pitch without changing the speed, in semitones."),
            Row("chorus", "Chorus/flanger", "toggle", False, apply="instance"),
            Row("karaoke", "Karaoke", "toggle", False, apply="instance", info="Remove the centre (usually the voice)."),
        ]),
        Section("Tracks", [
            Row("audio_language", "Preferred audio language", "dropdown", "System language",
                options=["System language", "English", "Arabic", "Chinese", "French", "German", "Hindi", "Italian",
                         "Japanese", "Korean", "Portuguese", "Russian", "Spanish", "Turkish", "Urdu"],
                apply="instance"),
            Row("audio_delay_default", "Audio delay", "number", 0.0, min=-10.0, max=10.0, step=0.05, suffix=" s",
                apply="runtime", info="Applied to every file; per-file delays are in the playback panel."),
        ]),
        Section("Visualization", [
            Row("visualizer", "Visualizer", "dropdown", "Disabled", options=VISUALIZERS, apply="instance", vizicons=True,
                info="Draw an animation while audio plays."),
        ]),
        Section("Advanced Options", [
            Row("spdif", "Pass compressed audio to the receiver", "toggle", False, apply="instance",
                info="Send Dolby/DTS untouched over S/PDIF or HDMI."),
            Row("dolby_detect", "Detect Dolby Surround", "dropdown", "Automatic", options=["Automatic", "On", "Off"],
                apply="instance"),
            Row("resampler", "Resampling quality", "dropdown", "Balanced", options=["Fast", "Balanced", "Best"],
                apply="instance"),
            Row("media_role", "Media role", "dropdown", "Video",
                options=["Video", "Music", "Communication", "Game", "Notification", "Animation", "Production",
                         "Accessibility", "Test"], apply="instance",
                info="Tells the sound server what kind of audio this is."),
            Row("viz_size", "Visualization size", "dropdown", "Match window", options=["Match window", "640×360", "1280×720"],
                apply="instance"),
        ], advanced=True),
    ]),
    Category("subtitles", "Media", "Subtitles", "subtitles", "Subtitle Defaults", [
        Section("Tracks & Languages", [
            Row("enable_subs", "Enable subtitles", "toggle", True, apply="instance"),
            Row("sub_language", "Preferred language", "dropdown", "System language",
                options=["System language", "English", "Arabic", "Chinese", "French", "German", "Hindi", "Italian",
                         "Japanese", "Korean", "Portuguese", "Russian", "Spanish", "Turkish", "Urdu"],
                apply="instance"),
            Row("sub_encoding", "Text encoding", "dropdown", "Automatic",
                options=["Automatic", "UTF-8", "Windows-1252", "Windows-1256 (Arabic)", "Windows-1251 (Cyrillic)",
                         "ISO-8859-1", "ISO-8859-2", "Shift_JIS", "GB18030", "Big5", "EUC-KR"],
                apply="instance", info="Only needed for old subtitle files that show garbled characters."),
        ]),
        Section("Loading Subtitles", [
            Row("sub_autoload", "Load subtitle files automatically", "toggle", True, apply="instance",
                info="Load subtitle files found next to the video."),
            Row("sub_fuzzy", "File name matching", "dropdown", "Title with extra characters", options=FUZZY,
                apply="instance",
                info="How closely a subtitle's file name has to match the video's: any subtitle file, one containing "
                     "the title, the title plus extra characters (Movie.en.srt), or exactly the same name."),
        ]),
        Section("Appearance", [
            Row("sub_font", "Font", "font", "", apply="instance", info="Empty uses the system's sans-serif font."),
            Row("sub_size_mode", "Size values", "segmented", "relative", options=[("relative", "Relative"), ("pixels", "Pixels")],
                apply="instance"),
            Row("sub_size", "Font size", "dropdown", "Automatic",
                options=["Automatic", "Smaller", "Small", "Normal", "Large", "Larger"], apply="instance", pixels_key="sub_px"),
            Row("sub_px", "Font size", "number", 32, min=8, max=200, suffix=" px", apply="instance", hidden=True),
            Row("sub_bold", "Bold", "toggle", False, apply="instance"),
            Row("sub_color", "Text color", "color", "#FFFFFF", apply="instance"),
            Row("sub_opacity", "Text opacity", "slider", 100, min=0, max=100, step=5, ticks=11, labels=["0%", "50%", "100%"],
                suffix="%", apply="instance"),
            Row("sub_outline", "Outline thickness", "dropdown", "Normal", options=["None", "Thin", "Normal", "Thick"],
                apply="instance"),
            Row("sub_outline_color", "Outline color", "color", "#000000", apply="instance"),
            Row("sub_shadow", "Shadow", "toggle", True, apply="instance"),
            Row("sub_shadow_color", "Shadow color", "color", "#000000", apply="instance"),
            Row("sub_shadow_distance", "Shadow distance", "dropdown", "Small", options=["Small", "Medium", "Large"],
                apply="instance"),
            Row("sub_background", "Background", "dropdown", "None", options=["None", "Box"], apply="instance"),
            Row("sub_bg_color", "Background color", "color", "#000000", apply="instance"),
            Row("sub_bg_opacity", "Background opacity", "slider", 50, min=0, max=100, step=5, ticks=11,
                labels=["0%", "50%", "100%"], suffix="%", apply="instance"),
            Row("sub_position", "Vertical position", "dropdown", "Bottom", options=["Bottom", "Raised", "High"],
                apply="instance", info="Lift subtitles above the bottom edge (useful with black bars)."),
        ]),
        Section("Timing", [
            Row("sub_delay_default", "Subtitle delay", "slider", 0.0, min=-5.0, max=5.0, step=0.1, ticks=11,
                labels=["-5 s", "0 s", "+5 s"], suffix=" s", apply="runtime", centered=True,
                info="Applied to every file; per-file delays are in the playback panel."),
        ]),
        Section("On-Screen Messages", [
            Row("osd_feedback", "Show feedback for shortcuts", "toggle", True, apply="ui",
                info="Show what a shortcut did, and which key you pressed, in the corner of the video."),
            Row("osd_duration", "Show feedback for", "slider", 1.5, min=0.5, max=5.0, step=0.5, ticks=10,
                labels=["0.5 s", "5 s"], suffix=" s", apply="ui"),
            Row("osd_filename", "Show filename when a video starts", "toggle", True, apply="ui"),
            Row("osd_filename_duration", "Show filename for", "slider", 3.0, min=1.0, max=10.0, step=0.5, ticks=10,
                labels=["1 s", "10 s"], suffix=" s", apply="ui"),
        ]),
        Section("Advanced Options", [
            Row("sub_paths", "Load subtitles from these folders", "text", "./Subtitles, ./subtitles, ./Subs, ./subs",
                apply="instance", info="Folders, relative to the video, that are searched for subtitle files."),
            Row("sub_ignore_ass", "Ignore styling in ASS and SSA subtitles", "toggle", False,
                disabled=NOT_IN_LIBVLC + " libass styling can't be switched off per player.",
                info="Show ASS/SSA subtitles with your font and colours."),
            Row("sub_shadow_angle", "Shadow angle", "number", -45, min=-360, max=360, suffix="°", apply="instance"),
            Row("sub_direction", "Text direction", "dropdown", "Automatic", options=["Automatic", "Left to right", "Right to left"],
                apply="instance"),
            Row("sub_monofont", "Monospace font", "font", "", apply="instance", mono=True,
                info="Used for subtitles that ask for a fixed-width font."),
        ], advanced=True),
    ]),
    Category("library", "Media", "Library", "folder", "Playlists, Folders & Files", [
        Section("Opening Files", [
            Row("single_window", "Only allow one VLC window", "toggle", True,
                info="Files you open while the player runs go to the open window."),
            Row("single_from_fm", "Apply to files opened from the file manager", "toggle", True, requires="single_window"),
            Row("enqueue_opened", "Add to the playlist instead of replacing it", "toggle", False, requires="single_window",
                info="Files opened from elsewhere are added to the queue instead of playing right away."),
            Row("folder_mode", "When opening a folder", "dropdown", "Include subfolders",
                options=["Include subfolders", "This folder only"]),
            Row("show_hidden", "Show hidden files", "toggle", False, apply="ui"),
        ]),
        Section("Metadata & Artwork", [
            Row("fetch_art", "Fetch titles and artwork", "dropdown", "From the file only",
                options=["From the file only", "From the file and the internet"], apply="instance"),
            Row("use_library", "Use the media library", "toggle", True, apply="ui",
                info="Index your media folders for the library. When off, the library only browses folders."),
        ]),
    ]),
    Category("network", "System", "Network", "streaming", "Network Connections & Security", [
        Section("Buffering", [
            Row("buffering", "Buffering", "dropdown", "Normal", options=["Low latency", "Normal", "High", "Custom"],
                apply="instance", info="How much is buffered before playback starts. Custom lets you set each value."),
            Row("cache_file", "Local files", "number", 300, min=0, max=60000, suffix=" ms", apply="instance", requires="custom"),
            Row("cache_disc", "Discs", "number", 300, min=0, max=60000, suffix=" ms", apply="instance", requires="custom"),
            Row("cache_net", "Network streams", "number", 1000, min=0, max=60000, suffix=" ms", apply="instance", requires="custom"),
            Row("cache_live", "Live capture", "number", 300, min=0, max=60000, suffix=" ms", apply="instance", requires="custom"),
        ]),
        Section("Proxy", [
            Row("http_proxy", "HTTP proxy", "text", "", apply="instance", placeholder="System proxy",
                info="http://host:port, or empty to use the system proxy settings."),
            Row("http_proxy_pwd", "HTTP proxy password", "text", "", apply="instance", password=True,
                info="Only needed if the proxy asks for one."),
        ]),
        Section("Security", [
            Row("tls_system_trust", "Verify certificates against the system trust store", "toggle", True, apply="instance"),
            Row("tls_ciphers", "Cipher preference", "dropdown", "Balanced", options=["Balanced", "Performance", "Secure"],
                apply="instance"),
        ]),
    ]),
    Category("performance", "System", "Performance", "preferences", "Performance Settings", [
        Section("Decoding", [
            Row("hwdec", "Hardware-accelerated decoding", "dropdown", "Automatic",
                options=["Automatic", "VDPAU", "Off"], apply="instance",
                info="Let the graphics card decode video. Turn off if you see green frames or glitches."),
            Row("postproc", "Video post-processing", "dropdown", "Off", options=["Off", "Low", "Medium", "High"],
                apply="instance", info="Reduce blocking in low-quality video. Costs CPU."),
            Row("skip_loop", "Skip the H.264 loop filter", "dropdown", "Never",
                options=["Never", "Non-reference frames", "B-frames", "Non-key frames", "All frames"], apply="instance",
                info="Faster H.264 decoding on slow computers, at some quality cost."),
            Row("dec_threads", "Decoding threads", "number", 0, min=0, max=64, apply="instance", info="0 picks automatically."),
        ]),
        Section("Smooth Playback", [
            Row("drop_late", "Drop frames when the system can't keep up", "toggle", True, apply="instance"),
            Row("rt_priority", "Give VLC real-time priority", "toggle", False, apply="instance"),
            Row("rt_offset", "Process priority", "number", 0, min=-20, max=20, apply="instance",
                info="Used with real-time priority."),
            Row("rescan", "Rescan plugins", "button", "Rescan", wide=True,
                info="Rebuild libVLC's plugin cache (after installing VLC plugins)."),
        ]),
        Section("Developer", [
            Row("codec", "Decoder", "dropdown", "Automatic", options=["Automatic", "avcodec", "dav1d", "libmpeg2"],
                apply="instance"),
            Row("encoder", "Encoder", "dropdown", "Automatic", options=["Automatic", "x264", "x265", "vpx"], apply="instance"),
            Row("clock_sync", "Clock synchronisation", "dropdown", "Default", options=["Default", "Disabled", "Enabled"],
                apply="instance"),
            Row("clock_jitter", "Clock jitter", "number", 5000, min=0, max=60000, suffix=" ms", apply="instance"),
            Row("cr_average", "Clock reference averaging", "number", 40, min=1, max=1000, apply="instance"),
        ], advanced=True),
    ]),
    Category("recording", "System", "Recording", "record", "Recording, Casting & Streaming Out", [
        Section("Recording", [
            Row("record_dir", "Save recordings to", "path", HOME + "/Videos", mode="dir",
                info="Where Record (Shift+R) saves clips and stream recordings."),
            Row("record_copy", "Record without re-encoding when possible", "toggle", True,
                info="Copy the original streams. Off re-encodes to H.264/AAC, which is slower but cuts precisely."),
        ]),
        Section("Casting", [
            Row("cast_discover", "Discover cast devices", "toggle", False,
                disabled="This libVLC build has no renderer discovery (microdns) plugin.",
                info="Find Chromecasts on your network."),
            Row("cast_quality", "Cast quality", "dropdown", "Automatic", options=["Automatic", "High", "Medium", "Low"],
                disabled="Needs cast device discovery."),
        ]),
        Section("Streaming", [
            Row("stream_local", "Play locally while streaming", "toggle", True),
            Row("stream_audio", "Include audio", "toggle", True),
            Row("stream_video", "Include video", "toggle", True),
            Row("stream_subs", "Include subtitles", "toggle", True),
            Row("stream_preset", "Encoding preset", "dropdown", "Medium",
                options=["Ultrafast", "Veryfast", "Fast", "Medium", "Slow"],
                info="Faster presets use less CPU but give bigger files or lower quality."),
            Row("stream_tune", "Tuning", "dropdown", "None", options=["None", "Film", "Animation", "Grain", "Zero latency"]),
        ]),
    ]),
    Category("extensions", "System", "Extensions", "plugins", "Add-ons & Scripts", [
        Section("Add-ons", [
            Row("addons_list", "", "addons", None),
            Row("addons_startup", "Load add-ons on startup", "toggle", True,
                disabled="VLC Lua extensions need VLC's own interface; they are listed here but not run.",
                info="Run activated extensions when VLC starts."),
        ]),
    ]),
]

ROWS = {r.key: r for cat in CATEGORIES for s in cat.sections for r in s.rows}
DEFAULTS = {k: r.default for k, r in ROWS.items() if r.kind not in ("button", "addons")}
CATEGORY = {cat.key: cat for cat in CATEGORIES}
