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
- `autocut/transcribe.py`, `aitools.py`, `ops.py`, `aicli.py`, `cli.py`, `setup_ai.py` : transcription locale et outils pour IA (voir ci-dessous)

## Transcription locale et montage assiste par IA (gratuit, sans Internet)

Installation **une seule fois** (environ 750 Mo, dans `C:\Users\<toi>\AutoCutAI`, rien dans l'exe) :

    python setup_ai.py

Ensuite, dans l'onglet Montage, groupe **Transcription** :
- **Transcrire** : ecrit le texte de ta voix avec l'heure de chaque mot (le resultat est garde, jamais refait) ;
- la **recherche** retrouve un mot ou une phrase dans le montage (double-clic = aller a cet endroit) ;
- **Trouver les euh / hum**, puis **Couper tout** / **Couper la selection** : retire ces passages de toutes les pistes, sans vide (Ctrl+Z annule) ;
- **Exporter pour l'IA** : ecrit `ia/resume.md` (montage + ce qui est dit et quand) et `ia/planche.png` (vignettes avec l'heure).

En ligne de commande (pour qu'une IA agisse sur un projet, une copie `.bak` est gardee) :

    python cli.py summary|sheet|transcribe|search|cut|cut-words|remove-fillers|close-gaps|volume|add-text|export-ai <projet> ...

Voir l'en-tete de `autocut/aicli.py` pour le detail des commandes.

### Couper les moments vides (Montage > Projet > « Couper les moments vides… »)
Retire les passages ou il ne se passe rien : silence sur les pistes cochees (ta voix, le jeu ; decoche la musique de fond) ET image figee. Tu verifies le nombre de passages avant de couper (Ctrl+Z annule). En ligne de commande : `python cli.py cut-idle <projet> --db -45 --min 1.5 [--tracks A1] [--dry-run]`.

### Mixer : faders groupés et ducking
- **Ctrl+clic** (ou double-clic) sur un fader = retour à 0 dB. **Maj+clic** sur des faders = sélectionner plusieurs pistes (cadre coloré) : elles bougent ensemble, et Ctrl+clic remet toute la sélection à 0 dB. Clic simple sur une piste hors sélection = on désélectionne.
- **Ducking** (carte à côté des faders) : la piste de voix (réglable) baisse les autres pistes marquées « Baisse » pendant qu'elle parle (baisse en dB, seuil de voix, durée du retour). Appliqué à l'aperçu et à l'export (`autocut/duck.py`).

### Mixage automatique (onglet Son)
Sous chaque fader, choisis le rôle de la piste (Voix, Jeu, Musique ; par défaut Audio 1 = Voix, 2 = Jeu, 3 = Musique). Le bouton « Régler les volumes automatiquement » mesure chaque piste seule et règle son fader pour atteindre le volume cible de son rôle (par défaut Voix −16, Jeu −24, Musique −30 LUFS, modifiables) ; la normalisation YouTube amène ensuite le tout à −14 LUFS. Ctrl+Z annule.

### Onglets, transferts et exports
- **Couper les silences** : un seul export, « Envoyer dans le montage ». Cela **remplace** le contenu du Montage (confirmation demandée ; Ctrl+Z dans le Montage revient en arrière).
- **Montage** : exporter en **MP4** (option « multipiste » : flux 1 = mixage complet, puis une piste audio par piste du montage, avec ducking et volumes), en **XML** (DaVinci/Final Cut 7) ou en **FCPXML** ; ou **transférer** le montage vers « Couper les silences » (remplace ce qui y est ouvert, confirmation demandée) puis « Appliquer au montage » (confirmation aussi).
- En cas d'échec d'export, le détail technique est écrit dans `%APPDATA%\AutoCut\export_error.log`. L'export vérifie avant de commencer (fichiers présents, dossier inscriptible, place sur le disque, pas d'écrasement d'un fichier source) et écrit d'abord dans un fichier `.part` renommé à la fin.
