# -*- coding: utf-8 -*-
"""Prüfungen für netz/: WLAN (echt über localhost), Discord (Protokoll rein
rechnerisch, Transport mit FakeKanal statt discord.py) und den Vermittler.

Wird von test_vp4.py über pruefen(R, hilfen) aufgerufen. Ein echter Bot und
ein echtes WLAN zwischen zwei PCs kommen hier nicht vor - beides lässt sich
nur von Hand prüfen.
"""

import base64
import hashlib
import inspect
import os
import random
import socket
import struct
import sys
import tempfile
import threading
import time
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

os.environ.setdefault("VP4_TESTMODUS", "1")

from kern import umschlag  # noqa: E402
from kern.identitaet import CROCKFORD  # noqa: E402
from kern.umschlag import Umschlag  # noqa: E402
from netz import discord_netz as dn  # noqa: E402
from netz import lan as lan_modul  # noqa: E402
from netz import vermittler as vm  # noqa: E402
from netz.discord_netz import (DiscordNetz, DiscordProtokoll2, FakeKanal,  # noqa: E402
                               FakeNachricht)
from netz.lan import LanNetz  # noqa: E402
from netz.vermittler import Vermittler  # noqa: E402


# ---------------------------------------------------------------------------
#  Hilfen
# ---------------------------------------------------------------------------

def _id():
    z = "".join(random.choice(CROCKFORD) for _ in range(10))
    return z[:5] + "-" + z[5:]


def _community():
    return "G-" + "".join(random.choice(CROCKFORD) for _ in range(8))


def _umschlag(von, an, groesse=200) -> bytes:
    return Umschlag(umschlag.NACHRICHT, von, an, os.urandom(16), int(time.time() * 1000),
                    os.urandom(12), os.urandom(groesse)).packen()


def _msg_id(daten: bytes) -> str:
    return Umschlag.entpacken(daten).msg_id.hex()


def _warten(bedingung, frist=3.0):
    ende = time.monotonic() + frist
    while time.monotonic() < ende:
        if bedingung():
            return True
        time.sleep(0.02)
    return bool(bedingung())


def _freier_udp_port():
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


class _Sammler:
    """Nimmt Umschläge und Ereignisse entgegen (threadsicher genug für Tests)."""

    def __init__(self):
        self.umschlaege = []
        self.ereignisse = []
        self._lock = threading.Lock()

    def empfangen(self, daten, weg, adresse=None):
        with self._lock:
            self.umschlaege.append((daten, weg))

    def ereignis(self, typ, **daten):
        with self._lock:
            self.ereignisse.append((typ, daten))

    def hat(self, daten):
        with self._lock:
            return any(d == daten for d, _ in self.umschlaege)

    def anzahl(self, daten):
        with self._lock:
            return sum(1 for d, _ in self.umschlaege if d == daten)


def _lan_paar(ablauf=12.0):
    ua, ub = _freier_udp_port(), _freier_udp_port()
    sa, sb = _Sammler(), _Sammler()
    ida, idb = _id(), _id()
    a = LanNetz(ida, sa.empfangen, sa.ereignis, port_tcp=0, port_udp=ua,
                bind_host="127.0.0.1", ziele=[("127.0.0.1", ub)], intervall=0.2,
                ablauf=ablauf)
    b = LanNetz(idb, sb.empfangen, sb.ereignis, port_tcp=0, port_udp=ub,
                bind_host="127.0.0.1", ziele=[("127.0.0.1", ua)], intervall=0.2)
    return a, b, sa, sb, ida, idb, ua, ub


def _rahmen(daten: bytes) -> bytes:
    return struct.pack("!I", len(daten)) + daten


# ---------------------------------------------------------------------------
#  WLAN
# ---------------------------------------------------------------------------

