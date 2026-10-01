# -*- coding: utf-8 -*-
"""
=====================================================================
 tresor.py - der Tresor von VP4 5.0 (Format "VP4K4")
=====================================================================
Der Tresor ist der Nachfolger des Schlüsselspeichers (KeyStore, "VP4K3").
Er hält alles, was geheim bleiben muss: die eigene Identität, den
Schlüsselring der Werkzeuge, die Paar-Schlüssel der Kontakte und die
Schlüssel der Communities.

DER UNTERSCHIED ZU VP4K3: EIN DATENSCHLÜSSEL
--------------------------------------------
Beim alten Speicher wurde der Inhalt direkt mit dem Schlüssel aus dem
Master-Passwort verschlüsselt. Jetzt gibt es dazwischen einen zufälligen
32-Byte-DATENSCHLÜSSEL:

    Master-Passwort --Argon2id--> Hüllschlüssel --umhüllt--> Datenschlüssel
                                                  Datenschlüssel --> Inhalt

Wozu der Umweg?

  1. Der Datenschlüssel lässt sich ZWEIMAL verpacken: einmal mit dem
     Passwort (steht im Tresorkopf) und einmal mit Windows-DPAPI (steht in
     vp4_daten/tresor.dpapi). So kann Windows den Tresor beim Start still
     öffnen, ohne dass das Passwort irgendwo gespeichert wird.
  2. Passwort ändern heißt nur: den Datenschlüssel neu einpacken. Der
     Inhalt bleibt Byte für Byte, wie er ist - und die DPAPI-Datei bleibt
     gültig, weil sich der Datenschlüssel nicht ändert.
  3. Die Datenbank (kern/datenbank.py) leitet ihren Schlüssel ebenfalls vom
     Datenschlüssel ab. Sie muss bei einem Passwortwechsel nicht neu
     verschlüsselt werden.

Dateiaufbau:

    Marke "VP4K4" (5) | KDF-Kennung (1) | KDF-Einstellungen | Salt (16)
    | Nonce Hülle (12) | umhüllter Datenschlüssel (32 + 16)
    | Nonce Inhalt (12) | verschlüsselter Inhalt

Der Teil bis einschließlich Salt ist derselbe selbstbeschreibende Kopf wie
bei VP4K3/VP4P2 (ModernCrypto.kopf_bauen/kopf_lesen). Er geht als AAD in
die Hülle ein - wer die Ableitungs-Einstellungen verdreht, bekommt einen
Fehler statt still einen schwächeren Schlüssel.

Der Inhalt hängt per AAD an der Marke, NICHT an der Hülle. Das ist Absicht:
Würde die Hülle mit in die AAD des Inhalts gehen, müsste der Inhalt bei
jedem Passwortwechsel neu verschlüsselt werden - mit demselben
Datenschlüssel, also mit neuem Nonce, sonst wäre es bei GCM der
Totalschaden (gleiches Nonce, anderes AAD verrät den Authentisierungs-
schlüssel). Getrennt bleibt der Inhalt beim Passwortwechsel unangetastet.
Ein Angreifer gewinnt dadurch nichts: Eine fremde Hülle müsste denselben
Datenschlüssel enthalten, und den kennt er nicht.

Es gibt weiterhin KEINE Wiederherstellung (Leons ausdrücklicher Wunsch).
Passwort vergessen und DPAPI aus = Programm neu einrichten.

WAS DPAPI LEISTET UND WAS NICHT
-------------------------------
DPAPI bindet den Datenschlüssel an das Windows-Konto. Wer die Datei auf
einen anderen PC kopiert, kann damit nichts anfangen. Wer aber an diesem
PC unter diesem Konto angemeldet ist - auch ein Schadprogramm, das dort
läuft -, kommt an den Tresor heran. Bequemlichkeit gegen Schutz; wer das
nicht will, stellt "Bei jedem Start fragen" ein.
=====================================================================
"""

import base64
import json
import os
import shutil
import sys
import tempfile
import threading
from datetime import datetime
from pathlib import Path

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from kern.krypto import ARGON2_STANDARD, KDF_ARGON2ID, ModernCrypto
from kern.speicher import (DATA_DIR, FalschesPasswortError, KeyStore,
                           KeyStoreLockedError)


