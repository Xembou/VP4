#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
=====================================================================
 update.py - gibt es eine neuere VP4-Version auf GitHub?
=====================================================================
Einmal pro Start, eine einzige Anfrage an die öffentliche GitHub-API,
ohne Anmeldung und ohne etwas über den Benutzer mitzuschicken (ausser
der VP4-Version im User-Agent, den GitHub verlangt).

Heruntergeladen wird NIE von selbst. Das Programm sagt nur: "Version
5.1 ist da" und zeigt die Seite. Warum das wichtig ist: Wird der
eingebaute Discord-Zugang gesperrt (siehe README), ist eine neue
Version der einzige Weg zurück - also soll man davon erfahren.
=====================================================================
"""

import json
import re
import urllib.request

from version import REPO, VERSION

ZEITLIMIT = 5


def _zahlen(version: str) -> tuple:
    teile = re.findall(r"\d+", version or "")
    return tuple(int(t) for t in teile[:3]) + (0,) * (3 - len(teile[:3]))


def ist_neuer(kandidat: str, aktuell: str = VERSION) -> bool:
    return _zahlen(kandidat) > _zahlen(aktuell)


def pruefen(timeout: float = ZEITLIMIT) -> dict:
    """{"neu": bool, "version": "5.1.0", "url": "..."}; wirft OSError/ValueError bei Netzproblemen."""
    anfrage = urllib.request.Request(
        f"https://api.github.com/repos/{REPO}/releases/latest",
        headers={"User-Agent": f"VP4/{VERSION}", "Accept": "application/vnd.github+json"})
    with urllib.request.urlopen(anfrage, timeout=timeout) as antwort:
        daten = json.loads(antwort.read(512 * 1024).decode("utf-8"))
    version = str(daten.get("tag_name") or "").lstrip("vV")
    if not version:
        raise ValueError("GitHub hat keine Version genannt.")
    return {"neu": ist_neuer(version), "version": version,
            "url": daten.get("html_url") or f"https://github.com/{REPO}/releases/latest"}
