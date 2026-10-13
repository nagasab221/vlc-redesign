"""Window-manager drags (move / resize) for the frameless windows.

Qt's QWindow.startSystemMove() tells the window manager where the drag started using the
last pointer position that the top-level X window itself received. Ours never receives
one: the video is a native X window, which makes Qt turn the widgets around it into
native windows too, so Qt reports (0, 0) and the window jumps to put its corner under
the pointer. This sends _NET_WM_MOVERESIZE with the real position instead, and falls back
to Qt's call when python-xlib isn't there.
"""

from PyQt5.QtCore import Qt
from PyQt5.QtGui import QCursor, QGuiApplication

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


def _x():
    global _display
    if X is None or QGuiApplication.platformName() != "xcb":
        return None
    if _display is None:
        try:
            _display = display.Display()
        except Exception:
            return None
    return _display


def start(window, edges=None):
    """Hand the current left-button drag on `window` to the window manager."""
    handle = window.windowHandle()
    if handle is None:
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