TRESOR_DATEI = DATA_DIR / "tresor.enc"
TRESOR_DPAPI_DATEI = DATA_DIR / "tresor.dpapi"

DATENSCHLUESSEL_LAENGE = 32
_NONCE = 12
_TAG = 16
_HUELLE_LAENGE = DATENSCHLUESSEL_LAENGE + _TAG


class TresorGesperrtError(KeyStoreLockedError):
    """Zugriff auf einen gesperrten Tresor.

    Erbt vom alten Fehler, damit Code, der schon KeyStoreLockedError
    abfängt, auch den Tresor richtig behandelt.
    """


class TresorBeschaedigtError(ValueError):
    """Das Passwort stimmte, aber der Inhalt ließ sich nicht öffnen.

    Das kann nur heißen: Die Datei ist beschädigt oder wurde verändert.
    Unterscheiden darf man das hier gefahrlos vom falschen Passwort - wer
    so weit kommt, hat das Passwort ja bereits bewiesen.
    """


def _atomar_schreiben(pfad: Path, daten: bytes):
    """Schreibt erst in eine Nebendatei und benennt sie dann um.

    Wie speicher.save_json(), aber für Bytes. Bricht mittendrin etwas ab
    (Absturz, Stromausfall), bleibt die alte Datei vollständig stehen.
    """
    pfad = Path(pfad)
    pfad.parent.mkdir(parents=True, exist_ok=True)
    tmp = pfad.with_name(pfad.name + ".tmp")
    with open(tmp, "wb") as f:
        f.write(daten)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, pfad)


def _b64(roh: bytes) -> str:
    return base64.b64encode(bytes(roh)).decode("ascii")


def _unb64(text: str) -> bytes:
    return base64.b64decode(text)


def _bytes_pruefen(wert, was: str) -> bytes:
    if not isinstance(wert, (bytes, bytearray)) or not wert:
        raise ValueError(f"{was} muss als Bytes übergeben werden.")
    return bytes(wert)


# =============================================================================
#  Alten Schlüsselring lesen (VP4K2 / VP4K3)
# =============================================================================

class _NurLesenKeyStore(KeyStore):
    """Ein KeyStore, der beim Entsperren nichts zurückschreibt.

    Der normale KeyStore hebt einen alten Speicher beim Entsperren still auf
    den heutigen Stand und schreibt ihn dabei neu. Bei der Übernahme in den
    Tresor soll die alte Datei aber unangetastet bleiben.
    """

    def _auf_aktuellen_stand_heben(self, master_password: str):
        pass


def alten_schluesselring_lesen(alt_pfad, master_password: str) -> list:
    """Liest den Schlüsselring eines alten Speichers (VP4K2 oder VP4K3).

    Das Entschlüsseln übernimmt der vorhandene KeyStore - die Altformate
    werden hier bewusst NICHT ein zweites Mal nachgebaut. Gelesen wird aus
    einer Kopie in einem Wegwerf-Ordner, damit die Originaldatei auch dann
    unverändert bleibt, wenn KeyStore beim Entsperren doch einmal schreibt.

    Wirft FalschesPasswortError bei falschem Passwort, FileNotFoundError,
    wenn es die Datei nicht gibt, ValueError bei fremden Dateien.
    """
    alt_pfad = Path(alt_pfad)
    if not alt_pfad.exists():
        raise FileNotFoundError("Es gibt keinen alten Schlüsselspeicher.")
    with tempfile.TemporaryDirectory(prefix="vp4_alt_") as ordner:
        kopie = Path(ordner) / "schluessel.enc"
        shutil.copyfile(alt_pfad, kopie)
        alt = _NurLesenKeyStore(kopie)
        alt.unlock(master_password)
        ring = [dict(k) for k in alt.list_keys()]
        alt.lock()
    return ring


# =============================================================================
#  Der Tresor
# =============================================================================

