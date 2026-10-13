"""Global hotkeys on X11: keys grabbed on the root window work while other apps have focus."""

import select
import threading

from PyQt5.QtCore import QObject, pyqtSignal

try:
    from Xlib import X, XK, display
    from Xlib.error import XError
except ImportError:          # python3-xlib missing: global hotkeys are simply unavailable
    display = None

MODS = {"ctrl": "ControlMask", "shift": "ShiftMask", "alt": "Mod1Mask", "meta": "Mod4Mask", "super": "Mod4Mask"}
KEYNAMES = {"space": "space", "esc": "Escape", "left": "Left", "right": "Right", "up": "Up", "down": "Down",
            "pgup": "Prior", "pgdown": "Next", "pageup": "Prior", "pagedown": "Next", "del": "Delete",
            "ins": "Insert", "return": "Return", "enter": "Return", "media play": "XF86AudioPlay",
            "media next": "XF86AudioNext", "media previous": "XF86AudioPrev", "media stop": "XF86AudioStop",
            "volume up": "XF86AudioRaiseVolume", "volume down": "XF86AudioLowerVolume", "volume mute": "XF86AudioMute"}


class GlobalHotkeys(QObject):
    triggered = pyqtSignal(str)

    def __init__(self, mapping):
        super().__init__()
        self.mapping = mapping
        self._stop = threading.Event()
        self._thread = None
        self.grabs = {}

    def start(self):
        if display is None:
            return
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self):
        self._stop.set()

    def _parse(self, d, seq):
        parts = [p.strip() for p in seq.replace("++", "+plus").split("+") if p.strip()]
        mask = 0
        key = None
        for p in parts:
            low = p.lower()
            if low in MODS:
                mask |= getattr(X, MODS[low])
            else:
                key = p
        if key is None:
            return None
        name = KEYNAMES.get(key.lower(), key)
        ks = XK.string_to_keysym(name) or XK.string_to_keysym(name.lower())
        if not ks and len(name) == 1:
            ks = ord(name.lower())
        code = d.keysym_to_keycode(ks)
        return (code, mask) if code else None

    def _run(self):
        try:
            d = display.Display()
        except Exception:
            return
        root = d.screen().root
        extra = [0, X.LockMask, X.Mod2Mask, X.LockMask | X.Mod2Mask]
        for aid, seq in self.mapping.items():
            parsed = self._parse(d, seq)
            if not parsed:
                continue
            code, mask = parsed
            self.grabs[(code, mask)] = aid
            for e in extra:
                try:
                    root.grab_key(code, mask | e, True, X.GrabModeAsync, X.GrabModeAsync)
                except Exception:
                    pass
        d.sync()
        fd = d.fileno()
        while not self._stop.is_set():
            r, _, _ = select.select([fd], [], [], 0.3)
            while d.pending_events():
                ev = d.next_event()
                if ev.type == X.KeyPress:
                    state = ev.state & ~(X.LockMask | X.Mod2Mask)
                    aid = self.grabs.get((ev.detail, state))
                    if aid:
                        self.triggered.emit(aid)
        for (code, mask) in self.grabs:
            for e in extra:
                try:
                    root.ungrab_key(code, mask | e)
                except Exception:
                    pass
        d.sync()
        d.close()
