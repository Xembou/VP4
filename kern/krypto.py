#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
=====================================================================
 krypto.py - alle Ver- und Entschlüsselungsverfahren von VP4
=====================================================================
Hier steckt die eigentliche Rechenarbeit. Dieses Modul weiß nichts von
Fenstern, Knöpfen oder Netzwerk - es bekommt Text und Schlüssel und
gibt Text zurück. Genau deshalb lässt es sich gut testen.

Aufgeteilt in vier Gruppen:

  ClassicCiphers  - klassische Verfahren zum Spielen und Ausprobieren
                    (Caesar, Vigenère, XOR, Base64, ROT13, Atbash,
                    Morse, Rail-Fence, Playfair)
  ModernCrypto    - echte, sichere Verfahren (AES-256-GCM, ChaCha20,
                    RSA-2048, Passwort-Verschlüsselung)
  Signaturen      - Ed25519: beweist, dass etwas wirklich von dir kommt
  Pruefsummen     - SHA-256 & Co.: prüft, ob etwas unverändert ist

WICHTIG - was ist wirklich sicher?
-----------------------------------
Die Verfahren in ClassicCiphers sind NICHT sicher. Sie sind hunderte
Jahre alt und mit einem Computer in Sekunden zu knacken. Sie sind zum
Ausprobieren und Herumspielen da - nicht, um etwas Wichtiges zu
schützen. Das steht auch in der App an jedem dieser Verfahren.