class Tresor:
    """Der verschlüsselte Tresor (VP4K4). Aufbau: siehe Modulbeschreibung.

    Die Schlüsselring-Methoden verhalten sich genau wie beim alten KeyStore
    (gleiche Einträge, gleiche Fehler, jede Änderung wird sofort
    gespeichert). Unter den alten Namen (list_keys, add_key, ...) sind sie
    ebenfalls erreichbar, damit die Werkzeuge ohne Umbau umsteigen können.
    """

    MARKE = b"VP4K4"
    # AAD des Inhalts - siehe Modulbeschreibung, warum hier nicht die Hülle
    # mit hineingeht.
    _INHALT_AAD = MARKE + b" inhalt"

    def __init__(self, path=None):
        self.path = Path(path) if path is not None else TRESOR_DATEI
        self._lock = threading.RLock()
        self._daten = None          # entschlüsselter Inhalt (dict)
        self._dk = None             # Datenschlüssel
        self._huelle = None         # Kopf + Nonce + umhüllter Datenschlüssel
        self._inhalt_roh = None     # Nonce + Geheimtext des Inhalts
        # Wahr, wenn beim Entsperren die Hülle auf die heutige Ableitung
        # gehoben wurde (z.B. nach einer Änderung von ARGON2_STANDARD).
        self.migriert = False

    # ------------------------------------------------------------- Zustand

    def exists(self) -> bool:
        return self.path.exists()

    def is_unlocked(self) -> bool:
        return self._daten is not None

    ist_entsperrt = is_unlocked

    def _require_unlocked(self):
        if self._daten is None:
            raise TresorGesperrtError("Der Tresor ist gesperrt.")

    @property
    def datenschluessel(self) -> bytes:
        """Der Datenschlüssel - nur bei entsperrtem Tresor."""
        with self._lock:
            self._require_unlocked()
            return self._dk

    # ---------------------------------------------------------- Hülle

    def _huelle_bauen(self, master_password: str, dk: bytes) -> bytes:
        """Verpackt den Datenschlüssel mit dem Master-Passwort."""
        salt = os.urandom(16)
        kdf = dict(ARGON2_STANDARD)
        kopf = ModernCrypto.kopf_bauen(self.MARKE, salt, kdf)
        hk = ModernCrypto.schluessel_ableiten(master_password, salt, kdf)
        nonce = os.urandom(_NONCE)
        return kopf + nonce + AESGCM(hk).encrypt(nonce, dk, kopf)

    @classmethod
    def _zerlegen(cls, roh: bytes) -> dict:
        """Zerlegt eine Tresordatei, ohne etwas zu entschlüsseln."""
        if not roh.startswith(cls.MARKE):
            raise ValueError("Die Datei ist kein VP4-Tresor.")
        kopf, kdf, salt = ModernCrypto.kopf_lesen(roh, cls.MARKE)
        if kdf.get("kdf") != KDF_ARGON2ID:
            # Ein VP4K4 wird nur mit Argon2id geschrieben. Alles andere ist
            # verändert - und PBKDF2 mit zehn Millionen Runden würde sonst
            # erst einmal lange rechnen, bevor der Fehler auffällt.
            raise ValueError("Der Tresorkopf ist beschädigt oder wurde verändert.")
        rest = roh[len(kopf):]
        if len(rest) < _NONCE + _HUELLE_LAENGE + _NONCE + _TAG:
            raise ValueError("Der Tresor ist abgeschnitten oder beschädigt.")
        nonce_h = rest[:_NONCE]
        umhuellt = rest[_NONCE:_NONCE + _HUELLE_LAENGE]
        inhalt_roh = rest[_NONCE + _HUELLE_LAENGE:]
        return {"kopf": kopf, "kdf": kdf, "salt": salt, "nonce_huelle": nonce_h,
                "umhuellt": umhuellt, "huelle": kopf + nonce_h + umhuellt,
                "inhalt_roh": inhalt_roh}

    def _auspacken(self, teile: dict, master_password: str) -> bytes:
        hk = ModernCrypto.schluessel_ableiten(
            master_password, teile["salt"], teile["kdf"])
        try:
            return AESGCM(hk).decrypt(teile["nonce_huelle"], teile["umhuellt"],
                                      teile["kopf"])
        except Exception:
            # Falsches Passwort und veränderte Hülle sehen hier gleich aus -
            # und sollen es auch, sonst verriete die Meldung, ob das
            # Passwort stimmte.
            raise FalschesPasswortError("Falsches Master-Passwort.")

    def _datei_lesen(self) -> dict:
        if not self.exists():
            raise FileNotFoundError("Es gibt noch keinen Tresor.")
        return self._zerlegen(self.path.read_bytes())

    # ------------------------------------------------------ Inhalt

    def _inhalt_oeffnen(self, dk: bytes, inhalt_roh: bytes) -> dict:
        try:
            klar = AESGCM(dk).decrypt(inhalt_roh[:_NONCE], inhalt_roh[_NONCE:],
                                      self._INHALT_AAD)
        except Exception:
            raise TresorBeschaedigtError(
                "Der Tresor ist beschädigt oder wurde verändert.")
        daten = json.loads(klar.decode("utf-8"))
        return self._vervollstaendigen(daten)

    @staticmethod
    def _vervollstaendigen(daten: dict) -> dict:
        daten.setdefault("identitaet", None)
        daten.setdefault("schluessel", [])
        daten.setdefault("paare", {})
        daten.setdefault("communities", {})
        daten.setdefault("extra", {})
        return daten

    def _inhalt_verschluesseln(self) -> bytes:
        klar = json.dumps(self._daten, ensure_ascii=False).encode("utf-8")
        nonce = os.urandom(_NONCE)
        return nonce + AESGCM(self._dk).encrypt(nonce, klar, self._INHALT_AAD)

    def _schreiben(self):
        _atomar_schreiben(self.path, self._huelle + self._inhalt_roh)

    def save(self):
        """Verschlüsselt den Inhalt neu (frisches Nonce) und schreibt ihn.

        Die Hülle wird unverändert übernommen - deshalb braucht Speichern
        kein Passwort, auch nicht nach dem Entsperren über Windows.
        """
        with self._lock:
            self._require_unlocked()
            self._inhalt_roh = self._inhalt_verschluesseln()
            self._schreiben()

    # ------------------------------------------- Anlegen / Entsperren

    def create(self, master_password: str, ueberschreiben: bool = False):
        """Legt einen neuen, leeren Tresor mit frischem Datenschlüssel an.

        Ein bestehender Tresor wird nur mit ueberschreiben=True ersetzt -
        dann ist alles, was darin war, unwiederbringlich weg.
        """
        if not master_password:
            raise ValueError("Bitte ein Master-Passwort angeben.")
        with self._lock:
            if self.exists() and not ueberschreiben:
                raise FileExistsError("Es gibt bereits einen Tresor.")
            dk = os.urandom(DATENSCHLUESSEL_LAENGE)
            self._huelle = self._huelle_bauen(master_password, dk)
            self._dk = dk
            self._daten = self._vervollstaendigen({
                "angelegt": datetime.now().strftime("%Y-%m-%d %H:%M")})
            self.migriert = False
            self.save()

    def unlock(self, master_password: str):
        """Öffnet den Tresor mit dem Master-Passwort.

        FalschesPasswortError bei falschem Passwort oder veränderter Hülle,
        TresorBeschaedigtError bei verändertem Inhalt, ValueError bei
        unmöglichem Kopf (wird VOR dem Ableiten erkannt).
        """
        if not master_password:
            raise FalschesPasswortError("Falsches Master-Passwort.")
        with self._lock:
            teile = self._datei_lesen()
            dk = self._auspacken(teile, master_password)
            daten = self._inhalt_oeffnen(dk, teile["inhalt_roh"])
            self._dk, self._daten = dk, daten
            self._huelle, self._inhalt_roh = teile["huelle"], teile["inhalt_roh"]
            self.migriert = False
            # Solange das Passwort noch da ist: eine Hülle mit veralteter
            # Ableitung gleich neu einpacken. Kostet nur den Kopf, der
            # Inhalt bleibt, wie er ist.
            if teile["kdf"] != ARGON2_STANDARD:
                alte_huelle = self._huelle
                try:
                    self._huelle = self._huelle_bauen(master_password, dk)
                    self._schreiben()
                    self.migriert = True
                except OSError:
                    self._huelle = alte_huelle

    def unlock_mit_datenschluessel(self, dk: bytes):
        """Öffnet den Tresor direkt mit dem Datenschlüssel (Weg über DPAPI).

        TresorBeschaedigtError, wenn der Schlüssel nicht passt - etwa weil
        der Tresor inzwischen neu angelegt wurde.
        """
        dk = _bytes_pruefen(dk, "Der Datenschlüssel")
        if len(dk) != DATENSCHLUESSEL_LAENGE:
            raise ValueError("Der Datenschlüssel hat die falsche Länge.")
        with self._lock:
            teile = self._datei_lesen()
            daten = self._inhalt_oeffnen(dk, teile["inhalt_roh"])
            self._dk, self._daten = dk, daten
            self._huelle, self._inhalt_roh = teile["huelle"], teile["inhalt_roh"]
            self.migriert = False

    def lock(self):
        with self._lock:
            self._daten = None
            self._dk = None
            self._huelle = None
            self._inhalt_roh = None
            self.migriert = False

    def change_password(self, altes: str, neues: str):
        """Ändert das Master-Passwort: nur die Hülle wird neu gepackt.

        Das alte Passwort wird immer gegen die Datei geprüft, auch wenn der
        Tresor schon offen ist - wer kurz an einem entsperrten PC sitzt,
        soll das Passwort nicht einfach austauschen können.
        """
        if not neues:
            raise ValueError("Bitte ein neues Master-Passwort angeben.")
        with self._lock:
            teile = self._datei_lesen()
            dk = self._auspacken(teile, altes or "")
            if self._daten is None:
                self._daten = self._inhalt_oeffnen(dk, teile["inhalt_roh"])
                self._dk = dk
                self._inhalt_roh = teile["inhalt_roh"]
            elif dk != self._dk:
                raise TresorBeschaedigtError(
                    "Die Tresordatei wurde inzwischen durch eine andere ersetzt.")
            self._huelle = self._huelle_bauen(neues, dk)
            self._schreiben()

    # --------------------------------------------------- Identität

    def identitaet_holen(self):
        with self._lock:
            self._require_unlocked()
            wert = self._daten["identitaet"]
            return _unb64(wert) if wert else None

    def identitaet_setzen(self, roh):
        with self._lock:
            self._require_unlocked()
            self._daten["identitaet"] = (
                None if roh is None else _b64(_bytes_pruefen(roh, "Die Identität")))
            self.save()

    identitaet_bytes = property(identitaet_holen, identitaet_setzen,
                                doc="Die eigenen Identitätsschlüssel als Bytes.")

    # ------------------------------------------------- Schlüsselring

    def schluessel_liste(self) -> list:
        with self._lock:
            self._require_unlocked()
            return [dict(k) for k in self._daten["schluessel"]]

    def schluessel_holen(self, label: str):
        with self._lock:
            self._require_unlocked()
            for k in self._daten["schluessel"]:
                if k["label"] == label:
                    return dict(k)
            return None

    def schluessel_hinzufuegen(self, label: str, typ: str, value, meta: str = ""):
        with self._lock:
            self._require_unlocked()
            label = (label or "").strip()
            if not label:
                raise ValueError("Bitte einen Namen für den Schlüssel angeben.")
            if any(k["label"] == label for k in self._daten["schluessel"]):
                raise ValueError(
                    f"Es gibt bereits einen Schlüssel mit dem Namen '{label}'.")
            self._daten["schluessel"].append({
                "label": label,
                "typ": typ,
                "wert": value,
                "meta": meta,
                "erstellt": datetime.now().strftime("%Y-%m-%d %H:%M"),
            })
            self.save()

    def schluessel_loeschen(self, label: str):
        with self._lock:
            self._require_unlocked()
            self._daten["schluessel"] = [
                k for k in self._daten["schluessel"] if k["label"] != label]
            self.save()

    def schluessel_ersetzen(self, keys: list):
        """Führt eine Liste von Schlüsseln ein (z.B. aus Obsidian).

        Gleiche Namen werden überschrieben, alles andere bleibt stehen.
        """
        with self._lock:
            self._require_unlocked()
            nach_label = {k["label"]: k for k in self._daten["schluessel"]}
            for k in keys:
                nach_label[k["label"]] = dict(k)
            self._daten["schluessel"] = list(nach_label.values())
            self.save()

    # Die Namen des alten KeyStore - damit die Werkzeuge umsteigen können.
    list_keys = schluessel_liste
    get_key = schluessel_holen
    add_key = schluessel_hinzufuegen
    delete_key = schluessel_loeschen
    replace_all = schluessel_ersetzen

    # ------------------------------------------- Paar-Schlüssel (Kontakte)

    def _bytes_tabelle_holen(self, tabelle: str, kennung: str):
        with self._lock:
            self._require_unlocked()
            wert = self._daten[tabelle].get(kennung)
            return _unb64(wert) if wert else None

    def _bytes_tabelle_setzen(self, tabelle: str, kennung: str, roh, was: str):
        kennung = (kennung or "").strip()
        if not kennung:
            raise ValueError("Bitte eine Kennung angeben.")
        with self._lock:
            self._require_unlocked()
            self._daten[tabelle][kennung] = _b64(_bytes_pruefen(roh, was))
            self.save()

    def _bytes_tabelle_loeschen(self, tabelle: str, kennung: str) -> bool:
        with self._lock:
            self._require_unlocked()
            if self._daten[tabelle].pop(kennung, None) is None:
                return False
            self.save()
            return True

    def paar_schluessel(self, kontakt_id: str):
        """Der Paar-Wurzelschlüssel eines Kontakts (Bytes) oder None."""
        return self._bytes_tabelle_holen("paare", kontakt_id)

    def paar_schluessel_setzen(self, kontakt_id: str, roh: bytes):
        self._bytes_tabelle_setzen("paare", kontakt_id, roh, "Der Paar-Schlüssel")

    def paar_schluessel_loeschen(self, kontakt_id: str) -> bool:
        return self._bytes_tabelle_loeschen("paare", kontakt_id)

    def paar_ids(self) -> list:
        with self._lock:
            self._require_unlocked()
            return list(self._daten["paare"])

    # ------------------------------------------- Community-Schlüssel

    def community_schluessel(self, community_id: str):
        """Der Schlüssel einer Community (Bytes) oder None."""
        return self._bytes_tabelle_holen("communities", community_id)

    def community_schluessel_setzen(self, community_id: str, roh: bytes):
        self._bytes_tabelle_setzen("communities", community_id, roh,
                                   "Der Community-Schlüssel")

    def community_schluessel_loeschen(self, community_id: str) -> bool:
        return self._bytes_tabelle_loeschen("communities", community_id)

    def community_ids(self) -> list:
        with self._lock:
            self._require_unlocked()
            return list(self._daten["communities"])

    # ------------------------------------------- Sonstiges

    def extra_holen(self, name: str, standard=None):
        """Freier Platz für Geheimes, das in kein Fach oben passt (JSON)."""
        with self._lock:
            self._require_unlocked()
            return self._daten["extra"].get(name, standard)

    def extra_setzen(self, name: str, wert):
        with self._lock:
            self._require_unlocked()
            json.dumps(wert)            # muss als JSON speicherbar sein
            self._daten["extra"][name] = wert
            self.save()

    # ------------------------------------------- Übernahme aus 4.x

    @classmethod
    def aus_altem_speicher(cls, alt_pfad, neues_pfad, master_password: str,
                           ueberschreiben: bool = False) -> "Tresor":
        """Baut aus einem alten Speicher (VP4K2/VP4K3) einen neuen Tresor.

        Gleicher Schlüsselring, gleiches Passwort. Die alte Datei bleibt
        unverändert liegen. Rückgabe: der neue, entsperrte Tresor.
        """
        ring = alten_schluesselring_lesen(alt_pfad, master_password)
        tresor = cls(neues_pfad)
        tresor.create(master_password, ueberschreiben=ueberschreiben)
        with tresor._lock:
            tresor._daten["schluessel"] = ring
            tresor.save()
        return tresor


