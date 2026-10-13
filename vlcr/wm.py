"""Window-manager drags (move / resize) for the frameless windows.

Qt's QWindow.startSystemMove() tells the window manager where the drag started using the
last pointer position that the top-level X window itself received. Ours never receives
one: the video is a native X window, which makes Qt turn the widgets around it into
native windows too, so Qt reports (0, 0) and the window jumps to put its corner under
the pointer. This sends _NET_WM_MOVERESIZE with the real position instead, and falls back
to Qt's call when python-xlib isn't there.

In a Wayland session (the app runs through XWayland) Cinnamon ignores that request from
frameless X11 windows, Qt's own included, so there we move / resize the window ourselves.
"""

import os

from PyQt5.QtCore import QEvent, QObject, QRect, Qt
from PyQt5.QtGui import QCursor, QGuiApplication
from PyQt5.QtWidgets import QApplication

try:
    from Xlib import X, display, protocol
except ImportError:          # pragma: no cover
    X = None

MOVE = 8
_DIRECTIONS = {
    int(Qt.TopEdge | Qt.LeftEdge): 0, int(Qt.TopEdge): 1, int(Qt.TopEdge | Qt.RightEdge): 2,
    int(Qt.RightEdge): 3, int(Qt.BottomEdge | Qt.RightEdge): 4, int(Qt.BottomEdge): 5,
    int(Qt.BottomEdge | Qt.LeftEdge): 6, int(Qt.LeftEdge): 7,
}
_display = None


def core_display():
    """An Xlib connection without its extension setup. We only send core requests, and the
    python-xlib in Mint (0.33) crashes setting up RandR on XWayland (Wayland sessions)."""
    import Xlib.ext
    saved = Xlib.ext.__extensions__
    Xlib.ext.__extensions__ = []
    try:
        return display.Display()
    finally:
        Xlib.ext.__extensions__ = saved


def _x():
    global _display
    if X is None or QGuiApplication.platformName() != "xcb":
        return None
    if _display is None:
        try:
            _display = core_display()
        except Exception:
            return None
    return _display


class _SelfDrag(QObject):
    """Follows the pointer until the button is released, moving or resizing `window`."""

    def __init__(self, window, edges):
        super().__init__(window)
        self.window, self.edges = window, edges
        self.start, self.geo = QCursor.pos(), window.geometry()
        QApplication.instance().installEventFilter(self)

    def eventFilter(self, obj, ev):
        t = ev.type()
        if t == QEvent.MouseMove:
            if not ev.buttons() & Qt.LeftButton:
                self._finish()
                return False
            d = ev.globalPos() - self.start
            if self.edges is None:
                self.window.move(self.geo.topLeft() + d)
            else:
                self._resize(d.x(), d.y())
            return True
        if t == QEvent.MouseButtonRelease:
            self._finish()
            return True
        return False

    def _resize(self, dx, dy):
        g, m, e = QRect(self.geo), self.window.minimumSize(), self.edges
        if e & Qt.LeftEdge:
            g.setLeft(min(g.left() + dx, g.right() - m.width()))
        if e & Qt.RightEdge:
            g.setRight(max(g.right() + dx, g.left() + m.width()))
        if e & Qt.TopEdge:
            g.setTop(min(g.top() + dy, g.bottom() - m.height()))
        if e & Qt.BottomEdge:
            g.setBottom(max(g.bottom() + dy, g.top() + m.height()))
        self.window.setGeometry(g)

    def _finish(self):
        QApplication.instance().removeEventFilter(self)
        self.deleteLater()


def _wayland_session():
    return bool(os.environ.get("WAYLAND_DISPLAY")) or os.environ.get("XDG_SESSION_TYPE") == "wayland"


def start(window, edges=None):
    """Hand the current left-button drag on `window` to the window manager."""
    handle = window.windowHandle()
    if handle is None:
        return
    if _wayland_session():
        _SelfDrag(window, edges)
        return
    direction = MOVE if edges is None else _DIRECTIONS.get(int(edges))
    d = _x()
    if d is None or direction is None:
        handle.startSystemMove() if edges is None else handle.startSystemResize(edges)
        return
    ratio = handle.devicePixelRatio()
    p = QCursor.pos()
    # Qt still holds the pointer from the button press; release it so the WM can grab it,
    # and make sure that reached the X server before the request below does.
    window.grabMouse()
    window.releaseMouse()
    QGuiApplication.sync()
    try:
        win = d.create_resource_object("window", int(window.winId()))
        ev = protocol.event.ClientMessage(window=win, client_type=d.intern_atom("_NET_WM_MOVERESIZE"),
                                          data=(32, [int(p.x() * ratio), int(p.y() * ratio), direction, 1, 1]))
        d.screen().root.send_event(ev, event_mask=X.SubstructureRedirectMask | X.SubstructureNotifyMask)
        d.flush()
    except Exception:
        handle.startSystemMove() if edges is None else handle.startSystemResize(edges)
