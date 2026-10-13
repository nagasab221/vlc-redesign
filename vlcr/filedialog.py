"""Open/save dialogs with a sane size.

Qt's own file dialog sizes itself to its widest combo box. Our media filter lists every
extension, so it came out as wide as the screen and couldn't be made narrower. Here the
filter shows only its name ("Media") and the combos stop growing with their contents.
Same call signatures as QFileDialog's static helpers.
"""

from PyQt5.QtWidgets import QApplication, QComboBox, QFileDialog


def _run(parent, caption, directory, file_filter, mode, options):
    dlg = QFileDialog(parent, caption or "", directory or "", file_filter or "")
    dlg.setOptions(QFileDialog.Options(options or 0) | QFileDialog.HideNameFilterDetails)
    if mode == "file":
        dlg.setFileMode(QFileDialog.ExistingFile)
    elif mode == "files":
        dlg.setFileMode(QFileDialog.ExistingFiles)
    elif mode == "dir":
        dlg.setFileMode(QFileDialog.Directory)
        dlg.setOption(QFileDialog.ShowDirsOnly, True)
    elif mode == "save":
        dlg.setAcceptMode(QFileDialog.AcceptSave)
        dlg.setFileMode(QFileDialog.AnyFile)
    for combo in dlg.findChildren(QComboBox):
        combo.setSizeAdjustPolicy(QComboBox.AdjustToMinimumContentsLengthWithIcon)
        combo.setMinimumContentsLength(12)
    anchor = parent.window() if parent is not None else None
    screen = (anchor.screen() if anchor is not None else None) or QApplication.primaryScreen()
    avail = screen.availableGeometry()
    dlg.setMinimumSize(480, 320)
    w, h = min(960, int(avail.width() * 0.8)), min(620, int(avail.height() * 0.8))
    dlg.resize(w, h)
    if anchor is not None and anchor.isVisible():
        c = anchor.frameGeometry().center()
        dlg.move(max(avail.left(), c.x() - w // 2), max(avail.top(), c.y() - h // 2))
    if dlg.exec_() != QFileDialog.Accepted:
        return []
    return dlg.selectedFiles()


class FileDialog:
    @staticmethod
    def getOpenFileName(parent=None, caption="", directory="", filter="", initialFilter="", options=0):
        files = _run(parent, caption, directory, filter, "file", options)
        return (files[0] if files else "", "")

    @staticmethod
    def getOpenFileNames(parent=None, caption="", directory="", filter="", initialFilter="", options=0):
        return (_run(parent, caption, directory, filter, "files", options), "")

    @staticmethod
    def getExistingDirectory(parent=None, caption="", directory="", options=0):
        files = _run(parent, caption, directory, "", "dir", options)
        return files[0] if files else ""

    @staticmethod
    def getSaveFileName(parent=None, caption="", directory="", filter="", initialFilter="", options=0):
        files = _run(parent, caption, directory, filter, "save", options)
        return (files[0] if files else "", "")