# =============================================================================
#  Windows-DPAPI
# =============================================================================

_CRYPTPROTECT_UI_FORBIDDEN = 0x01
_DPAPI_ENTROPIE = b"VP4 tresor v1"


class DPAPI:
    """Windows-DPAPI über ctypes (CryptProtectData / CryptUnprotectData).

    Bindet Daten an das angemeldete Windows-Konto. Auf anderen Systemen
    ist es nicht verfügbar - dann fragt VP4 eben jedes Mal nach dem
    Passwort. Das Modul lässt sich trotzdem überall importieren: alles
    Windows-Spezifische wird erst beim Aufruf geladen.
    """

    def __init__(self, entropie: bytes = _DPAPI_ENTROPIE):
        self.entropie = entropie

    @staticmethod
    def verfuegbar() -> bool:
        if sys.platform != "win32":
            return False
        try:
            import ctypes
            ctypes.WinDLL("crypt32")
            ctypes.WinDLL("kernel32")
            return True
        except Exception:
            return False

    @staticmethod
    def _api():
        """Lädt die Funktionen und legt ihre Signaturen fest."""
        import ctypes
        from ctypes import wintypes

        class DATA_BLOB(ctypes.Structure):
            _fields_ = [("cbData", wintypes.DWORD),
                        ("pbData", ctypes.POINTER(ctypes.c_ubyte))]

        crypt32 = ctypes.WinDLL("crypt32", use_last_error=True)
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        zeiger = ctypes.POINTER(DATA_BLOB)

        schuetzen = crypt32.CryptProtectData
        schuetzen.argtypes = [zeiger, wintypes.LPCWSTR, zeiger, ctypes.c_void_p,
                              ctypes.c_void_p, wintypes.DWORD, zeiger]
        schuetzen.restype = wintypes.BOOL

        entschuetzen = crypt32.CryptUnprotectData
        entschuetzen.argtypes = [zeiger, ctypes.POINTER(wintypes.LPWSTR), zeiger,
                                 ctypes.c_void_p, ctypes.c_void_p,
                                 wintypes.DWORD, zeiger]
        entschuetzen.restype = wintypes.BOOL

        freigeben = kernel32.LocalFree
        freigeben.argtypes = [ctypes.c_void_p]
        freigeben.restype = ctypes.c_void_p
        return ctypes, DATA_BLOB, schuetzen, entschuetzen, freigeben

    @staticmethod
    def _blob(ctypes, DATA_BLOB, daten: bytes):
        """Baut einen DATA_BLOB. Der Puffer muss leben, solange der Blob
        benutzt wird - deshalb wird er mit zurückgegeben."""
        puffer = ctypes.create_string_buffer(bytes(daten), len(daten))
        blob = DATA_BLOB(len(daten),
                         ctypes.cast(puffer, ctypes.POINTER(ctypes.c_ubyte)))
        return blob, puffer

    def _aufrufen(self, funktion_name: str, daten: bytes) -> bytes:
        if not self.verfuegbar():
            raise OSError("Windows-DPAPI ist auf diesem System nicht verfügbar.")
        ctypes, DATA_BLOB, schuetzen, entschuetzen, freigeben = self._api()
        ein, ein_puffer = self._blob(ctypes, DATA_BLOB, daten)
        if self.entropie:
            ent, ent_puffer = self._blob(ctypes, DATA_BLOB, self.entropie)
            ent_zeiger = ctypes.byref(ent)
        else:
            ent_puffer, ent_zeiger = None, None
        aus = DATA_BLOB()
        if funktion_name == "schuetzen":
            ok = schuetzen(ctypes.byref(ein), "VP4", ent_zeiger, None, None,
                           _CRYPTPROTECT_UI_FORBIDDEN, ctypes.byref(aus))
        else:
            ok = entschuetzen(ctypes.byref(ein), None, ent_zeiger, None, None,
                              _CRYPTPROTECT_UI_FORBIDDEN, ctypes.byref(aus))
        if not ok:
            raise OSError(f"DPAPI ist fehlgeschlagen (Fehler {ctypes.get_last_error()}).")
        try:
            return ctypes.string_at(aus.pbData, aus.cbData)
        finally:
            # Den Ausgabepuffer hat Windows angelegt - er muss mit LocalFree
            # zurück. Die Eingabepuffer (ein_puffer, ent_puffer) gehören
            # Python und leben bis hierher, solange die Blobs auf sie zeigen.
            freigeben(ctypes.cast(aus.pbData, ctypes.c_void_p))
            del ein_puffer, ent_puffer

    def schuetzen(self, daten: bytes) -> bytes:
        return self._aufrufen("schuetzen", _bytes_pruefen(daten, "Die Daten"))

    def entschuetzen(self, blob: bytes) -> bytes:
        return self._aufrufen("entschuetzen", _bytes_pruefen(blob, "Der DPAPI-Block"))


