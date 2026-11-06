"""Transcription locale de la voix (faster-whisper) : le texte, avec l'heure de chaque mot.
Tout se passe sur l'ordinateur : rien n'est envoye sur Internet (seul le telechargement du modele, une fois).

faster-whisper n'est PAS dans l'exe : il vit dans un dossier a part (par defaut C:\\Users\\<toi>\\AutoCutAI, voir setup_ai.py)
et est lance comme un petit programme separe. Sans lui, Auto Cut marche normalement, sans la transcription."""
import hashlib
import json
import os
import re
import subprocess
import tempfile
import unicodedata

from . import settings
from .ffmpeg_tools import _NOWIN, find_tool

DEFAULT_PROMPT = "Alors, euh, du coup, hum, voilà."          # aide Whisper a ecrire les hesitations (euh, hum)

# Programme lance dans l'environnement d'IA (ecrit dans le dossier d'IA au premier usage)
WORKER = r'''
import json, os, sys, wave
os.environ["HF_HUB_DISABLE_TELEMETRY"] = "1"
import numpy as np
from faster_whisper import WhisperModel
wav, out, model_dir, model_name, lang, prompt, threads = sys.argv[1:8]
with wave.open(wav, "rb") as wf:                      # on decode nous-memes (PyAV 19 casse le decodage de faster-whisper)
    audio = np.frombuffer(wf.readframes(wf.getnframes()), np.int16).astype(np.float32) / 32768.0
dur = max(len(audio) / 16000.0, 0.01)
model = WhisperModel(model_name, device="cpu", compute_type="int8", download_root=model_dir, cpu_threads=int(threads))
segs, info = model.transcribe(audio, language=lang or None, vad_filter=True, word_timestamps=True, beam_size=1,
                              initial_prompt=prompt or None, condition_on_previous_text=False)
res = []
for s in segs:
    res.append(dict(start=round(s.start, 2), end=round(s.end, 2), text=s.text.strip(),
                    words=[dict(w=w.word.strip(), s=round(w.start, 2), e=round(w.end, 2), p=round(w.probability, 2))
                           for w in (s.words or [])]))
    print("PROGRESS", min(0.99, s.end / dur), flush=True)
json.dump(dict(language=info.language, duration=round(dur, 2), segments=res), open(out, "w", encoding="utf-8"), ensure_ascii=False)
print("DONE", flush=True)
'''


# ---------------------------------------------------------------- l'environnement d'IA
def ai_dir():
    d = settings.get("ai_dir")
    return d or os.path.join(os.path.expanduser("~"), "AutoCutAI")


def python_exe():
    return os.path.join(ai_dir(), "venv", "Scripts", "python.exe")


def available():
    """L'environnement d'IA est-il installe ? (voir setup_ai.py)"""
    return os.path.isfile(python_exe())


def model_name():
    return settings.get("whisper_model") or "small"


def cache_file(path, audio_index, model=None):
    st = os.stat(path)
    key = hashlib.sha1(f"{os.path.abspath(path)}|{st.st_size}|{st.st_mtime_ns}|{audio_index}|{model or model_name()}".encode()).hexdigest()[:20]
    return os.path.join(ai_dir(), "cache", key + ".json")


