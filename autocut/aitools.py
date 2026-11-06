"""Outils pour qu'une IA (Claude ici, ou un modele local) puisse comprendre un montage sans l'ouvrir :
- un RESUME texte du projet (pistes, elements, ce qui est dit et quand) ;
- des PLANCHES D'IMAGES (une vignette toutes les N secondes, avec l'heure) pour "voir" la video."""
import os
import subprocess

from . import ops, transcribe
from .ffmpeg_tools import _NOWIN, find_tool


def fmt_t(t):
    t = max(0.0, t)
    return f"{int(t // 60)}:{t % 60:04.1f}"


# ---------------------------------------------------------------- resume du projet
def source_transcript(project, path):
    """Transcription deja faite d'un fichier du montage (on essaie les pistes audio utilisees), ou None."""
    idxs = [c.audio_index for c in project.clips if c.kind == "audio" and os.path.abspath(c.path) == os.path.abspath(path)]
    for i in sorted(set(idxs)) or [0]:
        try:
            data = transcribe.get_cached(path, i)
        except OSError:
            data = None
        if data:
            return data
    return None


def project_summary(project):
    """Texte (Markdown) qui decrit le montage : format, pistes, elements, et la transcription en TEMPS DU MONTAGE."""
    p = project
    L = [f"# Projet : {p.name or '(sans nom)'}",
         f"Format {p.width}x{p.height}, {p.fps} images/s - duree du montage {fmt_t(p.duration())} ({p.duration():.1f} s)",
         f"Source : {p.source or '-'}",
         f"Son final : normalisation {'oui' if p.normalize else 'non'} ({p.norm_lufs:g} LUFS, pic {p.norm_tp:g} dBTP), master {p.master_db:g} dB",
         "", "## Pistes"]
    for t in p.tracks():
        flags = [k for k, d in (("muet", p.track_mute), ("solo", p.track_solo)) if d.get(t)]
        gain = p.track_gain.get(t, 0.0)
        L.append(f"- {t}" + (f" ({', '.join(flags)})" if flags else "") + (f", volume {gain:+g} dB" if gain else ""))
    L += ["", "## Elements (temps du montage)"]
    for c in sorted(p.clips, key=lambda c: (c.track, c.start)):
        what = {"video": "video", "image": "image", "text": "texte", "audio": "son"}[c.kind]
        name = c.text.strip().splitlines()[0] if c.kind == "text" and c.text.strip() else os.path.basename(c.path)
        extra = f", {len(c.segments)} passages gardes" if len(c.segments) > 1 else ""
        extra += f", piste audio {c.audio_index + 1}" if c.kind == "audio" else ""
        extra += ", coupe (muet)" if c.kind == "audio" and c.mute_audio else ""
        L.append(f"- {c.track} [{fmt_t(c.start)} -> {fmt_t(c.end)}] {what} \"{name}\"{extra}")
    L += ["", "## Ce qui est dit (temps du montage)"]
    shown = False
    for path in sorted({os.path.abspath(c.path) for c in p.clips if c.kind == "video" and c.path}):
        data = source_transcript(p, path)
        if not data:
            continue
        shown = True
        L.append(f"(fichier {os.path.basename(path)})")
        for seg in data["segments"]:
            rs = ops.source_to_timeline(p, path, [(seg["start"], seg["end"])])
            if rs:
                L.append(f"[{fmt_t(rs[0][0])} -> {fmt_t(rs[-1][1])}] {seg['text']}")
    if not shown:
        L.append("(pas de transcription : lance 'Transcrire' dans Auto Cut)")
    return "\n".join(L) + "\n"


# ---------------------------------------------------------------- planches d'images
def _frame_png(path, t, width):
    r = subprocess.run([find_tool("ffmpeg"), "-v", "error", "-ss", f"{max(0.0, t):.3f}", "-i", path, "-frames:v", "1",
                        "-vf", f"scale={width}:-2", "-f", "image2pipe", "-vcodec", "png", "-"],
                       capture_output=True, creationflags=_NOWIN)
    return r.stdout if r.returncode == 0 and r.stdout else None


