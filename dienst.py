#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
=====================================================================
 dienst.py - VP4 ohne Oberfläche: Tresor, Bote, Netz, Versand
=====================================================================
Der Dienst hält alles zusammen, was läuft, solange VP4 offen ist:

  - den Tresor (Master-Passwort, Datenschlüssel, Windows merkt es sich),
  - die verschlüsselte Datenbank,
  - den Boten (kern/nachrichten.py), der weiss, was eine Nachricht ist,
  - das Netz (WLAN und Discord, netz/vermittler.py),
  - einen Versand-Thread, damit die Oberfläche nie auf das Netz wartet,
  - den Zeitplaner für Lesebestätigungen, Aufräumen und Sperren.

Die Oberfläche spricht nur über api.py mit dem Dienst. Getestet wird der
Dienst ohne Fenster: tests/test_dienst.py spielt zwei Dienste gegen ein
Spielzeug-Netz durch.
=====================================================================
"""

import json
import logging
import os
import queue
import shutil
import threading
import time
from pathlib import Path

from ereignisse import Ereignisse, Zeitplaner
from kern import speicher
from kern.datenbank import Datenbank
from kern.identitaet import Identitaet
from kern.nachrichten import Auftrag, Bote
from kern.speicher import FalschesPasswortError
from kern.tresor import (DPAPI, Tresor, datenschluessel_merken, datenschluessel_vergessen,
                         mit_windows_entsperren)
from version import VERSION

log = logging.getLogger("vp4.dienst")

STANDARD_EINSTELLUNGEN = {
    "design": "system", "farbe": "blau", "tapete": "tahoe",
    "glas_stark": False, "transparenz_reduzieren": False, "bewegung_reduzieren": False,
    "transport_modus": "beide", "lesebestaetigungen": True, "tippanzeige": True,
    "medien_auto_mb": 20, "mitteilungen": True, "mitteilung_vorschau": True, "mitteilung_ton": True,
    "bei_start_fragen": False, "auto_sperre_min": 0, "obsidian_vault": "",
}
# Was die Oberfläche setzen darf - und mit welchem Typ
ERLAUBTE_EINSTELLUNGEN = {
    "design": ("system", "light", "dark"), "farbe": str, "tapete": str,
    "glas_stark": bool, "transparenz_reduzieren": bool, "bewegung_reduzieren": bool,
    "transport_modus": ("lan", "discord", "beide"), "lesebestaetigungen": bool,
    "tippanzeige": bool, "medien_auto_mb": int, "mitteilungen": bool,
    "mitteilung_vorschau": bool, "mitteilung_ton": bool, "bei_start_fragen": bool,
    "auto_sperre_min": int, "obsidian_vault": str,
}


class VP4Dienst:
    def __init__(self, ordner=None, dpapi=None, netz_fabrik=None, update_pruefen=True):
        self.ordner = Path(ordner) if ordner else speicher.DATA_DIR
        self.ordner.mkdir(parents=True, exist_ok=True)
        self.dpapi = dpapi if dpapi is not None else DPAPI()
        self._netz_fabrik = netz_fabrik or self._standard_netz
        self._update_pruefen = update_pruefen
        self.ereignisse = Ereignisse()
        self.zeitplaner = Zeitplaner()
        self.tresor = Tresor(self.ordner / "tresor.enc")
        self.dpapi_datei = self.ordner / "tresor.dpapi"
        self.einst_datei = self.ordner / "einstellungen.json"
        self.medien_ordner = self.ordner / "medien"
        self.einst = dict(STANDARD_EINSTELLUNGEN)
        self.einst.update(speicher.load_json(self.einst_datei, {}).get("einstellungen", {}))
        self._profil = speicher.load_json(self.einst_datei, {}).get("profil", {})
        self.db = None
        self.bote = None
        self.netz = None
        self.medien = None
        self.update = None
        self._versand = None
        self._versand_schlange = queue.Queue()
        self._jobs = []
        self._lock = threading.RLock()
        self.letzte_aktivitaet = time.time()

    # =================================================================
    #  Zustand
    # =================================================================

    def phase(self) -> str:
        if not self.tresor.exists():
            return "einrichten"
        return "bereit" if self.tresor.is_unlocked() and self.bote else "gesperrt"

    def start(self):
        """Beim Programmstart: wenn Windows den Schlüssel kennt, gleich entsperren."""
        if self.tresor.exists() and not self.einst.get("bei_start_fragen"):
            if mit_windows_entsperren(self.tresor, self.dpapi, self.dpapi_datei):
                self._hochfahren()

    def windows_verfuegbar(self) -> bool:
        try:
            return bool(self.dpapi.verfuegbar())
        except Exception:
            return False

    def _einst_speichern(self):
        speicher.save_json(self.einst_datei, {"einstellungen": self.einst, "profil": self._profil})

    def profil(self) -> dict:
        return dict(self._profil)

    # =================================================================
    #  Einrichten, Entsperren, Sperren
    # =================================================================

    def einrichten(self, name: str, avatar_farbe: str, passwort: str, windows_merken: bool) -> dict:
        if self.tresor.exists():
            raise ValueError("VP4 ist schon eingerichtet.")
        name = (name or "").strip()[:64]
        if not name:
            raise ValueError("Bitte gib einen Namen ein.")
        if len(passwort or "") < 8:
            raise ValueError("Das Master-Passwort braucht mindestens 8 Zeichen.")
        self.tresor.create(passwort)
        ich = Identitaet.neu()
        self.tresor.identitaet_setzen(ich.to_bytes())
        self._profil = {"name": name, "avatar_farbe": avatar_farbe or "#0088FF", "id": ich.id}
        self.einst["bei_start_fragen"] = not windows_merken
        self._einst_speichern()
        if windows_merken and self.windows_verfuegbar():
            datenschluessel_merken(self.tresor, self.dpapi, self.dpapi_datei)
        self._hochfahren()
        return self.profil()

    def entsperren(self, passwort: str):
        try:
            self.tresor.unlock(passwort)
        except FalschesPasswortError:
            time.sleep(0.4)       # Raten etwas bremsen
            raise ValueError("Das Passwort stimmt nicht.") from None
        if not self.einst.get("bei_start_fragen") and self.windows_verfuegbar() \
                and not self.dpapi_datei.exists():
            datenschluessel_merken(self.tresor, self.dpapi, self.dpapi_datei)
        self._hochfahren()

    def sperren(self):
        self._herunterfahren()
        self.tresor.lock()
        self.ereignisse.melden("gesperrt")

    def _hochfahren(self):
        with self._lock:
            if self.bote:
                return
            roh = self.tresor.identitaet_holen()
            if roh is None:
                roh = Identitaet.neu().to_bytes()
                self.tresor.identitaet_setzen(roh)
            ich = Identitaet.from_bytes(roh)
            if self._profil.get("id") != ich.id:
                self._profil["id"] = ich.id
                self._einst_speichern()
            self.db = Datenbank(self.ordner / "vp4.db", self.tresor.datenschluessel, ich.id)
            try:
                from medien_server import MedienServer
                self.medien = MedienServer()
                url = self.medien.url
            except OSError:
                self.medien, url = None, (lambda pfad: "")
            self.bote = Bote(ich, self.tresor, self.db, ausgang=self._ausgang,
                             ereignis=self._bote_ereignis, medien_ordner=self.medien_ordner,
                             profil=lambda: self._profil, einstellungen=lambda: self.einst,
                             medien_url=url, loeschen_lassen=self._discord_loeschen)
            self._versand = threading.Thread(target=self._versand_schleife, name="vp4-versand", daemon=True)
            self._versand.start()
            self.netz = self._netz_fabrik(self)
            if self.netz:
                self.netz.start()
            self._jobs = [
                self.zeitplaner.jede(3, self.bote.quittungen_senden, "quittungen"),
                self.zeitplaner.jede(3600, self.bote.teile_aufraeumen, "aufraeumen"),
                self.zeitplaner.jede(30, self._auto_sperre, "auto-sperre"),
            ]
            if self._update_pruefen:
                self._jobs.append(self.zeitplaner.einmal(8, self._update_im_hintergrund, "update"))

    def _herunterfahren(self):
        with self._lock:
            for j in self._jobs:
                self.zeitplaner.abbestellen(j)
            self._jobs = []
            if self.netz:
                try:
                    self.netz.stop()
                except Exception:
                    log.exception("Netz liess sich nicht sauber stoppen")
                self.netz = None
            if self._versand:
                self._versand_schlange.put(None)
                self._versand.join(3)
                self._versand = None
            if self.medien:
                self.medien.stoppen()
                self.medien = None
            if self.db:
                self.db.close()
                self.db = None
            self.bote = None

    def beenden(self):
        self._herunterfahren()
        self.zeitplaner.stoppen()

    # =================================================================
    #  Netz und Versand
    # =================================================================

    def _standard_netz(self, dienst):
        """WLAN + Discord, so wie im fertigen Programm."""
        from netz.vermittler import standard_vermittler
        return standard_vermittler(self.bote.meine_id, self._empfangen, self._netz_ereignis,
                                   self.einst.get("transport_modus", "beide"),
                                   stand_holen=self.db.discord_stand_holen,
                                   stand_setzen=self.db.discord_stand_setzen,
                                   interessant=self._interessant)

    def _interessant(self, an: str) -> bool:
        if not self.bote:
            return False
        return an == self.bote.meine_id or self.db.community_holen(an) is not None

    def _empfangen(self, roh: bytes, weg: str, *rest):
        bote = self.bote
        if bote:
            bote.empfangen(roh, weg)

    def _netz_ereignis(self, typ, **daten):
        if typ == "update_pruefen":
            self.zeitplaner.einmal(1, self._update_im_hintergrund, "update")
            return
        self.ereignisse.melden(typ, **daten)

    def _bote_ereignis(self, typ, **daten):
        if typ == "mitteilung" and not self.einst.get("mitteilungen", True):
            return
        if typ == "mitteilung" and not self.einst.get("mitteilung_vorschau", True):
            daten = dict(daten, text="Neue Nachricht")
        self.ereignisse.melden(typ, **daten)

    def _ausgang(self, auftrag: Auftrag):
        self._versand_schlange.put(auftrag)

    def _versand_schleife(self):
        while True:
            auftrag = self._versand_schlange.get()
            if auftrag is None:
                return
            bote, netz = self.bote, self.netz
            if not bote:
                return
            try:
                if not netz:
                    raise ConnectionError("Kein Netz.")
                weg = netz.senden(auftrag.bytes(), auftrag.an, community=auftrag.community,
                                  fluechtig=auftrag.fluechtig)
                if auftrag.nachricht_id and auftrag.teil is None:
                    bote.gesendet(auftrag.nachricht_id, weg)
                elif auftrag.teil is not None and auftrag.teile:
                    bote._geaendert(auftrag.unterhaltung, auftrag.nachricht_id,
                                    fortschritt=(auftrag.teil + 1) / auftrag.teile)
            except (ConnectionError, OSError, ValueError) as e:
                if auftrag.fluechtig:
                    continue
                if auftrag.nachricht_id:
                    bote.fehlgeschlagen(auftrag.nachricht_id, str(e))
                    self.ereignisse.melden("fehler", text=f"Nicht gesendet: {e}")
                else:
                    log.info("Steuernachricht nicht gesendet: %s", e)
            except Exception:
                log.exception("Versand ist gescheitert")
                if auftrag.nachricht_id:
                    bote.fehlgeschlagen(auftrag.nachricht_id, "unerwarteter Fehler")

    def _discord_loeschen(self, nachricht_id: str):
        netz = self.netz
        if netz and hasattr(netz, "loeschen"):
            try:
                netz.loeschen(nachricht_id)
            except Exception:
                log.info("Löschen in Discord ging nicht", exc_info=True)

    def verbindung(self) -> dict:
        if not self.netz:
            return {"lan": "aus", "discord": "aus"}
        try:
            s = self.netz.status()
            return {"lan": s.get("lan", "aus"), "discord": s.get("discord", "aus"), "meldung": s.get("meldung", "")}
        except Exception:
            return {"lan": "aus", "discord": "aus"}

    def online(self, kontakt_id) -> bool:
        try:
            return bool(self.netz and self.netz.ist_online(kontakt_id))
        except Exception:
            return False

    def weg_zu(self, kontakt_id):
        try:
            return self.netz.weg_zu(kontakt_id) if self.netz else None
        except Exception:
            return None

    # =================================================================
    #  Einstellungen und Profil
    # =================================================================

    def einstellung_setzen(self, schluessel: str, wert):
        regel = ERLAUBTE_EINSTELLUNGEN.get(schluessel)
        if regel is None:
            raise ValueError(f"Unbekannte Einstellung: {schluessel}")
        if isinstance(regel, tuple):
            if wert not in regel:
                raise ValueError("Ungültiger Wert.")
        elif regel is bool:
            wert = bool(wert)
        elif regel is int:
            wert = int(wert)
        else:
            wert = str(wert)[:500]
        self.einst[schluessel] = wert
        self._einst_speichern()
        if schluessel == "transport_modus" and self.netz and hasattr(self.netz, "modus_setzen"):
            self.netz.modus_setzen(wert)
        if schluessel == "bei_start_fragen":
            if wert:
                datenschluessel_vergessen(self.dpapi_datei)
            elif self.tresor.is_unlocked() and self.windows_verfuegbar():
                datenschluessel_merken(self.tresor, self.dpapi, self.dpapi_datei)

    def profil_setzen(self, name=None, avatar_farbe=None) -> dict:
        if name is not None:
            name = str(name).strip()[:64]
            if not name:
                raise ValueError("Der Name darf nicht leer sein.")
            self._profil["name"] = name
        if avatar_farbe:
            self._profil["avatar_farbe"] = str(avatar_farbe)[:20]
        self._einst_speichern()
        if self.bote:
            self.bote.profil_verteilen()
        return self.profil()

    def passwort_aendern(self, alt: str, neu: str):
        if len(neu or "") < 8:
            raise ValueError("Das neue Passwort braucht mindestens 8 Zeichen.")
        try:
            self.tresor.change_password(alt, neu)
        except FalschesPasswortError:
            raise ValueError("Das aktuelle Passwort stimmt nicht.") from None

    def alles_loeschen(self):
        """Alles auf diesem PC weg - der Weg, wenn das Passwort vergessen ist."""
        self._herunterfahren()
        self.tresor.lock()
        for name in ("tresor.enc", "tresor.dpapi", "vp4.db", "vp4.db-wal", "vp4.db-shm", "einstellungen.json"):
            try:
                (self.ordner / name).unlink()
            except FileNotFoundError:
                pass
        shutil.rmtree(self.medien_ordner, ignore_errors=True)
        self.einst = dict(STANDARD_EINSTELLUNGEN)
        self._profil = {}
        self.tresor = Tresor(self.ordner / "tresor.enc")

    def _auto_sperre(self):
        minuten = int(self.einst.get("auto_sperre_min") or 0)
        if minuten and time.time() - self.letzte_aktivitaet > minuten * 60 and self.bote:
            self.sperren()

    def _update_im_hintergrund(self):
        try:
            from netz import update
            ergebnis = update.pruefen()
        except Exception as e:
            log.info("Update-Prüfung ging nicht: %s", e)
            return
        self.update = ergebnis
        if ergebnis.get("neu"):
            self.ereignisse.melden("update_verfuegbar", **ergebnis)

    def status(self) -> dict:
        from version import REPO_URL
        from kern import migration
        alt = None
        if self.phase() == "einrichten":
            try:
                gefunden = migration.alte_daten_finden(self.ordner)
                if gefunden.get("noetig"):
                    alt = {"kontakte": gefunden.get("freunde", 0), "gruppen": gefunden.get("gruppen", 0),
                           "braucht_passwort": gefunden.get("braucht_passwort", False)}
            except Exception:
                alt = None
        return {"phase": self.phase(), "version": VERSION, "repo_url": REPO_URL,
                "profil": self.profil() or None, "einstellungen": dict(self.einst),
                "verbindung": self.verbindung(), "windows_verfuegbar": self.windows_verfuegbar(),
                "alte_daten": alt, "update": self.update if (self.update or {}).get("neu") else None}
