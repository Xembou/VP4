#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
=====================================================================
 medien_server.py - Bilder, Videos und Sprachnachrichten für die Seite
=====================================================================
Die Oberfläche läuft im WebView und darf aus gutem Grund nicht einfach
Dateien von der Platte lesen. Empfangene Medien liefert deshalb dieser
kleine Server aus:

  - er hört NUR auf 127.0.0.1 (nicht im WLAN erreichbar),
  - jede Datei bekommt einen zufälligen Schlüssel in der Adresse
    (http://127.0.0.1:<port>/m/<32 Byte als Hex>),
  - nur ausdrücklich angemeldete Dateien sind erreichbar - kein
    Verzeichnis, kein ../, keine Liste.

Wer die Adresse nicht kennt, kommt an nichts heran; andere Programme auf
demselben PC müssten den Schlüssel erraten (2^256 Möglichkeiten).
Videos brauchen "Range"-Anfragen zum Spulen - die gehen auch.
=====================================================================
"""

import http.server
import mimetypes
import os
import secrets
import socketserver
import threading
from pathlib import Path


class _Handler(http.server.BaseHTTPRequestHandler):
    server_version = "VP4"
    sys_version = ""

    def log_message(self, *args):
        pass

    def do_GET(self):
        self._senden(mit_inhalt=True)

    def do_HEAD(self):
        self._senden(mit_inhalt=False)

    def _senden(self, mit_inhalt):
        teile = self.path.split("?", 1)[0].split("/")
        pfad = None
        if len(teile) == 3 and teile[1] == "m":
            pfad = self.server.freigaben.get(teile[2])
        if not pfad or not os.path.isfile(pfad):
            self.send_error(404)
            return
        groesse = os.path.getsize(pfad)
        anfang, ende = 0, groesse - 1
        bereich = self.headers.get("Range")
        if bereich and bereich.startswith("bytes="):
            try:
                a, b = bereich[6:].split("-", 1)
                anfang = int(a) if a else max(0, groesse - int(b))
                ende = int(b) if a and b else groesse - 1
            except ValueError:
                self.send_error(416)
                return
            if anfang > ende or ende >= groesse:
                self.send_error(416)
                return
            self.send_response(206)
            self.send_header("Content-Range", f"bytes {anfang}-{ende}/{groesse}")
        else:
            self.send_response(200)
        typ = mimetypes.guess_type(str(pfad))[0] or "application/octet-stream"
        self.send_header("Content-Type", typ)
        self.send_header("Content-Length", str(ende - anfang + 1))
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("Cache-Control", "private, max-age=3600")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        if not mit_inhalt:
            return
        with open(pfad, "rb") as f:
            f.seek(anfang)
            rest = ende - anfang + 1
            while rest > 0:
                stueck = f.read(min(256 * 1024, rest))
                if not stueck:
                    break
                try:
                    self.wfile.write(stueck)
                except (BrokenPipeError, ConnectionResetError):
                    return
                rest -= len(stueck)


class _Server(socketserver.ThreadingMixIn, http.server.HTTPServer):
    daemon_threads = True
    allow_reuse_address = True


class MedienServer:
    def __init__(self):
        self._server = _Server(("127.0.0.1", 0), _Handler)
        self._server.freigaben = {}
        self._nach_pfad = {}
        self._lock = threading.Lock()
        self.port = self._server.server_address[1]
        self._thread = threading.Thread(target=self._server.serve_forever, name="vp4-medien", daemon=True)
        self._thread.start()

    def url(self, pfad) -> str:
        """Meldet eine Datei an und gibt ihre geheime Adresse zurück."""
        pfad = str(Path(pfad).resolve())
        with self._lock:
            schluessel = self._nach_pfad.get(pfad)
            if not schluessel:
                schluessel = secrets.token_hex(32)
                self._nach_pfad[pfad] = schluessel
                self._server.freigaben[schluessel] = pfad
        return f"http://127.0.0.1:{self.port}/m/{schluessel}"

    def stoppen(self):
        self._server.shutdown()
        self._server.server_close()
