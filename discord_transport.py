#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
=====================================================================
 discord_transport.py - der zweite Weg: Chat über das Internet
=====================================================================
Der Chat in chat.py läuft nur im selben WLAN. Dieses Modul schickt
dieselben verschlüsselten Nachrichten stattdessen durch einen
Discord-Textkanal - damit funktioniert der Chat auch zwischen zwei
PCs, die kilometerweit auseinander stehen.

Verschlüsselt wird NICHT hier. Das machen dieselben Funktionen wie im
WLAN-Chat (chat.payload_verschluesseln / chat.payload_entschluesseln).
Discord bekommt nur den fertigen Geheimtext zu sehen.

WIE DIE ZUORDNUNG FUNKTIONIERT
-------------------------------
Alle Freunde benutzen denselben Bot und denselben Kanal. Jede
Installation sieht also auch alles, was für andere bestimmt ist.
Deshalb trägt jede Zeile im Klartext, von wem sie kommt und für wen
sie ist:

    VP4D1|<von>|<an>|<nachrichten-id>|<teil>|<gesamt>|<typ>|<base64>

Nur der Kopf ist offen. Die eigentliche Nachricht dahinter ist
AES-256-GCM und ohne den gemeinsamen Schlüssel nicht zu lesen - auch
nicht für die anderen Freunde im selben Kanal.

GRUPPEN
--------
Im Feld <an> steht entweder eine Freundes-ID oder eine Gruppenkennung
(G-XXXXXXXX). Bei einer Gruppe entschlüsselt der Gruppenschlüssel, und
es darf auch jemand schreiben, der nicht in der Freundesliste steht -
in einer Gruppe zählt der Code, nicht die Bekanntschaft. Der Name des
Absenders reist dabei in der verschlüsselten Nutzlast mit
(TYP_GRUPPENTEXT), sonst stünde im Chat nur eine ID.

ÜBER DISCORD GEHT NUR VERSCHLÜSSELTES
--------------------------------------
Wer keinen gemeinsamen Schlüssel mit dem Empfänger hinterlegt hat,
kann über diesen Weg gar nichts senden. Im WLAN wäre unverschlüsselt
noch vertretbar - da liest bestenfalls mit, wer gerade im selben
Netz sitzt. Ein Discord-Kanal ist etwas anderes: dort liest jedes
Mitglied des Servers mit, Discord speichert alles dauerhaft, und
gelöscht ist es damit noch lange nicht. Klartext dorthin zu schicken
wäre eine ganz andere Hausnummer, deshalb wird es abgelehnt statt
still gemacht.

WAS DIESER WEG NICHT LEISTET
-----------------------------
Wer im Kanal mitliest, sieht: wer wann wem wie viel schreibt. Diese
Verkehrsdaten sind offen, und Discord sammelt sie mit. Der Inhalt ist
geschützt, die Tatsache des Schreibens nicht.

