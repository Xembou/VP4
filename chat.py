#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
=====================================================================
 chat.py - der LAN-Chat von VP4
=====================================================================
Läuft komplett im lokalen Netzwerk (WLAN), ohne Server und ohne
Internet:

  - Jede Installation gibt ihre ID per UDP-Broadcast bekannt, damit
    Freunde im selben WLAN sie finden ("Discovery").
  - Chat und Dateiübertragung laufen über TCP.
  - Eingehende Verbindungen werden nur von IDs angenommen, die man
    vorher selbst als Freund hinzugefügt hat.
  - Wenn für einen Freund ein gemeinsamer Schlüssel hinterlegt ist,
    werden Nachrichten und Dateien mit AES-256-GCM verschlüsselt.

Ereignisse (neue Nachricht, empfangene Datei, Fehler) werden in eine
Queue gelegt. Die Oberfläche liest sie im Hauptthread regelmäßig aus -
direkt aus einem Netzwerk-Thread heraus die GUI anzufassen wäre nicht
zulässig und führt zu schwer auffindbaren Abstürzen.

ZUR PORTWAHL
-------------
Die Ports liegen bewusst UNTER 49152. Windows vergibt alles ab 49152
dynamisch an beliebige ausgehende Verbindungen - liegt ein fester
Server-Port in diesem Bereich, kann er beim Start bereits von einem
fremden Programm belegt sein. Genau das ist in der Vorversion passiert:
die Ports lagen bei 51230/51231, und der Chat-Server startete
gelegentlich gar nicht.

