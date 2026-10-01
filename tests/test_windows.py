# -*- coding: utf-8 -*-
"""Prüfungen, die nur auf echtem Windows etwas bedeuten.

Unter Linux/macOS wird alles übersprungen - dort gibt es weder DPAPI noch
WebView2. Auf Windows (lokal oder im Workflow pruefen.yml auf GitHubs
Windows-Rechnern) läuft es WIRKLICH:

  - DPAPI echt (CryptProtectData), nicht die Attrappe: hin und zurück,
    falsche Entropie, Müll, und der ganze Weg über den Tresor
  - pywebview: Import, WebView2-Laufzeit in der Registry, und ob die
    Windows-Seite von pywebview (pythonnet + WebView2-DLLs) wirklich
    "edgechromium" wählt - ohne ein Fenster zu öffnen
  - WLAN: SO_EXCLUSIVEADDRUSE, SIO_UDP_CONNRESET, zwei LanNetz über
    127.0.0.1, und ob die festen Ports 41230/41231 belegbar sind
    (Hyper-V/WSL reservieren unter Windows gern ganze Portbereiche)
  - os.startfile gibt es (Ordner/Dateien öffnen)
  - der Medien-Server liefert über 127.0.0.1 aus, auch Range-Anfragen

Wird von test_vp4.py über pruefen(R, hilfen) aufgerufen.
"""

import os
import random
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

WURZEL = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(WURZEL))

os.environ.setdefault("VP4_TESTMODUS", "1")

# Kennung der WebView2-Laufzeit ("Evergreen") bei EdgeUpdate
WEBVIEW2_GUID = "{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}"


def _warten(bedingung, frist=5.0):
    ende = time.monotonic() + frist
    while time.monotonic() < ende:
        if bedingung():
            return True
        time.sleep(0.05)
    return bool(bedingung())


def _wirft(funktion, art, *argumente):
    try:
        funktion(*argumente)
    except art:
        return True
    except Exception:
        return False
    return False


def _ohne_proxy():
    """urllib ohne Proxy - sonst ginge 127.0.0.1 womöglich über einen Proxy
    aus der Umgebung und der Test prüfte etwas anderes."""
    return urllib.request.build_opener(urllib.request.ProxyHandler({}))


# ---------------------------------------------------------------------------
#  DPAPI
# ---------------------------------------------------------------------------

