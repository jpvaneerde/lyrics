"""Desktop UI (PySide6)."""
from __future__ import annotations

import os
import sys
import traceback
from pathlib import Path

from PySide6.QtCore import QObject, QSettings, Qt, QThread, QTimer, Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QAbstractItemView, QApplication, QCheckBox, QComboBox, QFileDialog, QHBoxLayout,
    QHeaderView, QLabel, QLineEdit, QMainWindow, QMessageBox, QPlainTextEdit,
    QProgressBar, QPushButton, QSplitter, QTableWidget, QTableWidgetItem, QVBoxLayout,
    QWidget,
)

from .batch import Job, Options, process, scan
from .util import check_ffmpeg

LANGS = ["auto", "en", "nl", "sv", "de", "fr", "es", "it", "pt", "da", "no", "fi",
         "pl", "ja", "ko", "zh"]
COLS = ["File", "Lyrics", "Status", "Lang", "Confidence", "Time"]
C_FILE, C_LYR, C_STATUS, C_LANG, C_CONF, C_TIME = range(len(COLS))


class Worker(QObject):
    log = Signal(str)
    device = Signal(str)
    row = Signal(int, str, str, str, str, str)  # row, status, lang, conf, time, color
    progress = Signal(int, int)
    row_tip = Signal(int, str)
    finished = Signal()

    def __init__(self, holder, items: list[tuple[int, Job]], opts: Options):
        super().__init__()
        self.holder, self.items, self.opts = holder, items, opts
        self._cancel = False

    def cancel(self):
        self._cancel = True

    def run(self):
        total = len(self.items)
        done = 0
        try:
            if self.holder.engine is None:
                self.log.emit("Starting engine (loading PyTorch/CUDA)…")
                from .engine import Engine

                self.holder.engine = Engine()
            eng = self.holder.engine
            eng.log = self.log.emit
            self.device.emit(eng.device_name)
            for row, job in self.items:
                if self._cancel:
                    self.log.emit("Cancelled.")
                    break
                self.progress.emit(done, total)
                self.row.emit(row, "working…", "", "", "", "#1565c0")
                self.log.emit(f"▶ {job.audio}")
                r = process(eng, job, self.opts, log=self.log.emit)
                for w in r.warnings:
                    self.log.emit(f"  ⚠ {w}")
                color = "#c62828" if r.status.startswith("error") else (
                    "#ef6c00" if r.warnings else ("#2e7d32" if r.ok else "#757575"))
                conf = f"{r.confidence:.2f}" if r.confidence is not None else ""
                secs = f"{r.seconds:.1f}s" if r.seconds else ""
                self.row.emit(row, r.status, r.language, conf, secs, color)
                if r.structure:
                    self.row_tip.emit(row, r.structure)
                self.log.emit(f"  {r.status}")
                done += 1
        except Exception:
            self.log.emit(traceback.format_exc())
        finally:
            self.progress.emit(done, total)
            self.finished.emit()


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("LyricSync")
        self.resize(1100, 750)
        self.s = QSettings("jplay", "lyricsync")
        self.engine = None
        self.jobs: list[Job] = []
        self.thread: QThread | None = None
        self.worker: Worker | None = None

        # folder row
        self.folder = QLineEdit(self.s.value("folder", ""))
        self.folder.returnPressed.connect(self.scan)
        b_browse = QPushButton("Browse…")
        b_browse.clicked.connect(self.browse)
        self.recursive = QCheckBox("Include subfolders")
        self.recursive.setChecked(self.s.value("recursive", True, type=bool))
        self.b_scan = QPushButton("Rescan")
        self.b_scan.clicked.connect(self.scan)
        r1 = QHBoxLayout()
        for w in (QLabel("Folder:"), self.folder, b_browse, self.recursive, self.b_scan):
            r1.addWidget(w)
        r1.setStretch(1, 1)

        # options row
        self.model = QComboBox()
        self.model.addItems(["small", "medium", "large"])
        self.model.setCurrentText(self.s.value("model", "medium"))
        self.lang = QComboBox()
        self.lang.setEditable(True)
        self.lang.addItems(LANGS)
        self.lang.setCurrentText(self.s.value("lang", "auto"))
        self.separate = QCheckBox("Isolate vocals (recommended)")
        self.separate.setChecked(self.s.value("separate", True, type=bool))
        self.words = QCheckBox("Word timing")
        self.words.setToolTip("Enhanced LRC with a timestamp per word (karaoke style)")
        self.words.setChecked(self.s.value("words", False, type=bool))
        self.structure = QCheckBox("Fix chorus repeats from audio")
        self.structure.setToolTip(
            "Transcribes the vocals first and fills in / moves / removes choruses so the\n"
            "lyrics follow what is actually sung. Adds a few seconds per song.")
        self.structure.setChecked(self.s.value("structure", True, type=bool))
        self.overwrite = QCheckBox("Overwrite existing .lrc")
        self.overwrite.setChecked(self.s.value("overwrite", False, type=bool))
        self.backup = QCheckBox("Keep old as .lrc.bak")
        self.backup.setToolTip("When overwriting, first save the existing .lrc next to it as .lrc.bak\n"
                               "(.bak2, .bak3 ... if there already is one), so hand-fixed files aren't lost.")
        self.backup.setChecked(self.s.value("backup", True, type=bool))
        r2 = QHBoxLayout()
        for w in (QLabel("Model:"), self.model, QLabel("Language:"), self.lang,
                  self.separate, self.structure, self.words, self.overwrite, self.backup):
            r2.addWidget(w)
        r2.addStretch(1)

        self.show = QComboBox()
        for label, key in (("All", "all"), ("No .lrc yet", "no lrc"), ("No lyrics text", "no lyrics"),
                           ("Empty .lrc", "empty lrc"), ("Needs review", "review")):
            self.show.addItem(label, key)
        self.show.setToolTip("No .lrc yet: lyrics text exists but no .lrc.\n"
                             "No lyrics text: no .txt next to the song and none in its tags (nothing to sync).\n"
                             "Empty .lrc: an .lrc file with no lyric lines in it.\n"
                             "Needs review: songs marked 'check it' in this session.")
        self.show.currentIndexChanged.connect(self.apply_filter)
        self.shown = QLabel("")
        rf = QHBoxLayout()
        rf.addWidget(QLabel("Show:"))
        rf.addWidget(self.show)
        rf.addWidget(self.shown)
        rf.addStretch(1)

        # table
        self.table = QTableWidget(0, len(COLS))
        self.table.setHorizontalHeaderLabels(COLS)
        hh = self.table.horizontalHeader()
        hh.setSectionResizeMode(QHeaderView.ResizeToContents)
        hh.setSectionResizeMode(C_FILE, QHeaderView.Stretch)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.verticalHeader().setVisible(False)
        self.table.cellDoubleClicked.connect(self.open_row)

        # log
        self.logview = QPlainTextEdit()
        self.logview.setReadOnly(True)
        self.logview.setMaximumBlockCount(5000)
        split = QSplitter(Qt.Vertical)
        split.addWidget(self.table)
        split.addWidget(self.logview)
        split.setSizes([520, 180])

        # bottom row
        b_all = QPushButton("Select ready (shown)")
        b_all.clicked.connect(lambda: self.check(lambda j: j.ready))
        b_none = QPushButton("Select none")
        b_none.clicked.connect(lambda: self.check(lambda j: False))
        self.progress = QProgressBar()
        self.b_start = QPushButton("Sync checked")
        self.b_start.setDefault(True)
        self.b_start.clicked.connect(self.start)
        self.b_cancel = QPushButton("Cancel")
        self.b_cancel.setEnabled(False)
        self.b_cancel.clicked.connect(self.cancel)
        r3 = QHBoxLayout()
        for w in (b_all, b_none, self.progress, self.b_start, self.b_cancel):
            r3.addWidget(w)
        r3.setStretch(2, 1)

        central = QWidget()
        v = QVBoxLayout(central)
        v.addLayout(r1)
        v.addLayout(r2)
        v.addLayout(rf)
        v.addWidget(split, 1)
        v.addLayout(r3)
        self.setCentralWidget(central)
        self.statusBar().showMessage(
            "Lyrics are taken from <song>.txt next to the audio file, or from embedded tags. "
            "Output: <song>.lrc. Double-click a row to open its .lrc.")

        if self.folder.text():
            QTimer.singleShot(0, self.scan)

    # ---- helpers ----------------------------------------------------------
    def log(self, msg: str):
        self.logview.appendPlainText(msg)

    def browse(self):
        d = QFileDialog.getExistingDirectory(self, "Choose music folder", self.folder.text())
        if d:
            self.folder.setText(d)
            self.scan()

    def scan(self):
        folder = Path(self.folder.text().strip())
        if not folder.is_dir():
            self.log(f"Not a folder: {folder}")
            return
        QApplication.setOverrideCursor(Qt.WaitCursor)
        try:
            self.jobs = scan(folder, self.recursive.isChecked())
        finally:
            QApplication.restoreOverrideCursor()
        self.table.setRowCount(len(self.jobs))
        for i, j in enumerate(self.jobs):
            try:
                name = str(j.audio.relative_to(folder))
            except ValueError:
                name = str(j.audio)
            it = QTableWidgetItem(name)
            it.setFlags(it.flags() | Qt.ItemIsUserCheckable)
            it.setCheckState(Qt.Checked if j.ready else Qt.Unchecked)
            it.setToolTip(str(j.audio))
            self.table.setItem(i, C_FILE, it)
            lyr = f"{j.lyrics_source} ({j.n_lines} lines)" if j.n_lines else "—"
            self.table.setItem(i, C_LYR, QTableWidgetItem(lyr))
            st = "empty .lrc" if j.empty_lrc and not j.n_lines else (
                "has .lrc" if j.usable_lrc else ("ready" if j.n_lines else "no lyrics"))
            self.set_row(i, st, "", "", "", "#757575" if st != "ready" else "#000000")
        ready = sum(j.ready for j in self.jobs)
        self.log(f"Scanned {folder}: {len(self.jobs)} audio files, {ready} ready to sync.")
        self.progress.setValue(0)
        self.apply_filter()

    def matches(self, j: Job) -> bool:
        key = self.show.currentData()
        if key == "all":
            return True
        if key == "review":
            return j.review
        return j.category == key

    def apply_filter(self):
        n = 0
        for i, j in enumerate(self.jobs):
            show = self.matches(j)
            self.table.setRowHidden(i, not show)
            n += show
        self.shown.setText(f"{n} of {len(self.jobs)} shown")

    def check(self, pred):
        for i, j in enumerate(self.jobs):   # only the rows that are shown (the filter), the rest are unchecked
            self.table.item(i, C_FILE).setCheckState(
                Qt.Checked if pred(j) and not self.table.isRowHidden(i) else Qt.Unchecked)

    def set_row(self, row, status, lang, conf, secs, color):
        for col, val in ((C_STATUS, status), (C_LANG, lang), (C_CONF, conf), (C_TIME, secs)):
            it = QTableWidgetItem(val)
            if col == C_STATUS:
                it.setForeground(QColor(color))
            self.table.setItem(row, col, it)
        if status.startswith("working"):
            self.table.scrollToItem(self.table.item(row, C_FILE))

    def open_row(self, row, _col):
        j = self.jobs[row]
        target = j.lrc if j.lrc.exists() else j.audio.parent
        if sys.platform == "win32":
            os.startfile(str(target))  # noqa: S606

    def options(self) -> Options:
        lang = self.lang.currentText().strip().lower()
        return Options(self.model.currentText(), None if lang in ("", "auto") else lang,
                       self.separate.isChecked(), self.words.isChecked(),
                       self.overwrite.isChecked(), self.structure.isChecked(), self.backup.isChecked())

    def save_settings(self):
        for k, v in (("folder", self.folder.text()), ("recursive", self.recursive.isChecked()),
                     ("model", self.model.currentText()), ("lang", self.lang.currentText()),
                     ("separate", self.separate.isChecked()), ("words", self.words.isChecked()),
                     ("overwrite", self.overwrite.isChecked()), ("backup", self.backup.isChecked()),
                     ("structure", self.structure.isChecked())):
            self.s.setValue(k, v)

    # ---- run --------------------------------------------------------------
    def start(self):
        items = [(i, j) for i, j in enumerate(self.jobs)
                 if self.table.item(i, C_FILE).checkState() == Qt.Checked and not self.table.isRowHidden(i)]
        if not items:
            self.log("Nothing checked.")
            return
        err = check_ffmpeg()
        if err:
            QMessageBox.critical(self, "ffmpeg missing", err)
            return
        self.save_settings()
        self.set_running(True)
        self.progress.setRange(0, len(items))
        self.progress.setValue(0)

        self.thread = QThread(self)
        self.worker = Worker(self, items, self.options())
        self.worker.moveToThread(self.thread)
        self.thread.started.connect(self.worker.run)
        self.worker.log.connect(self.log)
        self.worker.device.connect(lambda d: self.setWindowTitle(f"LyricSync — {d}"))
        self.worker.row.connect(self.set_row)
        self.worker.row_tip.connect(
            lambda r, t: self.table.item(r, C_STATUS) and self.table.item(r, C_STATUS).setToolTip(t))
        self.worker.progress.connect(lambda d, t: self.progress.setValue(d))
        self.worker.finished.connect(self.thread.quit)
        self.worker.finished.connect(lambda: self.set_running(False))
        self.worker.finished.connect(self.apply_filter)
        self.worker.finished.connect(self.worker.deleteLater)
        self.thread.finished.connect(self.thread.deleteLater)
        self.thread.start()

    def cancel(self):
        if self.worker:
            self.worker.cancel()
            self.log("Cancelling after the current file…")
            self.b_cancel.setEnabled(False)

    def set_running(self, running: bool):
        self.b_start.setEnabled(not running)
        self.b_scan.setEnabled(not running)
        self.b_cancel.setEnabled(running)
        if not running:
            self.worker = None
            self.thread = None

    def closeEvent(self, e):
        self.save_settings()
        if self.worker is not None:
            r = QMessageBox.question(self, "Still running",
                                     "A sync is running. Quit anyway?")
            if r != QMessageBox.Yes:
                e.ignore()
                return
            os._exit(0)  # .lrc files are written atomically, nothing half-written
        e.accept()


def run():
    app = QApplication(sys.argv)
    app.setApplicationName("LyricSync")
    w = MainWindow()
    w.show()
    sys.exit(app.exec())
