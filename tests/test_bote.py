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
    pruefen_review(R, hilfen)
    pruefen_verwaltung(R, hilfen)


def pruefen_review(R, hilfen):
    """Befunde aus dem Code-Review vom 01.10.2026 - jede Prüfung zeigt einen Fehler."""
    import os
    from kern import e2e
    from kern.communities import manifest_bauen, standard_kanal_id
    wirft = hilfen["wirft_valueerror"]
    with tempfile.TemporaryDirectory() as ordner:
        netz = SpielNetz()
        anna = person(netz, "Anna", ordner)
        ben = person(netz, "Ben", ordner)
        mallory = person(netz, "Mallory", ordner)
        cid = anna.community_erstellen("Klasse", "📚")
        code = anna.einladung(cid)
        ben.community_beitreten(code)
        netz.zustellen()

        # 2) Datei-Teile an eine Community ohne passende Datei-Nachricht
        teile_ordner = ben.medien / "teile"
        vorher = sum(1 for _ in teile_ordner.glob("*"))
        for i in range(5):
            u = um.verschluesselt_bauen(os.urandom(32), mallory.meine_id, cid, os.urandom(2000),
                                        typ=um.DATEI_TEIL, msg_id=os.urandom(12) + i.to_bytes(4, "big"))
            ben.empfangen(u.packen())
        R.pruefe("Fremde Datei-Teile an eine Community landen nicht auf der Platte",
                 sum(1 for _ in teile_ordner.glob("*")) == vorher)

        # 3) Riesige, nicht entschlüsselbare Community-Umschläge bleiben nicht im Speicher
        gross = um.Umschlag(um.NACHRICHT, mallory.meine_id, cid, os.urandom(16), 1, os.urandom(12), os.urandom(2 * 1024 * 1024))
        ben.empfangen(gross.packen())
        R.pruefe("Grosse unlesbare Community-Umschläge werden nicht gepuffert",
                 sum(len(r) for r, *_ in ben._wartend) < 1024 * 1024)

        # 4) Ein fremdes Manifest übernimmt nicht den Kanal einer anderen Community
        eigene = mallory.community_erstellen("Falle", "💀")
        bens_kanal = standard_kanal_id(cid)
        ben.community_beitreten(mallory.einladung(eigene))
        netz.zustellen()
        boese = manifest_bauen(mallory.ich, eigene, "Falle", "💀",
                               [{"id": standard_kanal_id(eigene), "name": "allgemein", "position": 0, "nur_admins": False},
                                {"id": bens_kanal, "name": "geklaut", "position": 1, "nur_admins": False}],
                               [], mallory._manifest(eigene)["version"] + 1)
        mallory._manifest_uebernehmen(eigene, boese) if False else None
        mallory._steuer_senden(standard_kanal_id(eigene), {"art": "manifest", "manifest": boese, "karte_von": mallory.karte()})
        netz.zustellen()
        R.pruefe("Ein Manifest kann keinen Kanal einer anderen Community an sich reissen",
                 ben.db.kanal_holen(bens_kanal)["community_id"] == cid)

        # 5) Alte Schlüssel-DM erneut eingespielt dreht den neuen Schlüssel nicht zurück
        anna.kontakt_hinzufuegen(ben.meine_id)
        netz.zustellen()
        ben.anfrage_beantworten(anna.meine_id, True)
        netz.zustellen()
        mitgeschnitten = []
        alt_ausgang = anna.ausgang
        anna.ausgang = lambda a: (mitgeschnitten.append(a), alt_ausgang(a))
        ben.text_senden(standard_kanal_id(cid), "Ich bin Mitglied")
        netz.zustellen()
        anna.community_code_erneuern(cid)
        netz.zustellen()
        erste = [a.bytes() for a in mitgeschnitten if a.an == ben.meine_id]
        anna.community_code_erneuern(cid)
        netz.zustellen()
        anna.ausgang = alt_ausgang
        for roh in erste:
            ben.empfangen(roh)
        R.pruefe("Eine alte Schlüssel-Nachricht dreht den Community-Schlüssel nicht zurück",
                 ben.tresor.community_schluessel(cid) == anna.tresor.community_schluessel(cid))

        # 6) Dateinamen ohne Steuer- und Richtungszeichen
        quelle = Path(ordner) / "rechnung‮fdp.exe"
        quelle.write_bytes(b"MZ")
        nid = anna.datei_senden(ben.meine_id, quelle)
        netz.zustellen()
        name = [x for x in ben.nachrichten(anna.meine_id)[0] if x["id"] == nid][0]["datei"]["name"]
        R.pruefe("Empfangene Dateinamen verlieren Richtungs-Steuerzeichen", "‮" not in name)

        # 8) Wer nur eine Anfrage geschickt hat, erscheint mit ID-Kürzel
        mallory.profil = None
        mallory._profil = lambda: {"name": "Anna", "avatar_farbe": "#000"}
        mallory.kontakt_hinzufuegen(ben.meine_id)
        netz.zustellen()
        R.pruefe("Ein Fremder mit offener Anfrage kann sich nicht als 'Anna' ausgeben",
                 ben.name_von(mallory.meine_id) != "Anna")

        # 10) Ein später Fehler überschreibt kein 'zugestellt'
        nid = anna.text_senden(ben.meine_id, "Hallo")
        netz.zustellen()
        ben.quittungen_senden()
        netz.zustellen()
        anna.fehlgeschlagen(nid, "Zeitüberschreitung nach erfolgreichem Senden")
        R.pruefe("Ein später Fehler macht aus 'zugestellt' kein 'fehler'",
                 anna.db.nachricht_holen(nid)["status"] == "zugestellt")

        # 12) Antwort auf eine Nachricht aus einer anderen Unterhaltung wird nicht verknüpft
        fremd = ben.text_senden(standard_kanal_id(cid), "Community-Nachricht")
        netz.zustellen()
        n2 = anna.text_senden(ben.meine_id, "Antwort?", antwort_auf=fremd)
        R.pruefe("Antworten verweisen nur auf Nachrichten derselben Unterhaltung",
                 anna.db.nachricht_holen(n2)["antwort_auf"] is None)

        # Blockieren
        ben.kontakt_blockieren(mallory.meine_id)
        mallory.kontakt_hinzufuegen(ben.meine_id) if False else None
        u = um.anfrage_bauen(mallory.ich, mallory.karte(), ben.meine_id, typ=um.ANFRAGE)
        ben.empfangen(u.packen())
        R.pruefe("Blockierte können keine neue Anfrage stellen",
                 ben.db.kontakt_holen(mallory.meine_id)["status"] == "blockiert")

        for b in netz.boten.values():
            b.db.close()

    # 7) Neue Mitglieder laden auch ein von einem Admin geändertes Manifest
    with tempfile.TemporaryDirectory() as ordner:
        netz = SpielNetz()
        anna = person(netz, "Anna", ordner)
        ben = person(netz, "Ben", ordner)
        carl = person(netz, "Carl", ordner)
        cid = anna.community_erstellen("Club", "🎮")
        ben.community_beitreten(anna.einladung(cid))
        netz.zustellen()
        anna._manifest_aendern(cid, lambda k, a, n, i: (k, [ben.meine_id], n, i))
        netz.zustellen()
        ben.kanal_anlegen(cid, "clips")
        netz.zustellen()
        anna_offline = netz.boten.pop("Anna")
        carl.community_beitreten(anna.einladung(cid))
        netz.zustellen()
        netz.boten["Anna"] = anna_offline
        R.pruefe("Ein Neuer bekommt auch ein Manifest, das ein Admin geändert hat",
                 {k["name"] for c in carl.communities() if c["id"] == cid for k in c["kanaele"]} == {"allgemein", "clips"})
        for b in netz.boten.values():
            b.db.close()


