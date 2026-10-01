# -*- coding: utf-8 -*-
"""kern/umschlag.py - der binäre Umschlag, in dem jede Nachricht reist.

Derselbe Umschlag geht übers WLAN und über Discord; der Transport ist egal.

    b"VP4N" | ver (1) | typ (1) | von (10) | an (12) | msg_id (16)
            | ts_ms (8, big endian) | nonce (12) | body

Alles vor `body` ist der Kopf (64 Byte) und geht vollständig als AAD in
AES-GCM ein bzw. wird mitunterschrieben. Wer also Absender, Empfänger, Zeit
oder ID ändert, macht den Umschlag ungültig.

* `von`: die Nutzer-ID OHNE Bindestrich (10 ASCII-Zeichen). Beim Lesen wird
  der Bindestrich wieder eingesetzt; nach außen heißt eine ID immer
  "XXXXX-XXXXX".
* `an`: 12 ASCII-Zeichen, mit Leerzeichen aufgefüllt - entweder eine
  Nutzer-ID ohne Bindestrich (10 + 2 Leerzeichen) oder eine Community-
  Kennung "G-XXXXXXXX" (10 + 2 Leerzeichen). Die zwei freien Stellen halten
  Platz für eine spätere, längere Kennung, ohne die Version zu ändern.
* Die Kanal-ID steht NICHT im Kopf, sondern im verschlüsselten Inhalt
  (Begründung in kern/communities.py).

Offen bleibt, was im Kopf steht: wer wem wann wie viel schickt. Das sieht
Discord, und das soll die Oberfläche auch so sagen.

Die Typen ANFRAGE, ANNAHME, ABLEHNUNG und KARTE tragen eine unterschriebene
Visitenkarte und sind NICHT verschlüsselt - Karten sind öffentlich, und vor
dem Handschlag gibt es noch keinen gemeinsamen Schlüssel. Ihr Nonce ist
12 Nullbytes. NACHRICHT und DATEI_TEIL sind verschlüsselt.
"""

import json
import os
import time
from dataclasses import dataclass

from kern import e2e
from kern.communities import COMMUNITY_ID_LAENGE, ist_community_id
from kern.identitaet import (ID_ZEICHEN, KARTE_MAX_BYTES, SIGNATUR_LAENGE,
                             Identitaet, ist_nutzer_id, kanonisch,
                             karte_pruefen, karte_schluessel)

MARKE = b"VP4N"
VERSION = 1

ANFRAGE = 1
ANNAHME = 2
ABLEHNUNG = 3
NACHRICHT = 4
DATEI_TEIL = 5
KARTE = 6

TYPEN = frozenset({ANFRAGE, ANNAHME, ABLEHNUNG, NACHRICHT, DATEI_TEIL, KARTE})
KARTEN_TYPEN = frozenset({ANFRAGE, ANNAHME, ABLEHNUNG, KARTE})
VERSCHLUESSELTE_TYPEN = frozenset({NACHRICHT, DATEI_TEIL})

VON_LAENGE = ID_ZEICHEN          # 10
AN_LAENGE = 12
MSG_ID_LAENGE = 16
TS_LAENGE = 8
NONCE_LAENGE = 12
KOPF_LAENGE = len(MARKE) + 1 + 1 + VON_LAENGE + AN_LAENGE + MSG_ID_LAENGE + TS_LAENGE + NONCE_LAENGE

# Größte Datei (100 MB) plus Luft für Kopf, Tag und JSON drumherum.
MAX_UMSCHLAG = 110 * 1024 * 1024

NULL_NONCE = bytes(NONCE_LAENGE)


# ---------------------------------------------------------------------------
#  Adressfelder
# ---------------------------------------------------------------------------

def von_kodieren(nutzer_id: str) -> bytes:
    if not ist_nutzer_id(nutzer_id):
        raise ValueError(f"Ungültiger Absender: {nutzer_id!r}")
    return nutzer_id.replace("-", "").encode("ascii")


