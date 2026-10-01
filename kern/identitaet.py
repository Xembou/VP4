# -*- coding: utf-8 -*-
"""kern/identitaet.py - wer man im Messenger ist, und wie andere das prüfen.

Jede Installation erzeugt beim ersten Start eine Identität aus zwei
Schlüsselpaaren:

* **Ed25519** zum Unterschreiben (Visitenkarte, Anfragen, Community-
  Nachrichten, Manifeste),
* **X25519** zum Vereinbaren des gemeinsamen Schlüssels mit einem Freund.

Zwei getrennte Paare statt eines umgerechneten, weil `cryptography` beides
fertig anbietet und man so nie einen Schlüssel für zwei Zwecke benutzt.

Die Nutzer-ID ist KEINE frei gewählte Zahl mehr, sondern ein Fingerabdruck
der beiden öffentlichen Schlüssel:

    ID = Crockford-Base32( SHA-256(b"VP4-ID1" || ed_pub || x_pub) )[:50 Bit]

Damit ist die ID an die Schlüssel gebunden. In 4.x wurde beim
Verbindungsaufbau nur *behauptet*, wer man ist (CLAUDE.md, "Bekannte
Design-Schwäche"); jetzt wird eine Visitenkarte, deren Schlüssel nicht zur
behaupteten ID passen, abgelehnt.

Ehrliche Grenze: 50 Bit sind kurz genug zum Abtippen, aber kein voller
Fingerabdruck. Eine zweite Schlüsselkombination mit derselben ID zu finden
kostet rund 2^50 Versuche - für Mitschüler unerreichbar, für jemanden mit
viel Rechenzeit nicht. Dagegen helfen zwei Dinge, die NICHT hier stehen:
Die erste geprüfte Karte pro ID wird festgehalten ("Pinning"), und die
Sicherheitsnummer (kern/e2e.py) läuft über die vollen Schlüssel.

Hier wird nichts gespeichert und nichts verschickt; das machen andere
Module. Alles hier ist reine Rechnung und lässt sich ohne Netz testen.
"""

import base64
import hashlib
import json
import re
import time

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey, Ed25519PublicKey)
from cryptography.hazmat.primitives.asymmetric.x25519 import (
    X25519PrivateKey, X25519PublicKey)


# ---------------------------------------------------------------------------
#  Crockford-Base32
# ---------------------------------------------------------------------------
#
# Crockford statt des üblichen Base32, weil Menschen die ID abtippen: Es
# fehlen I, L, O und U - die drei ersten verwechselt man mit 1 und 0, das U
# verhindert zufällige Schimpfwörter. Beim Einlesen wird O als 0 und I/L als 1
# gelesen, so dass ein Vertipper trotzdem die richtige ID ergibt.

CROCKFORD = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"
_CROCKFORD_MENGE = frozenset(CROCKFORD)
_VERWECHSLUNG = str.maketrans({"O": "0", "I": "1", "L": "1"})


def crockford_kodieren(daten: bytes, zeichen: int) -> str:
    """Die ersten `zeichen` * 5 Bit von `daten` als Crockford-Text.

    Gebraucht für die Nutzer-ID (50 Bit aus einem SHA-256) und für Community-
    und Kanal-Kennungen (40 Bit = 5 zufällige Bytes = 8 Zeichen).
    """
    bits = zeichen * 5
    if bits > len(daten) * 8:
        raise ValueError("Zu wenige Bytes für so viele Zeichen.")
    zahl = int.from_bytes(daten, "big") >> (len(daten) * 8 - bits)
    aus = []
    for _ in range(zeichen):
        aus.append(CROCKFORD[zahl & 31])
        zahl >>= 5
    return "".join(reversed(aus))


def crockford_dekodieren(text: str, laenge_bytes: int) -> bytes:
    """Gegenstück für den Fall, dass die Zeichen genau `laenge_bytes` füllen.

    Nur für 8 Zeichen <-> 5 Bytes gedacht (Community-Kennung im
    Einladungscode); bei der Nutzer-ID gehen 50 Bit nicht in ganze Bytes auf.
    """
    if len(text) * 5 != laenge_bytes * 8:
        raise ValueError("Länge passt nicht zu ganzen Bytes.")
    zahl = 0
    for z in text:
        if z not in _CROCKFORD_MENGE:
            raise ValueError(f"Ungültiges Zeichen in der Kennung: {z!r}")
        zahl = (zahl << 5) | CROCKFORD.index(z)
    return zahl.to_bytes(laenge_bytes, "big")


