"""Fenetre principale de Auto Cut."""
import os
from bisect import bisect_right
from copy import copy

from PySide6.QtCore import QEvent, QObject, QSizeF, Qt, QThread, QTimer, QUrl, Signal
from PySide6.QtGui import QAction, QBrush, QColor, QFont, QKeySequence, QPalette, QPen, QShortcut
from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer
from PySide6.QtMultimediaWidgets import QGraphicsVideoItem
from PySide6.QtWidgets import (
    QAbstractItemView, QApplication, QInputDialog, QDialog, QFrame, QTextBrowser, QGraphicsRectItem, QGraphicsScene, QGraphicsSimpleTextItem, QGraphicsView, QCheckBox, QComboBox, QDoubleSpinBox, QFileDialog, QFormLayout,
    QGridLayout, QGroupBox, QHBoxLayout, QHeaderView, QLabel, QMainWindow, QMessageBox, QPushButton,
    QButtonGroup, QRadioButton, QProgressBar, QScrollArea, QSlider, QSplitter, QStackedWidget, QTableWidget, QTableWidgetItem,
    QVBoxLayout, QWidget,
)

from .audiotab import AudioPage
from .detect import DEFAULTS, MODE_NAMES, Cut, Settings, detect_cuts, normalize_cuts
from . import render
from .editor import EditorPage, make_cut_clips
from .project import MIN_DUR, Clip
from .export import export_xml, project_dir, unique_path
from .ffmpeg_tools import ENV_HOP, audio_envelope, probe
from . import i18n, presets, settings, theme
from .i18n import tr
from . import projects
from .dialogs import HelpDialog, OptionsDialog, WelcomeDialog
from .theme import apply_theme
from .waveform import OverviewWidget, WaveformWidget


def fmt(t):
    t = max(0.0, t)
    return f"{int(t // 60)}:{t % 60:05.2f}"


apply_dark = apply_theme


# ---------------------------------------------------------------- travaux en arriere-plan
class EnvWorker(QThread):
    done = Signal(int, object)
    failed = Signal(str)

    def __init__(self, path, idx):
        super().__init__()
        self.path, self.idx = path, idx

    def run(self):
        try:
            self.done.emit(self.idx, audio_envelope(self.path, self.idx))
        except Exception as e:  # noqa: BLE001
            self.failed.emit(tr("st.analyse.fail", n=self.idx + 1, err=e))


class ExportWorker(QThread):
    progress = Signal(str)
    done = Signal(str)
    failed = Signal(str)

    def __init__(self, media, cuts, mode, xml_path, separate):
        super().__init__()
        self.args = (media, cuts, mode, xml_path, separate)

    def run(self):
        try:
            export_xml(*self.args, progress=self.progress.emit)
            self.done.emit(self.args[3])
        except Exception as e:  # noqa: BLE001
            self.failed.emit(str(e))


# ---------------------------------------------------------------- video (avec teinte rouge possible)
class Mp4Worker(QThread):
    """Cree le MP4 avec les coupes appliquees, en arriere-plan."""
    progress = Signal(float)
    done = Signal(str)
    failed = Signal(str)

    def __init__(self, media, segs, path):
        super().__init__()
        self.media, self.segs, self.path = media, segs, path
        self._stop = False

    def stop(self):
        self._stop = True

    def run(self):
        try:
            render.render_cuts(self.media, self.segs, self.path, progress=self.progress.emit,
                               should_stop=lambda: self._stop)
            self.done.emit(self.path)
        except InterruptedError:
            self.failed.emit("")
        except Exception as e:  # noqa: BLE001
            self.failed.emit(str(e))


class VideoView(QGraphicsView):
    def __init__(self):
        super().__init__()
        self.setScene(QGraphicsScene(self))
        self.setBackgroundBrush(QBrush(Qt.black))
        self.setFrameShape(QGraphicsView.NoFrame)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setMinimumSize(320, 150)
        self.item = QGraphicsVideoItem()
        self.scene().addItem(self.item)
        self.tint = QGraphicsRectItem()
        self.tint.setBrush(QBrush(QColor(230, 40, 40, 95)))
        self.tint.setPen(QPen(Qt.NoPen))
        self.tint.setZValue(1)
        self.scene().addItem(self.tint)
        self.label = QGraphicsSimpleTextItem(tr("tint.label"))
        f = QFont("Segoe UI")
        f.setBold(True)
        f.setPixelSize(56)
        self.label.setFont(f)
        self.label.setBrush(QBrush(Qt.white))
        self.label.setPos(40, 30)
        self.label.setZValue(2)
        self.scene().addItem(self.label)
        self.set_tinted(False)
        self.item.nativeSizeChanged.connect(self._fit)
        self._fit()

    def set_tinted(self, on):
        self.tint.setVisible(on)
        self.label.setVisible(on)

    def _fit(self, *_):
        s = self.item.nativeSize()
        if s.isEmpty():
            s = QSizeF(1920, 1080)
        self.item.setSize(s)
        self.tint.setRect(0, 0, s.width(), s.height())
        self.scene().setSceneRect(0, 0, s.width(), s.height())
        self.fitInView(self.scene().sceneRect(), Qt.KeepAspectRatio)

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self._fit()


class UndoKeyFilter(QObject):
    """Laisse Ctrl+Z / Ctrl+Y agir sur les coupes meme quand le curseur est dans une case de saisie."""

    def eventFilter(self, obj, ev):
        if ev.type() == QEvent.ShortcutOverride:
            for k in (QKeySequence.Undo, QKeySequence.Redo):
                if ev.matches(k):
                    ev.ignore()
                    return True
        return False


# ---------------------------------------------------------------- un reglage = curseur + valeur
class ParamRow(QObject):
    changed = Signal(float)

    def __init__(self, grid, row, label_key, tip_key, lo, hi, step, decimals, suffix):
        super().__init__()
        self.label_key, self.tip_key = label_key, tip_key
        self.scale = round(1 / step)
        self.lab = QLabel()
        self.slider = QSlider(Qt.Horizontal)
        self.slider.setRange(round(lo * self.scale), round(hi * self.scale))
        self.spin = QDoubleSpinBox()
        self.spin.setRange(lo, hi)
        self.spin.setSingleStep(step)
        self.spin.setDecimals(decimals)
        self.spin.setSuffix(suffix)
        grid.addWidget(self.lab, row, 0)
        grid.addWidget(self.slider, row, 1)
        grid.addWidget(self.spin, row, 2)
        self.slider.valueChanged.connect(lambda v: self.spin.setValue(v / self.scale))
        self.spin.valueChanged.connect(self._from_spin)
        self.retranslate()

    def retranslate(self):
        tip = tr(self.tip_key)
        self.lab.setText(tr(self.label_key))
        for w in (self.lab, self.slider, self.spin):
            w.setToolTip(tip)

    def _from_spin(self, v):
        self.slider.blockSignals(True)
        self.slider.setValue(round(v * self.scale))
        self.slider.blockSignals(False)
        self.changed.emit(v)

    def set_value(self, v):
        self.spin.blockSignals(True)
        self.slider.blockSignals(True)
        self.spin.setValue(v)
        self.slider.setValue(round(v * self.scale))
        self.spin.blockSignals(False)
        self.slider.blockSignals(False)


