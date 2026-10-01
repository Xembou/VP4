# -*- coding: utf-8 -*-
"""netz/lan.py - Umschläge direkt übers WLAN (Nachfolger von chat.py).

Läuft komplett im lokalen Netz, ohne Server und ohne Internet:

* **Erkennung (UDP):** Jede Installation ruft alle 3 s ein kleines JSON ins
  Netz: {"v": 2, "id": "<eigene ID>", "port": <TCP-Port>}. Wer 12 s lang
  nichts mehr von sich hören lässt, gilt als weg.
* **Übertragung (TCP):** Ein Rahmen = 4 Byte Länge (big endian) + genau ein
  Umschlag. Pro Gegenstelle bleibt eine ausgehende Verbindung offen und wird
  bei Bedarf neu aufgebaut.

WEM HIER GEGLAUBT WIRD: NIEMANDEM
---------------------------------
Die ID im Erkennungspaket ist eine bloße Behauptung - jeder im selben WLAN
kann sich als jeder ausgeben. Sie dient nur dazu, einen Umschlag an die
richtige IP-Adresse zu schicken. Ob ein Umschlag wirklich vom Freund
stammt, zeigt sich erst eine Schicht höher, wenn er sich mit dem
Paarschlüssel entschlüsseln bzw. mit der Unterschrift prüfen lässt. Wer
eine fremde ID behauptet, bekommt also bestenfalls Geheimtext zu sehen,
den er nicht lesen kann - stören (Umschläge abfangen) kann er trotzdem.

ZUR PORTWAHL
------------
Die Ports liegen bewusst UNTER 49152. Windows vergibt alles ab 49152
dynamisch an ausgehende Verbindungen; ein fester Server-Port dort kann beim
Start schon belegt sein. Genau das ist in VP4 4.0 passiert (Ports
51230/51231, der Chat-Server startete gelegentlich nicht). Die Belegung ist
dieselbe wie in 4.x: UDP 41230 für die Erkennung, TCP 41231 für die Daten -
so bleibt eine schon eingerichtete Firewall-Regel gültig. Ein Test prüft
die Grenze.

WAS AUS 4.x ÜBERNOMMEN IST
--------------------------
* Ein kaputter Rahmen darf den Empfang nicht anhalten (Fehler 3 in
  CLAUDE.md): Ein unlesbarer Umschlag wird verworfen, die Verbindung lebt
  weiter. Nur eine unsinnige Längenangabe beendet die Verbindung - danach
  ist der Datenstrom nicht mehr sauber zu lesen. Der Server nimmt die
  nächste Verbindung trotzdem an.
* Eine tote Verbindung blockiert nie das Senden an andere: Jede
  Gegenstelle hat ihre eigene Sperre, und jeder Versuch hat eine Frist
  (5 s Verbindungsaufbau, 30 s ohne Fortschritt beim Schreiben).
* Fehler gehen nicht per print() ins Leere (Fehler 5), sondern über
  `ereignis(...)` an die Oberfläche.

FÜR TESTS
---------
Zwei Instanzen im selben Prozess gehen so: `port_tcp=0` (das System wählt
einen freien Port; der echte steht dann im Erkennungspaket), eigene
UDP-Ports pro Instanz, `bind_host="127.0.0.1"` und `ziele=[("127.0.0.1",
<UDP-Port der anderen>)]`. `ziele` ersetzt den Broadcast an
255.255.255.255, der in manchen Sandboxen nicht zugestellt wird.
"""

import json
import os
import socket
import struct
import sys
import threading
import time

from kern.identitaet import ist_nutzer_id
from kern.umschlag import MAX_UMSCHLAG, Umschlag

# Feste Ports, bewusst unterhalb des dynamischen Bereichs (siehe oben).
BROADCAST_PORT = 41230      # UDP, Erkennung
CHAT_PORT = 41231           # TCP, Umschläge
PORT_UDP = BROADCAST_PORT
PORT_TCP = CHAT_PORT

BROADCAST_INTERVALL = 3.0   # Sekunden zwischen zwei Erkennungspaketen
PEER_ABLAUF = 12.0          # so lange gilt ein Gerät ohne neues Paket als da

