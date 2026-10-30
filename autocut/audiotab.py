"""Onglet Son : une table de mixage. Une tranche par piste de la timeline (fader, muet, solo), un master, et la
normalisation du son final (norme YouTube : -14 LUFS, -1 dBTP) mesuree puis appliquee a l'export.
Les tranches suivent les pistes du montage : une piste qui n'existe plus n'a plus de tranche."""
import math
import time
from copy import deepcopy

from PySide6.QtCore import QRectF, Qt, QThread, Signal
from PySide6.QtGui import QColor, QPainter, QPen
from PySide6.QtWidgets import (
    QCheckBox, QDoubleSpinBox, QFrame, QHBoxLayout, QLabel, QProgressBar, QPushButton, QScrollArea, QSlider,
    QVBoxLayout, QWidget,
)

from . import render
from .i18n import tr
from .mediacache import PEAK_RATE, get_cache
from .project import src_at

DB_MIN, DB_MAX = -60.0, 12.0
STRIP_W, MASTER_W = 100, 124
FADER_H = 200
YT_LUFS, YT_TP = -14.0, -1.0


class Fader(QSlider):
    """Fader vertical en dB (pas de 0,5 dB). Double-clic = retour a 0 dB."""

    def __init__(self):
        super().__init__(Qt.Vertical)
        self.setRange(int(DB_MIN * 2), int(DB_MAX * 2))
        self.setMinimumHeight(FADER_H)
        self.setTickPosition(QSlider.TicksRight)
        self.setTickInterval(12)                       # un repere tous les 6 dB

    def mouseDoubleClickEvent(self, e):
        self.setValue(0)
        e.accept()


METER_MIN = -60.0                     # bas de l'echelle du vu-metre (dB)


def to_db(amp):
    return 20.0 * math.log10(amp) if amp > 1e-6 else -120.0


def db_text(db):
    return "−∞" if db <= METER_MIN else f"{db:+.1f} dB"


class LevelMeter(QWidget):
    """Vu-metre vertical (pic en dB) : vert, puis jaune vers -18 dB, rouge vers -6 dB. Le trait blanc garde le pic
    recent. Il monte tout de suite et redescend doucement, comme sur une table de mixage."""

    def __init__(self):
        super().__init__()
        self.setFixedWidth(16)
        self.setMinimumHeight(FADER_H)
        self.db = self.hold = -120.0
        self._hold_t = 0.0

    def push(self, db, smooth=True):
        now = time.perf_counter()
        if smooth:
            self.db = db if db >= self.db else max(db, self.db - 1.4)          # descente ~ 45 dB/s (tick de 30 ms)
            if db >= self.hold:
                self.hold, self._hold_t = db, now
            elif now - self._hold_t > 1.2:
                self.hold = max(db, self.hold - 2.0)
        else:
            self.db = self.hold = db
            self._hold_t = now
        self.update()

    def _y(self, db, h):
        return h - max(0.0, min(1.0, (db - METER_MIN) / -METER_MIN)) * h

    def paintEvent(self, _):
        p = QPainter(self)
        w, h = self.width(), self.height()
        p.fillRect(0, 0, w, h, QColor(0, 0, 0, 90))
        top = self._y(self.db, h)
        for lo, hi, colr in ((METER_MIN, -18.0, "#3fbf5f"), (-18.0, -6.0, "#e6c229"), (-6.0, 6.0, "#e0463c")):
            y0, y1 = self._y(min(hi, 0.0), h), self._y(lo, h)
            if y1 > top:                                   # partie allumee de cette zone
                p.fillRect(QRectF(2, max(y0, top), w - 4, y1 - max(y0, top)), QColor(colr))
        if self.hold > METER_MIN:
            p.setPen(QPen(QColor("#ffffff"), 2))
            y = self._y(self.hold, h)
            p.drawLine(2, int(y), w - 2, int(y))
        p.setPen(QPen(QColor(255, 255, 255, 110), 1))
        for db in (0, -6, -12, -24, -36, -48):             # reperes
            y = int(self._y(db, h))
            p.drawLine(0, y, 3, y)
            p.drawLine(w - 3, y, w, y)
        if self.db > 0.0:                                  # ecretage : le haut devient rouge vif
            p.fillRect(QRectF(0, 0, w, 3), QColor("#ff2b2b"))


