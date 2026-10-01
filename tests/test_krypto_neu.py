# -*- coding: utf-8 -*-
"""Prüfungen für die neuen Verfahren aus kern/krypto_neu.py.

Wird von test_vp4.py aufgerufen (weitere_pruefungen) und meldet über
dasselbe R. Für jedes neue Verfahren gilt dasselbe Pflichtprogramm:

  - hin und zurück, mit Umlauten, ß und Emoji
  - leerer Text geht
  - zweimal derselbe Text ergibt zwei verschiedene Geheimtexte
    (sonst wäre das Nonce nicht zufällig)
  - falscher Schlüssel, ein gekipptes Byte, abgeschnittene Eingabe:
    jeweils ein ValueError - und zwar ein ValueError, kein InvalidTag,
    CryptoError oder DecryptError. Die Oberfläche kennt nur ValueError.

Dazu kommt bei age der Abgleich mit dem offiziellen Programm `age`, falls
es auf dem Rechner liegt - sonst wird der Teil still übersprungen.
"""

import base64
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

_WURZEL = Path(__file__).resolve().parent.parent
if str(_WURZEL) not in sys.path:
    sys.path.insert(0, str(_WURZEL))

from kern import krypto_neu as neu                        # noqa: E402
from kern.krypto import VERFAHREN, SCHLUESSEL_ARTEN, ModernCrypto   # noqa: E402


TEXT = "Grüße äöüß ÄÖÜ 123 – Ende 🔒"


# ---------------------------------------------------------------------------
#  Werkzeuge zum Verderben von Geheimtext
# ---------------------------------------------------------------------------

def _b64_kippen(text: str, stelle: int = -1) -> str:
    roh = bytearray(base64.b64decode(text))
    roh[stelle] ^= 0x01
    return base64.b64encode(bytes(roh)).decode("ascii")


def _b64_kuerzen(text: str, laenge: int) -> str:
    return base64.b64encode(base64.b64decode(text)[:laenge]).decode("ascii")


def _pq_kippen(text: str, stelle: int) -> str:
    kopf = neu.PQ_GEHEIMTEXT_PREFIX
    return kopf + _b64_kippen(text[len(kopf):], stelle)


def _pq_kuerzen(text: str, laenge: int) -> str:
    kopf = neu.PQ_GEHEIMTEXT_PREFIX
    return kopf + _b64_kuerzen(text[len(kopf):], laenge)


def _age_entpanzern(text: str) -> bytes:
    zeilen = text.strip().splitlines()
    assert zeilen[0] == neu.AGE_ANFANG and zeilen[-1] == neu.AGE_ENDE
    return base64.b64decode("".join(zeilen[1:-1]))


def _age_panzern(roh: bytes) -> str:
    b64 = base64.b64encode(roh).decode("ascii")
    zeilen = [b64[i:i + 64] for i in range(0, len(b64), 64)]
    return "\n".join([neu.AGE_ANFANG] + zeilen + [neu.AGE_ENDE]) + "\n"


def _age_kippen(text: str, stelle: int = -1) -> str:
    """Kippt genau ein Bit in den Daten - die Panzerung bleibt gültig."""
    roh = bytearray(_age_entpanzern(text))
    roh[stelle] ^= 0x01
    return _age_panzern(bytes(roh))


def _age_kuerzen(text: str, weg: int) -> str:
    """Schneidet hinten Daten ab - die Panzerung bleibt gültig."""
    return _age_panzern(_age_entpanzern(text)[:-weg])


# ---------------------------------------------------------------------------
#  Pflichtprogramm für jedes Text-Verfahren
# ---------------------------------------------------------------------------