def get_cached(path, audio_index=0):
    """La transcription deja faite de ce fichier (meme piste), ou None. On ne retranscrit jamais deux fois."""
    try:
        with open(cache_file(path, audio_index), encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def transcribe(path, audio_index=0, progress=None, should_stop=None, prompt=DEFAULT_PROMPT, language="fr", force=False):
    """Transcrit une piste audio d'une video (sans la modifier). Renvoie un dict :
    {"language", "duration", "segments": [{"start", "end", "text", "words": [{"w", "s", "e", "p"}]}]} (instants du FICHIER).
    progress(0..1) est appele ; should_stop() permet d'annuler (InterruptedError)."""
    if not available():
        raise RuntimeError("ai-missing")
    if not force:
        data = get_cached(path, audio_index)
        if data is not None:
            if progress:
                progress(1.0)
            return data
    os.makedirs(os.path.join(ai_dir(), "cache"), exist_ok=True)
    worker = os.path.join(ai_dir(), "whisper_worker.py")
    if not os.path.isfile(worker) or open(worker, encoding="utf-8").read() != WORKER:
        with open(worker, "w", encoding="utf-8") as f:
            f.write(WORKER)
    work = tempfile.mkdtemp(prefix="autocut_stt_")
    try:
        wav, out = os.path.join(work, "audio.wav"), os.path.join(work, "out.json")
        r = subprocess.run([find_tool("ffmpeg"), "-y", "-v", "error", "-i", path, "-map", f"0:a:{audio_index}", "-vn",
                            "-ac", "1", "-ar", "16000", wav], capture_output=True, text=True, creationflags=_NOWIN)
        if r.returncode != 0:
            raise RuntimeError(r.stderr.strip()[-400:] or "ffmpeg")
        if progress:
            progress(0.03)
        env = dict(os.environ, HF_HUB_DISABLE_TELEMETRY="1", PYTHONIOENCODING="utf-8")
        cmd = [python_exe(), "-I", worker, wav, out, os.path.join(ai_dir(), "models"), model_name(), language or "", prompt or "",
               str(max(2, (os.cpu_count() or 4)))]
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding="utf-8", errors="replace",
                                env=env, creationflags=_NOWIN)
        for line in proc.stdout:
            if should_stop and should_stop():
                proc.kill()
                raise InterruptedError("stopped")
            if line.startswith("PROGRESS ") and progress:
                try:
                    progress(0.03 + 0.97 * float(line.split()[1]))
                except ValueError:
                    pass
        err = proc.stderr.read()
        if proc.wait() != 0 or not os.path.isfile(out):
            raise RuntimeError(err.strip()[-600:] or "transcription failed")
        with open(out, encoding="utf-8") as f:
            data = json.load(f)
        with open(cache_file(path, audio_index), "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False)
        if progress:
            progress(1.0)
        return data
    finally:
        import shutil
        shutil.rmtree(work, ignore_errors=True)


# ---------------------------------------------------------------- recherche dans le texte
def norm(s):
    """Minuscules, sans accents ni ponctuation : pour comparer des mots."""
    s = unicodedata.normalize("NFKD", s.lower())
    return "".join(ch for ch in s if ch.isalnum())


def words(data):
    """Tous les mots, a plat : [{"w", "s", "e", "n"}] (n = mot normalise)."""
    out = []
    for seg in data.get("segments", []):
        for w in seg.get("words") or []:
            n = norm(w["w"])
            if n:
                out.append(dict(w=w["w"], s=w["s"], e=w["e"], n=n))
    return out


def search(data, query, context=5):
    """Cherche un mot ou une phrase : liste de {"start", "end", "text", "context"} (instants du fichier). Insensible aux
    majuscules et aux accents ; une phrase = des mots qui se suivent."""
    toks = [norm(t) for t in query.split() if norm(t)]
    if not toks:
        return []
    ws = words(data)
    res, i, n = [], 0, len(toks)
    while i <= len(ws) - n:
        if all(ws[i + k]["n"] == toks[k] or (k == n - 1 and len(toks[k]) >= 3 and ws[i + k]["n"].startswith(toks[k])) for k in range(n)):
            a, b = i, i + n
            ctx = " ".join(w["w"] for w in ws[max(0, a - context): b + context])
            res.append(dict(start=ws[a]["s"], end=ws[b - 1]["e"], text=" ".join(w["w"] for w in ws[a:b]), context=ctx))
            i = b
        else:
            i += 1
    return res


_FILLER = [re.compile(p) for p in (r"^h*e+u+h*$", r"^h+e+u+h*$", r"^h+m+$", r"^h+u+m+$", r"^m{2,}h*$", r"^e+u+m+$")]


def fillers(data, max_len=1.6):
    """Les hesitations (euh, hum...) : liste de {"start", "end", "text"} (instants du fichier). Une 'hesitation' trop longue
    (plus de max_len s) est ignoree : c'est probablement un autre bruit."""
    out = []
    for w in words(data):
        if any(p.match(w["n"]) for p in _FILLER) and w["e"] - w["s"] <= max_len:
            out.append(dict(start=w["s"], end=w["e"], text=w["w"]))
    return out


def pad(ranges, before=0.04, after=0.06):
    """Elargit un peu chaque intervalle (les mots Whisper sont serres) et fusionne ceux qui se touchent."""
    rs = sorted((max(0.0, a - before), b + after) for a, b in ranges)
    out = []
    for a, b in rs:
        if out and a <= out[-1][1]:
            out[-1] = (out[-1][0], max(out[-1][1], b))
        else:
            out.append((a, b))
    return out
