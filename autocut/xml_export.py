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


# ====================================================================== FCPXML (Final Cut Pro / DaVinci Resolve)
def _fcp_rate(fps):
    """(numerateur, denominateur) de la cadence : 59.94 -> (60000, 1001), 30 -> (30, 1)."""
    f = float(fps)
    for n in (24, 25, 30, 48, 50, 60, 120):
        if abs(f - n) < 0.01:
            return n, 1
        if abs(f - n * 1000 / 1001) < 0.01:
            return n * 1000, 1001
    return int(round(f)), 1


def build_project_fcpxml(project, name="Montage"):
    """FCPXML 1.9 du MONTAGE : toutes les pistes (images en haut, sons en bas) sont des elements connectes a un plan de
    base de la duree du montage ; les clips composes sont coupes en passages ; volumes de clip, de piste et master en
    niveau audio ; pistes coupees desactivees. Les textes ne sont pas transferes.
    Renvoie (texte FCPXML, nombre d'elements non transferes)."""
    num, den = _fcp_rate(project.fps)
    p = project.expanded()
    skipped = sum(1 for c in project.clips if c.kind == "text")

    def tm(t):                                    # secondes -> temps FCPXML (nombre entier d'images)
        return f"{int(round(max(0.0, t) * num / den)) * den}/{num}s"

    files = {}                                    # chemin -> infos
    for c in p.clips:
        if c.kind == "text" or not c.path:
            continue
        key = c.path + "|a" if c.kind == "audio" else c.path          # le son d'un fichier video = un "asset" sans image
        f = files.setdefault(key, dict(path=c.path, name=os.path.basename(c.path), dur=0.0, n_audio=1, video=False, still=False))
        f["dur"] = max(f["dur"], c.src_duration if c.src_duration > 0 else c.src_in + c.duration)
        if c.kind in ("video", "image"):
            f["video"] = True
            f["still"] = f["still"] or c.kind == "image"
        if c.kind == "audio":
            f["n_audio"] = max(f["n_audio"], c.audio_index + 1)
    ids, res = {}, []
    res.append(f'<format id="r1" name="Montage" frameDuration="{den}/{num}s" width="{project.width}" height="{project.height}" '
               f'colorSpace="1-1-1 (Rec. 709)"/>')
    for i, (key, f) in enumerate(files.items(), 2):
        path = f["path"]
        ids[key] = f"r{i}"
        dur = 3600.0 if f["still"] else max(f["dur"], 0.1)
        attrs = (f'id="r{i}" name="{escape(f["name"])}" start="0s" duration="{tm(dur)}" format="r1" '
                 f'hasAudio="{0 if f["still"] else 1}" hasVideo="{1 if f["video"] else 0}" '
                 f'audioSources="{f["n_audio"]}" audioChannels="{2 * f["n_audio"]}" audioRate="48000"')
        res.append(f'<asset {attrs} src="{escape(_fileurl(path))}"/>')

    def vol_xml(c):
        db = c.volume_db + project.track_gain.get(c.track, 0.0) + project.master_db
        return f'<adjust-volume amount="{db:.2f}dB"/>' if abs(db) > 0.01 else ""

    total = project.duration()
    lanes = ""
    vt = p.visual_tracks()                       # V1 = lane 1, V2 = lane 2 ... (au-dessus)
    for t in vt:
        lane = track_num_of(t)
        off = "" if not project.track_mute.get(t) else ' enabled="0"'
        for c in sorted((c for c in p.clips if c.track == t and c.kind in ("video", "image")), key=lambda c: c.start):
            mute_audio = '<adjust-volume amount="-96dB"/>' if c.kind == "video" else ""
            lanes += (f'<asset-clip ref="{ids[c.path]}" lane="{lane}" offset="{tm(c.start)}" name="{escape(c.label())}" '
                      f'start="{tm(c.src_in if c.kind == "video" else 0)}" duration="{tm(c.duration)}" format="r1"{off}>{mute_audio}</asset-clip>')
    for t in p.audio_tracks():
        lane = -track_num_of(t)                   # A1 = lane -1, A2 = lane -2 ...
        off = ' enabled="0"' if not project.track_active(t) else ""
        for c in sorted((c for c in p.clips if c.track == t and c.kind == "audio"), key=lambda c: c.start):
            ch = f'<audio-channel-source srcCh="{2 * c.audio_index + 1}, {2 * c.audio_index + 2}" outCh="L, R" role="dialogue" active="1"/>'
            en = ' enabled="0"' if (c.mute_audio or off) else ""
            lanes += (f'<asset-clip ref="{ids[c.path + '|a']}" lane="{lane}" offset="{tm(c.start)}" name="{escape(c.label())}" '
                      f'start="{tm(c.src_in)}" duration="{tm(c.duration)}" audioRole="dialogue"{en}>{ch}{vol_xml(c)}</asset-clip>')
    xml = ('<?xml version="1.0" encoding="UTF-8"?>\n<!DOCTYPE fcpxml>\n<fcpxml version="1.9">'
           f'<resources>{"".join(res)}</resources>'
           f'<library><event name="{escape(name)}"><project name="{escape(name)}">'
           f'<sequence format="r1" duration="{tm(total)}" tcStart="0s" tcFormat="NDF" audioLayout="stereo" audioRate="48k">'
           f'<spine><gap name="Montage" offset="0s" start="0s" duration="{tm(total)}">{lanes}</gap></spine>'
           '</sequence></project></event></library></fcpxml>\n')
    return xml, skipped


def track_num_of(t):
    return int(t[1:])


def _fileurl(path):
    return "file:///" + quote(os.path.abspath(path).replace("\\", "/"), safe="/:")