def ist_crockford(text, laenge: int) -> bool:
    """Wahr, wenn `text` genau `laenge` Zeichen aus dem Crockford-Alphabet hat."""
    return (isinstance(text, str) and len(text) == laenge
            and all(z in _CROCKFORD_MENGE for z in text))


# ---------------------------------------------------------------------------
#  Nutzer-ID
# ---------------------------------------------------------------------------

ID_MARKE = b"VP4-ID1"
ID_ZEICHEN = 10          # 50 Bit
SCHLUESSEL_LAENGE = 32   # Ed25519 und X25519, roh
SIGNATUR_LAENGE = 64     # Ed25519

_ID_MUSTER = re.compile(r"^[0-9A-HJKMNP-TV-Z]{5}-[0-9A-HJKMNP-TV-Z]{5}$")


def id_aus_schluesseln(ed_pub: bytes, x_pub: bytes) -> str:
    """Die Nutzer-ID "XXXXX-XXXXX" zu zwei öffentlichen Schlüsseln.

    Die Marke b"VP4-ID1" sorgt dafür, dass derselbe Hash nirgends sonst eine
    Bedeutung hat; die 1 lässt Platz für ein späteres Verfahren.
    """
    if (not isinstance(ed_pub, (bytes, bytearray)) or len(ed_pub) != SCHLUESSEL_LAENGE
            or not isinstance(x_pub, (bytes, bytearray)) or len(x_pub) != SCHLUESSEL_LAENGE):
        raise ValueError("Öffentliche Schlüssel müssen je 32 Byte lang sein.")
    digest = hashlib.sha256(ID_MARKE + bytes(ed_pub) + bytes(x_pub)).digest()
    roh = crockford_kodieren(digest, ID_ZEICHEN)
    return roh[:5] + "-" + roh[5:]


def ist_nutzer_id(text) -> bool:
    """Wahr nur für die genaue Schreibweise "XXXXX-XXXXX" (Großbuchstaben).

    Bewusst streng: Was aus dem Netz kommt, muss schon richtig sein. Für das,
    was ein Mensch eintippt, gibt es id_normalisieren().
    """
    return isinstance(text, str) and bool(_ID_MUSTER.match(text))


def id_normalisieren(text: str) -> str:
    """Macht aus einer eingetippten ID die genaue Schreibweise.

    Großschreibung, Leerzeichen und Bindestriche egal, O wird 0, I und L
    werden 1 (Crockford). Was danach keine 10 gültigen Zeichen sind, ist
    keine ID -> ValueError.
    """
    if not isinstance(text, str):
        raise ValueError("Die ID muss ein Text sein.")
    roh = re.sub(r"[\s\-‐-―]", "", text).upper().translate(_VERWECHSLUNG)
    if not ist_crockford(roh, ID_ZEICHEN):
        raise ValueError("Das ist keine gültige VP4-ID (Form: XXXXX-XXXXX).")
    return roh[:5] + "-" + roh[5:]


# ---------------------------------------------------------------------------
#  Identität
# ---------------------------------------------------------------------------

IDENTITAET_VERSION = 1


def _roh_privat(schluessel) -> bytes:
    return schluessel.private_bytes(serialization.Encoding.Raw,
                                    serialization.PrivateFormat.Raw,
                                    serialization.NoEncryption())


def _roh_oeffentlich(schluessel) -> bytes:
    return schluessel.public_bytes(serialization.Encoding.Raw,
                                   serialization.PublicFormat.Raw)


