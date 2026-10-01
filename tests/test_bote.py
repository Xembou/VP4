# -*- coding: utf-8 -*-
"""
Prüfungen für kern/nachrichten.py - der ganze Ablauf zwischen mehreren
Personen in einem Prozess: Anfrage, Annahme, Nachrichten, Antworten,
Reaktionen, Bearbeiten, Löschen, Lesebestätigungen, Dateien, Gruppen,
Communities, Ankündigungskanäle und ein neuer Code.

Statt WLAN oder Discord gibt es hier ein Spielzeug-Netz, das Aufträge
gesammelt zustellt - auf Wunsch doppelt und in verkehrter Reihenfolge,
weil Discord genau das tun kann.
"""

import random
import tempfile
from pathlib import Path

from kern import nachrichten as nachrichten_modul
from kern import umschlag as um
from kern.datenbank import Datenbank
from kern.identitaet import Identitaet
from kern.nachrichten import Bote
from kern.tresor import Tresor


class SpielNetz:
    def __init__(self):
        self.boten = {}
        self.schlange = []
        self.zugestellt = 0

    def ausgang_fuer(self, name):
        return lambda auftrag: self.schlange.append((name, auftrag))

    def zustellen(self, mischen=False, doppelt=False, fluechtig_weg=True):
        runden = 0
        while self.schlange and runden < 50:
            runden += 1
            jetzt, self.schlange = self.schlange, []
            if mischen:
                random.shuffle(jetzt)
            for absender, auftrag in jetzt:
                roh = auftrag.bytes()
                ziele = []
                if auftrag.community:
                    ziele = [n for n, b in self.boten.items() if n != absender
                             and b.db.community_holen(auftrag.an)]
                else:
                    ziele = [n for n, b in self.boten.items() if b.meine_id == auftrag.an]
                for z in ziele:
                    self.boten[z].empfangen(roh, "lan")
                    if doppelt:
                        self.boten[z].empfangen(roh, "discord")
                    self.zugestellt += 1
                if auftrag.nachricht_id and auftrag.teil is None:
                    self.boten[absender].gesendet(auftrag.nachricht_id, "lan")


def person(netz, name, ordner):
    ich = Identitaet.neu()
    tresor = Tresor(Path(ordner) / f"{name}.tresor")
    tresor.create("Passwort-" + name)
    db = Datenbank(Path(ordner) / f"{name}.db", tresor.datenschluessel, ich.id)
    ereignisse = []
    profil = {"name": name, "avatar_farbe": "#34C759"}
    bote = Bote(ich, tresor, db, ausgang=netz.ausgang_fuer(name),
                ereignis=lambda typ, **d: ereignisse.append((typ, d)),
                medien_ordner=Path(ordner) / f"medien-{name}", profil=lambda: profil,
                einstellungen=lambda: {"lesebestaetigungen": True, "tippanzeige": True},
                medien_url=lambda pfad: "http://127.0.0.1/" + Path(pfad).name)
    bote.ereignisse = ereignisse
    netz.boten[name] = bote
    return bote


def letzte_texte(bote, unterhaltung, n=5):
    liste, _ = bote.nachrichten(unterhaltung)
    return [x["text"] for x in liste if x["art"] != "system"][-n:]