def von_dekodieren(roh: bytes) -> str:
    try:
        text = roh.decode("ascii")
    except UnicodeDecodeError:
        raise ValueError("Absender im Umschlag ist kein ASCII.") from None
    nutzer_id = text[:5] + "-" + text[5:]
    if len(roh) != VON_LAENGE or not ist_nutzer_id(nutzer_id):
        raise ValueError("Absender im Umschlag hat die falsche Form.")
    return nutzer_id


def ist_ziel(text) -> bool:
    """Nutzer-ID oder Community-Kennung - beides darf in `an` stehen."""
    return ist_nutzer_id(text) or ist_community_id(text)


def an_kodieren(ziel: str) -> bytes:
    if ist_nutzer_id(ziel):
        roh = ziel.replace("-", "")
    elif ist_community_id(ziel):
        roh = ziel
    else:
        raise ValueError(f"Ungültiger Empfänger: {ziel!r}")
    return roh.ljust(AN_LAENGE).encode("ascii")


def an_dekodieren(roh: bytes) -> str:
    try:
        text = roh.decode("ascii")
    except UnicodeDecodeError:
        raise ValueError("Empfänger im Umschlag ist kein ASCII.") from None
    if len(roh) != AN_LAENGE:
        raise ValueError("Empfänger im Umschlag hat die falsche Länge.")
    kern_text = text.rstrip(" ")
    # Genau die Füllung, die an_kodieren() schreibt - nichts anderes.
    if len(kern_text) == COMMUNITY_ID_LAENGE and ist_community_id(kern_text):
        return kern_text
    if len(kern_text) == ID_ZEICHEN:
        nutzer_id = kern_text[:5] + "-" + kern_text[5:]
        if ist_nutzer_id(nutzer_id):
            return nutzer_id
    raise ValueError("Empfänger im Umschlag hat die falsche Form.")


def _neue_msg_id() -> bytes:
    return os.urandom(MSG_ID_LAENGE)


def _jetzt_ms() -> int:
    return int(time.time() * 1000)


# ---------------------------------------------------------------------------
#  Der Umschlag
# ---------------------------------------------------------------------------

@dataclass
class Umschlag:
    typ: int
    von: str          # "XXXXX-XXXXX"
    an: str           # "XXXXX-XXXXX" oder "G-XXXXXXXX"
    msg_id: bytes     # 16 Byte
    ts_ms: int        # Millisekunden seit 1970 (vom Absender behauptet)
    nonce: bytes      # 12 Byte
    body: bytes

    def kopf(self) -> bytes:
        """Die 64 Kopfbytes - genau das, was als AAD bzw. Kontext dient."""
        if self.typ not in TYPEN:
            raise ValueError(f"Unbekannter Umschlagtyp: {self.typ!r}")
        if not isinstance(self.msg_id, (bytes, bytearray)) or len(self.msg_id) != MSG_ID_LAENGE:
            raise ValueError("Die Nachrichten-ID muss 16 Byte lang sein.")
        if not isinstance(self.nonce, (bytes, bytearray)) or len(self.nonce) != NONCE_LAENGE:
            raise ValueError("Der Nonce muss 12 Byte lang sein.")
        if (not isinstance(self.ts_ms, int) or isinstance(self.ts_ms, bool)
                or not 0 <= self.ts_ms < 2 ** 63):
            raise ValueError("Ungültiger Zeitstempel.")
        return (MARKE + bytes([VERSION, self.typ]) + von_kodieren(self.von)
                + an_kodieren(self.an) + bytes(self.msg_id)
                + self.ts_ms.to_bytes(TS_LAENGE, "big") + bytes(self.nonce))

    def packen(self) -> bytes:
        daten = self.kopf() + bytes(self.body)
        if len(daten) > MAX_UMSCHLAG:
            raise ValueError("Der Umschlag ist zu groß.")
        return daten

    @classmethod
    def entpacken(cls, daten: bytes) -> "Umschlag":
        """Liest einen Umschlag; ValueError bei allem, was nicht passt.

        Hier wird nur der Aufbau geprüft. Ob er echt ist, zeigt sich erst beim
        Entschlüsseln bzw. bei der Unterschrift (dm_lesen, kanal_lesen,
        karten_umschlag_lesen).
        """
        if not isinstance(daten, (bytes, bytearray)):
            raise ValueError("Ein Umschlag besteht aus Bytes.")
        daten = bytes(daten)
        if len(daten) > MAX_UMSCHLAG:
            raise ValueError("Der Umschlag ist zu groß.")
        if len(daten) < KOPF_LAENGE:
            raise ValueError("Zu kurz für einen VP4-Umschlag.")
        if daten[:4] != MARKE:
            raise ValueError("Das ist kein VP4-Umschlag.")
        if daten[4] != VERSION:
            raise ValueError(f"Unbekannte Umschlag-Version: {daten[4]}")
        typ = daten[5]
        if typ not in TYPEN:
            raise ValueError(f"Unbekannter Umschlagtyp: {typ}")
        p = 6
        von = von_dekodieren(daten[p:p + VON_LAENGE]); p += VON_LAENGE
        an = an_dekodieren(daten[p:p + AN_LAENGE]); p += AN_LAENGE
        msg_id = daten[p:p + MSG_ID_LAENGE]; p += MSG_ID_LAENGE
        ts_ms = int.from_bytes(daten[p:p + TS_LAENGE], "big"); p += TS_LAENGE
        if ts_ms >= 2 ** 63:
            raise ValueError("Ungültiger Zeitstempel.")
        nonce = daten[p:p + NONCE_LAENGE]; p += NONCE_LAENGE
        return cls(typ, von, an, msg_id, ts_ms, nonce, daten[p:])


