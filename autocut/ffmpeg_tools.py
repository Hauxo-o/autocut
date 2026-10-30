"""Appels a FFmpeg / ffprobe : trouver les programmes, lire les infos, extraire le son."""
import glob
import json
import os
import shutil
import subprocess
import sys
from fractions import Fraction

import numpy as np

# Empeche l'ouverture d'une fenetre noire a chaque appel (Windows).
_NOWIN = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def _base_dir():
    if getattr(sys, "frozen", False):
        return getattr(sys, "_MEIPASS", os.path.dirname(sys.executable))
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def find_tool(name):
    """Cherche ffmpeg.exe / ffprobe.exe : dossier 'bin' de l'app d'abord, puis le PATH, puis winget."""
    exe = name + (".exe" if os.name == "nt" else "")
    base = _base_dir()
    for d in (os.path.join(base, "bin"), base):
        p = os.path.join(d, exe)
        if os.path.isfile(p):
            return p
    p = shutil.which(name)
    if p:
        return p
    local = os.environ.get("LOCALAPPDATA", "")
    pattern = os.path.join(local, "Microsoft", "WinGet", "Packages", "Gyan.FFmpeg*", "*", "bin", exe)
    found = sorted(glob.glob(pattern))
    if found:
        return found[-1]
    raise FileNotFoundError(f"{exe} introuvable. Installe FFmpeg (winget install Gyan.FFmpeg).")


def probe(path):
    """Lit les infos de la video avec ffprobe. Ne modifie jamais le fichier."""
    out = subprocess.run(
        [find_tool("ffprobe"), "-v", "error", "-show_streams", "-show_format", "-of", "json", path],
        capture_output=True, text=True, encoding="utf-8", creationflags=_NOWIN, check=True,
    ).stdout
    info = json.loads(out)
    video = next((s for s in info["streams"] if s["codec_type"] == "video"), None)
    if video is None:
        raise ValueError("Aucune piste video dans ce fichier.")
    num, den = video["r_frame_rate"].split("/")
    fps = Fraction(int(num), int(den))
    duration = float(info["format"]["duration"])
    audios = []
    for s in (s for s in info["streams"] if s["codec_type"] == "audio"):
        audios.append({
            "audio_index": len(audios),          # numero parmi les pistes audio (0, 1, 2...)
            "stream_index": s["index"],
            "codec": s.get("codec_name", "?"),
            "channels": int(s.get("channels", 2)),
            "sample_rate": int(s.get("sample_rate", 48000)),
            "title": s.get("tags", {}).get("title", ""),
        })
    return {
        "path": os.path.abspath(path),
        "name": os.path.basename(path),
        "fps": fps,
        "width": int(video["width"]),
        "height": int(video["height"]),
        "duration": duration,
        "total_frames": int(round(duration * float(fps))),
        "audio": audios,
    }


ENV_HOP = 0.01          # un point d'enveloppe toutes les 10 ms
_ENV_SR = 16000         # on analyse en 16 kHz mono : largement suffisant et rapide


def audio_envelope(path, audio_index):
    """Extrait une piste audio et renvoie son niveau en dB toutes les 10 ms (tableau numpy)."""
    cmd = [find_tool("ffmpeg"), "-v", "error", "-i", path, "-map", f"0:a:{audio_index}",
           "-vn", "-ac", "1", "-ar", str(_ENV_SR), "-f", "f32le", "-"]
    raw = subprocess.run(cmd, capture_output=True, creationflags=_NOWIN, check=True).stdout
    samples = np.frombuffer(raw, dtype=np.float32)
    win = int(_ENV_SR * ENV_HOP)
    n = len(samples) // win
    blocks = samples[: n * win].reshape(n, win)
    rms = np.sqrt(np.mean(blocks.astype(np.float64) ** 2, axis=1))
    return (20.0 * np.log10(rms + 1e-9)).astype(np.float32)