def _pruefen_lan(R):
    print("\n--- netz/lan.py: WLAN über localhost ---")

    R.pruefe("WLAN-Ports liegen unter 49152",
             lan_modul.CHAT_PORT < 49152 and lan_modul.BROADCAST_PORT < 49152,
             f"{lan_modul.BROADCAST_PORT}/{lan_modul.CHAT_PORT}")
    standard = inspect.signature(LanNetz.__init__).parameters
    R.pruefe("LanNetz benutzt standardmäßig 41230/41231",
             {standard["port_tcp"].default, standard["port_udp"].default} == {41230, 41231},
             f"{standard['port_tcp'].default}/{standard['port_udp'].default}")

    a, b, sa, sb, ida, idb, ua, ub = _lan_paar(ablauf=1.0)
    c = None
    geist = None
    blockierer = None
    try:
        a.start()
        b.start()
        R.pruefe("Zwei LanNetz starten auf freien Ports",
                 a.zustand == "an" and b.zustand == "an" and a.echter_tcp_port
                 and b.echter_tcp_port, f"{a.zustand}/{b.zustand}")
        R.pruefe("Die Instanzen finden sich über die Erkennung",
                 _warten(lambda: a.ist_online(idb) and b.ist_online(ida)),
                 f"{a.online_ids()} / {b.online_ids()}")
        R.pruefe("Der echte TCP-Port steht im Erkennungspaket",
                 a.adresse_von(idb) == ("127.0.0.1", b.echter_tcp_port),
                 str(a.adresse_von(idb)))

        u1 = _umschlag(ida, idb)
        a.senden(u1, idb)
        R.pruefe("Umschlag A → B kommt an", _warten(lambda: sb.hat(u1)))
        R.pruefe("Der Weg heißt 'lan'", sb.umschlaege and sb.umschlaege[0][1] == "lan")
        u2 = _umschlag(idb, ida)
        b.senden(u2, ida)
        R.pruefe("Umschlag B → A kommt an", _warten(lambda: sa.hat(u2)))
        gross = _umschlag(ida, idb, 3 * 1024 * 1024)
        a.senden(gross, idb)
        R.pruefe("Ein 3-MB-Umschlag kommt Byte für Byte an", _warten(lambda: sb.hat(gross), 5))
        u3 = _umschlag(ida, idb)
        a.senden(u3, idb)
        R.pruefe("Die gehaltene Verbindung trägt weitere Umschläge",
                 _warten(lambda: sb.hat(u3)) and len(a._ausgehend) == 1)

        try:
            a.senden(_umschlag(ida, idb), _id())
            R.pruefe("Unbekannte Gegenstelle: ConnectionError", False, "kein Fehler")
        except ConnectionError as e:
            R.pruefe("Unbekannte Gegenstelle: ConnectionError mit deutschem Text",
                     "WLAN" in str(e), str(e))

        # --- Kaputte Rahmen ------------------------------------------
        roh = socket.create_connection(("127.0.0.1", b.echter_tcp_port), timeout=3)
        gut = _umschlag(ida, idb)
        roh.sendall(_rahmen(b"kein umschlag") + _rahmen(b"") + _rahmen(gut))
        R.pruefe("Ein unlesbarer Rahmen hält die Verbindung nicht an",
                 _warten(lambda: sb.hat(gut)))
        roh.close()

        roh = socket.create_connection(("127.0.0.1", b.echter_tcp_port), timeout=3)
        roh.sendall(struct.pack("!I", umschlag.MAX_UMSCHLAG + 1))
        roh.settimeout(3)
        try:
            zu = roh.recv(1) == b""
        except OSError:
            zu = True
        roh.close()
        R.pruefe("Eine unsinnige Längenangabe beendet nur diese Verbindung", zu)
        roh = socket.create_connection(("127.0.0.1", b.echter_tcp_port), timeout=3)
        gut2 = _umschlag(ida, idb)
        roh.sendall(_rahmen(gut2))
        R.pruefe("Danach kommt der nächste gute Rahmen trotzdem an",
                 _warten(lambda: sb.hat(gut2)))
        roh.close()

        # --- Eine hängende Gegenstelle blockiert die anderen nicht -----
        geist_id = _id()
        geist = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        geist.bind(("127.0.0.1", 0))
        geist.listen(1)          # nimmt an, liest aber nie
        paket = ('{"v":2,"id":"%s","port":%d}' % (geist_id, geist.getsockname()[1])).encode()
        udp = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        udp.sendto(paket, ("127.0.0.1", ua))
        udp.close()
        _warten(lambda: a.ist_online(geist_id))
        haengt = threading.Event()

        def an_geist():
            try:
                a.senden(_umschlag(ida, geist_id, 40 * 1024 * 1024), geist_id)
            except Exception:
                pass
            haengt.set()

        blockierer = threading.Thread(target=an_geist, daemon=True)
        blockierer.start()
        time.sleep(0.5)
        R.pruefe("Der Sender an die hängende Gegenstelle steckt fest",
                 not haengt.is_set())
        start = time.monotonic()
        u4 = _umschlag(ida, idb)
        a.senden(u4, idb)
        dauer = time.monotonic() - start
        R.pruefe("Senden an B geht, während eine andere Gegenstelle hängt",
                 _warten(lambda: sb.hat(u4)) and dauer < 2.0,
                 f"{dauer:.2f} s, Geist hängt noch: {not haengt.is_set()}")

        # --- Eine verschwundene Gegenstelle ----------------------------
        uc = _freier_udp_port()
        sc = _Sammler()
        idc = _id()
        c = LanNetz(idc, sc.empfangen, sc.ereignis, port_tcp=0, port_udp=uc,
                    bind_host="127.0.0.1", ziele=[("127.0.0.1", ua)], intervall=0.2)
        c.start()
        R.pruefe("Ein drittes Gerät wird gefunden", _warten(lambda: a.ist_online(idc)))
        c.stop()
        c = None
        start = time.monotonic()
        try:
            a.senden(_umschlag(ida, idc), idc)
            weg_ok = False
        except ConnectionError:
            weg_ok = True
        R.pruefe("An ein beendetes Gerät: schnell ConnectionError",
                 weg_ok and time.monotonic() - start < 6.0)
        R.pruefe("Ein beendetes Gerät verschwindet nach der Ablaufzeit",
                 _warten(lambda: not a.ist_online(idc), 4))
        u5 = _umschlag(ida, idb)
        a.senden(u5, idb)
        R.pruefe("Danach geht Senden an B weiter", _warten(lambda: sb.hat(u5)))
        R.pruefe("Ereignis 'online' meldet die Liste",
                 any(t == "online" for t, _ in sa.ereignisse))
    except Exception as e:
        R.fehlschlag("WLAN-Prüfungen liefen nicht durch", e)
    finally:
        zeiten = []
        for netz in (a, b, c):
            if netz is None:
                continue
            t0 = time.monotonic()
            netz.stop()
            zeiten.append(time.monotonic() - t0)
        if geist is not None:
            geist.close()
        R.pruefe("stop() ist in unter 2,5 s fertig", max(zeiten) < 2.5,
                 ", ".join(f"{z:.2f}" for z in zeiten))
        R.pruefe("Nach stop() läuft kein WLAN-Thread mehr",
                 a.offene_threads() == 0 and b.offene_threads() == 0,
                 f"{a.offene_threads()}/{b.offene_threads()}")
        if blockierer is not None:
            blockierer.join(3)
            R.pruefe("stop() befreit auch einen hängenden Sender",
                     not blockierer.is_alive())
        R.pruefe("Nach stop() meldet LanNetz 'aus'",
                 a.zustand == "aus" and sa.ereignisse[-1][0] == "verbindung"
                 and sa.ereignisse[-1][1]["zustand"] == {"lan": "aus"})
        try:
            a.senden(_umschlag(ida, idb), idb)
            R.pruefe("Nach stop() wird nichts mehr gesendet", False)
        except ConnectionError:
            R.pruefe("Nach stop() wird nichts mehr gesendet", True)


