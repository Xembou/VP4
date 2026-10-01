#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
=====================================================================
 bauen.py - macht aus dem Projekt eine fertige VP4.exe
=====================================================================
    python bauen.py

Danach liegt die fertige Datei unter  dist/VP4.exe  und kann verschickt
oder bei GitHub hochgeladen werden. Deine Freunde brauchen dafür weder
Python noch sonst etwas installiert zu haben - Doppelklick genügt.

Was das Skript macht:
  1. prüft, ob alle nötigen Pakete da sind
  2. lässt den Selbsttest laufen (bei Fehlern wird nicht gebaut)
  3. erzeugt das Icon neu
  4. baut die .exe
  5. prüft, dass die entstandene Datei auch wirklich startet

Warum ein Skript und kein einzelner Befehl: Die Oberfläche (ui/) muss
mit in die .exe, argon2 und discord.py bringen Teile mit, die PyInstaller
von allein nicht findet - und Qt/GTK/Tk, die zufällig installiert sind,
sollen NICHT mit hinein (sonst wird die Datei dreimal so groß). Fehlt
etwas, baut die .exe fehlerfrei und stürzt erst beim Doppelklick ab -
ohne Konsole sieht man nicht einmal warum.
=====================================================================
"""

import os
import shutil
import subprocess
import tempfile
import sys
import time
from pathlib import Path

ORDNER = Path(__file__).resolve().parent
NAME = "VP4"


def melde(text):
    print(f"\n>>> {text}")


def schritt_pakete():
    melde("Schritt 1/5: Pakete prüfen")
    fehlt = []
    for paket, zweck in [("cryptography", "Verschlüsselung"),
                         ("argon2", "Ableitung des Master-Schlüssels"),
                         ("webview", "Fenster (pywebview)"),
                         ("nacl", "XChaCha20"),
                         ("pyrage", "age-Format"),
                         ("discord", "Chat über Discord"),
                         ("PIL", "Icon und Vorschaubilder"),
                         ("PyInstaller", "Bauen der .exe")]:
        try:
            __import__(paket)
            print(f"    [OK] {paket}  ({zweck})")
        except ImportError:
            fehlt.append(paket)
            print(f"    [FEHLT] {paket}  ({zweck})")
    if fehlt:
        namen = {"PIL": "pillow", "PyInstaller": "pyinstaller", "webview": "pywebview",
                 "argon2": "argon2-cffi", "nacl": "PyNaCl", "discord": "discord.py"}
        print("\nBitte zuerst installieren:")
        print("    pip install " + " ".join(namen.get(p, p) for p in fehlt))
        return False
    return True


def schritt_test():
    melde("Schritt 2/5: Selbsttest")
    ergebnis = subprocess.run([sys.executable, str(ORDNER / "test_vp4.py")],
                              cwd=ORDNER, capture_output=True, text=True,
                              encoding="utf-8", errors="replace")
    letzte = [z for z in (ergebnis.stdout or "").splitlines() if z.strip()][-6:]
    for z in letzte:
        print("    " + z)
    if ergebnis.returncode != 0:
        print("\n    Der Selbsttest ist fehlgeschlagen - es wird nicht gebaut.")
        print("    Erst den Fehler beheben, sonst verschickst du ein kaputtes Programm.")
        return False
    return True


def schritt_icon():
    melde("Schritt 3/5: Icon erzeugen")
    ergebnis = subprocess.run([sys.executable, str(ORDNER / "icon_erzeugen.py")],
                              cwd=ORDNER, capture_output=True, text=True,
                              encoding="utf-8", errors="replace")
    if ergebnis.returncode != 0:
        print("    Icon konnte nicht erzeugt werden - es wird ohne gebaut.")
        return None
    print("    [OK] vp4.ico")
    return ORDNER / "vp4.ico"


def schritt_bauen(icon):
    melde("Schritt 4/5: .exe bauen (das dauert ein paar Minuten)")
    for ordner in ("build", "dist"):
        shutil.rmtree(ORDNER / ordner, ignore_errors=True)

    befehl = [
        sys.executable, "-m", "PyInstaller",
        "--onefile",           # alles in eine einzige Datei
        "--noconsole",         # kein schwarzes Konsolenfenster daneben
        "--name", NAME,
        "--noconfirm",
        # Die Oberfläche: HTML, CSS, JavaScript, Icons, Schrift, Emoji-Liste
        "--add-data", f"{ORDNER / 'ui'}{os.pathsep}ui",
        # argon2-cffi bringt eine kompilierte Bibliothek mit, die PyInstaller
        # nicht von allein findet. Ohne die Zeile baut die .exe fehlerfrei
        # und scheitert erst beim Entsperren - also genau dann, wenn man es
        # am wenigsten gebrauchen kann.
        "--collect-all", "argon2",
        "--hidden-import", "_argon2_cffi_bindings",
        # discord.py wird erst beim Verbinden importiert
        "--collect-all", "discord",
        "--collect-all", "pyrage",
        "--collect-submodules", "netz",
        "--collect-submodules", "kern",
    ]
    # Was nicht mit hinein soll, auch wenn es installiert ist
    for weg in ("tkinter", "customtkinter", "PyQt5", "PyQt6", "PySide2", "PySide6",
                "gi", "numpy", "matplotlib", "playwright"):
        befehl += ["--exclude-module", weg]
    ]
    if icon:
        befehl += ["--icon", str(icon)]
    befehl.append(str(ORDNER / "VP4.py"))

    start = time.time()
    ergebnis = subprocess.run(befehl, cwd=ORDNER, capture_output=True,
                              text=True, encoding="utf-8", errors="replace")
    if ergebnis.returncode != 0:
        print("    Bauen fehlgeschlagen:")
        for z in (ergebnis.stderr or "").splitlines()[-15:]:
            print("    " + z)
        return None

    exe = ORDNER / "dist" / f"{NAME}.exe"
    if not exe.exists():
        print("    Die .exe wurde nicht gefunden.")
        return None
    mb = exe.stat().st_size / 1024 / 1024
    print(f"    [OK] {exe.name} — {mb:.1f} MB, gebaut in {time.time() - start:.0f} Sekunden")
    return exe


def schritt_probelauf(exe):
    melde("Schritt 5/5: Probelauf")
    # Die .exe kurz starten und schauen, ob sie am Leben bleibt. Startet sie
    # gar nicht, ist sie sofort wieder weg - genau das passiert z.B., wenn
    # ui/ fehlt oder WebView2 nicht startet.
    # In einem Wegwerf-Ordner, nicht in dist/. VP4 legt seinen Datenordner
    # immer NEBEN die .exe - der Probelauf würde sonst ein leeres
    # vp4_daten mit eigener Chat-ID in dist/ hinterlassen, das man beim
    # Verschicken versehentlich mitgibt. Nebenbei ist das der ehrlichere
    # Test: genau so kommt die Datei bei einem Freund an, ohne alles.
    wegwerf = Path(tempfile.mkdtemp(prefix="vp4_probelauf_"))
    probe_exe = wegwerf / exe.name
    shutil.copy2(exe, probe_exe)

    lauf = subprocess.Popen([str(probe_exe)], cwd=wegwerf)
    time.sleep(9)
    laeuft = lauf.poll() is None
    if laeuft:
        print("    [OK] Die .exe startet und läuft.")
        lauf.terminate()
        try:
            lauf.wait(timeout=5)
        except subprocess.TimeoutExpired:
            lauf.kill()
        shutil.rmtree(wegwerf, ignore_errors=True)
    else:
        shutil.rmtree(wegwerf, ignore_errors=True)
        print(f"    [FEHLER] Die .exe hat sich sofort beendet "
              f"(Rückgabewert {lauf.returncode}).")
        print("    Zum Nachsehen einmal ohne --noconsole bauen, dann wird der")
        print("    Fehler im Konsolenfenster sichtbar.")
    return laeuft


def main():
    print("=" * 64)
    print(" VP4 bauen")
    print("=" * 64)

    if not schritt_pakete():
        return 1
    if not schritt_test():
        return 1
    icon = schritt_icon()
    exe = schritt_bauen(icon)
    if exe is None:
        return 1
    if not schritt_probelauf(exe):
        return 1

    print("\n" + "=" * 64)
    print(f" FERTIG:  {exe}")
    print("=" * 64)
    print("\nDiese eine Datei kannst du verschicken oder bei GitHub als")
    print("Release hochladen. Wer sie bekommt, braucht kein Python.")
    print("\nHinweis für deine Freunde: Windows zeigt beim ersten Start")
    print("wahrscheinlich eine Warnung ('Windows hat den Start geschützt'),")
    print("weil die Datei nicht kostenpflichtig signiert ist. Über")
    print("'Weitere Informationen' -> 'Trotzdem ausführen' geht es weiter.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
