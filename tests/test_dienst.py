# -*- coding: utf-8 -*-
"""
Prüfungen für dienst.py und api.py - VP4 ohne Fenster, aber mit allem
anderen: Einrichten, Entsperren über Windows (Attrappe), Sperren, zwei
Personen über ein Spielzeug-Netz, und der Vertrag zwischen Oberfläche
und Python (jede Funktion, die ui/js aufruft, muss es in VP4Api geben).
"""

import re
import tempfile
import threading
import time
from pathlib import Path

WURZEL = Path(__file__).resolve().parent.parent


class SpielNetz:
    """Verbindet mehrere Dienste im selben Prozess, wie ein WLAN ohne Sockets."""

    def __init__(self):
        self.dienste = []

    def fabrik(self, dienst):
        netz = _Anschluss(self, dienst)
        return netz


class _Anschluss:
    def __init__(self, netz, dienst):
        self.netz = netz
        self.dienst = dienst
        self.an = False

    def start(self):
        self.an = True
        if self.dienst not in self.netz.dienste:
            self.netz.dienste.append(self.dienst)

    def stop(self):
        self.an = False

    def senden(self, roh, an, community=False, fluechtig=False):
        ziele = [d for d in self.netz.dienste if d is not self.dienst and d.bote and d.netz and d.netz.an
                 and (d.bote.meine_id == an or (community and d.db.community_holen(an)))]
        if not ziele and not community:
            raise ConnectionError("Nicht erreichbar.")
        for d in ziele:
            threading.Thread(target=d._empfangen, args=(roh, "lan"), daemon=True).start()
        return "lan"

    def status(self):
        return {"lan": "an" if self.an else "aus", "discord": "aus"}

    def ist_online(self, i):
        return any(d.bote and d.bote.meine_id == i for d in self.netz.dienste)

    def weg_zu(self, i):
        return "lan" if self.ist_online(i) else None

    def modus_setzen(self, modus):
        pass


def _warten(bedingung, sekunden=5.0):
    ende = time.time() + sekunden
    while time.time() < ende:
        if bedingung():
            return True
        time.sleep(0.03)
    return bedingung()