VERBINDEN_FRIST = 5.0       # Verbindungsaufbau
SCHREIB_FRIST = 30.0        # ohne Fortschritt beim Schreiben
LESE_FRIST = 60.0           # ohne Fortschritt mitten in einem Rahmen
SOCKET_FRIST = SCHREIB_FRIST  # fest pro Socket, siehe _genau_lesen()
BLOCK = 1024 * 1024         # Schreiben in Häppchen, damit die Frist pro Stück gilt

MAX_EINGEHEND = 64          # gleichzeitige eingehende Verbindungen
MAX_PAKET = 512             # ein Erkennungspaket ist winzig

STOPP_FRIST = 2.0

PROTOKOLL_VERSION = 2

_LAENGE = struct.Struct("!I")


def _schliessen(sock):
    """Schließt einen Socket so, dass ein wartendes recv() sofort zurückkehrt.

    Nur close() reicht unter Linux nicht immer, um einen anderen Thread aus
    recv()/accept() zu holen - shutdown() vorher schon.
    """
    if sock is None:
        return
    try:
        sock.shutdown(socket.SHUT_RDWR)
    except OSError:
        pass
    try:
        sock.close()
    except OSError:
        pass


class _Gegenstelle:
    __slots__ = ("ip", "port", "zuletzt")

    def __init__(self, ip, port, zuletzt):
        self.ip = ip
        self.port = port
        self.zuletzt = zuletzt


def _udp_connreset_aus(sock) -> bool:
    """Schaltet unter Windows SIO_UDP_CONNRESET ab. True, wenn es geklappt hat.

    Pythons socket-Modul kennt die Konstante NICHT (auf echtem Windows
    nachgesehen, Oktober 2026) und sock.ioctl() nimmt nur drei feste
    Befehle an - deshalb direkt über WSAIoctl. Klappt es nicht, fängt
    der Empfang den ConnectionResetError trotzdem ab; das hier ist die
    zweite Sicherung.
    """
    if sys.platform != "win32":
        return False
    try:
        import ctypes
        aus = ctypes.c_uint32(0)
        zurueck = ctypes.c_uint32(0)
        ergebnis = ctypes.windll.ws2_32.WSAIoctl(
            ctypes.c_size_t(sock.fileno()), ctypes.c_uint32(0x9800000C),
            ctypes.byref(aus), ctypes.sizeof(aus), None, 0, ctypes.byref(zurueck), None, None)
        return ergebnis == 0
    except Exception:
        return False