def _pflichtprogramm(R, wve, name, enc, dec, k_enc, k_dec, k_falsch,
                     kippen, kuerzen):
    """Die sechs Prüfungen, die jedes neue Verfahren bestehen muss."""
    try:
        g1 = enc(TEXT, k_enc)
        R.pruefe(f"{name}: hin und zurück mit Umlauten und Emoji",
                 dec(g1, k_dec) == TEXT)

        leer = enc("", k_enc)
        R.pruefe(f"{name}: leerer Text geht hin und zurück", dec(leer, k_dec) == "")

        # Nur vergleichen, nicht nochmal entschlüsseln: bei age mit Passwort
        # kostet jedes Entschlüsseln absichtlich ein, zwei Sekunden.
        R.pruefe(f"{name}: derselbe Text ergibt jedes Mal einen anderen Geheimtext",
                 g1 != enc(TEXT, k_enc))

        R.pruefe(f"{name}: falscher Schlüssel wird mit ValueError abgelehnt",
                 wve(dec, g1, k_falsch))
        R.pruefe(f"{name}: ein gekipptes Byte wird mit ValueError abgelehnt",
                 wve(dec, kippen(g1), k_dec))
        R.pruefe(f"{name}: abgeschnittener Geheimtext wird mit ValueError abgelehnt",
                 wve(dec, kuerzen(g1), k_dec))
        return g1
    except Exception as e:
        R.fehlschlag(f"{name}: Pflichtprogramm lief nicht durch", e)
        return None


# ---------------------------------------------------------------------------
#  Die einzelnen Verfahren
# ---------------------------------------------------------------------------

def _xchacha(R, wve):
    k1, k2 = neu.xchacha_schluessel_erzeugen(), neu.xchacha_schluessel_erzeugen()
    R.pruefe("XChaCha20: erzeugter Schlüssel ist 32 Byte als Base64 (44 Zeichen)",
             len(k1) == 44 and len(base64.b64decode(k1)) == 32 and k1 != k2)
    g = _pflichtprogramm(
        R, wve, "XChaCha20", neu.xchacha_verschluesseln, neu.xchacha_entschluesseln,
        k1, k1, k2, kippen=_b64_kippen, kuerzen=lambda t: _b64_kuerzen(t, 30))
    if g is None:
        return
    roh = base64.b64decode(g)
    laenge = len(TEXT.encode("utf-8"))
    R.pruefe("XChaCha20: Marke VP4X1, 24-Byte-Nonce, 16-Byte-Siegel",
             roh.startswith(b"VP4X1") and len(roh) == 5 + 24 + laenge + 16)
    R.pruefe("XChaCha20: gekipptes Byte im Nonce fällt auch auf",
             wve(neu.xchacha_entschluesseln, _b64_kippen(g, 6), k1))
    R.pruefe("XChaCha20: zu kurzer Schlüssel (16 Byte) wird abgelehnt",
             wve(neu.xchacha_verschluesseln, "x",
                 base64.b64encode(os.urandom(16)).decode()))
    R.pruefe("XChaCha20: leerer Schlüssel wird abgelehnt",
             wve(neu.xchacha_verschluesseln, "x", ""))
    R.pruefe("XChaCha20: Geheimtext von ChaCha20 (12-Byte-Nonce) wird abgelehnt",
             wve(neu.xchacha_entschluesseln, ModernCrypto.chacha_encrypt("x", k1), k1))
    R.pruefe("XChaCha20: Unsinn statt Base64 wird abgelehnt",
             wve(neu.xchacha_entschluesseln, "das ist kein base64 !!!", k1))


def _gcmsiv(R, wve):
    k1, k2 = neu.gcmsiv_schluessel_erzeugen(), neu.gcmsiv_schluessel_erzeugen()
    R.pruefe("GCM-SIV: erzeugter Schlüssel ist 32 Byte als Base64 (44 Zeichen)",
             len(k1) == 44 and len(base64.b64decode(k1)) == 32 and k1 != k2)
    g = _pflichtprogramm(
        R, wve, "GCM-SIV", neu.gcmsiv_verschluesseln, neu.gcmsiv_entschluesseln,
        k1, k1, k2, kippen=_b64_kippen, kuerzen=lambda t: _b64_kuerzen(t, 20))
    if g is None:
        return
    roh = base64.b64decode(g)
    laenge = len(TEXT.encode("utf-8"))
    R.pruefe("GCM-SIV: Marke VP4S1, 12-Byte-Nonce, 16-Byte-Siegel",
             roh.startswith(b"VP4S1") and len(roh) == 5 + 12 + laenge + 16)
    R.pruefe("GCM-SIV: ein AES-GCM-Geheimtext wird nicht für GCM-SIV gehalten",
             wve(neu.gcmsiv_entschluesseln, ModernCrypto.aes_encrypt("x", k1), k1))
    R.pruefe("GCM-SIV: 16-Byte-Schlüssel wird abgelehnt (es heißt AES-256)",
             wve(neu.gcmsiv_verschluesseln, "x",
                 base64.b64encode(os.urandom(16)).decode()))
    R.pruefe("GCM-SIV: verdrehte Marke fällt auf (sie ist mitversiegelt)",
             wve(neu.gcmsiv_entschluesseln, _b64_kippen(g, 0), k1))


