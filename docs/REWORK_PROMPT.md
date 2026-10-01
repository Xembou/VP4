# VP4 5.0 – Bau-Prompt für den Komplett-Umbau

Entstanden am 01.10.2026 aus einem Interview mit Leon (Prompt-Skill). Der Prompt
selbst ist Englisch, weil Coding-KIs damit verlässlicher arbeiten. Kommentare
und alle sichtbaren Texte im Programm bleiben Deutsch.

**Entscheidungen aus dem Interview (nicht neu verhandeln):**

| Thema | Entscheidung |
|---|---|
| Look | macOS 26 „Tahoe“ / Liquid Glass |
| Technik | pywebview + HTML/CSS/JS, Python bleibt die Logik |
| Discord-Zugang | Bot-Token steckt in der öffentlichen `.exe` – Leon kennt das Risiko |
| Chat-Krypto | X25519-Handschlag + Sicherheitsnummer, Gruppenschlüssel fällt weg |
| Kanäle | wie bei Discord: Community mit mehreren Kanälen |
| Chat-Funktionen | alles: Antworten, Reaktionen, Bearbeiten/Löschen, Medien, Profil, Benachrichtigungen, Sprachnachrichten |
| Alte Funktionen | WLAN-Chat und Obsidian bleiben, der Rest nach Ermessen (alles bleibt, als „Werkzeuge“) |
| Master-Passwort | einmal festlegen, Windows (DPAPI) merkt es sich, „bei jedem Start fragen“ optional |
| Name | VP4 bleibt |
| Neue Verfahren | XChaCha20-Poly1305, AES-256-GCM-SIV, age-Format, ML-KEM-768+X25519 (Post-Quanten-Hybrid) |
| Altdaten | Daten übernehmen, altes Chat-Protokoll nicht |

