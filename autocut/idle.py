"""Trouve les moments ou il ne se passe RIEN : pas de son sur les pistes choisies (voix, jeu...) ET une image figee.
Les pistes ignorees (la musique de fond par exemple) ne comptent pas. Ne modifie jamais les videos.
Les resultats sont des intervalles en secondes du MONTAGE, a donner a ops.cut_ranges."""
import os
import subprocess

import numpy as np

from .ffmpeg_tools import _NOWIN, find_tool

STEP = 0.1                       # on regarde le montage par tranches de 0,1 s
PICTURE_THRESHOLDS = {"low": 3.0, "normal": 1.5, "high": 0.7}   # difference moyenne d'image (0-255) = "ca bouge"


def pieces(c):
    """Morceaux d'un element : [(decalage dans l'element, debut source, fin source)]."""
    if c.segments:
        out, off = [], 0.0
        for a, b in c.segments:
            out.append((off, a, b))
            off += b - a
        return out
    return [(0.0, c.src_in, c.src_in + c.duration)]


def _run(cmd, should_stop):
    p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, creationflags=_NOWIN)
    chunks = []
    while True:
        b = p.stdout.read(1 << 20)
        if not b:
            break
        chunks.append(b)
        if should_stop and should_stop():
            p.kill()
            raise InterruptedError("stopped")
    p.wait()
    return b"".join(chunks)


def audio_levels(path, audio_index, s, e, should_stop=None):
    """Niveau (dB) de chaque tranche de 0,1 s du morceau [s, e] du fichier, piste audio audio_index."""
    raw = _run([find_tool("ffmpeg"), "-v", "error", "-ss", f"{s:.3f}", "-t", f"{e - s:.3f}", "-i", path, "-map", f"0:a:{audio_index}",
                "-vn", "-ac", "1", "-ar", "8000", "-f", "s16le", "-"], should_stop)
    x = np.frombuffer(raw[: len(raw) // 2 * 2], np.int16).astype(np.float32) / 32768.0
    n = int(round(STEP * 8000))
    k = len(x) // n
    if k == 0:
        return np.zeros(0)
    rms = np.sqrt((x[: k * n].reshape(k, n) ** 2).mean(axis=1) + 1e-12)
    return 20 * np.log10(rms)


def picture_motion(path, s, e, should_stop=None):
    """Pour chaque tranche de 0,1 s : de combien l'image a change (0 = figee). Petites images grises, tres rapide."""
    w, h = 64, 36
    raw = _run([find_tool("ffmpeg"), "-v", "error", "-ss", f"{s:.3f}", "-t", f"{e - s:.3f}", "-i", path, "-an", "-sn",
                "-vf", f"fps={1 / STEP:g},scale={w}:{h},format=gray", "-f", "rawvideo", "-"], should_stop)
    k = len(raw) // (w * h)
    if k == 0:
        return np.zeros(0)
    f = np.frombuffer(raw[: k * w * h], np.uint8).reshape(k, w * h).astype(np.float32)
    d = np.abs(np.diff(f, axis=0)).mean(axis=1)
    return np.concatenate([[d[0] if len(d) else 0.0], d])


def audio_track_list(project):
    """Pistes audio du montage qui apportent du son : [(nom de piste, description)]."""
    seen = {}
    for c in project.clips:
        if c.has_sound() and c.path:
            seen.setdefault(c.track, set()).add(f"{os.path.basename(c.path)} (piste audio {c.audio_index + 1})")
    return [(t, ", ".join(sorted(v))) for t, v in sorted(seen.items())]


def idle_ranges(project, tracks=None, db=-45.0, min_len=1.5, picture="normal", keep=0.15, progress=None, should_stop=None):
    """Moments sans rien : son sous `db` sur les pistes `tracks` (None = toutes) ET image figee (picture = low/normal/high,
    ou None pour ne pas regarder l'image). Garde `keep` secondes de marge de chaque cote. Duree minimale `min_len`.
    Renvoie [(debut, fin)] en secondes du montage."""
    total = project.duration()
    n = int(total / STEP) + 1
    active = np.zeros(n, bool)
    jobs = []
    for c in project.clips:
        if c.kind in ("video", "audio") and c.path:
            if c.has_sound() and (tracks is None or c.track in tracks):
                jobs.append(("a", c))
            if c.kind == "video" and picture:
                jobs.append(("v", c))
    thr = PICTURE_THRESHOLDS.get(picture, 1.5) if picture else None
    for j, (kind, c) in enumerate(jobs):
        for off, s, e in pieces(c):
            if e - s < STEP:
                continue
            i0 = int(round((c.start + off) / STEP))
            if kind == "a":
                lv = audio_levels(c.path, c.audio_index, s, e, should_stop)
                m = lv > db
            else:
                mv = picture_motion(c.path, s, e, should_stop)
                m = mv > thr
            hi = min(n, i0 + len(m))
            if hi > i0 >= 0:
                active[i0:hi] |= m[: hi - i0]
        if progress:
            progress((j + 1) / max(1, len(jobs)))
    pad = int(round(keep / STEP))
    if pad and active.any():
        grown = active.copy()
        for k in range(1, pad + 1):
            grown[k:] |= active[:-k]
            grown[:-k] |= active[k:]
        active = grown
    out, i = [], 0
    while i < n:
        if active[i]:
            i += 1
            continue
        j = i
        while j < n and not active[j]:
            j += 1
        a, b = i * STEP, min(total, j * STEP)
        if b - a >= min_len:
            out.append((a, b))
        i = j
    return out