# ---------------------------------------------------------------------------
#  Karten-Umschläge: Anfrage, Annahme, Ablehnung, Kartenupdate
# ---------------------------------------------------------------------------

def anfrage_bauen(identitaet: Identitaet, eigene_karte: dict, an_id: str,
                  typ: int = ANFRAGE) -> Umschlag:
    """Ein unterschriebener (nicht verschlüsselter) Umschlag mit der eigenen Karte.

    Unterschrieben wird die Karte zusammen mit dem ganzen Kopf. Damit lässt
    sich eine echte Anfrage von Max an Anna nicht umadressieren und als
    Anfrage von Max an jemand anderen weiterreichen.
    """
    if typ not in KARTEN_TYPEN:
        raise ValueError("anfrage_bauen() ist nur für Karten-Umschläge.")
    karte = karte_pruefen(eigene_karte)
    if karte["id"] != identitaet.id or karte_schluessel(karte) != (identitaet.ed_pub, identitaet.x_pub):
        raise ValueError("Die Karte gehört nicht zu dieser Identität.")
    u = Umschlag(typ, identitaet.id, an_id, _neue_msg_id(), _jetzt_ms(), NULL_NONCE, b"")
    u.body = e2e.signiert_einpacken(identitaet, kanonisch(karte), kontext=u.kopf())
    return u