```text
Rework the existing Python desktop app "VP4" (repo root = this folder) from an
encryption tool with a chat bolted on into an end-to-end encrypted MESSENGER
for friends — DMs, groups, Discord-style communities with channels — that
anyone can download from GitHub Releases as a single VP4.exe and start
chatting with, plus a "Werkzeuge" (tools) area that keeps and extends the
encryption features. New UI in the Apple macOS 26 "Tahoe" Liquid Glass style.

Read CLAUDE.md and README.md first. They document past bugs and invariants
that must survive this rework. Where this prompt and CLAUDE.md disagree, THIS
PROMPT wins (the decisions below were made with the owner, Leon, on
2026-10-01), and you must update CLAUDE.md accordingly at the end.

=====================================================================
0. HARD RULES (read twice)
=====================================================================
- NO AI / no Claude / no LLM calls at runtime. No paid APIs, no API keys
  except the Discord bot token, no accounts, no servers of our own. Runtime
  network = Discord gateway/REST, LAN (UDP/TCP), and one unauthenticated
  GitHub Releases API call for the update check. Nothing else.
- NO CDN, no remote fonts, no remote scripts. The UI must work fully
  offline. Every asset (icons, fonts, emoji data) is vendored into ui/.
- Never roll your own crypto primitives. Use `cryptography` (>=50, it ships
  ML-KEM, HPKE with MLKEM768_X25519, AES-GCM-SIV), `PyNaCl` (XChaCha20),
  `pyrage` (age), `argon2-cffi`. Self-written code may only COMBINE
  primitives in documented ways (HKDF, AEAD, signatures).
- Over Discord ONLY ciphertext travels. If no key is available for a
  recipient, refuse to send and say so. Never silently fall back to
  plaintext. (Invariant from CLAUDE.md.)
- Do not oversell security (CLAUDE.md rule 3). UI copy says what is
  protected AND what is not (metadata, no forward secrecy yet, hobby project,
  unaudited). Classical ciphers keep the label "Nur zum Spielen".
- Never put message text into innerHTML. All user/remote content is set via
  textContent or created DOM nodes. Strict CSP. An XSS in a chat message
  would otherwise reach the Python bridge, which holds the keys.
- Chat ports stay 41230/41231 (below the Windows dynamic range 49152+; a
  test enforces this).
- Recurring background work must be cancellable on shutdown (successor of
  the `_spaeter()` rule in CLAUDE.md): one scheduler, tracked jobs, a test
  that counts them.
- Two hard-coded legacy blobs in test_vp4.py (old VP4K2 keystore / VP4P1
  password ciphertext) must never be edited. Old formats VP4K2, VP4K3, VP4P1,
  VP4P2, VP4F1 must stay READABLE.
- After every step run `python test_vp4.py`. When fixing a bug, first add a
  check that shows the bug. UI work is verified with real screenshots
  (Playwright headless Chromium against the real UI), not by guessing.
- German: all comments, docstrings and visible UI strings in German.
  Identifiers may be German (match the existing code: `senden`, `schluessel`).
- Big design decisions not covered here: stop and ask Leon, don't guess.

=====================================================================
1. ENVIRONMENT
=====================================================================
- Target: Windows 10/11 x64, Python 3.13, VS Code. Dev/CI may be Linux.
- Libraries (pin in requirements.txt after verifying they install on
  Windows/py3.13; all have win_amd64/abi3 wheels as of 2026-10):
    pywebview>=6.2   (WinForms + Edge WebView2 backend, needs pythonnet)
    cryptography>=50
    argon2-cffi>=25
    PyNaCl>=1.6      (XChaCha20-Poly1305 only)
    pyrage>=1.4      (age format)
    discord.py>=2.7
    pillow           (icon, thumbnails)
    pyinstaller      (build only)
    playwright       (dev/test only, for screenshots; never in the exe)
  Optional, only if it installs cleanly and you verified it on Windows:
    a toast-notification package (e.g. windows-toasts). If not, fall back to
    in-app banners + taskbar flash (FlashWindowEx via ctypes).
- Remove customtkinter and tkinter from the app entirely.
- WebView2 runtime: preinstalled on Windows 11 and nearly all Windows 10.
  On start, if pywebview cannot create the EdgeChromium window, show a
  native MessageBox (ctypes) with the Evergreen Bootstrapper link instead of
  crashing silently (the exe has no console).
- exe target size: <= 40 MB. Build from a clean venv and add PyInstaller
  excludes for tkinter, PyQt*, PySide*, gi, numpy, matplotlib so nothing
  gets dragged in by accident. Print the size at the end of bauen.py.

=====================================================================
2. ARCHITECTURE AND FILE TREE
=====================================================================
Python owns all logic, keys and I/O. The web UI is a thin view that talks
to Python only through `api.py`. Keep modules small; no file over ~800
lines (gui.py with 2800 lines in one class is exactly what we are leaving).

VP4.py                 entry: package check, single-instance lock, starts window
version.py             VERSION = "5.0.0", REPO = "Xembou/VP4" (the ONE place)
api.py                 class VP4Api: every method the UI may call (JSON in/out)
ereignisse.py          thread-safe event queue + scheduler (successor of _spaeter)
dev_server.py          `python VP4.py --browser`: same VP4Api over 127.0.0.1 HTTP
medien_server.py       127.0.0.1-only HTTP server for local media (token URLs)

kern/                  (pure logic, no UI imports, fully unit-tested)
  krypto.py            moved from root: classic + modern ciphers, registry
  krypto_neu.py        XChaCha20, AES-GCM-SIV, ML-KEM hybrid (HPKE), age text
  dateien.py           moved: .vp4 container (unchanged format) + age files
  tresor.py            keystore v4 (VP4K4), data key, DPAPI wrapper, migration
  identitaet.py        device identity: Ed25519 + X25519, ID, contact card
  e2e.py               pair keys, per-message encryption, signatures, safety number
  umschlag.py          binary envelope format (pack/unpack/verify)
  nachrichten.py       message service: send/receive/edit/delete/react/receipts
  communities.py       communities, channels, invite codes, signed manifest
  datenbank.py         SQLite schema + access (bodies encrypted at rest)
  speicher.py          settings, paths, Obsidian sync (existing, trimmed)
  migration.py         one-time import of VP4 4.x data

netz/
  lan.py               successor of chat.py: UDP discovery + TCP, carries envelopes
  discord_netz.py      successor of discord_transport.py: VP4D2 lines, history catch-up
  vermittler.py        successor of transport.py: picks the route per envelope
  update.py            GitHub releases check (once per start, 5 s timeout)

discord_konfig.py      placeholders BOT_TOKEN = "", KANAL_IDS = "" (CI fills them)

ui/                    no build step, no npm. Plain ES modules.
  index.html           single page, strict CSP, loads css + js/main.js
  css/tokens.css       all design tokens (colors, radii, spacing, type, motion)
  css/glass.css        glass material + fallback
  css/komponenten.css  buttons, inputs, lists, sheets, menus, toasts, badges
  css/chat.css         bubbles, composer, reactions, attachments
  css/seiten.css       onboarding, settings, tools
  js/main.js           boot, routing, theme
  js/bruecke.js        calls pywebview.api OR dev HTTP; event polling
  js/dom.js            h(tag, attrs, ...children) builder, never innerHTML
  js/zustand.js        tiny store (subscribe/set), no framework
  js/ansichten/*.js    seitenleiste, chat, composer, sheets, einstellungen,
                       onboarding, sperre, werkzeuge/*, community
  js/demo.js           ?demo=1 → fake data, for design screenshots only
  icons/sprite.svg     Lucide subset (ISC license file next to it)
  fonts/               Inter variable woff2 (OFL) as fallback when Segoe UI
                       Variable is missing (Windows 10)
  emoji/emoji.json     vendored emoji list for the picker (no network)

tests/                 test modules; test_vp4.py stays the single runner
  ui_pruefung.py       Playwright: open every screen (demo + real dev server),
                       fail on console errors, overlapping text, horizontal
                       overflow; write screenshots to tests/screenshots/
test_vp4.py            runs everything; skips Playwright with a clear note if
                       it is not installed (CI on Windows installs it)
bauen.py, icon_erzeugen.py, .github/workflows/release.yml   (updated)

Old root modules (gui.py, chat.py, transport.py, discord_transport.py) are
deleted at the end once their tests are ported. Keep `git mv` for files
that move so history survives.

=====================================================================
3. PYTHON <-> UI BRIDGE
=====================================================================
- `webview.create_window("VP4", url="ui/index.html", js_api=VP4Api(...),
  width=1280, height=820, min_size=(960, 620), background_color=<theme bg>)`.
  Native window frame (robust snapping/resizing). Set dark title bar via
  DwmSetWindowAttribute(DWMWA_USE_IMMERSIVE_DARK_MODE=20) to match theme,
  and on Windows 11 22H2+ the Mica backdrop (attr 38, value 2) for the title
  bar. Do NOT rely on transparent windows (documented as unsupported on
  Windows; WebView2 only accepts alpha 0/255).
- Every VP4Api method returns {"ok": true, ...} or {"ok": false,
  "fehler": "<German, human readable>"}. Never raise into JS.
- Long work (file encryption, sending media, Argon2) runs in worker
  threads; progress goes through events.
- Events: Python pushes into ereignisse.py; JS polls
  `api.ereignisse_holen()` every 200 ms and dispatches. One mechanism for
  both pywebview and the dev server (no evaluate_js from worker threads).
  Event types: nachricht_neu, nachricht_geaendert, status (sent/delivered/
  read/failed), tippt, kontakt_anfrage, kontakt_geaendert, online,
  verbindung (lan/discord state), fortschritt, auftrag_fertig,
  auftrag_fehler, schluessel_geaendert, update_verfuegbar, fehler.
- File dialogs via `window.create_file_dialog`. Drag & drop: pywebview
  exposes `pywebviewFullPath` on dropped files — verify on Windows; if
  unavailable, accept dropped files via FileReader (<= 25 MB) instead.
- JS never sees private keys, the data key or pair keys.
- Media: received/sent files live in vp4_daten/medien/. The UI loads them
  from medien_server.py at http://127.0.0.1:<random port>/<random 32-byte
  token> — only registered tokens resolve, server binds 127.0.0.1 only.
  Small thumbnails may be data: URLs.
- CSP: default-src 'self'; script-src 'self'; style-src 'self';
  img-src 'self' data: blob: http://127.0.0.1:*; media-src 'self' blob:
  http://127.0.0.1:*; connect-src 'self' http://127.0.0.1:*; no inline JS.

=====================================================================
4. IDENTITY, CONTACTS AND E2E CRYPTO (kern/identitaet.py, kern/e2e.py)
=====================================================================
Identity (generated on first start, stored in the tresor):
- Ed25519 signing key + X25519 agreement key.
- User ID = Crockford-Base32 of SHA-256(b"VP4-ID1" || ed_pub || x_pub),
  first 50 bits → 10 chars shown as "XXXXX-XXXXX". The ID is bound to the
  keys: a contact card whose keys don't hash to the claimed ID is rejected.
  This fixes the "IDs are only claimed, never checked" weakness in CLAUDE.md.
- Contact card ("Visitenkarte"): {"v":1, "id", "name", "ed", "x" (base64),
  "avatar_farbe", "ts"} + Ed25519 signature over the canonical JSON
  (sorted keys, no spaces). Max ~1 KB.

Adding a friend (handshake):
1. A enters B's ID (or pastes B's "Freundescode" = "VP4C1-" + base64url of
   the signed card, which also works offline/QR-free).
2. A sends an ANFRAGE envelope to B containing A's signed card (cards are
   public; this envelope type is signed, not encrypted).
3. B sees a request ("Max (ABCDE-12345) möchte mit dir schreiben") →
   Annehmen / Ablehnen / Blockieren. On accept B sends ANNAHME with B's card.
4. Both derive the pair root key:
     shared = X25519(own_x_priv, peer_x_pub)
     root   = HKDF-SHA256(shared, salt=SHA256(sorted(idA,idB) joined "|"),
                          info=b"VP4 paar v1", length=32)
- Pinning: the first verified card per ID is pinned. If a different card
  ever appears for a known ID: block messages from it, show a red banner
  "Der Schlüssel von Max hat sich geändert" with "Prüfen" (safety number)
  and "Neuen Schlüssel annehmen".
- Safety number: SHA-512 iterated 5200x over (ed_pub||x_pub||id) per side
  (Signal-style), take 30 digits per side, sort the two halves, show 12
  blocks of 5 digits. Plus "Als verifiziert markieren" (stored per contact,
  reset on key change). UI badge: "Verifiziert" vs "Nicht verifiziert".

Message encryption (DMs):
- Per message: msg_key = HKDF(root, salt=message_id (16 random bytes),
  info=b"VP4 nachricht v1"), AES-256-GCM, random 12-byte nonce, AAD = the
  envelope header bytes. Static-static DH authenticates both sides; no
  extra signature needed for DMs.
- Honest limitation (write it in the UI "Sicherheit" sheet and README):
  no forward secrecy yet; a stolen device key opens old messages. A Double
  Ratchet is a possible later phase, not part of this rework.

Communities (see section 6): one random 32-byte community key; channel
key = HKDF(community_key, salt=channel_id, info=b"VP4 kanal v1"). Every
community message is ALSO signed with the sender's Ed25519 key inside the
ciphertext, and receivers verify it against the sender's card (cards are
attached to a member's first message in a community and cached). This
replaces the old "sender name is a self-claim" weakness.

The old built-in GRUPPEN_SCHLUESSEL is removed completely (code, CI
secret, docs). A test asserts the symbol no longer exists.

=====================================================================
5. ENVELOPE AND TRANSPORTS (kern/umschlag.py, netz/*)
=====================================================================
Envelope (binary):
  b"VP4N" | ver=1 (1) | typ (1) | from_id (10 ascii) | to (10 ascii, or
  "G-" + 8 chars for community/channel targets, padded to 12) | msg_id (16)
  | ts_ms (8, big endian) | nonce (12) | ciphertext+tag
Header (everything before ciphertext) is the AEAD AAD. Types:
  ANFRAGE, ANNAHME, ABLEHNUNG (signed cards), NACHRICHT (encrypted inner
  JSON), DATEI_TEIL (encrypted chunk), KARTE (signed card update).
Inner JSON of NACHRICHT (after decryption), field "art":
  text {text, antwort_auf?}
  bearbeiten {ziel, text}
  loeschen {ziel}
  reaktion {ziel, emoji, an: true|false}
  quittung {ids: [...], stufe: "zugestellt"|"gelesen"}   (batched)
  tippt {}                                                (LAN only)
  datei {datei_id, name, groesse, mime, teile, schluessel, sha256,
         vorschau? (small jpeg b64), dauer_ms? (voice), wellenform? (voice)}
  profil {name, avatar_farbe, avatar? (<=64 KB jpeg)}
  community_* (manifest, kanal_neu, kanal_umbenannt, ...)
Dedupe by msg_id everywhere (LAN + Discord may both deliver).

Discord (netz/discord_netz.py) — keeps all invariants from CLAUDE.md:
- Line format: VP4D2|<from>|<to>|<msg_id hex>|<part>|<total>|<base64>
  Split at 1900 chars, reassemble in any order, drop incomplete after 300 s.
- `payload_entschluesseln`-style rule stays: InvalidTag → ValueError with
  German text, so "für dich, ging aber nicht auf" is distinguishable.
- Does nothing in VP4_TESTMODUS (no real bot in tests).
- KANAL_IDS is a comma-separated list. Route by SHA-256(to) % n to spread
  Discord's per-channel rate limit (~5 msgs / 5 s per channel, SHARED by
  every client because everyone uses the same bot token). Listen to all.
- Offline delivery: persist the last processed Discord message id per
  channel; on (re)connect page through `channel.history(after=...)`
  (max 7 days / 5000 lines), then switch to live on_message.
- Files: encrypt with the per-file random key (in the datei message),
  upload as attachment parts of <= 8 MiB, max 100 MB per file. Only clients
  addressed in <to> download attachments.
- Never send "tippt" over Discord (would burn the shared rate limit).
  Batch read receipts: at most one quittung per conversation per 10 s.
- Send queue with retry (2 retries, exponential backoff), honour 429
  retry_after, status bar + per-message "Erneut senden" on final failure.
- Status mapping: login 401 → "Der eingebaute Discord-Zugang wurde
  gesperrt. Lade die neueste VP4-Version von GitHub." and trigger the update
  check; session start limit hit → explain and retry later, never hammer
  IDENTIFY (Discord resets the token after 1000 identifies / 24 h, shared
  by ALL clients).
- Optional cleanup: after a DM is read (quittung gelesen), the sender's
  client deletes those Discord lines (low priority, background).
- Settings → Discord: own bot token + channel ids override the built-in
  ones (as today).

LAN (netz/lan.py): keep UDP discovery + TCP framing, but frames now carry
envelopes. Discovery packet: {"v":2, "id", "port"}. Identity is no longer
trusted from the packet: an envelope only counts if it decrypts/verifies.
Typing indicators and instant receipts may use LAN.

Route choice (netz/vermittler.py): modes lan / discord / beide (default
beide). In beide: LAN if the peer is seen on LAN, else Discord.
Communities always go over Discord. The chat header shows the route
("über WLAN" / "über Discord").

=====================================================================
6. MESSENGER FEATURES (kern/nachrichten.py, kern/communities.py)
=====================================================================
DMs and groups:
- Text with links (linkified safely), emoji, multi-line.
- Reply (quote with sender + excerpt, click jumps and flashes).
- Reactions (quick bar ❤️ 👍 😂 😮 😢 🔥 + full picker), toggle on/off.
- Edit own message (shows "bearbeitet"), delete for me / for everyone.
- Status under own messages: Senden… / Gesendet / Zugestellt / Gelesen
  (read receipts can be disabled in settings; then you also don't get any).
- Images, videos, files, voice messages; progress ring while sending;
  "Erneut senden" on failure. Images: thumbnail (max 320x320) generated in
  Python with Pillow; click opens a lightbox (zoom, save, next/prev).
- Voice: record in the WebView with MediaRecorder (audio/webm;codecs=opus).
  Check that WebView2 grants the microphone permission under pywebview; if
  it can't be granted, hide the mic button and note it — do not add numpy/
  sounddevice. Waveform: 48 bars computed in JS from the recording.
- Typing indicator (LAN only), online dot (LAN seen or Discord activity
  within 2 min).
- Unread counters, mute, pin chat, per-chat search (search decrypted
  bodies in Python, not SQL), drafts per chat.
- Profile: name, avatar color (auto from ID) or picture; sent as `profil`.
- Groups: small private groups (like WhatsApp) = a community with exactly
  one channel and no channel UI. Old 4.x groups are not migrated (protocol
  change); show them once as "Bitte neu anlegen".

Communities with channels (like Discord):
- Create: name, icon (emoji or color), default channels "allgemein".
- Owner signs a manifest {community_id, name, icon, kanaele:[{id, name,
  position, nur_admins}], admins:[ids], version} with Ed25519. Clients only
  accept manifest updates signed by the owner or a listed admin and with a
  higher version.
- Invite code "VP4G2-" + base64url(community_id 5 bytes + key 32 bytes +
  owner_id 10 ascii) — one line, copy button. Joining = having the code
  (no member list, same honest trade-off as today: codes can't be revoked;
  joiners can read old lines still in Discord). "Mitglieder" shows people
  seen recently, labelled as such.
- Admins: add/rename/reorder/delete channels, announcement channels
  (nur_admins). "Neuen Code erstellen" = rotate the community key and send
  it to selected members via their DM pair keys (this is how you remove
  someone). If time is short, rotation may come last in the build order,
  but it must not be faked.

=====================================================================
7. STORAGE, MASTER PASSWORD, MIGRATION (kern/tresor.py, kern/datenbank.py)
=====================================================================
- Data folder stays vp4_daten/ next to the exe (unchanged behaviour).
- Tresor v4 ("VP4K4"), self-describing header like VP4K3 (marker | KDF id |
  KDF params | salt | nonce), header = AAD. A random 32-byte DATA KEY
  encrypts the tresor content (identity keys, key ring, pair roots,
  community keys). The data key is wrapped twice:
    a) Argon2id(master password) (64 MiB, 3 passes, p=1 — the pinned
       parameters from CLAUDE.md), stored in the tresor header;
    b) Windows DPAPI CryptProtectData (ctypes, CRYPTPROTECT_UI_FORBIDDEN),
       stored in vp4_daten/tresor.dpapi.
  Start: if tresor.dpapi exists and "Bei jedem Start fragen" is off →
  unlock silently. Else show the lock screen. On non-Windows (dev/CI) DPAPI
  is unavailable → always the lock screen; tests use a stub.
- Still no recovery (Leon's explicit wish). Forgetting the password with
  DPAPI disabled = reset. Onboarding says so in plain words.
- Auto-lock after N minutes idle (setting, default off).
- SQLite vp4_daten/vp4.db (WAL). Tables:
    kontakte(id PK, spitzname, karte_json, verifiziert INT, schluessel_neu INT,
             status TEXT CHECK(status IN ('anfrage_raus','anfrage_rein','ok','blockiert')),
             hinzugefuegt INT)
    unterhaltungen(id PK, art TEXT CHECK(art IN ('dm','gruppe','kanal')),
             community_id, titel, stumm INT, angeheftet INT, entwurf_enc BLOB,
             zuletzt_gelesen INT)
    nachrichten(id PK, unterhaltung_id, absender_id, ts INT, art TEXT,
             inhalt_enc BLOB, antwort_auf, bearbeitet INT, geloescht INT,
             status TEXT, weg TEXT)        INDEX(unterhaltung_id, ts)
    reaktionen(nachricht_id, absender_id, emoji, ts,
             PRIMARY KEY(nachricht_id, absender_id, emoji))
    anhaenge(id PK, nachricht_id, name_enc, groesse, mime, pfad, zustand,
             vorschau BLOB)
    communities(id PK, name, icon, schluessel_enc, besitzer_id, manifest_json,
             beigetreten INT)
    kanaele(id PK, community_id, name, position, nur_admins INT)
    discord_stand(kanal_id PK, letzte_nachricht_id)
  inhalt_enc / *_enc = AES-256-GCM with HKDF(data key, info=b"VP4 db v1"),
  AAD = row id. Load messages in pages of 50 (newest first).
- Migration from 4.x (migration.py, runs once, idempotent, logged):
  key ring from VP4K2/VP4K3 (asks the old master password once), settings
  (theme, Obsidian path, transport mode), discord.json, received files.
  Friends/groups cannot carry over (IDs are now key-bound); show the old
  nicknames + IDs once in a "Kontakte neu hinzufügen" sheet.

=====================================================================
8. WERKZEUGE (tools area)
=====================================================================
Keep all 13 existing methods (krypto.VERFAHREN registry, same behaviour,
same tests) and add, as registry entries with the same structure:
- XChaCha20-Poly1305 (PyNaCl crypto_aead_xchacha20poly1305_ietf_*,
  24-byte random nonce) — "sicher".
- AES-256-GCM-SIV (cryptography AESGCMSIV) — "sicher", hint: tolerates a
  repeated nonce without collapsing.
- Post-Quanten (Hybrid): HPKE Suite(KEM.MLKEM768_X25519, KDF.HKDF_SHA256,
  AEAD.AES_256_GCM) from cryptography>=50. Key pair generator; export public
  key as "VP4PQ1-" + base64url. Output "VP4Q1|" + base64(enc||ct). Label
  "Post-Quanten (Hybrid)", art "sicher", honest hint that this protects
  against future quantum computers, combined with classic X25519.
  If the running cryptography lacks it, hide the entry (don't reimplement).
- age: text (ASCII-armored) with passphrase or X25519 recipients, and files
  (.age) via pyrage. Interoperable with the official `age` CLI — test a
  round trip and, if the `age` binary is available, cross-decrypt.
Pages: Text (method picker grouped "Sicher" / "Post-Quanten" / "Nur zum
Spielen"), Dateien & Ordner (.vp4 + .age, progress, cancel), Schlüsselbund,
Signieren & Prüfen (Ed25519), Prüfsummen, Obsidian (export/import as
today; NEW option: export as an age-encrypted note with a passphrase).
The .vp4 container format and its four tamper tests stay untouched.

=====================================================================
9. DESIGN SYSTEM — macOS 26 "Tahoe" Liquid Glass (ui/css/tokens.css)
=====================================================================
Principles (from Apple HIG + the GitHub design skills
rshankras/claude-code-apple-skills design/liquid-glass,
Zettersten/skills liquid-glass, axiaoge2/apple-hig-designer,
anthropics/skills frontend-design):
- Glass is ONLY for the navigation/control layer that floats above
  content: sidebar, chat toolbar, composer, segmented controls, menus,
  popovers, sheets, toasts. Content (message bubbles, list rows, cards in
  tools) is solid. Never glass on glass. Tint only the primary action.
- Content scrolls UNDER the floating toolbar and composer, so the blur has
  something to show. Toolbar and composer are inset 12 px from the panel
  edges and do not touch the window border.
- One accent color, used sparingly. One bold element per screen.
- No emoji as UI icons (only in content and reactions). Icons: Lucide
  subset, stroke 1.75, 18 px in lists, 20 px in toolbars.

Wallpaper: the window background is an in-page wallpaper (layered radial
gradients, 6 presets incl. one neutral graphite; slow 60 s drift, off when
reduced motion). Glass blurs this wallpaper and scrolling content. Never
depend on OS transparency.

Glass material (glass.css):
  .glas {
    background: var(--glas-fuellung);
    backdrop-filter: blur(24px) saturate(180%);
    border: 1px solid var(--glas-rand);
    box-shadow: inset 0 1px 0 var(--glas-licht),
                inset 0 -1px 0 rgba(255,255,255,.06),
                0 8px 32px rgba(0,0,0,var(--glas-schatten));
    isolation: isolate;
  }
  light: --glas-fuellung rgba(255,255,255,.55); --glas-rand rgba(255,255,255,.55);
         --glas-licht rgba(255,255,255,.85); --glas-schatten .14
  dark:  --glas-fuellung rgba(30,30,32,.50);   --glas-rand rgba(255,255,255,.12);
         --glas-licht rgba(255,255,255,.22); --glas-schatten .40
  "Glas stark" setting adds the SVG refraction filter
  (feTurbulence + feDisplacementMap, scale -24) via
  backdrop-filter: url(#fluessigglas) blur(14px) saturate(160%) — Chromium/
  WebView2 only; default is plain blur for performance.
  "Transparenz reduzieren" setting and @supports-not(backdrop-filter) →
  solid fills (light #F2F2F7 / dark #1C1C1E, 96% alpha).

Colors (Apple iOS/macOS 26 system colors):
  accent choices (light / dark):
    Blau #0088FF/#0091FF (default), Indigo #6155F5/#6D7CFF,
    Lila #CB30E0/#DB34F2, Pink #FF2D55/#FF375F, Rot #FF383C/#FF4245,
    Orange #FF8D28/#FF9230, Grün #34C759/#30D158, Mint #00C8B3/#00DAC3
  label: light #000000 / .60 / .30 alpha ; dark #FFFFFF / .60 / .30
  gray backgrounds: light #F2F2F7 #FFFFFF ; dark #000000 #1C1C1E #2C2C2E
  separators: light rgba(60,60,67,.18) ; dark rgba(84,84,88,.48)
  received bubble: light #E9E9EB / dark #262629 ; sent bubble: accent, text #FFF
  Every filled element sets an explicit text color (lesson from CLAUDE.md:
  grey text on a light accent).
  Contrast: body text >= 4.5:1 on its actual background, check in tests for
  every accent in both themes.

Typography (Windows-adapted macOS scale, px):
  stack: -apple-system, "SF Pro Text", "Segoe UI Variable Text",
         "Segoe UI Variable", "Segoe UI", Inter, system-ui, sans-serif
  Large Title 28/34 700 · Title 20/25 600 · Headline 14/19 600 ·
  Body 14/20 400 · Callout 13/18 · Subhead 12/16 · Caption 11/14 ·
  monospace for IDs/keys: "Cascadia Mono", Consolas, ui-monospace
  letter-spacing -0.01em on titles. No all-caps eyebrow labels.

Spacing: 4 8 12 16 20 24 32 40 48. Radii: window panels 26, sheets 22,
cards 16, inputs 12, list rows 10, bubbles 18, buttons/segmented/composer
capsule (9999px). Concentric rule: inner radius = outer radius - padding.
Use `corner-shape: squircle` where supported (Chromium 139+), plain
border-radius otherwise.

Motion: --dauer-kurz 160ms, --dauer 280ms, --dauer-lang 420ms;
--feder: cubic-bezier(.32,.72,0,1) (sheets, panels);
--feder-federnd: cubic-bezier(.175,.885,.32,1.275) (reactions pop);
press = scale(.97). New bubble: from translateY(8px) scale(.96) opacity 0.
Sheets slide up 24px + fade; menus scale from .96 at the pointer.
@media (prefers-reduced-motion) and the in-app setting → fades only.

Components (komponenten.css): Knopf (primär = accent capsule, sekundär =
glass/tinted capsule, leise = text, gefährlich = red), min height 32 px
(toolbar) / 40 px (forms); Eingabefeld (12 px radius, focus ring
0 0 0 4px accent @30%); Segmented control (glass capsule, sliding pill);
Schalter (iOS toggle 38x22); Liste (inset grouped like System Settings,
44 px rows, separators inset 16 px); Sheet (centered, max 560 px, glass,
dim layer rgba(0,0,0,.35)); Kontextmenü (glass, 6 px item radius, keyboard
navigable); Toast (glass capsule top center, 3 s); Badge (accent capsule,
min 18 px, 11 px bold white); Avatar (circle, initials on color derived
from ID, online dot 10 px with 2 px ring).

=====================================================================
10. SCREENS
=====================================================================
Layout: wallpaper → floating glass sidebar (left, 300 px, 12 px margin,
radius 26) + main content panel (solid-ish, radius 26, 12 px margin).
Below 1100 px width the sidebar collapses to 76 px (avatars/icons only).

A) Onboarding (first start, glass card centered over the wallpaper,
   4 steps with progress dots): 1 Willkommen (what VP4 is, honest one-liner
   about security) → 2 Profil (name, avatar color/picture) → 3
   Master-Passwort (twice, strength meter from speicher.passwort_staerke,
   red box "Es gibt keine Wiederherstellung", checkbox "Ich habe es mir
   aufgeschrieben", toggle "Windows merkt sich das Passwort" default on) →
   4 Deine ID (big mono "XXXXX-XXXXX", copy, "Freundescode kopieren") →
   if 4.x data was found, the migration step comes before 4.
B) Sperrbildschirm: blurred wallpaper, avatar, password field, "Entsperren".
C) Sidebar: search field (capsule) · segmented control
   [Chats | Communities | Werkzeuge] · list · footer with own avatar + name
   + ID (click = copy) and a gear button (Einstellungen).
   Chats: rows 64 px: avatar, name, last message preview (1 line, "Du: "
   prefix for own), time, unread badge, mute icon, lock state icon only if
   NOT verified (subtle). Top: "Neuer Chat" (+) menu: Freund hinzufügen,
   Gruppe erstellen, Anfragen (badge).
   Communities: community rows; selecting one drills in (back chevron) to
   its channel list "# allgemein", unread dots, admin "+" to add channel,
   header menu: Einladen (code), Einstellungen, Verlassen.
   Werkzeuge: Text, Dateien & Ordner, Schlüsselbund, Signieren, Prüfsummen,
   Obsidian.
D) Chat: floating glass toolbar: avatar, name, subline ("online · über
   WLAN" / "über Discord" / "zuletzt aktiv 14:02" / channel topic), shield
   button → Sicherheit sheet (E2E explanation, what is NOT protected, safety
   number, verify), search, info panel toggle (shared media, files, mute).
   Messages: day separators ("Heute", "Gestern", "Mo., 29. Sep."),
   time shown on hover and after >15 min gaps, grouping (2 px gap within a
   run, 10 px between runs, tail only on the last bubble of a run,
   max width 68%), sender name above runs in groups/channels, emoji-only
   messages (<=3 emoji) large without bubble, hover action pill (react,
   reply, more), right-click context menu (Antworten, Reagieren, Kopieren,
   Bearbeiten, Weiterleiten, Löschen, Info: route + times). Reactions as
   small capsules overlapping the bubble bottom by 6 px. Jump-to-latest
   button when scrolled up. Append-only rendering + "load older" on scroll
   top (no full redraw per message — a known weakness of 4.x).
   Composer: floating glass capsule: + (Foto/Video, Datei, Sprachnachricht),
   autogrowing textarea (1–6 lines), emoji button, then send button (accent
   circle, arrow-up icon) that replaces the mic button when text exists.
   Enter = send, Shift+Enter = newline, Esc = cancel reply/edit, ↑ in empty
   field = edit last own message. Reply/edit banner above the composer.
   Paste image from clipboard, drag & drop files with a glass drop overlay.
   Empty state: centered "Wähle einen Chat" + button "Freund hinzufügen".
E) Sheets: Freund hinzufügen (ID or Freundescode), Anfragen, Gruppe
   erstellen, Community erstellen / beitreten (paste code), Einladungscode,
   Sicherheitsnummer, Schlüssel geändert (red), Lightbox, Bestätigen.
F) Werkzeuge pages in the main panel, each a clean form: input, method
   picker (glass popover grouped with "Sicher" / "Post-Quanten" / "Nur zum
   Spielen" badges), key field with "Erzeugen", primary action, output with
   copy. Files page: big drop zone, queue with progress and cancel.
G) Einstellungen (System Settings style: left list inside the main panel,
   grouped inset lists right): Profil · Erscheinungsbild (System/Hell/
   Dunkel, Akzentfarbe swatches, Hintergrund presets, Glas stark,
   Transparenz reduzieren, Bewegung reduzieren) · Chat (Weg: WLAN /
   Discord / beide, Lesebestätigungen, Tippanzeige, Medien automatisch
   laden bis X MB) · Benachrichtigungen (an/aus, Vorschau zeigen, Ton) ·
   Discord (status, own bot override, channel ids) · Sicherheit
   (Master-Passwort ändern, Bei jedem Start fragen, Automatisch sperren,
   eigene Sicherheitsdaten, blockierte Kontakte) · Daten (Ordner öffnen,
   Speicherplatz, Alles löschen und neu einrichten with typed confirmation)
   · Über (version, update check, honest notes, licenses for Lucide/Inter).

Keyboard: Ctrl+K search/quick switcher, Ctrl+N new chat, Ctrl+, settings,
Ctrl+L lock, Alt+↑/↓ next/prev chat. Visible focus rings everywhere.

=====================================================================
11. RELEASE, TOKEN, UPDATE CHECK
=====================================================================
Leon decided (2026-10-01, knowing the risk): the PUBLIC exe on GitHub
Releases contains the bot token, so anyone can download and chat at once.
Consequences you must implement and document:
- release.yml (windows-latest, Python 3.13) injects DISCORD_BOT_TOKEN and
  DISCORD_KANAL_IDS from repo secrets into discord_konfig.py AFTER the self
  test; VP4_GRUPPEN_SCHLUESSEL is gone. Without secrets it still builds.
  The source file stays empty; the existing test that forbids a real token
  in discord_konfig.py stays.
- Document in README + CLAUDE.md (German, honest): the token can be
  extracted from the exe; message CONTENT stays safe (E2E), but someone
  could spam the channel, delete lines, or burn the shared 1000 identifies /
  24 h, after which Discord resets the token and every copy of VP4 loses
  Discord until a new release. Recovery playbook: reset token in the
  Developer Portal → update the secret → tag a new release → clients see
  the update banner.
- Bot setup guide in README: a dedicated Discord server, bot with only
  View Channel, Send Messages, Read Message History, Attach Files in the
  VP4 channels (no admin, no manage messages, no other servers), Message
  Content intent on.
- netz/update.py: GET https://api.github.com/repos/<REPO>/releases/latest
  once per start (timeout 5 s, User-Agent "VP4/<version>"), compare semver,
  show a glass banner with the release page link. Never auto-download.
- bauen.py: clean build with --add-data "ui;ui", pywebview's own
  PyInstaller hook, --collect-all discord, excludes (tkinter, PyQt*,
  PySide*, gi, numpy), icon, then smoke-start the exe with
  VP4_SELBSTTEST_START=1 (opens + closes) and print the size.

=====================================================================
12. TESTS AND VERIFICATION
=====================================================================
- test_vp4.py remains the single command. Keep every existing check for
  krypto, dateien, formats (legacy blobs!), Obsidian, ports. Port chat/
  discord/group checks to the new protocol. Add checks for:
  ID derivation + card signature + reject mismatched card; handshake gives
  equal roots on both sides; tampered header/ciphertext rejected; wrong
  pair rejected; key-change detection; safety number symmetric; envelope
  pack/unpack; Discord split/reassemble in shuffled order; dedupe; offline
  catch-up from a fake history; community manifest signature + version
  rule; forged community message (bad signature) rejected; channel key
  separation; invite code round trip; tresor v4 create/unlock/change
  password; DPAPI stub path; migration from a fixture 4.x data folder;
  database encryption at rest (raw SQLite file contains no plaintext);
  every new cipher round-trips and rejects tampering; age interop; HPKE
  hybrid round trip; GRUPPEN_SCHLUESSEL absent; scheduler job count after
  repeated theme switches; Discord does nothing in VP4_TESTMODUS.
- UI (tests/ui_pruefung.py, Playwright headless Chromium, dev server +
  ?demo=1): every screen in light and dark, widths 960 and 1440; fail on
  console errors, text elements whose bounding boxes overlap, horizontal
  scroll, contrast below 4.5:1, and any element containing message text
  that was rendered via innerHTML (inject "<img src=x onerror=...>" as a
  message and assert it shows as text). Save PNGs to tests/screenshots/
  (git-ignored) and LOOK at them after each UI step.
- Two clients end-to-end in one process (LAN over localhost and a fake
  Discord transport): request → accept → text → reply → reaction → edit →
  delete → file → receipts.

=====================================================================
13. DOCS
=====================================================================
Rewrite README.md (German, same honest tone): download, first start,
adding friends, communities, what is protected and what not, Discord bot
setup for maintainers, troubleshooting, build from source. Update
CLAUDE.md: new structure, invariants, the token decision, removed group
key, open points (forward secrecy, LAN on two real PCs, Windows firewall).

=====================================================================
14. BUILD ORDER — one stage at a time, tests + screenshots + commit each
=====================================================================
Stage 1  Skeleton: VP4.py starts pywebview with ui/index.html; bruecke.js
         + event polling; dev_server.py; tokens.css + glass.css + a demo
         page with every component; Playwright screenshot script. Show
         screenshots before continuing.
Stage 2  kern/: move krypto/dateien/speicher (git mv), tresor v4 + DPAPI,
         identitaet, e2e, umschlag, datenbank — with tests. No UI yet.
Stage 3  netz/: lan + discord_netz + vermittler on envelopes; nachrichten
         service; two-client end-to-end test.
Stage 4  Onboarding, lock screen, sidebar, chat view, composer (text,
         reply, reactions, edit, delete, receipts).
Stage 5  Media: images, video, files, voice, lightbox, drag & drop.
Stage 6  Communities + channels + invite codes + admin manifest (+ key
         rotation).
Stage 7  Werkzeuge pages incl. new ciphers (XChaCha20, GCM-SIV, HPKE
         hybrid, age) and Obsidian age export.
Stage 8  Settings, notifications, update check, migration from 4.x.
Stage 9  Delete old modules, bauen.py + release.yml, README, CLAUDE.md,
         final full test run and a screenshot tour of every screen.
Start with Stage 1: create ui/index.html, ui/css/tokens.css,
ui/css/glass.css, ui/js/bruecke.js, api.py (with ping + version),
dev_server.py and a minimal VP4.py that opens the window.
```
