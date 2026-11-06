"""Ducking (sidechain) : la piste de voix baisse le volume des autres pistes choisies pendant qu'elle parle.
On calcule une courbe de volume (1 = normal, moins = baisse) a partir du niveau de la voix ; la meme courbe sert
a l'apercu (volume des lecteurs) et a l'export (fichier son de courbe multiplie avec le mixage des pistes baissees)."""
import os
import subprocess
import wave

import numpy as np

from .ffmpeg_tools import _NOWIN, find_tool
from .idle import pieces

RATE = 50                        # points de courbe par seconde (un tous les 20 ms)
ATTACK = 0.05                    # vitesse de la baisse (s)
HOLD = 0.25                      # on garde la baisse un peu apres la fin d'un mot (s)
LOOKAHEAD = 0.06                 # la baisse commence juste avant la voix (s)
_cache = {}                      # niveaux deja calcules : (fichier, piste, debut, fin) -> dB par tranche de 20 ms


def _levels(path, idx, s, e):
    key = (os.path.abspath(path), idx, round(s, 3), round(e, 3), os.path.getmtime(path))
    if key in _cache:
        return _cache[key]
    r = subprocess.run([find_tool("ffmpeg"), "-v", "error", "-ss", f"{s:.3f}", "-t", f"{e - s:.3f}", "-i", path, "-map", f"0:a:{idx}",
                        "-vn", "-ac", "1", "-ar", "8000", "-f", "s16le", "-"], capture_output=True, creationflags=_NOWIN)
    raw = r.stdout if r.returncode == 0 else b""
    x = np.frombuffer(raw[: len(raw) // 2 * 2], np.int16).astype(np.float32) / 32768.0
    n = 8000 // RATE
    k = len(x) // n
    out = (10 * np.log10((x[: k * n].reshape(k, n) ** 2).mean(axis=1) + 1e-12)) if k else np.zeros(0)
    if len(_cache) > 400:
        _cache.clear()
    _cache[key] = out
    return out


def target_tracks(project):
    """Pistes baissees par la voix : les pistes son choisies, sauf la piste de voix elle-meme."""
    return [t for t in project.audio_tracks() if t != project.duck_source and project.duck_targets.get(t, True)]


def active(project):
    return bool(project.duck_on and project.duck_source in project.audio_tracks() and target_tracks(project))


def voice_db(project, total):
    """Niveau de la voix (dB) tous les 20 ms sur tout le montage (apres volume du clip, fader de la piste et master)."""
    n = int(total * RATE) + 2
    power = np.zeros(n)
    for c in project.clips:
        if c.track != project.duck_source or not c.has_sound() or not c.path:
            continue
        g = project.gain_db(c)
        if g is None:
            continue
        for off, s, e in pieces(c):
            if e - s < 0.02:
                continue
            lv = _levels(c.path, max(0, c.audio_index), s, e)
            i0 = int(round((c.start + off) * RATE))
            hi = min(n, i0 + len(lv))
            if hi > i0 >= 0:
                power[i0:hi] += 10 ** ((lv[: hi - i0] + g) / 10)
    return 10 * np.log10(power + 1e-12)


def curve(db, thr, amount_db, release):
    """Courbe de volume (1 = normal) : baisse de amount_db (negatif) quand db depasse thr, retour progressif en `release` s."""
    speaking = db > thr
    h = int(round(HOLD * RATE))
    if h and speaking.any():                          # garde la baisse un peu apres la voix
        grown = speaking.copy()
        for k in range(1, h + 1):
            grown[k:] |= speaking[:-k]
        speaking = grown
    la = int(round(LOOKAHEAD * RATE))
    if la:
        speaking = np.concatenate([speaking[la:], np.zeros(la, bool)])
    low = 10 ** (min(0.0, amount_db) / 20)
    target = np.where(speaking, low, 1.0)
    a_att = 1 - np.exp(-1 / max(1e-3, ATTACK * RATE))
    a_rel = 1 - np.exp(-1 / max(1e-3, release * RATE))
    g, out = 1.0, np.empty(len(target))
    for i, t in enumerate(target.tolist()):
        g += (t - g) * (a_att if t < g else a_rel)
        out[i] = g
    return out


def envelope(project, total=None):
    """Courbe de volume du ducking du projet (RATE points par seconde), ou None si le ducking est inactif."""
    if not active(project):
        return None
    total = project.duration() if total is None else total
    return curve(voice_db(project, total), project.duck_thr, project.duck_amount, project.duck_release)


def gain_at(env, t):
    if env is None or len(env) == 0:
        return 1.0
    return float(env[min(len(env) - 1, max(0, int(t * RATE)))])


def write_wav(env, path, total):
    """Courbe -> petit fichier son stereo 1000 Hz (valeur = volume x 32767) que FFmpeg multiplie avec le son."""
    n = int(total * 1000) + 1000
    t = np.arange(n) / 1000.0
    x = np.interp(t, np.arange(len(env)) / RATE, env, right=1.0)
    pcm = np.clip(x * 32767, 0, 32767).astype("<i2")
    with wave.open(path, "wb") as w:
        w.setnchannels(2)
        w.setsampwidth(2)
        w.setframerate(1000)
        w.writeframes(np.repeat(pcm, 2).tobytes())
