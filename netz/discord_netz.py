# -*- coding: utf-8 -*-
"""netz/discord_netz.py - Umschläge über Discord-Textkanäle (Nachfolger von
discord_transport.py).

Der WLAN-Weg reicht nur bis zur Wohnungstür. Über Discord gehen dieselben
Umschläge (kern/umschlag.py) durch einen oder mehrere Textkanäle, die alle
Installationen mit demselben Bot-Token lesen. Verschlüsselt ist ein Umschlag
schon, bevor er hier ankommt; dieses Modul entschlüsselt nichts und glaubt
keiner Absenderangabe.

ZEILENFORMAT
------------
    VP4D2|<von>|<an>|<msg_id hex32>|<teil>|<gesamt>|<base64>

* Umschläge bis 6 KiB gehen als Text, zerlegt in Zeilen von höchstens
  1900 Zeichen (Discord lässt 2000 zu; der Rest ist Luft). Der Empfänger
  setzt sie in BELIEBIGER Reihenfolge wieder zusammen - Discord garantiert
  keine. Unvollständiges fliegt nach 300 s aus dem Zwischenspeicher.
* Größere Umschläge gehen als EINE Nachricht mit dem Kopf
  `VP4D2|<von>|<an>|<msg_id>|A|1|` und dem Umschlag als Anhang "vp4.bin"
  (höchstens MAX_ANHANG = 9,5 MiB; ein kostenloser Server nimmt 10 MiB).
  Größeres muss der Aufrufer vorher in Datei-Teile zerlegen.
* Der offene Kopf verrät, wer wann wem wie viel schreibt. Das ist so, und
  die Oberfläche sagt es auch so. Der Inhalt ist Geheimtext.

WER WAS HERUNTERLÄDT
--------------------
Alle Installationen sehen alle Zeilen. `interessant(an)` sagt, ob eine
Zeile uns etwas angeht (die eigene ID oder eine Community, in der man ist).
Alles andere wird verworfen, BEVOR ein Anhang geladen wird - sonst lüde
jeder Client jede Datei jedes anderen herunter.

KANÄLE
------
`KANAL_IDS` ist eine Liste. Discord bremst pro Kanal (etwa 5 Nachrichten in
5 s), und weil alle Installationen DENSELBEN Bot benutzen, teilen sich alle
dieses Budget. Deshalb geht jede Zeile in den Kanal
kanal_ids[sha256(an) % n]: Gespräche verteilen sich, bleiben aber jeweils in
einem Kanal. Gelauscht wird in allen.

NACHHOLEN
---------
Wer offline war, holt nach: Pro Kanal merkt sich die höhere Schicht die
zuletzt verarbeitete Discord-Nachricht (`stand_holen`/`stand_setzen`). Beim
Verbinden wird ab dort `channel.history(after=...)` durchgeblättert
(höchstens 7 Tage zurück, höchstens 5000 Zeilen), danach geht es live über
on_message weiter. Was während des Nachholens live hereinkommt, wird
zurückgehalten und danach verarbeitet, damit der Stand nie über eine Lücke
springt. Doppeltes (Nachholen + live) fängt eine Liste der zuletzt
gesehenen Discord-IDs ab; die höhere Schicht entdoppelt zusätzlich nach
msg_id.

SENDEN
------
Ein einziger Sende-Arbeiter in der Discord-Schleife, damit dieser Client
nicht mit sich selbst um das Kanalbudget rangelt. `senden()` wartet, bis es
geklappt hat oder endgültig gescheitert ist (2 Wiederholungen nach 1 s und
3 s). Auf ein 429 wartet discord.py selbst die von Discord verlangte Zeit
ab; kommt es trotzdem durch, warten wir mindestens retry_after.

**Flüchtiges (z. B. "tippt gerade") geht NIE über Discord** - es würde das
gemeinsame Kanalbudget ALLER Nutzer verbrennen, und nach Sekunden ist es
ohnehin wertlos. `senden(..., fluechtig=True)` lehnt sofort ab.

ANMELDUNGEN SPAREN
------------------
Discord erlaubt pro Bot-Token rund 1000 Anmeldungen (IDENTIFY) in 24 h -
für ALLE Installationen zusammen. Ist das aufgebraucht, setzt Discord den
Token zurück und jede VP4-Kopie verliert Discord bis zum nächsten Release.
Darum: vor jeder Anmeldung das Restbudget abfragen, unter einer Reserve gar
nicht erst anmelden, und nach Fehlern mit wachsenden Pausen (5 s bis 5 min)
statt sofort neu versuchen.

IM TESTMODUS
------------
`start()` tut bei gesetztem VP4_TESTMODUS nichts - sonst hinge sich jeder
Selbsttest mit dem echten Bot in die echten Kanäle. Die Prüfungen benutzen
stattdessen `DiscordProtokoll2` (reine Rechnung) und `mit_attrappe_starten()`
mit `FakeKanal` (unten in dieser Datei).
"""

import asyncio
import base64
import concurrent.futures
import hashlib
import io
import itertools
import os
import re
import threading
import time
from collections import OrderedDict, namedtuple
from pathlib import Path

from kern import speicher
from kern.umschlag import Umschlag, ist_ziel
from kern.identitaet import ist_nutzer_id

MARKE = "VP4D2"
ZEILEN_LIMIT = 1900                     # ganze Zeile, Kopf inklusive
TEXT_MAX = 6 * 1024                     # Umschläge bis hierhin gehen als Text
MAX_TEILE = 9                           # 6 KiB brauchen 5 Zeilen; mehr ist Unfug
MAX_ANHANG = int(9.5 * 1024 * 1024)     # ein Umschlag als Anhang
ANHANG_NAME = "vp4.bin"
ANHANG_TEIL = "A"
TEILE_TIMEOUT = 300                     # Sekunden für unvollständige Nachrichten
MAX_OFFEN = 500                         # gleichzeitig offene Zusammensetzungen

NACHHOLEN_TAGE = 7
NACHHOLEN_MAX = 5000

SENDE_FRIST = 60.0                      # so lange wartet senden() höchstens
WIEDERHOLUNGEN = (1.0, 3.0)             # Pausen vor den 2 Wiederholungen
GESEHEN_MAX = 20000                     # gemerkte Discord-Nachrichten-IDs
GESENDET_MAX = 5000                     # gemerkte msg_id -> Discord-IDs (für loeschen)

