"""Projets : un dossier par projet dans le dossier des projets, avec le fichier <nom>.autocut.json dedans
(les exports XML / MP4 du projet vont dans le meme dossier)."""
import json
import os

from .export import safe_name

EXT = ".autocut.json"


def project_folder(root, name):
    return os.path.join(root, safe_name(name))


def project_file(root, name):
    return os.path.join(project_folder(root, name), safe_name(name) + EXT)


def exists(root, name):
    """Un projet de ce nom existe-t-il deja ?"""
    folder = project_folder(root, name)
    return os.path.isdir(folder) and any(f.endswith(EXT) for f in os.listdir(folder))


def unique_name(root, name):
    """'Nom', sinon 'Nom (2)', 'Nom (3)'... : un nom de projet encore libre."""
    name = name.strip() or "Projet"
    cand, n = name, 2
    while exists(root, cand):
        cand = f"{name} ({n})"
        n += 1
    return cand


def read_info(path):
    """Resume d'un fichier de projet : nom, video d'origine, nombre d'elements, date. None si illisible."""
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        return dict(name=data.get("name") or os.path.basename(path)[:-len(EXT)], path=path, source=data.get("source", ""),
                    n_clips=len(data.get("clips", [])), mtime=os.path.getmtime(path))
    except (OSError, ValueError, AttributeError):
        return None


def list_projects(root):
    """Les projets existants, du plus recent au plus ancien. Un projet = un sous-dossier qui contient un .autocut.json
    (le plus recent s'il y en a plusieurs) ; les anciens fichiers du dossier 'Montage' comptent chacun pour un projet."""
    out = []
    if not os.path.isdir(root):
        return out
    for d in sorted(os.listdir(root)):
        folder = os.path.join(root, d)
        if not os.path.isdir(folder):
            continue
        files = [os.path.join(folder, f) for f in os.listdir(folder) if f.endswith(EXT)]
        if d == "Montage":                                   # ancien dossier commun : un projet par fichier
            picks = files
        else:
            picks = [max(files, key=os.path.getmtime)] if files else []
        for f in picks:
            info = read_info(f)
            if info:
                out.append(info)
    return sorted(out, key=lambda i: i["mtime"], reverse=True)
