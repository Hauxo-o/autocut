"""Projet de montage : des elements (videos, images, textes, sons) poses sur des pistes."""
import json
import os
import uuid
from dataclasses import asdict, dataclass, field, replace

# Les pistes sont creees a la demande : "V1", "V2"... (images, du bas vers le haut) et "A1", "A2"... (sons).
# Une piste vide n'existe pas : elle disparait des qu'elle n'a plus d'element (voir Project.compact).
ROW_H = 44                          # hauteur d'une piste dans la timeline (px) tant qu'on ne l'a pas redimensionnee

FONTS = {   # nom affiche -> fichier de police Windows
    "Arial": "arial.ttf", "Segoe UI": "segoeui.ttf", "Impact": "impact.ttf", "Comic Sans MS": "comic.ttf",
    "Georgia": "georgia.ttf", "Verdana": "verdana.ttf", "Trebuchet MS": "trebuc.ttf",
    "Times New Roman": "times.ttf", "Consolas": "consola.ttf",
}

RESOLUTIONS = [(1920, 1080), (1280, 720), (2560, 1440), (3840, 2160), (1080, 1920)]
FPS_CHOICES = [24, 30, 60]
MIN_DUR = 0.1


def kind_of_track(track):
    return "visual" if track.startswith("V") else "audio"


def track_num(track):
    return int(track[1:])


def seg_total(segs):
    return sum(e - s for s, e in segs)


def src_at(clip, t):
    """Instant du FICHIER SOURCE qui correspond a l'instant t de la timeline pour cet element."""
    local = t - clip.start
    if not clip.segments:
        return clip.src_in + local
    acc = 0.0
    for s, e in clip.segments:
        if local < acc + (e - s):
            return s + max(0.0, local - acc)
        acc += e - s
    return clip.segments[-1][1]


def seg_index_at(clip, t):
    """Numero du passage en cours (clip compose), -1 pour un element simple."""
    if not clip.segments:
        return -1
    local = t - clip.start
    acc = 0.0
    for i, (s, e) in enumerate(clip.segments):
        if local < acc + (e - s):
            return i
        acc += e - s
    return len(clip.segments) - 1


def seg_window(segs, a, b, src_duration=0.0):
    """Nouveaux passages couvrant l'intervalle [a, b) du temps LOCAL (secondes depuis le debut de l'element).
    Avant le debut / apres la fin : on prolonge le 1er / dernier passage dans le fichier.
    Renvoie None si c'est impossible (on sortirait du fichier)."""
    total = seg_total(segs)
    out, acc = [], 0.0
    for s, e in segs:
        x0, x1 = max(a, acc), min(b, acc + (e - s))
        if x1 > x0 + 1e-9:
            out.append([s + (x0 - acc), s + (x1 - acc)])
        acc += e - s
    if not out:
        return None
    if a < 0:
        out[0][0] += a
        if out[0][0] < -1e-9:
            return None
        out[0][0] = max(0.0, out[0][0])
    if b > total:
        out[-1][1] += b - total
        if src_duration > 0 and out[-1][1] > src_duration + 1e-9:
            return None
    return out


@dataclass
class Clip:
    kind: str                       # 'video' | 'image' | 'text' | 'audio'
    track: str
    start: float                    # debut sur la timeline (s)
    duration: float                 # duree sur la timeline (s)
    path: str = ""
    src_in: float = 0.0             # debut dans le fichier source (s) : sert a couper le debut
    src_duration: float = 0.0       # duree totale du fichier source (0 = illimite : image, texte)
    name: str = ""
    volume_db: float = 0.0
    audio_index: int = 0            # quelle piste audio du fichier on utilise
    has_audio: bool = True          # le fichier video contient du son
    mute_audio: bool = False        # on ignore le son de cette video
    text: str = ""
    font: str = "Arial"
    size: int = 64                  # taille en pixels pour une video de 1080 de haut
    color: str = "#ffffff"
    outline: float = 3.0            # epaisseur du contour du texte (px pour une video de 1080 de haut), 0 = aucun
    outline_color: str = "#000000"
    pos_x: float = 50.0             # position du centre, en % de la largeur
    pos_y: float = 50.0             # en % de la hauteur
    scale: float = 100.0            # % (image / video)
    segments: list = field(default_factory=list)   # clip compose : [[debut_source_s, fin_source_s], ...] (les passages gardes d'un fichier, comme dans l'onglet Couper)
    link: str = ""                  # elements lies (une video et ses sons) : ils se deplacent ensemble
    origin: str = ""                # d'ou vient l'element ("cut:<fichier>" = envoye depuis l'onglet Couper)
    id: str = field(default_factory=lambda: uuid.uuid4().hex[:10])

    @property
    def end(self):
        return self.start + self.duration

    def label(self):
        if self.kind == "text":
            return (self.text.strip().splitlines() or [""])[0][:40]
        return self.name or os.path.basename(self.path)

    def source_in(self):
        return self.segments[0][0] if self.segments else self.src_in

    def has_sound(self):
        """Ce clip apporte-t-il du son au montage ?"""
        if self.kind == "audio":
            return not self.mute_audio
        return self.kind == "video" and self.has_audio and not self.mute_audio


