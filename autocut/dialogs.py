"""Fenetres Aide (avec schemas dessines) et Options (themes, langue, plein ecran...)."""
import os
import random
import time

from PySide6.QtCore import QPointF, QRectF, QSize, Qt, Signal
from PySide6.QtGui import QColor, QFont, QFontMetricsF, QGuiApplication, QPainter, QPen, QPolygonF
from PySide6.QtWidgets import (
    QAbstractButton, QButtonGroup, QCheckBox, QComboBox, QDialog, QFileDialog, QFrame, QGridLayout, QHBoxLayout, QLabel,
    QInputDialog, QLineEdit, QListWidget, QListWidgetItem, QMenu, QMessageBox, QPushButton, QScrollArea, QVBoxLayout, QWidget,
)

from . import i18n, projects, settings, theme
from .i18n import tr
from .theme import FONT_FAMILIES, col


def _font(size, bold=False):
    f = QFont("Segoe UI")
    f.setFamilies(FONT_FAMILIES)
    f.setPointSizeF(float(size))
    f.setBold(bold)
    return f


# ================================================================== schemas de l'aide
def _bars(seed, n, quiet):
    r = random.Random(seed)
    out = []
    for i in range(n):
        f = i / (n - 1)
        out.append(0.05 if any(a <= f <= b for a, b in quiet) else 0.35 + 0.6 * r.random())
    return out


def _draw_wave(p, rect, bars, color):
    n = len(bars)
    bw = rect.width() / n
    p.setPen(Qt.NoPen)
    p.setBrush(color)
    cy = rect.center().y()
    for i, a in enumerate(bars):
        hh = max(2.0, a * rect.height())
        p.drawRoundedRect(QRectF(rect.left() + i * bw + bw * 0.15, cy - hh / 2, bw * 0.7, hh), 1, 1)


def _text(p, rect, s, color, size=8.5, bold=False, align=Qt.AlignCenter):
    """Ecrit un texte en reduisant la taille si besoin pour qu'il tienne dans le rectangle."""
    size = float(size)
    f = _font(size, bold)
    while size > 5.5:
        f.setPointSizeF(size)
        if QFontMetricsF(f).horizontalAdvance(s) <= rect.width() - 4:
            break
        size -= 0.5
    f.setPointSizeF(size)
    p.setFont(f)
    p.setPen(color)
    p.drawText(rect, int(align), s)


def _arrow(p, x1, y, x2, color):
    p.setPen(QPen(color, 2))
    p.drawLine(QPointF(x1, y), QPointF(x2 - 4, y))
    p.setBrush(color)
    p.setPen(Qt.NoPen)
    p.drawPolygon(QPolygonF([QPointF(x2, y), QPointF(x2 - 9, y - 5), QPointF(x2 - 9, y + 5)]))


def _pill(p, rect, s, fill, fg):
    p.setPen(Qt.NoPen)
    p.setBrush(fill)
    p.drawRoundedRect(rect, rect.height() / 2, rect.height() / 2)
    _text(p, rect, s, fg, 8, True)


