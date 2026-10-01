# -*- coding: utf-8 -*-
"""
=====================================================================
 migration.py - einmalige Übernahme aus VP4 4.x
=====================================================================
Was aus einem alten Datenordner mitkommt:

  schluessel.enc   der Schlüsselring (VP4K2 oder VP4K3) - wandert in den
                   neuen Tresor (VP4K4), mit DEMSELBEN Master-Passwort.
                   Das alte Passwort wird dafür genau einmal abgefragt.
  konfig.json      Design, Akzentfarbe, Obsidian-Ordner, Chat-Weg,
                   Anzeigename
  discord.json     selbst eingetragener Bot-Zugang
  empfangen/       empfangene Dateien

Was NICHT mitkommt, und warum:

  freunde.json / gruppen.json - Die IDs hängen jetzt an Schlüsseln (siehe
  kern/identitaet.py); eine alte ID wie "ABCD-1234" hat keinen. Auch das
  alte Chat-Protokoll samt Gruppenschlüssel fällt weg. Damit niemand seine
  Freunde aus dem Gedächtnis zusammensuchen muss, gibt migrieren() die
  alten Spitznamen und IDs zurück - die Oberfläche zeigt sie einmal im
  Blatt "Kontakte neu hinzufügen".

Die alten Dateien werden NICHT verändert und nicht gelöscht. Nach der
Übernahme liegt im Ordner eine Markierung "migriert_v5.json"; steht die
da, tut migrieren() nichts mehr. Sie enthält nur Zählungen und das
Protokoll, keine Schlüssel und keine Namen.
=====================================================================
"""

import json
import logging
import shutil
from datetime import datetime
from pathlib import Path

from kern.speicher import load_json
from kern.tresor import Tresor, _atomar_schreiben, alten_schluesselring_lesen


log = logging.getLogger("vp4.migration")

MARKIERUNG = "migriert_v5.json"

ALT_SCHLUESSEL = "schluessel.enc"
ALT_KONFIG = "konfig.json"
ALT_FREUNDE = "freunde.json"
ALT_GRUPPEN = "gruppen.json"
ALT_DISCORD = "discord.json"
ALT_EMPFANGEN = "empfangen"

# Diese Einstellungen gelten in 5.0 weiter.
UEBERNOMMENE_EINSTELLUNGEN = ("design", "farbe", "obsidian_vault",
                              "transport_modus", "anzeigename")


def _speicher_art(pfad: Path):
    """"VP4K2", "VP4K3" - oder None, wenn es keine (bekannte) Datei ist."""
    try:
        with open(pfad, "rb") as f:
            marke = f.read(5)
    except OSError:
        return None
    return marke.decode("ascii") if marke in (b"VP4K2", b"VP4K3") else None


def _empfangene_dateien(ordner: Path) -> list:
    wurzel = ordner / ALT_EMPFANGEN
    if not wurzel.is_dir():
        return []
    return sorted(p for p in wurzel.rglob("*") if p.is_file())


def alte_kontakte_lesen(ordner) -> list:
    """Die alten Freunde und Gruppen - nur ID, Name und Art.

    Schlüssel werden bewusst nicht mitgegeben: Sie gehören zum alten
    Protokoll und taugen für 5.0 nicht.
    """
    ordner = Path(ordner)
    ergebnis = []
    freunde = load_json(ordner / ALT_FREUNDE, {})
    if isinstance(freunde, dict):
        for kennung, eintrag in sorted(freunde.items()):
            name = eintrag.get("nickname", "") if isinstance(eintrag, dict) else ""
            ergebnis.append({"id": kennung, "spitzname": name or "", "art": "freund"})
    gruppen = load_json(ordner / ALT_GRUPPEN, {})
    if isinstance(gruppen, dict):
        for kennung, eintrag in sorted(gruppen.items()):
            name = eintrag.get("name", "") if isinstance(eintrag, dict) else ""
            ergebnis.append({"id": kennung, "spitzname": name or "", "art": "gruppe"})
    return ergebnis


def _markierung_schreiben(pfad: Path, inhalt: dict):
    _atomar_schreiben(pfad, json.dumps(inhalt, indent=2,
                                       ensure_ascii=False).encode("utf-8"))


def ist_migriert(ordner) -> bool:
    return (Path(ordner) / MARKIERUNG).exists()


def alte_daten_finden(ordner) -> dict:
    """Schaut nach, was aus 4.x im Ordner liegt. Ändert nichts.

    "noetig" ist wahr, wenn es etwas zu übernehmen gibt und das noch nicht
    geschehen ist. "braucht_passwort" sagt, ob dafür das alte
    Master-Passwort abgefragt werden muss.
    """
    ordner = Path(ordner)
    art = _speicher_art(ordner / ALT_SCHLUESSEL)
    kontakte = alte_kontakte_lesen(ordner)
    konfig = (ordner / ALT_KONFIG).is_file()
    discord = (ordner / ALT_DISCORD).is_file()
    empfangen = _empfangene_dateien(ordner)
    gefunden = bool(art or konfig or kontakte or discord or empfangen)
    bereits = ist_migriert(ordner)
    return {
        "ordner": str(ordner),
        "gefunden": gefunden,
        "bereits_migriert": bereits,
        "noetig": gefunden and not bereits,
        "schluesselspeicher": art,
        "braucht_passwort": art is not None,
        "konfig": konfig,
        "freunde": sum(1 for k in kontakte if k["art"] == "freund"),
        "gruppen": sum(1 for k in kontakte if k["art"] == "gruppe"),
        "discord": discord,
        "empfangen": len(empfangen),
    }


