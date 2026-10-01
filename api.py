#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
=====================================================================
 api.py - alles, was die Oberfläche von Python wollen darf
=====================================================================
pywebview macht JEDE öffentliche Methode dieses Objekts aus JavaScript
aufrufbar - und auch die Methoden öffentlicher Attribute. Deshalb:

  - Alles Interne beginnt mit "_" (pywebview lässt das aus). Ein Test
    prüft, dass hier keine öffentlichen Attribute ausser Methoden stehen;
    sonst wären Tresor oder Datenbank aus der Seite heraus erreichbar.
  - Jede Methode gibt {"ok": True, ...} oder {"ok": False, "fehler": "..."}
    zurück und wirft nie bis nach JavaScript durch.
  - Schlüssel verlassen Python nur dort, wo der Benutzer sie ausdrücklich
    sehen will (Werkzeuge, Schlüsselbund) - nie aus dem Chat.
=====================================================================
"""

import base64
import functools
import json
import logging
import os
import secrets
import subprocess
import sys
import tempfile
import threading
import time
import webbrowser
from pathlib import Path

from kern import krypto, speicher
from kern.krypto import (SCHLUESSEL_ARTEN, VERFAHREN, ClassicCiphers, ModernCrypto,
                         Pruefsummen, Signaturen)

log = logging.getLogger("vp4.api")

# Methoden, die keine "Aktivität" sind (sonst sperrt Auto-Sperre nie)
_HINTERGRUND = {"ereignisse_holen", "status"}


def _antwort(funktion):
    """Fängt alles ab und macht daraus eine Antwort für die Seite."""
    @functools.wraps(funktion)
    def huelle(self, *args, **kwargs):
        if funktion.__name__ not in _HINTERGRUND:
            self._dienst.letzte_aktivitaet = time.time()
        try:
            ergebnis = funktion(self, *args, **kwargs)
            if ergebnis is None:
                ergebnis = {}
            return {"ok": True, **ergebnis} if isinstance(ergebnis, dict) else {"ok": True, "wert": ergebnis}
        except (ValueError, FileNotFoundError, PermissionError, ConnectionError) as e:
            return {"ok": False, "fehler": str(e) or type(e).__name__}
        except Exception as e:
            log.exception("Fehler in %s", funktion.__name__)
            return {"ok": False, "fehler": f"Unerwarteter Fehler: {type(e).__name__}: {e}"}
    return huelle


def _bote_noetig(funktion):
    @functools.wraps(funktion)
    def huelle(self, *args, **kwargs):
        if not self._dienst.bote:
            raise ValueError("VP4 ist gesperrt.")
        return funktion(self, *args, **kwargs)
    return huelle


class VP4Api:
    def __init__(self, dienst, fenster_holen=lambda: None):
        self._dienst = dienst
        self._fenster_holen = fenster_holen
        self._auftraege = {}
        self._lock = threading.Lock()

    # ------------------------------------------------------- intern
    @property
    def _bote(self):
        return self._dienst.bote

    def _dialog(self, art="datei", mehrere=False, speichern_als=None, typen=()):
        """Datei-Dialog von pywebview - im Test/Browser-Modus nicht vorhanden."""
        fenster = self._fenster_holen()
        if fenster is None:
            raise ValueError("Dateiauswahl gibt es nur im Programmfenster.")
        import webview
        if art == "ordner":
            arten = webview.FileDialog.FOLDER
        elif speichern_als:
            arten = webview.FileDialog.SAVE
        else:
            arten = webview.FileDialog.OPEN
        ergebnis = fenster.create_file_dialog(arten, allow_multiple=mehrere,
                                              save_filename=speichern_als or "", file_types=tuple(typen))
        if not ergebnis:
            return None
        return list(ergebnis) if mehrere else (ergebnis[0] if isinstance(ergebnis, (list, tuple)) else ergebnis)

    def _ereignis(self, typ, **daten):
        self._dienst.ereignisse.melden(typ, **daten)

    # =================================================================
    #  Zustand, Einrichten, Sperren
    # =================================================================

    @_antwort
    def status(self):
        return self._dienst.status()

    @_antwort
    def ereignisse_holen(self):
        liste = self._dienst.ereignisse.holen()
        if any(e["typ"] == "mitteilung" for e in liste):
            self._aufmerksam_machen()
        return {"liste": liste}

    def _aufmerksam_machen(self):
        """Taskleiste blinken lassen, wenn VP4 nicht vorne ist (nur Windows)."""
        if sys.platform != "win32":
            return
        try:
            import ctypes
            from ctypes import wintypes
            fenster = self._fenster_holen()
            hwnd = getattr(getattr(fenster, "native", None), "Handle", None)
            if hwnd is None:
                return
            hwnd = int(str(hwnd.ToInt64())) if hasattr(hwnd, "ToInt64") else int(hwnd)
            if ctypes.windll.user32.GetForegroundWindow() == hwnd:
                return

            class FLASHWINFO(ctypes.Structure):
                _fields_ = [("cbSize", wintypes.UINT), ("hwnd", wintypes.HWND), ("dwFlags", wintypes.DWORD),
                            ("uCount", wintypes.UINT), ("dwTimeout", wintypes.DWORD)]
            info = FLASHWINFO(ctypes.sizeof(FLASHWINFO), hwnd, 0x0000000E, 0, 0)   # TRAY | TIMERNOFG
            ctypes.windll.user32.FlashWindowEx(ctypes.byref(info))
        except Exception:
            log.debug("Blinken ging nicht", exc_info=True)

    @_antwort
    def passwort_staerke(self, passwort):
        stufe, text = speicher.passwort_staerke(passwort or "")
        return {"stufe": stufe, "text": text[:1].upper() + text[1:]}

    @_antwort
    def einrichten(self, daten):
        profil = self._dienst.einrichten(daten.get("name", ""), daten.get("avatar_farbe", ""),
                                         daten.get("passwort", ""), bool(daten.get("windows_merken")))
        return {"profil": profil}

    @_antwort
    def entsperren(self, passwort):
        self._dienst.entsperren(passwort or "")

    @_antwort
    def sperren(self):
        self._dienst.sperren()

    @_antwort
    def migrieren(self, alt_passwort):
        from kern import migration
        try:
            ergebnis = migration.migrieren(self._dienst.ordner, alt_passwort or "", tresor=self._dienst.tresor)
        except speicher.FalschesPasswortError:
            raise ValueError("Das alte Passwort stimmt nicht.") from None
        for k, v in (ergebnis.get("einstellungen") or {}).items():
            if k == "design" and v in ("light", "dark", "system"):
                self._dienst.einstellung_setzen("design", v)
            elif k == "obsidian_vault" and v:
                self._dienst.einstellung_setzen("obsidian_vault", v)
            elif k == "transport_modus" and v in ("lan", "discord", "beide"):
                self._dienst.einstellung_setzen("transport_modus", v)
        discord = ergebnis.get("discord")
        if discord and discord.get("bot_token"):
            from netz.discord_netz import zugang_speichern
            zugang_speichern(discord["bot_token"], discord.get("kanal_id", ""))
        return {"schluessel": ergebnis.get("schluessel_anzahl", 0),
                "alte_kontakte": [{"id": k["id"], "name": k.get("spitzname") or k["id"]}
                                  for k in ergebnis.get("alte_kontakte", [])]}

    @_antwort
    def passwort_aendern(self, alt, neu):
        self._dienst.passwort_aendern(alt or "", neu or "")

    @_antwort
    def alles_loeschen(self):
        self._dienst.alles_loeschen()

    # =================================================================
    #  Profil und Einstellungen
    # =================================================================

    @_antwort
    def einstellung_setzen(self, schluessel, wert):
        self._dienst.einstellung_setzen(schluessel, wert)

    @_antwort
    def profil_setzen(self, aenderung):
        return {"profil": self._dienst.profil_setzen(aenderung.get("name"), aenderung.get("avatar_farbe"))}

    @_antwort
    @_bote_noetig
    def freundescode(self):
        return {"code": self._bote.freundescode()}

    @_antwort
    def sicherheit_status(self):
        return {"windows_verfuegbar": self._dienst.windows_verfuegbar()}

    @_antwort
    def daten_status(self):
        gesamt = 0
        for wurzel, _, dateien in os.walk(self._dienst.ordner):
            for d in dateien:
                try:
                    gesamt += os.path.getsize(os.path.join(wurzel, d))
                except OSError:
                    pass
        from kern.dateien import groesse_lesbar
        return {"ordner": str(self._dienst.ordner), "belegt": groesse_lesbar(gesamt)}

    @_antwort
    def ordner_oeffnen(self):
        self._oeffnen(self._dienst.ordner)

    def _oeffnen(self, pfad):
        pfad = str(pfad)
        if sys.platform == "win32":
            os.startfile(pfad)          # noqa - nur unter Windows vorhanden
        elif sys.platform == "darwin":
            subprocess.Popen(["open", pfad])
        else:
            subprocess.Popen(["xdg-open", pfad])

    @_antwort
    def link_oeffnen(self, url):
        url = str(url or "")
        if not url.startswith(("https://", "http://")):
            raise ValueError("Nur Webadressen lassen sich öffnen.")
        webbrowser.open(url)

    @_antwort
    def update_pruefen(self):
        from netz import update
        try:
            ergebnis = update.pruefen()
        except Exception as e:
            raise ValueError(f"GitHub war nicht erreichbar ({type(e).__name__}).") from None
        self._dienst.update = ergebnis
        return ergebnis

    @_antwort
    def discord_status(self):
        from netz.discord_netz import zugang_laden
        z = zugang_laden()
        v = self._dienst.verbindung()
        return {"verbunden": v.get("discord") == "an", "fehler": v.get("discord") == "fehler",
                "meldung": v.get("meldung") or ("Eingebauter Zugang" if z.get("eingebaut") else "Eigener Bot" if z.get("token") else "Kein Discord-Zugang hinterlegt"),
                "eingebaut": bool(z.get("eingebaut")), "anzahl_kanaele": len(z.get("kanal_ids") or []),
                "eigene_kanaele": ""}

    @_antwort
    def discord_setzen(self, token, kanal_ids):
        from netz.discord_netz import zugang_speichern
        zugang_speichern(token or "", kanal_ids or "")
        # Neu verbinden: Netz einmal neu aufbauen
        d = self._dienst
        if d.bote and d.netz:
            d.netz.stop()
            d.netz = d._netz_fabrik(d)
            d.netz.start()

    # =================================================================
    #  Chats
    # =================================================================

    @_antwort
    @_bote_noetig
    def unterhaltungen(self):
        return {"liste": self._bote.unterhaltungen(self._dienst.online, self._dienst.weg_zu)}

    @_antwort
    @_bote_noetig
    def communities(self):
        return {"liste": [c for c in self._bote.communities()]}

    @_antwort
    @_bote_noetig
    def anfragen(self):
        return {"liste": self._bote.anfragen()}

    @_antwort
    @_bote_noetig
    def nachrichten(self, unterhaltung, vor_ts=None):
        liste, mehr = self._bote.nachrichten(unterhaltung, vor_ts)
        return {"liste": liste, "mehr": mehr}

    @_antwort
    @_bote_noetig
    def senden(self, unterhaltung, daten):
        return {"id": self._bote.text_senden(unterhaltung, (daten or {}).get("text", ""), (daten or {}).get("antwort_auf"))}

    @_antwort
    @_bote_noetig
    def erneut_senden(self, unterhaltung, nachricht_id):
        return {"id": self._bote.erneut_senden(unterhaltung, nachricht_id)}

    @_antwort
    @_bote_noetig
    def bearbeiten(self, unterhaltung, nachricht_id, text):
        self._bote.bearbeiten(unterhaltung, nachricht_id, text)

    @_antwort
    @_bote_noetig
    def loeschen(self, unterhaltung, nachricht_id, fuer_alle):
        self._bote.loeschen(unterhaltung, nachricht_id, bool(fuer_alle))

    @_antwort
    @_bote_noetig
    def reagieren(self, unterhaltung, nachricht_id, emoji):
        self._bote.reagieren(unterhaltung, nachricht_id, emoji)

    @_antwort
    @_bote_noetig
    def gelesen(self, unterhaltung):
        self._bote.gelesen(unterhaltung)

    @_antwort
    @_bote_noetig
    def tippt(self, unterhaltung):
        self._bote.tippt(unterhaltung)

    @_antwort
    @_bote_noetig
    def entwurf_setzen(self, unterhaltung, text):
        if self._dienst.db.unterhaltung_holen(unterhaltung):
            self._dienst.db.entwurf_setzen(unterhaltung, (text or "")[:8000])

    @_antwort
    @_bote_noetig
    def suchen(self, unterhaltung, text):
        treffer = self._dienst.db.nachrichten_suchen(unterhaltung, text or "", 100)
        return {"liste": [self._bote.ui_nachricht(n) for n in treffer if n["art"] != "system"]}

    @_antwort
    @_bote_noetig
    def unterhaltung_setzen(self, unterhaltung, aenderung):
        db = self._dienst.db
        if "stumm" in aenderung:
            db.stumm_setzen(unterhaltung, bool(aenderung["stumm"]))
        if "angeheftet" in aenderung:
            db.angeheftet_setzen(unterhaltung, bool(aenderung["angeheftet"]))
        self._ereignis("unterhaltungen_geaendert")

    @_antwort
    @_bote_noetig
    def unterhaltung_entfernen(self, unterhaltung):
        u = self._dienst.db.unterhaltung_holen(unterhaltung)
        if not u:
            raise ValueError("Gibt es nicht mehr.")
        if u["art"] == "dm":
            self._bote.kontakt_entfernen(unterhaltung)
        else:
            self._bote.community_verlassen(u["community_id"])

    @_antwort
    @_bote_noetig
    def unterhaltung_info(self, unterhaltung):
        u = self._dienst.db.unterhaltung_holen(unterhaltung)
        medien = []
        for n in self._dienst.db.nachrichten_seite(unterhaltung, anzahl=200):
            if n["art"] == "bild" and not n["geloescht"]:
                ui = self._bote.ui_nachricht(n, [])
                if (ui.get("datei") or {}).get("vorschau"):
                    medien.append({"vorschau": ui["datei"]["vorschau"], "url": ui["datei"].get("url"), "name": ui["datei"].get("name")})
            if len(medien) >= 30:
                break
        mitglieder = self._bote.mitglieder(u["community_id"]) if u and u["community_id"] else []
        return {"medien": medien, "mitglieder": mitglieder[:50]}

    # ---------------------------------------------------------- Kontakte
    @_antwort
    @_bote_noetig
    def kontakt_hinzufuegen(self, eingabe):
        return self._bote.kontakt_hinzufuegen(eingabe)

    @_antwort
    @_bote_noetig
    def anfrage_beantworten(self, kontakt_id, annehmen):
        self._bote.anfrage_beantworten(kontakt_id, bool(annehmen))

    @_antwort
    @_bote_noetig
    def kontakt_info(self, kontakt_id):
        return self._bote.kontakt_info(kontakt_id)

    @_antwort
    @_bote_noetig
    def verifizieren(self, kontakt_id, ja):
        self._bote.verifizieren(kontakt_id, bool(ja))

    @_antwort
    @_bote_noetig
    def schluessel_annehmen(self, kontakt_id):
        # Dieselbe ID mit anderen Schlüsseln wird nie angenommen (die ID IST
        # der Schlüssel). Der Weg ist: Kontakt entfernen und neu hinzufügen.
        raise ValueError("Entferne den Kontakt und füge ihn mit seiner aktuellen ID neu hinzu.")

    # ------------------------------------------------- Gruppen, Communities
    @_antwort
    @_bote_noetig
    def community_erstellen(self, name, icon):
        return {"id": self._bote.community_erstellen(name, icon or "", gruppe=False)}

    @_antwort
    @_bote_noetig
    def gruppe_erstellen(self, name, icon):
        return {"id": self._bote.community_erstellen(name, icon or "", gruppe=True)}

    @_antwort
    @_bote_noetig
    def community_beitreten(self, code):
        return self._bote.community_beitreten(code)

    @_antwort
    @_bote_noetig
    def community_verlassen(self, cid):
        self._bote.community_verlassen(cid)

    @_antwort
    @_bote_noetig
    def einladung(self, cid):
        return {"code": self._bote.einladung(cid)}

    @_antwort
    @_bote_noetig
    def kanal_anlegen(self, cid, name, nur_admins):
        return {"id": self._bote.kanal_anlegen(cid, name, bool(nur_admins))}

    @_antwort
    @_bote_noetig
    def community_code_erneuern(self, cid):
        return {"code": self._bote.community_code_erneuern(cid)}

    # ------------------------------------------------------------ Dateien
    @_antwort
    @_bote_noetig
    def datei_senden(self, unterhaltung, pfad=None, art="auto"):
        if not pfad:
            typen = ("Bilder und Videos (*.jpg;*.jpeg;*.png;*.gif;*.webp;*.heic;*.mp4;*.mov;*.webm)",) if art == "medien" else ()
            pfad = self._dialog(typen=typen)
            if not pfad:
                return {}
        return {"id": self._bote.datei_senden(unterhaltung, pfad)}

    @_antwort
    @_bote_noetig
    def datei_daten_senden(self, unterhaltung, name, mime, daten_b64):
        roh = base64.b64decode(daten_b64 or "")
        with tempfile.TemporaryDirectory() as tmp:
            ziel = Path(tmp) / Path(name or "Datei").name
            ziel.write_bytes(roh)
            return {"id": self._bote.datei_senden(unterhaltung, ziel)}

    @_antwort
    @_bote_noetig
    def sprachnachricht_senden(self, unterhaltung, daten_b64, dauer_ms, wellenform):
        roh = base64.b64decode(daten_b64 or "")
        if len(roh) > 20 * 1024 * 1024:
            raise ValueError("Die Sprachnachricht ist zu lang.")
        with tempfile.TemporaryDirectory() as tmp:
            ziel = Path(tmp) / f"Sprachnachricht-{time.strftime('%Y%m%d-%H%M%S')}.webm"
            ziel.write_bytes(roh)
            return {"id": self._bote.datei_senden(unterhaltung, ziel, art="sprache",
                                                  dauer_ms=dauer_ms, wellenform=wellenform)}

    def _anhang_pfad(self, nachricht_id):
        n = self._dienst.db.nachricht_holen(nachricht_id)
        meta = ((n or {}).get("inhalt") or {}).get("datei") or {}
        anhang = self._dienst.db.anhang_holen(meta.get("datei_id", "")) or {}
        if anhang.get("zustand") != "fertig" or not anhang.get("pfad"):
            raise ValueError("Die Datei ist noch nicht (vollständig) da.")
        return Path(anhang["pfad"]), anhang.get("name") or meta.get("name") or "Datei"

    @_antwort
    @_bote_noetig
    def datei_oeffnen(self, nachricht_id):
        pfad, name = self._anhang_pfad(nachricht_id)
        # Mit echtem Namen in einen Ordner kopieren - Programme brauchen die Endung
        ziel_ordner = self._dienst.ordner / "geoeffnet"
        ziel_ordner.mkdir(exist_ok=True)
        ziel = ziel_ordner / Path(name).name
        import shutil
        shutil.copyfile(pfad, ziel)
        self._oeffnen(ziel)

    @_antwort
    @_bote_noetig
    def datei_speichern(self, nachricht_id):
        pfad, name = self._anhang_pfad(nachricht_id)
        ziel = self._dialog(speichern_als=Path(name).name)
        if ziel:
            import shutil
            shutil.copyfile(pfad, ziel)
            return {"ziel": str(ziel)}

    # =================================================================
    #  Werkzeuge
    # =================================================================

    @_antwort
    def verfahren(self):
        liste = []
        for name, info in VERFAHREN.items():
            beschriftung, erzeugbar = SCHLUESSEL_ARTEN.get(info["key"], ("Schlüssel", False))
            liste.append({"name": name, "art": "pq" if info["key"] == "pq" else info["art"], "key": info["key"],
                          "hinweis": info["hinweis"], "schluessel_beschriftung": beschriftung,
                          "erzeugbar": erzeugbar})
        return {"liste": liste}

    @_antwort
    def text_verarbeiten(self, name, modus, text, schluessel):
        info = VERFAHREN.get(name)
        if not info:
            raise ValueError("Unbekanntes Verfahren.")
        if not text:
            raise ValueError("Bitte zuerst einen Text eingeben.")
        if info["key"] != "keiner" and not (schluessel or "").strip():
            raise ValueError("Bitte einen Schlüssel eingeben.")
        funktion = info["enc"] if modus == "ver" else info["dec"]
        if info["key"] == "keiner":
            ergebnis = funktion(text)
        else:
            ergebnis = funktion(text, schluessel.strip() if info["key"] not in ("passwort", "wort") else schluessel)
        return {"ergebnis": ergebnis}

    @_antwort
    def schluessel_erzeugen(self, name):
        info = VERFAHREN.get(name)
        if not info:
            raise ValueError("Unbekanntes Verfahren.")
        art = info["key"]
        if art == "aes":
            return {"schluessel": ModernCrypto.generate_aes_key()}
        if art == "chacha":
            return {"schluessel": ModernCrypto.generate_chacha_key()}
        if art == "rsa":
            privat, oeffentlich = ModernCrypto.generate_rsa_keypair()
            return {"privat": privat, "oeffentlich": oeffentlich}
        if art == "pq":
            from kern import krypto_neu
            privat, oeffentlich = krypto_neu.pq_schluesselpaar()
            return {"privat": privat, "oeffentlich": oeffentlich}
        if art == "age":
            from kern import krypto_neu
            privat, oeffentlich = krypto_neu.age_schluesselpaar()
            return {"privat": privat, "oeffentlich": oeffentlich}
        if art == "passwort":
            # Ein starkes Passwort zum Merken: 6 Wörter wären schöner, aber
            # 20 zufällige Zeichen ohne Verwechsler tun es auch
            zeichen = "ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz23456789-_"
            return {"schluessel": "".join(secrets.choice(zeichen) for _ in range(20))}
        raise ValueError("Für dieses Verfahren gibt es keinen Schlüssel zum Erzeugen.")

    def _tresor(self):
        if not self._dienst.tresor.is_unlocked():
            raise ValueError("VP4 ist gesperrt.")
        return self._dienst.tresor

    @_antwort
    def schluesselbund(self):
        liste = []
        for k in self._tresor().schluessel_liste():
            liste.append({"label": k["label"], "typ": k["typ"], "meta": k.get("meta", ""),
                          "verfahren": "", "erstellt": k.get("erstellt", "")})
        return {"liste": liste}

    @_antwort
    def schluessel_speichern(self, label, typ, wert, meta=""):
        label = (label or "").strip()
        if not label:
            raise ValueError("Bitte einen Namen angeben.")
        if isinstance(wert, dict):
            wert = json.dumps({"privat": wert.get("privat", ""), "oeffentlich": wert.get("oeffentlich", "")})
        self._tresor().schluessel_hinzufuegen(label, typ, wert, meta or "")

    @_antwort
    def schluessel_wert(self, label, zum_verschluesseln=True):
        k = self._tresor().schluessel_holen(label)
        if not k:
            raise ValueError("Diesen Schlüssel gibt es nicht.")
        wert = k.get("wert") if "wert" in k else k.get("value")
        if isinstance(wert, str) and wert.startswith("{\"privat\""):
            paar = json.loads(wert)
            wert = paar["oeffentlich"] if zum_verschluesseln else paar["privat"]
        elif isinstance(wert, dict):
            wert = wert.get("oeffentlich" if zum_verschluesseln else "privat") or json.dumps(wert)
        return {"wert": wert}

    @_antwort
    def schluessel_loeschen(self, label):
        self._tresor().schluessel_loeschen(label)

    @_antwort
    def signatur_paar(self):
        privat, oeffentlich = Signaturen.generate_keypair()
        return {"privat": privat, "oeffentlich": oeffentlich}

    @_antwort
    def signieren(self, text, privat):
        if not text:
            raise ValueError("Bitte einen Text eingeben.")
        return {"signatur": Signaturen.sign(text, (privat or "").strip())}

    @_antwort
    def signatur_pruefen(self, text, signatur, oeffentlich):
        return {"gueltig": bool(Signaturen.verify(text or "", (signatur or "").strip(), (oeffentlich or "").strip()))}

    @_antwort
    def pruefsumme_text(self, text):
        return {"name": "Text", "summen": {v: Pruefsummen.berechne(text or "", v) for v in Pruefsummen.VERFAHREN}}

    @_antwort
    def pruefsumme_datei(self):
        pfad = self._dialog()
        if not pfad:
            raise ValueError("")
        return {"name": Path(pfad).name, "groesse": Path(pfad).stat().st_size,
                "summen": {v: Pruefsummen.berechne_datei(pfad, v) for v in ("SHA-256", "SHA-512")}}

    # ------------------------------------------------ Dateien verschlüsseln
    @_antwort
    def datei_verschluesseln(self, optionen):
        art = optionen.get("art", "datei")
        quelle = self._dialog(art="ordner" if art == "ordner" else "datei")
        if not quelle:
            raise ValueError("")
        return self._auftrag_starten("ver", Path(quelle), optionen)

    @_antwort
    def datei_entschluesseln(self, optionen):
        quelle = self._dialog(typen=("Verschlüsselt (*.vp4;*.age)", "Alle Dateien (*.*)"))
        if not quelle:
            raise ValueError("")
        return self._auftrag_starten("ent", Path(quelle), optionen)

    def _auftrag_starten(self, modus, quelle: Path, optionen):
        from kern import dateien
        kennung = secrets.token_hex(8)
        abbruch = threading.Event()
        self._auftraege[kennung] = abbruch
        passwort = optionen.get("passwort") or ""
        format_ = optionen.get("format", "vp4")

        def melden(getan, gesamt):
            self._ereignis("fortschritt", auftrag=kennung, fertig=getan, gesamt=gesamt or 1,
                           anteil=(getan / gesamt) if gesamt else 0)

        def arbeit():
            try:
                if modus == "ver":
                    if format_ == "age":
                        from kern import krypto_neu
                        if quelle.is_dir():
                            raise ValueError("Ordner gehen im age-Format nicht direkt – nimm .vp4 oder packe ihn vorher als ZIP.")
                        ziel = quelle.with_name(quelle.name + ".age")
                        krypto_neu.age_datei_verschluesseln(quelle, ziel, passwort=passwort)
                    else:
                        ziel = dateien.zielname(quelle)
                        dateien.verschluesseln(quelle, ziel, passwort, dateien.ART_PASSWORT,
                                               fortschritt=melden, abbruch=abbruch)
                else:
                    if quelle.suffix.lower() == ".age":
                        from kern import krypto_neu
                        ziel = quelle.with_suffix("") if quelle.suffix else quelle.with_name(quelle.name + ".entschluesselt")
                        if ziel.exists():
                            ziel = ziel.with_name(ziel.stem + " (entschlüsselt)" + ziel.suffix)
                        krypto_neu.age_datei_entschluesseln(quelle, ziel, passwort=passwort)
                    else:
                        ziel = dateien.entschluesseln(quelle, quelle.parent, passwort,
                                                      fortschritt=melden, abbruch=abbruch)
                self._ereignis("auftrag_fertig", auftrag=kennung, ziel=str(ziel))
            except dateien.AbgebrochenError:
                self._ereignis("auftrag_fehler", auftrag=kennung, fehler="Abgebrochen.")
            except Exception as e:
                self._ereignis("auftrag_fehler", auftrag=kennung, fehler=str(e) or type(e).__name__)
            finally:
                self._auftraege.pop(kennung, None)

        threading.Thread(target=arbeit, name=f"vp4-auftrag-{kennung}", daemon=True).start()
        return {"auftrag": kennung, "name": quelle.name}

    @_antwort
    def auftrag_abbrechen(self, kennung):
        ereignis = self._auftraege.get(kennung)
        if ereignis:
            ereignis.set()

    # ----------------------------------------------------------- Obsidian
    @_antwort
    def obsidian_status(self):
        ordner = self._dienst.einst.get("obsidian_vault", "")
        return {"ordner": ordner, "notiz": speicher.ObsidianSync.NOTE_NAME if ordner else ""}

    @_antwort
    def obsidian_ordner_waehlen(self):
        ordner = self._dialog(art="ordner")
        if not ordner:
            raise ValueError("")
        self._dienst.einstellung_setzen("obsidian_vault", str(ordner))

    @_antwort
    def obsidian_export(self, passwort=None):
        sync = speicher.ObsidianSync(self._dienst.einst.get("obsidian_vault", ""))
        schluessel = [dict(k, wert=k.get("wert", k.get("value", ""))) for k in self._tresor().schluessel_liste()]
        if not passwort:
            sync.export_keys(schluessel)
            return {"anzahl": len(schluessel)}
        from kern import krypto_neu
        with tempfile.TemporaryDirectory() as tmp:
            text = speicher.ObsidianSync(tmp).export_keys(schluessel).read_text(encoding="utf-8")
        geheim = krypto_neu.age_passwort_verschluesseln(text, passwort)
        ziel = sync.vault_path / "VP4 Schluessel (verschluesselt).md"
        ziel.write_text("Mit VP4 oder dem Programm `age` und dem Passwort zu öffnen.\n\n```\n"
                        + geheim.strip() + "\n```\n", encoding="utf-8")
        return {"anzahl": len(schluessel)}

    @_antwort
    def obsidian_import(self, passwort=None):
        sync = speicher.ObsidianSync(self._dienst.einst.get("obsidian_vault", ""))
        verschluesselt = sync.vault_path / "VP4 Schluessel (verschluesselt).md"
        if verschluesselt.exists() and not sync.note_path.exists():
            if not passwort:
                return {"ok": False, "passwort_noetig": True, "fehler": "Die Notiz ist verschlüsselt."}
            from kern import krypto_neu
            text = verschluesselt.read_text(encoding="utf-8")
            anfang = text.find("-----BEGIN AGE ENCRYPTED FILE-----")
            ende = text.find("-----END AGE ENCRYPTED FILE-----")
            klar = krypto_neu.age_passwort_entschluesseln(text[anfang:ende + 32], passwort)
            with tempfile.TemporaryDirectory() as tmp:
                (Path(tmp) / speicher.ObsidianSync.NOTE_NAME).write_text(klar, encoding="utf-8")
                keys = speicher.ObsidianSync(tmp).import_keys()
        else:
            keys = sync.import_keys()
        tresor = self._tresor()
        for k in keys:
            tresor.schluessel_hinzufuegen(k["label"], k["typ"], k.get("wert", k.get("value", "")), k.get("meta", ""))
        return {"anzahl": len(keys)}


def oeffentliche_namen(api_klasse=VP4Api) -> list:
    """Für den Test: was pywebview aus diesem Objekt nach JavaScript reicht."""
    return [n for n in dir(api_klasse) if not n.startswith("_")]
