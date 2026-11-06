"""Installe la transcription locale (faster-whisper) pour Auto Cut, une seule fois, dans un dossier a part :
  python setup_ai.py            -> C:\\Users\\<toi>\\AutoCutAI  (venv + modele "small", environ 750 Mo)
  python setup_ai.py --dir D:\\IA --model medium

Gratuit, sans compte ni cle : tout tourne sur ton ordinateur. Pour tout enlever : supprime le dossier.
(Le seul acces a Internet : telecharger faster-whisper et le modele Whisper, une fois.)"""
import argparse
import os
import subprocess
import sys

ap = argparse.ArgumentParser()
ap.add_argument("--dir", default=os.path.join(os.path.expanduser("~"), "AutoCutAI"))
ap.add_argument("--model", default="small", choices=["tiny", "base", "small", "medium"])
a = ap.parse_args()

venv = os.path.join(a.dir, "venv")
py = os.path.join(venv, "Scripts", "python.exe")
os.makedirs(a.dir, exist_ok=True)
if not os.path.isfile(py):
    print("1/3  creation de l'environnement isole :", venv, flush=True)
    subprocess.check_call([sys.executable, "-m", "venv", venv])
print("2/3  installation de faster-whisper (quelques minutes)...", flush=True)
subprocess.check_call([py, "-m", "pip", "install", "--disable-pip-version-check", "-q", "--only-binary=:all:", "faster-whisper"])
print(f"3/3  telechargement du modele '{a.model}' (une seule fois)...", flush=True)
code = ("import os; os.environ['HF_HUB_DISABLE_TELEMETRY']='1'; from faster_whisper import WhisperModel; "
        f"WhisperModel({a.model!r}, device='cpu', compute_type='int8', download_root={os.path.join(a.dir, 'models')!r}); print('ok')")
subprocess.check_call([py, "-I", "-c", code])
print("\nTermine. Dans Auto Cut, le bouton 'Transcrire' est maintenant disponible.")
if os.path.normpath(a.dir) != os.path.normpath(os.path.join(os.path.expanduser("~"), "AutoCutAI")):
    print("Dossier different du dossier par defaut : indique-le dans les reglages (cle 'ai_dir').")
