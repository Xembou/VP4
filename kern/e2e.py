# -*- coding: utf-8 -*-
"""kern/e2e.py - Ende-zu-Ende-Verschlüsselung für Freunde und Communities.

Nur fertige Bausteine aus `cryptography`, hier lediglich zusammengesteckt:
X25519 (Schlüsselvereinbarung), HKDF-SHA256 (Ableitung), AES-256-GCM
(Verschlüsselung mit Echtheitsprüfung), Ed25519 (Unterschrift), SHA-512
(Sicherheitsnummer). Kein eigenes Verfahren.

Was das schützt
---------------
* Discord, der Bot und alle anderen im Server sehen nur Geheimtext.
* Eine DM kann nur der Freund lesen, mit dem man den Paarschlüssel hat.
* Ein veränderter Kopf (Absender, Empfänger, Zeit, ID) oder Inhalt fällt
  beim Entschlüsseln auf und wird abgelehnt.

Was das NICHT schützt (gehört so auch in die Oberfläche, CLAUDE.md Regel 3)
----------------------------------------------------------------------------
* **Keine Forward Secrecy.** Der Paarschlüssel hängt nur an den beiden
  dauerhaften X25519-Schlüsseln. Wer später ein Gerät samt Tresor-Passwort
  erbeutet, kann damit auch alle alten, irgendwo mitgeschnittenen
  Nachrichten öffnen. Ein Double Ratchet wäre eine spätere Ausbaustufe.
* **Metadaten sind offen:** wer wem wann wie viel schreibt, steht im
  Umschlagkopf und damit bei Discord.
* In einer Community kann jedes Mitglied jeden Kanal lesen - alle
  Kanalschlüssel lassen sich aus dem Community-Schlüssel ableiten. Die
  Kanaltrennung ist saubere Schlüsselhygiene, keine Zugangskontrolle.
* Ein Hobby-Projekt, von niemandem geprüft.
"""

import hashlib
import os

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

from kern.identitaet import (SIGNATUR_LAENGE, Identitaet, karte_pruefen,
                             karte_schluessel, signatur_pruefen)

SCHLUESSEL_LAENGE = 32
NONCE_LAENGE = 12
NACHRICHTEN_ID_LAENGE = 16

PAAR_INFO = b"VP4 paar v1"
NACHRICHT_INFO = b"VP4 nachricht v1"
KANAL_INFO = b"VP4 kanal v1"
SIGNATUR_MARKE = b"VP4 sig v1"
SIGNATUR_MARKE_KONTEXT = b"VP4 sig kontext v1"

SICHERHEITSNUMMER_RUNDEN = 5200
SICHERHEITSNUMMER_VERSION = b"\x00\x00"


def _hkdf(geheim: bytes, salt: bytes, info: bytes, laenge: int = 32) -> bytes:
    return HKDF(algorithm=hashes.SHA256(), length=laenge, salt=salt, info=info).derive(geheim)


def _schluessel_pruefen(schluessel, was="Schlüssel") -> bytes:
    if not isinstance(schluessel, (bytes, bytearray)) or len(schluessel) != SCHLUESSEL_LAENGE:
        raise ValueError(f"{was} muss 32 Byte lang sein.")
    return bytes(schluessel)


# ---------------------------------------------------------------------------
#  Paarschlüssel (DMs)
# ---------------------------------------------------------------------------

def paar_schluessel(identitaet: Identitaet, peer_karte: dict) -> bytes:
    """Der gemeinsame 32-Byte-Schlüssel mit einem Freund.

        geteilt = X25519(eigener x privat, x öffentlich des Freundes)
        wurzel  = HKDF-SHA256(geteilt, salt=SHA256("idA|idB" sortiert),
                              info=b"VP4 paar v1")

    Beide Seiten rechnen dasselbe aus, ohne dass je ein Schlüssel über die
    Leitung geht. Der Salt aus beiden sortierten IDs bindet das Ergebnis an
    genau dieses Paar.

    Die Karte wird hier noch einmal vollständig geprüft. Das kostet fast
    nichts und heißt: Ein Schlüssel aus einer ungeprüften Karte kommt hier
    gar nicht erst durch. Ein Schlüssel "niedriger Ordnung" (etwa lauter
    Nullen) wird abgelehnt - mit ihm wäre das gemeinsame Geheimnis für jeden
    ausrechenbar.
    """
    karte = karte_pruefen(peer_karte)
    _, peer_x = karte_schluessel(karte)
    if karte["id"] == identitaet.id:
        raise ValueError("Mit sich selbst kann man keinen Paarschlüssel bilden.")
    geteilt = identitaet.dh(peer_x)
    salt = hashlib.sha256("|".join(sorted([identitaet.id, karte["id"]])).encode("ascii")).digest()
    return _hkdf(geteilt, salt, PAAR_INFO)


