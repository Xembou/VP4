"""
=====================================================================
 dateien.py - Dateien und ganze Ordner verschlüsseln
=====================================================================

Bis hierher konnte VP4 nur Text verschlüsseln, den man ins Fenster
tippt. Dieses Modul macht dasselbe mit Dateien - auch mit sehr grossen,
ohne dass sie dafür in den Arbeitsspeicher passen müssen.

Aufbau einer .vp4-Datei:

    "VP4F1"                         5 Byte   Kennzeichnung
    Schlüsselart                    1 Byte   1 = Passwort, 2 = Schlüssel
    KDF-Kennung + Einstellungen              siehe krypto.kopf_bauen()
    Salt                           16 Byte
    Nonce-Basis                     8 Byte
    Länge des Kopfsatzes            4 Byte
    Kopfsatz                                 verschlüsselt: Name, Grösse, Art
    dann beliebig viele Blöcke:
        Länge                       4 Byte
        Block                                verschlüsselt, je bis 1 MiB

Verschlüsselt wird durchgehend mit AES-256-GCM.

Drei Dinge, die dabei wichtig sind:

1. Jeder Block bekommt sein eigenes Nonce: die 8 Byte Nonce-Basis plus
   die Blocknummer. Ein Nonce zweimal mit demselben Schlüssel zu
   benutzen wäre der klassische Totalschaden bei GCM.

2. In die "zusätzlichen Daten" jedes Blocks gehen die Blocknummer UND
   ein Kennzeichen, ob es der letzte Block ist. Ohne das könnte jemand
   Blöcke vertauschen oder die Datei hinten abschneiden, ohne dass es
   auffällt - jeder einzelne Block wäre ja für sich in Ordnung.

3. Der Dateiname steht im verschlüsselten Kopfsatz, nicht im Klartext.
   Sonst verrät "Zeugnis Halbjahr.pdf.vp4" schon alles Wesentliche.

Ein Ordner wird als ZIP direkt in die Verschlüsselung hineingeschrieben.
Es entsteht zu keinem Zeitpunkt eine unverschlüsselte Zwischendatei -
das wäre genau das Loch, das der ganze Aufwand stopfen soll.
=====================================================================
"""

import json
import os
import struct
import zipfile
from datetime import datetime
from pathlib import Path

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from kern.krypto import ARGON2_STANDARD, KDF_HKDF, ModernCrypto

# Kennzeichnung am Dateianfang.
MARKE = b"VP4F1"
ENDUNG = ".vp4"

# Woher der Schlüssel kommt.
ART_PASSWORT = 1
ART_SCHLUESSEL = 2

# Was drinsteckt.
INHALT_DATEI = "datei"
INHALT_ORDNER = "ordner"

# 1 MiB pro Block. Gross genug, dass der Verwaltungsaufwand nicht ins
# Gewicht fällt, klein genug, dass der Fortschrittsbalken sich flüssig
# bewegt und ein Abbruch schnell greift.
BLOCK = 1024 * 1024


class AbgebrochenError(Exception):
    """Der Benutzer hat den Vorgang abgebrochen."""


# ---------------------------------------------------------------------------
#  Schlüssel für eine einzelne Datei
# ---------------------------------------------------------------------------

def _kdf_fuer(art: int) -> dict:
    if art == ART_PASSWORT:
        return dict(ARGON2_STANDARD)
    if art == ART_SCHLUESSEL:
        return {"kdf": KDF_HKDF}
    raise ValueError(f"Unbekannte Schlüsselart: {art}")


