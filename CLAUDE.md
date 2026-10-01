# Auftrag: VP4 5 – verschlüsselter Messenger mit Werkzeugkasten

Diese Datei wird von Claude Code automatisch als Projekt-Kontext gelesen.
Lies sie zuerst.

## Wer das hier ist

Leon (16, Schüler) baut ein eigenständiges Windows-Desktop-Programm. Seit
VP4 5 (Oktober 2026) kann es **jeder** von GitHub Releases herunterladen und
sofort chatten.

## Was Leon will

1. **Ein Messenger**: Freunde, Gruppen, Communities mit Kanälen wie bei
   Discord, Bilder/Videos/Dateien/Sprachnachrichten, Antworten, Reaktionen,
   Bearbeiten, Löschen, Lesebestätigungen.
2. **Verschlüsselung als Extra**: der Werkzeugkasten aus VP4 4 (alle
   Verfahren, Dateien/Ordner, Schlüsselbund, Signaturen, Prüfsummen,
   Obsidian) plus XChaCha20, AES-GCM-SIV, age und Post-Quanten-Hybrid.
3. **Kein Claude / keine KI zur Laufzeit** – und kein eigener Server.
4. **Läuft über Discord**, im selben WLAN direkt.
5. **Optik ist ein eigenes Qualitätsmerkmal.** Vorbild: macOS 26 „Tahoe"
   mit Liquid Glass. Die Tkinter- und später die CustomTkinter-Fassung hat
   Leon als zu simpel abgelehnt.
6. **Master-Passwort ohne Wiederherstellung** – Windows darf es sich auf
   Wunsch merken (DPAPI), „bei jedem Start fragen" ist einschaltbar.

Der vollständige Bauplan mit allen Entscheidungen steht in
`docs/REWORK_PROMPT.md` (Interview vom 01.10.2026).

