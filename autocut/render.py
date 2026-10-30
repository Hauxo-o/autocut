"""Rendu en MP4 avec FFmpeg. Les fichiers d'origine ne sont jamais modifies (on les lit seulement)."""
import os
import shutil
import subprocess
import copy
import json
import re
import tempfile

from .ffmpeg_tools import _NOWIN, find_tool
from .project import FONTS, track_num

WIN_FONTS = os.path.join(os.environ.get("WINDIR", r"C:\Windows"), "Fonts")


def _num(v):
    return f"{v:.4f}".rstrip("0").rstrip(".") or "0"


def _color(hexstr):
    return "0x" + hexstr.lstrip("#")[:6]


def _runs(clips):
    """Regroupe les elements qui se suivent bout a bout et viennent du meme fichier, dans l'ordre.
    Un montage issu de l'onglet Couper en contient des centaines : on les traite d'un seul bloc
    (un seul fichier ouvert), sinon FFmpeg devrait ouvrir le meme fichier des centaines de fois."""
    runs, cur = [], []
    for c in sorted(clips, key=lambda c: c.start):
        if cur:
            p = cur[-1]
            same = (abs(p.end - c.start) < 1e-4 and p.path == c.path and p.audio_index == c.audio_index
                    and c.src_in >= p.src_in and p.volume_db == c.volume_db and p.scale == c.scale
                    and p.pos_x == c.pos_x and p.pos_y == c.pos_y)
            if same:
                cur.append(c)
                continue
            runs.append(cur)
        cur = [c]
    if cur:
        runs.append(cur)
    return runs


TP_MARGIN = 0.3        # l'encodage AAC peut depasser un peu le pic : on garde une petite marge


def _loudnorm_filter(project, m):
    """Filtre loudnorm (norme EBU R128 / BS.1770). m == "measure" : 1re passe (mesure) ; sinon : les valeurs
    mesurees, pour une 2e passe qui applique un gain fixe (lineaire) sans abimer la dynamique."""
    base = f"loudnorm=I={_num(project.norm_lufs)}:TP={_num(project.norm_tp - TP_MARGIN)}:LRA=50"
    if m == "measure":
        return base + ":print_format=json"
    return (base + f":measured_I={m['input_i']}:measured_LRA={m['input_lra']}:measured_TP={m['input_tp']}"
            f":measured_thresh={m['input_thresh']}:offset={m['target_offset']}:linear=true,"
            "aresample=48000,aformat=channel_layouts=stereo")


def _parse_loudnorm(err):
    """Lit le bloc JSON affiche par loudnorm. Renvoie None si le son est vide."""
    mt = re.findall(r"\{[^{}]*\"input_i\"[^{}]*\}", err, re.S)
    if not mt:
        raise RuntimeError(err.strip()[-600:] or "mesure impossible")
    d = json.loads(mt[-1])
    try:
        lufs, tp, lra = float(d["input_i"]), float(d["input_tp"]), float(d["input_lra"])
    except ValueError:
        return None
    if lufs < -69.0:                                     # silence
        return None
    d.update(lufs=lufs, tp=tp, lra=lra, I=lufs, TP=tp, LRA=lra)
    return d


