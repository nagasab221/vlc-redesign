"""Customize Player: drag elements between the title bar, the control bar rows and the
dialog (which removes them). Bars show dashed outlines while customising; the layout is
saved per profile."""

import copy
import json

from PyQt5.QtCore import QEvent, QMimeData, QObject, QPoint, QPointF, QRect, QRectF, Qt, QTimer
from PyQt5.QtGui import QColor, QDrag, QPainter, QPen, QPixmap
from PyQt5.QtWidgets import QApplication, QDialog, QGridLayout, QHBoxLayout, QVBoxLayout, QWidget

from . import icons, theme
from .controls import CUSTOMIZE_ORDER, ELEMENTS, LOCKED, REPEATABLE
from .store import ALT_LAYOUT, DEFAULT_LAYOUT
from .theme import c
from .widgets import Button, Customizable, Label, paint_shadow

MIME = "application/x-vlcr-element"


class ElementTile(QWidget):
    """An element in the dialog grid: dashed icon box + label."""

    def __init__(self, eid, dialog):
        super().__init__()
        self.eid = eid
        self.dialog = dialog
        self.setFixedSize(110, 104)
        self.setCursor(Qt.OpenHandCursor)
        self._press = None

    def mousePressEvent(self, e):
        self._press = e.pos()

    def mouseMoveEvent(self, e):
        if self._press is not None and (e.pos() - self._press).manhattanLength() > 6:
            self._press = None
            self.dialog.ctrl.start_drag(self, self.eid, None, None)

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        box = QRectF(self.width() / 2 - 15, 8, 30, 30)
        pen = QPen(c("text_2"), 1, Qt.DashLine)
        pen.setDashPattern([2, 2])
        p.setPen(pen)
        p.setBrush(Qt.NoBrush)
        p.drawRect(box)
        label, icon, _ = ELEMENTS[self.eid]
        if icon == "thumbnail":
            p.setPen(Qt.NoPen)
            p.setBrush(c("thumb"))
            p.drawRect(box.adjusted(3, 3, -3, -3))
        elif icon == "divider":
            p.setPen(QPen(c("text_3"), 1.5))
            p.drawLine(QPointF(box.center().x(), box.top() + 6), QPointF(box.center().x(), box.bottom() - 6))
        elif icon:
            p.drawPixmap(box.adjusted(3, 3, -3, -3).toRect(), icons.pixmap(icon, 24))
        p.setFont(theme.font(14))
        p.setPen(c("text"))
        p.drawText(QRectF(0, 46, self.width(), 52), Qt.AlignHCenter | Qt.AlignTop | Qt.TextWordWrap, label)


class DropMarker(QWidget):
    def paintEvent(self, e):
        p = QPainter(self)
        p.fillRect(self.rect(), c("accent"))