class Project:
    def __init__(self):
        self.width, self.height, self.fps = 1920, 1080, 30
        self.clips = []
        self.track_gain = {}                            # dB par piste
        self.track_mute = {}                            # piste coupee (son et image)
        self.track_solo = {}                            # piste isolee (les autres de meme type sont coupees)
        self.track_media = {}                           # False : pas de miniatures / forme d'onde dans la timeline
        self.track_h = {}                               # hauteur de la piste dans la timeline (px)
        self.master_db = 0.0
        self.normalize = True                           # normaliser le son final a l'export (norme YouTube)
        self.norm_lufs = -14.0                          # volume cible (LUFS integres)
        self.norm_tp = -1.0                             # pic maximum autorise (dBTP, "true peak")
        self.path = ""                                  # fichier de projet (.json) si enregistre
        self.name = ""                                  # nom du projet (affiche dans la fenetre d'accueil)
        self.source = ""                                # video d'origine (rouverte dans l'onglet Couper avec le projet)
        self.linked = True                              # False : chaque element est traite seul (pas de groupe)

    # ---------------------------------------------------------------- infos
    def duration(self):
        return max((c.end for c in self.clips), default=0.0)

    def visual_tracks(self):
        """Pistes image/texte/video qui contiennent quelque chose, de la plus basse a la plus haute."""
        return sorted({c.track for c in self.clips if c.track.startswith("V")}, key=track_num)

    def audio_tracks(self):
        return sorted({c.track for c in self.clips if c.track.startswith("A")}, key=track_num)

    def tracks(self):
        """Pistes dans l'ordre d'affichage : images de haut en bas (la plus haute d'abord), puis sons."""
        return self.visual_tracks()[::-1] + self.audio_tracks()

    def compact(self):
        """Renumerote les pistes sans trou (une piste vide disparait) et garde leurs reglages. True si ca a change."""
        mapping = {}
        for names in (self.visual_tracks(), self.audio_tracks()):
            for i, t in enumerate(names):
                mapping[t] = f"{t[0]}{i + 1}"
        changed = False
        for c in self.clips:
            if c.track != mapping[c.track]:
                c.track = mapping[c.track]
                changed = True
        for attr in ("track_gain", "track_mute", "track_solo", "track_media", "track_h"):
            d = getattr(self, attr)
            nd = {mapping[k]: v for k, v in d.items() if k in mapping}
            if nd != d:
                setattr(self, attr, nd)
                changed = True
        return changed

    def free_track(self, prefix, start, end, prefer="low", ignore=None):
        """Une piste ('V' ou 'A') libre entre start et end ; sinon le nom de la prochaine piste a creer."""
        names = self.visual_tracks() if prefix == "V" else self.audio_tracks()
        for t in (names if prefer == "low" else names[::-1]):
            if self.free_at(t, start, end, ignore=ignore):
                return t
        return f"{prefix}{track_num(names[-1]) + 1 if names else 1}"

    def track_active(self, track):
        """La piste est-elle entendue / visible ? (pas coupee, et si un solo existe pour ce type, c'est elle)."""
        if self.track_mute.get(track):
            return False
        if any(v and k.startswith(track[0]) for k, v in self.track_solo.items()):
            return bool(self.track_solo.get(track))
        return True

    def clips_on(self, track):
        return sorted((c for c in self.clips if c.track == track), key=lambda c: c.start)

    def free_at(self, track, start, end, ignore=None):
        """Vrai si [start, end] ne chevauche aucun autre element de la piste.
        ignore : un element, ou une liste d'elements, a ne pas compter."""
        ign = ignore if isinstance(ignore, (list, tuple, set)) else ([ignore] if ignore is not None else [])
        ids = {i.id for i in ign}
        for c in self.clips:
            if c.track == track and c.id not in ids and c.start < end - 1e-6 and c.end > start + 1e-6:
                return False
        return True

    def group_of(self, clip):
        """Les elements lies a celui-ci (lui compris)."""
        if not clip.link or not self.linked:
            return [clip]
        return [c for c in self.clips if c.link == clip.link] or [clip]

    @staticmethod
    def new_link():
        return uuid.uuid4().hex[:8]

    def first_free_start(self, track, wanted, duration):
        """Premier endroit libre a partir de 'wanted' sur la piste."""
        t = max(0.0, wanted)
        for c in self.clips_on(track):
            if c.end <= t + 1e-6:
                continue
            if c.start >= t + duration - 1e-6:
                break
            t = c.end
        return t

    def gain_db(self, clip):
        """Gain total applique au son de ce clip (clip + piste + master), ou None si la piste est muette."""
        if not self.track_active(clip.track):
            return None
        return clip.volume_db + self.track_gain.get(clip.track, 0.0) + self.master_db

    def expanded(self):
        """Copie du projet ou chaque clip compose est remplace par ses passages (pour le rendu FFmpeg)."""
        p = Project()
        p.width, p.height, p.fps = self.width, self.height, self.fps
        p.track_gain, p.track_mute, p.master_db = dict(self.track_gain), dict(self.track_mute), self.master_db
        p.track_solo, p.track_media, p.track_h = dict(self.track_solo), dict(self.track_media), dict(self.track_h)
        p.normalize, p.norm_lufs, p.norm_tp = self.normalize, self.norm_lufs, self.norm_tp
        for c in self.clips:
            if not c.segments:
                p.clips.append(c)
                continue
            cur = c.start
            for s_, e_ in c.segments:
                p.clips.append(replace(c, start=cur, duration=e_ - s_, src_in=s_, segments=[], link="",
                                       id=uuid.uuid4().hex[:10]))
                cur += e_ - s_
        return p

    # ---------------------------------------------------------------- annuler / retablir
    def snapshot(self):
        return {"clips": [asdict(c) for c in self.clips], "width": self.width, "height": self.height,
                "fps": self.fps, "track_gain": dict(self.track_gain), "track_mute": dict(self.track_mute),
                "track_solo": dict(self.track_solo), "track_media": dict(self.track_media),
                "track_h": dict(self.track_h), "master_db": self.master_db,
                "normalize": self.normalize, "norm_lufs": self.norm_lufs, "norm_tp": self.norm_tp}

    def restore(self, snap):
        self.clips = [Clip(**c) for c in snap["clips"]]
        self.width, self.height, self.fps = snap["width"], snap["height"], snap["fps"]
        self.track_gain = dict(snap["track_gain"])
        self.track_mute = dict(snap["track_mute"])
        self.track_solo = dict(snap.get("track_solo", {}))
        self.track_media = dict(snap.get("track_media", {}))
        self.track_h = dict(snap.get("track_h", {}))
        self.master_db = snap["master_db"]
        self.normalize = bool(snap.get("normalize", False))     # anciens projets : comme avant (pas de normalisation)
        self.norm_lufs = float(snap.get("norm_lufs", -14.0))
        self.norm_tp = float(snap.get("norm_tp", -1.0))
        self.compact()                                   # anciens fichiers : pistes V3/V2/V1 vides ou avec des trous

    # ---------------------------------------------------------------- fichier de projet
    def save(self, path):
        with open(path, "w", encoding="utf-8") as f:
            json.dump(dict(self.snapshot(), version=1, name=self.name, source=self.source), f, ensure_ascii=False,
                      indent=1)
        self.path = path

    def load(self, path):
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        known = {k for k in Clip.__dataclass_fields__}
        data["clips"] = [{k: v for k, v in c.items() if k in known} for c in data["clips"]]
        self.restore(data)
        self.path = path
        self.name = data.get("name", "") or os.path.basename(path).replace(".autocut.json", "")
        self.source = data.get("source", "")
