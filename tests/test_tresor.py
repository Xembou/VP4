# -*- coding: utf-8 -*-
"""Prüfungen für Tresor (VP4K4), DPAPI-Weg, Datenbank und die Übernahme aus 4.x.

Wird von test_vp4.py über pruefen(R, hilfen) aufgerufen. Alles läuft in
Wegwerf-Ordnern - der echte vp4_daten-Ordner wird nie angefasst.
"""

import ast
import base64
import contextlib
import hashlib
import json
import os
import sys
import tempfile
import threading
import time
import traceback
from pathlib import Path

from kern import speicher
from kern.speicher import FalschesPasswortError, KeyStore, KeyStoreLockedError
from kern.tresor import (DPAPI, DPAPIAttrappe, Tresor, TresorBeschaedigtError,
                         TresorGesperrtError, datenschluessel_merken,
                         datenschluessel_vergessen, mit_windows_entsperren)
from kern.datenbank import Datenbank
from kern import migration


# Lage der Felder in einer VP4K4-Datei (Argon2id-Kopf):
#   Marke 0-4 | KDF 5 | Zeit 6-9 | Speicher 10-13 | Parallel 14 | Salt 15-30
#   | Nonce Hülle 31-42 | umhüllter Schlüssel 43-90 | Nonce Inhalt 91-102 | Inhalt
_KDF = 5
_ZEIT = 6
_SPEICHER = 10
_SALT = 15
_HUELLE = 43
_INHALT = 91


def _wirft(funktion, art, *argumente) -> bool:
    try:
        funktion(*argumente)
    except art:
        return True
    except Exception:
        return False
    return False


def _gekippt(roh: bytes, stelle: int, maske: int = 0x01) -> bytes:
    veraendert = bytearray(roh)
    veraendert[stelle] ^= maske
    return bytes(veraendert)


@contextlib.contextmanager
def _ohne_echten_datenordner():
    """KeyStore._save() legt nebenbei den echten vp4_daten-Ordner an. Für die
    Dauer der Prüfung wird das abgeschaltet - die Testdateien liegen ja
    ohnehin woanders."""
    vorher = speicher.ordner_anlegen
    speicher.ordner_anlegen = lambda: None
    try:
        yield
    finally:
        speicher.ordner_anlegen = vorher


def _altbestand():
    """Holt den fest einkodierten VP4K2-Speicher aus test_vp4.py.

    Die Blobs werden NICHT kopiert: erst aus dem laufenden Hauptmodul
    gelesen, sonst aus dem Quelltext von test_vp4.py (nur gelesen, per ast).
    Rückgabe (blob_b64, passwort) oder None.
    """
    namen = ("ALT_KEYSTORE_B64", "ALT_KEYSTORE_PASSWORT")
    for modul in (sys.modules.get("__main__"), sys.modules.get("test_vp4")):
        werte = [getattr(modul, n, None) for n in namen]
        if all(werte):
            return tuple(werte)
    pfad = Path(__file__).resolve().parent.parent / "test_vp4.py"
    try:
        baum = ast.parse(pfad.read_text(encoding="utf-8"))
    except (OSError, SyntaxError):
        return None
    gefunden = {}
    for knoten in baum.body:
        if isinstance(knoten, ast.Assign):
            for ziel in knoten.targets:
                if isinstance(ziel, ast.Name) and ziel.id in namen:
                    gefunden[ziel.id] = ast.literal_eval(knoten.value)
    if all(n in gefunden for n in namen):
        return gefunden[namen[0]], gefunden[namen[1]]
    return None


def _hash(pfad: Path) -> str:
    return hashlib.sha256(pfad.read_bytes()).hexdigest()


def _db_rohbytes(pfad: Path) -> bytes:
    """Hauptdatei samt WAL und Shared-Memory-Datei - alles, was auf der
    Platte liegt."""
    roh = b""
    for endung in ("", "-wal", "-shm"):
        teil = Path(str(pfad) + endung)
        if teil.exists():
            roh += teil.read_bytes()
    return roh


# =============================================================================
#  Tresor
# =============================================================================

def _tresor_grundlagen(R, ordner: Path):
    pfad = ordner / "tresor.enc"
    t = Tresor(pfad)
    R.pruefe("Tresor: gibt es vor dem Anlegen noch nicht", not t.exists())
    R.pruefe("Tresor: leeres Master-Passwort wird abgelehnt",
             _wirft(t.create, ValueError, ""))
    t.create("Tresor-Passwort 1")
    roh = pfad.read_bytes()
    R.pruefe("Tresor: neue Datei trägt die Marke VP4K4 und Argon2id",
             roh[:5] == b"VP4K4" and roh[_KDF] == 2, f"Anfang: {roh[:6]!r}")
    R.pruefe("Tresor: frisch angelegt ist er offen, Datenschlüssel 32 Byte",
             t.is_unlocked() and len(t.datenschluessel) == 32)
    R.pruefe("Tresor: zweites Anlegen über einen bestehenden wird verweigert",
             _wirft(Tresor(pfad).create, FileExistsError, "anderes"))

    t.add_key("Mein AES", "AES", "c2NobMO8c3NlbA==", "Notiz")
    t.identitaet_bytes = b"\x01\x02identitaet\xff"
    t.paar_schluessel_setzen("ABCDE-12345", b"P" * 32)
    t.community_schluessel_setzen("C-1", b"C" * 32)
    t.extra_setzen("einstellung", {"a": 1})
    dk = t.datenschluessel
    t.lock()
    R.pruefe("Tresor: gesperrt gibt es keinen Datenschlüssel",
             _wirft(lambda: t.datenschluessel, TresorGesperrtError))
    R.pruefe("Tresor: gesperrter Zugriff ist auch ein KeyStoreLockedError",
             _wirft(t.list_keys, KeyStoreLockedError))

    neu = Tresor(pfad)
    R.pruefe("Tresor: falsches Passwort wird abgelehnt",
             _wirft(neu.unlock, FalschesPasswortError, "falsch"))
    R.pruefe("Tresor: leeres Passwort wird abgelehnt",
             _wirft(neu.unlock, FalschesPasswortError, ""))
    R.pruefe("Tresor: nach falschem Passwort bleibt er gesperrt", not neu.is_unlocked())
    neu.unlock("Tresor-Passwort 1")
    R.pruefe("Tresor: richtiges Passwort öffnet, gleicher Datenschlüssel",
             neu.is_unlocked() and neu.datenschluessel == dk)
    R.pruefe("Tresor: Schlüsselring kommt vollständig zurück",
             [(k["label"], k["typ"], k["wert"], k["meta"]) for k in neu.list_keys()]
             == [("Mein AES", "AES", "c2NobMO8c3NlbA==", "Notiz")])
    R.pruefe("Tresor: Identität (Bytes) kommt unverändert zurück",
             neu.identitaet_bytes == b"\x01\x02identitaet\xff")
    R.pruefe("Tresor: Paar- und Community-Schlüssel kommen zurück",
             neu.paar_schluessel("ABCDE-12345") == b"P" * 32
             and neu.community_schluessel("C-1") == b"C" * 32
             and neu.paar_schluessel("UNBEKANNT") is None)
    R.pruefe("Tresor: Zusatzfach kommt zurück", neu.extra_holen("einstellung") == {"a": 1})
    R.pruefe("Tresor: nichts davon steht im Klartext in der Datei",
             b"Mein AES" not in pfad.read_bytes()
             and b"ABCDE-12345" not in pfad.read_bytes())
    R.pruefe("Tresor: keine Nebendatei bleibt liegen",
             sorted(p.name for p in ordner.iterdir()) == ["tresor.enc"])
    return pfad