Sicher sind: AES-256-GCM, ChaCha20-Poly1305, die
Passwort-Verschlüsselung und RSA-2048.
=====================================================================
"""

import base64
import hashlib
import os
import secrets
import struct

from cryptography.hazmat.primitives.ciphers.aead import AESGCM, ChaCha20Poly1305
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa, ed25519, padding as asym_padding
from cryptography.exceptions import InvalidSignature
from argon2.low_level import Type as Argon2Typ, hash_secret_raw as argon2_hash_raw


# =============================================================================
#  Klassische Chiffren
# =============================================================================

class ClassicCiphers:
    """Alte Verfahren zum Ausprobieren. Nicht sicher - siehe Kopfkommentar.

    Alle Verfahren hier arbeiten mit dem 26-Buchstaben-Alphabet A-Z.
    Umlaute (ä/ö/ü/ß) und Sonderzeichen haben darin keinen Platz und
    bleiben deshalb unverändert stehen, statt kaputtgerechnet zu werden.
    """

    # ---------------------------------------------------------------- Caesar

    @staticmethod
    def caesar(text: str, shift: int) -> str:
        shift %= 26
        out = []
        for ch in text:
            if "a" <= ch <= "z":
                out.append(chr((ord(ch) - 97 + shift) % 26 + 97))
            elif "A" <= ch <= "Z":
                out.append(chr((ord(ch) - 65 + shift) % 26 + 65))
            else:
                out.append(ch)
        return "".join(out)

    @staticmethod
    def caesar_encrypt(text: str, key: str) -> str:
        return ClassicCiphers.caesar(text, ClassicCiphers._caesar_key_to_int(key))

    @staticmethod
    def caesar_decrypt(text: str, key: str) -> str:
        return ClassicCiphers.caesar(text, -ClassicCiphers._caesar_key_to_int(key))

    @staticmethod
    def _caesar_key_to_int(key: str) -> int:
        key = (key or "").strip()
        if not key:
            raise ValueError("Bitte eine Verschiebung (Zahl, z.B. 3) angeben.")
        try:
            return int(key)
        except ValueError:
            raise ValueError("Der Caesar-Schlüssel muss eine ganze Zahl sein (z.B. 3).")

    # -------------------------------------------------------------- Vigenère

    @staticmethod
    def vigenere(text: str, key: str, decrypt: bool) -> str:
        # Nur A-Z/a-z. Umlaute (ä/ö/ü/ß) und andere Sonderzeichen haben im
        # 26-Buchstaben-Alphabet keinen Platz und bleiben deshalb - genau wie
        # bei Caesar - unverändert stehen. Wichtig: ch.isalpha() wäre hier
        # falsch, denn Python zählt auch "ä" als Buchstabe. Die Rechnung
        # darunter ist aber reines ASCII, wodurch Umlaute unwiederbringlich
        # in den a-z-Bereich gequetscht würden (aus "äöüß" wurde "btzw").
        key = "".join(ch for ch in (key or "") if "a" <= ch <= "z" or "A" <= ch <= "Z")
        if not key:
            raise ValueError("Bitte ein Schlüsselwort angeben (nur die Buchstaben A-Z).")
        key_upper = key.upper()
        out = []
        ki = 0
        for ch in text:
            if "a" <= ch <= "z" or "A" <= ch <= "Z":
                shift = ord(key_upper[ki % len(key_upper)]) - 65
                if decrypt:
                    shift = -shift
                base = 97 if ch.islower() else 65
                out.append(chr((ord(ch) - base + shift) % 26 + base))
                ki += 1
            else:
                out.append(ch)
        return "".join(out)

    @staticmethod
    def vigenere_encrypt(text: str, key: str) -> str:
        return ClassicCiphers.vigenere(text, key, decrypt=False)

    @staticmethod
    def vigenere_decrypt(text: str, key: str) -> str:
        return ClassicCiphers.vigenere(text, key, decrypt=True)

    # ------------------------------------------------------------------- XOR

    @staticmethod
    def xor_encrypt(text: str, key: str) -> str:
        if not key:
            raise ValueError("Bitte einen Schlüssel-Text angeben.")
        data = text.encode("utf-8")
        kb = key.encode("utf-8")
        result = bytes(b ^ kb[i % len(kb)] for i, b in enumerate(data))
        return base64.b64encode(result).decode("ascii")

    @staticmethod
    def xor_decrypt(text: str, key: str) -> str:
        if not key:
            raise ValueError("Bitte einen Schlüssel-Text angeben.")
        try:
            data = base64.b64decode(text)
        except Exception:
            raise ValueError("Das ist kein gültiger XOR-Geheimtext (erwartet Base64).")
        kb = key.encode("utf-8")
        result = bytes(b ^ kb[i % len(kb)] for i, b in enumerate(data))
        try:
            return result.decode("utf-8")
        except UnicodeDecodeError:
            raise ValueError("Falscher Schlüssel oder beschädigter Text.")

    # ---------------------------------------------------------------- Base64

    @staticmethod
    def base64_encode(text: str, key: str = "") -> str:
        return base64.b64encode(text.encode("utf-8")).decode("ascii")

    @staticmethod
    def base64_decode(text: str, key: str = "") -> str:
        try:
            return base64.b64decode(text).decode("utf-8")
        except Exception:
            raise ValueError("Das ist kein gültiger Base64-Text.")

    # ----------------------------------------------------------------- ROT13

    @staticmethod
    def rot13(text: str, key: str = "") -> str:
        """Caesar mit fester Verschiebung 13.

        Der Witz an ROT13: 13 ist die Hälfte von 26, deshalb ist Ver- und
        Entschlüsseln dieselbe Rechnung. Zweimal angewendet kommt wieder
        der Ausgangstext heraus.
        """
        return ClassicCiphers.caesar(text, 13)

    # ---------------------------------------------------------------- Atbash

    @staticmethod
    def atbash(text: str, key: str = "") -> str:
        """Spiegelt das Alphabet: A wird zu Z, B zu Y, C zu X und so weiter.

        Eines der ältesten bekannten Verfahren überhaupt, ursprünglich für
        das hebräische Alphabet. Wie ROT13 ist es sein eigenes Gegenstück.
        """
        out = []
        for ch in text:
            if "a" <= ch <= "z":
                out.append(chr(122 - (ord(ch) - 97)))
            elif "A" <= ch <= "Z":
                out.append(chr(90 - (ord(ch) - 65)))
            else:
                out.append(ch)
        return "".join(out)

    # ----------------------------------------------------------------- Morse

    MORSE = {
        "A": ".-", "B": "-...", "C": "-.-.", "D": "-..", "E": ".", "F": "..-.",
        "G": "--.", "H": "....", "I": "..", "J": ".---", "K": "-.-", "L": ".-..",
        "M": "--", "N": "-.", "O": "---", "P": ".--.", "Q": "--.-", "R": ".-.",
        "S": "...", "T": "-", "U": "..-", "V": "...-", "W": ".--", "X": "-..-",
        "Y": "-.--", "Z": "--..",
        "0": "-----", "1": ".----", "2": "..---", "3": "...--", "4": "....-",
        "5": ".....", "6": "-....", "7": "--...", "8": "---..", "9": "----.",
        # Im deutschen Morsealphabet gibt es eigene Zeichen für die Umlaute.
        "Ä": ".-.-", "Ö": "---.", "Ü": "..--", "ß": "...--..",
        ".": ".-.-.-", ",": "--..--", "?": "..--..", "!": "-.-.--",
        "-": "-....-", "/": "-..-.", ":": "---...", "'": ".----.",
        "@": ".--.-.", "(": "-.--.", ")": "-.--.-", "=": "-...-", "+": ".-.-.",
    }
    MORSE_UMGEKEHRT = {code: zeichen for zeichen, code in MORSE.items()}

    @staticmethod
    def morse_encode(text: str, key: str = "") -> str:
        """Wandelt Text in Morsezeichen. Buchstaben mit Leerzeichen getrennt,
        Wörter mit einem Schrägstrich."""
        woerter = []
        for wort in text.upper().split():
            zeichen = []
            for ch in wort:
                if ch in ClassicCiphers.MORSE:
                    zeichen.append(ClassicCiphers.MORSE[ch])
                # Zeichen ohne Morse-Entsprechung werden weggelassen -
                # das lässt sich nicht vermeiden, Morse kennt sie schlicht nicht.
            if zeichen:
                woerter.append(" ".join(zeichen))
        if not woerter:
            raise ValueError("Der Text enthält kein einziges Zeichen, das sich morsen lässt.")
        return " / ".join(woerter)

    @staticmethod
    def morse_decode(text: str, key: str = "") -> str:
        """Wandelt Morsezeichen zurück in Text."""
        text = text.strip()
        if not text:
            raise ValueError("Bitte Morsezeichen eingeben (z.B. '.... .- .-.. .-.. ---').")
        erlaubt = set(".-/ \t\n")
        if not set(text) <= erlaubt:
            raise ValueError("Das sind keine Morsezeichen - erlaubt sind nur "
                             ". (Punkt), - (Strich), / (Worttrenner) und Leerzeichen.")
        woerter = []
        for wort in text.split("/"):
            buchstaben = []
            for code in wort.split():
                if code not in ClassicCiphers.MORSE_UMGEKEHRT:
                    raise ValueError(f"'{code}' ist kein gültiges Morsezeichen.")
                buchstaben.append(ClassicCiphers.MORSE_UMGEKEHRT[code])
            if buchstaben:
                woerter.append("".join(buchstaben))
        return " ".join(woerter)

    # ------------------------------------------------------------ Rail-Fence

    @staticmethod
    def railfence_encrypt(text: str, key: str) -> str:
        """Schreibt den Text im Zickzack über mehrere Zeilen und liest ihn
        dann zeilenweise ab. Der Schlüssel ist die Anzahl der Zeilen."""
        zeilen = ClassicCiphers._railfence_key(key, len(text))
        if zeilen == 1 or not text:
            return text
        muster = ClassicCiphers._railfence_muster(len(text), zeilen)
        # Zeichen nach Zeile sortiert einsammeln
        return "".join(text[i] for i in sorted(range(len(text)), key=lambda i: (muster[i], i)))

    @staticmethod
    def railfence_decrypt(text: str, key: str) -> str:
        zeilen = ClassicCiphers._railfence_key(key, len(text))
        if zeilen == 1 or not text:
            return text
        muster = ClassicCiphers._railfence_muster(len(text), zeilen)
        # Die Reihenfolge, in der die Zeichen beim Verschlüsseln abgelesen
        # wurden - dieselbe Reihenfolge rückwärts angewendet stellt den
        # Ausgangstext wieder her.
        reihenfolge = sorted(range(len(text)), key=lambda i: (muster[i], i))
        out = [""] * len(text)
        for zeichen, ziel in zip(text, reihenfolge):
            out[ziel] = zeichen
        return "".join(out)

    @staticmethod
    def _railfence_key(key: str, textlaenge: int) -> int:
        key = (key or "").strip()
        if not key:
            raise ValueError("Bitte die Anzahl der Zeilen angeben (Zahl, z.B. 3).")
        try:
            zeilen = int(key)
        except ValueError:
            raise ValueError("Der Schlüssel muss eine ganze Zahl sein (Anzahl der Zeilen, z.B. 3).")
        if zeilen < 1:
            raise ValueError("Die Anzahl der Zeilen muss mindestens 1 sein.")
        return zeilen

    @staticmethod
    def _railfence_muster(laenge: int, zeilen: int) -> list:
        """Gibt für jede Textposition an, in welcher Zeile sie landet."""
        muster = []
        zeile, richtung = 0, 1
        for _ in range(laenge):
            muster.append(zeile)
            if zeile == 0:
                richtung = 1
            elif zeile == zeilen - 1:
                richtung = -1
            zeile += richtung
        return muster

    # -------------------------------------------------------------- Playfair

    @staticmethod
    def _playfair_tabelle(key: str) -> list:
        """Baut die 5x5-Buchstabentabelle aus dem Schlüsselwort.

        In 25 Felder passen keine 26 Buchstaben - deshalb teilen sich I und J
        traditionell ein Feld. Beim Entschlüsseln kommt darum immer "I" heraus,
        auch wenn ursprünglich ein "J" dastand.
        """
        gesehen = []
        for ch in (key or "").upper():
            if "A" <= ch <= "Z":
                if ch == "J":
                    ch = "I"
                if ch not in gesehen:
                    gesehen.append(ch)
        for ch in "ABCDEFGHIKLMNOPQRSTUVWXYZ":     # ohne J
            if ch not in gesehen:
                gesehen.append(ch)
        return gesehen

    @staticmethod
    def _playfair(text: str, key: str, decrypt: bool) -> str:
        tabelle = ClassicCiphers._playfair_tabelle(key)
        if not any("A" <= ch <= "Z" for ch in (key or "").upper()):
            raise ValueError("Bitte ein Schlüsselwort angeben (nur die Buchstaben A-Z).")
        pos = {ch: (i // 5, i % 5) for i, ch in enumerate(tabelle)}

        # Nur Buchstaben, J wird zu I. Alles andere fällt weg - Playfair
        # arbeitet ausschließlich mit Buchstabenpaaren.
        rein = [("I" if ch == "J" else ch) for ch in text.upper() if "A" <= ch <= "Z"]
        if not rein:
            raise ValueError("Der Text enthält keine Buchstaben A-Z.")

        if not decrypt:
            # Paare bilden. Zwei gleiche Buchstaben in einem Paar sind nicht
            # erlaubt - dazwischen kommt ein X. Am Ende ggf. mit X auffüllen.
            paare = []
            i = 0
            while i < len(rein):
                a = rein[i]
                b = rein[i + 1] if i + 1 < len(rein) else "X"
                if a == b:
                    b = "X"
                    i += 1
                else:
                    i += 2
                paare.append((a, b))
        else:
            if len(rein) % 2 != 0:
                raise ValueError("Ein Playfair-Geheimtext muss eine gerade Anzahl Buchstaben haben.")
            paare = [(rein[i], rein[i + 1]) for i in range(0, len(rein), 2)]

        richtung = -1 if decrypt else 1
        out = []
        for a, b in paare:
            za, sa = pos[a]
            zb, sb = pos[b]
            if za == zb:                                  # gleiche Zeile
                out.append(tabelle[za * 5 + (sa + richtung) % 5])
                out.append(tabelle[zb * 5 + (sb + richtung) % 5])
            elif sa == sb:                                # gleiche Spalte
                out.append(tabelle[((za + richtung) % 5) * 5 + sa])
                out.append(tabelle[((zb + richtung) % 5) * 5 + sb])
            else:                                         # Rechteck: Spalten tauschen
                out.append(tabelle[za * 5 + sb])
                out.append(tabelle[zb * 5 + sa])
        return "".join(out)

    @staticmethod
    def playfair_encrypt(text: str, key: str) -> str:
        return ClassicCiphers._playfair(text, key, decrypt=False)

    @staticmethod
    def playfair_decrypt(text: str, key: str) -> str:
        return ClassicCiphers._playfair(text, key, decrypt=True)


# =============================================================================
#  Moderne, sichere Verfahren
# =============================================================================

# ---------------------------------------------------------------------------
#  Schlüsselableitung: wie aus einem Passwort ein Schlüssel wird
# ---------------------------------------------------------------------------
#
# Ein Passwort wie "hallo123" taugt nicht als Schlüssel - zu kurz, zu
# vorhersehbar. Eine Ableitungsfunktion rechnet es absichtlich langsam durch
# und macht Durchprobieren dadurch teuer.
#
# WICHTIG, und der Grund für den ganzen Aufbau hier: Die Einstellungen dieser
# Rechnung stehen MIT IN DER DATEI. Früher standen sie nur im Programm. Wer
# sie erhöht hätte - und irgendwann erhöht man sie, weil Rechner schneller
# werden -, hätte damit jeden bestehenden Schlüsselspeicher unlesbar gemacht,
# ohne dass irgendetwas gewarnt hätte. Steht die Einstellung in der Datei,
# lässt sich jede alte Datei weiterhin öffnen, egal was das Programm heute
# für richtig hält.

KDF_PBKDF2 = 1        # PBKDF2 mit SHA-256 - die erste Fassung
KDF_ARGON2ID = 2      # Argon2id - was heute aus Passwörtern gemacht wird
KDF_HKDF = 3          # HKDF-SHA256 - wenn schon ein echter Schlüssel da ist

# Wozu KDF_HKDF? Wenn der Schlüssel aus dem Schlüsselspeicher kommt, ist er
# bereits zufällig - ihn künstlich langsam durchzurechnen brächte nichts.
# Trotzdem soll nicht jede Datei denselben Schlüssel benutzen: HKDF mischt
# das Salt der Datei unter, und damit bekommt jede Datei ihren eigenen.
# Ohne das müsste man beim Nonce sehr genau aufpassen, dass sich über
# tausend Dateien hinweg nie eines wiederholt.

# Der alte Wert. 600.000 Runden waren die OWASP-Empfehlung für PBKDF2 mit
# SHA-256. Wird nur noch zum Lesen alter Dateien gebraucht.
PBKDF2_RUNDEN = 600_000

# Argon2id ist gegenüber PBKDF2 im Vorteil, weil es nicht nur Rechenzeit,
# sondern auch Arbeitsspeicher braucht. Genau daran scheitern Grafikkarten,
# die sonst Tausende Passwörter gleichzeitig durchprobieren könnten.
# 64 MiB liegen deutlich über der OWASP-Untergrenze von 19 MiB und bleiben
# auf einem normalen Rechner trotzdem unter einer Sekunde.
ARGON2_STANDARD = {
    "kdf": KDF_ARGON2ID,
    "zeit": 3,                # Durchgänge
    "speicher_kib": 64 * 1024,
    "parallel": 1,
}


# Was in einem Dateikopf höchstens stehen darf. Der Kopf ist zwar per AAD
# mitversiegelt - aber geprüft wird das Siegel erst NACH dem Ableiten. Ohne
# diese Grenzen rechnet das Programm also zuerst mit dem, was ein
# Angreifer hineingeschrieben hat: ein einziges gekipptes Bit machte aus
# 64 MiB Argon2-Speicher 16 GiB, und statt "falsches Passwort" kam ein
# Speicherfehler (oder der Rechner war minutenlang beschäftigt).
KDF_GRENZEN = {
    "zeit": (1, 20),
    "speicher_kib": (8 * 1024, 1024 * 1024),     # 8 MiB bis 1 GiB
    "parallel": (1, 16),
    "runden": (1_000, 10_000_000),
}


class ModernCrypto:
    """Echte Verschlüsselung. Alles hier gilt als sicher."""

    @staticmethod
    def _kdf_grenzen_pruefen(kdf: dict):
        """Lehnt Ableitungs-Einstellungen ab, die kein VP4 je schreiben würde."""
        for feld, (unten, oben) in KDF_GRENZEN.items():
            if feld in kdf and not unten <= kdf[feld] <= oben:
                raise ValueError(
                    "Die Einstellungen im Dateikopf sind unmöglich - die Daten "
                    "sind beschädigt oder wurden verändert.")

    # ----------------------------------------------- Schlüssel aus Passwort

    @staticmethod
    def derive_key(password: str, salt: bytes, runden: int = PBKDF2_RUNDEN) -> bytes:
        """Der alte Weg: PBKDF2 mit SHA-256.

        Bleibt erhalten, weil Dateien aus der ersten Fassung damit
        verschlüsselt sind. Für Neues wird schluessel_ableiten() benutzt.
        """
        kdf = PBKDF2HMAC(algorithm=hashes.SHA256(), length=32,
                         salt=salt, iterations=runden)
        return kdf.derive(password.encode("utf-8"))

    @staticmethod
    def schluessel_ableiten(password, salt: bytes, kdf: dict) -> bytes:
        """Macht einen 32 Byte langen Schlüssel.

        Welches Verfahren mit welchen Einstellungen benutzt wird, steht im
        übergebenen kdf-Wörterbuch - und das stammt aus dem Kopf der Datei,
        nicht aus dem Programm. Das Salt sorgt dafür, dass gleiche Passwörter
        trotzdem verschiedene Schlüssel ergeben.

        Bei KDF_PBKDF2 und KDF_ARGON2ID ist "password" ein Text, bei
        KDF_HKDF ein bereits fertiger Schlüssel als bytes.
        """
        if not isinstance(salt, (bytes, bytearray)) or len(salt) != 16:
            raise ValueError("Das Salt muss 16 Byte lang sein.")

        art = kdf.get("kdf")
        if art == KDF_HKDF:
            if not isinstance(password, (bytes, bytearray)):
                raise ValueError("HKDF braucht einen fertigen Schlüssel, "
                                 "kein Passwort.")
            return HKDF(algorithm=hashes.SHA256(), length=32, salt=bytes(salt),
                        info=b"VP4 Datei v1").derive(bytes(password))
        if isinstance(password, (bytes, bytearray)):
            raise ValueError("Dieses Verfahren erwartet ein Passwort als Text.")
        if art == KDF_PBKDF2:
            return ModernCrypto.derive_key(password, bytes(salt), kdf["runden"])
        if art == KDF_ARGON2ID:
            return argon2_hash_raw(
                secret=password.encode("utf-8"),
                salt=bytes(salt),
                time_cost=kdf["zeit"],
                memory_cost=kdf["speicher_kib"],
                parallelism=kdf["parallel"],
                hash_len=32,
                type=Argon2Typ.ID,
            )
        raise ValueError(f"Unbekanntes Ableitungsverfahren: {art}")

    # ------------------------------------------------------------ Dateikopf
    #
    # Aufbau:
    #     Marke          5 Byte    z.B. b"VP4P2"
    #     KDF-Kennung    1 Byte    1 = PBKDF2, 2 = Argon2id
    #     Einstellungen            PBKDF2:   Runden                    (4 Byte)
    #                              Argon2id: Zeit, Speicher, Parallel  (9 Byte)
    #     Salt          16 Byte
    #
    # Der fertige Kopf wird beim Verschlüsseln als "zusätzliche Daten" (AAD)
    # an AES-GCM übergeben. Dadurch ist er mitversiegelt: Wer die Parameter
    # verdreht - etwa die Speichergrösse heruntersetzt, um die Ableitung zu
    # schwächen -, bekommt beim Entschlüsseln einen Fehler statt still einen
    # falschen Schlüssel.

    @staticmethod
    def kopf_bauen(marke: bytes, salt: bytes, kdf: dict = None) -> bytes:
        kdf = kdf or ARGON2_STANDARD
        art = kdf.get("kdf")
        if art == KDF_PBKDF2:
            einstellungen = struct.pack("!I", kdf["runden"])
        elif art == KDF_ARGON2ID:
            einstellungen = struct.pack("!IIB", kdf["zeit"], kdf["speicher_kib"],
                                        kdf["parallel"])
        elif art == KDF_HKDF:
            einstellungen = b""
        else:
            raise ValueError(f"Unbekanntes Ableitungsverfahren: {art}")
        return marke + bytes([art]) + einstellungen + salt

    @staticmethod
    def kopf_lesen(roh: bytes, marke: bytes) -> tuple:
        """Zerlegt den Kopf. Gibt (Kopf-Bytes, kdf, Salt) zurück."""
        if not roh.startswith(marke):
            raise ValueError("Die Daten tragen nicht die erwartete Kennzeichnung.")
        if len(roh) < len(marke) + 1:
            raise ValueError("Die Daten sind abgeschnitten oder beschädigt.")

        art = roh[len(marke)]
        rest = roh[len(marke) + 1:]
        if art == KDF_PBKDF2:
            laenge = 4
        elif art == KDF_ARGON2ID:
            laenge = 9
        elif art == KDF_HKDF:
            laenge = 0
        else:
            raise ValueError(
                f"Unbekanntes Ableitungsverfahren ({art}). Vermutlich stammen "
                "die Daten aus einer neueren Fassung des Programms.")

        if len(rest) < laenge + 16:
            raise ValueError("Die Daten sind abgeschnitten oder beschädigt.")

        werte, salt = rest[:laenge], rest[laenge:laenge + 16]
        if art == KDF_PBKDF2:
            kdf = {"kdf": art, "runden": struct.unpack("!I", werte)[0]}
        elif art == KDF_HKDF:
            kdf = {"kdf": art}
        else:
            zeit, speicher, parallel = struct.unpack("!IIB", werte)
            kdf = {"kdf": art, "zeit": zeit, "speicher_kib": speicher,
                   "parallel": parallel}
        ModernCrypto._kdf_grenzen_pruefen(kdf)

        kopf = roh[:len(marke) + 1 + laenge + 16]
        return kopf, kdf, salt

    # ------------------------------------------------------------ AES-256

    @staticmethod
    def generate_aes_key() -> str:
        return base64.b64encode(secrets.token_bytes(32)).decode("ascii")

    @staticmethod
    def _aes_key_bytes(key_b64: str) -> bytes:
        try:
            raw = base64.b64decode((key_b64 or "").strip())
        except Exception:
            raise ValueError("Der AES-Schlüssel ist kein gültiger Base64-Text.")
        if len(raw) not in (16, 24, 32):
            raise ValueError("Ein AES-Schlüssel muss 16, 24 oder 32 Byte lang sein "
                             "(erzeuge am besten einen neuen über 'Schlüssel erzeugen').")
        return raw

    @staticmethod
    def aes_encrypt(plaintext: str, key_b64: str) -> str:
        key = ModernCrypto._aes_key_bytes(key_b64)
        nonce = os.urandom(12)
        ct = AESGCM(key).encrypt(nonce, plaintext.encode("utf-8"), None)
        return base64.b64encode(nonce + ct).decode("ascii")

    @staticmethod
    def aes_decrypt(ciphertext_b64: str, key_b64: str) -> str:
        key = ModernCrypto._aes_key_bytes(key_b64)
        try:
            raw = base64.b64decode((ciphertext_b64 or "").strip())
        except Exception:
            raise ValueError("Das ist kein gültiger AES-Geheimtext.")
        if len(raw) < 13:
            raise ValueError("Der Geheimtext ist zu kurz oder beschädigt.")
        try:
            pt = AESGCM(key).decrypt(raw[:12], raw[12:], None)
        except Exception:
            raise ValueError("Falscher Schlüssel oder der Text wurde verändert.")
        return pt.decode("utf-8", errors="replace")

    @staticmethod
    def aes_encrypt_bytes(data: bytes, key: bytes) -> bytes:
        nonce = os.urandom(12)
        return nonce + AESGCM(key).encrypt(nonce, data, None)

    @staticmethod
    def aes_decrypt_bytes(data: bytes, key: bytes) -> bytes:
        if len(data) < 13:
            raise ValueError("Daten zu kurz oder beschädigt.")
        return AESGCM(key).decrypt(data[:12], data[12:], None)

    # ------------------------------------------------- ChaCha20-Poly1305

    @staticmethod
    def generate_chacha_key() -> str:
        return base64.b64encode(ChaCha20Poly1305.generate_key()).decode("ascii")

    @staticmethod
    def chacha_encrypt(plaintext: str, key_b64: str) -> str:
        try:
            key = base64.b64decode((key_b64 or "").strip())
        except Exception:
            raise ValueError("Der ChaCha20-Schlüssel ist kein gültiger Base64-Text.")
        if len(key) != 32:
            raise ValueError("Ein ChaCha20-Schlüssel muss genau 32 Byte lang sein.")
        nonce = os.urandom(12)
        ct = ChaCha20Poly1305(key).encrypt(nonce, plaintext.encode("utf-8"), None)
        return base64.b64encode(nonce + ct).decode("ascii")

    @staticmethod
    def chacha_decrypt(ciphertext_b64: str, key_b64: str) -> str:
        try:
            key = base64.b64decode((key_b64 or "").strip())
        except Exception:
            raise ValueError("Der ChaCha20-Schlüssel ist kein gültiger Base64-Text.")
        if len(key) != 32:
            raise ValueError("Ein ChaCha20-Schlüssel muss genau 32 Byte lang sein.")
        try:
            raw = base64.b64decode((ciphertext_b64 or "").strip())
        except Exception:
            raise ValueError("Das ist kein gültiger ChaCha20-Geheimtext.")
        if len(raw) < 13:
            raise ValueError("Der Geheimtext ist zu kurz oder beschädigt.")
        try:
            pt = ChaCha20Poly1305(key).decrypt(raw[:12], raw[12:], None)
        except Exception:
            raise ValueError("Falscher Schlüssel oder der Text wurde verändert.")
        return pt.decode("utf-8", errors="replace")

    # --------------------------------------------- Passwort-Verschlüsselung

    # Kennzeichnung am Anfang, damit klar ist, womit der Text erzeugt wurde.
    PASSWORT_MARKE = b"VP4P2"
    PASSWORT_MARKE_ALT = b"VP4P1"      # erste Fassung, wird nur noch gelesen

    @staticmethod
    def password_encrypt(plaintext: str, password: str) -> str:
        """Verschlüsselt mit einem Passwort statt mit einem Schlüsseltext.

        Praktisch, wenn man jemandem etwas schicken will, ohne vorher einen
        44 Zeichen langen Schlüssel austauschen zu müssen - ein am Telefon
        genanntes Passwort reicht.

        Aufbau des Ergebnisses:
          Kopf (Marke + Ableitungs-Einstellungen + Salt) + Nonce (12)
          + verschlüsselter Text

        Der Kopf ist mitversiegelt, siehe kopf_bauen().
        """
        if not password:
            raise ValueError("Bitte ein Passwort angeben.")
        salt = os.urandom(16)
        kopf = ModernCrypto.kopf_bauen(ModernCrypto.PASSWORT_MARKE, salt)
        key = ModernCrypto.schluessel_ableiten(password, salt, ARGON2_STANDARD)
        nonce = os.urandom(12)
        ct = AESGCM(key).encrypt(nonce, plaintext.encode("utf-8"), kopf)
        return base64.b64encode(kopf + nonce + ct).decode("ascii")

    @staticmethod
    def password_decrypt(ciphertext_b64: str, password: str) -> str:
        if not password:
            raise ValueError("Bitte das Passwort angeben.")
        try:
            raw = base64.b64decode((ciphertext_b64 or "").strip())
        except Exception:
            raise ValueError("Das ist kein gültiger Geheimtext.")

        if raw.startswith(ModernCrypto.PASSWORT_MARKE_ALT):
            return ModernCrypto._password_decrypt_alt(raw, password)

        if not raw.startswith(ModernCrypto.PASSWORT_MARKE):
            raise ValueError("Dieser Text wurde nicht mit der Passwort-Verschlüsselung erzeugt.")

        kopf, kdf, salt = ModernCrypto.kopf_lesen(raw, ModernCrypto.PASSWORT_MARKE)
        rest = raw[len(kopf):]
        if len(rest) < 12 + 1:
            raise ValueError("Der Geheimtext ist zu kurz oder beschädigt.")
        nonce, ct = rest[:12], rest[12:]
        key = ModernCrypto.schluessel_ableiten(password, salt, kdf)
        try:
            pt = AESGCM(key).decrypt(nonce, ct, kopf)
        except Exception:
            raise ValueError("Falsches Passwort oder der Text wurde verändert.")
        return pt.decode("utf-8", errors="replace")

    @staticmethod
    def _password_decrypt_alt(raw: bytes, password: str) -> str:
        """Liest Geheimtext der ersten Fassung ("VP4P1").

        Dort standen die Einstellungen nicht in den Daten: es war immer
        PBKDF2 mit 600.000 Runden, und der Kopf war nicht mitversiegelt.
        """
        marke = ModernCrypto.PASSWORT_MARKE_ALT
        if len(raw) < len(marke) + 16 + 12 + 1:
            raise ValueError("Der Geheimtext ist zu kurz oder beschädigt.")
        rest = raw[len(marke):]
        salt, nonce, ct = rest[:16], rest[16:28], rest[28:]
        key = ModernCrypto.derive_key(password, salt, PBKDF2_RUNDEN)
        try:
            pt = AESGCM(key).decrypt(nonce, ct, None)
        except Exception:
            raise ValueError("Falsches Passwort oder der Text wurde verändert.")
        return pt.decode("utf-8", errors="replace")

    # -------------------------------------------------------------- RSA-2048

    @staticmethod
    def generate_rsa_keypair():
        """Erzeugt ein Schlüsselpaar. Gibt (privat, öffentlich) zurück.

        Den öffentlichen Schlüssel darf jeder haben - damit kann man dir
        etwas verschlüsseln. Den privaten musst du für dich behalten - nur
        damit kommt man wieder an den Text.
        """
        key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        priv = key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=serialization.NoEncryption(),
        ).decode("ascii")
        pub = key.public_key().public_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PublicFormat.SubjectPublicKeyInfo,
        ).decode("ascii")
        return priv, pub

    @staticmethod
    def rsa_encrypt(plaintext: str, pub_pem: str) -> str:
        try:
            pub = serialization.load_pem_public_key((pub_pem or "").encode("utf-8"))
        except Exception:
            raise ValueError("Ungültiger öffentlicher RSA-Schlüssel.")
        data = plaintext.encode("utf-8")
        # RSA kann nur kleine Datenmengen direkt verschlüsseln - bei 2048 Bit
        # und OAEP/SHA-256 sind das höchstens 190 Byte.
        if len(data) > 190:
            raise ValueError(
                f"Der Text ist zu lang für RSA ({len(data)} von höchstens 190 Zeichen). "
                "Für längere Texte eignet sich AES-256 oder die Passwort-Verschlüsselung.")
        try:
            ct = pub.encrypt(data, asym_padding.OAEP(
                mgf=asym_padding.MGF1(algorithm=hashes.SHA256()),
                algorithm=hashes.SHA256(), label=None))
        except Exception:
            raise ValueError("Verschlüsseln mit RSA fehlgeschlagen.")
        return base64.b64encode(ct).decode("ascii")

    @staticmethod
    def rsa_decrypt(ciphertext_b64: str, priv_pem: str) -> str:
        try:
            priv = serialization.load_pem_private_key(
                (priv_pem or "").encode("utf-8"), password=None)
        except Exception:
            raise ValueError("Ungültiger RSA Private Key.")
        try:
            raw = base64.b64decode((ciphertext_b64 or "").strip())
        except Exception:
            raise ValueError("Das ist kein gültiger RSA-Geheimtext.")
        try:
            pt = priv.decrypt(raw, asym_padding.OAEP(
                mgf=asym_padding.MGF1(algorithm=hashes.SHA256()),
                algorithm=hashes.SHA256(), label=None))
        except Exception:
            raise ValueError("Falscher Schlüssel oder der Text wurde verändert.")
        return pt.decode("utf-8", errors="replace")


# =============================================================================
#  Signaturen (Ed25519)
# =============================================================================

class Signaturen:
    """Beweist, dass eine Nachricht wirklich von dir kommt.

    Das ist etwas anderes als Verschlüsseln: der Text bleibt lesbar. Aber
    wer deinen öffentlichen Schlüssel hat, kann prüfen, dass der Text
    wirklich von dir stammt und unterwegs nicht verändert wurde.
    """

    @staticmethod
    def generate_keypair():
        """Gibt (privat, öffentlich) als Base64-Text zurück."""
        priv = ed25519.Ed25519PrivateKey.generate()
        priv_raw = priv.private_bytes(
            encoding=serialization.Encoding.Raw,
            format=serialization.PrivateFormat.Raw,
            encryption_algorithm=serialization.NoEncryption())
        pub_raw = priv.public_key().public_bytes(
            encoding=serialization.Encoding.Raw,
            format=serialization.PublicFormat.Raw)
        return (base64.b64encode(priv_raw).decode("ascii"),
                base64.b64encode(pub_raw).decode("ascii"))

    @staticmethod
    def sign(text: str, priv_b64: str) -> str:
        try:
            raw = base64.b64decode((priv_b64 or "").strip())
            priv = ed25519.Ed25519PrivateKey.from_private_bytes(raw)
        except Exception:
            raise ValueError("Ungültiger privater Signaturschlüssel.")
        return base64.b64encode(priv.sign(text.encode("utf-8"))).decode("ascii")

    @staticmethod
    def verify(text: str, signatur_b64: str, pub_b64: str) -> bool:
        """True, wenn die Signatur zum Text und zum Schlüssel passt."""
        try:
            raw = base64.b64decode((pub_b64 or "").strip())
            pub = ed25519.Ed25519PublicKey.from_public_bytes(raw)
        except Exception:
            raise ValueError("Ungültiger öffentlicher Signaturschlüssel.")
        try:
            sig = base64.b64decode((signatur_b64 or "").strip())
        except Exception:
            raise ValueError("Die Signatur ist kein gültiger Base64-Text.")
        try:
            pub.verify(sig, text.encode("utf-8"))
            return True
        except InvalidSignature:
            return False


# =============================================================================
#  Prüfsummen
# =============================================================================

class Pruefsummen:
    """Erzeugt einen kurzen Fingerabdruck eines Textes.

    Damit lässt sich prüfen, ob zwei Texte exakt gleich sind - schon ein
    einziges geändertes Zeichen ergibt einen völlig anderen Fingerabdruck.
    Zurückrechnen kann man daraus nichts, das ist keine Verschlüsselung.
    """

    VERFAHREN = {
        "SHA-256": hashlib.sha256,
        "SHA-512": hashlib.sha512,
        "SHA-1": hashlib.sha1,
        "MD5": hashlib.md5,
    }

    @staticmethod
    def berechne(text: str, verfahren: str = "SHA-256") -> str:
        fn = Pruefsummen.VERFAHREN.get(verfahren)
        if fn is None:
            raise ValueError(f"Unbekanntes Prüfsummen-Verfahren: {verfahren}")
        return fn(text.encode("utf-8")).hexdigest()

    @staticmethod
    def berechne_datei(pfad, verfahren: str = "SHA-256") -> str:
        fn = Pruefsummen.VERFAHREN.get(verfahren)
        if fn is None:
            raise ValueError(f"Unbekanntes Prüfsummen-Verfahren: {verfahren}")
        h = fn()
        with open(pfad, "rb") as f:
            for block in iter(lambda: f.read(65536), b""):
                h.update(block)
        return h.hexdigest()


# =============================================================================
#  Übersicht aller Verfahren - davon lebt die Auswahlliste in der Oberfläche
# =============================================================================

# "sicher": echte Kryptografie, für Wichtiges geeignet
# "spiel":  klassische Verfahren, in Sekunden zu knacken
# "key":    was für ein Schlüssel gebraucht wird - steuert den Hinweistext
#           und ob der "Schlüssel erzeugen"-Knopf etwas tut

VERFAHREN = {
    "AES-256-GCM": {
        "enc": ModernCrypto.aes_encrypt, "dec": ModernCrypto.aes_decrypt,
        "art": "sicher", "key": "aes",
        "hinweis": "Der Standard für sichere Verschlüsselung. Schlüssel als Base64 "
                   "(44 Zeichen) - über 'Schlüssel erzeugen' bekommst du einen.",
    },
    "ChaCha20-Poly1305": {
        "enc": ModernCrypto.chacha_encrypt, "dec": ModernCrypto.chacha_decrypt,
        "art": "sicher", "key": "chacha",
        "hinweis": "Gleichwertig zu AES-256 und auf Geräten ohne AES-Beschleunigung "
                   "schneller. Wird u.a. in TLS und von Google verwendet.",
    },
    "Passwort (AES-256)": {
        "enc": ModernCrypto.password_encrypt, "dec": ModernCrypto.password_decrypt,
        "art": "sicher", "key": "passwort",
        "hinweis": "Du gibst einfach ein Passwort ein statt einen langen Schlüssel. "
                   "Praktisch, um jemandem etwas zu schicken - Passwort genügt.",
    },
    "RSA-2048": {
        "enc": ModernCrypto.rsa_encrypt, "dec": ModernCrypto.rsa_decrypt,
        "art": "sicher", "key": "rsa",
        "hinweis": "Zwei Schlüssel: mit dem öffentlichen wird verschlüsselt, mit dem "
                   "privaten entschlüsselt. Nur für kurze Texte (max. 190 Zeichen).",
    },
    "Caesar": {
        "enc": ClassicCiphers.caesar_encrypt, "dec": ClassicCiphers.caesar_decrypt,
        "art": "spiel", "key": "zahl",
        "hinweis": "Jeder Buchstabe wird um eine feste Anzahl verschoben. "
                   "Schlüssel: eine Zahl, z.B. 3. Nur zum Spielen - 26 Möglichkeiten.",
    },
    "Vigenère": {
        "enc": ClassicCiphers.vigenere_encrypt, "dec": ClassicCiphers.vigenere_decrypt,
        "art": "spiel", "key": "wort",
        "hinweis": "Caesar mit wechselnder Verschiebung durch ein Schlüsselwort. "
                   "Schlüssel: ein Wort aus A-Z. Galt 300 Jahre als unknackbar.",
    },
    "Playfair": {
        "enc": ClassicCiphers.playfair_encrypt, "dec": ClassicCiphers.playfair_decrypt,
        "art": "spiel", "key": "wort",
        "hinweis": "Verschlüsselt Buchstabenpaare über eine 5x5-Tabelle. Gut zu wissen: "
                   "I und J teilen sich ein Feld (aus J wird ein I), zwischen doppelte "
                   "Buchstaben kommt ein X, und Leer- und Satzzeichen fallen weg.",
    },
    "Rail-Fence": {
        "enc": ClassicCiphers.railfence_encrypt, "dec": ClassicCiphers.railfence_decrypt,
        "art": "spiel", "key": "zahl",
        "hinweis": "Schreibt den Text im Zickzack über mehrere Zeilen und liest ihn "
                   "zeilenweise ab. Schlüssel: Anzahl der Zeilen, z.B. 3.",
    },
    "ROT13": {
        "enc": ClassicCiphers.rot13, "dec": ClassicCiphers.rot13,
        "art": "spiel", "key": "keiner",
        "hinweis": "Caesar mit fester Verschiebung 13. Braucht keinen Schlüssel - "
                   "zweimal angewendet kommt der Ausgangstext wieder heraus.",
    },
    "Atbash": {
        "enc": ClassicCiphers.atbash, "dec": ClassicCiphers.atbash,
        "art": "spiel", "key": "keiner",
        "hinweis": "Spiegelt das Alphabet: A wird zu Z, B zu Y. Uralt und ohne "
                   "Schlüssel - wie ROT13 sein eigenes Gegenstück.",
    },
    "Morse": {
        "enc": ClassicCiphers.morse_encode, "dec": ClassicCiphers.morse_decode,
        "art": "spiel", "key": "keiner",
        "hinweis": "Keine Verschlüsselung, sondern eine andere Schreibweise. "
                   "Buchstaben mit Leerzeichen, Wörter mit / getrennt.",
    },
    "XOR": {
        "enc": ClassicCiphers.xor_encrypt, "dec": ClassicCiphers.xor_decrypt,
        "art": "spiel", "key": "wort",
        "hinweis": "Verrechnet den Text Byte für Byte mit dem Schlüssel. Nur sicher, "
                   "wenn der Schlüssel so lang ist wie der Text und nie wiederverwendet wird.",
    },
    "Base64": {
        "enc": ClassicCiphers.base64_encode, "dec": ClassicCiphers.base64_decode,
        "art": "spiel", "key": "keiner",
        "hinweis": "Gar keine Verschlüsselung, nur eine andere Schreibweise. "
                   "Jeder kann das ohne Schlüssel zurückwandeln.",
    },
}

# Welcher Hinweis zum Schlüsselfeld gehört, und ob sich ein Schlüssel
# automatisch erzeugen lässt.
SCHLUESSEL_ARTEN = {
    "aes":      ("Schlüssel (Base64, 44 Zeichen)", True),
    "chacha":   ("Schlüssel (Base64, 44 Zeichen)", True),
    "passwort": ("Passwort", False),
    "rsa":      ("Zum Verschlüsseln: öffentlicher Schlüssel / zum Entschlüsseln: privater", True),
    "zahl":     ("Schlüssel (eine Zahl, z.B. 3)", False),
    "wort":     ("Schlüsselwort", False),
    "keiner":   ("Für dieses Verfahren wird kein Schlüssel gebraucht", False),
}


# =============================================================================
#  Neue Verfahren aus VP4 5.0 (kern/krypto_neu.py)
# =============================================================================
#
# Erst hier unten und vorsichtig geladen: Fehlt PyNaCl oder pyrage, oder ist
# cryptography zu alt für den Post-Quanten-Hybrid, soll krypto.py trotzdem
# funktionieren. Das betroffene Verfahren steht dann einfach nicht in der
# Liste - nachgebaut wird es nie.
#
# XChaCha20 und GCM-SIV benutzen dieselbe Schlüsselform wie ChaCha20 und
# AES (32 Byte als Base64), deshalb die vorhandenen Schlüsselarten.

def _neue_verfahren_eintragen():
    try:
        from kern import krypto_neu as neu
    except ImportError:
        return

    if neu.xchacha_verfuegbar():
        VERFAHREN["XChaCha20-Poly1305"] = {
            "enc": neu.xchacha_verschluesseln, "dec": neu.xchacha_entschluesseln,
            "art": "sicher", "key": "chacha",
            "hinweis": "Wie ChaCha20, aber mit 24-Byte-Nonce – zufällige Nonces "
                       "können sich praktisch nie wiederholen, auch nicht nach "
                       "Milliarden Nachrichten mit demselben Schlüssel.",
        }
    if neu.gcmsiv_verfuegbar():
        VERFAHREN["AES-256-GCM-SIV"] = {
            "enc": neu.gcmsiv_verschluesseln, "dec": neu.gcmsiv_entschluesseln,
            "art": "sicher", "key": "aes",
            "hinweis": "AES-256 in einer robusteren Bauart: verzeiht ein versehentlich "
                       "doppelt benutztes Nonce, statt komplett zu versagen. Verraten "
                       "würde dann nur, ob zweimal derselbe Text verschlüsselt wurde.",
        }
    if neu.pq_verfuegbar():
        VERFAHREN["Post-Quanten (Hybrid)"] = {
            "enc": neu.pq_verschluesseln, "dec": neu.pq_entschluesseln,
            "art": "sicher", "key": "pq",
            "hinweis": "Schützt auch gegen künftige Quantencomputer (ML-KEM-768) und "
                       "ist mit dem klassischen X25519 kombiniert – heute also "
                       "mindestens so stark wie X25519 allein. Schlüssel und "
                       "Geheimtext sind lang (über 1500 Zeichen).",
        }
    if neu.age_verfuegbar():
        VERFAHREN["age (Passwort)"] = {
            "enc": neu.age_passwort_verschluesseln,
            "dec": neu.age_passwort_entschluesseln,
            "art": "sicher", "key": "passwort",
            "hinweis": "Das offene age-Format: lässt sich auch mit dem offiziellen "
                       "Programm age öffnen, ganz ohne VP4. Absichtlich langsam "
                       "(ein, zwei Sekunden), damit Passwort-Raten teuer wird.",
        }
        VERFAHREN["age (Schlüsselpaar)"] = {
            "enc": neu.age_verschluesseln, "dec": neu.age_entschluesseln,
            "art": "sicher", "key": "age",
            "hinweis": "age mit Schlüsselpaar (age1… / AGE-SECRET-KEY-1…), dieselben "
                       "Schlüssel wie bei age-keygen – mit dem offiziellen Programm "
                       "age austauschbar. Mehrere Empfänger mit Komma trennen.",
        }

    SCHLUESSEL_ARTEN.setdefault(
        "pq", ("Zum Verschlüsseln: öffentlicher Schlüssel (VP4PQ1-…) / "
               "zum Entschlüsseln: privater (VP4PQS1-…)", True))
    SCHLUESSEL_ARTEN.setdefault(
        "age", ("Zum Verschlüsseln: öffentlicher Schlüssel (age1…) / "
                "zum Entschlüsseln: privater (AGE-SECRET-KEY-1…)", True))


_neue_verfahren_eintragen()
