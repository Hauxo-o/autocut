"""Onglet Montage : un mini logiciel de montage (videos, images, textes, sons) avec apercu et export MP4."""
import json
import os
import subprocess
import time
from copy import deepcopy

from PySide6.QtCore import QEvent, QObject, QSizeF, Qt, QThread, QTimer, QUrl, Signal
from PySide6.QtGui import QBrush, QColor, QFont, QKeySequence, QPen, QPixmap, QShortcut
from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer
from PySide6.QtMultimediaWidgets import QGraphicsVideoItem
from PySide6.QtWidgets import (
    QButtonGroup, QColorDialog, QGraphicsItem, QInputDialog, QLineEdit, QListWidget, QListWidgetItem, QComboBox, QDoubleSpinBox, QFileDialog, QFormLayout, QFrame, QGraphicsPixmapItem, QGraphicsRectItem,
    QGraphicsScene, QGraphicsSimpleTextItem, QGraphicsView, QGroupBox, QHBoxLayout, QLabel, QMessageBox,
    QPlainTextEdit, QProgressBar, QPushButton, QScrollArea, QScrollBar, QSpinBox, QSplitter, QVBoxLayout, QWidget,
    QCheckBox,
)

from . import aitools, ops, projects, render, settings, transcribe
from .export import project_dir, unique_path
from .ffmpeg_tools import _NOWIN, find_tool
from .i18n import tr
from .project import (FONTS, FPS_CHOICES, MIN_DUR, RESOLUTIONS, Clip, Project, seg_index_at, seg_window, src_at,
                      track_num)
from .timeline import ProjectOverview, TimelineWidget
from .xml_export import build_project_xml

VIDEO_EXT = {".mp4", ".mkv", ".mov", ".avi", ".webm", ".flv", ".m4v", ".wmv"}
IMAGE_EXT = {".png", ".jpg", ".jpeg", ".bmp", ".webp", ".gif"}
AUDIO_EXT = {".wav", ".mp3", ".m4a", ".aac", ".flac", ".ogg", ".wma", ".opus"}


def kind_from_path(path):
    ext = os.path.splitext(path)[1].lower()
    return "video" if ext in VIDEO_EXT else "image" if ext in IMAGE_EXT else "audio" if ext in AUDIO_EXT else None


def probe_media(path):
    """Infos d'un fichier media (duree, a-t-il du son...). Leve ValueError si illisible."""
    out = subprocess.run([find_tool("ffprobe"), "-v", "error", "-show_streams", "-show_format", "-of", "json", path],
                         capture_output=True, text=True, encoding="utf-8", errors="replace", creationflags=_NOWIN)
    if out.returncode != 0:
        raise ValueError(out.stderr.strip() or "ffprobe")
    info = json.loads(out.stdout)
    streams = info.get("streams", [])
    v = next((s for s in streams if s.get("codec_type") == "video"), None)
    fps = None
    if v and v.get("r_frame_rate", "0/0") not in ("0/0", ""):
        n, d = v["r_frame_rate"].split("/")
        fps = float(n) / float(d) if float(d) else None
    return {"duration": float(info.get("format", {}).get("duration") or 0.0), "has_video": v is not None,
            "n_audio": sum(1 for s in streams if s.get("codec_type") == "audio"),
            "width": int(v["width"]) if v else 0, "height": int(v["height"]) if v else 0, "fps": fps}


def autosave_path():
    """Fichier de sauvegarde automatique du montage en cours (un seul, mis a jour regulierement)."""
    base = os.environ.get("APPDATA") or os.path.expanduser("~")
    return os.path.join(base, "AutoCut", "autosave.autocut.json")


def fmt_t(t):
    t = max(0.0, t)
    return f"{int(t // 60)}:{t % 60:05.2f}"


def _lin(db):
    return 10 ** (db / 20.0)


def make_cut_clips(media, segs, start, mix_index, origin):
    """Les passages gardes d'une video = UN clip video (compose de tous les passages) + UN clip par piste audio,
    lies entre eux. segs : liste de (image_debut, image_fin). Seule la piste 'mixee' est audible au depart."""
    fps = float(media["fps"])
    segs_s = [[a / fps, b / fps] for a, b in segs]
    total = sum(e - s for s, e in segs_s)
    link = Project.new_link()
    n_aud = len(media["audio"])
    clips = [Clip("video", "V1", start, total, path=media["path"], src_in=segs_s[0][0], src_duration=media["duration"],
                  name=media["name"], has_audio=bool(media["audio"]), mute_audio=True, link=link, origin=origin,
                  segments=[list(x) for x in segs_s])]
    for k in range(n_aud):
        clips.append(Clip("audio", f"A{k + 1}", start, total, path=media["path"], src_in=segs_s[0][0],
                          src_duration=media["duration"], name=tr("ed.audio.track", name=media["name"], n=k + 1),
                          audio_index=k, mute_audio=(k != mix_index), link=link, origin=origin,
                          segments=[list(x) for x in segs_s]))
    return clips


# ====================================================================== apercu
class ScrubFilter(QObject):
    """Rester appuye sur un champ numerique et glisser a gauche / droite change sa valeur (comme un curseur).
    Un simple clic sert toujours a taper une valeur."""

    def __init__(self, spin, per_px):
        super().__init__(spin)
        self.spin, self.per_px = spin, per_px
        self._press = None
        spin.installEventFilter(self)
        spin.lineEdit().installEventFilter(self)
        spin.lineEdit().setCursor(Qt.SizeHorCursor)

    def eventFilter(self, _obj, e):
        t = e.type()
        if t == QEvent.MouseButtonPress and e.button() == Qt.LeftButton:
            self._press = (e.globalPosition().x(), self.spin.value(), False)
        elif t == QEvent.MouseMove and self._press and e.buttons() & Qt.LeftButton:
            x0, v0, dragging = self._press
            dx = e.globalPosition().x() - x0
            if dragging or abs(dx) >= 4:
                self._press = (x0, v0, True)
                v = min(max(v0 + dx * self.per_px, self.spin.minimum()), self.spin.maximum())
                self.spin.setValue(int(round(v)) if isinstance(self.spin, QSpinBox) else round(v, self.spin.decimals()))
                self.spin.lineEdit().deselect()
                return True
        elif t == QEvent.MouseButtonRelease and self._press:
            dragging, self._press = self._press[2], None
            if dragging:
                return True
        return False