def _pq(R, wve):
    privat, oeffentlich = neu.pq_schluesselpaar()
    privat2, oeffentlich2 = neu.pq_schluesselpaar()
    R.pruefe("PQ: öffentlicher Schlüssel beginnt mit VP4PQ1-",
             oeffentlich.startswith("VP4PQ1-") and not oeffentlich.startswith("VP4PQS1-"))
    R.pruefe("PQ: privater Schlüssel beginnt mit VP4PQS1-",
             privat.startswith("VP4PQS1-"))
    R.pruefe("PQ: zwei Schlüsselpaare sind verschieden",
             privat != privat2 and oeffentlich != oeffentlich2)
    R.pruefe("PQ: Schlüssel sind Base64url ohne Auffüllzeichen",
             all(c.isalnum() or c in "-_" for c in oeffentlich[7:] + privat[8:]))

    g = _pflichtprogramm(
        R, wve, "PQ", neu.pq_verschluesseln, neu.pq_entschluesseln,
        oeffentlich, privat, privat2,
        kippen=lambda t: _pq_kippen(t, -1), kuerzen=lambda t: _pq_kuerzen(t, 600))
    if g is None:
        return
    R.pruefe("PQ: Geheimtext beginnt mit VP4Q1|", g.startswith("VP4Q1|"))
    R.pruefe("PQ: mit einem anderen Schlüsselpaar geht nichts auf (ValueError)",
             wve(neu.pq_entschluesseln, g, privat2)
             and neu.pq_entschluesseln(neu.pq_verschluesseln(TEXT, oeffentlich2),
                                       privat2) == TEXT)
    R.pruefe("PQ: gekipptes Byte im gekapselten Schlüssel fällt auf",
             wve(neu.pq_entschluesseln, _pq_kippen(g, 5), privat))
    R.pruefe("PQ: gekipptes Byte im X25519-Teil der Kapsel fällt auf",
             wve(neu.pq_entschluesseln, _pq_kippen(g, 1100), privat))
    R.pruefe("PQ: privater Schlüssel zum Verschlüsseln wird abgelehnt",
             wve(neu.pq_verschluesseln, TEXT, privat))
    R.pruefe("PQ: öffentlicher Schlüssel zum Entschlüsseln wird abgelehnt",
             wve(neu.pq_entschluesseln, g, oeffentlich))
    R.pruefe("PQ: abgeschnittener öffentlicher Schlüssel wird abgelehnt",
             wve(neu.pq_verschluesseln, TEXT, oeffentlich[:-10]))
    R.pruefe("PQ: Text ohne VP4Q1| wird abgelehnt",
             wve(neu.pq_entschluesseln, g[len("VP4Q1|"):], privat))
    R.pruefe("PQ: Zeilenumbrüche im kopierten Schlüssel stören nicht",
             neu.pq_entschluesseln(
                 neu.pq_verschluesseln("x", oeffentlich[:80] + "\n" + oeffentlich[80:]),
                 privat[:40] + "\r\n" + privat[40:]) == "x")