class Identitaet:
    """Die beiden privaten Schlüssel einer Installation.

    Gespeichert wird sie von kern/tresor.py über to_bytes(); hier steht nur,
    wie man mit ihr rechnet. Die privaten Schlüssel verlassen das Objekt nur
    über to_bytes() - alles andere gibt öffentliche Werte oder Ergebnisse
    heraus.
    """

    def __init__(self, ed_privat: Ed25519PrivateKey, x_privat: X25519PrivateKey):
        self._ed = ed_privat
        self._x = x_privat
        self._ed_pub = _roh_oeffentlich(ed_privat.public_key())
        self._x_pub = _roh_oeffentlich(x_privat.public_key())

    @classmethod
    def neu(cls) -> "Identitaet":
        """Erzeugt eine frische Identität (beim allerersten Start)."""
        return cls(Ed25519PrivateKey.generate(), X25519PrivateKey.generate())

    def to_bytes(self) -> bytes:
        """65 Byte: Version (1) | Ed25519 privat (32) | X25519 privat (32).

        Roh und ohne Schutz - verschlüsselt wird das vom Tresor. Die
        Versionsnummer vorn ist dieselbe Lehre wie bei den Dateiformaten in
        CLAUDE.md: Was in einer Datei steht, muss sagen, wie es zu lesen ist.
        """
        return bytes([IDENTITAET_VERSION]) + _roh_privat(self._ed) + _roh_privat(self._x)

    @classmethod
    def from_bytes(cls, daten: bytes) -> "Identitaet":
        if not isinstance(daten, (bytes, bytearray)) or len(daten) != 1 + 2 * SCHLUESSEL_LAENGE:
            raise ValueError("Gespeicherte Identität hat die falsche Länge.")
        if daten[0] != IDENTITAET_VERSION:
            raise ValueError(f"Unbekannte Version der Identität: {daten[0]}")
        try:
            ed = Ed25519PrivateKey.from_private_bytes(bytes(daten[1:33]))
            x = X25519PrivateKey.from_private_bytes(bytes(daten[33:65]))
        except Exception as e:
            raise ValueError(f"Gespeicherte Identität ist beschädigt: {e}") from None
        return cls(ed, x)

    @property
    def ed_pub(self) -> bytes:
        return self._ed_pub

    @property
    def x_pub(self) -> bytes:
        return self._x_pub

    @property
    def id(self) -> str:
        return id_aus_schluesseln(self.ed_pub, self.x_pub)

    def signieren(self, daten: bytes) -> bytes:
        """Ed25519-Unterschrift (64 Byte) über `daten`."""
        return self._ed.sign(bytes(daten))

    def dh(self, peer_x_pub: bytes) -> bytes:
        """X25519 mit dem öffentlichen Schlüssel des Gegenübers.

        Ein Schlüssel "niedriger Ordnung" (z. B. 32 Nullbytes) ergäbe ein
        Ergebnis aus lauter Nullen - jeder könnte es ausrechnen. OpenSSL lehnt
        das selbst ab; die zweite Prüfung hier steht nur, damit es nicht von
        dieser einen Stelle abhängt.
        """
        if not isinstance(peer_x_pub, (bytes, bytearray)) or len(peer_x_pub) != SCHLUESSEL_LAENGE:
            raise ValueError("Der X25519-Schlüssel des Gegenübers muss 32 Byte lang sein.")
        try:
            geteilt = self._x.exchange(X25519PublicKey.from_public_bytes(bytes(peer_x_pub)))
        except ValueError:
            raise ValueError("Unbrauchbarer Schlüssel des Gegenübers "
                             "(Punkt niedriger Ordnung).") from None
        if geteilt == bytes(len(geteilt)):
            raise ValueError("Unbrauchbarer Schlüssel des Gegenübers "
                             "(gemeinsames Geheimnis wäre null).")
        return geteilt


def signatur_pruefen(ed_pub: bytes, signatur: bytes, daten: bytes) -> None:
    """Prüft eine Ed25519-Unterschrift; ValueError mit deutschem Text, wenn nicht.

    `cryptography` meldet eine falsche Unterschrift mit InvalidSignature ohne
    Text. Überall in VP4 heißt "kaputte Eingabe" aber ValueError - dieselbe
    Lehre wie bei payload_entschluesseln in CLAUDE.md.
    """
    if not isinstance(ed_pub, (bytes, bytearray)) or len(ed_pub) != SCHLUESSEL_LAENGE:
        raise ValueError("Ed25519-Schlüssel muss 32 Byte lang sein.")
    if not isinstance(signatur, (bytes, bytearray)) or len(signatur) != SIGNATUR_LAENGE:
        raise ValueError("Unterschrift muss 64 Byte lang sein.")
    try:
        Ed25519PublicKey.from_public_bytes(bytes(ed_pub)).verify(bytes(signatur), bytes(daten))
    except InvalidSignature:
        raise ValueError("Die Unterschrift ist ungültig.") from None
    except ValueError as e:
        raise ValueError(f"Ungültiger Ed25519-Schlüssel: {e}") from None


# ---------------------------------------------------------------------------
#  Kanonisches JSON und Base64
# ---------------------------------------------------------------------------

def kanonisch(daten: dict) -> bytes:
    """Genau eine Byte-Folge pro Inhalt - Voraussetzung für Unterschriften.

    Sortierte Schlüssel, keine Leerzeichen, Umlaute als UTF-8 statt \\u-Folgen.
    Wer unterschreibt und wer prüft, muss dieselben Bytes bekommen, sonst
    passt keine Unterschrift.
    """
    return json.dumps(daten, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False).encode("utf-8")