# ---------------------------------------------------------------- fenetre principale
class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle(tr("app.title"))
        self.resize(1500, 920)
        self.setMinimumSize(900, 520)
        self.setAcceptDrops(True)

        self.media = None
        self.envs = {}                   # numero de piste audio -> enveloppe en dB
        self.workers = []
        self.mode = "dead"
        self.settings = {m: presets.load_last(m) or copy(d) for m, d in DEFAULTS.items()}
        self.cuts_by_mode = {"dead": [], "classic": []}
        self.cuts = []
        self.sel = -1
        self.preview = None
        self._skip = ([], [])
        self._updating = False
        self.undo_stack = {"dead": [], "classic": []}
        self.redo_stack = {"dead": [], "classic": []}
        self._coalesce = QTimer(self)
        self._coalesce.setSingleShot(True)
        self._coalesce.setInterval(700)
        self._key_filter = UndoKeyFilter(self)
        self._tr = []
        self._play_txt = None
        self._auto_sent = None
        self.done = []                       # coupes definitives deja faites (numeros d'image du fichier ouvert)
        self.pass_rev = None                 # not None : l'onglet Couper travaille sur un montage (passe)
        self.mp4_worker = None

        self.player = QMediaPlayer(self)
        self.audio_out = QAudioOutput(self)
        self.player.setAudioOutput(self.audio_out)
        self.video = VideoView()
        self.player.setVideoOutput(self.video.item)
        self.player.errorOccurred.connect(lambda _e, msg: self.status(tr("st.player", msg=msg)))
        self.player.tracksChanged.connect(self._apply_preview_track)

        self._build_ui()
        self.timer = QTimer(self)
        self.timer.setInterval(20)
        self.timer.timeout.connect(self._tick)
        self.timer.start()
        QShortcut(QKeySequence(Qt.Key_Space), self, activated=self.toggle_play)
        QShortcut(QKeySequence(Qt.Key_F1), self, activated=self.show_help)
        QShortcut(QKeySequence(Qt.Key_F2), self, activated=self.toggle_panel)
        for n in range(3):
            QShortcut(QKeySequence(f"Ctrl+{n + 1}"), self, activated=lambda i=n: self.set_tab(i))
        QShortcut(QKeySequence(Qt.Key_F11), self, activated=self.toggle_fullscreen)
        QShortcut(QKeySequence(Qt.Key_Escape), self, activated=self._leave_fullscreen)
        self.act_undo = QAction("Annuler", self)
        self.act_undo.setShortcut(QKeySequence.Undo)
        self.act_undo.triggered.connect(self.undo)
        self.act_redo = QAction("Retablir", self)
        self.act_redo.setShortcuts([QKeySequence.Redo, QKeySequence("Ctrl+Shift+Z")])
        self.act_redo.triggered.connect(self.redo)
        self.addAction(self.act_undo)
        self.addAction(self.act_redo)
        self._update_undo_buttons()
        self.status(tr("st.start"))

    # ------------------------------------------------------------ construction de l'interface
    def _r(self, widget, key, method="setText", **kw):
        """Donne un texte traduit a un widget ET le memorise pour le retraduire si la langue change."""
        def apply(w=widget, k=key, m=method, a=kw):
            getattr(w, m)(tr(k, **a))
        self._tr.append(apply)
        apply()
        return widget

    def _build_ui(self):
        r = self._r
        root = QWidget()
        root.setObjectName("root")
        rl = QVBoxLayout(root)
        rl.setContentsMargins(0, 0, 0, 0)
        rl.setSpacing(0)

        # ---- en-tête
        header = QFrame()
        header.setObjectName("header")
        hl = QHBoxLayout(header)
        hl.setContentsMargins(18, 10, 18, 10)
        hl.setSpacing(14)
        logo = QLabel("✂  Auto Cut")
        logo.setObjectName("logo")
        self.lbl_file = QLabel()
        self.lbl_file.setObjectName("fileinfo")
        self.tab_group = QButtonGroup(self)
        self.tab_buttons = []
        for i, key in enumerate(("tab.cut", "tab.edit", "tab.audio")):
            b = r(QPushButton(), key)
            b.setObjectName("tab")
            b.setCheckable(True)
            b.setCursor(Qt.PointingHandCursor)
            b.setChecked(i == 0)
            self.tab_group.addButton(b, i)
            self.tab_buttons.append(b)
        self.tab_group.idClicked.connect(self.set_tab)
        self.btn_open = r(QPushButton(), "btn.open")
        self.btn_open.setObjectName("primary")
        self.btn_open.setCursor(Qt.PointingHandCursor)
        self.btn_open.clicked.connect(self.open_dialog)
        self.btn_options = r(QPushButton(), "btn.options")
        r(self.btn_options, "tip.options", "setToolTip")
        self.btn_options.clicked.connect(self.show_options)
        self.btn_help = r(QPushButton(), "btn.help")
        r(self.btn_help, "tip.help", "setToolTip")
        self.btn_help.clicked.connect(self.show_help)
        self.btn_home = r(QPushButton(), "btn.home")
        r(self.btn_home, "tip.home", "setToolTip")
        self.btn_home.clicked.connect(self.show_welcome)
        self.btn_fs = QPushButton("⛶")
        self.btn_fs.setObjectName("icon")
        r(self.btn_fs, "tip.fs", "setToolTip")
        self.btn_fs.clicked.connect(self.toggle_fullscreen)
        hl.addWidget(logo)
        hl.addSpacing(14)
        for b in self.tab_buttons:
            hl.addWidget(b)
        hl.addStretch(1)
        hl.addWidget(self.btn_open)
        hl.addWidget(self.btn_home)
        hl.addWidget(self.btn_options)
        hl.addWidget(self.btn_help)
        hl.addWidget(self.btn_fs)
        rl.addWidget(header)

        # ---- colonne gauche : vidéo + forme d'onde
        left = QWidget()
        lv = QVBoxLayout(left)
        lv.setContentsMargins(14, 12, 6, 10)
        lv.setSpacing(6)
        lv.addWidget(self.video, 1)

        ctl = QHBoxLayout()
        ctl.setSpacing(10)
        self.btn_play = QPushButton(tr("btn.play"))
        self.btn_play.setFixedWidth(130)
        self.btn_play.clicked.connect(self.toggle_play)
        self.pos_slider = QSlider(Qt.Horizontal)
        self.pos_slider.sliderMoved.connect(lambda v: self.player.setPosition(v))
        self.lbl_time = QLabel("0:00.00 / 0:00.00")
        self.lbl_time.setMinimumWidth(130)
        self.lbl_time.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        ctl.addWidget(self.btn_play)
        ctl.addWidget(self.pos_slider, 1)
        ctl.addWidget(self.lbl_time)
        lv.addLayout(ctl)

        self.chk_skip = r(QCheckBox(), "chk.skip")
        self.chk_skip.setChecked(True)
        self.chk_tint = r(QCheckBox(), "chk.tint")
        r(self.chk_tint, "tip.tint", "setToolTip")
        self.chk_tint.toggled.connect(lambda _: self._update_tint(self.player.position() / 1000.0))
        opts = QHBoxLayout()
        opts.setSpacing(24)
        opts.addWidget(self.chk_skip)
        opts.addWidget(self.chk_tint)
        opts.addStretch()
        lv.addLayout(opts)

        self.wave = WaveformWidget()
        self.wave.seekRequested.connect(self.seek_seconds)
        self.wave.cutSelected.connect(lambda i: self.select_cut(i, seek=True))
        self.wave.cutEdited.connect(self._cut_edited_from_wave)
        self.wave.editStarted.connect(lambda _i: self.push_undo())
        self.wave.cutToggled.connect(self.toggle_cut)
        self.wave.viewChanged.connect(self._update_zoom_back)
        self.overview = OverviewWidget(self.wave)
        lv.addWidget(self.overview)
        lv.addWidget(self.wave)

        nav = QHBoxLayout()
        nav.setSpacing(6)
        self.btn_zoom_back = None
        for name, fn in (("prev", lambda: self.goto_cut(-1)), ("next", lambda: self.goto_cut(1)),
                         ("zoomout", lambda: self.wave.zoom(1.5)), ("zoomin", lambda: self.wave.zoom(0.66)),
                         ("zoomcut", self.zoom_on_cut), ("zoomback", self.zoom_back), ("all", self.show_all_view)):
            b = r(QPushButton(), "nav." + name)
            r(b, "tip." + name, "setToolTip")
            b.setObjectName("nav")
            b.clicked.connect(fn)
            nav.addWidget(b)
            if name == "zoomback":
                self.btn_zoom_back = b
        lv.addLayout(nav)
        self._update_zoom_back()

        # ---- colonne droite : réglages
        right = QWidget()
        rv = QVBoxLayout(right)
        rv.setContentsMargins(6, 4, 14, 10)
        rv.setSpacing(6)

        # 1. pistes audio
        g1 = r(QGroupBox(), "grp.tracks", "setTitle")
        f1 = QFormLayout(g1)
        f1.setVerticalSpacing(8)
        f1.setRowWrapPolicy(QFormLayout.WrapLongRows)
        self.cb_mix = QComboBox()
        self.cb_voice = QComboBox()
        self.cb_listen = QComboBox()
        r(self.cb_mix, "tip.mix", "setToolTip")
        r(self.cb_voice, "tip.voice", "setToolTip")
        r(self.cb_listen, "tip.listen", "setToolTip")
        f1.addRow(r(QLabel(), "lbl.mix"), self.cb_mix)
        f1.addRow(r(QLabel(), "lbl.voice"), self.cb_voice)
        f1.addRow(r(QLabel(), "lbl.listen"), self.cb_listen)
        self.cb_mix.currentIndexChanged.connect(lambda _: self._track_changed("dead"))
        self.cb_voice.currentIndexChanged.connect(lambda _: self._track_changed("classic"))
        self.cb_listen.currentIndexChanged.connect(lambda _: self._apply_preview_track())
        rv.addWidget(g1)

        # 2. détection
        g2 = r(QGroupBox(), "grp.detect", "setTitle")
        v2 = QVBoxLayout(g2)
        v2.setSpacing(8)
        mrow = QHBoxLayout()
        self.rb_dead = r(QRadioButton(), "mode.dead")
        self.rb_classic = r(QRadioButton(), "mode.classic")
        r(self.rb_dead, "tip.mode.dead", "setToolTip")
        r(self.rb_classic, "tip.mode.classic", "setToolTip")
        self.rb_dead.setChecked(True)
        mrow.addWidget(self.rb_dead)
        mrow.addWidget(self.rb_classic)
        mrow.addStretch()
        v2.addLayout(mrow)

        prow0 = QHBoxLayout()
        prow0.addWidget(r(QLabel(), "lbl.preset"))
        self.cb_preset = QComboBox()
        r(self.cb_preset, "tip.preset", "setToolTip")
        self.cb_preset.activated.connect(self._preset_chosen)
        prow0.addWidget(self.cb_preset, 1)
        v2.addLayout(prow0)

        grid = QGridLayout()
        grid.setVerticalSpacing(10)
        self.p_thr = ParamRow(grid, 0, "p.thr", "tip.thr", -80, -10, 1, 0, " dB")
        self.p_min = ParamRow(grid, 1, "p.min", "tip.min", 0.05, 10, 0.05, 2, " s")
        self.p_before = ParamRow(grid, 2, "p.before", "tip.before", 0, 1.0, 0.01, 2, " s")
        self.p_after = ParamRow(grid, 3, "p.after", "tip.after", 0, 1.0, 0.01, 2, " s")
        for row in (self.p_thr, self.p_min, self.p_before, self.p_after):
            self._tr.append(row.retranslate)
        grid.setColumnStretch(1, 1)
        v2.addLayout(grid)
        brow = QHBoxLayout()
        self.btn_reset = r(QPushButton(), "btn.defaults")
        self.btn_reset.clicked.connect(self.reset_settings)
        self.btn_preset_save = r(QPushButton(), "btn.preset.save")
        r(self.btn_preset_save, "tip.preset.save", "setToolTip")
        self.btn_preset_save.clicked.connect(self.save_preset)
        self.btn_preset_delete = r(QPushButton(), "btn.preset.delete")
        r(self.btn_preset_delete, "tip.preset.delete", "setToolTip")
        self.btn_preset_delete.clicked.connect(self.delete_preset)
        brow.addWidget(self.btn_reset)
        brow.addStretch()
        brow.addWidget(self.btn_preset_save)
        brow.addWidget(self.btn_preset_delete)
        v2.addLayout(brow)
        warn = r(QLabel(), "warn.redetect")
        warn.setWordWrap(True)
        warn.setObjectName("muted")
        v2.addWidget(warn)
        rv.addWidget(g2)
        self.rb_dead.toggled.connect(lambda on: on and self.set_mode("dead"))
        self.rb_classic.toggled.connect(lambda on: on and self.set_mode("classic"))
        for row, key in ((self.p_thr, "threshold_db"), (self.p_min, "min_silence"),
                         (self.p_before, "margin_before"), (self.p_after, "margin_after")):
            row.changed.connect(lambda v, k=key: self._param_changed(k, v))
        self.redetect_timer = QTimer(self)
        self.redetect_timer.setSingleShot(True)
        self.redetect_timer.setInterval(200)
        self.redetect_timer.timeout.connect(self.redetect)

        # 3. coupes
        g3 = r(QGroupBox(), "grp.cuts", "setTitle")
        v3 = QVBoxLayout(g3)
        v3.setSpacing(8)
        self.lbl_sum = QLabel("-")
        self.lbl_sum.setObjectName("summary")
        self.lbl_sum.setWordWrap(True)
        v3.addWidget(self.lbl_sum)
        self.table = QTableWidget(0, 5)
        self.table.setHorizontalHeaderLabels([tr(k) for k in ("th.cut", "th.n", "th.start", "th.end", "th.dur")])
        self.table.verticalHeader().setVisible(False)
        self.table.verticalHeader().setDefaultSectionSize(30)
        self.table.setShowGrid(False)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setMinimumHeight(190)
        hh = self.table.horizontalHeader()
        hh.setSectionResizeMode(QHeaderView.Stretch)
        hh.setSectionResizeMode(0, QHeaderView.ResizeToContents)
        hh.setSectionResizeMode(1, QHeaderView.ResizeToContents)
        self.table.itemSelectionChanged.connect(self._table_selection)
        self.table.itemChanged.connect(self._table_item_changed)
        v3.addWidget(self.table, 1)

        det = QFormLayout()
        det.setVerticalSpacing(6)
        det.setRowWrapPolicy(QFormLayout.WrapLongRows)
        self.sp_start = QDoubleSpinBox()
        self.sp_end = QDoubleSpinBox()
        for sp in (self.sp_start, self.sp_end):
            sp.setDecimals(3)
            sp.setRange(0, 999999)
            sp.setSuffix(" s")
            sp.setSingleStep(1 / 60)
            sp.setEnabled(False)
        self.sp_start.valueChanged.connect(lambda v: self._spin_edited("start", v))
        self.sp_end.valueChanged.connect(lambda v: self._spin_edited("end", v))
        det.addRow(r(QLabel(), "lbl.start"), self.sp_start)
        det.addRow(r(QLabel(), "lbl.end"), self.sp_end)
        v3.addLayout(det)

        prow = QHBoxLayout()
        self.btn_prev_cut = r(QPushButton(), "btn.prevcut")
        r(self.btn_prev_cut, "tip.prevcut", "setToolTip")
        self.btn_prev_gap = r(QPushButton(), "btn.prevgap")
        r(self.btn_prev_gap, "tip.prevgap", "setToolTip")
        self.btn_prev_cut.clicked.connect(self.preview_with_cut)
        self.btn_prev_gap.clicked.connect(self.preview_removed)
        prow.addWidget(self.btn_prev_cut)
        prow.addWidget(self.btn_prev_gap)
        v3.addLayout(prow)

        orow = QHBoxLayout()
        orow.addWidget(r(QLabel(), "lbl.selcut"))
        self.btn_cut_one = r(QPushButton(), "btn.cutone")
        r(self.btn_cut_one, "tip.cutone", "setToolTip")
        self.btn_keep_one = r(QPushButton(), "btn.keepone")
        r(self.btn_keep_one, "tip.keepone", "setToolTip")
        self.btn_cut_one.clicked.connect(lambda: self.set_selected(True))
        self.btn_keep_one.clicked.connect(lambda: self.set_selected(False))
        orow.addWidget(self.btn_cut_one)
        orow.addWidget(self.btn_keep_one)
        v3.addLayout(orow)

        hrow = QHBoxLayout()
        hrow.addWidget(r(QLabel(), "lbl.hard"))
        self.btn_hard_one = r(QPushButton(), "btn.hardone")
        r(self.btn_hard_one, "tip.hardone", "setToolTip")
        self.btn_hard_all = r(QPushButton(), "btn.hardall")
        r(self.btn_hard_all, "tip.hardall", "setToolTip")
        self.btn_hard_one.clicked.connect(lambda: self.cut_definitively("one"))
        self.btn_hard_all.clicked.connect(lambda: self.cut_definitively("all"))
        hrow.addWidget(self.btn_hard_one)
        hrow.addWidget(self.btn_hard_all)
        v3.addLayout(hrow)

        arow = QHBoxLayout()
        arow.addWidget(r(QLabel(), "lbl.allcuts"))
        self.btn_cutall = r(QPushButton(), "btn.cutall")
        r(self.btn_cutall, "tip.cutall", "setToolTip")
        self.btn_keepall = r(QPushButton(), "btn.keepall")
        r(self.btn_keepall, "tip.keepall", "setToolTip")
        self.btn_cutall.clicked.connect(lambda: self.set_all(True))
        self.btn_keepall.clicked.connect(lambda: self.set_all(False))
        arow.addWidget(self.btn_cutall)
        arow.addWidget(self.btn_keepall)
        v3.addLayout(arow)

        rrow = QHBoxLayout()
        self.btn_reset_one = r(QPushButton(), "btn.reset1")
        r(self.btn_reset_one, "tip.reset1", "setToolTip")
        self.btn_reset_all = r(QPushButton(), "btn.resetall")
        r(self.btn_reset_all, "tip.resetall", "setToolTip")
        self.btn_reset_one.clicked.connect(self.reset_selected)
        self.btn_reset_all.clicked.connect(self.reset_all_cuts)
        rrow.addWidget(r(QLabel(), "lbl.reset"))
        rrow.addWidget(self.btn_reset_one)
        rrow.addWidget(self.btn_reset_all)
        v3.addLayout(rrow)

        urow = QHBoxLayout()
        self.btn_undo = r(QPushButton(), "btn.undo")
        self.btn_redo = r(QPushButton(), "btn.redo")
        self.btn_undo.clicked.connect(self.undo)
        self.btn_redo.clicked.connect(self.redo)
        urow.addWidget(self.btn_undo)
        urow.addWidget(self.btn_redo)
        v3.addLayout(urow)
        for sp in (self.sp_start, self.sp_end):
            sp.installEventFilter(self._key_filter)
            sp.lineEdit().installEventFilter(self._key_filter)
        for row in (self.p_thr, self.p_min, self.p_before, self.p_after):
            row.spin.installEventFilter(self._key_filter)
            row.spin.lineEdit().installEventFilter(self._key_filter)
        rv.addWidget(g3, 1)

        # 4. export
        g4 = r(QGroupBox(), "grp.export", "setTitle")
        v4 = QVBoxLayout(g4)
        v4.setSpacing(8)
        self.rb_mark = r(QRadioButton(), "rb.mark")
        self.rb_remove = r(QRadioButton(), "rb.remove")
        self.rb_remove.setChecked(True)
        self.chk_sep = r(QCheckBox(), "chk.sep")
        self.chk_sep.setChecked(True)
        r(self.chk_sep, "tip.sep", "setToolTip")
        self.btn_export = r(QPushButton(), "btn.export")
        self.btn_export.clicked.connect(self.do_export)
        self.btn_export.setEnabled(False)
        self.btn_mp4 = r(QPushButton(), "btn.mp4")
        r(self.btn_mp4, "tip.mp4", "setToolTip")
        self.btn_mp4.clicked.connect(self.do_export_mp4)
        self.btn_mp4.setEnabled(False)
        self.btn_send = r(QPushButton(), "btn.send")
        r(self.btn_send, "tip.send", "setToolTip")
        self.btn_send.clicked.connect(self.send_to_editor)
        self.btn_send.setEnabled(False)
        for b in (self.btn_export, self.btn_mp4, self.btn_send):          # les 3 exports ont exactement le meme aspect
            b.setStyleSheet("padding: 11px 14px; font-size: 11pt;")
            b.setCursor(Qt.PointingHandCursor)
        for w in (self.rb_mark, self.rb_remove, self.chk_sep, self.btn_export, self.btn_mp4, self.btn_send):
            v4.addWidget(w)
        rv.addWidget(g4)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(right)
        scroll.setMinimumWidth(480)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        scroll.setFrameShape(QScrollArea.NoFrame)

        # fleche collee au bord du panneau : cache ou montre la colonne de reglages
        self.panel = scroll
        self._panels = {"cut": True, "edit": True}      # chaque onglet garde son propre etat
        self.btn_panel = QPushButton("▶")
        self.btn_panel.setObjectName("panelarrow")
        self.btn_panel.setFixedSize(18, 88)
        self.btn_panel.setCursor(Qt.PointingHandCursor)
        self.btn_panel.clicked.connect(lambda: self.toggle_panel("cut"))
        left_wrap = QWidget()
        lw = QHBoxLayout(left_wrap)
        lw.setContentsMargins(0, 0, 0, 0)
        lw.setSpacing(0)
        lw.addWidget(left, 1)
        lw.addWidget(self.btn_panel, 0, Qt.AlignVCenter)

        split = QSplitter()
        split.setHandleWidth(4)
        split.addWidget(left_wrap)
        split.addWidget(scroll)
        split.setStretchFactor(0, 3)
        split.setStretchFactor(1, 1)
        split.setSizes([1000, 480])
        self.stack = QStackedWidget()
        self.stack.addWidget(split)
        self.editor = EditorPage(self)
        self.audio = AudioPage(self, self.editor)
        self.editor.audio_page = self.audio
        self.stack.addWidget(self.editor)
        self.stack.addWidget(self.audio)
        rl.addWidget(self.stack, 1)
        # bandeau d'avancement : visible dans tous les onglets pendant un export / une preparation (annulable)
        self.task_bar = QFrame()
        self.task_bar.setObjectName("taskbar")
        tl_ = QHBoxLayout(self.task_bar)
        tl_.setContentsMargins(18, 8, 18, 8)
        tl_.setSpacing(14)
        self.task_label = QLabel()
        self.task_label.setMinimumWidth(300)
        self.task_progress = QProgressBar()
        self.task_progress.setTextVisible(False)
        self.task_progress.setFixedHeight(12)
        self.btn_task_cancel = QPushButton(tr("btn.cancel"))
        self.btn_task_cancel.clicked.connect(self._task_cancel_clicked)
        tl_.addWidget(self.task_label)
        tl_.addWidget(self.task_progress, 1)
        tl_.addWidget(self.btn_task_cancel)
        self.task_bar.hide()
        self._task_cancel = None
        rl.addWidget(self.task_bar)
        self.setCentralWidget(root)
        self.statusBar().addPermanentWidget(self.lbl_file)
        self.set_panel_visible(settings.get("panel_cut"), "cut", save=False)
        self.set_panel_visible(settings.get("panel_edit"), "edit", save=False)
        self._rebuild_presets()
        self._load_sliders()
        self._update_file_label()
        self.chk_skip.setChecked(settings.get("skip"))
        self.chk_tint.setChecked(settings.get("tint"))
        self.chk_sep.setChecked(settings.get("separate"))

    # ------------------------------------------------------------ langue
    def set_language(self, code):
        settings.put("lang", code)
        i18n.set_lang(code)
        self.retranslate()

    def retranslate(self):
        """Remet tous les textes dans la langue choisie, sans redemarrer."""
        for apply in self._tr:
            apply()
        self.table.setHorizontalHeaderLabels([tr(k) for k in ("th.cut", "th.n", "th.start", "th.end", "th.dur")])
        self._fill_track_combos()
        self._rebuild_presets()
        self._update_file_label()
        self._update_summary()
        self.video.label.setText(tr("tint.label"))
        self._play_txt = None
        self.editor.retranslate()
        self.audio.retranslate()
        self._update_pass_ui()
        for k in ("cut", "edit"):
            self.set_panel_visible(self._panels[k], k, save=False)
        self._update_title()
        if not self.media:
            self.status(tr("st.start"))

    def _update_file_label(self):
        m = self.media
        if m:
            self.lbl_file.setText(tr("file.info", name=m["name"], w=m["width"], h=m["height"], fps=f"{self.fps:g}",
                                     dur=fmt(m["duration"]), n=len(m["audio"])))
        else:
            self.lbl_file.setText(tr("file.none"))

    def _fill_track_combos(self, defaults=False):
        if not self.media:
            return
        n = len(self.media["audio"])
        for cb, default in ((self.cb_mix, 0), (self.cb_voice, n - 1), (self.cb_listen, 0)):
            cur = cb.currentIndex()
            cb.blockSignals(True)
            cb.clear()
            for a in self.media["audio"]:
                cb.addItem(tr("track.item", n=a["audio_index"] + 1, c=a["channels"]), a["audio_index"])
            cb.setCurrentIndex(min(default if (defaults or cur < 0) else cur, n - 1))
            cb.blockSignals(False)

    # ------------------------------------------------------------ preregalges
    def _rebuild_presets(self):
        cb = self.cb_preset
        cb.blockSignals(True)
        cb.clear()
        cb.addItem(tr("preset.custom"), None)
        for key, _s in presets.BUILTIN[self.mode]:
            cb.addItem(tr(f"preset.{self.mode}.{key}"), ("b", key))
        for name in sorted(presets.user_presets(self.mode), key=str.lower):
            cb.addItem("★  " + name, ("u", name))
        cb.blockSignals(False)
        self._sync_preset_combo()

    def _preset_values(self, data):
        kind, key = data
        if kind == "b":
            return dict(presets.BUILTIN[self.mode]).get(key)
        return presets.user_presets(self.mode).get(key)

    def _sync_preset_combo(self):
        """Affiche le prereglage qui correspond aux reglages actuels (sinon « Personnalise »)."""
        cur = self.settings[self.mode]
        users = presets.user_presets(self.mode)
        idx = 0
        for i in range(1, self.cb_preset.count()):
            kind, key = self.cb_preset.itemData(i)
            v = dict(presets.BUILTIN[self.mode]).get(key) if kind == "b" else users.get(key)
            if v and presets.same(v, cur):
                idx = i
                break
        self.cb_preset.blockSignals(True)
        self.cb_preset.setCurrentIndex(idx)
        self.cb_preset.blockSignals(False)
        data = self.cb_preset.itemData(idx)
        self.btn_preset_delete.setEnabled(bool(data) and data[0] == "u")

    def _preset_chosen(self, index):
        data = self.cb_preset.itemData(index)
        if not data:
            return
        v = self._preset_values(data)
        if not v:
            return
        self.push_undo()
        self.settings[self.mode] = copy(v)
        self._load_sliders()
        self.redetect()

    def _confirm(self, title, text, yes_label):
        box = QMessageBox(QMessageBox.Question, title, text, QMessageBox.NoButton, self)
        yes = box.addButton(yes_label, QMessageBox.AcceptRole)
        box.addButton(tr("btn.cancel"), QMessageBox.RejectRole)
        box.exec()
        return box.clickedButton() is yes

    def save_preset(self):
        box = QInputDialog(self)
        box.setWindowTitle(tr("preset.name.title"))
        box.setLabelText(tr("preset.name.label"))
        box.setOkButtonText(tr("btn.save"))
        box.setCancelButtonText(tr("btn.cancel"))
        data = self.cb_preset.currentData()
        box.setTextValue(data[1] if data and data[0] == "u" else "")
        if not box.exec():
            return
        name = box.textValue().strip()
        if not name:
            return
        reserved = {tr(f"preset.{self.mode}.{k}").lower() for k, _ in presets.BUILTIN[self.mode]}
        reserved.add(tr("preset.custom").lower())
        if name.lower() in reserved:
            QMessageBox.warning(self, tr("preset.name.title"), tr("preset.builtin"))
            return
        if name in presets.user_presets(self.mode) and not self._confirm(
                tr("preset.name.title"), tr("preset.exists", name=name), tr("btn.replace")):
            return
        presets.save_user(self.mode, name, copy(self.settings[self.mode]))
        self._rebuild_presets()
        self.status(tr("st.preset.saved", name=name))

    def delete_preset(self):
        data = self.cb_preset.currentData()
        if not data or data[0] != "u":
            return
        name = data[1]
        if not self._confirm(tr("btn.preset.delete"), tr("preset.delete.confirm", name=name), tr("btn.delete")):
            return
        presets.delete_user(self.mode, name)
        self._rebuild_presets()
        self.status(tr("st.preset.deleted", name=name))

    def set_tab(self, i):
        """0 = Couper les silences, 1 = Montage, 2 = Son."""
        if (i == 1 and self.media and self.pass_rev is None and self._auto_sent != self.media["path"]
                and self.track_for_mode() in self.envs):
            # 1re visite du montage avec une video analysee : les coupes y sont deja appliquees
            self._auto_sent = self.media["path"]
            if not self.editor.project.clips:
                self.send_to_editor(go=False)
        self.stack.setCurrentIndex(i)
        self.tab_buttons[i].setChecked(True)
        self.btn_open.setVisible(i == 0)
        if i != 0 and self.player.playbackState() == QMediaPlayer.PlayingState:
            self.player.pause()
        if i == 0:                                       # onglet Couper : il a son propre lecteur
            self.editor.preview.pause()
        if i == 2:
            self.audio.refresh()

    def effective_cuts(self, pending=True):
        """Toutes les coupes qui comptent : celles en attente (rouges) + celles deja faites definitivement."""
        return (list(self.cuts) if pending else []) + [Cut(a, b, True) for a, b in self.done]

    def kept_segments(self, pending=True):
        """Passages gardes une fois les coupes actives retirees : liste de (image_debut, image_fin).
        pending=False : seulement les coupes definitives deja faites."""
        m = self.media
        total, fps = m["total_frames"], float(m["fps"])
        segs, pos = [], 0
        for a, b in normalize_cuts(self.effective_cuts(pending), total):
            if a > pos:
                segs.append((pos, a))
            pos = b
        if pos < total:
            segs.append((pos, total))
        return [(a, b) for a, b in segs if (b - a) / fps >= MIN_DUR]

    def send_to_editor(self, go=True):
        """Place les passages gardes (apres les coupes) dans l'onglet Montage : video + toutes les pistes audio."""
        if not self.media:
            return
        if self.pass_rev is not None:                    # passe sur le montage : on retire les silences de la timeline
            return self.apply_pass()
        m = self.media
        segs = self.kept_segments()
        if not segs:
            return
        ed = self.editor
        origin = "cut:" + m["path"]
        others = [c for c in ed.project.clips if c.origin != origin]
        if not others:
            ed._adopt_format({"width": m["width"], "height": m["height"], "fps": float(m["fps"])})
        cursor = max((c.end for c in others if c.track in ("V1", "A1", "A2", "A3")), default=0.0)
        clips = make_cut_clips(m, segs, cursor, self.cb_mix.currentData() or 0, origin)
        ed.add_clips(clips, replace_origin=origin)
        self._auto_sent = m["path"]
        if go:
            self.set_tab(1)
        self.status(tr("st.sent", n=len(segs)))

    # ---- MP4 avec les coupes appliquees (nouveau fichier definitif, la video d'origine reste intacte)
    def do_export_mp4(self):
        if self.mp4_worker is not None and self.mp4_worker.isRunning():
            self.mp4_worker.stop()
            return
        if not self.media:
            return
        segs = self.kept_segments()
        if not segs:
            return
        proj = self._export_dir()
        created = not os.path.isdir(proj)
        os.makedirs(proj, exist_ok=True)
        vbase = os.path.splitext(self.media["name"])[0]
        default = unique_path(proj, vbase + " - sans silences", ".mp4")
        path, _ = QFileDialog.getSaveFileName(self, tr("dlg.mp4.title"), default, "MP4 (*.mp4)")
        if not path:
            if created:
                try:
                    os.rmdir(proj)
                except OSError:
                    pass
            return
        self.mp4_worker = Mp4Worker(self.media, segs, path)
        self.task_begin(tr("st.mp4.progress", pct="0"), cancel=self.mp4_worker.stop)
        self.mp4_worker.progress.connect(lambda p: (self.status(tr("st.mp4.progress", pct=f"{p * 100:.0f}")),
                                                    self.task_update(p, tr("st.mp4.progress", pct=f"{p * 100:.0f}"))))
        self.mp4_worker.done.connect(self._mp4_done)
        self.mp4_worker.failed.connect(self._mp4_failed)
        self.btn_mp4.setText(tr("btn.cancel"))
        self.mp4_worker.start()

    def _mp4_finished(self):
        self.task_end()
        self.btn_mp4.setText(tr("btn.mp4"))

    def _mp4_done(self, path):
        self._mp4_finished()
        self.status(tr("st.export.done", path=path))
        box = QMessageBox(QMessageBox.Information, tr("dlg.done.title"), tr("dlg.mp4.done", path=path),
                          QMessageBox.NoButton, self)
        open_b = box.addButton(tr("btn.openfolder"), QMessageBox.AcceptRole)
        box.addButton(tr("btn.close"), QMessageBox.RejectRole)
        box.exec()
        if box.clickedButton() is open_b and hasattr(os, "startfile"):
            os.startfile(os.path.dirname(path))

    def _mp4_failed(self, msg):
        self._mp4_finished()
        if msg:
            self.status(tr("st.export.fail"))
            QMessageBox.critical(self, tr("dlg.exporterr.title"), msg)

    def set_panel_visible(self, show, key="cut", save=True):
        """Montre ou cache le panneau de droite d'UN onglet (key : "cut" ou "edit"). Chaque onglet a le sien."""
        if getattr(self, "editor", None) is None and key == "edit":
            return
        self._panels[key] = bool(show)
        panel, btn = (self.panel, self.btn_panel) if key == "cut" else (self.editor.panel, self.editor.btn_panel)
        panel.setVisible(self._panels[key])
        btn.setText("▶" if self._panels[key] else "◀")
        btn.setToolTip(tr("tip.panel.hide" if self._panels[key] else "tip.panel.show"))
        if save:
            settings.put("panel_cut" if key == "cut" else "panel_edit", self._panels[key])

    def toggle_panel(self, key=None):
        """Fleche (ou F2) : cache / montre le panneau de l'onglet affiche. L'onglet Son n'en a pas."""
        if key is None:
            key = {0: "cut", 1: "edit"}.get(self.stack.currentIndex())
        if key:
            self.set_panel_visible(not self._panels[key], key)

    def show_welcome(self):
        """Ecran d'accueil : creer un projet (avec son nom), ou rouvrir un projet existant."""
        d = WelcomeDialog(self)
        res = d.exec()
        p = self.editor.project
        if p.path and not os.path.isfile(p.path):         # le projet ouvert vient d'etre supprime depuis l'accueil
            p.path = ""
        if not res or not d.choice:
            return False
        if d.choice[0] == "create":
            return self.create_project(d.choice[1], d.choice[2])
        return self.open_project_file(d.choice[1])

    def create_project(self, name, video=""):
        """Nouveau projet : un dossier a son nom dans le dossier des projets, avec son fichier de projet dedans."""
        root = settings.projects_root()
        if projects.exists(root, name):
            QMessageBox.warning(self, tr("np.title"), tr("np.exists", name=name))
            return False
        ed = self.editor
        ed.new_project()
        p = ed.project
        p.name, p.source, p.path = name, video or "", projects.project_file(root, name)
        try:
            os.makedirs(os.path.dirname(p.path), exist_ok=True)
            p.save(p.path)
        except OSError as e:
            QMessageBox.warning(self, tr("dlg.err.title"), str(e))
            p.path = ""
            return False
        self._update_title()
        if video:
            self.set_tab(0)
            self.load(video)
        else:
            self.set_tab(1)
        self.status(tr("home.created", name=name))
        return True

    def open_project_file(self, path):
        """Rouvre un projet : le montage dans l'onglet Montage, et sa video d'origine dans l'onglet Couper."""
        ed = self.editor
        ed.open_project(path)
        p = ed.project
        if p.path != path:                               # le fichier n'a pas pu etre lu
            return False
        missing = projects.missing_files(path)
        if missing:                                      # on le dit clairement (le projet s'ouvre quand meme)
            names = "\n".join("• " + f for f in missing[:8]) + ("\n…" if len(missing) > 8 else "")
            QMessageBox.warning(self, tr("home.missing.title"), tr("home.missing.open", n=len(missing), names=names))
        if p.source and os.path.isfile(p.source):
            self.load(p.source)
        self._update_title()
        self.set_tab(1 if p.clips else 0)
        return True

    def _update_title(self):
        name = self.editor.project.name or (self.media["name"] if self.media else "")
        self.setWindowTitle(f"Auto Cut - {name}" if name else tr("app.title"))

    def _export_dir(self):
        """Dossier ou vont les exports de l'onglet Couper : celui du projet ouvert, sinon un dossier au nom de la video."""
        if self.editor.project.path:
            return os.path.dirname(self.editor.project.path)
        return project_dir(settings.projects_root(), self.media["name"])

    def show_help(self):
        HelpDialog(self).exec()

    def show_options(self):
        while True:
            d = OptionsDialog(self)
            d.exec()
            if not d.reopen:
                break

    def toggle_fullscreen(self):
        if self.isFullScreen():
            self.showMaximized()
        else:
            self.showFullScreen()

    def _leave_fullscreen(self):
        if self.isFullScreen():
            self.showMaximized()

    def set_theme(self, name):
        theme.apply_theme(QApplication.instance(), name)
        settings.put("theme", name)
        self.wave.update()
        self.overview.update()
        self.editor.timeline.invalidate()
        self.editor.overview.update()

    def set_scale(self, pct):
        settings.put("scale", pct)
        theme.apply_theme(QApplication.instance(), None, pct)

    def apply_pref(self, key, value):
        settings.put(key, value)
        {"skip": self.chk_skip, "tint": self.chk_tint, "separate": self.chk_sep}[key].setChecked(value)

    # ------------------------------------------------------------ utilitaires
    def status(self, msg):
        self.statusBar().showMessage(msg)

    # ------------------------------------------------------------ bandeau d'avancement (visible partout)
    def task_begin(self, text, cancel=None, indeterminate=False):
        self._task_cancel = cancel
        self.task_label.setText(text)
        self.task_progress.setRange(0, 0 if indeterminate else 1000)
        self.task_progress.setValue(0)
        self.btn_task_cancel.setText(tr("btn.cancel"))
        self.btn_task_cancel.setVisible(cancel is not None)
        self.task_bar.show()

    def task_update(self, p=None, text=None):
        if text is not None:
            self.task_label.setText(text)
        if p is not None:
            if self.task_progress.maximum() == 0:
                self.task_progress.setRange(0, 1000)
            self.task_progress.setValue(int(max(0.0, min(1.0, p)) * 1000))

    def task_end(self):
        self.task_bar.hide()
        self._task_cancel = None

    def _task_cancel_clicked(self):
        if self._task_cancel:
            self._task_cancel()

    def dragEnterEvent(self, e):
        if e.mimeData().hasUrls():
            e.acceptProposedAction()

    def dropEvent(self, e):
        for u in e.mimeData().urls():
            if u.isLocalFile():
                self.load(u.toLocalFile())
                break

    @property
    def fps(self):
        return float(self.media["fps"])

    def track_for_mode(self, mode=None):
        cb = self.cb_mix if (mode or self.mode) == "dead" else self.cb_voice
        return cb.currentData()

    # ------------------------------------------------------------ chargement
    def open_dialog(self):
        path, _ = QFileDialog.getOpenFileName(self, tr("dlg.open.title"), "", tr("dlg.open.filter"))
        if path:
            self.load(path)

    def begin_pass(self, path, rev):
        """Ouvre dans l'onglet Couper le fichier leger d'un montage : les coupes qu'on y valide seront retirees de la
        timeline ('Appliquer au montage') au lieu d'etre envoyees comme un nouveau clip."""
        self.set_tab(0)
        self.load(path, pass_rev=rev)

    def _update_pass_ui(self):
        """En mode passe : le bouton d'envoi devient 'Appliquer au montage', les exports du fichier leger sont grises."""
        if getattr(self, "btn_send", None) is None:
            return
        p = self.pass_rev is not None
        self.btn_send.setText(tr("btn.apply" if p else "btn.send"))
        self.btn_send.setToolTip(tr("tip.apply" if p else "tip.send"))
        if p:
            self.btn_export.setEnabled(False)
            self.btn_mp4.setEnabled(False)
        elif self.media:
            self.btn_export.setEnabled(True)
            self.btn_mp4.setEnabled(True)

    def apply_pass(self):
        """'Appliquer au montage' : coupe definitivement tous les passages rouges, puis retour dans le Montage."""
        if self.cut_definitively("all") is not None:
            self.set_tab(1)

    def _tl(self, frame):
        """Instant (s) du montage pour un numero d'image du fichier de la passe : on retire ce qui a deja ete coupe avant."""
        gone = sum(b - a for a, b in self.done if b <= frame)
        return (frame - gone) / self.fps

    def _ensure_in_editor(self):
        """La video n'est pas encore dans le montage : on l'y met en entier (moins ce qui est deja coupe definitivement)."""
        m, ed = self.media, self.editor
        origin = "cut:" + m["path"]
        segs = self.kept_segments(pending=False)
        if not segs:
            return
        others = [c for c in ed.project.clips if c.origin != origin]
        if not others:
            ed._adopt_format({"width": m["width"], "height": m["height"], "fps": float(m["fps"])})
        cursor = max((c.end for c in others if c.track.startswith(("V", "A"))), default=0.0)
        ed.add_clips(make_cut_clips(m, segs, cursor, self.cb_mix.currentData() or 0, origin), replace_origin=origin)
        self._auto_sent = m["path"]

    def cut_definitively(self, which="all"):
        """Coupe DEFINITIVEMENT les passages rouges (le choisi, ou tous) dans le montage : ils sont retires de toutes les
        pistes et la suite est rapprochee. Ils disparaissent de la liste (zone hachuree sur la forme d'onde).
        Renvoie les secondes retirees, ou None si rien n'a ete fait."""
        if not self.media:
            return None
        if which == "one":
            idx = [self.sel] if 0 <= self.sel < len(self.cuts) and self.cuts[self.sel].enabled else []
        else:
            idx = [i for i, c in enumerate(self.cuts) if c.enabled]
        if not idx:
            self.status(tr("st.hard.none"))
            return None
        ed, fps = self.editor, self.fps
        ranges = normalize_cuts([self.cuts[i] for i in idx], self.media["total_frames"])
        if self.pass_rev is not None:                    # fichier de passe : instants du montage
            if ed._rev != self.pass_rev:
                QMessageBox.warning(self, tr("ed.pass.title"), tr("pass.stale"))
                return None
            removed = ed.apply_cut_ranges([(self._tl(a), self._tl(b)) for a, b in ranges])
            self.pass_rev = ed._rev
        else:                                            # video d'origine : on retire ces passages du clip du montage
            if not any(c.path and os.path.abspath(c.path) == os.path.abspath(self.media["path"]) for c in ed.project.clips):
                self._ensure_in_editor()
            removed = ed.cut_source_ranges(self.media["path"], [(a / fps, b / fps) for a, b in ranges])
        gone = set(idx)
        for cuts in self.cuts_by_mode.values():          # la zone n'existe plus, dans les deux modes
            cuts[:] = [c for c in cuts if not (c in [self.cuts[i] for i in gone] or
                                                any(c.end > a and c.start < b for a, b in ranges))]
        self.cuts = self.cuts_by_mode[self.mode]
        self.done += ranges
        self.wave.set_done(self.done)
        for mode in self.undo_stack:                     # une coupe definitive ne s'annule plus depuis cet onglet
            self.undo_stack[mode].clear()
            self.redo_stack[mode].clear()
        self._update_undo_buttons()
        self.sel = -1
        self.refresh_all()
        self.status(tr("st.hard.done", n=len(ranges), sec=f"{removed:.1f}"))
        return removed

    def load(self, path, pass_rev=None):
        try:
            media = probe(path)
        except Exception as e:  # noqa: BLE001
            QMessageBox.critical(self, tr("dlg.err.title"), tr("dlg.err.read", err=e))
            return
        if not media["audio"]:
            QMessageBox.warning(self, tr("dlg.nosound.title"), tr("dlg.nosound"))
            return
        self.player.stop()
        self.pass_rev = pass_rev
        if pass_rev is not None:
            media["name"] = tr("pass.name")
        self.media = media
        self.envs.clear()
        self.cuts_by_mode = {"dead": [], "classic": []}
        self.done = []
        self.wave.set_done([])
        self.cuts, self.sel, self.preview = [], -1, None
        self.undo_stack = {"dead": [], "classic": []}
        self.redo_stack = {"dead": [], "classic": []}
        self._update_undo_buttons()
        self._update_file_label()
        self._fill_track_combos(defaults=True)
        self.wave.env = None
        self.wave.set_data(None, ENV_HOP, media["duration"], media["fps"])
        self.pos_slider.setRange(0, int(media["duration"] * 1000))
        self.player.setSource(QUrl.fromLocalFile(media["path"]))
        self.btn_export.setEnabled(True)
        self.btn_send.setEnabled(True)
        self.btn_mp4.setEnabled(True)
        self._update_pass_ui()
        if pass_rev is None and self.editor.project.path:      # un projet est ouvert : il se souvient de sa video
            self.editor.project.source = media["path"]
        self._update_title()
        self.redetect()

    def _apply_preview_track(self, *_):
        if not self.media:
            return
        tracks = self.player.audioTracks()
        i = self.cb_listen.currentData()
        if i is not None and i < len(tracks):
            self.player.setActiveAudioTrack(i)

    # ------------------------------------------------------------ reglages
    def _load_sliders(self):
        s = self.settings[self.mode]
        self.p_thr.set_value(s.threshold_db)
        self.p_min.set_value(s.min_silence)
        self.p_before.set_value(s.margin_before)
        self.p_after.set_value(s.margin_after)
        self.wave.set_threshold(s.threshold_db)
        self._sync_preset_combo()

    def set_mode(self, mode):
        self.mode = mode
        self._rebuild_presets()
        self._load_sliders()
        self.cuts = self.cuts_by_mode[mode]
        self.sel = -1
        self.wave.env = None
        if self.media:
            self.redetect(keep_manual=bool(self.cuts))

    def _param_changed(self, key, value):
        self.push_undo(coalesce=True)
        setattr(self.settings[self.mode], key, value)
        if key == "threshold_db":
            self.wave.set_threshold(value)
        self._sync_preset_combo()
        self.redetect_timer.start()

    def reset_settings(self):
        self.push_undo()
        self.settings[self.mode] = copy(DEFAULTS[self.mode])
        self._load_sliders()
        self.redetect()

    def _track_changed(self, mode):
        if self.media and mode == self.mode:
            self.redetect()
        elif self.media:
            self.cuts_by_mode[mode] = []

    # ------------------------------------------------------------ detection
    def request_env(self, idx):
        if any(w.idx == idx and w.isRunning() for w in self.workers):
            return
        self.status(tr("st.analysing", n=idx + 1))
        w = EnvWorker(self.media["path"], idx)
        w.done.connect(self._env_ready)
        w.failed.connect(lambda m: self.status(m))
        self.workers.append(w)
        w.start()

    def _env_ready(self, idx, env):
        if not self.media:
            return
        self.envs[idx] = env
        self.status(tr("st.analysed", n=idx + 1))
        if idx == self.track_for_mode():
            self.redetect()

    def redetect(self, keep_manual=False):
        """Refait la detection depuis l'enveloppe en memoire (instantane)."""
        presets.save_last(self.mode, self.settings[self.mode])
        if not self.media:
            return
        idx = self.track_for_mode()
        env = self.envs.get(idx)
        if env is None:
            self.request_env(idx)
            return
        self.wave.set_data(env, ENV_HOP, self.media["duration"], self.media["fps"])
        if not keep_manual:
            self.cuts_by_mode[self.mode] = detect_cuts(env, self.settings[self.mode], self.media["fps"],
                                                       self.media["total_frames"])
        self._drop_done_overlap()
        self.cuts = self.cuts_by_mode[self.mode]
        self.sel = -1
        self.refresh_all()
        if self.pass_rev is not None and not keep_manual:       # passe : on explique ce qu'on voit (ou pourquoi il n'y a rien)
            self.status(tr("st.pass.ready" if self.cuts else "st.pass.zero"))

    def _drop_done_overlap(self):
        """Une zone coupee definitivement n'existe plus : la detection ne la propose plus."""
        if not self.done:
            return
        for mode, cuts in self.cuts_by_mode.items():
            cuts[:] = [c for c in cuts if not any(c.end > a and c.start < b for a, b in self.done)]

    # ------------------------------------------------------------ liste des coupes
    def refresh_all(self):
        self._updating = True
        self.table.blockSignals(True)
        self.table.setRowCount(len(self.cuts))
        for i in range(len(self.cuts)):
            self._fill_row(i)
        self.table.blockSignals(False)
        self._updating = False
        self.wave.set_cuts(self.cuts, self.sel)
        self._update_detail()
        self._update_summary()

    def _fill_row(self, i):
        c = self.cuts[i]
        chk = QTableWidgetItem()
        chk.setFlags(Qt.ItemIsUserCheckable | Qt.ItemIsEnabled | Qt.ItemIsSelectable)
        chk.setCheckState(Qt.Checked if c.enabled else Qt.Unchecked)
        self.table.setItem(i, 0, chk)
        vals = [str(i + 1), fmt(c.start / self.fps), fmt(c.end / self.fps), f"{c.frames / self.fps:.2f} s"]
        for j, v in enumerate(vals, 1):
            it = QTableWidgetItem(v)
            it.setFlags(Qt.ItemIsEnabled | Qt.ItemIsSelectable)
            it.setForeground(QColor(225, 228, 235) if c.enabled else QColor(120, 122, 130))
            self.table.setItem(i, j, it)

    def _set_on(self, btn, on):
        """Etat 'actif' d'un bouton (couleur d'accent) : montre que la situation actuelle est deja celle-la."""
        on = bool(on)
        if btn.property("on") != on:
            btn.setProperty("on", on)
            btn.style().unpolish(btn)
            btn.style().polish(btn)

    def _update_action_states(self):
        if getattr(self, "btn_cutall", None) is None:
            return
        cuts = self.cuts if self.media else []
        self._set_on(self.btn_cutall, bool(cuts) and all(c.enabled for c in cuts))
        self._set_on(self.btn_keepall, bool(cuts) and not any(c.enabled for c in cuts))
        sel = 0 <= self.sel < len(cuts)
        self._set_on(self.btn_cut_one, sel and cuts[self.sel].enabled)
        self._set_on(self.btn_keep_one, sel and not cuts[self.sel].enabled)
        self.btn_hard_one.setEnabled(sel and cuts[self.sel].enabled)
        self.btn_hard_all.setEnabled(any(c.enabled for c in cuts))

    def _update_summary(self):
        self._update_action_states()
        act = [c for c in self.cuts if c.enabled]
        removed = sum(c.frames for c in act) / self.fps if self.media else 0
        total = self.media["duration"] if self.media else 0
        self._rebuild_skip()
        if not self.media:
            self.lbl_sum.setText("-")
            return
        done_s = sum(b - a for a, b in self.done) / self.fps
        txt = tr("summary", n=len(act), total=len(self.cuts), sec=f"{removed:.1f}",
                 pct=f"{100 * removed / max(total, 1):.0f}", final=fmt(total - removed - done_s))
        if self.done:
            txt += "\n" + tr("summary.done", n=len(self.done), sec=f"{done_s:.1f}")
        self.lbl_sum.setText(txt)

    def _rebuild_skip(self):
        act = sorted((c.start / self.fps, c.end / self.fps) for c in self.cuts if c.enabled) if self.media else []
        self._skip = ([a for a, _ in act], [b for _, b in act])

    def _table_selection(self):
        if self._updating:
            return
        rows = self.table.selectionModel().selectedRows()
        self.select_cut(rows[0].row() if rows else -1, seek=True, from_table=True)

    def _table_item_changed(self, item):
        if self._updating or item.column() != 0 or item.row() >= len(self.cuts):
            return
        self.push_undo()
        self.cuts[item.row()].enabled = item.checkState() == Qt.Checked
        self._updating = True
        self.table.blockSignals(True)
        self._fill_row(item.row())
        self.table.blockSignals(False)
        self._updating = False
        self.wave.set_cuts(self.cuts, self.sel)
        self._update_summary()

    def set_selected(self, value):
        """Coupe ou garde SEULEMENT la coupe selectionnee."""
        if not (0 <= self.sel < len(self.cuts)):
            self.status(tr("st.reset.hint"))
            return
        self.push_undo()
        self.cuts[self.sel].enabled = value
        self._cut_edited(self.sel)
        self.status(tr("st.cutone" if value else "st.keepone", n=self.sel + 1))

    def toggle_cut(self, i):
        """Double-clic sur une zone de la forme d'onde : bascule cette coupe."""
        if not (0 <= i < len(self.cuts)):
            return
        self.select_cut(i)
        self.set_selected(not self.cuts[i].enabled)

    def set_all(self, value):
        self.push_undo()
        for c in self.cuts:
            c.enabled = value
        self.refresh_all()
        if 0 <= self.sel < len(self.cuts):
            self._updating = True
            self.table.selectRow(self.sel)
            self._updating = False
        n = len(self.cuts)
        self.status(tr("st.setall.on", n=n) if value else tr("st.setall.off", n=n))

    def select_cut(self, i, seek=False, from_table=False):
        self.sel = i if 0 <= i < len(self.cuts) else -1
        self.wave.set_cuts(self.cuts, self.sel)
        if not from_table and self.sel >= 0:
            self._updating = True
            self.table.selectRow(self.sel)
            self._updating = False
        self._update_detail()
        if self.sel >= 0:
            c = self.cuts[self.sel]
            self.wave.ensure_visible(c.start / self.fps - 1, c.end / self.fps + 1)
            if seek:
                self.preview = None
                self.player.setPosition(int(max(0, c.start / self.fps - 1.0) * 1000))

    def _update_detail(self):
        self._update_action_states()
        on = 0 <= self.sel < len(self.cuts)
        for sp in (self.sp_start, self.sp_end):
            sp.setEnabled(on)
        self.btn_prev_cut.setEnabled(on)
        self.btn_prev_gap.setEnabled(on)
        self.btn_cut_one.setEnabled(on)
        self.btn_keep_one.setEnabled(on)
        if on:
            c = self.cuts[self.sel]
            self._updating = True
            self.sp_start.setValue(c.start / self.fps)
            self.sp_end.setValue(c.end / self.fps)
            self._updating = False

    def _spin_edited(self, which, v):
        if self._updating or not (0 <= self.sel < len(self.cuts)):
            return
        c = self.cuts[self.sel]
        f = int(round(v * self.fps))
        self.push_undo(coalesce=True)
        if which == "start":
            c.start = max(0, min(f, c.end - 1))
        else:
            c.end = min(self.media["total_frames"], max(f, c.start + 1))
        self._cut_edited(self.sel)
        self._update_detail()

    def _cut_edited_from_wave(self, i):
        self._cut_edited(i)
        self._update_detail()

    def _cut_edited(self, i):
        self._updating = True
        self.table.blockSignals(True)
        self._fill_row(i)
        self.table.blockSignals(False)
        self._updating = False
        self.wave.set_cuts(self.cuts, self.sel)
        self._update_summary()

    # ------------------------------------------------------------ annuler / retablir
    def _snapshot(self):
        return {"settings": copy(self.settings[self.mode]), "sel": self.sel,
                "cuts": [(c.start, c.end, c.enabled, c.orig_start, c.orig_end, c.cid) for c in self.cuts]}

    def push_undo(self, coalesce=False):
        """Memorise l'etat actuel avant une modification. coalesce : une rafale de petits changements
        (curseur, fleches d'une case) ne compte que pour une seule etape."""
        if not self.media:
            return
        if coalesce:
            burst = self._coalesce.isActive()
            self._coalesce.start()
            if burst:
                return
        st = self.undo_stack[self.mode]
        st.append(self._snapshot())
        del st[:-200]
        self.redo_stack[self.mode].clear()
        self._update_undo_buttons()

    def _restore(self, snap):
        self.redetect_timer.stop()
        self._coalesce.stop()
        self.settings[self.mode] = copy(snap["settings"])
        self._load_sliders()
        cuts = []
        for (a, b, en, oa, ob, cid) in snap["cuts"]:
            c = Cut(a, b, en, cid)
            c.orig_start, c.orig_end = oa, ob
            cuts.append(c)
        self.cuts_by_mode[self.mode] = cuts
        self.cuts = cuts
        self.sel = snap["sel"] if snap["sel"] < len(cuts) else -1
        self.refresh_all()
        if self.sel >= 0:
            self._updating = True
            self.table.selectRow(self.sel)
            self._updating = False

    def undo(self):
        if self.stack.currentIndex() != 0:
            self.editor.undo()
            return
        st = self.undo_stack[self.mode]
        if not (self.media and st):
            return
        self.redo_stack[self.mode].append(self._snapshot())
        self._restore(st.pop())
        self._update_undo_buttons()
        self.status(tr("st.undo"))

    def redo(self):
        if self.stack.currentIndex() != 0:
            self.editor.redo()
            return
        st = self.redo_stack[self.mode]
        if not (self.media and st):
            return
        self.undo_stack[self.mode].append(self._snapshot())
        self._restore(st.pop())
        self._update_undo_buttons()
        self.status(tr("st.redo"))

    def _update_undo_buttons(self):
        self.btn_undo.setEnabled(bool(self.undo_stack[self.mode]))
        self.btn_redo.setEnabled(bool(self.redo_stack[self.mode]))

    # ------------------------------------------------------------ reinitialiser les coupes
    def reset_selected(self):
        if not (0 <= self.sel < len(self.cuts)):
            self.status(tr("st.reset.hint"))
            return
        self.push_undo()
        c = self.cuts[self.sel]
        c.start, c.end = c.orig_start, c.orig_end
        self._cut_edited(self.sel)
        self._update_detail()
        self.status(tr("st.reset1"))

    def reset_all_cuts(self):
        if not self.cuts:
            return
        self.push_undo()
        for c in self.cuts:
            c.start, c.end = c.orig_start, c.orig_end
        sel = self.sel
        self.refresh_all()
        if 0 <= sel < len(self.cuts):
            self._updating = True
            self.table.selectRow(sel)
            self._updating = False
        self.status(tr("st.resetall"))

    # ------------------------------------------------------------ navigation dans la forme d'onde
    def goto_cut(self, delta):
        if not self.cuts:
            return
        if self.sel >= 0:
            i = self.sel + delta
        else:
            pos = self.player.position() / 1000.0
            starts = [c.start / self.fps for c in self.cuts]
            i = bisect_right(starts, pos) if delta > 0 else bisect_right(starts, pos) - 1
        i = max(0, min(len(self.cuts) - 1, i))
        self.select_cut(i, seek=True)

    def zoom_on_cut(self):
        if 0 <= self.sel < len(self.cuts):
            c = self.cuts[self.sel]
            self.wave.push_view()
            self.wave.show_range(c.start / self.fps, c.end / self.fps)

    def show_all_view(self):
        self.wave.push_view()
        self.wave.show_all()

    def zoom_back(self):
        if not self.wave.back_view():
            self.status(tr("st.zoomback.none"))

    def _update_zoom_back(self):
        b = getattr(self, "btn_zoom_back", None)
        if b is not None:
            b.setEnabled(bool(self.wave.history))

    def _update_tint(self, pos):
        on = False
        if self.chk_tint.isChecked() and self.media and self._skip[0]:
            starts, ends = self._skip
            i = bisect_right(starts, pos) - 1
            on = i >= 0 and pos < ends[i]
        self.video.set_tinted(on)

    # ------------------------------------------------------------ lecture
    def toggle_play(self):
        if self.stack.currentIndex() != 0:
            self.editor.toggle_play()
            return
        if not self.media:
            return
        if self.player.playbackState() == QMediaPlayer.PlayingState:
            self.player.pause()
            self.preview = None
        else:
            self.player.play()

    def seek_seconds(self, t):
        self.preview = None
        self.player.setPosition(int(t * 1000))
        self.wave.set_playhead(t)

    def preview_with_cut(self):
        if not (0 <= self.sel < len(self.cuts)):
            return
        c = self.cuts[self.sel]
        a, b = c.start / self.fps, c.end / self.fps
        self.preview = {"from": a, "to": b, "jumped": False, "stop": b + 1.5}
        self.player.setPosition(int(max(0, a - 1.5) * 1000))
        self.player.play()

    def preview_removed(self):
        if not (0 <= self.sel < len(self.cuts)):
            return
        c = self.cuts[self.sel]
        a, b = c.start / self.fps, c.end / self.fps
        self.preview = {"from": None, "to": None, "jumped": True, "stop": b}
        self.player.setPosition(int(a * 1000))
        self.player.play()

    def _tick(self):
        if not self.media:
            return
        pos = self.player.position() / 1000.0
        playing = self.player.playbackState() == QMediaPlayer.PlayingState
        self.wave.set_playhead(pos, follow=playing)
        if not self.pos_slider.isSliderDown():
            self.pos_slider.setValue(int(pos * 1000))
        self.lbl_time.setText(f"{fmt(pos)} / {fmt(self.media['duration'])}")
        self._update_tint(pos)
        txt = tr("btn.pause") if playing else tr("btn.play")
        if txt != self._play_txt:
            self._play_txt = txt
            self.btn_play.setText(txt)
        if not playing:
            return
        pv = self.preview
        if pv:
            if not pv["jumped"] and pos >= pv["from"]:
                pv["jumped"] = True
                self.player.setPosition(int(pv["to"] * 1000))
            elif pos >= pv["stop"]:
                self.player.pause()
                self.preview = None
            return
        if self.chk_skip.isChecked() and self._skip[0]:
            starts, ends = self._skip
            i = bisect_right(starts, pos) - 1
            if i >= 0 and pos < ends[i] - 0.03:
                self.player.setPosition(int(ends[i] * 1000))

    # ------------------------------------------------------------ export
    def do_export(self):
        if not self.media:
            return
        if not any(c.enabled for c in self.cuts):
            if not self._confirm(tr("dlg.nocut.title"), tr("dlg.nocut"), tr("btn.continue")):
                return
        mode = "mark" if self.rb_mark.isChecked() else "remove"
        # un dossier par projet (celui du projet ouvert, sinon <dossier des projets>/<nom de la video>/), XML dedans
        proj = self._export_dir()
        created = not os.path.isdir(proj)
        os.makedirs(proj, exist_ok=True)
        vbase = os.path.splitext(self.media["name"])[0]
        default = unique_path(proj, vbase + (" - coupes" if mode == "mark" else " - sans silences"))
        path, _ = QFileDialog.getSaveFileName(self, tr("dlg.save.title"), default, "XML (*.xml)")
        if not path:
            if created:
                try:
                    os.rmdir(proj)          # on ne laisse pas de dossier vide si on annule
                except OSError:
                    pass
            return
        self.btn_export.setEnabled(False)
        w = ExportWorker(self.media, self.effective_cuts(), mode, path, self.chk_sep.isChecked())
        self.task_begin(tr("task.xml"), indeterminate=True)
        w.progress.connect(lambda m: (self.status(m), self.task_update(text=m)))
        w.done.connect(self._export_done)
        w.failed.connect(self._export_failed)
        self.workers.append(w)
        w.start()

    def _export_done(self, path):
        self.task_end()
        self.btn_export.setEnabled(True)
        self.status(tr("st.export.done", path=path))
        box = QMessageBox(QMessageBox.Information, tr("dlg.done.title"), tr("dlg.done", path=path),
                          QMessageBox.NoButton, self)
        open_btn = box.addButton(tr("btn.openfolder"), QMessageBox.AcceptRole)
        box.addButton(tr("btn.close"), QMessageBox.RejectRole)
        box.exec()
        if box.clickedButton() is open_btn and hasattr(os, "startfile"):
            os.startfile(os.path.dirname(path))

    def _export_failed(self, msg):
        self.task_end()
        self.btn_export.setEnabled(True)
        self.status(tr("st.export.fail"))
        QMessageBox.critical(self, tr("dlg.exporterr.title"), msg)

    def closeEvent(self, e):
        # garde-fou : si un lecteur video bloque a la fermeture (rare, bug de Qt), on force l'arret apres 6 s
        import threading
        guard = threading.Timer(6.0, lambda: os._exit(0))
        guard.daemon = True
        guard.start()
        self.player.stop()
        if self.mp4_worker is not None and self.mp4_worker.isRunning():
            self.mp4_worker.stop()
            self.mp4_worker.wait(3000)
        self.editor.shutdown()
        for w in self.workers:
            w.wait(2000)
        super().closeEvent(e)
