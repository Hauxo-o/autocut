"""Preregalges de detection : ceux fournis + ceux que l'utilisateur enregistre.
Garde aussi les derniers reglages utilises de chaque mode."""
import json
from copy import copy

from PySide6.QtCore import QSettings

from .detect import DEFAULTS, Settings

# Preregalges fournis : (cle de traduction, reglages)
BUILTIN = {
    "dead": [
        ("default", copy(DEFAULTS["dead"])),
        ("careful", Settings(threshold_db=-50.0, min_silence=3.0, margin_before=0.30, margin_after=0.30)),
        ("bold", Settings(threshold_db=-40.0, min_silence=1.2, margin_before=0.15, margin_after=0.15)),
    ],
    "classic": [
        ("default", copy(DEFAULTS["classic"])),
        ("soft", Settings(threshold_db=-38.0, min_silence=0.6, margin_before=0.12, margin_after=0.12)),
        ("tight", Settings(threshold_db=-32.0, min_silence=0.2, margin_before=0.04, margin_after=0.04)),
    ],
}


def _store():
    return QSettings("AutoCut", "AutoCut")


def _load(key):
    try:
        data = json.loads(str(_store().value(key, "{}")))
        return data if isinstance(data, dict) else {}
    except (ValueError, TypeError):
        return {}


def _to_settings(v):
    try:
        return Settings(float(v[0]), float(v[1]), float(v[2]), float(v[3]))
    except (TypeError, ValueError, IndexError):
        return None


def _to_list(s):
    return [s.threshold_db, s.min_silence, s.margin_before, s.margin_after]


# ---------------------------------------------------------------- tes preregalges
def user_presets(mode):
    out = {}
    for name, v in _load("presets").get(mode, {}).items():
        s = _to_settings(v)
        if s:
            out[name] = s
    return out


def save_user(mode, name, s):
    data = _load("presets")
    data.setdefault(mode, {})[name] = _to_list(s)
    _store().setValue("presets", json.dumps(data, ensure_ascii=False))


def delete_user(mode, name):
    data = _load("presets")
    data.get(mode, {}).pop(name, None)
    _store().setValue("presets", json.dumps(data, ensure_ascii=False))


# ---------------------------------------------------------------- derniers reglages
def load_last(mode):
    v = _load("last_settings").get(mode)
    return _to_settings(v) if v else None


def save_last(mode, s):
    data = _load("last_settings")
    data[mode] = _to_list(s)
    _store().setValue("last_settings", json.dumps(data))


def same(a, b, eps=1e-6):
    return (abs(a.threshold_db - b.threshold_db) < eps and abs(a.min_silence - b.min_silence) < eps
            and abs(a.margin_before - b.margin_before) < eps and abs(a.margin_after - b.margin_after) < eps)
