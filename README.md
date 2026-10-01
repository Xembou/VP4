<div align="center">

<img src="vp4.png" width="120" alt="VP4">

# VP4

**Verschlüsselt chatten mit Freunden – plus ein Werkzeugkasten zum Verschlüsseln.**

Kein Konto, keine Telefonnummer, kein eigener Server. Im selben WLAN geht der
Chat direkt von PC zu PC, sonst über einen Discord-Kanal – Ende-zu-Ende
verschlüsselt, Discord sieht nur Geheimtext.

</div>

---

## Herunterladen

1. Auf **[Releases](../../releases)** gehen und `VP4.exe` herunterladen.
2. Doppelklicken. Du brauchst kein Python und musst nichts installieren.

> **Windows warnt beim ersten Start.** „Der Computer wurde durch Windows
> geschützt" erscheint, weil das Programm nicht signiert ist – eine Signatur
> kostet mehrere hundert Euro im Jahr, und das hier ist ein Hobby-Projekt.
> Klick auf **Weitere Informationen** → **Trotzdem ausführen**.
> Wer dem nicht traut: Der Quelltext liegt hier offen, `python bauen.py`
> baut dieselbe `.exe` selbst.

VP4 braucht die **Microsoft Edge WebView2-Laufzeit**. Auf Windows 11 ist sie
eingebaut, auf fast allen Windows-10-PCs auch. Fehlt sie, sagt VP4 das beim
Start und nennt die Seite, auf der es sie kostenlos gibt.

---

## Der erste Start

1. **Name und Farbe** – so sehen dich deine Freunde.
2. **Master-Passwort** – schützt deinen Tresor (Schlüssel, Kontakte, Chats).
   **Es gibt keine Wiederherstellung.** Vergisst du es, ist alles auf diesem
   PC weg. Auf Wunsch merkt sich Windows das Passwort (an dein
   Windows-Konto gebunden), dann musst du es nicht bei jedem Start tippen.
3. **Deine ID** – sieht so aus: `7AC5E-HTN4Q`. Die gibst du Freunden.

Hast du vorher VP4 4 benutzt und liegt `vp4_daten` daneben, übernimmt VP4 5
mit deinem alten Passwort den Schlüsselbund und die Einstellungen.
Kontakte und Gruppen lassen sich nicht übernehmen – der Chat ist komplett
neu verschlüsselt. VP4 zeigt dir eine Liste, wen du neu hinzufügen solltest.

---

## Chatten

### Freunde

**＋ → Freund hinzufügen**, ID eintippen (Groß/klein und der Strich sind
egal), **Anfrage senden**. Dein Freund sieht unter **Anfragen**, dass du
schreiben möchtest, und nimmt an. Ab dann tauschen eure Programme die
Schlüssel von selbst aus – niemand muss etwas abtippen.

Alles, was man von einem Messenger erwartet: Antworten, Reaktionen
(Doppelklick = ❤️), Bearbeiten (↑ im leeren Feld), Löschen für dich oder
für alle, Bilder, Videos, Dateien bis 100 MB, Sprachnachrichten, „schreibt …",
Zugestellt/Gelesen (abschaltbar), Anheften, Stummschalten, Suche.

### Gruppen und Communities

- **Gruppe** – ein Chat für ein paar Leute.
- **Community** – wie ein eigener Discord-Server: mehrere Kanäle
  (`#allgemein`, `#hausaufgaben` …), Admins, Ankündigungskanäle, in denen nur
  Admins schreiben.

Beides funktioniert mit einem **Einladungscode** (`VP4G2-…`). Wer ihn hat, ist
dabei. Das macht es einfach und hat einen Preis, den du kennen solltest:
Ein weitergegebener Code lässt sich nicht zurückholen, und wer beitritt, kann
auch Älteres lesen, das noch im Kanal steht. Soll jemand raus, erstellt der
Besitzer **einen neuen Code** – deine Kontakte unter den Mitgliedern bekommen
den neuen Schlüssel automatisch, alle anderen brauchen den neuen Code.

Gruppen und Communities laufen immer über Discord, auch im selben WLAN.

---

## Wie gut sind die Nachrichten geschützt?

