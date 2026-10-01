#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
=====================================================================
 speicher.py - alles, was VP4 auf die Festplatte schreibt
=====================================================================
Enthält:

  Pfade            - wo die Daten liegen (Ordner vp4_daten)
  Einstellungen    - eigene ID, Design, Obsidian-Ordner
  KeyStore         - der mit dem Master-Passwort geschützte Schlüsselspeicher
  FriendsStore     - die Freundesliste für den Chat
  ObsidianSync     - Export/Import der Schlüssel als Obsidian-Notiz

ZUM MASTER-PASSWORT
--------------------
Der Schlüsselspeicher wird mit einem Passwort verschlüsselt, das nirgends
gespeichert wird - weder im Klartext noch als Hash, aus dem man es
zurückrechnen könnte. Aus dem Passwort wird der Schlüssel abgeleitet, mit
dem die Datei ver- und entschlüsselt ist.

Das heißt: Wer das Passwort vergisst, kommt an die gespeicherten Schlüssel
nicht mehr heran. Nicht "schwer", sondern gar nicht - es gibt keine
Hintertür und keine Wiederherstellung. Das ist Absicht: jede
Wiederherstellungsmöglichkeit wäre auch ein Weg für jemand anderen.
=====================================================================
"""

import base64
import json
import os
import secrets
import string
import sys
import threading
from datetime import datetime
from pathlib import Path

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from kern.krypto import (ARGON2_STANDARD, KDF_PBKDF2, PBKDF2_RUNDEN,
                    ModernCrypto)


# =============================================================================
#  Pfade
# =============================================================================

def _base_dir() -> Path:
    """Der Ordner, in dem die Daten liegen.

    Wichtig für die .exe: sys.frozen ist gesetzt, wenn das Programm von
    PyInstaller gebaut wurde. Dann liegt die .exe irgendwo, und die Daten
    sollen daneben liegen - nicht im temporären Entpack-Ordner.
    """
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    # kern/ liegt eine Ebene unter dem Programmordner
    return Path(__file__).resolve().parent.parent


BASE_DIR = _base_dir()
DATA_DIR = BASE_DIR / "vp4_daten"
KEYSTORE_FILE = DATA_DIR / "schluessel.enc"
CONFIG_FILE = DATA_DIR / "konfig.json"
FRIENDS_FILE = DATA_DIR / "freunde.json"
RECEIVED_DIR = DATA_DIR / "empfangen"


def ordner_anlegen():
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    RECEIVED_DIR.mkdir(parents=True, exist_ok=True)


# =============================================================================
#  JSON-Dateien lesen und schreiben
# =============================================================================

def load_json(path: Path, default):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return default


def save_json(path: Path, data):
    """Schreibt erst in eine Nebendatei und benennt sie dann um.

    Damit kann die eigentliche Datei nicht halb beschrieben zurückbleiben,
    wenn mittendrin etwas schiefgeht (Absturz, Stromausfall).
    """
    ordner_anlegen()
    tmp = path.with_suffix(path.suffix + ".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    tmp.replace(path)


# =============================================================================
#  Einstellungen
# =============================================================================

STANDARD_EINSTELLUNGEN = {
    "my_id": None,
    "obsidian_vault": "",
    "design": "dark",              # "dark", "light" oder "system"
    "farbe": "blau",               # Akzentfarbe der Oberfläche
    "master_passwort_gesetzt": False,
    "chat_aktiv": True,
    # Welcher Weg für den Chat: "lan", "discord" oder "beide".
    # "beide" ist die Voreinstellung, damit der Chat einfach funktioniert,
    # sobald jemand das Programm offen hat - im selben WLAN direkt, sonst über
    # Discord. Ohne eingebauten oder eingetragenen Discord-Zugang bleibt es
    # still beim WLAN, es wird also nichts ungefragt ins Internet geschickt.
    "transport_modus": "beide",
    # Der Name, unter dem man in Gruppen auftaucht. Leer heißt: die eigene
    # ID. In einer Gruppe kennt man sich nicht gegenseitig als Freunde, es
    # gäbe also sonst nichts als "MAXX-0002" zu lesen.
    "anzeigename": "",
}


def generate_id() -> str:
    """Erzeugt eine gut vorlesbare ID der Form ABCD-1234.

    Ohne die Zeichen I, O, 0 und 1 - die verwechselt man beim Abtippen
    und Vorlesen zu leicht.
    """
    zeichen = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
    teil = lambda: "".join(secrets.choice(zeichen) for _ in range(4))
    return f"{teil()}-{teil()}"


def load_config() -> dict:
    cfg = dict(STANDARD_EINSTELLUNGEN)
    cfg.update(load_json(CONFIG_FILE, {}))
    if not cfg.get("my_id"):
        cfg["my_id"] = generate_id()
        save_json(CONFIG_FILE, cfg)
    return cfg


def save_config(cfg: dict):
    """Schreibt die Einstellungen.

    Der Testmodus ist eine Sicherung gegen einen Fehler, der einmal echten
    Schaden angerichtet hat: Tests und Vorschau-Läufe bauen das Hauptfenster
    mit erfundenen Standardeinstellungen auf. Sobald darin etwas gespeichert
    wurde, landete das in der echten konfig.json - und überschrieb den
    tatsächlich eingestellten Obsidian-Ordner mit einem leeren Wert.

    Wer das Fenster zum Testen baut, setzt VP4_TESTMODUS. Dann wird nichts
    geschrieben, egal was im Programm passiert.
    """
    if os.environ.get("VP4_TESTMODUS"):
        return
    save_json(CONFIG_FILE, cfg)


# =============================================================================
#  Schlüsselspeicher
# =============================================================================

class KeyStoreLockedError(Exception):
    """Wird ausgelöst, wenn auf einen gesperrten Speicher zugegriffen wird."""


class FalschesPasswortError(Exception):
    """Das eingegebene Master-Passwort passt nicht."""


class KeyStore:
    """Der mit dem Master-Passwort verschlüsselte Schlüsselspeicher.

    Dateiaufbau:
        Kopf | Nonce (12 Byte) | verschlüsselter Inhalt

    Der Kopf enthält die Marke "VP4K3", die Einstellungen der
    Schlüsselableitung und das Salt - Aufbau und Begründung stehen bei
    ModernCrypto.kopf_bauen(). Verschlüsselt wird mit AES-256-GCM, und der
    Kopf geht als zusätzliche Daten mit ein. GCM merkt dadurch selbst, wenn
    an der Datei etwas verändert wurde - beim Entsperren fällt das auf, statt
    still Unsinn zurückzugeben.

    Die erste Fassung trug die Marke "VP4K2" und hatte die Einstellungen
    nirgends vermerkt (immer PBKDF2 mit 600.000 Runden). Solche Dateien
    werden weiterhin gelesen und beim Entsperren still auf das neue Format
    gehoben.
    """

    MARKE = b"VP4K3"
    MARKE_ALT = b"VP4K2"

    def __init__(self, path: Path):
        self.path = Path(path)
        self._data = None
        self._key = None
        self._salt = None
        self._kdf = None
        # Wird wahr, wenn beim Entsperren eine alte Datei umgestellt wurde.
        # Die Oberfläche kann das in der Statusleiste erwähnen.
        self.migriert = False

    # ------------------------------------------------------------- Zustand

    def exists(self) -> bool:
        return self.path.exists()

    def is_unlocked(self) -> bool:
        return self._data is not None

    def _require_unlocked(self):
        if not self.is_unlocked():
            raise KeyStoreLockedError("Der Schlüsselspeicher ist gesperrt.")

    # ------------------------------------------------- Anlegen / Entsperren

    def create(self, master_password: str):
        """Legt einen neuen, leeren Speicher an."""
        if not master_password:
            raise ValueError("Bitte ein Master-Passwort angeben.")
        self._salt = os.urandom(16)
        self._kdf = dict(ARGON2_STANDARD)
        self._key = ModernCrypto.schluessel_ableiten(
            master_password, self._salt, self._kdf)
        self._data = {"keys": [], "angelegt": datetime.now().strftime("%Y-%m-%d %H:%M")}
        self._save()

    def unlock(self, master_password: str):
        """Öffnet einen bestehenden Speicher."""
        if not self.exists():
            raise FileNotFoundError("Es gibt noch keinen Schlüsselspeicher.")
        roh = self.path.read_bytes()

        if roh.startswith(self.MARKE_ALT):
            self._unlock_alt(roh, master_password)
        elif roh.startswith(self.MARKE):
            self._unlock_neu(roh, master_password)
        else:
            raise ValueError("Die Datei ist kein VP4-Schlüsselspeicher.")

        # Solange wir das Passwort noch in der Hand haben: eine Datei mit
        # veralteter Ableitung gleich auf den heutigen Stand heben. Später
        # ginge das nicht mehr, denn dafür muss der Schlüssel neu aus dem
        # Passwort berechnet werden.
        if self._kdf != ARGON2_STANDARD:
            self._auf_aktuellen_stand_heben(master_password)

    def _unlock_neu(self, roh: bytes, master_password: str):
        kopf, kdf, salt = ModernCrypto.kopf_lesen(roh, self.MARKE)
        rest = roh[len(kopf):]
        if len(rest) < 12 + 1:
            raise ValueError("Der Schlüsselspeicher ist beschädigt.")
        nonce, ct = rest[:12], rest[12:]
        key = ModernCrypto.schluessel_ableiten(master_password, salt, kdf)
        self._entschluesseln(key, nonce, ct, kopf)
        self._salt, self._kdf = salt, kdf

    def _unlock_alt(self, roh: bytes, master_password: str):
        """Liest einen Speicher der ersten Fassung ("VP4K2").

        Dort stand die Ableitung nicht in der Datei: es war immer PBKDF2 mit
        600.000 Runden, und der Kopf war nicht mitversiegelt.
        """
        rest = roh[len(self.MARKE_ALT):]
        if len(rest) < 16 + 12 + 1:
            raise ValueError("Der Schlüsselspeicher ist beschädigt.")
        salt, nonce, ct = rest[:16], rest[16:28], rest[28:]
        key = ModernCrypto.derive_key(master_password, salt)
        self._entschluesseln(key, nonce, ct, None)
        self._salt = salt
        self._kdf = {"kdf": KDF_PBKDF2, "runden": PBKDF2_RUNDEN}

    def _entschluesseln(self, key: bytes, nonce: bytes, ct: bytes, aad):
        try:
            klar = AESGCM(key).decrypt(nonce, ct, aad)
        except Exception:
            # Hier landet man sowohl bei falschem Passwort als auch bei einer
            # veränderten Datei. Unterscheiden lässt sich das nicht - und man
            # sollte es auch nicht, sonst verrät die Meldung, ob das Passwort
            # stimmte.
            raise FalschesPasswortError("Falsches Master-Passwort.")
        self._data = json.loads(klar.decode("utf-8"))
        self._key = key

    def _auf_aktuellen_stand_heben(self, master_password: str):
        """Schreibt den Speicher mit der heutigen Ableitung neu."""
        alter_schluessel, altes_salt, altes_kdf = self._key, self._salt, self._kdf
        self._salt = os.urandom(16)
        self._kdf = dict(ARGON2_STANDARD)
        self._key = ModernCrypto.schluessel_ableiten(
            master_password, self._salt, self._kdf)
        try:
            self._save()
            self.migriert = True
        except OSError:
            # Zum Beispiel ein schreibgeschützter Ordner. Kein Grund, das
            # Entsperren scheitern zu lassen - die Schlüssel sind ja da.
            # Beim nächsten Speichern wird es erneut versucht.
            self._key, self._salt, self._kdf = alter_schluessel, altes_salt, altes_kdf

    def lock(self):
        self._data = None
        self._key = None
        self._salt = None
        self._kdf = None
        self.migriert = False

    def change_password(self, altes: str, neues: str):
        """Ändert das Master-Passwort. Der Inhalt bleibt erhalten."""
        if not self.is_unlocked():
            self.unlock(altes)
        if not neues:
            raise ValueError("Bitte ein neues Master-Passwort angeben.")
        self._salt = os.urandom(16)
        self._kdf = dict(ARGON2_STANDARD)
        self._key = ModernCrypto.schluessel_ableiten(neues, self._salt, self._kdf)
        self._save()

    def _save(self):
        ordner_anlegen()
        klar = json.dumps(self._data, ensure_ascii=False).encode("utf-8")
        kopf = ModernCrypto.kopf_bauen(self.MARKE, self._salt, self._kdf)
        nonce = os.urandom(12)
        ct = AESGCM(self._key).encrypt(nonce, klar, kopf)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_bytes(kopf + nonce + ct)
        tmp.replace(self.path)

    # --------------------------------------------------- Schlüssel verwalten

    def list_keys(self) -> list:
        self._require_unlocked()
        return list(self._data["keys"])

    def get_key(self, label: str):
        self._require_unlocked()
        for k in self._data["keys"]:
            if k["label"] == label:
                return k
        return None

    def add_key(self, label: str, typ: str, value, meta: str = ""):
        self._require_unlocked()
        label = (label or "").strip()
        if not label:
            raise ValueError("Bitte einen Namen für den Schlüssel angeben.")
        if any(k["label"] == label for k in self._data["keys"]):
            raise ValueError(f"Es gibt bereits einen Schlüssel mit dem Namen '{label}'.")
        self._data["keys"].append({
            "label": label,
            "typ": typ,
            "wert": value,
            "meta": meta,
            "erstellt": datetime.now().strftime("%Y-%m-%d %H:%M"),
        })
        self._save()

    def delete_key(self, label: str):
        self._require_unlocked()
        self._data["keys"] = [k for k in self._data["keys"] if k["label"] != label]
        self._save()

    def replace_all(self, keys: list):
        """Führt eine Liste von Schlüsseln ein (z.B. aus Obsidian).

        Gleiche Namen werden überschrieben, alles andere bleibt stehen.
        """
        self._require_unlocked()
        nach_label = {k["label"]: k for k in self._data["keys"]}
        for k in keys:
            nach_label[k["label"]] = k
        self._data["keys"] = list(nach_label.values())
        self._save()


def passwort_staerke(passwort: str) -> tuple:
    """Schätzt grob, wie gut ein Passwort ist.

    Gibt (Stufe 0-4, Text) zurück. Das ist bewusst eine einfache Schätzung
    nach Länge und Zeichenvielfalt - keine echte Analyse.
    """
    if not passwort:
        return 0, "leer"
    punkte = 0
    if len(passwort) >= 8:
        punkte += 1
    if len(passwort) >= 12:
        punkte += 1
    if len(passwort) >= 16:
        punkte += 1
    arten = sum([
        any(c.islower() for c in passwort),
        any(c.isupper() for c in passwort),
        any(c.isdigit() for c in passwort),
        any(not c.isalnum() for c in passwort),
    ])
    if arten >= 3:
        punkte += 1
    punkte = min(punkte, 4)
    return punkte, ["sehr schwach", "schwach", "geht so", "gut", "sehr gut"][punkte]


# =============================================================================
#  Freundesliste
# =============================================================================

class FriendsStore:
    def __init__(self, path: Path):
        self.path = Path(path)
        self.data = load_json(self.path, {})
        self._lock = threading.Lock()

    def save(self):
        save_json(self.path, self.data)

    def add(self, friend_id: str, nickname: str = ""):
        friend_id = (friend_id or "").strip().upper()
        if not friend_id:
            raise ValueError("Bitte eine Freundes-ID angeben.")
        with self._lock:
            if friend_id not in self.data:
                self.data[friend_id] = {"nickname": nickname, "shared_key_b64": None}
            elif nickname:
                self.data[friend_id]["nickname"] = nickname
        self.save()

    def remove(self, friend_id: str):
        with self._lock:
            self.data.pop(friend_id, None)
        self.save()

    def set_shared_key(self, friend_id: str, key_b64: str):
        with self._lock:
            if friend_id in self.data:
                self.data[friend_id]["shared_key_b64"] = key_b64
        self.save()

    def __contains__(self, friend_id: str):
        return friend_id in self.data

    def get(self, friend_id: str):
        return self.data.get(friend_id)

    def all(self):
        return dict(self.data)


# =============================================================================
#  Gruppen
# =============================================================================
# Eine Gruppe ist eine Kennung plus ein Schlüssel, den alle Mitglieder haben.
# Wer den Einladungscode bekommt, ist dabei - eine Mitgliederliste gibt es
# bewusst nicht. Das ist der ganze Trick: Niemand muss verwalten, wer dazu
# gehört, und trotzdem kann nur mitlesen, wer den Code hat.
#
# Deshalb gilt aber auch: Ein Code, der einmal weitergegeben wurde, lässt
# sich nicht zurückholen. Wer jemanden loswerden will, macht eine neue
# Gruppe auf. Genau so steht es auch im Programm.

GROUPS_FILE = DATA_DIR / "gruppen.json"

GRUPPEN_PRAEFIX = "G-"
GRUPPEN_CODE_MARKE = "VP4G1-"


def ist_gruppen_id(kennung: str) -> bool:
    """Gruppen-IDs fangen mit "G-" an, Freundes-IDs nie.

    Im Kanal steht in jeder Zeile, für wen sie ist - mal ein Freund, mal eine
    Gruppe. Ohne einen Unterschied, den man ansieht, müsste der Empfänger
    raten, mit welchem Schlüssel er es versuchen soll.
    """
    return (kennung or "").upper().startswith(GRUPPEN_PRAEFIX)


def gruppen_id_erzeugen() -> str:
    """Eine neue Gruppenkennung der Form G-XXXXXXXX."""
    roh = base64.b32encode(os.urandom(5)).decode("ascii").rstrip("=")
    return GRUPPEN_PRAEFIX + roh[:8]


def gruppen_code_bauen(gruppen_id: str, key_b64: str) -> str:
    """Packt Kennung und Schlüssel in einen einzigen Text zum Weitergeben.

    Beides wandert als rohe Bytes hinein - 5 für die Kennung, 32 für den
    Schlüssel -, nicht als lesbarer Text. Der Code wird dadurch nur halb so
    lang und passt in eine Zeile einer Nachricht; das ist der Unterschied
    zwischen "kurz rüberschicken" und "der zerreißt beim Kopieren".
    """
    teil = gruppen_id[len(GRUPPEN_PRAEFIX):]
    kennung = base64.b32decode(teil + "=" * (-len(teil) % 8))[:5]
    roh = kennung + base64.b64decode(key_b64)
    return GRUPPEN_CODE_MARKE + base64.urlsafe_b64encode(roh).decode("ascii").rstrip("=")


def gruppen_code_lesen(code: str):
    """Gegenstück zu gruppen_code_bauen(). Rückgabe: (gruppen_id, key_b64).

    Löst ValueError aus, wenn der Code nicht stimmt - jemand hat ihn beim
    Kopieren abgeschnitten, oder es war gar keiner.
    """
    code = (code or "").strip()
    if not code.startswith(GRUPPEN_CODE_MARKE):
        raise ValueError(
            "Das sieht nicht nach einem Gruppen-Code aus.\n\n"
            "Ein Code fängt mit 'VP4G1-' an. Lass ihn dir noch einmal "
            "schicken und kopier ihn ganz.")
    roh_text = code[len(GRUPPEN_CODE_MARKE):]
    try:
        roh = base64.urlsafe_b64decode(roh_text + "=" * (-len(roh_text) % 4))
        if len(roh) != 37:
            raise ValueError
        gruppen_id = GRUPPEN_PRAEFIX + base64.b32encode(roh[:5]).decode("ascii")
        key_b64 = base64.b64encode(roh[5:]).decode("ascii")
    except Exception:
        raise ValueError(
            "Der Gruppen-Code ist unvollständig oder beschädigt.\n\n"
            "Meistens fehlt beim Kopieren das Ende. Lass ihn dir noch "
            "einmal schicken.")
    return gruppen_id, key_b64


class GruppenStore:
    """Die Gruppen, in denen man ist - Kennung, Name und Schlüssel."""

    def __init__(self, path: Path):
        self.path = Path(path)
        self.data = load_json(self.path, {})
        self._lock = threading.Lock()

    def save(self):
        save_json(self.path, self.data)

    def erstellen(self, name: str) -> str:
        """Legt eine neue Gruppe an und gibt ihre Kennung zurück."""
        from kern.krypto import ModernCrypto

        gruppen_id = gruppen_id_erzeugen()
        with self._lock:
            self.data[gruppen_id] = {
                "name": (name or "").strip() or "Gruppe",
                "key_b64": ModernCrypto.generate_aes_key(),
            }
        self.save()
        return gruppen_id

    def beitreten(self, code: str, name: str = "") -> str:
        """Tritt über einen Einladungscode bei. Rückgabe: die Kennung."""
        gruppen_id, key_b64 = gruppen_code_lesen(code)
        with self._lock:
            vorher = self.data.get(gruppen_id, {})
            self.data[gruppen_id] = {
                "name": (name or "").strip() or vorher.get("name") or "Gruppe",
                "key_b64": key_b64,
            }
        self.save()
        return gruppen_id

    def verlassen(self, gruppen_id: str):
        with self._lock:
            self.data.pop(gruppen_id, None)
        self.save()

    def code(self, gruppen_id: str) -> str:
        gruppe = self.data.get(gruppen_id)
        if not gruppe:
            raise ValueError("Diese Gruppe gibt es nicht (mehr).")
        return gruppen_code_bauen(gruppen_id, gruppe["key_b64"])

    def __contains__(self, gruppen_id: str):
        return gruppen_id in self.data

    def get(self, gruppen_id: str):
        return self.data.get(gruppen_id)

    def all(self):
        return dict(self.data)


# =============================================================================
#  Obsidian-Verknüpfung
# =============================================================================

class ObsidianSync:
    NOTE_NAME = "VP4 Schluessel.md"
    START_MARK = "<!-- VP4-ANFANG - nicht von Hand aendern -->"
    END_MARK = "<!-- VP4-ENDE -->"

    def __init__(self, vault_path: str):
        # Ein leerer Pfad muss abgelehnt werden, bevor daraus ein Path wird:
        # Path("") ist in Python das AKTUELLE Verzeichnis und besteht die
        # is_dir()-Prüfung anstandslos. Dadurch hat ein Export mit leerem
        # Vault-Feld die Schlüsseldatei stillschweigend in den
        # Programmordner geschrieben - bei einem Projekt auf GitHub wären
        # die Schlüssel damit beim nächsten Hochladen öffentlich gewesen.
        if not (vault_path or "").strip():
            raise ValueError(
                "Es ist noch kein Obsidian-Ordner ausgewählt.\n\n"
                "Wähle oben über 'Durchsuchen …' deinen Vault aus.")
        self.vault_path = Path(vault_path)
        if not self.vault_path.is_dir():
            raise ValueError(f"Der Ordner '{vault_path}' existiert nicht.")

    @property
    def note_path(self) -> Path:
        return self.vault_path / self.NOTE_NAME

    # ------------------------------------------------- Kodieren / Dekodieren

    @staticmethod
    def _wert_kodieren(wert) -> str:
        """Macht aus einem Schlüssel einen einzeiligen, tabellensicheren Text.

        Eine Zelle in einer Markdown-Tabelle darf weder einen Zeilenumbruch
        noch ein "|" enthalten - beides würde die Tabelle zerreißen. Deshalb
        wird beides umkehrbar ersetzt, und der Backslash selbst gleich mit,
        damit die Rückwandlung eindeutig bleibt.

        Gekürzt wird hier bewusst NICHT: ein RSA-Schlüssel ist im PEM-Format
        rund 1700 Zeichen lang und enthält knapp 30 Zeilenumbrüche. Früher
        wurde er auf 120 Zeichen abgeschnitten - der Export sah ordentlich
        aus, aber der Schlüssel war beim Zurücklesen unbrauchbar, und damit
        auch alles, was mit ihm verschlüsselt wurde.
        """
        return (str(wert)
                .replace("\\", "\\\\")
                .replace("\r\n", "\\n")
                .replace("\n", "\\n")
                .replace("\r", "\\n")
                .replace("|", "\\|"))

    @staticmethod
    def _wert_dekodieren(wert: str) -> str:
        """Macht _wert_kodieren() wieder rückgängig."""
        out = []
        i = 0
        while i < len(wert):
            ch = wert[i]
            if ch == "\\" and i + 1 < len(wert):
                folgt = wert[i + 1]
                if folgt == "n":
                    out.append("\n")
                    i += 2
                    continue
                if folgt in ("|", "\\"):
                    out.append(folgt)
                    i += 2
                    continue
            out.append(ch)
            i += 1
        return "".join(out)

    @staticmethod
    def _zeile_aufteilen(zeile: str) -> list:
        """Teilt eine Zeile der Markdown-Tabelle an den echten Trennstrichen.

        Ein "|", das zum Inhalt gehört, steht als "\\|" in der Zeile und ist
        kein Spaltentrenner. Ein einfaches split("|") würde die Zeile an
        dieser Stelle fälschlich auseinanderreißen.
        """
        spalten = []
        aktuell = []
        i = 0
        while i < len(zeile):
            ch = zeile[i]
            if ch == "\\" and i + 1 < len(zeile):
                aktuell.append(ch)
                aktuell.append(zeile[i + 1])
                i += 2
                continue
            if ch == "|":
                spalten.append("".join(aktuell))
                aktuell = []
                i += 1
                continue
            aktuell.append(ch)
            i += 1
        spalten.append("".join(aktuell))
        return spalten

    # ------------------------------------------------------ Export / Import

    def export_keys(self, keys: list) -> Path:
        lines = [self.START_MARK, ""]
        lines.append(f"Zuletzt synchronisiert: {datetime.now().strftime('%Y-%m-%d %H:%M')}")
        lines.append("")
        lines.append("> [!warning] Diese Notiz enthält deine Schlüssel im Klartext.")
        lines.append("> Teile diesen Vault nur mit Personen/Diensten, denen du vertraust.")
        lines.append("")
        lines.append("| Name | Typ | Wert | Notiz | Erstellt |")
        lines.append("|---|---|---|---|---|")
        for k in keys:
            wert = self._wert_kodieren(k["wert"])
            meta = self._wert_kodieren(k.get("meta", ""))
            lines.append(f"| {k['label']} | {k['typ']} | `{wert}` | {meta} | {k.get('erstellt', '')} |")
        lines.append("")
        lines.append(self.END_MARK)
        new_block = "\n".join(lines)

        if self.note_path.exists():
            existing = self.note_path.read_text(encoding="utf-8")
            if self.START_MARK in existing and self.END_MARK in existing:
                before = existing.split(self.START_MARK)[0]
                after = existing.split(self.END_MARK)[-1]
                content = before + new_block + after
            else:
                # Bestehende, von Hand geschriebene Notiz -> Block ergänzen, nichts löschen.
                content = existing.rstrip() + "\n\n" + new_block + "\n"
        else:
            content = f"# Verschlüsselungs-Schlüssel (VP4)\n\n{new_block}\n"

        self.note_path.write_text(content, encoding="utf-8")
        return self.note_path

    def import_keys(self) -> list:
        if not self.note_path.exists():
            raise ValueError(f"Keine Datei '{self.NOTE_NAME}' im Vault gefunden.")
        content = self.note_path.read_text(encoding="utf-8")
        imported = []
        beschaedigt = []
        for line in content.splitlines():
            line = line.strip()
            if not line.startswith("|") or line.startswith("|---") or line.startswith("| Name |"):
                continue
            cols = self._zeile_aufteilen(line)
            # Vor dem ersten und hinter dem letzten "|" steht nichts - weg damit.
            # (Nicht mit line.strip("|") machen: das würde einem Wert, der auf
            # ein escaptes "\|" endet, den Strich wegschneiden.)
            if cols and not cols[0].strip():
                cols = cols[1:]
            if cols and not cols[-1].strip():
                cols = cols[:-1]
            cols = [c.strip() for c in cols]
            if len(cols) < 5:
                continue
            label, typ, wert, meta, erstellt = cols[0], cols[1], cols[2], cols[3], cols[4]
            # Der Wert steht beim Export in Backticks - genau ein Paar entfernen.
            if len(wert) >= 2 and wert.startswith("`") and wert.endswith("`"):
                wert = wert[1:-1]
            wert = self._wert_dekodieren(wert)
            meta = self._wert_dekodieren(meta)
            if not label:
                continue
            # Notizen, die noch mit einer älteren Programmversion geschrieben
            # wurden, können abgeschnittene Schlüssel enthalten - erkennbar am
            # angehängten "...". Die sind nicht mehr zu retten. Besser einmal
            # deutlich sagen als stillschweigend einen kaputten Schlüssel
            # zurückgeben, mit dem sich später nichts mehr entschlüsseln lässt.
            if wert.endswith("..."):
                beschaedigt.append(label)
            imported.append({"label": label, "typ": typ, "wert": wert,
                             "meta": meta, "erstellt": erstellt})
        if beschaedigt:
            raise ValueError(
                "Diese Schlüssel wurden von einer älteren Programmversion beim "
                "Export abgeschnitten und sind unvollständig:\n\n  - "
                + "\n  - ".join(beschaedigt)
                + "\n\nSie lassen sich nicht mehr reparieren. Falls du sie noch "
                  "im Schlüsselspeicher des Programms hast, exportiere sie "
                  "bitte einmal neu - dann werden sie vollständig geschrieben."
            )
        return imported