def _tresor_manipulation(R, pfad: Path, hilfen):
    echt = pfad.read_bytes()
    faelle = [
        ("Zeit-Parameter unmöglich (vor dem Rechnen abgelehnt)", _ZEIT, 0x80),
        ("Speicher-Parameter unmöglich (vor dem Rechnen abgelehnt)", _SPEICHER, 0xFF),
        ("Zeit-Parameter möglich, aber anders (3 -> 2)", _ZEIT + 3, 0x01),
        ("KDF-Kennung verändert", _KDF, 0x03),
        ("Salt verändert", _SALT + 4, 0x01),
        ("umhüllter Datenschlüssel verändert", _HUELLE + 7, 0x01),
        ("Nonce der Hülle verändert", _HUELLE - 1, 0x01),
    ]
    with tempfile.TemporaryDirectory() as ordner:
        for name, stelle, maske in faelle:
            kaputt = Path(ordner) / "kaputt.enc"
            kaputt.write_bytes(_gekippt(echt, stelle, maske))
            start = time.perf_counter()
            abgelehnt = _wirft(Tresor(kaputt).unlock,
                               (FalschesPasswortError, ValueError), "Tresor-Passwort 1")
            dauer = time.perf_counter() - start
            R.pruefe(f"Tresor-Kopf: {name} -> abgelehnt in unter 1 s",
                     abgelehnt and dauer < 1.0, f"abgelehnt={abgelehnt}, {dauer:.2f} s")

        kaputt = Path(ordner) / "kaputt.enc"
        kaputt.write_bytes(_gekippt(echt, len(echt) - 3))
        R.pruefe("Tresor: veränderter Inhalt -> TresorBeschaedigtError",
                 _wirft(Tresor(kaputt).unlock, TresorBeschaedigtError, "Tresor-Passwort 1"))
        kaputt.write_bytes(echt[:60])
        R.pruefe("Tresor: abgeschnittene Datei -> ValueError",
                 hilfen["wirft_valueerror"](Tresor(kaputt).unlock, "Tresor-Passwort 1"))
        kaputt.write_bytes(b"VP4K3" + echt[5:])
        R.pruefe("Tresor: fremde Marke -> ValueError",
                 hilfen["wirft_valueerror"](Tresor(kaputt).unlock, "Tresor-Passwort 1"))
        R.pruefe("Tresor: fehlende Datei -> FileNotFoundError",
                 _wirft(Tresor(Path(ordner) / "gibtsnicht").unlock, FileNotFoundError, "x"))


def _tresor_passwortwechsel(R, pfad: Path):
    t = Tresor(pfad)
    t.unlock("Tresor-Passwort 1")
    dk = t.datenschluessel
    vorher = pfad.read_bytes()

    R.pruefe("Passwortwechsel: falsches altes Passwort wird abgelehnt",
             _wirft(t.change_password, FalschesPasswortError, "falsch", "neu"))
    R.pruefe("Passwortwechsel: nach Ablehnung ist die Datei unverändert",
             pfad.read_bytes() == vorher)
    R.pruefe("Passwortwechsel: leeres neues Passwort wird abgelehnt",
             _wirft(t.change_password, ValueError, "Tresor-Passwort 1", ""))

    t.change_password("Tresor-Passwort 1", "Neues Passwort äöü")
    nachher = pfad.read_bytes()
    R.pruefe("Passwortwechsel: Inhalt bleibt Byte für Byte gleich (nur neu umhüllt)",
             nachher[_INHALT:] == vorher[_INHALT:])
    R.pruefe("Passwortwechsel: Hülle und Salt sind neu",
             nachher[_SALT:_INHALT] != vorher[_SALT:_INHALT])
    R.pruefe("Passwortwechsel: Datenschlüssel bleibt derselbe", t.datenschluessel == dk)

    neu = Tresor(pfad)
    R.pruefe("Passwortwechsel: altes Passwort gilt nicht mehr",
             _wirft(neu.unlock, FalschesPasswortError, "Tresor-Passwort 1"))
    neu.unlock("Neues Passwort äöü")
    R.pruefe("Passwortwechsel: neues Passwort öffnet, alles noch da",
             neu.get_key("Mein AES") is not None
             and neu.paar_schluessel("ABCDE-12345") == b"P" * 32)

    # Speichern packt den Inhalt neu (frisches Nonce), lässt die Hülle stehen.
    vorher = pfad.read_bytes()
    neu.save()
    nachher = pfad.read_bytes()
    R.pruefe("Speichern: Hülle bleibt, Inhalt bekommt ein frisches Nonce",
             nachher[:_INHALT] == vorher[:_INHALT]
             and nachher[_INHALT:_INHALT + 12] != vorher[_INHALT:_INHALT + 12])


