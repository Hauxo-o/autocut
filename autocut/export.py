"""Export complet : fichiers audio separes (optionnel) + XML pour DaVinci Resolve."""
import os
import subprocess

from .ffmpeg_tools import _NOWIN, find_tool
from .i18n import tr
from .xml_export import write_xml


_FORBIDDEN = '<>:"/\\|?*'


def safe_name(name):
    """Nom utilisable comme nom de dossier Windows."""
    out = "".join("_" if c in _FORBIDDEN or ord(c) < 32 else c for c in name).strip().rstrip(".")
    return out or "projet"


def project_dir(root, video_name):
    """<root>/<nom de la video sans extension> (sans le creer)."""
    return os.path.join(root, safe_name(os.path.splitext(video_name)[0]))


def unique_path(folder, base, ext=".xml"):
    """Premier nom libre : 'base.xml', puis 'base (2).xml', 'base (3).xml'..."""
    path = os.path.join(folder, base + ext)
    n = 2
    while os.path.exists(path):
        path = os.path.join(folder, f"{base} ({n}){ext}")
        n += 1
    return path


def extract_wav(video_path, audio_index, out_path):
    """Copie UNE piste audio de la video dans un nouveau fichier .wav (la video n'est pas touchee)."""
    if os.path.abspath(out_path) == os.path.abspath(video_path):
        raise ValueError("Securite : on n'ecrit jamais sur la video d'origine.")
    subprocess.run([find_tool("ffmpeg"), "-y", "-v", "error", "-i", video_path, "-map", f"0:a:{audio_index}",
                    "-vn", "-c:a", "pcm_s16le", out_path],
                   capture_output=True, creationflags=_NOWIN, check=True)


def export_xml(media, cuts, mode, xml_path, separate=True, progress=None):
    """
    mode     : 'mark' (coupes seulement) ou 'remove' (supprimer)
    separate : True = chaque piste audio est extraite dans un .wav (video et audio non lies)
    Renvoie le dossier des audios (ou None).
    """
    audio_files, folder = None, None
    if separate:
        vbase = os.path.splitext(media["name"])[0]
        # Dossier lie a la video (pas au XML) : les audios sont crees une fois et reutilises ensuite.
        folder = os.path.join(os.path.dirname(os.path.abspath(xml_path)), vbase + " - audio")
        os.makedirs(folder, exist_ok=True)
        audio_files = []
        n = len(media["audio"])
        for a in media["audio"]:
            k = a["audio_index"] + 1
            name = f"{vbase} - piste audio {k}.wav"
            out = os.path.join(folder, name)
            fresh = os.path.isfile(out) and os.path.getmtime(out) >= os.path.getmtime(media["path"])                 and os.path.getsize(out) > 1000
            if not fresh:
                if progress:
                    progress(tr("exp.extract", k=k, n=n))
                extract_wav(media["path"], a["audio_index"], out)
            audio_files.append({"name": name, "path": os.path.abspath(out), "channels": a["channels"]})
    if progress:
        progress(tr("exp.xml"))
    write_xml(xml_path, media, cuts, mode, audio_files)
    return folder
