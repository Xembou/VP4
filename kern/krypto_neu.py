#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
=====================================================================
 krypto_neu.py - die neuen Verfahren aus VP4 5.0
=====================================================================
Vier Verfahren kommen dazu, alle aus fertigen, geprüften Bibliotheken.
Selbst geschrieben ist hier nur das Zusammenstecken: Schlüssel als Text
ein- und auspacken, Kennzeichnung davor, Fehler übersetzen. Gerechnet
wird ausschließlich in den Bibliotheken.

  XChaCha20-Poly1305    PyNaCl (libsodium)
  AES-256-GCM-SIV       cryptography
  Post-Quanten (Hybrid) cryptography, HPKE mit ML-KEM-768 + X25519
  age                   pyrage (die Rust-Umsetzung "rage" des age-Formats)

Jedes Verfahren hat eine Funktion ..._verfuegbar(). Fehlt eine
Bibliothek oder ist sie zu alt, wird das Verfahren in krypto.py einfach
nicht eingetragen - und dann auch nicht in der Oberfläche angezeigt.
Nachgebaut wird es auf keinen Fall: selbst geschriebene Kryptografie
ist genau der Fehler, den man nicht macht.

Fehler: Falscher Schlüssel, verändertes oder abgeschnittenes Material
ergeben IMMER einen ValueError mit einem verständlichen deutschen Text.
Die Bibliotheken melden so etwas sonst mit eigenen Fehlerarten
(InvalidTag ganz ohne Text, CryptoError, DecryptError, OSError), und die
Oberfläche müsste jede davon kennen. Siehe CLAUDE.md: genau so ein
textloser InvalidTag hat schon einmal Chat-Nachrichten verschwinden lassen.

