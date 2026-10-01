#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
=====================================================================
 ereignisse.py - Warteschlange zur Oberfläche und ein Zeitplaner
=====================================================================
Die Oberfläche fragt alle 200 ms nach, was passiert ist
(VP4Api.ereignisse_holen). Netz-Threads, Versand und Hintergrund-
aufträge legen ihre Meldungen hier ab - niemand ausser der Oberfläche
selbst fasst die Oberfläche an. Das ist die Regel aus 4.x ("nur der
Hauptthread fasst Widgets an"), sinngemäss für den WebView.

Der Zeitplaner ist der Nachfolger von VP4App._spaeter(): EIN Thread für
alle wiederkehrenden Aufträge, jeder Auftrag hat eine Kennung, und
stoppen() beendet alle auf einmal. In 4.x blieben bei jedem Farbwechsel
Aufträge zurück, und gelaufene trugen sich nie aus - beides prüft ein
Test hier ausdrücklich.
=====================================================================
"""

import heapq
import itertools
import logging
import threading
import time
from collections import deque

log = logging.getLogger("vp4.ereignisse")


class Ereignisse:
    """Thread-sichere Warteschlange. Läuft sie über, fliegen die ältesten raus."""

    def __init__(self, hoechstens: int = 5000):
        self._schlange = deque(maxlen=hoechstens)
        self._lock = threading.Lock()

    def melden(self, typ: str, **daten):
        with self._lock:
            self._schlange.append(dict(daten, typ=typ))

    def holen(self, hoechstens: int = 300) -> list:
        with self._lock:
            aus = []
            while self._schlange and len(aus) < hoechstens:
                aus.append(self._schlange.popleft())
            return aus

    def __len__(self):
        with self._lock:
            return len(self._schlange)


class Zeitplaner:
    """Wiederkehrende und einmalige Aufträge in einem einzigen Thread."""

    def __init__(self):
        self._heap = []
        self._zaehler = itertools.count()
        self._auftraege = {}          # kennung -> (abstand oder None, funktion, name)
        self._lock = threading.Condition()
        self._laeuft = True
        self._thread = threading.Thread(target=self._schleife, name="vp4-zeitplaner", daemon=True)
        self._thread.start()

    def jede(self, sekunden: float, funktion, name: str = "") -> int:
        return self._anmelden(sekunden, funktion, name, wiederholen=True)

    def einmal(self, sekunden: float, funktion, name: str = "") -> int:
        return self._anmelden(sekunden, funktion, name, wiederholen=False)

    def _anmelden(self, sekunden, funktion, name, wiederholen):
        with self._lock:
            kennung = next(self._zaehler)
            self._auftraege[kennung] = (sekunden if wiederholen else None, funktion, name)
            heapq.heappush(self._heap, (time.monotonic() + sekunden, kennung))
            self._lock.notify()
            return kennung

    def abbestellen(self, kennung: int):
        with self._lock:
            self._auftraege.pop(kennung, None)

    def anzahl(self) -> int:
        """Wie viele Aufträge noch offen sind - gelaufene einmalige zählen nicht."""
        with self._lock:
            return len(self._auftraege)

    def stoppen(self, warten: float = 2.0):
        with self._lock:
            self._laeuft = False
            self._auftraege.clear()
            self._heap.clear()
            self._lock.notify()
        if threading.current_thread() is not self._thread:
            self._thread.join(warten)

    def _schleife(self):
        while True:
            with self._lock:
                while self._laeuft and (not self._heap or self._heap[0][0] > time.monotonic()):
                    wartezeit = (self._heap[0][0] - time.monotonic()) if self._heap else None
                    self._lock.wait(wartezeit)
                if not self._laeuft:
                    return
                _, kennung = heapq.heappop(self._heap)
                eintrag = self._auftraege.get(kennung)
                if eintrag is None:
                    continue             # abbestellt
                abstand, funktion, name = eintrag
                if abstand is None:
                    # Einmaliger Auftrag trägt sich VOR dem Laufen aus - sonst
                    # bliebe er als toter Eintrag stehen (der Fehler aus 4.x).
                    del self._auftraege[kennung]
                else:
                    heapq.heappush(self._heap, (time.monotonic() + abstand, kennung))
            try:
                funktion()
            except Exception:
                log.exception("Zeitgesteuerter Auftrag %s ist gescheitert", name or kennung)
