#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
=====================================================================
 exe_probelauf.py - startet die fertige VP4.exe einmal wirklich
=====================================================================
    python werkzeuge/exe_probelauf.py dist/VP4.exe
    python werkzeuge/exe_probelauf.py dist/VP4.exe --protokoll probelauf

Benutzt von bauen.py (Schritt 5) und vom Workflow pruefen.yml.

Wie es prüft:
  VP4.py kennt einen Selbsttest: Mit VP4_SELBSTTEST_START=1 wartet es,
  bis das Fenster da ist, fragt die Seite, ob die Einrichtung bzw. die
  Seitenleiste steht, schreibt {"ok": true} oder {"ok": false,
  "fehler": "..."} in die Datei aus VP4_SELBSTTEST_DATEI und schliesst
  das Fenster wieder. Das ist der ehrliche Test: "läuft nach 9 Sekunden
  noch" gilt auch für ein weisses Fenster mit einem Skriptfehler.

  Kennt die .exe den Selbsttest nicht (alte VP4.py), bleibt nur der
  alte Weg: läuft sie nach 9 Sekunden noch, gilt sie als gestartet.

Die .exe läuft in einem Wegwerf-Ordner: VP4 legt vp4_daten/ immer NEBEN
die .exe, und genau so kommt sie bei einem Freund an - ohne alles.

