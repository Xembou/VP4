# -*- coding: utf-8 -*-
"""kern/communities.py - Communities mit Kanälen: Kennungen, Einladung, Manifest.

Nur Rechnung und Datenprüfung; gespeichert und verschickt wird woanders.

Kennungen
---------
* Community: "G-" + 8 Crockford-Zeichen (40 zufällige Bit), z. B.
  "G-7K2M9QXA". Dieselbe Form wie die Gruppen aus 4.x, nur mit Crockford-
  statt Standard-Base32. Im Umschlag steht sie im Feld `an` (12 Zeichen,
  mit zwei Leerzeichen aufgefüllt). Das "G-" kann bei einer Nutzer-ID nie
  vorkommen (dort gibt es an zweiter Stelle keinen Bindestrich), deshalb
  sieht der Empfänger sofort, ob eine Zeile einer Person oder einer
  Community gilt.
* Kanal: 8 Crockford-Zeichen.

Warum die Kanal-ID NICHT im Umschlagkopf steht
---------------------------------------------
Im offenen Kopf ist nur Platz für die Community (12 Zeichen). Die Kanal-ID
steckt als Feld "kanal" im verschlüsselten, unterschriebenen Inhalt. Das
verrät Discord nicht, in welchem Kanal geschrieben wird - kostet aber, dass
der Empfänger den passenden Kanalschlüssel ausprobieren muss (ein
fehlgeschlagener GCM-Versuch kostet Mikrosekunden, eine Community hat
wenige Kanäle). kern/umschlag.kanal_lesen() erledigt das und prüft danach,
dass der Inhalt wirklich den Kanal nennt, dessen Schlüssel gepasst hat.

Einladung
---------
"VP4G2-" + Base64url(Community-Kennung 5 Byte roh | Schlüssel 32 | Besitzer-
ID 10 ASCII ohne Bindestrich) = 6 + 63 Zeichen, eine Zeile. Wer den Code
hat, ist dabei; eine Mitgliederliste gibt es bewusst nicht. Ein Code lässt
sich nicht zurückholen - wer jemanden loswerden will, erzeugt einen neuen
Community-Schlüssel und verteilt ihn über die DM-Paarschlüssel.

Manifest
--------
Name, Symbol, Kanäle und Admins einer Community, unterschrieben vom
Besitzer oder einem Admin. Gültig ist ein neues Manifest nur, wenn
1. es vom Besitzer oder einem Admin des BISHERIGEN Manifests stammt
   (sonst könnte sich jemand in seinem eigenen Manifest selbst zum Admin
   machen),
2. seine Versionsnummer echt größer ist (sonst ließe sich ein altes,
   gültig unterschriebenes Manifest erneut einspielen),
3. nur der Besitzer die Admin-Liste ändert - ein Admin darf Kanäle
   bearbeiten, aber weder den Besitzer entmachten noch weitere Admins
   ernennen.
"""

import os
import time

from kern.identitaet import (ID_ZEICHEN, SIGNATUR_LAENGE, Identitaet,
                             b64, b64_lesen, b64url, b64url_lesen,
                             crockford_dekodieren, crockford_kodieren,
                             ist_crockford, ist_nutzer_id, kanonisch,
                             karte_pruefen, karte_schluessel, name_pruefen,
                             signatur_pruefen)

COMMUNITY_PRAEFIX = "G-"
COMMUNITY_ZEICHEN = 8          # 40 Bit
COMMUNITY_ID_LAENGE = len(COMMUNITY_PRAEFIX) + COMMUNITY_ZEICHEN   # 10
KANAL_ZEICHEN = 8
COMMUNITY_SCHLUESSEL_LAENGE = 32

EINLADUNG_MARKE = "VP4G2-"
_EINLADUNG_ROH = 5 + COMMUNITY_SCHLUESSEL_LAENGE + ID_ZEICHEN      # 47 Byte

