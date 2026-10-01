# -*- coding: utf-8 -*-
"""
Die Oberfläche im echten Chromium - nur wenn Playwright installiert ist
und nicht VP4_TEST_OHNE_GUI gesetzt ist (GitHubs Windows-Rechner bauen
ohne Bildschirm und ohne Playwright).

Zwei Teile:
  - jede Ansicht mit Demo-Daten, hell/dunkel, breit/schmal:
    keine Konsolenfehler, keine überlappenden Texte, kein Überlaufen
  - ein echter Durchlauf mit zwei VP4-Diensten: einrichten, verbinden,
    schreiben, reagieren - per Klick
Die Bilder landen in tests/screenshots/ - ANSEHEN, nicht nur zählen.
"""

import os
import sys
from pathlib import Path


def pruefen(R, hilfen):
    if os.environ.get("VP4_TEST_OHNE_GUI"):
        print("  (übersprungen: VP4_TEST_OHNE_GUI ist gesetzt)")
        return
    try:
        import playwright  # noqa: F401
    except ImportError:
        print("  (übersprungen: Playwright ist nicht installiert - pip install playwright)")
        return
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import ui_pruefung
    anzahl, fehler = ui_pruefung.pruefen(ausgeben=lambda *_: None)
    R.pruefe(f"Alle {anzahl + len(fehler)} Ansichten ohne Befund", not fehler,
             "; ".join(f"{k}: {p[0]}" for k, p in fehler[:5]))
    probleme = ui_pruefung.zwei_personen(ausgeben=lambda *_: None)
    R.pruefe("Zwei Personen chatten durch die echte Oberfläche", not probleme, "; ".join(probleme[:5]))
