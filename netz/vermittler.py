# -*- coding: utf-8 -*-
"""netz/vermittler.py - welcher Weg nimmt ein Umschlag? (Nachfolger von transport.py)

Zwei Wege bringen einen Umschlag zum Empfänger:

    netz.lan.LanNetz             direkt übers WLAN
    netz.discord_netz.DiscordNetz  über Discord-Kanäle

Der Vermittler hält beide und entscheidet pro Umschlag. Die höhere Schicht
(kern/nachrichten.py) ruft nur senden() auf und bekommt zurück, welcher Weg
es war ("lan" / "discord") - das zeigt die Kopfzeile des Chats an ("über
WLAN" / "über Discord").

BETRIEBSARTEN
-------------
    "lan"      nur WLAN
    "discord"  nur Discord
    "beide"    (Voreinstellung) WLAN, wenn der Empfänger gerade im WLAN zu
               sehen ist, sonst Discord. Scheitert das WLAN trotzdem, geht
               der Umschlag über Discord - außer er ist flüchtig.

WARUM WLAN VORRANG HAT
----------------------
Im WLAN geht der Umschlag direkt von PC zu PC: Nichts verlässt die Wohnung,
niemand protokolliert, wer wem wann schreibt, und es gibt kein gemeinsames
Sendebudget, das alle VP4-Nutzer teilen.

COMMUNITIES GEHEN IMMER ÜBER DISCORD
------------------------------------
Eine Community hat im WLAN keine Adresse: Wer dazugehört, steht nirgends.
Über Discord ist der Kanal selbst die Verteilung.

FLÜCHTIGES NUR IM WLAN
----------------------
"tippt gerade" und Ähnliches geht nie über Discord (gemeinsames
Sendebudget, siehe netz/discord_netz.py). Ist der Empfänger nicht im WLAN,
wird es einfach nicht verschickt (ConnectionError).
"""

from kern.communities import ist_community_id

MODI = ("lan", "discord", "beide")

MODUS_NAMEN = {
    "lan": "Nur WLAN",
    "discord": "Nur Discord (Internet)",
    "beide": "Beide – WLAN bevorzugt",
}