MANIFEST_VERSION = 1
MANIFEST_SIGNATUR_MARKE = b"VP4 manifest v1\n"
MANIFEST_FELDER = frozenset({"v", "community_id", "name", "icon", "kanaele",
                             "admins", "version", "von", "ts", "art"})
# Eine "gruppe" ist technisch eine Community mit genau einem Kanal und ohne
# Kanal-Oberfläche - so muss es nur EINEN Mechanismus geben. Damit der
# Beitretende weiss, was er vor sich hat, steht die Art im Manifest.
MANIFEST_ARTEN = ("community", "gruppe")
KANAL_FELDER = frozenset({"id", "name", "position", "nur_admins"})
MANIFEST_MAX_BYTES = 32 * 1024
MAX_KANAELE = 100
MAX_ADMINS = 50
ICON_MAX = 32


# ---------------------------------------------------------------------------
#  Kennungen
# ---------------------------------------------------------------------------

def community_id_neu() -> str:
    return COMMUNITY_PRAEFIX + crockford_kodieren(os.urandom(5), COMMUNITY_ZEICHEN)


def ist_community_id(text) -> bool:
    return (isinstance(text, str) and text.startswith(COMMUNITY_PRAEFIX)
            and ist_crockford(text[len(COMMUNITY_PRAEFIX):], COMMUNITY_ZEICHEN))


def kanal_id_neu() -> str:
    return crockford_kodieren(os.urandom(5), KANAL_ZEICHEN)


def ist_kanal_id(text) -> bool:
    return ist_crockford(text, KANAL_ZEICHEN)


def standard_kanal_id(community_id: str) -> str:
    """Die ID des ersten Kanals ("allgemein"), aus der Community-ID berechnet.

    Wer mit einem Code beitritt, hat noch kein Manifest - und ohne Kanal-ID
    keinen Kanalschlüssel. Weil der erste Kanal aus der Community-ID folgt,
    kann der Neue sofort dort mitlesen und "Hallo" sagen; das Manifest mit
    allen anderen Kanälen schickt ihm dann irgendein Mitglied.
    """
    import hashlib
    roh = hashlib.sha256(b"VP4 standardkanal v1|" + community_id.encode("ascii")).digest()
    return crockford_kodieren(roh, KANAL_ZEICHEN)


def community_schluessel_neu() -> bytes:
    return os.urandom(COMMUNITY_SCHLUESSEL_LAENGE)


# ---------------------------------------------------------------------------
#  Einladungscode
# ---------------------------------------------------------------------------

def einladung_bauen(community_id: str, schluessel: bytes, besitzer_id: str) -> str:
    """Der Code, den man Freunden schickt. Wer ihn hat, ist Mitglied."""
    if not ist_community_id(community_id):
        raise ValueError("Ungültige Community-Kennung.")
    if not isinstance(schluessel, (bytes, bytearray)) or len(schluessel) != COMMUNITY_SCHLUESSEL_LAENGE:
        raise ValueError("Der Community-Schlüssel muss 32 Byte lang sein.")
    if not ist_nutzer_id(besitzer_id):
        raise ValueError("Ungültige Besitzer-ID.")
    roh = (crockford_dekodieren(community_id[len(COMMUNITY_PRAEFIX):], 5)
           + bytes(schluessel) + besitzer_id.replace("-", "").encode("ascii"))
    return EINLADUNG_MARKE + b64url(roh)