def pruefen(R, hilfen):
    from api import VP4Api, oeffentliche_namen
    from dienst import VP4Dienst
    from ereignisse import Zeitplaner
    from kern.tresor import DPAPIAttrappe

    # --- Vertrag Oberfläche <-> Python ------------------------------------
    js = "\n".join(p.read_text(encoding="utf-8") for p in (WURZEL / "ui" / "js").rglob("*.js")
                   if p.name != "demo.js")
    aufgerufen = set(re.findall(r'rufe\("([a-z_]+)"', js))
    vorhanden = set(oeffentliche_namen())
    fehlt = sorted(aufgerufen - vorhanden)
    R.pruefe("Jede Funktion, die die Oberfläche aufruft, gibt es in VP4Api", not fehlt, f"fehlt: {fehlt}")
    demo = (WURZEL / "ui" / "js" / "demo.js").read_text(encoding="utf-8")
    R.pruefe("Die Demo-Daten erfinden keine Funktionen, die es nicht gibt",
             not (set(re.findall(r"^  async ([a-z_]+)\(", demo, re.M)) - vorhanden - {"ereignisse_holen"}))
    R.pruefe("VP4Api hat nach aussen nur Methoden (sonst reicht pywebview Tresor & Co. an JavaScript)",
             all(callable(getattr(VP4Api, n)) for n in vorhanden))
    ganz = "\n".join(p.read_text(encoding="utf-8") for p in (WURZEL / "ui").rglob("*.js"))
    ganz = re.sub(r"/\*.*?\*/", "", ganz, flags=re.S)
    ganz = re.sub(r"(^|\s)//.*", "", ganz)          # Kommentare dürfen das Wort erwähnen
    R.pruefe("Die Oberfläche benutzt nirgends innerHTML & Co.",
             not re.search(r"innerHTML|outerHTML\s*=|insertAdjacentHTML|document\.write", ganz))
    html = (WURZEL / "ui" / "index.html").read_text(encoding="utf-8")
    R.pruefe("Die Seite lädt nichts von fremden Servern",
             not re.search(r'(src|href)="https?://', html) and "<script>" not in html)

    # --- Zeitplaner: gelaufene Aufträge tragen sich aus -----------------------
    z = Zeitplaner()
    try:
        for _ in range(20):
            z.einmal(0.01, lambda: None)
        z.jede(0.05, lambda: None)
        _warten(lambda: z.anzahl() == 1, 2)
        R.pruefe("Gelaufene einmalige Aufträge tragen sich selbst aus", z.anzahl() == 1)
        k = z.jede(10, lambda: None)
        z.abbestellen(k)
        R.pruefe("Abbestellen nimmt den Auftrag heraus", z.anzahl() == 1)
        laeuft = []
        import logging
        logging.getLogger("vp4.ereignisse").disabled = True    # der Absturz ist hier Absicht
        z.einmal(0.01, lambda: (_ for _ in ()).throw(RuntimeError("Absicht")))
        z.einmal(0.05, lambda: laeuft.append(1))
        _warten(lambda: laeuft, 2)
        R.pruefe("Ein abstürzender Auftrag hält die anderen nicht auf", laeuft == [1])
        logging.getLogger("vp4.ereignisse").disabled = False
    finally:
        z.stoppen()

    with tempfile.TemporaryDirectory() as tmp:
        netz = SpielNetz()
        attrappe = DPAPIAttrappe()
        a_ordner, b_ordner = Path(tmp) / "anna", Path(tmp) / "ben"
        anna = VP4Dienst(a_ordner, dpapi=attrappe, netz_fabrik=netz.fabrik, update_pruefen=False)
        ben = VP4Dienst(b_ordner, dpapi=attrappe, netz_fabrik=netz.fabrik, update_pruefen=False)
        a_api, b_api = VP4Api(anna), VP4Api(ben)
        try:
            R.pruefe("Ganz neu: Phase 'einrichten'", a_api.status()["phase"] == "einrichten")
            r = a_api.einrichten({"name": "Anna", "avatar_farbe": "#34C759", "passwort": "kurz", "windows_merken": True})
            R.pruefe("Zu kurzes Master-Passwort wird abgelehnt", not r["ok"])
            r = a_api.einrichten({"name": "Anna", "avatar_farbe": "#34C759", "passwort": "Annas-Passwort-1", "windows_merken": True})
            R.pruefe("Einrichten klappt und liefert eine ID", r["ok"] and len(r["profil"]["id"]) == 11)
            b_api.einrichten({"name": "Ben", "avatar_farbe": "#0088FF", "passwort": "Bens-Passwort-12", "windows_merken": False})
            R.pruefe("Nach dem Einrichten: Phase 'bereit'", a_api.status()["phase"] == "bereit")
            R.pruefe("Ohne 'Windows merkt es sich' liegt kein DPAPI-Schlüssel", not (b_ordner / "tresor.dpapi").exists())
            R.pruefe("Mit 'Windows merkt es sich' liegt ein DPAPI-Schlüssel", (a_ordner / "tresor.dpapi").exists())

            # Freundschaft und erste Nachricht über die API
            ben_id = b_api.status()["profil"]["id"]
            anna_id = a_api.status()["profil"]["id"]
            r = a_api.kontakt_hinzufuegen(ben_id.lower().replace("-", ""))
            R.pruefe("ID ohne Strich und klein geschrieben wird verstanden", r["ok"], r.get("fehler", ""))
            R.pruefe("Die Anfrage kommt bei Ben an",
                     _warten(lambda: any(a["id"] == anna_id for a in b_api.anfragen().get("liste", []))))
            b_api.anfrage_beantworten(anna_id, True)
            R.pruefe("Nach dem Annehmen hat Anna den Chat mit Ben",
                     _warten(lambda: any(u["id"] == ben_id for u in a_api.unterhaltungen().get("liste", []))))
            r = a_api.senden(ben_id, {"text": "Hallo Ben, alles klar?"})
            R.pruefe("Senden über die API klappt", r["ok"], r.get("fehler", ""))
            R.pruefe("Die Nachricht kommt bei Ben an",
                     _warten(lambda: any(n["text"] == "Hallo Ben, alles klar?"
                                         for n in b_api.nachrichten(anna_id).get("liste", []))))
            R.pruefe("Status wird über den Versand-Thread auf 'gesendet' gesetzt",
                     _warten(lambda: a_api.nachrichten(ben_id)["liste"][-1]["status"] in ("gesendet", "zugestellt")))
            R.pruefe("Und nach der Sammel-Quittung auf 'zugestellt'",
                     _warten(lambda: a_api.nachrichten(ben_id)["liste"][-1]["status"] == "zugestellt", 6))
            typen = {e["typ"] for e in b_api.ereignisse_holen()["liste"]}
            R.pruefe("Die Oberfläche bekommt nachricht_neu und mitteilung", {"nachricht_neu", "mitteilung"} <= typen)

            # Werkzeuge über die API
            v = a_api.verfahren()["liste"]
            R.pruefe("Die Werkzeuge zeigen auch Post-Quanten und age",
                     any(x["art"] == "pq" for x in v) and any(x["name"].startswith("age") for x in v))
            k = a_api.schluessel_erzeugen("AES-256-GCM")["schluessel"]
            g = a_api.text_verarbeiten("AES-256-GCM", "ver", "Geheim äöü", k)["ergebnis"]
            R.pruefe("Text verschlüsseln und entschlüsseln über die API",
                     a_api.text_verarbeiten("AES-256-GCM", "ent", g, k)["ergebnis"] == "Geheim äöü")
            R.pruefe("Falscher Schlüssel gibt eine Fehlermeldung statt Absturz",
                     not a_api.text_verarbeiten("AES-256-GCM", "ent", g, a_api.schluessel_erzeugen("AES-256-GCM")["schluessel"])["ok"])
            paar = a_api.schluessel_erzeugen("Post-Quanten (Hybrid)")
            a_api.schluessel_speichern("Mein PQ", "pq", {"privat": paar["privat"], "oeffentlich": paar["oeffentlich"]}, "")
            oeff = a_api.schluessel_wert("Mein PQ", True)["wert"]
            priv = a_api.schluessel_wert("Mein PQ", False)["wert"]
            g = a_api.text_verarbeiten("Post-Quanten (Hybrid)", "ver", "Quanten?", oeff)["ergebnis"]
            R.pruefe("Schlüsselpaar im Schlüsselbund: öffentlich zum Ver-, privat zum Entschlüsseln",
                     a_api.text_verarbeiten("Post-Quanten (Hybrid)", "ent", g, priv)["ergebnis"] == "Quanten?")

            # Sperren und Entsperren
            jobs_vorher = anna.zeitplaner.anzahl()
            a_api.sperren()
            R.pruefe("Gesperrt: keine Chats mehr erreichbar", not a_api.unterhaltungen()["ok"])
            R.pruefe("Falsches Passwort wird abgelehnt", not a_api.entsperren("falsch")["ok"])
            a_api.entsperren("Annas-Passwort-1")
            a_api.sperren()
            a_api.entsperren("Annas-Passwort-1")
            R.pruefe("Nach mehrmals Sperren/Entsperren bleiben genauso viele Aufträge offen",
                     anna.zeitplaner.anzahl() == jobs_vorher, f"{anna.zeitplaner.anzahl()} statt {jobs_vorher}")
            R.pruefe("Nach dem Entsperren ist der Verlauf noch da",
                     any(n["text"] == "Hallo Ben, alles klar?" for n in a_api.nachrichten(ben_id)["liste"]))
            # Review-Befund 1: Sperren von Hand legte ein "gesperrt" in die
            # Warteschlange, das die Oberfläche nach dem Entsperren abholte -
            # und daraufhin sofort wieder sperrte, immer wieder.
            a_api.ereignisse_holen()
            a_api.sperren()
            a_api.entsperren("Annas-Passwort-1")
            R.pruefe("Von Hand sperren hinterlässt kein 'gesperrt', das gleich wieder sperrt",
                     not any(e["typ"] == "gesperrt" for e in a_api.ereignisse_holen()["liste"]))
            # Review-Befund 9: hing beim Sperren gerade ein Versand, blieb danach
            # alles auf "Wird gesendet" stehen.
            langsam = threading.Event()
            echtes = anna.netz.senden
            def zaeh(*a, **k):
                langsam.wait(5)
                return echtes(*a, **k)
            anna.netz.senden = zaeh
            a_api.senden(ben_id, {"text": "hängt"})
            time.sleep(0.2)
            a_api.sperren()
            langsam.set()
            a_api.entsperren("Annas-Passwort-1")
            a_api.senden(ben_id, {"text": "Nach dem Hänger"})
            R.pruefe("Nach einem hängenden Versand beim Sperren wird danach wieder gesendet",
                     _warten(lambda: any(n["text"] == "Nach dem Hänger" for n in b_api.nachrichten(anna_id).get("liste", []))))
            # Review-Befund 11: geöffnete Anhänge lagen im Klartext herum
            (anna.ordner / "geoeffnet").mkdir(exist_ok=True)
            (anna.ordner / "geoeffnet" / "foto.jpg").write_bytes(b"x")
            a_api.sperren()
            R.pruefe("Beim Sperren verschwinden geöffnete Klartext-Kopien",
                     not any((anna.ordner / "geoeffnet").glob("*")))
            a_api.entsperren("Annas-Passwort-1")
        finally:
            anna.beenden()
            ben.beenden()

        # Neustart: Windows kennt Annas Schlüssel, Ben muss tippen
        anna2 = VP4Dienst(a_ordner, dpapi=attrappe, netz_fabrik=netz.fabrik, update_pruefen=False)
        ben2 = VP4Dienst(b_ordner, dpapi=attrappe, netz_fabrik=netz.fabrik, update_pruefen=False)
        try:
            anna2.start()
            ben2.start()
            R.pruefe("Neustart mit 'Windows merkt es sich': gleich entsperrt", anna2.phase() == "bereit")
            R.pruefe("Neustart ohne: Sperrbildschirm", ben2.phase() == "gesperrt")
            R.pruefe("Der Sperrbildschirm kennt Namen und Farbe trotzdem",
                     VP4Api(ben2).status()["profil"]["name"] == "Ben")
            VP4Api(anna2).einstellung_setzen("bei_start_fragen", True)
            R.pruefe("'Bei jedem Start fragen' löscht den Windows-Schlüssel", not (a_ordner / "tresor.dpapi").exists())
            R.pruefe("Unbekannte Einstellungen werden abgelehnt",
                     not VP4Api(anna2).einstellung_setzen("bot_token", "x")["ok"])
        finally:
            anna2.beenden()
            ben2.beenden()