def build_graph(project, workdir, audio_only=False, loudnorm=None, per_track=False):
    """Construit le graphe de filtres FFmpeg. Renvoie (arguments -i, script, duree totale).
    audio_only : seulement le son (pour mesurer le volume). loudnorm : "measure" ou les valeurs mesurees."""
    project = project.expanded()                     # les clips composes deviennent leurs passages
    W, H, fps = project.width, project.height, project.fps
    total = project.duration()
    inputs, lines = [], []
    n_in = 0
    lines.append(f"color=c=black:s={W}x{H}:r={_num(fps)}:d={_num(total)},format=yuv420p[b0]")
    k = 0
    uid = 0

    for track in project.visual_tracks():            # V1 en bas, les suivantes au-dessus
        if not project.track_active(track):          # piste coupee ou pas en solo : on ne l'affiche pas
            continue
        tclips = project.clips_on(track)
        items = []                                   # (kind, [clips])
        vids = [c for c in tclips if c.kind == "video"]
        others = [c for c in tclips if c.kind != "video"]
        items += [("video", run) for run in _runs(vids)]
        items += [(c.kind, [c]) for c in others]
        for kind, run in sorted(items, key=lambda it: it[1][0].start):
            c0 = run[0]
            s, e = c0.start, run[-1].end
            px, py = c0.pos_x / 100.0, c0.pos_y / 100.0
            uid += 1
            if kind == "text":
                tfile = f"t{uid}.txt"
                with open(os.path.join(workdir, tfile), "w", encoding="utf-8") as f:
                    f.write(c0.text)
                src = os.path.join(WIN_FONTS, FONTS.get(c0.font, "arial.ttf"))
                if not os.path.isfile(src):
                    src = os.path.join(WIN_FONTS, "arial.ttf")
                local = f"font_{uid}.ttf"
                shutil.copyfile(src, os.path.join(workdir, local))
                size = max(8, round(c0.size * H / 1080))
                ow = round(c0.outline * H / 1080)
                border = f":borderw={ow}:bordercolor={_color(c0.outline_color)}" if ow > 0 else ""
                lines.append(
                    f"[b{k}]drawtext=fontfile={local}:textfile={tfile}:reload=0:fontsize={size}"
                    f":fontcolor={_color(c0.color)}{border}"
                    f":x=w*{_num(px)}-text_w/2:y=h*{_num(py)}-text_h/2:enable='between(t,{_num(s)},{_num(e)})'[b{k + 1}]")
                k += 1
                continue
            sc = max(1.0, c0.scale) / 100.0
            if kind == "video":
                inputs += ["-i", c0.path]
                if len(run) == 1:
                    pre = (f"[{n_in}:v:0]trim=start={_num(c0.src_in)}:duration={_num(c0.duration)},"
                           f"setpts=PTS-STARTPTS,fps={_num(fps)}")
                else:   # plusieurs passages d'un meme fichier : on le lit une fois et on le decoupe
                    outs = "".join(f"[s{uid}_{i}]" for i in range(len(run)))
                    lines.append(f"[{n_in}:v:0]split={len(run)}{outs}")
                    for i, c in enumerate(run):
                        lines.append(f"[s{uid}_{i}]trim=start={_num(c.src_in)}:duration={_num(c.duration)},"
                                     f"setpts=PTS-STARTPTS,fps={_num(fps)}[r{uid}_{i}]")
                    ins = "".join(f"[r{uid}_{i}]" for i in range(len(run)))
                    lines.append(f"{ins}concat=n={len(run)}:v=1:a=0[run{uid}]")
                    pre = f"[run{uid}]null"
            else:   # image : repetee pendant toute sa duree
                inputs += ["-loop", "1", "-framerate", _num(fps), "-t", _num(c0.duration), "-i", c0.path]
                pre = f"[{n_in}:v:0]format=rgba"
            n_in += 1
            lines.append(
                f"{pre},scale=w='trunc({W}*{_num(sc)}/2)*2':h='trunc({H}*{_num(sc)}/2)*2':"
                f"force_original_aspect_ratio=decrease,setsar=1,setpts=PTS-STARTPTS+{_num(s)}/TB[v{uid}]")
            lines.append(
                f"[b{k}][v{uid}]overlay=x=W*{_num(px)}-w/2:y=H*{_num(py)}-h/2:eof_action=pass:repeatlast=0:"
                f"enable='between(t,{_num(s)},{_num(e)})'[b{k + 1}]")
            k += 1
    lines.append(f"[b{k}]format=yuv420p[vout]")

    # ----- audio : chaque bloc est coupe, regle en volume, retarde, puis tout est additionne
    audio_start = len(lines)
    labels = []
    track_labels = {}                                # per_track : etiquettes de sortie, une par piste son
    if per_track:
        names = project.audio_tracks()
    else:
        names = sorted({c.track for c in project.clips if c.has_sound()})
    for track in names:
        gain = None
        for run in _runs([c for c in project.clips_on(track) if c.has_sound()]):
            gain = project.gain_db(run[0])
            if gain is None:                         # piste muette
                continue
            uid += 1
            c0 = run[0]
            inputs += ["-i", c0.path]
            ai = max(0, c0.audio_index)
            delay = max(0, round(c0.start * 1000))
            if len(run) == 1:
                lines.append(f"[{n_in}:a:{ai}]atrim=start={_num(c0.src_in)}:duration={_num(c0.duration)},"
                             f"asetpts=PTS-STARTPTS[x{uid}]")
            else:
                outs = "".join(f"[as{uid}_{i}]" for i in range(len(run)))
                lines.append(f"[{n_in}:a:{ai}]asplit={len(run)}{outs}")
                for i, c in enumerate(run):
                    lines.append(f"[as{uid}_{i}]atrim=start={_num(c.src_in)}:duration={_num(c.duration)},"
                                 f"asetpts=PTS-STARTPTS[ar{uid}_{i}]")
                ins = "".join(f"[ar{uid}_{i}]" for i in range(len(run)))
                lines.append(f"{ins}concat=n={len(run)}:v=0:a=1[x{uid}]")
            lines.append(f"[x{uid}]aresample=48000,aformat=channel_layouts=stereo,volume={_num(gain)}dB,"
                         f"adelay={delay}|{delay}[a{uid}]")
            labels.append(f"[a{uid}]")
            track_labels.setdefault(track, []).append(f"[a{uid}]")
            n_in += 1
    if per_track:                                    # un flux son par piste (pour l'analyse des silences)
        for k, track in enumerate(project.audio_tracks()):
            ls = track_labels.get(track, [])
            if ls:
                lines.append("".join(ls) + f"amix=inputs={len(ls)}:duration=longest:normalize=0,"
                             f"apad=whole_dur={_num(total)}[aout{k}]")
            else:
                lines.append(f"anullsrc=r=48000:cl=stereo,atrim=duration={_num(total)}[aout{k}]")
    elif labels:
        mix = ("".join(labels) + f"amix=inputs={len(labels)}:duration=longest:normalize=0,"
               f"apad=whole_dur={_num(total)}")
        lines.append(mix + (f",{_loudnorm_filter(project, loudnorm)}" if loudnorm is not None else "") + "[aout]")
    else:
        lines.append(f"anullsrc=r=48000:cl=stereo,atrim=duration={_num(total)}[aout]")
    if audio_only:
        lines = lines[audio_start:]                      # les entrees video restent ouvertes mais ne servent a rien
    return inputs, ";\n".join(lines), total


