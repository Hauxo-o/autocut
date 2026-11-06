"""Options de l'utilisateur, gardees d'un lancement a l'autre."""
import os

from PySide6.QtCore import QSettings, QStandardPaths

from . import i18n

DEFAULTS = {
    "theme": "nuit",        # voir theme.THEMES
    "fullscreen": True,     # demarrer en plein ecran
    "scale": 100,           # taille du texte en %
    "skip": True,           # sauter les coupes pendant la lecture
    "tint": False,          # teinter la video en rouge dans les coupes
    "separate": True,       # exporter video et audios separes
    "lang": i18n.default_lang(),   # langue de l'interface
    "panel_open": True,     # (ancien reglage, remplace par les deux suivants)
    "panel_cut": True,      # onglet Couper : panneau de reglages (colonne de droite) visible
    "panel_edit": True,     # onglet Montage : panneau de droite visible
    "projects_dir": "",     # dossier des projets ("" = Documents/Auto Cut)
    "tool_magnet": True,    # editeur : aimant active
    "tool_linked": True,    # editeur : bouton 'toutes les pistes' active
    "ai_dir": "",           # dossier de l'IA locale (transcription) ; "" = C:\Users\<toi>\AutoCutAI
    "whisper_model": "small",   # modele de transcription (tiny / base / small / medium)
    "idle_db": -45,
    "idle_len": 1.5,
    "mix_voice": -16.0,        # mixage automatique : volume cible de chaque role (LUFS)
    "mix_game": -24.0,
    "mix_music": -30.0,
    "tool_ripple": False,   # editeur : collage automatique des morceaux (pas de vide)
    "tool_active": "select",  # editeur : outil actif ("select" ou "cut")
}


def _store():
    return QSettings("AutoCut", "AutoCut")


def get(key):
    default = DEFAULTS[key]
    v = _store().value(key, default)
    try:
        if isinstance(default, bool):
            return str(v).lower() in ("true", "1", "yes")
        if isinstance(default, int):
            return int(v)
    except (TypeError, ValueError):
        return default
    return str(v)


def projects_root():
    """Dossier qui contient un sous-dossier par projet (par video)."""
    v = get("projects_dir")
    if v:
        return v
    docs = QStandardPaths.writableLocation(QStandardPaths.DocumentsLocation) or os.path.expanduser("~")
    return os.path.join(docs, "Auto Cut")


def put(key, value):
    _store().setValue(key, value)
