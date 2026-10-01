# -*- coding: utf-8 -*-
"""
=====================================================================
 datenbank.py - Kontakte, Unterhaltungen und Nachrichten (SQLite)
=====================================================================
Alles, was der Messenger sich merken muss, liegt in vp4_daten/vp4.db.

WAS VERSCHLÜSSELT IST - UND WAS NICHT
--------------------------------------
Die SQLite-Datei selbst ist nicht verschlüsselt (dafür bräuchte es
SQLCipher, und das gibt es nicht als fertiges Rad für Windows). Stattdessen
wird jede Spalte mit Inhalt einzeln mit AES-256-GCM verschlüsselt:

    nachrichten.inhalt_enc       der Nachrichtentext
    unterhaltungen.entwurf_enc   der angefangene Entwurf
    anhaenge.name_enc            der Dateiname eines Anhangs
    anhaenge.vorschau            das Vorschaubild (ein Bild IST Inhalt)
    communities.schluessel_enc   der Community-Schlüssel
    kontakte.spitzname           der selbst vergebene Name

Die letzten beiden ohne "_enc" im Namen: Die Spaltennamen sind aus dem
Bauplan übernommen, verschlüsselt werden sie trotzdem - ein Vorschaubild
oder ein Spitzname im Klartext wäre genau das, was niemand auf der Platte
finden soll.

Im Klartext bleiben die METADATEN: wer mit wem, wann, wie viele
Nachrichten, Reaktions-Emojis, Community- und Kanalnamen, Status. Wer die
Datei in die Hand bekommt, sieht also, DASS geschrieben wurde - aber nicht,
WAS. So steht es auch in der Oberfläche.

Schlüssel: HKDF-SHA256 aus dem Datenschlüssel des Tresors
(info=b"VP4 db v1"). AAD jedes Eintrags: Tabelle, Spalte und Zeilen-ID.
Damit lässt sich ein verschlüsselter Wert nicht unbemerkt in eine andere
Zeile oder Spalte umkopieren - etwa ein Nachrichtentext als Entwurf einer
anderen Unterhaltung. Aufbau eines Werts: Nonce (12) | Geheimtext.

Gelöschte Inhalte werden überschrieben (PRAGMA secure_delete) und das WAL
sofort zurückgeschrieben - sonst stünde der alte Geheimtext noch eine
Weile in freien Seiten herum, und der Datenschlüssel öffnet ihn ja weiter.
=====================================================================
"""

import json
import os
import sqlite3
import threading
import time
from contextlib import contextmanager
from pathlib import Path

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

from kern.speicher import DATA_DIR


DATENBANK_DATEI = DATA_DIR / "vp4.db"

_HKDF_INFO = b"VP4 db v1"
_PRUEFWERT = b"VP4 Datenbank - Schluessel stimmt"
_NONCE = 12

KONTAKT_STATUS = ("anfrage_raus", "anfrage_rein", "ok", "blockiert")
UNTERHALTUNG_ARTEN = ("dm", "gruppe", "kanal")

# Wie viele Nachrichten eine Seite hat (Bauplan: Seiten zu 50, neueste zuerst).
SEITENGROESSE = 50


_SCHEMA_V1 = """
CREATE TABLE meta(
    name TEXT PRIMARY KEY,
    wert
);

CREATE TABLE kontakte(
    id TEXT PRIMARY KEY,
    spitzname BLOB,
    karte_json TEXT,
    verifiziert INTEGER NOT NULL DEFAULT 0 CHECK(verifiziert IN (0, 1)),
    schluessel_neu INTEGER NOT NULL DEFAULT 0 CHECK(schluessel_neu IN (0, 1)),
    status TEXT NOT NULL
        CHECK(status IN ('anfrage_raus','anfrage_rein','ok','blockiert')),
    hinzugefuegt INTEGER
);

CREATE TABLE communities(
    id TEXT PRIMARY KEY,
    name TEXT,
    icon TEXT,
    schluessel_enc BLOB,
    besitzer_id TEXT,
    manifest_json TEXT,
    beigetreten INTEGER
);

CREATE TABLE kanaele(
    id TEXT PRIMARY KEY,
    community_id TEXT NOT NULL REFERENCES communities(id) ON DELETE CASCADE,
    name TEXT,
    position INTEGER NOT NULL DEFAULT 0,
    nur_admins INTEGER NOT NULL DEFAULT 0 CHECK(nur_admins IN (0, 1))
);

CREATE TABLE unterhaltungen(
    id TEXT PRIMARY KEY,
    art TEXT NOT NULL CHECK(art IN ('dm','gruppe','kanal')),
    community_id TEXT REFERENCES communities(id) ON DELETE CASCADE,
    titel TEXT,
    stumm INTEGER NOT NULL DEFAULT 0 CHECK(stumm IN (0, 1)),
    angeheftet INTEGER NOT NULL DEFAULT 0 CHECK(angeheftet IN (0, 1)),
    entwurf_enc BLOB,
    zuletzt_gelesen INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE nachrichten(
    id TEXT PRIMARY KEY,
    unterhaltung_id TEXT NOT NULL REFERENCES unterhaltungen(id) ON DELETE CASCADE,
    absender_id TEXT,
    ts INTEGER NOT NULL,
    art TEXT NOT NULL DEFAULT 'text',
    inhalt_enc BLOB,
    antwort_auf TEXT,
    bearbeitet INTEGER NOT NULL DEFAULT 0,
    geloescht INTEGER NOT NULL DEFAULT 0 CHECK(geloescht IN (0, 1)),
    status TEXT,
    weg TEXT
);
CREATE INDEX nachrichten_unterhaltung_ts ON nachrichten(unterhaltung_id, ts);

CREATE TABLE reaktionen(
    nachricht_id TEXT NOT NULL REFERENCES nachrichten(id) ON DELETE CASCADE,
    absender_id TEXT NOT NULL,
    emoji TEXT NOT NULL,
    ts INTEGER,
    PRIMARY KEY(nachricht_id, absender_id, emoji)
);

CREATE TABLE anhaenge(
    id TEXT PRIMARY KEY,
    nachricht_id TEXT NOT NULL REFERENCES nachrichten(id) ON DELETE CASCADE,
    name_enc BLOB,
    groesse INTEGER,
    mime TEXT,
    pfad TEXT,
    zustand TEXT,
    vorschau BLOB
);
CREATE INDEX anhaenge_nachricht ON anhaenge(nachricht_id);

CREATE TABLE discord_stand(
    kanal_id TEXT PRIMARY KEY,
    letzte_nachricht_id TEXT
);
"""