WAS DIESER CHAT NICHT LEISTET
------------------------------
Die IDs werden offen im Netz bekannt gegeben, und beim Verbindungsaufbau
wird nur behauptet, wer man ist - nachgeprüft wird es nicht. Wer im
selben WLAN ist, könnte sich also als ein Freund von dir ausgeben. Für
ein Programm unter Freunden im Heimnetz ist das vertretbar, für
Vertrauliches nicht.
=====================================================================
"""

import base64
import json
import os
import queue
import socket
import struct
import threading
import time
import uuid
from pathlib import Path

from cryptography.exceptions import InvalidTag

from kern.krypto import ModernCrypto
from kern import speicher


# Feste Ports, bewusst unterhalb des dynamischen Bereichs (siehe Kopfkommentar).
BROADCAST_PORT = 41230
CHAT_PORT = 41231

PEER_TIMEOUT_SEC = 15
BROADCAST_INTERVALL = 4

MAX_ENCRYPTED_FILE_SIZE = 50 * 1024 * 1024    # 50 MB
MAX_FILE_SIZE = 300 * 1024 * 1024             # 300 MB

MSG_HELLO = 0
MSG_TEXT = 1
MSG_META = 2
MSG_DATA = 3


# =============================================================================
#  Gemeinsame Bausteine für alle Transportwege
# =============================================================================
# Diese drei Funktionen hängen an nichts als der Freundesliste - kein Socket,
# kein Discord, keine Oberflaeche. Genau deshalb stehen sie hier auf Modulebene
# und nicht in ChatNetwork: der Discord-Transport benutzt woertlich dieselben.
# Wären es zwei Kopien, wuerde eine davon irgendwann anders verschluesseln als
# die andere, und der Fehler faellt erst auf, wenn eine Nachricht nicht mehr
# ankommt.

def gruppen_schluessel_b64():
    """Der Schlüssel, den alle mit derselben VP4.exe teilen - oder None.

    Er wird beim Bauen der .exe eingesetzt (discord_konfig.py). Sein Zweck ist
    Bequemlichkeit: Freunde sollen sofort schreiben können, ohne vorher einen
    Schlüssel auszutauschen.

    WAS ER LEISTET UND WAS NICHT
    -----------------------------
    Gegen Discord und gegen jeden Fremden im Server schützt er wie jeder
    andere AES-256-Schlüssel. Untereinander schützt er NICHT: Wer dieselbe
    .exe hat, kann alles im Kanal mitlesen, auch Nachrichten zwischen zwei
    anderen. Er ist ein Gruppenschlüssel, kein Schlüssel pro Freundschaft.

    Ein Schlüssel, der für einen bestimmten Freund hinterlegt ist, geht ihm
    deshalb immer vor - nur der schützt auch vor den anderen aus der Gruppe.
    """
    try:
        import discord_konfig
    except ImportError:
        return None
    return (getattr(discord_konfig, "GRUPPEN_SCHLUESSEL", "") or "").strip() or None


def schluessel_fuer(friends, ziel_id: str, gruppen=None):
    """Der Schlüssel für dieses Ziel.

    Die Reihenfolge ist die Rangfolge:

    1. Ist das Ziel eine **Gruppe**, gilt ihr Schlüssel - alle Mitglieder
       haben denselben, sonst wäre es keine Gruppe.
    2. Sonst ein Schlüssel, der für **diesen Freund** hinterlegt ist. Nur
       der schützt auch vor den anderen mit derselben VP4.exe.
    3. Sonst der eingebaute **Gruppenschlüssel** aus der .exe.

    Rückgabe: (schluessel oder None, ist_gemeinschaftlich)
    """
    if speicher.ist_gruppen_id(ziel_id):
        gruppe = gruppen.get(ziel_id) if gruppen is not None else None
        if gruppe and gruppe.get("key_b64"):
            return base64.b64decode(gruppe["key_b64"]), True
        return None, False

    freund = friends.get(ziel_id)
    if freund and freund.get("shared_key_b64"):
        return base64.b64decode(freund["shared_key_b64"]), False
    eingebaut = gruppen_schluessel_b64()
    if eingebaut:
        return base64.b64decode(eingebaut), True
    return None, False


def payload_verschluesseln(friends, friend_id: str, payload: bytes, gruppen=None):
    """Verschlüsselt, wenn für das Ziel ein gemeinsamer Schlüssel da ist.

    Rückgabe: (wurde_verschlüsselt, daten)
    """
    key, _gemeinsam = schluessel_fuer(friends, friend_id, gruppen)
    if key is not None:
        return True, ModernCrypto.aes_encrypt_bytes(payload, key)
    return False, payload


def payload_entschluesseln(friends, friend_id: str, verschluesselt: bool,
                           payload: bytes, gruppen=None) -> bytes:
    """Gegenstück zu payload_verschluesseln().

    Löst ValueError aus, wenn etwas verschlüsselt ankommt, aber kein
    gemeinsamer Schlüssel hinterlegt ist.
    """
    if verschluesselt:
        key, _gemeinsam = schluessel_fuer(friends, friend_id, gruppen)
        if key is None:
            raise ValueError("Verschlüsselt, aber kein gemeinsamer Schlüssel hinterlegt.")
        try:
            return ModernCrypto.aes_decrypt_bytes(payload, key)
        except InvalidTag:
            # AES-GCM meldet einen falschen Schlüssel oder veränderte Daten mit
            # einem leeren InvalidTag - ein Fehler ohne Text. Hier wird ein
            # ValueError mit Erklärung daraus, denn genau danach unterscheidet
            # der Discord-Empfang: ValueError heißt "war für uns, ging aber
            # nicht auf" und wird gemeldet, alles andere fliegt still raus.
            raise ValueError(
                "Die Nachricht ließ sich nicht entschlüsseln - der gemeinsame "
                "Schlüssel passt nicht.")
    return payload


def zieldatei(name: str) -> Path:
    """Baut einen Pfad im Empfangsordner, der nichts überschreiben kann.

    Path(...).name wirft dabei alle Verzeichnisangaben weg. Ohne das könnte
    ein Absender einen Dateinamen mit Punkt-Punkt-Schritten nach oben
    schicken und damit aus dem Empfangsordner ausbrechen.
    """
    sicher = f"{int(time.time())}_{uuid.uuid4().hex[:8]}_{Path(name or 'datei').name}"
    speicher.ordner_anlegen()
    return speicher.RECEIVED_DIR / sicher


class ChatNetwork:

    def __init__(self, my_id: str, friends, event_queue: "queue.Queue",
                 chat_port: int = CHAT_PORT, broadcast_port: int = BROADCAST_PORT,
                 bind_host: str = ""):
        self.my_id = my_id
        self.friends = friends
        self.events = event_queue
        self.chat_port = chat_port
        self.broadcast_port = broadcast_port
        # Normalerweise "" (alle Netzwerkkarten). Nur für Tests auf einer
        # einzelnen Maschine wird hier 127.0.0.1 gesetzt.
        self.bind_host = bind_host

        self.peers = {}              # {friend_id: (ip, zuletzt_gesehen)}
        self._peers_lock = threading.Lock()
        self._connections = {}       # {friend_id: socket}
        self._conn_lock = threading.Lock()
        self._running = False
        self._server_sock = None
        self.server_laeuft = False   # für die Anzeige in der Oberfläche

    # ---------------------------------------------------------- Start / Stop

    def start(self):
        self._running = True
        threading.Thread(target=self._broadcast_loop, daemon=True).start()
        threading.Thread(target=self._listen_broadcast_loop, daemon=True).start()
        threading.Thread(target=self._tcp_server_loop, daemon=True).start()

    def stop(self):
        self._running = False
        self.server_laeuft = False
        try:
            if self._server_sock:
                self._server_sock.close()
        except OSError:
            pass
        with self._conn_lock:
            for s in self._connections.values():
                try:
                    s.close()
                except OSError:
                    pass
            self._connections.clear()

    # ------------------------------------------------------ Discovery (UDP)

    def _broadcast_loop(self):
        """Gibt die eigene ID regelmäßig im Netz bekannt."""
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        msg = f"VP4|ANNOUNCE|{self.my_id}|{self.chat_port}".encode("utf-8")
        while self._running:
            try:
                sock.sendto(msg, ("255.255.255.255", self.broadcast_port))
            except OSError:
                pass
            time.sleep(BROADCAST_INTERVALL)
        try:
            sock.close()
        except OSError:
            pass

    def _listen_broadcast_loop(self):
        """Hört zu, wer sich sonst noch im Netz meldet."""
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            sock.bind((self.bind_host, self.broadcast_port))
        except OSError as e:
            self.events.put(("error",
                f"Die Geräte-Erkennung konnte nicht starten: Port {self.broadcast_port} "
                f"ist belegt.\n\nDu wirst Freunde nicht automatisch finden.\n\n"
                f"(Technisch: {e})"))
            return
        sock.settimeout(1.0)
        while self._running:
            try:
                data, addr = sock.recvfrom(1024)
            except socket.timeout:
                self._prune_peers()
                continue
            except OSError:
                break
            try:
                parts = data.decode("utf-8").split("|")
                # Ab Version 4.1 wird der Chat-Port mitgeschickt. Ältere
                # Installationen senden ihn nicht - dann gilt der Standard.
                if len(parts) >= 3 and parts[0] == "VP4" and parts[1] == "ANNOUNCE":
                    peer_id = parts[2]
                    port = int(parts[3]) if len(parts) > 3 and parts[3].isdigit() else CHAT_PORT
                    if peer_id != self.my_id:
                        with self._peers_lock:
                            neu = peer_id not in self.peers
                            self.peers[peer_id] = (addr[0], time.time(), port)
                        if neu:
                            self.events.put(("peer_update", None))
            except (UnicodeDecodeError, IndexError, ValueError):
                continue
        try:
            sock.close()
        except OSError:
            pass

    def _prune_peers(self):
        """Entfernt Geräte, von denen länger nichts mehr kam."""
        jetzt = time.time()
        with self._peers_lock:
            alt = [pid for pid, eintrag in self.peers.items()
                   if jetzt - eintrag[1] > PEER_TIMEOUT_SEC]
            for pid in alt:
                del self.peers[pid]
        if alt:
            self.events.put(("peer_update", None))

    def is_online(self, friend_id: str) -> bool:
        with self._peers_lock:
            return friend_id in self.peers

    def online_ids(self) -> set:
        with self._peers_lock:
            return set(self.peers)

    # ---------------------------------------------------------- TCP: Server

    def _tcp_server_loop(self):
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            sock.bind((self.bind_host, self.chat_port))
            sock.listen(20)
        except OSError as e:
            self.events.put(("error",
                f"Der Chat-Server konnte nicht starten: Port {self.chat_port} ist "
                f"belegt.\n\nDu kannst dann selbst niemanden empfangen - andere "
                f"erreichen dich nicht. Meist hilft es, VP4 einmal zu beenden und "
                f"neu zu starten.\n\n(Technisch: {e})"))
            return
        self._server_sock = sock
        self.server_laeuft = True
        self.events.put(("server_bereit", self.chat_port))
        while self._running:
            try:
                conn, addr = sock.accept()
            except OSError:
                break
            threading.Thread(target=self._handle_connection,
                             args=(conn,), daemon=True).start()
        self.server_laeuft = False

    def _handle_connection(self, conn: socket.socket):
        try:
            flags, msgtype, payload = self._recv_frame(conn)
            if msgtype != MSG_HELLO:
                conn.close()
                return
            sender_id = payload.decode("utf-8")
            if sender_id not in self.friends:
                # Nur bereits hinzugefügte Freunde dürfen sich verbinden.
                conn.close()
                return
            with self._conn_lock:
                alt = self._connections.get(sender_id)
                if alt is not None and alt is not conn:
                    try:
                        alt.close()
                    except OSError:
                        pass
                self._connections[sender_id] = conn
            self._read_loop(sender_id, conn)
        except (ConnectionError, OSError, struct.error, UnicodeDecodeError):
            pass
        finally:
            try:
                conn.close()
            except OSError:
                pass

    # ------------------------------------------------------------ Empfangen

    def _read_loop(self, friend_id: str, conn: socket.socket):
        try:
            while self._running:
                try:
                    flags, msgtype, payload = self._recv_frame(conn)
                except (ConnectionError, OSError, struct.error):
                    break

                if msgtype == MSG_TEXT:
                    try:
                        klartext = self._maybe_decrypt(friend_id, flags, payload)
                    except Exception:
                        # Die Nachricht kam verschlüsselt an, aber der
                        # gemeinsame Schlüssel fehlt oder passt nicht. Nur
                        # diese eine Nachricht verwerfen - die Verbindung
                        # muss deswegen nicht abgebrochen werden.
                        self.events.put(("error",
                            f"Eine Nachricht konnte nicht entschlüsselt werden. "
                            f"Habt ihr beide denselben gemeinsamen Schlüssel hinterlegt?"))
                        continue
                    self.events.put(("message", {
                        "from": friend_id,
                        "text": klartext.decode("utf-8", errors="replace"),
                        "encrypted": bool(flags & 1),
                    }))

                elif msgtype == MSG_META:
                    try:
                        meta_raw = self._maybe_decrypt(friend_id, flags, payload)
                        meta = json.loads(meta_raw.decode("utf-8"))
                    except Exception:
                        # Ohne die Ankündigung wissen wir nicht, wie viele
                        # Bytes gleich folgen. Ab hier ist der Datenstrom
                        # nicht mehr sauber zu lesen - Verbindung beenden,
                        # sie wird beim nächsten Senden neu aufgebaut.
                        self.events.put(("error",
                            "Eine Dateiankündigung war unlesbar - Verbindung getrennt."))
                        break
                    if not self._receive_data_frame(friend_id, conn, meta):
                        break
        finally:
            # Das muss auch bei einem unerwarteten Fehler passieren. Sonst
            # bleibt eine längst tote Verbindung in der Liste stehen, das
            # Programm hält sie für intakt, und ab da schlägt jedes weitere
            # Senden an diesen Freund fehl - bis VP4 neu gestartet wird.
            with self._conn_lock:
                if self._connections.get(friend_id) is conn:
                    del self._connections[friend_id]
            try:
                conn.close()
            except OSError:
                pass

    def _receive_data_frame(self, friend_id: str, conn: socket.socket, meta: dict) -> bool:
        """Empfängt den Datenblock, der auf eine Dateiankündigung folgt.

        Rückgabe: True, wenn danach auf der Verbindung weitergelesen werden
        kann, False, wenn sie beendet werden muss.
        """
        try:
            header = self._recv_exact(conn, 6)
        except (ConnectionError, OSError, struct.error):
            return False
        flags, msgtype, length = struct.unpack("!BBI", header)

        if msgtype != MSG_DATA or length > MAX_FILE_SIZE:
            # Hier stimmt etwas nicht. Den angekündigten Rest einfach
            # wegzulesen wäre gefährlich - die Länge kann beliebig groß
            # angegeben sein. Also lieber die Verbindung beenden, sie wird
            # beim nächsten Senden automatisch neu aufgebaut.
            self.events.put(("error",
                "Ungültige Dateiübertragung empfangen - Verbindung getrennt."))
            return False

        if (flags & 1) and length > MAX_ENCRYPTED_FILE_SIZE + 65536:
            # Eine verschlüsselte Datei muss am Stück in den Arbeitsspeicher.
            # Beim Senden gilt dafür ein Limit von 50 MB - beim Empfangen
            # dieselbe Grenze ziehen, sonst könnte uns eine einzige
            # Übertragung den Speicher volllaufen lassen.
            self.events.put(("error",
                "Eine verschlüsselte Datei war zu groß - Verbindung getrennt."))
            return False

        ziel = zieldatei(meta.get("name"))

        try:
            if flags & 1:
                # Verschlüsselt -> komplett im Speicher empfangen und entschlüsseln
                roh = self._recv_exact(conn, length)
                try:
                    daten = self._maybe_decrypt(friend_id, flags, roh)
                except Exception:
                    self.events.put(("error",
                        "Eine Datei konnte nicht entschlüsselt werden. Habt ihr beide "
                        "denselben gemeinsamen Schlüssel hinterlegt?"))
                    # Der Datenstrom selbst ist in Ordnung - es wurden genau
                    # so viele Bytes gelesen wie angekündigt. Nur der Inhalt
                    # ist unlesbar. Die Verbindung bleibt bestehen.
                    return True
                ziel.write_bytes(daten)
            else:
                # Unverschlüsselt -> direkt in die Datei streamen (spart RAM bei Videos)
                rest = length
                with open(ziel, "wb") as f:
                    while rest > 0:
                        block = self._recv_exact(conn, min(65536, rest))
                        f.write(block)
                        rest -= len(block)
        except (ConnectionError, OSError, struct.error):
            # Übertragung mittendrin abgebrochen, z.B. weil das WLAN kurz weg
            # war. Die halb geschriebene Datei ist wertlos und würde sonst als
            # scheinbar gültiges Bild/Video im Ordner liegen bleiben.
            try:
                if ziel.exists():
                    ziel.unlink()
            except OSError:
                pass
            return False

        self.events.put(("file", {"from": friend_id, "path": str(ziel),
                                  "kind": meta.get("kind", "file"),
                                  "name": meta.get("name", ziel.name)}))
        return True

    # -------------------------------------------------------------- Senden

    def _get_connection(self, friend_id: str):
        with self._conn_lock:
            sock = self._connections.get(friend_id)
        if sock is not None:
            return sock

        with self._peers_lock:
            eintrag = self.peers.get(friend_id)
        if eintrag is None:
            return None
        ip = eintrag[0]
        port = eintrag[2] if len(eintrag) > 2 else self.chat_port

        try:
            sock = socket.create_connection((ip, port), timeout=4)
            self._send_frame(sock, MSG_HELLO, self.my_id.encode("utf-8"), encrypted=False)
        except OSError:
            return None

        with self._conn_lock:
            self._connections[friend_id] = sock
        threading.Thread(target=self._read_loop, args=(friend_id, sock), daemon=True).start()
        return sock

    def send_text(self, friend_id: str, text: str):
        sock = self._get_connection(friend_id)
        if sock is None:
            raise ConnectionError(
                "Der Freund ist gerade nicht erreichbar. Ist er im selben WLAN "
                "und hat VP4 offen?")
        verschluesselt, payload = self._maybe_encrypt(friend_id, text.encode("utf-8"))
        self._send_frame(sock, MSG_TEXT, payload, verschluesselt)
        return verschluesselt

    def send_file(self, friend_id: str, filepath: str, kind: str):
        sock = self._get_connection(friend_id)
        if sock is None:
            raise ConnectionError(
                "Der Freund ist gerade nicht erreichbar. Ist er im selben WLAN "
                "und hat VP4 offen?")

        groesse = os.path.getsize(filepath)
        if groesse > MAX_FILE_SIZE:
            raise ValueError(f"Die Datei ist zu groß ({groesse / 1024 / 1024:.0f} MB, "
                             f"erlaubt sind 300 MB).")

        name = os.path.basename(filepath)
        freund = self.friends.get(friend_id) or {}
        will_verschluesseln = bool(freund.get("shared_key_b64"))

        # Früher wurde eine zu grosse Datei einfach im Klartext geschickt und
        # nur eine Zeile in die Statusleiste geschrieben. Wer einen
        # gemeinsamen Schlüssel eingetragen hat, erwartet aber, dass auch
        # verschlüsselt wird - eine Sicherheitszusage still zu unterlaufen
        # ist schlimmer, als die Übertragung abzulehnen.
        if will_verschluesseln and groesse > MAX_ENCRYPTED_FILE_SIZE:
            raise ValueError(
                f"'{name}' ist {groesse / 1024 / 1024:.0f} MB gross. "
                f"Verschlüsselt gehen zurzeit höchstens "
                f"{MAX_ENCRYPTED_FILE_SIZE // 1024 // 1024} MB, und "
                f"unverschlüsselt wird sie nicht heimlich verschickt. "
                f"Verschlüssele sie auf der Seite 'Dateien' und schicke "
                f"dann die .vp4-Datei.")

        kann_verschluesseln = will_verschluesseln

        meta = {"kind": kind, "name": name, "size": groesse}
        meta_verschluesselt, meta_payload = self._maybe_encrypt(
            friend_id, json.dumps(meta).encode("utf-8"))
        self._send_frame(sock, MSG_META, meta_payload, meta_verschluesselt)

        if kann_verschluesseln:
            with open(filepath, "rb") as f:
                roh = f.read()
            key = base64.b64decode(freund["shared_key_b64"])
            self._send_frame(sock, MSG_DATA, ModernCrypto.aes_encrypt_bytes(roh, key),
                             encrypted=True)
        else:
            sock.sendall(struct.pack("!BBI", 0, MSG_DATA, groesse))
            with open(filepath, "rb") as f:
                while True:
                    block = f.read(65536)
                    if not block:
                        break
                    sock.sendall(block)
        return kann_verschluesseln

    # ------------------------------------------ Ver-/Entschlüsseln der Daten

    def _maybe_encrypt(self, friend_id: str, payload: bytes):
        return payload_verschluesseln(self.friends, friend_id, payload)

    def _maybe_decrypt(self, friend_id: str, flags: int, payload: bytes) -> bytes:
        return payload_entschluesseln(self.friends, friend_id, bool(flags & 1), payload)

    # --------------------------------------------------------- Datenrahmen

    @staticmethod
    def _send_frame(sock: socket.socket, msgtype: int, payload: bytes, encrypted: bool):
        flags = 1 if encrypted else 0
        sock.sendall(struct.pack("!BBI", flags, msgtype, len(payload)) + payload)

    @staticmethod
    def _recv_exact(sock: socket.socket, n: int) -> bytes:
        buf = bytearray()
        while len(buf) < n:
            block = sock.recv(min(65536, n - len(buf)))
            if not block:
                raise ConnectionError("Verbindung wurde beendet.")
            buf.extend(block)
        return bytes(buf)

    @classmethod
    def _recv_frame(cls, sock: socket.socket):
        header = cls._recv_exact(sock, 6)
        flags, msgtype, length = struct.unpack("!BBI", header)
        if length > MAX_FILE_SIZE:
            raise struct.error("Unsinnig große Längenangabe empfangen.")
        return flags, msgtype, cls._recv_exact(sock, length)