Fälschen kann trotzdem niemand etwas: Wer sich mit fremdem "von" in
den Kanal setzt, scheitert an der Prüfsumme von AES-GCM - ohne den
gemeinsamen Schlüssel kommt keine lesbare Nachricht zustande.
=====================================================================
"""

import asyncio
import base64
import json
import os
import threading
import time

import chat
from kern import speicher


# Discord lässt höchstens 2000 Zeichen pro Nachricht zu. 1900 lässt Luft
# für den Kopf und dafür, dass Discord bei manchen Zeichen anders zählt
# als Python.
ZEILEN_LIMIT = 1900

MARKE = "VP4D1"
TYP_TEXT = "T"
TYP_DATEI = "M"
# Text in einer Gruppe. Anders als bei TYP_TEXT steckt in der Nutzlast noch
# der Name des Absenders: In einer Gruppe hat niemand den anderen in seiner
# Freundesliste, sonst stünde dort nur "MAXX-0002" statt "Max".
TYP_GRUPPENTEXT = "G"

# Ein kostenloser Discord-Server nimmt 10 MiB pro Anhang. Verschlüsseln
# legt 28 Byte drauf; mit etwas Abstand sind 8 MB Nutzdaten sicher drin.
MAX_DISCORD_DATEI = 8 * 1024 * 1024

# Angefangene Nachrichten, deren restliche Teile nie ankamen, fliegen nach
# dieser Zeit aus dem Zwischenspeicher.
TEILE_TIMEOUT = 300


# =============================================================================
#  Einstellungen (vp4_daten/discord.json)
# =============================================================================

DISCORD_CONFIG_FILE = speicher.DATA_DIR / "discord.json"

STANDARD_DISCORD = {
    "bot_token": "",
    "kanal_id": "",
}


def _eingebaute_werte() -> dict:
    """Was beim Bauen der .exe fest eingesetzt wurde - oder nichts.

    Aus dem Quelltext heraus ist discord_konfig.py leer; in der fertigen
    .exe stehen dort die Werte aus den GitHub-Secrets. Dadurch müssen
    Freunde nichts eintragen: .exe starten und schreiben.
    """
    try:
        import discord_konfig
    except ImportError:
        return {}
    werte = {}
    if getattr(discord_konfig, "BOT_TOKEN", "").strip():
        werte["bot_token"] = discord_konfig.BOT_TOKEN.strip()
    if str(getattr(discord_konfig, "KANAL_ID", "")).strip():
        werte["kanal_id"] = str(discord_konfig.KANAL_ID).strip()
    return werte


def discord_config_laden() -> dict:
    """Die geltenden Zugangsdaten.

    Reihenfolge: erst die eingebauten Werte, darüber das, was der Benutzer
    unter Einstellungen → Discord eingetragen hat. Eigene Eingaben gewinnen
    also immer - sonst käme man aus einem eingebauten, aber gesperrten Bot
    nie wieder heraus.
    """
    cfg = dict(STANDARD_DISCORD)
    cfg.update(_eingebaute_werte())
    eigene = speicher.load_json(DISCORD_CONFIG_FILE, {})
    cfg.update({k: v for k, v in eigene.items() if str(v).strip()})
    return cfg


def discord_config_speichern(cfg: dict):
    """Schreibt Token und Kanal-ID.

    Wie speicher.save_config() schreibt auch das im Testmodus nichts - sonst
    würde ein Testlauf die echten Zugangsdaten überschreiben. Genau dieser
    Fehler hat beim Obsidian-Ordner schon einmal echten Schaden angerichtet.
    """
    if os.environ.get("VP4_TESTMODUS"):
        return
    speicher.save_json(DISCORD_CONFIG_FILE, cfg)


def ist_eingerichtet() -> bool:
    cfg = discord_config_laden()
    return bool(cfg.get("bot_token")) and bool(str(cfg.get("kanal_id") or "").strip())


# =============================================================================
#  Das Protokoll - ohne Netz, damit es sich prüfen lässt
# =============================================================================

class DiscordProtokoll:
    """Baut die Zeilen für den Kanal und setzt eingehende wieder zusammen.

    Diese Klasse fasst kein Netzwerk an. Sie bekommt Text und gibt Zeilen
    zurück, und umgekehrt. Damit lässt sich der ganze Ablauf - aufteilen,
    verschlüsseln, zusammensetzen, entschlüsseln - im Selbsttest prüfen,
    ohne dass eine Verbindung zu Discord nötig wäre.
    """

    def __init__(self, my_id: str, friends, gruppen=None):
        self.my_id = my_id
        self.friends = friends
        # Die Gruppen, in denen wir sind. Eine Zeile an eine Gruppe ist nicht
        # an uns adressiert und käme sonst nie an.
        self.gruppen = gruppen
        # {(von, nachrichten_id): {"gesamt": int, "teile": {nr: text}, "zeit": float}}
        self._offen = {}

    # ------------------------------------------------------------- Senden

    def _kopf(self, an: str, nid: str, teil: int, gesamt: int, typ: str) -> str:
        return f"{MARKE}|{self.my_id}|{an}|{nid}|{teil}|{gesamt}|{typ}|"

    def zeilen_bauen(self, an: str, nutzlast: bytes, typ: str = TYP_TEXT) -> list:
        """Verschlüsselt die Nutzlast und zerlegt sie in versandfertige Zeilen.

        Löst ValueError aus, wenn für den Freund kein gemeinsamer Schlüssel
        hinterlegt ist - über Discord wird nichts im Klartext verschickt.
        """
        verschluesselt, daten = chat.payload_verschluesseln(
            self.friends, an, nutzlast, self.gruppen)
        if not verschluesselt:
            raise ValueError(
                "Über Discord wird nur Verschlüsseltes verschickt, und für diesen "
                "Freund ist kein gemeinsamer Schlüssel hinterlegt.\n\n"
                "Der Kanal ist für alle im Server lesbar und Discord speichert "
                "alles dauerhaft - unverschlüsselt wäre die Nachricht dort für "
                "immer offen einsehbar.\n\n"
                "Trag den Schlüssel über das 🔑 neben der Freundesliste ein.")

        text = base64.b64encode(daten).decode("ascii")
        nid = base64.b32encode(os.urandom(5)).decode("ascii").rstrip("=")

        # Wie viel Nutzlast pro Zeile übrig bleibt, hängt von der Länge des
        # Kopfes ab - und die Teilnummern machen den Kopf länger, je mehr
        # Teile es werden. Deshalb erst grob rechnen, dann mit dem echten
        # Kopf nachschärfen.
        probe = len(self._kopf(an, nid, 99, 99, typ))
        pro_zeile = ZEILEN_LIMIT - probe
        gesamt = max(1, -(-len(text) // pro_zeile))
        probe = len(self._kopf(an, nid, gesamt, gesamt, typ))
        pro_zeile = ZEILEN_LIMIT - probe
        gesamt = max(1, -(-len(text) // pro_zeile))

        zeilen = []
        for i in range(gesamt):
            stueck = text[i * pro_zeile:(i + 1) * pro_zeile]
            zeilen.append(self._kopf(an, nid, i + 1, gesamt, typ) + stueck)
        return zeilen

    # ---------------------------------------------------------- Empfangen

    def zeile_lesen(self, zeile: str):
        """Nimmt eine Zeile aus dem Kanal entgegen.

        Rückgabe:
            None                          - nicht für uns, unbrauchbar, oder
                                            es fehlen noch Teile
            (von, typ, klartext, gruppe)  - die Nachricht ist vollständig und
                                            entschlüsselt; gruppe ist die
                                            Gruppenkennung oder None

        Löst ValueError aus, wenn eine vollständige Nachricht sich nicht
        entschlüsseln lässt. Der Aufrufer meldet das dem Benutzer - eine
        einzelne unlesbare Nachricht darf den Empfang nicht anhalten.
        """
        self._aufraeumen()

        if not zeile or not zeile.startswith(MARKE + "|"):
            return None
        teile = zeile.split("|", 7)
        if len(teile) < 8:
            return None
        _, von, an, nid, teil_s, gesamt_s, typ, nutzlast = teile

        if von == self.my_id:
            return None            # das eigene Echo aus dem Kanal

        in_gruppe = (self.gruppen is not None
                     and speicher.ist_gruppen_id(an) and an in self.gruppen)
        if in_gruppe:
            # In einer Gruppe zählt der Code, nicht die Freundesliste: Wer den
            # Schlüssel hat, gehört dazu - auch wenn wir ihn noch nie gesehen
            # haben. Ohne das könnte man in einer Gruppe nur mit Leuten
            # schreiben, die man vorher einzeln hinzugefügt hat, und die
            # Gruppe wäre sinnlos.
            pass
        elif an != self.my_id:
            return None            # gehört einem anderen Freund
        elif von not in self.friends:
            return None            # von jemandem, den wir nicht kennen

        try:
            teil = int(teil_s)
            gesamt = int(gesamt_s)
        except ValueError:
            return None
        if teil < 1 or gesamt < 1 or teil > gesamt:
            return None

        schluessel = (von, nid)
        eintrag = self._offen.get(schluessel)
        if eintrag is None or eintrag["gesamt"] != gesamt:
            eintrag = {"gesamt": gesamt, "teile": {}, "zeit": time.time()}
            self._offen[schluessel] = eintrag
        eintrag["teile"][teil] = nutzlast
        eintrag["zeit"] = time.time()

        if len(eintrag["teile"]) < gesamt:
            return None

        del self._offen[schluessel]
        text = "".join(eintrag["teile"][i] for i in range(1, gesamt + 1))
        try:
            roh = base64.b64decode(text)
        except Exception:
            raise ValueError("Eine Nachricht kam beschädigt an.")
        # Bei einer Gruppe entschlüsselt der Gruppenschlüssel, sonst der des
        # Absenders - deshalb geht hier "an" hinein und nicht "von".
        ziel = an if in_gruppe else von
        klartext = chat.payload_entschluesseln(
            self.friends, ziel, True, roh, self.gruppen)
        return von, typ, klartext, (an if in_gruppe else None)

    def _aufraeumen(self):
        """Wirft Nachrichten weg, deren fehlende Teile nie gekommen sind."""
        jetzt = time.time()
        alt = [k for k, v in self._offen.items() if jetzt - v["zeit"] > TEILE_TIMEOUT]
        for k in alt:
            del self._offen[k]


# =============================================================================
#  Der Bot - Gateway-Verbindung in einem eigenen Thread
# =============================================================================

class DiscordTransport:
    """Hält die Verbindung zu Discord und verschickt darüber Nachrichten.

    Nach aussen sieht die Klasse aus wie ChatNetwork: start(), stop(),
    send_text(), send_file(). Die Ereignisse landen in derselben Queue, die
    auch der WLAN-Chat benutzt - die Oberfläche merkt nicht, welcher Weg
    eine Nachricht gebracht hat.

    discord.py arbeitet mit asyncio, die Oberfläche mit Threads. Deshalb
    läuft die ganze Bibliothek in einem eigenen Thread mit eigener
    Ereignisschleife, und Senden aus der Oberfläche geht über
    asyncio.run_coroutine_threadsafe().
    """

    def __init__(self, my_id: str, friends, event_queue, bot_token: str,
                 kanal_id: int, gruppen=None, anzeigename: str = ""):
        self.my_id = my_id
        self.friends = friends
        self.gruppen = gruppen
        self.anzeigename = (anzeigename or "").strip()
        self.events = event_queue
        self.bot_token = bot_token
        self.kanal_id = int(kanal_id)

        self.protokoll = DiscordProtokoll(my_id, friends, gruppen)
        self.verbunden = False

        self._client = None
        self._loop = None
        self._thread = None
        self._running = False

    # ---------------------------------------------------------- Start/Stop

    def start(self):
        if self._running:
            return
        if os.environ.get("VP4_TESTMODUS"):
            # Sonst würde jeder Selbsttestlauf sich mit dem echten Bot in den
            # echten Kanal hängen, sobald in vp4_daten/discord.json Zugangs-
            # daten stehen - und auf einem Rechner ohne Internet minutenlang
            # in Verbindungsversuchen hängen.
            self.events.put(("info", "Testmodus: keine Verbindung zu Discord."))
            return
        self._running = True
        self._thread = threading.Thread(target=self._thread_laufen, daemon=True)
        self._thread.start()

    def stop(self):
        self._running = False
        self.verbunden = False
        client, loop = self._client, self._loop
        if client is not None and loop is not None:
            try:
                asyncio.run_coroutine_threadsafe(client.close(), loop)
            except Exception:
                pass

    def _thread_laufen(self):
        """Der Thread, in dem discord.py lebt."""
        try:
            import discord
        except ImportError:
            self.events.put(("error",
                "Für den Chat über Discord fehlt das Paket 'discord.py'.\n\n"
                "Installieren mit:\n    pip install discord.py"))
            self._running = False
            return

        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)

        intents = discord.Intents.default()
        # Ohne diese Berechtigung kommen alle Nachrichten leer an. Sie muss
        # zusätzlich im Developer-Portal eingeschaltet werden, sonst lässt
        # Discord den Bot gar nicht erst herein.
        intents.message_content = True
        client = discord.Client(intents=intents)
        self._client = client

        @client.event
        async def on_ready():
            self.verbunden = True
            self.events.put(("discord_bereit", str(client.user)))
            self.events.put(("peer_update", None))

        @client.event
        async def on_message(message):
            await self._nachricht_verarbeiten(message)

        try:
            self._loop.run_until_complete(client.start(self.bot_token))
        except Exception as e:
            if self._running:
                self._fehler_melden(e)
        finally:
            self.verbunden = False
            try:
                self._loop.run_until_complete(self._loop.shutdown_asyncgens())
                self._loop.close()
            except Exception:
                pass
            self._client = None
            self._loop = None

    def _fehler_melden(self, e: Exception):
        """Übersetzt die Ausnahmen von discord.py in etwas Verständliches.

        Ohne das stünde in der Statusleiste ein englischer Klassenname, und
        in der .exe ohne Konsole sähe man gar nichts - genau der Fehler, der
        beim WLAN-Chat schon einmal alles unsichtbar gemacht hat.
        """
        import discord

        if isinstance(e, discord.LoginFailure):
            text = ("Discord hat den Bot-Token abgelehnt.\n\n"
                    "Prüf ihn in den Einstellungen. Wenn du ihn im "
                    "Developer-Portal zurückgesetzt hast, gilt der alte "
                    "nicht mehr.")
        elif isinstance(e, discord.PrivilegedIntentsRequired):
            text = ("Dem Bot fehlt die Berechtigung, Nachrichten zu lesen.\n\n"
                    "So schaltest du sie ein:\n"
                    "discord.com/developers/applications - deine App - Bot -\n"
                    "'MESSAGE CONTENT INTENT' einschalten und speichern.\n\n"
                    "Ohne das kommen alle Nachrichten leer an.")
        elif isinstance(e, (OSError, discord.ConnectionClosed,
                            discord.GatewayNotFound)):
            text = ("Die Verbindung zu Discord ist abgebrochen.\n\n"
                    f"Bist du online? (Technisch: {e})")
        else:
            text = f"Der Chat über Discord ist gestoppt.\n\n(Technisch: {e})"
        self.events.put(("error", text))

    # ----------------------------------------------------------- Empfangen

    async def _nachricht_verarbeiten(self, message):
        """Wird für jede Nachricht im Kanal aufgerufen."""
        if message.channel.id != self.kanal_id:
            return
        try:
            gelesen = self.protokoll.zeile_lesen(message.content or "")
        except ValueError:
            # Sie war für uns, ließ sich aber nicht entschlüsseln. Genau wie
            # im WLAN-Chat: nur diese eine Nachricht verwerfen, der Empfang
            # läuft weiter.
            self.events.put(("error",
                "Eine Nachricht über Discord konnte nicht entschlüsselt werden. "
                "Habt ihr beide denselben gemeinsamen Schlüssel hinterlegt?"))
            return
        except Exception:
            return

        if gelesen is None:
            return
        von, typ, klartext, gruppe = gelesen

        if typ in (TYP_TEXT, TYP_GRUPPENTEXT):
            name = None
            text = klartext.decode("utf-8", errors="replace")
            if typ == TYP_GRUPPENTEXT:
                try:
                    inhalt = json.loads(text)
                    text = str(inhalt["t"])
                    # Der Name ist eine Selbstauskunft des Absenders - nachprüfen
                    # kann ihn niemand. Deshalb geht ein Name, den man selbst für
                    # diese ID vergeben hat, in der Oberfläche immer vor.
                    name = str(inhalt.get("n") or "")[:40] or None
                except Exception:
                    pass
            self.events.put(("message", {
                "from": von,
                "text": text,
                "name": name,
                "encrypted": True,
                "weg": "discord",
                "gruppe": gruppe,
            }))
        elif typ == TYP_DATEI:
            await self._datei_empfangen(message, von, klartext, gruppe)

    async def _datei_empfangen(self, message, von: str, meta_roh: bytes,
                               gruppe=None):
        """Holt den Anhang, entschlüsselt ihn und legt ihn im Empfangsordner ab."""
        try:
            meta = json.loads(meta_roh.decode("utf-8"))
        except Exception:
            self.events.put(("error", "Eine Dateiankündigung über Discord war unlesbar."))
            return
        if not message.attachments:
            self.events.put(("error",
                f"'{meta.get('name', 'Eine Datei')}' wurde angekündigt, aber der "
                f"Anhang fehlt."))
            return

        anhang = message.attachments[0]
        if anhang.size > MAX_DISCORD_DATEI + 65536:
            self.events.put(("error", "Ein Anhang über Discord war zu groß."))
            return

        try:
            roh = await anhang.read()
        except Exception as e:
            self.events.put(("error", f"Ein Anhang ließ sich nicht laden. (Technisch: {e})"))
            return

        try:
            daten = chat.payload_entschluesseln(
                self.friends, gruppe or von, True, roh, self.gruppen)
        except Exception:
            self.events.put(("error",
                "Eine Datei über Discord konnte nicht entschlüsselt werden. Habt "
                "ihr beide denselben gemeinsamen Schlüssel hinterlegt?"))
            return

        ziel = chat.zieldatei(meta.get("name"))
        try:
            ziel.write_bytes(daten)
        except OSError as e:
            self.events.put(("error", f"Die Datei ließ sich nicht speichern. (Technisch: {e})"))
            return

        self.events.put(("file", {"from": von, "path": str(ziel),
                                  "kind": meta.get("kind", "datei"),
                                  "name": meta.get("name", ziel.name),
                                  "weg": "discord",
                                  "gruppe": gruppe}))

    # -------------------------------------------------------------- Senden

    def _kanal(self):
        kanal = self._client.get_channel(self.kanal_id)
        if kanal is None:
            raise ConnectionError(
                f"Den Discord-Kanal {self.kanal_id} findet der Bot nicht.\n\n"
                f"Ist die Kanal-ID richtig, und ist der Bot auf dem Server, zu "
                f"dem der Kanal gehört?")
        return kanal

    def _im_loop(self, coro, timeout: float):
        """Führt eine Coroutine im Discord-Thread aus und wartet auf sie."""
        if not self.verbunden or self._loop is None:
            coro.close()
            raise ConnectionError(
                "Der Chat über Discord ist gerade nicht verbunden.\n\n"
                "Bist du online, und stimmen Bot-Token und Kanal-ID in den "
                "Einstellungen?")
        zukunft = asyncio.run_coroutine_threadsafe(coro, self._loop)
        try:
            return zukunft.result(timeout=timeout)
        except TimeoutError:
            zukunft.cancel()
            raise ConnectionError("Discord hat zu lange nicht geantwortet.")

    def send_text(self, friend_id: str, text: str) -> bool:
        if speicher.ist_gruppen_id(friend_id):
            nutzlast = json.dumps(
                {"n": self.anzeigename or self.my_id, "t": text},
                ensure_ascii=False).encode("utf-8")
            typ = TYP_GRUPPENTEXT
        else:
            nutzlast, typ = text.encode("utf-8"), TYP_TEXT
        zeilen = self.protokoll.zeilen_bauen(friend_id, nutzlast, typ)
        self._im_loop(self._zeilen_senden(zeilen), timeout=15 + 5 * len(zeilen))
        return True

    async def _zeilen_senden(self, zeilen):
        kanal = self._kanal()
        for zeile in zeilen:
            # Wenn Discord bremst, wartet discord.py von selbst ab und
            # schickt danach weiter - darum kümmern wir uns hier nicht.
            await kanal.send(zeile)

    def send_file(self, friend_id: str, filepath: str, kind: str) -> bool:
        groesse = os.path.getsize(filepath)
        if groesse > MAX_DISCORD_DATEI:
            raise ValueError(
                f"'{os.path.basename(filepath)}' ist {groesse / 1024 / 1024:.1f} MB "
                f"groß. Über Discord gehen höchstens "
                f"{MAX_DISCORD_DATEI // 1024 // 1024} MB.\n\n"
                f"Im selben WLAN sind bis zu 50 MB möglich. Sonst: die Datei auf "
                f"der Seite 'Dateien' verschlüsseln und die .vp4-Datei anders "
                f"übertragen.")

        name = os.path.basename(filepath)
        meta = {"kind": kind, "name": name, "size": groesse}
        zeilen = self.protokoll.zeilen_bauen(
            friend_id, json.dumps(meta).encode("utf-8"), TYP_DATEI)
        if len(zeilen) != 1:
            # Kann bei den kurzen Metadaten nicht passieren; falls doch,
            # lieber abbrechen als einen Anhang ohne Ankündigung schicken.
            raise ValueError("Der Dateiname ist zu lang für den Versand über Discord.")

        with open(filepath, "rb") as f:
            roh = f.read()
        verschluesselt, daten = chat.payload_verschluesseln(
            self.friends, friend_id, roh, self.gruppen)
        if not verschluesselt:
            raise ValueError(
                "Über Discord werden nur verschlüsselte Dateien verschickt, und "
                "für diesen Freund ist kein gemeinsamer Schlüssel hinterlegt.")

        self._im_loop(self._datei_senden(zeilen[0], daten),
                      timeout=60 + groesse / (50 * 1024))
        return True

    async def _datei_senden(self, zeile: str, daten: bytes):
        import io

        import discord

        kanal = self._kanal()
        # Der echte Dateiname steht verschlüsselt in der Ankündigung. Der
        # Anhang selbst heißt neutral - so wie im .vp4-Container auch.
        anhang = discord.File(io.BytesIO(daten), filename="nachricht.vp4d")
        await kanal.send(zeile, file=anhang)

    # ------------------------------------------------------------ Zustand

    def erreichbar(self, friend_id: str) -> bool:
        """Über Discord ist jeder bekannte Freund erreichbar, sobald wir online sind.

        Anders als im WLAN gibt es hier kein "ist gerade da": Discord hebt die
        Nachricht auf, bis der andere sein VP4 öffnet. Ob er sie schon gelesen
        hat, wissen wir nicht - deshalb wird auch nichts anderes behauptet.
        """
        return self.verbunden and friend_id in self.friends
