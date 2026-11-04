"""Forme d'onde avec les coupes surlignees en rouge + mini-carte de navigation.
Clic = aller a cet endroit (on peut glisser), molette = zoom, glisser les bords de la coupe selectionnee
= ajuster son debut / sa fin."""
import numpy as np
from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QBrush, QColor, QFont, QPainter, QPen
from PySide6.QtWidgets import QWidget

from .theme import col

DB_FLOOR = -70.0
RULER_H = 20
EDGE_PX = 7
MIN_VIEW = 0.2          # zoom maximum : 0,2 s a l'ecran


class WaveformWidget(QWidget):
    seekRequested = Signal(float)     # clic dans le vide : position en secondes
    cutSelected = Signal(int)         # clic sur une coupe : son numero dans la liste
    cutEdited = Signal(int)           # bord glisse : numero de la coupe modifiee
    cutToggled = Signal(int)          # double-clic sur une coupe : la garder / la couper
    editStarted = Signal(int)         # un bord va etre glisse (pour pouvoir annuler avec Ctrl+Z)
    viewChanged = Signal()            # la vue, les coupes ou la tete de lecture ont change

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumHeight(120)
        self.setMouseTracking(True)
        self.env = None
        self.hop = 0.01
        self.fps = 60.0
        self.duration = 0.0
        self.cuts = []
        self.done = []          # zones coupees definitivement (numeros d'image) : elles n'existent plus a l'ecran
        self._done_s = []       # les memes, en secondes (triees)
        self._cenv = None       # enveloppe SANS les zones coupees (c'est elle qu'on dessine)
        self.cdur = 0.0         # duree restante (sans les zones coupees) : c'est l'axe du temps affiche
        self.sel = -1
        self.threshold = None
        self.playhead = 0.0
        self.vs = 0.0           # debut de la partie visible (s)
        self.vl = 60.0          # largeur de la partie visible (s)
        self._drag = None
        self.history = []       # vues precedentes (pour "Zoom precedent")

    def push_view(self):
        """Memorise la vue actuelle."""
        cur = (self.vs, self.vl)
        if not self.history or self.history[-1] != cur:
            self.history.append(cur)
            del self.history[:-30]

    def back_view(self):
        """Revient a la vue memorisee. Renvoie False s'il n'y en a pas."""
        if not self.history:
            return False
        self.vs, self.vl = self.history.pop()
        self._clamp_view()
        self._refresh()
        return True

    def _refresh(self):
        self.update()
        self.viewChanged.emit()

    # ---------- donnees
    def set_data(self, env, hop, duration, fps):
        first = self.env is None
        self.env, self.hop, self.duration, self.fps = env, hop, duration, float(fps)
        self._rebuild_compact()
        if first:
            self.vs, self.vl = 0.0, max(1.0, min(60.0, duration))
        self._refresh()

    def set_cuts(self, cuts, sel=-1):
        self.cuts, self.sel = cuts, sel
        self._refresh()

    def set_done(self, ranges):
        """Zones coupees pour de bon : elles disparaissent de la forme d'onde, les deux parties voisines se rejoignent."""
        self.done = sorted(ranges)
        self._rebuild_compact()
        self._clamp_view()
        self._refresh()

    def _rebuild_compact(self):
        f = self.fps or 60.0
        self._done_s = [(a / f, b / f) for a, b in self.done]
        self.cdur = max(0.0, self.duration - sum(b - a for a, b in self._done_s))
        if self.env is not None and self._done_s:
            keep = np.ones(len(self.env), bool)
            for a, b in self._done_s:
                keep[int(a / self.hop): int(np.ceil(b / self.hop))] = False
            self._cenv = self.env[keep]
        else:
            self._cenv = self.env

    def to_c(self, t):
        """Instant du fichier -> instant affiche (sans les zones coupees)."""
        return t - sum(min(b, t) - a for a, b in self._done_s if a < t)

    def to_m(self, tc, prefer_end=False):
        """Instant affiche -> instant du fichier. Pile sur une jointure : juste apres la zone coupee
        (prefer_end=True : juste avant, pour la FIN d'une coupe)."""
        add = 0.0
        for a, b in self._done_s:
            ca = a - add
            if tc > ca + 1e-9 or (tc >= ca - 1e-9 and not prefer_end):
                add += b - a
            else:
                break
        return tc + add

    def _bounds(self, i):
        """Limites en images entre lesquelles les bords de la coupe i peuvent bouger (ni autre coupe, ni zone coupee)."""
        c = self.cuts[i]
        lo = self.cuts[i - 1].end if i > 0 else 0
        hi = self.cuts[i + 1].start if i + 1 < len(self.cuts) else int(self.duration * self.fps)
        for a, b in self.done:
            if b <= c.start:
                lo = max(lo, b)
            if a >= c.end:
                hi = min(hi, a)
        return lo, hi

    def set_threshold(self, db):
        self.threshold = db
        self.update()

    def set_playhead(self, t, follow=False):
        changed = t != self.playhead
        self.playhead = t
        tc = self.to_c(t)
        if follow and self._drag is None and (tc < self.vs or tc > self.vs + self.vl):
            self._center_c(tc, ratio=0.1)
        elif changed:
            self._refresh()

    # ---------- vue (zoom / deplacement)
    def _clamp_view(self):
        self.vl = min(max(MIN_VIEW, self.vl), max(MIN_VIEW, self.cdur))
        self.vs = min(max(0.0, self.vs), max(0.0, self.cdur - self.vl))

    def _center_c(self, tc, ratio=0.5):
        """Centre la vue sur un instant AFFICHE (sans les zones coupees)."""
        self.vs = tc - ratio * self.vl
        self._clamp_view()
        self._refresh()

    def center_on(self, t, ratio=0.5):
        self._center_c(self.to_c(t), ratio)

    def ensure_visible(self, a, b):
        a, b = self.to_c(a), self.to_c(b)
        if a < self.vs or b > self.vs + self.vl:
            self._center_c((a + b) / 2)

    def zoom(self, factor, center_t=None):
        """factor < 1 : zoom avant ; factor > 1 : zoom arriere."""
        t = self.to_c(self.playhead if center_t is None else center_t)
        ratio = (t - self.vs) / self.vl if self.vs <= t <= self.vs + self.vl else 0.5
        self.vl *= factor
        self.vs = t - ratio * self.vl
        self._clamp_view()
        self._refresh()

    def show_all(self):
        self.vs, self.vl = 0.0, max(MIN_VIEW, self.cdur)
        self._refresh()

    def show_range(self, a, b, margin=1.0):
        a, b = self.to_c(a), self.to_c(b)
        self.vl = max(2.0, (b - a) + 2 * margin)
        self.vs = (a + b) / 2 - self.vl / 2
        self._clamp_view()
        self._refresh()

    def set_view(self, vs, vl):
        """Fixe la partie visible : debut (s) et largeur (s)."""
        self.vs, self.vl = vs, vl
        self._clamp_view()
        self._refresh()

    # ---------- conversions
    def _t2x(self, t):
        return (self.to_c(t) - self.vs) / self.vl * self.width()

    def _x2t(self, x, prefer_end=False):
        return self.to_m(self.vs + x / max(1, self.width()) * self.vl, prefer_end)

    def _amp(self, db, half):
        return float(np.clip((db - DB_FLOOR) / -DB_FLOOR, 0, 1)) * half

    # ---------- dessin
    def paintEvent(self, _):
        p = QPainter(self)
        w, h = self.width(), self.height()
        p.fillRect(0, 0, w, h, col("wave_bg"))
        top, ah = RULER_H, h - RULER_H
        cy, half = top + ah / 2, ah / 2 - 3

        env = self._cenv
        if env is not None and len(env):
            i0 = max(0, int(self.vs / self.hop))
            i1 = min(len(env), int((self.vs + self.vl) / self.hop) + 1)
            seg = env[i0:i1]
            if len(seg):
                if len(seg) > w:
                    idx = np.linspace(0, len(seg), w, endpoint=False).astype(int)
                    cols = np.maximum.reduceat(seg, idx)
                    xs = ((i0 + idx) * self.hop - self.vs) / self.vl * w
                    pw = 1.0
                else:
                    cols = seg
                    xs = ((i0 + np.arange(len(seg))) * self.hop - self.vs) / self.vl * w
                    pw = max(1.0, w / len(seg))
                amps = np.clip((cols - DB_FLOOR) / -DB_FLOOR, 0, 1) * half
                p.setPen(QPen(col("wave"), pw))
                for x, a in zip(xs, amps):
                    p.drawLine(QPointF(x, cy - a), QPointF(x, cy + a))

        # ligne du seuil
        if self.threshold is not None:
            a = self._amp(self.threshold, half)
            p.setPen(QPen(col("thr", 150), 1, Qt.DashLine))
            p.drawLine(QPointF(0, cy - a), QPointF(w, cy - a))
            p.drawLine(QPointF(0, cy + a), QPointF(w, cy + a))

        # coupes
        for i, c in enumerate(self.cuts):
            x0, x1 = self._t2x(c.start / self.fps), self._t2x(c.end / self.fps)
            if x1 < 0 or x0 > w:
                continue
            r = QRectF(x0, top, max(1.0, x1 - x0), ah)
            p.fillRect(r, col("cut", 120) if c.enabled else col("muted", 55))
            if i == self.sel:
                p.setPen(QPen(col("handle"), 2))
                p.drawRect(r)
                p.fillRect(QRectF(x0 - 2, top, 5, ah), col("handle"))
                p.fillRect(QRectF(x1 - 2, top, 5, ah), col("handle"))

        self._ruler(p, w)
        x = self._t2x(self.playhead)
        p.setPen(QPen(col("playhead"), 2))
        p.drawLine(QPointF(x, 0), QPointF(x, h))

    def _ruler(self, p, w):
        p.fillRect(0, 0, w, RULER_H, col("ruler"))
        steps = [0.05, 0.1, 0.5, 1, 2, 5, 10, 30, 60, 120, 300, 600, 1800]
        step = next((s for s in steps if s / self.vl * w >= 80), steps[-1])
        t = int(self.vs / step) * step
        p.setFont(QFont("Segoe UI", 8))
        p.setPen(col("muted"))
        while t < self.vs + self.vl:
            x = self._t2x(t)
            p.drawLine(QPointF(x, RULER_H - 6), QPointF(x, RULER_H))
            m, s = divmod(t, 60)
            p.drawText(QPointF(x + 3, 13), f"{int(m)}:{s:04.1f}" if step < 1 else f"{int(m)}:{int(s):02d}")
            t += step

    # ---------- souris
    def _edge_at(self, x):
        if 0 <= self.sel < len(self.cuts):
            c = self.cuts[self.sel]
            if abs(x - self._t2x(c.start / self.fps)) <= EDGE_PX:
                return "start"
            if abs(x - self._t2x(c.end / self.fps)) <= EDGE_PX:
                return "end"
        return None

    def mousePressEvent(self, e):
        x = e.position().x()
        if e.button() in (Qt.RightButton, Qt.MiddleButton):
            self._drag = ("pan", x, self.vs)
            return
        edge = self._edge_at(x)
        if edge:
            self.editStarted.emit(self.sel)
            self._drag = (edge, self.sel)
            return
        t = self._x2t(x)
        for i, c in enumerate(self.cuts):
            if c.start / self.fps <= t <= c.end / self.fps:
                self.cutSelected.emit(i)
                return
        self._drag = ("scrub",)
        self.seekRequested.emit(max(0.0, min(t, self.duration)))

    def mouseMoveEvent(self, e):
        x = e.position().x()
        if self._drag is None:
            self.setCursor(Qt.SizeHorCursor if self._edge_at(x) else Qt.ArrowCursor)
            return
        if self._drag[0] == "scrub":
            self.seekRequested.emit(max(0.0, min(self._x2t(x), self.duration)))
            return
        if self._drag[0] == "pan":
            dt = (x - self._drag[1]) / max(1, self.width()) * self.vl
            self.vs = self._drag[2] - dt
            self._clamp_view()
            self._refresh()
            return
        which, i = self._drag
        c = self.cuts[i]
        f = int(round(self._x2t(x, prefer_end=(which == "end")) * self.fps))
        lo, hi = self._bounds(i)
        if which == "start":
            c.start = max(lo, min(f, c.end - 1))
        else:
            c.end = min(hi, max(f, c.start + 1))
        self._refresh()
        self.cutEdited.emit(i)

    def mouseReleaseEvent(self, _):
        self._drag = None

    def mouseDoubleClickEvent(self, e):
        t = self._x2t(e.position().x())
        for i, c in enumerate(self.cuts):
            if c.start / self.fps <= t <= c.end / self.fps:
                self.cutToggled.emit(i)
                return

    def wheelEvent(self, e):
        d = e.angleDelta().y() or e.angleDelta().x()
        if e.modifiers() & Qt.ShiftModifier:
            self.vs -= d / 120 * self.vl * 0.1
            self._clamp_view()
            self._refresh()
        else:
            self.zoom(0.8 if d > 0 else 1.25, self._x2t(e.position().x()))