# ---------------------------------------------------------------------------
#  Discord: Protokoll
# ---------------------------------------------------------------------------

def _pruefen_protokoll(R):
    print("\n--- netz/discord_netz.py: Protokoll ---")
    ich, du, fremd = _id(), _id(), _id()
    gruppe = _community()
    sender = DiscordProtokoll2(ich)
    empf = DiscordProtokoll2(du, interessant=lambda an: an in (du, gruppe))

    klein = _umschlag(ich, du, 100)
    zeilen = sender.zeilen_bauen(klein, du)
    R.pruefe("Kleiner Umschlag: eine Zeile mit VP4D2-Kopf",
             len(zeilen) == 1 and zeilen[0].startswith(f"VP4D2|{ich}|{du}|{_msg_id(klein)}|1|1|"),
             zeilen[0][:80])
    R.pruefe("Kleiner Umschlag kommt zurück", empf.zeile_lesen(zeilen[0]) == ("fertig", klein))

    mittel = _umschlag(ich, du, dn.TEXT_MAX - umschlag.KOPF_LAENGE)
    plan = sender.plan(mittel, du)
    R.pruefe("6 KiB gehen als mehrere Text-Zeilen",
             len(plan) > 1 and all(s[0] == "text" for s in plan), str(len(plan)))
    R.pruefe("Keine Zeile ist länger als 1900 Zeichen",
             all(len(s[1]) <= dn.ZEILEN_LIMIT for s in plan),
             str(max(len(s[1]) for s in plan)))
    gemischt = [s[1] for s in plan]
    random.shuffle(gemischt)
    ergebnisse = [empf.zeile_lesen(z) for z in gemischt]
    R.pruefe("In verkehrter Reihenfolge zusammengesetzt",
             ergebnisse[-1] == ("fertig", mittel) and all(e is None for e in ergebnisse[:-1]))
    R.pruefe("Danach ist nichts mehr offen", empf.offen() == 0)

    gross = _umschlag(ich, du, 200 * 1024)
    plan = sender.plan(gross, du)
    R.pruefe("Großer Umschlag: genau ein Anhang-Schritt",
             len(plan) == 1 and plan[0][0] == "anhang" and plan[0][2] == gross)
    kopfzeile = plan[0][1]
    R.pruefe("Anhang-Kopf endet auf |A|1|",
             kopfzeile == f"VP4D2|{ich}|{du}|{_msg_id(gross)}|A|1|", kopfzeile)
    gelesen = empf.zeile_lesen(kopfzeile)
    R.pruefe("Anhang-Kopf wird als Anhang erkannt",
             gelesen is not None and gelesen[0] == "anhang" and gelesen[1].ist_anhang)
    R.pruefe("Anhang wird angenommen", empf.anhang_annehmen(gelesen[1], gross) == gross)
    R.pruefe("Fremder Anhang (passt nicht zum Kopf) wird verworfen",
             empf.anhang_annehmen(gelesen[1], _umschlag(ich, du, 50)) is None)
    zu_gross = _umschlag(ich, du, dn.MAX_ANHANG)
    try:
        sender.plan(zu_gross, du)
        R.pruefe("Über 9,5 MiB: ValueError", False)
    except ValueError as e:
        R.pruefe("Über 9,5 MiB: ValueError mit Hinweis aufs Zerlegen", "Teile" in str(e), str(e))
    R.pruefe("Umschlag an jemand anderen als 'an': ValueError",
             hilfen_wirft(lambda: sender.plan(klein, fremd)))

    # interessant
    an_fremd = sender.zeilen_bauen(_umschlag(ich, fremd), fremd)[0]
    R.pruefe("Zeile an jemand anderen wird ignoriert", empf.zeile_lesen(an_fremd) is None)
    an_gruppe = _umschlag(ich, gruppe)
    R.pruefe("Zeile an eine eigene Community kommt an",
             empf.zeile_lesen(sender.zeilen_bauen(an_gruppe, gruppe)[0]) == ("fertig", an_gruppe))
    andere_gruppe = _community()
    R.pruefe("Zeile an eine fremde Community wird ignoriert",
             empf.zeile_lesen(sender.zeilen_bauen(_umschlag(ich, andere_gruppe),
                                                  andere_gruppe)[0]) is None)
    echo = DiscordProtokoll2(ich)
    R.pruefe("Das eigene Echo wird ignoriert", echo.zeile_lesen(zeilen[0]) is None)
    R.pruefe("Fremder Anhang-Kopf wird gar nicht erst als Anhang gemeldet",
             empf.zeile_lesen(sender.plan(_umschlag(ich, fremd, 10000), fremd)[0][1]) is None)

    # Kanäle
    kanaele = ["111111111111111111", "222222222222222222", "333333333333333333"]
    ziele = [_id() for _ in range(60)]
    R.pruefe("Kanalwahl ist stabil",
             all(dn.kanal_waehlen(z, kanaele) == dn.kanal_waehlen(z, list(kanaele)) for z in ziele))
    R.pruefe("Kanalwahl = sha256(an) mod n",
             all(dn.kanal_waehlen(z, kanaele)
                 == kanaele[int(hashlib.sha256(z.encode()).hexdigest(), 16) % 3] for z in ziele))
    R.pruefe("Gespräche verteilen sich auf mehrere Kanäle",
             len({dn.kanal_waehlen(z, kanaele) for z in ziele}) == 3)

    # Verfallen
    uhr = [1000.0]
    langsam = DiscordProtokoll2(du, uhr=lambda: uhr[0])
    teile = [s[1] for s in sender.plan(mittel, du)]
    langsam.zeile_lesen(teile[0])
    R.pruefe("Ein angefangener Umschlag bleibt offen", langsam.offen() == 1)
    uhr[0] += dn.TEILE_TIMEOUT + 1
    langsam.aufraeumen()
    R.pruefe("Nach 300 s fliegt er raus", langsam.offen() == 0)
    rest = [langsam.zeile_lesen(z) for z in teile[1:]]
    R.pruefe("Die späten Reste ergeben keinen Umschlag", all(r is None for r in rest))

    # Kaputte Zeilen
    mid = _msg_id(klein)
    b64 = base64.b64encode(klein).decode()
    kaputt = ["", "Hallo zusammen", "VP4D2", "VP4D2|x|y|z|1|1|abc",
              f"VP4D1|{ich}|{du}|{mid}|1|1|{b64}",                    # alte Marke
              f"VP4D2|{ich}|{du}|{mid[:-1]}|1|1|{b64}",               # ID zu kurz
              f"VP4D2|{ich}|{du}|{mid.upper()}|1|1|{b64}",            # Großbuchstaben
              f"VP4D2|{ich}|{du}|{mid}|2|1|{b64}",                    # teil > gesamt
              f"VP4D2|{ich}|{du}|{mid}|0|1|{b64}",
              f"VP4D2|{ich}|{du}|{mid}|1|99|{b64}",                   # zu viele Teile
              f"VP4D2|{ich}|{du}|{mid}|1|1|",                          # leer
              f"VP4D2|{ich}|{du}|{mid}|1|1|%%%nicht-base64%%%",
              f"VP4D2|{ich}|{du}|{mid}|A|1|nutzlast",                 # Anhang mit Text
              f"VP4D2|{ich}|{du}|{mid}|A|2|",
              f"VP4D2|{fremd}|{du}|{mid}|1|1|{b64}",                  # Kopf ≠ Umschlag
              f"VP4D2|{ich}|{du}|{'0' * 32}|1|1|{b64}",               # msg_id ≠ Umschlag
              f"VP4D2|{ich}|{du}|{mid}|1|1|" + base64.b64encode(b"Unsinn").decode(),
              "VP4D2|" + "x" * 2100]
    try:
        r = [DiscordProtokoll2(du).zeile_lesen(z) for z in kaputt]
        R.pruefe("Kaputte Zeilen werden still verworfen", all(x is None for x in r),
                 str([i for i, x in enumerate(r) if x is not None]))
    except Exception as e:
        R.fehlschlag("Kaputte Zeilen werden still verworfen", e)

    g = DiscordProtokoll2(du)
    R.pruefe("Discord-ID wird beim ersten Mal nicht als gesehen gemeldet",
             g.schon_gesehen(42) is False and g.schon_gesehen(42) is True)