def _tresor_schluesselring(R, ordner: Path, hilfen):
    t = Tresor(ordner / "ring.enc")
    t.create("ring")
    t.schluessel_hinzufuegen("Eins", "AES", "w1")
    t.add_key("  Zwei  ", "RSA", {"privat": "p", "oeffentlich": "o"})
    R.pruefe("Schlüsselring: Name wird wie beim KeyStore getrimmt",
             t.get_key("Zwei") is not None)
    R.pruefe("Schlüsselring: doppelter Name -> ValueError",
             hilfen["wirft_valueerror"](t.add_key, "Eins", "AES", "x"))
    R.pruefe("Schlüsselring: leerer Name -> ValueError",
             hilfen["wirft_valueerror"](t.add_key, "  ", "AES", "x"))
    R.pruefe("Schlüsselring: Eintrag hat dieselben Felder wie beim KeyStore",
             set(t.get_key("Eins")) == {"label", "typ", "wert", "meta", "erstellt"})
    kopie = t.list_keys()
    kopie[0]["wert"] = "verändert"
    R.pruefe("Schlüsselring: list_keys gibt Kopien heraus",
             t.get_key("Eins")["wert"] == "w1")
    t.replace_all([{"label": "Eins", "typ": "AES", "wert": "neu", "meta": "",
                    "erstellt": "2026-01-01 00:00"},
                   {"label": "Drei", "typ": "AES", "wert": "w3", "meta": "",
                    "erstellt": "2026-01-01 00:00"}])
    R.pruefe("Schlüsselring: replace_all überschreibt gleiche Namen, ergänzt neue",
             [k["label"] for k in t.list_keys()] == ["Eins", "Zwei", "Drei"]
             and t.get_key("Eins")["wert"] == "neu")
    t.delete_key("Zwei")
    R.pruefe("Schlüsselring: löschen", t.get_key("Zwei") is None)

    t.identitaet_setzen(None)
    R.pruefe("Identität: None setzt zurück", t.identitaet_holen() is None)
    R.pruefe("Paar-Schlüssel: Text statt Bytes -> ValueError",
             hilfen["wirft_valueerror"](t.paar_schluessel_setzen, "X", "kein bytes"))
    t.paar_schluessel_setzen("X", b"x" * 32)
    t.community_schluessel_setzen("C", b"c" * 32)
    R.pruefe("Paar-/Community-IDs werden aufgelistet",
             t.paar_ids() == ["X"] and t.community_ids() == ["C"])
    R.pruefe("Paar-Schlüssel löschen",
             t.paar_schluessel_loeschen("X") and t.paar_schluessel("X") is None
             and not t.paar_schluessel_loeschen("X"))
    R.pruefe("Community-Schlüssel löschen",
             t.community_schluessel_loeschen("C") and t.community_schluessel("C") is None)

    frisch = Tresor(ordner / "ring.enc")
    frisch.unlock("ring")
    R.pruefe("Schlüsselring: jede Änderung ist sofort gespeichert",
             [k["label"] for k in frisch.list_keys()] == ["Eins", "Drei"])


def _tresor_altbestand(R, ordner: Path):
    alt = _altbestand()
    if alt is None:
        print("  (übersprungen: alter VP4K2-Speicher aus test_vp4.py nicht erreichbar)")
        return
    blob_b64, passwort = alt
    alt_pfad = ordner / "alt" / "schluessel.enc"
    alt_pfad.parent.mkdir()
    alt_pfad.write_bytes(base64.b64decode(blob_b64))
    vorher = _hash(alt_pfad)

    ziel = ordner / "neu" / "tresor.enc"
    R.pruefe("Altbestand: falsches Passwort -> FalschesPasswortError, kein neuer Tresor",
             _wirft(Tresor.aus_altem_speicher, FalschesPasswortError,
                    alt_pfad, ziel, "falsch") and not ziel.exists())
    t = Tresor.aus_altem_speicher(alt_pfad, ziel, passwort)
    R.pruefe("Altbestand: VP4K2 wird zu VP4K4 mit demselben Schlüsselring",
             ziel.read_bytes()[:5] == b"VP4K4"
             and [k["label"] for k in t.list_keys()] == ["Alter AES"],
             f"gelesen: {[k['label'] for k in t.list_keys()]}")
    frisch = Tresor(ziel)
    frisch.unlock(passwort)
    R.pruefe("Altbestand: das alte Passwort öffnet den neuen Tresor",
             frisch.get_key("Alter AES") is not None)
    R.pruefe("Altbestand: die alte Datei bleibt Byte für Byte unverändert",
             _hash(alt_pfad) == vorher)
    R.pruefe("Altbestand: neben der alten Datei entsteht nichts",
             [p.name for p in alt_pfad.parent.iterdir()] == ["schluessel.enc"])


def _dpapi(R, ordner: Path):
    if sys.platform != "win32":
        R.pruefe("DPAPI: ohne Windows ist sie nicht verfügbar", not DPAPI().verfuegbar())
        R.pruefe("DPAPI: Entsperren über echtes DPAPI liefert hier False statt Fehler",
                 mit_windows_entsperren(Tresor(ordner / "x.enc"), DPAPI(),
                                        ordner / "x.dpapi") is False)

    attrappe = DPAPIAttrappe()
    pfad = ordner / "dp.enc"
    dp = ordner / "tresor.dpapi"
    t = Tresor(pfad)
    t.create("dpapi-pw")
    t.add_key("K", "AES", "wert")

    R.pruefe("DPAPI: ohne Datei -> False", mit_windows_entsperren(Tresor(pfad), attrappe, dp) is False)
    gesperrt = Tresor(pfad)
    R.pruefe("DPAPI: merken bei gesperrtem Tresor wird abgelehnt",
             _wirft(datenschluessel_merken, Exception, gesperrt, attrappe, dp)
             and not dp.exists())
    R.pruefe("DPAPI: merken ohne verfügbares DPAPI -> ValueError",
             _wirft(datenschluessel_merken, ValueError, t, DPAPIAttrappe(False), dp))

    datenschluessel_merken(t, attrappe, dp)
    R.pruefe("DPAPI: Datei wird angelegt", dp.exists())
    still = Tresor(pfad)
    R.pruefe("DPAPI: Tresor öffnet still ohne Passwort",
             mit_windows_entsperren(still, attrappe, dp) is True
             and still.get_key("K")["wert"] == "wert")
    still.add_key("Nach DPAPI", "AES", "w2")
    mit_pw = Tresor(pfad)
    mit_pw.unlock("dpapi-pw")
    R.pruefe("DPAPI: Speichern nach stillem Öffnen, Passwort gilt weiter",
             mit_pw.get_key("Nach DPAPI") is not None)

    mit_pw.change_password("dpapi-pw", "anderes-pw")
    R.pruefe("DPAPI: bleibt nach Passwortwechsel gültig",
             mit_windows_entsperren(Tresor(pfad), attrappe, dp) is True)
    R.pruefe("DPAPI: abgeschaltetes DPAPI -> False",
             mit_windows_entsperren(Tresor(pfad), DPAPIAttrappe(False), dp) is False)

    echt = dp.read_bytes()
    for name, inhalt in [("Müll", b"\x00\xffkaputt"), ("leer", b""),
                         ("nur Marke", b"VP4W1"),
                         ("verkürzter Schlüssel", echt[:-5]),
                         ("gekipptes Bit im Schlüssel", _gekippt(echt, len(echt) - 1))]:
        dp.write_bytes(inhalt)
        neu = Tresor(pfad)
        R.pruefe(f"DPAPI: kaputte Datei ({name}) -> False, Tresor bleibt zu",
                 mit_windows_entsperren(neu, attrappe, dp) is False and not neu.is_unlocked())
    dp.write_bytes(echt)

    anderer = Tresor(ordner / "anderer.enc")
    anderer.create("x")
    R.pruefe("DPAPI: Schlüssel eines anderen Tresors -> False",
             mit_windows_entsperren(anderer.__class__(ordner / "anderer.enc"),
                                    attrappe, dp) is False)
    R.pruefe("DPAPI: Tresor fehlt -> False",
             mit_windows_entsperren(Tresor(ordner / "fehlt.enc"), attrappe, dp) is False)

    R.pruefe("DPAPI: vergessen löscht die Datei",
             datenschluessel_vergessen(dp) is True and not dp.exists())
    R.pruefe("DPAPI: zweites Vergessen ist harmlos", datenschluessel_vergessen(dp) is False)
    R.pruefe("DPAPI: nach dem Vergessen -> False",
             mit_windows_entsperren(Tresor(pfad), attrappe, dp) is False)