SITZUNGEN_RESERVE = 25                  # darunter melden wir uns nicht an
PAUSE_START, PAUSE_MAX = 5.0, 300.0     # Wartezeiten nach Verbindungsfehlern

DISCORD_EPOCHE_MS = 1420070400000

TEXT_GESPERRT = ("Der eingebaute Discord-Zugang wurde gesperrt oder ist ungültig. "
                 "Lade die neueste VP4-Version von GitHub.")
TEXT_INTENT = ("Dem Discord-Bot fehlt die Berechtigung, Nachrichten zu lesen "
               "(Message Content Intent).\n\nSo schaltet man sie ein: "
               "discord.com/developers/applications → die App → Bot → "
               "„MESSAGE CONTENT INTENT“ einschalten und speichern.\n\n"
               "Ohne das kommen alle Nachrichten leer an.")
TEXT_NICHT_VERBUNDEN = ("Der Chat über Discord ist gerade nicht verbunden. Bist du "
                        "online? Der Umschlag wurde nicht verschickt.")
TEXT_FLUECHTIG = ("Kurzlebiges wie „tippt gerade“ geht nie über Discord: Alle "
                  "VP4-Nutzer teilen sich dort dasselbe Sendebudget pro Kanal.")

_HEX32 = re.compile(r"^[0-9a-f]{32}$")
_ZIFFERN = re.compile(r"^[0-9]{15,22}$")

Kopf = namedtuple("Kopf", "von an msg_id teil gesamt nutzlast ist_anhang")


def _jetzt_snowflake(sekunden_zurueck: float = 0.0) -> int:
    """Eine Discord-ID für einen Zeitpunkt - "alles danach" beim Nachholen."""
    ms = int((time.time() - sekunden_zurueck) * 1000)
    return max(0, ms - DISCORD_EPOCHE_MS) << 22


def _nachgeben(fehler) -> float:
    """Wie lange Discord bei einem 429 Ruhe haben will (0, wenn nichts dasteht)."""
    wert = getattr(fehler, "retry_after", None)
    if wert is None:
        try:
            wert = fehler.response.headers.get("Retry-After")
        except Exception:
            wert = None
    try:
        return min(max(float(wert), 0.0), 60.0)
    except (TypeError, ValueError):
        return 0.0


def kanal_waehlen(an: str, kanal_ids) -> str:
    """In welchen Kanal eine Zeile an `an` gehört. Für alle Clients gleich."""
    if not kanal_ids:
        raise ValueError("Es ist kein Discord-Kanal eingerichtet.")
    zahl = int(hashlib.sha256(an.encode("utf-8")).hexdigest(), 16)
    return kanal_ids[zahl % len(kanal_ids)]


# =============================================================================
#  Das Protokoll - ohne Netz, damit es sich prüfen lässt
# =============================================================================