def hilfen_wirft(f):
    try:
        f()
    except ValueError:
        return True
    return False


# ---------------------------------------------------------------------------
#  Discord: Transport mit FakeKanal
# ---------------------------------------------------------------------------

def _netz(eigene, kanal_ids, interessant=None, staende=None):
    s = _Sammler()
    staende = staende if staende is not None else {}
    n = DiscordNetz(eigene, s.empfangen, s.ereignis, "token", kanal_ids,
                    stand_holen=lambda k: staende.get(k),
                    stand_setzen=lambda k, v: staende.__setitem__(k, v),
                    interessant=interessant)
    return n, s, staende


def _pruefen_discord_transport(R):
    print("\n--- netz/discord_netz.py: Transport mit FakeKanal ---")
    ids = ["100000000000000001", "100000000000000002"]

    # Testmodus
    n, s, _ = _netz(_id(), ids)
    n.start()
    R.pruefe("DiscordNetz.start() tut im VP4_TESTMODUS nichts",
             os.environ.get("VP4_TESTMODUS") and n._thread is None and not n.verbunden)
    irgendwer = _id()
    try:
        n.senden(_umschlag(n.eigene_id, irgendwer), irgendwer)
        R.pruefe("Ohne Verbindung: ConnectionError", False)
    except ValueError:
        R.pruefe("Ohne Verbindung: ConnectionError", False, "ValueError statt ConnectionError")
    except ConnectionError as e:
        R.pruefe("Ohne Verbindung: ConnectionError mit deutschem Text", "Discord" in str(e))

    kanaele = {k: FakeKanal(k) for k in ids}
    ida, idb, idc = _id(), _id(), _id()
    gruppe = _community()
    a, sa, _ = _netz(ida, ids)
    b, sb, staende_b = _netz(idb, ids, interessant=lambda an: an in (idb, gruppe))
    c, sc, _ = _netz(idc, ids)
    alte_pausen = dn.WIEDERHOLUNGEN
    try:
        for x in (a, b, c):
            x.mit_attrappe_starten(kanaele)
        R.pruefe("Mit Attrappe verbunden", a.verbunden and b.verbunden and a.zustand == "an")

        u = _umschlag(ida, idb, 300)
        a.senden(u, idb)
        R.pruefe("Text-Umschlag A → B kommt über Discord an", _warten(lambda: sb.hat(u)))
        R.pruefe("... mit Weg 'discord'", sb.umschlaege and sb.umschlaege[-1][1] == "discord")
        ziel = kanaele[dn.kanal_waehlen(idb, ids)]
        R.pruefe("... im berechneten Kanal",
                 any(m.content.startswith(f"VP4D2|{ida}|{idb}|{_msg_id(u)}|") for m in ziel.nachrichten))
        R.pruefe("C bekommt den Umschlag an B nicht", not sc.hat(u) and not sa.hat(u))

        gross = _umschlag(ida, idb, 300 * 1024)
        a.senden(gross, idb)
        R.pruefe("Anhang-Umschlag A → B kommt an", _warten(lambda: sb.hat(gross)))
        anhang_msg = [m for k in kanaele.values() for m in k.nachrichten if m.attachments]
        R.pruefe("Den Anhang lädt nur der Empfänger herunter",
                 len(anhang_msg) == 1 and anhang_msg[0].attachments[0].geladen == 1
                 and anhang_msg[0].attachments[0].filename == "vp4.bin",
                 str([m.attachments[0].geladen for m in anhang_msg]))

        ug = _umschlag(ida, gruppe, 500)
        a.senden(ug, gruppe)
        R.pruefe("Community-Umschlag kommt beim Mitglied an", _warten(lambda: sb.hat(ug)))
        time.sleep(0.1)
        R.pruefe("... und nicht beim Nicht-Mitglied", not sc.hat(ug))

        mittel = _umschlag(ida, idb, 5000)
        a.senden(mittel, idb)
        R.pruefe("Mehrzeiliger Umschlag kommt genau einmal an",
                 _warten(lambda: sb.hat(mittel)) and sb.anzahl(mittel) == 1)

        # Flüchtiges
        vorher = sum(k.gesendet for k in kanaele.values())
        try:
            a.senden(_umschlag(ida, idb), idb, fluechtig=True)
            R.pruefe("Flüchtiges geht nie über Discord", False)
        except ConnectionError:
            R.pruefe("Flüchtiges geht nie über Discord",
                     sum(k.gesendet for k in kanaele.values()) == vorher)

        # Wiederholen
        dn.WIEDERHOLUNGEN = (0.05, 0.1)
        ziel.fehler_beim_senden = 1
        u2 = _umschlag(ida, idb)
        a.senden(u2, idb)
        R.pruefe("Nach einem Fehlschlag wird wiederholt", _warten(lambda: sb.hat(u2)))
        ziel.fehler_beim_senden = 5
        try:
            a.senden(_umschlag(ida, idb), idb)
            R.pruefe("Nach 2 Wiederholungen: ConnectionError", False)
        except ConnectionError as e:
            R.pruefe("Nach 2 Wiederholungen: ConnectionError",
                     ziel.fehler_beim_senden == 2 and "drei Versuchen" in str(e), str(e))
        ziel.fehler_beim_senden = 3
        ziel.fehler = type("Verboten", (Exception,), {"status": 403})()
        try:
            a.senden(_umschlag(ida, idb), idb)
            R.pruefe("403 wird nicht wiederholt", False)
        except ConnectionError as e:
            R.pruefe("403 wird nicht wiederholt und nennt den Kanal",
                     ziel.fehler_beim_senden == 2 and str(ziel.id) in str(e), str(e))
        ziel.fehler_beim_senden = 0

        # Löschen
        u3 = _umschlag(ida, idb, 100)
        a.senden(u3, idb)
        gesendet = a.gesendet_fuer(_msg_id(u3))
        R.pruefe("A merkt sich, welche Discord-Nachrichten zum Umschlag gehören",
                 len(gesendet) == 1)
        a.loeschen(_msg_id(u3))
        R.pruefe("loeschen() entfernt sie im Hintergrund",
                 _warten(lambda: not any(m.id == gesendet[0][1]
                                         for k in kanaele.values() for m in k.nachrichten)))

        R.pruefe("Der Stand wird pro Kanal fortgeschrieben",
                 staende_b.get(ziel and str(ziel.id)) is not None, str(staende_b))
    except Exception as e:
        R.fehlschlag("Discord-Transport lief nicht durch", e)
    finally:
        dn.WIEDERHOLUNGEN = alte_pausen
        for x in (a, b, c):
            x.stop()
    R.pruefe("stop() beendet den Discord-Thread",
             all(x._thread is None or not x._thread.is_alive() for x in (a, b, c)))

    # --- Nachholen ------------------------------------------------------
    print("\n--- netz/discord_netz.py: Nachholen ---")
    kanaele = {k: FakeKanal(k) for k in ids}
    ida, idd = _id(), _id()
    protokoll = DiscordProtokoll2(ida)
    staende = {}
    d, sd = None, None
    try:
        alt_kanal = kanaele[ids[0]]
        uralt = _umschlag(ida, idd)
        alte_id = dn._jetzt_snowflake(8 * 86400)
        alt_kanal.nachrichten.append(FakeNachricht(alt_kanal, alte_id,
                                                   protokoll.zeilen_bauen(uralt, idd)[0]))
        offline = [_umschlag(ida, idd, 100 + i) for i in range(5)]
        for i, u in enumerate(offline):
            k = kanaele[ids[i % 2]]
            for z in protokoll.zeilen_bauen(u, idd):
                k.ablegen(z)
            k.ablegen("irgendwas anderes im Kanal")
            fremd = _id()
            k.ablegen(protokoll.zeilen_bauen(_umschlag(ida, fremd), fremd)[0])
        mehrteilig = _umschlag(ida, idd, 5000)
        teile = protokoll.zeilen_bauen(mehrteilig, idd)
        random.shuffle(teile)
        for z in teile:
            kanaele[ids[1]].ablegen(z)

        d, sd, staende = _netz(idd, ids, staende=staende)
        d.mit_attrappe_starten(kanaele)
        R.pruefe("Beim Verbinden wird alles Verpasste nachgeholt",
                 _warten(lambda: all(sd.hat(u) for u in offline) and sd.hat(mehrteilig)),
                 f"{len(sd.umschlaege)} von {len(offline) + 1}")
        R.pruefe("Älter als 7 Tage wird nicht nachgeholt", not sd.hat(uralt))
        reihenfolge = [x for x, _ in sd.umschlaege if x in offline]
        k0 = [u for i, u in enumerate(offline) if i % 2 == 0]
        R.pruefe("Pro Kanal in der richtigen Reihenfolge",
                 [x for x in reihenfolge if x in k0] == k0)
        hoechste = {k: str(max(m.id for m in kanaele[k].nachrichten)) for k in ids}
        R.pruefe("Der Stand steht danach auf der letzten Nachricht",
                 _warten(lambda: staende == hoechste), f"{staende} / {hoechste}")

        # Live-Doppel: dieselbe Discord-Nachricht noch einmal "live"
        schon = kanaele[ids[0]].nachrichten[-1]
        vorher = len(sd.umschlaege)
        d.einspeisen(schon)
        live = _umschlag(ida, idd, 77)
        live_msg = kanaele[ids[0]].ablegen(protokoll.zeilen_bauen(live, idd)[0])
        d.einspeisen(live_msg)
        d.einspeisen(live_msg)
        R.pruefe("Live danach kommt genau einmal an",
                 _warten(lambda: sd.hat(live)) and sd.anzahl(live) == 1)
        R.pruefe("Nachgeholtes kommt live nicht noch einmal",
                 len(sd.umschlaege) == vorher + 1, f"{vorher} → {len(sd.umschlaege)}")
        d.stop()

        # Neustart: nur das Neue
        neu = _umschlag(ida, idd, 55)
        kanaele[ids[1]].ablegen(protokoll.zeilen_bauen(neu, idd)[0])
        d, sd, staende = _netz(idd, ids, staende=staende)
        d.mit_attrappe_starten(kanaele)
        R.pruefe("Nach einem Neustart wird nur Neues nachgeholt",
                 _warten(lambda: sd.hat(neu)) and len(sd.umschlaege) == 1,
                 str(len(sd.umschlaege)))
    except Exception as e:
        R.fehlschlag("Nachholen lief nicht durch", e)
    finally:
        if d is not None:
            d.stop()