def b64(daten: bytes) -> str:
    return base64.b64encode(bytes(daten)).decode("ascii")


def b64_lesen(text, laenge: int = None) -> bytes:
    """Standard-Base64 streng lesen.

    Streng heißt: nur das Alphabet, und neu kodiert muss genau derselbe Text
    herauskommen. Sonst gäbe es mehrere Schreibweisen derselben Bytes, und
    eine Karte ließe sich verändern, ohne dass sich ihr Inhalt ändert.
    """
    if not isinstance(text, str):
        raise ValueError("Base64-Feld muss ein Text sein.")
    try:
        roh = base64.b64decode(text.encode("ascii"), validate=True)
    except Exception:
        raise ValueError("Ungültiges Base64.") from None
    if b64(roh) != text:
        raise ValueError("Base64 nicht in der üblichen Schreibweise.")
    if laenge is not None and len(roh) != laenge:
        raise ValueError(f"Feld muss {laenge} Byte lang sein, hat {len(roh)}.")
    return roh


def b64url(daten: bytes) -> str:
    """URL-sicheres Base64 ohne '=' - für Codes, die man kopiert."""
    return base64.urlsafe_b64encode(bytes(daten)).decode("ascii").rstrip("=")


def b64url_lesen(text: str) -> bytes:
    if not isinstance(text, str) or not re.fullmatch(r"[A-Za-z0-9_-]*", text):
        raise ValueError("Der Code enthält ungültige Zeichen.")
    if len(text) % 4 == 1:
        raise ValueError("Der Code ist abgeschnitten.")
    try:
        roh = base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))
    except Exception:
        raise ValueError("Der Code ist beschädigt.") from None
    if b64url(roh) != text:
        raise ValueError("Der Code ist beschädigt.")
    return roh


# ---------------------------------------------------------------------------
#  Visitenkarte
# ---------------------------------------------------------------------------
#
# Die Karte ist öffentlich: Name, Farbe und die beiden öffentlichen Schlüssel,
# unterschrieben mit dem eigenen Ed25519-Schlüssel. Wer sie bekommt, prüft
# dreierlei: Unterschrift, Schlüssellängen, und dass die Schlüssel wirklich
# die behauptete ID ergeben.
#
# Unterschrieben wird KARTE_SIGNATUR_MARKE + kanonisches JSON. Die Marke
# trennt die Bereiche: Eine Karten-Unterschrift kann nie als Unterschrift
# unter einem Manifest oder einer Nachricht durchgehen, auch wenn sich die
# Felder zufällig ähneln.

KARTE_VERSION = 1
KARTE_SIGNATUR_MARKE = b"VP4 karte v1\n"
KARTE_FELDER = frozenset({"v", "id", "name", "ed", "x", "avatar_farbe", "ts"})
KARTE_MAX_BYTES = 2048
NAME_MAX = 64
FARBE_MAX = 32
FREUNDESCODE_MARKE = "VP4C1-"

_STEUERZEICHEN = re.compile(r"[\x00-\x1f\x7f  ‪-‮⁦-⁩]")


def name_pruefen(name, was: str = "Name") -> str:
    """1 bis 64 Zeichen, keine Steuer- oder Richtungszeichen.

    Richtungszeichen (U+202E usw.) könnten in der Oberfläche einen Namen
    rückwärts anzeigen und so einen anderen vortäuschen.
    """
    if not isinstance(name, str):
        raise ValueError(f"{was} muss ein Text sein.")
    if not name.strip():
        raise ValueError(f"{was} darf nicht leer sein.")
    if len(name) > NAME_MAX:
        raise ValueError(f"{was} ist zu lang (höchstens {NAME_MAX} Zeichen).")
    if _STEUERZEICHEN.search(name):
        raise ValueError(f"{was} enthält unerlaubte Steuerzeichen.")
    return name


def _karten_bytes(felder: dict) -> bytes:
    return KARTE_SIGNATUR_MARKE + kanonisch(felder)


def karte_bauen(identitaet: Identitaet, name: str, avatar_farbe: str, ts: int = None) -> dict:
    """Die eigene, unterschriebene Visitenkarte.

    `ts` (Sekunden seit 1970) zeigt, welche von zwei gültigen Karten
    derselben ID die neuere ist - etwa nach einer Namensänderung.
    """
    felder = {
        "v": KARTE_VERSION,
        "id": identitaet.id,
        "name": name_pruefen(name),
        "ed": b64(identitaet.ed_pub),
        "x": b64(identitaet.x_pub),
        "avatar_farbe": _farbe_pruefen(avatar_farbe),
        "ts": int(time.time()) if ts is None else int(ts),
    }
    karte = dict(felder, sig=b64(identitaet.signieren(_karten_bytes(felder))))
    if len(kanonisch(karte)) > KARTE_MAX_BYTES:
        raise ValueError("Die Visitenkarte wäre zu groß.")
    return karte


