"""Miniatures video et formes d'onde pour la timeline.
Elles sont calculees en arriere-plan par FFmpeg (une seule fois par fichier) puis gardees en memoire :
la timeline les dessine des qu'elles sont pretes, sans jamais attendre."""
import subprocess
import threading

import numpy as np
from PySide6.QtCore import QObject, Qt, Signal
from PySide6.QtGui import QImage, QPixmap

from .ffmpeg_tools import _NOWIN, find_tool

THUMB_W, THUMB_H = 96, 54
MAX_THUMBS = 300            # au plus 300 miniatures par fichier (une toutes les N secondes)
PEAK_RATE = 50              # points de forme d'onde par seconde
PEAK_SR = 8000              # le son est lu en 8 kHz mono : largement assez pour une forme d'onde


class MediaCache(QObject):
    changed = Signal()      # quelque chose vient d'etre calcule : la timeline doit se redessiner

    def __init__(self):
        super().__init__()
        self._thumbs = {}   # fichier -> "busy" | "fail" | dict(interval, images, pixmaps)
        self._peaks = {}    # (fichier, piste audio) -> "busy" | "fail" | tableau numpy
        self._pix = {}      # image -> miniature

    # ---------------------------------------------------------------- miniatures video
    def thumbs(self, path, duration):
        """Les miniatures du fichier (dict) ou None tant qu'elles ne sont pas pretes."""
        e = self._thumbs.get(path)
        if e is None:
            self._thumbs[path] = "busy"
            threading.Thread(target=self._thumb_job, args=(path, max(1.0, duration)), daemon=True).start()
            return None
        return e if isinstance(e, dict) else None

    def _thumb_job(self, path, duration):
        interval = max(1.0, duration / MAX_THUMBS)
        w, h = THUMB_W, THUMB_H
        vf = (f"fps=1/{interval:.4f},scale={w}:{h}:force_original_aspect_ratio=decrease,"
              f"pad={w}:{h}:(ow-iw)/2:(oh-ih)/2")
        cmd = [find_tool("ffmpeg"), "-v", "error", "-skip_frame", "nokey", "-i", path, "-an", "-sn", "-vf", vf,
               "-f", "rawvideo", "-pix_fmt", "rgb24", "-"]
        try:
            raw = subprocess.run(cmd, capture_output=True, creationflags=_NOWIN, timeout=900).stdout
            size = w * h * 3
            images = [QImage(raw[i * size:(i + 1) * size], w, h, w * 3, QImage.Format_RGB888).copy()
                      for i in range(len(raw) // size)]
            self._thumbs[path] = dict(interval=interval, images=images, pixmaps={}) if images else "fail"
        except Exception:  # noqa: BLE001
            self._thumbs[path] = "fail"
        self.changed.emit()

    @staticmethod
    def thumb_pixmap(entry, src_t):
        """Miniature la plus proche de l'instant src_t du fichier."""
        images = entry["images"]
        i = min(len(images) - 1, max(0, int(src_t / entry["interval"])))
        pm = entry["pixmaps"].get(i)
        if pm is None:
            pm = entry["pixmaps"][i] = QPixmap.fromImage(images[i])
        return pm

    def image(self, path):
        """Miniature d'une image du montage (chargee tout de suite : c'est petit)."""
        pm = self._pix.get(path)
        if pm is None:
            src = QPixmap(path)
            pm = src.scaledToHeight(THUMB_H, Qt.SmoothTransformation) if not src.isNull() else QPixmap()
            self._pix[path] = pm
        return pm if not pm.isNull() else None

    # ---------------------------------------------------------------- forme d'onde
    def peaks(self, path, audio_index):
        """Niveau max (0..1) du son, PEAK_RATE fois par seconde, ou None tant que ce n'est pas pret."""
        key = (path, audio_index)
        e = self._peaks.get(key)
        if e is None:
            self._peaks[key] = "busy"
            threading.Thread(target=self._peak_job, args=(key,), daemon=True).start()
            return None
        return e if isinstance(e, np.ndarray) else None

    def _peak_job(self, key):
        path, idx = key
        cmd = [find_tool("ffmpeg"), "-v", "error", "-i", path, "-map", f"0:a:{idx}", "-vn", "-ac", "1",
               "-ar", str(PEAK_SR), "-f", "f32le", "-"]
        try:
            raw = subprocess.run(cmd, capture_output=True, creationflags=_NOWIN, timeout=900).stdout
            s = np.abs(np.frombuffer(raw, dtype=np.float32))
            blk = PEAK_SR // PEAK_RATE
            n = len(s) // blk
            self._peaks[key] = s[: n * blk].reshape(n, blk).max(axis=1) if n else "fail"
        except Exception:  # noqa: BLE001
            self._peaks[key] = "fail"
        self.changed.emit()


_cache = None


def get_cache():
    global _cache
    if _cache is None:
        _cache = MediaCache()
    return _cache