class LanNetz:
    """UDP-Erkennung + TCP-Übertragung für Umschläge.

    empfangen(umschlag_bytes, "lan", (ip, port))  - für jeden gültig
        aufgebauten Umschlag. Ob er echt ist, prüft der Aufrufer.
    ereignis(typ, **daten) - "verbindung" (zustand={"lan": "an"|"aus"|
        "fehler"}, meldung=...), "fehler" (text=...), "online" (ids=[...]).

    Beide Rückrufe kommen aus Netzwerk-Threads; der Aufrufer muss sie in
    seinen eigenen Thread umleiten (Ereignis-Warteschlange). Sie sind als
    Attribute `empfangen` und `ereignis` offen, damit der Vermittler sich
    dazwischenhängen kann.
    """

    def __init__(self, eigene_id: str, empfangen, ereignis,
                 port_tcp: int = CHAT_PORT, port_udp: int = BROADCAST_PORT, *,
                 bind_host: str = "", ziele=None,
                 intervall: float = BROADCAST_INTERVALL,
                 ablauf: float = PEER_ABLAUF):
        self.eigene_id = eigene_id
        self.empfangen = empfangen
        self.ereignis = ereignis
        self.port_tcp = int(port_tcp)
        self.port_udp = int(port_udp)
        self.bind_host = bind_host
        self._ziele = list(ziele) if ziele else [("255.255.255.255", self.port_udp)]
        self.intervall = float(intervall)
        self.ablauf = float(ablauf)

        self.zustand = "aus"
        self.echter_tcp_port = None     # nach start(), auch bei port_tcp=0

        self._laeuft = False
        self._stopp = threading.Event()
        self._threads = []
        self._threads_lock = threading.Lock()

        self._udp = None
        self._server = None

        self._peers = {}                # id -> _Gegenstelle
        self._peers_lock = threading.Lock()

        self._ausgehend = {}            # id -> socket
        self._sende_sperren = {}        # id -> Lock (eine pro Gegenstelle)
        self._verb_lock = threading.Lock()
        self._eingehend = set()         # offene eingehende Sockets

    # ------------------------------------------------------------ Hilfen

    def _melden(self, typ, **daten):
        """Rückruf an die Oberfläche - ein Fehler dort darf hier nichts stoppen."""
        try:
            self.ereignis(typ, **daten)
        except Exception:
            pass

    def _zustand_setzen(self, zustand, meldung=""):
        self.zustand = zustand
        self._melden("verbindung", zustand={"lan": zustand}, meldung=meldung)

    def _thread(self, ziel, *args, name="vp4-lan"):
        t = threading.Thread(target=ziel, args=args, daemon=True, name=name)
        with self._threads_lock:
            self._threads = [x for x in self._threads if x.is_alive()]
            self._threads.append(t)
        t.start()
        return t

    # ------------------------------------------------------- Start / Stop

    def start(self):
        if self._laeuft:
            return
        self._laeuft = True
        self._stopp.clear()

        fehler = []
        try:
            self._server = self._server_oeffnen()
            self.echter_tcp_port = self._server.getsockname()[1]
        except OSError as e:
            self._server = None
            fehler.append(
                f"Der WLAN-Empfang konnte nicht starten: TCP-Port {self.port_tcp} "
                f"ist belegt.\n\nAndere erreichen dich dann im WLAN nicht. Meist "
                f"hilft es, VP4 einmal zu beenden und neu zu starten.\n\n"
                f"(Technisch: {e})")
        try:
            self._udp = self._udp_oeffnen()
        except OSError as e:
            self._udp = None
            fehler.append(
                f"Die Geräte-Erkennung im WLAN konnte nicht starten: UDP-Port "
                f"{self.port_udp} ist belegt.\n\nDu wirst Freunde im WLAN nicht "
                f"automatisch finden.\n\n(Technisch: {e})")

        if self._server is not None:
            self._thread(self._annehmen_schleife, name="vp4-lan-tcp")
        if self._udp is not None:
            self._thread(self._erkennung_schleife, name="vp4-lan-udp")

        if fehler:
            text = "\n\n".join(fehler)
            self._zustand_setzen("fehler", text)
            self._melden("fehler", text=text)
        else:
            self._zustand_setzen("an", "Im WLAN bereit.")

    def stop(self):
        """Hält alles an; alle Threads sind spätestens nach 2 s beendet."""
        if not self._laeuft:
            return
        self._laeuft = False
        self._stopp.set()
        _schliessen(self._udp)
        _schliessen(self._server)
        with self._verb_lock:
            socks = list(self._ausgehend.values()) + list(self._eingehend)
            self._ausgehend.clear()
            self._eingehend.clear()
        for s in socks:
            _schliessen(s)

        frist = time.monotonic() + STOPP_FRIST
        with self._threads_lock:
            threads = list(self._threads)
        for t in threads:
            if t is threading.current_thread():
                continue
            t.join(max(0.0, frist - time.monotonic()))
        with self._threads_lock:
            self._threads = [t for t in self._threads if t.is_alive()]
        with self._peers_lock:
            hatte = bool(self._peers)
            self._peers.clear()
        self._udp = None
        self._server = None
        if hatte:
            self._melden("online", ids=[])
        self._zustand_setzen("aus")

    def offene_threads(self) -> int:
        """Für den Selbsttest: wie viele Threads noch laufen."""
        with self._threads_lock:
            return sum(1 for t in self._threads if t.is_alive())

    # ------------------------------------------------- Erkennung (UDP)

    def _udp_oeffnen(self):
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
            # Windows meldet ein "Port nicht erreichbar" auf ein früheres
            # sendto() beim NÄCHSTEN recvfrom() als ConnectionResetError. Ohne
            # das hier würde eine einzige Antwort an ein gerade beendetes VP4
            # die ganze Erkennung abwürgen.
            _udp_connreset_aus(sock)
            sock.bind((self.bind_host, self.port_udp))
            sock.settimeout(0.25)
        except OSError:
            sock.close()
            raise
        return sock

    def _paket(self) -> bytes:
        return json.dumps({"v": PROTOKOLL_VERSION, "id": self.eigene_id,
                           "port": self.echter_tcp_port},
                          separators=(",", ":")).encode("utf-8")

    def _ankuendigen(self, ziele):
        if self._server is None or self._udp is None:
            return      # ohne TCP-Empfang hätte niemand etwas von uns
        paket = self._paket()
        for ziel in ziele:
            try:
                self._udp.sendto(paket, ziel)
            except OSError:
                pass    # z. B. kein Netz - beim nächsten Mal wieder

    def _erkennung_schleife(self):
        naechstes = 0.0
        sock = self._udp
        while not self._stopp.is_set():
            jetzt = time.monotonic()
            if jetzt >= naechstes:
                self._ankuendigen(self._ziele)
                naechstes = jetzt + self.intervall
                self._aufraeumen()
            try:
                daten, adresse = sock.recvfrom(MAX_PAKET)
            except socket.timeout:
                continue
            except ConnectionResetError:
                continue    # Windows-Eigenheit, siehe _udp_oeffnen()
            except OSError:
                if self._stopp.is_set():
                    break
                time.sleep(0.2)
                continue
            try:
                self._paket_lesen(daten, adresse)
            except Exception:
                continue    # ein kaputtes Paket hält die Erkennung nicht an

    def _paket_lesen(self, daten: bytes, adresse):
        try:
            inhalt = json.loads(daten.decode("utf-8"))
        except (UnicodeDecodeError, ValueError):
            return          # z. B. ein "VP4|ANNOUNCE" aus 4.x - stilles Aus
        if not isinstance(inhalt, dict) or inhalt.get("v") != PROTOKOLL_VERSION:
            return
        peer_id, port = inhalt.get("id"), inhalt.get("port")
        if not ist_nutzer_id(peer_id) or peer_id == self.eigene_id:
            return
        if (not isinstance(port, int) or isinstance(port, bool)
                or not 0 < port < 65536):
            return

        ip = adresse[0]
        with self._peers_lock:
            alt = self._peers.get(peer_id)
            neu = alt is None
            umgezogen = alt is not None and (alt.ip, alt.port) != (ip, port)
            self._peers[peer_id] = _Gegenstelle(ip, port, time.monotonic())
            ids = sorted(self._peers) if neu else None
        if umgezogen:
            # Neue Adresse (VP4 neu gestartet, anderes WLAN): die alte
            # Verbindung zeigt ins Leere.
            self._ausgehend_verwerfen(peer_id)
        if neu:
            # Sofort zurückmelden, damit der andere uns nicht erst beim
            # nächsten Rundruf findet. Nur bei Neuen - sonst schaukelt sich
            # das Hin und Her auf.
            self._ankuendigen([adresse])
            self._melden("online", ids=ids)

    def _aufraeumen(self):
        grenze = time.monotonic() - self.ablauf
        with self._peers_lock:
            weg = [pid for pid, g in self._peers.items() if g.zuletzt < grenze]
            for pid in weg:
                del self._peers[pid]
            ids = sorted(self._peers)
        for pid in weg:
            self._ausgehend_verwerfen(pid)
        if weg:
            self._melden("online", ids=ids)

    def online_ids(self) -> set:
        with self._peers_lock:
            return set(self._peers)

    def ist_online(self, peer_id: str) -> bool:
        with self._peers_lock:
            return peer_id in self._peers

    def adresse_von(self, peer_id: str):
        """(ip, tcp_port) oder None - nur zur Anzeige und für Tests."""
        with self._peers_lock:
            g = self._peers.get(peer_id)
            return (g.ip, g.port) if g else None

    # ------------------------------------------------------ TCP: Empfang

    def _server_oeffnen(self):
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            if sys.platform == "win32" and hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
                # Unter Windows erlaubt SO_REUSEADDR einem ZWEITEN Programm,
                # denselben Port zu belegen - dann landet die Hälfte der
                # Verbindungen dort. Exklusiv ist hier richtig.
                sock.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
            else:
                # Unter Linux/macOS nötig, um nach einem Neustart sofort
                # wieder binden zu können (TIME_WAIT).
                sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            sock.bind((self.bind_host, self.port_tcp))
            sock.listen(16)
            # accept() mit kurzer Frist: so bemerkt die Schleife stop()
            # auch dort, wo close() ein wartendes accept() nicht weckt.
            sock.settimeout(0.25)
        except OSError:
            sock.close()
            raise
        return sock

    def _annehmen_schleife(self):
        server = self._server
        while not self._stopp.is_set():
            try:
                conn, adresse = server.accept()
            except socket.timeout:
                continue
            except OSError:
                if self._stopp.is_set():
                    break
                time.sleep(0.2)
                continue
            with self._verb_lock:
                zu_viele = len(self._eingehend) >= MAX_EINGEHEND
                if not zu_viele:
                    self._eingehend.add(conn)
            if zu_viele:
                _schliessen(conn)
                continue
            self._thread(self._lese_schleife, conn, adresse, None,
                         name="vp4-lan-lesen")

    def _genau_lesen(self, sock, n: int, warten: bool = False) -> bytearray:
        """Liest genau n Bytes.

        Die Socket-Frist ist fest (SOCKET_FRIST) und wird hier nie verstellt:
        Auf einer ausgehenden Verbindung liest ein Thread, während ein anderer
        schreibt, und settimeout() gilt für beide Richtungen. Stattdessen
        zählt hier, wie lange kein Byte mehr kam:

        * warten=True und noch nichts gelesen: Die Verbindung darf ruhen,
          beliebig lange.
        * sonst: LESE_FRIST ohne Fortschritt beendet die Verbindung.

        Der Puffer wächst mit dem, was wirklich ankommt. Eine gelogene
        Längenangabe von 110 MiB belegt so keinen Speicher, solange nichts
        nachkommt.
        """
        puffer = bytearray()
        zuletzt = time.monotonic()
        while len(puffer) < n:
            if self._stopp.is_set():
                raise ConnectionError("VP4 wird beendet.")
            try:
                block = sock.recv(min(n - len(puffer), BLOCK))
            except socket.timeout:
                if warten and not puffer:
                    continue
                if time.monotonic() - zuletzt > LESE_FRIST:
                    raise ConnectionError("Die Gegenseite schickt nichts mehr.")
                continue
            if not block:
                raise ConnectionError("Verbindung wurde beendet.")
            puffer += block
            zuletzt = time.monotonic()
        return puffer

    def _lese_schleife(self, sock, adresse, peer_id):
        """Liest Rahmen, bis die Verbindung endet.

        peer_id ist gesetzt, wenn es UNSERE ausgehende Verbindung ist. Über
        sie kommt normalerweise nichts zurück - das Lesen dient dann vor
        allem dazu, ein Ende der Gegenseite sofort zu bemerken, statt den
        nächsten Umschlag in eine tote Verbindung zu schreiben.
        """
        try:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1)
        except OSError:
            pass
        try:
            sock.settimeout(SOCKET_FRIST)
        except OSError:
            pass
        try:
            while not self._stopp.is_set():
                try:
                    kopf = self._genau_lesen(sock, _LAENGE.size, warten=True)
                    (laenge,) = _LAENGE.unpack(kopf)
                    if laenge == 0:
                        continue            # leerer Rahmen: nichts zu tun
                    if laenge > MAX_UMSCHLAG:
                        # Den angekündigten Rest wegzulesen wäre gefährlich -
                        # die Länge kann beliebig gelogen sein. Verbindung
                        # beenden; die Gegenseite baut bei Bedarf neu auf.
                        break
                    daten = self._genau_lesen(sock, laenge)
                except (OSError, ConnectionError, ValueError):
                    break
                self._umschlag_abgeben(bytes(daten), adresse)
        finally:
            with self._verb_lock:
                self._eingehend.discard(sock)
                if peer_id is not None and self._ausgehend.get(peer_id) is sock:
                    del self._ausgehend[peer_id]
            _schliessen(sock)

    def _umschlag_abgeben(self, daten: bytes, adresse):
        try:
            Umschlag.entpacken(daten)
        except ValueError:
            return      # kein VP4-Umschlag: nur diesen Rahmen verwerfen
        try:
            self.empfangen(daten, "lan", tuple(adresse))
        except Exception as e:
            # Ein Fehler beim Verarbeiten darf den Empfang nicht anhalten -
            # genau so ist in 4.0 ein Freund unerreichbar geworden.
            self._melden("fehler", text=f"Ein Umschlag aus dem WLAN ließ sich nicht "
                                        f"verarbeiten. (Technisch: {e})")

    # ------------------------------------------------------------ Senden

    def _sperre_fuer(self, peer_id):
        with self._verb_lock:
            sperre = self._sende_sperren.get(peer_id)
            if sperre is None:
                sperre = self._sende_sperren[peer_id] = threading.Lock()
            return sperre

    def _ausgehend_verwerfen(self, peer_id, nur=None):
        with self._verb_lock:
            sock = self._ausgehend.get(peer_id)
            if sock is None or (nur is not None and sock is not nur):
                return
            del self._ausgehend[peer_id]
        _schliessen(sock)

    def _verbindung(self, peer_id, ip, port):
        with self._verb_lock:
            sock = self._ausgehend.get(peer_id)
        if sock is not None:
            return sock
        sock = socket.create_connection((ip, port), timeout=VERBINDEN_FRIST)
        try:
            sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        except OSError:
            pass
        # Ab hier gilt die feste Frist - für das Schreiben hier UND das Lesen
        # im eigenen Thread (siehe _genau_lesen()).
        sock.settimeout(SOCKET_FRIST)
        with self._verb_lock:
            if not self._laeuft:
                _schliessen(sock)
                raise ConnectionError("Der WLAN-Chat wurde beendet.")
            self._ausgehend[peer_id] = sock
        # Eigener Lese-Thread: bemerkt sofort, wenn die Gegenseite auflegt.
        self._thread(self._lese_schleife, sock, (ip, port), peer_id,
                     name="vp4-lan-aus")
        return sock

    def _schreiben(self, sock, daten: bytes):
        # Die Frist (SOCKET_FRIST) steht seit dem Verbindungsaufbau fest; hier
        # wird sie bewusst nicht verstellt, weil der Lese-Thread denselben
        # Socket benutzt.
        sock.sendall(_LAENGE.pack(len(daten)))
        sicht = memoryview(daten)
        for i in range(0, len(daten), BLOCK):
            # In Stücken, damit die Frist "30 s ohne Fortschritt" heißt und
            # nicht "30 s für 100 MB" (seit Python 3.5 gilt die Frist von
            # sendall() für den ganzen Aufruf).
            sock.sendall(sicht[i:i + BLOCK])

    def senden(self, umschlag_bytes: bytes, an_id: str) -> None:
        """Schickt einen Umschlag an eine Gegenstelle im WLAN.

        Läuft im Thread des Aufrufers und hat immer eine Frist. Löst
        ConnectionError aus, wenn die Gegenstelle im WLAN nicht erreichbar
        ist, ValueError, wenn der Umschlag zu groß ist.
        """
        daten = bytes(umschlag_bytes)
        if len(daten) > MAX_UMSCHLAG:
            raise ValueError("Der Umschlag ist zu groß für eine Übertragung.")
        if not self._laeuft or self._server is None and self._udp is None:
            raise ConnectionError("Der Chat im WLAN ist gerade aus.")
        with self._peers_lock:
            g = self._peers.get(an_id)
            ziel = (g.ip, g.port) if g else None
        if ziel is None:
            raise ConnectionError(
                f"{an_id} ist gerade nicht im WLAN zu sehen. Seid ihr im selben "
                f"Netz, und hat er VP4 offen?")

        with self._sperre_fuer(an_id):
            letzter = None
            # Zwei Versuche: Eine gespeicherte Verbindung kann still
            # gestorben sein (der andere hat VP4 neu gestartet) - dann einmal
            # frisch verbinden.
            for _versuch in range(2):
                if not self._laeuft:
                    break
                try:
                    sock = self._verbindung(an_id, *ziel)
                except (OSError, ConnectionError) as e:
                    letzter = e
                    break       # Verbindungsaufbau scheitert: nicht hämmern
                try:
                    self._schreiben(sock, daten)
                    return
                except OSError as e:
                    letzter = e
                    self._ausgehend_verwerfen(an_id, nur=sock)
        raise ConnectionError(
            f"{an_id} ist im WLAN gerade nicht erreichbar.\n\n"
            f"Vielleicht blockiert die Windows-Firewall VP4, oder das Gerät "
            f"ist gerade weg. (Technisch: {letzter})")
