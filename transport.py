#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
=====================================================================
 transport.py - welcher Weg nimmt eine Nachricht?
=====================================================================
VP4 hat seit der Discord-Erweiterung zwei Wege, eine Nachricht zum
Freund zu bringen:

    chat.ChatNetwork                 direkt übers WLAN
    discord_transport.DiscordTransport   über einen Discord-Kanal

Der ChatVermittler hier hält beide und entscheidet, welcher benutzt
wird. Nach aussen sieht er genauso aus wie ChatNetwork allein - die
Oberfläche ruft weiterhin send_text(), send_file(), start(), stop()
auf und weiß nichts von der Aufteilung.

DIE DREI BETRIEBSARTEN
-----------------------
    "lan"      nur WLAN. So verhält sich VP4 wie vorher, und das ist
               weiterhin die Voreinstellung.
    "discord"  nur über Discord.
    "beide"    WLAN, wenn der Freund gerade im selben Netz sichtbar
               ist - sonst Discord.

GRUPPEN GEHEN IMMER ÜBER DISCORD
---------------------------------
Eine Gruppe hat keine Adresse im WLAN. Wer dazugehört, steht nirgends
- dabei zu sein heißt nur, den Code zu haben. Über Discord ist der
Kanal selbst die Verteilung; im WLAN müsste VP4 wissen, an wen es
schicken soll, und das weiß niemand.

