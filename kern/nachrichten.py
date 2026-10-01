#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
=====================================================================
 nachrichten.py - der Bote: was mit Nachrichten passiert
=====================================================================
Hier laufen alle Fäden des Messengers zusammen: Kontakte und ihre
Anfragen, Direktnachrichten, Gruppen und Communities, Antworten,
Reaktionen, Bearbeiten, Löschen, Lesebestätigungen und Dateien.

Der Bote selbst hat KEINE Threads und KEIN Netz. Er bekommt fertige
Umschlag-Bytes herein (empfangen) und gibt Aufträge zum Verschicken
heraus (über die Funktion `ausgang`). Wer sie wirklich verschickt - WLAN,
Discord oder im Test einfach ein zweiter Bote im selben Prozess -, ist
ihm egal. Dadurch lässt sich der ganze Ablauf zwischen zwei Personen in
einem einzigen Testlauf durchspielen, ohne Discord und ohne Sockets.

WER DARF WAS
-------------
- Direktnachrichten nimmt der Bote nur von Kontakten an, deren Anfrage
  angenommen ist. Alles andere wird verworfen (Ausnahme: Anfragen selbst).
- Bearbeiten, Löschen und Reaktionen gelten nur für den, der sie schickt:
  Niemand kann die Nachricht eines anderen ändern oder löschen.
- In Communities zählt der Code, nicht die Freundesliste - jede Nachricht
  ist aber vom Absender unterschrieben (kern/umschlag.kanal_bauen). Ein
  Name ist deshalb nicht zu fälschen, ein selbst gegebener Spitzname geht
  trotzdem immer vor.
- In Ankündigungskanälen schreiben nur Besitzer und Admins.

