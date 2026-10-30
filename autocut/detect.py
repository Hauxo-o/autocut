"""Detection des silences a partir de l'enveloppe audio."""
import math
from dataclasses import dataclass, field

import numpy as np

from .ffmpeg_tools import ENV_HOP


@dataclass
class Settings:
    threshold_db: float      # en dessous = silence
    min_silence: float       # duree minimale d'un silence (s)
    margin_before: float     # on garde ce temps AVANT le retour du son (s)
    margin_after: float      # on garde ce temps APRES la fin du son (s)


# Valeurs par defaut de chaque mode (reglables dans l'app)
DEFAULTS = {
    "dead":    Settings(threshold_db=-45.0, min_silence=2.0,  margin_before=0.25, margin_after=0.25),
    "classic": Settings(threshold_db=-35.0, min_silence=0.35, margin_before=0.08, margin_after=0.08),
}
MODE_NAMES = {"dead": "Temps morts", "classic": "Classique"}


@dataclass
class Cut:
    start: int               # premiere image supprimee
    end: int                 # image apres la derniere supprimee (fin exclue)
    enabled: bool = True     # False = coupe annulee, on garde ce passage
    cid: int = field(default=0, compare=False)

    def __post_init__(self):
        # valeurs trouvees par la detection : sert au bouton "Reinitialiser"
        self.orig_start, self.orig_end = self.start, self.end

    @property
    def frames(self):
        return self.end - self.start


def detect_cuts(env_db, s, fps, total_frames):
    """Renvoie la liste des coupes (en numeros d'image) pour ces reglages."""
    fps = float(fps)
    mask = env_db < s.threshold_db
    if not len(mask):
        return []
    d = np.diff(np.concatenate(([0], mask.astype(np.int8), [0])))
    starts, ends = np.where(d == 1)[0], np.where(d == -1)[0]
    cuts = []
    for a, b in zip(starts, ends):
        t0, t1 = a * ENV_HOP, b * ENV_HOP
        if t1 - t0 < s.min_silence:
            continue
        # La marge ne s'applique pas au tout debut / toute fin de la video
        c0 = t0 + (s.margin_after if a > 0 else 0.0)
        c1 = t1 - (s.margin_before if b < len(mask) else 0.0)
        # On arrondit vers l'interieur de la zone gardee : on coupe un peu moins plutot que trop.
        f0 = max(0, math.ceil(c0 * fps - 1e-6))
        f1 = min(total_frames, math.floor(c1 * fps + 1e-6))
        if b >= len(mask) - 1:
            f1 = total_frames
        if f1 - f0 >= 2:
            cuts.append(Cut(f0, f1))
    for i, c in enumerate(cuts):
        c.cid = i + 1
    return cuts


def normalize_cuts(cuts, total_frames):
    """Garde les coupes actives, bornees, triees, sans chevauchement."""
    act = sorted((max(0, c.start), min(total_frames, c.end)) for c in cuts if c.enabled)
    merged = []
    for a, b in act:
        if b - a < 1:
            continue
        if merged and a <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], b)
        else:
            merged.append([a, b])
    return [(a, b) for a, b in merged]