## Aufbau

    VP4.py              Einstieg: Pakete prüfen, pywebview-Fenster öffnen (--browser: im Browser)
    api.py              VP4Api - alles, was die Oberfläche von Python wollen darf
    dienst.py           VP4Dienst - Tresor, Datenbank, Bote, Netz, Versand-Thread, Zeitplaner
    ereignisse.py       Warteschlange zur Oberfläche + Zeitplaner (Nachfolger von _spaeter)
    medien_server.py    Medien für die Seite, nur 127.0.0.1, zufällige Adressen
    dev_server.py       dieselbe VP4Api über HTTP (Token im #-Teil der Adresse)
    version.py          VERSION und REPO - die eine Stelle
    discord_konfig.py   leere Platzhalter; der Workflow setzt Token und Kanal-IDs ein
    kern/
      krypto.py         alle Verfahren (VERFAHREN-Liste), Signaturen, Prüfsummen, KDF-Kopf
      krypto_neu.py     XChaCha20, AES-GCM-SIV, PQ-Hybrid (HPKE), age
      dateien.py        .vp4-Container (gestreamt)
      speicher.py       Pfade, JSON, alter KeyStore (VP4K2/K3), Obsidian, Passwortstärke
      tresor.py         Tresor VP4K4: Datenschlüssel, Argon2id-Hülle, DPAPI-Hülle
      datenbank.py      SQLite, Inhalte verschlüsselt, secure_delete
      identitaet.py     Ed25519 + X25519, ID aus den Schlüsseln, Visitenkarte, Freundescode
      e2e.py            Paarschlüssel, Nachrichtenschlüssel, Kanalschlüssel, Sicherheitsnummer
      umschlag.py       binäres Umschlagformat (Kopf = AAD)
      communities.py    Einladungscodes, signiertes Manifest, Standardkanal
      nachrichten.py    der Bote: alles, was mit Nachrichten passiert (ohne Threads, ohne Netz)
      migration.py      einmalige Übernahme aus VP4 4.x
    netz/
      lan.py            UDP-Suche + TCP, trägt Umschläge (Ports 41230/41231)
      discord_netz.py   VP4D2-Zeilen/Anhänge, Nachholen, Kanalverteilung, Fehlertexte
      vermittler.py     wählt WLAN oder Discord
      update.py         eine Anfrage an GitHubs Releases-API pro Start
    ui/                 HTML/CSS/JS ohne Build-Schritt und ohne Internet
      css/tokens.css    ALLE Gestaltungswerte (Farben, Radien, Typo, Bewegung, Tapeten)
      js/dom.js         h()-Baukasten - es gibt kein innerHTML
      js/bruecke.js     pywebview / HTTP / Demo, Ereignisse per Abfrage alle 200 ms
      js/demo.js        erfundene Daten für ?demo=1 (nur Screenshots)
    tests/              Prüfmodule (pruefen(R, hilfen)); test_vp4.py führt alle aus
      ui_pruefung.py    Chromium/Playwright: jede Ansicht + echter Zwei-Personen-Durchlauf
    werkzeuge/icons_bauen.py   baut ui/icons/sprite.svg aus Lucide

Abhängigkeiten: `requirements.txt` (pywebview, cryptography ≥ 50, argon2-cffi,
PyNaCl, pyrage, discord.py, pillow). Zum Testen der Oberfläche zusätzlich
`playwright`. CustomTkinter ist raus.

## Regeln für die Arbeit hier

1. **Nach jeder Änderung `python test_vp4.py` laufen lassen.** Wenn du etwas
   reparierst, ergänze vorher eine Prüfung, die den Fehler zeigt.
2. **Bei Oberflächenarbeit wirklich hinsehen.** `python tests/ui_pruefung.py`
   macht Screenshots nach `tests/screenshots/` – öffne sie. Die Prüfung meldet
   überlappende Texte und Konsolenfehler, aber nicht, ob es gut aussieht.
3. **Keine übertriebenen Sicherheitsversprechen.** Was geschützt ist und was
   nicht, steht in der Oberfläche (Sicherheitsblatt, Einstellungen →
   Sicherheit) und in der README. Klassische Verfahren bleiben „Nur zum
   Spielen".
4. **Größere Design-Entscheidungen mit Leon klären, nicht raten.**
5. **Keine Werte außerhalb von `ui/css/tokens.css`.** Gefüllte Elemente
   setzen ihre Textfarbe ausdrücklich (`--akzent-text`) – bei Mint und Orange
   ist sie dunkel.
6. **Kein innerHTML, kein Inline-Skript.** Alles aus Nachrichten geht über
   `h()`/`textContent`. Ein Test sucht danach, und `ui_pruefung.py` schickt
   `<img onerror>` als Nachricht.

## Entscheidungen vom 01.10.2026 (nicht neu verhandeln)

- **pywebview + HTML/CSS** statt CustomTkinter (echtes Glas mit
  `backdrop-filter`) und statt Qt (zu große `.exe`).
- **Der Bot-Token steckt in der öffentlichen `.exe`.** Leon kennt das
  Risiko: Der Token ist herauszuholen; Nachrichten bleiben trotzdem geheim,
  aber jemand kann den Bot stören oder das gemeinsame Kontingent von 1000
  Anmeldungen/Tag verbrennen – dann setzt Discord den Token zurück und alle
  brauchen eine neue Version. Deshalb: `DISCORD_BOT_TOKEN` und
  `DISCORD_KANAL_IDS` gehören jetzt **bewusst** in die Repo-Secrets, der
  Workflow setzt sie ein, `netz/update.py` meldet neue Versionen. Die alte
  Regel „die Secrets dürfen nicht hinterlegt werden" gilt nicht mehr.
- **Der eingebaute Gruppenschlüssel ist weg.** Statt eines gemeinsamen
  Schlüssels für alle gibt es einen X25519-Handschlag pro Freund. Ein Test
  prüft, dass `GRUPPEN_SCHLUESSEL` nirgends mehr auftaucht.
- **Daten werden übernommen, das alte Chat-Protokoll nicht.** IDs aus 4.x
  (`ABCD-1234`) gelten nicht mehr.

## Invarianten – beim Ändern nicht verlieren

### Identität und Ende-zu-Ende
1. **Die ID ist aus den Schlüsseln berechnet** (SHA-256 über Ed25519 + X25519,
   50 Bit, `XXXXX-XXXXX`). Eine Karte, deren Schlüssel nicht zur ID passen,
   wird abgelehnt. Das war die alte Schwäche „IDs werden nur behauptet".
2. **Erste Karte wird festgehalten.** Andere Schlüssel unter bekannter ID:
   ablehnen, warnen (`schluessel_neu`), nie still übernehmen.
3. **DMs** nur von Kontakten mit Status `ok`; Anfragen sind die einzige
   Ausnahme. **Bearbeiten/Löschen** nur durch den Absender der Nachricht.
4. **Communities**: jede Nachricht ist signiert und enthält die Karte des
   Absenders; Manifeste nur vom Besitzer oder einem Admin des aktuellen
   Manifests, Version streng steigend, nur der Besitzer ändert die Admins.
   Ankündigungskanäle: Nicht-Admins werden beim Senden UND beim Empfangen
   abgewiesen. Ein selbst vergebener Spitzname geht vor; Fremde stehen mit
   ID-Kürzel da („Carl (ABCDE)").
5. **Der Standardkanal folgt aus der Community-ID**
   (`communities.standard_kanal_id`). Darüber meldet sich ein Neuer
   („beitritt"), und jedes Mitglied darf das (vom Besitzer signierte)
   Manifest weiterreichen – höchstens einmal pro Minute.
6. **Kein Forward Secrecy** – so steht es auch in der Oberfläche. Ein Double
   Ratchet wäre die nächste große Stufe; mit Leon besprechen.

### Discord
1. **Über Discord geht ausschließlich Verschlüsseltes.** Karten-Umschläge
   (Anfrage, Annahme) sind signiert, nicht verschlüsselt – sie enthalten nur
   öffentliche Schlüssel und den Namen.
2. **Zeilen ≤ 1900 Zeichen**, Zusammensetzen in beliebiger Reihenfolge,
   Umschläge > 6 KiB als Anhang (≤ 9,5 MiB), Dateien in Teilen à 8 MiB.
3. **„schreibt …" nie über Discord**, Lesebestätigungen gesammelt (alle 3 s):
   Alle teilen sich EIN Limit, weil alle denselben Bot benutzen.
4. **Mehrere Kanäle** (`KANAL_IDS`) verteilen das Limit; der Kanal folgt aus
   `sha256(an) % n`.
5. **`DiscordNetz.start()` tut im `VP4_TESTMODUS` nichts.**
6. **Nachholen nach dem Start** ab dem gespeicherten Stand (höchstens 7 Tage),
   doppelte Zustellung ist harmlos (Datenbank-ID = msg_id).

### Speicher und Formate
1. **Selbstbeschreibende Köpfe** (`Marke | KDF-Kennung | Einstellungen | Salt
   | Nonce`), Kopf als AAD. Marken: `VP4K4` (Tresor), `VP4K3`/`VP4K2` (alter
   Schlüsselspeicher, nur lesen), `VP4P2`/`VP4P1` (Passwort-Text), `VP4F1`
   (.vp4-Container), `VP4X1`, `VP4S1`, `VP4Q1|` (neue Verfahren), `VP4N`
   (Umschlag), `VP4D2|` (Discord-Zeile), `VP4C1-` (Freundescode), `VP4G2-`
   (Einladung). **Die zwei Alt-Blobs in test_vp4.py nie anfassen.**
2. **KDF-Grenzen vor dem Ableiten** (`krypto.KDF_GRENZEN`): Ein gekipptes Bit
   im Kopf machte aus 64 MiB Argon2 16 GiB – das Siegel wird ja erst danach
   geprüft. Argon2id: 64 MiB, 3 Durchgänge, p=1 (Test nagelt das fest).
3. **Tresor**: zufälliger Datenschlüssel, zweimal eingepackt (Argon2id +
   optional DPAPI). Passwortwechsel packt nur neu ein. **DPAPI ist auf echtem
   Windows noch nicht ausprobiert** – getestet ist nur die Attrappe.
4. **Datenbank**: Inhalte, Spitznamen, Vorschauen verschlüsselt;
   `secure_delete` + WAL-Checkpoint, damit Gelöschtes wirklich weg ist.
   Anhänge liegen unter Zufallsnamen in `vp4_daten/medien/`.
5. **.vp4-Container**: Nonce = Basis + Blocknummer, AAD mit Blocknummer und
   Letzter-Block-Kennzeichen, Dateiname im verschlüsselten Kopfsatz.

### Oberfläche und Brücke
1. **pywebview reicht auch öffentliche Attribute an JavaScript durch** (und
   deren Methoden). In `VP4Api` ist deshalb alles Interne `_privat`; ein Test
   wacht. Ein zweiter prüft, dass jede in `ui/js` aufgerufene Funktion in
   `VP4Api` existiert.
2. **Ereignisse nur per Abfrage** (`ereignisse_holen`), kein `evaluate_js` aus
   fremden Threads.
3. **Wiederkehrende Aufträge nur über den Zeitplaner** (`ereignisse.Zeitplaner`),
   nie eigene Threads mit `sleep`. Gelaufene einmalige Aufträge tragen sich
   selbst aus; nach Sperren/Entsperren sind genauso viele offen wie vorher –
   beides testet `tests/test_dienst.py` (Nachfolger der 4.x-Lehre um
   `_spaeter()`).
4. **Glas nur auf der schwebenden Ebene** (Seitenleiste, Werkzeugleiste,
   Eingabezeile, Menüs, Blätter, Hinweise), Inhalt massiv, nie Glas auf Glas.
   Die Tapete liegt in der Seite (WebView2 kann unter Windows keine
   durchsichtigen Fenster).
5. **CSP**: `script-src 'self' 'unsafe-eval'` – das `eval` braucht pywebview
   für seine Brücke (`new Function`). Kein Inline-Skript, kein Inline-Stil
   (Stile nur über CSSOM, `h()` macht das).
6. **Der Verlauf wird angehängt, nicht neu gezeichnet**; nur die Nachbarzeile
   bekommt neue Lauf-Klassen.

## Die fünf ernsten Fehler aus VP4 4 (Lehren, die bleiben)

1. **Vigenère zerstörte Umlaute** – `isalpha()` ist auch für `ä` wahr.
2. **Obsidian-Export schnitt lange Schlüssel ab** (RSA).
3. **Ein abgestürzter Empfangs-Thread machte einen Freund unerreichbar** –
   deshalb fängt `Bote.empfangen` alles ab, und der LAN-Leser übersteht
   kaputte Rahmen.
4. **Chat-Ports im dynamischen Windows-Bereich** – jetzt 41230/41231, ein
   Test prüft `< 49152`.
5. **Netzwerkfehler waren unsichtbar** – die `.exe` hat keine Konsole.
   Fehler gehen als Ereignis in die Oberfläche (Hinweis unten, Punkt in der
   Seitenleiste), Startfehler als Windows-MessageBox, alles andere in
   `vp4_daten/vp4.log`.

## Stand (Oktober 2026)

Gebaut und hier (Linux, Python 3.13) getestet: alle Module, 660+ Prüfungen,
Oberfläche in Chromium mit Screenshots, echter Zwei-Personen-Durchlauf durch
die Oberfläche. Das Repository liegt unter <https://github.com/Xembou/VP4>.

### Noch nicht auf echtem Windows ausprobiert – zuerst prüfen
- **Das pywebview-Fenster selbst** (WebView2, `create_window`, Datei-Dialoge,
  `pywebviewFullPath` beim Hineinziehen, Mikrofon-Freigabe für
  Sprachnachrichten, dunkle Titelleiste/Mica).
- **DPAPI** (`kern/tresor.DPAPI`) – nur die Attrappe ist getestet.
- **Die `.exe`** aus `bauen.py`/Workflow mit pywebview + pythonnet (Größe,
  ob `ui/` mitkommt).
- **Echter Discord** mit dem neuen Protokoll (Login, Nachholen, Anhänge,
  429, Kanalrechte) – der Sandbox-Proxy lässt discord.com nicht durch.
- **WLAN zwischen zwei echten PCs** und die Windows-Firewall (schon in 4.x
  offen).

### Ideen für später (mit Leon besprechen)
- Forward Secrecy (Double Ratchet) für DMs.
- Windows-Benachrichtigungen (Toast); jetzt: Taskleiste blinkt + Hinweis.
- Metadaten verstecken (Briefkasten-Kennung statt offener IDs im Kopf).
- Mitglieder entfernen ohne neuen Code (Schlüssel einzeln verteilen).