def _dateischluessel(art: int, geheimnis, salt: bytes, kdf: dict) -> bytes:
    """Berechnet den Schlüssel für genau diese eine Datei.

    Bei einem Passwort wird es mit Argon2id durchgerechnet. Kommt der
    Schlüssel dagegen aus dem Schlüsselspeicher, ist er schon zufällig -
    dann mischt HKDF nur noch das Salt der Datei unter, damit nicht alle
    Dateien denselben Schlüssel teilen.
    """
    if art == ART_PASSWORT:
        if not isinstance(geheimnis, str) or not geheimnis:
            raise ValueError("Bitte ein Passwort angeben.")
        return ModernCrypto.schluessel_ableiten(geheimnis, salt, kdf)

    if isinstance(geheimnis, str):
        import base64
        try:
            geheimnis = base64.b64decode(geheimnis.strip())
        except Exception:
            raise ValueError("Der Schlüssel ist kein gültiger Base64-Text.")
    if not isinstance(geheimnis, (bytes, bytearray)) or len(geheimnis) < 16:
        raise ValueError("Der Schlüssel ist zu kurz oder fehlt.")
    return ModernCrypto.schluessel_ableiten(bytes(geheimnis), salt, kdf)


def _block_zusatz(nummer: int, letzter: bool) -> bytes:
    """Die zusätzlichen Daten, mit denen ein Block versiegelt wird."""
    return struct.pack("!IB", nummer, 1 if letzter else 0)


def _nonce(basis: bytes, nummer: int) -> bytes:
    return basis + struct.pack("!I", nummer)


# ---------------------------------------------------------------------------
#  Verschlüsseln
# ---------------------------------------------------------------------------

class _Blockschreiber:
    """Nimmt Daten entgegen und schreibt sie blockweise verschlüsselt weg.

    Der letzte Block muss als solcher gekennzeichnet werden, und ob ein
    Block der letzte ist, weiss man erst, wenn nichts mehr nachkommt.
    Deshalb wird immer ein voller Block zurückgehalten: geschrieben wird
    erst, wenn MEHR als ein Block im Puffer liegt.
    """

    def __init__(self, ziel, schluessel: bytes, nonce_basis: bytes,
                 fortschritt=None, abbruch=None):
        self.ziel = ziel
        self.aes = AESGCM(schluessel)
        self.nonce_basis = nonce_basis
        self.puffer = bytearray()
        self.nummer = 1                 # 0 ist der Kopfsatz
        self.geschrieben = 0            # Klartext-Bytes, für den Fortschritt
        self.fortschritt = fortschritt
        self.abbruch = abbruch

    def write(self, daten: bytes) -> int:
        self.puffer += daten
        while len(self.puffer) > BLOCK:
            self._schreiben(bytes(self.puffer[:BLOCK]), letzter=False)
            del self.puffer[:BLOCK]
        return len(daten)

    def flush(self):
        # Absichtlich nichts tun. zipfile ruft flush() auf, aber der Puffer
        # DARF hier nicht weggeschrieben werden: ob ein Block der letzte
        # ist, steht erst bei close() fest, und der letzte Block muss als
        # solcher versiegelt werden.
        pass

    def close(self):
        # Was übrig ist, ist der letzte Block - notfalls ein leerer, damit
        # auch eine leere Datei einen abschliessenden Block hat.
        self._schreiben(bytes(self.puffer), letzter=True)
        self.puffer = bytearray()

    def _schreiben(self, klar: bytes, letzter: bool):
        if self.abbruch is not None and self.abbruch.is_set():
            raise AbgebrochenError("Abgebrochen.")
        ct = self.aes.encrypt(_nonce(self.nonce_basis, self.nummer), klar,
                              _block_zusatz(self.nummer, letzter))
        self.ziel.write(struct.pack("!I", len(ct)))
        self.ziel.write(ct)
        self.nummer += 1
        self.geschrieben += len(klar)
        if self.fortschritt is not None:
            self.fortschritt(self.geschrieben)