class PreviewCanvas(QGraphicsView):
    """Image du montage : fond noir + video + images + textes, comme dans le rendu final.
    Mode 'deplacer sur la video' : on attrape l'element selectionne (cadre en pointilles) et on le glisse."""
    dragStarted = Signal()
    moved = Signal(float, float)         # nouvelle position du centre, en % de la largeur / hauteur

    def __init__(self, project):
        super().__init__()
        self.project = project
        self.edit_on = False
        self.edit_clip = None
        self._grab = None
        self.setScene(QGraphicsScene(self))
        self.setBackgroundBrush(QBrush(QColor("#000000")))
        self.setFrameShape(QGraphicsView.NoFrame)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setMinimumSize(320, 150)
        self.video_item = QGraphicsVideoItem()
        self.video_item.setVisible(False)
        self.scene().addItem(self.video_item)
        self.video_item.nativeSizeChanged.connect(lambda *_: self._layout_video())
        self.items = {}            # id du clip -> (signature, item)
        pen = QPen(QColor("#ffffff"), 2, Qt.DashLine)
        pen.setCosmetic(True)
        self.sel_rect = QGraphicsRectItem()
        self.sel_rect.setPen(pen)
        self.sel_rect.setZValue(1000)
        self.sel_rect.setVisible(False)
        self.scene().addItem(self.sel_rect)
        self.setMouseTracking(True)
        self._vclip = None
        self._vsig = None
        self.set_size(project.width, project.height)

    def set_size(self, w, h):
        self.scene().setSceneRect(0, 0, w, h)
        self.fitInView(self.scene().sceneRect(), Qt.KeepAspectRatio)
        self._vsig = None
        self._layout_video()

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self.fitInView(self.scene().sceneRect(), Qt.KeepAspectRatio)

    # ---- deplacer l'element selectionne directement sur l'image
    def set_edit_on(self, on):
        self.edit_on = bool(on)
        self._update_sel_rect()

    def set_edit_clip(self, clip):
        self.edit_clip = clip if clip is not None and clip.kind in ("video", "image", "text") else None
        self._update_sel_rect()

    def _update_sel_rect(self):
        c, show = self.edit_clip, False
        if self.edit_on and c is not None:
            it = self.video_item if (c.kind == "video" and c is self._vclip) else (self.items.get(c.id) or (None, None))[1]
            if it is not None and it.isVisible():
                self.sel_rect.setRect(it.sceneBoundingRect())
                show = True
        self.sel_rect.setVisible(show)

    def _grabbable(self, sp):
        return self.sel_rect.isVisible() and self.sel_rect.rect().adjusted(-24, -24, 24, 24).contains(sp)

    def mousePressEvent(self, e):
        if e.button() == Qt.LeftButton and self.edit_on and self.edit_clip is not None:
            sp = self.mapToScene(e.position().toPoint())
            if self._grabbable(sp):
                c = self.edit_clip
                self._grab = (sp, c.pos_x, c.pos_y)
                self.dragStarted.emit()
                e.accept()
                return
        super().mousePressEvent(e)

    def mouseMoveEvent(self, e):
        W, H = self.project.width, self.project.height
        sp = self.mapToScene(e.position().toPoint())
        if self._grab is not None:
            sp0, x0, y0 = self._grab
            px = min(150.0, max(-50.0, x0 + (sp.x() - sp0.x()) / W * 100.0))
            py = min(150.0, max(-50.0, y0 + (sp.y() - sp0.y()) / H * 100.0))
            if not e.modifiers() & Qt.ShiftModifier:          # petit aimant sur le centre (Maj = sans aimant)
                px = 50.0 if abs(px - 50.0) < 1.5 else px
                py = 50.0 if abs(py - 50.0) < 1.5 else py
            self.moved.emit(px, py)
            e.accept()
            return
        if self.edit_on:
            self.viewport().setCursor(Qt.SizeAllCursor if self._grabbable(sp) else Qt.ArrowCursor)
        super().mouseMoveEvent(e)

    def mouseReleaseEvent(self, e):
        if self._grab is not None:
            self._grab = None
            e.accept()
            return
        super().mouseReleaseEvent(e)

    def _layout_video(self):
        c = self._vclip
        if c is None:
            return
        W, H = self.project.width, self.project.height
        s = self.video_item.nativeSize()
        if s.isEmpty():
            return
        sig = (c.id, c.scale, c.pos_x, c.pos_y, s.width(), s.height(), W, H)
        if sig == self._vsig:               # rien n'a change : on ne touche pas a l'image (evite des saccades)
            return
        self._vsig = sig
        f = min(W / s.width(), H / s.height()) * max(1.0, c.scale) / 100.0
        w, h = s.width() * f, s.height() * f
        self.video_item.setSize(QSizeF(w, h))
        self.video_item.setPos(c.pos_x / 100.0 * W - w / 2, c.pos_y / 100.0 * H - h / 2)

    def sync(self, t, vclip, overlays):
        """Montre/cache les images et textes selon l'instant t. vclip = video affichee (ou None)."""
        W, H = self.project.width, self.project.height
        alive = set()
        for c in overlays:
            alive.add(c.id)
            layer = track_num(c.track) + 1
            if c.kind == "image":
                sig = ("i", c.path, c.scale, W, H)
                cur = self.items.get(c.id)
                if cur is None or cur[0] != sig:
                    if cur:
                        self.scene().removeItem(cur[1])
                    pm = QPixmap(c.path)
                    if pm.isNull():
                        continue
                    sc = max(1.0, c.scale) / 100.0
                    pm = pm.scaled(int(W * sc), int(H * sc), Qt.KeepAspectRatio, Qt.SmoothTransformation)
                    it = QGraphicsPixmapItem(pm)
                    self.scene().addItem(it)
                    cur = self.items[c.id] = (sig, it)
                it = cur[1]
                it.setPos(c.pos_x / 100.0 * W - it.pixmap().width() / 2, c.pos_y / 100.0 * H - it.pixmap().height() / 2)
            else:
                sig = ("t", c.text, c.font, c.size, c.color, c.outline, c.outline_color, W, H)
                cur = self.items.get(c.id)
                if cur is None or cur[0] != sig:
                    if cur:
                        self.scene().removeItem(cur[1])
                    it = QGraphicsSimpleTextItem(c.text)
                    f = QFont(c.font)
                    f.setPixelSize(max(8, round(c.size * H / 1080)))
                    it.setFont(f)
                    it.setBrush(QBrush(QColor(c.color)))
                    it.setPen(QPen(Qt.NoPen))
                    ow = c.outline * H / 1080
                    if ow > 0:                               # contour : le meme texte, en dessous, avec un trait epais
                        o = QGraphicsSimpleTextItem(c.text, it)
                        o.setFont(f)
                        o.setBrush(QBrush(QColor(c.outline_color)))
                        o.setPen(QPen(QColor(c.outline_color), ow * 2, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
                        o.setFlag(QGraphicsItem.ItemStacksBehindParent)
                    self.scene().addItem(it)
                    cur = self.items[c.id] = (sig, it)
                it = cur[1]
                br = it.boundingRect()
                it.setPos(c.pos_x / 100.0 * W - br.width() / 2, c.pos_y / 100.0 * H - br.height() / 2)
            cur[1].setZValue(layer)
            cur[1].setVisible(c.start <= t < c.end)
        for cid in [k for k in self.items if k not in alive]:
            self.scene().removeItem(self.items.pop(cid)[1])
        if vclip is not self._vclip:
            self._vclip = vclip
            self._vsig = None
            self.video_item.setVisible(vclip is not None)
            if vclip is not None:
                self.video_item.setZValue(track_num(vclip.track) + 1)
        self._layout_video()
        self._update_sel_rect()


class Preview(QObject):
    """Lecture du montage.

    Pour que la lecture soit fluide :
    - le lecteur video est l'HORLOGE : le temps du montage suit sa position (on ne le re-positionne pas sans arret) ;
    - si le son choisi vient du meme fichier que la video, c'est le lecteur video qui le joue (une seule lecture
      du fichier, image et son restent synchronises) ;
    - on ne re-positionne un lecteur qu'aux coupes d'un clip compose ou apres un deplacement."""
    timeChanged = Signal(float)
    stateChanged = Signal(bool)
    SETTLE = 0.5                     # apres un deplacement, on laisse le lecteur se stabiliser (s)

    def __init__(self, project, canvas):
        super().__init__()
        self.project, self.canvas = project, canvas
        self.vplayer = QMediaPlayer(self)
        self.vout = QAudioOutput(self)
        self.vplayer.setAudioOutput(self.vout)
        self.vplayer.setVideoOutput(canvas.video_item)
        self.vclip = None
        self._vpath = None
        self._vpending = None
        self._vseg = None
        self._vtrack = None
        self._vvol = None
        self._vcmd = None
        self._vseek = 0.0
        self.vplayer.mediaStatusChanged.connect(self._v_status)
        self.vplayer.tracksChanged.connect(self._apply_track)
        self.aplayers = {}         # (fichier, piste) -> dict du lecteur
        self.t = 0.0
        self.playing = False
        self._t0 = self._base = 0.0
        self._videos, self._sounds, self._overlays = [], [], []
        self._rebuild_index()
        self.timer = QTimer(self)
        self.timer.setInterval(30)
        self.timer.timeout.connect(self._tick)
        self.timer.start()

    # ---------------------------------------------------------------- commandes
    def total(self):
        return self.project.duration()

    def _rebuild_index(self):
        cl = self.project.clips
        self._videos = [c for c in cl if c.kind == "video"]
        self._sounds = [c for c in cl if c.kind == "audio"]
        self._overlays = [c for c in cl if c.kind in ("image", "text")]

    def seek(self, t):
        self.t = min(max(0.0, t), max(self.total(), 0.0))
        self._base, self._t0 = self.t, time.perf_counter()
        self._sync(force=True)
        self.timeChanged.emit(self.t)

    def play(self):
        if self.total() <= 0:
            return
        if self.t >= self.total() - 0.02:
            self.t = 0.0
        self.playing = True
        self._base, self._t0 = self.t, time.perf_counter()
        self._sync(force=True)
        self.stateChanged.emit(True)

    def pause(self):
        self.playing = False
        self.vplayer.pause()
        self._vcmd = False
        for e in self.aplayers.values():
            e["p"].pause()
            e["cmd"] = False
        self.stateChanged.emit(False)

    def toggle(self):
        self.pause() if self.playing else self.play()

    def refresh(self):
        """A appeler apres une modification du projet."""
        self._rebuild_index()
        if self.t > self.total():
            self.t = self.total()
        self._sync(force=True)
        self.timeChanged.emit(self.t)

    # ---------------------------------------------------------------- moteur
    def _tick(self):
        if not self.playing:
            return
        t = self._base + (time.perf_counter() - self._t0)
        if t >= self.total():
            self.t = self.total()
            self.pause()
            self.timeChanged.emit(self.t)
            return
        self.t = t
        self._sync()
        self.timeChanged.emit(self.t)

    def _volume(self, c):
        if not c.has_sound():
            return 0.0
        g = self.project.gain_db(c)
        return 0.0 if g is None else min(1.0, _lin(g))      # l'apercu ne depasse jamais 0 dB

    @staticmethod
    def _same_map(a, b):
        """Les deux elements lisent-ils le meme instant du fichier au meme instant du montage ?"""
        if a.segments or b.segments:
            return a.segments == b.segments and abs(a.start - b.start) < 1e-3
        return abs((a.start - a.src_in) - (b.start - b.src_in)) < 1e-3

    @staticmethod
    def _timeline_at(clip, idx, src_t):
        """Instant du montage pour un instant du fichier, dans le passage idx (inverse de src_at)."""
        if idx < 0:
            return clip.start + (src_t - clip.src_in)
        acc = sum(e - s for s, e in clip.segments[:idx])
        s, e = clip.segments[idx]
        return clip.start + acc + min(max(src_t - s, 0.0), e - s)

    def _sync(self, force=False):
        now = time.perf_counter()
        t = self.t
        v = None
        for c in self._videos:
            if (c.start <= t < c.end and self.project.track_active(c.track)
                    and (v is None or track_num(c.track) > track_num(v.track))):
                v = c
        self.canvas.sync(t, v, [c for c in self._overlays if self.project.track_active(c.track)])
        audible = {}
        for c in self._sounds:
            if c.start <= t < c.end and self._volume(c) > 0:
                audible.setdefault((c.path, c.audio_index), c)
        carrier = None
        if v is not None:
            for key, c in audible.items():
                if c.path == v.path and self._same_map(c, v):
                    carrier = key
                    break
        carrier_clip = audible.pop(carrier) if carrier else None

        # ---- video (et le son qui vient du meme fichier)
        if v is None:
            if self.vplayer.playbackState() != QMediaPlayer.StoppedState:
                self.vplayer.pause()
            self.vclip, self._vseg, self._vcmd = None, None, None
        else:
            idx = seg_index_at(v, t)
            seg = (v.id, idx)
            want = int(src_at(v, t) * 1000)
            if v.path != self._vpath:
                self._vpath, self._vpending, self._vcmd = v.path, want, None
                self._vseek = now
                self.vplayer.setSource(QUrl.fromLocalFile(v.path))
            elif self._vpending is not None:
                self._vpending = want
            elif force or seg != self._vseg:
                self.vplayer.setPosition(want)                 # moment precis : coupe, clic, modification
                self._vseek = now
            elif (self.playing and now - self._vseek > self.SETTLE
                  and self.vplayer.playbackState() == QMediaPlayer.PlayingState):
                # le lecteur est l'horloge : si l'horloge s'ecarte de lui, c'est l'horloge qui se recale
                tp = self._timeline_at(v, idx, self.vplayer.position() / 1000.0)
                if abs(tp - self.t) > 0.04:
                    self._base, self._t0, self.t = tp, now, tp
            self._vseg = seg
            self.vclip = v
            src = carrier_clip if carrier_clip is not None else v
            if src.audio_index != self._vtrack:
                self._vtrack = src.audio_index
                self._apply_track()
            vol = self._volume(src)
            if vol != self._vvol:
                self._vvol = vol
                self.vout.setVolume(vol)
            if self._vpending is None and (force or self.playing != self._vcmd):
                self._vcmd = self.playing
                self.vplayer.play() if self.playing else self.vplayer.pause()

        # ---- autres sons : un lecteur par (fichier, piste audio)
        for key, c in audible.items():
            idx = seg_index_at(c, t)
            seg = (c.id, idx)
            want = int(src_at(c, t) * 1000)
            e = self.aplayers.get(key)
            if e is None:
                p, o = QMediaPlayer(self), QAudioOutput(self)
                p.setAudioOutput(o)
                e = dict(p=p, o=o, pending=want, seg=None, cmd=None, seek=now, vol=None)
                p.mediaStatusChanged.connect(lambda st, k=key: self._a_status(k, st))
                self.aplayers[key] = e
                p.setSource(QUrl.fromLocalFile(c.path))
            if e["pending"] is not None:                # le fichier est encore en chargement
                e["pending"] = want
                continue
            p = e["p"]
            vol = self._volume(c)
            if vol != e["vol"]:
                e["vol"] = vol
                e["o"].setVolume(vol)
            if (force or seg != e["seg"] or (now - e["seek"] > self.SETTLE and abs(p.position() - want) > 250)):
                p.setPosition(want)
                e["seek"] = now
            e["seg"] = seg
            if force or self.playing != e["cmd"]:
                e["cmd"] = self.playing
                p.play() if self.playing else p.pause()
        for key, e in self.aplayers.items():
            if key not in audible and e["cmd"] is not False:
                e["p"].pause()
                e["cmd"] = False

    def _v_status(self, st):
        if st in (QMediaPlayer.LoadedMedia, QMediaPlayer.BufferedMedia) and self._vpending is not None:
            self.vplayer.setPosition(self._vpending)
            self._vpending = None
            self._vseek = time.perf_counter()
            self._apply_track()
            self._vcmd = self.playing
            self.vplayer.play() if self.playing else self.vplayer.pause()

    def _a_status(self, key, st):
        e = self.aplayers.get(key)
        if e and st in (QMediaPlayer.LoadedMedia, QMediaPlayer.BufferedMedia) and e["pending"] is not None:
            p = e["p"]
            p.setPosition(e["pending"])
            e["pending"], e["seek"] = None, time.perf_counter()
            p.setActiveAudioTrack(min(max(0, key[1]), max(0, len(p.audioTracks()) - 1)))
            e["cmd"] = self.playing
            p.play() if self.playing else p.pause()

    def _apply_track(self, *_):
        n = len(self.vplayer.audioTracks())
        if self._vtrack is not None and n:
            self.vplayer.setActiveAudioTrack(min(max(0, self._vtrack), n - 1))

    def shutdown(self):
        self.timer.stop()
        self.vplayer.stop()
        for e in self.aplayers.values():
            e["p"].stop()


# ====================================================================== rendu en arriere-plan
class FuncWorker(QThread):
    """Execute une fonction longue (fn(progress, should_stop)) sans bloquer la fenetre."""
    progress = Signal(float)
    done = Signal(object)
    failed = Signal(str)

    def __init__(self, fn):
        super().__init__()
        self.fn, self._stop = fn, False

    def stop(self):
        self._stop = True

    def run(self):
        try:
            self.done.emit(self.fn(self.progress.emit, lambda: self._stop))
        except InterruptedError:
            self.failed.emit("")
        except Exception as e:  # noqa: BLE001
            self.failed.emit(str(e))


class PassWorker(QThread):
    """Fabrique le fichier leger de la passe 'Couper les silences' (sans bloquer la fenetre)."""
    progress = Signal(float)
    done = Signal(str)
    failed = Signal(str)

    def __init__(self, project, path):
        super().__init__()
        self.project, self.path = deepcopy(project), path
        self._stop = False

    def stop(self):
        self._stop = True

    def run(self):
        try:
            render.render_pass(self.project, self.path, progress=self.progress.emit, should_stop=lambda: self._stop)
            self.done.emit(self.path)
        except InterruptedError:
            self.failed.emit("")
        except Exception as e:  # noqa: BLE001
            self.failed.emit(str(e))


class RenderWorker(QThread):
    progress = Signal(float)
    done = Signal(str)
    failed = Signal(str)

    def __init__(self, project, path):
        super().__init__()
        self.project, self.path = deepcopy(project), path
        self.info = {}                    # volume du fichier cree (si la normalisation est active)
        self._stop = False

    def stop(self):
        self._stop = True

    def run(self):
        try:
            render.render(self.project, self.path, progress=self.progress.emit, should_stop=lambda: self._stop)
            if getattr(self.project, "normalize", False) and render.has_sound(self.project):
                try:                      # on verifie le resultat : volume reel du fichier cree
                    self.info["after"] = render.measure_file(self.path)
                except Exception:  # noqa: BLE001
                    self.info["after"] = None
            self.done.emit(self.path)
        except InterruptedError:
            self.failed.emit("")
        except Exception as e:  # noqa: BLE001
            self.failed.emit(str(e))


# ====================================================================== la page
class EditorPage(QWidget):
    def __init__(self, win):
        super().__init__()
        self.win = win
        self.project = Project()
        self._tr = []
        self._undo, self._redo = [], []
        self._updating = False
        self._coalesce = QTimer(self)
        self._coalesce.setSingleShot(True)
        self._coalesce.setInterval(700)
        self.sel = None
        self.worker = None
        self.setAcceptDrops(True)
        self.audio_page = None            # rempli par la fenetre principale
        self._dirty = False               # le montage a change depuis le dernier enregistrement
        self._clip_buf = []               # presse-papiers du montage : [(clip copie, decalage dans le groupe)]
        self._rev, self._saved_rev = 0, -1
        self._autosave_on = False
        self._build()
        self._restore_tools()
        self._load_props()

    # ------------------------------------------------------------ traduction
    def _r(self, widget, key, method="setText", **kw):
        def apply(w=widget, k=key, m=method, a=kw):
            getattr(w, m)(tr(k, **a))
        self._tr.append(apply)
        apply()
        return widget

    def retranslate(self):
        for apply in self._tr:
            apply()
        self._update_play_text()
        self._load_props()
        self.timeline.invalidate()

    # ------------------------------------------------------------ construction
    def _build(self):
        r = self._r
        # ---- colonne de gauche : (apercu + transport) / (vue d'ensemble + timeline), separes par une poignee
        top = QWidget()
        tv = QVBoxLayout(top)
        tv.setContentsMargins(14, 12, 6, 2)
        tv.setSpacing(6)
        self.canvas = PreviewCanvas(self.project)
        self.preview = Preview(self.project, self.canvas)
        self.preview.timeChanged.connect(self._on_time)
        self.preview.stateChanged.connect(lambda _p: self._update_play_text())
        tv.addWidget(self.canvas, 1)

        tr_row = QHBoxLayout()
        tr_row.setSpacing(5)
        self.btn_play = QPushButton()
        self.btn_play.setFixedWidth(96)
        self.btn_play.clicked.connect(self.toggle_play)
        self.lbl_time = QLabel("0:00.00 / 0:00.00")
        self.lbl_time.setMinimumWidth(128)
        tr_row.addWidget(self.btn_play)
        tr_row.addWidget(self.lbl_time)
        tr_row.addSpacing(6)
        self._build_tools(tr_row)
        tr_row.addStretch(1)
        for glyph, key, fn in (("−", "nav.zoomout", lambda: self.timeline.zoom(0.7)),
                               ("+", "nav.zoomin", lambda: self.timeline.zoom(1.4)),
                               ("↔", "nav.all", lambda: self.timeline.fit_all())):
            b = r(QPushButton(glyph), key, "setToolTip")
            b.setObjectName("tool")
            b.setFixedSize(30, 28)
            b.setFocusPolicy(Qt.NoFocus)
            b.clicked.connect(fn)
            tr_row.addWidget(b)
        tv.addLayout(tr_row)

        bottom = QWidget()
        bv = QVBoxLayout(bottom)
        bv.setContentsMargins(14, 2, 6, 10)
        bv.setSpacing(4)
        self.timeline = TimelineWidget(self.project)
        self.overview = ProjectOverview(self.timeline)
        bv.addWidget(self.overview)
        self.timeline.cutRequested.connect(self.cut_at)
        self.timeline.trackToggled.connect(self.toggle_track)
        self.timeline.seekRequested.connect(self.preview.seek)
        self.timeline.selectionChanged.connect(self._on_select)
        self.timeline.clipsChanged.connect(self._changed)
        self.timeline.editStarted.connect(self.push_undo)
        self.timeline.editFinished.connect(self._edit_finished)
        self.timeline.viewChanged.connect(self._sync_scrollbar)
        bv.addWidget(self.timeline, 1)
        self.hbar = QScrollBar(Qt.Horizontal)
        self.hbar.valueChanged.connect(lambda v: self.timeline.set_scroll(v / 100.0) if not self._updating else None)
        bv.addWidget(self.hbar)
        self.vsplit = QSplitter(Qt.Vertical)             # on tire la poignee pour agrandir l'apercu ou la timeline
        self.vsplit.setObjectName("vsplit")
        self.vsplit.setHandleWidth(9)
        self.vsplit.setChildrenCollapsible(False)
        self.vsplit.addWidget(top)
        self.vsplit.addWidget(bottom)
        self.vsplit.setStretchFactor(0, 3)
        self.vsplit.setStretchFactor(1, 2)
        self.vsplit.setSizes([430, 330])
        left = self.vsplit

        # ---- panneau de droite
        right = QWidget()
        rv = QVBoxLayout(right)
        rv.setContentsMargins(6, 4, 14, 10)
        rv.setSpacing(6)

        g1 = r(QGroupBox(), "ed.add", "setTitle")
        gl = QHBoxLayout(g1)
        for kind, key in (("video", "ed.add.video"), ("image", "ed.add.image"), ("text", "ed.add.text"),
                          ("audio", "ed.add.audio")):
            b = r(QPushButton(), key)
            b.clicked.connect(lambda _=False, k=kind: self.add_dialog(k))
            gl.addWidget(b)
        rv.addWidget(g1)

        g2 = r(QGroupBox(), "ed.clip", "setTitle")
        v2 = QVBoxLayout(g2)
        self.lbl_none = r(QLabel(), "ed.none")
        self.lbl_none.setObjectName("muted")
        self.lbl_none.setWordWrap(True)
        v2.addWidget(self.lbl_none)
        self.form_host = QWidget()
        self.form = QFormLayout(self.form_host)
        self.form.setContentsMargins(0, 0, 0, 0)
        self.form.setVerticalSpacing(6)
        self.form.setRowWrapPolicy(QFormLayout.WrapLongRows)
        self._rows = []

        def row(key, widget, kinds):
            lab = r(QLabel(), key)
            self.form.addRow(lab, widget)
            self._rows.append((lab, widget, set(kinds)))
            return widget

        ALL = ("video", "image", "text", "audio")
        self.sp_start = row("ed.start", self._dspin(0, 36000, 0.1, 2, " s"), ALL)
        self.sp_dur = row("ed.dur", self._dspin(MIN_DUR, 36000, 0.1, 2, " s"), ALL)
        self.sp_vol = row("ed.volume", self._dspin(-60, 24, 0.5, 1, " dB"), ("video", "audio"))
        self.chk_mute = row("ed.mute", QCheckBox(), ("video", "audio"))
        self.txt = row("ed.text", QPlainTextEdit(), ("text",))
        self.txt.setFixedHeight(70)
        self.cb_font = row("ed.font", QComboBox(), ("text",))
        self.cb_font.addItems(list(FONTS))
        self.sp_size = row("ed.size", QSpinBox(), ("text",))
        self.sp_size.setRange(8, 600)
        self.btn_color = row("ed.color", QPushButton(), ("text",))
        self.sp_outline = row("ed.outline", self._dspin(0, 60, 0.5, 1, " px"), ("text",))
        self.btn_ocolor = row("ed.outlinecolor", QPushButton(), ("text",))
        self.sp_px = row("ed.posx", self._dspin(-50, 150, 1, 1, " %"), ("video", "image", "text"))
        self.sp_py = row("ed.posy", self._dspin(-50, 150, 1, 1, " %"), ("video", "image", "text"))
        self.sp_scale = row("ed.scale", self._dspin(5, 400, 5, 0, " %"), ("video", "image"))
        self.btn_move = row("ed.placement", QPushButton(), ("video", "image", "text"))
        r(self.btn_move, "ed.move")
        r(self.btn_move, "tip.move", "setToolTip")
        self.btn_move.setCheckable(True)
        self.btn_move.toggled.connect(self._move_mode)
        for sp_, per_px in ((self.sp_px, 0.2), (self.sp_py, 0.2), (self.sp_scale, 0.5), (self.sp_size, 0.5),
                            (self.sp_outline, 0.05), (self.sp_vol, 0.1)):
            ScrubFilter(sp_, per_px)
        v2.addWidget(self.form_host)
        brow = QHBoxLayout()
        self.btn_split = r(QPushButton(), "ed.split")
        self.btn_dup = r(QPushButton(), "ed.dup")
        self.btn_del = r(QPushButton(), "ed.delete")
        self.btn_split.clicked.connect(self.split_at_playhead)
        self.btn_dup.clicked.connect(self.duplicate_selected)
        self.btn_del.clicked.connect(self.delete_selected)
        for b in (self.btn_split, self.btn_dup, self.btn_del):
            brow.addWidget(b)
        v2.addLayout(brow)
        crow = QHBoxLayout()
        self.btn_copy = r(QPushButton(), "ed.copy")
        self.btn_cutclip = r(QPushButton(), "ed.cutclip")
        self.btn_paste = r(QPushButton(), "ed.paste")
        r(self.btn_paste, "tip.paste", "setToolTip")
        self.btn_copy.clicked.connect(self.copy_selected)
        self.btn_cutclip.clicked.connect(self.cut_selected)
        self.btn_paste.clicked.connect(self.paste_clips)
        for b in (self.btn_copy, self.btn_cutclip, self.btn_paste):
            crow.addWidget(b)
        v2.addLayout(crow)
        self.btn_unlink = r(QPushButton(), "ed.unlink")
        r(self.btn_unlink, "tip.unlink", "setToolTip")
        self.btn_unlink.clicked.connect(self.unlink_selected)
        v2.addWidget(self.btn_unlink)
        rv.addWidget(g2)

        for w_, sig, attr in ((self.sp_start, "valueChanged", "start"), (self.sp_dur, "valueChanged", "duration"),
                              (self.sp_vol, "valueChanged", "volume_db"), (self.sp_px, "valueChanged", "pos_x"),
                              (self.sp_py, "valueChanged", "pos_y"), (self.sp_scale, "valueChanged", "scale"),
                              (self.sp_size, "valueChanged", "size"), (self.sp_outline, "valueChanged", "outline")):
            getattr(w_, sig).connect(lambda v, a=attr: self._prop(a, v))
        self.chk_mute.toggled.connect(lambda v: self._prop("mute_audio", v))
        self.txt.textChanged.connect(lambda: self._prop("text", self.txt.toPlainText()))
        self.cb_font.currentTextChanged.connect(lambda v: self._prop("font", v))
        self.btn_color.clicked.connect(lambda: self._pick_color("color"))
        self.btn_ocolor.clicked.connect(lambda: self._pick_color("outline_color"))
        self.canvas.dragStarted.connect(self.push_undo)
        self.canvas.moved.connect(self._canvas_moved)

        gt = r(QGroupBox(), "tr.title", "setTitle")
        vt = QVBoxLayout(gt)
        vt.setSpacing(6)
        self.lbl_tr = QLabel()
        self.lbl_tr.setObjectName("muted")
        self.lbl_tr.setWordWrap(True)
        vt.addWidget(self.lbl_tr)
        trow = QHBoxLayout()
        self.cb_trtrack = QComboBox()
        self.btn_transcribe = r(QPushButton(), "tr.btn")
        r(self.btn_transcribe, "tip.tr.btn", "setToolTip")
        self.btn_transcribe.clicked.connect(self.start_transcription)
        trow.addWidget(self.cb_trtrack, 1)
        trow.addWidget(self.btn_transcribe)
        vt.addLayout(trow)
        self.ed_search = QLineEdit()
        self.ed_search.setClearButtonEnabled(True)
        self.ed_search.returnPressed.connect(self.tr_search)
        self.ed_search.textChanged.connect(lambda t: self.tr_search() if not t.strip() or len(t.strip()) >= 3 else None)
        self._tr.append(lambda: self.ed_search.setPlaceholderText(tr("tr.search.ph")))
        self._tr[-1]()
        vt.addWidget(self.ed_search)
        self.lst_res = QListWidget()
        self.lst_res.setMinimumHeight(120)
        self.lst_res.setSelectionMode(QListWidget.ExtendedSelection)
        self.lst_res.itemActivated.connect(self._tr_goto)
        self.lst_res.itemDoubleClicked.connect(self._tr_goto)
        vt.addWidget(self.lst_res)
        self.lbl_trcount = QLabel()
        self.lbl_trcount.setObjectName("muted")
        vt.addWidget(self.lbl_trcount)
        r1 = QHBoxLayout()
        self.btn_fillers = r(QPushButton(), "tr.fillers")
        r(self.btn_fillers, "tip.tr.fillers", "setToolTip")
        self.btn_fillers.clicked.connect(self.tr_fillers)
        self.btn_cut_sel = r(QPushButton(), "tr.cut.sel")
        r(self.btn_cut_sel, "tip.tr.cut", "setToolTip")
        self.btn_cut_sel.clicked.connect(lambda: self.tr_cut(False))
        self.btn_cut_all = r(QPushButton(), "tr.cut.all")
        r(self.btn_cut_all, "tip.tr.cut", "setToolTip")
        self.btn_cut_all.clicked.connect(lambda: self.tr_cut(True))
        for b in (self.btn_fillers, self.btn_cut_sel, self.btn_cut_all):
            r1.addWidget(b)
        vt.addLayout(r1)
        self.btn_ai = r(QPushButton(), "tr.ai")
        r(self.btn_ai, "tip.tr.ai", "setToolTip")
        self.btn_ai.clicked.connect(self.export_for_ai)
        vt.addWidget(self.btn_ai)
        rv.addWidget(gt)
        self._tr_matches, self._tr_kind, self._tr_worker, self._probed = [], "search", None, {}

        g3 = r(QGroupBox(), "ed.project", "setTitle")
        f3 = QFormLayout(g3)
        f3.setVerticalSpacing(8)
        f3.setRowWrapPolicy(QFormLayout.WrapLongRows)
        self.cb_res = QComboBox()
        for w_, h_ in RESOLUTIONS:
            self.cb_res.addItem(f"{w_} x {h_}", (w_, h_))
        self.cb_fps = QComboBox()
        for f_ in FPS_CHOICES:
            self.cb_fps.addItem(f"{f_}", f_)
        f3.addRow(r(QLabel(), "ed.res"), self.cb_res)
        f3.addRow(r(QLabel(), "ed.fps"), self.cb_fps)
        self.cb_res.activated.connect(self._format_changed)
        self.cb_fps.activated.connect(self._format_changed)
        prow = QHBoxLayout()
        for key, fn in (("ed.new", self.new_project), ("ed.open", self.open_project), ("ed.save", self.save_project)):
            b = r(QPushButton(), key)
            b.clicked.connect(fn)
            prow.addWidget(b)
        self.btn_pass = r(QPushButton(), "ed.pass")
        r(self.btn_pass, "tip.pass", "setToolTip")
        self.btn_pass.clicked.connect(self.start_silence_pass)
        f3.addRow(self.btn_pass)
        f3.addRow(prow)
        self.btn_export = r(QPushButton(), "ed.export")
        self.btn_export.setObjectName("primary")
        self.btn_export.setStyleSheet("padding: 11px 14px; font-size: 11pt;")
        self.btn_export.clicked.connect(self.export_video)
        self.bar = QProgressBar()
        self.bar.setRange(0, 1000)
        self.bar.setTextVisible(False)
        self.bar.setFixedHeight(8)
        self.bar.hide()
        rv.addWidget(g3)
        rv.addStretch(1)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(right)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        scroll.setFrameShape(QScrollArea.NoFrame)
        # le bouton d'export reste toujours visible en bas du panneau (hors de la zone defilante)
        panel_w = QWidget()
        panel_w.setMinimumWidth(400)
        pl = QVBoxLayout(panel_w)
        pl.setContentsMargins(0, 0, 0, 0)
        pl.setSpacing(6)
        pl.addWidget(scroll, 1)
        foot = QWidget()
        fl = QVBoxLayout(foot)
        fl.setContentsMargins(6, 0, 14, 10)
        fl.setSpacing(6)
        self.btn_xml = r(QPushButton(), "ed.xml")
        r(self.btn_xml, "tip.xml", "setToolTip")
        self.btn_xml.setStyleSheet("padding: 8px 14px;")
        self.btn_xml.clicked.connect(self.export_xml)
        fl.addWidget(self.bar)
        fl.addWidget(self.btn_export)
        fl.addWidget(self.btn_xml)
        pl.addWidget(foot)
        self.panel = panel_w
        self.btn_panel = QPushButton("▶")
        self.btn_panel.setObjectName("panelarrow")
        self.btn_panel.setFixedSize(18, 88)
        self.btn_panel.setCursor(Qt.PointingHandCursor)
        self.btn_panel.clicked.connect(lambda: self.win.toggle_panel("edit"))
        wrap = QWidget()
        wl = QHBoxLayout(wrap)
        wl.setContentsMargins(0, 0, 0, 0)
        wl.setSpacing(0)
        wl.addWidget(left, 1)
        wl.addWidget(self.btn_panel, 0, Qt.AlignVCenter)
        split = QSplitter()
        split.setHandleWidth(4)
        split.addWidget(wrap)
        split.addWidget(panel_w)
        split.setStretchFactor(0, 3)
        split.setStretchFactor(1, 1)
        split.setSizes([1000, 440])
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(split)

        # raccourcis (actifs seulement quand on est dans cet onglet)
        for key, fn in ((Qt.Key_Delete, self.delete_selected), (Qt.Key_S, self.split_at_playhead),
                        (Qt.Key_V, lambda: self.set_tool("select")), (Qt.Key_C, lambda: self.set_tool("cut")),
                        (Qt.Key_M, self.btn_magnet.toggle), (Qt.Key_L, self.btn_all.toggle),
                        (Qt.Key_Q, lambda: self.trim_to_playhead("l")), (Qt.Key_W, lambda: self.trim_to_playhead("r")),
                        ("Shift+Delete", self.ripple_delete), (Qt.Key_Z, self.fit_selection),
                        ("Ctrl+C", self.copy_selected), ("Ctrl+X", self.cut_selected), ("Ctrl+V", self.paste_clips),
                        (Qt.Key_R, self.btn_ripple.toggle), (Qt.Key_G, self.close_gaps),
                        (Qt.Key_PageUp, lambda: self.goto_edge(-1)), (Qt.Key_PageDown, lambda: self.goto_edge(1))):
            QShortcut(QKeySequence(key), self, activated=fn, context=Qt.WidgetWithChildrenShortcut)
        self._update_play_text()

    def _build_tools(self, row):
        """Outils du montage, en une seule rangee d'icones (le nom et le raccourci sont dans l'info-bulle)."""

        def tool(glyph, key, tip, fn=None, checkable=False):
            b = QPushButton(glyph)
            b.setObjectName("tool")
            b.setFixedSize(32, 28)
            b.setCheckable(checkable)
            b.setFocusPolicy(Qt.NoFocus)
            self._tr.append(lambda b=b, k=key, t=tip: b.setToolTip(tr(k) + "\n" + tr(t)))
            self._tr[-1]()
            if fn:
                b.clicked.connect(fn)
            row.addWidget(b)
            return b

        grp = QButtonGroup(self)
        self.btn_t_select = tool("↖", "tool.select", "tip.select", lambda: self.set_tool("select"), True)
        self.btn_t_cut = tool("✂", "tool.cut", "tip.cut", lambda: self.set_tool("cut"), True)
        for b in (self.btn_t_select, self.btn_t_cut):
            grp.addButton(b)
        self.btn_t_select.setChecked(True)
        self.btn_magnet = tool("🧲", "tool.magnet", "tip.magnet", None, True)
        self.btn_magnet.setChecked(True)
        self.btn_magnet.toggled.connect(self._set_magnet)
        self.btn_all = tool("⛓", "tool.all", "tip.all", None, True)
        self.btn_all.setChecked(True)
        self.btn_all.toggled.connect(self._set_linked)
        row.addSpacing(4)
        tool("⇤", "tool.trim_l", "tip.trim_l", lambda: self.trim_to_playhead("l"))
        tool("⇥", "tool.trim_r", "tip.trim_r", lambda: self.trim_to_playhead("r"))
        tool("⌫", "tool.ripple", "tip.ripple", self.ripple_delete)
        tool("⤢", "tool.fit", "tip.fit", self.fit_selection)
        self.btn_ripple = tool("⇌", "tool.ripple_mode", "tip.ripple_mode", None, True)
        self.btn_ripple.toggled.connect(self._set_ripple)
        tool("⊟", "tool.closegaps", "tip.closegaps", self.close_gaps)
        row.addSpacing(4)
        tool("⏮", "tool.prev", "tip.prev", lambda: self.goto_edge(-1))
        tool("⏭", "tool.next", "tip.next", lambda: self.goto_edge(1))

    def toggle_track(self, track, what):
        """Boutons Solo / Muet / Miniatures d'une piste (colonne de gauche de la timeline)."""
        p = self.project
        d = {"solo": p.track_solo, "mute": p.track_mute, "media": p.track_media}[what]
        self.push_undo()
        d[track] = not d.get(track, what == "media")
        self._after_project_change()

    def set_tool(self, tool):
        self.timeline.set_tool(tool)
        (self.btn_t_cut if tool == "cut" else self.btn_t_select).setChecked(True)
        settings.put("tool_active", tool)

    def _restore_tools(self):
        """Retrouve les outils comme on les avait laisses (aimant, toutes les pistes, outil actif)."""
        self.btn_magnet.setChecked(settings.get("tool_magnet"))
        self.btn_all.setChecked(settings.get("tool_linked"))
        self.btn_ripple.setChecked(settings.get("tool_ripple"))
        self.set_tool("cut" if settings.get("tool_active") == "cut" else "select")

    def _set_ripple(self, on):
        settings.put("tool_ripple", bool(on))

    def _set_magnet(self, on):
        self.timeline.magnet = on
        settings.put("tool_magnet", bool(on))

    def _set_linked(self, on):
        self.project.linked = on
        settings.put("tool_linked", bool(on))
        self.timeline.invalidate()
        self._load_props()

    @staticmethod
    def _dspin(lo, hi, step, dec, suffix):
        s = QDoubleSpinBox()
        s.setRange(lo, hi)
        s.setSingleStep(step)
        s.setDecimals(dec)
        s.setSuffix(suffix)
        s.setKeyboardTracking(False)
        return s

    # ------------------------------------------------------------ lecture
    def toggle_play(self):
        self.preview.toggle()

    def _update_play_text(self):
        self.btn_play.setText(tr("btn.pause") if self.preview.playing else tr("btn.play"))

    def _on_time(self, t):
        self.lbl_time.setText(f"{fmt_t(t)} / {fmt_t(self.project.duration())}")
        self.timeline.set_playhead(t, follow=self.preview.playing)
        self.overview.update()

    def _sync_scrollbar(self):
        self._updating = True
        lo, hi = self.timeline.scroll_limits()
        self.hbar.setRange(int(lo * 100), int(hi * 100))
        self.hbar.setPageStep(int(self.timeline.visible_seconds() * 100))
        self.hbar.setValue(int(self.timeline.scroll * 100))
        self._updating = False

    # ------------------------------------------------------------ annuler / retablir
    def push_undo(self, coalesce=False):
        self._dirty = True
        self._rev += 1
        if coalesce:
            burst = self._coalesce.isActive()
            self._coalesce.start()
            if burst:
                return
        self._undo.append(self.project.snapshot())
        del self._undo[:-100]
        self._redo.clear()

    def _restore(self, snap):
        sel_id = self.sel.id if self.sel else None
        self.project.restore(snap)
        self.sel = next((c for c in self.project.clips if c.id == sel_id), None)
        self.timeline.selected = self.sel
        self._after_project_change(full=True)

    def undo(self):
        if self._undo:
            self._dirty = True
            self._rev += 1
            self._redo.append(self.project.snapshot())
            self._restore(self._undo.pop())

    def redo(self):
        if self._redo:
            self._dirty = True
            self._rev += 1
            self._undo.append(self.project.snapshot())
            self._restore(self._redo.pop())

    # ------------------------------------------------------------ mises a jour
    def _changed(self):
        """Un element a change (glisse, propriete...)."""
        self._after_project_change()
        self._load_props(keep_focus=True)

    def _after_project_change(self, full=False):
        if not self.timeline.dragging():
            self.project.compact()                   # pas de piste vide : elle disparait, les autres se renumerotent
        self.canvas.set_size(self.project.width, self.project.height) if full else None
        if full:
            self._load_format()
            self._load_props()
        self.timeline.invalidate()
        self.overview.update()
        self.preview.refresh()
        self._sync_scrollbar()
        if self.audio_page is not None:
            self.audio_page.refresh()

    def _load_format(self):
        self._updating = True
        i = self.cb_res.findData((self.project.width, self.project.height))
        self.cb_res.setCurrentIndex(max(0, i))
        j = self.cb_fps.findData(self.project.fps)
        self.cb_fps.setCurrentIndex(max(0, j))
        self._updating = False

    def _format_changed(self, _):
        if self._updating:
            return
        self.push_undo()
        self.project.width, self.project.height = self.cb_res.currentData()
        self.project.fps = self.cb_fps.currentData()
        self.canvas.set_size(self.project.width, self.project.height)
        self.preview.refresh()

    # ------------------------------------------------------------ selection / proprietes
    def _on_select(self, clip):
        self.sel = clip
        self._load_props()

    def _load_props(self, keep_focus=False):
        c = self.sel
        self.canvas.set_edit_clip(c)
        if hasattr(self, "cb_trtrack") and not self._updating:
            self._tr_refresh()
        self._updating = True
        self.lbl_none.setVisible(c is None)
        self.form_host.setVisible(c is not None)
        for b in (self.btn_split, self.btn_dup, self.btn_del):
            b.setEnabled(c is not None)
        self.btn_unlink.setVisible(c is not None and len(self.project.group_of(c)) > 1)
        if c is not None:
            for lab, w_, kinds in self._rows:
                on = c.kind in kinds
                lab.setVisible(on)
                w_.setVisible(on)
            self.sp_start.setValue(c.start)
            self.sp_dur.setValue(c.duration)
            self.sp_vol.setValue(c.volume_db)
            self.chk_mute.setChecked(c.mute_audio)
            if self.txt.toPlainText() != c.text:
                self.txt.setPlainText(c.text)
            self.cb_font.setCurrentText(c.font)
            self.sp_size.setValue(c.size)
            self.sp_px.setValue(c.pos_x)
            self.sp_py.setValue(c.pos_y)
            self.sp_scale.setValue(c.scale)
            self.sp_outline.setValue(c.outline)
            for btn, hexcol in ((self.btn_color, c.color), (self.btn_ocolor, c.outline_color)):
                btn.setText(hexcol.upper())
                col = QColor(hexcol)
                fg = "#101216" if (0.299 * col.red() + 0.587 * col.green() + 0.114 * col.blue()) > 150 else "#ffffff"
                btn.setStyleSheet(f"background:{hexcol}; color:{fg}; border:1px solid #888;")
        self._updating = False

    def _prop(self, attr, value):
        c = self.sel
        if self._updating or c is None:
            return
        group = self.project.group_of(c)
        if attr == "start":
            delta = max(0.0, value) - c.start
            ok = all(m.start + delta >= -1e-9 and self.project.free_at(m.track, m.start + delta, m.end + delta, ignore=group)
                     for m in group)
            if not ok:
                self._load_props()
                return
            self.push_undo(coalesce=True)
            for m in group:
                m.start = max(0.0, m.start + delta)
        elif attr == "duration":
            ddur = max(MIN_DUR, value) - c.duration
            plan = []
            for m in group:
                nd = max(MIN_DUR, m.duration + ddur)
                segs = None
                if m.segments:                                   # clip compose : on rogne / prolonge ses passages
                    segs = seg_window(m.segments, 0.0, nd, m.src_duration)
                    if segs is None:
                        self._load_props()
                        return
                elif m.kind in ("video", "audio") and m.src_duration > 0:
                    nd = min(nd, m.src_duration - m.src_in)
                if not self.project.free_at(m.track, m.start, m.start + nd, ignore=group):
                    self._load_props()
                    return
                plan.append((m, nd, segs))
            self.push_undo(coalesce=True)
            for m, nd, segs in plan:
                m.duration = nd
                if segs is not None:
                    m.segments = segs
        else:
            self.push_undo(coalesce=True)
            setattr(c, attr, value)
        self._after_project_change()
        if attr in ("start", "duration"):
            self._load_props()

    def _pick_color(self, attr="color"):
        c = self.sel
        if c is None:
            return
        col = QColorDialog.getColor(QColor(getattr(c, attr)), self.win)
        if col.isValid():
            self._prop(attr, col.name())
            self._load_props()

    # ------------------------------------------------------------ deplacer sur la video
    def _move_mode(self, on):
        self.canvas.set_edit_on(on)
        c = self.sel
        if on and c is not None and c.kind in ("video", "image", "text") and not (c.start <= self.preview.t < c.end):
            self.preview.seek(c.start + 0.01)            # on se place sur l'element pour le voir

    def _canvas_moved(self, x, y):
        c = self.sel
        if c is None:
            return
        c.pos_x, c.pos_y = round(x, 1), round(y, 1)
        self._updating = True
        self.sp_px.setValue(c.pos_x)
        self.sp_py.setValue(c.pos_y)
        self._updating = False
        self.preview._sync()

    # ------------------------------------------------------------ ajouter des elements
    def add_dialog(self, kind):
        if kind == "text":
            self.add_text()
            return
        flt = {"video": tr("ed.filter.video"), "image": tr("ed.filter.image"), "audio": tr("ed.filter.audio")}[kind]
        paths, _ = QFileDialog.getOpenFileNames(self.win, tr("ed.dlg." + kind), "", flt)
        if paths:
            self.add_files(paths, kind)

    def add_text(self):
        self.push_undo()
        c = Clip("text", "V1", self.preview.t, 4.0, text=tr("ed.text.default"), font="Arial", size=72, pos_x=50, pos_y=82)
        c.track = self.project.free_track("V", c.start, c.end, prefer="high")
        self.project.clips.append(c)
        self._select_new(c)

    def _active_stream(self, path):
        """Piste audio audible au depart : celle choisie comme 'mixee' dans l'onglet Couper si c'est le meme fichier."""
        m = self.win.media
        if m and os.path.abspath(m["path"]) == os.path.abspath(path):
            return self.win.cb_mix.currentData() or 0
        return 0

    def _audio_clips(self, path, n_audio, start, duration, name, link, src_in=0.0, src_duration=0.0):
        """Un element audio par piste audio du fichier (A1, A2, A3). Seule la piste 'mixee' est audible au depart."""
        active = self._active_stream(path)
        return [Clip("audio", f"A{k + 1}", start, duration, path=path, src_in=src_in, src_duration=src_duration,
                     name=tr("ed.audio.track", name=name, n=k + 1) if n_audio > 1 else name, audio_index=k,
                     mute_audio=(k != active), link=link)
                for k in range(n_audio)]

    def _group_start(self, tracks, wanted, duration):
        """Premier endroit libre a partir de 'wanted' sur TOUTES ces pistes en meme temps."""
        t = max(0.0, wanted)
        for _ in range(200):
            nt = max(self.project.first_free_start(tr_, t, duration) for tr_ in tracks)
            if nt <= t + 1e-9:
                break
            t = nt
        return t

    def add_files(self, paths, kind=None, track=None, at=None):
        """track / at : piste et instant ou on a depose le fichier (sinon : pistes par defaut, a la tete de lecture)."""
        for path in paths:
            k = kind or kind_from_path(path)
            if k is None:
                continue
            try:
                info = probe_media(path)
            except Exception as e:  # noqa: BLE001
                QMessageBox.warning(self.win, tr("dlg.err.title"), tr("ed.err.media", err=f"{os.path.basename(path)}: {e}"))
                continue
            name = os.path.basename(path)
            new = []
            if k == "video":
                if not self.project.clips and info["has_video"]:
                    self._adopt_format(info)
                dur = info["duration"] or 5.0
                link = Project.new_link() if info["n_audio"] else ""
                v = Clip("video", "V1", 0, dur, path=path, src_duration=dur, name=name, has_audio=info["n_audio"] > 0,
                         mute_audio=info["n_audio"] > 0, link=link)    # ses sons sont des elements A1, A2, A3
                new = [v] + self._audio_clips(path, info["n_audio"], 0, dur, name, link, 0.0, dur)
            elif k == "image":
                new = [Clip("image", "V1", 0, 5.0, path=path, name=name)]
            else:
                if info["n_audio"] == 0:
                    QMessageBox.warning(self.win, tr("dlg.err.title"), tr("ed.err.media", err=f"{name}: no audio"))
                    continue
                dur = info["duration"] or 5.0
                link = Project.new_link() if info["n_audio"] > 1 else ""
                new = self._audio_clips(path, info["n_audio"], 0, dur, name, link, 0.0, dur)
            wanted = self.preview.t if at is None else at
            if track and track[0] == "V" and k in ("video", "image"):
                new[0].track = track
            elif track and track[0] == "A" and k == "audio":
                for i, c in enumerate(new):
                    c.track = f"A{track_num(track) + i}"
            if k == "image" and not (track and track[0] == "V"):
                new[0].track = self.project.free_track("V", wanted, wanted + new[0].duration, prefer="high")
            start = self._group_start({c.track for c in new}, wanted, new[0].duration)
            self.push_undo()
            for c in new:
                c.start = start
            self.project.clips.extend(new)
            self._select_new(new[0])

    def _adopt_format(self, info):
        """La 1re video ajoutee donne sa resolution et ses images/s au projet."""
        res = (info["width"], info["height"])
        if res in RESOLUTIONS:
            self.project.width, self.project.height = res
        fps = info["fps"] or 30
        self.project.fps = 60 if fps >= 50 else 30 if fps >= 27 else 24
        self.canvas.set_size(self.project.width, self.project.height)
        self._load_format()

    def _select_new(self, c):
        self.sel = c
        self.timeline.selected = c
        self._after_project_change()
        self._load_props()
        self.timeline.invalidate()

    # ------------------------------------------------------------ edition
    def delete_selected(self):
        if self.sel is None:
            return
        if self.btn_ripple.isChecked():                  # collage automatique : on ne laisse pas de trou
            self.ripple_delete()
            return
        self.push_undo()
        ids = {m.id for m in self.project.group_of(self.sel)}
        self.project.clips = [c for c in self.project.clips if c.id not in ids]
        self.sel = None
        self.timeline.selected = None
        self._after_project_change()
        self._load_props()

    # ------------------------------------------------------------ copier / couper / coller, collage automatique
    def copy_selected(self):
        """Copie l'element choisi (et ses lies : la video avec ses sons) dans le presse-papiers du montage."""
        if self.sel is None:
            return
        group = self.project.group_of(self.sel)
        first = min(m.start for m in group)
        self._clip_buf = [(deepcopy(m), m.start - first) for m in group]
        self.win.status(tr("ed.copied", n=len(group)))

    def cut_selected(self):
        """Couper (Ctrl+X) : copie puis supprime ; avec le collage automatique, le trou est bouche."""
        if self.sel is None:
            return
        self.copy_selected()
        self.delete_selected()

    def paste_clips(self):
        """Colle a la tete de lecture, sur les memes pistes. Collage automatique : on INSERE (la suite est decalee,
        pas de chevauchement) ; sinon on pose au premier endroit libre a partir de la tete de lecture."""
        if not self._clip_buf:
            self.win.status(tr("ed.paste.empty"))
            return
        t = self.preview.t
        dur = max(off + m.duration for m, off in self._clip_buf)
        self.push_undo()
        if self.btn_ripple.isChecked():
            self._split(t, list(self.project.clips), record=False, min_dur=1e-3)
            for c in self.project.clips:
                if c.start >= t - 1e-6:
                    c.start += dur
            start = t
        else:
            start = self._group_start({m.track for m, _ in self._clip_buf}, t, dur)
        link = Project.new_link() if len(self._clip_buf) > 1 else ""
        copies = []
        for m, off in self._clip_buf:
            n = deepcopy(m)
            n.id = Clip("text", "V1", 0, 1).id
            n.start, n.link, n.origin = start + off, link, ""
            copies.append(n)
        self.project.clips.extend(copies)
        self._select_new(copies[0])
        self.win.status(tr("ed.pasted", n=len(copies)))

    def close_gaps(self, record=True, leading=True):
        """Colle les morceaux : supprime les vides ou AUCUNE piste n'a quelque chose (les pistes restent synchro).
        leading : y compris le vide avant le premier element. Renvoie le nombre de secondes retirees."""
        if not ops.find_gaps(self.project, leading):
            if record and self.project.clips:
                self.win.status(tr("st.gaps.none"))
            return 0.0
        if record:
            self.push_undo()
        n, total = ops.close_gaps(self.project, leading)
        self._after_project_change()
        self._load_props()
        if record:
            self.win.status(tr("st.gaps.closed", n=n, sec=f"{total:.1f}"))
        return total

    def _edit_finished(self):
        """Apres un deplacement / rognage a la souris : avec le collage automatique, on referme les vides entre les morceaux."""
        if self.btn_ripple.isChecked():
            self.close_gaps(record=False, leading=False)

    def cut_source_ranges(self, path, ranges):
        """Retire du montage des intervalles (secondes) du FICHIER SOURCE `path` : ils sont traduits en instants du montage
        grace aux clips de ce fichier, puis retires de toutes les pistes. Renvoie les secondes retirees."""
        merged = ops.source_to_timeline(self.project, path, ranges)
        return self.apply_cut_ranges(merged) if merged else 0.0

    def duplicate_selected(self):
        c = self.sel
        if c is None:
            return
        group = self.project.group_of(c)
        self.push_undo()
        first = min(m.start for m in group)
        last = max(m.end for m in group)
        start = self._group_start({m.track for m in group}, last, last - first)
        link = Project.new_link() if len(group) > 1 else ""
        copies = []
        for m in group:
            n = deepcopy(m)
            n.id = Clip("text", "V1", 0, 1).id
            n.start = m.start - first + start
            n.link = link
            n.origin = ""                                # une copie ne disparait pas quand on renvoie les coupes
            copies.append(n)
        self.project.clips.extend(copies)
        self._select_new(copies[group.index(c)])

    def unlink_selected(self):
        if self.sel is None or not self.sel.link:
            return
        self.push_undo()
        for m in self.project.group_of(self.sel):
            m.link = ""
        self._after_project_change()
        self._load_props()

    def split_at_playhead(self):
        t = self.preview.t
        base = self.project.group_of(self.sel) if self.sel is not None else [c for c in self.project.clips if c.start < t < c.end]
        self._split(t, base)

    def cut_at(self, t, clip):
        """Outil ciseaux : coupe a l'instant t. 'Toutes les pistes' actif : tout ce qui passe a cet instant ;
        sinon seulement l'element clique."""
        base = [c for c in self.project.clips if c.start < t < c.end] if self.project.linked else [clip]
        self._split(t, base)

    def _split(self, t, base, record=True, min_dur=MIN_DUR):
        targets = [c for c in base if c.start + min_dur <= t <= c.end - min_dur]
        if not targets:
            return
        if record:
            self.push_undo()
        ops.split_clips(self.project, t, targets, min_dur)
        if record:
            self._after_project_change()
            self._load_props()

    def apply_cut_ranges(self, ranges):
        """Retire des intervalles de temps (secondes) de TOUT le montage : on coupe toutes les pistes aux deux bouts,
        on supprime ce qui est dedans, et on rapproche la suite pour boucher le trou. Un seul Ctrl+Z annule tout.
        Renvoie le nombre de secondes retirees."""
        if not ops.plan_ranges(self.project, ranges):
            return 0.0
        self.push_undo()
        removed = ops.cut_ranges(self.project, ranges)
        self.sel = None
        self.timeline.selected = None
        self._after_project_change()
        self._load_props()
        return removed

    # ------------------------------------------------------------ passe "Couper les silences" sur le montage
    def start_silence_pass(self):
        """Prepare un fichier leger du montage (une piste son par piste) et l'ouvre dans l'onglet Couper les silences :
        on y regle et verifie les coupes, puis 'Appliquer au montage' les retire de la timeline."""
        if getattr(self, "pass_worker", None) is not None and self.pass_worker.isRunning():
            self.pass_worker.stop()
            return
        if not any(c.kind == "audio" for c in self.project.clips):
            QMessageBox.information(self.win, tr("ed.pass.title"), tr("ed.pass.nosound"))
            return
        import tempfile
        self.preview.pause()
        d = os.path.join(tempfile.gettempdir(), "autocut_pass")
        os.makedirs(d, exist_ok=True)
        for f in os.listdir(d):                                  # on ne garde pas les anciennes passes
            try:
                os.remove(os.path.join(d, f))
            except OSError:
                pass
        out = os.path.join(d, f"pass_{int(time.time())}.mp4")
        self._pass_rev = self._rev
        self.pass_worker = PassWorker(self.project, out)
        self.pass_worker.progress.connect(lambda p_: (self.bar.setValue(int(p_ * 1000)),
                                                      self.win.task_update(p_, tr("st.pass.prep", pct=f"{p_ * 100:.0f}"))))
        self.win.task_begin(tr("st.pass.prep", pct="0"), cancel=self.pass_worker.stop)
        self.pass_worker.done.connect(self._pass_ready)
        self.pass_worker.failed.connect(self._pass_failed)
        self.bar.setValue(0)
        self.bar.show()
        self.btn_pass.setText(tr("btn.cancel"))
        self.pass_worker.start()

    def _pass_end(self):
        self.win.task_end()
        self.bar.hide()
        self.btn_pass.setText(tr("ed.pass"))

    def _pass_ready(self, path):
        self._pass_end()
        self.win.begin_pass(path, self._pass_rev)

    def _pass_failed(self, msg):
        self._pass_end()
        if msg:
            QMessageBox.warning(self.win, tr("ed.pass.title"), msg)

    def trim_to_playhead(self, side):
        """side 'l' : supprime ce qui est avant la tete de lecture ; 'r' : ce qui est apres."""
        if self.sel is None:
            return
        t = self.preview.t
        group = [m for m in self.project.group_of(self.sel) if m.start + MIN_DUR <= t <= m.end - MIN_DUR]
        if not group:
            return
        self.push_undo()
        for m in group:
            local = t - m.start
            if side == "r":
                if m.segments:
                    m.segments = seg_window(m.segments, 0.0, local, 0.0)
                m.duration = local
            else:
                if m.segments:
                    m.segments = seg_window(m.segments, local, m.duration, 0.0)
                    m.src_in = m.segments[0][0]
                elif m.kind in ("video", "audio"):
                    m.src_in += local
                m.duration -= local
                m.start = t
        self._after_project_change()
        self._load_props()

    def ripple_delete(self):
        """Supprime l'element (et ses lies) puis decale la suite vers la gauche pour boucher le trou."""
        if self.sel is None:
            return
        group = self.project.group_of(self.sel)
        a, b = min(m.start for m in group), max(m.end for m in group)
        ids = {m.id for m in group}
        tracks = None if self.project.linked else {m.track for m in group}
        self.push_undo()
        self.project.clips = [c for c in self.project.clips if c.id not in ids]
        for c in self.project.clips:
            if c.start >= b - 1e-6 and (tracks is None or c.track in tracks):
                c.start = max(0.0, c.start - (b - a))
        self.sel = None
        self.timeline.selected = None
        self._after_project_change()
        self._load_props()

    def fit_selection(self):
        if self.sel is None:
            return
        group = self.project.group_of(self.sel)
        self.timeline.fit_span(min(m.start for m in group), max(m.end for m in group))

    def goto_edge(self, direction):
        """Place la tete de lecture sur la coupe / le bord precedent ou suivant."""
        edges = {0.0, self.project.duration()}
        for c in self.project.clips:
            edges.update((c.start, c.end))
            acc = 0.0
            for s0, e0 in c.segments[:-1]:
                acc += e0 - s0
                edges.add(c.start + acc)
        t = self.preview.t
        cand = sorted(x for x in edges if (x > t + 0.01 if direction > 0 else x < t - 0.01))
        if not cand:
            return
        target = cand[0] if direction > 0 else cand[-1]
        self.preview.seek(target)
        tl = self.timeline
        if not tl.scroll <= target <= tl.scroll + tl.visible_seconds():
            tl.set_scroll(target - tl.visible_seconds() * 0.3)

    def add_clips(self, clips, replace_origin=None):
        """Ajoute des elements deja prepares (par exemple les coupes de l'onglet Couper).
        replace_origin : on retire d'abord les elements qui venaient du meme envoi."""
        self.push_undo()
        if replace_origin:
            self.project.clips = [c for c in self.project.clips if c.origin != replace_origin]
            self.sel = None
            self.timeline.selected = None
        self.project.clips.extend(clips)
        self._after_project_change()
        self.timeline.fit_all()

    # ------------------------------------------------------------ projet
    def new_project(self):
        self.preview.pause()
        self.push_undo()
        self.project.restore(Project().snapshot())
        self.project.path = self.project.name = self.project.source = ""
        self.sel = None
        self.timeline.selected = None
        self.preview.seek(0)
        self._after_project_change(full=True)
        self._dirty = False                                     # nouveau montage vide : rien a sauvegarder
        self.clear_autosave()

    def _default_dir(self):
        d = os.path.join(settings.projects_root(), "Montage")
        os.makedirs(d, exist_ok=True)
        return d

    def save_project(self):
        """Enregistre le projet dans son dossier. Premier enregistrement : on demande un nom (un dossier est cree)."""
        p = self.project
        if not p.path:
            root = settings.projects_root()
            name, ok = QInputDialog.getText(self.win, tr("np.title"), tr("np.name"), text=projects.unique_name(root, tr("ed.project.name")))
            name = name.strip()
            if not ok or not name:
                return
            if projects.exists(root, name):
                QMessageBox.warning(self.win, tr("np.title"), tr("np.exists", name=name))
                return
            p.name, p.path = name, projects.project_file(root, name)
        try:
            os.makedirs(os.path.dirname(p.path), exist_ok=True)
            p.save(p.path)
        except OSError as e:
            QMessageBox.warning(self.win, tr("dlg.err.title"), str(e))
            return
        self._dirty = False
        self.clear_autosave()
        self.win._update_title()
        self.win.status(tr("ed.saved", path=p.path))

    # ------------------------------------------------------------ sauvegarde automatique
    def enable_autosave(self, interval_ms=20000):
        """Copie le montage toutes les 20 s s'il a change (jamais sur le fichier du projet : un fichier a part)."""
        self._autosave_on = True
        self._autosave_timer = QTimer(self)
        self._autosave_timer.setInterval(interval_ms)
        self._autosave_timer.timeout.connect(self._autosave_tick)
        self._autosave_timer.start()

    def _autosave_tick(self, force=False):
        if not self._autosave_on or not self._dirty or not self.project.clips:
            return
        if self._rev == self._saved_rev and not force:
            return
        if self.project.path:                                # projet nomme : on enregistre directement dedans
            try:
                self.project.save(self.project.path)
                self._dirty = False
                self._saved_rev = self._rev
                self.clear_autosave()
            except OSError:
                pass
            return
        path = autosave_path()
        try:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            data = dict(self.project.snapshot(), version=1, saved_at=time.time())
            with open(path + ".tmp", "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False)
            os.replace(path + ".tmp", path)                  # ecriture "tout ou rien"
            self._saved_rev = self._rev
        except OSError:
            pass

    def clear_autosave(self):
        try:
            os.remove(autosave_path())
        except OSError:
            pass
        self._saved_rev = -1

    def on_close(self):
        """A la fermeture : on garde la sauvegarde si le montage n'a pas ete enregistre, sinon on la supprime."""
        if not self._autosave_on:
            return
        if self._dirty and self.project.clips:
            self._autosave_tick(force=True)
        elif not self._dirty:
            self.clear_autosave()

    def offer_restore(self):
        """Au lancement : propose de reprendre le montage de la derniere session s'il n'a pas ete enregistre."""
        path = autosave_path()
        if not os.path.isfile(path):
            return False
        try:
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
        except (OSError, ValueError):
            self.clear_autosave()
            return False
        clips = data.get("clips") or []
        files = [c for c in clips if c.get("kind") in ("video", "image", "audio")]
        missing = [c for c in files if not os.path.isfile(c.get("path", ""))]
        if not clips or (files and len(missing) == len(files)):
            self.clear_autosave()
            return False
        when = time.strftime("%Y-%m-%d %H:%M", time.localtime(data.get("saved_at", os.path.getmtime(path))))
        msg = tr("ed.restore.msg", n=len(clips), when=when)
        if missing:
            msg += tr("ed.restore.missing", n=len(missing))
        ans = QMessageBox.question(self.win, tr("ed.restore.title"), msg, QMessageBox.Yes | QMessageBox.No,
                                   QMessageBox.Yes)
        if ans != QMessageBox.Yes:
            self.clear_autosave()
            return False
        try:
            self.preview.pause()
            self.push_undo()
            self.project.load(path)
        except Exception as e:  # noqa: BLE001
            QMessageBox.warning(self.win, tr("dlg.err.title"), str(e))
            return False
        self.project.path = ""                                  # ce n'est pas un fichier de projet : "Enregistrer" demandera un nom
        self.sel = None
        self.timeline.selected = None
        self.preview.seek(0)
        self._after_project_change(full=True)
        self.timeline.fit_all()
        self._dirty = True
        return True

    def open_project(self, path=None):
        if not path:
            path, _ = QFileDialog.getOpenFileName(self.win, tr("ed.open.title"), self._default_dir(),
                                                  "Auto Cut (*.autocut.json)")
        if not path:
            return
        try:
            self.preview.pause()
            self.push_undo()
            self.project.load(path)
        except Exception as e:  # noqa: BLE001
            QMessageBox.warning(self.win, tr("dlg.err.title"), str(e))
            return
        self.sel = None
        self.timeline.selected = None
        self.preview.seek(0)
        self._after_project_change(full=True)
        self.timeline.fit_all()
        self._dirty = False                                     # vient d'un fichier : rien a sauvegarder pour l'instant
        self.clear_autosave()

    # ------------------------------------------------------------ transcription, recherche dans le texte
    def _tr_source(self):
        """Fichier video dont on parle : celui de l'element choisi, sinon le premier du montage."""
        c = self.sel
        if c is not None and c.kind in ("video", "audio") and c.path:
            return os.path.abspath(c.path)
        files = aitools.source_files(self.project)
        return files[0] if files else None

    def _tr_tracks(self, path):
        if path not in self._probed:
            try:
                self._probed[path] = probe_media(path)
            except Exception:  # noqa: BLE001
                self._probed[path] = None
        return self._probed[path]

    def _tr_refresh(self):
        """Met a jour le texte d'etat et la liste des pistes audio du groupe Transcription."""
        path = self._tr_source()
        self.cb_trtrack.blockSignals(True)
        self.cb_trtrack.clear()
        if path is None:
            self.lbl_tr.setText(tr("tr.status.nofile"))
            self.btn_transcribe.setEnabled(False)
        else:
            info = self._tr_tracks(path)
            n = info["n_audio"] if info else 1
            used = sorted({c.audio_index for c in self.project.clips if c.kind == "audio" and os.path.abspath(c.path) == path})
            voice = used[-1] if used else max(0, n - 1)
            for k in range(max(1, n)):
                self.cb_trtrack.addItem(tr("track.item", n=k + 1, c=2), k)
            self.cb_trtrack.setCurrentIndex(min(voice, self.cb_trtrack.count() - 1))
            self.btn_transcribe.setEnabled(True)
            data = self._tr_data(path)
            if data is not None:
                nw = sum(len(s["words"]) for s in data["segments"])
                self.lbl_tr.setText(tr("tr.status.done", n=nw, name=os.path.basename(path)))
            elif not transcribe.available():
                self.lbl_tr.setText(tr("tr.status.noai"))
            else:
                self.lbl_tr.setText(tr("tr.status.none", name=os.path.basename(path)))
        self.cb_trtrack.blockSignals(False)

    def _tr_data(self, path):
        for i in range(self.cb_trtrack.count() or 1):
            try:
                d = transcribe.get_cached(path, i)
            except OSError:
                d = None
            if d is not None:
                return d
        return None

    def start_transcription(self):
        w = getattr(self, "_tr_worker", None)
        if w is not None and w.isRunning():
            w.stop()
            return
        path = self._tr_source()
        if path is None:
            return
        if not transcribe.available():
            QMessageBox.information(self.win, tr("tr.title"), tr("tr.missing", dir=transcribe.ai_dir()))
            return
        idx = self.cb_trtrack.currentData() or 0
        self._tr_worker = FuncWorker(lambda prog, stop: transcribe.transcribe(path, idx, progress=prog, should_stop=stop))
        self._tr_worker.progress.connect(lambda p_: self.win.task_update(p_, tr("tr.running", pct=f"{p_ * 100:.0f}")))
        self._tr_worker.done.connect(self._tr_done)
        self._tr_worker.failed.connect(self._tr_failed)
        self.win.task_begin(tr("tr.running", pct="0"), cancel=self._tr_worker.stop)
        self.btn_transcribe.setText(tr("btn.cancel"))
        self._tr_worker.start()

    def _tr_end(self):
        self.win.task_end()
        self.btn_transcribe.setText(tr("tr.btn"))

    def _tr_done(self, data):
        self._tr_end()
        self._tr_refresh()
        self.win.status(tr("tr.status.done", n=sum(len(s["words"]) for s in data["segments"]), name=""))
        if self.ed_search.text().strip():
            self.tr_search()

    def _tr_failed(self, msg):
        self._tr_end()
        if msg:
            QMessageBox.warning(self.win, tr("tr.title"), tr("tr.err", err=msg[-300:]))

    def _tr_show(self, matches, kind):
        self._tr_matches, self._tr_kind = matches, kind
        self.lst_res.clear()
        for m in matches:
            it = QListWidgetItem(f"{fmt_t(m['start'])}   {m['context']}")
            it.setData(Qt.UserRole, m)
            self.lst_res.addItem(it)
        self.lbl_trcount.setText(tr("tr.count", n=len(matches)) if matches else "")

    def tr_search(self):
        q = self.ed_search.text().strip()
        if not q:
            self._tr_show([], "search")
            return
        self._tr_show(aitools.find_in_project(self.project, q), "search")

    def tr_fillers(self):
        if self._tr_source() is None:
            return
        ms = aitools.find_in_project(self.project, fillers=True)
        self._tr_show(ms, "fillers")
        if not ms:
            self.win.status(tr("tr.none"))

    def _tr_goto(self, item):
        m = item.data(Qt.UserRole)
        if m:
            self.preview.seek(m["start"])
            tl = self.timeline
            if not tl.scroll <= m["start"] <= tl.scroll + tl.visible_seconds():
                tl.set_scroll(m["start"] - tl.visible_seconds() * 0.3)

    def tr_cut(self, everything):
        """Coupe du montage les passages de la liste (la selection, ou tous) : toutes les pistes, sans laisser de vide."""
        if everything or not self.lst_res.selectedItems():
            ms = list(self._tr_matches)
        else:
            ms = [it.data(Qt.UserRole) for it in self.lst_res.selectedItems()]
        if not ms:
            self.win.status(tr("tr.none"))
            return
        self.push_undo()
        removed = aitools.cut_matches(self.project, ms)
        self.sel = None
        self.timeline.selected = None
        self._after_project_change()
        self._load_props()
        self.win.status(tr("tr.cut.done", n=len(ms), sec=f"{removed:.1f}"))
        if self._tr_kind == "fillers":
            self.tr_fillers()
        else:
            self.tr_search()

    def export_for_ai(self):
        """Ecrit le resume du montage + une planche d'images dans le dossier du projet (sous-dossier ia) : de quoi
        laisser une IA comprendre le montage sans l'ouvrir."""
        if not self.project.clips:
            return
        base = os.path.dirname(self.project.path) if self.project.path else self._default_dir()
        folder = os.path.join(base, "ia")
        proj = deepcopy(self.project)
        w = FuncWorker(lambda prog, stop: aitools.export_for_ai(proj, folder))
        self._ai_worker = w
        w.done.connect(lambda res: (self.win.task_end(), self._ai_done(folder, res)))
        w.failed.connect(lambda m: (self.win.task_end(), QMessageBox.warning(self.win, tr("tr.title"), m[-300:])))
        self.win.task_begin(tr("tr.ai.running"), indeterminate=True)
        w.start()

    def _ai_done(self, folder, res):
        box = QMessageBox(QMessageBox.Information, tr("tr.title"), tr("tr.ai.done", folder=folder), QMessageBox.NoButton, self.win)
        open_b = box.addButton(tr("btn.openfolder"), QMessageBox.AcceptRole)
        box.addButton(tr("btn.close"), QMessageBox.RejectRole)
        box.exec()
        if box.clickedButton() is open_b and hasattr(os, "startfile"):
            os.startfile(folder)

    # ------------------------------------------------------------ export XML (DaVinci Resolve)
    def export_xml(self, path=None):
        """Ecrit le montage au format XML (Final Cut 7) : Fichier > Importer > Timeline dans DaVinci Resolve."""
        if not self.project.clips:
            QMessageBox.information(self.win, tr("ed.xml.title"), tr("ed.xml.empty"))
            return
        base = self.project.name or tr("ed.project.name")
        if not path:
            default = unique_path(os.path.dirname(self.project.path) if self.project.path else self._default_dir(), base)
            path, _ = QFileDialog.getSaveFileName(self.win, tr("ed.xml.title"), default, "XML (*.xml)")
        if not path:
            return
        try:
            xml, skipped = build_project_xml(self.project, base)
            with open(path, "w", encoding="utf-8") as f:
                f.write(xml)
        except OSError as e:
            QMessageBox.warning(self.win, tr("dlg.err.title"), str(e))
            return
        msg = tr("ed.xml.done", path=path) + (tr("ed.xml.skipped", n=skipped) if skipped else "")
        self.win.status(tr("st.export.done", path=path))
        box = QMessageBox(QMessageBox.Information, tr("dlg.done.title"), msg, QMessageBox.NoButton, self.win)
        open_b = box.addButton(tr("btn.openfolder"), QMessageBox.AcceptRole)
        box.addButton(tr("btn.close"), QMessageBox.RejectRole)
        box.exec()
        if box.clickedButton() is open_b and hasattr(os, "startfile"):
            os.startfile(os.path.dirname(path))

    # ------------------------------------------------------------ export video
    def export_video(self):
        if self.worker is not None and self.worker.isRunning():
            self.worker.stop()
            return
        if not self.project.clips:
            QMessageBox.information(self.win, tr("ed.export"), tr("ed.export.empty"))
            return
        self.preview.pause()
        d = os.path.dirname(self.project.path) if self.project.path else self._default_dir()     # dossier du projet ouvert
        path, _ = QFileDialog.getSaveFileName(self.win, tr("ed.export.title"),
                                              unique_path(d, self.project.name or tr("ed.project.name"), ".mp4"), "MP4 (*.mp4)")
        if not path:
            return
        self.worker = RenderWorker(self.project, path)
        self.worker.progress.connect(self._on_progress)
        self.worker.done.connect(self._export_done)
        self.worker.failed.connect(self._export_failed)
        self.bar.setValue(0)
        self.bar.show()
        self.win.task_begin(tr("ed.export.progress", pct="0"), cancel=self.worker.stop)
        self.btn_export.setText(tr("btn.cancel"))
        self.worker.start()

    def _on_progress(self, p):
        self.bar.setValue(int(p * 1000))
        self.win.status(tr("ed.export.progress", pct=f"{p * 100:.0f}"))
        self.win.task_update(p, tr("ed.export.progress", pct=f"{p * 100:.0f}"))

    def _end_export(self):
        self.win.task_end()
        self.bar.hide()
        self._r(self.btn_export, "ed.export")

    def _export_done(self, path):
        self._end_export()
        self.win.status(tr("ed.export.done", path=path))
        info = self.worker.info if self.worker is not None else {}
        text = tr("ed.export.done", path=path)
        if info.get("after"):
            b = info["after"]
            text += "\n\n" + tr("ed.export.loud", target=f"{self.project.norm_lufs:.1f}", after=f"{b['I']:.1f}",
                                  tp=f"{b['TP']:.1f}")
        if self.audio_page is not None:
            self.audio_page.show_result(info)
        box = QMessageBox(QMessageBox.Information, tr("dlg.done.title"), text, QMessageBox.NoButton, self.win)
        open_b = box.addButton(tr("btn.openfolder"), QMessageBox.AcceptRole)
        box.addButton(tr("btn.close"), QMessageBox.RejectRole)
        box.exec()
        if box.clickedButton() is open_b and hasattr(os, "startfile"):
            os.startfile(os.path.dirname(path))

    def _export_failed(self, msg):
        self._end_export()
        if msg:
            self.win.status(tr("ed.export.fail"))
            QMessageBox.critical(self.win, tr("dlg.exporterr.title"), msg)

    # ------------------------------------------------------------ glisser-deposer de fichiers
    def dragEnterEvent(self, e):
        if e.mimeData().hasUrls():
            e.acceptProposedAction()

    def dropEvent(self, e):
        paths = [u.toLocalFile() for u in e.mimeData().urls() if u.isLocalFile()]
        paths = [p for p in paths if kind_from_path(p)]
        tl = self.timeline
        pt = tl.mapFrom(self, e.position().toPoint())
        if tl.rect().contains(pt) and pt.y() >= 24:       # depose sur une piste (ou sur "nouvelle piste")
            track, t = tl.drop_target(pt.x(), pt.y())
            self.add_files(paths, track=track, at=t)
        else:
            self.add_files(paths)

    def shutdown(self):
        self.on_close()
        pw = getattr(self, "pass_worker", None)
        if pw is not None and pw.isRunning():
            pw.stop()
            pw.wait(3000)
        for w_ in (getattr(self, "_tr_worker", None), getattr(self, "_ai_worker", None)):
            if w_ is not None and w_.isRunning():
                w_.stop()
                w_.wait(3000)
        if self.worker is not None and self.worker.isRunning():
            self.worker.stop()
            self.worker.wait(3000)
        self.preview.shutdown()