class MeasureWorker(QThread):
    """Mesure le volume du son final du montage, sans bloquer la fenetre."""
    progress = Signal(float)
    done = Signal(object)
    failed = Signal(str)

    def __init__(self, project):
        super().__init__()
        self.project = deepcopy(project)
        self._stop = False

    def stop(self):
        self._stop = True

    def run(self):
        try:
            p = self.project
            self.done.emit(render.measure_loudness(p, p.norm_lufs, p.norm_tp, progress=self.progress.emit,
                                                   should_stop=lambda: self._stop))
        except InterruptedError:
            self.failed.emit("")
        except Exception as e:  # noqa: BLE001
            self.failed.emit(str(e))


class AudioPage(QWidget):
    def __init__(self, win, editor):
        super().__init__()
        self.win, self.editor = win, editor
        self._updating = False
        self.rows = {}
        self._sig = None
        self.worker = None
        self._measured = None
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.NoFrame)
        outer.addWidget(scroll)
        host = QWidget()
        scroll.setWidget(host)
        v = QVBoxLayout(host)
        v.setContentsMargins(24, 20, 24, 20)
        v.setSpacing(12)
        self.title = QLabel()
        self.title.setObjectName("helptitle")
        self.note = QLabel()
        self.note.setObjectName("muted")
        self.note.setWordWrap(True)
        self.empty = QLabel()
        self.empty.setObjectName("muted")
        self.empty.setWordWrap(True)
        top = QHBoxLayout()
        top.addWidget(self.title)
        top.addStretch(1)
        self.btn_play = QPushButton()
        self.btn_play.setFixedWidth(100)
        self.btn_play.clicked.connect(self.editor.toggle_play)
        self.lbl_time = QLabel("0:00.00")
        self.lbl_time.setMinimumWidth(70)
        top.addWidget(self.btn_play)
        top.addWidget(self.lbl_time)
        v.addLayout(top)
        self.editor.preview.stateChanged.connect(lambda _p: self._update_play())
        self.editor.preview.timeChanged.connect(self._on_time)
        v.addWidget(self.note)
        v.addWidget(self.empty)

        # ---- la table : tranches des pistes (defilement si elles sont nombreuses) + master toujours visible
        mix = QHBoxLayout()
        mix.setSpacing(14)
        self.strips_scroll = QScrollArea()
        self.strips_scroll.setWidgetResizable(True)
        self.strips_scroll.setFrameShape(QScrollArea.NoFrame)
        self.strips_scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.strips_scroll.setFixedHeight(FADER_H + 190)
        self.strips_host = QWidget()
        self.strips_lay = QHBoxLayout(self.strips_host)
        self.strips_lay.setContentsMargins(0, 0, 0, 0)
        self.strips_lay.setSpacing(10)
        self.strips_scroll.setWidget(self.strips_host)
        mix.addWidget(self.strips_scroll, 1)
        self.master_box = QHBoxLayout()
        mix.addLayout(self.master_box)
        v.addLayout(mix)

        # ---- normalisation du son final
        card = QFrame()
        card.setObjectName("card")
        cl = QVBoxLayout(card)
        cl.setContentsMargins(18, 14, 18, 14)
        cl.setSpacing(8)
        self.loud_title = QLabel()
        self.loud_title.setObjectName("cardtitle")
        self.chk_norm = QCheckBox()
        row = QHBoxLayout()
        row.setSpacing(10)
        self.lbl_target = QLabel()
        self.sp_lufs = QDoubleSpinBox()
        self.sp_lufs.setRange(-40.0, -5.0)
        self.sp_lufs.setSingleStep(0.5)
        self.sp_lufs.setDecimals(1)
        self.sp_lufs.setSuffix(" LUFS")
        self.lbl_tp = QLabel()
        self.sp_tp = QDoubleSpinBox()
        self.sp_tp.setRange(-9.0, 0.0)
        self.sp_tp.setSingleStep(0.1)
        self.sp_tp.setDecimals(1)
        self.sp_tp.setSuffix(" dBTP")
        for w_ in (self.sp_lufs, self.sp_tp):
            w_.setKeyboardTracking(False)
        self.btn_yt = QPushButton()
        self.btn_measure = QPushButton()
        row.addWidget(self.lbl_target)
        row.addWidget(self.sp_lufs)
        row.addWidget(self.lbl_tp)
        row.addWidget(self.sp_tp)
        row.addWidget(self.btn_yt)
        row.addStretch(1)
        row.addWidget(self.btn_measure)
        self.bar = QProgressBar()
        self.bar.setRange(0, 1000)
        self.bar.setTextVisible(False)
        self.bar.setFixedHeight(8)
        self.bar.hide()
        self.lbl_before = QLabel()
        self.lbl_gain = QLabel()
        self.lbl_after = QLabel()
        for l_ in (self.lbl_before, self.lbl_gain, self.lbl_after):
            l_.setWordWrap(True)
        self.loud_note = QLabel()
        self.loud_note.setObjectName("muted")
        self.loud_note.setWordWrap(True)
        for w_ in (self.loud_title, self.chk_norm):
            cl.addWidget(w_)
        cl.addLayout(row)
        cl.addWidget(self.bar)
        cl.addWidget(self.lbl_before)
        cl.addWidget(self.lbl_gain)
        cl.addWidget(self.lbl_after)
        cl.addWidget(self.loud_note)
        v.addWidget(card)
        v.addStretch(1)

        self.chk_norm.toggled.connect(lambda on: self._set_norm("normalize", bool(on)))
        self.sp_lufs.valueChanged.connect(lambda x: self._set_norm("norm_lufs", float(x)))
        self.sp_tp.valueChanged.connect(lambda x: self._set_norm("norm_tp", float(x)))
        self.btn_yt.clicked.connect(self._youtube)
        self.btn_measure.clicked.connect(self.measure)
        self.retranslate()

    # ------------------------------------------------------------ tranches (une par piste du montage + master)
    def _names(self):
        p = self.editor.project
        # Les sons sont sur les pistes audio. Une piste video n'a sa tranche que si elle porte du son.
        keep = [t for t in p.tracks() if t.startswith("A") or any(c.track == t and c.has_sound() for c in p.clips)]
        return keep + ["M"]                           # meme ordre que la timeline, de haut en bas

    def _make_strip(self, t):
        master = t == "M"
        frame = QFrame()
        frame.setObjectName("card")
        frame.setFixedWidth(MASTER_W if master else STRIP_W)
        lay = QVBoxLayout(frame)
        lay.setContentsMargins(8, 10, 8, 10)
        lay.setSpacing(6)
        name = QLabel()
        name.setAlignment(Qt.AlignCenter)
        name.setObjectName("cardtitle" if master else "")
        count = QLabel()
        count.setObjectName("muted")
        count.setAlignment(Qt.AlignCenter)
        lay.addWidget(name)
        lay.addWidget(count)
        mute = QPushButton("M")
        solo = QPushButton("S")
        btns = QHBoxLayout()
        btns.setSpacing(4)
        for b in (mute, solo):
            b.setObjectName("tool")
            b.setCheckable(True)
            b.setFixedSize(34, 26)
            b.setFocusPolicy(Qt.NoFocus)
            btns.addWidget(b)
        if not master:
            lay.addLayout(btns)
        else:
            mute.hide()
            solo.hide()
            lay.addSpacing(30)
        slider = Fader()
        meter = LevelMeter()
        meter.setToolTip(tr("au.meter"))
        fr = QHBoxLayout()
        fr.setSpacing(6)
        fr.addStretch(1)
        fr.addWidget(slider)
        fr.addWidget(meter)
        fr.addStretch(1)
        lay.addLayout(fr, 1)
        peak = QLabel(db_text(-120.0))
        peak.setAlignment(Qt.AlignCenter)
        peak.setObjectName("muted")
        peak.setToolTip(tr("au.meter"))
        lay.addWidget(peak)
        spin = QDoubleSpinBox()
        spin.setRange(DB_MIN, DB_MAX)
        spin.setSingleStep(0.5)
        spin.setDecimals(1)
        spin.setSuffix(" dB")
        spin.setKeyboardTracking(False)
        spin.setAlignment(Qt.AlignCenter)
        lay.addWidget(spin)
        self.rows[t] = dict(frame=frame, name=name, count=count, mute=mute, solo=solo, slider=slider, spin=spin,
                            meter=meter, peak=peak)
        slider.valueChanged.connect(lambda val, tk=t: self._set_gain(tk, val / 2.0, from_slider=True))
        spin.valueChanged.connect(lambda val, tk=t: self._set_gain(tk, val))
        mute.toggled.connect(lambda on, tk=t: self._set_mute(tk, on))
        solo.toggled.connect(lambda on, tk=t: self._set_solo(tk, on))
        return frame

    def _rebuild(self, names):
        for lay in (self.strips_lay, self.master_box):
            while lay.count():
                it = lay.takeAt(0)
                w_ = it.widget()
                if w_:
                    w_.hide()
                    w_.setParent(None)                 # disparait tout de suite (deleteLater seul laisse un fantome)
                    w_.deleteLater()
        self.rows = {}
        for t in names:
            frame = self._make_strip(t)
            (self.master_box if t == "M" else self.strips_lay).addWidget(frame)
        self.strips_lay.addStretch(1)
        self._sig = tuple(names)
        self._texts()

    # ------------------------------------------------------------ textes
    def _texts(self):
        for t, r in self.rows.items():
            if t == "M":
                r["name"].setText(tr("au.master"))
            else:
                r["name"].setText(tr("au.video" if t.startswith("V") else "au.audio", n=t[1:]))
                r["mute"].setToolTip(tr("au.mute"))
                r["solo"].setToolTip(tr("tl.solo"))
            r["slider"].setToolTip(tr("au.fader"))
            r["meter"].setToolTip(tr("au.meter"))
            r["peak"].setToolTip(tr("au.meter"))

    def retranslate(self):
        self.title.setText(tr("au.title"))
        self.note.setText(tr("au.note"))
        self.empty.setText(tr("au.empty"))
        self.loud_title.setText(tr("au.loud.title"))
        self.chk_norm.setText(tr("au.norm"))
        self.lbl_target.setText(tr("au.target"))
        self.lbl_tp.setText(tr("au.tp"))
        self.btn_yt.setText(tr("au.yt"))
        self.btn_measure.setText(tr("au.measure"))
        self.loud_note.setText(tr("au.normnote"))
        self._texts()
        self._update_play()
        self._show_measure()
        self.refresh()

    # ------------------------------------------------------------ lecture / ecriture du projet
    def refresh(self):
        p = self.editor.project
        names = self._names()
        if tuple(names) != self._sig:
            self._updating = True
            self._rebuild(names)
            self._updating = False
        self._updating = True
        for t, r in self.rows.items():
            db = p.master_db if t == "M" else p.track_gain.get(t, 0.0)
            r["slider"].setValue(int(round(db * 2)))
            r["spin"].setValue(db)
            if t != "M":
                r["mute"].setChecked(bool(p.track_mute.get(t, False)))
                r["solo"].setChecked(bool(p.track_solo.get(t, False)))
                n = sum(1 for c in p.clips if c.track == t and c.has_sound())
                r["count"].setText(tr("au.clips", n=n))
        self.empty.setVisible(not any(c.has_sound() for c in p.clips))
        self.update_meters(force=True)
        self.chk_norm.setChecked(p.normalize)
        self.sp_lufs.setValue(p.norm_lufs)
        self.sp_tp.setValue(p.norm_tp)
        for w_ in (self.sp_lufs, self.sp_tp, self.btn_yt):
            w_.setEnabled(True)
        self._updating = False

    # ------------------------------------------------------------ lecture et vu-metres
    def _update_play(self):
        self.btn_play.setText(tr("btn.pause") if self.editor.preview.playing else tr("btn.play"))

    def _on_time(self, t):
        if not self.isVisible():
            return
        self.lbl_time.setText(f"{int(t // 60)}:{t % 60:05.2f}")
        self.update_meters()

    def update_meters(self, force=False):
        """Niveau (pic, en dB) de chaque piste a la position de lecture : volume du clip x fader de la piste.
        Une piste muette, ou ecartee par un solo, reste a zero. Master : somme des pistes (estimation)."""
        if not self.isVisible() and not force:
            return
        ed = self.editor
        p, t, playing = ed.project, ed.preview.t, ed.preview.playing
        cache = get_cache()
        total = 0.0
        for tk, r in self.rows.items():
            if tk == "M":
                continue
            amp = 0.0
            if p.track_active(tk):
                lin = 10 ** ((p.track_gain.get(tk, 0.0)) / 20.0)
                for c in p.clips:
                    if c.track != tk or not c.has_sound() or not (c.start <= t < c.end):
                        continue
                    pk = cache.peaks(c.path, c.audio_index)
                    if pk is None:
                        continue
                    i = int(src_at(c, t) * PEAK_RATE)
                    seg = pk[max(0, i - 1): i + 2]
                    if len(seg):
                        amp = max(amp, float(seg.max()) * 10 ** (c.volume_db / 20.0) * lin)
            total += amp * amp
            db = to_db(amp)
            r["meter"].push(db, smooth=playing)
            r["peak"].setText(db_text(r["meter"].hold))
        if "M" in self.rows:
            db = to_db(math.sqrt(total) * 10 ** (p.master_db / 20.0))
            self.rows["M"]["meter"].push(db, smooth=playing)
            self.rows["M"]["peak"].setText(db_text(self.rows["M"]["meter"].hold))

    def _set_gain(self, track, db, from_slider=False):
        if self._updating:
            return
        db = max(DB_MIN, min(DB_MAX, db))
        ed = self.editor
        ed.push_undo(coalesce=True)
        if track == "M":
            ed.project.master_db = db
        else:
            ed.project.track_gain[track] = db
        self._updating = True
        r = self.rows[track]
        if from_slider:
            r["spin"].setValue(db)
        else:
            r["slider"].setValue(int(round(db * 2)))
        self._updating = False
        ed.preview.refresh()

    def _set_mute(self, track, on):
        if self._updating:
            return
        self.editor.push_undo()
        self.editor.project.track_mute[track] = bool(on)
        self.editor.preview.refresh()
        self.editor.timeline.invalidate()

    def _set_solo(self, track, on):
        if self._updating:
            return
        self.editor.push_undo()
        self.editor.project.track_solo[track] = bool(on)
        self.editor.preview.refresh()
        self.editor.timeline.invalidate()

    # ------------------------------------------------------------ normalisation du son final
    def _set_norm(self, attr, value):
        if self._updating:
            return
        self.editor.push_undo(coalesce=True)
        setattr(self.editor.project, attr, value)
        if attr != "normalize":
            self._measured = None                     # la cible a change : la mesure d'avant ne vaut plus pour le gain
            self._show_measure()

    def _youtube(self):
        self.sp_lufs.setValue(YT_LUFS)
        self.sp_tp.setValue(YT_TP)

    def measure(self):
        if self.worker is not None and self.worker.isRunning():
            self.worker.stop()
            return
        p = self.editor.project
        if not any(c.has_sound() and p.gain_db(c) is not None for c in p.clips):
            self._measured = {"silent": True}
            self._show_measure()
            return
        self.worker = MeasureWorker(p)
        self.worker.progress.connect(lambda x: self.bar.setValue(int(x * 1000)))
        self.worker.done.connect(self._measure_done)
        self.worker.failed.connect(self._measure_failed)
        self.bar.setValue(0)
        self.bar.show()
        self.btn_measure.setText(tr("btn.cancel"))
        self.worker.start()

    def _measure_done(self, m):
        self.bar.hide()
        self.btn_measure.setText(tr("au.measure"))
        self._measured = m or {"silent": True}
        self._show_measure()

    def _measure_failed(self, msg):
        self.bar.hide()
        self.btn_measure.setText(tr("au.measure"))
        if msg:
            self.lbl_before.setText(tr("au.err", err=msg[-200:]))

    def _show_measure(self):
        m, p = self._measured, self.editor.project
        self.lbl_gain.setText("")
        if m is None:
            self.lbl_before.setText(tr("au.nomeasure"))
            return
        if m.get("silent"):
            self.lbl_before.setText(tr("au.silent"))
            return
        self.lbl_before.setText(tr("au.before", i=f"{m['I']:.1f}", tp=f"{m['TP']:.1f}", lra=f"{m['LRA']:.1f}"))
        gain = p.norm_lufs - m["I"]
        text = tr("au.gain", g=f"{gain:+.1f}", t=f"{p.norm_lufs:.1f}")
        if m["TP"] + gain > p.norm_tp - render.TP_MARGIN + 0.05:
            text += "  " + tr("au.limited", tp=f"{p.norm_tp:.1f}")
        self.lbl_gain.setText(text)

    def show_result(self, info):
        """Apres un export avec normalisation : volume avant / apres du fichier cree."""
        b = info.get("after")
        self.lbl_after.setText(tr("au.after", i=f"{b['I']:.1f}", tp=f"{b['TP']:.1f}") if b else "")