def pruefen(R, hilfen):
    wirft = hilfen["wirft_valueerror"]
    with tempfile.TemporaryDirectory() as ordner:
        netz = SpielNetz()
        anna = person(netz, "Anna", ordner)
        ben = person(netz, "Ben", ordner)
        carl = person(netz, "Carl", ordner)

        # --- Anfrage und Annahme -----------------------------------------
        anna.kontakt_hinzufuegen(ben.meine_id)
        netz.zustellen()
        R.pruefe("Anfrage kommt beim anderen als offene Anfrage an",
                 [a["id"] for a in ben.anfragen() if a["richtung"] == "rein"] == [anna.meine_id])
        R.pruefe("Vor dem Annehmen kann man noch nicht schreiben",
                 wirft(anna.text_senden, ben.meine_id, "Hallo?") or anna.db.unterhaltung_holen(ben.meine_id) is None)
        ben.anfrage_beantworten(anna.meine_id, True)
        netz.zustellen()
        R.pruefe("Nach dem Annehmen sind beide verbunden",
                 anna.db.kontakt_holen(ben.meine_id)["status"] == "ok"
                 and ben.db.kontakt_holen(anna.meine_id)["status"] == "ok")
        R.pruefe("Beide rechnen denselben Paarschlüssel aus",
                 anna.tresor.paar_schluessel(ben.meine_id) == ben.tresor.paar_schluessel(anna.meine_id))
        R.pruefe("Sicherheitsnummer ist auf beiden Seiten gleich",
                 anna.kontakt_info(ben.meine_id)["sicherheitsnummer"] == ben.kontakt_info(anna.meine_id)["sicherheitsnummer"])

        # --- Text, Status, Lesebestätigung ---------------------------------
        nid = anna.text_senden(ben.meine_id, "Hallo Ben! Grüße äöüß 🔒")
        netz.zustellen()
        R.pruefe("Text kommt an, mit Umlauten und Emoji",
                 letzte_texte(ben, anna.meine_id, 1) == ["Hallo Ben! Grüße äöüß 🔒"])
        R.pruefe("Nach dem Versand steht 'gesendet'", anna.db.nachricht_holen(nid)["status"] == "gesendet")
        ben.quittungen_senden()
        netz.zustellen()
        R.pruefe("Zustellbestätigung kommt zurück", anna.db.nachricht_holen(nid)["status"] == "zugestellt")
        ben.gelesen(anna.meine_id)
        ben.quittungen_senden()
        netz.zustellen()
        R.pruefe("Lesebestätigung kommt zurück", anna.db.nachricht_holen(nid)["status"] == "gelesen")
        R.pruefe("Neue Nachricht meldet eine Mitteilung",
                 any(t == "mitteilung" for t, _ in ben.ereignisse))

        # --- Doppelt und verdreht zugestellt --------------------------------
        for i in range(5):
            anna.text_senden(ben.meine_id, f"Nummer {i}")
        netz.zustellen(mischen=True, doppelt=True)
        texte = letzte_texte(ben, anna.meine_id, 20)
        R.pruefe("Doppelt zugestellte Nachrichten erscheinen nur einmal",
                 sorted(t for t in texte if t.startswith("Nummer")) == [f"Nummer {i}" for i in range(5)])

        # --- Antworten, Reaktion, Bearbeiten, Löschen --------------------------
        antwort = ben.text_senden(anna.meine_id, "Hi Anna", antwort_auf=nid)
        netz.zustellen()
        ui = [x for x in anna.nachrichten(ben.meine_id)[0] if x["id"] == antwort][0]
        R.pruefe("Antwort zeigt das Zitat", (ui.get("antwort_auf") or {}).get("text") == "Hallo Ben! Grüße äöüß 🔒")
        ben.reagieren(anna.meine_id, nid, "❤️")
        netz.zustellen()
        ui = [x for x in anna.nachrichten(ben.meine_id)[0] if x["id"] == nid][0]
        R.pruefe("Reaktion kommt an", [r["emoji"] for r in ui["reaktionen"]] == ["❤️"])
        ben.reagieren(anna.meine_id, nid, "❤️")
        netz.zustellen()
        ui = [x for x in anna.nachrichten(ben.meine_id)[0] if x["id"] == nid][0]
        R.pruefe("Zweites Tippen nimmt die Reaktion zurück", ui["reaktionen"] == [])

        anna.bearbeiten(ben.meine_id, nid, "Hallo Ben (korrigiert)")
        netz.zustellen()
        ui = [x for x in ben.nachrichten(anna.meine_id)[0] if x["id"] == nid][0]
        R.pruefe("Bearbeiten kommt an und ist markiert", ui["text"] == "Hallo Ben (korrigiert)" and ui["bearbeitet"])

        # Ben versucht, Annas Nachricht bei Anna zu ändern
        falsch = um.dm_bauen(ben.ich, ben.tresor.paar_schluessel(anna.meine_id), ben.meine_id, anna.meine_id,
                             {"art": "bearbeiten", "ziel": nid, "text": "gefälscht"})
        anna.empfangen(falsch.packen())
        R.pruefe("Fremde Nachrichten kann niemand bearbeiten",
                 anna.db.nachricht_holen(nid)["inhalt"]["text"] == "Hallo Ben (korrigiert)")
        falsch = um.dm_bauen(ben.ich, ben.tresor.paar_schluessel(anna.meine_id), ben.meine_id, anna.meine_id,
                             {"art": "loeschen", "ziel": nid})
        anna.empfangen(falsch.packen())
        R.pruefe("Fremde Nachrichten kann niemand löschen", not anna.db.nachricht_holen(nid)["geloescht"])
        R.pruefe("Fremde Nachricht für alle löschen wird abgelehnt", wirft(ben.loeschen, anna.meine_id, nid, True))

        anna.loeschen(ben.meine_id, nid, True)
        netz.zustellen()
        R.pruefe("Für alle löschen kommt an", ben.db.nachricht_holen(nid)["geloescht"])

        # --- Unbekannte -----------------------------------------------------
        vorher = len(anna.db.unterhaltungen_alle())
        import os
        fremd = um.dm_bauen(carl.ich, os.urandom(32), carl.meine_id, anna.meine_id, {"art": "text", "text": "Spam"})
        anna.empfangen(fremd.packen())
        anna.empfangen(b"VP4N" + os.urandom(100))
        anna.empfangen(b"")
        R.pruefe("Nachrichten von Unbekannten und Datenmüll werden verworfen",
                 len(anna.db.unterhaltungen_alle()) == vorher and "Spam" not in letzte_texte(anna, ben.meine_id, 50))
        R.pruefe("An Unbekannte schreiben geht nicht", wirft(anna.text_senden, carl.meine_id, "Hallo"))

        # --- Tippen geht nie über Discord -----------------------------------
        netz.schlange.clear()
        anna.tippt(ben.meine_id)
        R.pruefe("'schreibt …' ist als flüchtig markiert (nur WLAN)",
                 len(netz.schlange) == 1 and netz.schlange[0][1].fluechtig)
        netz.zustellen()
        R.pruefe("'schreibt …' kommt im WLAN an", any(t == "tippt" for t, _ in ben.ereignisse))

        # --- Dateien ----------------------------------------------------------
        alt = nachrichten_modul.TEIL_GROESSE
        nachrichten_modul.TEIL_GROESSE = 1000
        try:
            quelle = Path(ordner) / "bericht.pdf"
            daten = os.urandom(2500)
            quelle.write_bytes(daten)
            fid = anna.datei_senden(ben.meine_id, quelle)
            R.pruefe("Datei wird in Teile zerlegt", sum(1 for _, a in netz.schlange if a.teil is not None) == 3)
            # Discord kann die Teile vor der Nachricht liefern
            netz.schlange.reverse()
            netz.zustellen(mischen=True)
            ui = [x for x in ben.nachrichten(anna.meine_id)[0] if x["id"] == fid][0]
            pfad = ben.db.anhang_holen(anna.db.nachricht_holen(fid)["inhalt"]["datei"]["datei_id"])["pfad"]
            R.pruefe("Datei kommt vollständig und unverändert an",
                     ui["art"] == "datei" and ui["datei"].get("url") and Path(pfad).read_bytes() == daten)
            R.pruefe("Der Dateischlüssel erreicht nie die Oberfläche", "schluessel" not in str(ui))
            R.pruefe("Gespeichert wird unter zufälligem Namen", "bericht" not in Path(pfad).name)

            from PIL import Image
            bild = Path(ordner) / "foto.png"
            Image.new("RGB", (800, 600), (30, 120, 220)).save(bild)
            bid = anna.datei_senden(ben.meine_id, bild)
            netz.zustellen()
            ui = [x for x in ben.nachrichten(anna.meine_id)[0] if x["id"] == bid][0]
            R.pruefe("Bilder kommen mit Vorschau und Grösse",
                     ui["art"] == "bild" and ui["datei"]["vorschau"].startswith("data:image/jpeg;base64,")
                     and ui["datei"]["breite"] == 800)
        finally:
            nachrichten_modul.TEIL_GROESSE = alt
        R.pruefe("Zu grosse Dateien werden abgelehnt",
                 wirft(anna.datei_senden, ben.meine_id, Path(ordner) / "gibtsnicht.bin"))

        # --- Gruppe -------------------------------------------------------------
        gid = anna.community_erstellen("Die Jungs", "💬", gruppe=True)
        code = anna.einladung(gid)
        ben.community_beitreten(code)
        netz.zustellen()
        bens_gruppe = [u for u in ben.unterhaltungen() if u["community_id"] == gid]
        R.pruefe("Nach dem Beitritt kommt das Manifest: es ist eine Gruppe mit Namen",
                 len(bens_gruppe) == 1 and bens_gruppe[0]["art"] == "gruppe" and bens_gruppe[0]["titel"] == "Die Jungs")
        kanal = bens_gruppe[0]["id"]
        ben.text_senden(kanal, "Moin in die Runde")
        netz.zustellen()
        texte = [x for x in anna.nachrichten(kanal)[0] if x["art"] == "text"]
        R.pruefe("Gruppennachricht kommt an, mit dem Namen des Absenders",
                 texte and texte[-1]["text"] == "Moin in die Runde" and texte[-1]["von_name"] == "Ben")
        R.pruefe("Wer nicht in der Gruppe ist, bekommt nichts", carl.db.unterhaltung_holen(kanal) is None)

        # Carl kommt mit Code dazu - er ist KEIN Kontakt von Anna
        carl.community_beitreten(code)
        netz.zustellen()
        carl.text_senden(kanal, "Hallo, ich bin Carl")
        netz.zustellen()
        name = [x for x in anna.nachrichten(kanal)[0] if x["text"] == "Hallo, ich bin Carl"]
        R.pruefe("Fremde in der Gruppe stehen mit ID-Kürzel da (Name ist Selbstauskunft)",
                 name and name[0]["von_name"].startswith("Carl (") )

        # Gefälschte Gruppennachricht: Carl gibt sich als Ben aus
        from kern import e2e
        ckey = carl.tresor.community_schluessel(gid)
        gefaelscht = um.kanal_bauen(carl.ich, e2e.kanal_schluessel(ckey, kanal), carl.meine_id, gid,
                                    {"art": "text", "text": "Ich bin Ben", "kanal": kanal, "karte": carl.karte()})
        gefaelscht.von = ben.meine_id
        anna.empfangen(gefaelscht.packen())
        R.pruefe("Umadressierte Gruppennachricht wird abgelehnt",
                 "Ich bin Ben" not in [x["text"] for x in anna.nachrichten(kanal)[0]])

        # --- Community mit Kanälen und Ankündigungen ----------------------------
        cid = anna.community_erstellen("Klasse 10b", "📚")
        ben.community_beitreten(anna.einladung(cid))
        netz.zustellen()
        hid = anna.kanal_anlegen(cid, "hausaufgaben")
        aid = anna.kanal_anlegen(cid, "ankuendigungen", nur_admins=True)
        netz.zustellen()
        bens_kanaele = {k["name"] for c in ben.communities() if c["id"] == cid for k in c["kanaele"]}
        R.pruefe("Neue Kanäle kommen per Manifest an", bens_kanaele == {"allgemein", "hausaufgaben", "ankuendigungen"})
        R.pruefe("Ankündigungskanal: Mitglieder dürfen nicht schreiben", wirft(ben.text_senden, aid, "Darf ich?"))
        anna.text_senden(aid, "Klassenfahrt am Freitag")
        netz.zustellen()
        R.pruefe("Ankündigungen vom Besitzer kommen an", letzte_texte(ben, aid, 1) == ["Klassenfahrt am Freitag"])
        ckey = ben.tresor.community_schluessel(cid)
        verboten = um.kanal_bauen(ben.ich, e2e.kanal_schluessel(ckey, aid), ben.meine_id, cid,
                                  {"art": "text", "text": "Hack", "kanal": aid, "karte": ben.karte()})
        anna.empfangen(verboten.packen())
        R.pruefe("Ankündigungskanal: Nachricht eines Nicht-Admins wird verworfen",
                 "Hack" not in letzte_texte(anna, aid, 10))
        R.pruefe("Mitglieder dürfen keine Kanäle anlegen", wirft(ben.kanal_anlegen, cid, "eigener"))

        # Gefälschtes Manifest von Ben (kein Admin)
        from kern.communities import manifest_bauen
        alt_m = ben._manifest(cid)
        boese = manifest_bauen(ben.ich, cid, "Gekapert", "💀", alt_m["kanaele"], [ben.meine_id], alt_m["version"] + 1)
        ben._steuer_senden(hid, {"art": "manifest", "manifest": boese, "karte_von": ben.karte()})
        netz.zustellen()
        R.pruefe("Manifest von einem Nicht-Admin wird abgelehnt",
                 [c["name"] for c in anna.communities() if c["id"] == cid] == ["Klasse 10b"])

        # --- Neuer Code ---------------------------------------------------------
        neuer_code = anna.community_code_erneuern(cid)
        netz.zustellen()
        R.pruefe("Neuer Code: Kontakte bekommen den neuen Schlüssel automatisch",
                 ben.tresor.community_schluessel(cid) == anna.tresor.community_schluessel(cid))
        R.pruefe("Neuer Code unterscheidet sich vom alten", neuer_code != code)
        anna.text_senden(hid, "Nach dem neuen Code")
        netz.zustellen()
        R.pruefe("Nach dem neuen Code liest das Mitglied weiter mit", letzte_texte(ben, hid, 1) == ["Nach dem neuen Code"])

        # --- Ablehnen und gleichzeitiges Hinzufügen ------------------------------
        dora = person(netz, "Dora", ordner)
        anna.kontakt_hinzufuegen(dora.meine_id)
        dora.kontakt_hinzufuegen(anna.meine_id)
        netz.zustellen()
        R.pruefe("Gleichzeitiges Hinzufügen verbindet beide",
                 anna.db.kontakt_holen(dora.meine_id)["status"] == "ok"
                 and dora.db.kontakt_holen(anna.meine_id)["status"] == "ok")
        emil = person(netz, "Emil", ordner)
        emil.kontakt_hinzufuegen(ben.meine_id)
        netz.zustellen()
        ben.anfrage_beantworten(emil.meine_id, False)
        netz.zustellen()
        R.pruefe("Abgelehnte Anfrage verschwindet auf beiden Seiten",
                 emil.db.kontakt_holen(ben.meine_id) is None and ben.db.kontakt_holen(emil.meine_id) is None)
        R.pruefe("Freundescode funktioniert wie eine ID",
                 emil.kontakt_hinzufuegen(carl.freundescode())["id"] == carl.meine_id)
        R.pruefe("Die eigene ID kann man nicht hinzufügen", wirft(emil.kontakt_hinzufuegen, emil.meine_id))

        for b in netz.boten.values():
            b.db.close()