WAS DER BOTE NICHT LEISTET
---------------------------
Er macht aus einem gestohlenen Gerät keinen sicheren Ort: Wer den Tresor
offen hat, liest alles. Und er versteckt nicht, wer wann wem schreibt -
die IDs stehen offen im Umschlagkopf.
=====================================================================
"""

import base64
import hashlib
import io
import json
import logging
import mimetypes
import os
import re
import secrets
import shutil
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

from kern import e2e
from kern import umschlag as um
from kern.communities import (community_id_neu, community_schluessel_neu, einladung_bauen,
                              einladung_lesen, ist_community_id, kanal_id_neu, manifest_bauen,
                              manifest_pruefen, standard_kanal_id)
from kern.identitaet import (freundescode_bauen, freundescode_lesen, id_normalisieren,
                             karte_bauen, karte_pruefen, karte_schluessel)

log = logging.getLogger("vp4.nachrichten")

# Dateien gehen in Teilen à 8 MiB: Ein Discord-Anhang darf für einen Bot
# rund 10 MiB gross sein, und der Umschlag legt noch etwas drauf.
TEIL_GROESSE = 8 * 1024 * 1024
MAX_DATEI = 100 * 1024 * 1024
VORSCHAU_KANTE = 320
MAX_TEXT = 8000
QUICK_EMOJI_LAENGE = 16
# Ein Zeitstempel ist eine Behauptung des Absenders. Liegt er zu weit in der
# Zukunft, sortiert sich die Nachricht sonst für immer ganz nach unten.
MAX_VORLAUF_MS = 5 * 60 * 1000

SYSTEM = "system"
# Echte Community-Nachrichten sind klein (Text, Karte, Manifest). Was grösser
# ist und sich nicht entschlüsseln lässt, wird nicht aufgehoben - sonst
# könnte jeder, der die (offene) Community-ID kennt, den Speicher füllen.
MAX_COMMUNITY_NACHRICHT = 256 * 1024
MAX_WARTEND_BYTES = 4 * 1024 * 1024
WARTEND_ABLAUF_S = 15 * 60
# Kanalnamen wie bei Discord: klein, ohne Leerzeichen, höchstens 32 Zeichen.
KANALNAME_MAX = 32
_KANALNAME = re.compile(r"[a-z0-9äöüß-]{1,%d}" % KANALNAME_MAX)


def kanalname_saeubern(name) -> str:
    """Macht aus "Haus Aufgaben " den Kanalnamen "haus-aufgaben" - oder lehnt ab.

    Erlaubt sind a-z, 0-9, äöüß und der Bindestrich, 1 bis 32 Zeichen.
    Leerzeichen werden zu Bindestrichen, Grossbuchstaben zu kleinen.
    """
    if not isinstance(name, str):
        raise ValueError("Der Kanalname muss ein Text sein.")
    sauber = "-".join(name.strip().lower().split())
    if not sauber:
        raise ValueError("Der Kanalname darf nicht leer sein.")
    if len(sauber) > KANALNAME_MAX:
        raise ValueError(f"Der Kanalname ist zu lang (höchstens {KANALNAME_MAX} Zeichen).")
    if not _KANALNAME.fullmatch(sauber):
        raise ValueError("Kanalnamen bestehen nur aus a-z, 0-9, ä, ö, ü, ß und Bindestrichen.")
    return sauber


def _dateiname_saeubern(name: str) -> str:
    """Steuer- und Richtungszeichen raus: "rechnung\u202efdp.exe" sähe sonst
    aus wie "rechnungexe.pdf"."""
    import unicodedata
    sauber = "".join(z for z in name if unicodedata.category(z) not in ("Cc", "Cf"))
    sauber = Path(sauber.replace("\\", "/")).name.strip().strip(".")
    return sauber[:200] or "Datei"


# Dateiendungen, die Windows beim Öffnen ausführt
AUSFUEHRBAR = {".exe", ".com", ".scr", ".bat", ".cmd", ".pif", ".lnk", ".url", ".hta", ".js", ".jse",
               ".vbs", ".vbe", ".wsf", ".wsh", ".ps1", ".psm1", ".msi", ".msp", ".reg", ".cpl", ".jar",
               ".appref-ms", ".application", ".gadget", ".msc", ".scf", ".inf", ".dll", ".sys", ".iso", ".img", ".vhd", ".vhdx"}


def _jetzt_ms() -> int:
    return int(time.time() * 1000)


def _b64(roh: bytes) -> str:
    return base64.b64encode(roh).decode("ascii")


def _unb64(text: str) -> bytes:
    return base64.b64decode(text.encode("ascii"), validate=True)


@dataclass
class Auftrag:
    """Etwas, das verschickt werden soll.

    `daten` sind die fertigen Umschlag-Bytes - oder eine Funktion, die sie
    erst beim Senden baut (grosse Dateien sollen nicht alle gleichzeitig im
    Speicher liegen).
    """
    daten: object
    an: str
    community: bool = False
    fluechtig: bool = False          # Tippanzeige: nur WLAN, nie Discord
    nachricht_id: str = None         # dessen Status aktualisiert wird
    unterhaltung: str = None
    teil: int = None                 # bei Datei-Teilen: Nummer (0-basiert)
    teile: int = None

    def bytes(self) -> bytes:
        return self.daten() if callable(self.daten) else self.daten


@dataclass
class _Sammler:
    """Lesebestätigungen werden gesammelt verschickt, nicht einzeln.

    Über Discord teilen sich ALLE VP4-Nutzer ein Limit von rund fünf
    Nachrichten in fünf Sekunden pro Kanal. Eine Bestätigung pro Nachricht
    würde das Limit schnell auffressen.
    """
    zugestellt: dict = field(default_factory=dict)   # unterhaltung -> set(ids)
    gelesen: dict = field(default_factory=dict)


class Bote:
    def __init__(self, identitaet, tresor, db, *, ausgang, ereignis,
                 medien_ordner: Path, profil, einstellungen=lambda: {},
                 medien_url=lambda pfad: "", loeschen_lassen=lambda msg_id_hex: None):
        self.ich = identitaet
        self.tresor = tresor
        self.db = db
        self.ausgang = ausgang
        self.ereignis = ereignis
        self.medien = Path(medien_ordner)
        self.medien.mkdir(parents=True, exist_ok=True)
        (self.medien / "teile").mkdir(exist_ok=True)
        self._profil = profil
        self.einstellungen = einstellungen
        self.medien_url = medien_url
        self.loeschen_lassen = loeschen_lassen
        self._lock = threading.RLock()
        self._sammler = _Sammler()
        self._gesehen = {}                 # msg_id -> Zeit, für Steuer-Nachrichten
        self._wartend = []                 # Community-Umschläge ohne passenden Kanal
        self._manifest_antwort = {}        # community -> Zeitpunkt der letzten Antwort auf einen Beitritt
        self._beitritt_gesehen = {}        # community -> Zeitpunkt des letzten Beitritts
        self._entschluesselungsfehler = {}  # absender -> Zeitpunkt der letzten Meldung

    # =================================================================
    #  Profil und Visitenkarte
    # =================================================================

    @property
    def meine_id(self) -> str:
        return self.ich.id

    def karte(self) -> dict:
        p = self._profil()
        return karte_bauen(self.ich, p.get("name") or "VP4", p.get("avatar_farbe") or "#0088FF")

    def freundescode(self) -> str:
        return freundescode_bauen(self.karte())

    def profil_verteilen(self):
        """Neue Karte (Name, Farbe) an alle Kontakte - unterschrieben, unverschlüsselt."""
        karte = self.karte()
        for k in self.db.kontakte_alle("ok"):
            u = um.anfrage_bauen(self.ich, karte, k["id"], typ=um.KARTE)
            self.ausgang(Auftrag(u.packen(), k["id"]))

    # =================================================================
    #  Kontakte
    # =================================================================

    def name_von(self, kontakt_id: str) -> str:
        """Der Anzeigename: eigener Spitzname vor dem Namen auf der Karte."""
        if kontakt_id == self.meine_id:
            return self._profil().get("name") or "Ich"
        k = self.db.kontakt_holen(kontakt_id)
        if not k:
            return kontakt_id
        if k["spitzname"]:
            return k["spitzname"]
        if k.get("karte"):
            name = k["karte"].get("name") or kontakt_id
            # In Communities ist der Name eine Selbstauskunft - die ID gehört dazu
            return name if k["status"] == "ok" else f"{name} ({kontakt_id[:5]})"
        return kontakt_id

    def farbe_von(self, kontakt_id: str) -> str:
        if kontakt_id == self.meine_id:
            return self._profil().get("avatar_farbe") or "#0088FF"
        k = self.db.kontakt_holen(kontakt_id)
        return ((k or {}).get("karte") or {}).get("avatar_farbe") or ""

    def kontakt_hinzufuegen(self, eingabe: str) -> dict:
        """ID oder Freundescode. Schickt eine Anfrage, die der andere annehmen muss."""
        eingabe = (eingabe or "").strip()
        karte = None
        if eingabe.upper().startswith("VP4C1-"):
            karte = freundescode_lesen(eingabe)
            ziel = karte["id"]
        else:
            ziel = id_normalisieren(eingabe)
        if ziel == self.meine_id:
            raise ValueError("Das ist deine eigene ID.")
        vorhanden = self.db.kontakt_holen(ziel)
        if vorhanden and vorhanden["status"] == "ok":
            raise ValueError("Ihr seid schon verbunden.")
        if vorhanden and vorhanden["status"] == "blockiert":
            self.db.kontakt_status_setzen(ziel, "anfrage_raus")
        if vorhanden and vorhanden["status"] == "anfrage_rein":
            # Er hat uns schon gefragt - dann ist das hier ein Ja.
            self.anfrage_beantworten(ziel, True)
            return {"id": ziel, "meldung": "Ihr seid jetzt verbunden."}
        if vorhanden and vorhanden.get("karte") and karte and \
                karte_schluessel(vorhanden["karte"]) != karte_schluessel(karte):
            raise ValueError("Zu dieser ID kennt VP4 schon andere Schlüssel - Vorsicht.")
        self.db.kontakt_speichern(ziel, karte_json=karte or (vorhanden or {}).get("karte_json"),
                                  status="anfrage_raus")
        u = um.anfrage_bauen(self.ich, self.karte(), ziel, typ=um.ANFRAGE)
        self.ausgang(Auftrag(u.packen(), ziel))
        self.ereignis("kontakt_geaendert", id=ziel)
        name = karte["name"] if karte else ziel
        return {"id": ziel, "meldung": f"Anfrage an {name} geschickt – sobald sie angenommen ist, könnt ihr schreiben."}

    def anfragen(self) -> list:
        liste = []
        for status, richtung in (("anfrage_rein", "rein"), ("anfrage_raus", "raus")):
            for k in self.db.kontakte_alle(status):
                karte = k.get("karte") or {}
                liste.append({"id": k["id"], "name": karte.get("name") or k["id"],
                              "farbe": karte.get("avatar_farbe") or "", "richtung": richtung})
        return liste

    def anfrage_beantworten(self, kontakt_id: str, annehmen: bool):
        k = self.db.kontakt_holen(kontakt_id)
        if not k or k["status"] != "anfrage_rein" or not k.get("karte"):
            raise ValueError("Diese Anfrage gibt es nicht mehr.")
        if not annehmen:
            self.db.kontakt_loeschen(kontakt_id)
            u = um.anfrage_bauen(self.ich, self.karte(), kontakt_id, typ=um.ABLEHNUNG)
            self.ausgang(Auftrag(u.packen(), kontakt_id))
            self.ereignis("kontakt_geaendert", id=kontakt_id)
            return
        self._verbinden(k["karte"])
        u = um.anfrage_bauen(self.ich, self.karte(), kontakt_id, typ=um.ANNAHME)
        self.ausgang(Auftrag(u.packen(), kontakt_id))

    def _verbinden(self, karte: dict):
        """Paarschlüssel ausrechnen und die Unterhaltung anlegen."""
        kid = karte["id"]
        self.tresor.paar_schluessel_setzen(kid, e2e.paar_schluessel(self.ich, karte))
        self.db.kontakt_speichern(kid, karte_json=karte, status="ok")
        neu = self.db.unterhaltung_holen(kid) is None
        self.db.unterhaltung_anlegen_oder_holen(kid, "dm", karte.get("name", ""))
        if neu:
            self._system(kid, f"Ihr seid verbunden. Nachrichten mit {karte.get('name') or kid} sind Ende-zu-Ende verschlüsselt.")
        self.ereignis("kontakt_geaendert", id=kid)
        self.ereignis("unterhaltungen_geaendert")

    def kontakt_blockieren(self, kontakt_id: str):
        """Keine Anfragen, keine Nachrichten mehr - bis man ihn selbst wieder hinzufügt."""
        if kontakt_id == self.meine_id:
            raise ValueError("Dich selbst kannst du nicht blockieren.")
        self.db.kontakt_speichern(kontakt_id, status="blockiert")
        self.tresor.paar_schluessel_loeschen(kontakt_id)
        self.ereignis("kontakt_geaendert", id=kontakt_id)
        self.ereignis("unterhaltungen_geaendert")

    def kontakt_entfernen(self, kontakt_id: str):
        self.db.unterhaltung_loeschen(kontakt_id)
        self.db.kontakt_loeschen(kontakt_id)
        self.tresor.paar_schluessel_loeschen(kontakt_id)
        self.ereignis("unterhaltungen_geaendert")

    def kontakt_info(self, kontakt_id: str) -> dict:
        k = self.db.kontakt_holen(kontakt_id)
        if not k or not k.get("karte"):
            raise ValueError("Diesen Kontakt kennt VP4 (noch) nicht genau.")
        return {"kontakt": {"id": kontakt_id, "name": self.name_von(kontakt_id),
                            "farbe": k["karte"].get("avatar_farbe") or "",
                            "verifiziert": k["verifiziert"], "schluessel_geaendert": k["schluessel_neu"]},
                "sicherheitsnummer": e2e.sicherheitsnummer(self.karte(), k["karte"])}

    def verifizieren(self, kontakt_id: str, ja: bool):
        self.db.kontakt_verifiziert_setzen(kontakt_id, ja)
        self.ereignis("kontakt_geaendert", id=kontakt_id)

    # =================================================================
    #  Empfangen
    # =================================================================

    def empfangen(self, roh: bytes, weg: str = "discord"):
        """Ein Umschlag kam an. Wirft nie - kaputte Daten werden verworfen."""
        try:
            u = um.Umschlag.entpacken(roh)
        except ValueError as e:
            log.info("Umschlag verworfen: %s", e)
            return
        if u.von == self.meine_id:
            return          # das eigene Echo aus dem Discord-Verlauf
        try:
            with self._lock:
                if u.typ in um.KARTEN_TYPEN:
                    if u.an == self.meine_id:
                        self._karte_empfangen(u)
                elif ist_community_id(u.an):
                    self._community_empfangen(u, weg)
                elif u.an == self.meine_id:
                    if u.typ == um.DATEI_TEIL:
                        # Teile nur von Kontakten - sonst könnte jeder die
                        # Platte mit Datenmüll vollschreiben
                        k = self.db.kontakt_holen(u.von)
                        if k and k["status"] == "ok":
                            self._teil_empfangen(u, roh)
                    else:
                        self._dm_empfangen(u, weg)
        except Exception:
            # Ein einzelner kaputter Umschlag darf den Empfang nie lahmlegen
            # (Fehler 3 aus CLAUDE.md: der tote Empfangs-Thread).
            log.exception("Fehler beim Verarbeiten eines Umschlags von %s", u.von)

    # --------------------------------------------------- Karten-Umschläge
    def _karte_empfangen(self, u):
        try:
            karte = um.karten_umschlag_lesen(u)
        except ValueError as e:
            log.info("Karte von %s abgelehnt: %s", u.von, e)
            return
        vorhanden = self.db.kontakt_holen(u.von)
        if vorhanden and vorhanden["status"] == "blockiert":
            return
        if vorhanden and vorhanden.get("karte") and \
                karte_schluessel(vorhanden["karte"]) != karte_schluessel(karte):
            # Dieselbe ID mit anderen Schlüsseln: Das geht praktisch nur, wenn
            # jemand eine passende ID erzwungen hat. Nicht annehmen, warnen.
            self.db.kontakt_schluessel_neu_setzen(u.von, True)
            self._system(u.von, "Jemand hat sich mit anderen Schlüsseln als dieser Kontakt gemeldet. "
                                "VP4 hat das abgelehnt. Vergleicht die Sicherheitsnummer.")
            self.ereignis("kontakt_geaendert", id=u.von)
            return

        status = (vorhanden or {}).get("status")
        if u.typ == um.ANFRAGE:
            if status == "ok":
                # Er kennt uns nicht mehr (z. B. Nachricht verloren) - einfach nochmal Ja sagen
                self.db.kontakt_speichern(u.von, karte_json=karte)
                antwort = um.anfrage_bauen(self.ich, self.karte(), u.von, typ=um.ANNAHME)
                self.ausgang(Auftrag(antwort.packen(), u.von))
            elif status == "anfrage_raus":
                # Beide haben sich gleichzeitig hinzugefügt
                self._verbinden(karte)
                antwort = um.anfrage_bauen(self.ich, self.karte(), u.von, typ=um.ANNAHME)
                self.ausgang(Auftrag(antwort.packen(), u.von))
            else:
                self.db.kontakt_speichern(u.von, karte_json=karte, status="anfrage_rein")
                self.ereignis("kontakt_anfrage", id=u.von, name=karte["name"])
                self.ereignis("mitteilung", titel="Neue Anfrage",
                              text=f"{karte['name']} möchte mit dir schreiben.", unterhaltung=None)
        elif u.typ == um.ANNAHME:
            if status in ("anfrage_raus", "ok"):
                self._verbinden(karte)
        elif u.typ == um.ABLEHNUNG:
            # Nur eine Ablehnung, die NACH unserer Anfrage geschrieben wurde -
            # eine alte, erneut eingespielte darf keine neue Anfrage löschen.
            if status == "anfrage_raus" and u.ts_ms >= (vorhanden.get("hinzugefuegt") or 0) - 60_000:
                self.db.kontakt_loeschen(u.von)
                self.ereignis("hinweis", text=f"{karte['name']} hat deine Anfrage abgelehnt.")
                self.ereignis("kontakt_geaendert", id=u.von)
        elif u.typ == um.KARTE:
            alt_ts = ((vorhanden or {}).get("karte") or {}).get("ts", 0)
            if status in ("ok", "mitglied", "anfrage_rein", "anfrage_raus") and karte.get("ts", 0) > alt_ts:
                self.db.kontakt_speichern(u.von, karte_json=karte)
                if status == "ok":
                    self.db.unterhaltung_titel_setzen(u.von, karte["name"])
                self.ereignis("kontakt_geaendert", id=u.von)

    # ------------------------------------------------------ Direktnachrichten
    def _dm_empfangen(self, u, weg):
        k = self.db.kontakt_holen(u.von)
        if not k or k["status"] != "ok" or k["schluessel_neu"]:
            return
        schluessel = self.tresor.paar_schluessel(u.von)
        if not schluessel:
            return
        try:
            inner = um.dm_lesen(u, schluessel)
        except ValueError:
            self._melde_entschluesselungsfehler(u.von)
            return
        self._inner_verarbeiten(u, inner, unterhaltung=u.von, weg=weg, community=None)

    def _melde_entschluesselungsfehler(self, von):
        jetzt = time.time()
        if jetzt - self._entschluesselungsfehler.get(von, 0) > 300:
            self._entschluesselungsfehler[von] = jetzt
            self.ereignis("hinweis", art="fehler",
                          text=f"Eine Nachricht von {self.name_von(von)} ließ sich nicht entschlüsseln.")

    # ------------------------------------------- gemeinsam für DM und Kanal
    def _inner_verarbeiten(self, u, inner, *, unterhaltung, weg, community):
        art = inner.get("art")
        nid = u.msg_id.hex()
        ts = min(int(u.ts_ms), _jetzt_ms() + MAX_VORLAUF_MS)

        if art == "text":
            text = str(inner.get("text") or "")[:MAX_TEXT]
            antwort = inner.get("antwort_auf") if isinstance(inner.get("antwort_auf"), str) else None
            if antwort:
                bezug = self.db.nachricht_holen(antwort)
                if not bezug or bezug["unterhaltung_id"] != unterhaltung:
                    antwort = None
            if self.db.nachricht_einfuegen(nid, unterhaltung, u.von, ts, {"text": text}, "text",
                                           antwort, status="", weg=weg):
                self._neu_gemeldet(unterhaltung, nid, text)
                self._quittung_vormerken(unterhaltung, nid, "zugestellt", u.von, community)
        elif art == "datei":
            self._datei_meta_empfangen(u, inner, unterhaltung, weg, ts, community)
        elif art in ("bearbeiten", "loeschen", "reaktion"):
            self._steuerung(u, inner, unterhaltung)
        elif art == "quittung" and community is None:
            self._quittung_empfangen(u, inner, unterhaltung)
        elif art == "tippt":
            if self.einstellungen().get("tippanzeige", True):
                self.ereignis("tippt", unterhaltung=unterhaltung, name=self.name_von(u.von))
        elif art == "community_schluessel" and community is None:
            self._neuer_community_schluessel(u, inner)

    def _steuerung(self, u, inner, unterhaltung):
        ziel = inner.get("ziel")
        if not isinstance(ziel, str) or u.msg_id.hex() in self._gesehen:
            return
        self._gesehen[u.msg_id.hex()] = time.time()
        if len(self._gesehen) > 5000:
            for k in sorted(self._gesehen, key=self._gesehen.get)[:1000]:
                del self._gesehen[k]
        n = self.db.nachricht_holen(ziel)
        if not n or n["unterhaltung_id"] != unterhaltung:
            return
        art = inner["art"]
        if art == "reaktion":
            emoji = str(inner.get("emoji") or "")[:QUICK_EMOJI_LAENGE]
            if not emoji:
                return
            if inner.get("an", True):
                self.db.reaktion_setzen(ziel, u.von, emoji)
            else:
                self.db.reaktion_entfernen(ziel, u.von, emoji)
        elif n["absender_id"] != u.von:
            return          # fremde Nachrichten ändert niemand
        elif art == "bearbeiten":
            if u.ts_ms <= (n["bearbeitet"] or 0):
                return      # eine ältere Fassung, erneut eingespielt
            text = str(inner.get("text") or "")[:MAX_TEXT]
            inhalt = dict(n["inhalt"] or {}, text=text)
            self.db.nachricht_bearbeiten(ziel, inhalt, ts=u.ts_ms)
        elif art == "loeschen":
            for pfad in self.db.nachricht_als_geloescht_markieren(ziel):
                self._datei_weg(pfad)
        self._geaendert(unterhaltung, ziel)

    # ---------------------------------------------------- Lesebestätigungen
    def _quittung_vormerken(self, unterhaltung, nid, stufe, von, community):
        if community is not None:
            return          # in Gruppen gibt es keine Lesebestätigung
        if stufe == "gelesen" and not self.einstellungen().get("lesebestaetigungen", True):
            return
        ziel = self._sammler.zugestellt if stufe == "zugestellt" else self._sammler.gelesen
        ziel.setdefault(unterhaltung, set()).add(nid)

    def quittungen_senden(self):
        """Wird regelmässig aufgerufen (alle paar Sekunden) und schickt alles Gesammelte."""
        with self._lock:
            for stufe, sammlung in (("zugestellt", self._sammler.zugestellt),
                                    ("gelesen", self._sammler.gelesen)):
                for unterhaltung, ids in list(sammlung.items()):
                    if not ids:
                        continue
                    schluessel = self.tresor.paar_schluessel(unterhaltung)
                    if schluessel:
                        u = um.dm_bauen(self.ich, schluessel, self.meine_id, unterhaltung,
                                        {"art": "quittung", "stufe": stufe, "ids": sorted(ids)[:200]})
                        self.ausgang(Auftrag(u.packen(), unterhaltung))
                    sammlung[unterhaltung] = set()

    def _quittung_empfangen(self, u, inner, unterhaltung):
        stufe = inner.get("stufe")
        rang = {"senden": 0, "gesendet": 1, "zugestellt": 2, "gelesen": 3}
        if stufe not in ("zugestellt", "gelesen"):
            return
        for nid in inner.get("ids") or []:
            n = self.db.nachricht_holen(str(nid))
            if not n or n["absender_id"] != self.meine_id or n["unterhaltung_id"] != unterhaltung:
                continue
            if rang.get(n["status"], 1) < rang[stufe]:
                self.db.nachricht_status_setzen(n["id"], stufe)
                self._geaendert(unterhaltung, n["id"])

    def gelesen(self, unterhaltung: str):
        u = self.db.unterhaltung_holen(unterhaltung)
        if not u:
            return
        vorher = u["zuletzt_gelesen"]
        self.db.gelesen_bis_setzen(unterhaltung, _jetzt_ms())
        if u["art"] == "dm":
            for n in self.db.nachrichten_seite(unterhaltung, anzahl=100):
                if n["ts"] <= vorher:
                    break
                if n["absender_id"] not in (self.meine_id, SYSTEM) and not n["geloescht"]:
                    self._quittung_vormerken(unterhaltung, n["id"], "gelesen", n["absender_id"], None)
        self.ereignis("gelesen", unterhaltung=unterhaltung)

    # =================================================================
    #  Senden
    # =================================================================

    def _umschlag_bauen(self, unterhaltung: str, inner: dict):
        """Gibt (umschlag, ziel, community) zurück - oder ValueError mit Grund."""
        u = self.db.unterhaltung_holen(unterhaltung)
        if not u:
            raise ValueError("Diese Unterhaltung gibt es nicht.")
        if u["art"] == "dm":
            k = self.db.kontakt_holen(unterhaltung)
            if not k or k["status"] != "ok":
                raise ValueError("Ihr seid noch nicht verbunden – die Anfrage muss erst angenommen werden.")
            if k["schluessel_neu"]:
                raise ValueError("Der Schlüssel dieses Kontakts ist fragwürdig. Prüft erst die Sicherheitsnummer.")
            schluessel = self.tresor.paar_schluessel(unterhaltung)
            if not schluessel:
                raise ValueError("Für diesen Kontakt fehlt der Schlüssel.")
            return um.dm_bauen(self.ich, schluessel, self.meine_id, unterhaltung, inner), unterhaltung, False
        cid = u["community_id"]
        kanal = self.db.kanal_holen(unterhaltung)
        ckey = self.tresor.community_schluessel(cid)
        if not kanal or not ckey:
            raise ValueError("Für diese Gruppe fehlt der Schlüssel.")
        if kanal["nur_admins"] and inner.get("art") in ("text", "datei") and not self._ist_admin(cid, self.meine_id):
            raise ValueError("In diesem Kanal schreiben nur Admins.")
        inner = dict(inner, kanal=kanal["id"], karte=self.karte())
        key = e2e.kanal_schluessel(ckey, kanal["id"])
        return um.kanal_bauen(self.ich, key, self.meine_id, cid, inner), cid, True

    def text_senden(self, unterhaltung: str, text: str, antwort_auf: str = None) -> str:
        text = (text or "").strip()
        if not text:
            raise ValueError("Leere Nachricht.")
        if len(text) > MAX_TEXT:
            raise ValueError(f"Höchstens {MAX_TEXT} Zeichen pro Nachricht.")
        if antwort_auf:
            bezug = self.db.nachricht_holen(antwort_auf)
            if not bezug or bezug["unterhaltung_id"] != unterhaltung:
                antwort_auf = None
        inner = {"art": "text", "text": text}
        if antwort_auf:
            inner["antwort_auf"] = antwort_auf
        umschlag, ziel, community = self._umschlag_bauen(unterhaltung, inner)
        nid = umschlag.msg_id.hex()
        self.db.nachricht_einfuegen(nid, unterhaltung, self.meine_id, umschlag.ts_ms, {"text": text},
                                    "text", antwort_auf, status="senden")
        self._neu_gemeldet(unterhaltung, nid, text)
        self.ausgang(Auftrag(umschlag.packen(), ziel, community=community, nachricht_id=nid,
                             unterhaltung=unterhaltung))
        return nid

    def gesendet(self, nachricht_id: str, weg: str):
        """Vom Versand gemeldet: ist raus (über WLAN oder Discord)."""
        n = self.db.nachricht_holen(nachricht_id)
        if not n:
            return
        if n["status"] in ("senden", "fehler", ""):
            self.db.nachricht_status_setzen(nachricht_id, "gesendet")
        self.db.nachricht_weg_setzen(nachricht_id, weg)
        self._geaendert(n["unterhaltung_id"], nachricht_id)

    def fehlgeschlagen(self, nachricht_id: str, grund: str):
        n = self.db.nachricht_holen(nachricht_id)
        if not n:
            return
        if n["status"] not in ("senden", ""):
            return      # schon angekommen - ein später Zeitfehler ändert daran nichts
        self.db.nachricht_status_setzen(nachricht_id, "fehler")
        self._geaendert(n["unterhaltung_id"], nachricht_id)

    def erneut_senden(self, unterhaltung: str, nachricht_id: str):
        n = self.db.nachricht_holen(nachricht_id)
        if not n or n["absender_id"] != self.meine_id or n["status"] != "fehler":
            raise ValueError("Diese Nachricht lässt sich nicht erneut senden.")
        if n["art"] != "text":
            raise ValueError("Dateien bitte neu verschicken.")
        # Neue Nachricht, alte weg - sonst hätte sie zwei IDs auf zwei Seiten
        for pfad in self.db.nachricht_als_geloescht_markieren(nachricht_id):
            self._datei_weg(pfad)
        self.db.nachricht_entfernen(nachricht_id)
        self.ereignis("unterhaltungen_geaendert")
        return self.text_senden(unterhaltung, (n["inhalt"] or {}).get("text", ""), n["antwort_auf"])

    def _steuer_senden(self, unterhaltung, inner, fluechtig=False):
        umschlag, ziel, community = self._umschlag_bauen(unterhaltung, inner)
        self.ausgang(Auftrag(umschlag.packen(), ziel, community=community, fluechtig=fluechtig,
                             unterhaltung=unterhaltung))
        return umschlag

    def bearbeiten(self, unterhaltung, nachricht_id, text):
        n = self.db.nachricht_holen(nachricht_id)
        if not n or n["absender_id"] != self.meine_id or n["geloescht"] or n["art"] != "text":
            raise ValueError("Diese Nachricht kannst du nicht bearbeiten.")
        text = (text or "").strip()
        if not text:
            raise ValueError("Leere Nachricht – zum Entfernen bitte löschen.")
        self._steuer_senden(unterhaltung, {"art": "bearbeiten", "ziel": nachricht_id, "text": text})
        self.db.nachricht_bearbeiten(nachricht_id, dict(n["inhalt"] or {}, text=text))
        self._geaendert(unterhaltung, nachricht_id)

    def loeschen(self, unterhaltung, nachricht_id, fuer_alle: bool):
        n = self.db.nachricht_holen(nachricht_id)
        if not n or n["unterhaltung_id"] != unterhaltung:
            raise ValueError("Diese Nachricht gibt es nicht.")
        if fuer_alle:
            if n["absender_id"] != self.meine_id:
                raise ValueError("Nur eigene Nachrichten lassen sich für alle löschen.")
            self._steuer_senden(unterhaltung, {"art": "loeschen", "ziel": nachricht_id})
            # Auch den Geheimtext aus Discord nehmen (geht, weil alle denselben Bot benutzen)
            self.loeschen_lassen(nachricht_id)
        for pfad in self.db.nachricht_als_geloescht_markieren(nachricht_id):
            self._datei_weg(pfad)
        self._geaendert(unterhaltung, nachricht_id)

    def reagieren(self, unterhaltung, nachricht_id, emoji):
        n = self.db.nachricht_holen(nachricht_id)
        emoji = (emoji or "")[:QUICK_EMOJI_LAENGE]
        if not n or n["unterhaltung_id"] != unterhaltung or n["geloescht"] or not emoji:
            raise ValueError("Darauf lässt sich nicht reagieren.")
        hat = any(r["absender_id"] == self.meine_id and r["emoji"] == emoji
                  for r in self.db.reaktionen_fuer_nachrichten([nachricht_id])[nachricht_id])
        self._steuer_senden(unterhaltung, {"art": "reaktion", "ziel": nachricht_id, "emoji": emoji, "an": not hat})
        if hat:
            self.db.reaktion_entfernen(nachricht_id, self.meine_id, emoji)
        else:
            self.db.reaktion_setzen(nachricht_id, self.meine_id, emoji)
        self._geaendert(unterhaltung, nachricht_id)

    def tippt(self, unterhaltung):
        u = self.db.unterhaltung_holen(unterhaltung)
        if not u or u["art"] != "dm" or not self.einstellungen().get("tippanzeige", True):
            return
        try:
            self._steuer_senden(unterhaltung, {"art": "tippt"}, fluechtig=True)
        except ValueError:
            pass

    # =================================================================
    #  Dateien
    # =================================================================

    @staticmethod
    def _typ_fuer(mime: str, art: str = "auto") -> str:
        if art == "sprache":
            return "sprache"
        if mime.startswith("image/") and mime != "image/svg+xml":
            return "bild"
        if mime.startswith("video/"):
            return "video"
        return "datei"

    def _vorschau(self, pfad: Path, typ: str):
        """Kleines JPEG fürs Erste-Anzeigen, dazu Breite und Höhe."""
        if typ != "bild":
            return None, None, None
        try:
            from PIL import Image, ImageOps
            with Image.open(pfad) as bild:
                bild = ImageOps.exif_transpose(bild)
                breite, hoehe = bild.size
                bild.thumbnail((VORSCHAU_KANTE, VORSCHAU_KANTE))
                if bild.mode not in ("RGB", "L"):
                    bild = bild.convert("RGB")
                puffer = io.BytesIO()
                bild.save(puffer, "JPEG", quality=78, optimize=True)
                return puffer.getvalue(), breite, hoehe
        except Exception:
            return None, None, None

    def datei_senden(self, unterhaltung: str, quelle, name: str = None, art: str = "auto",
                     dauer_ms: int = None, wellenform=None, text: str = "") -> str:
        quelle = Path(quelle)
        if not quelle.is_file():
            raise ValueError("Die Datei gibt es nicht.")
        groesse = quelle.stat().st_size
        if groesse > MAX_DATEI:
            raise ValueError(f"Über den Chat gehen höchstens {MAX_DATEI // 1024 // 1024} MB. "
                             "Größeres verschlüssle unter Werkzeuge → Dateien und schick es anders.")
        name = (name or quelle.name)[:200]
        mime = mimetypes.guess_type(name)[0] or "application/octet-stream"
        if art == "sprache":
            mime = "audio/webm"
        typ = self._typ_fuer(mime, art)

        # Eigene Kopie im Medienordner, mit zufälligem Namen (der echte steht
        # nur verschlüsselt in der Datenbank)
        ziel = self.medien / f"{secrets.token_hex(12)}{Path(name).suffix.lower()[:10]}"
        shutil.copyfile(quelle, ziel)
        sha = hashlib.sha256()
        with open(ziel, "rb") as f:
            for block in iter(lambda: f.read(1024 * 1024), b""):
                sha.update(block)
        vorschau, breite, hoehe = self._vorschau(ziel, typ)
        datei_id = secrets.token_bytes(12)
        schluessel = secrets.token_bytes(32)
        teile = max(1, -(-groesse // TEIL_GROESSE))
        meta = {"datei_id": datei_id.hex(), "name": name, "groesse": groesse, "mime": mime,
                "typ": typ, "teile": teile, "schluessel": _b64(schluessel), "sha256": sha.hexdigest()}
        if vorschau and len(vorschau) < 60_000:
            meta["vorschau"] = _b64(vorschau)
        if breite:
            meta["breite"], meta["hoehe"] = breite, hoehe
        if dauer_ms:
            meta["dauer_ms"] = int(dauer_ms)
        if wellenform:
            meta["wellenform"] = [round(float(x), 2) for x in list(wellenform)[:64]]
        inner = {"art": "datei", "datei": meta}
        if text:
            inner["text"] = text[:MAX_TEXT]
        umschlag, an, community = self._umschlag_bauen(unterhaltung, inner)
        nid = umschlag.msg_id.hex()
        self.db.nachricht_einfuegen(nid, unterhaltung, self.meine_id, umschlag.ts_ms,
                                    {"text": text, "datei": meta}, typ, status="senden")
        self.db.anhang_anlegen(datei_id.hex(), nid, name, groesse, mime, str(ziel), "fertig", vorschau)
        self._neu_gemeldet(unterhaltung, nid, self._vorschau_text(typ, name))
        self.ausgang(Auftrag(umschlag.packen(), an, community=community, nachricht_id=nid,
                             unterhaltung=unterhaltung))
        for i in range(teile):
            def bauen(i=i):
                with open(ziel, "rb") as f:
                    f.seek(i * TEIL_GROESSE)
                    stueck = f.read(TEIL_GROESSE)
                return um.verschluesselt_bauen(schluessel, self.meine_id, an, stueck, typ=um.DATEI_TEIL,
                                               msg_id=datei_id + i.to_bytes(4, "big")).packen()
            self.ausgang(Auftrag(bauen, an, community=community, nachricht_id=nid,
                                 unterhaltung=unterhaltung, teil=i, teile=teile))
        return nid

    @staticmethod
    def _vorschau_text(typ, name):
        return {"bild": "Foto", "video": "Video", "sprache": "Sprachnachricht"}.get(typ, name)

    def _datei_meta_empfangen(self, u, inner, unterhaltung, weg, ts, community):
        meta = inner.get("datei")
        if not isinstance(meta, dict):
            return
        try:
            datei_id = bytes.fromhex(meta["datei_id"])
            schluessel = _unb64(meta["schluessel"])
            groesse = int(meta["groesse"])
            teile = int(meta["teile"])
            if len(datei_id) != 12 or len(schluessel) != 32 or not 0 <= groesse <= MAX_DATEI \
                    or not 1 <= teile <= MAX_DATEI // TEIL_GROESSE + 1:
                return
        except (KeyError, ValueError, TypeError):
            return
        name = _dateiname_saeubern(str(meta.get("name") or "Datei"))
        mime = str(meta.get("mime") or "application/octet-stream")[:100]
        typ = self._typ_fuer(mime, "sprache" if meta.get("typ") == "sprache" else "auto")
        vorschau = None
        if meta.get("vorschau"):
            try:
                vorschau = _unb64(meta["vorschau"])
            except ValueError:
                vorschau = None
        sauber = {k: meta[k] for k in ("datei_id", "groesse", "teile", "schluessel", "sha256",
                                       "breite", "hoehe", "dauer_ms", "wellenform") if k in meta}
        sauber.update(name=name, mime=mime, typ=typ)
        nid = u.msg_id.hex()
        text = str(inner.get("text") or "")[:MAX_TEXT]
        if not self.db.nachricht_einfuegen(nid, unterhaltung, u.von, ts, {"text": text, "datei": sauber},
                                           typ, status="", weg=weg):
            return
        self.db.anhang_anlegen(datei_id.hex(), nid, name, groesse, mime, "", "laden", vorschau)
        self._neu_gemeldet(unterhaltung, nid, text or self._vorschau_text(typ, name))
        self._quittung_vormerken(unterhaltung, nid, "zugestellt", u.von, community)
        self._zusammensetzen(datei_id.hex())

    def _teil_empfangen(self, u, roh, nur_bekannte=False):
        datei_id = u.msg_id[:12].hex()
        nummer = int.from_bytes(u.msg_id[12:], "big")
        if nummer > MAX_DATEI // TEIL_GROESSE + 1:
            return
        anhang = self.db.anhang_holen(datei_id)
        if anhang:
            n = self.db.nachricht_holen(anhang["nachricht_id"])
            meta = ((n or {}).get("inhalt") or {}).get("datei") or {}
            if not n or n["absender_id"] != u.von or nummer >= int(meta.get("teile", 0)):
                return      # fremder Absender oder Nummer, die es nicht gibt
        elif nur_bekannte:
            # In einer Community kennt jeder die ID - Teile ohne vorherige
            # Datei-Nachricht werden deshalb gar nicht erst gespeichert.
            return
        ordner = self.medien / "teile" / datei_id
        ordner.mkdir(parents=True, exist_ok=True)
        ziel = ordner / f"{nummer}.bin"
        if not ziel.exists():
            tmp = ziel.with_suffix(".tmp")
            tmp.write_bytes(roh)
            tmp.replace(ziel)
        self._zusammensetzen(datei_id)

    def _zusammensetzen(self, datei_id: str):
        anhang = self.db.anhang_holen(datei_id)
        if not anhang or anhang["zustand"] != "laden":
            return
        n = self.db.nachricht_holen(anhang["nachricht_id"])
        meta = ((n or {}).get("inhalt") or {}).get("datei") or {}
        ordner = self.medien / "teile" / datei_id
        teile = int(meta.get("teile", 0))
        vorhanden = [i for i in range(teile) if (ordner / f"{i}.bin").exists()]
        if len(vorhanden) < teile:
            if vorhanden:
                self._geaendert(n["unterhaltung_id"], n["id"], fortschritt=len(vorhanden) / teile)
            return
        schluessel = _unb64(meta["schluessel"])
        ziel = self.medien / f"{secrets.token_hex(12)}{Path(anhang['name'] or '').suffix.lower()[:10]}"
        sha = hashlib.sha256()
        try:
            with open(ziel, "wb") as aus:
                for i in range(teile):
                    teil = um.Umschlag.entpacken((ordner / f"{i}.bin").read_bytes())
                    # Nur Teile vom selben Absender an dasselbe Ziel - sonst
                    # könnte jemand anderes Stücke unterschieben (es ginge zwar
                    # ohne Schlüssel nicht auf, aber sauber ist sauber).
                    if teil.von != n["absender_id"]:
                        raise ValueError("Teil von falschem Absender.")
                    stueck = um.verschluesselt_lesen(teil, schluessel)
                    sha.update(stueck)
                    aus.write(stueck)
            if meta.get("sha256") and sha.hexdigest() != meta["sha256"]:
                raise ValueError("Prüfsumme stimmt nicht.")
        except (ValueError, OSError, FileNotFoundError) as e:
            ziel.unlink(missing_ok=True)
            self.db.anhang_zustand_setzen(datei_id, "kaputt")
            self.ereignis("hinweis", art="fehler", text=f"Eine Datei kam beschädigt an ({e}).")
            shutil.rmtree(ordner, ignore_errors=True)
            self._geaendert(n["unterhaltung_id"], n["id"])
            return
        shutil.rmtree(ordner, ignore_errors=True)
        self.db.anhang_zustand_setzen(datei_id, "fertig", str(ziel))
        self._geaendert(n["unterhaltung_id"], n["id"])

    def _datei_weg(self, pfad):
        try:
            p = Path(pfad)
            if p.is_file() and self.medien in p.resolve().parents:
                p.unlink()
        except OSError:
            pass

    def teile_aufraeumen(self, max_alter_s: int = 24 * 3600):
        """Teile, zu denen nie eine Nachricht kam, nach einem Tag wegwerfen."""
        grenze = time.time() - max_alter_s
        for ordner in (self.medien / "teile").glob("*"):
            try:
                if ordner.stat().st_mtime < grenze:
                    shutil.rmtree(ordner, ignore_errors=True)
            except OSError:
                pass

    # =================================================================
    #  Communities und Gruppen
    # =================================================================

    def _manifest(self, cid):
        c = self.db.community_holen(cid)
        if not c or not c["manifest_json"]:
            return None
        try:
            return json.loads(c["manifest_json"])
        except ValueError:
            return None

    def _ist_admin(self, cid, nutzer_id) -> bool:
        c = self.db.community_holen(cid)
        if not c:
            return False
        if c["besitzer_id"] == nutzer_id:
            return True
        m = self._manifest(cid) or {}
        return nutzer_id in (m.get("admins") or [])

    def community_erstellen(self, name: str, icon: str = "", gruppe: bool = False) -> str:
        cid = community_id_neu()
        schluessel = community_schluessel_neu()
        kanal = {"id": standard_kanal_id(cid), "name": "allgemein", "position": 0, "nur_admins": False}
        manifest = manifest_bauen(self.ich, cid, name, icon or "", [kanal], [], 1,
                                  art="gruppe" if gruppe else "community")
        self.tresor.community_schluessel_setzen(cid, schluessel)
        self.db.community_speichern(cid, name=manifest["name"], schluessel=schluessel,
                                    besitzer_id=self.meine_id, manifest_json=manifest, icon=icon or "")
        self._basis_setzen(cid, manifest)
        self._kanaele_anwenden(cid, manifest)
        self._system(kanal["id"], "Du hast diese Gruppe erstellt." if gruppe else "Du hast diese Community gegründet.")
        self.ereignis("community_geaendert", id=cid)
        self.ereignis("unterhaltungen_geaendert")
        return cid

    def _kanaele_anwenden(self, cid, manifest):
        gruppe = manifest.get("art") == "gruppe"
        neu = {k["id"]: k for k in manifest["kanaele"]}
        for alt in self.db.kanaele(cid):
            if alt["id"] not in neu:
                self.db.kanal_loeschen(alt["id"])
        for k in manifest["kanaele"]:
            vorhanden = self.db.kanal_holen(k["id"])
            u_vorhanden = self.db.unterhaltung_holen(k["id"])
            if (vorhanden and vorhanden["community_id"] != cid) or \
                    (u_vorhanden and u_vorhanden["community_id"] != cid):
                # Die ID gehört schon woanders hin (einer anderen Community oder
                # einem Chat) - ein fremdes Manifest darf sie nicht an sich reissen.
                continue
            self.db.kanal_speichern(k["id"], cid, k["name"], k.get("position", 0), bool(k.get("nur_admins")))
            self.db.unterhaltung_anlegen_oder_holen(k["id"], "gruppe" if gruppe else "kanal",
                                                    manifest["name"] if gruppe else k["name"], cid)
            self.db.unterhaltung_art_setzen(k["id"], "gruppe" if gruppe else "kanal")
            self.db.unterhaltung_titel_setzen(k["id"], manifest["name"] if gruppe else k["name"])

    def einladung(self, cid) -> str:
        c = self.db.community_holen(cid)
        if not c:
            raise ValueError("Diese Community gibt es nicht.")
        return einladung_bauen(cid, self.tresor.community_schluessel(cid), c["besitzer_id"])

    def community_beitreten(self, code: str) -> dict:
        cid, schluessel, besitzer = einladung_lesen(code)
        if self.db.community_holen(cid):
            raise ValueError("Da bist du schon drin.")
        self.tresor.community_schluessel_setzen(cid, schluessel)
        self.db.community_speichern(cid, name="Neue Community", schluessel=schluessel,
                                    besitzer_id=besitzer, icon="")
        kanal = standard_kanal_id(cid)
        self.db.kanal_speichern(kanal, cid, "allgemein", 0, False)
        self.db.unterhaltung_anlegen_oder_holen(kanal, "kanal", "allgemein", cid)
        self._system(kanal, "Du bist beigetreten. Die Kanäle erscheinen, sobald jemand aus der Gruppe online ist.")
        self._steuer_senden(kanal, {"art": "beitritt"})
        self.ereignis("community_geaendert", id=cid)
        self.ereignis("unterhaltungen_geaendert")
        return {"id": cid, "name": "Neue Community", "gruppe": False}

    def community_verlassen(self, cid):
        for k in self.db.kanaele(cid):
            self.db.unterhaltung_loeschen(k["id"])
        self.db.community_loeschen(cid)
        self.tresor.community_schluessel_loeschen(cid)
        self.ereignis("community_geaendert", id=cid)
        self.ereignis("unterhaltungen_geaendert")

    def _manifest_aendern(self, cid, aendern):
        # Unter dem Empfangs-Schloss: Kommt gleichzeitig ein Manifest herein,
        # bauten sonst beide auf derselben alten Version auf.
        with self._lock:
            self._manifest_aendern_gesperrt(cid, aendern)

    def _manifest_aendern_gesperrt(self, cid, aendern):
        if not self._ist_admin(cid, self.meine_id):
            raise ValueError("Das dürfen nur Besitzer und Admins.")
        alt = self._manifest(cid)
        if not alt:
            raise ValueError("Die Community ist noch nicht vollständig geladen.")
        kanaele, admins, name, icon = aendern([dict(k) for k in alt["kanaele"]], list(alt["admins"]),
                                              alt["name"], alt["icon"])
        neu = manifest_bauen(self.ich, cid, name, icon, kanaele, admins, alt["version"] + 1,
                             art=alt.get("art", "community"))
        self._manifest_uebernehmen(cid, neu)
        self._manifest_verschicken(cid, neu)

    def kanal_anlegen(self, cid, name, nur_admins=False) -> str:
        name = kanalname_saeubern(name)
        kid = kanal_id_neu()
        def aendern(kanaele, admins, cname, icon):
            self._keine_gruppe(cid)
            self._name_frei(kanaele, name)
            kanaele.append({"id": kid, "name": name, "position": len(kanaele), "nur_admins": bool(nur_admins)})
            return kanaele, admins, cname, icon
        self._manifest_aendern(cid, aendern)
        return kid

    # ------------------------------------------------ Verwaltung (Besitzer/Admins)
    def _keine_gruppe(self, cid):
        if (self._manifest(cid) or {}).get("art") == "gruppe":
            raise ValueError("Eine Gruppe hat genau einen Kanal.")

    @staticmethod
    def _name_frei(kanaele, name, ausser=None):
        if any(k["name"].lower() == name and k["id"] != ausser for k in kanaele):
            raise ValueError("Einen Kanal mit diesem Namen gibt es schon.")

    @staticmethod
    def _kanal_finden(kanaele, kanal_id) -> dict:
        for k in kanaele:
            if k["id"] == kanal_id:
                return k
        raise ValueError("Diesen Kanal gibt es nicht (mehr).")

    @staticmethod
    def _neu_nummerieren(kanaele) -> list:
        kanaele = sorted(kanaele, key=lambda k: (k["position"], k["name"], k["id"]))
        for i, k in enumerate(kanaele):
            k["position"] = i
        return kanaele

    def kanal_umbenennen(self, cid, kanal_id, name):
        """Besitzer oder Admin. Name nach den Kanalregeln, keine Doppelten."""
        name = kanalname_saeubern(name)
        def aendern(kanaele, admins, cname, icon):
            self._name_frei(kanaele, name, ausser=kanal_id)
            self._kanal_finden(kanaele, kanal_id)["name"] = name
            return kanaele, admins, cname, icon
        self._manifest_aendern(cid, aendern)

    def kanal_loeschen(self, cid, kanal_id):
        """Besitzer oder Admin. Der Standardkanal bleibt immer: Über ihn meldet
        sich ein Neuer, und er kennt anfangs keinen anderen."""
        if kanal_id == standard_kanal_id(cid):
            raise ValueError("Der erste Kanal einer Community lässt sich nicht löschen.")
        def aendern(kanaele, admins, cname, icon):
            self._kanal_finden(kanaele, kanal_id)
            return self._neu_nummerieren([k for k in kanaele if k["id"] != kanal_id]), admins, cname, icon
        self._manifest_aendern(cid, aendern)

    def kanal_verschieben(self, cid, kanal_id, position):
        """Besitzer oder Admin. `position` zählt ab 0 und wird auf die Liste begrenzt."""
        if not isinstance(position, int) or isinstance(position, bool):
            raise ValueError("Die Position muss eine Zahl sein.")
        def aendern(kanaele, admins, cname, icon):
            kanaele = self._neu_nummerieren(kanaele)
            k = self._kanal_finden(kanaele, kanal_id)
            kanaele.remove(k)
            kanaele.insert(max(0, min(position, len(kanaele))), k)
            for i, x in enumerate(kanaele):
                x["position"] = i
            return kanaele, admins, cname, icon
        self._manifest_aendern(cid, aendern)

    def kanal_nur_admins_setzen(self, cid, kanal_id, ja: bool):
        """Ankündigungskanal an/aus. Nie für den Standardkanal - dort meldet
        sich ein Neuer, und dort muss jeder "Hallo" sagen können."""
        if ja and kanal_id == standard_kanal_id(cid):
            raise ValueError("Im ersten Kanal müssen alle schreiben dürfen.")
        def aendern(kanaele, admins, cname, icon):
            self._kanal_finden(kanaele, kanal_id)["nur_admins"] = bool(ja)
            return kanaele, admins, cname, icon
        self._manifest_aendern(cid, aendern)

    def community_umbenennen(self, cid, name, icon=None):
        """Besitzer oder Admin. Ohne `icon` (oder leer) bleibt das alte Symbol."""
        if not isinstance(name, str) or not name.strip():
            raise ValueError("Der Name darf nicht leer sein.")
        def aendern(kanaele, admins, cname, alt_icon):
            return kanaele, admins, name.strip(), (icon or alt_icon)
        self._manifest_aendern(cid, aendern)

    def admin_setzen(self, cid, nutzer_id, ja: bool):
        """Nur der Besitzer ernennt und entlässt Admins. Ernannt werden kann
        nur, wen man in der Community gesehen hat oder als Kontakt kennt."""
        c = self.db.community_holen(cid)
        if not c or c["besitzer_id"] != self.meine_id:
            raise ValueError("Nur der Besitzer kann Admins ernennen.")
        nutzer_id = id_normalisieren(nutzer_id)
        if nutzer_id == self.meine_id:
            raise ValueError("Der Besitzer ist immer Admin.")
        if ja:
            kontakt = self.db.kontakt_holen(nutzer_id)
            gesehen = any(m["id"] == nutzer_id for m in self.mitglieder(cid))
            if not gesehen and not (kontakt and kontakt["status"] == "ok"):
                raise ValueError("Diese Person ist weder Mitglied noch Kontakt.")
        def aendern(kanaele, admins, cname, icon):
            admins = [a for a in admins if a != nutzer_id]
            if ja:
                admins.append(nutzer_id)
            return kanaele, admins, cname, icon
        self._manifest_aendern(cid, aendern)

    def _rolle(self, cid) -> str:
        c = self.db.community_holen(cid)
        if c and c["besitzer_id"] == self.meine_id:
            return "besitzer"
        if self.meine_id in ((self._manifest(cid) or {}).get("admins") or []):
            return "admin"
        return "mitglied"

    def _manifest_uebernehmen(self, cid, manifest):
        c = self.db.community_holen(cid)
        if c and manifest["von"] == c["besitzer_id"]:
            self._basis_setzen(cid, manifest)
        self.db.community_speichern(cid, name=manifest["name"], icon=manifest["icon"], manifest_json=manifest)
        self._kanaele_anwenden(cid, manifest)
        self.ereignis("community_geaendert", id=cid)
        self.ereignis("unterhaltungen_geaendert")
        # Was vorher an keinem Kanal passte, jetzt nochmal versuchen
        wartend, self._wartend = self._wartend, []
        grenze = time.time() - WARTEND_ABLAUF_S
        for roh, weg, zeit in wartend:
            if zeit >= grenze:
                self.empfangen(roh, weg)

    def _warten_lassen(self, roh, weg):
        """Merkt sich eine Kanalnachricht, deren Kanal (noch) unbekannt ist -
        klein, begrenzt und mit Ablaufzeit."""
        grenze = time.time() - WARTEND_ABLAUF_S
        self._wartend = [w for w in self._wartend if w[2] >= grenze]
        while self._wartend and sum(len(w[0]) for w in self._wartend) + len(roh) > MAX_WARTEND_BYTES:
            self._wartend.pop(0)
        if len(roh) <= MAX_COMMUNITY_NACHRICHT:
            self._wartend.append((roh, weg, time.time()))

    def _karte_von(self, nutzer_id):
        if nutzer_id == self.meine_id:
            return self.karte()
        return (self.db.kontakt_holen(nutzer_id) or {}).get("karte")

    def _basis(self, cid):
        """Das letzte vom BESITZER unterschriebene Manifest. Ein Neuer hat noch
        keine Admin-Liste - ohne diesen Anker könnte er ein von einem Admin
        geändertes Manifest gar nicht prüfen."""
        return (self.tresor.extra_holen("basis_manifeste", {}) or {}).get(cid)

    def _basis_setzen(self, cid, manifest):
        alle = dict(self.tresor.extra_holen("basis_manifeste", {}) or {})
        alle[cid] = manifest
        self.tresor.extra_setzen("basis_manifeste", alle)

    def _manifest_verschicken(self, cid, manifest):
        karte_von = self._karte_von(manifest["von"])
        if not karte_von:
            return
        inner = {"art": "manifest", "manifest": manifest, "karte_von": karte_von}
        c = self.db.community_holen(cid)
        basis = self._basis(cid)
        if manifest["von"] != c["besitzer_id"] and basis and self._karte_von(c["besitzer_id"]):
            inner.update(basis=basis, karte_besitzer=self._karte_von(c["besitzer_id"]))
        self._steuer_senden(standard_kanal_id(cid), inner)

    def _community_empfangen(self, u, weg):
        c = self.db.community_holen(u.an)
        if not c:
            return
        if u.typ == um.DATEI_TEIL:
            self._teil_empfangen(u, u.packen(), nur_bekannte=True)
            return
        if len(u.body) > MAX_COMMUNITY_NACHRICHT:
            return
        ckey = self.tresor.community_schluessel(u.an)
        kanaele = {k["id"]: e2e.kanal_schluessel(ckey, k["id"]) for k in self.db.kanaele(u.an)}
        bekannt = self.db.kontakt_holen(u.von)
        if bekannt and bekannt["status"] == "blockiert":
            return
        ed = karte_schluessel(bekannt["karte"])[0] if bekannt and bekannt.get("karte") else None
        try:
            inner, karte = um.kanal_lesen(u, kanaele, ed)
        except ValueError as e:
            if "keinem Kanal" in str(e):
                self._warten_lassen(u.packen(), weg)
            return
        if karte and not bekannt:
            self.db.kontakt_speichern(u.von, karte_json=karte, status="mitglied")
        kanal = self.db.kanal_holen(inner.get("kanal"))
        if not kanal:
            return
        art = inner.get("art")
        if art == "manifest":
            # Kam das als Antwort auf einen Beitritt, muss nicht jeder nochmal antworten
            if time.time() - self._beitritt_gesehen.get(u.an, 0) < 10:
                self._manifest_antwort[u.an] = time.time()
            self._manifest_empfangen(u.an, inner)
            return
        if art == "beitritt":
            self._system(kanal["id"], f"{self.name_von(u.von)} ist beigetreten.", ts=u.ts_ms,
                         kennung=u.msg_id.hex(), absender=u.von)
            self._beitritt_gesehen[u.an] = time.time()
            self._auf_beitritt_antworten(u.an)
            return
        if kanal["nur_admins"] and art in ("text", "datei") and not self._ist_admin(u.an, u.von):
            return
        self._inner_verarbeiten(u, inner, unterhaltung=kanal["id"], weg=weg, community=u.an)

    def _manifest_empfangen(self, cid, inner):
        c = self.db.community_holen(cid)
        alt = self._manifest(cid)
        karte_von = inner.get("karte_von")
        m = inner.get("manifest")
        if not isinstance(m, dict) or not isinstance(karte_von, dict):
            return
        try:
            karte_von = karte_pruefen(karte_von)
            stand_version = (alt or {}).get("version", 0)
            stand_admins = (alt or {}).get("admins", [])
            basis = inner.get("basis")
            if isinstance(basis, dict) and isinstance(inner.get("karte_besitzer"), dict) \
                    and m.get("von") != c["besitzer_id"] and basis.get("version", 0) > stand_version:
                # Erst den Anker (vom Besitzer) prüfen, dann das Admin-Manifest darauf
                karte_besitzer = karte_pruefen(inner["karte_besitzer"])
                basis = manifest_pruefen(basis, {karte_besitzer["id"]: karte_besitzer}, c["besitzer_id"],
                                         stand_version, stand_admins, community_id=cid)
                self._standardkanal_pruefen(cid, basis)
                stand_version, stand_admins = basis["version"], basis["admins"]
                self._manifest_uebernehmen(cid, basis)
            sauber = manifest_pruefen(m, {karte_von["id"]: karte_von}, c["besitzer_id"],
                                      stand_version, stand_admins, community_id=cid)
            self._standardkanal_pruefen(cid, sauber)
        except ValueError as e:
            log.info("Manifest für %s abgelehnt: %s", cid, e)
            return
        self._manifest_uebernehmen(cid, sauber)

    @staticmethod
    def _standardkanal_pruefen(cid, manifest):
        """Ein (womöglich böswilliger) Admin darf den Standardkanal weder
        löschen noch zum Ankündigungskanal machen: Neue könnten sich sonst
        nicht mehr melden und bekämen nie ein Manifest."""
        std = standard_kanal_id(cid)
        kanal = next((k for k in manifest["kanaele"] if k["id"] == std), None)
        if kanal is None or kanal["nur_admins"]:
            raise ValueError("Das Manifest entfernt oder sperrt den ersten Kanal.")

    def _auf_beitritt_antworten(self, cid):
        """Ein Neuer braucht das Manifest. Jeder, der es hat, darf es weiterreichen -
        es ist ja vom Besitzer unterschrieben. Damit nicht alle gleichzeitig
        antworten: höchstens einmal pro Minute und Community."""
        m = self._manifest(cid)
        if not m or time.time() - self._manifest_antwort.get(cid, 0) < 10:
            return
        self._manifest_antwort[cid] = time.time()
        try:
            self._manifest_verschicken(cid, m)
        except ValueError:
            pass

    def community_code_erneuern(self, cid) -> str:
        """Neuer Schlüssel: Wer nur den alten Code hat, liest ab jetzt nichts Neues mehr.

        Kontakte, die man in der Community gesehen hat, bekommen den neuen
        Schlüssel automatisch über ihre eigene, verschlüsselte DM.
        """
        return self._schluessel_wechseln(cid)

    def mitglied_entfernen(self, cid, nutzer_id) -> str:
        """Wirft jemanden hinaus - so weit das ohne Server geht. Nur der Besitzer.

        Technisch ist das ein neuer Code (wie community_code_erneuern), nur
        bekommt die entfernte Person den neuen Schlüssel NICHT, und ist sie
        Admin, verliert sie das Amt im selben Manifest.

        Was das ehrlich bedeutet:
        - Es stoppt nur NEUE Nachrichten. Was die Person schon gelesen oder
          heruntergeladen hat, behält sie; ihr Programm merkt nicht einmal,
          dass sie draussen ist - es kann bloss nichts Neues mehr öffnen.
        - Den neuen Schlüssel bekommen automatisch nur Mitglieder, die
          Kontakte des Besitzers sind. Alle anderen brauchen den neuen Code
          (Rückgabewert) - und wer ihn weitergibt, holt die Person zurück.
        """
        c = self.db.community_holen(cid)
        if not c or c["besitzer_id"] != self.meine_id:
            raise ValueError("Nur der Besitzer kann Mitglieder entfernen.")
        nutzer_id = id_normalisieren(nutzer_id)
        if nutzer_id == self.meine_id:
            raise ValueError("Dich selbst kannst du nicht entfernen - verlass die Community stattdessen.")
        admins = (self._manifest(cid) or {}).get("admins") or []
        eintrag = next((m for m in self.mitglieder(cid) if m["id"] == nutzer_id), None)
        if nutzer_id not in admins and eintrag is None:
            raise ValueError("Diese Person ist in dieser Community nicht bekannt.")
        name = self.name_von(nutzer_id)
        # Die Marke liegt mindestens auf ihrer letzten Zeile: Zeitstempel sind
        # eine Behauptung des Absenders und dürfen etwas in der Zukunft liegen.
        marke = max(_jetzt_ms(), int((eintrag or {}).get("ts") or 0))
        alle = dict(self.tresor.extra_holen("entfernt", {}) or {})
        alle[cid] = dict(alle.get(cid) or {}, **{nutzer_id: marke})
        self.tresor.extra_setzen("entfernt", alle)
        code = self._schluessel_wechseln(
            cid, ausser=nutzer_id,
            aendern=lambda k, a, n, i: (k, [x for x in a if x != nutzer_id], n, i))
        self._system(standard_kanal_id(cid), f"{name} wurde entfernt.")
        return code

    def _schluessel_wechseln(self, cid, ausser=None, aendern=lambda k, a, n, i: (k, a, n, i)) -> str:
        with self._lock:
            return self._schluessel_wechseln_gesperrt(cid, ausser, aendern)

    def _schluessel_wechseln_gesperrt(self, cid, ausser, aendern) -> str:
        c = self.db.community_holen(cid)
        if not c or c["besitzer_id"] != self.meine_id:
            raise ValueError("Nur der Besitzer kann einen neuen Code erstellen.")
        neu = community_schluessel_neu()
        stand = _jetzt_ms()
        self._schluessel_stand_setzen(cid, stand)
        mitglieder = {m["id"] for m in self.mitglieder(cid)} - {ausser, self.meine_id}
        self.tresor.community_schluessel_setzen(cid, neu)
        self.db.community_speichern(cid, schluessel=neu)
        for kid in mitglieder:
            k = self.db.kontakt_holen(kid)
            if k and k["status"] == "ok" and self.tresor.paar_schluessel(kid):
                u = um.dm_bauen(self.ich, self.tresor.paar_schluessel(kid), self.meine_id, kid,
                                {"art": "community_schluessel", "community": cid, "schluessel": _b64(neu),
                                 "stand": stand})
                self.ausgang(Auftrag(u.packen(), kid))
        self._manifest_aendern(cid, aendern)
        return self.einladung(cid)

    def _neuer_community_schluessel(self, u, inner):
        cid = inner.get("community")
        c = self.db.community_holen(cid) if isinstance(cid, str) else None
        if not c or c["besitzer_id"] != u.von:
            return
        try:
            neu = _unb64(inner["schluessel"])
            stand = int(inner["stand"])
        except (KeyError, ValueError, TypeError):
            return
        # Nur ein NEUERER Schlüssel: Eine alte, erneut eingespielte Nachricht
        # würde sonst den Schlüssel zurückdrehen - und wer beim letzten
        # Wechsel ausgesperrt wurde, läse wieder mit.
        if stand <= self._schluessel_stand(cid):
            return
        if len(neu) == 32:
            self._schluessel_stand_setzen(cid, stand)
            self.tresor.community_schluessel_setzen(cid, neu)
            self.db.community_speichern(cid, schluessel=neu)
            self._system(standard_kanal_id(cid), "Die Gruppe hat einen neuen Schlüssel bekommen.")

    def _schluessel_stand(self, cid) -> int:
        return int((self.tresor.extra_holen("schluessel_stand", {}) or {}).get(cid, 0))

    def _schluessel_stand_setzen(self, cid, stand):
        staende = dict(self.tresor.extra_holen("schluessel_stand", {}) or {})
        staende[cid] = int(stand)
        self.tresor.extra_setzen("schluessel_stand", staende)

    def mitglieder(self, cid) -> list:
        """Wer zuletzt geschrieben hat - eine Mitgliederliste gibt es bewusst nicht.

        Wer entfernt wurde, fehlt, bis er danach wieder schreibt (also mit
        einem neuen Code zurück ist). Sonst bekäme er beim nächsten neuen
        Code den Schlüssel wieder automatisch.
        """
        entfernt = ((self.tresor.extra_holen("entfernt", {}) or {}).get(cid)) or {}
        gesehen = {}
        for k in self.db.kanaele(cid):
            for n in self.db.nachrichten_seite(k["id"], anzahl=200):
                if n["absender_id"] not in (SYSTEM, None):
                    if n["ts"] > gesehen.get(n["absender_id"], -1):
                        gesehen[n["absender_id"]] = n["ts"]
        c = self.db.community_holen(cid) or {}
        besitzer = c.get("besitzer_id")
        admins = set((self._manifest(cid) or {}).get("admins") or [])
        return [{"id": i, "name": self.name_von(i), "farbe": self.farbe_von(i), "ts": ts,
                 "besitzer": i == besitzer, "admin": i == besitzer or i in admins,
                 "ich": i == self.meine_id}
                for i, ts in sorted(gesehen.items(), key=lambda x: -x[1])
                if ts > int(entfernt.get(i, -1))]

    # =================================================================
    #  Für die Oberfläche
    # =================================================================

    def _system(self, unterhaltung, text, ts=None, kennung=None, absender=SYSTEM):
        """Eine Hinweiszeile im Verlauf. `absender` darf eine echte ID sein
        ("X ist beigetreten") - so taucht X auch unter "Zuletzt aktiv" auf."""
        nid = kennung or ("s" + secrets.token_hex(15))
        if self.db.nachricht_einfuegen(nid, unterhaltung, absender, ts or _jetzt_ms(), {"text": text}, "system"):
            self._neu_gemeldet(unterhaltung, nid, text, mitteilung=False)

    def _neu_gemeldet(self, unterhaltung, nid, vorschau, mitteilung=True):
        n = self.db.nachricht_holen(nid)
        if not n:
            return
        ui = self.ui_nachricht(n)
        self.ereignis("nachricht_neu", unterhaltung=unterhaltung, nachricht=ui, vorschau=vorschau)
        if mitteilung and not ui["ich"] and n["art"] != "system":
            u = self.db.unterhaltung_holen(unterhaltung)
            if u and not u["stumm"]:
                titel = self.name_von(n["absender_id"]) if u["art"] == "dm" else self._unterhaltung_titel(u)
                self.ereignis("mitteilung", titel=titel, text=vorschau, unterhaltung=unterhaltung)

    def _geaendert(self, unterhaltung, nid, fortschritt=None):
        n = self.db.nachricht_holen(nid)
        if n:
            ui = self.ui_nachricht(n)
            if fortschritt is not None and ui.get("datei"):
                ui["datei"]["fortschritt"] = fortschritt
            self.ereignis("nachricht_geaendert", unterhaltung=unterhaltung, nachricht=ui)

    def ui_nachricht(self, n, reaktionen=None, namen_cache=None) -> dict:
        """Eine Datenbank-Zeile so, wie die Oberfläche sie braucht. Ohne Schlüssel!"""
        inhalt = n.get("inhalt") or {}
        von = n["absender_id"]
        ich = von == self.meine_id
        namen_cache = namen_cache if namen_cache is not None else {}
        def name(i):
            if i not in namen_cache:
                namen_cache[i] = self.name_von(i)
            return namen_cache[i]
        aus = {"id": n["id"], "von": von, "ich": ich, "ts": n["ts"],
               "art": n["art"], "text": inhalt.get("text", ""),
               "von_name": "Du" if ich else name(von), "von_farbe": self.farbe_von(von),
               "bearbeitet": bool(n["bearbeitet"]), "geloescht": n["geloescht"],
               "status": n["status"] or ("gesendet" if ich else ""), "weg": n["weg"] or None}
        if n["antwort_auf"]:
            bezug = self.db.nachricht_holen(n["antwort_auf"])
            if bezug:
                b_inhalt = bezug.get("inhalt") or {}
                aus["antwort_auf"] = {"id": bezug["id"],
                                      "von_name": "Du" if bezug["absender_id"] == self.meine_id else name(bezug["absender_id"]),
                                      "text": "Gelöscht" if bezug["geloescht"] else (b_inhalt.get("text") or (b_inhalt.get("datei") or {}).get("name", "Anhang"))}
        if reaktionen is None:
            reaktionen = self.db.reaktionen_fuer_nachrichten([n["id"]]).get(n["id"], [])
        gruppiert = {}
        for r in reaktionen:
            g = gruppiert.setdefault(r["emoji"], {"emoji": r["emoji"], "anzahl": 0, "meine": False, "namen": []})
            g["anzahl"] += 1
            g["meine"] = g["meine"] or r["absender_id"] == self.meine_id
            g["namen"].append("Du" if r["absender_id"] == self.meine_id else name(r["absender_id"]))
        aus["reaktionen"] = list(gruppiert.values())
        meta = inhalt.get("datei")
        if meta and not n["geloescht"]:
            anhang = self.db.anhang_holen(meta.get("datei_id", "")) or {}
            datei = {k: meta[k] for k in ("name", "groesse", "mime", "breite", "hoehe", "dauer_ms", "wellenform") if k in meta}
            if anhang.get("vorschau"):
                datei["vorschau"] = "data:image/jpeg;base64," + _b64(anhang["vorschau"])
            if anhang.get("zustand") == "fertig" and anhang.get("pfad"):
                datei["url"] = self.medien_url(anhang["pfad"])
            else:
                datei["fortschritt"] = 0 if anhang.get("zustand") == "laden" else None
                if anhang.get("zustand") == "kaputt":
                    datei["kaputt"] = True
            aus["datei"] = datei
        return aus

    def _unterhaltung_titel(self, u) -> str:
        if u["art"] == "dm":
            return self.name_von(u["id"])
        c = self.db.community_holen(u["community_id"]) if u["community_id"] else None
        if u["art"] == "gruppe":
            return (c or {}).get("name") or u["titel"]
        return u["titel"]

    def nachrichten(self, unterhaltung, vor_ts=None, anzahl=50, vor_id=None):
        zeilen = self.db.nachrichten_seite(unterhaltung, vor_ts=vor_ts, anzahl=anzahl + 1, vor_id=vor_id)
        mehr = len(zeilen) > anzahl
        zeilen = list(reversed(zeilen[:anzahl]))
        reaktionen = self.db.reaktionen_fuer_nachrichten([z["id"] for z in zeilen])
        cache = {}
        return [self.ui_nachricht(z, reaktionen.get(z["id"], []), cache) for z in zeilen], mehr

    def unterhaltungen(self, online=lambda i: False, weg_zu=lambda i: None) -> list:
        liste = []
        for u in self.db.unterhaltungen_alle(self.meine_id):
            letzte = u.get("letzte_nachricht")
            eintrag = {"id": u["id"], "art": u["art"], "ungelesen": u["ungelesen"], "stumm": u["stumm"],
                       "angeheftet": u["angeheftet"], "entwurf": u["entwurf"],
                       "titel": self._unterhaltung_titel(u), "community_id": u["community_id"]}
            if letzte:
                inhalt = letzte.get("inhalt") or {}
                text = "Gelöscht" if letzte["geloescht"] else (inhalt.get("text") or self._vorschau_text(letzte["art"], (inhalt.get("datei") or {}).get("name", "")))
                eintrag["letzte"] = {"text": text, "ts": letzte["ts"], "ich": letzte["absender_id"] == self.meine_id,
                                     "von_name": self.name_von(letzte["absender_id"]) if letzte["absender_id"] != SYSTEM else ""}
            if u["art"] == "dm":
                k = self.db.kontakt_holen(u["id"]) or {}
                eintrag.update(farbe=((k.get("karte") or {}).get("avatar_farbe") or ""),
                               verifiziert=k.get("verifiziert", False),
                               schluessel_geaendert=k.get("schluessel_neu", False),
                               online=online(u["id"]), weg=weg_zu(u["id"]))
            else:
                c = self.db.community_holen(u["community_id"]) or {}
                kanal = self.db.kanal_holen(u["id"]) or {}
                eintrag.update(farbe="", community_name=c.get("name", ""), kanal_name=kanal.get("name", u["titel"]),
                               nur_admins=kanal.get("nur_admins", False),
                               darf_schreiben=not kanal.get("nur_admins") or self._ist_admin(u["community_id"], self.meine_id))
                if u["art"] == "gruppe":
                    eintrag["untertitel"] = "Gruppe"
            liste.append(eintrag)
        return liste

    def communities(self) -> list:
        alle = self.db.unterhaltungen_alle(self.meine_id)
        ungelesen = {u["id"]: u["ungelesen"] for u in alle}
        letzte = {u["id"]: (u.get("letzte_nachricht") or {}).get("ts", 0) for u in alle}
        liste = []
        for c in self.db.communities_alle():
            m = self._manifest(c["id"]) or {}
            kanaele = self.db.kanaele(c["id"])
            liste.append({"id": c["id"], "name": c["name"], "icon": c["icon"] or "", "farbe": "",
                          "gruppe": m.get("art") == "gruppe",
                          "admin": self._ist_admin(c["id"], self.meine_id),
                          "besitzer": c["besitzer_id"] == self.meine_id,
                          "rolle": self._rolle(c["id"]),
                          "geladen": bool(m),
                          "letzte_ts": max([letzte.get(k["id"], 0) for k in kanaele] or [0]),
                          "mitglieder": len({m["id"] for m in self.mitglieder(c["id"])} | {self.meine_id}),
                          "kanaele": [{"id": k["id"], "name": k["name"], "unterhaltung": k["id"],
                                       "nur_admins": k["nur_admins"], "ungelesen": ungelesen.get(k["id"], 0)}
                                      for k in kanaele]})
        return liste