def build_command(project, out_path, workdir, loudnorm=None, per_track=False):
    """Renvoie (commande ffmpeg, duree totale). per_track : version legere (apercu) avec un flux son par piste."""
    inputs, script, total = build_graph(project, workdir, loudnorm=loudnorm, per_track=per_track)
    with open(os.path.join(workdir, "graph.txt"), "w", encoding="utf-8") as f:
        f.write(script)
    head = [find_tool("ffmpeg"), "-y", "-hide_banner", "-v", "error", "-nostats", "-progress", "pipe:1"] + inputs
    if per_track:
        maps = ["-map", "[vout]"]
        for k in range(len(project.audio_tracks())):
            maps += ["-map", f"[aout{k}]"]
        cmd = (head + ["-/filter_complex", "graph.txt"] + maps +
               ["-c:v", "libx264", "-preset", "ultrafast", "-crf", "30", "-pix_fmt", "yuv420p", "-r", _num(project.fps),
                "-c:a", "aac", "-b:a", "96k", "-t", _num(total), "-movflags", "+faststart", out_path])
        return cmd, total
    cmd = (head + ["-/filter_complex", "graph.txt", "-map", "[vout]", "-map", "[aout]",
                   "-c:v", "libx264", "-preset", "veryfast", "-crf", "18", "-pix_fmt", "yuv420p", "-r", _num(project.fps),
                   "-c:a", "aac", "-b:a", "192k", "-t", _num(total), "-movflags", "+faststart", out_path])
    return cmd, total