class Diagram(QWidget):
    def __init__(self, kind):
        super().__init__()
        self.kind = kind
        self.setMinimumHeight(112)
        self.setMinimumWidth(250)

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        getattr(self, "_" + self.kind)(p, self.width(), self.height())

    # 1. ouvrir
    def _open(self, p, w, h):
        fx, fy, fw, fh = 14, h * 0.18, 78, h * 0.62
        p.setBrush(col("btn"))
        p.setPen(QPen(col("btn_border"), 1.5))
        p.drawRoundedRect(QRectF(fx, fy, fw, fh), 9, 9)
        p.setBrush(col("accent"))
        p.setPen(Qt.NoPen)
        p.drawPolygon(QPolygonF([QPointF(fx + fw * 0.38, fy + fh * 0.2), QPointF(fx + fw * 0.38, fy + fh * 0.58),
                                 QPointF(fx + fw * 0.68, fy + fh * 0.39)]))
        _text(p, QRectF(fx, fy + fh - 22, fw, 18), "video.mp4", col("muted"), 8)
        _arrow(p, fx + fw + 10, h / 2, w * 0.5, col("accent_soft"))
        wx, ww = w * 0.53, w - w * 0.53 - 14
        r = QRectF(wx, fy - 6, ww, fh + 12)
        p.setBrush(col("card"))
        p.setPen(QPen(col("border"), 1.5))
        p.drawRoundedRect(r, 8, 8)
        p.setPen(Qt.NoPen)
        p.setBrush(col("header"))
        p.drawRoundedRect(QRectF(wx + 1, fy - 5, ww - 2, 14), 6, 6)
        wave = QRectF(wx + 8, fy + 14, ww - 16, fh - 14)
        _draw_wave(p, wave, _bars(3, 26, [(0.3, 0.45), (0.72, 0.84)]), col("wave"))
        for a, b in ((0.3, 0.45), (0.72, 0.84)):
            p.setBrush(col("cut", 110))
            p.drawRect(QRectF(wave.left() + a * wave.width(), wave.top(), (b - a) * wave.width(), wave.height()))

    # 2. pistes
    def _tracks(self, p, w, h):
        rows = [(1, "diag.mixed", "mode.dead", "accent"), (2, None, None, None),
                (3, "diag.voice", "mode.classic", "accent_soft")]
        rh = (h - 12) / 3
        for i, (n, pill, mode, key) in enumerate(rows):
            y = 6 + i * rh
            _text(p, QRectF(10, y, 50, rh), tr("diag.track", n=n), col("muted"), 8.5, True, Qt.AlignLeft | Qt.AlignVCenter)
            _draw_wave(p, QRectF(62, y + 5, w * 0.27, rh - 10), _bars(10 + i, 22, [(0.4, 0.55)] if i != 1 else []),
                       col("wave") if key != "accent_soft" else col("accent_soft"))
            if pill:
                x0 = 62 + w * 0.27 + 12
                _pill(p, QRectF(x0, y + rh / 2 - 10, w - x0 - 10, 20), f"{tr(pill)}  ›  {tr(mode)}",
                      col(key), col("on_accent"))

    # 3. detection
    def _detect(self, p, w, h):
        wave = QRectF(12, 12, w - 24, h - 46)
        quiet = [(0.26, 0.42), (0.68, 0.78)]
        _draw_wave(p, wave, _bars(7, 44, quiet), col("wave"))
        for a, b in quiet:
            p.setPen(Qt.NoPen)
            p.setBrush(col("cut", 110))
            p.drawRect(QRectF(wave.left() + a * wave.width(), wave.top(), (b - a) * wave.width(), wave.height()))
        cy, dy = wave.center().y(), wave.height() * 0.13
        p.setPen(QPen(col("thr"), 1.6, Qt.DashLine))
        p.drawLine(QPointF(wave.left(), cy - dy), QPointF(wave.right(), cy - dy))
        p.drawLine(QPointF(wave.left(), cy + dy), QPointF(wave.right(), cy + dy))
        _text(p, QRectF(wave.left() + 4, wave.top() - 2, 80, 14), tr("diag.thr"), col("thr"), 8, True, Qt.AlignLeft)
        a, b = quiet[0]
        x0, x1, y = wave.left() + a * wave.width(), wave.left() + b * wave.width(), wave.bottom() + 8
        p.setPen(QPen(col("muted"), 1.5))
        p.drawLine(QPointF(x0, y), QPointF(x1, y))
        p.drawLine(QPointF(x0, y - 4), QPointF(x0, y + 4))
        p.drawLine(QPointF(x1, y - 4), QPointF(x1, y + 4))
        _text(p, QRectF(x0 - 20, y + 3, x1 - x0 + 40, 16), tr("diag.min"), col("muted"), 8)
        a, b = quiet[1]
        x0, x1 = wave.left() + a * wave.width(), wave.left() + b * wave.width()
        p.setPen(QPen(col("handle"), 2))
        for x in (x0, x1):
            p.drawLine(QPointF(x, wave.top()), QPointF(x, wave.bottom()))
        _text(p, QRectF(x0 - 20, y - 5, x1 - x0 + 40, 16), tr("diag.margins"), col("handle"), 8, True)

    # 4. retoucher
    def _edit(self, p, w, h):
        wave = QRectF(12, 34, w - 24, h - 48)
        quiet = [(0.34, 0.64)]
        _draw_wave(p, wave, _bars(5, 40, quiet), col("wave"))
        a, b = quiet[0]
        x0, x1 = wave.left() + a * wave.width(), wave.left() + b * wave.width()
        p.setPen(Qt.NoPen)
        p.setBrush(col("cut", 120))
        p.drawRect(QRectF(x0, wave.top(), x1 - x0, wave.height()))
        p.setBrush(col("handle"))
        for x in (x0, x1):
            p.drawRect(QRectF(x - 2, wave.top() - 2, 4, wave.height() + 4))
        p.drawPolygon(QPolygonF([QPointF(x0 - 5, wave.center().y()), QPointF(x0 - 14, wave.center().y() - 6),
                                 QPointF(x0 - 14, wave.center().y() + 6)]))
        p.drawPolygon(QPolygonF([QPointF(x1 + 5, wave.center().y()), QPointF(x1 + 14, wave.center().y() - 6),
                                 QPointF(x1 + 14, wave.center().y() + 6)]))
        cx = (x0 + x1) / 2
        box = QRectF(cx - 40, 8, 16, 16)
        p.setBrush(col("accent"))
        p.drawRoundedRect(box, 4, 4)
        p.setPen(QPen(col("on_accent"), 2, Qt.SolidLine, Qt.RoundCap))
        p.drawLine(QPointF(box.left() + 4, box.center().y()), QPointF(box.left() + 7, box.center().y() + 3.5))
        p.drawLine(QPointF(box.left() + 7, box.center().y() + 3.5), QPointF(box.left() + 12, box.top() + 4))
        _text(p, QRectF(cx - 20, 6, 80, 20), tr("diag.cut"), col("text"), 8.5, True, Qt.AlignLeft | Qt.AlignVCenter)

    # 5. export
    def _export(self, p, w, h):
        items = [("✂  Auto Cut", tr("diag.yourcuts"), False), (tr("diag.xml"), tr("diag.files"), False),
                 ("DaVinci", tr("diag.import"), True)]
        gap = 26
        bw = (w - 20 - 2 * gap) / 3
        for i, (t1, t2, hot) in enumerate(items):
            x = 10 + i * (bw + gap)
            r = QRectF(x, h * 0.2, bw, h * 0.56)
            p.setBrush(col("accent") if hot else col("btn"))
            p.setPen(QPen(col("accent") if hot else col("btn_border"), 1.5))
            p.drawRoundedRect(r, 10, 10)
            fg = col("on_accent") if hot else col("text")
            _text(p, QRectF(r.left(), r.top() + 8, r.width(), 22), t1, fg, 9, True)
            _text(p, QRectF(r.left() + 2, r.top() + 30, r.width() - 4, 30), t2, fg if hot else col("muted"), 7.5)
            if i < 2:
                _arrow(p, x + bw + 4, r.center().y(), x + bw + gap - 3, col("accent_soft"))