def _age_text(R, wve):
    privat, oeffentlich = neu.age_schluesselpaar()
    privat2, oeffentlich2 = neu.age_schluesselpaar()
    R.pruefe("age: Schlüsselpaar hat die Form von age-keygen",
             oeffentlich.startswith("age1")
             and privat.startswith("AGE-SECRET-KEY-1") and privat != privat2)

    # -- mit Passwort
    g = _pflichtprogramm(
        R, wve, "age (Passwort)", neu.age_passwort_verschluesseln,
        neu.age_passwort_entschluesseln, "geheim 123", "geheim 123", "falsch",
        kippen=_age_kippen, kuerzen=lambda t: _age_kuerzen(t, 5))
    if g is not None:
        R.pruefe("age (Passwort): gepanzert, beginnt mit der BEGIN-Zeile",
                 g.startswith("-----BEGIN AGE ENCRYPTED FILE-----\n")
                 and g.rstrip().endswith("-----END AGE ENCRYPTED FILE-----"))
        R.pruefe("age (Passwort): Windows-Zeilenenden stören nicht",
                 neu.age_passwort_entschluesseln(g.replace("\n", "\r\n"),
                                                 "geheim 123") == TEXT)
        R.pruefe("age (Passwort): halb kopierter Text wird abgelehnt",
                 wve(neu.age_passwort_entschluesseln, g[:len(g) // 2], "geheim 123"))
        R.pruefe("age (Passwort): Passwort-Text mit Schlüssel öffnen gibt ValueError",
                 wve(neu.age_entschluesseln, g, privat))
    R.pruefe("age (Passwort): leeres Passwort wird abgelehnt",
             wve(neu.age_passwort_verschluesseln, TEXT, ""))

    # -- mit Schlüsselpaar
    g = _pflichtprogramm(
        R, wve, "age (Schlüsselpaar)", neu.age_verschluesseln,
        neu.age_entschluesseln, oeffentlich, privat, privat2,
        kippen=_age_kippen, kuerzen=lambda t: _age_kuerzen(t, 5))
    if g is None:
        return
    R.pruefe("age (Schlüsselpaar): gepanzert, beginnt mit der BEGIN-Zeile",
             g.startswith(neu.AGE_ANFANG + "\n"))
    beide = neu.age_verschluesseln(TEXT, f"{oeffentlich}, {oeffentlich2}")
    R.pruefe("age (Schlüsselpaar): mehrere Empfänger können jeder für sich öffnen",
             neu.age_entschluesseln(beide, privat) == TEXT
             and neu.age_entschluesseln(beide, privat2) == TEXT)
    keygen_datei = (f"# created: 2026-10-01T12:00:00+02:00\n"
                    f"# public key: {oeffentlich}\n{privat}\n")
    R.pruefe("age (Schlüsselpaar): Inhalt einer age-keygen-Datei wird verstanden",
             neu.age_entschluesseln(g, keygen_datei) == TEXT)
    R.pruefe("age (Schlüsselpaar): privater Schlüssel als Empfänger wird abgelehnt",
             wve(neu.age_verschluesseln, TEXT, privat))
    R.pruefe("age (Schlüsselpaar): öffentlicher Schlüssel zum Öffnen wird abgelehnt",
             wve(neu.age_entschluesseln, g, oeffentlich))
    R.pruefe("age (Schlüsselpaar): vertippter Schlüssel wird abgelehnt",
             wve(neu.age_verschluesseln, TEXT, oeffentlich[:-3] + "qqq"))
    R.pruefe("age (Schlüsselpaar): Schlüssel-Text mit Passwort öffnen gibt ValueError",
             wve(neu.age_passwort_entschluesseln, g, "geheim 123"))
    R.pruefe("age: Text ohne BEGIN-Zeile wird abgelehnt",
             wve(neu.age_entschluesseln, "hallo", privat))


def _age_dateien(R, wve):
    privat, oeffentlich = neu.age_schluesselpaar()
    privat2, _ = neu.age_schluesselpaar()
    inhalt = os.urandom(200_000) + "äöü 🔒".encode("utf-8")

    with tempfile.TemporaryDirectory() as ordner:
        o = Path(ordner)
        quelle = o / "Grüße äöü.bin"
        quelle.write_bytes(inhalt)

        # -- Schlüsselpaar (gestreamt)
        paket = neu.age_datei_verschluesseln(quelle, o / "paket.age",
                                             empfaenger=oeffentlich)
        R.pruefe("age-Datei (Schlüsselpaar): .age entsteht, binär wie bei age",
                 paket.exists() and paket.read_bytes().startswith(b"age-encryption.org/v1"))
        zurueck = neu.age_datei_entschluesseln(paket, o / "zurueck.bin",
                                               identitaet=privat)
        R.pruefe("age-Datei (Schlüsselpaar): Byte für Byte zurück",
                 zurueck.read_bytes() == inhalt)
        R.pruefe("age-Datei (Schlüsselpaar): falscher Schlüssel gibt ValueError",
                 wve(neu.age_datei_entschluesseln, paket, o / "x1.bin", None, privat2)
                 and not (o / "x1.bin").exists())

        kaputt = bytearray(paket.read_bytes())
        kaputt[-100] ^= 0x01
        (o / "kaputt.age").write_bytes(bytes(kaputt))
        R.pruefe("age-Datei: gekipptes Byte gibt ValueError, und nichts bleibt liegen",
                 wve(neu.age_datei_entschluesseln, o / "kaputt.age", o / "x2.bin",
                     None, privat)
                 and not (o / "x2.bin").exists()
                 and not (o / "x2.bin.unfertig").exists())
        (o / "kurz.age").write_bytes(paket.read_bytes()[:-1000])
        R.pruefe("age-Datei: abgeschnittene Datei gibt ValueError, nichts bleibt liegen",
                 wve(neu.age_datei_entschluesseln, o / "kurz.age", o / "x3.bin",
                     None, privat)
                 and not (o / "x3.bin").exists())

        # -- Passwort (im Arbeitsspeicher)
        paket_pw = neu.age_datei_verschluesseln(quelle, o / "pw.age", passwort="pw 1")
        zurueck_pw = neu.age_datei_entschluesseln(paket_pw, o / "pw.bin", passwort="pw 1")
        R.pruefe("age-Datei (Passwort): Byte für Byte zurück",
                 zurueck_pw.read_bytes() == inhalt)
        R.pruefe("age-Datei (Passwort): falsches Passwort gibt ValueError",
                 wve(neu.age_datei_entschluesseln, paket_pw, o / "x4.bin", "falsch")
                 and not (o / "x4.bin").exists())

        R.pruefe("age-Datei: Passwort UND Schlüssel zugleich wird abgelehnt",
                 wve(neu.age_datei_verschluesseln, quelle, o / "x5.age", "pw",
                     oeffentlich))
        R.pruefe("age-Datei: weder Passwort noch Schlüssel wird abgelehnt",
                 wve(neu.age_datei_verschluesseln, quelle, o / "x6.age"))
        R.pruefe("age-Datei: Quelle gleich Ziel wird abgelehnt",
                 wve(neu.age_datei_verschluesseln, quelle, quelle, "pw"))

        # Die 512-MB-Grenze: statt eine riesige Datei zu schreiben, wird die
        # Grenze für die Dauer der Prüfung heruntergesetzt.
        alt = neu.AGE_PASSWORT_DATEI_GRENZE
        try:
            neu.AGE_PASSWORT_DATEI_GRENZE = 1000
            R.pruefe("age-Datei (Passwort): zu große Datei wird mit ValueError abgelehnt",
                     wve(neu.age_datei_verschluesseln, quelle, o / "x7.age", "pw")
                     and not (o / "x7.age").exists())
            gross = neu.age_datei_verschluesseln(quelle, o / "gross.age",
                                                 empfaenger=oeffentlich)
            R.pruefe("age-Datei (Schlüsselpaar): für sie gilt die Grenze nicht (gestreamt)",
                     gross.exists())
        finally:
            neu.AGE_PASSWORT_DATEI_GRENZE = alt

        _age_gegen_offizielles_programm(R, o, privat, oeffentlich, paket,
                                        paket_pw, inhalt)


# ---------------------------------------------------------------------------
#  Abgleich mit dem offiziellen Programm age
# ---------------------------------------------------------------------------

def _age_mit_passwort(argumente, passwort):
    """Ruft `age` in einem Pseudo-Terminal auf und tippt das Passwort ein.

    age liest Passwörter absichtlich nur vom Terminal, nie aus einer Pipe.
    Pseudo-Terminals gibt es nur auf Linux/macOS - auf Windows wird dieser
    Teil übersprungen (None).
    """
    try:
        import pty
        import select
    except ImportError:
        return None
    if not hasattr(os, "fork"):
        return None
    pid, fd = pty.fork()
    if pid == 0:                                          # pragma: no cover
        try:
            os.execvp("age", ["age"] + argumente)
        finally:
            os._exit(127)
    ausgabe, getippt, frist = b"", 0, time.time() + 30
    try:
        while time.time() < frist:
            bereit, _, _ = select.select([fd], [], [], 0.5)
            if not bereit:
                continue
            try:
                stueck = os.read(fd, 4096)
            except OSError:
                break
            if not stueck:
                break
            ausgabe += stueck
            if ausgabe.lower().count(b"passphrase") > getippt:
                os.write(fd, passwort.encode("utf-8") + b"\n")
                getippt += 1
    finally:
        os.close(fd)
    _, status = os.waitpid(pid, 0)
    return os.waitstatus_to_exitcode(status)


def _age_gegen_offizielles_programm(R, o, privat, oeffentlich, paket, paket_pw,
                                    inhalt):
    if not shutil.which("age"):
        return                    # kein age installiert: still überspringen

    schluesseldatei = o / "schluessel.txt"
    schluesseldatei.write_text(privat + "\n", encoding="ascii")

    lauf = subprocess.run(["age", "-d", "-i", str(schluesseldatei),
                           "-o", str(o / "cli.bin"), str(paket)],
                          capture_output=True, timeout=60)
    R.pruefe("age-Programm öffnet eine Datei, die VP4 verschlüsselt hat",
             lauf.returncode == 0 and (o / "cli.bin").read_bytes() == inhalt,
             lauf.stderr.decode(errors="replace"))

    lauf = subprocess.run(["age", "-r", oeffentlich, "-a"],
                          input=TEXT.encode("utf-8"), capture_output=True, timeout=60)
    R.pruefe("VP4 öffnet einen Text, den das age-Programm verschlüsselt hat",
             lauf.returncode == 0
             and neu.age_entschluesseln(lauf.stdout.decode("ascii"), privat) == TEXT,
             lauf.stderr.decode(errors="replace"))

    # Passwort in beide Richtungen - nur wo es ein Pseudo-Terminal gibt.
    code = _age_mit_passwort(["-d", "-o", str(o / "cli_pw.bin"), str(paket_pw)],
                             "pw 1")
    if code is None:
        return
    R.pruefe("age-Programm öffnet eine Passwort-Datei von VP4",
             code == 0 and (o / "cli_pw.bin").read_bytes() == inhalt,
             f"Rückgabe {code}")

    (o / "klar.txt").write_text(TEXT, encoding="utf-8")
    code = _age_mit_passwort(["-p", "-a", "-o", str(o / "cli_pw.age"),
                              str(o / "klar.txt")], "pw äöü")
    R.pruefe("VP4 öffnet einen Passwort-Text vom age-Programm",
             code == 0 and neu.age_passwort_entschluesseln(
                 (o / "cli_pw.age").read_text(encoding="ascii"), "pw äöü") == TEXT,
             f"Rückgabe {code}")


# ---------------------------------------------------------------------------
#  Eintrag in der Verfahrensliste
# ---------------------------------------------------------------------------

NEUE_NAMEN = ("XChaCha20-Poly1305", "AES-256-GCM-SIV", "Post-Quanten (Hybrid)",
              "age (Passwort)", "age (Schlüsselpaar)")


def _verfahrensliste(R):
    verfuegbar = {
        "XChaCha20-Poly1305": neu.xchacha_verfuegbar(),
        "AES-256-GCM-SIV": neu.gcmsiv_verfuegbar(),
        "Post-Quanten (Hybrid)": neu.pq_verfuegbar(),
        "age (Passwort)": neu.age_verfuegbar(),
        "age (Schlüsselpaar)": neu.age_verfuegbar(),
    }
    R.pruefe("Alle neuen Verfahren sind hier verfügbar (PyNaCl, pyrage, cryptography>=50)",
             all(verfuegbar.values()),
             f"fehlt: {[n for n, v in verfuegbar.items() if not v]}")
    R.pruefe("Genau die verfügbaren neuen Verfahren stehen in der Liste",
             all((n in VERFAHREN) == v for n, v in verfuegbar.items()))

    neue = {n: VERFAHREN[n] for n in NEUE_NAMEN if n in VERFAHREN}
    R.pruefe("Neue Einträge haben enc, dec, art, key und hinweis",
             all(all(f in i for f in ("enc", "dec", "art", "key", "hinweis"))
                 for i in neue.values()))
    R.pruefe("Neue Einträge sind 'sicher' und haben eine bekannte Schlüsselart",
             all(i["art"] == "sicher" and i["key"] in SCHLUESSEL_ARTEN
                 for i in neue.values()))

    # Genau so, wie die Oberfläche es tut: über die Liste, mit dem Schlüssel,
    # den die Schlüsselart verlangt - und diesmal auch wieder zurück.
    pq_privat, pq_oeff = neu.pq_schluesselpaar()
    age_privat, age_oeff = neu.age_schluesselpaar()
    aes, chacha = ModernCrypto.generate_aes_key(), ModernCrypto.generate_chacha_key()
    schluessel = {
        "aes": (aes, aes), "chacha": (chacha, chacha),
        "passwort": ("Testpasswort", "Testpasswort"),
        "pq": (pq_oeff, pq_privat), "age": (age_oeff, age_privat),
    }
    fehler = []
    for name, info in neue.items():
        try:
            k_enc, k_dec = schluessel[info["key"]]
            if info["dec"](info["enc"](TEXT, k_enc), k_dec) != TEXT:
                fehler.append(f"{name}: anderer Text zurück")
        except Exception as e:
            fehler.append(f"{name}: {type(e).__name__}: {e}")
    R.pruefe("Jedes neue Verfahren geht über die Liste hin und zurück",
             not fehler, "\n         ".join(fehler))


def _ohne_bibliotheken(R):
    """Fehlen PyNaCl, pyrage oder HPKE, muss krypto.py trotzdem laden.

    In einem eigenen Prozess, damit dieser Lauf hier unberührt bleibt.
    """
    code = (
        "import sys\n"
        "for m in ('nacl', 'nacl.bindings', 'nacl.exceptions', 'pyrage',\n"
        "          'cryptography.hazmat.primitives.hpke'):\n"
        "    sys.modules[m] = None\n"
        "from kern import krypto\n"
        "print('|'.join(krypto.VERFAHREN))\n"
    )
    lauf = subprocess.run([sys.executable, "-c", code], cwd=str(_WURZEL),
                          capture_output=True, timeout=60)
    namen = lauf.stdout.decode("utf-8", errors="replace").strip().split("|")
    R.pruefe("Ohne PyNaCl/pyrage/HPKE lädt krypto.py trotzdem, die Verfahren fehlen nur",
             lauf.returncode == 0 and "AES-256-GCM" in namen
             and not any(n in namen for n in ("XChaCha20-Poly1305",
                                              "Post-Quanten (Hybrid)",
                                              "age (Passwort)", "age (Schlüsselpaar)")),
             lauf.stderr.decode("utf-8", errors="replace")[-500:])


# ---------------------------------------------------------------------------

def pruefen(R, hilfen):
    wve = hilfen["wirft_valueerror"]
    teile = [("Verfahrensliste", lambda: _verfahrensliste(R)),
             ("Ohne Bibliotheken", lambda: _ohne_bibliotheken(R))]
    if neu.xchacha_verfuegbar():
        teile.append(("XChaCha20", lambda: _xchacha(R, wve)))
    if neu.gcmsiv_verfuegbar():
        teile.append(("GCM-SIV", lambda: _gcmsiv(R, wve)))
    if neu.pq_verfuegbar():
        teile.append(("Post-Quanten", lambda: _pq(R, wve)))
    if neu.age_verfuegbar():
        teile.append(("age-Text", lambda: _age_text(R, wve)))
        teile.append(("age-Dateien", lambda: _age_dateien(R, wve)))

    for name, teil in teile:
        try:
            teil()
        except Exception as e:
            R.fehlschlag(f"Neue Verfahren / {name}: Prüfung abgestürzt", e)
