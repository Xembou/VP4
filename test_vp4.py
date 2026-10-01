#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
=====================================================================
 Selbsttest für VP4 5
=====================================================================
Prüft, ob nach einer Änderung noch alles funktioniert:

    python test_vp4.py

Am Ende steht, wie viele Prüfungen bestanden wurden.

Der Test fasst deine echten Daten NICHT an - Schlüsselspeicher und
empfangene Dateien landen in einem Wegwerf-Ordner, der danach wieder
gelöscht wird. Nur der Oberflächen-Test startet kurz das echte
Fenster und schließt es sofort wieder.

Ohne Fenster testen (z.B. auf einem Server):
    set VP4_TEST_OHNE_GUI=1  &&  python test_vp4.py

Diese Datei gehört nicht ins fertige Programm - die .exe wird nur aus
VP4.py gebaut.
=====================================================================
"""

import base64
import json
import os
import queue
import struct
import sys
import tempfile
import threading
import time
import traceback
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

# MUSS vor dem Import von speicher/gui stehen: verhindert, dass ein Testlauf
# die echten Einstellungen überschreibt. Ohne das hat ein Testlauf schon
# einmal den eingestellten Obsidian-Ordner gelöscht, weil der Test das
# Hauptfenster mit erfundenen Standardwerten aufbaut und die dann gespeichert
# wurden.
os.environ["VP4_TESTMODUS"] = "1"

sys.path.insert(0, str(Path(__file__).resolve().parent))

from kern import dateien
from kern import krypto
from kern import speicher
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
import asyncio

from kern.krypto import (VERFAHREN, SCHLUESSEL_ARTEN, ClassicCiphers, ModernCrypto,
                    Pruefsummen, Signaturen)
from kern.speicher import (FalschesPasswortError, KeyStore, ObsidianSync,
                      passwort_staerke)


# ---------------------------------------------------------------------------
#  Testhilfe
# ---------------------------------------------------------------------------

class Ergebnis:
    def __init__(self):
        self.ok = 0
        self.fehler = []

    def pruefe(self, name, bedingung, detail=""):
        if bedingung:
            self.ok += 1
            print(f"  [OK]   {name}")
        else:
            self.fehler.append(name)
            print(f"  [FEHL] {name}")
            if detail:
                print(f"         {detail}")

    def fehlschlag(self, name, ausnahme):
        self.fehler.append(name)
        print(f"  [FEHL] {name}")
        print(f"         {type(ausnahme).__name__}: {ausnahme}")


R = Ergebnis()


def _wirft_valueerror(funktion, *argumente) -> bool:
    """Wahr, wenn der Aufruf mit einem ValueError abbricht.

    Spart das immer gleiche try/except-Gerüst bei den vielen Prüfungen, die
    nur wissen wollen: wird kaputte Eingabe sauber abgelehnt?
    """
    try:
        funktion(*argumente)
    except ValueError:
        return True
    except Exception:
        return False
    return False


def _wirft_fehler(funktion, art, *argumente) -> bool:
    """Wie _wirft_valueerror(), aber für eine beliebige Fehlerart.

    Beim Chat kommt es darauf an, WELCHER Fehler gemeldet wird: ein
    ConnectionError heißt "gerade kein Weg offen", ein ValueError heißt
    "so nicht" - die Oberfläche schreibt Verschiedenes daraufhin.
    """
    try:
        funktion(*argumente)
    except art:
        return True
    except Exception:
        return False
    return False

# Enthält absichtlich alles, was erfahrungsgemäß Probleme macht:
# Umlaute, ß, Ziffern, Leer- und Sonderzeichen.
TESTTEXT = "Hallo Leon! Grüße aus Straße 5 - äöüß ÄÖÜ 123 & % Ende"


# ---------------------------------------------------------------------------
#  1) Klassische Verfahren
# ---------------------------------------------------------------------------

def test_klassische_verfahren():
    print("\n=== Klassische Verfahren ===")
    C = ClassicCiphers

    # Verfahren, bei denen exakt derselbe Text zurückkommen muss
    verlustfrei = [
        ("Caesar", C.caesar_encrypt, C.caesar_decrypt, "5"),
        ("Vigenere", C.vigenere_encrypt, C.vigenere_decrypt, "SCHLUESSEL"),
        ("XOR", C.xor_encrypt, C.xor_decrypt, "geheim"),
        ("Base64", C.base64_encode, C.base64_decode, ""),
        ("ROT13", C.rot13, C.rot13, ""),
        ("Atbash", C.atbash, C.atbash, ""),
        ("Rail-Fence", C.railfence_encrypt, C.railfence_decrypt, "3"),
    ]
    for name, enc, dec, key in verlustfrei:
        try:
            zurueck = dec(enc(TESTTEXT, key), key)
            R.pruefe(f"{name}: Text kommt unverändert zurück",
                     zurueck == TESTTEXT,
                     f"erwartet: {TESTTEXT!r}\n         bekam:    {zurueck!r}")
        except Exception as e:
            R.fehlschlag(f"{name}: Roundtrip", e)

    # Gegen bekannte Referenzwerte prüfen. Damit ist belegt, dass die
    # Verfahren wirklich richtig rechnen und nicht nur in sich umkehrbar sind.
    referenzen = [
        ("Vigenere", lambda: C.vigenere_encrypt("ATTACKATDAWN", "LEMON"), "LXFOPVEFRNHR"),
        ("ROT13", lambda: C.rot13("Hallo"), "Unyyb"),
        ("Atbash", lambda: C.atbash("ABC"), "ZYX"),
        ("Caesar", lambda: C.caesar_encrypt("abc", "3"), "def"),
        ("Rail-Fence", lambda: C.railfence_encrypt("WEAREDISCOVEREDFLEEATONCE", "3"),
         "WECRLTEERDSOEEFEAOCAIVDEN"),
        ("Morse", lambda: C.morse_encode("SOS"), "... --- ..."),
        # Das kanonische Playfair-Beispiel aus der Wikipedia.
        ("Playfair", lambda: C.playfair_encrypt("hide the gold in the tree stump",
                                                "playfair example"),
         "BMODZBXDNABEKUDMUIXMMOUVIF"),
    ]
    for name, fn, erwartet in referenzen:
        try:
            wert = fn()
            R.pruefe(f"{name} stimmt mit der bekannten Referenz überein",
                     wert == erwartet, f"bekam {wert!r}, erwartet {erwartet!r}")
        except Exception as e:
            R.fehlschlag(f"{name} Referenz", e)

    # Morse und Playfair sind nicht buchstabengetreu umkehrbar (Morse kennt
    # keine Satzzeichenvielfalt, Playfair schiebt X ein) - deshalb hier auf
    # passendem Text prüfen.
    try:
        R.pruefe("Morse: Roundtrip auf Buchstaben und Ziffern",
                 C.morse_decode(C.morse_encode("HALLO LEON 123")) == "HALLO LEON 123")
    except Exception as e:
        R.fehlschlag("Morse Roundtrip", e)
    try:
        zurueck = C.playfair_decrypt(C.playfair_encrypt("TREFFENUMDREI", "GEHEIM"), "GEHEIM")
        R.pruefe("Playfair: Roundtrip liefert den Text wieder",
                 zurueck.replace("X", "") == "TREFFENUMDREI".replace("X", ""),
                 f"bekam {zurueck!r}")
    except Exception as e:
        R.fehlschlag("Playfair Roundtrip", e)

    # Ungültige Schlüssel müssen eine verständliche Meldung geben, nicht abstürzen
    ungueltig = [
        ("Caesar ohne Schlüssel", C.caesar_encrypt, ""),
        ("Caesar mit Buchstaben statt Zahl", C.caesar_encrypt, "abc"),
        ("Vigenere ohne Schlüssel", C.vigenere_encrypt, ""),
        ("Vigenere nur mit Umlauten", C.vigenere_encrypt, "äöü"),
        ("XOR ohne Schlüssel", C.xor_encrypt, ""),
        ("Rail-Fence ohne Schlüssel", C.railfence_encrypt, ""),
        ("Rail-Fence mit 0 Zeilen", C.railfence_encrypt, "0"),
        ("Playfair ohne Schlüsselwort", C.playfair_encrypt, ""),
    ]
    for name, fn, key in ungueltig:
        try:
            fn("Testtext", key)
            R.pruefe(name + " wird abgelehnt", False, "kein Fehler ausgelöst!")
        except ValueError:
            R.pruefe(name + " wird abgelehnt", True)
        except Exception as e:
            R.pruefe(name + " wird abgelehnt", False,
                     f"falsche Fehlerart: {type(e).__name__}: {e}")

    # Kaputte Geheimtexte dürfen nicht mit einem Absturz enden
    for name, fn, key in [("XOR", C.xor_decrypt, "geheim"),
                          ("Base64", C.base64_decode, ""),
                          ("Morse", C.morse_decode, "")]:
        try:
            fn("das ist kein gültiger Geheimtext !!!", key)
            R.pruefe(f"{name}: kaputte Eingabe wird abgefangen", False,
                     "kein Fehler ausgelöst")
        except ValueError:
            R.pruefe(f"{name}: kaputte Eingabe wird abgefangen", True)
        except Exception as e:
            R.pruefe(f"{name}: kaputte Eingabe wird abgefangen", False,
                     f"falsche Fehlerart: {type(e).__name__}: {e}")


# ---------------------------------------------------------------------------
#  2) Moderne Verfahren
# ---------------------------------------------------------------------------

def test_moderne_verfahren():
    print("\n=== Moderne Verfahren (AES / ChaCha20 / Passwort / RSA) ===")
    M = ModernCrypto

    for name, erzeuge, enc, dec in [
        ("AES-256-GCM", M.generate_aes_key, M.aes_encrypt, M.aes_decrypt),
        ("ChaCha20-Poly1305", M.generate_chacha_key, M.chacha_encrypt, M.chacha_decrypt),
    ]:
        try:
            k = erzeuge()
            geheim = enc(TESTTEXT, k)
            R.pruefe(f"{name}: Text kommt unverändert zurück", dec(geheim, k) == TESTTEXT)
            try:
                dec(geheim, erzeuge())
                R.pruefe(f"{name} lehnt einen falschen Schlüssel ab", False,
                         "hat trotzdem entschlüsselt!")
            except ValueError:
                R.pruefe(f"{name} lehnt einen falschen Schlüssel ab", True)
            # Zweimal derselbe Text muss zwei verschiedene Geheimtexte ergeben,
            # sonst wäre erkennbar, wenn zweimal dasselbe gesendet wurde.
            R.pruefe(f"{name}: gleicher Text ergibt verschiedene Geheimtexte",
                     enc("gleich", k) != enc("gleich", k))
        except Exception as e:
            R.fehlschlag(name, e)

    # Passwort-Verschlüsselung
    try:
        geheim = M.password_encrypt(TESTTEXT, "mein geheimes Passwort")
        R.pruefe("Passwort-Verschlüsselung: Text kommt unverändert zurück",
                 M.password_decrypt(geheim, "mein geheimes Passwort") == TESTTEXT)
        try:
            M.password_decrypt(geheim, "falsches Passwort")
            R.pruefe("Falsches Passwort wird abgelehnt", False, "hat entschlüsselt!")
        except ValueError:
            R.pruefe("Falsches Passwort wird abgelehnt", True)
        R.pruefe("Gleiches Passwort ergibt verschiedene Geheimtexte",
                 M.password_encrypt("x", "pw") != M.password_encrypt("x", "pw"))
        try:
            M.password_decrypt("QUJD", "pw")     # Base64, aber ohne Kennzeichnung
            R.pruefe("Fremder Text wird als solcher erkannt", False, "kein Fehler")
        except ValueError:
            R.pruefe("Fremder Text wird als solcher erkannt", True)
    except Exception as e:
        R.fehlschlag("Passwort-Verschlüsselung", e)

    # RSA
    try:
        privat, oeffentlich = M.generate_rsa_keypair()
        kurz = "Kurzer Text für RSA"
        R.pruefe("RSA-2048: Text kommt unverändert zurück",
                 M.rsa_decrypt(M.rsa_encrypt(kurz, oeffentlich), privat) == kurz)
        try:
            M.rsa_encrypt("x" * 500, oeffentlich)
            R.pruefe("RSA sagt bei zu langem Text verständlich Bescheid", False,
                     "kein Fehler")
        except ValueError as e:
            R.pruefe("RSA sagt bei zu langem Text verständlich Bescheid",
                     "190" in str(e), str(e))
    except Exception as e:
        R.fehlschlag("RSA", e)


# ---------------------------------------------------------------------------
#  3) Signaturen und Prüfsummen
# ---------------------------------------------------------------------------

def test_signaturen():
    print("\n=== Signaturen & Prüfsummen ===")
    try:
        privat, oeffentlich = Signaturen.generate_keypair()
        signatur = Signaturen.sign(TESTTEXT, privat)
        R.pruefe("Ed25519: die eigene Signatur wird anerkannt",
                 Signaturen.verify(TESTTEXT, signatur, oeffentlich) is True)
        R.pruefe("Ed25519: ein verändertes Zeichen fällt auf",
                 Signaturen.verify(TESTTEXT + "!", signatur, oeffentlich) is False)
        _, fremd = Signaturen.generate_keypair()
        R.pruefe("Ed25519: fremder Schlüssel passt nicht",
                 Signaturen.verify(TESTTEXT, signatur, fremd) is False)
    except Exception as e:
        R.fehlschlag("Signaturen", e)

    try:
        R.pruefe("SHA-256 stimmt mit der bekannten Referenz überein",
                 Pruefsummen.berechne("") ==
                 "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855")
        R.pruefe("Ein geändertes Zeichen ergibt eine andere Prüfsumme",
                 Pruefsummen.berechne("Hallo") != Pruefsummen.berechne("Hallo!"))
        R.pruefe("Alle Prüfsummen-Verfahren rechnen",
                 all(Pruefsummen.berechne("x", v) for v in Pruefsummen.VERFAHREN))
    except Exception as e:
        R.fehlschlag("Prüfsummen", e)


# ---------------------------------------------------------------------------
#  4) Schlüsselspeicher
# ---------------------------------------------------------------------------

def test_schluesselspeicher():
    print("\n=== Schlüsselspeicher ===")
    with tempfile.TemporaryDirectory() as ordner:
        pfad = Path(ordner) / "test_schluessel.enc"
        passwort = "MeinMasterPasswort123"
        try:
            sp = KeyStore(pfad)
            sp.create(passwort)
            geheimer_wert = ModernCrypto.generate_aes_key()
            sp.add_key("Testschluessel", "AES", geheimer_wert, "eine Notiz")
            R.pruefe("Schlüssel anlegen", len(sp.list_keys()) == 1)

            try:
                sp.add_key("Testschluessel", "AES", "x")
                R.pruefe("Doppelter Name wird abgelehnt", False, "kein Fehler")
            except ValueError:
                R.pruefe("Doppelter Name wird abgelehnt", True)

            sp.lock()
            R.pruefe("Nach dem Sperren ist der Speicher zu", not sp.is_unlocked())

            try:
                KeyStore(pfad).unlock("FALSCHESPASSWORT")
                R.pruefe("Falsches Master-Passwort wird abgelehnt", False,
                         "wurde trotzdem geöffnet!")
            except FalschesPasswortError:
                R.pruefe("Falsches Master-Passwort wird abgelehnt", True)

            wieder = KeyStore(pfad)
            wieder.unlock(passwort)
            R.pruefe("Richtiges Master-Passwort öffnet wieder",
                     len(wieder.list_keys()) == 1)

            R.pruefe("Die Datei enthält den Schlüssel nicht im Klartext",
                     geheimer_wert.encode("utf-8") not in pfad.read_bytes())

            # Eine veränderte Datei muss auffallen - AES-GCM merkt das selbst.
            roh = bytearray(pfad.read_bytes())
            roh[-1] ^= 0xFF
            manipuliert = Path(ordner) / "manipuliert.enc"
            manipuliert.write_bytes(bytes(roh))
            try:
                KeyStore(manipuliert).unlock(passwort)
                R.pruefe("Eine veränderte Speicherdatei fällt auf", False,
                         "wurde klaglos geöffnet!")
            except Exception:
                R.pruefe("Eine veränderte Speicherdatei fällt auf", True)

            # Passwort ändern - der Inhalt muss erhalten bleiben
            wieder.change_password(passwort, "NeuesPasswort456")
            noch_mal = KeyStore(pfad)
            noch_mal.unlock("NeuesPasswort456")
            R.pruefe("Nach Passwortwechsel ist der Inhalt noch da",
                     len(noch_mal.list_keys()) == 1)
            try:
                KeyStore(pfad).unlock(passwort)
                R.pruefe("Das alte Passwort gilt nicht mehr", False, "ging noch!")
            except FalschesPasswortError:
                R.pruefe("Das alte Passwort gilt nicht mehr", True)

            noch_mal.delete_key("Testschluessel")
            R.pruefe("Schlüssel löschen", len(noch_mal.list_keys()) == 0)
        except Exception as e:
            R.fehlschlag("Schlüsselspeicher", e)
            traceback.print_exc()

    # Gesperrter Speicher darf nichts herausgeben
    with tempfile.TemporaryDirectory() as ordner:
        try:
            sp = KeyStore(Path(ordner) / "x.enc")
            sp.list_keys()
            R.pruefe("Gesperrter Speicher gibt nichts heraus", False, "kein Fehler")
        except Exception:
            R.pruefe("Gesperrter Speicher gibt nichts heraus", True)

    stufen = [passwort_staerke(p)[0] for p in ["a", "abcdefgh", "Abcdefgh1!ngLang"]]
    R.pruefe("Passwortstärke steigt mit Länge und Vielfalt",
             stufen[0] < stufen[-1], f"Stufen: {stufen}")

    # Ein Testlauf darf die echten Einstellungen niemals verändern. Das ist
    # schon einmal schiefgegangen: der Test baut das Hauptfenster mit
    # erfundenen Standardwerten auf, die wurden gespeichert, und der
    # tatsächlich eingestellte Obsidian-Ordner war weg.
    vorher = speicher.CONFIG_FILE.read_bytes() if speicher.CONFIG_FILE.exists() else None
    speicher.save_config({"my_id": "KAPUTT", "obsidian_vault": "weg"})
    nachher = speicher.CONFIG_FILE.read_bytes() if speicher.CONFIG_FILE.exists() else None
    R.pruefe("Ein Testlauf fasst die echten Einstellungen nicht an",
             vorher == nachher,
             "save_config hat trotz VP4_TESTMODUS geschrieben!")


# ---------------------------------------------------------------------------
#  5) Formatversionen und Schlüsselableitung
# ---------------------------------------------------------------------------

# Ein Schlüsselspeicher, der mit der allerersten Fassung geschrieben wurde:
# Marke "VP4K2", PBKDF2 mit 600.000 Runden, Parameter nirgends vermerkt.
# Dieser Block darf NIE angepasst werden - er ist der Beweis dafür, dass ein
# Speicher, den jemand vor einem Jahr angelegt hat, sich heute noch öffnen
# lässt. Bricht dieser Test, hat ein Update alte Daten unbrauchbar gemacht.
ALT_KEYSTORE_B64 = (
    "VlA0SzL+2a7Ru1svLltoo34Quj4yMDOEaXr+2sFo7YwhI9gHc6aRR25QBC36mqD1NB9r2iUl"
    "v3YDKgVUOKAG2AD1Q20roJ7nfXPcMs3dHue4YlCjuTWTxdbYFjSrGSo8Id6YvK/62TTtDCO8"
    "GmAjjof2Frjrr/c7tRRJwSyUVnPfRIN3VOJ9OaqiKwtuhAvvFwIiL9O2dYbWTjrB6IZubCfF"
    "GWfnMNgHYsiFZyqg0uwolmow7ygdVGEZsPiiPQwkFm/rFazD8XAyEbuogwt2+vV5PlbOGjHu"
    "JrcLOArdKihTVZEElYCgdXEw"
)
ALT_KEYSTORE_PASSWORT = "AltesMasterPasswort2026"

# Ein mit der alten Passwort-Verschlüsselung (Marke "VP4P1") erzeugter Text.
ALT_TEXT_B64 = (
    "VlA0UDFENE/6VQj8cw+VODkprGcL4UG6IF6mVF6LT+icDn9dnxNEzbovEDp6Xl8+C8UKRciD"
    "AjDZ+2Zoo1DP8dobkLPVC0kaV26Orl0C5R6zyQ=="
)
ALT_TEXT_PASSWORT = "AltesPasswort"
ALT_TEXT_KLAR = "Alter Text mit Umlauten: äöüß"


def test_formatversionen():
    print("\n=== Formatversionen und Schlüsselableitung ===")

    # --- Der eigentliche Grund für diese Testgruppe -------------------------
    # Früher stand die Rundenzahl nirgends in der Datei, sondern fest im
    # Programm. Wer sie erhöht hätte - und irgendwann erhöht man sie -, hätte
    # damit jeden bestehenden Schlüsselspeicher unlesbar gemacht, ohne dass
    # irgendetwas gewarnt hätte. Deshalb muss sich ein Speicher mit
    # ungewöhnlichen Parametern öffnen lassen: das ist der Beweis, dass die
    # Parameter tatsächlich aus der Datei kommen und nicht aus dem Code.
    with tempfile.TemporaryDirectory() as ordner:
        pfad = Path(ordner) / "fremd.enc"
        eigenwillig = {"kdf": krypto.KDF_PBKDF2, "runden": 1000}
        inhalt = {"keys": [{"label": "X", "typ": "AES", "wert": "abc",
                            "meta": "", "erstellt": "2026-01-01 00:00"}],
                  "angelegt": "2026-01-01 00:00"}
        salt = os.urandom(16)
        kopf = ModernCrypto.kopf_bauen(KeyStore.MARKE, salt, eigenwillig)
        abgeleitet = ModernCrypto.schluessel_ableiten(
            "fremdes Passwort", salt, eigenwillig)
        nonce = os.urandom(12)
        ct = AESGCM(abgeleitet).encrypt(
            nonce, json.dumps(inhalt).encode("utf-8"), kopf)
        pfad.write_bytes(kopf + nonce + ct)

        ks = KeyStore(pfad)
        try:
            ks.unlock("fremdes Passwort")
            R.pruefe("Speicher mit anderen KDF-Parametern lässt sich öffnen",
                     ks.list_keys()[0]["label"] == "X")
        except Exception as e:
            R.fehlschlag("Speicher mit anderen KDF-Parametern lässt sich öffnen", e)

    # --- Alte Dateien bleiben lesbar ---------------------------------------
    with tempfile.TemporaryDirectory() as ordner:
        pfad = Path(ordner) / "alt.enc"
        pfad.write_bytes(base64.b64decode(ALT_KEYSTORE_B64))

        ks = KeyStore(pfad)
        try:
            ks.unlock(ALT_KEYSTORE_PASSWORT)
            vorhanden = ks.list_keys()
            R.pruefe("Alter Speicher (VP4K2) lässt sich noch öffnen",
                     len(vorhanden) == 1 and vorhanden[0]["label"] == "Alter AES",
                     f"gelesen: {vorhanden}")
        except Exception as e:
            R.fehlschlag("Alter Speicher (VP4K2) lässt sich noch öffnen", e)

        # Beim nächsten Speichern soll er still auf das neue Format wechseln.
        try:
            ks.add_key("Neuer Schlüssel", "AES", "neu", "")
            roh = pfad.read_bytes()
            R.pruefe("Alter Speicher wandert beim Speichern auf VP4K3",
                     roh.startswith(b"VP4K3"), f"Marke: {roh[:5]!r}")

            frisch = KeyStore(pfad)
            frisch.unlock(ALT_KEYSTORE_PASSWORT)
            labels = sorted(k["label"] for k in frisch.list_keys())
            R.pruefe("Nach dem Umzug ist alles da und das Passwort gilt weiter",
                     labels == ["Alter AES", "Neuer Schlüssel"], f"gefunden: {labels}")
        except Exception as e:
            R.fehlschlag("Alter Speicher wandert beim Speichern auf VP4K3", e)

    try:
        R.pruefe("Alter Geheimtext (VP4P1) lässt sich noch entschlüsseln",
                 ModernCrypto.password_decrypt(ALT_TEXT_B64, ALT_TEXT_PASSWORT)
                 == ALT_TEXT_KLAR)
    except Exception as e:
        R.fehlschlag("Alter Geheimtext (VP4P1) lässt sich noch entschlüsseln", e)

    # --- Neu Geschriebenes benutzt Argon2id --------------------------------
    neu = ModernCrypto.password_encrypt("Hallo Welt", "geheim")
    roh = base64.b64decode(neu)
    R.pruefe("Neuer Geheimtext trägt die Marke VP4P2",
             roh.startswith(b"VP4P2"), f"Marke: {roh[:5]!r}")
    R.pruefe("Neuer Geheimtext benutzt Argon2id",
             roh[5] == krypto.KDF_ARGON2ID, f"KDF-Kennung: {roh[5]}")
    R.pruefe("Neuer Geheimtext lässt sich wieder entschlüsseln",
             ModernCrypto.password_decrypt(neu, "geheim") == "Hallo Welt")
    R.pruefe("Falsches Passwort wird auch im neuen Format abgewiesen",
             _wirft_valueerror(ModernCrypto.password_decrypt, neu, "falsch"))

    with tempfile.TemporaryDirectory() as ordner:
        pfad = Path(ordner) / "neu.enc"
        ks = KeyStore(pfad)
        ks.create("MeinMasterPasswort")
        roh = pfad.read_bytes()
        R.pruefe("Neuer Speicher trägt die Marke VP4K3", roh.startswith(b"VP4K3"),
                 f"Marke: {roh[:5]!r}")
        R.pruefe("Neuer Speicher benutzt Argon2id", roh[5] == krypto.KDF_ARGON2ID)

    # Die Parameter sind festgenagelt. Sie zu ändern ist erlaubt - aber dann
    # muss man diesen Test bewusst anfassen und dabei über die Folgen
    # nachdenken, statt sie versehentlich zu verschieben.
    R.pruefe("Argon2id-Parameter sind die vereinbarten",
             krypto.ARGON2_STANDARD == {"kdf": krypto.KDF_ARGON2ID, "zeit": 3,
                                        "speicher_kib": 65536, "parallel": 1},
             f"tatsächlich: {krypto.ARGON2_STANDARD}")

    # --- Der Kopf ist mitversiegelt ----------------------------------------
    # Die Parameter stehen offen in der Datei. Wenn jemand sie verdreht, etwa
    # die Speichergrösse heruntersetzt, muss das auffallen - sonst liesse sich
    # die Ableitung von aussen schwächen.
    verbogen = bytearray(base64.b64decode(
        ModernCrypto.password_encrypt("geheim", "pw")))
    verbogen[10] ^= 0x01          # irgendwo in den Argon2-Parametern
    R.pruefe("Verdrehte KDF-Parameter fallen auf",
             _wirft_valueerror(ModernCrypto.password_decrypt,
                               base64.b64encode(bytes(verbogen)).decode("ascii"), "pw"))

    # Abgelehnt werden muss das VOR dem Ableiten: das Siegel wird erst danach
    # geprüft. Vorher rechnete Argon2 mit 16 GiB los und scheiterte mit
    # einem Speicherfehler statt mit "beschädigt".
    import struct as _struct
    for zeit, speicher_kib, name in [(3, 0x7FFFFFFF, "riesiger Speicher"),
                                     (4_000_000_000, 65536, "Milliarden Durchgänge"),
                                     (3, 1, "winziger Speicher")]:
        kopf = (b"VP4P2" + bytes([krypto.KDF_ARGON2ID])
                + _struct.pack("!IIB", zeit, speicher_kib, 1))
        roh = kopf + b"s" * 16 + b"n" * 12 + b"c" * 32
        start = time.time()
        R.pruefe(f"Unmögliche KDF-Parameter werden vor dem Rechnen abgelehnt ({name})",
                 _wirft_valueerror(ModernCrypto.password_decrypt,
                                   base64.b64encode(roh).decode("ascii"), "pw")
                 and time.time() - start < 1.0)

    for kaputt, name in [(b"VP4P9" + b"x" * 40, "unbekannte Marke"),
                         (b"VP4P2" + bytes([99]) + b"x" * 40, "unbekannte KDF-Kennung"),
                         (b"VP4P2", "abgeschnittener Kopf")]:
        R.pruefe(f"Kaputter Geheimtext wird abgelehnt ({name})",
                 _wirft_valueerror(ModernCrypto.password_decrypt,
                                   base64.b64encode(kaputt).decode("ascii"), "pw"))

    # --- Passwortwechsel schreibt neu ab -----------------------------------
    with tempfile.TemporaryDirectory() as ordner:
        pfad = Path(ordner) / "wechsel.enc"
        pfad.write_bytes(base64.b64decode(ALT_KEYSTORE_B64))
        ks = KeyStore(pfad)
        try:
            ks.change_password(ALT_KEYSTORE_PASSWORT, "GanzNeuesPasswort")
            frisch = KeyStore(pfad)
            frisch.unlock("GanzNeuesPasswort")
            R.pruefe("Passwortwechsel hebt einen alten Speicher auf das neue Format",
                     pfad.read_bytes().startswith(b"VP4K3")
                     and len(frisch.list_keys()) == 1)
        except Exception as e:
            R.fehlschlag("Passwortwechsel hebt einen alten Speicher auf das neue Format", e)


# ---------------------------------------------------------------------------
#  6) Dateien und Ordner
# ---------------------------------------------------------------------------

def _blockgrenzen(pfad):
    """Findet die Byte-Bereiche der einzelnen Blöcke - ohne Schlüssel.

    Der äussere Aufbau einer .vp4-Datei ist absichtlich auch ohne Schlüssel
    lesbar (Längen stehen im Klartext davor). Nur so lassen sich hier
    gezielt Blöcke verbiegen, entfernen oder vertauschen.
    """
    roh = pfad.read_bytes()
    praefix = dateien.MARKE + bytes([roh[5]])
    kopf, _, _ = ModernCrypto.kopf_lesen(roh, praefix)
    pos = len(kopf) + 8                       # + Nonce-Basis
    pos += 4 + struct.unpack("!I", roh[pos:pos + 4])[0]   # + Kopfsatz
    grenzen = []
    while pos < len(roh):
        laenge = struct.unpack("!I", roh[pos:pos + 4])[0]
        grenzen.append((pos, pos + 4 + laenge))
        pos += 4 + laenge
    return roh, grenzen


def test_dateien():
    print("\n=== Dateien und Ordner ===")

    echte_blockgroesse = dateien.BLOCK
    with tempfile.TemporaryDirectory() as ordner:
        basis = Path(ordner)
        aus = basis / "wieder"
        aus.mkdir()

        # ---------------------------------------------------- Grundfälle
        faelle = [
            ("Kleine Datei", b"Hallo Leon! Gruesse aus Strasse 5 - aeoeuess"),
            ("Leere Datei", b""),
            ("Datei ueber mehrere Bloecke", os.urandom(2_500_000)),
        ]
        for name, inhalt in faelle:
            quelle = basis / f"{name}.bin"
            quelle.write_bytes(inhalt)
            paket = dateien.verschluesseln(quelle, dateien.zielname(quelle),
                                           "MeinPasswort")
            zurueck = dateien.entschluesseln(paket, aus, "MeinPasswort")
            R.pruefe(f"{name}: kommt Byte für Byte zurück",
                     zurueck.read_bytes() == inhalt,
                     f"{len(zurueck.read_bytes())} statt {len(inhalt)} Byte")

        # Umlaute im Namen, und der Name darf nicht im Klartext dastehen.
        heikel = basis / "Zeugnis Halbjahr äöüß.txt"
        heikel.write_text("streng geheim", encoding="utf-8")
        paket = dateien.verschluesseln(heikel, dateien.zielname(heikel), "pw")
        roh = paket.read_bytes()
        R.pruefe("Der Dateiname steht nicht im Klartext im Container",
                 "Zeugnis".encode("utf-8") not in roh
                 and "Zeugnis".encode("utf-16-le") not in roh)
        zurueck = dateien.entschluesseln(paket, aus, "pw")
        R.pruefe("Umlaute im Dateinamen überstehen die Runde",
                 zurueck.name == "Zeugnis Halbjahr äöüß.txt"
                 and zurueck.read_text(encoding="utf-8") == "streng geheim",
                 f"zurück kam: {zurueck.name}")

        # ------------------------------------- Schlüssel statt Passwort
        schluessel = ModernCrypto.generate_aes_key()
        quelle = basis / "mit_schluessel.bin"
        quelle.write_bytes(b"x" * 5000)
        paket = dateien.verschluesseln(quelle, dateien.zielname(quelle),
                                       schluessel, art=dateien.ART_SCHLUESSEL)
        R.pruefe("Die Oberfläche erkennt, welcher Schlüssel gebraucht wird",
                 dateien.kopf_ansehen(paket)["art"] == dateien.ART_SCHLUESSEL)
        zurueck = dateien.entschluesseln(paket, aus, schluessel)
        R.pruefe("Datei mit gespeichertem Schlüssel kommt zurück",
                 zurueck.read_bytes() == b"x" * 5000)

        # Zweimal derselbe Schlüssel darf nicht zweimal dasselbe ergeben -
        # sonst wäre irgendwann ein Nonce doppelt benutzt.
        paket2 = dateien.verschluesseln(quelle, basis / "zweitfassung.vp4",
                                        schluessel, art=dateien.ART_SCHLUESSEL)
        R.pruefe("Zweimal verschlüsselt ergibt zweimal etwas anderes",
                 paket.read_bytes() != paket2.read_bytes())

        # -------------------------------------------- Falscher Schlüssel
        quelle = basis / "geheim.bin"
        quelle.write_bytes(b"Inhalt" * 100)
        paket = dateien.verschluesseln(quelle, dateien.zielname(quelle), "richtig")
        R.pruefe("Falsches Passwort wird abgewiesen",
                 _wirft_valueerror(dateien.entschluesseln, paket, aus, "falsch"))
        R.pruefe("Eine fremde Datei wird als solche erkannt",
                 _wirft_valueerror(dateien.entschluesseln, heikel, aus, "pw"))

        # ------------------------------------------ Angriffe auf Blöcke
        # Ab hier mit kleinen Blöcken, damit mehrere Blöcke entstehen,
        # ohne dass der Test megabyteweise Daten schaufeln muss.
        dateien.BLOCK = 1024
        try:
            quelle = basis / "mehrere_bloecke.bin"
            inhalt = os.urandom(5000)          # ergibt fünf Blöcke
            quelle.write_bytes(inhalt)
            original = dateien.verschluesseln(quelle, basis / "angriff.vp4", "pw")
            roh, grenzen = _blockgrenzen(original)
            R.pruefe("Grosse Daten werden in mehrere Blöcke zerlegt",
                     len(grenzen) >= 4, f"{len(grenzen)} Blöcke")

            # a) Ein Bit in der Mitte kippen
            verbogen = bytearray(roh)
            verbogen[grenzen[1][0] + 10] ^= 0x01
            ziel = basis / "gekippt.vp4"
            ziel.write_bytes(bytes(verbogen))
            R.pruefe("Ein gekipptes Bit fällt auf",
                     _wirft_valueerror(dateien.entschluesseln, ziel, aus, "pw"))

            # b) Den letzten Block abschneiden. Der Rest ist für sich
            #    genommen unversehrt - erst das Kennzeichen "letzter Block"
            #    verrät, dass etwas fehlt.
            ziel = basis / "abgeschnitten.vp4"
            ziel.write_bytes(roh[:grenzen[-1][0]])
            R.pruefe("Ein hinten abgeschnittener Container fällt auf",
                     _wirft_valueerror(dateien.entschluesseln, ziel, aus, "pw"))

            # c) Zwei Blöcke vertauschen
            a, b = grenzen[0], grenzen[1]
            getauscht = (roh[:a[0]] + roh[b[0]:b[1]] + roh[a[0]:a[1]]
                         + roh[b[1]:])
            ziel = basis / "vertauscht.vp4"
            ziel.write_bytes(getauscht)
            R.pruefe("Vertauschte Blöcke fallen auf",
                     _wirft_valueerror(dateien.entschluesseln, ziel, aus, "pw"))

            # d) Einen Block in der Mitte entfernen
            ziel = basis / "block_fehlt.vp4"
            ziel.write_bytes(roh[:grenzen[1][0]] + roh[grenzen[1][1]:])
            R.pruefe("Ein fehlender Block in der Mitte fällt auf",
                     _wirft_valueerror(dateien.entschluesseln, ziel, aus, "pw"))
        finally:
            dateien.BLOCK = echte_blockgroesse

        # ------------------------------------------------ Ganzer Ordner
        baum = basis / "Projekt"
        (baum / "unterordner" / "tiefer").mkdir(parents=True)
        (baum / "notiz.txt").write_text("oben äöü", encoding="utf-8")
        (baum / "unterordner" / "bild.bin").write_bytes(os.urandom(3000))
        (baum / "unterordner" / "tiefer" / "leer.txt").write_text("", encoding="utf-8")

        paket = dateien.verschluesseln(baum, basis / "Projekt.vp4", "ordnerpw")
        zurueck = dateien.entschluesseln(paket, aus, "ordnerpw")
        gefunden = sorted(p.relative_to(zurueck).as_posix()
                          for p in zurueck.rglob("*") if p.is_file())
        R.pruefe("Ein ganzer Ordner kommt mit allen Dateien zurück",
                 gefunden == ["notiz.txt", "unterordner/bild.bin",
                              "unterordner/tiefer/leer.txt"],
                 f"gefunden: {gefunden}")
        R.pruefe("Auch die Dateien tief im Ordner sind unverändert",
                 (zurueck / "unterordner" / "bild.bin").read_bytes()
                 == (baum / "unterordner" / "bild.bin").read_bytes()
                 and (zurueck / "notiz.txt").read_text(encoding="utf-8") == "oben äöü")
        R.pruefe("Das Zwischen-ZIP wird wieder weggeräumt",
                 not any(p.name.endswith(".vp4zip") for p in aus.iterdir()))

        # ------------------------------------ Abbruch und Fortschritt
        quelle = basis / "gross.bin"
        quelle.write_bytes(os.urandom(3_000_000))

        stand = []
        dateien.verschluesseln(quelle, basis / "mit_anzeige.vp4", "pw",
                               fortschritt=lambda getan, gesamt: stand.append((getan, gesamt)))
        R.pruefe("Der Fortschritt wird gemeldet und läuft bis zum Ende",
                 len(stand) >= 3 and stand[-1][0] == stand[-1][1] == 3_000_000,
                 f"letzter Stand: {stand[-1] if stand else None}")

        halt = threading.Event()

        def bei_fortschritt(getan, gesamt):
            if getan > 0:
                halt.set()

        ziel = basis / "abgebrochen.vp4"
        try:
            dateien.verschluesseln(quelle, ziel, "pw",
                                   fortschritt=bei_fortschritt, abbruch=halt)
            R.pruefe("Ein Abbruch bricht wirklich ab", False, "kein Abbruch")
        except dateien.AbgebrochenError:
            R.pruefe("Ein Abbruch bricht wirklich ab", True)
        R.pruefe("Nach einem Abbruch bleibt keine halbe Datei liegen",
                 not ziel.exists()
                 and not any(p.name.endswith(".unfertig") for p in basis.iterdir()))

        # ------------------------------- Nichts wird stillschweigend überschrieben
        quelle = basis / "doppelt.txt"
        quelle.write_text("erste Fassung", encoding="utf-8")
        paket = dateien.verschluesseln(quelle, basis / "doppelt.vp4", "pw")
        erste = dateien.entschluesseln(paket, aus, "pw")
        zweite = dateien.entschluesseln(paket, aus, "pw")
        R.pruefe("Beim zweiten Entschlüsseln wird nichts überschrieben",
                 erste != zweite and erste.exists() and zweite.exists(),
                 f"{erste.name} / {zweite.name}")


# ---------------------------------------------------------------------------
#  7) Obsidian
# ---------------------------------------------------------------------------

def test_obsidian():
    print("\n=== Obsidian-Verknüpfung ===")
    with tempfile.TemporaryDirectory() as vault:
        try:
            sync = ObsidianSync(vault)
            privat, oeffentlich = ModernCrypto.generate_rsa_keypair()

            schluessel = [
                {"label": "Mein AES", "typ": "AES",
                 "wert": ModernCrypto.generate_aes_key(), "meta": "kurzer Schlüssel"},
                # Der harte Fall: rund 1700 Zeichen mit vielen Zeilenumbrüchen.
                # Genau daran ist der Export früher gescheitert - er hat bei
                # 120 Zeichen abgeschnitten und den Schlüssel unbrauchbar gemacht.
                {"label": "RSA privat", "typ": "RSA-priv",
                 "wert": privat, "meta": "langer Schlüssel"},
                # Ein "|" würde die Markdown-Tabelle zerreißen.
                {"label": "Mit Sonderzeichen", "typ": "Text",
                 "wert": "a|b\\c\nzweite Zeile", "meta": "Notiz mit | Strich"},
            ]

            notiz = Path(sync.export_keys(schluessel))
            R.pruefe("Export legt die Notiz an", notiz.exists())

            with open(notiz, "a", encoding="utf-8") as f:
                f.write("\n\nDas hier habe ich selbst geschrieben.\n")
            sync.export_keys(schluessel)
            R.pruefe("Eigener Notiztext überlebt einen erneuten Export",
                     "Das hier habe ich selbst geschrieben."
                     in notiz.read_text(encoding="utf-8"))

            zurueck = {k["label"]: k for k in sync.import_keys()}
            R.pruefe("Import liest alle Schlüssel wieder ein",
                     len(zurueck) == len(schluessel),
                     f"erwartet {len(schluessel)}, bekam {len(zurueck)}")

            for original in schluessel:
                gelesen = zurueck.get(original["label"], {})
                R.pruefe(f"Roundtrip: {original['label']} kommt vollständig zurück",
                         gelesen.get("wert") == original["wert"],
                         f"{len(original['wert'])} Zeichen rein, "
                         f"{len(gelesen.get('wert', ''))} zurück")
                R.pruefe(f"Roundtrip: Notiz von {original['label']} bleibt erhalten",
                         gelesen.get("meta") == original["meta"])

            # Der wichtigste Test: lässt sich mit dem Schlüssel, der aus
            # Obsidian zurückkam, wirklich noch entschlüsseln?
            geheim = ModernCrypto.rsa_encrypt("Geheime Nachricht", oeffentlich)
            R.pruefe("Der zurückgelesene RSA-Schlüssel funktioniert noch",
                     ModernCrypto.rsa_decrypt(
                         geheim, zurueck["RSA privat"]["wert"]) == "Geheime Nachricht")
        except Exception as e:
            R.fehlschlag("Obsidian", e)
            traceback.print_exc()

    # Ein leerer Vault-Pfad muss abgelehnt werden. Path("") ist in Python das
    # aktuelle Verzeichnis und besteht is_dir() klaglos - dadurch hat ein
    # Export mit leerem Feld die Schlüsseldatei einmal in den Programmordner
    # geschrieben, wo sie beim nächsten Hochladen auf GitHub öffentlich
    # geworden wäre.
    for leer in ("", "   ", None):
        try:
            ObsidianSync(leer)
            R.pruefe(f"Leerer Vault-Pfad ({leer!r}) wird abgelehnt", False,
                     "wurde angenommen - Schlüssel landen im Programmordner!")
        except ValueError:
            R.pruefe(f"Leerer Vault-Pfad ({leer!r}) wird abgelehnt", True)
        except Exception as e:
            R.pruefe(f"Leerer Vault-Pfad ({leer!r}) wird abgelehnt", False,
                     f"falsche Fehlerart: {type(e).__name__}: {e}")

    # Im Projektordner darf keine exportierte Schlüsseldatei liegen.
    verirrt = list(Path(__file__).resolve().parent.glob("*Schluessel*.md"))
    R.pruefe("Keine Schlüsseldatei im Programmordner",
             not verirrt,
             f"gefunden: {[p.name for p in verirrt]} - gehört in den Vault, nicht hierher")

    # Notiz aus einer alten Programmversion: abgeschnittene Schlüssel müssen
    # gemeldet werden, statt still kaputt zurückzukommen.
    with tempfile.TemporaryDirectory() as vault:
        try:
            (Path(vault) / ObsidianSync.NOTE_NAME).write_text(
                "| Name | Typ | Wert | Notiz | Erstellt |\n"
                "|---|---|---|---|---|\n"
                "| Alter RSA | RSA-priv | `-----BEGIN PRIVATE KEY----- MIIEvAIBAD...` "
                "| x | 2026-01-01 |\n", encoding="utf-8")
            ObsidianSync(vault).import_keys()
            R.pruefe("Abgeschnittener Schlüssel aus alter Version wird gemeldet",
                     False, "wurde stillschweigend übernommen!")
        except ValueError:
            R.pruefe("Abgeschnittener Schlüssel aus alter Version wird gemeldet", True)
        except Exception as e:
            R.fehlschlag("Warnung bei alter Notiz", e)


# ---------------------------------------------------------------------------
#  8) Verfahrensliste
# ---------------------------------------------------------------------------
#
# Chat, Discord und Gruppen von VP4 4.x sind mit dem Umbau auf VP4 5 durch
# neue Module ersetzt - ihre Prüfungen stehen jetzt in tests/test_bote.py,
# tests/test_netz.py und tests/test_dienst.py (und laufen hier mit).

def test_verfahrensliste():
    print("\n=== Verfahrensliste (davon lebt die Auswahl in der Oberfläche) ===")
    R.pruefe(f"Es sind {len(VERFAHREN)} Verfahren eingetragen", len(VERFAHREN) >= 13)

    unvollstaendig = [n for n, i in VERFAHREN.items()
                      if not all(f in i for f in ("enc", "dec", "art", "key", "hinweis"))]
    R.pruefe("Jedes Verfahren hat alle nötigen Angaben",
             not unvollstaendig, f"unvollständig: {unvollstaendig}")

    unbekannt = [n for n, i in VERFAHREN.items() if i["key"] not in SCHLUESSEL_ARTEN]
    R.pruefe("Jedes Verfahren verweist auf eine bekannte Schlüsselart",
             not unbekannt, f"unbekannt: {unbekannt}")

    falsche_art = [n for n, i in VERFAHREN.items() if i["art"] not in ("sicher", "spiel")]
    R.pruefe("Jedes Verfahren ist als 'sicher' oder 'spiel' eingestuft",
             not falsche_art, f"falsch: {falsche_art}")

    # Jedes Verfahren muss über die Liste tatsächlich aufrufbar sein - genau
    # so ruft die Oberfläche es später auf.
    passende_schluessel = {
        "aes": ModernCrypto.generate_aes_key(),
        "chacha": ModernCrypto.generate_chacha_key(),
        "passwort": "Testpasswort",
        "zahl": "3", "wort": "SCHLUESSEL", "keiner": "",
    }
    fehler = []
    for name, info in VERFAHREN.items():
        if info["key"] in ("rsa", "pq", "age"):   # Schlüsselpaare; PQ und age prüft tests/test_krypto_neu.py
            continue                       # braucht zwei verschiedene Schlüssel
        try:
            info["enc"]("Testtext ABC", passende_schluessel[info["key"]])
        except Exception as e:
            fehler.append(f"{name}: {type(e).__name__}: {e}")
    R.pruefe("Jedes Verfahren lässt sich über die Liste aufrufen",
             not fehler, "\n         ".join(fehler))


# ---------------------------------------------------------------------------

def weitere_pruefungen():
    """Führt alle Prüfmodule aus tests/ aus (tests/test_*.py).

    Jedes Modul hat eine Funktion pruefen(R, hilfen) und meldet seine
    Ergebnisse über dasselbe R wie hier - so bleibt `python test_vp4.py`
    der eine Befehl für alles, und die Datei hier wächst nicht weiter.
    Stürzt ein Modul ab, zählt das als Fehlschlag statt alles abzubrechen.
    """
    import importlib.util
    hilfen = {"wirft_valueerror": _wirft_valueerror, "wirft_fehler": _wirft_fehler}
    for pfad in sorted((Path(__file__).resolve().parent / "tests").glob("test_*.py")):
        print(f"\n=== {pfad.stem} ===")
        try:
            spec = importlib.util.spec_from_file_location(pfad.stem, pfad)
            modul = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(modul)
            modul.pruefen(R, hilfen)
        except Exception as e:
            traceback.print_exc()
            R.fehlschlag(f"{pfad.name} lief nicht durch", e)


def main():
    print("=" * 64)
    print(" Selbsttest für VP4 5")
    print("=" * 64)

    test_klassische_verfahren()
    test_moderne_verfahren()
    test_signaturen()
    test_schluesselspeicher()
    test_formatversionen()
    test_dateien()
    test_obsidian()
    test_verfahrensliste()
    weitere_pruefungen()

    print("\n" + "=" * 64)
    if R.fehler:
        print(f" ERGEBNIS: {R.ok} bestanden, {len(R.fehler)} FEHLGESCHLAGEN")
        print("\n Fehlgeschlagen sind:")
        for name in R.fehler:
            print(f"   - {name}")
        print("=" * 64)
        return 1

    print(f" ERGEBNIS: alle {R.ok} Prüfungen bestanden.")
    print("=" * 64)
    return 0


if __name__ == "__main__":
    sys.exit(main())