class DiscordProtokoll2:
    """Baut Zeilen, liest Köpfe, setzt Teile zusammen, merkt sich Gesehenes.

    Kein Netz, kein discord.py, kein asyncio - nur Rechnung. Die Uhr ist
    austauschbar, damit der Test das Verfallen nach 300 s prüfen kann, ohne
    300 s zu warten.
    """

    def __init__(self, eigene_id: str, interessant=None, uhr=time.monotonic):
        self.eigene_id = eigene_id
        self.interessant = interessant or (lambda an: an == eigene_id)
        self.uhr = uhr
        self._offen = OrderedDict()     # (von, msg_id) -> {"gesamt", "teile", "zeit"}
        self._gesehen = OrderedDict()   # Discord-Nachrichten-ID -> None

    # ------------------------------------------------------------ Senden

    @staticmethod
    def _kopf(von, an, msg_id, teil, gesamt) -> str:
        return f"{MARKE}|{von}|{an}|{msg_id}|{teil}|{gesamt}|"

    @staticmethod
    def umschlag_kopf(umschlag_bytes: bytes, an: str):
        """(von, msg_id_hex) aus dem Umschlag; ValueError, wenn er nicht passt."""
        u = Umschlag.entpacken(umschlag_bytes)
        if u.an != an:
            raise ValueError("Der Umschlag ist an jemand anderen adressiert.")
        return u.von, u.msg_id.hex()

    def plan(self, umschlag_bytes: bytes, an: str) -> list:
        """Was für diesen Umschlag in den Kanal muss.

        Rückgabe: Liste von ("text", zeile) oder genau ein ("anhang", zeile,
        daten). ValueError, wenn der Umschlag kaputt oder zu groß ist.
        """
        daten = bytes(umschlag_bytes)
        von, msg_id = self.umschlag_kopf(daten, an)
        if len(daten) <= TEXT_MAX:
            return [("text", z) for z in self._zeilen(von, an, msg_id, daten)]
        if len(daten) > MAX_ANHANG:
            raise ValueError(
                f"Der Umschlag ist {len(daten) / 1024 / 1024:.1f} MiB groß; über "
                f"Discord gehen höchstens {MAX_ANHANG / 1024 / 1024:.1f} MiB am "
                f"Stück. Größere Dateien müssen vorher in Teile zerlegt werden.")
        return [("anhang", self._kopf(von, an, msg_id, ANHANG_TEIL, 1), daten)]

    def zeilen_bauen(self, umschlag_bytes: bytes, an: str) -> list:
        """Nur die Text-Zeilen; ValueError, wenn der Umschlag dafür zu groß ist."""
        daten = bytes(umschlag_bytes)
        if len(daten) > TEXT_MAX:
            raise ValueError("Der Umschlag ist zu groß für Text-Zeilen.")
        von, msg_id = self.umschlag_kopf(daten, an)
        return self._zeilen(von, an, msg_id, daten)

    def _zeilen(self, von, an, msg_id, daten) -> list:
        text = base64.b64encode(daten).decode("ascii")
        # Bei höchstens 9 Teilen sind Teil und Gesamt je eine Ziffer, der Kopf
        # hat also immer dieselbe Länge.
        pro_zeile = ZEILEN_LIMIT - len(self._kopf(von, an, msg_id, 9, 9))
        gesamt = max(1, -(-len(text) // pro_zeile))
        if gesamt > MAX_TEILE:
            raise ValueError("Der Umschlag ist zu groß für Text-Zeilen.")
        return [self._kopf(von, an, msg_id, i + 1, gesamt)
                + text[i * pro_zeile:(i + 1) * pro_zeile]
                for i in range(gesamt)]

    # --------------------------------------------------------- Empfangen

    def kopf_lesen(self, zeile):
        """Zerlegt eine Zeile; None für alles, was keine gültige VP4D2-Zeile ist."""
        if not isinstance(zeile, str) or not zeile.startswith(MARKE + "|"):
            return None
        if len(zeile) > 2000:
            return None
        teile = zeile.split("|", 6)
        if len(teile) != 7:
            return None
        _, von, an, msg_id, teil_s, gesamt_s, nutzlast = teile
        if not ist_nutzer_id(von) or not ist_ziel(an) or not _HEX32.match(msg_id):
            return None
        if teil_s == ANHANG_TEIL:
            if gesamt_s != "1" or nutzlast:
                return None
            return Kopf(von, an, msg_id, 0, 1, "", True)
        if not (teil_s.isdigit() and gesamt_s.isdigit()):
            return None
        teil, gesamt = int(teil_s), int(gesamt_s)
        if not (1 <= teil <= gesamt <= MAX_TEILE) or not nutzlast:
            return None
        return Kopf(von, an, msg_id, teil, gesamt, nutzlast, False)

    def fuer_uns(self, kopf) -> bool:
        """Geht uns die Zeile etwas an? Das eigene Echo nicht."""
        if kopf is None or kopf.von == self.eigene_id:
            return False
        try:
            return bool(self.interessant(kopf.an))
        except Exception:
            return False

    def _pruefen(self, kopf, daten: bytes):
        """Der fertige Umschlag muss zum offenen Kopf passen - sonst weg."""
        try:
            u = Umschlag.entpacken(daten)
        except ValueError:
            return None
        if u.von != kopf.von or u.an != kopf.an or u.msg_id.hex() != kopf.msg_id:
            return None
        return daten

    def teil_annehmen(self, kopf):
        """Nimmt einen Text-Teil an; gibt die Umschlag-Bytes zurück, sobald
        alle Teile da sind, sonst None."""
        self.aufraeumen()
        schluessel = (kopf.von, kopf.msg_id)
        eintrag = self._offen.get(schluessel)
        if eintrag is None or eintrag["gesamt"] != kopf.gesamt:
            eintrag = {"gesamt": kopf.gesamt, "teile": {}, "zeit": self.uhr()}
            self._offen[schluessel] = eintrag
            while len(self._offen) > MAX_OFFEN:
                self._offen.popitem(last=False)
        eintrag["teile"][kopf.teil] = kopf.nutzlast
        eintrag["zeit"] = self.uhr()
        if len(eintrag["teile"]) < kopf.gesamt:
            return None
        del self._offen[schluessel]
        text = "".join(eintrag["teile"][i] for i in range(1, kopf.gesamt + 1))
        try:
            daten = base64.b64decode(text, validate=True)
        except (ValueError, TypeError):
            return None
        return self._pruefen(kopf, daten)

    def anhang_annehmen(self, kopf, daten: bytes):
        if daten is None or len(daten) > MAX_ANHANG:
            return None
        return self._pruefen(kopf, bytes(daten))

    def zeile_lesen(self, zeile):
        """Kurzform für Text: ("fertig", bytes), ("anhang", kopf) oder None."""
        kopf = self.kopf_lesen(zeile)
        if not self.fuer_uns(kopf):
            return None
        if kopf.ist_anhang:
            return ("anhang", kopf)
        daten = self.teil_annehmen(kopf)
        return ("fertig", daten) if daten is not None else None

    def aufraeumen(self):
        grenze = self.uhr() - TEILE_TIMEOUT
        for k in [k for k, v in self._offen.items() if v["zeit"] < grenze]:
            del self._offen[k]

    def offen(self) -> int:
        return len(self._offen)

    def schon_gesehen(self, discord_id) -> bool:
        """Merkt sich die ID und sagt, ob sie schon einmal da war."""
        if discord_id is None:
            return False
        if discord_id in self._gesehen:
            return True
        self._gesehen[discord_id] = None
        while len(self._gesehen) > GESEHEN_MAX:
            self._gesehen.popitem(last=False)
        return False


# =============================================================================
#  Zugangsdaten: eingebaut (discord_konfig.py) + eigene (vp4_daten/discord.json)
# =============================================================================

DISCORD_DATEI = speicher.DATA_DIR / "discord.json"


def kanal_ids_lesen(text) -> list:
    """"123, 456 789" -> ["123", "456", "789"]; doppelte fallen weg."""
    if isinstance(text, (list, tuple)):
        text = ",".join(str(t) for t in text)
    ids = []
    for stueck in re.split(r"[\s,;]+", str(text or "")):
        if stueck and stueck not in ids:
            ids.append(stueck)
    return ids


def _gueltige_kanaele(text) -> list:
    return [k for k in kanal_ids_lesen(text) if _ZIFFERN.match(k)]


def zugang_laden(konfig=None, datei: Path = None) -> dict:
    """Die geltenden Zugangsdaten: {"token", "kanal_ids", "eingebaut"}.

    Erst das, was beim Bauen der .exe eingesetzt wurde, darüber das, was der
    Benutzer unter Einstellungen → Discord eingetragen hat. Eigene Werte
    gewinnen - sonst käme man aus einem eingebauten, aber gesperrten Bot nie
    heraus. Ein LEERES eigenes Feld löscht den eingebauten Wert aber nicht.

    `konfig` und `datei` sind nur für den Selbsttest austauschbar.
    """
    if konfig is None:
        try:
            import discord_konfig as konfig
        except ImportError:
            konfig = None
    token_ein = str(getattr(konfig, "BOT_TOKEN", "") or "").strip()
    kanaele_ein = _gueltige_kanaele(getattr(konfig, "KANAL_IDS", "") or "")
    if not kanaele_ein:
        # Die 4.x-Datei kannte nur einen Kanal.
        kanaele_ein = _gueltige_kanaele(getattr(konfig, "KANAL_ID", "") or "")

    eigene = speicher.load_json(datei or DISCORD_DATEI, {})
    if not isinstance(eigene, dict):
        eigene = {}
    token_eigen = str(eigene.get("bot_token") or "").strip()
    kanaele_eigen = _gueltige_kanaele(eigene.get("kanal_ids") or "")
    if not kanaele_eigen:
        kanaele_eigen = _gueltige_kanaele(eigene.get("kanal_id") or "")

    return {"token": token_eigen or token_ein,
            "kanal_ids": kanaele_eigen or kanaele_ein,
            "eingebaut": bool(token_ein) and not token_eigen}


def zugang_speichern(token: str, kanal_ids_text: str, datei: Path = None) -> list:
    """Schreibt eigene Zugangsdaten; gibt die erkannten Kanal-IDs zurück.

    ValueError bei einer Kanal-ID, die keine ist. Wie speicher.save_config()
    schreibt das im Testmodus nichts - sonst überschriebe ein Testlauf die
    echten Zugangsdaten.
    """
    roh = kanal_ids_lesen(kanal_ids_text)
    falsch = [k for k in roh if not _ZIFFERN.match(k)]
    if falsch:
        raise ValueError(
            f"„{falsch[0]}“ ist keine Discord-Kanal-ID. Eine Kanal-ID besteht "
            f"aus 17 bis 20 Ziffern (Rechtsklick auf den Kanal → „Kanal-ID "
            f"kopieren“, Entwicklermodus muss an sein).")
    werte = {"bot_token": (token or "").strip(), "kanal_ids": ",".join(roh)}
    if not os.environ.get("VP4_TESTMODUS"):
        speicher.save_json(datei or DISCORD_DATEI, werte)
    return roh


# =============================================================================
#  Der Transport
# =============================================================================

class _Auftrag:
    __slots__ = ("kanal_id", "schritte", "msg_id", "zukunft", "abgebrochen")

    def __init__(self, kanal_id, schritte, msg_id, zukunft):
        self.kanal_id = kanal_id
        self.schritte = schritte
        self.msg_id = msg_id
        self.zukunft = zukunft
        self.abgebrochen = False


class _Datei:
    """Ersatz für discord.File, wenn ohne discord.py (Attrappe) gesendet wird."""

    def __init__(self, fp, filename: str):
        self.fp = fp
        self.filename = filename


class _Objekt:
    def __init__(self, id):
        self.id = id


class DiscordNetz:
    """Hält die Verbindung zu Discord und bewegt darüber Umschläge.

    empfangen(umschlag_bytes, "discord", {"kanal": id, "nachricht": id})
    ereignis(typ, **daten): "verbindung" (zustand={"discord": "an"|"aus"|
        "fehler"}, meldung=...), "fehler" (text=...), "update_pruefen".
    stand_holen(kanal_id) -> str|None, stand_setzen(kanal_id, nachricht_id)
    interessant(an) -> bool

    discord.py arbeitet mit asyncio, VP4 mit Threads. Deshalb lebt die
    Bibliothek in einem eigenen Thread mit eigener Ereignisschleife; alle
    Rückrufe kommen aus diesem Thread.
    """

    def __init__(self, eigene_id: str, empfangen, ereignis, token: str,
                 kanal_ids, stand_holen=None, stand_setzen=None,
                 interessant=None):
        self.eigene_id = eigene_id
        self.empfangen = empfangen
        self.ereignis = ereignis
        self.token = (token or "").strip()
        self.kanal_ids = [str(k) for k in kanal_ids_lesen(kanal_ids)]
        self.stand_holen = stand_holen or (lambda kanal_id: None)
        self.stand_setzen = stand_setzen or (lambda kanal_id, nachricht_id: None)
        self.protokoll = DiscordProtokoll2(eigene_id, interessant)

        self.verbunden = False
        self.zustand = "aus"
        self.meldung = ""

        self._laeuft = False
        self._thread = None
        self._loop = None
        self._client = None
        self._stopp = None              # asyncio.Event in der Schleife
        self._schlange = None           # asyncio.PriorityQueue
        self._folge = itertools.count()
        self._kanaele = {}              # kanal_id -> Kanalobjekt
        self._staende = {}              # kanal_id -> höchste verarbeitete ID
        self._nachholen_laeuft = set()
        self._puffer = {}               # kanal_id -> [nachricht] während des Nachholens
        self._gesendet = OrderedDict()  # msg_id hex -> [(kanal_id, discord_id)]
        self._datei_fabrik = _Datei
        self._objekt_fabrik = _Objekt
        self._war_bereit = False

    # ---------------------------------------------------------- Hilfen

    def _melden(self, typ, **daten):
        try:
            self.ereignis(typ, **daten)
        except Exception:
            pass

    def _zustand_setzen(self, zustand, meldung=""):
        if zustand == self.zustand and meldung == self.meldung:
            return
        self.zustand = zustand
        self.meldung = meldung
        self._melden("verbindung", zustand={"discord": zustand}, meldung=meldung)

    @property
    def eingerichtet(self) -> bool:
        return bool(self.token) and bool(self.kanal_ids)

    # ------------------------------------------------------ Start / Stop

    def start(self):
        if self._laeuft:
            return
        if os.environ.get("VP4_TESTMODUS"):
            # Sonst hinge sich jeder Selbsttestlauf mit dem echten Bot in die
            # echten Kanäle, sobald Zugangsdaten da sind - und auf einem
            # Rechner ohne Internet minutenlang in Verbindungsversuchen.
            return
        if not self.eingerichtet:
            self._zustand_setzen("aus", "Discord ist nicht eingerichtet.")
            return
        self._laeuft = True
        self._thread = threading.Thread(target=self._thread_laufen,
                                        args=(self._mit_discord,),
                                        daemon=True, name="vp4-discord")
        self._thread.start()

    def mit_attrappe_starten(self, kanaele: dict):
        """Startet ohne discord.py mit Kanal-Attrappen (siehe FakeKanal).

        Für die Selbsttests: derselbe Sende-Arbeiter, dasselbe Nachholen,
        derselbe Empfang - nur ohne Gateway. Funktioniert auch im Testmodus.
        """
        if self._laeuft:
            return
        self._laeuft = True
        self._datei_fabrik = _Datei
        bereit = threading.Event()

        async def verbinden():
            for kanal in kanaele.values():
                kanal.abonnieren(self)
            await self._bereit(dict(kanaele))
            bereit.set()
            await self._stopp.wait()
            for kanal in kanaele.values():
                kanal.abbestellen(self)

        self._thread = threading.Thread(target=self._thread_laufen, args=(verbinden,),
                                        daemon=True, name="vp4-discord-attrappe")
        self._thread.start()
        bereit.wait(5)

    def stop(self):
        if not self._laeuft:
            return
        self._laeuft = False
        loop = self._loop
        if loop is not None:
            try:
                loop.call_soon_threadsafe(self._stopp.set)
            except RuntimeError:
                pass        # Schleife schon zu
        if self._thread is not None and self._thread is not threading.current_thread():
            self._thread.join(5)
        self.verbunden = False
        self._zustand_setzen("aus")

    def _thread_laufen(self, hauptteil):
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        self._loop = loop
        self._stopp = asyncio.Event()
        self._schlange = asyncio.PriorityQueue()
        try:
            loop.run_until_complete(self._mit_arbeiter(hauptteil))
        except Exception as e:
            # Darf nie passieren - und wenn doch, dann sichtbar.
            self._zustand_setzen("fehler", f"Der Discord-Weg ist abgestürzt. (Technisch: {e})")
        finally:
            self.verbunden = False
            self._offene_auftraege_abbrechen(loop)
            try:
                pending = [t for t in asyncio.all_tasks(loop) if not t.done()]
                for t in pending:
                    t.cancel()
                if pending:
                    loop.run_until_complete(asyncio.gather(*pending, return_exceptions=True))
                loop.run_until_complete(loop.shutdown_asyncgens())
            except Exception:
                pass
            loop.close()
            self._loop = None

    async def _mit_arbeiter(self, hauptteil):
        arbeiter = asyncio.ensure_future(self._sende_arbeiter())
        try:
            await hauptteil()
        finally:
            arbeiter.cancel()
            try:
                await arbeiter
            except (asyncio.CancelledError, Exception):
                pass

    def _offene_auftraege_abbrechen(self, loop):
        schlange = self._schlange
        if schlange is None:
            return
        while not schlange.empty():
            try:
                _, _, auftrag = schlange.get_nowait()
            except Exception:
                break
            if auftrag.zukunft is not None and not auftrag.zukunft.done():
                auftrag.zukunft.set_exception(ConnectionError(TEXT_NICHT_VERBUNDEN))

    async def _schlafen(self, sekunden: float) -> bool:
        """Wartet, bis die Zeit um ist oder stop() kommt. True = gestoppt."""
        try:
            await asyncio.wait_for(self._stopp.wait(), timeout=sekunden)
            return True
        except asyncio.TimeoutError:
            return False

    # ------------------------------------------------- Die echte Verbindung

    async def _mit_discord(self):
        try:
            import discord
        except ImportError:
            self._zustand_setzen("fehler", "Für den Chat über Discord fehlt das Paket "
                                           "„discord.py“ (pip install discord.py).")
            return
        self._datei_fabrik = discord.File
        self._objekt_fabrik = discord.Object
        pause = PAUSE_START

        while self._laeuft and not self._stopp.is_set():
            intents = discord.Intents.default()
            # Ohne diese Berechtigung kommen alle Nachrichten leer an. Sie muss
            # zusätzlich im Developer-Portal an sein (TEXT_INTENT).
            intents.message_content = True
            client = discord.Client(intents=intents)
            self._client = client
            self._war_bereit = False
            self._ereignisse_anhaengen(client, discord)

            # Der Versuch läuft als eigene Aufgabe, damit stop() auch mitten in
            # einer hängenden Anmeldung sofort greift.
            versuch = asyncio.ensure_future(self._verbindung_versuchen(client, discord))
            stopp = asyncio.ensure_future(self._stopp.wait())
            await asyncio.wait({versuch, stopp}, return_when=asyncio.FIRST_COMPLETED)
            stopp.cancel()
            if not versuch.done():
                await self._schliessen(client)      # connect() kehrt damit zurück
                try:
                    await asyncio.wait_for(versuch, 3)
                except BaseException:
                    versuch.cancel()
                break

            warten = pause
            try:
                bis_reset = versuch.result()
                if bis_reset:
                    warten = bis_reset
            except Exception as e:
                endgueltig, text = self._fehler_deuten(e, discord)
                self._zustand_setzen("fehler", text)
                if endgueltig:
                    # Erst ein Neustart (oder ein Update) hilft. Weiter zu
                    # versuchen würde nur Anmeldungen verbrennen.
                    return
            finally:
                self.verbunden = False
                await self._schliessen(client)
                self._client = None
            if self._war_bereit:
                pause = warten = PAUSE_START    # lief eine Weile: von vorn
            if self._stopp.is_set():
                break
            if self.zustand == "an":
                self._zustand_setzen("aus", "Verbindung zu Discord getrennt – neuer "
                                            "Versuch läuft.")
            if await self._schlafen(warten):
                break
            pause = min(pause * 2, PAUSE_MAX)

    async def _verbindung_versuchen(self, client, discord):
        """Anmelden, Budget prüfen, verbinden. Rückgabe: Sekunden Pause oder 0."""
        await client.login(self.token)          # 401 -> LoginFailure
        bis_reset = await self._sitzungen_pruefen(client, discord)
        if bis_reset:
            return bis_reset
        # reconnect=True: discord.py setzt kurze Aussetzer selbst fort (RESUME,
        # kostet keine Anmeldung). Was es nicht selbst kann, wirft es hierher.
        await client.connect(reconnect=True)
        return 0

    @staticmethod
    async def _schliessen(client):
        try:
            if not client.is_closed():
                await client.close()
        except Exception:
            pass

    async def _sitzungen_pruefen(self, client, discord):
        """Fragt das Anmeldebudget ab. Rückgabe: Sekunden zu warten oder 0.

        discord.py selbst prüft das beim einfachen Client nicht. Das Budget
        (rund 1000 Anmeldungen pro 24 h) teilen sich ALLE VP4-Kopien; ist es
        aufgebraucht, setzt Discord den Token zurück. Deshalb lassen wir eine
        Reserve stehen und warten lieber bis zum Zurücksetzen.
        """
        try:
            _, _, grenze = await client.http.get_bot_gateway()
        except discord.HTTPException as e:
            if getattr(e, "status", None) == 401:
                raise discord.LoginFailure("401") from e
            return 0        # Abfrage ging nicht - dann eben ohne
        except Exception:
            return 0
        rest = int(grenze.get("remaining", 1000))
        if rest > SITZUNGEN_RESERVE:
            return 0
        sekunden = max(60.0, int(grenze.get("reset_after", 3600000)) / 1000.0)
        stunden = sekunden / 3600
        zeit = f"{stunden:.0f} Stunden" if stunden >= 1.5 else f"{sekunden / 60:.0f} Minuten"
        self._zustand_setzen(
            "fehler",
            "Der eingebaute Discord-Zugang hat für heute fast alle Anmeldungen "
            "verbraucht (alle VP4-Nutzer teilen sich rund 1000 pro Tag). Damit "
            f"Discord ihn nicht sperrt, wartet VP4 etwa {zeit} und versucht es "
            "dann von selbst wieder. Im WLAN geht der Chat weiter.")
        return sekunden

    def _fehler_deuten(self, e, discord):
        """(endgültig, deutscher Text) für eine Ausnahme aus discord.py.

        Ohne das stünde ein englischer Klassenname in der Statusleiste - oder
        in der .exe ohne Konsole gar nichts (Fehler 5 in CLAUDE.md).
        """
        if isinstance(e, discord.LoginFailure) or (
                isinstance(e, discord.HTTPException) and getattr(e, "status", None) == 401):
            self._melden("update_pruefen")
            return True, TEXT_GESPERRT
        if isinstance(e, discord.PrivilegedIntentsRequired):
            return True, TEXT_INTENT
        if isinstance(e, discord.ConnectionClosed):
            code = getattr(e, "code", None)
            if code == 4004:
                self._melden("update_pruefen")
                return True, TEXT_GESPERRT
            if code in (4013, 4014):
                return True, TEXT_INTENT
            return False, (f"Die Verbindung zu Discord ist abgebrochen (Code {code}). "
                           f"Neuer Versuch gleich.")
        if isinstance(e, (OSError, asyncio.TimeoutError, discord.GatewayNotFound)):
            return False, ("Discord ist gerade nicht erreichbar. Bist du online? "
                           "Neuer Versuch gleich.")
        if isinstance(e, discord.HTTPException):
            return False, (f"Discord hat einen Fehler gemeldet ({getattr(e, 'status', '?')}). "
                           f"Neuer Versuch gleich.")
        return False, f"Der Chat über Discord ist unterbrochen. (Technisch: {e})"

    def _ereignisse_anhaengen(self, client, discord):
        @client.event
        async def on_ready():
            kanaele = {}
            fehlend = []
            for kid in self.kanal_ids:
                kanal = client.get_channel(int(kid))
                if kanal is None:
                    try:
                        kanal = await client.fetch_channel(int(kid))
                    except Exception:
                        kanal = None
                if kanal is None or not hasattr(kanal, "history"):
                    fehlend.append(kid)
                    continue
                luecken = self._rechte_pruefen(kanal)
                if luecken:
                    fehlend.append(f"{kid} (es fehlt: {luecken})")
                    if "Kanal sehen" in luecken or "Nachrichten senden" in luecken:
                        continue
                kanaele[kid] = kanal
            if fehlend:
                self._melden("fehler", text=(
                    "Auf diese Discord-Kanäle kommt der Bot nicht (oder nicht ganz): "
                    + ", ".join(fehlend) + ".\n\nIst die Kanal-ID richtig, ist der Bot "
                    "auf dem Server, und hat er dort „Kanal ansehen“, „Nachrichten "
                    "senden“, „Nachrichtenverlauf lesen“ und „Dateien anhängen“?"))
            if not kanaele:
                self.verbunden = False
                self._zustand_setzen("fehler", "Keiner der eingestellten Discord-Kanäle "
                                               "ist erreichbar: " + ", ".join(self.kanal_ids))
                return
            await self._bereit(kanaele)

        @client.event
        async def on_resumed():
            if self._kanaele:
                self.verbunden = True
                self._zustand_setzen("an", self._an_text())

        @client.event
        async def on_disconnect():
            if self.verbunden:
                self.verbunden = False
                self._zustand_setzen("aus", "Verbindung zu Discord unterbrochen – "
                                            "neuer Versuch läuft.")

        @client.event
        async def on_message(nachricht):
            await self._live(nachricht)

    @staticmethod
    def _rechte_pruefen(kanal) -> str:
        try:
            rechte = kanal.permissions_for(kanal.guild.me)
        except Exception:
            return ""
        fehlt = []
        for attr, name in (("view_channel", "Kanal sehen"),
                           ("send_messages", "Nachrichten senden"),
                           ("read_message_history", "Verlauf lesen"),
                           ("attach_files", "Dateien anhängen")):
            if not getattr(rechte, attr, True):
                fehlt.append(name)
        return ", ".join(fehlt)

    def _an_text(self) -> str:
        n = len(self._kanaele)
        return f"Mit Discord verbunden ({n} Kanal{'' if n == 1 else 'e'})."

    # ------------------------------------------------ Bereit + Nachholen

    async def _bereit(self, kanaele: dict):
        """Gemeinsamer Teil für echte Verbindung und Attrappe."""
        self._kanaele = kanaele
        self.verbunden = True
        self._war_bereit = True
        self._zustand_setzen("an", self._an_text())
        for kid, kanal in kanaele.items():
            if kid not in self._nachholen_laeuft:
                self._nachholen_laeuft.add(kid)
                self._puffer.setdefault(kid, [])
                asyncio.ensure_future(self._nachholen(kid, kanal))

    def _stand_untergrenze(self) -> int:
        return _jetzt_snowflake(NACHHOLEN_TAGE * 86400)

    async def _nachholen(self, kid, kanal):
        """Holt alles seit dem gespeicherten Stand, dann die zurückgehaltenen
        Live-Nachrichten."""
        try:
            try:
                stand = int(self.stand_holen(kid) or 0)
            except (TypeError, ValueError):
                stand = 0
            except Exception:
                stand = 0
            ab = max(stand, self._staende.get(kid, 0), self._stand_untergrenze())
            anzahl = 0
            async for nachricht in kanal.history(limit=NACHHOLEN_MAX,
                                                 after=self._objekt_fabrik(id=ab),
                                                 oldest_first=True):
                await self._verarbeiten(kid, nachricht)
                anzahl += 1
                if self._stopp.is_set():
                    return
        except asyncio.CancelledError:
            raise
        except Exception as e:
            self._melden("fehler", text=f"Das Nachholen aus dem Discord-Kanal {kid} "
                                        f"ging nicht ganz. (Technisch: {e})")
        finally:
            self._nachholen_laeuft.discard(kid)
            puffer = self._puffer.pop(kid, [])
        for nachricht in puffer:
            await self._verarbeiten(kid, nachricht)

    def einspeisen(self, nachricht):
        """Eine Nachricht zustellen, als käme sie über on_message (threadsicher).

        Für die Attrappe; der echte Weg ist on_message in _ereignisse_anhaengen.
        """
        loop = self._loop
        if loop is None or not self._laeuft:
            return
        try:
            loop.call_soon_threadsafe(lambda: asyncio.ensure_future(self._live(nachricht)))
        except RuntimeError:
            pass

    async def _live(self, nachricht):
        kid = str(getattr(getattr(nachricht, "channel", None), "id", ""))
        if kid not in self.kanal_ids:
            return
        if kid in self._nachholen_laeuft:
            # Erst nachholen, dann das hier - sonst spränge der Stand über
            # eine Lücke, und nach einem Absturz fehlte genau die.
            self._puffer.setdefault(kid, []).append(nachricht)
            return
        await self._verarbeiten(kid, nachricht)

    async def _verarbeiten(self, kid, nachricht):
        """Eine Nachricht aus einem Kanal: prüfen, ggf. Anhang laden, abgeben."""
        try:
            nid = int(nachricht.id)
        except Exception:
            return
        if self.protokoll.schon_gesehen(nid):
            return
        try:
            kopf = self.protokoll.kopf_lesen(getattr(nachricht, "content", "") or "")
            if self.protokoll.fuer_uns(kopf):
                if kopf.ist_anhang:
                    daten = await self._anhang_laden(nachricht, kopf)
                else:
                    daten = self.protokoll.teil_annehmen(kopf)
                if daten is not None:
                    try:
                        self.empfangen(daten, "discord", {"kanal": kid, "nachricht": nid})
                    except Exception as e:
                        self._melden("fehler", text=f"Ein Umschlag über Discord ließ sich "
                                                    f"nicht verarbeiten. (Technisch: {e})")
        except Exception:
            pass        # eine kaputte Zeile hält den Empfang nicht an
        finally:
            self._stand_merken(kid, nid)

    async def _anhang_laden(self, nachricht, kopf):
        anhaenge = list(getattr(nachricht, "attachments", None) or [])
        if len(anhaenge) != 1:
            return None
        anhang = anhaenge[0]
        if int(getattr(anhang, "size", 0) or 0) > MAX_ANHANG:
            return None     # gar nicht erst herunterladen
        try:
            daten = await anhang.read()
        except Exception as e:
            self._melden("fehler", text=f"Ein Anhang über Discord ließ sich nicht "
                                        f"laden. (Technisch: {e})")
            return None
        return self.protokoll.anhang_annehmen(kopf, daten)

    def _stand_merken(self, kid, nid: int):
        if nid <= self._staende.get(kid, 0):
            return
        self._staende[kid] = nid
        try:
            self.stand_setzen(kid, str(nid))
        except Exception:
            pass

    # ------------------------------------------------------------ Senden

    def kanal_fuer(self, an: str) -> str:
        return kanal_waehlen(an, self.kanal_ids)

    def senden(self, umschlag_bytes: bytes, an: str, *, fluechtig: bool = False) -> None:
        """Schickt einen Umschlag und wartet, bis er im Kanal steht.

        ConnectionError (mit deutschem Text), wenn Discord nicht verbunden ist
        oder es auch nach 2 Wiederholungen nicht klappt; ValueError, wenn der
        Umschlag kaputt oder zu groß ist. Flüchtiges lehnt diese Funktion
        sofort ab (siehe Kopf der Datei).
        """
        if fluechtig:
            raise ConnectionError(TEXT_FLUECHTIG)
        schritte = self.protokoll.plan(umschlag_bytes, an)     # ValueError
        loop = self._loop
        if not self._laeuft or not self.verbunden or loop is None:
            raise ConnectionError(TEXT_NICHT_VERBUNDEN)
        if threading.current_thread() is self._thread:
            raise RuntimeError("senden() darf nicht aus dem Discord-Thread kommen.")

        _, msg_id = self.protokoll.umschlag_kopf(umschlag_bytes, an)
        zukunft = concurrent.futures.Future()
        auftrag = _Auftrag(self.kanal_fuer(an), schritte, msg_id, zukunft)
        groesse = len(umschlag_bytes)
        try:
            loop.call_soon_threadsafe(self._schlange.put_nowait,
                                      (0, next(self._folge), auftrag))
        except RuntimeError:
            raise ConnectionError(TEXT_NICHT_VERBUNDEN) from None
        # Große Anhänge brauchen auf einer langsamen Leitung länger; 100 KB/s
        # sind dabei die Annahme.
        frist = SENDE_FRIST + groesse / (100 * 1024)
        try:
            zukunft.result(timeout=frist)
        except concurrent.futures.TimeoutError:
            auftrag.abgebrochen = True
            raise ConnectionError("Discord hat zu lange nicht geantwortet. Der "
                                  "Umschlag ist vielleicht nicht angekommen.") from None

    async def _sende_arbeiter(self):
        while True:
            _, _, auftrag = await self._schlange.get()
            if auftrag.abgebrochen:
                continue
            try:
                if auftrag.schritte == "loeschen":
                    await self._loeschen_ausfuehren(auftrag)
                    continue
                ids = await self._auftrag_senden(auftrag)
                self._gesendet_merken(auftrag.msg_id, ids)
                if auftrag.zukunft is not None and not auftrag.zukunft.done():
                    auftrag.zukunft.set_result(None)
            except asyncio.CancelledError:
                if auftrag.zukunft is not None and not auftrag.zukunft.done():
                    auftrag.zukunft.set_exception(ConnectionError(TEXT_NICHT_VERBUNDEN))
                raise
            except Exception as e:
                if auftrag.zukunft is not None and not auftrag.zukunft.done():
                    if not isinstance(e, (ConnectionError, ValueError)):
                        e = ConnectionError(f"Senden über Discord ging nicht. "
                                            f"(Technisch: {e})")
                    auftrag.zukunft.set_exception(e)

    def _kanal_objekt(self, kid):
        kanal = self._kanaele.get(kid)
        if kanal is not None:
            return kid, kanal
        # Der berechnete Kanal ist gerade nicht erreichbar. Gelauscht wird in
        # allen, also kommt es in jedem anderen genauso an.
        for anderer, kanal in self._kanaele.items():
            return anderer, kanal
        raise ConnectionError(TEXT_NICHT_VERBUNDEN)

    async def _auftrag_senden(self, auftrag) -> list:
        kid, kanal = self._kanal_objekt(auftrag.kanal_id)
        ids = []
        for schritt in auftrag.schritte:
            nachricht = await self._mit_wiederholung(kid, kanal, schritt)
            ids.append((kid, int(nachricht.id)))
            # Das eigene Echo kommt gleich über on_message - als gesehen merken.
            self.protokoll.schon_gesehen(int(nachricht.id))
        return ids

    async def _mit_wiederholung(self, kid, kanal, schritt):
        letzter = None
        for versuch in range(len(WIEDERHOLUNGEN) + 1):
            if versuch:
                pause = WIEDERHOLUNGEN[versuch - 1]
                pause = max(pause, _nachgeben(letzter))
                if await self._schlafen(pause):
                    raise ConnectionError(TEXT_NICHT_VERBUNDEN)
            try:
                if schritt[0] == "text":
                    return await kanal.send(schritt[1])
                datei = self._datei_fabrik(io.BytesIO(schritt[2]), filename=ANHANG_NAME)
                return await kanal.send(schritt[1], file=datei)
            except asyncio.CancelledError:
                raise
            except Exception as e:
                letzter = e
                status = getattr(e, "status", None)
                if status == 401:
                    self._melden("update_pruefen")
                    self._zustand_setzen("fehler", TEXT_GESPERRT)
                    raise ConnectionError(TEXT_GESPERRT) from None
                if status == 403:
                    raise ConnectionError(f"Der Bot darf im Discord-Kanal {kid} nicht "
                                          f"schreiben (oder keine Dateien anhängen).") from None
                if status == 404:
                    raise ConnectionError(f"Den Discord-Kanal {kid} gibt es nicht "
                                          f"(mehr).") from None
                if status == 413:
                    raise ValueError("Der Anhang ist Discord zu groß.") from None
        raise ConnectionError(f"Senden über Discord ging auch nach drei Versuchen "
                              f"nicht. (Technisch: {letzter})")

    def _gesendet_merken(self, msg_id, ids):
        self._gesendet[msg_id] = list(ids)
        while len(self._gesendet) > GESENDET_MAX:
            self._gesendet.popitem(last=False)

    def gesendet_fuer(self, msg_id_hex: str) -> list:
        """[(kanal_id, discord_id)], die dieser Client für den Umschlag schrieb."""
        return list(self._gesendet.get(msg_id_hex, []))

    # ----------------------------------------------------------- Löschen

    def loeschen(self, umschlag_msg_id_hex: str) -> None:
        """Löscht im Hintergrund die Discord-Nachrichten zu diesem Umschlag.

        Nur was DIESER Client geschickt hat (seit dem Start, höchstens die
        letzten GESENDET_MAX). Niedrige Priorität: Senden geht immer vor.
        Klappt es nicht, bleibt die Zeile eben stehen - sie ist Geheimtext.
        """
        loop = self._loop
        if not self._laeuft or loop is None or umschlag_msg_id_hex not in self._gesendet:
            return
        auftrag = _Auftrag(None, "loeschen", umschlag_msg_id_hex, None)
        try:
            loop.call_soon_threadsafe(self._schlange.put_nowait,
                                      (1, next(self._folge), auftrag))
        except RuntimeError:
            pass

    async def _loeschen_ausfuehren(self, auftrag):
        eintraege = self._gesendet.pop(auftrag.msg_id, [])
        for kid, did in eintraege:
            kanal = self._kanaele.get(kid)
            if kanal is None:
                continue
            try:
                await kanal.get_partial_message(did).delete()
            except Exception:
                pass


# =============================================================================
#  Attrappe für die Selbsttests (ohne discord.py, ohne Netz)
# =============================================================================

class FakeAnhang:
    def __init__(self, daten: bytes, filename: str):
        self._daten = bytes(daten)
        self.size = len(self._daten)
        self.filename = filename
        self.geladen = 0            # wie oft read() lief - für den Filtertest

    async def read(self):
        self.geladen += 1
        return self._daten


class FakeNachricht:
    def __init__(self, kanal, id, content, attachments=()):
        self.channel = kanal
        self.id = id
        self.content = content
        self.attachments = list(attachments)

    async def delete(self):
        self.channel._entfernen(self.id)


class _FakeTeilnachricht:
    def __init__(self, kanal, id):
        self.channel = kanal
        self.id = id

    async def delete(self):
        self.channel._entfernen(self.id)


class FakeKanal:
    """Ein Discord-Kanal im Speicher, den mehrere DiscordNetz teilen können.

    send() legt die Nachricht ab und stellt sie allen Abonnenten zu - auch
    dem Absender selbst, genau wie Discord das eigene Echo schickt.
    history() liefert wie discord.py alles nach `after`, älteste zuerst.
    `fehler_beim_senden = n` lässt die nächsten n send()-Aufrufe scheitern.
    """

    _ids = itertools.count(_jetzt_snowflake())

    def __init__(self, id):
        self.id = int(id)
        self.nachrichten = []
        self.gesendet = 0
        self.fehler_beim_senden = 0
        self.fehler = OSError("Attrappe: Netz weg")
        self.zustellen = True       # False = nur ablegen (für Nachhol-Tests)
        self._abonnenten = []
        self._lock = threading.Lock()

    def abonnieren(self, netz):
        with self._lock:
            if netz not in self._abonnenten:
                self._abonnenten.append(netz)

    def abbestellen(self, netz):
        with self._lock:
            if netz in self._abonnenten:
                self._abonnenten.remove(netz)

    def ablegen(self, content, daten=None) -> FakeNachricht:
        """Eine Nachricht ablegen, ohne sie zuzustellen (wie "während man
        offline war")."""
        anhaenge = [FakeAnhang(daten, ANHANG_NAME)] if daten is not None else []
        n = FakeNachricht(self, next(FakeKanal._ids), content, anhaenge)
        with self._lock:
            self.nachrichten.append(n)
        return n

    async def send(self, content, file=None):
        if self.fehler_beim_senden > 0:
            self.fehler_beim_senden -= 1
            raise self.fehler
        daten = file.fp.read() if file is not None else None
        n = self.ablegen(content, daten)
        self.gesendet += 1
        if self.zustellen:
            with self._lock:
                abonnenten = list(self._abonnenten)
            for netz in abonnenten:
                netz.einspeisen(n)
        return n

    async def history(self, limit=100, after=None, oldest_first=True):
        grenze = int(getattr(after, "id", 0) or 0)
        with self._lock:
            liste = sorted((n for n in self.nachrichten if n.id > grenze),
                           key=lambda n: n.id, reverse=not oldest_first)
        for n in liste[:limit]:
            yield n

    def get_partial_message(self, id):
        return _FakeTeilnachricht(self, id)

    def _entfernen(self, id):
        with self._lock:
            self.nachrichten = [n for n in self.nachrichten if n.id != id]