Rückgabewert 0 = in Ordnung, 1 = Fehler.
=====================================================================
"""

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ORDNER = Path(__file__).resolve().parent.parent

SELBSTTEST_FRIST = 90       # Sekunden bis zum Ergebnis (erster Start entpackt ~40 MB)
BEENDEN_FRIST = 20          # Sekunden, die sie nach dem Ergebnis zum Beenden hat
ALT_WARTEN = 9              # alter Weg: so lange muss sie am Leben bleiben


def selbsttest_bekannt() -> bool:
    """Kennt VP4.py den Selbsttest? (Die .exe wird aus genau dieser Datei gebaut.)"""
    try:
        return "VP4_SELBSTTEST_START" in (ORDNER / "VP4.py").read_text(encoding="utf-8")
    except OSError:
        return False


def _beenden(lauf):
    """Beendet die .exe samt Kindprozess.

    Eine --onefile-.exe besteht aus ZWEI Prozessen: dem Entpacker und dem
    eigentlichen Programm. terminate() träfe nur den ersten - deshalb
    unter Windows taskkill /T (der ganze Baum)."""
    if lauf.poll() is not None:
        return
    if sys.platform == "win32":
        subprocess.run(["taskkill", "/F", "/T", "/PID", str(lauf.pid)],
                       capture_output=True)
    else:
        lauf.terminate()
    try:
        lauf.wait(timeout=10)
    except subprocess.TimeoutExpired:
        lauf.kill()


def _protokoll_zeigen(wegwerf: Path, ziel):
    """Zeigt das Ende von vp4_daten/vp4.log - die .exe hat keine Konsole,
    das Protokoll ist das Einzige, was sie hinterlässt."""
    log = wegwerf / "vp4_daten" / "vp4.log"
    if log.exists():
        zeilen = log.read_text(encoding="utf-8", errors="replace").splitlines()
        print("    --- vp4.log (Ende) ---")
        for z in zeilen[-25:]:
            print("    " + z)
    if ziel:
        ziel = Path(ziel)
        ziel.mkdir(parents=True, exist_ok=True)
        for datei in (log, wegwerf / "selbsttest.json"):
            if datei.exists():
                shutil.copy2(datei, ziel / datei.name)


def probelauf(exe, protokoll=None, frist=SELBSTTEST_FRIST, selbsttest=None) -> bool:
    exe = Path(exe).resolve()
    if not exe.exists():
        print(f"    [FEHLER] {exe} gibt es nicht.")
        return False
    if selbsttest is None:
        selbsttest = selbsttest_bekannt()

    wegwerf = Path(tempfile.mkdtemp(prefix="vp4_probelauf_"))
    probe_exe = wegwerf / exe.name
    shutil.copy2(exe, probe_exe)
    ergebnis_datei = wegwerf / "selbsttest.json"

    umgebung = dict(os.environ)
    # Die .exe soll sich verhalten wie bei einem Freund, nicht wie im Test.
    for name in ("VP4_TESTMODUS", "VP4_TEST_OHNE_GUI", "VP4_DEBUG"):
        umgebung.pop(name, None)
    if selbsttest:
        umgebung["VP4_SELBSTTEST_START"] = "1"
        umgebung["VP4_SELBSTTEST_DATEI"] = str(ergebnis_datei)

    start = time.monotonic()
    lauf = subprocess.Popen([str(probe_exe)], cwd=wegwerf, env=umgebung)
    ok = False
    try:
        if not selbsttest:
            time.sleep(ALT_WARTEN)
            ok = lauf.poll() is None
            if ok:
                print(f"    [OK] Die .exe startet und läuft nach {ALT_WARTEN} Sekunden noch.")
                print("         (Ohne Selbsttest - ob man etwas sieht, ist damit nicht gesagt.)")
            else:
                print(f"    [FEHLER] Die .exe hat sich sofort beendet (Rückgabewert {lauf.returncode}).")
            return ok

        # Warten, bis das Ergebnis da ist oder die .exe von selbst aufhört
        while time.monotonic() - start < frist:
            if ergebnis_datei.exists() or lauf.poll() is not None:
                break
            time.sleep(0.5)
        time.sleep(0.5)     # die Datei könnte gerade noch geschrieben werden

        if not ergebnis_datei.exists():
            if lauf.poll() is None:
                print(f"    [FEHLER] Nach {frist} Sekunden kein Ergebnis - das Fenster")
                print("             kam nicht hoch oder der Selbsttest lief nicht an.")
            else:
                print(f"    [FEHLER] Die .exe hat sich ohne Ergebnis beendet "
                      f"(Rückgabewert {lauf.returncode}, nach {time.monotonic() - start:.0f} s).")
            return False

        try:
            daten = json.loads(ergebnis_datei.read_text(encoding="utf-8"))
        except (OSError, ValueError) as e:
            print(f"    [FEHLER] Ergebnisdatei unlesbar: {e}")
            return False
        dauer = time.monotonic() - start
        if not daten.get("ok"):
            print(f"    [FEHLER] Das Fenster ging auf, die Oberfläche stand aber nicht: "
                  f"{daten.get('fehler', '?')}")
            return False
        print(f"    [OK] Fenster offen, Oberfläche steht (nach {dauer:.0f} s).")

        # Nach destroy() muss sie sich auch wirklich beenden - ein hängender
        # Thread hielte sonst nach jedem Schliessen einen Geisterprozess
        # (mit belegten WLAN-Ports) am Leben.
        try:
            lauf.wait(timeout=BEENDEN_FRIST)
        except subprocess.TimeoutExpired:
            print(f"    [FEHLER] Nach dem Schliessen läuft die .exe nach {BEENDEN_FRIST} s noch weiter.")
            return False
        if lauf.returncode != 0:
            print(f"    [FEHLER] Beendet mit Rückgabewert {lauf.returncode}.")
            return False
        print("    [OK] Nach dem Schliessen sauber beendet (Rückgabewert 0).")
        ok = True
        return True
    finally:
        _beenden(lauf)
        if not ok or protokoll:
            _protokoll_zeigen(wegwerf, protokoll)
        # Kurz warten: Windows gibt die .exe nicht sofort frei
        for _ in range(10):
            shutil.rmtree(wegwerf, ignore_errors=True)
            if not wegwerf.exists():
                break
            time.sleep(0.5)


def main(argumente=None):
    teile = argparse.ArgumentParser(description="Startet VP4.exe einmal zur Probe.")
    teile.add_argument("exe", help="Pfad zur VP4.exe")
    teile.add_argument("--protokoll", metavar="ORDNER",
                       help="vp4.log und selbsttest.json hierhin kopieren")
    teile.add_argument("--frist", type=int, default=SELBSTTEST_FRIST,
                       help=f"Sekunden bis zum Ergebnis (Standard {SELBSTTEST_FRIST})")
    teile.add_argument("--ohne-selbsttest", action="store_true",
                       help="nur prüfen, ob sie nach 9 s noch läuft")
    a = teile.parse_args(argumente)
    ok = probelauf(a.exe, a.protokoll, a.frist, False if a.ohne_selbsttest else None)
    return 0 if ok else 1


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.exit(main())