WARUM WLAN VORRANG HAT
-----------------------
Im WLAN gehen die Daten direkt von PC zu PC. Nichts verlässt die
Wohnung, es gibt keine Größenbegrenzung durch einen fremden Dienst
und niemanden, der mitschreibt, wer wann wem geschrieben hat. Der Weg
über Discord ist der Ersatz für den Fall, dass das nicht geht - nicht
der bequemere Standard.
=====================================================================
"""

import chat
import discord_transport
from kern import speicher


MODI = ("lan", "discord", "beide")

MODUS_NAMEN = {
    "lan": "Nur lokales WLAN",
    "discord": "Nur Discord (Internet)",
    "beide": "Beide - WLAN bevorzugt",
}


class ChatVermittler:
    """Führt WLAN-Chat und Discord-Chat unter einer Oberfläche zusammen."""

    def __init__(self, my_id: str, friends, event_queue, modus: str = "lan",
                 discord_cfg: dict = None, gruppen=None, anzeigename: str = "",
                 **lan_args):
        self.my_id = my_id
        self.friends = friends
        self.gruppen = gruppen
        self.anzeigename = anzeigename
        self.events = event_queue
        self.modus = modus if modus in MODI else "lan"

        self.lan = chat.ChatNetwork(my_id, friends, event_queue, **lan_args)
        self.discord = None
        self._discord_cfg = discord_cfg or {}

    # ------------------------------------------------------- Start / Stop

    def start(self):
        if self.modus in ("lan", "beide"):
            self.lan.start()
        if self.modus in ("discord", "beide"):
            self._discord_starten()

    def stop(self):
        self.lan.stop()
        self._discord_stoppen()

    def _discord_starten(self):
        cfg = self._discord_cfg or discord_transport.discord_config_laden()
        token = (cfg.get("bot_token") or "").strip()
        kanal = str(cfg.get("kanal_id") or "").strip()
        if not token or not kanal:
            if self.modus == "beide":
                # "beide" ist die Voreinstellung. Wer VP4 aus dem Quelltext
                # startet und Discord nie eingerichtet hat, soll deswegen
                # nicht bei jedem Start eine rote Meldung sehen - das WLAN
                # funktioniert ja. Nur wer Discord ausdrücklich wählt, muss
                # erfahren, dass dafür noch etwas fehlt.
                self.events.put(("info",
                    "Chat läuft im WLAN. Für den Weg über Discord fehlen noch "
                    "Bot-Token und Kanal-ID (Einstellungen → Discord)."))
                return
            self.events.put(("error",
                "Für den Chat über Discord fehlen noch Bot-Token und Kanal-ID.\n\n"
                "Du trägst sie unter Einstellungen → Discord ein."))
            return
        try:
            kanal_id = int(kanal)
        except ValueError:
            self.events.put(("error",
                f"'{kanal}' ist keine gültige Kanal-ID. Erwartet wird eine lange "
                f"Zahl.\n\nIn Discord: Einstellungen → Erweitert → Entwicklermodus "
                f"einschalten, dann Rechtsklick auf den Kanal → 'Kanal-ID kopieren'."))
            return

        self.discord = discord_transport.DiscordTransport(
            self.my_id, self.friends, self.events, token, kanal_id,
            gruppen=self.gruppen, anzeigename=self.anzeigename)
        self.discord.start()

    def _discord_stoppen(self):
        if self.discord is not None:
            self.discord.stop()
            self.discord = None

    def modus_setzen(self, modus: str, discord_cfg: dict = None):
        """Schaltet den Transportweg im laufenden Betrieb um."""
        if modus not in MODI:
            return
        if discord_cfg is not None:
            self._discord_cfg = discord_cfg
        self.modus = modus

        if modus in ("lan", "beide"):
            if not self.lan.server_laeuft:
                self.lan.start()
        else:
            self.lan.stop()

        if modus in ("discord", "beide"):
            # Beim Umschalten immer neu aufbauen: Token oder Kanal können
            # sich seit dem letzten Start geändert haben.
            self._discord_stoppen()
            self._discord_starten()
        else:
            self._discord_stoppen()

    # ------------------------------------------------------------- Zustand

    @property
    def server_laeuft(self) -> bool:
        if self.modus == "discord":
            return self.discord_verbunden
        return self.lan.server_laeuft

    @property
    def discord_verbunden(self) -> bool:
        return self.discord is not None and self.discord.verbunden

    @property
    def chat_port(self):
        return self.lan.chat_port

    @property
    def broadcast_port(self):
        return self.lan.broadcast_port

    def online_ids(self) -> set:
        """Wer ist gerade direkt im WLAN sichtbar?"""
        if self.modus == "discord":
            return set()
        return self.lan.online_ids()

    def is_online(self, friend_id: str) -> bool:
        return self.erreichbar_ueber(friend_id) is not None

    def erreichbar_ueber(self, friend_id: str):
        """Auf welchem Weg wäre dieser Freund gerade erreichbar?

        Rückgabe: "lan", "discord" oder None. Die Freundesliste macht daraus
        die Anzeige 🟢 / 🌐 / ⚪ - so sieht man auf einen Blick, ob eine
        Nachricht direkt geht oder den Umweg über Discord nimmt.
        """
        if speicher.ist_gruppen_id(friend_id):
            return "discord" if self.discord_verbunden else None
        if self.modus in ("lan", "beide") and self.lan.is_online(friend_id):
            return "lan"
        if self.modus in ("discord", "beide") and self.discord is not None:
            if self.discord.erreichbar(friend_id):
                return "discord"
        return None

    # -------------------------------------------------------------- Senden

    def _wege(self, friend_id: str):
        """Die Wege für dieses Ziel, in der Reihenfolge, in der sie
        probiert werden."""
        if speicher.ist_gruppen_id(friend_id):
            # Eine Gruppe hat keine Adresse im WLAN: Wer dazugehört, steht
            # nirgends - dabei zu sein heißt nur, den Code zu haben. Über
            # Discord ist der Kanal selbst die Verteilung, im WLAN müsste
            # man wissen, an wen man schicken soll. Deshalb gehen Gruppen
            # nur über Discord.
            return [("discord", self.discord)]
        if self.modus == "lan":
            return [("lan", self.lan)]
        if self.modus == "discord":
            return [("discord", self.discord)]
        # "beide": direkt ist besser, also erst WLAN.
        if self.lan.is_online(friend_id):
            return [("lan", self.lan), ("discord", self.discord)]
        return [("discord", self.discord), ("lan", self.lan)]

    def _senden(self, friend_id: str, aufruf):
        """Probiert die Wege der Reihe nach durch.

        Wichtig ist, was passiert, wenn alles scheitert: dann wird der Fehler
        des ersten - also des bevorzugten - Weges gemeldet. Sonst stünde bei
        einem Freund ohne gemeinsamen Schlüssel die Discord-Meldung in der
        Statusleiste, obwohl das WLAN das eigentliche Problem war.
        """
        erster_fehler = None
        for name, weg in self._wege(friend_id):
            if weg is None:
                if erster_fehler is None:
                    erster_fehler = ConnectionError(
                        "Der Chat über Discord ist nicht eingerichtet.\n\n"
                        "Bot-Token und Kanal-ID trägst du unter "
                        "Einstellungen → Discord ein.")
                continue
            try:
                ergebnis = aufruf(weg)
                return ergebnis, name
            except (ConnectionError, OSError, ValueError) as e:
                if erster_fehler is None:
                    erster_fehler = e
        raise erster_fehler if erster_fehler is not None else ConnectionError(
            "Es ist gerade kein Weg zu diesem Freund offen.")

    def send_text(self, friend_id: str, text: str):
        """Rückgabe wie bei ChatNetwork: True, wenn verschlüsselt gesendet wurde."""
        ergebnis, _weg = self._senden(
            friend_id, lambda w: w.send_text(friend_id, text))
        return ergebnis

    def send_file(self, friend_id: str, filepath: str, kind: str):
        ergebnis, _weg = self._senden(
            friend_id, lambda w: w.send_file(friend_id, filepath, kind))
        return ergebnis

    def send_text_mit_weg(self, friend_id: str, text: str):
        """Wie send_text(), sagt aber dazu, welcher Weg es geworden ist.

        Die Oberfläche schreibt das in die Chatzeile - bei zwei möglichen
        Wegen will man sehen, welcher genommen wurde.
        """
        return self._senden(friend_id, lambda w: w.send_text(friend_id, text))

    def send_file_mit_weg(self, friend_id: str, filepath: str, kind: str):
        return self._senden(friend_id, lambda w: w.send_file(friend_id, filepath, kind))
