"""Etape 2 : detection + export XML en ligne de commande (avant l'interface graphique).

Exemple :
  python cli.py "video test.mp4" --mode dead --track 0 --out resultats
"""
import argparse
import os
import sys

from autocut.detect import DEFAULTS, MODE_NAMES, Settings, detect_cuts
from autocut.ffmpeg_tools import audio_envelope, probe
from autocut.export import export_xml


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("video")
    ap.add_argument("--mode", choices=["dead", "classic"], default="dead")
    ap.add_argument("--track", type=int, default=0, help="piste audio a analyser (0, 1, 2...)")
    ap.add_argument("--thr", type=float)
    ap.add_argument("--min", type=float, dest="minsil")
    ap.add_argument("--before", type=float)
    ap.add_argument("--after", type=float)
    ap.add_argument("--out", default="resultats")
    ap.add_argument("--embedded", action="store_true", help="pistes audio integrees au MP4 (liees)")
    a = ap.parse_args()

    m = probe(a.video)
    print(f"{m['name']} : {m['width']}x{m['height']} a {float(m['fps']):g} i/s, {m['duration']:.1f} s, "
          f"{len(m['audio'])} pistes audio")
    d = DEFAULTS[a.mode]
    s = Settings(a.thr if a.thr is not None else d.threshold_db,
                 a.minsil if a.minsil is not None else d.min_silence,
                 a.before if a.before is not None else d.margin_before,
                 a.after if a.after is not None else d.margin_after)
    print(f"Mode {MODE_NAMES[a.mode]} sur la piste audio {a.track} : {s}")
    env = audio_envelope(m["path"], a.track)
    cuts = detect_cuts(env, s, m["fps"], m["total_frames"])
    removed = sum(c.frames for c in cuts) / float(m["fps"])
    print(f"{len(cuts)} coupes, {removed:.1f} s retirees ({100 * removed / m['duration']:.0f} %)")
    os.makedirs(a.out, exist_ok=True)
    base = os.path.join(a.out, f"{a.mode}_piste{a.track}")
    for mode, tag in (("remove", "SUPPRIMER"), ("mark", "COUPES-SEULEMENT")):
        export_xml(m, cuts, mode, f"{base}_{tag}.xml", separate=not a.embedded, progress=print)
        print("XML ecrit :", f"{base}_{tag}.xml")


if __name__ == "__main__":
    sys.exit(main())