# ---------------------------------------------------------------------------
#  Nachrichten verschlüsseln
# ---------------------------------------------------------------------------

def _nachrichten_schluessel(schluessel: bytes, nachrichten_id: bytes) -> bytes:
    """Für jede Nachricht ein eigener Schlüssel, abgeleitet über ihre ID.

    Der Nonce ist zufällig; ein doppelter Nonce unter demselben Schlüssel
    wäre bei GCM der Totalschaden (CLAUDE.md, .vp4-Container). Mit einem
    Schlüssel pro Nachricht müssten schon ID UND Nonce zufällig gleich sein.
    """
    _schluessel_pruefen(schluessel)
    if not isinstance(nachrichten_id, (bytes, bytearray)) or len(nachrichten_id) != NACHRICHTEN_ID_LAENGE:
        raise ValueError("Die Nachrichten-ID muss 16 Byte lang sein.")
    return _hkdf(bytes(schluessel), bytes(nachrichten_id), NACHRICHT_INFO)


def nachricht_verschluesseln(schluessel: bytes, nachrichten_id: bytes, kopf: bytes,
                             klartext: bytes, nonce: bytes = None) -> tuple:
    """AES-256-GCM mit `kopf` als AAD. Rückgabe: (nonce, geheimtext).

    `nonce` darf vorgegeben werden, weil der Umschlag ihn selbst im Kopf
    trägt und der Kopf vollständig - samt Nonce - in die AAD gehen soll.
    Vorgegeben heißt aber: frisch aus os.urandom(12), nie wiederverwendet.
    """
    if nonce is None:
        nonce = os.urandom(NONCE_LAENGE)
    if not isinstance(nonce, (bytes, bytearray)) or len(nonce) != NONCE_LAENGE:
        raise ValueError("Der Nonce muss 12 Byte lang sein.")
    schl = _nachrichten_schluessel(schluessel, nachrichten_id)
    return bytes(nonce), AESGCM(schl).encrypt(bytes(nonce), bytes(klartext), bytes(kopf))


def nachricht_entschluesseln(schluessel: bytes, nachrichten_id: bytes, kopf: bytes,
                             nonce: bytes, geheimtext: bytes) -> bytes:
    """Gegenstück. ValueError mit Text, wenn Schlüssel, Kopf oder Inhalt nicht passen.

    AES-GCM meldet das mit InvalidTag ganz ohne Text. Der Empfang muss aber
    unterscheiden können zwischen "war für dich, ging nicht auf" und anderen
    Fehlern - dieselbe Übersetzung wie früher in payload_entschluesseln.
    """
    if not isinstance(nonce, (bytes, bytearray)) or len(nonce) != NONCE_LAENGE:
        raise ValueError("Der Nonce muss 12 Byte lang sein.")
    schl = _nachrichten_schluessel(schluessel, nachrichten_id)
    try:
        return AESGCM(schl).decrypt(bytes(nonce), bytes(geheimtext), bytes(kopf))
    except InvalidTag:
        raise ValueError("Nachricht ließ sich nicht entschlüsseln: falscher "
                         "Schlüssel oder unterwegs verändert.") from None


# ---------------------------------------------------------------------------
#  Communities
# ---------------------------------------------------------------------------

