"""Timeline du mini montage : des pistes, des elements qu'on deplace, qu'on rogne, qu'on selectionne.
Les elements lies (une video et ses pistes audio) bougent et se rognent ensemble.
Un clip compose (les passages gardes d'une video) est UN seul element, avec des reperes aux coupes.
Les pistes n'existent que si elles contiennent quelque chose : on en cree une en deposant un element sur la
bande "nouvelle piste". Chaque piste a ses boutons Solo / Muet / Miniatures-forme d'onde et sa hauteur."""
import numpy as np
from PySide6.QtCore import QEvent, QLineF, QPointF, QRectF, QSize, Qt, Signal
from PySide6.QtGui import QColor, QFont, QLinearGradient, QPainter, QPainterPath, QPen, QPixmap
from PySide6.QtWidgets import QScrollBar, QToolTip, QWidget

from .i18n import tr
from .mediacache import PEAK_RATE, get_cache
from .project import MIN_DUR, ROW_H, kind_of_track, seg_window, track_num
from .theme import col

LABEL_W = 104
RULER_H = 24
STRIP_H = 22                         # bande "nouvelle piste" (en haut des images, en bas des sons)
MIN_ROW_H, MAX_ROW_H = 14, 240
EDGE = 7
SNAP_PX = 8
MIN_PPS, MAX_PPS = 0.02, 600.0
PAD_PX = 40                          # vide laisse a gauche du debut et a droite de la fin
VBAR_W = 14


def _fg(c):
    """Noir ou blanc, selon la couleur de fond."""
    lum = 0.299 * c.red() + 0.587 * c.green() + 0.114 * c.blue()
    return QColor("#101216") if lum > 150 else QColor("#ffffff")


def clip_color(c):
    if c.kind == "audio" and c.mute_audio:
        return col("muted", 150)
    return {"video": col("accent"), "image": col("accent_soft"), "text": col("handle"), "audio": col("wave")}[c.kind]


def src_times(c, ts):
    """Instants du FICHIER SOURCE pour un tableau d'instants de la timeline (version rapide de project.src_at)."""
    local = ts - c.start
    if not c.segments:
        return c.src_in + local
    lens = np.array([e - s for s, e in c.segments])
    cum = np.concatenate(([0.0], np.cumsum(lens)))
    starts = np.array([s for s, _ in c.segments])
    idx = np.clip(np.searchsorted(cum, local, side="right") - 1, 0, len(lens) - 1)
    return starts[idx] + np.clip(local - cum[idx], 0.0, lens[idx])