def _pruefen_discord_fehler(R):
    """Die Übersetzung der discord.py-Fehler - ohne Verbindung prüfbar."""
    print("\n--- netz/discord_netz.py: Fehlermeldungen ---")
    try:
        import discord
    except ImportError:
        print("  (übersprungen: discord.py ist nicht installiert)")
        return
    import asyncio
    s = _Sammler()
    n = DiscordNetz(_id(), s.empfangen, s.ereignis, "t", ["1" * 18])
    endgueltig, text = n._fehler_deuten(discord.LoginFailure("Improper token"), discord)
    R.pruefe("Abgelehnter Token: endgültig, Hinweis auf neue Version",
             endgueltig and text == dn.TEXT_GESPERRT)
    R.pruefe("Abgelehnter Token löst die Update-Prüfung aus",
             any(t == "update_pruefen" for t, _ in s.ereignisse))
    endgueltig, text = n._fehler_deuten(discord.PrivilegedIntentsRequired(None), discord)
    R.pruefe("Fehlender Intent: endgültig, erklärt Message Content",
             endgueltig and "MESSAGE CONTENT" in text)
    endgueltig, text = n._fehler_deuten(OSError("Netz weg"), discord)
    R.pruefe("Netzfehler: nicht endgültig (neuer Versuch)", not endgueltig and "online" in text)

    class _Http:
        def __init__(self, rest):
            self.rest = rest

        async def get_bot_gateway(self):
            return 1, "wss://x", {"total": 1000, "remaining": self.rest,
                                  "reset_after": 7200000, "max_concurrency": 1}

    knapp = asyncio.run(n._sitzungen_pruefen(SimpleNamespace(http=_Http(3)), discord))
    R.pruefe("Fast aufgebrauchtes Anmeldebudget: warten statt anmelden",
             knapp >= 7000 and n.zustand == "fehler" and "Anmeldungen" in n.meldung,
             f"{knapp} / {n.meldung}")
    genug = asyncio.run(n._sitzungen_pruefen(SimpleNamespace(http=_Http(900)), discord))
    R.pruefe("Genug Anmeldebudget: sofort weiter", genug == 0)
    R.pruefe("429: retry_after wird beachtet",
             dn._nachgeben(SimpleNamespace(retry_after=2.5)) == 2.5
             and dn._nachgeben(ValueError()) == 0.0)


