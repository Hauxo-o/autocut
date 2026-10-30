"""Point d'entree : lance l'application Auto Cut."""
import os
import sys

from PySide6.QtWidgets import QApplication

from autocut import i18n, settings, theme
from autocut.gui import MainWindow


def main():
    app = QApplication(sys.argv)
    i18n.set_lang(settings.get("lang"))
    theme.apply_theme(app, settings.get("theme"), settings.get("scale"))
    win = MainWindow()
    report = os.environ.get("AUTOCUT_SELFTEST")        # mode de test interne (verification du .exe)
    if settings.get("fullscreen") and not report:
        win.showFullScreen()
    else:
        win.show()
    if len(sys.argv) > 1 and os.path.isfile(sys.argv[1]):
        win.load(sys.argv[1])
    if not report:                                     # sauvegarde automatique du montage + accueil au lancement
        from PySide6.QtCore import QTimer as _QT

        def startup():
            if win.editor.offer_restore():             # montage non enregistre de la derniere session : repris
                win.set_tab(1)
            elif not win.media:                        # sinon : ecran d'accueil (projets recents, ouvrir, nouveau)
                win.show_welcome()
        _QT.singleShot(700, startup)
        win.editor.enable_autosave()
    if report:
        from PySide6.QtCore import QTimer

        def check():
            if win.cuts:
                n = len(win.player.audioTracks())
                open(report, "w").write(f"OK {len(win.cuts)} coupes, {n} pistes vues par le lecteur")
                app.quit()
        t = QTimer()
        t.timeout.connect(check)
        t.start(300)
        QTimer.singleShot(90000, app.quit)
    sys.exit(app.exec())


if __name__ == "__main__":
    try:
        main()
    except Exception:  # noqa: BLE001  (l'app n'a pas de console : on note l'erreur dans un fichier)
        import traceback
        log = os.path.join(os.path.expanduser("~"), "autocut_erreur.log")
        with open(log, "w", encoding="utf-8") as f:
            f.write(traceback.format_exc())
        raise