def verschluesseln(quelle, ziel, geheimnis, art: int = ART_PASSWORT,
                   fortschritt=None, abbruch=None) -> Path:
    """Verschlüsselt eine Datei oder einen ganzen Ordner nach `ziel`.

    fortschritt(getan, gesamt) wird zwischendurch aufgerufen, abbruch ist
    ein threading.Event - ist es gesetzt, bricht der Vorgang ab und die
    halbfertige Zieldatei wird gelöscht.
    """
    quelle = Path(quelle)
    ziel = Path(ziel)
    if not quelle.exists():
        raise FileNotFoundError(f"Nicht gefunden: {quelle}")

    inhalt = INHALT_ORDNER if quelle.is_dir() else INHALT_DATEI
    gesamt = _gesamtgroesse(quelle)

    salt = os.urandom(16)
    kdf = _kdf_fuer(art)
    schluessel = _dateischluessel(art, geheimnis, salt, kdf)
    nonce_basis = os.urandom(8)

    kopf_gesamt = ModernCrypto.kopf_bauen(MARKE + bytes([art]), salt, kdf) + nonce_basis
    kopfsatz = {
        "name": quelle.name,
        "art": inhalt,
        "groesse": gesamt,
        "block": BLOCK,
        "erstellt": datetime.now().strftime("%Y-%m-%d %H:%M"),
    }
    aes = AESGCM(schluessel)
    kopfsatz_ct = aes.encrypt(_nonce(nonce_basis, 0),
                              json.dumps(kopfsatz, ensure_ascii=False).encode("utf-8"),
                              kopf_gesamt)

    def melden(getan):
        if fortschritt is not None:
            fortschritt(getan, gesamt)

    ziel.parent.mkdir(parents=True, exist_ok=True)
    unfertig = ziel.with_name(ziel.name + ".unfertig")
    try:
        with open(unfertig, "wb") as aus:
            aus.write(kopf_gesamt)
            aus.write(struct.pack("!I", len(kopfsatz_ct)))
            aus.write(kopfsatz_ct)

            schreiber = _Blockschreiber(aus, schluessel, nonce_basis,
                                        fortschritt=melden, abbruch=abbruch)
            if inhalt == INHALT_DATEI:
                _datei_hineinschieben(quelle, schreiber, abbruch)
            else:
                _ordner_hineinschieben(quelle, schreiber, abbruch)
            schreiber.close()
        unfertig.replace(ziel)
    except BaseException:
        # Auch bei Abbruch oder Absturz darf keine halbe Datei liegen
        # bleiben - die sähe aus wie ein gültiger Container.
        unfertig.unlink(missing_ok=True)
        raise

    melden(gesamt)
    return ziel


def _datei_hineinschieben(quelle: Path, schreiber, abbruch):
    with open(quelle, "rb") as ein:
        while True:
            if abbruch is not None and abbruch.is_set():
                raise AbgebrochenError("Abgebrochen.")
            stueck = ein.read(BLOCK)
            if not stueck:
                break
            schreiber.write(stueck)


def _ordner_hineinschieben(quelle: Path, schreiber, abbruch):
    # ZIP_STORED, nicht ZIP_DEFLATED: verschlüsselte Daten lassen sich
    # ohnehin nicht mehr packen, und Packen vor dem Verschlüsseln verrät
    # über die Grösse mehr, als es einbringt.
    with zipfile.ZipFile(schreiber, "w", compression=zipfile.ZIP_STORED,
                         allowZip64=True) as zf:
        for pfad in sorted(quelle.rglob("*")):
            if abbruch is not None and abbruch.is_set():
                raise AbgebrochenError("Abgebrochen.")
            innen = pfad.relative_to(quelle).as_posix()
            if pfad.is_dir():
                zf.writestr(innen + "/", b"")
            elif pfad.is_file():
                zf.write(pfad, innen)


def _gesamtgroesse(pfad: Path) -> int:
    if pfad.is_file():
        return pfad.stat().st_size
    summe = 0
    for p in pfad.rglob("*"):
        if p.is_file():
            summe += p.stat().st_size
    return summe


# ---------------------------------------------------------------------------
#  Entschlüsseln
# ---------------------------------------------------------------------------

