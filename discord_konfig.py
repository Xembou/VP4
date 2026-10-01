#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
=====================================================================
 discord_konfig.py - der eingebaute Discord-Zugang
=====================================================================
Hier stehen absichtlich nur leere Platzhalter. Die echten Werte setzt
der GitHub-Workflow beim Bauen der .exe ein (Schritt "Discord-Zugang
einsetzen" in .github/workflows/release.yml); sie kommen dort aus den
Repository-Secrets DISCORD_BOT_TOKEN und DISCORD_KANAL_IDS und landen nur
in der fertigen .exe - nie in diesem Quelltext. Ein Test wacht darüber.

WARUM ÜBERHAUPT EIN EINGEBAUTER ZUGANG
---------------------------------------
Damit jeder VP4 von GitHub herunterladen und sofort schreiben kann, ohne
einen eigenen Discord-Bot anzulegen. Leon hat das am 01.10.2026 so
entschieden - in Kenntnis dessen, was unten steht.

EHRLICH DAZU
-------------
In der .exe ist der Token nicht versteckt; wer will, holt ihn heraus.
Die NACHRICHTEN bleiben trotzdem geheim - sie sind Ende-zu-Ende
verschlüsselt, der Bot sieht nur Geheimtext. Was jemand mit dem Token
aber kann:
  - den Kanal mit Unsinn fluten oder Zeilen löschen (alle benutzen
    denselben Bot, also auch dieselben Rechte),
  - das gemeinsame Kontingent von 1000 Anmeldungen pro Tag aufbrauchen.
    Dann setzt Discord den Token zurück, und jede VP4-Kopie verliert den
    Discord-Weg, bis es eine neue Version gibt.
Der Weg zurück steht in der README ("Wenn der Discord-Zugang gesperrt
ist"). Wer das nicht will, trägt unter Einstellungen → Discord einen
eigenen Bot ein - der geht dem eingebauten immer vor.

KANAL_IDS: eine oder mehrere Kanal-IDs, mit Komma getrennt. Mehrere
Kanäle verteilen das Discord-Limit (rund 5 Nachrichten in 5 Sekunden pro
Kanal), das sich alle VP4-Nutzer teilen.
=====================================================================
"""

BOT_TOKEN = ""
KANAL_IDS = ""