class Vermittler:
    """Führt WLAN und Discord zusammen.

    empfangen(umschlag_bytes, weg) - weg ist "lan" oder "discord".
    ereignis(typ, **daten) - alles von den Wegen, "verbindung" mit dem
        zusammengeführten Zustand {"lan": ..., "discord": ...}.

    `lan` und `discord` sind fertige Instanzen (oder None, wenn es den Weg
    nicht gibt). Ihre Rückrufe biegt der Vermittler auf sich selbst um.
    """

    def __init__(self, eigene_id: str, empfangen, ereignis, modus: str = "beide",
                 lan=None, discord=None):
        self.eigene_id = eigene_id
        self.empfangen = empfangen
        self.ereignis = ereignis
        self.modus = modus if modus in MODI else "beide"
        self.lan = lan
        self.discord = discord
        self._zustand = {"lan": "aus", "discord": "aus"}
        self._meldung = ""
        self._laeuft = False

        if lan is not None:
            lan.empfangen = lambda daten, weg="lan", adresse=None: self._eingang(daten, "lan")
            lan.ereignis = self._ereignis_von("lan")
        if discord is not None:
            discord.empfangen = lambda daten, weg="discord", herkunft=None: \
                self._eingang(daten, "discord")
            discord.ereignis = self._ereignis_von("discord")

    # ------------------------------------------------------------ Hilfen

    def _melden(self, typ, **daten):
        try:
            self.ereignis(typ, **daten)
        except Exception:
            pass

    def _eingang(self, daten, weg):
        self.empfangen(daten, weg)

    def _ereignis_von(self, weg):
        def weiter(typ, **daten):
            if typ == "verbindung":
                zustand = (daten.get("zustand") or {}).get(weg)
                if zustand:
                    self._zustand[weg] = zustand
                if daten.get("meldung"):
                    self._meldung = daten["meldung"]
                self._melden("verbindung", zustand=self._zustand_aussen(),
                             meldung=daten.get("meldung", ""), weg=weg)
            else:
                self._melden(typ, **{"weg": weg, **daten})
        return weiter

    def _lan_erlaubt(self) -> bool:
        return self.lan is not None and self.modus in ("lan", "beide")

    def _discord_erlaubt(self) -> bool:
        return self.discord is not None and self.modus in ("discord", "beide")

    def _zustand_aussen(self) -> dict:
        return {"lan": self._zustand["lan"] if self._lan_erlaubt() else "aus",
                "discord": self._zustand["discord"] if self._discord_erlaubt() else "aus"}

    # ------------------------------------------------------- Start / Stop

    def start(self):
        self._laeuft = True
        if self._lan_erlaubt():
            self.lan.start()
        if self._discord_erlaubt():
            self.discord.start()

    def stop(self):
        self._laeuft = False
        for weg in (self.lan, self.discord):
            if weg is not None:
                try:
                    weg.stop()
                except Exception:
                    pass

    def modus_setzen(self, modus: str):
        """Wechselt die Betriebsart und startet/stoppt die Wege passend."""
        if modus not in MODI:
            raise ValueError(f"Unbekannte Betriebsart: {modus!r}")
        self.modus = modus
        if not self._laeuft:
            return
        for weg, erlaubt in ((self.lan, self._lan_erlaubt()),
                             (self.discord, self._discord_erlaubt())):
            if weg is None:
                continue
            try:
                weg.start() if erlaubt else weg.stop()
            except Exception as e:
                self._melden("fehler", text=f"Der Wechsel der Betriebsart ging nicht "
                                            f"ganz. (Technisch: {e})")
        self._melden("verbindung", zustand=self._zustand_aussen(), meldung="")

    # ------------------------------------------------------------ Zustand

    def status(self) -> dict:
        z = self._zustand_aussen()
        return {"lan": z["lan"], "discord": z["discord"], "meldung": self._meldung,
                "modus": self.modus}

    def _discord_verbunden(self) -> bool:
        return self._discord_erlaubt() and bool(getattr(self.discord, "verbunden", False))

    def ist_online(self, peer_id: str) -> bool:
        """Im WLAN zu sehen? (Über Discord gibt es kein "gerade da".)"""
        return self._lan_erlaubt() and self.lan.ist_online(peer_id)

    def weg_zu(self, ziel: str):
        """Welcher Weg jetzt benutzt würde: "lan", "discord" oder None."""
        if not ist_community_id(ziel) and self.ist_online(ziel):
            return "lan"
        if self._discord_verbunden():
            return "discord"
        return None

    def online_ids(self) -> set:
        return self.lan.online_ids() if self._lan_erlaubt() else set()

    # ------------------------------------------------------------- Senden

    def senden(self, umschlag_bytes: bytes, an: str, *, community: bool = False,
               fluechtig: bool = False) -> str:
        """Schickt den Umschlag über den passenden Weg; gibt den Weg zurück.

        ConnectionError mit deutscher Erklärung, wenn es keinen Weg gibt.
        ValueError (z. B. zu groß für Discord) wird durchgereicht.
        """
        community = community or ist_community_id(an)

        if community:
            if fluechtig:
                raise ConnectionError("Kurzlebiges wie „tippt gerade“ geht in "
                                      "Communities nicht: Die laufen über Discord.")
            if self.discord is None:
                raise ConnectionError("Communities laufen über Discord, und Discord "
                                      "ist nicht eingerichtet.")
            if self.modus == "lan":
                raise ConnectionError("Communities laufen über Discord, aber in den "
                                      "Einstellungen ist „Nur WLAN“ gewählt.")
            self.discord.senden(umschlag_bytes, an)
            return "discord"

        if self.modus == "lan":
            if self.lan is None:
                raise ConnectionError("Der Chat im WLAN ist nicht verfügbar.")
            self.lan.senden(umschlag_bytes, an)
            return "lan"

        if self.modus == "discord":
            if fluechtig:
                # Hier und nicht erst in DiscordNetz: Flüchtiges soll gar nicht
                # erst in die Nähe des gemeinsamen Sendebudgets kommen.
                raise ConnectionError("Kurzlebiges wie „tippt gerade“ geht nie über "
                                      "Discord, und „Nur Discord“ ist gewählt.")
            if self.discord is None:
                raise ConnectionError("Discord ist nicht eingerichtet.")
            self.discord.senden(umschlag_bytes, an)
            return "discord"

        # beide
        lan_fehler = None
        if self.lan is not None and self.lan.ist_online(an):
            try:
                self.lan.senden(umschlag_bytes, an)
                return "lan"
            except ConnectionError as e:
                lan_fehler = e
                if fluechtig:
                    raise
        if fluechtig:
            raise ConnectionError(f"{an} ist nicht im WLAN zu sehen; Kurzlebiges wie "
                                  f"„tippt gerade“ geht nicht über Discord.")
        if self.discord is None:
            if lan_fehler is not None:
                raise lan_fehler
            raise ConnectionError(f"{an} ist gerade nicht im WLAN zu sehen, und Discord "
                                  f"ist nicht eingerichtet. Die Nachricht wurde nicht "
                                  f"verschickt.")
        try:
            self.discord.senden(umschlag_bytes, an)
        except ConnectionError as e:
            if lan_fehler is not None:
                raise ConnectionError(f"Weder WLAN noch Discord haben geklappt.\n\n"
                                      f"WLAN: {lan_fehler}\n\nDiscord: {e}") from None
            raise
        return "discord"

    def loeschen(self, msg_id_hex: str) -> None:
        """Räumt die Discord-Zeilen zu einem Umschlag weg (Hintergrund, best effort)."""
        if self.discord is not None:
            try:
                self.discord.loeschen(msg_id_hex)
            except Exception:
                pass


def standard_vermittler(eigene_id: str, empfangen, ereignis, modus: str = "beide", *,
                        stand_holen=None, stand_setzen=None, interessant=None,
                        lan_args=None) -> Vermittler:
    """Baut den Vermittler mit den echten Wegen und den geltenden Zugangsdaten.

    Ohne Bot-Token oder Kanal gibt es keinen Discord-Weg (None) - dann wird
    auch nichts ungefragt ins Internet geschickt.
    """
    from netz.discord_netz import DiscordNetz, zugang_laden
    from netz.lan import LanNetz

    lan = LanNetz(eigene_id, empfangen, ereignis, **(lan_args or {}))
    zugang = zugang_laden()
    discord = None
    if zugang["token"] and zugang["kanal_ids"]:
        discord = DiscordNetz(eigene_id, empfangen, ereignis, zugang["token"],
                              zugang["kanal_ids"], stand_holen, stand_setzen, interessant)
    return Vermittler(eigene_id, empfangen, ereignis, modus, lan=lan, discord=discord)