# ---------------------------------------------------------------------------
#  Zugangsdaten
# ---------------------------------------------------------------------------

def _pruefen_zugang(R):
    print("\n--- netz/discord_netz.py: Zugangsdaten ---")
    k1, k2, k3 = "111111111111111111", "222222222222222222", "333333333333333333"
    with tempfile.TemporaryDirectory() as tmp:
        datei = Path(tmp) / "discord.json"
        ein = SimpleNamespace(BOT_TOKEN="eingebaut", KANAL_IDS=f"{k1}, {k2}")
        z = dn.zugang_laden(ein, datei)
        R.pruefe("Ohne eigene Werte gilt der eingebaute Zugang",
                 z == {"token": "eingebaut", "kanal_ids": [k1, k2], "eingebaut": True}, str(z))
        datei.write_text('{"bot_token": "eigen", "kanal_ids": "%s"}' % k3, encoding="utf-8")
        z = dn.zugang_laden(ein, datei)
        R.pruefe("Eigene Werte gewinnen",
                 z == {"token": "eigen", "kanal_ids": [k3], "eingebaut": False}, str(z))
        datei.write_text('{"bot_token": "  ", "kanal_ids": ""}', encoding="utf-8")
        z = dn.zugang_laden(ein, datei)
        R.pruefe("Leere eigene Werte löschen die eingebauten nicht",
                 z == {"token": "eingebaut", "kanal_ids": [k1, k2], "eingebaut": True}, str(z))
        datei.write_text('{"kanal_id": "%s"}' % k3, encoding="utf-8")
        z = dn.zugang_laden(SimpleNamespace(BOT_TOKEN="t", KANAL_ID=k1), datei)
        R.pruefe("Alte Einzel-Kanal-ID (4.x) wird gelesen",
                 z["kanal_ids"] == [k3] and dn.zugang_laden(SimpleNamespace(BOT_TOKEN="t", KANAL_ID=k1),
                                                            Path(tmp) / "fehlt.json")["kanal_ids"] == [k1],
                 str(z))
        z = dn.zugang_laden(SimpleNamespace(), Path(tmp) / "fehlt.json")
        R.pruefe("Ganz ohne Zugang: leer, nicht eingebaut",
                 z == {"token": "", "kanal_ids": [], "eingebaut": False}, str(z))
        ziel = Path(tmp) / "neu.json"
        erkannt = dn.zugang_speichern("x", f"{k1} {k2},{k1}", ziel)
        R.pruefe("zugang_speichern erkennt die Kanal-IDs", erkannt == [k1, k2], str(erkannt))
        R.pruefe("zugang_speichern schreibt im Testmodus nichts", not ziel.exists())
        R.pruefe("Eine falsche Kanal-ID wird abgelehnt",
                 hilfen_wirft(lambda: dn.zugang_speichern("x", "123abc", ziel)))