# =============================================================================
#  Datenbank
# =============================================================================

def _datenbank(R, ordner: Path, hilfen):
    dk = os.urandom(32)
    pfad = ordner / "vp4.db"
    db = Datenbank(pfad, dk, eigene_id="ICH-0001")
    try:
        R.pruefe("Datenbank: läuft im WAL-Modus",
                 db._conn.execute("PRAGMA journal_mode").fetchone()[0] == "wal")
        R.pruefe("Datenbank: Fremdschlüssel sind eingeschaltet",
                 db._conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1)
        R.pruefe("Datenbank: Schema-Version 1", db.schema_version() == 1)
        tabellen = {z[0] for z in db._conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")}
        R.pruefe("Datenbank: alle Tabellen aus dem Bauplan sind da",
                 {"kontakte", "unterhaltungen", "nachrichten", "reaktionen", "anhaenge",
                  "communities", "kanaele", "discord_stand"} <= tabellen)
        R.pruefe("Datenbank: Index auf (unterhaltung_id, ts)",
                 db._conn.execute("SELECT 1 FROM sqlite_master WHERE type='index' "
                                  "AND name='nachrichten_unterhaltung_ts'").fetchone()
                 is not None)

        # --- Kontakte --------------------------------------------------------
        k = db.kontakt_speichern("MAXI-0002", spitzname="Geheimname Maximilian",
                                 karte_json={"ik": "abc"}, status="anfrage_rein")
        R.pruefe("Kontakt: anlegen und holen",
                 k["spitzname"] == "Geheimname Maximilian" and k["status"] == "anfrage_rein"
                 and k["karte"] == {"ik": "abc"} and not k["verifiziert"])
        db.kontakt_speichern("MAXI-0002", status="ok")
        R.pruefe("Kontakt: Teiländerung lässt den Rest stehen",
                 db.kontakt_holen("MAXI-0002")["spitzname"] == "Geheimname Maximilian"
                 and db.kontakt_holen("MAXI-0002")["status"] == "ok")
        R.pruefe("Kontakt: verifiziert und Schlüsselwechsel setzen",
                 db.kontakt_verifiziert_setzen("MAXI-0002", True)
                 and db.kontakt_schluessel_neu_setzen("MAXI-0002", True)
                 and db.kontakt_holen("MAXI-0002")["verifiziert"]
                 and db.kontakt_holen("MAXI-0002")["schluessel_neu"])
        R.pruefe("Kontakt: ungültiger Status wird abgelehnt (CHECK)",
                 hilfen["wirft_valueerror"](db.kontakt_status_setzen, "MAXI-0002", "freund")
                 and db.kontakt_holen("MAXI-0002")["status"] == "ok")
        db.kontakt_speichern("LISA-0003", spitzname="Lisa", status="blockiert")
        R.pruefe("Kontakt: alle / nach Status",
                 len(db.kontakte_alle()) == 2
                 and [x["id"] for x in db.kontakte_alle("blockiert")] == ["LISA-0003"])
        R.pruefe("Kontakt: löschen",
                 db.kontakt_loeschen("LISA-0003") and db.kontakt_holen("LISA-0003") is None)

        # --- Unterhaltungen und Nachrichten ---------------------------------
        u = db.unterhaltung_anlegen_oder_holen("MAXI-0002", "dm", titel="Maxi")
        db.unterhaltung_anlegen_oder_holen("MAXI-0002", "dm", titel="anders")
        R.pruefe("Unterhaltung: anlegen_oder_holen verändert Bestehendes nicht",
                 u["art"] == "dm" and db.unterhaltung_holen("MAXI-0002")["titel"] == "Maxi")
        R.pruefe("Unterhaltung: ungültige Art wird abgelehnt (CHECK)",
                 hilfen["wirft_valueerror"](db.unterhaltung_anlegen_oder_holen, "X", "chat"))

        geheim = "Streng geheime Nachricht über den Schatz im Garten"
        R.pruefe("Nachricht: einfügen",
                 db.nachricht_einfuegen("n-geheim", "MAXI-0002", "MAXI-0002", 1000, geheim))
        R.pruefe("Nachricht: doppelte ID -> False, Inhalt bleibt",
                 db.nachricht_einfuegen("n-geheim", "MAXI-0002", "MAXI-0002", 1000, "anders")
                 is False and db.nachricht_holen("n-geheim")["inhalt"] == geheim)
        db.entwurf_setzen("MAXI-0002", "Entwurf über das Versteck")
        R.pruefe("Entwurf: setzen und holen",
                 db.entwurf_holen("MAXI-0002") == "Entwurf über das Versteck")
        db.anhang_anlegen("a-1", "n-geheim", "urlaubsfoto_geheim.jpg", 1234, "image/jpeg",
                          "anhaenge/0f3a", "fertig", vorschau=b"VORSCHAU-PIXELDATEN")
        db.community_speichern("C-1", name="Clubhaus", schluessel=b"K" * 32,
                               besitzer_id="ICH-0001")

        roh = _db_rohbytes(pfad)
        db.wal_zurueckschreiben()
        roh += _db_rohbytes(pfad)
        R.pruefe("Ruhezustand: Nachrichtentext steht nirgends im Klartext",
                 geheim.encode() not in roh and b"Schatz" not in roh)
        R.pruefe("Ruhezustand: Spitzname steht nirgends im Klartext",
                 "Geheimname Maximilian".encode() not in roh)
        R.pruefe("Ruhezustand: Entwurf, Anhangname, Vorschau, Community-Schlüssel auch nicht",
                 "Versteck".encode() not in roh and b"urlaubsfoto" not in roh
                 and b"VORSCHAU-PIXELDATEN" not in roh and b"K" * 32 not in roh)

        # Ein verschlüsselter Wert lässt sich nicht in eine andere Zeile kopieren.
        db.nachricht_einfuegen("n-zweite", "MAXI-0002", "ICH-0001", 1001, "harmlos")
        with db._lock:
            db._conn.execute(
                "UPDATE nachrichten SET inhalt_enc=(SELECT inhalt_enc FROM nachrichten "
                "WHERE id='n-geheim') WHERE id='n-zweite'")
        umkopiert = db.nachricht_holen("n-zweite")
        R.pruefe("AAD: umkopierter Geheimtext wird als unlesbar erkannt",
                 umkopiert["inhalt"] is None and umkopiert["lesbar"] is False)
        db.nachricht_bearbeiten("n-zweite", "harmlos, repariert", ts=1500)
        R.pruefe("Nachricht: bearbeiten setzt Inhalt und Zeitpunkt",
                 db.nachricht_holen("n-zweite")["inhalt"] == "harmlos, repariert"
                 and db.nachricht_holen("n-zweite")["bearbeitet"] == 1500)

        # --- Fremdschlüssel ----------------------------------------------------
        R.pruefe("Fremdschlüssel: Nachricht in unbekannte Unterhaltung -> abgelehnt",
                 hilfen["wirft_valueerror"](db.nachricht_einfuegen, "n-x", "GIBTS-NICHT",
                                            "A", 1, "x")
                 and db.nachricht_holen("n-x") is None)
        R.pruefe("Fremdschlüssel: Reaktion auf unbekannte Nachricht -> abgelehnt",
                 hilfen["wirft_valueerror"](db.reaktion_setzen, "gibts-nicht", "A", "👍"))

        # --- Seiten, neueste zuerst -----------------------------------------
        db.unterhaltung_anlegen_oder_holen("SEITEN", "gruppe")
        for i in range(120):
            # Je zwei Nachrichten teilen sich einen Zeitstempel.
            db.nachricht_einfuegen(f"s-{i:03d}", "SEITEN", "MAXI-0002", 10_000 + i // 2,
                                   f"Nachricht {i}")
        erste = db.nachrichten_seite("SEITEN")
        R.pruefe("Seiten: 50 Stück, neueste zuerst",
                 len(erste) == 50 and erste[0]["id"] == "s-119"
                 and all(a["ts"] >= b["ts"] for a, b in zip(erste, erste[1:])))
        gesehen = [n["id"] for n in erste]
        while True:
            letzte = gesehen and db.nachricht_holen(gesehen[-1])
            seite = db.nachrichten_seite("SEITEN", vor_ts=letzte["ts"], vor_id=letzte["id"])
            if not seite:
                break
            gesehen += [n["id"] for n in seite]
        R.pruefe("Seiten: durchblättern verliert bei gleichen Zeitstempeln nichts",
                 gesehen == [f"s-{i:03d}" for i in range(119, -1, -1)],
                 f"{len(gesehen)} gesehen")
        R.pruefe("Seiten: vor_ts allein liefert nur Älteres",
                 all(n["ts"] < 10_030 for n in db.nachrichten_seite("SEITEN", vor_ts=10_030)))

        # --- Suche ------------------------------------------------------------
        db.nachricht_einfuegen("q-1", "SEITEN", "A", 20_000, "Großer ÄRGER im Büro")
        db.nachricht_einfuegen("q-2", "SEITEN", "A", 20_001,
                               {"text": "kein ärger mehr", "zitat": "x"})
        db.nachricht_einfuegen("q-3", "MAXI-0002", "A", 20_002, "ärger anderswo")
        treffer = [n["id"] for n in db.nachrichten_suchen("SEITEN", "ärger")]
        R.pruefe("Suche: ohne Groß-/Kleinschreibung, auch Umlaute, neueste zuerst",
                 treffer == ["q-2", "q-1"], f"gefunden: {treffer}")
        R.pruefe("Suche: in allen Unterhaltungen",
                 len(db.nachrichten_suchen(None, "ÄRGER")) == 3)
        R.pruefe("Suche: leerer Suchtext findet nichts", db.nachrichten_suchen("SEITEN", "") == [])

        # --- Reaktionen ---------------------------------------------------------
        db.reaktion_setzen("q-1", "MAXI-0002", "👍", ts=1)
        db.reaktion_setzen("q-1", "LISA-0003", "😂", ts=2)
        db.reaktion_setzen("q-1", "MAXI-0002", "👍", ts=3)   # doppelt -> nur einmal
        r = db.reaktionen_fuer_nachrichten(["q-1", "q-2"])
        R.pruefe("Reaktionen: setzen, doppelt zählt einmal, Liste je Nachricht",
                 len(r["q-1"]) == 2 and r["q-2"] == [])
        R.pruefe("Reaktionen: entfernen",
                 db.reaktion_entfernen("q-1", "LISA-0003", "😂")
                 and len(db.reaktionen_fuer_nachrichten(["q-1"])["q-1"]) == 1)

        # --- Anhänge --------------------------------------------------------------
        a = db.anhang_holen("a-1")
        R.pruefe("Anhang: Name und Vorschau kommen entschlüsselt zurück",
                 a["name"] == "urlaubsfoto_geheim.jpg" and a["vorschau"] == b"VORSCHAU-PIXELDATEN"
                 and a["groesse"] == 1234)
        R.pruefe("Anhang: Zustand setzen",
                 db.anhang_zustand_setzen("a-1", "geladen", "anhaenge/neu")
                 and db.anhang_holen("a-1")["zustand"] == "geladen"
                 and len(db.anhaenge_fuer_nachricht("n-geheim")) == 1)

        # --- Löschen wischt den Inhalt ------------------------------------------
        alter_blob = bytes(db._conn.execute(
            "SELECT inhalt_enc FROM nachrichten WHERE id='n-geheim'").fetchone()[0])
        db.reaktion_setzen("n-geheim", "MAXI-0002", "❤️")
        pfade = db.nachricht_als_geloescht_markieren("n-geheim")
        n = db.nachricht_holen("n-geheim")
        R.pruefe("Löschen: Platzhalter bleibt, Inhalt ist weg",
                 n is not None and n["geloescht"] and n["inhalt"] is None)
        R.pruefe("Löschen: Reaktionen und Anhangname sind weg, Pfad kommt zurück",
                 db.reaktionen_fuer_nachrichten(["n-geheim"])["n-geheim"] == []
                 and db.anhang_holen("a-1")["name"] == ""
                 and db.anhang_holen("a-1")["vorschau"] is None
                 and pfade == ["anhaenge/neu"])
        # Ohne secure_delete und das sofortige Zurückschreiben des WAL stünde
        # der alte Geheimtext hier noch in einer freien Seite (ausprobiert:
        # jedes von beiden allein reicht nicht).
        # In einer frischen Datei, weil sich der Rest dort leicht zufällig
        # über die alten Bytes legt und die Prüfung dann nichts mehr beweist.
        with Datenbank(ordner / "wischen.db", dk) as wdb:
            wdb.unterhaltung_anlegen_oder_holen("WISCHEN", "dm")
            for i in range(30):
                wdb.nachricht_einfuegen(f"w-{i}", "WISCHEN", "A", i, "lang " * 40 + str(i))
            wisch_blob = bytes(wdb._conn.execute(
                "SELECT inhalt_enc FROM nachrichten WHERE id='w-5'").fetchone()[0])
            vorher_drin = wisch_blob in _db_rohbytes(ordner / "wischen.db")
            wdb.nachricht_als_geloescht_markieren("w-5")
            R.pruefe("Löschen: der alte Geheimtext steht nicht mehr in der Datei",
                     vorher_drin and wisch_blob not in _db_rohbytes(ordner / "wischen.db")
                     and alter_blob not in _db_rohbytes(pfad))
        R.pruefe("Löschen: gelöschte Nachricht lässt sich nicht bearbeiten",
                 db.nachricht_bearbeiten("n-geheim", "wieder da?") is False)

        # --- Ungelesen --------------------------------------------------------------
        db.unterhaltung_anlegen_oder_holen("UNGELESEN", "dm")
        db.nachricht_einfuegen("g-1", "UNGELESEN", "MAXI-0002", 30_000, "eins")
        db.nachricht_einfuegen("g-2", "UNGELESEN", "ICH-0001", 30_001, "meine")
        db.nachricht_einfuegen("g-3", "UNGELESEN", "MAXI-0002", 30_002, "drei")
        db.nachricht_einfuegen("g-4", "UNGELESEN", "MAXI-0002", 30_003, "vier")
        db.nachricht_als_geloescht_markieren("g-4")

        def eintrag(kennung):
            return next(x for x in db.unterhaltungen_alle() if x["id"] == kennung)

        e = eintrag("UNGELESEN")
        R.pruefe("Ungelesen: eigene und gelöschte zählen nicht",
                 e["ungelesen"] == 2, f"ungelesen={e['ungelesen']}")
        R.pruefe("Ungelesen: letzte Nachricht ist dabei",
                 e["letzte_nachricht"]["id"] == "g-4" and e["letzte_nachricht"]["geloescht"])
        db.gelesen_bis_setzen("UNGELESEN", 30_000)
        R.pruefe("Ungelesen: gelesen_bis zählt herunter", eintrag("UNGELESEN")["ungelesen"] == 1)
        db.gelesen_bis_setzen("UNGELESEN", 10)
        R.pruefe("Ungelesen: gelesen_bis geht nie zurück",
                 eintrag("UNGELESEN")["ungelesen"] == 1)
        db.angeheftet_setzen("MAXI-0002", True)
        db.stumm_setzen("MAXI-0002", True)
        liste = db.unterhaltungen_alle()
        R.pruefe("Unterhaltungen: Angeheftete zuerst, dann nach letzter Nachricht",
                 [x["id"] for x in liste] == ["MAXI-0002", "UNGELESEN", "SEITEN"]
                 and liste[0]["stumm"], f"{[x['id'] for x in liste]}")

        # --- Communities und Kanäle --------------------------------------------
        db.kanal_speichern("K-2", "C-1", "allgemein", position=2)
        db.kanal_speichern("K-1", "C-1", "ankündigungen", position=1, nur_admins=True)
        R.pruefe("Kanäle: nach Position, nur_admins",
                 [x["id"] for x in db.kanaele("C-1")] == ["K-1", "K-2"]
                 and db.kanal_holen("K-1")["nur_admins"])
        R.pruefe("Community: Schlüssel kommt als Bytes zurück",
                 db.community_holen("C-1")["schluessel"] == b"K" * 32
                 and len(db.communities_alle()) == 1)
        R.pruefe("Fremdschlüssel: Kanal ohne Community -> abgelehnt",
                 hilfen["wirft_valueerror"](db.kanal_speichern, "K-x", "C-gibtsnicht", "x"))
        db.unterhaltung_anlegen_oder_holen("K-2", "kanal", community_id="C-1")
        db.nachricht_einfuegen("k-n", "K-2", "MAXI-0002", 40_000, "im Kanal")
        db.reaktion_setzen("k-n", "A", "👍")
        R.pruefe("Kanal löschen nimmt seine Unterhaltung mit",
                 db.kanal_loeschen("K-2") and db.unterhaltung_holen("K-2") is None
                 and db.nachricht_holen("k-n") is None)
        db.unterhaltung_anlegen_oder_holen("K-1", "kanal", community_id="C-1")
        db.nachricht_einfuegen("k-m", "K-1", "MAXI-0002", 40_001, "Ankündigung")
        R.pruefe("Fremdschlüssel: Community löschen nimmt Kanäle, Verlauf und Reaktionen mit",
                 db.community_loeschen("C-1") and db.kanaele("C-1") == []
                 and db.unterhaltung_holen("K-1") is None and db.nachricht_holen("k-m") is None)
        anzahl_vorher = len(db.nachrichten_seite("UNGELESEN", anzahl=100))
        R.pruefe("Fremdschlüssel: Unterhaltung löschen nimmt Nachrichten mit",
                 anzahl_vorher == 4 and db.unterhaltung_loeschen("UNGELESEN")
                 and db.nachricht_holen("g-1") is None
                 and db._conn.execute("SELECT COUNT(*) FROM nachrichten WHERE "
                                      "unterhaltung_id='UNGELESEN'").fetchone()[0] == 0)

        # --- Discord-Stand ----------------------------------------------------------
        db.discord_stand_setzen("123", "1000")
        db.discord_stand_setzen("123", "999")
        R.pruefe("Discord-Stand: geht nur vorwärts", db.discord_stand_holen("123") == "1000")
        db.discord_stand_setzen("123", "5", erzwingen=True)
        R.pruefe("Discord-Stand: erzwingen setzt zurück, unbekannter Kanal -> None",
                 db.discord_stand_holen("123") == "5" and db.discord_stand_holen("9") is None)

        # --- Mehrere Threads gleichzeitig --------------------------------------------
        db.unterhaltung_anlegen_oder_holen("THREADS", "gruppe")
        fehler = []

        def schreiber(nr):
            try:
                for i in range(40):
                    db.nachricht_einfuegen(f"t{nr}-{i}", "THREADS", f"T{nr}", i, f"x{i}")
                    db.nachrichten_seite("THREADS", anzahl=5)
            except Exception as e:      # pragma: no cover - nur im Fehlerfall
                fehler.append(e)

        threads = [threading.Thread(target=schreiber, args=(nr,)) for nr in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(30)
        R.pruefe("Threads: vier Schreiber gleichzeitig, nichts geht verloren",
                 not fehler and len(db.nachrichten_seite("THREADS", anzahl=1000)) == 160,
                 f"Fehler: {fehler[:1]}")
    finally:
        db.close()

    R.pruefe("Datenbank: mit falschem Datenschlüssel lässt sie sich nicht öffnen",
             hilfen["wirft_valueerror"](Datenbank, pfad, os.urandom(32)))
    R.pruefe("Datenbank: zu kurzer Datenschlüssel -> ValueError",
             hilfen["wirft_valueerror"](Datenbank, pfad, b"kurz"))
    with Datenbank(pfad, dk) as wieder:
        R.pruefe("Datenbank: wieder geöffnet ist alles da",
                 wieder.kontakt_holen("MAXI-0002")["spitzname"] == "Geheimname Maximilian")

    # --- Umbau-Haken für spätere Schema-Versionen ---------------------------------
    gelaufen = []

    class DatenbankV2(Datenbank):
        SCHEMA_VERSION = 2
        MIGRATIONEN = {2: lambda c: (gelaufen.append(2),
                                     c.execute("ALTER TABLE kontakte ADD COLUMN notiz TEXT"))}

    with DatenbankV2(pfad, dk) as v2:
        R.pruefe("Schema: Umbauschritt läuft einmal, Version steigt",
                 gelaufen == [2] and v2.schema_version() == 2)
    with DatenbankV2(pfad, dk):
        R.pruefe("Schema: beim zweiten Öffnen läuft nichts mehr", gelaufen == [2])
    R.pruefe("Schema: ältere Programmfassung lehnt neuere Datenbank ab",
             hilfen["wirft_valueerror"](Datenbank, pfad, dk))


# =============================================================================
#  Übernahme aus 4.x
# =============================================================================

def _altordner_bauen(ordner: Path) -> dict:
    """Ein Datenordner, wie ihn VP4 4.x hinterlässt."""
    ordner.mkdir(parents=True)
    with _ohne_echten_datenordner():
        ks = KeyStore(ordner / "schluessel.enc")
        ks.create("Altes Passwort 4x")
        ks.add_key("Alter Schlüssel", "AES", "QUJD", "von 2026")
        ks.add_key("Zweiter", "Passwort", "hunter2")
    (ordner / "konfig.json").write_text(json.dumps({
        "my_id": "ABCD-1234", "obsidian_vault": "C:/Vault", "design": "light",
        "farbe": "gruen", "master_passwort_gesetzt": True, "chat_aktiv": True,
        "transport_modus": "lan", "anzeigename": "Leon"}), encoding="utf-8")
    (ordner / "freunde.json").write_text(json.dumps({
        "MAXX-0002": {"nickname": "Max", "shared_key_b64": "R0VIRUlNLUZSRVVORA=="},
        "LISA-0003": {"nickname": "", "shared_key_b64": None}}), encoding="utf-8")
    (ordner / "gruppen.json").write_text(json.dumps({
        "G-ABCDEFGH": {"name": "Klasse 10b", "key_b64": "R0VIRUlNLUdSVVBQRQ=="}}),
        encoding="utf-8")
    (ordner / "discord.json").write_text(json.dumps({
        "bot_token": "token-aus-4x", "kanal_id": "4242"}), encoding="utf-8")
    (ordner / "empfangen").mkdir()
    (ordner / "empfangen" / "bild.png").write_bytes(b"\x89PNG bild")
    return {p.name: _hash(p) for p in ordner.rglob("*") if p.is_file()}


def _migration(R, ordner: Path):
    alt = ordner / "vp4_daten_alt"
    vorher = _altordner_bauen(alt)
    ziel = ordner / "neu" / "tresor.enc"

    leer = ordner / "leer"
    leer.mkdir()
    R.pruefe("Migration: leerer Ordner -> nichts gefunden",
             not migration.alte_daten_finden(leer)["gefunden"])

    befund = migration.alte_daten_finden(alt)
    R.pruefe("Migration: Befund erkennt alles",
             befund["noetig"] and befund["schluesselspeicher"] == "VP4K3"
             and befund["braucht_passwort"] and befund["freunde"] == 2
             and befund["gruppen"] == 1 and befund["discord"] and befund["empfangen"] == 1,
             f"{befund}")

    R.pruefe("Migration: falsches Passwort -> Fehler, nichts geschrieben",
             _wirft(migration.migrieren, FalschesPasswortError, alt, "falsch", ziel)
             and not ziel.exists() and not migration.ist_migriert(alt))

    ergebnis = migration.migrieren(alt, "Altes Passwort 4x", ziel)
    t = ergebnis["tresor"]
    R.pruefe("Migration: Tresor mit altem Schlüsselring",
             ergebnis["schluessel_anzahl"] == 2
             and [k["label"] for k in t.list_keys()] == ["Alter Schlüssel", "Zweiter"])
    frisch = Tresor(ziel)
    frisch.unlock("Altes Passwort 4x")
    R.pruefe("Migration: neuer Tresor öffnet mit dem alten Passwort",
             frisch.get_key("Zweiter")["wert"] == "hunter2")
    R.pruefe("Migration: genau die fünf Einstellungen kommen mit",
             ergebnis["einstellungen"] == {
                 "design": "light", "farbe": "gruen", "obsidian_vault": "C:/Vault",
                 "transport_modus": "lan", "anzeigename": "Leon"},
             f"{ergebnis['einstellungen']}")
    R.pruefe("Migration: eigener Discord-Zugang kommt mit",
             ergebnis["discord"] == {"bot_token": "token-aus-4x", "kanal_id": "4242"})
    kontakte = ergebnis["alte_kontakte"]
    R.pruefe("Migration: alte Freunde und Gruppen mit ID und Namen",
             [(k["id"], k["spitzname"], k["art"]) for k in kontakte] == [
                 ("LISA-0003", "", "freund"), ("MAXX-0002", "Max", "freund"),
                 ("G-ABCDEFGH", "Klasse 10b", "gruppe")], f"{kontakte}")
    R.pruefe("Migration: alte Chat-Schlüssel werden nicht weitergereicht",
             "R0VIRUlN" not in json.dumps(kontakte))
    R.pruefe("Migration: empfangene Dateien werden gemeldet",
             len(ergebnis["empfangen"]) == 1 and ergebnis["empfangen"][0].endswith("bild.png"))
    R.pruefe("Migration: Protokoll wird geführt", len(ergebnis["protokoll"]) >= 4)

    nachher = {p.name: _hash(p) for p in alt.rglob("*")
               if p.is_file() and p.name != migration.MARKIERUNG}
    R.pruefe("Migration: alte Dateien bleiben Byte für Byte unverändert", nachher == vorher)
    markierung = alt / migration.MARKIERUNG
    text = markierung.read_text(encoding="utf-8") if markierung.exists() else ""
    R.pruefe("Migration: Markierung geschrieben, ohne Schlüssel und Namen",
             text and "hunter2" not in text and "QUJD" not in text and "Max" not in text
             and "token-aus-4x" not in text and json.loads(text)["schluessel"] == 2)

    tresor_hash = _hash(ziel)
    zweites = migration.migrieren(alt, "Altes Passwort 4x", ziel)
    R.pruefe("Migration: zweiter Lauf tut nichts",
             zweites["bereits_migriert"] and _hash(ziel) == tresor_hash
             and not migration.alte_daten_finden(alt)["noetig"])
    R.pruefe("Migration: zweiter Lauf braucht kein Passwort",
             migration.migrieren(alt, None, ziel)["bereits_migriert"])

    # In einen schon bestehenden Tresor einmischen, Dateien woanders hin kopieren.
    alt2 = ordner / "zweiter_alt"
    _altordner_bauen(alt2)
    vorhanden = Tresor(ordner / "vorhanden.enc")
    vorhanden.create("ganz anderes Passwort")
    vorhanden.add_key("Schon da", "AES", "x")
    neu_empfangen = ordner / "neu_empfangen"
    erg = migration.migrieren(alt2, "Altes Passwort 4x", tresor=vorhanden,
                              empfangen_ziel=neu_empfangen)
    R.pruefe("Migration: in bestehenden Tresor eingemischt",
             sorted(k["label"] for k in vorhanden.list_keys())
             == ["Alter Schlüssel", "Schon da", "Zweiter"] and erg["tresor"] is vorhanden)
    R.pruefe("Migration: empfangene Dateien werden kopiert, nicht verschoben",
             (neu_empfangen / "bild.png").read_bytes() == b"\x89PNG bild"
             and (alt2 / "empfangen" / "bild.png").exists())

    # Altbestand aus der allerersten Fassung (VP4K2) über den ganzen Weg.
    altbestand = _altbestand()
    if altbestand is not None:
        alt3 = ordner / "erste_fassung"
        alt3.mkdir()
        (alt3 / "schluessel.enc").write_bytes(base64.b64decode(altbestand[0]))
        R.pruefe("Migration: Befund erkennt VP4K2",
                 migration.alte_daten_finden(alt3)["schluesselspeicher"] == "VP4K2")
        erg = migration.migrieren(alt3, altbestand[1], ordner / "erste.enc")
        R.pruefe("Migration: VP4K2-Speicher landet im neuen Tresor",
                 [k["label"] for k in erg["tresor"].list_keys()] == ["Alter AES"]
                 and (alt3 / "schluessel.enc").read_bytes()[:5] == b"VP4K2")

    # Ordner ohne Schlüsselspeicher: Einstellungen trotzdem, kein Passwort nötig.
    alt4 = ordner / "nur_konfig"
    alt4.mkdir()
    (alt4 / "konfig.json").write_text(json.dumps({"design": "dark"}), encoding="utf-8")
    befund = migration.alte_daten_finden(alt4)
    erg = migration.migrieren(alt4, None, ordner / "nie.enc")
    R.pruefe("Migration: ohne alten Speicher kein Passwort und kein Tresor",
             not befund["braucht_passwort"] and erg["tresor"] is None
             and erg["einstellungen"] == {"design": "dark"}
             and not (ordner / "nie.enc").exists())


# =============================================================================

def pruefen(R, hilfen):
    echter_ordner = speicher.DATA_DIR
    vorher = sorted(p.name for p in echter_ordner.iterdir()) if echter_ordner.exists() else None

    with tempfile.TemporaryDirectory() as ordner:
        ordner = Path(ordner)
        grund = {}
        abschnitte = [
            ("Tresor: Grundlagen",
             lambda o: grund.setdefault("pfad", _tresor_grundlagen(R, o))),
            ("Tresor: Manipulation am Kopf",
             lambda o: _tresor_manipulation(R, grund["pfad"], hilfen)),
            ("Tresor: Passwortwechsel",
             lambda o: _tresor_passwortwechsel(R, grund["pfad"])),
            ("Tresor: Schlüsselring", lambda o: _tresor_schluesselring(R, o, hilfen)),
            ("Tresor: Altbestand VP4K2", lambda o: _tresor_altbestand(R, o)),
            ("DPAPI-Weg", lambda o: _dpapi(R, o)),
            ("Datenbank", lambda o: _datenbank(R, o, hilfen)),
            ("Übernahme aus 4.x", lambda o: _migration(R, o)),
        ]
        for nr, (name, funktion) in enumerate(abschnitte):
            unterordner = ordner / f"teil{nr}"
            unterordner.mkdir()
            try:
                funktion(unterordner)
            except Exception as e:
                traceback.print_exc()
                R.fehlschlag(f"{name} lief nicht durch", e)

    nachher = sorted(p.name for p in echter_ordner.iterdir()) if echter_ordner.exists() else None
    R.pruefe("Der echte vp4_daten-Ordner blieb unberührt", vorher == nachher,
             f"vorher {vorher}, nachher {nachher}")
