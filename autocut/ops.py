"""Operations de montage SANS fenetre : couper, supprimer des passages, boucher les vides.
L'editeur graphique et la ligne de commande (cli.py) utilisent exactement les memes fonctions."""
import os
from copy import deepcopy

from .project import MIN_DUR, Clip, Project, seg_window


def split_clips(project, t, base, min_dur=MIN_DUR):
    """Coupe a l'instant t (secondes) les elements de `base` qui passent par la. Les elements lies forment, pour la moitie
    de droite, un nouveau groupe lie. Renvoie le nombre d'elements coupes."""
    targets = [c for c in base if c.start + min_dur <= t <= c.end - min_dur]
    new_links = {}
    for c in targets:
        n = deepcopy(c)
        n.id = Clip("text", "V1", 0, 1).id
        old_end, local = c.end, t - c.start
        if c.segments:                                   # clip compose : chaque moitie garde ses passages
            left = seg_window(c.segments, 0.0, local, 0.0)
            right = seg_window(c.segments, local, c.duration, 0.0)
            c.segments, n.segments = left, right
            c.src_in, n.src_in = left[0][0], right[0][0]
        elif c.kind in ("video", "audio"):
            n.src_in = c.src_in + local
        n.start, n.duration = t, old_end - t
        if c.link:
            n.link = new_links.setdefault(c.link, Project.new_link())
        c.duration = local
        project.clips.append(n)
    return len(targets)


def plan_ranges(project, ranges):
    """Intervalles (a, b) a retirer, bornes et nettoyes. b depasse la fin => jusqu'a la fin du montage."""
    total = project.duration()
    out = []
    for a, b in sorted(ranges):
        a, b = max(0.0, a), (total + 1.0 if b >= total - 0.05 else b)
        if b - a > 1e-3:
            out.append((a, b))
    return out if total > 0 else []


def cut_ranges(project, ranges):
    """Retire des intervalles de temps (secondes) de TOUT le montage : toutes les pistes sont coupees aux deux bouts,
    ce qui est dedans disparait, et la suite est rapprochee (aucun vide). Renvoie les secondes retirees."""
    todo = plan_ranges(project, ranges)
    total = project.duration()
    removed = 0.0
    for a, b in sorted(todo, reverse=True):               # de la fin vers le debut : le debut ne bouge pas
        split_clips(project, b, list(project.clips), 1e-3)
        split_clips(project, a, list(project.clips), 1e-3)
        keep, gap = [], min(b, total) - a
        for c in project.clips:
            if c.start >= a - 1e-6 and c.end <= b + 1e-6:
                continue                                   # entierement dans l'intervalle : supprime
            if c.start >= b - 1e-6:
                c.start = max(0.0, c.start - gap)
            keep.append(c)
        project.clips = keep
        removed += gap
    return removed


def find_gaps(project, leading=True):
    """Vides ou AUCUNE piste n'a quelque chose : liste de (debut, fin). leading : y compris avant le premier element."""
    iv = sorted((c.start, c.end) for c in project.clips)
    gaps, cur = [], 0.0
    for k, (s, e) in enumerate(iv):
        if s > cur + 1e-4 and (k > 0 or leading):
            gaps.append((cur, s))
        cur = max(cur, e)
    return gaps


def close_gaps(project, leading=True):
    """Colle tous les morceaux (les pistes restent synchronisees). Renvoie (nombre de vides, secondes retirees)."""
    gaps = find_gaps(project, leading)
    for a, b in sorted(gaps, reverse=True):
        for c in project.clips:
            if c.start >= b - 1e-6:
                c.start = max(0.0, c.start - (b - a))
    return len(gaps), sum(b - a for a, b in gaps)


def source_to_timeline(project, path, ranges):
    """Intervalles (secondes) d'un FICHIER SOURCE -> intervalles du montage, d'apres les clips de ce fichier (video d'abord,
    sinon son). Les intervalles qui ne sont plus dans le montage sont ignores."""
    p_abs = os.path.abspath(path)
    refs = [c for c in project.clips if c.kind == "video" and os.path.abspath(c.path) == p_abs] or \
           [c for c in project.clips if c.kind == "audio" and os.path.abspath(c.path) == p_abs]
    tl = []
    for c in refs:
        acc = c.start
        for s0, e0 in (c.segments or [[c.src_in, c.src_in + c.duration]]):
            for a, b in ranges:
                x0, x1 = max(a, s0), min(b, e0)
                if x1 - x0 > 1e-4:
                    tl.append((acc + x0 - s0, acc + x1 - s0))
            acc += e0 - s0
    merged = []
    for a, b in sorted(tl):
        if merged and a <= merged[-1][1] + 1e-6:
            merged[-1] = (merged[-1][0], max(merged[-1][1], b))
        else:
            merged.append((a, b))
    return merged


def timeline_to_source(project, path, t):
    """Instant du montage -> instant du fichier source `path` (None si ce fichier n'est pas joue a cet instant)."""
    p_abs = os.path.abspath(path)
    for c in project.clips:
        if c.kind == "video" and c.start <= t < c.end and os.path.abspath(c.path) == p_abs:
            acc = c.start
            for s0, e0 in (c.segments or [[c.src_in, c.src_in + c.duration]]):
                if t < acc + (e0 - s0):
                    return s0 + (t - acc)
                acc += e0 - s0
    return None