**Geschützt** ist der Inhalt jeder Nachricht, jedes Bilds, jeder Datei:
Ende-zu-Ende mit AES-256-GCM. Bei zwei Personen kommt der Schlüssel aus einem
X25519-Schlüsseltausch, den nur ihr zwei ausrechnen könnt; jede Nachricht
bekommt davon ihren eigenen Schlüssel. In Gruppen ist zusätzlich jede
Nachricht vom Absender unterschrieben (Ed25519) – einen Namen kann man
deshalb nicht fälschen.

Deine **ID wird aus deinen Schlüsseln berechnet.** Wer sich als dein Freund
ausgeben will, bräuchte eine andere ID. Zur letzten Sicherheit gibt es pro
Kontakt eine **Sicherheitsnummer** (Schild-Symbol über dem Chat): Vergleicht
sie einmal am Telefon oder nebeneinander. Stimmt sie, liest niemand mit.

**Nicht geschützt:**

- **Wer wann wem schreibt.** Die IDs stehen offen im Discord-Kanal, und
  Discord sieht, wie viel und wann.
- **Alte Nachrichten nach einem Gerätediebstahl.** VP4 hat noch keine
  „Forward Secrecy" wie Signal: Wer später deinen entsperrten PC hat, kann
  auch ältere Nachrichten öffnen.
- **Dein PC selbst.** Ist VP4 entsperrt, liest jeder mit, der davorsitzt.

Und ehrlich: VP4 ist ein privates Hobby-Projekt, **kein geprüftes
Sicherheitsprodukt**. Die Kryptografie stammt aus geprüften Bibliotheken
(`cryptography`, `PyNaCl`, `age`), das Programm drumherum hat niemand fachlich
geprüft.

---

## Werkzeuge

Verschlüsseln ganz ohne Chat:

| Verfahren | Wofür |
|---|---|
| **AES-256-GCM** | Der Standard. Wenn du dich nicht entscheiden willst: nimm das. |
| **ChaCha20-Poly1305 / XChaCha20** | Gleichwertig zu AES; XChaCha mit 24-Byte-Nonce. |
| **AES-256-GCM-SIV** | Verzeiht ein versehentlich doppelt benutztes Nonce. |
| **Passwort (AES-256)** | Ein Passwort statt eines langen Schlüssels. |
| **age (Passwort / Schlüsselpaar)** | Das offene age-Format – auch mit dem offiziellen Programm `age` lesbar. |
| **RSA-2048** | Zwei Schlüssel, nur für kurze Texte. |
| **Post-Quanten (Hybrid)** | ML-KEM-768 + X25519 (HPKE): auch gegen künftige Quantencomputer gedacht, heute mindestens so stark wie X25519. |

**Nur zum Spielen – in Sekunden zu knacken:** Caesar · Vigenère · Playfair ·
Rail-Fence · ROT13 · Atbash · Morse · XOR · Base64.

Außerdem: **Dateien und ganze Ordner** als `.vp4` oder `.age` (beliebig
groß, mit Fortschritt und Abbrechen), **Schlüsselbund**, **Signieren &
Prüfen** (Ed25519), **Prüfsummen** und **Obsidian** – Schlüssel in deinen
Vault schreiben, wahlweise im Klartext oder **verschlüsselt mit age**.
VP4 löscht von sich aus nie ein Original.

---

## Der Discord-Weg – und sein Haken

Damit jeder VP4 herunterladen und sofort schreiben kann, steckt in der
`VP4.exe` von GitHub ein **eingebauter Discord-Bot-Zugang**. Das ist eine
bewusste Entscheidung, und sie hat einen Haken:

- Den Token kann jeder, der will, aus der `.exe` herausholen.
- Deine **Nachrichten bleiben trotzdem geheim** – der Bot sieht nur Geheimtext.
- Aber jemand könnte den Bot **stören**: den Kanal fluten, Zeilen löschen,
  oder das gemeinsame Kontingent von 1000 Anmeldungen pro Tag aufbrauchen.
  Dann setzt Discord den Token zurück, und der Discord-Weg geht bei allen
  nicht mehr, bis es eine neue Version gibt. Das WLAN geht weiter.

Wenn das passiert, zeigt VP4 „Der eingebaute Discord-Zugang wurde gesperrt"
und weist auf die neue Version hin, sobald es sie gibt.