def _dpapi(R, ordner: Path):
    print("\n--- kern/tresor.py: echtes Windows-DPAPI ---")
    from kern.tresor import (DPAPI, Tresor, datenschluessel_merken,
                             datenschluessel_vergessen, mit_windows_entsperren)

    d = DPAPI()
    R.pruefe("DPAPI ist unter Windows verfügbar", d.verfuegbar())
    if not d.verfuegbar():
        return

    daten = os.urandom(32)
    try:
        blob = d.schuetzen(daten)
        R.pruefe("DPAPI: schützen liefert einen Block, der den Klartext nicht enthält",
                 isinstance(blob, bytes) and len(blob) > len(daten) and daten not in blob,
                 f"{len(blob)} Byte")
        R.pruefe("DPAPI: hin und zurück ergibt dieselben 32 Byte", d.entschuetzen(blob) == daten)
        R.pruefe("DPAPI: zweimal schützen ergibt zwei verschiedene Blöcke",
                 d.schuetzen(daten) != blob)
        gross = os.urandom(256 * 1024)
        R.pruefe("DPAPI: auch 256 KiB gehen hin und zurück",
                 d.entschuetzen(d.schuetzen(gross)) == gross)
    except Exception as e:
        R.fehlschlag("DPAPI: schützen/entschützen", e)
        return

    R.pruefe("DPAPI: andere Entropie -> OSError",
             _wirft(DPAPI(b"eine ganz andere").entschuetzen, OSError, blob))
    R.pruefe("DPAPI: ohne Entropie -> OSError",
             _wirft(DPAPI(b"").entschuetzen, OSError, blob))
    R.pruefe("DPAPI: Müll -> OSError (kein Absturz)",
             _wirft(d.entschuetzen, OSError, b"\x00\xffkaputt" * 8))
    gekippt = bytearray(blob)
    gekippt[len(gekippt) // 2] ^= 0x01
    R.pruefe("DPAPI: ein gekipptes Bit -> OSError",
             _wirft(d.entschuetzen, OSError, bytes(gekippt)))
    R.pruefe("DPAPI: leere Eingabe -> ValueError",
             _wirft(d.schuetzen, ValueError, b"") and _wirft(d.entschuetzen, ValueError, b""))

    # Der ganze Weg, wie VP4 ihn geht - mit echtem DPAPI und Wegwerf-Pfaden
    pfad = ordner / "tresor.enc"
    dp = ordner / "tresor.dpapi"
    try:
        t = Tresor(pfad)
        t.create("windows-pw")
        t.add_key("K", "AES", "wert")
        datenschluessel_merken(t, None, dp)
        roh = dp.read_bytes()
        R.pruefe("Tresor: tresor.dpapi entsteht, mit Marke VP4W1",
                 dp.exists() and roh.startswith(b"VP4W1"))
        R.pruefe("Tresor: der Datenschlüssel steht nicht im Klartext in tresor.dpapi",
                 t.datenschluessel not in roh)
        still = Tresor(pfad)
        R.pruefe("Tresor: öffnet still über echtes DPAPI",
                 mit_windows_entsperren(still, None, dp) is True
                 and still.get_key("K")["wert"] == "wert")
        t.change_password("windows-pw", "neues-pw")
        R.pruefe("Tresor: DPAPI-Weg gilt nach Passwortwechsel weiter",
                 mit_windows_entsperren(Tresor(pfad), None, dp) is True)
        R.pruefe("Tresor: andere Entropie (andere Anwendung) öffnet nicht",
                 mit_windows_entsperren(Tresor(pfad), DPAPI(b"fremd"), dp) is False)
        dp.write_bytes(b"VP4W1" + b"\x00" * 100)
        neu = Tresor(pfad)
        R.pruefe("Tresor: kaputte tresor.dpapi -> False, Tresor bleibt zu",
                 mit_windows_entsperren(neu, None, dp) is False and not neu.is_unlocked())
        dp.write_bytes(roh)
        R.pruefe("Tresor: vergessen löscht die Datei, danach kein stilles Öffnen",
                 datenschluessel_vergessen(dp) is True and not dp.exists()
                 and mit_windows_entsperren(Tresor(pfad), None, dp) is False)
    except Exception as e:
        R.fehlschlag("Tresor mit echtem DPAPI", e)


# ---------------------------------------------------------------------------
#  pywebview / WebView2
# ---------------------------------------------------------------------------

def _webview2_version():
    """Version der installierten WebView2-Laufzeit oder None.

    Dieselben Stellen, die auch pywebview und Microsoft nennen: 64-Bit-
    Windows unter WOW6432Node, sonst direkt; pro Benutzer unter HKCU."""
    import winreg
    stellen = [
        (winreg.HKEY_LOCAL_MACHINE, rf"SOFTWARE\WOW6432Node\Microsoft\EdgeUpdate\Clients\{WEBVIEW2_GUID}"),
        (winreg.HKEY_LOCAL_MACHINE, rf"SOFTWARE\Microsoft\EdgeUpdate\Clients\{WEBVIEW2_GUID}"),
        (winreg.HKEY_CURRENT_USER, rf"Software\Microsoft\EdgeUpdate\Clients\{WEBVIEW2_GUID}"),
    ]
    for wurzel, pfad in stellen:
        try:
            with winreg.OpenKey(wurzel, pfad) as schluessel:
                wert, _ = winreg.QueryValueEx(schluessel, "pv")
            if wert and str(wert) != "0.0.0.0":
                return str(wert)
        except OSError:
            continue
    return None


def _webview(R):
    print("\n--- pywebview und WebView2 ---")
    try:
        import webview
        R.pruefe("pywebview lässt sich importieren", True)
    except Exception as e:
        R.fehlschlag("pywebview lässt sich importieren", e)
        return

    lib = Path(webview.__file__).resolve().parent / "lib"
    fehlen = [n for n in ("Microsoft.Web.WebView2.Core.dll", "Microsoft.Web.WebView2.WinForms.dll")
              if not (lib / n).exists()]
    R.pruefe("pywebview bringt die WebView2-DLLs mit", not fehlen, ", ".join(fehlen))

    version = _webview2_version()
    R.pruefe("WebView2-Laufzeit ist installiert (Registry)", version is not None,
             "Ohne sie öffnet VP4 kein Fenster - https://developer.microsoft.com/microsoft-edge/webview2/")
    if version:
        print(f"         WebView2 {version}")

    # Die Windows-Seite von pywebview in einem eigenen Prozess laden: Sie
    # startet die .NET-Laufzeit (pythonnet) und lädt die WebView2-DLLs.
    # Das soll den Testprozess nicht verbiegen - und falls pythonnet hart
    # abstürzt, nimmt es nur den Kindprozess mit.
    skript = ("import webview.platforms.winforms as w, clr; "
              "print('RENDERER=' + w.renderer)")
    try:
        lauf = subprocess.run([sys.executable, "-c", skript], capture_output=True, text=True,
                              encoding="utf-8", errors="replace", timeout=180, cwd=str(WURZEL))
        ausgabe = (lauf.stdout or "") + (lauf.stderr or "")
        renderer = next((z.split("=", 1)[1].strip() for z in ausgabe.splitlines()
                         if z.startswith("RENDERER=")), None)
        R.pruefe("pywebview wählt unter Windows Edge Chromium (pythonnet + WebView2 laden)",
                 lauf.returncode == 0 and renderer == "edgechromium",
                 f"Rückgabewert {lauf.returncode}, renderer={renderer}; "
                 + " | ".join(ausgabe.strip().splitlines()[-4:]))
    except subprocess.TimeoutExpired:
        R.pruefe("pywebview wählt unter Windows Edge Chromium (pythonnet + WebView2 laden)",
                 False, "nach 180 s keine Antwort")


# ---------------------------------------------------------------------------
#  WLAN
# ---------------------------------------------------------------------------

def _freier_udp_port():
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


class _Sammler:
    def __init__(self):
        self.umschlaege = []
        self.ereignisse = []

    def empfangen(self, daten, weg, adresse=None):
        self.umschlaege.append((daten, weg))

    def ereignis(self, typ, **daten):
        self.ereignisse.append((typ, daten))

    def hat(self, daten):
        return any(d == daten for d, _ in list(self.umschlaege))


def _id():
    from kern.identitaet import CROCKFORD
    z = "".join(random.choice(CROCKFORD) for _ in range(10))
    return z[:5] + "-" + z[5:]


def _umschlag(von, an, groesse=200) -> bytes:
    from kern import umschlag
    from kern.umschlag import Umschlag
    return Umschlag(umschlag.NACHRICHT, von, an, os.urandom(16), int(time.time() * 1000),
                    os.urandom(12), os.urandom(groesse)).packen()


def _feste_ports(R, lan_modul):
    """Lassen sich 41230/41231 überhaupt belegen? Windows reserviert mit
    Hyper-V/WSL/Docker gern ganze Bereiche ("netsh int ipv4 show
    excludedportrange protocol=tcp") - dann schlägt bind() mit
    WinError 10013 fehl, egal ob der Port frei ist."""
    for art, port, name in ((socket.SOCK_STREAM, lan_modul.CHAT_PORT, "TCP"),
                            (socket.SOCK_DGRAM, lan_modul.BROADCAST_PORT, "UDP")):
        s = socket.socket(socket.AF_INET, art)
        try:
            s.bind(("127.0.0.1", port))
            R.pruefe(f"Fester {name}-Port {port} lässt sich belegen", True)
        except OSError as e:
            if getattr(e, "winerror", None) == 10048:
                # Belegt - von einem laufenden VP4 vermutlich. Kein Fehler im Code.
                print(f"  (Hinweis: {name}-Port {port} ist gerade belegt: {e})")
            else:
                R.pruefe(f"Fester {name}-Port {port} lässt sich belegen", False,
                         f"{e} - liegt er in einem reservierten Bereich? "
                         "netsh int ipv4 show excludedportrange protocol=tcp")
        finally:
            s.close()


def _lan(R):
    print("\n--- netz/lan.py: Windows-Sockets ---")
    from netz import lan as lan_modul
    from netz.lan import LanNetz

    R.pruefe("socket.SO_EXCLUSIVEADDRUSE gibt es", hasattr(socket, "SO_EXCLUSIVEADDRUSE"))
    R.pruefe("socket.SIO_UDP_CONNRESET gibt es", hasattr(socket, "SIO_UDP_CONNRESET"))
    _feste_ports(R, lan_modul)

    ua, ub = _freier_udp_port(), _freier_udp_port()
    tot = _freier_udp_port()       # an diesen Port hört niemand
    sa, sb = _Sammler(), _Sammler()
    ida, idb = _id(), _id()
    # A kündigt sich auch an einen toten Port an: Windows meldet das beim
    # nächsten recvfrom() als ConnectionResetError (WSAECONNRESET) - genau
    # das soll SIO_UDP_CONNRESET abschalten.
    a = LanNetz(ida, sa.empfangen, sa.ereignis, port_tcp=0, port_udp=ua, bind_host="127.0.0.1",
                ziele=[("127.0.0.1", tot), ("127.0.0.1", ub)], intervall=0.2)
    b = LanNetz(idb, sb.empfangen, sb.ereignis, port_tcp=0, port_udp=ub, bind_host="127.0.0.1",
                ziele=[("127.0.0.1", ua)], intervall=0.2)
    try:
        a.start()
        b.start()
        R.pruefe("Windows: zwei LanNetz starten auf 127.0.0.1",
                 a.zustand == "an" and b.zustand == "an", f"{a.zustand}/{b.zustand}")
        try:
            exklusiv = a._server.getsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE)
        except (OSError, AttributeError) as e:
            exklusiv = f"{type(e).__name__}: {e}"
        R.pruefe("Windows: der TCP-Empfang ist exklusiv (SO_EXCLUSIVEADDRUSE)",
                 exklusiv not in (0, None) and not isinstance(exklusiv, str), str(exklusiv))
        zweiter = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            zweiter.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            zweiter.bind(("127.0.0.1", a.echter_tcp_port))
            doppelt = True
        except OSError:
            doppelt = False
        finally:
            zweiter.close()
        R.pruefe("Windows: ein zweites Programm kann den TCP-Port nicht mitbenutzen",
                 not doppelt)

        R.pruefe("Windows: die Instanzen finden sich",
                 _warten(lambda: a.ist_online(idb) and b.ist_online(ida)),
                 f"{a.online_ids()} / {b.online_ids()}")
        u1 = _umschlag(ida, idb)
        a.senden(u1, idb)
        R.pruefe("Windows: Umschlag A -> B kommt an", _warten(lambda: sb.hat(u1)))
        u2 = _umschlag(idb, ida, 2 * 1024 * 1024)
        b.senden(u2, ida)
        R.pruefe("Windows: 2-MB-Umschlag B -> A kommt Byte für Byte an",
                 _warten(lambda: sa.hat(u2), 10))
        # Nach mehreren Runden an den toten Port muss A noch hören
        time.sleep(1.5)
        R.pruefe("Windows: Erkennung übersteht 'Port nicht erreichbar' (SIO_UDP_CONNRESET)",
                 a.zustand == "an" and a.ist_online(idb) and b.ist_online(ida),
                 f"{a.zustand}, {a.online_ids()}")
        u3 = _umschlag(ida, idb)
        a.senden(u3, idb)
        R.pruefe("Windows: danach geht weiter Post durch", _warten(lambda: sb.hat(u3)))
    except Exception as e:
        R.fehlschlag("Windows: LanNetz über 127.0.0.1", e)
    finally:
        for netz in (a, b):
            try:
                netz.stop()
            except Exception:
                pass