def einladung_lesen(code: str) -> tuple:
    """Gegenstück. Rückgabe: (community_id, schluessel, besitzer_id)."""
    if not isinstance(code, str):
        raise ValueError("Der Einladungscode muss ein Text sein.")
    code = "".join(code.split())
    if not code.startswith(EINLADUNG_MARKE):
        raise ValueError("Das ist kein VP4-Einladungscode (er beginnt mit VP4G2-).")
    roh = b64url_lesen(code[len(EINLADUNG_MARKE):])
    if len(roh) != _EINLADUNG_ROH:
        raise ValueError("Der Einladungscode ist unvollständig oder zu lang.")
    community_id = COMMUNITY_PRAEFIX + crockford_kodieren(roh[:5], COMMUNITY_ZEICHEN)
    schluessel = roh[5:37]
    try:
        besitzer = roh[37:].decode("ascii")
    except UnicodeDecodeError:
        raise ValueError("Der Einladungscode ist beschädigt.") from None
    besitzer_id = besitzer[:5] + "-" + besitzer[5:]
    if not ist_nutzer_id(besitzer_id):
        raise ValueError("Der Einladungscode ist beschädigt (Besitzer-ID).")
    return community_id, schluessel, besitzer_id


# ---------------------------------------------------------------------------
#  Manifest
# ---------------------------------------------------------------------------

def _ganzzahl(wert) -> bool:
    return isinstance(wert, int) and not isinstance(wert, bool)


def _kanaele_pruefen(kanaele) -> list:
    if not isinstance(kanaele, list) or not kanaele:
        raise ValueError("Eine Community braucht mindestens einen Kanal.")
    if len(kanaele) > MAX_KANAELE:
        raise ValueError(f"Höchstens {MAX_KANAELE} Kanäle.")
    gesehen = set()
    sauber = []
    for k in kanaele:
        if not isinstance(k, dict) or set(k) != KANAL_FELDER:
            raise ValueError("Ein Kanal hat nicht die erwarteten Felder.")
        if not ist_kanal_id(k["id"]) or k["id"] in gesehen:
            raise ValueError("Ungültige oder doppelte Kanal-ID.")
        gesehen.add(k["id"])
        name_pruefen(k["name"], "Kanalname")
        if not _ganzzahl(k["position"]) or not isinstance(k["nur_admins"], bool):
            raise ValueError("Ungültige Kanal-Einstellungen.")
        sauber.append({"id": k["id"], "name": k["name"],
                       "position": k["position"], "nur_admins": k["nur_admins"]})
    return sauber


def _admins_pruefen(admins) -> list:
    if not isinstance(admins, list) or len(admins) > MAX_ADMINS:
        raise ValueError("Ungültige Admin-Liste.")
    if not all(ist_nutzer_id(a) for a in admins) or len(set(admins)) != len(admins):
        raise ValueError("Ungültige oder doppelte Admin-ID.")
    return list(admins)


def _icon_pruefen(icon) -> str:
    if not isinstance(icon, str) or not icon or len(icon) > ICON_MAX:
        raise ValueError("Ungültiges Community-Symbol.")
    return icon


def _manifest_bytes(felder: dict) -> bytes:
    return MANIFEST_SIGNATUR_MARKE + kanonisch(felder)


def manifest_bauen(identitaet: Identitaet, community_id: str, name: str, icon: str,
                   kanaele: list, admins: list, version: int, ts: int = None,
                   art: str = "community") -> dict:
    """Ein unterschriebenes Manifest. "von" ist die ID dessen, der unterschreibt."""
    if not ist_community_id(community_id):
        raise ValueError("Ungültige Community-Kennung.")
    if not _ganzzahl(version) or version < 1:
        raise ValueError("Die Manifest-Version muss eine Zahl ab 1 sein.")
    felder = {
        "v": MANIFEST_VERSION,
        "community_id": community_id,
        "name": name_pruefen(name, "Community-Name"),
        "icon": _icon_pruefen(icon),
        "kanaele": _kanaele_pruefen(kanaele),
        "admins": _admins_pruefen(admins),
        "version": version,
        "von": identitaet.id,
        "ts": int(time.time()) if ts is None else int(ts),
        "art": art,
    }
    if art not in MANIFEST_ARTEN:
        raise ValueError("Unbekannte Art der Community.")
    manifest = dict(felder, sig=b64(identitaet.signieren(_manifest_bytes(felder))))
    if len(kanonisch(manifest)) > MANIFEST_MAX_BYTES:
        raise ValueError("Das Manifest wäre zu groß.")
    return manifest