Wer unabhängig sein will, trägt unter **Einstellungen → Discord** einen
eigenen Bot ein (siehe unten). Der geht dem eingebauten immer vor.

### Für den, der den Bot betreibt

1. Eigenen Discord-Server nur für VP4 anlegen, darin 1–3 Textkanäle.
2. Im [Developer Portal](https://discord.com/developers/applications) eine
   Anwendung mit Bot anlegen, **Message Content Intent** einschalten.
3. Den Bot einladen – **nur** mit: Kanäle ansehen, Nachrichten senden,
   Nachrichtenverlauf lesen, Dateien anhängen. Keine Admin-Rechte, kein
   „Nachrichten verwalten", in keinem anderen Server.
4. Im GitHub-Repo unter *Settings → Secrets and variables → Actions*:
   `DISCORD_BOT_TOKEN` und `DISCORD_KANAL_IDS` (mehrere mit Komma).
   Mehrere Kanäle verteilen Discords Limit von rund fünf Nachrichten in fünf
   Sekunden pro Kanal – das teilen sich alle VP4-Nutzer.
5. `git tag v5.0.1 && git push origin v5.0.1` – GitHub baut und veröffentlicht.

**Wenn Discord den Token sperrt:** Im Developer Portal *Reset Token*, das neue
Secret eintragen, neuen Tag pushen. VP4 meldet die neue Version beim nächsten
Start (eine einzige Anfrage an GitHubs öffentliche API, ohne etwas über dich
mitzuschicken; heruntergeladen wird nie von selbst).

---

## Wenn etwas nicht geht

| Problem | Was hilft |
|---|---|
| Freund taucht im WLAN nicht auf | Gäste-WLANs trennen Geräte. Windows-Firewall: VP4 erlauben (sie fragt beim ersten Start). |
| „Keine Verbindung" unten links | Kein Internet, oder der Discord-Zugang ist gesperrt – siehe oben. |
| Nachricht hat ein rotes „Nicht gesendet" | Draufklicken sendet sie nochmal. |
| Anfrage kommt nicht an | Beide brauchen VP4 5. IDs aus VP4 4 (`ABCD-1234`) gelten nicht mehr. |
| „Schlüssel geändert" | Jemand hat sich mit anderen Schlüsseln unter dieser ID gemeldet – VP4 hat es abgelehnt. Sicherheitsnummer vergleichen. |
| Passwort vergessen | Keine Wiederherstellung. *Einstellungen → Daten → Alles löschen* und neu einrichten. |

---

## Selbst bauen

```bash
git clone https://github.com/Xembou/VP4.git
cd VP4
pip install -r requirements.txt pyinstaller

python VP4.py              # Programmfenster
python VP4.py --browser    # dieselbe App im Browser (zum Entwickeln)
python test_vp4.py         # Selbsttest
python bauen.py            # eigene VP4.exe -> dist/VP4.exe
```

Für die Oberflächen-Prüfung mit Screenshots zusätzlich `pip install playwright`
(und einmal `playwright install chromium`), dann `python tests/ui_pruefung.py`.

### Aufbau

| Ordner / Datei | Inhalt |
|---|---|
| `VP4.py` | Einstieg: prüft Pakete, öffnet das Fenster |
| `api.py` | Was die Oberfläche von Python wollen darf |
| `dienst.py` | Tresor, Datenbank, Bote, Netz, Versand |
| `kern/` | Reine Logik: Verfahren, Tresor, Identität, Ende-zu-Ende, Nachrichten |
| `netz/` | WLAN, Discord, Wegwahl, Update-Prüfung |
| `ui/` | Die Oberfläche – HTML, CSS, JavaScript, ohne Build-Schritt |
| `tests/` | Prüfmodule; `test_vp4.py` führt alle aus |

Deine Daten liegen in `vp4_daten` neben dem Programm. Dieser Ordner gehört dir
allein und wird nie mit hochgeladen.

Mitgeliefert: [Lucide](https://lucide.dev) Icons (ISC), [Inter](https://rsms.me/inter/)
(SIL OFL 1.1), Emoji-Namen von [emojibase](https://emojibase.dev) (MIT).
