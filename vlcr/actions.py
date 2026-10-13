"""Every keyboard action: id, category, label, default key and the window method it calls.
The Keyboard Shortcuts dialog edits the keys (stored per profile in store["hotkeys"])."""

ACTIONS = [
    # Basic
    ("fullscreen", "Basic", "Toggle fullscreen", "F", "toggle_fullscreen"),
    ("escape", "Basic", "Exit fullscreen / close dialog", "Esc", "escape"),
    ("show_time", "Basic", "Show playback position and time", "T", "show_time"),
    ("show_controls", "Basic", "Show interface controls in fullscreen", "I", "show_overlay_bars"),
    ("hide_ui", "Basic", "Hide the interface", "Ctrl+\\", "toggle_hide_ui"),
    ("library", "Basic", "Show / hide the file browser", "L", "toggle_sidebar"),
    ("queue", "Basic", "Show / hide the queue", "Q", "toggle_queue"),
    ("mini", "Basic", "Picture in picture", "Ctrl+M", "toggle_pip"),
    ("home", "Basic", "Go to the library", "Ctrl+H", "go_home"),
    # Playback
    ("play_pause", "Playback", "Play / pause", "Space", "engine.toggle"),
    ("stop", "Playback", "Stop playback", "S", "engine.stop"),
    ("next", "Playback", "Next track", "N", "engine.next"),
    ("prev", "Playback", "Previous track", "P", "engine.prev"),
    ("next2", "Playback", "Next item", ">", "engine.next"),
    ("prev2", "Playback", "Previous item", "<", "engine.prev"),
    ("next_frame", "Playback", "Next frame", ".", "engine.next_frame"),
    ("prev_frame", "Playback", "Previous frame", ",", "engine.prev_frame"),
    ("faster", "Playback", "Faster", "]", "faster"),
    ("slower", "Playback", "Slower", "[", "slower"),
    ("normal_speed", "Playback", "Normal speed", "=", "normal_speed"),
    ("fwd_vshort", "Playback", "Very short forward jump", "Shift+Right", "jump:vshort:1"),
    ("back_vshort", "Playback", "Very short backward jump", "Shift+Left", "jump:vshort:-1"),
    ("fwd_short", "Playback", "Short forward jump", "Right", "jump:short:1"),
    ("back_short", "Playback", "Short backward jump", "Left", "jump:short:-1"),
    ("fwd_medium", "Playback", "Medium forward jump", "Ctrl+Right", "jump:medium:1"),
    ("back_medium", "Playback", "Medium backward jump", "Ctrl+Left", "jump:medium:-1"),
    ("fwd_long", "Playback", "Long forward jump", "Alt+Right", "jump:long:1"),
    ("back_long", "Playback", "Long backward jump", "Alt+Left", "jump:long:-1"),
    ("repeat", "Playback", "Repeat (off / all / one)", "R", "engine.cycle_repeat"),
    ("shuffle", "Playback", "Shuffle", "Shift+Z", "engine.toggle_shuffle"),
    ("bookmark", "Playback", "Add a bookmark", "Ctrl+B", "add_bookmark"),
    ("record", "Playback", "Record", "Shift+R", "engine.toggle_record"),
    ("snapshot", "Playback", "Take a snapshot", "Shift+S", "take_snapshot"),
    # Audio
    ("vol_up", "Audio", "Volume up", "Up", "volume_up"),
    ("vol_down", "Audio", "Volume down", "Down", "volume_down"),
    ("mute", "Audio", "Mute", "M", "engine.toggle_mute"),
    ("audio_track", "Audio", "Cycle audio track", "B", "cycle_audio"),
    ("audio_delay_down", "Audio", "Audio delay −50 ms", "J", "audio_delay:-0.05"),
    ("audio_delay_up", "Audio", "Audio delay +50 ms", "K", "audio_delay:0.05"),
    # Subtitles
    ("sub_track", "Subtitles", "Cycle subtitle track", "V", "cycle_spu"),
    ("sub_delay_down", "Subtitles", "Subtitle delay −100 ms", "G", "sub_delay:-0.1"),
    ("sub_delay_up", "Subtitles", "Subtitle delay +100 ms", "H", "sub_delay:0.1"),
    ("sub_add", "Subtitles", "Add subtitles…", "Ctrl+Shift+S", "open_add_subtitles"),
    # Video
    ("aspect", "Video", "Cycle aspect ratio", "A", "cycle_aspect"),
    ("crop", "Video", "Cycle crop", "C", "cycle_crop"),
    ("zoom", "Video", "Cycle zoom", "Z", "cycle_zoom"),
    ("deinterlace", "Video", "Toggle deinterlacing", "D", "toggle_deinterlace"),
    # Media & app
    ("open_file", "Media", "Open file(s)", "Ctrl+O", "open_dialog:file"),
    ("open_folder", "Media", "Open folder", "Ctrl+F", "open_dialog:folder"),
    ("open_network", "Media", "Open network stream", "Ctrl+N", "open_network"),
    ("open_disc", "Media", "Open disc", "Ctrl+D", "open_disc"),
    ("open_capture", "Media", "Open capture device", "Ctrl+Shift+C", "open_capture"),
    ("convert", "Media", "Convert", "Ctrl+R", "open_convert"),
    ("properties", "Media", "Properties & metadata", "Ctrl+I", "show_properties"),
    ("settings", "App", "Settings", "Ctrl+P", "open_settings"),
    ("shortcuts", "App", "Keyboard shortcuts", "Ctrl+K", "open_shortcuts"),
    ("about", "App", "About", "Shift+F1", "show_about"),
    ("quit", "App", "Quit", "Ctrl+Q", "quit"),
]

ACTION = {a[0]: a for a in ACTIONS}

# Shortcuts that act on the player even while a text field elsewhere has focus.
WINDOW_WIDE = {"fullscreen", "escape", "hide_ui", "library", "queue", "mini", "home", "open_file", "open_folder",
               "open_network", "open_disc", "open_capture", "convert", "properties", "settings", "shortcuts", "about",
               "quit", "bookmark", "record", "snapshot", "sub_add"}
