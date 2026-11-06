"""Montage en ligne de commande, sans fenetre : pour qu'une IA (Claude ici, ou un modele local) agisse sur un projet.
Chaque commande qui MODIFIE un projet garde d'abord une copie '<projet>.bak' (la derniere version avant modification).

  python cli.py summary  <projet.autocut.json> [--out resume.md]      resume : pistes, elements, ce qui est dit et quand
  python cli.py sheet    <projet.autocut.json> [--count 30] [--out planche.png]   vignettes avec l'heure
  python cli.py transcribe <video ou projet> [--track 0]               transcription locale (mise en cache)
  python cli.py search   <projet> "texte"                              ou est-ce dit ? (temps du montage)
  python cli.py cut      <projet> --range 12.5 18 [--range 40 41.2]    retire ces intervalles (secondes du montage)
  python cli.py cut-words <projet> "texte"                             retire chaque passage ou ce texte est dit
  python cli.py remove-fillers <projet>                                retire les euh / hum
  python cli.py close-gaps <projet> [--keep-start]                     colle les morceaux (pas de vide)
  python cli.py volume   <projet> --track A1 --db -3   (ou --master --db -2)
  python cli.py add-text <projet> --text "Titre" --start 0 --duration 4 [--x 50 --y 82 --size 72 --color #ffffff]
  python cli.py export-ai <projet> [--folder dossier]                  ecrit resume.md + planche.png
Les temps sont en SECONDES du montage (pas du fichier d'origine)."""
import argparse
import os
import shutil
import sys

COMMANDS = {"summary", "sheet", "transcribe", "search", "cut", "cut-words", "remove-fillers", "close-gaps", "volume",
            "add-text", "export-ai"}


def _load(path):
    from autocut.project import Project
    p = Project()
    p.load(path)
    return p


def _save(p, path):
    shutil.copyfile(path, path + ".bak")                 # on garde la version d'avant
    p.save(path)


def _print_matches(ms):
    from autocut.aitools import fmt_t
    for m in ms:
        print(f"[{fmt_t(m['start'])} -> {fmt_t(m['end'])}] ... {m['context']} ...")
    print(f"{len(ms)} resultat(s)")


def main(argv=None):
    ap = argparse.ArgumentParser(prog="cli.py", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("summary", "sheet", "search", "cut", "cut-words", "remove-fillers", "close-gaps", "volume", "add-text",
                 "export-ai", "transcribe"):
        s = sub.add_parser(name)
        s.add_argument("target", help="projet .autocut.json (ou, pour transcribe, une video)")
        if name == "search" or name == "cut-words":
            s.add_argument("query")
        if name in ("summary", "sheet"):
            s.add_argument("--out")
        if name in ("sheet", "export-ai"):
            s.add_argument("--count", type=int, default=30)
        if name == "export-ai":
            s.add_argument("--folder")
        if name == "transcribe":
            s.add_argument("--track", type=int, default=0)
        if name == "cut":
            s.add_argument("--range", nargs=2, type=float, action="append", required=True, metavar=("DEBUT", "FIN"))
        if name == "close-gaps":
            s.add_argument("--keep-start", action="store_true", help="ne pas boucher le vide avant le premier element")
        if name == "volume":
            s.add_argument("--track")
            s.add_argument("--master", action="store_true")
            s.add_argument("--db", type=float, required=True)
        if name == "add-text":
            s.add_argument("--text", required=True)
            s.add_argument("--start", type=float, default=0.0)
            s.add_argument("--duration", type=float, default=4.0)
            s.add_argument("--x", type=float, default=50.0)
            s.add_argument("--y", type=float, default=82.0)
            s.add_argument("--size", type=int, default=72)
            s.add_argument("--color", default="#ffffff")
    a = ap.parse_args(argv)

    from autocut import aitools, ops, transcribe
    if a.cmd == "transcribe":
        paths = [a.target]
        if a.target.endswith(".autocut.json"):
            paths = aitools.source_files(_load(a.target))
        for path in paths:
            print("Transcription de", path, flush=True)
            last = [-1]

            def prog(x):
                if int(x * 20) != last[0]:
                    last[0] = int(x * 20)
                    print(f"  {x * 100:.0f} %", flush=True)
            data = transcribe.transcribe(path, a.track, progress=prog)
            print(f"  {len(data['segments'])} phrases, {sum(len(s['words']) for s in data['segments'])} mots")
        return 0

    p = _load(a.target)
    if a.cmd == "summary":
        text = aitools.project_summary(p)
        if a.out:
            open(a.out, "w", encoding="utf-8").write(text)
            print("ecrit :", a.out)
        else:
            sys.stdout.reconfigure(encoding="utf-8")
            print(text)
    elif a.cmd == "sheet":
        out = a.out or os.path.join(os.path.dirname(os.path.abspath(a.target)), "planche.png")
        n = aitools.contact_sheet(aitools.project_frames(p, a.count), out)
        print(f"planche de {n} vignettes : {out}")
    elif a.cmd == "export-ai":
        folder = a.folder or os.path.join(os.path.dirname(os.path.abspath(a.target)), "ia")
        s, sh = aitools.export_for_ai(p, folder, a.count)
        print("resume :", s)
        print("planche :", sh)
    elif a.cmd == "search":
        _print_matches(aitools.find_in_project(p, a.query))
    elif a.cmd == "cut":
        removed = ops.cut_ranges(p, [tuple(r) for r in a.range])
        _save(p, a.target)
        print(f"{removed:.2f} s retirees ; nouvelle duree {p.duration():.2f} s")
    elif a.cmd == "cut-words":
        ms = aitools.find_in_project(p, a.query)
        removed = aitools.cut_matches(p, ms) if ms else 0.0
        if ms:
            _save(p, a.target)
        print(f"{len(ms)} passage(s) trouves, {removed:.2f} s retirees ; nouvelle duree {p.duration():.2f} s")
    elif a.cmd == "remove-fillers":
        ms = aitools.find_in_project(p, fillers=True)
        removed = aitools.cut_matches(p, ms) if ms else 0.0
        if ms:
            _save(p, a.target)
        print(f"{len(ms)} hesitation(s) (euh, hum...) retiree(s), {removed:.2f} s ; nouvelle duree {p.duration():.2f} s")
    elif a.cmd == "close-gaps":
        n, sec = ops.close_gaps(p, leading=not a.keep_start)
        if n:
            _save(p, a.target)
        print(f"{n} vide(s) bouche(s), {sec:.2f} s ; nouvelle duree {p.duration():.2f} s")
    elif a.cmd == "volume":
        if a.master:
            p.master_db = a.db
        elif a.track in p.tracks():
            p.track_gain[a.track] = a.db
        else:
            print("piste inconnue. Pistes :", ", ".join(p.tracks()))
            return 2
        _save(p, a.target)
        print("volume regle")
    elif a.cmd == "add-text":
        from autocut.project import Clip
        c = Clip("text", "V1", a.start, a.duration, text=a.text, font="Arial", size=a.size, color=a.color,
                 pos_x=a.x, pos_y=a.y)
        c.track = p.free_track("V", c.start, c.end, prefer="high")
        p.clips.append(c)
        p.compact()
        _save(p, a.target)
        print(f"texte ajoute sur {c.track}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
