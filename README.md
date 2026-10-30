# Auto Cut

Mini logiciel de montage pour Windows, fait pour des videos de jeu (OBS) :

- **Couper les silences** : detecte les temps morts, tu regles / verifies les coupes, export XML pour DaVinci Resolve ou MP4.
- **Montage** : timeline avec plusieurs pistes (images, textes, sons), outils (ciseaux, aimant...), solo / muet par piste,
  miniatures et formes d'onde, export MP4, export XML (DaVinci), passes « Couper les silences » sur le montage.
- **Son** : table de mixage (une tranche par piste, vu-metres) et normalisation du son final (norme YouTube : -14 LUFS, -1 dBTP).

Tes videos d'origine ne sont **jamais modifiees**. Tout est local et gratuit.

## Lancer depuis le code

```
pip install -r requirements.txt
python main.py
```

FFmpeg est necessaire : `winget install Gyan.FFmpeg`.

## Fabriquer AutoCut.exe

```
pip install pyinstaller
python build_exe.py            # sortie : dist\AutoCut\AutoCut.exe
```

## Organisation

- `autocut/gui.py` : fenetre principale et onglet « Couper les silences »
- `autocut/editor.py`, `timeline.py`, `project.py`, `render.py` : l'onglet Montage
- `autocut/audiotab.py` : l'onglet Son
- `autocut/detect.py`, `xml_export.py`, `export.py` : detection des silences et exports
- `autocut/i18n.py` : textes en francais, anglais, japonais, espagnol, allemand