def _farbe_pruefen(farbe) -> str:
    if not isinstance(farbe, str) or len(farbe) > FARBE_MAX or _STEUERZEICHEN.search(farbe):
        raise ValueError("Ungültige Avatarfarbe.")
    return farbe


def karte_pruefen(karte) -> dict:
    """Prüft eine fremde Karte vollständig und gibt eine saubere Kopie zurück.

    ValueError, wenn irgendetwas nicht stimmt: Aufbau, unbekannte Felder,
    Längen, Unterschrift, oder die Schlüssel ergeben nicht die behauptete ID.
    Unbekannte Felder werden abgelehnt statt ignoriert - sonst könnte jemand
    Zusätze anhängen, die zwar mitunterschrieben sind, die eine ältere
    Version aber gar nicht versteht.
    """
    if not isinstance(karte, dict):
        raise ValueError("Die Visitenkarte ist kein Objekt.")
    try:
        groesse = len(kanonisch(karte))
    except (TypeError, ValueError):
        raise ValueError("Die Visitenkarte enthält unerlaubte Werte.") from None
    if groesse > KARTE_MAX_BYTES:
        raise ValueError("Die Visitenkarte ist zu groß.")
    felder = {k: v for k, v in karte.items() if k != "sig"}
    if set(felder) != KARTE_FELDER or "sig" not in karte:
        raise ValueError("Die Visitenkarte hat nicht die erwarteten Felder.")
    if felder["v"] != KARTE_VERSION or isinstance(felder["v"], bool):
        raise ValueError(f"Unbekannte Version der Visitenkarte: {felder['v']!r}")
    if not ist_nutzer_id(felder["id"]):
        raise ValueError("Die ID auf der Visitenkarte hat die falsche Form.")
    name_pruefen(felder["name"])
    _farbe_pruefen(felder["avatar_farbe"])
    if not isinstance(felder["ts"], int) or isinstance(felder["ts"], bool) or felder["ts"] < 0:
        raise ValueError("Ungültiger Zeitstempel auf der Visitenkarte.")
    ed = b64_lesen(felder["ed"], SCHLUESSEL_LAENGE)
    x = b64_lesen(felder["x"], SCHLUESSEL_LAENGE)
    if id_aus_schluesseln(ed, x) != felder["id"]:
        raise ValueError("Die Schlüssel auf der Visitenkarte passen nicht zur ID.")
    signatur_pruefen(ed, b64_lesen(karte["sig"], SIGNATUR_LAENGE), _karten_bytes(felder))
    return dict(felder, sig=karte["sig"])


def karte_schluessel(karte: dict) -> tuple:
    """(ed_pub, x_pub) als rohe Bytes aus einer GEPRÜFTEN Karte."""
    return b64_lesen(karte["ed"], SCHLUESSEL_LAENGE), b64_lesen(karte["x"], SCHLUESSEL_LAENGE)


def freundescode_bauen(karte: dict) -> str:
    """"VP4C1-" + Base64url der ganzen Karte - zum Verschicken auf jedem Weg.

    Weil die Karte unterschrieben ist, darf der Code ruhig durch fremde Hände
    gehen: Verändern lässt er sich nicht, nur weitergeben.
    """
    return FREUNDESCODE_MARKE + b64url(kanonisch(karte_pruefen(karte)))


def freundescode_lesen(code: str) -> dict:
    """Gegenstück zu freundescode_bauen(); gibt die GEPRÜFTE Karte zurück."""
    if not isinstance(code, str):
        raise ValueError("Der Freundescode muss ein Text sein.")
    code = re.sub(r"\s", "", code)
    if not code.startswith(FREUNDESCODE_MARKE):
        raise ValueError("Das ist kein VP4-Freundescode (er beginnt mit VP4C1-).")
    rest = code[len(FREUNDESCODE_MARKE):]
    if len(rest) > (KARTE_MAX_BYTES * 4) // 3 + 4:
        raise ValueError("Der Freundescode ist zu lang.")
    roh = b64url_lesen(rest)
    try:
        karte = json.loads(roh.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise ValueError("Der Freundescode ist beschädigt.") from None
    return karte_pruefen(karte)