def contact_sheet(frames, out_png, cols=6, tile_w=320):
    """frames : liste de (fichier video, instant dans le fichier, etiquette). Cree une image avec une vignette par element
    et son etiquette (l'heure). Renvoie le nombre de vignettes."""
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    os.environ.setdefault("QT_QPA_FONTDIR", os.path.join(os.environ.get("WINDIR", r"C:\Windows"), "Fonts"))
    from PySide6.QtCore import QRectF, Qt
    from PySide6.QtGui import QColor, QFont, QGuiApplication, QImage, QPainter
    app = QGuiApplication.instance() or QGuiApplication([])          # noqa: F841 (garde la reference)
    tiles = []
    for path, t, label in frames:
        data = _frame_png(path, t, tile_w)
        img = QImage.fromData(data) if data else QImage()
        tiles.append((img, label))
    ok = [x for x in tiles if not x[0].isNull()]
    if not ok:
        return 0
    th = max(i.height() for i, _ in ok)
    rows = (len(tiles) + cols - 1) // cols
    sheet = QImage(cols * tile_w, rows * th, QImage.Format_RGB32)
    sheet.fill(QColor("#101216"))
    p = QPainter(sheet)
    f = QFont("Segoe UI")
    f.setPixelSize(max(12, tile_w // 16))
    f.setBold(True)
    p.setFont(f)
    for k, (img, label) in enumerate(tiles):
        x, y = (k % cols) * tile_w, (k // cols) * th
        if not img.isNull():
            p.drawImage(x, y, img)
        pill = QRectF(x + 4, y + 4, len(label) * (tile_w // 22) + 12, tile_w // 11)
        p.fillRect(pill, QColor(0, 0, 0, 190))
        p.setPen(QColor("#ffffff"))
        p.drawText(pill, Qt.AlignCenter, label)
    p.end()
    sheet.save(out_png)
    return len(ok)


def project_frames(project, count=30):
    """Instants regulierement repartis sur le montage -> [(fichier, instant dans le fichier, 'heure du montage')]."""
    d = project.duration()
    out = []
    if d <= 0:
        return out
    vids = [c for c in project.clips if c.kind == "video"]
    for k in range(count):
        t = min(d - 0.05, (k + 0.5) * d / count)
        for c in vids:
            src = ops.timeline_to_source(project, c.path, t)
            if src is not None:
                out.append((c.path, src, fmt_t(t)))
                break
    return out


def export_for_ai(project, folder, count=30):
    """Ecrit dans `folder` le resume (resume.md) et la planche d'images (planche.png) du montage. Renvoie les chemins."""
    os.makedirs(folder, exist_ok=True)
    summary = os.path.join(folder, "resume.md")
    with open(summary, "w", encoding="utf-8") as f:
        f.write(project_summary(project))
    sheet = os.path.join(folder, "planche.png")
    n = contact_sheet(project_frames(project, count), sheet)
    return summary, (sheet if n else None)


# ---------------------------------------------------------------- chercher / couper dans ce qui est dit
def source_files(project):
    return sorted({os.path.abspath(c.path) for c in project.clips if c.kind == "video" and c.path})


def find_in_project(project, query=None, fillers=False):
    """Passages du MONTAGE ou un mot / une phrase est dit (ou, fillers=True, les hesitations 'euh', 'hum'...).
    Liste triee de {"start", "end" (temps du montage), "path", "src_start", "src_end" (temps du fichier), "text", "context"}.
    Un passage qui a deja ete coupe du montage n'apparait pas."""
    res = []
    for path in source_files(project):
        data = source_transcript(project, path)
        if not data:
            continue
        items = transcribe.fillers(data) if fillers else transcribe.search(data, query or "")
        for it in items:
            rs = ops.source_to_timeline(project, path, [(it["start"], it["end"])])
            if rs:
                res.append(dict(path=path, src_start=it["start"], src_end=it["end"], start=rs[0][0], end=rs[-1][1],
                                text=it["text"], context=it.get("context", it["text"])))
    return sorted(res, key=lambda r: r["start"])


def cut_matches(project, matches):
    """Retire du montage (toutes les pistes, sans laisser de vide) les passages trouves. Renvoie les secondes retirees."""
    by_path = {}
    for m in matches:
        by_path.setdefault(m["path"], []).append((m["src_start"], m["src_end"]))
    tl = []
    for path, rngs in by_path.items():
        tl += ops.source_to_timeline(project, path, transcribe.pad(rngs))
    merged = []
    for a, b in sorted(tl):
        if merged and a <= merged[-1][1] + 1e-6:
            merged[-1] = (merged[-1][0], max(merged[-1][1], b))
        else:
            merged.append((a, b))
    return ops.cut_ranges(project, merged) if merged else 0.0