def kopf_ansehen(pfad) -> dict:
    """Liest nur, welche Schlüsselart eine Datei braucht - ohne Schlüssel.

    Damit kann die Oberfläche gleich das richtige Feld anbieten, statt den
    Benutzer raten zu lassen.
    """
    pfad = Path(pfad)
    with open(pfad, "rb") as ein:
        anfang = ein.read(6)
    if len(anfang) < 6 or not anfang.startswith(MARKE):
        raise ValueError("Das ist keine VP4-Datei.")
    art = anfang[5]
    if art not in (ART_PASSWORT, ART_SCHLUESSEL):
        raise ValueError("Unbekannte Schlüsselart - vermutlich aus einer "
                         "neueren Fassung des Programms.")
    return {"art": art}


def entschluesseln(quelle, zielordner, geheimnis, fortschritt=None,
                   abbruch=None) -> Path:
    """Stellt eine .vp4-Datei wieder her. Gibt den erzeugten Pfad zurück."""
    quelle = Path(quelle)
    zielordner = Path(zielordner)
    zielordner.mkdir(parents=True, exist_ok=True)

    with open(quelle, "rb") as ein:
        roh = ein.read(6)
        if len(roh) < 6 or not roh.startswith(MARKE):
            raise ValueError("Das ist keine VP4-Datei.")
        art = roh[5]
        praefix = MARKE + bytes([art])

        # Der Kopf ist höchstens 6 + 1 + 9 + 16 Byte lang; grosszügig lesen
        # und danach exakt zurückspulen.
        ein.seek(0)
        vorrat = ein.read(64)
        kopf, kdf, salt = ModernCrypto.kopf_lesen(vorrat, praefix)
        nonce_basis = vorrat[len(kopf):len(kopf) + 8]
        if len(nonce_basis) < 8:
            raise ValueError("Die Datei ist abgeschnitten oder beschädigt.")
        kopf_gesamt = kopf + nonce_basis

        ein.seek(len(kopf_gesamt))
        laenge_roh = ein.read(4)
        if len(laenge_roh) < 4:
            raise ValueError("Die Datei ist abgeschnitten oder beschädigt.")
        laenge = struct.unpack("!I", laenge_roh)[0]
        kopfsatz_ct = ein.read(laenge)
        if len(kopfsatz_ct) != laenge:
            raise ValueError("Die Datei ist abgeschnitten oder beschädigt.")

        schluessel = _dateischluessel(art, geheimnis, salt, kdf)
        aes = AESGCM(schluessel)
        try:
            kopfsatz = json.loads(
                aes.decrypt(_nonce(nonce_basis, 0), kopfsatz_ct,
                            kopf_gesamt).decode("utf-8"))
        except Exception:
            raise ValueError("Falsches Passwort beziehungsweise falscher "
                             "Schlüssel - oder die Datei wurde verändert.")

        gesamt = kopfsatz.get("groesse") or 0
        name = Path(kopfsatz.get("name") or quelle.stem).name or "wiederhergestellt"

        if kopfsatz.get("art") == INHALT_ORDNER:
            return _ordner_herstellen(ein, aes, nonce_basis, zielordner, name,
                                      gesamt, fortschritt, abbruch)
        return _datei_herstellen(ein, aes, nonce_basis, zielordner, name,
                                 gesamt, fortschritt, abbruch)


def _bloecke(ein, aes, nonce_basis, abbruch):
    """Gibt die entschlüsselten Blöcke der Reihe nach zurück.

    Ein Block wird immer erst dann herausgegeben, wenn feststeht, ob noch
    einer folgt - nur so lässt sich der letzte als letzter prüfen und ein
    hinten abgeschnittener Container erkennen.
    """
    offen = None
    nummer = 1
    while True:
        if abbruch is not None and abbruch.is_set():
            raise AbgebrochenError("Abgebrochen.")
        laenge_roh = ein.read(4)
        if not laenge_roh:
            break
        if len(laenge_roh) < 4:
            raise ValueError("Die Datei ist abgeschnitten oder beschädigt.")
        laenge = struct.unpack("!I", laenge_roh)[0]
        ct = ein.read(laenge)
        if len(ct) != laenge:
            raise ValueError("Die Datei ist abgeschnitten oder beschädigt.")
        if offen is not None:
            yield _block_pruefen(aes, nonce_basis, *offen, letzter=False)
        offen = (nummer, ct)
        nummer += 1

    if offen is None:
        raise ValueError("Die Datei enthält keine Daten.")
    yield _block_pruefen(aes, nonce_basis, *offen, letzter=True)