def _jetzt_ms() -> int:
    return int(time.time() * 1000)


def _bool(wert) -> int:
    return 1 if wert else 0


class DatenbankFehler(ValueError):
    """Ein Eintrag verstößt gegen die Regeln der Datenbank."""


class Datenbank:
    """Die Messenger-Datenbank. Gibt immer einfache dicts zurück, mit
    bereits entschlüsselten Feldern (ohne "_enc" im Namen).

    Thread-sicher: eine Verbindung, ein Schloss. Netzwerk-Threads und die
    Brücke zur Oberfläche dürfen gleichzeitig zugreifen.
    """

    SCHEMA_VERSION = 1
    # Für spätere Änderungen am Aufbau: {Zielversion: funktion(verbindung)}.
    # Beim Öffnen werden alle fehlenden Schritte der Reihe nach ausgeführt,
    # jeder in seiner eigenen Transaktion.
    MIGRATIONEN = {}

    def __init__(self, pfad=None, datenschluessel: bytes = None, eigene_id: str = None):
        if not isinstance(datenschluessel, (bytes, bytearray)) or len(datenschluessel) != 32:
            raise ValueError("Die Datenbank braucht den 32-Byte-Datenschlüssel des Tresors.")
        self.pfad = Path(pfad) if pfad is not None else DATENBANK_DATEI
        self.eigene_id = eigene_id
        self._schluessel = HKDF(algorithm=hashes.SHA256(), length=32, salt=None,
                                info=_HKDF_INFO).derive(bytes(datenschluessel))
        self._aead = AESGCM(self._schluessel)
        self._lock = threading.RLock()
        self.pfad.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(self.pfad), check_same_thread=False,
                                     isolation_level=None)
        self._conn.row_factory = sqlite3.Row
        try:
            self._einrichten()
        except Exception:
            self._conn.close()
            raise

    # ------------------------------------------------------------ Grundlagen

    def _einrichten(self):
        c = self._conn
        c.execute("PRAGMA journal_mode=WAL")
        c.execute("PRAGMA foreign_keys=ON")
        c.execute("PRAGMA secure_delete=ON")
        c.execute("PRAGMA synchronous=NORMAL")
        c.execute("PRAGMA busy_timeout=5000")
        with self._lock:
            neu = c.execute("SELECT 1 FROM sqlite_master WHERE type='table' "
                            "AND name='meta'").fetchone() is None
            if neu:
                # executescript() würde vorher selbst COMMIT sagen - deshalb
                # Befehl für Befehl, alles in einer Transaktion.
                with self._transaktion():
                    for befehl in _SCHEMA_V1.split(";"):
                        if befehl.strip():
                            c.execute(befehl)
                    c.execute("INSERT INTO meta(name, wert) VALUES('schema_version', ?)",
                              (self.SCHEMA_VERSION,))
                    c.execute("INSERT INTO meta(name, wert) VALUES('pruefwert', ?)",
                              (self._ver("meta", "wert", "pruefwert", _PRUEFWERT),))
            else:
                self._schluessel_pruefen()
                self._migrieren()

    def _schluessel_pruefen(self):
        zeile = self._conn.execute(
            "SELECT wert FROM meta WHERE name='pruefwert'").fetchone()
        try:
            ok = zeile is not None and self._ent(
                "meta", "wert", "pruefwert", zeile["wert"]) == _PRUEFWERT
        except ValueError:
            ok = False
        if not ok:
            raise ValueError("Die Datenbank gehört zu einem anderen Tresor "
                             "(der Datenschlüssel passt nicht).")

    def schema_version(self) -> int:
        with self._lock:
            zeile = self._conn.execute(
                "SELECT wert FROM meta WHERE name='schema_version'").fetchone()
            return int(zeile["wert"]) if zeile else 0

    def _migrieren(self):
        version = self.schema_version()
        if version > self.SCHEMA_VERSION:
            raise ValueError("Die Datenbank stammt aus einer neueren Fassung von VP4.")
        for ziel in range(version + 1, self.SCHEMA_VERSION + 1):
            schritt = self.MIGRATIONEN.get(ziel)
            if schritt is None:
                raise ValueError(f"Für Datenbank-Version {ziel} fehlt der Umbauschritt.")
            with self._transaktion():
                schritt(self._conn)
                self._conn.execute("UPDATE meta SET wert=? WHERE name='schema_version'",
                                   (ziel,))

    @contextmanager
    def _transaktion(self):
        """Eine Transaktion unter dem Schloss. Regelverstöße (CHECK,
        Fremdschlüssel) kommen als DatenbankFehler (ValueError) heraus."""
        with self._lock:
            self._conn.execute("BEGIN IMMEDIATE")
            try:
                yield self._conn
            except sqlite3.IntegrityError as e:
                self._conn.execute("ROLLBACK")
                raise DatenbankFehler(f"Ungültiger Eintrag: {e}") from None
            except BaseException:
                self._conn.execute("ROLLBACK")
                raise
            else:
                self._conn.execute("COMMIT")

    def _abfrage(self, sql: str, werte=()) -> list:
        with self._lock:
            return self._conn.execute(sql, werte).fetchall()

    def close(self):
        with self._lock:
            try:
                self._conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            except sqlite3.Error:
                pass
            self._conn.close()

    schliessen = close

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()

    def wal_zurueckschreiben(self):
        """Schreibt das WAL in die Hauptdatei und leert es."""
        with self._lock:
            self._conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")

    # ------------------------------------------------------- Verschlüsselung

    @staticmethod
    def _aad(tabelle: str, spalte: str, zeilen_id) -> bytes:
        return "\x00".join(("VP4 db v1", tabelle, spalte, str(zeilen_id))).encode("utf-8")

    def _ver(self, tabelle: str, spalte: str, zeilen_id, klar):
        if klar is None:
            return None
        nonce = os.urandom(_NONCE)
        return nonce + self._aead.encrypt(nonce, bytes(klar),
                                          self._aad(tabelle, spalte, zeilen_id))

    def _ent(self, tabelle: str, spalte: str, zeilen_id, blob):
        if blob is None:
            return None
        blob = bytes(blob)
        try:
            return self._aead.decrypt(blob[:_NONCE], blob[_NONCE:],
                                      self._aad(tabelle, spalte, zeilen_id))
        except Exception:
            raise ValueError(f"Ein Eintrag in '{tabelle}' ist beschädigt "
                             "oder wurde verändert.")

    def _ver_text(self, tabelle, spalte, zeilen_id, text):
        return None if text is None else self._ver(
            tabelle, spalte, zeilen_id, str(text).encode("utf-8"))

    def _ent_text(self, tabelle, spalte, zeilen_id, blob):
        roh = self._ent(tabelle, spalte, zeilen_id, blob)
        return None if roh is None else roh.decode("utf-8")

    def _sicher(self, funktion, *argumente):
        """Entschlüsselt, ohne bei einem einzelnen kaputten Eintrag die ganze
        Liste platzen zu lassen. Rückgabe: (wert, lesbar)."""
        try:
            return funktion(*argumente), True
        except ValueError:
            return None, False

    def _teil_speichern(self, tabelle: str, zeilen_id, werte: dict, standard: dict):
        """Legt eine Zeile an oder ändert nur die übergebenen Felder.

        None heißt "nicht ändern". Spaltennamen stammen immer aus diesem
        Modul, nie von außen.
        """
        werte = {k: v for k, v in werte.items() if v is not None}
        with self._transaktion() as c:
            da = c.execute(f"SELECT 1 FROM {tabelle} WHERE id=?",
                           (zeilen_id,)).fetchone()
            if da:
                if werte:
                    zuweisung = ", ".join(f"{k}=?" for k in werte)
                    c.execute(f"UPDATE {tabelle} SET {zuweisung} WHERE id=?",
                              (*werte.values(), zeilen_id))
            else:
                alles = dict(standard)
                alles.update(werte)
                alles["id"] = zeilen_id
                spalten = ", ".join(alles)
                platz = ", ".join("?" for _ in alles)
                c.execute(f"INSERT INTO {tabelle}({spalten}) VALUES({platz})",
                          tuple(alles.values()))

    def _aendern(self, sql: str, werte) -> bool:
        with self._transaktion() as c:
            return c.execute(sql, werte).rowcount > 0

    # =================================================================
    #  Kontakte
    # =================================================================

    def _kontakt_dict(self, z) -> dict:
        spitzname, lesbar = self._sicher(
            self._ent_text, "kontakte", "spitzname", z["id"], z["spitzname"])
        karte = None
        if z["karte_json"]:
            try:
                karte = json.loads(z["karte_json"])
            except ValueError:
                karte = None
        return {"id": z["id"], "spitzname": spitzname or "", "karte_json": z["karte_json"],
                "karte": karte, "verifiziert": bool(z["verifiziert"]),
                "schluessel_neu": bool(z["schluessel_neu"]), "status": z["status"],
                "hinzugefuegt": z["hinzugefuegt"], "lesbar": lesbar}

    def kontakt_speichern(self, kontakt_id: str, spitzname: str = None, karte_json=None,
                          status: str = None, verifiziert: bool = None,
                          schluessel_neu: bool = None, hinzugefuegt: int = None) -> dict:
        """Legt einen Kontakt an oder ändert die übergebenen Felder.

        karte_json darf ein dict oder schon fertiger JSON-Text sein.
        """
        kontakt_id = (kontakt_id or "").strip()
        if not kontakt_id:
            raise ValueError("Bitte eine Kontakt-ID angeben.")
        if isinstance(karte_json, (dict, list)):
            karte_json = json.dumps(karte_json, ensure_ascii=False, sort_keys=True)
        werte = {
            "spitzname": self._ver_text("kontakte", "spitzname", kontakt_id, spitzname),
            "karte_json": karte_json,
            "status": status,
            "verifiziert": None if verifiziert is None else _bool(verifiziert),
            "schluessel_neu": None if schluessel_neu is None else _bool(schluessel_neu),
            "hinzugefuegt": hinzugefuegt,
        }
        self._teil_speichern("kontakte", kontakt_id, werte, {
            "status": "anfrage_raus", "verifiziert": 0, "schluessel_neu": 0,
            "hinzugefuegt": _jetzt_ms()})
        return self.kontakt_holen(kontakt_id)

    def kontakt_holen(self, kontakt_id: str):
        z = self._abfrage("SELECT * FROM kontakte WHERE id=?", (kontakt_id,))
        return self._kontakt_dict(z[0]) if z else None

    def kontakte_alle(self, status: str = None) -> list:
        if status is None:
            zeilen = self._abfrage("SELECT * FROM kontakte ORDER BY hinzugefuegt, id")
        else:
            zeilen = self._abfrage("SELECT * FROM kontakte WHERE status=? "
                                   "ORDER BY hinzugefuegt, id", (status,))
        return [self._kontakt_dict(z) for z in zeilen]

    def kontakt_status_setzen(self, kontakt_id: str, status: str) -> bool:
        return self._aendern("UPDATE kontakte SET status=? WHERE id=?",
                             (status, kontakt_id))

    def kontakt_verifiziert_setzen(self, kontakt_id: str, verifiziert: bool = True) -> bool:
        return self._aendern("UPDATE kontakte SET verifiziert=? WHERE id=?",
                             (_bool(verifiziert), kontakt_id))

    def kontakt_schluessel_neu_setzen(self, kontakt_id: str, neu: bool = True) -> bool:
        return self._aendern("UPDATE kontakte SET schluessel_neu=? WHERE id=?",
                             (_bool(neu), kontakt_id))

    def kontakt_loeschen(self, kontakt_id: str) -> bool:
        return self._aendern("DELETE FROM kontakte WHERE id=?", (kontakt_id,))

    # =================================================================
    #  Unterhaltungen
    # =================================================================

    def _unterhaltung_dict(self, z) -> dict:
        entwurf, lesbar = self._sicher(
            self._ent_text, "unterhaltungen", "entwurf_enc", z["id"], z["entwurf_enc"])
        return {"id": z["id"], "art": z["art"], "community_id": z["community_id"],
                "titel": z["titel"] or "", "stumm": bool(z["stumm"]),
                "angeheftet": bool(z["angeheftet"]), "entwurf": entwurf or "",
                "zuletzt_gelesen": z["zuletzt_gelesen"], "lesbar": lesbar}

    def unterhaltung_anlegen_oder_holen(self, unterhaltung_id: str, art: str = "dm",
                                        titel: str = "", community_id: str = None) -> dict:
        """Gibt die Unterhaltung zurück und legt sie an, falls es sie nicht gibt.

        Eine bestehende Unterhaltung wird dabei nicht verändert.
        """
        unterhaltung_id = (unterhaltung_id or "").strip()
        if not unterhaltung_id:
            raise ValueError("Bitte eine Unterhaltungs-ID angeben.")
        with self._transaktion() as c:
            c.execute("INSERT INTO unterhaltungen(id, art, community_id, titel) "
                      "VALUES(?, ?, ?, ?) ON CONFLICT(id) DO NOTHING",
                      (unterhaltung_id, art, community_id, titel or ""))
        return self.unterhaltung_holen(unterhaltung_id)

    def unterhaltung_holen(self, unterhaltung_id: str):
        z = self._abfrage("SELECT * FROM unterhaltungen WHERE id=?", (unterhaltung_id,))
        return self._unterhaltung_dict(z[0]) if z else None

    def unterhaltungen_alle(self, eigene_id: str = None) -> list:
        """Alle Unterhaltungen mit letzter Nachricht und Zahl der ungelesenen.

        Ungelesen = nicht gelöscht, neuer als zuletzt_gelesen und nicht von
        einem selbst (eigene_id, sonst die beim Öffnen übergebene).
        Angeheftete zuerst, dann nach der letzten Nachricht.
        """
        eigene_id = eigene_id if eigene_id is not None else self.eigene_id
        with self._lock:
            zeilen = self._conn.execute(
                "SELECT u.*, (SELECT COUNT(*) FROM nachrichten n "
                "   WHERE n.unterhaltung_id = u.id AND n.ts > u.zuletzt_gelesen "
                "   AND n.geloescht = 0 AND (n.absender_id IS NOT ? OR ? IS NULL)"
                ") AS ungelesen FROM unterhaltungen u",
                (eigene_id, eigene_id)).fetchall()
            ergebnis = []
            for z in zeilen:
                eintrag = self._unterhaltung_dict(z)
                eintrag["ungelesen"] = z["ungelesen"]
                letzte = self._conn.execute(
                    "SELECT * FROM nachrichten WHERE unterhaltung_id=? "
                    "ORDER BY ts DESC, id DESC LIMIT 1", (z["id"],)).fetchone()
                eintrag["letzte_nachricht"] = self._nachricht_dict(letzte) if letzte else None
                ergebnis.append(eintrag)
        ergebnis.sort(key=lambda u: (
            not u["angeheftet"],
            -(u["letzte_nachricht"]["ts"] if u["letzte_nachricht"] else 0),
            u["id"]))
        return ergebnis

    def entwurf_setzen(self, unterhaltung_id: str, text: str) -> bool:
        blob = self._ver_text("unterhaltungen", "entwurf_enc", unterhaltung_id,
                              text) if text else None
        return self._aendern("UPDATE unterhaltungen SET entwurf_enc=? WHERE id=?",
                             (blob, unterhaltung_id))

    def entwurf_holen(self, unterhaltung_id: str) -> str:
        u = self.unterhaltung_holen(unterhaltung_id)
        return u["entwurf"] if u else ""

    def stumm_setzen(self, unterhaltung_id: str, stumm: bool = True) -> bool:
        return self._aendern("UPDATE unterhaltungen SET stumm=? WHERE id=?",
                             (_bool(stumm), unterhaltung_id))

    def angeheftet_setzen(self, unterhaltung_id: str, angeheftet: bool = True) -> bool:
        return self._aendern("UPDATE unterhaltungen SET angeheftet=? WHERE id=?",
                             (_bool(angeheftet), unterhaltung_id))

    def gelesen_bis_setzen(self, unterhaltung_id: str, ts: int) -> bool:
        """Merkt sich, bis wohin gelesen wurde. Geht nie zurück."""
        return self._aendern(
            "UPDATE unterhaltungen SET zuletzt_gelesen=MAX(zuletzt_gelesen, ?) "
            "WHERE id=?", (int(ts), unterhaltung_id))

    def unterhaltung_loeschen(self, unterhaltung_id: str) -> bool:
        """Löscht die Unterhaltung samt Nachrichten, Reaktionen, Anhängen."""
        ok = self._aendern("DELETE FROM unterhaltungen WHERE id=?", (unterhaltung_id,))
        if ok:
            self.wal_zurueckschreiben()
        return ok

    # =================================================================
    #  Nachrichten
    # =================================================================

    def _nachricht_dict(self, z) -> dict:
        roh, lesbar = self._sicher(
            self._ent, "nachrichten", "inhalt_enc", z["id"], z["inhalt_enc"])
        inhalt = None
        if roh is not None:
            try:
                inhalt = json.loads(roh.decode("utf-8"))
            except ValueError:
                inhalt, lesbar = None, False
        return {"id": z["id"], "unterhaltung_id": z["unterhaltung_id"],
                "absender_id": z["absender_id"], "ts": z["ts"], "art": z["art"],
                "inhalt": inhalt, "antwort_auf": z["antwort_auf"],
                "bearbeitet": z["bearbeitet"], "geloescht": bool(z["geloescht"]),
                "status": z["status"], "weg": z["weg"], "lesbar": lesbar}

    def _inhalt_ver(self, nachricht_id: str, inhalt):
        return self._ver("nachrichten", "inhalt_enc", nachricht_id,
                         json.dumps(inhalt, ensure_ascii=False).encode("utf-8"))

    def nachricht_einfuegen(self, nachricht_id: str, unterhaltung_id: str,
                            absender_id: str, ts: int, inhalt, art: str = "text",
                            antwort_auf: str = None, status: str = "",
                            weg: str = "") -> bool:
        """Speichert eine Nachricht. False, wenn es die ID schon gibt.

        Das macht doppelte Zustellung harmlos: Kommt dieselbe Nachricht über
        WLAN und Discord, landet sie trotzdem nur einmal im Verlauf.
        inhalt ist ein Text oder ein JSON-fähiges dict (Text unter "text").
        """
        nachricht_id = (nachricht_id or "").strip()
        if not nachricht_id:
            raise ValueError("Bitte eine Nachrichten-ID angeben.")
        blob = self._inhalt_ver(nachricht_id, inhalt)
        with self._transaktion() as c:
            if c.execute("SELECT 1 FROM nachrichten WHERE id=?",
                         (nachricht_id,)).fetchone():
                return False
            c.execute("INSERT INTO nachrichten(id, unterhaltung_id, absender_id, ts, "
                      "art, inhalt_enc, antwort_auf, status, weg) "
                      "VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?)",
                      (nachricht_id, unterhaltung_id, absender_id, int(ts), art,
                       blob, antwort_auf, status, weg))
        return True

    def nachrichten_seite(self, unterhaltung_id: str, vor_ts: int = None,
                          anzahl: int = SEITENGROESSE, vor_id: str = None) -> list:
        """Eine Seite Nachrichten, NEUESTE ZUERST.

        Ohne vor_ts die jüngsten; mit vor_ts die davor. Wer vor_id (die ID
        der ältesten bisher geladenen Nachricht) mitgibt, verliert auch
        dann nichts, wenn mehrere Nachrichten genau denselben Zeitstempel
        haben.
        """
        anzahl = max(1, int(anzahl))
        if vor_ts is None:
            zeilen = self._abfrage(
                "SELECT * FROM nachrichten WHERE unterhaltung_id=? "
                "ORDER BY ts DESC, id DESC LIMIT ?", (unterhaltung_id, anzahl))
        elif vor_id is None:
            zeilen = self._abfrage(
                "SELECT * FROM nachrichten WHERE unterhaltung_id=? AND ts < ? "
                "ORDER BY ts DESC, id DESC LIMIT ?", (unterhaltung_id, int(vor_ts), anzahl))
        else:
            zeilen = self._abfrage(
                "SELECT * FROM nachrichten WHERE unterhaltung_id=? "
                "AND (ts < ? OR (ts = ? AND id < ?)) "
                "ORDER BY ts DESC, id DESC LIMIT ?",
                (unterhaltung_id, int(vor_ts), int(vor_ts), vor_id, anzahl))
        return [self._nachricht_dict(z) for z in zeilen]

    def nachricht_holen(self, nachricht_id: str):
        z = self._abfrage("SELECT * FROM nachrichten WHERE id=?", (nachricht_id,))
        return self._nachricht_dict(z[0]) if z else None

    def nachricht_bearbeiten(self, nachricht_id: str, neuer_inhalt, ts: int = None) -> bool:
        """Ersetzt den Inhalt. bearbeitet = Zeitpunkt der Änderung.

        False, wenn es die Nachricht nicht gibt oder sie gelöscht ist.
        """
        blob = self._inhalt_ver(nachricht_id, neuer_inhalt)
        ok = self._aendern(
            "UPDATE nachrichten SET inhalt_enc=?, bearbeitet=? "
            "WHERE id=? AND geloescht=0", (blob, int(ts or _jetzt_ms()), nachricht_id))
        if ok:
            # Die alte Fassung soll nicht im WAL liegen bleiben.
            self.wal_zurueckschreiben()
        return ok

    def nachricht_als_geloescht_markieren(self, nachricht_id: str) -> list:
        """Löscht den Inhalt, lässt aber einen Platzhalter stehen.

        Der Platzhalter ("Nachricht gelöscht") bleibt, damit Antworten
        darauf nicht ins Leere zeigen. Gelöscht werden: der Text, die
        Reaktionen, Name und Vorschau der Anhänge. Rückgabe: die Pfade der
        Anhang-Dateien, damit der Aufrufer sie von der Platte nehmen kann.
        """
        with self._transaktion() as c:
            pfade = [z["pfad"] for z in c.execute(
                "SELECT pfad FROM anhaenge WHERE nachricht_id=?", (nachricht_id,))
                if z["pfad"]]
            c.execute("UPDATE nachrichten SET inhalt_enc=NULL, geloescht=1 WHERE id=?",
                      (nachricht_id,))
            c.execute("DELETE FROM reaktionen WHERE nachricht_id=?", (nachricht_id,))
            c.execute("UPDATE anhaenge SET name_enc=NULL, vorschau=NULL, pfad=NULL, "
                      "zustand='geloescht' WHERE nachricht_id=?", (nachricht_id,))
        self.wal_zurueckschreiben()
        return pfade

    def nachricht_status_setzen(self, nachricht_id: str, status: str) -> bool:
        return self._aendern("UPDATE nachrichten SET status=? WHERE id=?",
                             (status, nachricht_id))

    def nachrichten_suchen(self, unterhaltung_id, text: str, hoechstens: int = 200) -> list:
        """Sucht in einer Unterhaltung (oder mit None in allen).

        Die Texte sind verschlüsselt, SQLite kann also nicht selbst suchen:
        jede Nachricht wird hier in Python entschlüsselt und verglichen.
        Groß-/Kleinschreibung zählt nicht. Neueste zuerst.
        """
        suche = (text or "").casefold()
        if not suche:
            return []
        if unterhaltung_id is None:
            zeilen = self._abfrage("SELECT * FROM nachrichten WHERE geloescht=0 "
                                   "ORDER BY ts DESC, id DESC")
        else:
            zeilen = self._abfrage("SELECT * FROM nachrichten WHERE geloescht=0 AND "
                                   "unterhaltung_id=? ORDER BY ts DESC, id DESC",
                                   (unterhaltung_id,))
        treffer = []
        for z in zeilen:
            n = self._nachricht_dict(z)
            inhalt = n["inhalt"]
            if isinstance(inhalt, dict):
                inhalt = inhalt.get("text")
            if isinstance(inhalt, str) and suche in inhalt.casefold():
                treffer.append(n)
                if len(treffer) >= hoechstens:
                    break
        return treffer

    # =================================================================
    #  Reaktionen
    # =================================================================

    def reaktion_setzen(self, nachricht_id: str, absender_id: str, emoji: str,
                        ts: int = None):
        with self._transaktion() as c:
            c.execute("INSERT INTO reaktionen(nachricht_id, absender_id, emoji, ts) "
                      "VALUES(?, ?, ?, ?) ON CONFLICT(nachricht_id, absender_id, emoji) "
                      "DO UPDATE SET ts=excluded.ts",
                      (nachricht_id, absender_id, emoji, int(ts or _jetzt_ms())))

    def reaktion_entfernen(self, nachricht_id: str, absender_id: str, emoji: str) -> bool:
        return self._aendern("DELETE FROM reaktionen WHERE nachricht_id=? AND "
                             "absender_id=? AND emoji=?",
                             (nachricht_id, absender_id, emoji))

    def reaktionen_fuer_nachrichten(self, nachricht_ids) -> dict:
        """{nachricht_id: [{"absender_id", "emoji", "ts"}, ...]}"""
        ids = list(dict.fromkeys(nachricht_ids))
        ergebnis = {i: [] for i in ids}
        for anfang in range(0, len(ids), 500):
            teil = ids[anfang:anfang + 500]
            platz = ", ".join("?" for _ in teil)
            for z in self._abfrage(
                    f"SELECT * FROM reaktionen WHERE nachricht_id IN ({platz}) "
                    "ORDER BY ts, absender_id", teil):
                ergebnis[z["nachricht_id"]].append(
                    {"absender_id": z["absender_id"], "emoji": z["emoji"], "ts": z["ts"]})
        return ergebnis

    # =================================================================
    #  Anhänge
    # =================================================================

    def _anhang_dict(self, z) -> dict:
        name, lesbar1 = self._sicher(self._ent_text, "anhaenge", "name_enc",
                                     z["id"], z["name_enc"])
        vorschau, lesbar2 = self._sicher(self._ent, "anhaenge", "vorschau",
                                         z["id"], z["vorschau"])
        return {"id": z["id"], "nachricht_id": z["nachricht_id"], "name": name or "",
                "groesse": z["groesse"], "mime": z["mime"], "pfad": z["pfad"],
                "zustand": z["zustand"], "vorschau": vorschau,
                "lesbar": lesbar1 and lesbar2}

    def anhang_anlegen(self, anhang_id: str, nachricht_id: str, name: str,
                       groesse: int = 0, mime: str = "", pfad: str = "",
                       zustand: str = "neu", vorschau: bytes = None) -> dict:
        """Legt einen Anhang an.

        Achtung beim pfad: Er steht im Klartext. Die Datei auf der Platte
        sollte deshalb einen zufälligen Namen tragen, nicht den echten -
        der echte steht verschlüsselt in name_enc.
        """
        with self._transaktion() as c:
            c.execute("INSERT INTO anhaenge(id, nachricht_id, name_enc, groesse, mime, "
                      "pfad, zustand, vorschau) VALUES(?, ?, ?, ?, ?, ?, ?, ?)",
                      (anhang_id, nachricht_id,
                       self._ver_text("anhaenge", "name_enc", anhang_id, name),
                       int(groesse or 0), mime, pfad, zustand,
                       self._ver("anhaenge", "vorschau", anhang_id, vorschau)))
        return self.anhang_holen(anhang_id)

    def anhang_zustand_setzen(self, anhang_id: str, zustand: str, pfad: str = None) -> bool:
        if pfad is None:
            return self._aendern("UPDATE anhaenge SET zustand=? WHERE id=?",
                                 (zustand, anhang_id))
        return self._aendern("UPDATE anhaenge SET zustand=?, pfad=? WHERE id=?",
                             (zustand, pfad, anhang_id))

    def anhang_holen(self, anhang_id: str):
        z = self._abfrage("SELECT * FROM anhaenge WHERE id=?", (anhang_id,))
        return self._anhang_dict(z[0]) if z else None

    def anhaenge_fuer_nachricht(self, nachricht_id: str) -> list:
        return [self._anhang_dict(z) for z in self._abfrage(
            "SELECT * FROM anhaenge WHERE nachricht_id=? ORDER BY id", (nachricht_id,))]

    # =================================================================
    #  Communities und Kanäle
    # =================================================================

    def _community_dict(self, z) -> dict:
        schluessel, lesbar = self._sicher(self._ent, "communities", "schluessel_enc",
                                          z["id"], z["schluessel_enc"])
        return {"id": z["id"], "name": z["name"] or "", "icon": z["icon"],
                "schluessel": schluessel, "besitzer_id": z["besitzer_id"],
                "manifest_json": z["manifest_json"], "beigetreten": z["beigetreten"],
                "lesbar": lesbar}

    def community_speichern(self, community_id: str, name: str = None,
                            schluessel: bytes = None, besitzer_id: str = None,
                            manifest_json=None, icon: str = None,
                            beigetreten: int = None) -> dict:
        """Legt eine Community an oder ändert die übergebenen Felder."""
        community_id = (community_id or "").strip()
        if not community_id:
            raise ValueError("Bitte eine Community-ID angeben.")
        if isinstance(manifest_json, (dict, list)):
            manifest_json = json.dumps(manifest_json, ensure_ascii=False, sort_keys=True)
        werte = {"name": name, "icon": icon, "besitzer_id": besitzer_id,
                 "manifest_json": manifest_json, "beigetreten": beigetreten,
                 "schluessel_enc": self._ver("communities", "schluessel_enc",
                                             community_id, schluessel)}
        self._teil_speichern("communities", community_id, werte,
                             {"beigetreten": _jetzt_ms()})
        return self.community_holen(community_id)

    def community_holen(self, community_id: str):
        z = self._abfrage("SELECT * FROM communities WHERE id=?", (community_id,))
        return self._community_dict(z[0]) if z else None

    def communities_alle(self) -> list:
        return [self._community_dict(z) for z in self._abfrage(
            "SELECT * FROM communities ORDER BY beigetreten, id")]

    def community_loeschen(self, community_id: str) -> bool:
        """Löscht die Community mit allen Kanälen und deren Verläufen."""
        ok = self._aendern("DELETE FROM communities WHERE id=?", (community_id,))
        if ok:
            self.wal_zurueckschreiben()
        return ok

    def _kanal_dict(self, z) -> dict:
        return {"id": z["id"], "community_id": z["community_id"], "name": z["name"] or "",
                "position": z["position"], "nur_admins": bool(z["nur_admins"])}

    def kanal_speichern(self, kanal_id: str, community_id: str, name: str = None,
                        position: int = None, nur_admins: bool = None) -> dict:
        kanal_id = (kanal_id or "").strip()
        if not kanal_id:
            raise ValueError("Bitte eine Kanal-ID angeben.")
        werte = {"community_id": community_id, "name": name, "position": position,
                 "nur_admins": None if nur_admins is None else _bool(nur_admins)}
        self._teil_speichern("kanaele", kanal_id, werte,
                             {"name": "", "position": 0, "nur_admins": 0})
        return self.kanal_holen(kanal_id)

    def kanal_holen(self, kanal_id: str):
        z = self._abfrage("SELECT * FROM kanaele WHERE id=?", (kanal_id,))
        return self._kanal_dict(z[0]) if z else None

    def kanaele(self, community_id: str) -> list:
        return [self._kanal_dict(z) for z in self._abfrage(
            "SELECT * FROM kanaele WHERE community_id=? ORDER BY position, name, id",
            (community_id,))]

    def kanal_loeschen(self, kanal_id: str) -> bool:
        """Löscht den Kanal und seine Unterhaltung (gleiche ID)."""
        with self._transaktion() as c:
            c.execute("DELETE FROM unterhaltungen WHERE id=? AND art='kanal'", (kanal_id,))
            ok = c.execute("DELETE FROM kanaele WHERE id=?", (kanal_id,)).rowcount > 0
        if ok:
            self.wal_zurueckschreiben()
        return ok

    # =================================================================
    #  Discord-Stand (bis wohin der Kanal gelesen ist)
    # =================================================================

    def discord_stand_holen(self, kanal_id: str):
        z = self._abfrage("SELECT letzte_nachricht_id FROM discord_stand WHERE kanal_id=?",
                          (str(kanal_id),))
        return z[0]["letzte_nachricht_id"] if z else None

    def discord_stand_setzen(self, kanal_id: str, letzte_nachricht_id,
                             erzwingen: bool = False):
        """Merkt sich die letzte gelesene Discord-Nachricht.

        Discord-IDs wachsen mit der Zeit. Deshalb geht der Stand nur nach
        vorn - eine verspätet verarbeitete ältere Nachricht setzt ihn nicht
        zurück. Mit erzwingen=True wird trotzdem überschrieben.
        """
        neu = str(letzte_nachricht_id)
        with self._transaktion() as c:
            z = c.execute("SELECT letzte_nachricht_id FROM discord_stand WHERE kanal_id=?",
                          (str(kanal_id),)).fetchone()
            if z and not erzwingen:
                alt = z["letzte_nachricht_id"]
                if alt and alt.isdigit() and neu.isdigit() and int(neu) <= int(alt):
                    return
            c.execute("INSERT INTO discord_stand(kanal_id, letzte_nachricht_id) "
                      "VALUES(?, ?) ON CONFLICT(kanal_id) DO UPDATE SET "
                      "letzte_nachricht_id=excluded.letzte_nachricht_id",
                      (str(kanal_id), neu))