class DPAPIAttrappe:
    """NUR FÜR TESTS. Tut so, als wäre DPAPI da - und schützt NICHTS.

    Die Daten werden bloß mit einer Kennung davor abgelegt, im Klartext.
    Darf nie in einem echten Programmpfad landen: Der Ersatz wird immer
    ausdrücklich als Parameter hineingereicht, nie automatisch gewählt.
    """

    KENNUNG = b"VP4-DPAPI-ATTRAPPE-NUR-TEST:"

    def __init__(self, verfuegbar: bool = True):
        self._verfuegbar = verfuegbar

    def verfuegbar(self) -> bool:
        return self._verfuegbar

    def schuetzen(self, daten: bytes) -> bytes:
        if not self._verfuegbar:
            raise OSError("DPAPI-Attrappe ist abgeschaltet.")
        return self.KENNUNG + _bytes_pruefen(daten, "Die Daten")

    def entschuetzen(self, blob: bytes) -> bytes:
        if not self._verfuegbar:
            raise OSError("DPAPI-Attrappe ist abgeschaltet.")
        if not isinstance(blob, (bytes, bytearray)) or not blob.startswith(self.KENNUNG):
            raise OSError("Der Block stammt nicht von der Attrappe.")
        return bytes(blob[len(self.KENNUNG):])