def _run_ffmpeg(cmd, work, total, progress, should_stop):
    """Lance FFmpeg, suit sa progression (0..1), renvoie ce qu'il a ecrit sur stderr."""
    errfile = os.path.join(work, "stderr.txt")
    with open(errfile, "w", encoding="utf-8", errors="replace") as ef:
        proc = subprocess.Popen(cmd, cwd=work, stdout=subprocess.PIPE, stderr=ef, text=True,
                                encoding="utf-8", errors="replace", creationflags=_NOWIN)
        for line in proc.stdout:
            if should_stop and should_stop():
                proc.kill()
                raise InterruptedError("stopped")
            if line.startswith("out_time_us=") and progress and total > 0:
                try:
                    progress(min(1.0, int(line.split("=")[1]) / 1e6 / total))
                except ValueError:
                    pass
        code = proc.wait()
    with open(errfile, encoding="utf-8", errors="replace") as ef:
        err = ef.read()
    if code != 0:
        raise RuntimeError(err.strip()[-800:] or "ffmpeg a echoue")
    return err


def has_sound(project):
    return any(c.has_sound() and project.gain_db(c) is not None for c in project.clips)


def measure_loudness(project, lufs=None, tp=None, progress=None, should_stop=None):
    """Mesure le son final du montage (apres volumes, sourdines, solo et master) : dict avec lufs, tp, lra
    (et les valeurs brutes de loudnorm), ou None s'il n'y a aucun son. Ne modifie rien."""
    work = tempfile.mkdtemp(prefix="autocut_measure_")
    try:
        if lufs is not None or tp is not None:               # cibles differentes de celles du projet (mesure seulement)
            project = copy.copy(project)
            project.norm_lufs = project.norm_lufs if lufs is None else lufs
            project.norm_tp = project.norm_tp if tp is None else tp
        inputs, script, total = build_graph(project, work, audio_only=True, loudnorm="measure")
        if "loudnorm" not in script:
            return None
        with open(os.path.join(work, "graph.txt"), "w", encoding="utf-8") as f:
            f.write(script)
        cmd = ([find_tool("ffmpeg"), "-y", "-hide_banner", "-v", "info", "-nostats", "-progress", "pipe:1"] + inputs +
               ["-/filter_complex", "graph.txt", "-map", "[aout]", "-t", _num(total), "-f", "null", "-"])
        return _parse_loudnorm(_run_ffmpeg(cmd, work, total, progress, should_stop))
    finally:
        shutil.rmtree(work, ignore_errors=True)


def measure_file(path):
    """Mesure le volume (LUFS, pic) d'un fichier video/audio deja cree."""
    cmd = [find_tool("ffmpeg"), "-hide_banner", "-v", "info", "-nostats", "-i", path, "-map", "0:a:0", "-vn",
           "-af", "loudnorm=I=-14:TP=-1:LRA=50:print_format=json", "-f", "null", "-"]
    r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", creationflags=_NOWIN)
    return _parse_loudnorm(r.stderr)