def _einstellungen_lesen(ordner: Path) -> dict:
    alt = load_json(ordner / ALT_KONFIG, {})
    if not isinstance(alt, dict):
        return {}
    return {k: alt[k] for k in UEBERNOMMENE_EINSTELLUNGEN if k in alt}


def _discord_lesen(ordner: Path):
    alt = load_json(ordner / ALT_DISCORD, None)
    if not isinstance(alt, dict):
        return None
    werte = {k: str(v).strip() for k, v in alt.items()
             if k in ("bot_token", "kanal_id") and str(v).strip()}
    return werte or None


def migrieren(ordner, alt_passwort, neuer_tresor_pfad=None, tresor: Tresor = None,
              empfangen_ziel=None) -> dict:
    """Übernimmt einen 4.x-Datenordner - genau einmal.

    ordner              der alte Datenordner (meist vp4_daten/ selbst)
    alt_passwort        das alte Master-Passwort (nur nötig, wenn es einen
                        alten Schlüsselspeicher gibt)
    neuer_tresor_pfad   wohin der neue Tresor kommt; er wird mit demselben
                        Passwort angelegt
    tresor              stattdessen: ein schon entsperrter Tresor, in den
                        der alte Schlüsselring eingemischt wird
    empfangen_ziel      falls die empfangenen Dateien woanders hin sollen:
                        sie werden kopiert (nie verschoben); schon
                        vorhandene Dateien bleiben unangetastet

    Rückgabe (dict):
        bereits_migriert, tresor, schluessel_anzahl, einstellungen,
        discord, alte_kontakte, empfangen, protokoll

    Falsches Passwort: FalschesPasswortError, und es wird nichts
    geschrieben - der nächste Versuch fängt sauber von vorn an.
    """
    ordner = Path(ordner)
    protokoll = []

    def notiz(text: str):
        protokoll.append(text)
        log.info(text)

    if ist_migriert(ordner):
        notiz("Übernahme aus 4.x ist schon erledigt - nichts zu tun.")
        return {"bereits_migriert": True, "tresor": tresor, "schluessel_anzahl": 0,
                "einstellungen": {}, "discord": None, "alte_kontakte": [],
                "empfangen": [], "protokoll": protokoll}

    # 1. Schlüsselring - als Erstes, denn nur hier kann es scheitern
    #    (falsches Passwort). Bis dahin ist nichts geschrieben.
    alt_speicher = ordner / ALT_SCHLUESSEL
    anzahl = 0
    if _speicher_art(alt_speicher):
        if tresor is not None:
            ring = alten_schluesselring_lesen(alt_speicher, alt_passwort)
            tresor.schluessel_ersetzen(ring)
        else:
            if neuer_tresor_pfad is None:
                raise ValueError("Für den neuen Tresor fehlt der Pfad.")
            tresor = Tresor.aus_altem_speicher(alt_speicher, neuer_tresor_pfad,
                                               alt_passwort)
            ring = tresor.schluessel_liste()
        anzahl = len(ring)
        notiz(f"Schlüsselring übernommen: {anzahl} Schlüssel.")
    else:
        notiz("Kein alter Schlüsselspeicher gefunden.")

    # 2. Einstellungen und Discord-Zugang
    einstellungen = _einstellungen_lesen(ordner)
    notiz("Einstellungen übernommen: " + (", ".join(sorted(einstellungen)) or "keine"))
    discord = _discord_lesen(ordner)
    notiz("Eigener Discord-Zugang übernommen." if discord
          else "Kein eigener Discord-Zugang eingetragen.")

    # 3. Empfangene Dateien
    dateien = _empfangene_dateien(ordner)
    empfangen = [str(p) for p in dateien]
    if empfangen_ziel is not None:
        ziel = Path(empfangen_ziel)
        quelle = ordner / ALT_EMPFANGEN
        if ziel.resolve() != quelle.resolve():
            empfangen = []
            for p in dateien:
                neu = ziel / p.relative_to(quelle)
                if not neu.exists():
                    neu.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(p, neu)
                empfangen.append(str(neu))
    notiz(f"Empfangene Dateien: {len(empfangen)}.")

    # 4. Alte Kontakte - nur zum Anzeigen
    alte_kontakte = alte_kontakte_lesen(ordner)
    notiz(f"Alte Kontakte zum Neu-Hinzufügen: {len(alte_kontakte)}.")

    # 5. Markierung: ab jetzt nie wieder. Ohne Schlüssel, ohne Namen.
    # Nicht über speicher.save_json(): das legt nebenbei den echten
    # vp4_daten-Ordner an, auch wenn hier ein ganz anderer gemeint ist.
    _markierung_schreiben(ordner / MARKIERUNG, {
        "version": 1,
        "zeitpunkt": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "schluessel": anzahl,
        "einstellungen": sorted(einstellungen),
        "discord": bool(discord),
        "empfangen": len(empfangen),
        "alte_kontakte": len(alte_kontakte),
        "protokoll": protokoll,
    })
    log.info("Übernahme aus 4.x abgeschlossen.")

    return {"bereits_migriert": False, "tresor": tresor, "schluessel_anzahl": anzahl,
            "einstellungen": einstellungen, "discord": discord,
            "alte_kontakte": alte_kontakte, "empfangen": empfangen,
            "protokoll": protokoll}