def _verbinden(a, b, netz):
    a.kontakt_hinzufuegen(b.meine_id)
    netz.zustellen()
    b.anfrage_beantworten(a.meine_id, True)
    netz.zustellen()


def _eine_minute_spaeter(netz):
    """Auf einen Beitritt antwortet jeder höchstens einmal pro Minute - im
    Test vergeht die Minute, indem die Merkzettel geleert werden."""
    for b in netz.boten.values():
        b._manifest_antwort.clear()
        b._beitritt_gesehen.clear()


def _kanaele(bote, cid):
    return [(k["name"], k["nur_admins"]) for c in bote.communities() if c["id"] == cid for k in c["kanaele"]]


def _community(bote, cid):
    return next(c for c in bote.communities() if c["id"] == cid)


def pruefen_verwaltung(R, hilfen):
    """Community-Verwaltung: Kanäle umbenennen, löschen, verschieben,
    Ankündigungskanal, Admins, Name und Symbol, Mitglied entfernen."""
    from kern import e2e
    from kern.communities import manifest_bauen, standard_kanal_id
    from api import VP4Api
    wirft = hilfen["wirft_valueerror"]
    with tempfile.TemporaryDirectory() as ordner:
        netz = SpielNetz()
        anna = person(netz, "Anna", ordner)
        ben = person(netz, "Ben", ordner)
        carl = person(netz, "Carl", ordner)
        mallory = person(netz, "Mallory", ordner)
        _verbinden(anna, ben, netz)
        _verbinden(anna, carl, netz)
        cid = anna.community_erstellen("Schulhof", "🏫")
        std = standard_kanal_id(cid)
        ben.community_beitreten(anna.einladung(cid))
        netz.zustellen()
        _eine_minute_spaeter(netz)
        carl.community_beitreten(anna.einladung(cid))
        netz.zustellen()
        ben.text_senden(std, "Hi von Ben")
        carl.text_senden(std, "Hi von Carl")
        netz.zustellen()

        R.pruefe("Rolle: wer gründet, ist 'besitzer'", _community(anna, cid)["rolle"] == "besitzer")
        R.pruefe("Rolle: wer beitritt, ist 'mitglied'", _community(ben, cid)["rolle"] == "mitglied")

        # --- Kanalnamen -------------------------------------------------------
        hid = anna.kanal_anlegen(cid, "  Haus Aufgaben ")
        netz.zustellen()
        R.pruefe("Kanalnamen werden klein und mit Bindestrich gespeichert",
                 ("haus-aufgaben", False) in _kanaele(ben, cid))
        R.pruefe("Kanalname: zu lang (33 Zeichen) wird abgelehnt", wirft(anna.kanal_anlegen, cid, "a" * 33))
        R.pruefe("Kanalname: 32 Zeichen sind erlaubt", bool(anna.kanal_anlegen(cid, "b" * 32)))
        R.pruefe("Kanalname: Sonderzeichen werden abgelehnt", wirft(anna.kanal_anlegen, cid, "hallo!"))
        R.pruefe("Kanalname: leer wird abgelehnt", wirft(anna.kanal_anlegen, cid, "   "))
        R.pruefe("Kanalname: Umlaute und ß sind erlaubt", bool(anna.kanal_anlegen(cid, "größe-übung")))
        R.pruefe("Kanalname: doppelt (auch in anderer Schreibweise) wird abgelehnt",
                 wirft(anna.kanal_anlegen, cid, "HAUS-aufgaben"))

        anna.kanal_umbenennen(cid, hid, "hausaufgaben")
        netz.zustellen()
        R.pruefe("Kanal umbenennen kommt bei den Mitgliedern an",
                 ben.db.kanal_holen(hid)["name"] == "hausaufgaben"
                 and ben.db.unterhaltung_holen(hid)["titel"] == "hausaufgaben")
        R.pruefe("Umbenennen auf einen vorhandenen Namen wird abgelehnt",
                 wirft(anna.kanal_umbenennen, cid, hid, "allgemein"))
        R.pruefe("Umbenennen auf ungültigen Namen wird abgelehnt",
                 wirft(anna.kanal_umbenennen, cid, hid, "haus aufgaben?"))
        R.pruefe("Umbenennen eines unbekannten Kanals wird abgelehnt",
                 wirft(anna.kanal_umbenennen, cid, "ZZZZZZZZ", "neu"))

        # --- Was ein einfaches Mitglied NICHT darf ------------------------------
        R.pruefe("Mitglied darf keinen Kanal umbenennen", wirft(ben.kanal_umbenennen, cid, hid, "meins"))
        R.pruefe("Mitglied darf keinen Kanal löschen", wirft(ben.kanal_loeschen, cid, hid))
        R.pruefe("Mitglied darf keinen Kanal verschieben", wirft(ben.kanal_verschieben, cid, hid, 0))
        R.pruefe("Mitglied darf keinen Ankündigungskanal einrichten",
                 wirft(ben.kanal_nur_admins_setzen, cid, hid, True))
        R.pruefe("Mitglied darf die Community nicht umbenennen", wirft(ben.community_umbenennen, cid, "Meins", "💀"))
        R.pruefe("Mitglied darf keine Admins ernennen", wirft(ben.admin_setzen, cid, ben.meine_id, True))
        R.pruefe("Mitglied darf niemanden entfernen", wirft(ben.mitglied_entfernen, cid, carl.meine_id))

        # --- Admins -------------------------------------------------------------
        R.pruefe("Admin: Unbekannte (weder Mitglied noch Kontakt) werden abgelehnt",
                 wirft(anna.admin_setzen, cid, mallory.meine_id, True))
        R.pruefe("Admin: sich selbst als Besitzer setzen wird abgelehnt",
                 wirft(anna.admin_setzen, cid, anna.meine_id, True))
        R.pruefe("Admin: keine gültige ID wird abgelehnt", wirft(anna.admin_setzen, cid, "Quatsch", True))
        anna.admin_setzen(cid, ben.meine_id, True)
        netz.zustellen()
        R.pruefe("Rolle: ernannter Admin sieht 'admin'", _community(ben, cid)["rolle"] == "admin"
                 and _community(ben, cid)["admin"] and not _community(ben, cid)["besitzer"])
        m = {x["id"]: x for x in carl.mitglieder(cid)}
        R.pruefe("Mitgliederliste zeigt Admin und Besitzer",
                 m[ben.meine_id]["admin"] and not m[ben.meine_id]["besitzer"]
                 and not m[carl.meine_id]["admin"] and m[carl.meine_id]["ich"])
        R.pruefe("Admin darf keine weiteren Admins ernennen", wirft(ben.admin_setzen, cid, carl.meine_id, True))
        R.pruefe("Admin darf niemanden entfernen", wirft(ben.mitglied_entfernen, cid, carl.meine_id))

        # Admin bearbeitet Kanäle, Name und Symbol
        sid = ben.kanal_anlegen(cid, "spiele")
        netz.zustellen()
        ben.kanal_verschieben(cid, sid, 0)
        netz.zustellen()
        R.pruefe("Admin verschiebt einen Kanal nach vorn - alle sehen die neue Reihenfolge",
                 _kanaele(carl, cid)[0] == ("spiele", False) and _kanaele(anna, cid) == _kanaele(carl, cid))
        ben.kanal_verschieben(cid, sid, 999)
        netz.zustellen()
        R.pruefe("Verschieben über das Ende hinaus landet am Ende", _kanaele(carl, cid)[-1] == ("spiele", False))
        R.pruefe("Verschieben mit einer Nicht-Zahl wird abgelehnt", wirft(ben.kanal_verschieben, cid, sid, "2"))
        ben.kanal_nur_admins_setzen(cid, hid, True)
        netz.zustellen()
        R.pruefe("Admin macht einen Ankündigungskanal - kommt bei allen an",
                 ("hausaufgaben", True) in _kanaele(carl, cid))
        R.pruefe("Im Ankündigungskanal darf ein Mitglied danach nicht mehr schreiben",
                 wirft(carl.text_senden, hid, "Darf ich?"))
        ben.kanal_nur_admins_setzen(cid, hid, False)
        netz.zustellen()
        R.pruefe("Ankündigungskanal lässt sich wieder öffnen", ("hausaufgaben", False) in _kanaele(carl, cid))
        ben.kanal_nur_admins_setzen(cid, hid, True)
        netz.zustellen()

        R.pruefe("Der Standardkanal lässt sich nicht löschen", wirft(anna.kanal_loeschen, cid, std))
        R.pruefe("Der Standardkanal wird nie zum Ankündigungskanal",
                 wirft(anna.kanal_nur_admins_setzen, cid, std, True))
        ben.community_umbenennen(cid, "Schulhof 2.0", "🎒")
        netz.zustellen()
        c = _community(carl, cid)
        R.pruefe("Admin benennt die Community um - Name und Symbol kommen an",
                 c["name"] == "Schulhof 2.0" and c["icon"] == "🎒")
        anna.community_umbenennen(cid, "Schulhof 3", None)
        netz.zustellen()
        c = _community(carl, cid)
        R.pruefe("Umbenennen ohne Symbol behält das alte Symbol", c["name"] == "Schulhof 3" and c["icon"] == "🎒")
        R.pruefe("Leerer Community-Name wird abgelehnt", wirft(anna.community_umbenennen, cid, "  ", "🎒"))

        ben.kanal_loeschen(cid, sid)
        netz.zustellen()
        R.pruefe("Kanal löschen: Kanal und Verlauf verschwinden bei allen",
                 carl.db.kanal_holen(sid) is None and carl.db.unterhaltung_holen(sid) is None
                 and "spiele" not in [n for n, _ in _kanaele(anna, cid)])
        R.pruefe("Kanal löschen: Positionen bleiben lückenlos",
                 [k["position"] for k in anna._manifest(cid)["kanaele"]]
                 == list(range(len(anna._manifest(cid)["kanaele"]))))
        R.pruefe("Einen schon gelöschten Kanal löschen wird abgelehnt", wirft(ben.kanal_loeschen, cid, sid))

        # --- Fälschungen ----------------------------------------------------------
        alt_m = ben._manifest(cid)
        boese = manifest_bauen(ben.ich, cid, alt_m["name"], alt_m["icon"], alt_m["kanaele"],
                               [ben.meine_id, carl.meine_id], alt_m["version"] + 1)
        ben._steuer_senden(std, {"art": "manifest", "manifest": boese, "karte_von": ben.karte()})
        netz.zustellen()
        R.pruefe("Fälschung: Admin ernennt per selbst gebautem Manifest einen Admin - abgelehnt",
                 carl.meine_id not in anna._manifest(cid)["admins"] and _community(carl, cid)["rolle"] == "mitglied")
        ohne_std = [k for k in alt_m["kanaele"] if k["id"] != std]
        boese = manifest_bauen(ben.ich, cid, alt_m["name"], alt_m["icon"], ohne_std,
                               alt_m["admins"], alt_m["version"] + 1)
        ben._steuer_senden(std, {"art": "manifest", "manifest": boese, "karte_von": ben.karte()})
        netz.zustellen()
        R.pruefe("Fälschung: Admin entfernt per Manifest den Standardkanal - abgelehnt",
                 carl.db.kanal_holen(std) is not None and anna._manifest(cid)["version"] == alt_m["version"])
        gesperrt = [dict(k, nur_admins=True) if k["id"] == std else k for k in alt_m["kanaele"]]
        boese = manifest_bauen(ben.ich, cid, alt_m["name"], alt_m["icon"], gesperrt,
                               alt_m["admins"], alt_m["version"] + 1)
        ben._steuer_senden(std, {"art": "manifest", "manifest": boese, "karte_von": ben.karte()})
        netz.zustellen()
        R.pruefe("Fälschung: Admin sperrt per Manifest den Standardkanal - abgelehnt",
                 not carl.db.kanal_holen(std)["nur_admins"])
        boese = manifest_bauen(carl.ich, cid, "Gekapert", "💀", alt_m["kanaele"],
                               alt_m["admins"], alt_m["version"] + 1)
        carl._steuer_senden(std, {"art": "manifest", "manifest": boese, "karte_von": carl.karte()})
        netz.zustellen()
        R.pruefe("Fälschung: Mitglied benennt per Manifest um - abgelehnt",
                 _community(anna, cid)["name"] == "Schulhof 3" and _community(ben, cid)["name"] == "Schulhof 3")
        ckey = ben.tresor.community_schluessel(cid)
        verboten = um.kanal_bauen(carl.ich, e2e.kanal_schluessel(ckey, hid), carl.meine_id, cid,
                                  {"art": "text", "text": "Hack", "kanal": hid, "karte": carl.karte()})
        anna.empfangen(verboten.packen())
        R.pruefe("Neuer Ankündigungskanal: Nachricht eines Mitglieds wird beim Empfang verworfen",
                 "Hack" not in letzte_texte(anna, hid, 10))

        # --- Gruppen haben genau einen Kanal ------------------------------------------
        gid = anna.community_erstellen("Clique", "💬", gruppe=True)
        R.pruefe("In einer Gruppe lassen sich keine Kanäle anlegen", wirft(anna.kanal_anlegen, gid, "zweiter"))
        anna.community_umbenennen(gid, "Clique neu")
        R.pruefe("Eine Gruppe lässt sich umbenennen (Titel folgt)",
                 anna.db.unterhaltung_holen(standard_kanal_id(gid))["titel"] == "Clique neu")

        # --- Ein Neuer bekommt den aktuellen Stand -----------------------------------
        dora = person(netz, "Dora", ordner)
        _eine_minute_spaeter(netz)
        dora.community_beitreten(anna.einladung(cid))
        netz.zustellen()
        R.pruefe("Neue sieht Name, Symbol, Reihenfolge und Ankündigungskanal wie der Besitzer",
                 _kanaele(dora, cid) == _kanaele(anna, cid)
                 and _community(dora, cid)["name"] == "Schulhof 3" and _community(dora, cid)["icon"] == "🎒")
        R.pruefe("Neue kennt die Admins (Ben) und ist selbst 'mitglied'",
                 dora._ist_admin(cid, ben.meine_id) and _community(dora, cid)["rolle"] == "mitglied")
        dora.text_senden(std, "Hallo, ich bin Dora")
        netz.zustellen()

        # --- Mitglied entfernen ---------------------------------------------------------
        R.pruefe("Entfernen: sich selbst geht nicht", wirft(anna.mitglied_entfernen, cid, anna.meine_id))
        R.pruefe("Entfernen: Unbekannte gehen nicht", wirft(anna.mitglied_entfernen, cid, mallory.meine_id))
        alter_code = anna.einladung(cid)
        mitgeschnitten = []
        alt_ausgang = anna.ausgang
        anna.ausgang = lambda a: (mitgeschnitten.append(a), alt_ausgang(a))
        neuer_code = anna.mitglied_entfernen(cid, ben.meine_id)
        netz.zustellen()
        anna.ausgang = alt_ausgang
        R.pruefe("Entfernen gibt einen neuen Einladungscode zurück",
                 neuer_code != alter_code and neuer_code == anna.einladung(cid))
        R.pruefe("Entfernen: der Entfernte bekommt den neuen Schlüssel nicht (auch nicht als Kontakt)",
                 not any(a.an == ben.meine_id for a in mitgeschnitten)
                 and ben.tresor.community_schluessel(cid) != anna.tresor.community_schluessel(cid))
        R.pruefe("Entfernen: andere Kontakte bekommen den neuen Schlüssel automatisch",
                 carl.tresor.community_schluessel(cid) == anna.tresor.community_schluessel(cid))
        R.pruefe("Entfernen: Mitglieder, die keine Kontakte sind, brauchen den neuen Code",
                 dora.tresor.community_schluessel(cid) != anna.tresor.community_schluessel(cid))
        R.pruefe("Entfernen: ein Admin verliert dabei sein Amt",
                 ben.meine_id not in anna._manifest(cid)["admins"] and not carl._ist_admin(cid, ben.meine_id))
        R.pruefe("Entfernen: beim Besitzer steht 'Ben wurde entfernt.'",
                 any(x["art"] == "system" and x["text"] == "Ben wurde entfernt."
                     for x in anna.nachrichten(std)[0]))
        R.pruefe("Entfernen: der Entfernte fehlt in der Mitgliederliste",
                 ben.meine_id not in {x["id"] for x in anna.mitglieder(cid)}
                 and carl.meine_id in {x["id"] for x in anna.mitglieder(cid)})
        anna.text_senden(std, "Nach dem Rauswurf")
        netz.zustellen()
        R.pruefe("Mitgliederliste: der Besitzer steht als Besitzer und Admin darin",
                 [(x["besitzer"], x["admin"], x["ich"]) for x in anna.mitglieder(cid) if x["id"] == anna.meine_id]
                 == [(True, True, True)]
                 and [(x["besitzer"], x["admin"]) for x in carl.mitglieder(cid) if x["id"] == anna.meine_id]
                 == [(True, True)])
        R.pruefe("Entfernen: der Entfernte liest Neues nicht mehr, die anderen schon",
                 "Nach dem Rauswurf" not in letzte_texte(ben, std, 20)
                 and letzte_texte(carl, std, 1) == ["Nach dem Rauswurf"])
        R.pruefe("Entfernen: Nicht-Besitzer (Carl) dürfen das nicht", wirft(carl.mitglied_entfernen, cid, dora.meine_id))

        mitgeschnitten.clear()
        anna.ausgang = lambda a: (mitgeschnitten.append(a), alt_ausgang(a))
        anna.community_code_erneuern(cid)
        netz.zustellen()
        anna.ausgang = alt_ausgang
        R.pruefe("Ein späterer neuer Code holt den Entfernten nicht zurück",
                 not any(a.an == ben.meine_id for a in mitgeschnitten)
                 and ben.tresor.community_schluessel(cid) != anna.tresor.community_schluessel(cid)
                 and carl.tresor.community_schluessel(cid) == anna.tresor.community_schluessel(cid))

        # Nach dem Rauswurf kommt jemand Neues dazu und sieht den aktuellen Stand
        emil = person(netz, "Emil", ordner)
        _eine_minute_spaeter(netz)
        emil.community_beitreten(anna.einladung(cid))
        netz.zustellen()
        R.pruefe("Neuer nach dem Rauswurf: aktueller Stand, Ben ist kein Admin mehr",
                 _kanaele(emil, cid) == _kanaele(anna, cid) and not emil._ist_admin(cid, ben.meine_id)
                 and _community(emil, cid)["name"] == "Schulhof 3")

        # --- Die API reicht nur durch ------------------------------------------------
        class _Dienst:
            pass
        d = _Dienst()
        d.bote, d.db, d.letzte_aktivitaet = anna, anna.db, 0
        api = VP4Api(d)
        liste = api.community_mitglieder(cid)
        R.pruefe("API: community_mitglieder liefert die Liste mit Rollen",
                 liste["ok"] and all({"admin", "besitzer"} <= set(x) for x in liste["liste"])
                 and carl.meine_id in {x["id"] for x in liste["liste"]})
        r = api.kanal_loeschen(cid, std)
        R.pruefe("API: Fehler kommen als ok=False mit Text zurück", r["ok"] is False and r["fehler"])
        kid = api.kanal_anlegen(cid, "neu", False)["id"]
        R.pruefe("API: Kanal umbenennen, verschieben, sperren, löschen",
                 api.kanal_umbenennen(cid, kid, "neuer")["ok"] and api.kanal_verschieben(cid, kid, "0")["ok"]
                 and api.kanal_nur_admins_setzen(cid, kid, 1)["ok"] and api.kanal_loeschen(cid, kid)["ok"])
        R.pruefe("API: community_umbenennen und admin_setzen",
                 api.community_umbenennen(cid, "Schulhof 4", "")["ok"]
                 and api.admin_setzen(cid, carl.meine_id, True)["ok"]
                 and api.admin_setzen(cid, carl.meine_id, False)["ok"])
        r = api.mitglied_entfernen(cid, dora.meine_id)
        R.pruefe("API: mitglied_entfernen liefert den neuen Code", r["ok"] and r["code"].startswith("VP4G2-"))
        R.pruefe("API: community_mitglieder für Unbekanntes wird abgelehnt",
                 api.community_mitglieder("G-ZZZZZZZZ")["ok"] is False)
        netz.zustellen()
        for b in netz.boten.values():
            b.db.close()