def make_pass_project(project, height=360):
    """Copie legere du montage pour la 'passe' Couper les silences : image reduite, et chaque piste son telle quelle
    (sans sourdine, solo ni volume : on analyse le son brut de chaque piste)."""
    pp = copy.deepcopy(project)
    pp.width, pp.height = max(2, int(project.width * height / project.height) // 2 * 2), height
    pp.normalize, pp.master_db, pp.track_gain = False, 0.0, {}
    pp.track_mute = {k: v for k, v in project.track_mute.items() if k.startswith("V")}
    pp.track_solo = {k: v for k, v in project.track_solo.items() if k.startswith("V")}
    for c in pp.clips:
        if c.kind == "audio":
            c.mute_audio, c.volume_db = False, 0.0
        elif c.kind == "video":
            c.mute_audio = True
    return pp


def render_pass(project, out_path, progress=None, should_stop=None):
    """Cree le fichier leger de la passe : meme duree et memes images/s que le montage, une piste son par piste du montage."""
    pp = make_pass_project(project)
    if not pp.audio_tracks():
        raise ValueError("no audio")
    out_path = os.path.abspath(out_path)
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    work = tempfile.mkdtemp(prefix="autocut_pass_")
    try:
        cmd, total = build_command(pp, out_path, work, per_track=True)
        _run_ffmpeg(cmd, work, total, progress, should_stop)
        if progress:
            progress(1.0)
    finally:
        shutil.rmtree(work, ignore_errors=True)
    return out_path


def render(project, out_path, progress=None, should_stop=None):
    """Rend le montage dans out_path. progress(0..1) est appele pendant le rendu.
    Si project.normalize : le son final est d'abord mesure, puis amene au volume cible (norme YouTube par defaut)."""
    if not project.clips:
        raise ValueError("empty")
    out_path = os.path.abspath(out_path)
    for c in project.clips:
        if c.path and os.path.abspath(c.path) == out_path:
            raise ValueError("Securite : on n'ecrit jamais sur un fichier utilise dans le montage.")
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    measured, base = None, 0.0
    if getattr(project, "normalize", False) and has_sound(project):
        base = 0.2
        measured = measure_loudness(project, progress=(lambda p: progress(0.2 * p)) if progress else None,
                                    should_stop=should_stop)
        if measured is None:
            base = 0.0
    work = tempfile.mkdtemp(prefix="autocut_render_")
    try:
        cmd, total = build_command(project, out_path, work, loudnorm=measured)
        _run_ffmpeg(cmd, work, total, (lambda p: progress(base + (1 - base) * p)) if progress else None, should_stop)
        if progress:
            progress(1.0)
    finally:
        shutil.rmtree(work, ignore_errors=True)
    return out_path


# ====================================================================== MP4 avec les coupes appliquees
def render_cuts(media, segments, out_path, progress=None, should_stop=None):
    """Cree un MP4 ne contenant que les passages gardes, avec TOUTES les pistes audio d'origine.

    media    : infos de la video (ffmpeg_tools.probe)
    segments : liste de (image_debut, image_fin) a garder, dans l'ordre
    La video d'origine n'est pas modifiee. Le resultat est un nouveau fichier definitif (re-encode).

    Methode : chaque passage est encode a part (precis a l'image pres), puis tout est colle bout a bout.
    Les intermediaires gardent le son en PCM, pour que l'AAC final soit cree une seule fois (pas de decalage)."""
    src = os.path.abspath(media["path"])
    out_path = os.path.abspath(out_path)
    if out_path == src:
        raise ValueError("Securite : on n'ecrit jamais sur la video d'origine.")
    if not segments:
        raise ValueError("empty")
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    ff = find_tool("ffmpeg")
    fr = media["fps"]
    fps = float(fr)
    rate = f"{fr.numerator}/{fr.denominator}"
    n_audio = len(media["audio"])
    total_frames = sum(b - a for a, b in segments)
    work = tempfile.mkdtemp(prefix="autocut_cuts_")
    try:
        done = 0
        names = []
        for i, (a, b) in enumerate(segments):
            if should_stop and should_stop():
                raise InterruptedError("stopped")
            name = f"seg{i:05d}.mkv"
            cmd = [ff, "-y", "-hide_banner", "-v", "error", "-ss", f"{a / fps:.6f}", "-i", src,
                   "-t", f"{(b - a) / fps:.6f}", "-map", "0:v:0"]
            for k in range(n_audio):
                cmd += ["-map", f"0:a:{k}"]
            cmd += ["-c:v", "libx264", "-preset", "veryfast", "-crf", "18", "-pix_fmt", "yuv420p", "-r", rate,
                    "-c:a", "pcm_s16le", name]
            r = subprocess.run(cmd, cwd=work, capture_output=True, text=True, encoding="utf-8", errors="replace",
                               creationflags=_NOWIN)
            if r.returncode != 0:
                raise RuntimeError(r.stderr.strip()[-800:] or "ffmpeg a echoue")
            names.append(name)
            done += b - a
            if progress:
                progress(min(0.97, 0.97 * done / total_frames))
        with open(os.path.join(work, "list.txt"), "w", encoding="utf-8") as f:
            f.write("".join(f"file '{n}'\n" for n in names))
        cmd = [ff, "-y", "-hide_banner", "-v", "error", "-f", "concat", "-safe", "0", "-i", "list.txt", "-map", "0",
               "-c:v", "copy", "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart", out_path]
        r = subprocess.run(cmd, cwd=work, capture_output=True, text=True, encoding="utf-8", errors="replace",
                           creationflags=_NOWIN)
        if r.returncode != 0:
            raise RuntimeError(r.stderr.strip()[-800:] or "ffmpeg a echoue")
        if progress:
            progress(1.0)
    finally:
        shutil.rmtree(work, ignore_errors=True)
    return out_path