class OverviewWidget(QWidget):
    """Mini-carte de toute la video : clic ou glisser pour aller n'importe ou."""

    def __init__(self, wave, parent=None):
        super().__init__(parent)
        self.wave = wave
        self.setFixedHeight(48)
        self.setCursor(Qt.PointingHandCursor)
        self.setMouseTracking(True)
        self._drag = None
        wave.viewChanged.connect(self.update)

    def _t(self, x):                       # instants AFFICHES (sans les zones coupees definitivement)
        return x / max(1, self.width()) * max(self.wave.cdur, 0.001)

    def _x(self, t):
        return t / max(self.wave.cdur, 0.001) * self.width()

    def _xm(self, t):                      # instant du fichier -> position
        return self._x(self.wave.to_c(t))

    def paintEvent(self, _):
        wv = self.wave
        p = QPainter(self)
        w, h = self.width(), self.height()
        p.fillRect(0, 0, w, h, col("wave_bg"))
        env = wv._cenv
        if env is not None and len(env) and w > 4:
            idx = np.linspace(0, len(env), w, endpoint=False).astype(int)
            cols = np.maximum.reduceat(env, idx)
            amps = np.clip((cols - DB_FLOOR) / -DB_FLOOR, 0, 1) * (h / 2 - 3)
            p.setPen(QPen(col("wave_dim"), 1))
            for x, a in enumerate(amps):
                p.drawLine(x, int(h / 2 - a), x, int(h / 2 + a))
        for c in wv.cuts:
            x0, x1 = self._xm(c.start / wv.fps), self._xm(c.end / wv.fps)
            p.fillRect(QRectF(x0, 0, max(1.5, x1 - x0), h),
                       col("cut", 150) if c.enabled else col("muted", 60))
        if 0 <= wv.sel < len(wv.cuts):
            c = wv.cuts[wv.sel]
            p.setPen(QPen(col("handle"), 1))
            p.drawRect(QRectF(self._xm(c.start / wv.fps), 0, max(2.0, self._xm(c.end / wv.fps) - self._xm(c.start / wv.fps)), h - 1))
        # la partie visible dans la forme d'onde
        r = QRectF(self._x(wv.vs), 1, max(3.0, self._x(wv.vl)), h - 2)
        p.fillRect(r, col("text", 40))
        p.setPen(QPen(col("text", 200), 1.5))
        p.drawRect(r)
        x = self._xm(wv.playhead)
        p.setPen(QPen(col("playhead"), 2))
        p.drawLine(QPointF(x, 0), QPointF(x, h))

    # ---- souris : glisser le cadre = deplacer, etirer ses bords = zoomer / dezoomer
    EDGE = 7

    def _hit(self, x):
        """'left' / 'right' (bords du cadre), 'body' (dedans) ou None (en dehors)."""
        wv = self.wave
        x0, x1 = self._x(wv.vs), self._x(wv.vs + wv.vl)
        if abs(x - x0) <= self.EDGE:
            return "left"
        if abs(x - x1) <= self.EDGE:
            return "right"
        return "body" if x0 < x < x1 else None

    def mousePressEvent(self, e):
        if e.button() != Qt.LeftButton:
            return
        x = e.position().x()
        hit = self._hit(x)
        wv = self.wave
        if hit is None:                      # clic en dehors du cadre : on le centre ici, puis on peut glisser
            wv._center_c(self._t(x))
            hit = "body"
        self._drag = (hit, self._t(x) - wv.vs, wv.vs, wv.vs + wv.vl)

    def mouseMoveEvent(self, e):
        x = e.position().x()
        d = getattr(self, "_drag", None)
        if not (e.buttons() & Qt.LeftButton) or d is None:
            hit = self._hit(x)
            self.setCursor(Qt.SizeHorCursor if hit in ("left", "right") else
                           Qt.OpenHandCursor if hit == "body" else Qt.PointingHandCursor)
            return
        which, off, vs0, ve0 = d
        t = self._t(x)
        if which == "body":
            self.wave.set_view(t - off, ve0 - vs0)
        elif which == "left":                # on etire le bord gauche : le bord droit reste en place
            self.wave.set_view(min(t, ve0 - MIN_VIEW), ve0 - min(t, ve0 - MIN_VIEW))
        else:                                # bord droit
            self.wave.set_view(vs0, max(t - vs0, MIN_VIEW))

    def mouseReleaseEvent(self, _):
        self._drag = None

    def mouseDoubleClickEvent(self, _):
        self.wave.show_all()