# Aufbau von tresor.dpapi: Marke + der DPAPI-Block.
_DPAPI_MARKE = b"VP4W1"


def datenschluessel_merken(tresor: Tresor, dpapi=None, pfad=None):
    """Legt den Datenschlüssel DPAPI-geschützt ab (vp4_daten/tresor.dpapi).

    Wirft ValueError, wenn DPAPI fehlt oder der Tresor gesperrt ist.
    """
    dpapi = dpapi if dpapi is not None else DPAPI()
    pfad = Path(pfad) if pfad is not None else TRESOR_DPAPI_DATEI
    if not dpapi.verfuegbar():
        raise ValueError("Windows kann sich das Passwort hier nicht merken "
                         "(DPAPI gibt es nur unter Windows).")
    blob = dpapi.schuetzen(tresor.datenschluessel)
    _atomar_schreiben(pfad, _DPAPI_MARKE + blob)


def datenschluessel_vergessen(pfad=None) -> bool:
    """Löscht tresor.dpapi. Wahr, wenn es etwas zu löschen gab."""
    pfad = Path(pfad) if pfad is not None else TRESOR_DPAPI_DATEI
    try:
        pfad.unlink()
        return True
    except FileNotFoundError:
        return False


def mit_windows_entsperren(tresor: Tresor, dpapi=None, pfad=None) -> bool:
    """Versucht, den Tresor still über DPAPI zu öffnen.

    Gibt False zurück, wenn das nicht geht - Datei fehlt oder ist kaputt,
    DPAPI fehlt, der Schlüssel passt nicht (mehr) zum Tresor. Wirft in
    diesen Fällen nie; dann zeigt die Oberfläche eben den Sperrbildschirm.
    """
    dpapi = dpapi if dpapi is not None else DPAPI()
    pfad = Path(pfad) if pfad is not None else TRESOR_DPAPI_DATEI
    try:
        if not dpapi.verfuegbar() or not pfad.exists():
            return False
        roh = pfad.read_bytes()
        if not roh.startswith(_DPAPI_MARKE):
            return False
        dk = dpapi.entschuetzen(roh[len(_DPAPI_MARKE):])
        if not isinstance(dk, (bytes, bytearray)) or len(dk) != DATENSCHLUESSEL_LAENGE:
            return False
        tresor.unlock_mit_datenschluessel(bytes(dk))
        return True
    except Exception:
        # Bewusst breit: Dieser Weg ist reine Bequemlichkeit. Was auch immer
        # schiefgeht, der Sperrbildschirm ist die sichere Antwort darauf.
        return False