def _card(num, title, body):
    f = QFrame()
    f.setObjectName("card")
    v = QVBoxLayout(f)
    v.setContentsMargins(16, 14, 16, 14)
    v.setSpacing(8)
    top = QHBoxLayout()
    badge = QLabel(num)
    badge.setObjectName("badge")
    badge.setAlignment(Qt.AlignCenter)
    t = QLabel(title)
    t.setObjectName("cardtitle")
    top.addWidget(badge)
    top.addWidget(t, 1)
    v.addLayout(top)
    v.addWidget(body, 1)
    return f


class HelpDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle(tr("dlg.help.title"))
        avail = QGuiApplication.primaryScreen().availableGeometry().height()
        self.resize(1000, min(720, int(avail * 0.92)))
        lay = QVBoxLayout(self)
        lay.setContentsMargins(22, 18, 22, 16)
        lay.setSpacing(12)
        title = QLabel(tr("help.title"))
        title.setObjectName("helptitle")
        sub = QLabel(tr("help.sub"))
        sub.setObjectName("muted")
        lay.addWidget(title)
        lay.addWidget(sub)

        grid = QGridLayout()
        grid.setSpacing(14)
        for i, kind in enumerate(("open", "tracks", "detect", "edit", "export")):
            body = QWidget()
            bl = QVBoxLayout(body)
            bl.setContentsMargins(0, 0, 0, 0)
            bl.setSpacing(6)
            bl.addWidget(Diagram(kind), 1)
            c = QLabel(tr(f"help.s{i + 1}.c"))
            c.setObjectName("muted")
            c.setWordWrap(True)
            bl.addWidget(c)
            grid.addWidget(_card(str(i + 1), tr(f"help.s{i + 1}.t"), body), i // 3, i % 3)

        body = QWidget()
        g = QGridLayout(body)
        g.setContentsMargins(0, 4, 0, 0)
        g.setVerticalSpacing(7)
        for r, k in enumerate(("space", "ctrlz", "ctrly", "wheel", "rclick", "f11", "f1")):
            chip = QLabel(tr(f"key.{k}"))
            chip.setObjectName("chip")
            lab = QLabel(tr(f"kd.{ {'ctrlz': 'undo', 'ctrly': 'redo'}.get(k, k) }"))
            lab.setObjectName("muted")
            g.addWidget(chip, r, 0, Qt.AlignLeft)
            g.addWidget(lab, r, 1)
        g.setColumnStretch(1, 1)
        grid.addWidget(_card("★", tr("help.keys"), body), 1, 2)
        for c in range(3):
            grid.setColumnStretch(c, 1)

        inner = QWidget()
        inner.setLayout(grid)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setWidget(inner)
        lay.addWidget(scroll, 1)

        close = QPushButton(tr("btn.close"))
        close.setObjectName("primary")
        close.setMinimumWidth(120)
        close.clicked.connect(self.accept)
        row = QHBoxLayout()
        row.addStretch()
        row.addWidget(close)
        lay.addLayout(row)


# ================================================================== options
class NewProjectDialog(QDialog):
    """Creation d'un projet : on lui donne un nom (et, si on veut, la video de depart : on peut aussi la glisser ici)."""

    def __init__(self, parent=None, video="", name=""):
        super().__init__(parent)
        self.setWindowTitle(tr("np.title"))
        self.setMinimumWidth(520)
        self.setAcceptDrops(True)
        self._auto = not name
        v = QVBoxLayout(self)
        v.setContentsMargins(22, 18, 22, 18)
        v.setSpacing(10)
        v.addWidget(QLabel(tr("np.name")))
        self.ed_name = QLineEdit(name)
        self._programmatic = False
        self.ed_name.textChanged.connect(self._name_changed)
        v.addWidget(self.ed_name)
        v.addWidget(QLabel(tr("np.video")))
        row = QHBoxLayout()
        self.ed_video = QLineEdit(video)
        self.ed_video.setReadOnly(True)
        btn = QPushButton(tr("np.browse"))
        btn.clicked.connect(self._browse)
        row.addWidget(self.ed_video, 1)
        row.addWidget(btn)
        v.addLayout(row)
        hint = QLabel(tr("np.hint"))
        hint.setObjectName("muted")
        v.addWidget(hint)
        bot = QHBoxLayout()
        bot.addStretch(1)
        self.btn_cancel = QPushButton(tr("btn.cancel"))
        self.btn_ok = QPushButton(tr("np.create"))
        self.btn_ok.setObjectName("primary")
        self.btn_ok.setDefault(True)
        bot.addWidget(self.btn_cancel)
        bot.addWidget(self.btn_ok)
        v.addLayout(bot)
        self.btn_cancel.clicked.connect(self.reject)
        self.btn_ok.clicked.connect(self._accept)
        self.ed_name.returnPressed.connect(self._accept)
        if video and not name:
            self.set_video(video)
        self.ed_name.setFocus()
        self.ed_name.selectAll()

    def _name_changed(self, _t):
        if not self._programmatic:                           # le nom a ete tape a la main : on ne le remplace plus
            self._auto = False

    def set_video(self, path):
        path = os.path.normpath(path)
        self.ed_video.setText(path)
        if self._auto or not self.ed_name.text().strip():
            self._programmatic = True
            self.ed_name.setText(projects.unique_name(settings.projects_root(), os.path.splitext(os.path.basename(path))[0]))
            self._programmatic = False
            self._auto = True

    def _browse(self):
        path, _ = QFileDialog.getOpenFileName(self, tr("dlg.open.title"), "", tr("dlg.open.filter"))
        if path:
            self.set_video(path)

    def _accept(self):
        name = self.ed_name.text().strip()
        if not name:
            QMessageBox.warning(self, tr("np.title"), tr("np.empty"))
            return
        if projects.exists(settings.projects_root(), name):
            QMessageBox.warning(self, tr("np.title"), tr("np.exists", name=name))
            return
        self.accept()

    def values(self):
        return self.ed_name.text().strip(), self.ed_video.text()

    def dragEnterEvent(self, e):
        if e.mimeData().hasUrls():
            e.acceptProposedAction()

    def dropEvent(self, e):
        for u in e.mimeData().urls():
            if u.isLocalFile():
                self.set_video(u.toLocalFile())
                break


class WelcomeDialog(QDialog):
    """Ecran d'accueil : en haut a gauche 'Nouveau projet', tout le reste de la place pour les projets deja crees
    (vide s'il n'y en a pas). On peut aussi glisser une video dessus pour creer un projet.
    Apres exec(), self.choice vaut ("create", nom, video) | ("open", chemin) | None."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.choice = None
        self.setWindowTitle(tr("wel.title"))
        self.setAcceptDrops(True)
        self.resize(940, 620)
        self.setMinimumSize(700, 440)
        v = QVBoxLayout(self)
        v.setContentsMargins(22, 18, 22, 14)
        v.setSpacing(12)
        top = QHBoxLayout()
        self.btn_new = QPushButton(tr("home.new"))
        self.btn_new.setObjectName("primary")
        self.btn_new.setCursor(Qt.PointingHandCursor)
        self.btn_new.setStyleSheet("padding: 11px 20px; font-size: 11pt;")
        top.addWidget(self.btn_new)
        top.addStretch(1)
        v.addLayout(top)
        self.lst = QListWidget()
        self.lst.setSpacing(2)
        self.lst.setVerticalScrollMode(QListWidget.ScrollPerPixel)
        self.lst.setContextMenuPolicy(Qt.CustomContextMenu)
        self.lst.customContextMenuRequested.connect(self._menu)
        v.addWidget(self.lst, 1)
        self.lbl_drop = QLabel(tr("home.drop"), self.lst.viewport())
        self.lbl_drop.setAlignment(Qt.AlignCenter)
        self.lbl_drop.setStyleSheet(f"font-size: 15pt; font-weight: 700; color: {col('accent').name()}; background: transparent;")
        self.lbl_drop.hide()
        bottom = QHBoxLayout()
        bottom.addStretch(1)
        self.btn_skip = QPushButton(tr("home.skip"))
        self.btn_skip.setObjectName("nav")
        self.btn_skip.setCursor(Qt.PointingHandCursor)
        bottom.addWidget(self.btn_skip)
        v.addLayout(bottom)
        self._css(False)
        self._fill()
        self.btn_new.clicked.connect(self._new)
        self.btn_skip.clicked.connect(self.reject)
        self.lst.itemActivated.connect(self._item)
        self.lst.itemDoubleClicked.connect(self._item)

    def _css(self, drag):
        border = f"2px dashed {col('accent').name()}" if drag else "1px solid transparent"
        self.lst.setStyleSheet(
            f"QListWidget {{ background: transparent; border: {border}; border-radius: 12px; outline: 0; }}"
            f"QListWidget::item {{ background: {col('card').name()}; border: 1px solid {col('border').name()}; "
            f"border-radius: 10px; margin: 3px 2px; }}"
            f"QListWidget::item:hover {{ border-color: {col('accent_soft').name()}; }}"
            f"QListWidget::item:selected {{ border-color: {col('accent').name()}; }}")

    def _fill(self):
        self.lst.clear()
        for info in projects.list_projects(settings.projects_root()):
            it = QListWidgetItem()
            it.setData(Qt.UserRole, info["path"])
            it.setToolTip(info["path"])
            w = QWidget()
            row = QHBoxLayout(w)
            row.setContentsMargins(14, 10, 10, 10)
            row.setSpacing(8)
            lay = QVBoxLayout()
            lay.setSpacing(2)
            name = QLabel(info["name"])
            name.setFont(_font(12, True))
            when = time.strftime("%Y-%m-%d %H:%M", time.localtime(info["mtime"]))
            meta = tr("home.meta", when=when, n=info["n_clips"]) if info["n_clips"] else when + "   ·   " + tr("home.empty")
            if info["source"]:
                meta += "   ·   🎬 " + os.path.basename(info["source"])
            sub = QLabel(meta)
            sub.setObjectName("muted")
            lay.addWidget(name)
            lay.addWidget(sub)
            if info["missing"]:                                 # fichiers qui n'existent plus : bien visible
                names = ", ".join(os.path.basename(f) for f in info["missing"][:2]) + (" …" if len(info["missing"]) > 2 else "")
                warn = QLabel(tr("home.missing", n=len(info["missing"]), names=names))
                warn.setStyleSheet("color: #ff6b5e; font-weight: 600;")
                warn.setToolTip("\n".join(info["missing"]))
                lay.addWidget(warn)
            row.addLayout(lay, 1)
            for glyph, tip, fn in (("⧉", "home.duplicate", lambda _=False, p=info["path"]: self._duplicate(p)),
                                   ("🗑", "home.delete", lambda _=False, p=info["path"]: self._delete(p))):
                b_ = QPushButton(glyph)
                b_.setObjectName("tool")
                b_.setFixedSize(34, 30)
                b_.setToolTip(tr(tip))
                b_.setCursor(Qt.PointingHandCursor)
                b_.clicked.connect(fn)
                row.addWidget(b_, 0, Qt.AlignVCenter)
            if info["missing"]:
                w.setStyleSheet("")
            it.setSizeHint(w.sizeHint() + QSize(0, 6))
            self.lst.addItem(it)
            self.lst.setItemWidget(it, w)

    def _done(self, choice):
        self.choice = choice
        self.accept()

    def _item(self, it):
        path = it.data(Qt.UserRole)
        if path:
            self._done(("open", path))

    def _menu(self, pos):
        it = self.lst.itemAt(pos)
        if it is None:
            return
        m = QMenu(self)
        a_open = m.addAction(tr("home.open"))
        a_dup = m.addAction(tr("home.duplicate"))
        a_del = m.addAction(tr("home.delete"))
        a_dir = m.addAction(tr("home.folder"))
        act = m.exec(self.lst.viewport().mapToGlobal(pos))
        path = it.data(Qt.UserRole)
        if act is a_open:
            self._done(("open", path))
        elif act is a_dup:
            self._duplicate(path)
        elif act is a_del:
            self._delete(path)
        elif act is a_dir and hasattr(os, "startfile"):
            os.startfile(os.path.dirname(path))

    def keyPressEvent(self, e):
        it = self.lst.currentItem()
        if e.key() == Qt.Key_Delete and it is not None and it.data(Qt.UserRole):
            self._delete(it.data(Qt.UserRole))
            return
        super().keyPressEvent(e)

    def _confirm_delete(self, name):
        box = QMessageBox(QMessageBox.Warning, tr("home.delete"), tr("home.delete.ask", name=name), QMessageBox.NoButton, self)
        yes = box.addButton(tr("btn.delete"), QMessageBox.DestructiveRole)
        box.addButton(tr("btn.cancel"), QMessageBox.RejectRole)
        box.exec()
        return box.clickedButton() is yes

    def _delete(self, path):
        info = projects.read_info(path)
        name = info["name"] if info else os.path.basename(path)
        if not self._confirm_delete(name):
            return
        if not projects.delete(settings.projects_root(), path):
            QMessageBox.warning(self, tr("home.delete"), tr("home.delete.fail"))
        self._fill()

    def _duplicate(self, path):
        root = settings.projects_root()
        info = projects.read_info(path)
        base = (info["name"] if info else os.path.basename(path)) + " " + tr("home.copy.suffix")
        name, ok = QInputDialog.getText(self, tr("home.duplicate"), tr("np.name"), text=projects.unique_name(root, base))
        name = name.strip()
        if not ok or not name:
            return
        if projects.exists(root, name):
            QMessageBox.warning(self, tr("home.duplicate"), tr("np.exists", name=name))
            return
        try:
            projects.duplicate(root, path, name)
        except (OSError, ValueError) as e:
            QMessageBox.warning(self, tr("home.duplicate"), str(e))
            return
        self._fill()

    def _new(self, video=""):
        d = NewProjectDialog(self, video=video if isinstance(video, str) else "")
        if d.exec():
            name, vid = d.values()
            self._done(("create", name, vid))

    # ---- glisser une video : cree un projet (on lui donne son nom) ; glisser un projet : l'ouvre
    def dragEnterEvent(self, e):
        if e.mimeData().hasUrls():
            e.acceptProposedAction()
            self._css(True)
            self.lbl_drop.setGeometry(self.lst.viewport().rect())
            self.lbl_drop.show()

    def dragLeaveEvent(self, e):
        self._end_drag()

    def _end_drag(self):
        self._css(False)
        self.lbl_drop.hide()

    def dropEvent(self, e):
        self._end_drag()
        from .editor import kind_from_path
        for u in e.mimeData().urls():
            if not u.isLocalFile():
                continue
            path = os.path.normpath(u.toLocalFile())
            if path.endswith(projects.EXT):
                self._done(("open", path))
            elif kind_from_path(path) == "video":
                self._new(path)
            else:
                continue
            return


class ThemeChip(QAbstractButton):
    """Un theme : petit apercu de ses couleurs, avec son nom ecrit dessous."""
    hovered = Signal(object)
    W, H = 132, 66

    def __init__(self, key):
        super().__init__()
        self.key = key
        self.setCheckable(True)
        self.setCursor(Qt.PointingHandCursor)
        self.setFixedSize(self.W, self.H)
        self.setToolTip(tr("theme." + key))

    def enterEvent(self, e):
        self.hovered.emit(self.key)
        super().enterEvent(e)

    def leaveEvent(self, e):
        self.hovered.emit(None)
        super().leaveEvent(e)

    def paintEvent(self, _):
        t = theme.THEMES[self.key]
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        r = QRectF(self.rect()).adjusted(2, 2, -2, -2)
        sel = self.isChecked()
        p.setBrush(QColor(t["bg"]))
        p.setPen(QPen(QColor(t["accent"]) if sel else QColor(t["border"]), 3 if sel else 1.2))
        p.drawRoundedRect(r, 9, 9)
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(t["header"]))
        p.drawRoundedRect(QRectF(r.left() + 4, r.top() + 4, r.width() - 8, 9), 4, 4)
        for i, c in enumerate(("cut", "handle", "accent")):
            p.setBrush(QColor(t[c]))
            p.drawEllipse(QPointF(r.left() + 11 + i * 8, r.top() + 8.5), 2.2, 2.2)
        p.setBrush(QColor(t["card"]))
        p.drawRoundedRect(QRectF(r.left() + 4, r.top() + 16, r.width() - 8, 14), 4, 4)
        for i, c in enumerate(("accent", "accent_soft", "wave")):
            p.setBrush(QColor(t[c]))
            p.drawRoundedRect(QRectF(r.left() + 9 + i * 20, r.top() + 20, 16, 6), 3, 3)
        name = QRectF(r.left() + 4, r.bottom() - 25, r.width() - 8, 21)
        p.setBrush(QColor(t["card"]))
        p.drawRoundedRect(name, 4, 4)
        _text(p, name.adjusted(3, 0, -3, 0), tr("theme." + self.key), QColor(t["text"]), 8.5, True)


class OptionsDialog(QDialog):
    SCALES = [90, 100, 115, 130]

    @staticmethod
    def _card(title):
        card = QFrame()
        card.setObjectName("card")
        v = QVBoxLayout(card)
        v.setContentsMargins(16, 14, 16, 14)
        v.setSpacing(8)
        t = QLabel(title)
        t.setObjectName("cardtitle")
        v.addWidget(t)
        return card, v

    def __init__(self, win):
        super().__init__(win)
        self.win = win
        self.reopen = False          # vrai si la langue change : on reouvre la fenetre dans la nouvelle langue
        self.setWindowTitle(tr("dlg.options.title"))
        avail = QGuiApplication.primaryScreen().availableGeometry()
        self.resize(min(960, int(avail.width() * 0.92)), min(640, int(avail.height() * 0.9)))
        lay = QVBoxLayout(self)
        lay.setContentsMargins(22, 16, 22, 14)
        lay.setSpacing(10)
        title = QLabel(tr("opt.title"))
        title.setObjectName("helptitle")
        lay.addWidget(title)

        inner = QWidget()
        iv = QVBoxLayout(inner)
        iv.setContentsMargins(0, 0, 0, 0)
        iv.setSpacing(12)

        # ---- themes : tries jour / nuit, chacun avec son nom
        card, cv = self._card(tr("opt.theme"))
        self._theme_title = cv.itemAt(0).widget()
        self.group = QButtonGroup(self)
        self.group.setExclusive(True)
        cols = max(3, (self.width() - 110) // (ThemeChip.W + 10))
        for title_key, dark in (("opt.theme.day", False), ("opt.theme.night", True)):
            keys = [k for k, t in theme.THEMES.items() if bool(t["dark"]) == dark]
            head = QLabel(f"{'☾' if dark else '☀'}  {tr(title_key)}  ({len(keys)})")
            head.setObjectName("sub")
            cv.addWidget(head)
            grid = QGridLayout()
            grid.setSpacing(8)
            grid.setAlignment(Qt.AlignLeft | Qt.AlignTop)
            for i, key in enumerate(keys):
                chip = ThemeChip(key)
                chip.setChecked(key == theme.current_name())
                chip.clicked.connect(lambda _=False, k=key: self._theme_chosen(k))
                chip.hovered.connect(self._show_theme_name)
                self.group.addButton(chip)
                grid.addWidget(chip, i // cols, i % cols)
            cv.addLayout(grid)
        self._show_theme_name(None)
        iv.addWidget(card)

        # ---- affichage | comportement : cote a cote
        row = QHBoxLayout()
        row.setSpacing(12)
        card2, v2 = self._card(tr("opt.display"))
        self.chk_fs = QCheckBox(tr("opt.fullscreen"))
        self.chk_fs.setChecked(settings.get("fullscreen"))
        self.chk_fs.toggled.connect(lambda v: settings.put("fullscreen", v))
        note = QLabel(tr("opt.fs.note"))
        note.setObjectName("muted")
        note.setWordWrap(True)
        v2.addWidget(self.chk_fs)
        v2.addWidget(note)

        lrow = QHBoxLayout()
        lrow.addWidget(QLabel(tr("opt.language")))
        self.cb_lang = QComboBox()
        for code in i18n.CODES:
            self.cb_lang.addItem(i18n.NAMES[code], code)
        self.cb_lang.setCurrentIndex(i18n.CODES.index(i18n.lang()))
        self.cb_lang.activated.connect(self._lang_chosen)
        lrow.addWidget(self.cb_lang, 1)
        v2.addLayout(lrow)

        srow = QHBoxLayout()
        srow.addWidget(QLabel(tr("opt.scale")))
        self.cb_scale = QComboBox()
        for val in self.SCALES:
            self.cb_scale.addItem(tr(f"scale.{val}"), val)
        cur = settings.get("scale")
        self.cb_scale.setCurrentIndex(self.SCALES.index(cur) if cur in self.SCALES else 1)
        self.cb_scale.activated.connect(lambda _: self.win.set_scale(self.cb_scale.currentData()))
        srow.addWidget(self.cb_scale, 1)
        v2.addLayout(srow)
        v2.addStretch(1)
        row.addWidget(card2, 1)

        card3, v3 = self._card(tr("opt.defaults"))
        for key, text in (("skip", tr("chk.skip")), ("tint", tr("opt.tint")), ("separate", tr("opt.sep"))):
            c = QCheckBox(text)
            c.setChecked(settings.get(key))
            c.toggled.connect(lambda v, k=key: self.win.apply_pref(k, v))
            v3.addWidget(c)
        v3.addStretch(1)
        row.addWidget(card3, 1)
        iv.addLayout(row)

        # ---- dossier des projets : un sous-dossier par video, avec les XML dedans
        card4, v4 = self._card(tr("opt.projects"))
        self.lbl_proj = QLabel(settings.projects_root())
        self.lbl_proj.setWordWrap(True)
        self.lbl_proj.setTextInteractionFlags(Qt.TextSelectableByMouse)
        note4 = QLabel(tr("opt.projects.note"))
        note4.setObjectName("muted")
        note4.setWordWrap(True)
        b_choose = QPushButton(tr("btn.choose"))
        b_open = QPushButton(tr("btn.openfolder"))
        b_choose.clicked.connect(self._choose_projects)
        b_open.clicked.connect(self._open_projects)
        prow = QHBoxLayout()
        prow.addWidget(self.lbl_proj, 1)
        prow.addWidget(b_choose)
        prow.addWidget(b_open)
        v4.addLayout(prow)
        v4.addWidget(note4)
        iv.addWidget(card4)
        iv.addStretch(1)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        scroll.setWidget(inner)
        lay.addWidget(scroll, 1)

        # bouton toujours visible, en dehors de la zone defilante
        close = QPushButton(tr("btn.close"))
        close.setObjectName("primary")
        close.setMinimumWidth(130)
        close.setDefault(True)
        close.clicked.connect(self.accept)
        foot = QHBoxLayout()
        foot.addStretch()
        foot.addWidget(close)
        lay.addLayout(foot)

    def _choose_projects(self):
        path = QFileDialog.getExistingDirectory(self, tr("dlg.projects.choose"), settings.projects_root())
        if path:
            settings.put("projects_dir", os.path.normpath(path))
            self.lbl_proj.setText(settings.projects_root())

    def _open_projects(self):
        root = settings.projects_root()
        os.makedirs(root, exist_ok=True)
        if hasattr(os, "startfile"):
            os.startfile(root)

    def _show_theme_name(self, key):
        name = tr("theme." + (key or theme.current_name()))
        self._theme_title.setText(f"{tr('opt.theme')}  ·  {name}")

    def _theme_chosen(self, key):
        self.win.set_theme(key)
        self._show_theme_name(None)

    def _lang_chosen(self, _):
        code = self.cb_lang.currentData()
        if code != i18n.lang():
            self.win.set_language(code)
            self.reopen = True
            self.accept()