def kanal_schluessel(community_schluessel: bytes, kanal_id: str) -> bytes:
    """Schlüssel eines Kanals = HKDF(Community-Schlüssel, salt=Kanal-ID).

    Wer den Community-Schlüssel hat, kann jeden Kanalschlüssel ableiten - die
    Trennung verhindert nur, dass eine Nachricht aus einem Kanal in einem
    anderen als gültig durchgeht.
    """
    _schluessel_pruefen(community_schluessel, "Community-Schlüssel")
    if not isinstance(kanal_id, str) or not kanal_id:
        raise ValueError("Die Kanal-ID fehlt.")
    return _hkdf(bytes(community_schluessel), kanal_id.encode("utf-8"), KANAL_INFO)


def _signatur_daten(inhalt: bytes, kontext: bytes) -> bytes:
    """Was genau unterschrieben wird.

    Ohne Kontext: b"VP4 sig v1" + inhalt.
    Mit Kontext (z. B. dem Umschlagkopf): eigene Marke + Länge (4 Byte) +
    Kontext + inhalt. Die Länge macht die Grenze eindeutig, die andere Marke
    verhindert, dass eine Unterschrift der einen Form als die andere gilt.
    """
    if kontext:
        return (SIGNATUR_MARKE_KONTEXT + len(kontext).to_bytes(4, "big")
                + bytes(kontext) + bytes(inhalt))
    return SIGNATUR_MARKE + bytes(inhalt)


def signiert_einpacken(identitaet: Identitaet, inhalt: bytes, kontext: bytes = b"") -> bytes:
    """inhalt + 64 Byte Ed25519-Unterschrift.

    `kontext` wird mitunterschrieben, aber nicht mitgeschickt - der Empfänger
    hat ihn ohnehin (den Umschlagkopf). So lässt sich eine unterschriebene
    Nachricht nicht in einen anderen Umschlag mit anderer ID oder Zeit
    umpacken.
    """
    return bytes(inhalt) + identitaet.signieren(_signatur_daten(inhalt, kontext))


def signiert_auspacken(daten: bytes, ed_pub: bytes, kontext: bytes = b"") -> bytes:
    """Prüft die Unterschrift und gibt den Inhalt zurück; ValueError, wenn sie nicht passt."""
    if not isinstance(daten, (bytes, bytearray)) or len(daten) < SIGNATUR_LAENGE:
        raise ValueError("Zu kurz für eine unterschriebene Nachricht.")
    inhalt, signatur = bytes(daten[:-SIGNATUR_LAENGE]), bytes(daten[-SIGNATUR_LAENGE:])
    signatur_pruefen(ed_pub, signatur, _signatur_daten(inhalt, kontext))
    return inhalt


# ---------------------------------------------------------------------------
#  Sicherheitsnummer
# ---------------------------------------------------------------------------

def _haelfte(karte: dict) -> str:
    """30 Ziffern für eine Seite, nach dem Muster von Signal.

    SHA-512 5200-mal hintereinander, jede Runde über (vorheriges Ergebnis +
    Schlüssel). Die vielen Runden machen es teuer, gezielt einen zweiten
    Schlüssel zu suchen, dessen Nummer gleich aussieht.
    """
    ed, x = karte_schluessel(karte)
    schluessel = ed + x
    digest = SICHERHEITSNUMMER_VERSION + schluessel + karte["id"].encode("ascii")
    for _ in range(SICHERHEITSNUMMER_RUNDEN):
        digest = hashlib.sha512(digest + schluessel).digest()
    return "".join(f"{int.from_bytes(digest[i:i + 5], 'big') % 100000:05d}"
                   for i in range(0, 30, 5))


def sicherheitsnummer(karte_a: dict, karte_b: dict) -> str:
    """60 Ziffern in 12 Fünferblöcken - auf beiden Geräten dieselbe.

    Die beiden Hälften werden sortiert, damit A und B dieselbe Zahl sehen.
    Vergleichen die beiden sie (vorlesen, nebeneinanderhalten), wissen sie,
    dass kein Dritter einen Schlüssel untergeschoben hat. Das ist der einzige
    Schutz gegen einen, der schon die ERSTE Karte fälscht.
    """
    a = _haelfte(karte_pruefen(karte_a))
    b = _haelfte(karte_pruefen(karte_b))
    ziffern = "".join(sorted([a, b]))
    return " ".join(ziffern[i:i + 5] for i in range(0, 60, 5))
