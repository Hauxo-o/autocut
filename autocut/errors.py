"""Messages d'erreur clairs : on transforme une erreur technique (FFmpeg, Windows, Python) en une phrase simple qui dit
CE QUI NE VA PAS et QUE FAIRE, avec le detail technique seulement tout en bas. Le detail complet est aussi garde dans
%APPDATA%\\AutoCut\\export_error.log."""
import errno
import os
import time

from .i18n import tr


class UserError(RuntimeError):
    """Erreur dont le message est deja clair pour l'utilisateur : on l'affiche tel quel."""


def log_path():
    base = os.environ.get("APPDATA") or os.path.expanduser("~")
    return os.path.join(base, "AutoCut", "export_error.log")


def write_log(text, cmd=None):
    try:
        os.makedirs(os.path.dirname(log_path()), exist_ok=True)
        with open(log_path(), "a", encoding="utf-8", errors="replace") as f:
            f.write(f"\n===== {time.strftime('%Y-%m-%d %H:%M:%S')} =====\n")
            if cmd:
                f.write(" ".join(f'"{a}"' if " " in str(a) else str(a) for a in cmd)[:4000] + "\n")
            f.write(text[-6000:] + "\n")
    except OSError:
        pass


_RULES = (
    ("err.corrupt", ("invalid data found", "moov atom not found", "could not find codec parameters", "error while decoding",
                     "corrupt", "end of file", "truncated", "invalid nal")),
    ("err.nostream", ("matches no streams", "stream specifier", "does not contain any stream", "no audio", "no video",
                      "output file #0 does not contain")),
    ("err.missing", ("no such file", "does not exist", "error opening input", "cannot find the file", "filenotfounderror",
                     "[errno 2]", "winerror 2", "winerror 3", "introuvable")),
    ("err.locked", ("permission denied", "access is denied", "winerror 5", "winerror 32", "being used by another process",
                    "permissionerror", "[errno 13]")),
    ("err.space", ("no space left", "disk full", "[errno 28]", "winerror 112")),
    ("err.memory", ("memoryerror", "cannot allocate memory", "out of memory", "not enough memory")),
    ("err.codec", ("unknown encoder", "encoder not found", "unsupported codec", "decoder not found", "unknown decoder",
                   "no decoder", "codec not currently supported")),
)


def classify(err):
    """Cle de message pour cette erreur (texte ou exception)."""
    if isinstance(err, FileNotFoundError):
        return "err.missing"
    if isinstance(err, PermissionError):
        return "err.locked"
    if isinstance(err, MemoryError):
        return "err.memory"
    if isinstance(err, OSError) and getattr(err, "errno", None) == errno.ENOSPC:
        return "err.space"
    low = str(err).lower()
    for key, words in _RULES:
        if any(w in low for w in words):
            return key
    if "ffmpeg" in low and ("winerror 2" in low or "not found" in low or "introuvable" in low):
        return "err.ffmpeg"
    return "err.generic"


def explain(err, what=None):
    """Message clair pour l'utilisateur. what : ce qu'on essayait de faire (ex. « exporter la video »), facultatif."""
    if isinstance(err, UserError):
        return str(err)
    raw = str(err).strip()
    key = classify(err)
    write_log(f"{type(err).__name__ if isinstance(err, BaseException) else 'erreur'}: {raw}")
    lines = [ln.strip() for ln in raw.splitlines() if ln.strip()]
    detail = " | ".join(lines[-3:])[-350:]
    head = (tr("err.while", what=what) + "\n\n") if what else ""
    out = head + tr(key)
    if detail:
        out += "\n\n" + tr("err.detail") + " " + detail
    return out + "\n" + tr("err.log", log=log_path())