class Controller(QObject):
    """Turns the window's bars into drag sources / drop targets while customising."""

    def __init__(self, win, dialog):
        super().__init__()
        self.win = win
        self.dialog = dialog
        self.layout = copy.deepcopy(win.store.layout())
        self.layout["rows"] = [list(r) for r in self.layout["rows"]] + [[]]   # trailing empty row = "new row"
        self.marker = None
        self._drag_src = None

    # -- applying the working layout
    def rebuild(self):
        lay = copy.deepcopy(self.layout)
        # keep an empty row available above the bar to create a new row
        rows = [r for r in lay["rows"] if r]
        lay["rows"] = [["__newrow__"]] + rows
        self.win.titlebar.build(lay)
        bar = self.win.player.bar
        bar.build(lay["rows"])
        self.win._sync_all()
        self.attach()

    def attach(self):
        for bar in (self.win.titlebar, self.win.player.bar):
            bar.setAcceptDrops(True)
            bar.installEventFilter(self)
            for w in bar.widgets:
                w.customizing = True
                if hasattr(w, "set_text") and getattr(w, "element_id", "") in ("title", "folder", "file_name"):
                    w.set_text(getattr(w, "text", "") or ELEMENTS[w.element_id][0])
                w.installEventFilter(self)
                for ch in w.findChildren(QWidget):
                    ch.installEventFilter(self)
                w.update()
        for w in self.win.player.bar.widgets:
            if getattr(w, "element_id", "") == "__newrow__" or w.element_id not in ELEMENTS:
                pass

    def detach(self):
        for bar in (self.win.titlebar, self.win.player.bar):
            bar.setAcceptDrops(False)
            bar.removeEventFilter(self)
            for w in bar.widgets:
                w.customizing = False
                w.removeEventFilter(self)
                for ch in w.findChildren(QWidget):
                    ch.removeEventFilter(self)
        if self.marker:
            self.marker.hide()

    # -- element lookup
    def _element_of(self, obj):
        while obj is not None and not hasattr(obj, "element_id"):
            obj = obj.parentWidget() if isinstance(obj, QWidget) else None
        return obj

    def _locate(self, w):
        """(zone, row index or None, position) of an element widget in the working layout."""
        zone = getattr(w, "zone", None)
        if zone and zone.startswith("title"):
            ids = self.layout.get(zone, [])
            siblings = [x for x in self.win.titlebar.widgets if getattr(x, "zone", None) == zone]
            return zone, None, siblings.index(w) if w in siblings else ids.index(w.element_id)
        bar = self.win.player.bar
        rows = [r for r in self.layout["rows"] if r]
        # find row by the host widget
        host = w.parentWidget()
        while host is not None and host.parentWidget() is not bar:
            host = host.parentWidget()
        hosts = [bar.rows_lay.itemAt(i).widget() for i in range(bar.rows_lay.count())]
        ri = hosts.index(host) - 1 if host in hosts else 0     # first host is the "new row" strip
        siblings = [x for x in bar.widgets if self._row_host(x) is host]
        return "rows", ri, siblings.index(w) if w in siblings else 0

    def _row_host(self, w):
        bar = self.win.player.bar
        host = w.parentWidget()
        while host is not None and host.parentWidget() is not bar:
            host = host.parentWidget()
        return host

    # -- dragging
    def start_drag(self, source_widget, eid, zone, pos_info):
        md = QMimeData()
        md.setData(MIME, json.dumps({"id": eid, "from": zone, "info": pos_info}).encode())
        drag = QDrag(source_widget)
        drag.setMimeData(md)
        pm = source_widget.grab()
        drag.setPixmap(pm)
        drag.setHotSpot(QPoint(pm.width() // 2, pm.height() // 2))
        self._drag_src = (eid, zone, pos_info)
        drag.exec_(Qt.MoveAction)
        self._drag_src = None
        if self.marker:
            self.marker.hide()

    def eventFilter(self, obj, ev):
        t = ev.type()
        bars = (self.win.titlebar, self.win.player.bar)
        if t in (QEvent.DragEnter, QEvent.DragMove) and obj in bars:
            if ev.mimeData().hasFormat(MIME):
                ev.acceptProposedAction()
                self._show_marker(obj, ev.pos())
                return True
        if t == QEvent.DragLeave and obj in bars:
            if self.marker:
                self.marker.hide()
            return True
        if t == QEvent.Drop and obj in bars:
            if ev.mimeData().hasFormat(MIME):
                data = json.loads(bytes(ev.mimeData().data(MIME)).decode())
                target = self._target(obj, ev.pos())
                ev.acceptProposedAction()
                QTimer.singleShot(0, lambda: self.move(data, target))
                return True
        if t == QEvent.MouseButtonPress and obj not in bars:
            el = self._element_of(obj)
            if el is not None:
                self._press = (el, ev.globalPos())
                return True
        if t == QEvent.MouseMove and obj not in bars and getattr(self, "_press", None):
            el, start = self._press
            if (ev.globalPos() - start).manhattanLength() > 6 and el.element_id not in LOCKED \
                    and el.element_id != "__newrow__":
                self._press = None
                zone, ri, idx = self._locate(el)
                self.start_drag(el, el.element_id, zone, [ri, idx])
            return True
        if t in (QEvent.MouseButtonRelease, QEvent.MouseButtonDblClick) and obj not in bars:
            if self._element_of(obj) is not None:
                self._press = None
                return True
        if t in (QEvent.Enter, QEvent.HoverEnter) and obj not in bars:
            return False
        return False

    def _target(self, bar, pos):
        """(zone, row index, insert position) for a drop at pos in bar."""
        if bar is self.win.titlebar:
            for zone, w in bar.zone_widgets.items():
                g = QRect(w.mapTo(bar, QPoint(0, 0)), w.size())
                if g.adjusted(-6, -20, 6, 20).contains(pos):
                    els = [x for x in bar.widgets if getattr(x, "zone", None) == zone]
                    idx = sum(1 for x in els if x.mapTo(bar, QPoint(0, 0)).x() + x.width() / 2 < pos.x())
                    return zone, None, idx
            return "title_left", None, len(self.layout.get("title_left", []))
        hosts = [bar.rows_lay.itemAt(i).widget() for i in range(bar.rows_lay.count())]
        for hi, host in enumerate(hosts):
            g = QRect(host.mapTo(bar, QPoint(0, 0)), host.size())
            if g.top() <= pos.y() <= g.bottom():
                els = [x for x in bar.widgets if self._row_host(x) is host and x.element_id != "__newrow__"]
                idx = sum(1 for x in els if x.mapTo(bar, QPoint(0, 0)).x() + x.width() / 2 < pos.x())
                return "rows", hi - 1, idx          # -1 = the new-row strip
        return "rows", len(hosts) - 2, 0

    def _show_marker(self, bar, pos):
        zone, ri, idx = self._target(bar, pos)
        if self.marker is None or self.marker.parentWidget() is not bar:
            if self.marker:
                self.marker.deleteLater()
            self.marker = DropMarker(bar)
        if zone.startswith("title"):
            els = [x for x in bar.widgets if getattr(x, "zone", None) == zone]
            host_g = QRect(bar.zone_widgets[zone].mapTo(bar, QPoint(0, 0)), bar.zone_widgets[zone].size())
        else:
            hosts = [bar.rows_lay.itemAt(i).widget() for i in range(bar.rows_lay.count())]
            host = hosts[ri + 1] if 0 <= ri + 1 < len(hosts) else hosts[-1]
            els = [x for x in bar.widgets if self._row_host(x) is host and x.element_id != "__newrow__"]
            host_g = QRect(host.mapTo(bar, QPoint(0, 0)), host.size())
        if idx < len(els):
            x = els[idx].mapTo(bar, QPoint(0, 0)).x() - 3
        elif els:
            last = els[-1]
            x = last.mapTo(bar, QPoint(0, 0)).x() + last.width() + 1
        else:
            x = host_g.left() + 4
        self.marker.setGeometry(x, host_g.top() + 8, 2, max(16, host_g.height() - 16))
        self.marker.show()
        self.marker.raise_()

    def move(self, data, target):
        eid = data["id"]
        src_zone = data.get("from")
        info = data.get("info") or [None, None]
        lay = self.layout
        rows = [r for r in lay["rows"] if r]
        zone, ri, idx = target
        # remove from the source
        if src_zone:
            if src_zone.startswith("title"):
                lst = lay[src_zone]
                if 0 <= info[1] < len(lst) and lst[info[1]] == eid:
                    lst.pop(info[1])
                    if zone == src_zone and idx > info[1]:
                        idx -= 1
                elif eid in lst:
                    lst.remove(eid)
            else:
                sri, sidx = info
                if sri is not None and 0 <= sri < len(rows):
                    r = rows[sri]
                    if 0 <= sidx < len(r) and r[sidx] == eid:
                        r.pop(sidx)
                        if zone == "rows" and ri == sri and idx > sidx:
                            idx -= 1
                    elif eid in r:
                        r.remove(eid)
        # insert at the target
        if zone.startswith("title"):
            lay[zone].insert(min(idx, len(lay[zone])), eid)
        else:
            if ri is None or ri < 0:
                rows.insert(0, [eid])
            else:
                ri = min(ri, len(rows) - 1)
                if ri < 0:
                    rows.append([eid])
                else:
                    rows[ri].insert(min(idx, len(rows[ri])), eid)
        lay["rows"] = [r for r in rows if r]
        self.rebuild()
        self.dialog.refresh_tiles()

    def remove(self, data):
        eid = data["id"]
        zone = data.get("from")
        info = data.get("info") or [None, None]
        if not zone:
            return
        lay = self.layout
        if zone.startswith("title"):
            if eid in lay[zone]:
                lay[zone].remove(eid)
        else:
            rows = [r for r in lay["rows"] if r]
            sri, sidx = info
            if sri is not None and 0 <= sri < len(rows):
                r = rows[sri]
                if 0 <= sidx < len(r) and r[sidx] == eid:
                    r.pop(sidx)
                elif eid in r:
                    r.remove(eid)
            lay["rows"] = [r for r in rows if r]
        self.rebuild()
        self.dialog.refresh_tiles()

    def used(self):
        lay = self.layout
        out = set(lay.get("title_left", [])) | set(lay.get("title_center", [])) | set(lay.get("title_right", []))
        for r in lay["rows"]:
            out |= set(r)
        return out


class CustomizeDialog(QDialog):
    MARGIN = 24

    def __init__(self, win):
        super().__init__(win, Qt.Tool | Qt.FramelessWindowHint | Qt.NoDropShadowWindowHint)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAcceptDrops(True)
        self.win = win
        self.original = copy.deepcopy(win.store.layout())
        self.ctrl = Controller(win, self)
        m = self.MARGIN
        self.resize(1048 + 2 * m, 654 + 2 * m)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(m, m, m, m)
        outer.setSpacing(0)
        head = QWidget()
        hv = QVBoxLayout(head)
        hv.setContentsMargins(19, 14, 14, 16)
        top = QHBoxLayout()
        top.addWidget(Label("Customize Player", 14, "icon"))
        top.addStretch(1)
        from .widgets import IconButton
        close = IconButton("x", "Close", box=(32, 32))
        close.clicked.connect(self.reject)
        top.addWidget(close)
        hv.addLayout(top)
        hv.addSpacing(10)
        hv.addWidget(Label("Drag items around and out of the box below to re-order them. Drag items into the box to "
                           "remove them from the player.", 14, "text", wrap=True))
        outer.addWidget(head)
        self.head = head
        self.grid_host = QWidget()
        self.grid = QGridLayout(self.grid_host)
        self.grid.setContentsMargins(20, 20, 20, 10)
        self.grid.setHorizontalSpacing(0)
        self.grid.setVerticalSpacing(12)
        outer.addWidget(self.grid_host, 1)
        foot = QHBoxLayout()
        foot.setContentsMargins(20, 10, 20, 20)
        cancel = Button("Cancel", "face", height=32)
        cancel.setFixedWidth(100)
        cancel.clicked.connect(self.reject)
        foot.addWidget(cancel)
        foot.addSpacing(12)
        for label, lay in (("Default Layout", DEFAULT_LAYOUT), ("Alternative Layout", ALT_LAYOUT)):
            b = Button(label, "ghost", height=32, bold=False)
            b.clicked.connect(lambda _=False, lay=lay: self._preset(lay))
            foot.addWidget(b)
        foot.addStretch(1)
        self.save = Button("Save", "primary", height=32)
        self.save.setFixedWidth(100)
        self.save.clicked.connect(self._save)
        foot.addWidget(self.save)
        outer.addLayout(foot)
        self._drag = None

    def _preset(self, lay):
        self.ctrl.layout = copy.deepcopy(lay)
        self.ctrl.layout["rows"] = [list(r) for r in lay["rows"]]
        self.ctrl.rebuild()
        self.refresh_tiles()

    def refresh_tiles(self):
        while self.grid.count():
            w = self.grid.takeAt(0).widget()
            if w:
                w.deleteLater()
        used = self.ctrl.used()
        tiles = [e for e in CUSTOMIZE_ORDER if e in REPEATABLE or e not in used]
        for i, eid in enumerate(tiles):
            self.grid.addWidget(ElementTile(eid, self), i // 9, i % 9)

    def showEvent(self, e):
        self.ctrl.rebuild()
        self.refresh_tiles()
        par = self.win
        g = par.frameGeometry()
        self.move(g.center().x() - self.width() // 2, g.top() + max(40, (g.height() - self.height()) // 2 - 60))
        super().showEvent(e)

    def exec_(self):
        self.setWindowModality(Qt.NonModal)
        self.show()
        self.raise_()
        self.activateWindow()
        return 0

    def _save(self):
        lay = copy.deepcopy(self.ctrl.layout)
        lay["rows"] = [r for r in lay["rows"] if r and r != ["__newrow__"]]
        if not lay["rows"]:
            lay["rows"] = copy.deepcopy(DEFAULT_LAYOUT["rows"])
        self.ctrl.detach()
        self.win.store.set_layout(lay)
        self.win.notify("Player layout saved")
        self.hide()
        self.deleteLater()

    def reject(self):
        self.ctrl.detach()
        self.win.rebuild_bars()
        self.hide()
        self.deleteLater()

    # dropping an element onto the dialog removes it from the player
    def dragEnterEvent(self, e):
        if e.mimeData().hasFormat(MIME):
            e.acceptProposedAction()

    def dragMoveEvent(self, e):
        if e.mimeData().hasFormat(MIME):
            e.acceptProposedAction()

    def dropEvent(self, e):
        data = json.loads(bytes(e.mimeData().data(MIME)).decode())
        e.acceptProposedAction()
        QTimer.singleShot(0, lambda: self.ctrl.remove(data))

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        m = self.MARGIN
        r = QRectF(self.rect()).adjusted(m, m, -m, -m)
        paint_shadow(p, r, 14, spread=16, alpha=0.46, dy=8)
        p.setPen(Qt.NoPen)
        p.setBrush(c("rail", 0.97))
        p.drawRoundedRect(r, 14, 14)
        from PyQt5.QtGui import QPainterPath
        path = QPainterPath()
        path.addRoundedRect(r, 14, 14)
        p.setClipPath(path)
        p.fillRect(QRectF(r.left(), r.top(), r.width(), self.head.height()), c("panel_head"))

    def mousePressEvent(self, e):
        if e.pos().y() < self.MARGIN + 50:
            self._drag = e.globalPos() - self.frameGeometry().topLeft()

    def mouseMoveEvent(self, e):
        if self._drag is not None:
            self.move(e.globalPos() - self._drag)

    def mouseReleaseEvent(self, e):
        self._drag = None

    def keyPressEvent(self, e):
        if e.key() == Qt.Key_Escape:
            self.reject()