# ---------------------------------------------------------------------------
#  Medien-Server, os.startfile
# ---------------------------------------------------------------------------

def _medien(R, ordner: Path):
    print("\n--- medien_server.py über 127.0.0.1 ---")
    from medien_server import MedienServer

    inhalt = os.urandom(300 * 1024)
    datei = ordner / "bild mit Ümlaut.png"
    datei.write_bytes(inhalt)
    server = None
    try:
        server = MedienServer()
        R.pruefe("Medien-Server hört nur auf 127.0.0.1",
                 server._server.server_address[0] == "127.0.0.1",
                 str(server._server.server_address))
        url = server.url(datei)
        oeffner = _ohne_proxy()
        with oeffner.open(url, timeout=10) as antwort:
            daten = antwort.read()
            typ = antwort.headers.get("Content-Type")
        R.pruefe("Medien-Server liefert die Datei Byte für Byte (Pfad mit Umlaut)", daten == inhalt,
                 f"{len(daten)} von {len(inhalt)} Byte")
        R.pruefe("Medien-Server: .png kommt als image/png", typ == "image/png", str(typ))

        anfrage = urllib.request.Request(url, headers={"Range": "bytes=100-199"})
        with oeffner.open(anfrage, timeout=10) as antwort:
            teil = antwort.read()
            status = antwort.status
        R.pruefe("Medien-Server: Range-Anfrage (Spulen in Videos)",
                 status == 206 and teil == inhalt[100:200], f"{status}, {len(teil)} Byte")

        try:
            oeffner.open(url.rsplit("/", 1)[0] + "/" + "0" * 64, timeout=10)
            unbekannt = 200
        except urllib.error.HTTPError as e:
            unbekannt = e.code
        R.pruefe("Medien-Server: unbekannte Adresse -> 404", unbekannt == 404, str(unbekannt))
    except Exception as e:
        R.fehlschlag("Medien-Server über 127.0.0.1", e)
    finally:
        if server:
            server.stoppen()

    # Windows liest die Dateitypen aus der Registry - was dort fehlt,
    # kommt als application/octet-stream. Nur zur Auskunft.
    import mimetypes
    typen = {e: mimetypes.guess_type("x" + e)[0] for e in (".jpg", ".gif", ".webp", ".mp4",
                                                           ".webm", ".ogg", ".mp3", ".wav")}
    print("  (Dateitypen laut Windows: "
          + ", ".join(f"{e}={t or 'unbekannt'}" for e, t in typen.items()) + ")")


def pruefen(R, hilfen):
    if sys.platform != "win32":
        print("  (übersprungen: nur unter Windows - läuft im Workflow pruefen.yml auf windows-latest)")
        return

    with tempfile.TemporaryDirectory(prefix="vp4_windows_") as tmp:
        ordner = Path(tmp)
        _dpapi(R, ordner)
        _webview(R)
        _lan(R)
        print("\n--- os.startfile ---")
        R.pruefe("os.startfile gibt es (Ordner und Dateien öffnen)", callable(getattr(os, "startfile", None)))
        _medien(R, ordner)