Was diese Verfahren NICHT können: Sie verstecken weder, DASS etwas
verschlüsselt wurde, noch ungefähr wie lang es ist. Und VP4 ist ein
Hobby-Projekt, das niemand unabhängig geprüft hat.
=====================================================================
"""

import base64
import binascii
import functools
import os
import secrets
from pathlib import Path

from cryptography.exceptions import InvalidTag

# Die Bibliotheken werden vorsichtig geladen: Fehlt eine, soll nicht das
# ganze Programm stehen bleiben, sondern nur dieses eine Verfahren fehlen.
try:
    from nacl import bindings as _nacl
    from nacl.exceptions import CryptoError as _NaclFehler
except ImportError:                                     # pragma: no cover
    _nacl = None
    _NaclFehler = None

try:
    from cryptography.hazmat.primitives.ciphers.aead import AESGCMSIV
except ImportError:                                     # pragma: no cover
    AESGCMSIV = None

try:
    from cryptography.hazmat.primitives import hpke as _hpke
    from cryptography.hazmat.primitives.asymmetric import mlkem as _mlkem
    from cryptography.hazmat.primitives.asymmetric import x25519 as _x25519
except ImportError:                                     # pragma: no cover
    _hpke = _mlkem = _x25519 = None

try:
    import pyrage as _pyrage
    from pyrage import passphrase as _age_passwort
    from pyrage import x25519 as _age_x25519
except ImportError:                                     # pragma: no cover
    _pyrage = _age_passwort = _age_x25519 = None


# =============================================================================
#  Hilfen
# =============================================================================

def _b64_lesen(text: str, was: str) -> bytes:
    """Base64 lesen, streng: fremde Zeichen werden nicht still übergangen.

    Leerzeichen und Zeilenumbrüche werden vorher entfernt - beim Kopieren
    aus Chat oder Notizen rutschen die gern mit hinein.
    """
    rein = "".join((text or "").split())
    try:
        return base64.b64decode(rein, validate=True)
    except (binascii.Error, ValueError):
        raise ValueError(f"{was} ist kein gültiger Base64-Text.")


def _b64url_lesen(text: str, was: str) -> bytes:
    """Base64url ohne Auffüllzeichen (so stehen die PQ-Schlüssel da)."""
    rein = "".join((text or "").split())
    rein += "=" * (-len(rein) % 4)
    try:
        return base64.b64decode(rein, altchars=b"-_", validate=True)
    except (binascii.Error, ValueError):
        raise ValueError(f"{was} ist beschädigt (ungültige Zeichen).")


def _b64url(daten: bytes) -> str:
    return base64.urlsafe_b64encode(daten).decode("ascii").rstrip("=")


def _schluessel_32(key_b64: str, name: str) -> bytes:
    """Ein 32-Byte-Schlüssel als Base64 (44 Zeichen) - wie bei AES in krypto.py."""
    if not (key_b64 or "").strip():
        raise ValueError(f"Bitte einen {name}-Schlüssel angeben "
                         "(über 'Schlüssel erzeugen' bekommst du einen).")
    roh = _b64_lesen(key_b64, f"Der {name}-Schlüssel")
    if len(roh) != 32:
        raise ValueError(f"Ein {name}-Schlüssel muss genau 32 Byte lang sein "
                         "(44 Zeichen Base64). Erzeuge am besten einen neuen "
                         "über 'Schlüssel erzeugen'.")
    return roh


def _klartext(daten: bytes) -> str:
    # Wie in krypto.py: lieber ein Ersatzzeichen als gar nichts. Echte
    # Manipulation fängt vorher schon das Siegel (Poly1305 / GCM) ab.
    return daten.decode("utf-8", errors="replace")


# =============================================================================
#  XChaCha20-Poly1305
# =============================================================================
#
# Dasselbe Verfahren wie ChaCha20-Poly1305 in krypto.py, nur mit einem
# 24 statt 12 Byte langen Nonce. Der Unterschied klingt klein, ist aber
# genau der Punkt: Bei 12 Byte zufälligem Nonce wird es nach einigen
# Milliarden Nachrichten mit demselben Schlüssel gefährlich (Geburtstags-
# problem), bei 24 Byte praktisch nie. Man muss sich also keine Gedanken
# machen, wie oft man einen Schlüssel schon benutzt hat.
#
# Aufbau:  Base64( "VP4X1" | Nonce (24) | Geheimtext + Siegel (16) )
# Die Marke geht als zusätzliche Daten (AAD) ins Siegel ein. Sie lässt
# sich also nicht unbemerkt austauschen.

XCHACHA_MARKE = b"VP4X1"
_XCHACHA_NONCE = 24
_SIEGEL = 16


@functools.lru_cache(maxsize=None)
def xchacha_verfuegbar() -> bool:
    """True, wenn PyNaCl da ist und XChaCha20-Poly1305 anbietet."""
    return _nacl is not None and hasattr(
        _nacl, "crypto_aead_xchacha20poly1305_ietf_encrypt")


def xchacha_schluessel_erzeugen() -> str:
    return base64.b64encode(secrets.token_bytes(32)).decode("ascii")


def xchacha_verschluesseln(klartext: str, key_b64: str) -> str:
    schluessel = _schluessel_32(key_b64, "XChaCha20")
    nonce = os.urandom(_XCHACHA_NONCE)
    ct = _nacl.crypto_aead_xchacha20poly1305_ietf_encrypt(
        klartext.encode("utf-8"), XCHACHA_MARKE, nonce, schluessel)
    return base64.b64encode(XCHACHA_MARKE + nonce + ct).decode("ascii")


def xchacha_entschluesseln(geheimtext: str, key_b64: str) -> str:
    schluessel = _schluessel_32(key_b64, "XChaCha20")
    roh = _b64_lesen(geheimtext, "Der Geheimtext")
    if not roh.startswith(XCHACHA_MARKE):
        raise ValueError("Dieser Text wurde nicht mit XChaCha20-Poly1305 erzeugt.")
    rest = roh[len(XCHACHA_MARKE):]
    if len(rest) < _XCHACHA_NONCE + _SIEGEL:
        raise ValueError("Der Geheimtext ist zu kurz oder abgeschnitten.")
    nonce, ct = rest[:_XCHACHA_NONCE], rest[_XCHACHA_NONCE:]
    try:
        klar = _nacl.crypto_aead_xchacha20poly1305_ietf_decrypt(
            ct, XCHACHA_MARKE, nonce, schluessel)
    except (_NaclFehler, ValueError, TypeError):
        raise ValueError("Falscher Schlüssel oder der Text wurde verändert.")
    return _klartext(klar)


# =============================================================================
#  AES-256-GCM-SIV
# =============================================================================
#
# Normales AES-GCM hat eine böse Schwachstelle: Wird ein Nonce mit
# demselben Schlüssel zweimal benutzt - etwa durch einen Programmfehler
# oder einen schlechten Zufallsgenerator -, lässt sich daraus der
# Schlüssel für die Echtheitsprüfung errechnen, und Fälschungen werden
# möglich. GCM-SIV ist dafür gebaut, das zu überstehen: Bei doppeltem
# Nonce verrät es nur, ob zweimal GENAU derselbe Text verschlüsselt wurde.
# Mehr nicht. Ein Freibrief, Nonces absichtlich zu wiederholen, ist das
# trotzdem nicht - VP4 nimmt weiterhin jedes Mal ein zufälliges.
#
# Aufbau:  Base64( "VP4S1" | Nonce (12) | Geheimtext + Siegel (16) )
# Die Marke geht wieder als AAD ins Siegel ein.

GCMSIV_MARKE = b"VP4S1"
_GCMSIV_NONCE = 12


@functools.lru_cache(maxsize=None)
def gcmsiv_verfuegbar() -> bool:
    """True, wenn cryptography AES-GCM-SIV kann.

    Das hängt nicht nur an der Version von cryptography, sondern auch am
    OpenSSL darunter (ab 3.2). Deshalb wird es einmal wirklich ausprobiert.
    """
    if AESGCMSIV is None:
        return False
    try:
        AESGCMSIV(bytes(32)).encrypt(bytes(12), b"", None)
        return True
    except Exception:
        return False


def gcmsiv_schluessel_erzeugen() -> str:
    return base64.b64encode(secrets.token_bytes(32)).decode("ascii")


def gcmsiv_verschluesseln(klartext: str, key_b64: str) -> str:
    schluessel = _schluessel_32(key_b64, "AES-256-GCM-SIV")
    nonce = os.urandom(_GCMSIV_NONCE)
    ct = AESGCMSIV(schluessel).encrypt(nonce, klartext.encode("utf-8"), GCMSIV_MARKE)
    return base64.b64encode(GCMSIV_MARKE + nonce + ct).decode("ascii")


def gcmsiv_entschluesseln(geheimtext: str, key_b64: str) -> str:
    schluessel = _schluessel_32(key_b64, "AES-256-GCM-SIV")
    roh = _b64_lesen(geheimtext, "Der Geheimtext")
    if not roh.startswith(GCMSIV_MARKE):
        raise ValueError("Dieser Text wurde nicht mit AES-256-GCM-SIV erzeugt.")
    rest = roh[len(GCMSIV_MARKE):]
    if len(rest) < _GCMSIV_NONCE + _SIEGEL:
        raise ValueError("Der Geheimtext ist zu kurz oder abgeschnitten.")
    nonce, ct = rest[:_GCMSIV_NONCE], rest[_GCMSIV_NONCE:]
    try:
        klar = AESGCMSIV(schluessel).decrypt(nonce, ct, GCMSIV_MARKE)
    except (InvalidTag, ValueError):
        raise ValueError("Falscher Schlüssel oder der Text wurde verändert.")
    return _klartext(klar)


# =============================================================================
#  Post-Quanten (Hybrid): HPKE mit ML-KEM-768 + X25519
# =============================================================================
#
# Wozu? Ein ausreichend großer Quantencomputer könnte X25519 und RSA
# knacken. Den gibt es heute nicht - aber wer jetzt Geheimtext mitschneidet
# und aufhebt, könnte ihn später entschlüsseln ("erst speichern, später
# knacken"). ML-KEM-768 (NIST-Standard FIPS 203) gilt auch gegen
# Quantencomputer als sicher.
#
# Warum Hybrid? ML-KEM ist jung. Sollte darin doch ein Fehler stecken, soll
# man nicht schlechter dastehen als heute. Deshalb wird es mit dem
# klassischen X25519 kombiniert: Ein Angreifer muss BEIDE brechen. Das
# Ganze ist damit heute mindestens so stark wie X25519 allein.
#
# Gerechnet wird komplett in HPKE (RFC 9180) aus cryptography. HPKE macht
# genau das, was man sonst von Hand zusammenstecken würde: Schlüssel-
# kapselung, Ableitung, AES-256-GCM - geprüft und standardisiert.
#
# Schlüssel als Text:
#   öffentlich:  "VP4PQ1-"  + Base64url( ML-KEM-768 öffentlich (1184)
#                                        | X25519 öffentlich (32) )
#   privat:      "VP4PQS1-" + Base64url( ML-KEM-768 Seed (64)
#                                        | X25519 privat (32) )
# Diese Aufteilung ist VP4-eigen (cryptography bietet für den Hybrid-
# Schlüssel keine eigene Speicherform an). Andere Programme können mit
# diesen Schlüsseltexten also nichts anfangen.
#
# Geheimtext:  "VP4Q1|" + Base64( was suite.encrypt liefert:
#                                 gekapselter Schlüssel (1120) | Geheimtext )
#
# Ehrlich dazu: Der öffentliche Schlüssel ist rund 1600 Zeichen lang, jeder
# Geheimtext mindestens rund 1500. Und es schützt nur, solange der private
# Schlüssel geheim bleibt - Vorwärtssicherheit gibt es hier nicht.

PQ_OEFFENTLICH_PREFIX = "VP4PQ1-"
PQ_PRIVAT_PREFIX = "VP4PQS1-"
PQ_GEHEIMTEXT_PREFIX = "VP4Q1|"
PQ_INFO = b"VP4 PQ v1"

_MLKEM_OEFF = 1184
_MLKEM_SEED = 64
_X25519 = 32


def _pq_suite():
    return _hpke.Suite(_hpke.KEM.MLKEM768_X25519, _hpke.KDF.HKDF_SHA256,
                       _hpke.AEAD.AES_256_GCM)


@functools.lru_cache(maxsize=None)
def pq_verfuegbar() -> bool:
    """True, wenn das installierte cryptography den Hybrid kann (ab 50).

    Es wird einmal wirklich ein Schlüssel erzeugt und etwas verschlüsselt -
    eine Bibliothek kann die Namen kennen und trotzdem an einem zu alten
    OpenSSL scheitern.
    """
    if _hpke is None or _mlkem is None or _x25519 is None:
        return False
    if not (hasattr(_hpke, "MLKEM768X25519PrivateKey")
            and hasattr(_hpke.KEM, "MLKEM768_X25519")):
        return False
    try:
        privat = _hpke.MLKEM768X25519PrivateKey(
            _mlkem.MLKEM768PrivateKey.generate(),
            _x25519.X25519PrivateKey.generate())
        ct = _pq_suite().encrypt(b"probe", privat.public_key(), info=PQ_INFO)
        return _pq_suite().decrypt(ct, privat, info=PQ_INFO) == b"probe"
    except Exception:
        return False


def pq_schluesselpaar() -> tuple:
    """Erzeugt ein Schlüsselpaar. Gibt (privat, öffentlich) als Text zurück.

    Reihenfolge wie bei RSA in krypto.py. Den öffentlichen gibst du weiter,
    den privaten behältst du.
    """
    mlkem_privat = _mlkem.MLKEM768PrivateKey.generate()
    x_privat = _x25519.X25519PrivateKey.generate()
    privat_roh = mlkem_privat.private_bytes_raw() + x_privat.private_bytes_raw()
    oeffentlich_roh = (mlkem_privat.public_key().public_bytes_raw()
                       + x_privat.public_key().public_bytes_raw())
    return (PQ_PRIVAT_PREFIX + _b64url(privat_roh),
            PQ_OEFFENTLICH_PREFIX + _b64url(oeffentlich_roh))


def _pq_oeffentlich_laden(text: str):
    text = (text or "").strip()
    if text.startswith(PQ_PRIVAT_PREFIX):
        raise ValueError("Das ist der PRIVATE Schlüssel. Zum Verschlüsseln "
                         "brauchst du den öffentlichen (beginnt mit VP4PQ1-).")
    if not text.startswith(PQ_OEFFENTLICH_PREFIX):
        raise ValueError("Das ist kein öffentlicher Post-Quanten-Schlüssel "
                         "(er beginnt mit VP4PQ1-).")
    roh = _b64url_lesen(text[len(PQ_OEFFENTLICH_PREFIX):],
                        "Der öffentliche Schlüssel")
    if len(roh) != _MLKEM_OEFF + _X25519:
        raise ValueError("Der öffentliche Schlüssel ist unvollständig oder "
                         "beschädigt (falsche Länge).")
    try:
        return _hpke.MLKEM768X25519PublicKey(
            _mlkem.MLKEM768PublicKey.from_public_bytes(roh[:_MLKEM_OEFF]),
            _x25519.X25519PublicKey.from_public_bytes(roh[_MLKEM_OEFF:]))
    except (ValueError, TypeError):
        raise ValueError("Der öffentliche Schlüssel ist beschädigt.")


def _pq_privat_laden(text: str):
    text = (text or "").strip()
    if text.startswith(PQ_OEFFENTLICH_PREFIX):
        raise ValueError("Das ist der ÖFFENTLICHE Schlüssel. Zum Entschlüsseln "
                         "brauchst du den privaten (beginnt mit VP4PQS1-).")
    if not text.startswith(PQ_PRIVAT_PREFIX):
        raise ValueError("Das ist kein privater Post-Quanten-Schlüssel "
                         "(er beginnt mit VP4PQS1-).")
    roh = _b64url_lesen(text[len(PQ_PRIVAT_PREFIX):], "Der private Schlüssel")
    if len(roh) != _MLKEM_SEED + _X25519:
        raise ValueError("Der private Schlüssel ist unvollständig oder "
                         "beschädigt (falsche Länge).")
    try:
        return _hpke.MLKEM768X25519PrivateKey(
            _mlkem.MLKEM768PrivateKey.from_seed_bytes(roh[:_MLKEM_SEED]),
            _x25519.X25519PrivateKey.from_private_bytes(roh[_MLKEM_SEED:]))
    except (ValueError, TypeError):
        raise ValueError("Der private Schlüssel ist beschädigt.")


def pq_verschluesseln(klartext: str, oeffentlich: str) -> str:
    """Verschlüsselt für den Inhaber des privaten Schlüssels.

    Jeder Aufruf kapselt einen frischen Zufallsschlüssel - zweimal derselbe
    Text ergibt also zwei völlig verschiedene Geheimtexte.
    """
    schluessel = _pq_oeffentlich_laden(oeffentlich)
    ct = _pq_suite().encrypt(klartext.encode("utf-8"), schluessel, info=PQ_INFO)
    return PQ_GEHEIMTEXT_PREFIX + base64.b64encode(ct).decode("ascii")


def pq_entschluesseln(geheimtext: str, privat: str) -> str:
    schluessel = _pq_privat_laden(privat)
    text = (geheimtext or "").strip()
    if not text.startswith(PQ_GEHEIMTEXT_PREFIX):
        raise ValueError("Dieser Text wurde nicht mit dem Post-Quanten-Verfahren "
                         "erzeugt (er beginnt mit VP4Q1|).")
    roh = _b64_lesen(text[len(PQ_GEHEIMTEXT_PREFIX):], "Der Geheimtext")
    if len(roh) < _hpke.KEM.MLKEM768_X25519.enc_length() + _SIEGEL:
        raise ValueError("Der Geheimtext ist zu kurz oder abgeschnitten.")
    try:
        klar = _pq_suite().decrypt(roh, schluessel, info=PQ_INFO)
    except (InvalidTag, ValueError):
        raise ValueError("Falscher Schlüssel oder der Text wurde verändert.")
    return _klartext(klar)


# =============================================================================
#  age
# =============================================================================
#
# age (sprich "a-ge") ist ein einfaches, modernes Format für verschlüsselte
# Dateien von Filippo Valsorda. Der Vorteil gegenüber allem anderen hier:
# Es ist NICHT VP4-eigen. Was VP4 so verschlüsselt, lässt sich mit dem
# offiziellen Programm `age` (und rage, und vielen anderen) wieder öffnen -
# und umgekehrt. Wer VP4 nicht mehr hat, kommt trotzdem an seine Daten.
#
# Zwei Arten:
#   Passwort      - scrypt leitet aus dem Passwort einen Schlüssel ab.
#                   Absichtlich langsam (ein, zwei Sekunden), damit
#                   Durchprobieren teuer wird.
#   Schlüsselpaar - X25519. Öffentlich "age1...", privat
#                   "AGE-SECRET-KEY-1...". Genau die Schlüssel, die auch
#                   `age-keygen` erzeugt.
#
# Text wird "gepanzert" ausgegeben (-----BEGIN AGE ENCRYPTED FILE-----),
# also als reiner Text, der sich kopieren und verschicken lässt. Dateien
# werden binär geschrieben, wie `age` es auch tut.
#
# Ehrlich dazu: age schützt nur mit klassischer Kryptografie (X25519 bzw.
# scrypt), nicht gegen künftige Quantencomputer.

AGE_ANFANG = "-----BEGIN AGE ENCRYPTED FILE-----"
AGE_ENDE = "-----END AGE ENCRYPTED FILE-----"

# Passwort-Dateien gehen bei pyrage nur im Arbeitsspeicher (es gibt keine
# Datei-Funktion dafür). Klartext plus Geheimtext liegen dann gleichzeitig
# im Speicher - darüber wird es auf einem normalen PC eng. Für große
# Dateien ist der .vp4-Container (dateien.py) da, der liest blockweise.
AGE_PASSWORT_DATEI_GRENZE = 512 * 1024 * 1024


@functools.lru_cache(maxsize=None)
def age_verfuegbar() -> bool:
    """True, wenn pyrage installiert ist und die nötigen Teile mitbringt."""
    return (_pyrage is not None
            and all(hasattr(_pyrage, n) for n in
                    ("encrypt", "decrypt", "encrypt_file", "decrypt_file"))
            and hasattr(_age_passwort, "encrypt")
            and hasattr(_age_x25519, "Identity"))


def _age_fehler():
    """Alle Fehlerarten, mit denen pyrage ein Scheitern meldet."""
    return (_pyrage.DecryptError, _pyrage.EncryptError,
            _pyrage.IdentityError, _pyrage.RecipientError)


def _age_speicher_fehler():
    """Wie _age_fehler(), plus OSError - nur für Aufrufe im Arbeitsspeicher.

    Dort fasst pyrage keine Datei an; ein OSError kann also nur ein
    gemeldeter Siegelbruch sein, kein echter Plattenfehler.
    """
    return _age_fehler() + (OSError,)


def _ist_pyrage_entschluesselungsfehler(fehler: BaseException) -> bool:
    """Erkennt den Fall, dass pyrage einen Siegelbruch als OSError meldet.

    Bei Dateien kommt ein verändertes Stück nicht als DecryptError, sondern
    als blanker OSError("decryption error") zurück. Echte Plattenfehler
    tragen dagegen eine genauere Art (FileNotFoundError, PermissionError ...)
    und sollen auch als solche beim Aufrufer ankommen.
    """
    return type(fehler) is OSError


def _age_geheimtext_lesen(text: str) -> bytes:
    """Bereitet gepanzerten Text für pyrage vor.

    Beim Kopieren aus Mails, Chats oder Notizen gehen gern Zeilenenden
    verloren oder werden zu Windows-Zeilenenden. age ist da streng, also
    wird es hier geradegezogen - an den Daten selbst ändert sich nichts.
    """
    text = (text or "").replace("\r\n", "\n").replace("\r", "\n").strip()
    if not text:
        raise ValueError("Bitte einen age-Geheimtext einfügen.")
    if not text.startswith(AGE_ANFANG):
        raise ValueError("Das ist kein age-Text. Er beginnt mit der Zeile "
                         f"{AGE_ANFANG}")
    if not text.endswith(AGE_ENDE):
        raise ValueError("Der age-Text ist abgeschnitten - die Schlusszeile "
                         f"{AGE_ENDE} fehlt.")
    zeilen = [z.strip() for z in text.split("\n")]
    return ("\n".join(zeilen) + "\n").encode("ascii", errors="replace")


def _passwort_pruefen(passwort: str):
    if not passwort:
        raise ValueError("Bitte ein Passwort angeben.")


# ------------------------------------------------------------- Schlüssel

def age_schluesselpaar() -> tuple:
    """Erzeugt ein age-Schlüsselpaar. Gibt (privat, öffentlich) zurück.

    Dieselbe Art Schlüssel, die `age-keygen` erzeugt - beide lassen sich
    mit dem offiziellen Programm benutzen.
    """
    identitaet = _age_x25519.Identity.generate()
    return str(identitaet), str(identitaet.to_public())


def _zeilen_ohne_kommentar(text: str) -> list:
    """Zerlegt in einzelne Schlüssel. Kommentarzeilen (#) fallen weg.

    So lässt sich auch der komplette Inhalt einer Datei von `age-keygen`
    einfügen, der mit "# created: ..." beginnt.
    """
    teile = []
    for zeile in (text or "").replace(",", "\n").splitlines():
        zeile = zeile.strip()
        if zeile and not zeile.startswith("#"):
            teile.extend(zeile.split())
    return teile


def _empfaenger_laden(empfaenger) -> list:
    """Ein oder mehrere öffentliche Schlüssel (age1...) laden.

    Erlaubt ist ein Text (mehrere durch Komma, Leerzeichen oder Zeilen
    getrennt) oder eine Liste. Mehrere Empfänger heißt: jeder von ihnen
    kann den Text mit seinem eigenen privaten Schlüssel öffnen.
    """
    if isinstance(empfaenger, (list, tuple)):
        teile = [t for e in empfaenger for t in _zeilen_ohne_kommentar(e)]
    else:
        teile = _zeilen_ohne_kommentar(empfaenger)
    if not teile:
        raise ValueError("Bitte einen öffentlichen age-Schlüssel angeben "
                         "(beginnt mit age1...).")
    ergebnis = []
    for teil in teile:
        if teil.upper().startswith("AGE-SECRET-KEY-"):
            raise ValueError("Das ist ein PRIVATER age-Schlüssel. Zum "
                             "Verschlüsseln brauchst du den öffentlichen "
                             "(beginnt mit age1...).")
        if not teil.startswith("age1"):
            raise ValueError(f"Kein öffentlicher age-Schlüssel: {teil[:20]}... "
                             "(er beginnt mit age1).")
        try:
            ergebnis.append(_age_x25519.Recipient.from_str(teil))
        except _age_fehler():
            raise ValueError("Der öffentliche age-Schlüssel ist beschädigt "
                             "(Tippfehler oder unvollständig kopiert?).")
    return ergebnis


def _identitaeten_laden(identitaet) -> list:
    if isinstance(identitaet, (list, tuple)):
        teile = [t for e in identitaet for t in _zeilen_ohne_kommentar(e)]
    else:
        teile = _zeilen_ohne_kommentar(identitaet)
    if not teile:
        raise ValueError("Bitte den privaten age-Schlüssel angeben "
                         "(beginnt mit AGE-SECRET-KEY-1...).")
    ergebnis = []
    for teil in teile:
        if teil.startswith("age1"):
            raise ValueError("Das ist der ÖFFENTLICHE age-Schlüssel. Zum "
                             "Entschlüsseln brauchst du den privaten "
                             "(beginnt mit AGE-SECRET-KEY-1...).")
        try:
            ergebnis.append(_age_x25519.Identity.from_str(teil))
        except _age_fehler():
            raise ValueError("Der private age-Schlüssel ist beschädigt "
                             "(Tippfehler oder unvollständig kopiert?).")
    return ergebnis


# ------------------------------------------------------------------ Text

def age_passwort_verschluesseln(klartext: str, passwort: str) -> str:
    _passwort_pruefen(passwort)
    try:
        ct = _age_passwort.encrypt(klartext.encode("utf-8"), passwort, armored=True)
    except _age_fehler() as e:
        raise ValueError(f"age konnte nicht verschlüsseln: {e}")
    # Unter Windows liefert age \r\n - einheitlich \n, sonst stimmt kein Vergleich
    return ct.decode("ascii").replace("\r\n", "\n")


def age_passwort_entschluesseln(geheimtext: str, passwort: str) -> str:
    _passwort_pruefen(passwort)
    roh = _age_geheimtext_lesen(geheimtext)
    try:
        klar = _age_passwort.decrypt(roh, passwort)
    except _age_speicher_fehler():
        raise ValueError("Falsches Passwort, der Text wurde verändert - oder er "
                         "ist für einen Schlüssel statt für ein Passwort "
                         "verschlüsselt (dann 'age (Schlüsselpaar)' nehmen).")
    return _klartext(klar)


def age_verschluesseln(klartext: str, empfaenger) -> str:
    """Verschlüsselt für einen oder mehrere öffentliche age-Schlüssel."""
    ziele = _empfaenger_laden(empfaenger)
    try:
        ct = _pyrage.encrypt(klartext.encode("utf-8"), ziele, armored=True)
    except _age_fehler() as e:
        raise ValueError(f"age konnte nicht verschlüsseln: {e}")
    # Unter Windows liefert age \r\n - einheitlich \n, sonst stimmt kein Vergleich
    return ct.decode("ascii").replace("\r\n", "\n")


def age_entschluesseln(geheimtext: str, identitaet) -> str:
    schluessel = _identitaeten_laden(identitaet)
    roh = _age_geheimtext_lesen(geheimtext)
    try:
        klar = _pyrage.decrypt(roh, schluessel)
    except _age_speicher_fehler():
        raise ValueError("Falscher Schlüssel, der Text wurde verändert - oder er "
                         "ist mit einem Passwort verschlüsselt (dann "
                         "'age (Passwort)' nehmen).")
    return _klartext(klar)


# ---------------------------------------------------------------- Dateien

def _genau_eins(passwort, schluessel, was: str):
    if bool(passwort) == bool(schluessel):
        raise ValueError(f"Bitte entweder ein Passwort oder {was} angeben "
                         "(genau eins von beiden).")


def _quelle_und_ziel(quelle, ziel) -> tuple:
    quelle, ziel = Path(quelle), Path(ziel)
    if not quelle.is_file():
        raise FileNotFoundError(f"Nicht gefunden: {quelle}")
    if quelle.resolve() == ziel.resolve():
        raise ValueError("Quelle und Ziel dürfen nicht dieselbe Datei sein.")
    ziel.parent.mkdir(parents=True, exist_ok=True)
    return quelle, ziel


def _passwort_datei_groesse_pruefen(quelle: Path):
    if quelle.stat().st_size > AGE_PASSWORT_DATEI_GRENZE:
        raise ValueError(
            "Die Datei ist zu groß für age mit Passwort (höchstens 512 MB) - "
            "das geht nur im Arbeitsspeicher. Nimm für große Dateien den "
            ".vp4-Container, oder age mit Schlüsselpaar.")


def _ueber_unfertig(ziel: Path, schreiben):
    """Schreibt erst in "<ziel>.unfertig" und benennt danach um.

    Wie in dateien.py: Bricht etwas mittendrin ab, darf keine halbe Datei
    liegen bleiben. Beim Entschlüsseln wäre das besonders tückisch - pyrage
    schreibt die Blöcke vor dem kaputten schon hinaus, und das Ergebnis
    sähe aus wie eine echte, nur etwas kürzere Datei.
    """
    unfertig = ziel.with_name(ziel.name + ".unfertig")
    try:
        schreiben(unfertig)
        unfertig.replace(ziel)
    except BaseException:
        unfertig.unlink(missing_ok=True)
        raise
    return ziel


def age_datei_verschluesseln(quelle, ziel, passwort=None, empfaenger=None) -> Path:
    """Verschlüsselt eine Datei ins age-Format (binär, wie `age` es schreibt).

    Mit Schlüsselpaar wird gestreamt, die Größe ist egal. Mit Passwort geht
    es bei pyrage nur im Arbeitsspeicher - deshalb die Grenze von 512 MB.
    """
    _genau_eins(passwort, empfaenger, "einen öffentlichen Schlüssel")
    quelle, ziel = _quelle_und_ziel(quelle, ziel)

    if passwort:
        _passwort_datei_groesse_pruefen(quelle)

        def schreiben(pfad):
            daten = quelle.read_bytes()
            try:
                ct = _age_passwort.encrypt(daten, passwort)
            except _age_fehler() as e:
                raise ValueError(f"age konnte nicht verschlüsseln: {e}")
            pfad.write_bytes(ct)
    else:
        ziele = _empfaenger_laden(empfaenger)

        def schreiben(pfad):
            try:
                _pyrage.encrypt_file(os.fspath(quelle), os.fspath(pfad), ziele)
            except _age_fehler() as e:
                raise ValueError(f"age konnte nicht verschlüsseln: {e}")

    return _ueber_unfertig(ziel, schreiben)


def age_datei_entschluesseln(quelle, ziel, passwort=None, identitaet=None) -> Path:
    """Entschlüsselt eine .age-Datei nach `ziel` (binär oder gepanzert).

    Eine bestehende Datei unter `ziel` wird ersetzt - den Namen sucht der
    Aufrufer aus. Schlägt die Prüfung fehl, bleibt nichts liegen.
    """
    _genau_eins(passwort, identitaet, "einen privaten Schlüssel")
    quelle, ziel = _quelle_und_ziel(quelle, ziel)

    if passwort:
        # Der Geheimtext ist nur wenig größer als der Klartext - dieselbe
        # Grenze, plus etwas Luft für Kopf und Siegel.
        if quelle.stat().st_size > AGE_PASSWORT_DATEI_GRENZE + 1024 * 1024:
            raise ValueError(
                "Die Datei ist zu groß für age mit Passwort (höchstens 512 MB) "
                "- das geht nur im Arbeitsspeicher.")

        def schreiben(pfad):
            daten = quelle.read_bytes()
            try:
                klar = _age_passwort.decrypt(daten, passwort)
            except _age_speicher_fehler():
                raise ValueError("Falsches Passwort, die Datei wurde verändert - "
                                 "oder sie ist für einen Schlüssel statt für "
                                 "ein Passwort verschlüsselt.")
            pfad.write_bytes(klar)
    else:
        schluessel = _identitaeten_laden(identitaet)

        def schreiben(pfad):
            try:
                _pyrage.decrypt_file(os.fspath(quelle), os.fspath(pfad), schluessel)
            except _age_fehler():
                raise ValueError("Falscher Schlüssel, die Datei wurde verändert - "
                                 "oder sie ist mit einem Passwort verschlüsselt.")
            except OSError as e:
                if _ist_pyrage_entschluesselungsfehler(e):
                    raise ValueError("Die Datei wurde verändert oder ist "
                                     "abgeschnitten - entschlüsselt wurde nichts.")
                raise

    return _ueber_unfertig(ziel, schreiben)
