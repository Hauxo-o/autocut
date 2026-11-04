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
        files = {c.get("path") for c in data.get("clips", []) if c.get("kind") in ("video", "image", "audio") and c.get("path")}
        files.add(data.get("source", ""))
        missing = sorted(f for f in files if f and not os.path.isfile(f))          # fichiers qui n'existent plus
        return dict(name=data.get("name") or os.path.basename(path)[:-len(EXT)], path=path, source=data.get("source", ""),
                    n_clips=len(data.get("clips", [])), mtime=os.path.getmtime(path), missing=missing)
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


def missing_files(path):
    """Fichiers (videos, images, sons) cites par un projet et qui n'existent plus sur le disque."""
    info = read_info(path)
    return info["missing"] if info else []


def duplicate(root, path, new_name):
    """Copie le projet (son fichier : montage, reglages, video d'origine) sous un autre nom, dans son propre dossier.
    Les exports du projet (XML, MP4...) ne sont pas recopies : ils peuvent etre tres gros. Renvoie le nouveau fichier."""
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    data["name"] = new_name
    dest = project_file(root, new_name)
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    with open(dest, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=1)
    return dest


def trash(path):
    """Envoie un fichier ou un dossier dans la Corbeille de Windows (on peut le retrouver). True si c'est fait."""
    if os.name != "nt":
        return False
    import ctypes
    from ctypes import wintypes

    class SHFILEOPSTRUCTW(ctypes.Structure):
        _fields_ = [("hwnd", wintypes.HWND), ("wFunc", wintypes.UINT), ("pFrom", wintypes.LPCWSTR),
                    ("pTo", wintypes.LPCWSTR), ("fFlags", ctypes.c_ushort), ("fAnyOperationsAborted", wintypes.BOOL),
                    ("hNameMappings", ctypes.c_void_p), ("lpszProgressTitle", wintypes.LPCWSTR)]
    # FO_DELETE, avec FOF_ALLOWUNDO (Corbeille) + sans fenetre de confirmation (on a deja demande)
    op = SHFILEOPSTRUCTW(None, 3, os.path.abspath(path) + "\0", None, 0x40 | 0x10 | 0x4 | 0x400, False, None, None)
    return ctypes.windll.shell32.SHFileOperationW(ctypes.byref(op)) == 0 and not op.fAnyOperationsAborted


def delete(root, path, trasher=None):
    """Supprime un projet : son dossier (fichier de projet + exports) part dans la Corbeille. Les videos d'origine ne sont
    jamais touchees (elles ne sont pas dans le dossier du projet). Ancien dossier commun 'Montage' : seulement le fichier.
    Refuse tout ce qui est hors du dossier des projets. Renvoie True si c'est fait."""
    trasher = trasher or trash
    root, path = os.path.abspath(root), os.path.abspath(path)
    folder = os.path.dirname(path)
    inside = os.path.commonpath([root, folder]) == root
    if not inside or not path.endswith(EXT) or not os.path.isfile(path):
        return False
    target = path if (os.path.basename(folder) == "Montage" or folder == root) else folder
    return bool(trasher(target))
