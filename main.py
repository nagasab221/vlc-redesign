"""Entry point: python3 main.py [files, folders or URLs...]"""

import ctypes
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "vendor"))   # bundled python-vlc binding
sys.path.insert(0, HERE)


def tune_malloc():
    # libVLC decodes on many threads; glibc then keeps one memory arena per thread and rarely gives
    # freed frame buffers back. Fewer arenas keeps the footprint close to what is really in use.
    try:
        ctypes.CDLL("libc.so.6").mallopt(-8, 4)      # M_ARENA_MAX
    except (OSError, AttributeError):
        pass


def init_x11_threads():
    # libVLC renders from its own threads into our X window; VLC's own Qt UI does the same.
    try:
        ctypes.CDLL("libX11.so.6").XInitThreads()
    except OSError:
        pass


def main():
    if os.environ.get("XDG_SESSION_TYPE") == "wayland" and "QT_QPA_PLATFORM" not in os.environ:
        os.environ["QT_QPA_PLATFORM"] = "xcb"    # libVLC embeds into X11 windows
    tune_malloc()
    init_x11_threads()

    from PyQt5.QtCore import Qt
    from PyQt5.QtNetwork import QLocalServer, QLocalSocket
    from PyQt5.QtWidgets import QApplication

    from vlcr import APP_ID, APP_NAME

    QApplication.setAttribute(Qt.AA_EnableHighDpiScaling, True)
    QApplication.setAttribute(Qt.AA_UseHighDpiPixmaps, True)
    app = QApplication([APP_ID] + sys.argv[1:])   # argv[0] becomes the X11 WM_CLASS
    app.setApplicationName(APP_ID)
    app.setApplicationDisplayName(APP_NAME)
    app.setDesktopFileName(APP_ID)
    app.setQuitOnLastWindowClosed(False)

    from vlcr.store import Store
    store = Store()

    args = sys.argv[1:]
    server_name = f"{APP_ID}-{os.getuid()}"
    single = store.get("single_window") and (store.get("single_from_fm") or not args)

    # Single instance: hand the arguments to a running window, if there is one.
    if single:
        sock = QLocalSocket()
        sock.connectToServer(server_name)
        if sock.waitForConnected(300):
            sock.write(json.dumps([os.path.abspath(a) if os.path.exists(a) else a for a in args]).encode())
            sock.waitForBytesWritten(1000)
            sock.disconnectFromServer()
            return 0

    from vlcr import theme
    theme.load_fonts()
    app.setStyle("Fusion")
    theme.apply(store.get("theme"), store.get("accent"))
    app.setFont(theme.font(14))

    from vlcr.engine import Engine
    from vlcr.window import MainWindow

    engine = Engine(store)
    win = MainWindow(engine, store)

    server = None
    if store.get("single_window"):
        server = QLocalServer()
        QLocalServer.removeServer(server_name)
        server.listen(server_name)

        def incoming():
            while server.hasPendingConnections():
                conn = server.nextPendingConnection()
                buf = bytearray()

                def on_ready(c=conn, b=buf):
                    b.extend(bytes(c.readAll()))

                def on_done(c=conn, b=buf):
                    b.extend(bytes(c.readAll()))
                    try:
                        items = json.loads(b.decode() or "[]")
                    except ValueError:
                        items = []
                    win.handle_args(items)
                    c.deleteLater()

                conn.readyRead.connect(on_ready)
                conn.disconnected.connect(on_done)
                if conn.state() == QLocalSocket.UnconnectedState:
                    on_done()

        server.newConnection.connect(incoming)

    if store.get("start_in_tray") and win.tray is not None and win.tray.isVisible():
        pass
    else:
        win.show()
    if not store["onboarded"]:
        from vlcr.onboarding import Onboarding
        Onboarding(win).exec_()
        store.save()
    if args:
        win.handle_args(args)
    code = app.exec_()
    if server is not None:
        server.close()
    return code


if __name__ == "__main__":
    sys.exit(main())