def _block_pruefen(aes, nonce_basis, nummer, ct, letzter):
    try:
        return aes.decrypt(_nonce(nonce_basis, nummer), ct,
                           _block_zusatz(nummer, letzter))
    except Exception:
        raise ValueError(
            f"Block {nummer} der Datei ist beschädigt oder wurde verändert.")


def _freier_name(ordner: Path, name: str) -> Path:
    """Sucht einen Namen, der noch nicht vergeben ist.

    Sonst überschreibt ein Entschlüsseln stillschweigend die Datei, die
    man gerade wiederherstellen wollte.
    """
    ziel = ordner / name
    if not ziel.exists():
        return ziel
    stamm, endung = Path(name).stem, Path(name).suffix
    for i in range(2, 1000):
        ziel = ordner / f"{stamm} ({i}){endung}"
        if not ziel.exists():
            return ziel
    raise ValueError("Zu viele gleichnamige Dateien im Zielordner.")


def _datei_herstellen(ein, aes, nonce_basis, zielordner, name, gesamt,
                      fortschritt, abbruch):
    ziel = _freier_name(zielordner, name)
    unfertig = ziel.with_name(ziel.name + ".unfertig")
    getan = 0
    try:
        with open(unfertig, "wb") as aus:
            for klar in _bloecke(ein, aes, nonce_basis, abbruch):
                aus.write(klar)
                getan += len(klar)
                if fortschritt is not None:
                    fortschritt(getan, gesamt or getan)
        unfertig.replace(ziel)
    except BaseException:
        unfertig.unlink(missing_ok=True)
        raise
    return ziel


def _ordner_herstellen(ein, aes, nonce_basis, zielordner, name, gesamt,
                       fortschritt, abbruch):
    ziel = _freier_name(zielordner, name)
    # Das entschlüsselte ZIP muss kurz auf die Platte, weil zipfile beim
    # Lesen springen können muss. Es liegt im Zielordner - dort landet der
    # Klartext ohnehin - und wird auf jeden Fall wieder weggeräumt.
    zwischen = zielordner / f".{ziel.name}.vp4zip"
    getan = 0
    try:
        with open(zwischen, "wb") as aus:
            for klar in _bloecke(ein, aes, nonce_basis, abbruch):
                aus.write(klar)
                getan += len(klar)
                if fortschritt is not None:
                    fortschritt(getan, gesamt or getan)
        ziel.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(zwischen, "r") as zf:
            # extractall entfernt von sich aus Laufwerksbuchstaben,
            # führende Trenner und ".." - ein Archiv kann also nicht aus
            # dem Zielordner ausbrechen.
            zf.extractall(ziel)
    finally:
        zwischen.unlink(missing_ok=True)
    return ziel


# ---------------------------------------------------------------------------
#  Kleinkram für die Oberfläche
# ---------------------------------------------------------------------------

def zielname(quelle) -> Path:
    """Wie die verschlüsselte Fassung heissen soll."""
    quelle = Path(quelle)
    return quelle.with_name(quelle.name + ENDUNG)


def groesse_lesbar(bytes_: int) -> str:
    einheiten = ["B", "KB", "MB", "GB", "TB"]
    wert = float(bytes_)
    for einheit in einheiten:
        if wert < 1024 or einheit == einheiten[-1]:
            if einheit == "B":
                return f"{int(wert)} {einheit}"
            return f"{wert:.1f} {einheit}".replace(".", ",")
        wert /= 1024
    return f"{bytes_} B"