# ---------------------------------------------------------------------------
#  Vermittler
# ---------------------------------------------------------------------------

class _FakeWeg:
    def __init__(self, name, online=(), fehler=None, verbunden=True):
        self.name = name
        self.online = set(online)
        self.fehler = fehler
        self.verbunden = verbunden
        self.gesendet = []
        self.laeuft = False
        self.geloescht = []
        self.empfangen = None
        self.ereignis = None

    def start(self):
        self.laeuft = True

    def stop(self):
        self.laeuft = False

    def ist_online(self, i):
        return i in self.online

    def online_ids(self):
        return set(self.online)

    def senden(self, daten, an, **kw):
        self.gesendet.append((an, kw))
        if self.fehler:
            raise self.fehler

    def loeschen(self, m):
        self.geloescht.append(m)


def _pruefen_vermittler(R):
    print("\n--- netz/vermittler.py ---")
    ich, freund, weg = _id(), _id(), _id()
    gruppe = _community()
    u = _umschlag(ich, freund)

    def bauen(modus, lan_online=(), lan_fehler=None, discord=True):
        s = _Sammler()
        lan = _FakeWeg("lan", lan_online, lan_fehler)
        dc = _FakeWeg("discord") if discord else None
        return Vermittler(ich, s.empfangen, s.ereignis, modus, lan=lan, discord=dc), lan, dc, s

    def fehler(f):
        try:
            f()
        except ConnectionError as e:
            return str(e) or "?"
        return None

    v, lan, dc, _ = bauen("beide", lan_online=[freund])
    R.pruefe("beide: Freund im WLAN → lan", v.senden(u, freund) == "lan" and not dc.gesendet)
    v, lan, dc, _ = bauen("beide")
    R.pruefe("beide: Freund nicht im WLAN → discord",
             v.senden(u, freund) == "discord" and not lan.gesendet)
    v, lan, dc, _ = bauen("beide", lan_online=[freund], lan_fehler=ConnectionError("weg"))
    R.pruefe("beide: WLAN scheitert → Discord übernimmt",
             v.senden(u, freund) == "discord" and lan.gesendet and dc.gesendet)
    v, lan, dc, _ = bauen("beide", lan_online=[freund], lan_fehler=ConnectionError("weg"))
    R.pruefe("beide: flüchtig + WLAN scheitert → Fehler, nicht Discord",
             fehler(lambda: v.senden(u, freund, fluechtig=True)) and not dc.gesendet)
    v, lan, dc, _ = bauen("beide")
    R.pruefe("beide: flüchtig + nicht im WLAN → Fehler, nicht Discord",
             fehler(lambda: v.senden(u, freund, fluechtig=True)) and not dc.gesendet)
    v, lan, dc, _ = bauen("beide", lan_online=[freund])
    R.pruefe("beide: flüchtig im WLAN geht", v.senden(u, freund, fluechtig=True) == "lan")
    v, lan, dc, _ = bauen("beide", discord=False)
    text = fehler(lambda: v.senden(u, freund))
    R.pruefe("beide ohne Discord, Freund nicht im WLAN: deutsche Erklärung",
             text is not None and "nicht verschickt" in text, str(text))

    v, lan, dc, _ = bauen("lan")
    R.pruefe("lan: geht über lan", v.senden(u, freund) == "lan")
    v, lan, dc, _ = bauen("lan", lan_fehler=ConnectionError("weg"))
    R.pruefe("lan: kein Ausweichen auf Discord",
             fehler(lambda: v.senden(u, freund)) and not dc.gesendet)
    v, lan, dc, _ = bauen("discord", lan_online=[freund])
    R.pruefe("discord: geht über discord, auch wenn im WLAN",
             v.senden(u, freund) == "discord" and not lan.gesendet)
    R.pruefe("discord: flüchtig wird abgelehnt, ohne Discord zu berühren",
             fehler(lambda: v.senden(u, freund, fluechtig=True)) and len(dc.gesendet) == 1)

    ug = _umschlag(ich, gruppe)
    v, lan, dc, _ = bauen("beide", lan_online=[freund])
    R.pruefe("Community geht immer über Discord",
             v.senden(ug, gruppe) == "discord" and not lan.gesendet)
    R.pruefe("Community mit community=True ebenso",
             v.senden(u, freund, community=True) == "discord" and not lan.gesendet)
    v, lan, dc, _ = bauen("lan")
    R.pruefe("Community bei 'Nur WLAN': Fehler mit Erklärung",
             "Discord" in (fehler(lambda: v.senden(ug, gruppe)) or "") and not dc.gesendet)
    v, lan, dc, _ = bauen("beide", discord=False)
    R.pruefe("Community ohne Discord: Fehler",
             fehler(lambda: v.senden(ug, gruppe)) is not None)

    # Zustand, Wege, Start/Stop, Ereignisse
    v, lan, dc, s = bauen("beide", lan_online=[freund])
    v.start()
    R.pruefe("start() startet beide Wege in 'beide'", lan.laeuft and dc.laeuft)
    R.pruefe("weg_zu: im WLAN → lan, sonst → discord, Community → discord",
             v.weg_zu(freund) == "lan" and v.weg_zu(weg) == "discord"
             and v.weg_zu(gruppe) == "discord")
    R.pruefe("ist_online folgt dem WLAN", v.ist_online(freund) and not v.ist_online(weg))
    lan.ereignis("verbindung", zustand={"lan": "an"}, meldung="Im WLAN bereit.")
    dc.ereignis("verbindung", zustand={"discord": "fehler"}, meldung="kaputt")
    R.pruefe("status() führt beide Zustände zusammen",
             v.status() == {"lan": "an", "discord": "fehler", "meldung": "kaputt",
                            "modus": "beide"}, str(v.status()))
    R.pruefe("Ereignis 'verbindung' trägt den zusammengeführten Zustand",
             s.ereignisse[-1][0] == "verbindung"
             and s.ereignisse[-1][1]["zustand"] == {"lan": "an", "discord": "fehler"})
    lan.empfangen(b"aus-dem-wlan", "lan", ("1.2.3.4", 5))
    dc.empfangen(b"aus-discord", "discord", {"kanal": "1"})
    R.pruefe("Eingehendes von beiden Wegen landet bei empfangen(bytes, weg)",
             s.umschlaege == [(b"aus-dem-wlan", "lan"), (b"aus-discord", "discord")])
    v.modus_setzen("lan")
    R.pruefe("modus_setzen('lan') stoppt Discord", lan.laeuft and not dc.laeuft
             and v.weg_zu(weg) is None and v.status()["discord"] == "aus")
    v.modus_setzen("discord")
    R.pruefe("modus_setzen('discord') stoppt das WLAN",
             dc.laeuft and not lan.laeuft and not v.ist_online(freund))
    R.pruefe("Unbekannte Betriebsart: ValueError", hilfen_wirft(lambda: v.modus_setzen("tauben")))
    v.loeschen("ab" * 16)
    R.pruefe("loeschen() geht an Discord", dc.geloescht == ["ab" * 16])
    v.stop()
    R.pruefe("stop() stoppt alles", not lan.laeuft and not dc.laeuft)

    # Ein echtes LanNetz und ein echtes DiscordNetz hinter dem Vermittler
    s = _Sammler()
    echtes_dc = DiscordNetz(ich, None, None, "t", ["1" * 18])
    v = Vermittler(ich, s.empfangen, s.ereignis, "beide", lan=None, discord=echtes_dc)
    R.pruefe("Echtes DiscordNetz ohne Verbindung: ConnectionError beim Senden",
             fehler(lambda: v.senden(u, freund)) is not None)


# ---------------------------------------------------------------------------

def pruefen(R, hilfen):
    start = time.monotonic()
    for teil in (_pruefen_lan, _pruefen_protokoll, _pruefen_discord_transport,
                 _pruefen_discord_fehler, _pruefen_zugang, _pruefen_vermittler):
        try:
            teil(R)
        except Exception as e:
            import traceback
            traceback.print_exc()
            R.fehlschlag(f"{teil.__name__} lief nicht durch", e)
    dauer = time.monotonic() - start
    print(f"  (netz-Prüfungen: {dauer:.1f} s)")
    R.pruefe("Die Netz-Prüfungen dauern keine 20 s", dauer < 20, f"{dauer:.1f} s")
