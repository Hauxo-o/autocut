"""Export au format XML 'Final Cut Pro 7' (xmeml), importable dans DaVinci Resolve :
Fichier > Importer > Timeline."""
import os
from urllib.parse import quote
from xml.sax.saxutils import escape

from .detect import normalize_cuts

LABEL_SILENCE = "Rose"     # couleur de clip utilisee pour les silences (si Resolve la lit)


def _timebase(fps):
    """(timebase, ntsc) : 60 i/s -> (60, FALSE) ; 59.94 -> (60, TRUE)."""
    f = float(fps)
    tb = int(round(f))
    return tb, ("FALSE" if abs(f - tb) < 1e-6 else "TRUE")


def _rate(fps):
    tb, ntsc = _timebase(fps)
    return f"<rate><timebase>{tb}</timebase><ntsc>{ntsc}</ntsc></rate>"


def _pathurl(path):
    return "file://localhost/" + quote(path.replace("\\", "/"), safe="/:")


def _segments(cuts, total, mark_mode):
    """Decoupe [0, total) en segments (debut, fin, est_silence)."""
    segs, pos = [], 0
    for a, b in cuts:
        if a > pos:
            segs.append((pos, a, False))
        if mark_mode:
            segs.append((a, b, True))
        pos = b
    if pos < total:
        segs.append((pos, total, False))
    return segs


def build_xml(media, cuts, mode="remove", audio_files=None, name=None):
    """
    media        : dict renvoye par ffmpeg_tools.probe()
    cuts         : liste de Cut (les coupes desactivees sont ignorees)
    mode         : 'remove' (silences supprimes) ou 'mark' (coupes seulement, silences reperes)
    audio_files  : liste de dicts {name, path, channels} = un fichier audio par piste.
                   -> video et audios SEPARES (non lies), une piste de timeline par fichier.
                   Si None : on utilise les pistes audio integrees au MP4 (liees a la video).
    """
    fps, total = media["fps"], media["total_frames"]
    mark = mode == "mark"
    name = name or media["name"].rsplit(".", 1)[0] + (" - coupes" if mark else " - sans silences")
    segs = _segments(normalize_cuts(cuts, total), total, mark)
    linked = audio_files is None

    # Sources audio : une par piste de timeline
    if linked:
        srcs = [dict(fid="file-1", name=media["name"], path=media["path"], channels=len(media["audio"]),
                     chan=k + 1) for k in range(len(media["audio"]))]
    else:
        srcs = [dict(fid=f"file-a{k + 1}", name=f["name"], path=f["path"], channels=f["channels"], chan=1)
                for k, f in enumerate(audio_files)]

    # Position de chaque segment sur la timeline
    placed, cursor = [], 0
    for (a, b, sil) in segs:
        tl = a if mark else cursor
        placed.append((a, b, sil, tl, tl + (b - a)))
        cursor += b - a
    seq_len = total if mark else cursor

    defined = set()
    sample_audio = ('<samplecharacteristics><depth>16</depth><samplerate>48000</samplerate>'
                    '</samplecharacteristics>')
    sample_video = (f'<samplecharacteristics>{_rate(fps)}<width>{media["width"]}</width>'
                    f'<height>{media["height"]}</height><anamorphic>FALSE</anamorphic>'
                    f'<pixelaspectratio>square</pixelaspectratio><fielddominance>none</fielddominance>'
                    f'</samplecharacteristics>')

    def file_xml(fid, nm, path, has_video, ch):
        if fid in defined:
            return f'<file id="{fid}"/>'
        defined.add(fid)
        v = f'<video>{sample_video}</video>' if has_video else ''
        a = f'<audio>{sample_audio}<channelcount>{ch}</channelcount></audio>' if ch else ''
        return (f'<file id="{fid}"><name>{escape(nm)}</name><pathurl>{escape(_pathurl(path))}</pathurl>'
                f'{_rate(fps)}<duration>{total}</duration>'
                f'<timecode>{_rate(fps)}<string>00:00:00:00</string><frame>0</frame>'
                f'<displayformat>NDF</displayformat></timecode><media>{v}{a}</media></file>')

    def links(i):
        if not linked:
            return ""
        out = [f'<link><linkclipref>v{i}</linkclipref><mediatype>video</mediatype>'
               f'<trackindex>1</trackindex><clipindex>{i}</clipindex></link>']
        for t in range(len(srcs)):
            out.append(f'<link><linkclipref>a{t + 1}_{i}</linkclipref><mediatype>audio</mediatype>'
                       f'<trackindex>{t + 1}</trackindex><clipindex>{i}</clipindex></link>')
        return "".join(out)

    def clip(cid, seg, i, fdef, src_xml=""):
        a, b, sil, s, e = seg
        label = media["name"] + (" (silence)" if sil else "")
        lab = f"<labels><label2>{LABEL_SILENCE}</label2></labels>" if sil else ""
        return (f'<clipitem id="{cid}"><name>{escape(label)}</name><enabled>TRUE</enabled>'
                f'<duration>{total}</duration>{_rate(fps)}<start>{s}</start><end>{e}</end>'
                f'<in>{a}</in><out>{b}</out>{fdef}{src_xml}{links(i)}{lab}</clipitem>')

    # --- video (si les audios sont separes, le fichier video n'est declare que comme image)
    vch = len(media["audio"]) if linked else 0
    vclips = "".join(
        clip(f"v{i}", seg, i, file_xml("file-1", media["name"], media["path"], True, vch))
        for i, seg in enumerate(placed, 1))
    video = (f'<video><format>{sample_video}</format><track>{vclips}'
             f'<enabled>TRUE</enabled><locked>FALSE</locked></track></video>')

    # --- audio : une piste de timeline par source
    atracks = ""
    for t, src in enumerate(srcs):
        sx = (f'<sourcetrack><mediatype>audio</mediatype><trackindex>{src["chan"]}</trackindex></sourcetrack>')
        items = "".join(
            clip(f"a{t + 1}_{i}", seg, i,
                 file_xml(src["fid"], src["name"], src["path"], False, src["channels"]), sx)
            for i, seg in enumerate(placed, 1))
        atracks += (f'<track>{items}<enabled>TRUE</enabled><locked>FALSE</locked>'
                    f'<outputchannelindex>1</outputchannelindex></track>')
    audio = (f'<audio><numOutputChannels>2</numOutputChannels><format>{sample_audio}</format>'
             f'{atracks}</audio>')

    # --- marqueurs (mode 'mark') : un par silence, visible sur la regle de la timeline
    markers = ""
    if mark:
        for (a, b, sil, s, e) in placed:
            if sil:
                markers += (f'<marker><name>SILENCE</name><comment>silence de {(b - a) / float(fps):.2f} s'
                            f'</comment><in>{s}</in><out>{e}</out></marker>')

    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n<!DOCTYPE xmeml>\n<xmeml version="4">'
        f'<sequence id="sequence-1"><name>{escape(name)}</name><duration>{seq_len}</duration>{_rate(fps)}'
        f'<timecode>{_rate(fps)}<string>00:00:00:00</string><frame>0</frame><displayformat>NDF</displayformat>'
        f'</timecode>{markers}<media>{video}{audio}</media></sequence></xmeml>\n'
    )


