# -*- coding: utf-8 -*-
"""Prüfungen für Identität, Ende-zu-Ende-Krypto, Umschlag und Communities.

Wird von test_vp4.py über pruefen(R, hilfen) aufgerufen. Kein Netz, keine
Platte - alles hier ist reine Rechnung.
"""

import base64
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from kern import communities, e2e, identitaet, umschlag  # noqa: E402
from kern.identitaet import Identitaet  # noqa: E402
from kern.umschlag import Umschlag  # noqa: E402


class _NullpunktIdentitaet(Identitaet):
    """Eine Identität, die als X25519-Schlüssel 32 Nullbytes vorzeigt.

    So lässt sich eine korrekt unterschriebene Karte mit einem Punkt
    niedriger Ordnung bauen - genau das, was ein Angreifer schicken würde.
    """

    @property
    def x_pub(self):
        return bytes(32)


def _kopie(d):
    return json.loads(json.dumps(d))


def pruefen(R, hilfen):
    wirft = hilfen["wirft_valueerror"]

    alice = Identitaet.neu()
    bob = Identitaet.neu()
    carol = Identitaet.neu()
    k_alice = identitaet.karte_bauen(alice, "Alice Müller", "#ff8800")
    k_bob = identitaet.karte_bauen(bob, "Bob", "#0088ff")
    k_carol = identitaet.karte_bauen(carol, "Carol", "#00aa44")

    # --- ID ------------------------------------------------------------
    print("\n--- Identität und ID ---")
    R.pruefe("ID hat die Form XXXXX-XXXXX", identitaet.ist_nutzer_id(alice.id), alice.id)
    R.pruefe("ID ist deterministisch aus den Schlüsseln",
             identitaet.id_aus_schluesseln(alice.ed_pub, alice.x_pub) == alice.id)
    R.pruefe("Zwei Identitäten haben verschiedene IDs", alice.id != bob.id)
    R.pruefe("ID nutzt nur das Crockford-Alphabet",
             all(z in identitaet.CROCKFORD for z in alice.id.replace("-", "")))
    # Bekannter Wert, damit sich die Ableitung nie unbemerkt ändert.
    import hashlib
    erwartet_digest = hashlib.sha256(b"VP4-ID1" + bytes(32) + bytes(range(32))).digest()
    zahl = int.from_bytes(erwartet_digest, "big") >> (256 - 50)
    erwartet = "".join(identitaet.CROCKFORD[(zahl >> (5 * (9 - i))) & 31] for i in range(10))
    R.pruefe("ID-Ableitung entspricht SHA-256 der ersten 50 Bit",
             identitaet.id_aus_schluesseln(bytes(32), bytes(range(32)))
             == erwartet[:5] + "-" + erwartet[5:])
    roh = alice.id.replace("-", "")
    R.pruefe("ID ohne Bindestrich und klein geschrieben wird normalisiert",
             identitaet.id_normalisieren("  " + roh.lower() + " ") == alice.id)
    R.pruefe("O/I/L werden als 0/1/1 gelesen",
             identitaet.id_normalisieren("oilOI-LLOO0") == "01101-11000")
    R.pruefe("Ungültige ID wird abgelehnt (U, zu kurz)",
             wirft(identitaet.id_normalisieren, "UUUUU-UUUUU")
             and wirft(identitaet.id_normalisieren, "ABCD-1234"))
    R.pruefe("ist_nutzer_id ist streng (klein geschrieben = nein)",
             not identitaet.ist_nutzer_id(alice.id.lower()) and not identitaet.ist_nutzer_id(roh))
    wieder = Identitaet.from_bytes(alice.to_bytes())
    R.pruefe("Identität übersteht to_bytes/from_bytes",
             wieder.id == alice.id and wieder.x_pub == alice.x_pub
             and len(alice.to_bytes()) == 65)
    R.pruefe("Kaputte gespeicherte Identität wird abgelehnt",
             wirft(Identitaet.from_bytes, b"\x02" + alice.to_bytes()[1:])
             and wirft(Identitaet.from_bytes, alice.to_bytes()[:-1]))

    # --- Visitenkarte ---------------------------------------------------
    print("\n--- Visitenkarte ---")
    try:
        geprueft = identitaet.karte_pruefen(k_alice)
        R.pruefe("Eigene Karte besteht die Prüfung", geprueft == k_alice)
    except Exception as e:
        R.fehlschlag("Eigene Karte besteht die Prüfung", e)
    R.pruefe("Karte hat die verlangten Felder",
             set(k_alice) == {"v", "id", "name", "ed", "x", "avatar_farbe", "ts", "sig"}
             and k_alice["v"] == 1)
    getauscht = _kopie(k_alice)
    getauscht["ed"], getauscht["x"] = getauscht["x"], getauscht["ed"]
    R.pruefe("Karte mit vertauschten Schlüsseln wird abgelehnt",
             wirft(identitaet.karte_pruefen, getauscht))
    umbenannt = _kopie(k_alice)
    umbenannt["name"] = "Alice Mueller"
    R.pruefe("Karte mit geändertem Namen wird abgelehnt",
             wirft(identitaet.karte_pruefen, umbenannt))
    fremd = _kopie(k_alice)
    fremd["ed"], fremd["x"] = k_bob["ed"], k_bob["x"]
    R.pruefe("Fremde Schlüssel unter eigener ID werden abgelehnt",
             wirft(identitaet.karte_pruefen, fremd))
    # Bob unterschreibt eine Karte, die Alices ID behauptet: korrekt
    # unterschrieben, aber die Schlüssel ergeben nicht die ID.
    felder = {k: v for k, v in k_bob.items() if k != "sig"}
    felder["id"] = alice.id
    falsch_id = dict(felder, sig=identitaet.b64(bob.signieren(
        identitaet.KARTE_SIGNATUR_MARKE + identitaet.kanonisch(felder))))
    R.pruefe("Gültig unterschriebene Karte mit falscher ID wird abgelehnt",
             wirft(identitaet.karte_pruefen, falsch_id))
    zusatz = _kopie(k_alice)
    zusatz["admin"] = True
    R.pruefe("Karte mit unbekanntem Feld wird abgelehnt", wirft(identitaet.karte_pruefen, zusatz))
    R.pruefe("Zu langer Name wird abgelehnt",
             wirft(identitaet.karte_bauen, alice, "x" * 65, "#000000"))
    R.pruefe("Name mit Richtungs-Steuerzeichen wird abgelehnt",
             wirft(identitaet.karte_bauen, alice, "Max‮evil", "#000000"))
    R.pruefe("Karte bleibt unter 2 KB", len(identitaet.kanonisch(k_alice)) <= 2048)

    code = identitaet.freundescode_bauen(k_alice)
    R.pruefe("Freundescode beginnt mit VP4C1- und ist eine Zeile",
             code.startswith("VP4C1-") and "\n" not in code and "=" not in code)
    try:
        R.pruefe("Freundescode übersteht Hin und Zurück",
                 identitaet.freundescode_lesen(code) == k_alice)
        R.pruefe("Freundescode mit Zeilenumbruch beim Kopieren wird gelesen",
                 identitaet.freundescode_lesen(code[:30] + "\n " + code[30:]) == k_alice)
    except Exception as e:
        R.fehlschlag("Freundescode übersteht Hin und Zurück", e)
    kaputt = code[:-3] + ("A" if code[-3] != "A" else "B") + code[-2:]
    R.pruefe("Veränderter / abgeschnittener / fremder Freundescode wird abgelehnt",
             wirft(identitaet.freundescode_lesen, kaputt)
             and wirft(identitaet.freundescode_lesen, code[:40])
             and wirft(identitaet.freundescode_lesen, "VP4C1-!!!")
             and wirft(identitaet.freundescode_lesen, "hallo"))

    # --- Paarschlüssel --------------------------------------------------
    print("\n--- Paarschlüssel ---")
    ab = e2e.paar_schluessel(alice, k_bob)
    ba = e2e.paar_schluessel(bob, k_alice)
    ac = e2e.paar_schluessel(alice, k_carol)
    cb = e2e.paar_schluessel(carol, k_bob)
    R.pruefe("Beide Seiten leiten denselben Paarschlüssel ab", ab == ba and len(ab) == 32)
    R.pruefe("Ein Dritter bekommt einen anderen Schlüssel", ab != ac and ab != cb)
    R.pruefe("Paarschlüssel mit gefälschter Karte wird verweigert",
             wirft(e2e.paar_schluessel, alice, fremd))
    null = _NullpunktIdentitaet(bob._ed, bob._x)
    null_karte = identitaet.karte_bauen(null, "Nullpunkt", "#000000")
    R.pruefe("Karte mit Nullpunkt-Schlüssel ist für sich gültig unterschrieben",
             identitaet.karte_pruefen(null_karte)["x"] == base64.b64encode(bytes(32)).decode())
    R.pruefe("X25519-Punkt niedriger Ordnung (lauter Nullen) wird abgelehnt",
             wirft(e2e.paar_schluessel, alice, null_karte)
             and wirft(alice.dh, bytes(32)))

    # --- Nachrichtenverschlüsselung --------------------------------------
    print("\n--- DM-Verschlüsselung ---")
    mid = os.urandom(16)
    nonce, ct = e2e.nachricht_verschluesseln(ab, mid, b"kopf", "Grüße äöüß".encode())
    R.pruefe("Nachricht übersteht Hin und Zurück",
             e2e.nachricht_entschluesseln(ba, mid, b"kopf", nonce, ct) == "Grüße äöüß".encode())
    R.pruefe("Falscher Schlüssel / falsche ID / anderer Kopf ergeben ValueError",
             wirft(e2e.nachricht_entschluesseln, ac, mid, b"kopf", nonce, ct)
             and wirft(e2e.nachricht_entschluesseln, ab, os.urandom(16), b"kopf", nonce, ct)
             and wirft(e2e.nachricht_entschluesseln, ab, mid, b"kopF", nonce, ct))

    inner = {"art": "text", "text": "Hallo Bob! Grüße aus der Straße 5"}
    dm = umschlag.dm_bauen(alice, ab, alice.id, bob.id, inner)
    gepackt = dm.packen()
    try:
        empfangen = Umschlag.entpacken(gepackt)
        R.pruefe("Umschlag übersteht packen/entpacken",
                 empfangen == dm and empfangen.von == alice.id and empfangen.an == bob.id)
        R.pruefe("DM übersteht Hin und Zurück", umschlag.dm_lesen(empfangen, ba) == inner)
    except Exception as e:
        R.fehlschlag("DM übersteht Hin und Zurück", e)
    R.pruefe("Kopf ist 64 Byte und beginnt mit VP4N",
             len(dm.kopf()) == umschlag.KOPF_LAENGE == 64 and gepackt[:4] == b"VP4N")

    umadressiert = Umschlag.entpacken(gepackt)
    umadressiert.an = carol.id
    R.pruefe("Veränderter Empfänger im Kopf wird abgelehnt",
             wirft(umschlag.dm_lesen, Umschlag.entpacken(umadressiert.packen()), ba))
    roh = bytearray(gepackt)
    roh[-1] ^= 1
    R.pruefe("Veränderter Geheimtext wird abgelehnt",
             wirft(umschlag.dm_lesen, Umschlag.entpacken(bytes(roh)), ba))
    R.pruefe("Falscher Paarschlüssel wird abgelehnt",
             wirft(umschlag.dm_lesen, Umschlag.entpacken(gepackt), ac))
    nachgespielt = Umschlag.entpacken(gepackt)
    nachgespielt.ts_ms += 60_000
    R.pruefe("Wiederholter Umschlag mit geänderter Zeit wird abgelehnt",
             wirft(umschlag.dm_lesen, nachgespielt, ba))
    anderer_absender = Umschlag.entpacken(gepackt)
    anderer_absender.von = carol.id
    R.pruefe("Veränderter Absender im Kopf wird abgelehnt",
             wirft(umschlag.dm_lesen, anderer_absender, ba))

    # --- Umschlag: Aufbau ------------------------------------------------
    print("\n--- Umschlag ---")
    zu_gross = gepackt[:64] + bytes(umschlag.MAX_UMSCHLAG)
    R.pruefe("Kaputte Umschläge werden abgelehnt (Marke, Version, Länge, Typ)",
             wirft(Umschlag.entpacken, b"XXXX" + gepackt[4:])
             and wirft(Umschlag.entpacken, gepackt[:4] + b"\x02" + gepackt[5:])
             and wirft(Umschlag.entpacken, gepackt[:63])
             and wirft(Umschlag.entpacken, gepackt[:5] + b"\x63" + gepackt[6:])
             and wirft(Umschlag.entpacken, zu_gross)
             and wirft(Umschlag.entpacken, b""))
    nicht_ascii = bytearray(gepackt)
    nicht_ascii[6] = 0xC3
    leer_an = bytearray(gepackt)
    leer_an[16:28] = b" " * 12
    R.pruefe("Nicht-ASCII-Absender und leerer Empfänger werden abgelehnt",
             wirft(Umschlag.entpacken, bytes(nicht_ascii))
             and wirft(Umschlag.entpacken, bytes(leer_an)))
    gid = communities.community_id_neu()
    g_umschlag = Umschlag(umschlag.NACHRICHT, alice.id, gid, os.urandom(16), 123, os.urandom(12), b"x")
    R.pruefe("Community-Kennung als Empfänger übersteht packen/entpacken",
             Umschlag.entpacken(g_umschlag.packen()) == g_umschlag
             and g_umschlag.kopf()[16:28] == (gid + "  ").encode())
    R.pruefe("Nutzer-ID im Kopf steht ohne Bindestrich",
             dm.kopf()[6:16] == alice.id.replace("-", "").encode())
    teil = umschlag.verschluesselt_bauen(ab, alice.id, bob.id, os.urandom(5000), umschlag.DATEI_TEIL)
    R.pruefe("Datei-Teil übersteht Verschlüsseln und Entschlüsseln",
             len(umschlag.verschluesselt_lesen(Umschlag.entpacken(teil.packen()), ba)) == 5000)

    # --- Anfrage ---------------------------------------------------------
    print("\n--- Anfrage / Handschlag ---")
    anfrage = umschlag.anfrage_bauen(alice, k_alice, bob.id)
    try:
        angekommen = Umschlag.entpacken(anfrage.packen())
        R.pruefe("Anfrage wird geprüft und liefert Alices Karte",
                 umschlag.karten_umschlag_lesen(angekommen) == k_alice
                 and angekommen.typ == umschlag.ANFRAGE and angekommen.nonce == bytes(12))
    except Exception as e:
        R.fehlschlag("Anfrage wird geprüft und liefert Alices Karte", e)
    gefaelscht = Umschlag.entpacken(anfrage.packen())
    gefaelscht.von = carol.id
    R.pruefe("Anfrage mit gefälschtem Absender wird abgelehnt",
             wirft(umschlag.karten_umschlag_lesen, gefaelscht))
    weitergeleitet = Umschlag.entpacken(anfrage.packen())
    weitergeleitet.an = carol.id
    R.pruefe("Umadressierte Anfrage wird abgelehnt",
             wirft(umschlag.karten_umschlag_lesen, weitergeleitet))
    # Carol legt Alices echte Karte in einen eigenen, von Carol unterschriebenen Umschlag.
    unterschoben = Umschlag(umschlag.ANFRAGE, alice.id, bob.id, os.urandom(16), 1, bytes(12), b"")
    unterschoben.body = e2e.signiert_einpacken(carol, identitaet.kanonisch(k_alice),
                                               kontext=unterschoben.kopf())
    R.pruefe("Anfrage mit fremder Unterschrift unter echter Karte wird abgelehnt",
             wirft(umschlag.karten_umschlag_lesen, unterschoben))
    R.pruefe("Fremde Karte kann man nicht als eigene Anfrage schicken",
             wirft(umschlag.anfrage_bauen, carol, k_alice, bob.id))
    annahme = umschlag.anfrage_bauen(bob, k_bob, alice.id, umschlag.ANNAHME)
    karte_von_bob = umschlag.karten_umschlag_lesen(Umschlag.entpacken(annahme.packen()))
    R.pruefe("Handschlag: Anfrage + Annahme ergeben auf beiden Seiten denselben Schlüssel",
             e2e.paar_schluessel(alice, karte_von_bob)
             == e2e.paar_schluessel(bob, umschlag.karten_umschlag_lesen(angekommen)))
    R.pruefe("DM kann nicht als Karten-Umschlag gelesen werden",
             wirft(umschlag.karten_umschlag_lesen, dm))

    # --- Sicherheitsnummer -----------------------------------------------
    print("\n--- Sicherheitsnummer ---")
    nr_ab = e2e.sicherheitsnummer(k_alice, k_bob)
    nr_ba = e2e.sicherheitsnummer(k_bob, k_alice)
    bloecke = nr_ab.split(" ")
    R.pruefe("Sicherheitsnummer ist auf beiden Seiten gleich", nr_ab == nr_ba, nr_ab)
    R.pruefe("Sicherheitsnummer: 60 Ziffern in 12 Fünferblöcken",
             len(bloecke) == 12 and all(len(b) == 5 and b.isdigit() for b in bloecke))
    R.pruefe("Sicherheitsnummer ändert sich mit einem anderen Schlüssel",
             e2e.sicherheitsnummer(k_alice, k_carol) != nr_ab)
    neuer_name = identitaet.karte_bauen(alice, "Ali", "#123456", ts=5)
    R.pruefe("Sicherheitsnummer hängt nicht am Namen",
             e2e.sicherheitsnummer(neuer_name, k_bob) == nr_ab)

    # --- Kanäle -----------------------------------------------------------
    print("\n--- Community-Kanäle ---")
    ckey = communities.community_schluessel_neu()
    k1, k2 = communities.kanal_id_neu(), communities.kanal_id_neu()
    s1, s2 = e2e.kanal_schluessel(ckey, k1), e2e.kanal_schluessel(ckey, k2)
    R.pruefe("Kanalschlüssel unterscheiden sich je Kanal",
             s1 != s2 and s1 != ckey and len(s1) == 32)
    R.pruefe("Kanalschlüssel sind reproduzierbar", e2e.kanal_schluessel(ckey, k1) == s1)
    R.pruefe("Kanal-ID und Community-ID haben die richtige Form",
             communities.ist_kanal_id(k1) and communities.ist_community_id(gid)
             and len(gid) == 10 and not communities.ist_community_id(alice.id))

    eingepackt = e2e.signiert_einpacken(alice, b"Inhalt")
    R.pruefe("Unterschriebener Inhalt lässt sich prüfen",
             e2e.signiert_auspacken(eingepackt, alice.ed_pub) == b"Inhalt")
    R.pruefe("Unterschrift mit falschem Schlüssel / verändertem Inhalt wird abgelehnt",
             wirft(e2e.signiert_auspacken, eingepackt, bob.ed_pub)
             and wirft(e2e.signiert_auspacken, b"inhalt" + eingepackt[6:], alice.ed_pub)
             and wirft(e2e.signiert_auspacken, eingepackt, alice.ed_pub, b"kontext"))

    knachricht = {"art": "text", "text": "Hallo Kanal", "kanal": k1}
    ku = umschlag.kanal_bauen(alice, s1, alice.id, gid, knachricht)
    ku_rein = Umschlag.entpacken(ku.packen())
    try:
        inner, karte = umschlag.kanal_lesen(ku_rein, s1, alice.ed_pub)
        R.pruefe("Kanalnachricht mit bekanntem Absender wird angenommen",
                 inner == knachricht and karte is None)
        inner, _ = umschlag.kanal_lesen(ku_rein, {k2: s2, k1: s1}, alice.ed_pub)
        R.pruefe("Passender Kanalschlüssel wird aus mehreren gefunden", inner["kanal"] == k1)
    except Exception as e:
        R.fehlschlag("Kanalnachricht mit bekanntem Absender wird angenommen", e)
    R.pruefe("Kanalnachricht mit falschem Kanalschlüssel wird abgelehnt",
             wirft(umschlag.kanal_lesen, ku_rein, s2, alice.ed_pub))
    R.pruefe("Unbekannter Absender ohne Karte wird abgelehnt",
             wirft(umschlag.kanal_lesen, ku_rein, s1))
    try:
        umschlag.kanal_lesen(ku_rein, s1)
    except ValueError as e:
        R.pruefe("Fehlertext lautet 'unbekannter Absender'", str(e) == "unbekannter Absender")
    R.pruefe("Kanalnachricht, geprüft gegen den falschen Absender, wird abgelehnt",
             wirft(umschlag.kanal_lesen, ku_rein, s1, bob.ed_pub))

    mit_karte = umschlag.kanal_bauen(alice, s1, alice.id, gid, dict(knachricht, karte=k_alice))
    try:
        inner, karte = umschlag.kanal_lesen(Umschlag.entpacken(mit_karte.packen()), s1)
        R.pruefe("Unbekannter Absender mit beigelegter Karte wird angenommen",
                 inner["text"] == "Hallo Kanal" and karte == k_alice)
    except Exception as e:
        R.fehlschlag("Unbekannter Absender mit beigelegter Karte wird angenommen", e)

    # Carol (Mitglied, hat den Kanalschlüssel) fälscht eine Nachricht "von Alice"
    # mit Alices Karte, unterschreibt aber selbst.
    faelschung = Umschlag(umschlag.NACHRICHT, alice.id, gid, os.urandom(16), 1, os.urandom(12), b"")
    kopf = faelschung.kopf()
    signiert = e2e.signiert_einpacken(
        carol, identitaet.kanonisch(dict(knachricht, karte=k_alice)), kontext=kopf)
    _, faelschung.body = e2e.nachricht_verschluesseln(s1, faelschung.msg_id, kopf, signiert,
                                                      nonce=faelschung.nonce)
    R.pruefe("Gefälschte Kanalnachricht (falsche Unterschrift) wird abgelehnt",
             wirft(umschlag.kanal_lesen, faelschung, s1)
             and wirft(umschlag.kanal_lesen, faelschung, s1, alice.ed_pub))
    # Carol legt ihre eigene Karte bei, behauptet aber im Kopf, Alice zu sein.
    falsche_karte = umschlag.kanal_bauen(carol, s1, carol.id, gid, dict(knachricht, karte=k_carol))
    falsche_karte.von = alice.id
    R.pruefe("Beigelegte Karte eines anderen als des Absenders wird abgelehnt",
             wirft(umschlag.kanal_lesen, falsche_karte, s1))
    # Carol nimmt Alices echte Nachricht und wirft sie unter neuer ID erneut ein.
    neu_eingeworfen = Umschlag.entpacken(mit_karte.packen())
    alt_klar = umschlag.verschluesselt_lesen(neu_eingeworfen, s1)
    neu_eingeworfen.msg_id = os.urandom(16)
    neu_eingeworfen.nonce = os.urandom(12)
    _, neu_eingeworfen.body = e2e.nachricht_verschluesseln(
        s1, neu_eingeworfen.msg_id, neu_eingeworfen.kopf(), alt_klar, nonce=neu_eingeworfen.nonce)
    R.pruefe("Echte Kanalnachricht unter neuer ID erneut eingeworfen wird abgelehnt",
             wirft(umschlag.kanal_lesen, neu_eingeworfen, s1))
    # Mit dem Schlüssel von Kanal 1 verschlüsselt, behauptet aber Kanal 2.
    quer = umschlag.kanal_bauen(alice, s1, alice.id, gid, dict(knachricht, kanal=k2))
    R.pruefe("Nachricht, die einen anderen Kanal nennt als ihr Schlüssel, wird abgelehnt",
             wirft(umschlag.kanal_lesen, quer, {k1: s1, k2: s2}, alice.ed_pub))
    R.pruefe("Kanalnachricht ohne Kanalangabe wird nicht gebaut",
             wirft(umschlag.kanal_bauen, alice, s1, alice.id, gid, {"art": "text"}))

    # --- Einladung ---------------------------------------------------------
    print("\n--- Einladungscode ---")
    einladung = communities.einladung_bauen(gid, ckey, alice.id)
    R.pruefe("Einladungscode beginnt mit VP4G2- und ist eine Zeile",
             einladung.startswith("VP4G2-") and len(einladung) == 69 and "\n" not in einladung,
             einladung)
    try:
        R.pruefe("Einladungscode übersteht Hin und Zurück",
                 communities.einladung_lesen(einladung) == (gid, ckey, alice.id))
    except Exception as e:
        R.fehlschlag("Einladungscode übersteht Hin und Zurück", e)
    R.pruefe("Kaputte Einladungscodes werden abgelehnt",
             wirft(communities.einladung_lesen, einladung[:30])
             and wirft(communities.einladung_lesen, einladung + "AAAA")
             and wirft(communities.einladung_lesen, "VP4G2-$$$")
             and wirft(communities.einladung_lesen, "hallo ich bin ein code")
             and wirft(communities.einladung_lesen, "")
             and wirft(communities.einladung_lesen, "VP4G1-" + einladung[6:]))
    R.pruefe("Einladung mit ungültiger Kennung wird nicht gebaut",
             wirft(communities.einladung_bauen, "G-12", ckey, alice.id)
             and wirft(communities.einladung_bauen, gid, ckey[:16], alice.id))

    # --- Manifest ----------------------------------------------------------
    print("\n--- Community-Manifest ---")
    kanaele = [{"id": k1, "name": "allgemein", "position": 0, "nur_admins": False},
               {"id": k2, "name": "ankündigungen", "position": 1, "nur_admins": True}]
    karten = {alice.id: k_alice, bob.id: k_bob, carol.id: k_carol}
    m1 = communities.manifest_bauen(alice, gid, "Schulhof", "🎮", kanaele, [bob.id], 1)
    try:
        ok = communities.manifest_pruefen(m1, karten, alice.id, 0, [], community_id=gid)
        R.pruefe("Vom Besitzer unterschriebenes Manifest wird angenommen",
                 ok["admins"] == [bob.id] and ok["version"] == 1)
    except Exception as e:
        R.fehlschlag("Vom Besitzer unterschriebenes Manifest wird angenommen", e)
    m_carol = communities.manifest_bauen(carol, gid, "Gekapert", "💀", kanaele, [carol.id], 2)
    R.pruefe("Manifest von einem Nicht-Admin wird abgelehnt",
             wirft(communities.manifest_pruefen, m_carol, karten, alice.id, 1, [bob.id]))
    R.pruefe("Manifest mit gleicher oder kleinerer Version wird abgelehnt",
             wirft(communities.manifest_pruefen, m1, karten, alice.id, 1, [bob.id])
             and wirft(communities.manifest_pruefen, m1, karten, alice.id, 5, [bob.id]))
    m_bob = communities.manifest_bauen(bob, gid, "Schulhof", "🎮",
                                       kanaele + [{"id": communities.kanal_id_neu(),
                                                   "name": "memes", "position": 2,
                                                   "nur_admins": False}], [bob.id], 2)
    try:
        ok = communities.manifest_pruefen(m_bob, karten, alice.id, 1, [bob.id], community_id=gid)
        R.pruefe("Von einem Admin unterschriebenes Manifest wird angenommen",
                 len(ok["kanaele"]) == 3 and ok["von"] == bob.id)
    except Exception as e:
        R.fehlschlag("Von einem Admin unterschriebenes Manifest wird angenommen", e)
    m_bob_admins = communities.manifest_bauen(bob, gid, "Schulhof", "🎮", kanaele,
                                              [bob.id, carol.id], 2)
    R.pruefe("Ein Admin darf die Admin-Liste nicht ändern",
             wirft(communities.manifest_pruefen, m_bob_admins, karten, alice.id, 1, [bob.id]))
    R.pruefe("Ein Admin, der im neuen Manifest steht, aber nicht im bisherigen, zählt nicht",
             wirft(communities.manifest_pruefen, m_carol, karten, alice.id, 1, [bob.id]))
    veraendert = _kopie(m1)
    veraendert["name"] = "Anders"
    R.pruefe("Verändertes Manifest wird abgelehnt",
             wirft(communities.manifest_pruefen, veraendert, karten, alice.id, 0, []))
    R.pruefe("Manifest einer anderen Community wird abgelehnt",
             wirft(communities.manifest_pruefen, m1, karten, alice.id, 0, [],
                   communities.community_id_neu()))
    R.pruefe("Manifest ohne bekannte Karte des Unterzeichners wird abgelehnt",
             wirft(communities.manifest_pruefen, m1, {}, alice.id, 0, []))
    doppelt = [kanaele[0], dict(kanaele[0])]
    R.pruefe("Manifest mit doppelter Kanal-ID wird nicht gebaut",
             wirft(communities.manifest_bauen, alice, gid, "X", "🎮", doppelt, [], 1))