def manifest_pruefen(manifest, bekannte_karten: dict, besitzer_id: str,
                     aktuelle_version: int, aktuelle_admins=(),
                     community_id: str = None) -> dict:
    """Prüft ein empfangenes Manifest gegen den bisherigen Stand.

    `bekannte_karten`: ID -> Visitenkarte (die festgehaltenen Karten).
    `besitzer_id`: aus dem Einladungscode - der einzige feste Anker.
    `aktuelle_version` / `aktuelle_admins`: aus dem zuletzt angenommenen
    Manifest (beim allerersten: 0 und leer - dann darf nur der Besitzer).
    `community_id`: wenn angegeben, muss das Manifest genau dieser Community
    gehören; sonst könnte ein Admin zweier Communities eines in die andere
    einspielen.

    Gibt eine saubere Kopie zurück; ValueError, wenn etwas nicht stimmt.
    """
    if not isinstance(manifest, dict):
        raise ValueError("Das Manifest ist kein Objekt.")
    try:
        groesse = len(kanonisch(manifest))
    except (TypeError, ValueError):
        raise ValueError("Das Manifest enthält unerlaubte Werte.") from None
    if groesse > MANIFEST_MAX_BYTES:
        raise ValueError("Das Manifest ist zu groß.")
    felder = {k: v for k, v in manifest.items() if k != "sig"}
    if set(felder) != MANIFEST_FELDER or "sig" not in manifest:
        raise ValueError("Das Manifest hat nicht die erwarteten Felder.")
    if felder["art"] not in MANIFEST_ARTEN:
        raise ValueError("Unbekannte Art der Community im Manifest.")
    if felder["art"] == "gruppe" and len(felder["kanaele"]) != 1:
        raise ValueError("Eine Gruppe hat genau einen Kanal.")
    if not _ganzzahl(felder["v"]) or felder["v"] != MANIFEST_VERSION:
        raise ValueError("Unbekannte Manifest-Version.")
    if not ist_community_id(felder["community_id"]):
        raise ValueError("Ungültige Community-Kennung im Manifest.")
    if community_id is not None and felder["community_id"] != community_id:
        raise ValueError("Das Manifest gehört zu einer anderen Community.")
    name_pruefen(felder["name"], "Community-Name")
    _icon_pruefen(felder["icon"])
    _kanaele_pruefen(felder["kanaele"])
    neue_admins = _admins_pruefen(felder["admins"])
    if not _ganzzahl(felder["version"]) or not _ganzzahl(felder["ts"]):
        raise ValueError("Ungültige Version oder Zeit im Manifest.")
    if felder["version"] <= aktuelle_version:
        raise ValueError("Das Manifest ist nicht neuer als das bisherige "
                         f"(Version {felder['version']} <= {aktuelle_version}).")

    von = felder["von"]
    if von != besitzer_id and von not in set(aktuelle_admins):
        raise ValueError("Das Manifest ist weder vom Besitzer noch von einem Admin.")
    if von != besitzer_id and set(neue_admins) != set(aktuelle_admins):
        raise ValueError("Nur der Besitzer darf die Admin-Liste ändern.")

    karte = bekannte_karten.get(von) if isinstance(bekannte_karten, dict) else None
    if karte is None:
        raise ValueError("Die Visitenkarte des Unterzeichners ist unbekannt.")
    karte = karte_pruefen(karte)
    if karte["id"] != von:
        raise ValueError("Die Visitenkarte gehört nicht dem Unterzeichner.")
    ed, _ = karte_schluessel(karte)
    signatur_pruefen(ed, b64_lesen(manifest["sig"], SIGNATUR_LAENGE), _manifest_bytes(felder))
    return dict(felder, sig=manifest["sig"])