def write_xml(path, *args, **kwargs):
    with open(path, "w", encoding="utf-8") as f:
        f.write(build_xml(*args, **kwargs))


def build_project_xml(project, name="Montage"):
    """XML (Final Cut Pro 7 / xmeml) du MONTAGE de l'editeur, importable dans DaVinci Resolve (Fichier > Importer >
    Timeline) : une piste par piste image et par piste son, les passages des clips composes coupes bout a bout,
    sourdines et volumes des pistes en niveau audio. Les textes ne peuvent pas passer par un XML (DaVinci les ignore).
    Renvoie (texte XML, nombre d'elements non transferes)."""
    fps = project.fps
    p = project.expanded()
    skipped = sum(1 for c in project.clips if c.kind == "text")

    def fr(t):
        return int(round(t * fps))

    files = {}                                   # chemin -> description du fichier source (une seule fois)
    for c in p.clips:
        if c.kind == "text" or not c.path:
            continue
        f = files.setdefault(c.path, dict(id=f"file-{len(files) + 1}", name=os.path.basename(c.path), video=False,
                                          audio=False, dur=0, still=False))
        if c.kind in ("video", "image"):
            f["video"] = True
            f["still"] = f["still"] or c.kind == "image"
        if c.kind == "audio" or (c.kind == "video" and c.has_audio):
            f["audio"] = True
        f["dur"] = max(f["dur"], fr(c.src_duration) if c.src_duration > 0 else fr(c.src_in + c.duration))
    defined = set()
    sample_audio = ('<samplecharacteristics><depth>16</depth><samplerate>48000</samplerate></samplecharacteristics>')
    sample_video = (f'<samplecharacteristics>{_rate(fps)}<width>{project.width}</width><height>{project.height}</height>'
                    f'<anamorphic>FALSE</anamorphic><pixelaspectratio>square</pixelaspectratio>'
                    f'<fielddominance>none</fielddominance></samplecharacteristics>')

    def file_xml(path):
        f = files[path]
        if f["id"] in defined:
            return f'<file id="{f["id"]}"/>'
        defined.add(f["id"])
        dur = 108000 if f["still"] else max(f["dur"], 1)
        v = f'<video>{sample_video}</video>' if f["video"] else ""
        a = f'<audio>{sample_audio}<channelcount>2</channelcount></audio>' if f["audio"] else ""
        return (f'<file id="{f["id"]}"><name>{escape(f["name"])}</name><pathurl>{escape(_pathurl(path))}</pathurl>'
                f'{_rate(fps)}<duration>{dur}</duration><timecode>{_rate(fps)}<string>00:00:00:00</string>'
                f'<frame>0</frame><displayformat>NDF</displayformat></timecode><media>{v}{a}</media></file>')

    def item(cid, c, extra=""):
        start, end = fr(c.start), fr(c.end)
        n = max(1, end - start)
        a = fr(c.src_in) if c.kind != "image" else 0
        dur = 108000 if c.kind == "image" else max(fr(c.src_duration), a + n)
        return (f'<clipitem id="{cid}"><name>{escape(c.label())}</name><enabled>{"TRUE" if c.has_sound() or c.kind != "audio" else "FALSE"}'
                f'</enabled><duration>{dur}</duration>{_rate(fps)}<start>{start}</start><end>{start + n}</end>'
                f'<in>{a}</in><out>{a + n}</out>{file_xml(c.path)}{extra}</clipitem>')

    vtracks = ""
    for t in p.visual_tracks():
        clips = sorted((c for c in p.clips if c.track == t and c.kind in ("video", "image")), key=lambda c: c.start)
        if not clips:                                    # piste qui ne contient que des textes : rien a ecrire
            continue
        items = "".join(item(f"{t}_{i}", c) for i, c in enumerate(clips, 1))
        on = "FALSE" if project.track_mute.get(t) else "TRUE"
        vtracks += f'<track>{items}<enabled>{on}</enabled><locked>FALSE</locked></track>'
    atracks = ""
    for t in p.audio_tracks():
        clips = sorted((c for c in p.clips if c.track == t and c.kind == "audio"), key=lambda c: c.start)
        items = ""
        for i, c in enumerate(clips, 1):
            ex = f'<sourcetrack><mediatype>audio</mediatype><trackindex>{c.audio_index + 1}</trackindex></sourcetrack>'
            db = c.volume_db + project.track_gain.get(t, 0.0) + project.master_db
            if abs(db) > 0.01:                    # volume : filtre "Audio Levels" (niveau lineaire)
                ex += ('<filter><effect><name>Audio Levels</name><effectid>audiolevels</effectid>'
                       '<effectcategory>audiolevels</effectcategory><effecttype>audiolevels</effecttype>'
                       '<mediatype>audio</mediatype><parameter><parameterid>level</parameterid><name>Level</name>'
                       f'<valuemin>0</valuemin><valuemax>3.98109</valuemax><value>{10 ** (db / 20.0):.5f}</value>'
                       '</parameter></effect></filter>')
            items += item(f"{t}_{i}", c, ex)
        on = "FALSE" if project.track_mute.get(t) else "TRUE"
        atracks += (f'<track>{items}<enabled>{on}</enabled><locked>FALSE</locked>'
                    f'<outputchannelindex>1</outputchannelindex></track>')
    video = f'<video><format>{sample_video}</format>{vtracks}</video>'
    audio = f'<audio><numOutputChannels>2</numOutputChannels><format>{sample_audio}</format>{atracks}</audio>'
    xml = (
        '<?xml version="1.0" encoding="UTF-8"?>\n<!DOCTYPE xmeml>\n<xmeml version="4">'
        f'<sequence id="sequence-1"><name>{escape(name)}</name><duration>{fr(project.duration())}</duration>{_rate(fps)}'
        f'<timecode>{_rate(fps)}<string>00:00:00:00</string><frame>0</frame><displayformat>NDF</displayformat>'
        f'</timecode><media>{video}{audio}</media></sequence></xmeml>\n'
    )
    return xml, skipped