def karten_umschlag_lesen(umschlag: Umschlag) -> dict:
    """Prüft einen Karten-Umschlag und gibt die GEPRÜFTE Karte zurück.

    Drei Dinge müssen stimmen: Die Karte selbst ist gültig, der Umschlag ist
    mit dem Schlüssel AUF dieser Karte unterschrieben, und die ID der Karte
    ist der Absender im Kopf. Ob man die Karte annimmt (Pinning), entscheidet
    der Aufrufer.
    """
    if umschlag.typ not in KARTEN_TYPEN:
        raise ValueError("Das ist kein Karten-Umschlag.")
    if umschlag.nonce != NULL_NONCE:
        raise ValueError("Karten-Umschlag mit unerwartetem Nonce.")
    body = umschlag.body
    if len(body) <= SIGNATUR_LAENGE or len(body) > KARTE_MAX_BYTES + SIGNATUR_LAENGE:
        raise ValueError("Karten-Umschlag hat eine unpassende Länge.")
    try:
        karte = json.loads(body[:-SIGNATUR_LAENGE].decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise ValueError("Die Karte im Umschlag ist beschädigt.") from None
    karte = karte_pruefen(karte)
    if karte["id"] != umschlag.von:
        raise ValueError("Die Karte gehört nicht dem Absender des Umschlags.")
    ed, _ = karte_schluessel(karte)
    e2e.signiert_auspacken(body, ed, kontext=umschlag.kopf())
    return karte


# ---------------------------------------------------------------------------
#  Verschlüsselte Umschläge (DMs und Datei-Teile)
# ---------------------------------------------------------------------------

def _json_bytes(inner) -> bytes:
    if not isinstance(inner, dict):
        raise ValueError("Der Inhalt muss ein Objekt (dict) sein.")
    return kanonisch(inner)


def _json_lesen(roh: bytes) -> dict:
    try:
        inner = json.loads(roh.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise ValueError("Der entschlüsselte Inhalt ist kein gültiges JSON.") from None
    if not isinstance(inner, dict):
        raise ValueError("Der entschlüsselte Inhalt ist kein Objekt.")
    return inner


def verschluesselt_bauen(schluessel: bytes, von: str, an: str, klartext: bytes,
                         typ: int = NACHRICHT, msg_id: bytes = None) -> Umschlag:
    """Rohe Bytes verschlüsseln - für DATEI_TEIL, und als Unterbau von dm_bauen()."""
    if typ not in VERSCHLUESSELTE_TYPEN:
        raise ValueError("Nur NACHRICHT und DATEI_TEIL werden verschlüsselt.")
    u = Umschlag(typ, von, an, msg_id or _neue_msg_id(), _jetzt_ms(),
                 os.urandom(NONCE_LAENGE), b"")
    _, u.body = e2e.nachricht_verschluesseln(schluessel, u.msg_id, u.kopf(),
                                             klartext, nonce=u.nonce)
    return u


def verschluesselt_lesen(umschlag: Umschlag, schluessel: bytes) -> bytes:
    if umschlag.typ not in VERSCHLUESSELTE_TYPEN:
        raise ValueError("Dieser Umschlag ist nicht verschlüsselt.")
    return e2e.nachricht_entschluesseln(schluessel, umschlag.msg_id, umschlag.kopf(),
                                        umschlag.nonce, umschlag.body)


def dm_bauen(identitaet: Identitaet, paar_key: bytes, von: str, an: str, inner: dict) -> Umschlag:
    """Eine Direktnachricht: inneres JSON, mit dem Paarschlüssel verschlüsselt.

    Keine zusätzliche Unterschrift nötig: Den Paarschlüssel können nur die
    beiden ausrechnen (statisches X25519 auf beiden Seiten). Was damit
    aufgeht, kommt also vom anderen - oder von einem selbst.
    """
    if identitaet is not None and von != identitaet.id:
        raise ValueError("Absender passt nicht zur eigenen Identität.")
    if not ist_nutzer_id(an):
        raise ValueError("Eine DM geht an eine Nutzer-ID.")
    return verschluesselt_bauen(paar_key, von, an, _json_bytes(inner), NACHRICHT)


def dm_lesen(umschlag: Umschlag, paar_key: bytes) -> dict:
    if umschlag.typ != NACHRICHT:
        raise ValueError("Das ist keine Nachricht.")
    return _json_lesen(verschluesselt_lesen(umschlag, paar_key))


# ---------------------------------------------------------------------------
#  Community-Nachrichten
# ---------------------------------------------------------------------------
#
# In einer Community haben alle denselben Schlüssel. Verschlüsselung allein
# sagt deshalb nur "kommt von einem Mitglied", nicht von welchem. Darum wird
# das innere JSON vorher mit dem Ed25519-Schlüssel des Absenders
# unterschrieben (samt Kopf als Kontext): Ein anderes Mitglied kann weder
# fremde Nachrichten fälschen noch eine echte unter neuer ID oder Zeit noch
# einmal einwerfen.

def kanal_bauen(identitaet: Identitaet, kanal_key: bytes, von: str, ziel: str,
                inner: dict) -> Umschlag:
    """Community-Nachricht: unterschreiben, dann verschlüsseln.

    `inner` muss "kanal" (die Kanal-ID) enthalten. Bei der ersten Nachricht in
    einer Community sollte der Aufrufer "karte" (die eigene Visitenkarte)
    beilegen, damit die anderen die Unterschrift prüfen können.
    """
    if von != identitaet.id:
        raise ValueError("Absender passt nicht zur eigenen Identität.")
    if not ist_community_id(ziel):
        raise ValueError("Ziel einer Kanalnachricht ist eine Community-Kennung.")
    if not isinstance(inner, dict) or not isinstance(inner.get("kanal"), str):
        raise ValueError("Die Kanalnachricht nennt keinen Kanal.")
    u = Umschlag(NACHRICHT, von, ziel, _neue_msg_id(), _jetzt_ms(),
                 os.urandom(NONCE_LAENGE), b"")
    kopf = u.kopf()
    signiert = e2e.signiert_einpacken(identitaet, _json_bytes(inner), kontext=kopf)
    _, u.body = e2e.nachricht_verschluesseln(kanal_key, u.msg_id, kopf, signiert, nonce=u.nonce)
    return u


def _kanal_entschluesseln(umschlag: Umschlag, kanal_key):
    """Gibt (klartext, kanal_id_oder_None) zurück; probiert bei einem dict alle Kanäle."""
    if isinstance(kanal_key, dict):
        for kanal_id, schl in kanal_key.items():
            try:
                return verschluesselt_lesen(umschlag, schl), kanal_id
            except ValueError:
                continue
        raise ValueError("Nachricht passt zu keinem Kanal dieser Community.")
    return verschluesselt_lesen(umschlag, kanal_key), None


def kanal_lesen(umschlag: Umschlag, kanal_key, ed_pub_des_absenders: bytes = None) -> tuple:
    """Entschlüsseln und Unterschrift prüfen. Rückgabe: (inner, karte_oder_None).

    `kanal_key`: ein Kanalschlüssel ODER ein dict Kanal-ID -> Schlüssel. Beim
    dict wird der passende gesucht und geprüft, dass inner["kanal"] genau
    diesen Kanal nennt.

    `ed_pub_des_absenders`: der festgehaltene Schlüssel, wenn man den
    Absender schon kennt. Sonst muss eine gültige Karte des Absenders in
    inner["karte"] liegen; ohne beides: ValueError("unbekannter Absender").
    Liegt eine Karte bei, wird sie geprüft und zurückgegeben - ob sie
    festgehalten wird, entscheidet der Aufrufer.
    """
    if umschlag.typ != NACHRICHT:
        raise ValueError("Das ist keine Nachricht.")
    if not ist_community_id(umschlag.an):
        raise ValueError("Das ist keine Community-Nachricht.")
    klartext, kanal_id = _kanal_entschluesseln(umschlag, kanal_key)
    if len(klartext) < SIGNATUR_LAENGE:
        raise ValueError("Kanalnachricht ohne Unterschrift.")
    inner = _json_lesen(klartext[:-SIGNATUR_LAENGE])

    karte = None
    if "karte" in inner:
        karte = karte_pruefen(inner["karte"])
        if karte["id"] != umschlag.von:
            raise ValueError("Die beigelegte Karte gehört nicht dem Absender.")

    if ed_pub_des_absenders is not None:
        ed = bytes(ed_pub_des_absenders)
        if karte is not None and karte_schluessel(karte)[0] != ed:
            raise ValueError("Die beigelegte Karte passt nicht zum bekannten "
                             "Schlüssel des Absenders.")
    elif karte is not None:
        ed = karte_schluessel(karte)[0]
    else:
        raise ValueError("unbekannter Absender")

    e2e.signiert_auspacken(klartext, ed, kontext=umschlag.kopf())

    if kanal_id is not None and inner.get("kanal") != kanal_id:
        raise ValueError("Die Nachricht nennt einen anderen Kanal als ihren Schlüssel.")
    if not isinstance(inner.get("kanal"), str):
        raise ValueError("Die Kanalnachricht nennt keinen Kanal.")
    return inner, karte