class TimelineWidget(QWidget):
    seekRequested = Signal(float)        # clic dans le vide / sur la regle : position en secondes
    selectionChanged = Signal(object)    # Clip ou None
    clipsChanged = Signal()              # un element a ete deplace / rogne
    editStarted = Signal()               # juste avant la 1re modification d'un glisser (pour Ctrl+Z)
    viewChanged = Signal()
    cutRequested = Signal(float, object)  # outil ciseaux : instant (s), element clique
    trackToggled = Signal(str, str)      # piste, "solo" | "mute" | "media"
    editFinished = Signal()              # un deplacement / rognage vient d'etre lache (collage automatique des vides)

    def __init__(self, project, parent=None):
        super().__init__(parent)
        self.project = project
        self.pps = 40.0                  # pixels par seconde
        self.scroll = 0.0                # temps affiche tout a gauche (s)
        self.voff = 0                    # decalage vertical des pistes (px)
        self.playhead = 0.0
        self.selected = None             # Clip
        self._drag = None
        self._cache = None               # dessin de tout sauf la tete de lecture (refait seulement si besoin)
        self._cache_key = None
        self._dirty = True
        self.tool = "select"             # "select" ou "cut"
        self.magnet = True               # aimantation sur les bords, la tete de lecture et 0
        self.hover_x = None              # position de la souris (guide de l'outil ciseaux)
        self._rows, self._ymap, self._content_h, self._div_y = [], {}, 0, 0
        self._layout_key = None
        self._frozen = False             # pendant qu'on deplace un element, les pistes ne bougent pas
        self.hover_clip = None           # element sous la souris (mis en valeur)
        self.snap_t = None               # instant sur lequel l'aimant vient de coller (trait d'accent pendant le glisser)
        self.setMouseTracking(True)
        self.setMinimumHeight(RULER_H + 2 * STRIP_H + 60)
        self.setFocusPolicy(Qt.ClickFocus)
        self.vbar = QScrollBar(Qt.Vertical, self)
        self.vbar.valueChanged.connect(self._vbar_moved)
        self.vbar.hide()
        get_cache().changed.connect(self.invalidate)

    def invalidate(self):
        """Le contenu a change : il faut redessiner les pistes (pas seulement la tete de lecture)."""
        self._dirty = True
        self.update()

    def dragging(self):
        return isinstance(self._drag, dict) and self._drag.get("started", False)

    # ---------------------------------------------------------------- pistes (dessin en lignes)
    def row_h(self, track):
        return int(min(MAX_ROW_H, max(MIN_ROW_H, self.project.track_h.get(track, ROW_H))))

    def _ensure_layout(self):
        """Lignes affichees, de haut en bas : bande 'nouvelle piste video', pistes images, pistes sons, bande
        'nouvelle piste son'. Recalcule seulement si les pistes ou leurs hauteurs ont change."""
        if self._frozen and self._rows:
            return
        p = self.project
        vt, at = p.visual_tracks(), p.audio_tracks()
        key = (tuple(vt), tuple(at), tuple(self.row_h(t) for t in vt + at))
        if key == self._layout_key:
            return
        self._layout_key = key
        rows, y = [], 0
        rows.append(dict(track=f"V{track_num(vt[-1]) + 1 if vt else 1}", y=y, h=STRIP_H, ghost=True))
        y += STRIP_H
        for t in reversed(vt):
            h = self.row_h(t)
            rows.append(dict(track=t, y=y, h=h, ghost=False))
            y += h
        self._div_y = y
        for t in at:
            h = self.row_h(t)
            rows.append(dict(track=t, y=y, h=h, ghost=False))
            y += h
        rows.append(dict(track=f"A{track_num(at[-1]) + 1 if at else 1}", y=y, h=STRIP_H, ghost=True))
        y += STRIP_H
        self._rows, self._content_h = rows, y
        self._ymap = {r["track"]: r for r in rows}

    def content_height(self):
        self._ensure_layout()
        return self._content_h

    def _view_h(self):
        return max(1, self.height() - RULER_H)

    def _clamp_voff(self):
        self._ensure_layout()
        self.voff = int(min(max(0, self.voff), max(0, self._content_h - self._view_h())))

    def _update_vbar(self):
        more = max(0, self._content_h - self._view_h())
        self.vbar.blockSignals(True)
        self.vbar.setRange(0, more)
        self.vbar.setPageStep(self._view_h())
        self.vbar.setValue(self.voff)
        self.vbar.blockSignals(False)
        self.vbar.setVisible(more > 0)

    def _vbar_moved(self, v):
        self.voff = v
        self.invalidate()

    def set_voff(self, v):
        self.voff = v
        self._clamp_voff()
        self._update_vbar()
        self.invalidate()

    def _t2x(self, t):
        return LABEL_W + (t - self.scroll) * self.pps

    def _x2t(self, x):
        return (x - LABEL_W) / self.pps + self.scroll

    def visible_seconds(self):
        return max(0.5, (self.width() - LABEL_W) / self.pps)

    def _row_rect(self, track):
        row = self._ymap.get(track)
        if row is None:
            return QRectF(LABEL_W, -1000.0, max(1, self.width() - LABEL_W), 10.0)
        return QRectF(LABEL_W, RULER_H + row["y"] - self.voff, max(1, self.width() - LABEL_W), row["h"])

    def _track_at(self, y):
        if y < RULER_H:
            return None
        yy = y - RULER_H + self.voff
        for r in self._rows:
            if r["y"] <= yy < r["y"] + r["h"]:
                return r["track"]
        return None

    def _clip_rect(self, c):
        r = self._row_rect(c.track)
        x0, x1 = self._t2x(c.start), self._t2x(c.end)
        inset = 3 if r.height() >= 30 else 1
        return QRectF(x0, r.top() + inset, max(2.0, x1 - x0), r.height() - 2 * inset)

    def _clip_at(self, x, y):
        if x < LABEL_W:
            return None
        for c in reversed(self.project.clips):
            if self._clip_rect(c).adjusted(-2, 0, 2, 0).contains(x, y):
                return c
        return None

    def _btn_rects(self, track):
        """Boutons Solo / Muet / Miniatures de la colonne de gauche."""
        r = self._row_rect(track)
        bh = max(9.0, min(20.0, r.height() - 4))
        y = r.center().y() - bh / 2
        return {k: QRectF(34 + i * (bh + 3), y, bh, bh) for i, k in enumerate(("solo", "mute", "media"))}

    def _label_hit(self, x, y):
        if x >= LABEL_W or y < RULER_H:
            return None
        for r in self._rows:
            if r["ghost"]:
                continue
            for what, rect in self._btn_rects(r["track"]).items():
                if rect.contains(x, y):
                    return r["track"], what
        return None

    def _boundary_at(self, y):
        """Piste dont le bord du bas est sous la souris (pour la redimensionner)."""
        if y < RULER_H:
            return None
        for r in self._rows:
            if not r["ghost"] and abs(RULER_H + r["y"] + r["h"] - self.voff - y) <= 3:
                return r["track"]
        return None

    # ---------------------------------------------------------------- vue
    def scroll_limits(self):
        """Debut de vue minimum / maximum : on peut aller un peu avant 0 et un peu apres la fin (un peu de vide)."""
        pad = PAD_PX / self.pps
        lo = -pad
        return lo, max(lo, self.project.duration() + pad - self.visible_seconds())

    def _clamp_scroll(self):
        lo, hi = self.scroll_limits()
        self.scroll = min(max(lo, self.scroll), hi)

    def fit_range(self):
        """Partie du temps montree par 'tout afficher' : le montage entier avec un peu de vide de chaque cote."""
        total = max(self.project.duration(), 5.0)
        pps = max(1.0, self.width() - LABEL_W - 2 * PAD_PX) / total
        return -PAD_PX / pps, total + PAD_PX / pps

    def select(self, clip, emit=True):
        self.selected = clip
        self.invalidate()
        if emit:
            self.selectionChanged.emit(clip)

    def set_playhead(self, t, follow=False):
        self.playhead = t
        if follow and not (self.scroll <= t <= self.scroll + self.visible_seconds() * 0.95):
            self.scroll = t - self.visible_seconds() * 0.1
            self._clamp_scroll()
            self.viewChanged.emit()
            self.invalidate()
        else:
            self.update()                # seulement la tete de lecture : le reste vient du cache

    def set_scroll(self, s):
        self.scroll = s
        self._clamp_scroll()
        self.viewChanged.emit()
        self.invalidate()

    def set_view(self, scroll, seconds):
        """Fixe la partie visible : debut (s) et largeur (s). Sert a la barre de vue d'ensemble."""
        seconds = max(0.5, seconds)
        self.pps = min(MAX_PPS, max(MIN_PPS, (self.width() - LABEL_W) / seconds))
        self.scroll = scroll
        self._clamp_scroll()
        self.viewChanged.emit()
        self.invalidate()

    def fit_all(self):
        lo, hi = self.fit_range()
        self.pps = min(MAX_PPS, max(MIN_PPS, (self.width() - LABEL_W) / (hi - lo)))
        self.scroll = lo
        self._clamp_scroll()
        self.viewChanged.emit()
        self.invalidate()

    def fit_span(self, a, b):
        """Montre [a, b] (secondes) en grand, avec un peu de vide autour."""
        w = max(b - a, 0.5)
        self.set_view(a - w * 0.1, w * 1.2)

    def zoom(self, factor, center_t=None):
        t = max(0.0, self.playhead if center_t is None else center_t)
        ratio = min(1.0, max(0.0, (t - self.scroll) / self.visible_seconds()))
        self.pps = min(MAX_PPS, max(MIN_PPS, self.pps * factor))
        self.scroll = t - ratio * self.visible_seconds()
        self._clamp_scroll()
        self.viewChanged.emit()
        self.invalidate()

    def wheelEvent(self, e):
        d = e.angleDelta().y() or e.angleDelta().x()
        if e.modifiers() & Qt.ShiftModifier:
            self.set_scroll(self.scroll - d / 120 * self.visible_seconds() * 0.1)
        elif e.modifiers() & Qt.AltModifier or e.position().x() < LABEL_W:
            self.set_voff(self.voff - int(d / 120 * 40))       # Alt + molette (ou molette sur les noms de pistes)
        else:
            self.zoom(1.25 if d > 0 else 0.8, self._x2t(max(e.position().x(), LABEL_W)))

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self.vbar.setGeometry(self.width() - VBAR_W, RULER_H, VBAR_W, max(1, self.height() - RULER_H))
        self._clamp_scroll()
        self._clamp_voff()
        self._update_vbar()
        self._dirty = True

    def event(self, e):
        if e.type() == QEvent.ToolTip:
            self._ensure_layout()
            hit = self._label_hit(e.pos().x(), e.pos().y())
            if hit:
                track, what = hit
                key = {"solo": "tl.solo", "mute": "tl.mute"}.get(what) or ("tl.media_v" if track[0] == "V" else "tl.media_a")
                QToolTip.showText(e.globalPos(), tr(key), self)
            else:
                c = self._clip_at(e.pos().x(), e.pos().y())
                if c is not None:
                    kind = {"video": "▶", "image": "▣", "text": "T", "audio": "♪"}[c.kind]
                    QToolTip.showText(e.globalPos(), f"{kind} {c.label()}\n{c.start:.2f} s → {c.end:.2f} s  ({c.duration:.2f} s)", self)
                else:
                    QToolTip.hideText()
            return True
        return super().event(e)

    # ---------------------------------------------------------------- dessin
    def paintEvent(self, _):
        self._ensure_layout()
        self._clamp_scroll()
        self._clamp_voff()
        self._update_vbar()
        dpr = self.devicePixelRatioF()
        key = (self.width(), self.height(), dpr)
        if self._dirty or self._cache is None or self._cache_key != key:
            pm = QPixmap(QSize(int(self.width() * dpr), int(self.height() * dpr)))
            pm.setDevicePixelRatio(dpr)
            pm.fill(Qt.transparent)
            q = QPainter(pm)
            q.setFont(self.font())
            self._paint_static(q, self.width(), self.height())
            q.end()
            self._cache, self._cache_key, self._dirty = pm, key, False
        p = QPainter(self)
        p.drawPixmap(0, 0, self._cache)
        if self.snap_t is not None:                              # l'aimant vient de coller ici
            sx = self._t2x(self.snap_t)
            if sx >= LABEL_W:
                p.setPen(QPen(col("accent_soft"), 1, Qt.DashLine))
                p.drawLine(QPointF(sx, RULER_H), QPointF(sx, self.height()))
        x = self._t2x(self.playhead)
        if x >= LABEL_W:
            h = self.height()
            p.setPen(QPen(col("playhead"), 1.5))
            p.drawLine(QPointF(x, RULER_H), QPointF(x, h))
            p.setRenderHint(QPainter.Antialiasing, True)
            p.setBrush(col("playhead"))
            p.setPen(Qt.NoPen)
            p.drawRoundedRect(QRectF(x - 6, 2, 12, RULER_H - 6), 3, 3)          # poignee arrondie dans la regle
            p.drawPolygon([QPointF(x - 5, RULER_H - 5), QPointF(x + 5, RULER_H - 5), QPointF(x, RULER_H)])
            p.setRenderHint(QPainter.Antialiasing, False)
        if self.tool == "cut" and self.hover_x is not None and self.hover_x >= LABEL_W:
            p.setPen(QPen(col("text", 200), 1, Qt.DashLine))
            p.drawLine(QPointF(self.hover_x, RULER_H), QPointF(self.hover_x, self.height()))

    def _paint_static(self, p, w, h):
        p.fillRect(0, 0, w, h, col("wave_bg"))
        body = QRectF(0, RULER_H, w, h - RULER_H)
        p.setClipRect(body)
        small = QFont(self.font())
        small.setPointSizeF(max(7.0, self.font().pointSizeF() - 1.5))
        for i, row in enumerate(self._rows):                  # fonds des pistes
            r = self._row_rect(row["track"])
            if r.bottom() < RULER_H or r.top() > h:
                continue
            if row["ghost"]:
                p.fillRect(r, col("header", 120))
                p.setPen(QPen(col("border"), 1, Qt.DashLine))
                p.setBrush(Qt.NoBrush)
                p.drawRect(r.adjusted(3, 2, -VBAR_W, -2))
                p.setFont(small)
                p.setPen(col("muted"))
                p.drawText(r.adjusted(3, 0, -VBAR_W, 0), Qt.AlignCenter,
                           tr("tl.newv" if row["track"][0] == "V" else "tl.newa"))
            else:
                p.fillRect(r, col("card") if i % 2 == 0 else col("field"))
        self._grid(p, w, h)
        # elements (dessines seulement a droite de la colonne des noms de pistes)
        p.setClipRect(QRectF(LABEL_W, RULER_H, w - LABEL_W, h - RULER_H))
        group = {c.id for c in self.project.group_of(self.selected)} if self.selected is not None else set()
        font_main = QFont(self.font())
        p.setFont(font_main)
        for c in self.project.clips:
            r = self._clip_rect(c)
            if r.right() < LABEL_W or r.left() > w or r.bottom() < RULER_H or r.top() > h:
                continue
            base = clip_color(c)
            active = self.project.track_active(c.track)
            p.setOpacity(1.0 if active else 0.35)
            if c is self.selected:
                pen = QPen(col("text"), 2)
            elif c.id in group:
                pen = QPen(col("text", 150), 1.5)
            else:
                pen = QPen(base.darker(150), 1)
            p.setRenderHint(QPainter.Antialiasing, True)
            g = QLinearGradient(r.topLeft(), r.bottomLeft())             # degrade leger : le clip a du relief
            hov = c is self.hover_clip
            g.setColorAt(0.0, base.lighter(128 if hov else 112))
            g.setColorAt(1.0, base.darker(105) if not hov else base.lighter(105))
            p.setPen(pen)
            p.setBrush(g)
            p.drawRoundedRect(r, 6, 6)
            p.setRenderHint(QPainter.Antialiasing, False)
            media = self.project.track_media.get(c.track, True) and r.height() >= 24
            shown = False
            if media:
                shown = self._paint_media(p, c, r, base, w)
                p.setRenderHint(QPainter.Antialiasing, True)
                p.setPen(pen)
                p.setBrush(Qt.NoBrush)
                p.drawRoundedRect(r, 6, 6)
                p.setRenderHint(QPainter.Antialiasing, False)
            if len(c.segments) > 1 and r.width() > 30:        # reperes aux endroits des coupes
                p.setPen(QPen(col("wave_bg", 200) if not shown else QColor(255, 255, 255, 190), 1))
                acc = 0.0
                for s0, e0 in c.segments[:-1]:
                    acc += e0 - s0
                    x = self._t2x(c.start + acc)
                    if r.left() + 2 < x < r.right() - 2 and LABEL_W < x < w:
                        p.drawLine(QPointF(x, r.top() + 2), QPointF(x, r.bottom() - 2))
            if r.width() > 24 and r.height() >= 13:
                p.save()
                p.setClipRect(r.adjusted(4, 0, -4, 0).intersected(QRectF(LABEL_W, 0, w, h)))
                icon = {"video": "▶ ", "image": "▣ ", "text": "T ", "audio": "♪ "}[c.kind]
                extra = f"  ({len(c.segments)} ✂)" if len(c.segments) > 1 else ""
                text = icon + c.label() + extra
                tr_ = r.adjusted(7, 0, -4, 0)
                if shown:                                       # texte lisible par-dessus une image / une onde
                    fm = p.fontMetrics()
                    tw = min(fm.horizontalAdvance(text) + 8, tr_.width())
                    ty = r.top() + 2 if r.height() >= 34 else r.center().y() - fm.height() / 2
                    pill = QRectF(tr_.left() - 4, ty, tw, fm.height() + 2)
                    p.fillRect(pill, QColor(0, 0, 0, 140))
                    p.setPen(QColor("#ffffff"))
                    p.drawText(pill.adjusted(4, 0, 0, 0), Qt.AlignVCenter | Qt.AlignLeft, text)
                else:
                    p.setPen(_fg(base))
                    p.drawText(tr_, Qt.AlignVCenter | Qt.AlignLeft, text)
                p.restore()
            if c is self.selected:
                p.setRenderHint(QPainter.Antialiasing, True)
                p.setBrush(Qt.NoBrush)
                p.setPen(QPen(col("accent_soft", 120), 4))                      # halo autour de l'element choisi
                p.drawRoundedRect(r.adjusted(-1, -1, 1, 1), 7, 7)
                p.setPen(Qt.NoPen)
                p.setBrush(col("text"))
                if r.height() >= 16:
                    for x in (r.left() + 1, r.right() - 5):                      # poignees pour rogner
                        p.drawRoundedRect(QRectF(x, r.top() + 4, 4, max(4.0, r.height() - 8)), 2, 2)
                p.setRenderHint(QPainter.Antialiasing, False)
            p.setOpacity(1.0)
        p.setClipRect(body)
        # colonne des noms de pistes, par-dessus
        bold = QFont(self.font())
        bold.setBold(True)
        for row in self._rows:
            r = self._row_rect(row["track"])
            if r.bottom() < RULER_H or r.top() > h:
                continue
            p.fillRect(QRectF(0, r.top(), LABEL_W, r.height()), col("header"))
            if row["ghost"]:
                continue
            t = row["track"]
            p.fillRect(QRectF(0, r.top() + 1, 3, max(2.0, r.height() - 2)), col("accent") if t[0] == "V" else col("wave"))
            p.setFont(bold)
            p.setPen(col("muted"))
            p.drawText(QRectF(2, r.top(), 32, r.height()), Qt.AlignCenter, t)
            self._paint_buttons(p, t, small)
            p.setPen(QPen(col("border"), 1))                  # bord du bas : on peut le tirer pour changer la hauteur
            p.drawLine(QPointF(0, r.bottom()), QPointF(w, r.bottom()))
        gy = RULER_H + self._div_y - self.voff
        p.setPen(QPen(col("border"), 2))
        p.drawLine(0, int(gy), w, int(gy))
        p.setClipping(False)
        self._ruler(p, w)
        if not self.project.clips:
            p.setFont(font_main)
            p.setPen(col("muted"))
            p.drawText(QRectF(LABEL_W, RULER_H + STRIP_H, w - LABEL_W, max(10, h - RULER_H - 2 * STRIP_H)),
                       Qt.AlignCenter, tr("ed.drop"))

    def _paint_buttons(self, p, track, small):
        pr = self.project
        states = {"solo": bool(pr.track_solo.get(track)), "mute": bool(pr.track_mute.get(track)),
                  "media": pr.track_media.get(track, True)}
        glyph = {"solo": "S", "mute": "M", "media": "▦" if track[0] == "V" else "∿"}
        colors = {"solo": col("accent"), "mute": QColor("#d9534f"), "media": col("accent_soft")}
        p.setFont(small)
        for what, rect in self._btn_rects(track).items():
            on = states[what]
            p.setPen(QPen(col("border"), 1))
            p.setBrush(colors[what] if on else col("card"))
            p.drawRoundedRect(rect, 4, 4)
            p.setPen(_fg(colors[what]) if on else col("muted"))
            p.drawText(rect, Qt.AlignCenter, glyph[what])

    def _paint_media(self, p, c, r, base, w):
        """Miniatures (video, image) ou forme d'onde (son) dans le clip. Renvoie True si quelque chose est dessine."""
        cache = get_cache()
        p.save()
        path = QPainterPath()
        path.addRoundedRect(r.adjusted(1, 1, -1, -1), 4, 4)
        p.setClipPath(path, Qt.IntersectClip)
        drawn = False
        xa, xb = max(r.left(), float(LABEL_W)), min(r.right(), float(w))
        if c.kind in ("video", "image") and xb > xa:
            if c.kind == "video":
                entry = cache.thumbs(c.path, c.src_duration or c.duration)
                still = None
            else:
                entry, still = True, cache.image(c.path)
            if entry and (c.kind == "video" or still is not None):
                h = r.height() - 2
                tw = max(24.0, h * 16 / 9)
                k = int(max(0.0, xa - r.left()) // tw)
                while True:
                    tx = r.left() + k * tw
                    if tx >= xb:
                        break
                    cw = min(tw, r.right() - tx)
                    tile = QRectF(tx, r.top() + 1, cw, h)
                    if c.kind == "video":
                        src = float(src_times(c, np.array([self._x2t(tx + cw / 2)]))[0])
                        pm = cache.thumb_pixmap(entry, src)
                    else:
                        pm = still
                    crop = QRectF(0, 0, pm.width() * cw / tw, pm.height())
                    p.drawPixmap(tile, pm, crop)
                    k += 1
                drawn = True
        elif c.kind == "audio" and xb > xa:
            peaks = cache.peaks(c.path, c.audio_index)
            if peaks is not None:
                step = 1.0 if xb - xa < 1500 else 2.0
                xs = np.arange(xa, xb + step, step)
                ts = src_times(c, np.array([self._x2t(x) for x in xs] + [self._x2t(xb + step)]))
                i0 = np.maximum(0, (ts[:-1] * PEAK_RATE).astype(int))
                i1 = np.maximum(i0 + 1, (ts[1:] * PEAK_RATE).astype(int) + 1)
                mid, half = r.center().y(), (r.height() - 6) / 2
                lines = []
                n = len(peaks)
                for x, a, b in zip(xs, i0, i1):
                    if a >= n:
                        continue
                    amp = float(peaks[a:min(b, n)].max())
                    hh = max(0.5, min(1.0, amp ** 0.6) * half)
                    lines.append(QLineF(x, mid - hh, x, mid + hh))
                p.setPen(QPen(_fg(base) if base.lightness() > 120 else QColor(255, 255, 255, 215), max(1.0, step)))
                p.drawLines(lines)
                drawn = True
        p.restore()
        return drawn

    def _steps(self):
        steps = [0.1, 0.25, 0.5, 1, 2, 5, 10, 15, 30, 60, 120, 300, 600, 1800, 3600]
        return next((s_ for s_ in steps if s_ * self.pps >= 70), steps[-1])

    def _grid(self, p, w, h):
        """Fines lignes verticales sous chaque graduation principale : on repere mieux les temps sur les pistes."""
        step = self._steps()
        t = int(self.scroll / step) * step
        p.setPen(QPen(col("border", 70), 1))
        top, bottom = RULER_H, h
        while self._t2x(t) < w:
            x = self._t2x(t)
            if x >= LABEL_W:
                p.drawLine(QPointF(round(x) + 0.5, top), QPointF(round(x) + 0.5, bottom))
            t += step

    def _ruler(self, p, w):
        g = QLinearGradient(0, 0, 0, RULER_H)
        g.setColorAt(0.0, col("ruler").lighter(115))
        g.setColorAt(1.0, col("ruler"))
        p.fillRect(QRectF(0, 0, w, RULER_H), g)
        step = self._steps()
        sub = step / 5.0 if step * self.pps >= 140 else step / 2.0 if step * self.pps >= 90 else 0
        t = int(self.scroll / step) * step
        p.setPen(col("muted"))
        while self._t2x(t) < w:
            x = self._t2x(t)
            if sub:                                                    # petites graduations entre deux grandes
                n = round(step / sub)
                p.setPen(QPen(col("muted", 130), 1))
                for k in range(1, n):
                    xs = self._t2x(t + k * sub)
                    if LABEL_W <= xs < w:
                        p.drawLine(QPointF(xs, RULER_H - 4), QPointF(xs, RULER_H))
                p.setPen(col("muted"))
            if x >= LABEL_W - 1:
                p.drawLine(QPointF(x, RULER_H - 8), QPointF(x, RULER_H))
                m, s_ = divmod(t, 60)
                p.drawText(QPointF(x + 4, RULER_H - 10), f"{int(m)}:{s_:04.1f}" if step < 1 else f"{int(m)}:{int(s_):02d}")
            t += step
        p.setPen(QPen(col("border"), 1))
        p.drawLine(0, RULER_H, w, RULER_H)

    # ---------------------------------------------------------------- outils
    def set_tool(self, tool):
        self.tool = tool
        self.hover_x = None
        self.setCursor(Qt.CrossCursor if tool == "cut" else Qt.ArrowCursor)
        self.update()

    def drop_target(self, x, y):
        """Piste et instant (s) sous un point : sert quand on depose un fichier sur la timeline. (None, t) si hors pistes."""
        self._ensure_layout()
        return self._track_at(y), max(0.0, self._x2t(max(x, LABEL_W)))

    # ---------------------------------------------------------------- souris
    def _snap(self, t, ignore_ids, tol):
        """Aimante t sur 0, la tete de lecture ou les bords des autres elements."""
        if not self.magnet:
            return t
        best, dist = t, tol
        cands = [0.0, self.playhead]
        for c in self.project.clips:
            if c.id not in ignore_ids:
                cands += [c.start, c.end]
        snapped = None
        for v in cands:
            if abs(v - t) < dist:
                best, dist, snapped = v, abs(v - t), v
        self.snap_t = snapped if self._drag is not None and isinstance(self._drag, dict) else None
        return best

    def mousePressEvent(self, e):
        self._ensure_layout()
        x, y = e.position().x(), e.position().y()
        if e.button() in (Qt.RightButton, Qt.MiddleButton):
            self._drag = ("pan", x, self.scroll)
            return
        if y >= RULER_H:
            hit = self._label_hit(x, y)
            if hit:
                self.trackToggled.emit(*hit)
                return
            bt = self._boundary_at(y)
            if bt:
                self._drag = dict(mode="resize", track=bt, y0=y, h0=self.row_h(bt), started=False)
                return
            if x < LABEL_W:
                return
        c = self._clip_at(x, y) if y >= RULER_H else None
        if self.tool == "cut" and c is not None:
            t = self._snap(max(0.0, self._x2t(x)), set(), SNAP_PX / self.pps)
            self.cutRequested.emit(t, c)
            return
        if c is None:
            self.select(None)
            self._drag = ("scrub",)
            self.seekRequested.emit(max(0.0, self._x2t(max(x, LABEL_W))))
            return
        self.select(c)
        r = self._clip_rect(c)
        mode = "trim_l" if abs(x - r.left()) <= EDGE else "trim_r" if abs(x - r.right()) <= EDGE else "move"
        group = self.project.group_of(c)
        orig = {m.id: (m.start, m.duration, m.source_in(), [list(s) for s in m.segments]) for m in group}
        self._drag = dict(mode=mode, t0=self._x2t(x), s0=c.start, dur0=c.duration, in0=c.source_in(), tr0=c.track,
                          started=False, group=group, orig=orig)
        if mode == "move":
            self._frozen = True                          # les pistes ne bougent pas pendant le glisser

    def mouseMoveEvent(self, e):
        self._ensure_layout()
        x, y = e.position().x(), e.position().y()
        d = self._drag
        if d is None:
            c = self._clip_at(x, y) if y >= RULER_H else None
            if c is not self.hover_clip:
                self.hover_clip = c
                self.invalidate()
            if self._label_hit(x, y):
                self.setCursor(Qt.PointingHandCursor)
                return
            if self._boundary_at(y):
                self.setCursor(Qt.SizeVerCursor)
                return
            if self.tool == "cut":
                self.hover_x = self._t2x(self._snap(max(0.0, self._x2t(x)), set(), SNAP_PX / self.pps)) if c else None
                self.setCursor(Qt.CrossCursor)
                self.update()
                return
            if c:
                r = self._clip_rect(c)
                near = abs(x - r.left()) <= EDGE or abs(x - r.right()) <= EDGE
                self.setCursor(Qt.SizeHorCursor if near else Qt.OpenHandCursor)
            else:
                self.setCursor(Qt.ArrowCursor)
            return
        if isinstance(d, tuple):
            if d[0] == "pan":
                self.set_scroll(d[2] - (x - d[1]) / self.pps)
            elif d[0] == "scrub":
                self.seekRequested.emit(max(0.0, self._x2t(max(x, LABEL_W))))
            return
        if d["mode"] == "resize":
            nh = int(min(MAX_ROW_H, max(MIN_ROW_H, d["h0"] + (y - d["y0"]))))
            if nh != self.project.track_h.get(d["track"], d["h0"]):
                if not d["started"]:
                    self.editStarted.emit()
                    d["started"] = True
                self.project.track_h[d["track"]] = nh
                self._clamp_voff()
                self.invalidate()
            return
        c = self.selected
        if c is None:
            return
        group, orig = d["group"], d["orig"]
        ids = {m.id for m in group}
        solo = len(group) == 1
        s0, dur0, in0, tr0 = d["s0"], d["dur0"], d["in0"], d["tr0"]
        dt = self._x2t(x) - d["t0"]
        tol = SNAP_PX / self.pps
        plan = None                      # liste de (element, attributs a changer)
        mode = d["mode"]

        def free(m, start, dur, track=None):
            return self.project.free_at(track or m.track, start, start + dur, ignore=group)

        def trimmed(m, a, b):
            """Attributs d'un element rogne a la fenetre locale [a, b) de son etat de depart."""
            st, du, si, sg = orig[m.id]
            nd = b - a
            if nd < MIN_DUR - 1e-9:
                return None
            if sg:                                           # clip compose : on rogne les passages
                ns = seg_window(sg, a, b, m.src_duration)
                return None if ns is None else dict(start=st + a, duration=nd, segments=ns, src_in=ns[0][0])
            if m.kind in ("video", "audio"):
                if si + a < -1e-9 or (m.src_duration > 0 and si + a + nd > m.src_duration + 1e-9):
                    return None
                return dict(start=st + a, duration=nd, src_in=max(0.0, si + a))
            return dict(start=st + a, duration=nd)

        if mode == "move":
            ns = max(0.0, s0 + dt)
            a = self._snap(ns, ids, tol)
            sa = self.snap_t
            b = self._snap(ns + dur0, ids, tol) - dur0
            sb = self.snap_t
            cands = ([(abs(a - ns), a, sa)] if sa is not None else []) + ([(abs(b - ns), max(0.0, b), sb)] if sb is not None else [])
            if cands:                                        # on colle sur le bord (debut ou fin) le plus proche
                _d, ns, self.snap_t = min(cands)             # trait d'accent : l'instant sur lequel ca a colle
            else:
                self.snap_t = None
            delta = max(ns - s0, -min(o[0] for o in orig.values()))
            track = tr0
            if solo:
                t_ = self._track_at(y) or tr0
                track = t_ if kind_of_track(t_) == kind_of_track(tr0) else tr0
            for tk in ([track, tr0] if track != tr0 else [tr0]):
                cand = [(m, dict(start=orig[m.id][0] + delta, **({"track": tk} if solo else {}))) for m in group]
                if all(free(m, a_["start"], orig[m.id][1], tk if solo else None) for m, a_ in cand):
                    plan = cand
                    break
        elif mode == "trim_l":
            end0 = s0 + dur0
            ns = self._snap(max(0.0, s0 + dt), ids, tol)
            ns = min(ns, end0 - MIN_DUR)
            if c.kind in ("video", "audio"):
                ns = max(ns, s0 - in0)           # pas avant le debut du fichier
            ns = max(0.0, ns)
            delta = ns - s0
            cand = []
            for m in group:
                du = orig[m.id][1]
                at = trimmed(m, delta, du)
                if at is None or not free(m, at["start"], at["duration"]):
                    cand = None
                    break
                cand.append((m, at))
            plan = cand
        elif mode == "trim_r":
            end = self._snap(s0 + dur0 + dt, ids, tol)
            ddur = max(MIN_DUR, end - s0) - dur0
            cand = []
            for m in group:
                du = orig[m.id][1]
                nd = max(MIN_DUR, du + ddur)
                at = trimmed(m, 0.0, nd)
                if at is None and nd > du:               # on s'arrete a la fin du fichier
                    lo, hi = du, nd
                    for _ in range(24):
                        mid = (lo + hi) / 2
                        if trimmed(m, 0.0, mid) is None:
                            hi = mid
                        else:
                            lo = mid
                    nd = lo
                    at = trimmed(m, 0.0, nd)
                if at is None:
                    cand = None
                    break
                if not free(m, at["start"], at["duration"]):
                    nxt = min((o.start for o in self.project.clips if o.track == m.track and o.id not in ids
                               and o.start >= orig[m.id][0] + du - 1e-6), default=None)
                    if nxt is None or nxt - at["start"] < MIN_DUR:
                        cand = None
                        break
                    at = trimmed(m, 0.0, min(nd, nxt - at["start"]))
                    if at is None:
                        cand = None
                        break
                cand.append((m, at))
            plan = cand
        if not plan:
            return
        changed = any((abs(getattr(m, k) - v) > 1e-9) if isinstance(v, float) else getattr(m, k) != v
                      for m, a_ in plan for k, v in a_.items())
        if not changed:
            return
        if not d["started"]:
            self.editStarted.emit()
            d["started"] = True
        for m, attrs in plan:
            for k, v in attrs.items():
                setattr(m, k, v)
        self.clipsChanged.emit()
        self.invalidate()

    def mouseReleaseEvent(self, _):
        d, self._drag = self._drag, None
        if self.snap_t is not None:
            self.snap_t = None
            self.update()
        moved = isinstance(d, dict) and d.get("started") and d.get("mode") == "move"
        self._frozen = False
        if isinstance(d, dict) and d.get("started") and d.get("mode") in ("move", "trim_l", "trim_r"):
            self._after_gesture = True
        if moved:
            self.project.compact()                       # une piste devenue vide disparait, les autres se renumerotent
            self._layout_key = None
            self.invalidate()
            self.clipsChanged.emit()
        elif isinstance(d, dict) and d.get("mode") == "move":
            self._layout_key = None
            self.invalidate()
        if getattr(self, "_after_gesture", False):
            self._after_gesture = False
            self.editFinished.emit()

    def leaveEvent(self, e):
        if self.hover_clip is not None:
            self.hover_clip = None
            self.invalidate()
        if self.hover_x is not None:
            self.hover_x = None
            self.update()
        super().leaveEvent(e)


class ProjectOverview(QWidget):
    """Barre au-dessus de la timeline : tout le projet en petit. On glisse le cadre pour se deplacer et on etire
    ses bords pour zoomer / dezoomer (comme dans l'onglet Couper)."""
    EDGE = 7

    def __init__(self, timeline, parent=None):
        super().__init__(parent)
        self.tl = timeline
        self.setFixedHeight(46)
        self.setMouseTracking(True)
        self.setCursor(Qt.PointingHandCursor)
        self._drag = None
        timeline.viewChanged.connect(self.update)

    def _range(self):
        """Temps montre par la barre : le montage avec un peu de vide, comme 'tout afficher' de la timeline."""
        tl = self.tl
        lo, hi = tl.fit_range()
        return min(lo, tl.scroll), max(hi, tl.scroll + tl.visible_seconds())

    def _x(self, t):
        lo, hi = self._range()
        return LABEL_W + (t - lo) / (hi - lo) * (self.width() - LABEL_W)

    def _t(self, x):
        lo, hi = self._range()
        return lo + (x - LABEL_W) / max(1, self.width() - LABEL_W) * (hi - lo)

    def paintEvent(self, _):
        tl = self.tl
        p = QPainter(self)
        w, h = self.width(), self.height()
        p.fillRect(0, 0, w, h, col("wave_bg"))
        names = tl.project.tracks()
        rh = (h - 8) / max(1, len(names))
        for i, t in enumerate(names):                       # une bande par piste
            if i % 2 == 0:
                p.fillRect(QRectF(LABEL_W, 4 + i * rh, w - LABEL_W, rh), col("card", 120))
        p.setClipRect(QRectF(LABEL_W, 0, w - LABEL_W, h))
        p.setPen(Qt.NoPen)
        for c in tl.project.clips:
            if c.track not in names:
                continue
            i = names.index(c.track)
            x0, x1 = self._x(c.start), self._x(c.end)
            p.setBrush(clip_color(c))
            p.drawRect(QRectF(x0, 4 + i * rh + 1, max(1.5, x1 - x0), max(1.5, rh - 2)))
        vs, vl = tl.scroll, tl.visible_seconds()
        r = QRectF(self._x(vs), 1, max(4.0, self._x(vs + vl) - self._x(vs)), h - 2)
        p.fillRect(r, col("text", 40))
        p.setPen(QPen(col("text", 220), 1.5))
        p.setBrush(Qt.NoBrush)
        p.drawRect(r)
        x = self._x(tl.playhead)
        p.setPen(QPen(col("playhead"), 2))
        p.drawLine(QPointF(x, 0), QPointF(x, h))
        p.setClipping(False)
        p.fillRect(QRectF(0, 0, LABEL_W, h), col("header"))      # meme colonne que la timeline : tout est aligne

    def _hit(self, x):
        tl = self.tl
        x0, x1 = self._x(tl.scroll), self._x(tl.scroll + tl.visible_seconds())
        if abs(x - x0) <= self.EDGE:
            return "left"
        if abs(x - x1) <= self.EDGE:
            return "right"
        return "body" if x0 < x < x1 else None

    def mousePressEvent(self, e):
        if e.button() != Qt.LeftButton:
            return
        tl, x = self.tl, e.position().x()
        if x < LABEL_W:
            return
        hit = self._hit(x)
        if hit is None:                      # clic en dehors du cadre : on le centre ici, puis on peut glisser
            tl.set_view(self._t(x) - tl.visible_seconds() / 2, tl.visible_seconds())
            hit = "body"
        self._drag = (hit, self._t(x) - tl.scroll, tl.scroll, tl.scroll + tl.visible_seconds())

    def mouseMoveEvent(self, e):
        tl, x = self.tl, e.position().x()
        d = self._drag
        if not (e.buttons() & Qt.LeftButton) or d is None:
            hit = self._hit(x)
            self.setCursor(Qt.SizeHorCursor if hit in ("left", "right") else
                           Qt.OpenHandCursor if hit == "body" else Qt.PointingHandCursor)
            return
        which, off, vs0, ve0 = d
        t = self._t(x)
        if which == "body":
            tl.set_view(t - off, ve0 - vs0)
        elif which == "left":                # bord gauche etire : le bord droit reste en place
            ns = min(t, ve0 - 0.5)
            tl.set_view(ns, ve0 - ns)
        else:                                # bord droit
            tl.set_view(vs0, max(0.5, t - vs0))

    def mouseReleaseEvent(self, _):
        self._drag = None

    def mouseDoubleClickEvent(self, _):
        self.tl.fit_all()
