"""Construit AutoCut.exe (dossier dist/AutoCut). Lance : python build_exe.py [dossier_de_sortie]"""
import subprocess
import sys

from autocut.ffmpeg_tools import find_tool

cmd = [sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean", "--windowed", "--name", "AutoCut",
       "--add-binary", f"{find_tool('ffmpeg')};bin", "--add-binary", f"{find_tool('ffprobe')};bin",
       "--exclude-module", "tkinter"] + (["--distpath", sys.argv[1]] if len(sys.argv) > 1 else []) + ["main.py"]
sys.exit(subprocess.call(cmd))
