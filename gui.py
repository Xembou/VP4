#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
=====================================================================
 gui.py - die Oberfläche von VP4
=====================================================================
Gebaut mit CustomTkinter: moderne, abgerundete Bedienelemente mit
eingebautem Dark Mode, aber im Kern noch Tkinter - deshalb passt die
vorhandene Programmlogik unverändert darunter.

Aufbau:
  Sperrbildschirm   - fragt beim Start nach dem Master-Passwort
  VP4App            - das Hauptfenster mit Seitenleiste
  Seiten            - Verschlüsseln, Schlüssel, Signieren, Obsidian,
                      Chat, Einstellungen, Info

Die Seitenleiste links ersetzt die alte Tab-Leiste. Rechts wird immer
nur die gerade gewählte Seite angezeigt.
=====================================================================
"""

import os
import queue
import subprocess
import sys
import threading
import tkinter as tk
from datetime import datetime
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

import customtkinter as ctk

import chat
from kern import dateien
import discord_transport
from kern import speicher
import transport
from transport import ChatVermittler
from kern.krypto import (VERFAHREN, SCHLUESSEL_ARTEN, ClassicCiphers, ModernCrypto,
                    Pruefsummen, Signaturen)
from kern.speicher import (FalschesPasswortError, FriendsStore, KeyStore,
                      KeyStoreLockedError, ObsidianSync, passwort_staerke)


# =============================================================================
#  Farben und Maße
# =============================================================================

# CustomTkinter nimmt Farbpaare: (heller Modus, dunkler Modus).
FARBE = {
    "akzent":        ("#2563eb", "#3b82f6"),
    "akzent_hover":  ("#1d4ed8", "#60a5fa"),
    "flaeche":       ("#ffffff", "#1c1c22"),
    "flaeche_tief":  ("#f1f3f7", "#141418"),
    "leiste":        ("#e8ebf2", "#17171c"),
    "rand":          ("#d7dce6", "#2c2c35"),
    "text":          ("#12141a", "#f2f3f7"),
    "text_leise":    ("#5b6376", "#9aa1b1"),
    "gut":           ("#15803d", "#4ade80"),
    "warn":          ("#b45309", "#fbbf24"),
    "schlecht":      ("#b91c1c", "#f87171"),
}

# Zur Auswahl stehende Akzentfarben. Der erste Wert gilt im hellen, der
# zweite im dunklen Modus - dieselbe Ordnung wie in FARBE.
AKZENTE = {
    "blau":   (("#2563eb", "#3b82f6"), ("#1d4ed8", "#60a5fa")),
    "gruen":  (("#047857", "#10b981"), ("#065f46", "#34d399")),
    "lila":   (("#7c3aed", "#a78bfa"), ("#6d28d9", "#c4b5fd")),
    "orange": (("#c2410c", "#fb923c"), ("#9a3412", "#fdba74")),
    "rot":    (("#be123c", "#fb7185"), ("#9f1239", "#fda4af")),
}

# Wie die Farben in der Oberfläche heissen.
AKZENT_NAMEN = {"blau": "Blau", "gruen": "Grün", "lila": "Lila",
                "orange": "Orange", "rot": "Rot"}


def akzent_setzen(name: str):
    """Legt die Akzentfarbe fest.

    Muss VOR dem Aufbau der Fenster laufen: CustomTkinter liest die Farbe
    beim Erzeugen jedes Bedienelements einmal aus und merkt sie sich. Ein
    Wechsel im laufenden Betrieb würde nur die Hälfte der Oberfläche
    erwischen - deshalb greift die Einstellung erst beim nächsten Start.
    """
    akzent, akzent_hover = AKZENTE.get(name, AKZENTE["blau"])
    FARBE["akzent"] = akzent
    FARBE["akzent_hover"] = akzent_hover


RADIUS = 10
SCHRIFT = "Segoe UI"        # unter Windows immer vorhanden


def schrift(groesse=13, fett=False):
    return ctk.CTkFont(family=SCHRIFT, size=groesse,
                       weight="bold" if fett else "normal")


def knopf(eltern, text, befehl, art="primaer", breite=150, hoehe=40, **rest):
    """Baut einen Knopf im VP4-Stil.

    Vorher stand dasselbe Rezept aus acht Angaben rund dreissig Mal
    wortgleich im Code. Jede Farbänderung musste man dreissig Mal
    nachziehen, und beim ersten vergessenen Mal sieht die Oberfläche
    zusammengeflickt aus.

    art:  "primaer" - gefüllt, für die Haupthandlung einer Seite
          "zweit"   - Umriss in der Akzentfarbe
          "leise"   - Umriss in Grau, für Nebensächliches
    """
    stil = {
        "primaer": dict(fg_color=FARBE["akzent"], hover_color=FARBE["akzent_hover"],
                        text_color="#ffffff", border_width=0,
                        font=schrift(13, True)),
        "zweit": dict(fg_color="transparent", hover_color=FARBE["flaeche_tief"],
                      text_color=FARBE["akzent"], border_width=1,
                      border_color=FARBE["akzent"], font=schrift(13, True)),
        "leise": dict(fg_color="transparent", hover_color=FARBE["flaeche_tief"],
                      text_color=FARBE["text_leise"], border_width=1,
                      border_color=FARBE["rand"], font=schrift(12)),
    }[art]
    stil.update(rest)
    return ctk.CTkButton(eltern, text=text, command=befehl, width=breite,
                         height=hoehe, corner_radius=RADIUS, **stil)


# =============================================================================
#  Sperrbildschirm - der Einstieg ins Programm
# =============================================================================

class Sperrbildschirm(ctk.CTk):
    """Fragt beim Start nach dem Master-Passwort.

    Beim allerersten Start wird stattdessen eines festgelegt. Das Ergebnis
    steht danach in self.keystore - oder das Programm wird beendet.
    """

    def __init__(self, keystore: KeyStore, ersteinrichtung: bool):
        super().__init__()
        self.keystore = keystore
        self.ersteinrichtung = ersteinrichtung
        self.erfolgreich = False

        self.title("VP4")
        # Beim ersten Start ist mehr unterzubringen: zweites Eingabefeld,
        # Stärkeanzeige und der Hinweiskasten. Mit einer festen Höhe für
        # beide Fälle wurde der Knopf ganz unten abgeschnitten.
        self.geometry("470x700" if ersteinrichtung else "470x520")
        self.resizable(False, False)
        self.configure(fg_color=FARBE["flaeche_tief"])

        self._bauen()
        self.eingabe.focus_set()
        self.protocol("WM_DELETE_WINDOW", self._abbrechen)

    def _bauen(self):
        rahmen = ctk.CTkFrame(self, fg_color=FARBE["flaeche"], corner_radius=RADIUS * 2)
        rahmen.pack(fill="both", expand=True, padx=24, pady=24)

        ctk.CTkLabel(rahmen, text="🔐", font=ctk.CTkFont(size=52)).pack(pady=(38, 6))
        ctk.CTkLabel(rahmen, text="Verschlüsselungs Programm 4.0",
                     font=schrift(19, True), text_color=FARBE["text"]).pack()

        if self.ersteinrichtung:
            unter = "Lege ein Master-Passwort fest"
        else:
            unter = "Gib dein Master-Passwort ein"
        ctk.CTkLabel(rahmen, text=unter, font=schrift(13),
                     text_color=FARBE["text_leise"]).pack(pady=(4, 22))

        self.eingabe = ctk.CTkEntry(rahmen, show="•", width=300, height=42,
                                    corner_radius=RADIUS, font=schrift(14),
                                    placeholder_text="Master-Passwort",
                                    border_color=FARBE["rand"])
        self.eingabe.pack(pady=(0, 10))
        self.eingabe.bind("<Return>", lambda e: self._bestaetigen())
        self.eingabe.bind("<KeyRelease>", self._staerke_aktualisieren)

        # Zweites Feld und Stärkeanzeige nur beim ersten Start
        if self.ersteinrichtung:
            self.eingabe2 = ctk.CTkEntry(rahmen, show="•", width=300, height=42,
                                         corner_radius=RADIUS, font=schrift(14),
                                         placeholder_text="Passwort wiederholen",
                                         border_color=FARBE["rand"])
            self.eingabe2.pack(pady=(0, 10))
            self.eingabe2.bind("<Return>", lambda e: self._bestaetigen())

            self.staerke_balken = ctk.CTkProgressBar(rahmen, width=300, height=6,
                                                     corner_radius=3)
            self.staerke_balken.pack(pady=(4, 2))
            self.staerke_balken.set(0)
            self.staerke_text = ctk.CTkLabel(rahmen, text=" ", font=schrift(11),
                                             text_color=FARBE["text_leise"])
            self.staerke_text.pack()

            warnung = (
                "Merk dir dieses Passwort gut.\n\n"
                "Es wird nirgends gespeichert. Wenn du es vergisst, sind alle "
                "gespeicherten Schlüssel endgültig verloren – es gibt keine "
                "Wiederherstellung und keine Hintertür."
            )
            kasten = ctk.CTkFrame(rahmen, fg_color=FARBE["flaeche_tief"],
                                  corner_radius=RADIUS)
            kasten.pack(fill="x", padx=26, pady=(14, 6))
            ctk.CTkLabel(kasten, text=warnung, font=schrift(11), justify="left",
                         wraplength=330, text_color=FARBE["warn"]).pack(padx=14, pady=12)

        self.meldung = ctk.CTkLabel(rahmen, text=" ", font=schrift(12),
                                    text_color=FARBE["schlecht"], wraplength=300)
        self.meldung.pack(pady=(6, 4))

        knopf_text = "Festlegen und starten" if self.ersteinrichtung else "Entsperren"
        self.knopf = ctk.CTkButton(rahmen, text=knopf_text, width=300, height=44,
                                   corner_radius=RADIUS, font=schrift(14, True),
                                   fg_color=FARBE["akzent"], text_color="#ffffff",
                                   hover_color=FARBE["akzent_hover"],
                                   command=self._bestaetigen)
        self.knopf.pack(pady=(4, 10))

        if not self.ersteinrichtung:
            ctk.CTkButton(rahmen, text="Passwort vergessen?", width=300, height=30,
                          corner_radius=RADIUS, font=schrift(11),
                          fg_color="transparent", hover_color=FARBE["flaeche_tief"],
                          text_color=FARBE["text_leise"],
                          command=self._vergessen).pack()

    def _staerke_aktualisieren(self, _event=None):
        if not self.ersteinrichtung:
            return
        stufe, text = passwort_staerke(self.eingabe.get())
        self.staerke_balken.set(stufe / 4)
        farben = [FARBE["schlecht"], FARBE["schlecht"], FARBE["warn"],
                  FARBE["gut"], FARBE["gut"]]
        self.staerke_balken.configure(progress_color=farben[stufe])
        self.staerke_text.configure(text=f"Passwortstärke: {text}")

    def _bestaetigen(self):
        pw = self.eingabe.get()
        if not pw:
            self.meldung.configure(text="Bitte ein Passwort eingeben.")
            return

        if self.ersteinrichtung:
            if pw != self.eingabe2.get():
                self.meldung.configure(text="Die beiden Passwörter sind nicht gleich.")
                return
            if len(pw) < 8:
                self.meldung.configure(
                    text="Bitte mindestens 8 Zeichen – kürzer ist zu leicht zu erraten.")
                return
            if not messagebox.askyesno(
                    "Wirklich festlegen?",
                    "Hast du dir das Passwort wirklich gemerkt?\n\n"
                    "Es lässt sich nicht wiederherstellen. Wenn du es vergisst, "
                    "musst du VP4 neu einrichten und alle gespeicherten Schlüssel "
                    "sind weg.",
                    parent=self):
                return
            try:
                self.keystore.create(pw)
            except Exception as e:
                self.meldung.configure(text=f"Konnte nicht angelegt werden: {e}")
                return
            self.erfolgreich = True
            self.destroy()
            return

        # Normales Entsperren. Das Ableiten dauert bewusst einen Moment -
        # Argon2id braucht absichtlich 64 MiB Arbeitsspeicher und mehrere
        # Durchgänge -, deshalb läuft es in einem eigenen Thread, damit das
        # Fenster nicht einfriert.
        self.knopf.configure(state="disabled", text="Wird geprüft …")
        self.meldung.configure(text=" ")

        def pruefen():
            try:
                self.keystore.unlock(pw)
                self.after(0, self._entsperrt)
            except FalschesPasswortError:
                self.after(0, lambda: self._fehlgeschlagen("Falsches Master-Passwort."))
            except Exception as e:
                self.after(0, lambda: self._fehlgeschlagen(str(e)))

        threading.Thread(target=pruefen, daemon=True).start()

    def _entsperrt(self):
        self.erfolgreich = True
        self.destroy()

    def _fehlgeschlagen(self, text):
        self.knopf.configure(state="normal", text="Entsperren")
        self.meldung.configure(text=text)
        self.eingabe.delete(0, "end")
        self.eingabe.focus_set()

    def _vergessen(self):
        messagebox.showinfo(
            "Passwort vergessen",
            "Das Master-Passwort wird nirgends gespeichert – auch nicht "
            "verschlüsselt. Deshalb kann es niemand wiederherstellen, auch "
            "dieses Programm nicht.\n\n"
            "Wenn du es vergessen hast, bleibt nur: den Ordner 'vp4_daten' "
            "löschen und VP4 neu einrichten. Alle gespeicherten Schlüssel sind "
            "dann verloren.\n\n"
            "Das klingt hart, ist aber der Grund, warum der Speicher überhaupt "
            "etwas taugt: Jede Wiederherstellungsmöglichkeit wäre auch ein Weg "
            "für jemand anderen.",
            parent=self)

    def _abbrechen(self):
        self.erfolgreich = False
        self.destroy()


# =============================================================================
#  Hauptfenster
# =============================================================================

class VP4App(ctk.CTk):

    SEITEN = [
        ("krypto",       "🔒", "Verschlüsseln"),
        ("dateien",      "📁", "Dateien"),
        ("schluessel",   "🔑", "Schlüssel"),
        ("signieren",    "✍",  "Signieren"),
        ("obsidian",     "📓", "Obsidian"),
        ("chat",         "💬", "Chat"),
        ("einstellungen", "⚙", "Einstellungen"),
        ("info",         "ℹ",  "Info"),
    ]

    def __init__(self, keystore: KeyStore, config: dict):
        super().__init__()
        self.keystore = keystore
        self.config_data = config
        self.my_id = config["my_id"]

        self.friends = FriendsStore(speicher.FRIENDS_FILE)
        self.gruppen = speicher.GruppenStore(speicher.GROUPS_FILE)
        self.ereignisse = queue.Queue()
        self.network = ChatVermittler(
            self.my_id, self.friends, self.ereignisse,
            modus=config.get("transport_modus", "lan"),
            discord_cfg=discord_transport.discord_config_laden(),
            gruppen=self.gruppen,
            anzeigename=config.get("anzeigename", ""))
        self.chat_verlauf = {}          # {freund_id: [zeilen]}
        self.aktiver_chat = None
        self.seiten = {}

        # Für die Dateiseite: es läuft immer höchstens ein Auftrag.
        self.auftrag_laeuft = False
        self.auftrag_abbruch = None
        # Wird wahr, wenn der Benutzer "Sperren" gedrückt hat. starten()
        # baut daraufhin den Sperrbildschirm neu auf - siehe unten.
        self.wieder_sperren = False
        # Die laufenden Zeitgeber, damit sie beim Schliessen abbestellt
        # werden können - siehe _spaeter() und _zeitgeber_stoppen().
        self._zeitgeber = []

        self.title("Verschlüsselungs Programm 4.0")
        self.geometry("1080x720")
        self.minsize(900, 620)
        self.configure(fg_color=FARBE["flaeche_tief"])

        self.aktive_seite = "krypto"
        self._bauen()
        self._seite_zeigen("krypto")

        if self.config_data.get("chat_aktiv", True):
            self.network.start()
        else:
            # Sonst bliebe unten "Chat: startet …" stehen und man wartet auf
            # etwas, das gar nicht kommt.
            self.chat_status.configure(text="Chat: aus",
                                       text_color=FARBE["text_leise"])

        if getattr(self.keystore, "migriert", False):
            self.status("Der Schlüsselspeicher wurde auf das neue Format "
                        "umgestellt (Argon2id).", "gut")

        self.protocol("WM_DELETE_WINDOW", self._schliessen)
        self._spaeter(300, self._ereignisse_pruefen)
        self._spaeter(1500, self._freunde_aktualisieren)

    def _spaeter(self, ms, funktion):
        """Wie after(), merkt sich den Auftrag aber zum Abbestellen.

        Ein gelaufener Auftrag trägt sich selbst wieder aus. Ohne das stand
        seine Kennung für immer in der Liste, obwohl es nichts mehr
        abzubestellen gab: Die Ereignisschleife meldet sich alle 300 ms neu
        an, das sind rund 12.000 tote Einträge pro Stunde. Aufgefallen ist es
        an einem Test, der mal durchlief und mal nicht - je nachdem, ob
        zufällig gerade ein Zeitgeber gefeuert hatte.
        """
        kennung = None

        def ausfuehren():
            try:
                self._zeitgeber.remove(kennung)
            except ValueError:
                pass
            funktion()

        kennung = self.after(ms, ausfuehren)
        self._zeitgeber.append(kennung)
        return kennung

    def _zeitgeber_stoppen(self):
        """Bestellt alle wiederkehrenden Aufträge ab.

        Muss vor jedem destroy() laufen. Sonst feuern sie noch einmal auf
        ein Fenster, das es nicht mehr gibt, und Tk gibt Fehlermeldungen
        aus - in der .exe ohne Konsole sieht die zwar niemand, aber
        aufgeräumt ist es trotzdem nicht.
        """
        for kennung in self._zeitgeber:
            try:
                self.after_cancel(kennung)
            except Exception:
                pass
        self._zeitgeber.clear()

    def _oberflaeche_neu_bauen(self):
        """Wirft den gesamten Inhalt weg und baut ihn noch einmal auf.

        Nötig nach einem Farbwechsel: CustomTkinter liest die Farbe beim
        Erzeugen jedes Bedienelements einmal aus und merkt sie sich. Im
        Betrieb umzufärben würde nur die Hälfte der Oberfläche erwischen.

        Das Fenster selbst bleibt stehen - Grösse, Position, Netzwerk,
        Schlüsselspeicher und Chatverlauf bleiben unangetastet. Nur die
        Bedienelemente sind danach neue Objekte.
        """
        vorherige_seite = self.aktive_seite
        letzte_meldung = self.status_text.cget("text")

        self._zeitgeber_stoppen()
        for kind in self.winfo_children():
            kind.destroy()
        self.seiten = {}

        self.configure(fg_color=FARBE["flaeche_tief"])
        self._bauen()
        self._seite_zeigen(vorherige_seite)

        if not self.config_data.get("chat_aktiv", True):
            self.chat_status.configure(text="Chat: aus",
                                       text_color=FARBE["text_leise"])
        elif self.network.server_laeuft:
            self.chat_status.configure(text="Chat: bereit",
                                       text_color=FARBE["gut"])
        self.status_text.configure(text=letzte_meldung)

        # Der Chatverlauf lebt im Arbeitsspeicher weiter, die Textfelder
        # sind aber neu und leer.
        if self.aktiver_chat:
            self._chat_oeffnen(self.aktiver_chat)

        self._spaeter(300, self._ereignisse_pruefen)
        self._spaeter(1500, self._freunde_aktualisieren)

    # ------------------------------------------------------------- Aufbau

    def _bauen(self):
        self.grid_columnconfigure(1, weight=1)
        self.grid_rowconfigure(0, weight=1)

        self._seitenleiste_bauen()

        self.inhalt = ctk.CTkFrame(self, fg_color="transparent")
        self.inhalt.grid(row=0, column=1, sticky="nsew", padx=(0, 16), pady=16)
        self.inhalt.grid_columnconfigure(0, weight=1)
        self.inhalt.grid_rowconfigure(0, weight=1)

        self._statusleiste_bauen()

        for name, _, _ in self.SEITEN:
            seite = ctk.CTkFrame(self.inhalt, fg_color=FARBE["flaeche"],
                                 corner_radius=RADIUS * 1.4)
            seite.grid(row=0, column=0, sticky="nsew")
            seite.grid_remove()
            self.seiten[name] = seite
            getattr(self, f"_seite_{name}")(seite)

    def _seitenleiste_bauen(self):
        leiste = ctk.CTkFrame(self, width=210, corner_radius=0,
                              fg_color=FARBE["leiste"])
        leiste.grid(row=0, column=0, sticky="nsw")
        leiste.grid_propagate(False)
        leiste.grid_rowconfigure(len(self.SEITEN) + 1, weight=1)

        kopf = ctk.CTkFrame(leiste, fg_color="transparent")
        kopf.grid(row=0, column=0, sticky="ew", padx=18, pady=(22, 18))
        ctk.CTkLabel(kopf, text="🔐  VP4", font=schrift(21, True),
                     text_color=FARBE["akzent"]).pack(anchor="w")
        ctk.CTkLabel(kopf, text="Verschlüsseln & Chatten", font=schrift(10),
                     text_color=FARBE["text_leise"]).pack(anchor="w")

        self.nav_knoepfe = {}
        for i, (name, symbol, beschriftung) in enumerate(self.SEITEN, start=1):
            knopf = ctk.CTkButton(
                leiste, text=f"  {symbol}   {beschriftung}", anchor="w",
                height=42, corner_radius=RADIUS, font=schrift(13),
                fg_color="transparent", text_color=FARBE["text"],
                hover_color=FARBE["flaeche"],
                command=lambda n=name: self._seite_zeigen(n))
            knopf.grid(row=i, column=0, sticky="ew", padx=12, pady=2)
            self.nav_knoepfe[name] = knopf

        # Ganz unten: Design-Umschalter
        unten = ctk.CTkFrame(leiste, fg_color="transparent")
        unten.grid(row=len(self.SEITEN) + 2, column=0, sticky="ew", padx=18, pady=18)

        self.design_schalter = ctk.CTkSwitch(
            unten, text="Dark Mode", font=schrift(12),
            command=self._design_umschalten,
            progress_color=FARBE["akzent"])
        self.design_schalter.pack(anchor="w")
        if ctk.get_appearance_mode() == "Dark":
            self.design_schalter.select()

        self.sperr_knopf = ctk.CTkButton(
            unten, text="🔒  Sperren", height=34, corner_radius=RADIUS,
            font=schrift(12), fg_color="transparent",
            border_width=1, border_color=FARBE["rand"],
            text_color=FARBE["text_leise"], hover_color=FARBE["flaeche"],
            command=self._sperren)
        self.sperr_knopf.pack(anchor="w", fill="x", pady=(10, 0))

    def _statusleiste_bauen(self):
        """Die Zeile ganz unten. Hier landen Netzwerkmeldungen.

        Vorher gingen die über print() ins Leere - in einem Fenster ohne
        Konsole sieht die niemand, und in der .exe gibt es gar keine.
        """
        self.statusleiste = ctk.CTkFrame(self, height=30, corner_radius=0,
                                         fg_color=FARBE["leiste"])
        self.statusleiste.grid(row=1, column=0, columnspan=2, sticky="ew")
        self.statusleiste.grid_propagate(False)

        self.status_text = ctk.CTkLabel(self.statusleiste, text="Bereit.",
                                        font=schrift(11),
                                        text_color=FARBE["text_leise"], anchor="w")
        self.status_text.pack(side="left", padx=16)

        self.chat_status = ctk.CTkLabel(self.statusleiste, text="Chat: startet …",
                                        font=schrift(11),
                                        text_color=FARBE["text_leise"], anchor="e")
        self.chat_status.pack(side="right", padx=16)

    def status(self, text, art="normal"):
        """Zeigt eine Meldung in der Statusleiste."""
        farbe = {"normal": FARBE["text_leise"], "gut": FARBE["gut"],
                 "warn": FARBE["warn"], "fehler": FARBE["schlecht"]}[art]
        zeit = datetime.now().strftime("%H:%M")
        self.status_text.configure(text=f"[{zeit}]  {text}", text_color=farbe)

    def _seite_zeigen(self, name):
        self.aktive_seite = name
        for n, seite in self.seiten.items():
            seite.grid_remove()
            self.nav_knoepfe[n].configure(fg_color="transparent",
                                          text_color=FARBE["text"])
        self.seiten[name].grid()
        self.nav_knoepfe[name].configure(fg_color=FARBE["akzent"],
                                         text_color="#ffffff")

    def _design_umschalten(self):
        modus = "dark" if self.design_schalter.get() else "light"
        ctk.set_appearance_mode(modus)
        self.config_data["design"] = modus
        speicher.save_config(self.config_data)
        self._treeview_stil()

    # =========================================================== Seite: Krypto

    def _seite_krypto(self, seite):
        # Zeilenaufteilung (jede Zeile genau einmal belegen, sonst legen sich
        # Beschriftungen über die Felder darunter):
        #   0 Überschrift · 1 Verfahrenswahl · 2 Schlüsselblock
        #   3 "Text" · 4 Eingabefeld · 5 Knopfreihe
        #   6 "Ergebnis" · 7 Ausgabefeld
        seite.grid_columnconfigure(0, weight=1)
        seite.grid_rowconfigure(4, weight=1)
        seite.grid_rowconfigure(7, weight=1)

        kopf = ctk.CTkFrame(seite, fg_color="transparent")
        kopf.grid(row=0, column=0, sticky="ew", padx=26, pady=(22, 6))
        ctk.CTkLabel(kopf, text="Verschlüsseln & Entschlüsseln",
                     font=schrift(22, True), text_color=FARBE["text"]).pack(anchor="w")

        # --- Verfahrenswahl -------------------------------------------------
        wahl = ctk.CTkFrame(seite, fg_color=FARBE["flaeche_tief"], corner_radius=RADIUS)
        wahl.grid(row=1, column=0, sticky="ew", padx=26, pady=(6, 4))
        wahl.grid_columnconfigure(1, weight=1)

        ctk.CTkLabel(wahl, text="Verfahren", font=schrift(12, True),
                     text_color=FARBE["text_leise"]).grid(
                         row=0, column=0, padx=(16, 10), pady=14, sticky="w")

        self.verfahren_wahl = ctk.CTkOptionMenu(
            wahl, values=list(VERFAHREN), width=210, height=34,
            corner_radius=RADIUS, font=schrift(13),
            fg_color=FARBE["akzent"], text_color="#ffffff", button_color=FARBE["akzent"],
            button_hover_color=FARBE["akzent_hover"],
            command=self._verfahren_gewechselt)
        self.verfahren_wahl.grid(row=0, column=1, sticky="w", pady=14)

        self.sicherheits_marke = ctk.CTkLabel(wahl, text="", font=schrift(11, True))
        self.sicherheits_marke.grid(row=0, column=2, padx=14)

        self.verfahren_hinweis = ctk.CTkLabel(
            wahl, text="", font=schrift(11), text_color=FARBE["text_leise"],
            justify="left", wraplength=760, anchor="w")
        self.verfahren_hinweis.grid(row=1, column=0, columnspan=3,
                                    padx=16, pady=(0, 14), sticky="w")

        # --- Schlüsselzeile -------------------------------------------------
        schluessel = ctk.CTkFrame(seite, fg_color="transparent")
        schluessel.grid(row=2, column=0, sticky="ew", padx=26, pady=(8, 4))
        schluessel.grid_columnconfigure(0, weight=1)

        self.schluessel_beschriftung = ctk.CTkLabel(
            schluessel, text="Schlüssel", font=schrift(12, True),
            text_color=FARBE["text_leise"], anchor="w")
        self.schluessel_beschriftung.grid(row=0, column=0, sticky="w", pady=(0, 4))

        self.schluessel_feld = ctk.CTkTextbox(
            schluessel, height=58, corner_radius=RADIUS, font=schrift(12),
            border_width=1, border_color=FARBE["rand"],
            fg_color=FARBE["flaeche_tief"])
        self.schluessel_feld.grid(row=1, column=0, sticky="ew")

        knopfreihe = ctk.CTkFrame(schluessel, fg_color="transparent")
        knopfreihe.grid(row=1, column=1, sticky="ns", padx=(10, 0))
        self.erzeugen_knopf = ctk.CTkButton(
            knopfreihe, text="Erzeugen", width=110, height=26, corner_radius=8,
            font=schrift(11), fg_color=FARBE["akzent"], text_color="#ffffff",
            hover_color=FARBE["akzent_hover"], command=self._schluessel_erzeugen)
        self.erzeugen_knopf.pack(pady=(0, 4))
        ctk.CTkButton(knopfreihe, text="Aus Speicher", width=110, height=26,
                      corner_radius=8, font=schrift(11), fg_color="transparent",
                      border_width=1, border_color=FARBE["rand"],
                      text_color=FARBE["text"], hover_color=FARBE["flaeche_tief"],
                      command=self._schluessel_laden).pack()

        # --- Ein- und Ausgabe -----------------------------------------------
        ctk.CTkLabel(seite, text="Text", font=schrift(12, True),
                     text_color=FARBE["text_leise"], anchor="w").grid(
                         row=3, column=0, sticky="w", padx=26, pady=(10, 2))

        self.eingabe_feld = ctk.CTkTextbox(
            seite, corner_radius=RADIUS, font=schrift(13), border_width=1,
            border_color=FARBE["rand"], fg_color=FARBE["flaeche_tief"])
        self.eingabe_feld.grid(row=4, column=0, sticky="nsew", padx=26, pady=(0, 10))

        knoepfe = ctk.CTkFrame(seite, fg_color="transparent")
        knoepfe.grid(row=5, column=0, sticky="ew", padx=26, pady=4)
        ctk.CTkButton(knoepfe, text="🔒  Verschlüsseln", height=40, width=170,
                      corner_radius=RADIUS, font=schrift(13, True),
                      fg_color=FARBE["akzent"], text_color="#ffffff", hover_color=FARBE["akzent_hover"],
                      command=lambda: self._rechnen(True)).pack(side="left")
        ctk.CTkButton(knoepfe, text="🔓  Entschlüsseln", height=40, width=170,
                      corner_radius=RADIUS, font=schrift(13, True),
                      fg_color="transparent", border_width=1,
                      border_color=FARBE["akzent"], text_color=FARBE["akzent"],
                      hover_color=FARBE["flaeche_tief"],
                      command=lambda: self._rechnen(False)).pack(side="left", padx=8)
        ctk.CTkButton(knoepfe, text="Tauschen ⇅", height=40, width=110,
                      corner_radius=RADIUS, font=schrift(12),
                      fg_color="transparent", border_width=1,
                      border_color=FARBE["rand"], text_color=FARBE["text"],
                      hover_color=FARBE["flaeche_tief"],
                      command=self._tauschen).pack(side="left")
        ctk.CTkButton(knoepfe, text="Leeren", height=40, width=90,
                      corner_radius=RADIUS, font=schrift(12),
                      fg_color="transparent", border_width=1,
                      border_color=FARBE["rand"], text_color=FARBE["text_leise"],
                      hover_color=FARBE["flaeche_tief"],
                      command=self._leeren).pack(side="left", padx=8)
        ctk.CTkButton(knoepfe, text="📋 Kopieren", height=40, width=120,
                      corner_radius=RADIUS, font=schrift(12),
                      fg_color="transparent", border_width=1,
                      border_color=FARBE["rand"], text_color=FARBE["text"],
                      hover_color=FARBE["flaeche_tief"],
                      command=self._ausgabe_kopieren).pack(side="right")

        ctk.CTkLabel(seite, text="Ergebnis", font=schrift(12, True),
                     text_color=FARBE["text_leise"], anchor="w").grid(
                         row=6, column=0, sticky="w", padx=26, pady=(10, 2))
        self.ausgabe_feld = ctk.CTkTextbox(
            seite, corner_radius=RADIUS, font=schrift(13), border_width=1,
            border_color=FARBE["rand"], fg_color=FARBE["flaeche_tief"])
        self.ausgabe_feld.grid(row=7, column=0, sticky="nsew", padx=26, pady=(0, 22))

        self._verfahren_gewechselt(self.verfahren_wahl.get())

    def _verfahren_gewechselt(self, name):
        info = VERFAHREN[name]
        self.verfahren_hinweis.configure(text=info["hinweis"])

        if info["art"] == "sicher":
            self.sicherheits_marke.configure(text="●  sicher", text_color=FARBE["gut"])
        else:
            self.sicherheits_marke.configure(text="●  nur zum Spielen",
                                             text_color=FARBE["warn"])

        beschriftung, erzeugbar = SCHLUESSEL_ARTEN[info["key"]]
        self.schluessel_beschriftung.configure(text=beschriftung)
        self.erzeugen_knopf.configure(state="normal" if erzeugbar else "disabled")

        if info["key"] == "keiner":
            self.schluessel_feld.delete("1.0", "end")
            self.schluessel_feld.configure(state="disabled")
        else:
            self.schluessel_feld.configure(state="normal")

    def _schluessel_erzeugen(self):
        art = VERFAHREN[self.verfahren_wahl.get()]["key"]
        if art == "aes":
            wert, typ = ModernCrypto.generate_aes_key(), "AES"
        elif art == "chacha":
            wert, typ = ModernCrypto.generate_chacha_key(), "ChaCha20"
        elif art == "rsa":
            privat, oeffentlich = ModernCrypto.generate_rsa_keypair()
            self._rsa_paar_zeigen(privat, oeffentlich)
            return
        else:
            return
        self.schluessel_feld.delete("1.0", "end")
        self.schluessel_feld.insert("1.0", wert)
        self.status("Neuer Schlüssel erzeugt.", "gut")
        self._schluessel_speichern_anbieten(typ, wert)

    def _rsa_paar_zeigen(self, privat, oeffentlich):
        fenster = ctk.CTkToplevel(self)
        fenster.title("RSA-Schlüsselpaar")
        fenster.geometry("640x560")
        fenster.transient(self)
        fenster.after(100, fenster.grab_set)

        ctk.CTkLabel(fenster, text="Neues RSA-Schlüsselpaar", font=schrift(17, True)).pack(pady=(18, 4))
        ctk.CTkLabel(fenster,
                     text="Den öffentlichen Schlüssel gibst du weiter – damit kann dir\n"
                          "jemand etwas verschlüsseln. Den privaten behältst du für dich.",
                     font=schrift(11), text_color=FARBE["text_leise"],
                     justify="center").pack(pady=(0, 12))

        for titel, wert, typ in [("Öffentlich (weitergeben)", oeffentlich, "RSA-pub"),
                                 ("Privat (geheim halten!)", privat, "RSA-priv")]:
            ctk.CTkLabel(fenster, text=titel, font=schrift(12, True),
                         anchor="w").pack(fill="x", padx=22, pady=(8, 2))
            feld = ctk.CTkTextbox(fenster, height=150, font=ctk.CTkFont(family="Consolas", size=10))
            feld.pack(fill="x", padx=22)
            feld.insert("1.0", wert)
            feld.configure(state="disabled")

        reihe = ctk.CTkFrame(fenster, fg_color="transparent")
        reihe.pack(pady=16)
        ctk.CTkButton(reihe, text="Beide im Speicher sichern", width=200,
                      fg_color=FARBE["akzent"], hover_color=FARBE["akzent_hover"],
                      command=lambda: (self._rsa_paar_sichern(oeffentlich, privat),
                                       fenster.destroy())).pack(side="left", padx=6)
        ctk.CTkButton(reihe, text="Schließen", width=120, fg_color="transparent",
                      border_width=1, border_color=FARBE["rand"],
                      text_color=FARBE["text"],
                      command=fenster.destroy).pack(side="left", padx=6)

    def _rsa_paar_sichern(self, oeffentlich, privat):
        name = self._text_abfragen("Name", "Unter welchem Namen sollen die beiden "
                                           "Schlüssel gespeichert werden?")
        if not name:
            return
        try:
            self.keystore.add_key(f"{name} (öffentlich)", "RSA-pub", oeffentlich)
            self.keystore.add_key(f"{name} (privat)", "RSA-priv", privat)
            self._schluessel_liste_aktualisieren()
            self.status(f"RSA-Paar '{name}' gespeichert.", "gut")
        except Exception as e:
            messagebox.showerror("Fehler", str(e), parent=self)

    def _schluessel_speichern_anbieten(self, typ, wert):
        if not messagebox.askyesno("Schlüssel sichern?",
                                   "Soll der neue Schlüssel im Schlüsselspeicher "
                                   "gesichert werden?\n\nOhne Sicherung ist er weg, "
                                   "sobald du ihn aus dem Feld löschst.",
                                   parent=self):
            return
        name = self._text_abfragen("Name", "Unter welchem Namen?")
        if not name:
            return
        try:
            self.keystore.add_key(name, typ, wert)
            self._schluessel_liste_aktualisieren()
            self.status(f"Schlüssel '{name}' gespeichert.", "gut")
        except Exception as e:
            messagebox.showerror("Fehler", str(e), parent=self)

    def _schluessel_laden(self):
        eintraege = self.keystore.list_keys()
        if not eintraege:
            messagebox.showinfo("Leer", "Im Schlüsselspeicher ist noch nichts.",
                                parent=self)
            return
        namen = [f"{k['label']}  ({k['typ']})" for k in eintraege]
        gewaehlt = self._auswahl_abfragen("Schlüssel wählen",
                                          "Welchen Schlüssel einsetzen?", namen)
        if gewaehlt is None:
            return
        self.schluessel_feld.configure(state="normal")
        self.schluessel_feld.delete("1.0", "end")
        self.schluessel_feld.insert("1.0", eintraege[gewaehlt]["wert"])
        self.status(f"Schlüssel '{eintraege[gewaehlt]['label']}' eingesetzt.")

    def _rechnen(self, verschluesseln):
        name = self.verfahren_wahl.get()
        info = VERFAHREN[name]
        text = self.eingabe_feld.get("1.0", "end-1c")
        schluessel = self.schluessel_feld.get("1.0", "end-1c").strip()

        if not text:
            self.status("Bitte zuerst einen Text eingeben.", "warn")
            return
        try:
            fn = info["enc"] if verschluesseln else info["dec"]
            ergebnis = fn(text, schluessel)
        except ValueError as e:
            self.status(str(e), "fehler")
            messagebox.showerror("Das hat nicht geklappt", str(e), parent=self)
            return
        except Exception as e:
            self.status(f"Unerwarteter Fehler: {e}", "fehler")
            messagebox.showerror("Unerwarteter Fehler", str(e), parent=self)
            return

        self.ausgabe_feld.delete("1.0", "end")
        self.ausgabe_feld.insert("1.0", ergebnis)
        self.status(f"{name}: {'verschlüsselt' if verschluesseln else 'entschlüsselt'} "
                    f"({len(ergebnis)} Zeichen).", "gut")

    def _tauschen(self):
        """Ergebnis nach oben holen - praktisch zum direkten Zurückrechnen."""
        ergebnis = self.ausgabe_feld.get("1.0", "end-1c")
        eingabe = self.eingabe_feld.get("1.0", "end-1c")
        self.eingabe_feld.delete("1.0", "end")
        self.eingabe_feld.insert("1.0", ergebnis)
        self.ausgabe_feld.delete("1.0", "end")
        self.ausgabe_feld.insert("1.0", eingabe)

    def _leeren(self):
        self.eingabe_feld.delete("1.0", "end")
        self.ausgabe_feld.delete("1.0", "end")
        self.status("Felder geleert.")

    def _ausgabe_kopieren(self):
        text = self.ausgabe_feld.get("1.0", "end-1c")
        if not text:
            self.status("Es gibt noch kein Ergebnis zum Kopieren.", "warn")
            return
        self.clipboard_clear()
        self.clipboard_append(text)
        self.status("Ergebnis in die Zwischenablage kopiert.", "gut")

    # ------------------------------------------------------ Seite: Dateien

    def _seite_dateien(self, seite):
        """Dateien und ganze Ordner verschlüsseln.

        Achtung beim Ändern: jede Zeile im Raster genau einmal belegen,
        sonst legen sich Beschriftungen über die Felder.
        """
        seite.grid_columnconfigure(0, weight=1)
        seite.grid_rowconfigure(5, weight=1)

        ctk.CTkLabel(seite, text="Dateien & Ordner", font=schrift(22, True),
                     text_color=FARBE["text"], anchor="w").grid(
                         row=0, column=0, sticky="w", padx=26, pady=(22, 2))

        # --- Zeile 1: Was soll bearbeitet werden? -------------------------
        karte = ctk.CTkFrame(seite, fg_color=FARBE["flaeche_tief"],
                             corner_radius=RADIUS)
        karte.grid(row=1, column=0, sticky="ew", padx=26, pady=(10, 6))
        karte.grid_columnconfigure(0, weight=1)

        self.datei_pfad = ctk.CTkLabel(
            karte, text="Noch nichts ausgewählt.", font=schrift(13),
            text_color=FARBE["text_leise"], anchor="w", justify="left")
        self.datei_pfad.grid(row=0, column=0, sticky="ew", padx=16, pady=(14, 4))

        auswahl = ctk.CTkFrame(karte, fg_color="transparent")
        auswahl.grid(row=1, column=0, columnspan=2, sticky="w", padx=12, pady=(0, 12))
        knopf(auswahl, "📄  Datei wählen", self._datei_waehlen, "zweit",
              breite=170, hoehe=36).pack(side="left", padx=4)
        knopf(auswahl, "📁  Ordner wählen", self._ordner_waehlen, "zweit",
              breite=170, hoehe=36).pack(side="left", padx=4)

        # --- Zeile 2: Womit? ----------------------------------------------
        schluesselkarte = ctk.CTkFrame(seite, fg_color=FARBE["flaeche_tief"],
                                       corner_radius=RADIUS)
        schluesselkarte.grid(row=2, column=0, sticky="ew", padx=26, pady=6)
        schluesselkarte.grid_columnconfigure(1, weight=1)

        ctk.CTkLabel(schluesselkarte, text="Womit", font=schrift(12, True),
                     text_color=FARBE["text_leise"]).grid(
                         row=0, column=0, sticky="w", padx=(16, 10), pady=(14, 4))
        self.datei_art = ctk.CTkSegmentedButton(
            schluesselkarte, values=["Passwort", "Gespeicherter Schlüssel"],
            font=schrift(12), command=self._datei_art_gewechselt,
            selected_color=FARBE["akzent"], selected_hover_color=FARBE["akzent_hover"],
            # Ohne diese drei Farben steht im hellen Modus grauer Text auf
            # grauem Grund, und man sieht die zweite Möglichkeit kaum.
            fg_color=FARBE["rand"], unselected_color=FARBE["flaeche"],
            unselected_hover_color=FARBE["rand"], text_color=FARBE["text"])
        self.datei_art.set("Passwort")
        self.datei_art.grid(row=0, column=1, sticky="w", pady=(14, 4))

        self.datei_geheimnis = ctk.CTkEntry(
            schluesselkarte, show="•", height=38, corner_radius=RADIUS,
            font=schrift(13), border_color=FARBE["rand"],
            placeholder_text="Passwort")
        self.datei_geheimnis.grid(row=1, column=0, columnspan=2, sticky="ew",
                                  padx=16, pady=(4, 4))

        self.datei_aus_speicher = knopf(
            schluesselkarte, "Aus Speicher wählen", self._datei_schluessel_laden,
            "leise", breite=190, hoehe=32)
        self.datei_aus_speicher.grid(row=2, column=0, columnspan=2, sticky="w",
                                     padx=16, pady=(0, 14))
        self.datei_aus_speicher.grid_remove()

        # --- Zeile 3: Los ------------------------------------------------
        knoepfe = ctk.CTkFrame(seite, fg_color="transparent")
        knoepfe.grid(row=3, column=0, sticky="ew", padx=26, pady=6)
        knopf(knoepfe, "🔒  Verschlüsseln",
              lambda: self._datei_auftrag(True)).pack(side="left")
        knopf(knoepfe, "🔓  Entschlüsseln",
              lambda: self._datei_auftrag(False), "zweit").pack(side="left", padx=8)
        self.datei_abbruch_knopf = knopf(knoepfe, "Abbrechen",
                                         self._datei_abbrechen, "leise", breite=110)
        self.datei_abbruch_knopf.pack(side="left")
        self.datei_abbruch_knopf.configure(state="disabled")

        # --- Zeile 4: Wie weit? -------------------------------------------
        fortschritt = ctk.CTkFrame(seite, fg_color="transparent")
        fortschritt.grid(row=4, column=0, sticky="ew", padx=26, pady=(6, 2))
        fortschritt.grid_columnconfigure(0, weight=1)
        self.datei_balken = ctk.CTkProgressBar(fortschritt, height=10,
                                               corner_radius=RADIUS,
                                               progress_color=FARBE["akzent"])
        self.datei_balken.set(0)
        self.datei_balken.grid(row=0, column=0, sticky="ew")
        self.datei_stand = ctk.CTkLabel(fortschritt, text="", font=schrift(11),
                                        text_color=FARBE["text_leise"], width=150)
        self.datei_stand.grid(row=0, column=1, sticky="e", padx=(12, 0))

        # --- Zeile 5: Was ist passiert? -----------------------------------
        self.datei_protokoll = ctk.CTkTextbox(
            seite, corner_radius=RADIUS, font=schrift(12), border_width=1,
            border_color=FARBE["rand"], fg_color=FARBE["flaeche_tief"])
        self.datei_protokoll.grid(row=5, column=0, sticky="nsew", padx=26, pady=(8, 6))
        self.datei_protokoll.insert("1.0",
                                    "Hier steht später, was verschlüsselt wurde.\n")
        self.datei_protokoll.configure(state="disabled")

        # --- Zeile 6: Hinweis ---------------------------------------------
        ctk.CTkLabel(
            seite,
            text=("Verschlüsselte Dateien enden auf .vp4. Der Dateiname steckt "
                  "mit drin und ist von aussen nicht zu sehen. Das Original "
                  "bleibt liegen – VP4 löscht von sich aus nichts."),
            font=schrift(11), text_color=FARBE["text_leise"],
            anchor="w", justify="left", wraplength=760).grid(
                row=6, column=0, sticky="w", padx=26, pady=(0, 20))

        self.datei_quelle = None

    # ---------------------------------------------------- Dateiseite: Bedienung

    def _datei_waehlen(self):
        pfad = filedialog.askopenfilename(title="Datei auswählen", parent=self)
        if pfad:
            self._datei_uebernehmen(Path(pfad))

    def _ordner_waehlen(self):
        pfad = filedialog.askdirectory(title="Ordner auswählen", parent=self)
        if pfad:
            self._datei_uebernehmen(Path(pfad))

    def _datei_uebernehmen(self, pfad: Path):
        self.datei_quelle = pfad
        if pfad.is_dir():
            beschreibung = "Ordner"
        else:
            beschreibung = dateien.groesse_lesbar(pfad.stat().st_size)
        self.datei_pfad.configure(text=f"{pfad}\n({beschreibung})",
                                  text_color=FARBE["text"])

        # Bei einer .vp4-Datei gleich einstellen, was sie überhaupt braucht -
        # sonst probiert man mit dem falschen Feld herum.
        if pfad.is_file() and pfad.suffix.lower() == dateien.ENDUNG:
            try:
                art = dateien.kopf_ansehen(pfad)["art"]
            except ValueError:
                return
            gewuenscht = ("Passwort" if art == dateien.ART_PASSWORT
                          else "Gespeicherter Schlüssel")
            if self.datei_art.get() != gewuenscht:
                self.datei_art.set(gewuenscht)
                self._datei_art_gewechselt(gewuenscht)
                self.status(f"Diese Datei braucht: {gewuenscht.lower()}.", "normal")

    def _datei_art_gewechselt(self, wahl):
        mit_passwort = wahl == "Passwort"
        self.datei_geheimnis.configure(
            show="•" if mit_passwort else "",
            placeholder_text="Passwort" if mit_passwort else "Schlüssel (Base64)")
        self.datei_geheimnis.delete(0, "end")
        if mit_passwort:
            self.datei_aus_speicher.grid_remove()
        else:
            self.datei_aus_speicher.grid()

    def _datei_schluessel_laden(self):
        eintraege = [k for k in self.keystore.list_keys()
                     if not str(k.get("typ", "")).startswith("RSA")]
        if not eintraege:
            messagebox.showinfo("Leer", "Im Schlüsselspeicher ist kein passender "
                                        "Schlüssel.", parent=self)
            return
        beschriftungen = [f"{k['label']}  ({k.get('typ', '?')})" for k in eintraege]
        index = self._auswahl_abfragen("Schlüssel wählen",
                                       "Welchen Schlüssel benutzen?", beschriftungen)
        if index is None:
            return
        self.datei_geheimnis.delete(0, "end")
        self.datei_geheimnis.insert(0, eintraege[index]["wert"])

    def _datei_protokoll(self, zeile):
        self.datei_protokoll.configure(state="normal")
        self.datei_protokoll.insert("end", zeile + "\n")
        self.datei_protokoll.see("end")
        self.datei_protokoll.configure(state="disabled")

    def _datei_auftrag(self, verschluesseln: bool):
        if self.auftrag_laeuft:
            self.status("Es läuft schon ein Auftrag.", "warn")
            return
        if not self.datei_quelle or not self.datei_quelle.exists():
            messagebox.showwarning("Nichts ausgewählt",
                                   "Bitte zuerst eine Datei oder einen Ordner "
                                   "auswählen.", parent=self)
            return
        geheimnis = self.datei_geheimnis.get()
        if not geheimnis:
            messagebox.showwarning("Fehlt noch",
                                   "Bitte ein Passwort beziehungsweise einen "
                                   "Schlüssel eingeben.", parent=self)
            return

        quelle = self.datei_quelle
        art = (dateien.ART_PASSWORT if self.datei_art.get() == "Passwort"
               else dateien.ART_SCHLUESSEL)

        if verschluesseln:
            ziel = dateien.zielname(quelle)
            if ziel.exists() and not messagebox.askyesno(
                    "Schon vorhanden",
                    f"{ziel.name} gibt es bereits. Überschreiben?", parent=self):
                return

            def arbeit(melden, abbruch):
                return dateien.verschluesseln(quelle, ziel, geheimnis, art=art,
                                              fortschritt=melden, abbruch=abbruch)
            was = "Verschlüsseln"
        else:
            zielordner = quelle.parent

            def arbeit(melden, abbruch):
                return dateien.entschluesseln(quelle, zielordner, geheimnis,
                                              fortschritt=melden, abbruch=abbruch)
            was = "Entschlüsseln"

        self._auftrag_starten(was, arbeit)

    def _auftrag_starten(self, was, arbeit):
        """Lässt eine langwierige Arbeit in einem eigenen Thread laufen.

        Direkt im Knopfdruck ginge das nicht: die Oberfläche würde bei
        einem grossen Ordner minutenlang einfrieren, und Abbrechen wäre
        gar nicht erst anklickbar. Gemeldet wird über dieselbe Warteschlange
        wie beim Chat - nur der Hauptthread fasst Widgets an.
        """
        self.auftrag_laeuft = True
        self.auftrag_abbruch = threading.Event()
        abbruch = self.auftrag_abbruch
        self.datei_abbruch_knopf.configure(state="normal")
        self.datei_balken.set(0)
        self.datei_stand.configure(text="0 %")
        self.status(f"{was} läuft …", "normal")

        letzter = [-1]

        def melden(getan, gesamt):
            anteil = 1.0 if not gesamt else min(getan / gesamt, 1.0)
            prozent = int(anteil * 100)
            # Nicht bei jedem Block melden - sonst füllt sich die
            # Warteschlange schneller, als die Oberfläche sie leert.
            if prozent != letzter[0]:
                letzter[0] = prozent
                self.ereignisse.put(("fortschritt", (anteil, getan, gesamt)))

        def lauf():
            try:
                ergebnis = arbeit(melden, abbruch)
                self.ereignisse.put(("auftrag_fertig", (was, ergebnis)))
            except dateien.AbgebrochenError:
                self.ereignisse.put(("auftrag_abgebrochen", was))
            except Exception as fehler:
                self.ereignisse.put(("auftrag_fehler", (was, str(fehler))))

        threading.Thread(target=lauf, daemon=True).start()

    def _datei_abbrechen(self):
        if self.auftrag_abbruch is not None:
            self.auftrag_abbruch.set()
            self.status("Abbruch angefordert …", "warn")

    def _auftrag_beendet(self):
        self.auftrag_laeuft = False
        self.auftrag_abbruch = None
        self.datei_abbruch_knopf.configure(state="disabled")

    # ======================================================= Seite: Schlüssel

    def _seite_schluessel(self, seite):
        seite.grid_columnconfigure(0, weight=1)
        seite.grid_rowconfigure(2, weight=1)

        kopf = ctk.CTkFrame(seite, fg_color="transparent")
        kopf.grid(row=0, column=0, sticky="ew", padx=26, pady=(22, 4))
        ctk.CTkLabel(kopf, text="Schlüsselspeicher", font=schrift(22, True),
                     text_color=FARBE["text"]).pack(anchor="w")
        ctk.CTkLabel(kopf, text="Mit deinem Master-Passwort verschlüsselt auf der Festplatte.",
                     font=schrift(11), text_color=FARBE["text_leise"]).pack(anchor="w")

        reihe = ctk.CTkFrame(seite, fg_color="transparent")
        reihe.grid(row=1, column=0, sticky="ew", padx=26, pady=10)
        ctk.CTkButton(reihe, text="+  Neuer Schlüssel", height=36, width=160,
                      corner_radius=RADIUS, font=schrift(12, True),
                      fg_color=FARBE["akzent"], text_color="#ffffff", hover_color=FARBE["akzent_hover"],
                      command=self._schluessel_hinzufuegen).pack(side="left")
        for text, befehl in [("Anzeigen", self._schluessel_anzeigen),
                             ("Kopieren", self._schluessel_kopieren),
                             ("Löschen", self._schluessel_loeschen)]:
            ctk.CTkButton(reihe, text=text, height=36, width=110,
                          corner_radius=RADIUS, font=schrift(12),
                          fg_color="transparent", border_width=1,
                          border_color=FARBE["rand"], text_color=FARBE["text"],
                          hover_color=FARBE["flaeche_tief"],
                          command=befehl).pack(side="left", padx=6)

        tabelle_rahmen = ctk.CTkFrame(seite, fg_color=FARBE["flaeche_tief"],
                                      corner_radius=RADIUS)
        tabelle_rahmen.grid(row=2, column=0, sticky="nsew", padx=26, pady=(4, 22))
        tabelle_rahmen.grid_columnconfigure(0, weight=1)
        tabelle_rahmen.grid_rowconfigure(0, weight=1)

        # CustomTkinter hat keine Tabelle - dafür wird ttk.Treeview benutzt
        # und farblich an das übrige Fenster angepasst.
        self.schluessel_tabelle = ttk.Treeview(
            tabelle_rahmen, columns=("typ", "notiz", "erstellt"),
            show="tree headings", selectmode="browse")
        self.schluessel_tabelle.heading("#0", text="Name")
        self.schluessel_tabelle.heading("typ", text="Typ")
        self.schluessel_tabelle.heading("notiz", text="Notiz")
        self.schluessel_tabelle.heading("erstellt", text="Erstellt")
        self.schluessel_tabelle.column("#0", width=240)
        self.schluessel_tabelle.column("typ", width=110)
        self.schluessel_tabelle.column("notiz", width=260)
        self.schluessel_tabelle.column("erstellt", width=130)
        self.schluessel_tabelle.grid(row=0, column=0, sticky="nsew", padx=8, pady=8)

        rollbalken = ttk.Scrollbar(tabelle_rahmen, orient="vertical",
                                   command=self.schluessel_tabelle.yview)
        self.schluessel_tabelle.configure(yscrollcommand=rollbalken.set)
        rollbalken.grid(row=0, column=1, sticky="ns", pady=8, padx=(0, 8))

        self._treeview_stil()
        self._schluessel_liste_aktualisieren()

    def _treeview_stil(self):
        """Passt die ttk-Tabelle an den gewählten Hell/Dunkel-Modus an."""
        dunkel = ctk.get_appearance_mode() == "Dark"
        i = 1 if dunkel else 0
        stil = ttk.Style()
        try:
            stil.theme_use("clam")
        except tk.TclError:
            pass
        stil.configure("Treeview",
                       background=FARBE["flaeche_tief"][i],
                       fieldbackground=FARBE["flaeche_tief"][i],
                       foreground=FARBE["text"][i],
                       borderwidth=0, rowheight=30, font=(SCHRIFT, 10))
        stil.configure("Treeview.Heading",
                       background=FARBE["leiste"][i],
                       foreground=FARBE["text_leise"][i],
                       borderwidth=0, font=(SCHRIFT, 10, "bold"))
        stil.map("Treeview",
                 background=[("selected", FARBE["akzent"][i])],
                 foreground=[("selected", "#ffffff")])
        stil.map("Treeview.Heading",
                 background=[("active", FARBE["leiste"][i])])

    def _schluessel_liste_aktualisieren(self):
        if not hasattr(self, "schluessel_tabelle"):
            return
        for zeile in self.schluessel_tabelle.get_children():
            self.schluessel_tabelle.delete(zeile)
        try:
            for k in self.keystore.list_keys():
                self.schluessel_tabelle.insert(
                    "", "end", text=k["label"],
                    values=(k["typ"], k.get("meta", ""), k.get("erstellt", "")))
        except KeyStoreLockedError:
            pass

    def _gewaehlter_schluessel(self):
        auswahl = self.schluessel_tabelle.selection()
        if not auswahl:
            self.status("Bitte zuerst einen Schlüssel in der Liste anklicken.", "warn")
            return None
        return self.schluessel_tabelle.item(auswahl[0], "text")

    def _schluessel_hinzufuegen(self):
        fenster = ctk.CTkToplevel(self)
        fenster.title("Neuer Schlüssel")
        fenster.geometry("480x420")
        fenster.transient(self)
        fenster.after(100, fenster.grab_set)

        ctk.CTkLabel(fenster, text="Schlüssel hinzufügen",
                     font=schrift(17, True)).pack(pady=(20, 14))

        felder = {}
        for schluessel, beschriftung in [("label", "Name"), ("typ", "Typ (z.B. AES)"),
                                         ("meta", "Notiz (optional)")]:
            ctk.CTkLabel(fenster, text=beschriftung, font=schrift(11),
                         anchor="w").pack(fill="x", padx=30, pady=(6, 2))
            feld = ctk.CTkEntry(fenster, height=34, corner_radius=RADIUS)
            feld.pack(fill="x", padx=30)
            felder[schluessel] = feld

        ctk.CTkLabel(fenster, text="Schlüsselwert", font=schrift(11),
                     anchor="w").pack(fill="x", padx=30, pady=(10, 2))
        wert_feld = ctk.CTkTextbox(fenster, height=90, corner_radius=RADIUS,
                                   font=ctk.CTkFont(family="Consolas", size=11))
        wert_feld.pack(fill="x", padx=30)

        def sichern():
            try:
                self.keystore.add_key(
                    felder["label"].get(), felder["typ"].get() or "sonstiges",
                    wert_feld.get("1.0", "end-1c").strip(), felder["meta"].get())
            except Exception as e:
                messagebox.showerror("Fehler", str(e), parent=fenster)
                return
            self._schluessel_liste_aktualisieren()
            self.status("Schlüssel gespeichert.", "gut")
            fenster.destroy()

        ctk.CTkButton(fenster, text="Speichern", height=38, corner_radius=RADIUS,
                      font=schrift(13, True), fg_color=FARBE["akzent"], text_color="#ffffff",
                      hover_color=FARBE["akzent_hover"],
                      command=sichern).pack(pady=18, padx=30, fill="x")

    def _schluessel_anzeigen(self):
        label = self._gewaehlter_schluessel()
        if not label:
            return
        eintrag = self.keystore.get_key(label)
        if not eintrag:
            return
        fenster = ctk.CTkToplevel(self)
        fenster.title(label)
        fenster.geometry("580x340")
        fenster.transient(self)
        fenster.after(100, fenster.grab_set)
        ctk.CTkLabel(fenster, text=label, font=schrift(16, True)).pack(pady=(18, 2))
        ctk.CTkLabel(fenster, text=f"Typ: {eintrag['typ']}   ·   "
                                   f"Erstellt: {eintrag.get('erstellt', '?')}",
                     font=schrift(11), text_color=FARBE["text_leise"]).pack(pady=(0, 10))
        feld = ctk.CTkTextbox(fenster, font=ctk.CTkFont(family="Consolas", size=11))
        feld.pack(fill="both", expand=True, padx=22, pady=6)
        feld.insert("1.0", eintrag["wert"])
        ctk.CTkButton(fenster, text="Schließen", command=fenster.destroy,
                      fg_color="transparent", border_width=1,
                      border_color=FARBE["rand"], text_color=FARBE["text"]).pack(pady=12)

    def _schluessel_kopieren(self):
        label = self._gewaehlter_schluessel()
        if not label:
            return
        eintrag = self.keystore.get_key(label)
        if eintrag:
            self.clipboard_clear()
            self.clipboard_append(eintrag["wert"])
            self.status(f"'{label}' in die Zwischenablage kopiert.", "gut")

    def _schluessel_loeschen(self):
        label = self._gewaehlter_schluessel()
        if not label:
            return
        if not messagebox.askyesno(
                "Wirklich löschen?",
                f"'{label}' endgültig löschen?\n\nWas damit verschlüsselt wurde, "
                f"lässt sich danach nicht mehr öffnen.", parent=self):
            return
        self.keystore.delete_key(label)
        self._schluessel_liste_aktualisieren()
        self.status(f"'{label}' gelöscht.", "warn")

    # ======================================================= Seite: Signieren

    def _seite_signieren(self, seite):
        # Auch hier jede Zeile nur einmal belegen:
        #   0 Überschrift · 1 Knopfreihe · 2 "Text" · 3 Textfeld
        #   4 Schlüssel-Beschriftung · 5 Schlüsselfeld
        #   6 Ergebnis-Beschriftung · 7 Ergebnisfeld · 8 Knöpfe
        seite.grid_columnconfigure(0, weight=1)
        seite.grid_rowconfigure(3, weight=1)

        kopf = ctk.CTkFrame(seite, fg_color="transparent")
        kopf.grid(row=0, column=0, sticky="ew", padx=26, pady=(22, 4))
        ctk.CTkLabel(kopf, text="Signieren & Prüfsummen", font=schrift(22, True),
                     text_color=FARBE["text"]).pack(anchor="w")
        ctk.CTkLabel(kopf,
                     text="Eine Signatur beweist, dass ein Text wirklich von dir kommt. "
                          "Der Text bleibt dabei lesbar – das ist keine Verschlüsselung.",
                     font=schrift(11), text_color=FARBE["text_leise"],
                     justify="left", wraplength=740).pack(anchor="w", pady=(2, 0))

        reihe = ctk.CTkFrame(seite, fg_color="transparent")
        reihe.grid(row=1, column=0, sticky="ew", padx=26, pady=10)
        ctk.CTkButton(reihe, text="Signaturschlüssel erzeugen", height=34, width=210,
                      corner_radius=RADIUS, font=schrift(12),
                      fg_color=FARBE["akzent"], text_color="#ffffff", hover_color=FARBE["akzent_hover"],
                      command=self._signaturschluessel_erzeugen).pack(side="left")
        self.pruefsummen_wahl = ctk.CTkOptionMenu(
            reihe, values=list(Pruefsummen.VERFAHREN), width=130, height=34,
            corner_radius=RADIUS, font=schrift(12), fg_color=FARBE["akzent"], text_color="#ffffff",
            button_color=FARBE["akzent"], button_hover_color=FARBE["akzent_hover"])
        self.pruefsummen_wahl.pack(side="right")
        ctk.CTkLabel(reihe, text="Prüfsumme:", font=schrift(11),
                     text_color=FARBE["text_leise"]).pack(side="right", padx=8)

        ctk.CTkLabel(seite, text="Text", font=schrift(12, True),
                     text_color=FARBE["text_leise"], anchor="w").grid(
                         row=2, column=0, sticky="w", padx=26, pady=(8, 2))
        self.sig_text = ctk.CTkTextbox(seite, corner_radius=RADIUS, font=schrift(12),
                                       border_width=1, border_color=FARBE["rand"],
                                       fg_color=FARBE["flaeche_tief"])
        self.sig_text.grid(row=3, column=0, sticky="nsew", padx=26, pady=(0, 8))

        ctk.CTkLabel(seite, text="Signaturschlüssel (privat zum Signieren, "
                                 "öffentlich zum Prüfen)",
                     font=schrift(11), text_color=FARBE["text_leise"],
                     anchor="w").grid(row=4, column=0, sticky="w", padx=26, pady=(4, 2))
        self.sig_schluessel = ctk.CTkTextbox(seite, height=50, corner_radius=RADIUS,
                                             font=ctk.CTkFont(family="Consolas", size=11),
                                             border_width=1, border_color=FARBE["rand"],
                                             fg_color=FARBE["flaeche_tief"])
        self.sig_schluessel.grid(row=5, column=0, sticky="ew", padx=26, pady=(0, 8))

        ctk.CTkLabel(seite, text="Signatur / Prüfsumme", font=schrift(11),
                     text_color=FARBE["text_leise"], anchor="w").grid(
                         row=6, column=0, sticky="w", padx=26, pady=(4, 2))
        self.sig_ergebnis = ctk.CTkTextbox(seite, height=70, corner_radius=RADIUS,
                                           font=ctk.CTkFont(family="Consolas", size=11),
                                           border_width=1, border_color=FARBE["rand"],
                                           fg_color=FARBE["flaeche_tief"])
        self.sig_ergebnis.grid(row=7, column=0, sticky="ew", padx=26, pady=(0, 8))

        knoepfe = ctk.CTkFrame(seite, fg_color="transparent")
        knoepfe.grid(row=8, column=0, sticky="ew", padx=26, pady=(0, 22))
        ctk.CTkButton(knoepfe, text="✍  Signieren", height=38, width=150,
                      corner_radius=RADIUS, font=schrift(12, True),
                      fg_color=FARBE["akzent"], text_color="#ffffff", hover_color=FARBE["akzent_hover"],
                      command=self._signieren).pack(side="left")
        ctk.CTkButton(knoepfe, text="✓  Signatur prüfen", height=38, width=170,
                      corner_radius=RADIUS, font=schrift(12), fg_color="transparent",
                      border_width=1, border_color=FARBE["akzent"],
                      text_color=FARBE["akzent"], hover_color=FARBE["flaeche_tief"],
                      command=self._signatur_pruefen).pack(side="left", padx=8)
        ctk.CTkButton(knoepfe, text="# Prüfsumme berechnen", height=38, width=190,
                      corner_radius=RADIUS, font=schrift(12), fg_color="transparent",
                      border_width=1, border_color=FARBE["rand"],
                      text_color=FARBE["text"], hover_color=FARBE["flaeche_tief"],
                      command=self._pruefsumme).pack(side="left")

    def _signaturschluessel_erzeugen(self):
        privat, oeffentlich = Signaturen.generate_keypair()
        self.sig_schluessel.delete("1.0", "end")
        self.sig_schluessel.insert("1.0", privat)
        self.status("Signaturschlüssel erzeugt – der private steht im Feld.", "gut")
        if messagebox.askyesno(
                "Sichern?",
                "Beide Signaturschlüssel im Schlüsselspeicher sichern?\n\n"
                f"Öffentlich (weitergeben):\n{oeffentlich}",
                parent=self):
            name = self._text_abfragen("Name", "Unter welchem Namen?")
            if name:
                try:
                    self.keystore.add_key(f"{name} (Signatur privat)", "Ed25519-priv", privat)
                    self.keystore.add_key(f"{name} (Signatur öffentlich)", "Ed25519-pub", oeffentlich)
                    self._schluessel_liste_aktualisieren()
                    self.status("Signaturschlüssel gespeichert.", "gut")
                except Exception as e:
                    messagebox.showerror("Fehler", str(e), parent=self)

    def _signieren(self):
        try:
            sig = Signaturen.sign(self.sig_text.get("1.0", "end-1c"),
                                  self.sig_schluessel.get("1.0", "end-1c").strip())
        except ValueError as e:
            self.status(str(e), "fehler")
            messagebox.showerror("Fehler", str(e), parent=self)
            return
        self.sig_ergebnis.delete("1.0", "end")
        self.sig_ergebnis.insert("1.0", sig)
        self.status("Text signiert.", "gut")

    def _signatur_pruefen(self):
        try:
            passt = Signaturen.verify(self.sig_text.get("1.0", "end-1c"),
                                      self.sig_ergebnis.get("1.0", "end-1c").strip(),
                                      self.sig_schluessel.get("1.0", "end-1c").strip())
        except ValueError as e:
            self.status(str(e), "fehler")
            messagebox.showerror("Fehler", str(e), parent=self)
            return
        if passt:
            self.status("Signatur gültig – Text stammt von diesem Schlüssel.", "gut")
            messagebox.showinfo("Gültig",
                                "Die Signatur passt.\n\nDer Text stammt wirklich von "
                                "dem, der zu diesem öffentlichen Schlüssel gehört, "
                                "und wurde nicht verändert.", parent=self)
        else:
            self.status("Signatur ungültig!", "fehler")
            messagebox.showwarning("Ungültig",
                                   "Die Signatur passt NICHT.\n\nEntweder stammt der "
                                   "Text nicht von diesem Schlüssel, oder er wurde "
                                   "nachträglich verändert.", parent=self)

    def _pruefsumme(self):
        wert = Pruefsummen.berechne(self.sig_text.get("1.0", "end-1c"),
                                    self.pruefsummen_wahl.get())
        self.sig_ergebnis.delete("1.0", "end")
        self.sig_ergebnis.insert("1.0", wert)
        self.status(f"{self.pruefsummen_wahl.get()}-Prüfsumme berechnet.", "gut")

    # ======================================================== Seite: Obsidian

    def _seite_obsidian(self, seite):
        seite.grid_columnconfigure(0, weight=1)

        kopf = ctk.CTkFrame(seite, fg_color="transparent")
        kopf.grid(row=0, column=0, sticky="ew", padx=26, pady=(22, 4))
        ctk.CTkLabel(kopf, text="Obsidian-Verknüpfung", font=schrift(22, True),
                     text_color=FARBE["text"]).pack(anchor="w")
        ctk.CTkLabel(kopf, text="Schlüssel als Notiz in deinen Vault schreiben "
                                "und von dort wieder einlesen.",
                     font=schrift(11), text_color=FARBE["text_leise"]).pack(anchor="w")

        ordner = ctk.CTkFrame(seite, fg_color=FARBE["flaeche_tief"], corner_radius=RADIUS)
        ordner.grid(row=1, column=0, sticky="ew", padx=26, pady=16)
        ordner.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(ordner, text="Vault-Ordner", font=schrift(11, True),
                     text_color=FARBE["text_leise"]).grid(row=0, column=0, sticky="w",
                                                          padx=16, pady=(14, 2))
        self.vault_feld = ctk.CTkEntry(ordner, height=36, corner_radius=RADIUS,
                                       placeholder_text="z.B. C:\\Users\\…\\Mein Vault")
        self.vault_feld.grid(row=1, column=0, sticky="ew", padx=16, pady=(0, 16))
        self.vault_feld.insert(0, self.config_data.get("obsidian_vault", ""))
        ctk.CTkButton(ordner, text="Durchsuchen …", height=36, width=140,
                      corner_radius=RADIUS, font=schrift(12), fg_color=FARBE["akzent"], text_color="#ffffff",
                      hover_color=FARBE["akzent_hover"],
                      command=self._vault_waehlen).grid(row=1, column=1,
                                                        padx=(0, 16), pady=(0, 16))

        knoepfe = ctk.CTkFrame(seite, fg_color="transparent")
        knoepfe.grid(row=2, column=0, sticky="ew", padx=26)
        ctk.CTkButton(knoepfe, text="→  Nach Obsidian exportieren", height=40, width=250,
                      corner_radius=RADIUS, font=schrift(13, True),
                      fg_color=FARBE["akzent"], text_color="#ffffff", hover_color=FARBE["akzent_hover"],
                      command=self._obsidian_export).pack(side="left")
        ctk.CTkButton(knoepfe, text="←  Aus Obsidian importieren", height=40, width=250,
                      corner_radius=RADIUS, font=schrift(13), fg_color="transparent",
                      border_width=1, border_color=FARBE["akzent"],
                      text_color=FARBE["akzent"], hover_color=FARBE["flaeche_tief"],
                      command=self._obsidian_import).pack(side="left", padx=10)

        warnung = ctk.CTkFrame(seite, fg_color=FARBE["flaeche_tief"], corner_radius=RADIUS)
        warnung.grid(row=3, column=0, sticky="ew", padx=26, pady=22)
        ctk.CTkLabel(warnung,
                     text="⚠  Die Schlüssel stehen in der Notiz im Klartext.\n\n"
                          "Wenn dein Vault über Obsidian Sync, iCloud, Dropbox oder Git "
                          "synchronisiert wird, werden sie mitsynchronisiert. Exportiere "
                          "nur dorthin, wo du dem Speicherort vertraust.",
                     font=schrift(12), text_color=FARBE["warn"], justify="left",
                     wraplength=700).pack(padx=18, pady=16, anchor="w")

    def _vault_waehlen(self):
        ordner = filedialog.askdirectory(title="Obsidian-Vault wählen")
        if ordner:
            self.vault_feld.delete(0, "end")
            self.vault_feld.insert(0, ordner)

    def _obsidian_export(self):
        self.config_data["obsidian_vault"] = self.vault_feld.get()
        speicher.save_config(self.config_data)
        try:
            sync = ObsidianSync(self.vault_feld.get())
            pfad = sync.export_keys(self.keystore.list_keys())
        except Exception as e:
            self.status(str(e), "fehler")
            messagebox.showerror("Export fehlgeschlagen", str(e), parent=self)
            return
        self.status(f"Nach {pfad.name} exportiert.", "gut")
        messagebox.showinfo("Fertig", f"Die Schlüssel stehen jetzt in:\n\n{pfad}",
                            parent=self)

    def _obsidian_import(self):
        self.config_data["obsidian_vault"] = self.vault_feld.get()
        speicher.save_config(self.config_data)
        try:
            sync = ObsidianSync(self.vault_feld.get())
            eingelesen = sync.import_keys()
            self.keystore.replace_all(eingelesen)
        except Exception as e:
            self.status(str(e), "fehler")
            messagebox.showerror("Import fehlgeschlagen", str(e), parent=self)
            return
        self._schluessel_liste_aktualisieren()
        self.status(f"{len(eingelesen)} Schlüssel aus Obsidian eingelesen.", "gut")

    # ============================================================ Seite: Chat

    def _seite_chat(self, seite):
        seite.grid_columnconfigure(1, weight=1)
        seite.grid_rowconfigure(1, weight=1)

        kopf = ctk.CTkFrame(seite, fg_color="transparent")
        kopf.grid(row=0, column=0, columnspan=2, sticky="ew", padx=26, pady=(22, 8))
        # Die Überschrift sagt, wo die Nachrichten wirklich langlaufen. "Chat
        # im WLAN" stand vorher auch dann da, wenn alles über Discord ging -
        # das ist genau die Angabe, bei der man sich darauf verlässt.
        self.chat_ueberschrift = ctk.CTkLabel(
            kopf, text=self._chat_ueberschrift_text(), font=schrift(22, True),
            text_color=FARBE["text"])
        self.chat_ueberschrift.pack(side="left")

        id_kasten = ctk.CTkFrame(kopf, fg_color=FARBE["flaeche_tief"], corner_radius=RADIUS)
        id_kasten.pack(side="right")
        ctk.CTkLabel(id_kasten, text="Deine ID", font=schrift(10),
                     text_color=FARBE["text_leise"]).pack(padx=14, pady=(8, 0))
        ctk.CTkLabel(id_kasten, text=self.my_id,
                     font=ctk.CTkFont(family="Consolas", size=17, weight="bold"),
                     text_color=FARBE["akzent"]).pack(padx=14)
        ctk.CTkButton(id_kasten, text="Kopieren", width=90, height=24, corner_radius=8,
                      font=schrift(10), fg_color="transparent", border_width=1,
                      border_color=FARBE["rand"], text_color=FARBE["text_leise"],
                      hover_color=FARBE["flaeche"],
                      command=self._id_kopieren).pack(padx=14, pady=(2, 10))

        # --- Freundesliste links -------------------------------------------
        links = ctk.CTkFrame(seite, width=250, fg_color=FARBE["flaeche_tief"],
                             corner_radius=RADIUS)
        links.grid(row=1, column=0, sticky="nsw", padx=(26, 8), pady=(0, 20))
        links.grid_propagate(False)
        links.grid_rowconfigure(1, weight=1)

        ctk.CTkLabel(links, text="Freunde & Gruppen", font=schrift(12, True),
                     text_color=FARBE["text_leise"]).grid(row=0, column=0, sticky="w",
                                                          padx=14, pady=(12, 6))
        self.freunde_liste = ctk.CTkScrollableFrame(links, fg_color="transparent",
                                                    width=220)
        self.freunde_liste.grid(row=1, column=0, sticky="nsew", padx=6)

        knoepfe = ctk.CTkFrame(links, fg_color="transparent")
        knoepfe.grid(row=2, column=0, sticky="ew", padx=10, pady=10)
        ctk.CTkButton(knoepfe, text="+ Freund", height=32, corner_radius=RADIUS,
                      font=schrift(11), fg_color=FARBE["akzent"], text_color="#ffffff",
                      hover_color=FARBE["akzent_hover"],
                      command=self._freund_hinzufuegen).pack(side="left", expand=True, fill="x")
        ctk.CTkButton(knoepfe, text="🔑", width=38, height=32, corner_radius=RADIUS,
                      font=schrift(11), fg_color="transparent", border_width=1,
                      border_color=FARBE["rand"], text_color=FARBE["text"],
                      hover_color=FARBE["flaeche"],
                      command=self._gemeinsamen_schluessel_setzen).pack(side="left", padx=4)
        ctk.CTkButton(knoepfe, text="✕", width=38, height=32, corner_radius=RADIUS,
                      font=schrift(11), fg_color="transparent", border_width=1,
                      border_color=FARBE["rand"], text_color=FARBE["schlecht"],
                      hover_color=FARBE["flaeche"],
                      command=self._eintrag_entfernen).pack(side="left")

        # Zweite Reihe: Gruppen. Eine Gruppe ist kein Freund - sie hat keine
        # ID zum Hinzufügen, sondern einen Code zum Weitergeben.
        gruppen_knoepfe = ctk.CTkFrame(links, fg_color="transparent")
        gruppen_knoepfe.grid(row=3, column=0, sticky="ew", padx=10, pady=(0, 12))
        knopf(gruppen_knoepfe, "＋ Gruppe", self._gruppe_erstellen,
              art="leise", hoehe=32, breite=90).pack(side="left", expand=True, fill="x")
        knopf(gruppen_knoepfe, "Beitreten", self._gruppe_beitreten,
              art="leise", hoehe=32, breite=90).pack(side="left", expand=True, fill="x", padx=4)
        knopf(gruppen_knoepfe, "Code", self._gruppen_code_zeigen,
              art="leise", hoehe=32, breite=52).pack(side="left")

        # --- Chatfenster rechts --------------------------------------------
        rechts = ctk.CTkFrame(seite, fg_color="transparent")
        rechts.grid(row=1, column=1, sticky="nsew", padx=(8, 26), pady=(0, 20))
        rechts.grid_columnconfigure(0, weight=1)
        rechts.grid_rowconfigure(1, weight=1)

        self.chat_titel = ctk.CTkLabel(rechts, text="Kein Freund ausgewählt",
                                       font=schrift(14, True), anchor="w",
                                       text_color=FARBE["text"])
        self.chat_titel.grid(row=0, column=0, sticky="ew", pady=(0, 6))

        self.chat_verlauf_feld = ctk.CTkTextbox(
            rechts, corner_radius=RADIUS, font=schrift(12), border_width=1,
            border_color=FARBE["rand"], fg_color=FARBE["flaeche_tief"])
        self.chat_verlauf_feld.grid(row=1, column=0, sticky="nsew")
        self.chat_verlauf_feld.configure(state="disabled")

        eingabe = ctk.CTkFrame(rechts, fg_color="transparent")
        eingabe.grid(row=2, column=0, sticky="ew", pady=(8, 0))
        eingabe.grid_columnconfigure(0, weight=1)

        self.chat_eingabe = ctk.CTkEntry(eingabe, height=40, corner_radius=RADIUS,
                                         font=schrift(13),
                                         placeholder_text="Nachricht schreiben …")
        self.chat_eingabe.grid(row=0, column=0, sticky="ew")
        self.chat_eingabe.bind("<Return>", lambda e: self._chat_senden())

        ctk.CTkButton(eingabe, text="Senden", width=90, height=40, corner_radius=RADIUS,
                      font=schrift(12, True), fg_color=FARBE["akzent"], text_color="#ffffff",
                      hover_color=FARBE["akzent_hover"],
                      command=self._chat_senden).grid(row=0, column=1, padx=(8, 0))
        ctk.CTkButton(eingabe, text="📎", width=44, height=40, corner_radius=RADIUS,
                      font=schrift(14), fg_color="transparent", border_width=1,
                      border_color=FARBE["rand"], text_color=FARBE["text"],
                      hover_color=FARBE["flaeche_tief"],
                      command=self._datei_senden).grid(row=0, column=2, padx=(6, 0))

    def _id_kopieren(self):
        self.clipboard_clear()
        self.clipboard_append(self.my_id)
        self.status("Deine ID wurde kopiert – schick sie deinem Freund.", "gut")

    def _freunde_aktualisieren(self):
        """Zeichnet die Freundesliste neu und markiert, wer erreichbar ist."""
        if hasattr(self, "freunde_liste"):
            for kind in self.freunde_liste.winfo_children():
                kind.destroy()

            for fid, daten in sorted(self.friends.all().items()):
                name = daten.get("nickname") or fid
                weg = self.network.erreichbar_ueber(fid)

                zeile = ctk.CTkFrame(self.freunde_liste, fg_color="transparent")
                zeile.pack(fill="x", pady=1)

                # 🟢 direkt im WLAN, 🌐 nur über Discord, ⚪ gar nicht
                zeichen = {"lan": "🟢", "discord": "🌐"}.get(weg, "⚪")
                text = f"{zeichen}  {name}"
                # 🔒 eigener Schlüssel (nur ihr zwei), 👥 Gruppenschlüssel
                # (alle mit VP4 können mitlesen), nichts = im Klartext.
                if daten.get("shared_key_b64"):
                    text += "  🔒"
                elif chat.gruppen_schluessel_b64():
                    text += "  👥"
                knopf = ctk.CTkButton(
                    zeile, text=text, anchor="w", height=36, corner_radius=8,
                    font=schrift(12),
                    fg_color=FARBE["akzent"] if fid == self.aktiver_chat else "transparent",
                    text_color="#ffffff" if fid == self.aktiver_chat else FARBE["text"],
                    hover_color=FARBE["flaeche"],
                    command=lambda f=fid: self._chat_oeffnen(f))
                knopf.pack(fill="x")

            # Gruppen stehen unter den Freunden in derselben Liste - für den
            # Chat ist beides dasselbe: etwas, dem man schreibt.
            for gid, gruppe in sorted(self.gruppen.all().items(),
                                      key=lambda p: (p[1].get("name") or "").lower()):
                zeile = ctk.CTkFrame(self.freunde_liste, fg_color="transparent")
                zeile.pack(fill="x", pady=1)
                erreichbar = self.network.erreichbar_ueber(gid) is not None
                text = f"{'👥' if erreichbar else '⚪'}  {gruppe.get('name') or gid}"
                ctk.CTkButton(
                    zeile, text=text, anchor="w", height=36, corner_radius=8,
                    font=schrift(12),
                    fg_color=FARBE["akzent"] if gid == self.aktiver_chat else "transparent",
                    text_color="#ffffff" if gid == self.aktiver_chat else FARBE["text"],
                    hover_color=FARBE["flaeche"],
                    command=lambda g=gid: self._chat_oeffnen(g)).pack(fill="x")

            if not self.friends.all() and not self.gruppen.all():
                wo = ("jemandem im selben WLAN."
                      if self.config_data.get("transport_modus", "lan") == "lan"
                      else "einem Freund – egal wo er gerade ist.")
                ctk.CTkLabel(self.freunde_liste,
                             text=f"Noch niemand da.\n\nTausche deine ID mit\n{wo}\n\n"
                                  f"Oder mach eine Gruppe auf und\nschick den Code herum.",
                             font=schrift(11), text_color=FARBE["text_leise"],
                             justify="center").pack(pady=30)

        self._spaeter(3000, self._freunde_aktualisieren)

    def _chat_oeffnen(self, fid):
        self.aktiver_chat = fid

        if speicher.ist_gruppen_id(fid):
            gruppe = self.gruppen.get(fid) or {}
            name = gruppe.get("name") or fid
            self.chat_titel.configure(
                text=f"👥 {name}   ·   {fid}   ·   "
                     f"🔒 Gruppe – alle mit dem Code lesen mit")
            self._chat_verlauf_zeichnen(fid)
            return

        daten = self.friends.get(fid) or {}
        name = daten.get("nickname") or fid
        # Beim Gruppenschlüssel steht ausdrücklich etwas anderes da als bei
        # einem eigenen: er schützt vor Discord und vor Fremden, aber nicht
        # vor den anderen mit derselben VP4.exe. "🔒 verschlüsselt" würde
        # hier mehr versprechen, als er hält.
        if daten.get("shared_key_b64"):
            gesichert = "🔒 verschlüsselt"
        elif chat.gruppen_schluessel_b64():
            gesichert = "🔒 Gruppenschlüssel (alle mit VP4 können mitlesen)"
        else:
            gesichert = "🔓 unverschlüsselt"
        self.chat_titel.configure(text=f"{name}   ·   {fid}   ·   {gesichert}")
        self._chat_verlauf_zeichnen(fid)

    def _chat_verlauf_zeichnen(self, fid):
        self.chat_verlauf_feld.configure(state="normal")
        self.chat_verlauf_feld.delete("1.0", "end")
        for zeile in self.chat_verlauf.get(fid, []):
            self.chat_verlauf_feld.insert("end", zeile + "\n")
        self.chat_verlauf_feld.configure(state="disabled")
        self.chat_verlauf_feld.see("end")

    def _chat_zeile(self, fid, zeile):
        self.chat_verlauf.setdefault(fid, []).append(zeile)
        if fid == self.aktiver_chat:
            self._chat_verlauf_zeichnen(fid)

    # ------------------------------------------------------------- Gruppen

    def _gruppe_erstellen(self):
        name = self._text_abfragen("Gruppe erstellen",
                                   "Wie soll die Gruppe heißen?")
        if name is None:
            return
        gid = self.gruppen.erstellen(name)
        self.status(f"Gruppe '{self.gruppen.get(gid)['name']}' erstellt.", "gut")
        self._chat_oeffnen(gid)
        self._gruppen_code_zeigen(gid)

    def _gruppe_beitreten(self):
        code = self._text_abfragen(
            "Gruppe beitreten",
            "Füg den Code ein, den du bekommen hast:")
        if not code:
            return
        try:
            gid = self.gruppen.beitreten(code)
        except ValueError as e:
            self.status(str(e).split("\n")[0], "fehler")
            messagebox.showerror("Das hat nicht geklappt", str(e), parent=self)
            return
        name = self._text_abfragen("Name", "Wie soll die Gruppe bei dir heißen? "
                                           "(kann leer bleiben)")
        if name:
            self.gruppen.beitreten(code, name)
        self.status(f"Du bist in der Gruppe '{self.gruppen.get(gid)['name']}'.", "gut")
        self._chat_oeffnen(gid)

    def _gruppen_code_zeigen(self, gid=None):
        """Zeigt den Einladungscode der gewählten Gruppe zum Weitergeben."""
        gid = gid or self.aktiver_chat
        if not gid or not speicher.ist_gruppen_id(gid) or gid not in self.gruppen:
            self.status("Wähl zuerst links eine Gruppe aus.", "warn")
            return
        gruppe = self.gruppen.get(gid)
        code = self.gruppen.code(gid)

        fenster = ctk.CTkToplevel(self)
        fenster.title("Einladungscode")
        fenster.geometry("560x320")
        fenster.configure(fg_color=FARBE["flaeche"])
        fenster.transient(self)
        fenster.grab_set()

        ctk.CTkLabel(fenster, text=f"👥  {gruppe['name']}", font=schrift(17, True),
                     text_color=FARBE["text"]).pack(pady=(22, 2))
        ctk.CTkLabel(fenster, text=gid, font=schrift(11),
                     text_color=FARBE["text_leise"]).pack()
        ctk.CTkLabel(fenster,
                     text="Schick diesen Code deinen Freunden. Wer ihn hat, ist dabei.",
                     font=schrift(12), text_color=FARBE["text"]).pack(pady=(14, 6))

        feld = ctk.CTkTextbox(fenster, height=70, corner_radius=RADIUS,
                              font=schrift(11), border_width=1,
                              border_color=FARBE["rand"],
                              fg_color=FARBE["flaeche_tief"], wrap="char")
        feld.pack(fill="x", padx=24)
        feld.insert("1.0", code)
        feld.configure(state="disabled")

        def kopieren():
            self.clipboard_clear()
            self.clipboard_append(code)
            self.status("Einladungscode kopiert.", "gut")

        reihe = ctk.CTkFrame(fenster, fg_color="transparent")
        reihe.pack(pady=12)
        knopf(reihe, "Code kopieren", kopieren, art="primaer",
              breite=170, hoehe=36).pack(side="left", padx=5)
        knopf(reihe, "Schließen", fenster.destroy, art="leise",
              breite=120, hoehe=36).pack(side="left", padx=5)

        ctk.CTkLabel(fenster,
                     text="⚠  Im Code steckt der Schlüssel der Gruppe. Wer ihn "
                          "bekommt, kann alles mitlesen – auch Älteres, das noch "
                          "im Kanal steht. Zurückholen lässt er sich nicht; wenn "
                          "jemand raus soll, macht ihr eine neue Gruppe auf.",
                     font=schrift(10), text_color=FARBE["warn"], justify="left",
                     wraplength=500).pack(padx=24)

    def _freund_hinzufuegen(self):
        fid = self._text_abfragen("Freund hinzufügen",
                                  "ID deines Freundes (Form ABCD-1234):")
        if not fid:
            return
        name = self._text_abfragen("Name", "Wie soll er in der Liste heißen? "
                                           "(kann leer bleiben)") or ""
        try:
            self.friends.add(fid, name)
        except ValueError as e:
            messagebox.showerror("Fehler", str(e), parent=self)
            return
        self.status(f"'{fid}' zur Freundesliste hinzugefügt.", "gut")

    def _eintrag_entfernen(self):
        """Entfernt den gewählten Eintrag - einen Freund oder eine Gruppe."""
        gewaehlt = self.aktiver_chat
        if not gewaehlt:
            self.status("Bitte zuerst jemanden auswählen.", "warn")
            return

        if speicher.ist_gruppen_id(gewaehlt):
            gruppe = self.gruppen.get(gewaehlt) or {}
            name = gruppe.get("name") or gewaehlt
            if not messagebox.askyesno(
                    "Gruppe verlassen?",
                    f"'{name}' verlassen?\n\nDu bekommst dann nichts mehr aus "
                    f"dieser Gruppe. Zurück kommst du nur über den Code – heb "
                    f"ihn dir auf, wenn du dir nicht sicher bist.",
                    parent=self):
                return
            self.gruppen.verlassen(gewaehlt)
            self.status(f"Gruppe '{name}' verlassen.", "warn")
        else:
            if not messagebox.askyesno(
                    "Entfernen?", f"'{gewaehlt}' aus der Freundesliste entfernen?",
                    parent=self):
                return
            self.friends.remove(gewaehlt)

        self.aktiver_chat = None
        self.chat_titel.configure(text="Niemand ausgewählt")
        self._chat_verlauf_zeichnen(None)

    def _gemeinsamen_schluessel_setzen(self):
        if not self.aktiver_chat:
            self.status("Bitte zuerst einen Freund auswählen.", "warn")
            return
        if speicher.ist_gruppen_id(self.aktiver_chat):
            # Eine Gruppe hat ihren Schlüssel schon - er steckt im Code.
            self.status("Eine Gruppe hat ihren eigenen Schlüssel - "
                        "gib den Code weiter.", "warn")
            self._gruppen_code_zeigen()
            return
        fenster = ctk.CTkToplevel(self)
        fenster.title("Gemeinsamer Schlüssel")
        fenster.geometry("560x340")
        fenster.transient(self)
        fenster.after(100, fenster.grab_set)

        ctk.CTkLabel(fenster, text="Gemeinsamer Schlüssel",
                     font=schrift(17, True)).pack(pady=(20, 4))
        ctk.CTkLabel(fenster,
                     text=f"Für {self.aktiver_chat}. Ihr müsst BEIDE genau denselben\n"
                          f"Schlüssel eintragen – erst dann sind eure Nachrichten\n"
                          f"und Dateien verschlüsselt.",
                     font=schrift(11), text_color=FARBE["text_leise"],
                     justify="center").pack(pady=(0, 14))

        feld = ctk.CTkTextbox(fenster, height=80,
                              font=ctk.CTkFont(family="Consolas", size=11))
        feld.pack(fill="x", padx=26)
        vorhanden = (self.friends.get(self.aktiver_chat) or {}).get("shared_key_b64")
        if vorhanden:
            feld.insert("1.0", vorhanden)

        def erzeugen():
            feld.delete("1.0", "end")
            feld.insert("1.0", ModernCrypto.generate_aes_key())

        def sichern():
            wert = feld.get("1.0", "end-1c").strip()
            self.friends.set_shared_key(self.aktiver_chat, wert or None)
            self.status("Gemeinsamer Schlüssel gespeichert.", "gut")
            self._chat_oeffnen(self.aktiver_chat)
            fenster.destroy()

        reihe = ctk.CTkFrame(fenster, fg_color="transparent")
        reihe.pack(pady=18)
        ctk.CTkButton(reihe, text="Neuen erzeugen", width=150, command=erzeugen,
                      fg_color="transparent", border_width=1,
                      border_color=FARBE["rand"], text_color=FARBE["text"]).pack(side="left", padx=5)
        ctk.CTkButton(reihe, text="Speichern", width=150, command=sichern,
                      fg_color=FARBE["akzent"], text_color="#ffffff",
                      hover_color=FARBE["akzent_hover"]).pack(side="left", padx=5)

        ctk.CTkLabel(fenster,
                     text="Schick den Schlüssel deinem Freund auf einem anderen Weg –\n"
                          "nicht über diesen Chat selbst.",
                     font=schrift(10), text_color=FARBE["warn"]).pack()

    def _chat_senden(self):
        if not self.aktiver_chat:
            self.status("Bitte zuerst links jemanden auswählen.", "warn")
            return
        text = self.chat_eingabe.get().strip()
        if not text:
            return
        try:
            # ValueError gehört mit dazu: über Discord wird nur Verschlüsseltes
            # verschickt, und fehlt der gemeinsame Schlüssel, ist genau das der
            # Fehler. Ohne ihn hier abzufangen, stirbt der Rückruf still - in
            # einem Fenster ohne Konsole sieht das niemand.
            verschluesselt, weg = self.network.send_text_mit_weg(
                self.aktiver_chat, text)
        except (ConnectionError, OSError, ValueError) as e:
            self.status(str(e).split("\n")[0], "fehler")
            self._chat_zeile(self.aktiver_chat, f"   ✗ nicht zugestellt: {text}")
            messagebox.showerror("Senden fehlgeschlagen", str(e), parent=self)
            return
        self.chat_eingabe.delete(0, "end")
        zeit = datetime.now().strftime("%H:%M")
        self._chat_zeile(self.aktiver_chat,
                         f"[{zeit}] Du {'🔒' if verschluesselt else '🔓'}"
                         f"{self._weg_zeichen(weg)}: {text}")

    def _datei_senden(self):
        if not self.aktiver_chat:
            self.status("Bitte zuerst links jemanden auswählen.", "warn")
            return
        pfad = filedialog.askopenfilename(title="Datei senden")
        if not pfad:
            return
        endung = Path(pfad).suffix.lower()
        art = ("bild" if endung in (".png", ".jpg", ".jpeg", ".gif", ".bmp", ".webp")
               else "video" if endung in (".mp4", ".mov", ".avi", ".mkv")
               else "datei")
        try:
            verschluesselt, weg = self.network.send_file_mit_weg(
                self.aktiver_chat, pfad, art)
        except (ConnectionError, OSError, ValueError) as e:
            self.status(str(e).split("\n")[0], "fehler")
            messagebox.showerror("Senden fehlgeschlagen", str(e), parent=self)
            return
        zeit = datetime.now().strftime("%H:%M")
        self._chat_zeile(self.aktiver_chat,
                         f"[{zeit}] Du {'🔒' if verschluesselt else '🔓'}"
                         f"{self._weg_zeichen(weg)}: "
                         f"📎 {Path(pfad).name} gesendet")

    def _absendername(self, fid: str, selbstauskunft: str = None) -> str:
        """Der Name für eine ID.

        Reihenfolge: der Name, den man selbst vergeben hat, dann der Name,
        den der Absender in der Gruppe angibt, sonst die ID.

        Der eigene Name geht vor, weil der mitgeschickte nur eine Behauptung
        ist - nachprüfen kann ihn niemand. Wer bei dir "Max" heißt, bleibt
        "Max", auch wenn sich jemand anders so nennt.
        """
        eigener = (self.friends.get(fid) or {}).get("nickname")
        if eigener:
            return eigener
        if selbstauskunft:
            return f"{selbstauskunft} ({fid})"
        return fid

    def _benachrichtigung(self, name: str, gruppe) -> str:
        if gruppe:
            gruppen_name = (self.gruppen.get(gruppe) or {}).get("name") or gruppe
            return f"Neue Nachricht von {name} in '{gruppen_name}'."
        return f"Neue Nachricht von {name}."

    @staticmethod
    def _weg_zeichen(weg) -> str:
        """🌐 markiert eine Zeile, die über Discord gegangen ist.

        Im WLAN steht nichts dahinter - das ist der Normalfall und soll die
        Zeile nicht vollstellen. Sichtbar wird nur der Umweg übers Internet,
        denn dort landet die Nachricht auf einem fremden Server.
        """
        return "🌐" if weg == "discord" else ""

    # =================================================== Seite: Einstellungen

    def _seite_einstellungen(self, seite):
        seite.grid_columnconfigure(0, weight=1)

        kopf = ctk.CTkFrame(seite, fg_color="transparent")
        kopf.grid(row=0, column=0, sticky="ew", padx=26, pady=(22, 10))
        ctk.CTkLabel(kopf, text="Einstellungen", font=schrift(22, True),
                     text_color=FARBE["text"]).pack(anchor="w")

        # --- Darstellung ----------------------------------------------------
        block = self._einstellungsblock(seite, 1, "Darstellung")
        reihe = ctk.CTkFrame(block, fg_color="transparent")
        reihe.pack(fill="x", padx=18, pady=(0, 16))
        ctk.CTkLabel(reihe, text="Design", font=schrift(12),
                     text_color=FARBE["text"]).pack(side="left")
        self.design_wahl = ctk.CTkOptionMenu(
            reihe, values=["Dunkel", "Hell", "Wie das System"], width=180, height=32,
            corner_radius=RADIUS, font=schrift(12), fg_color=FARBE["akzent"], text_color="#ffffff",
            button_color=FARBE["akzent"], button_hover_color=FARBE["akzent_hover"],
            command=self._design_gewaehlt)
        self.design_wahl.pack(side="right")
        self.design_wahl.set({"dark": "Dunkel", "light": "Hell",
                              "system": "Wie das System"}.get(
                                  self.config_data.get("design", "dark"), "Dunkel"))

        reihe = ctk.CTkFrame(block, fg_color="transparent")
        reihe.pack(fill="x", padx=18, pady=(0, 4))
        ctk.CTkLabel(reihe, text="Akzentfarbe", font=schrift(12),
                     text_color=FARBE["text"]).pack(side="left")
        self.farb_wahl = ctk.CTkOptionMenu(
            reihe, values=[AKZENT_NAMEN[s] for s in AKZENTE], width=180, height=32,
            corner_radius=RADIUS, font=schrift(12), fg_color=FARBE["akzent"], text_color="#ffffff",
            button_color=FARBE["akzent"], button_hover_color=FARBE["akzent_hover"],
            command=self._farbe_gewaehlt)
        self.farb_wahl.pack(side="right")
        self.farb_wahl.set(AKZENT_NAMEN.get(
            self.config_data.get("farbe", "blau"), "Blau"))
        ctk.CTkLabel(block,
                     text="Wirkt sofort – die Oberfläche wird kurz neu gezeichnet.",
                     font=schrift(11), text_color=FARBE["text_leise"],
                     anchor="w").pack(anchor="w", padx=18, pady=(0, 16))

        # --- Sicherheit -----------------------------------------------------
        block = self._einstellungsblock(seite, 2, "Sicherheit")
        ctk.CTkButton(block, text="Master-Passwort ändern", height=36, width=220,
                      corner_radius=RADIUS, font=schrift(12), fg_color=FARBE["akzent"], text_color="#ffffff",
                      hover_color=FARBE["akzent_hover"],
                      command=self._passwort_aendern).pack(anchor="w", padx=18, pady=(0, 16))

        # --- Chat -----------------------------------------------------------
        block = self._einstellungsblock(seite, 3, "Chat")
        reihe = ctk.CTkFrame(block, fg_color="transparent")
        reihe.pack(fill="x", padx=18, pady=(0, 6))
        ctk.CTkLabel(reihe, text="Chat beim Start einschalten", font=schrift(12),
                     text_color=FARBE["text"]).pack(side="left")
        self.chat_schalter = ctk.CTkSwitch(reihe, text="", command=self._chat_umschalten,
                                           progress_color=FARBE["akzent"])
        self.chat_schalter.pack(side="right")
        if self.config_data.get("chat_aktiv", True):
            self.chat_schalter.select()
        ctk.CTkLabel(block,
                     text=f"Ports: {self.network.broadcast_port} (Erkennung) und "
                          f"{self.network.chat_port} (Chat). Beide liegen bewusst "
                          f"unter 49152 – darüber vergibt Windows Ports selbst, "
                          f"wodurch der Chat-Server manchmal gar nicht startete.",
                     font=schrift(10), text_color=FARBE["text_leise"],
                     justify="left", wraplength=660).pack(anchor="w", padx=18, pady=(0, 14))

        # --- Dein Name ------------------------------------------------------
        reihe = ctk.CTkFrame(block, fg_color="transparent")
        reihe.pack(fill="x", padx=18, pady=(0, 14))
        ctk.CTkLabel(reihe, text="Dein Name", font=schrift(12), width=170,
                     anchor="w", text_color=FARBE["text"]).pack(side="left")
        self.name_feld = ctk.CTkEntry(
            reihe, height=32, corner_radius=RADIUS, font=schrift(11),
            placeholder_text=f"leer = deine ID ({self.my_id})")
        self.name_feld.pack(side="left", fill="x", expand=True, padx=(8, 8))
        if self.config_data.get("anzeigename"):
            self.name_feld.insert(0, self.config_data["anzeigename"])
        knopf(reihe, "Merken", self._anzeigename_speichern, art="leise",
              breite=90, hoehe=32).pack(side="left")
        ctk.CTkLabel(block,
                     text="So tauchst du in Gruppen auf. In Einzelchats zählt "
                          "weiter der Name, den dein Freund dir gegeben hat.",
                     font=schrift(10), text_color=FARBE["text_leise"],
                     justify="left", wraplength=660).pack(anchor="w", padx=18,
                                                          pady=(0, 14))

        # --- Discord --------------------------------------------------------
        block = self._einstellungsblock(seite, 4, "Discord (Chat über das Internet)")

        reihe = ctk.CTkFrame(block, fg_color="transparent")
        reihe.pack(fill="x", padx=18, pady=(0, 10))
        ctk.CTkLabel(reihe, text="Transportweg", font=schrift(12),
                     text_color=FARBE["text"]).pack(side="left")
        self.transport_wahl = ctk.CTkOptionMenu(
            reihe, values=[transport.MODUS_NAMEN[m] for m in transport.MODI],
            width=210, height=32, corner_radius=RADIUS, font=schrift(12),
            fg_color=FARBE["akzent"], text_color="#ffffff",
            button_color=FARBE["akzent"], button_hover_color=FARBE["akzent_hover"],
            command=self._transport_gewaehlt)
        self.transport_wahl.pack(side="right")
        self.transport_wahl.set(transport.MODUS_NAMEN.get(
            self.config_data.get("transport_modus", "lan"),
            transport.MODUS_NAMEN["lan"]))

        d_cfg = discord_transport.discord_config_laden()

        reihe = ctk.CTkFrame(block, fg_color="transparent")
        reihe.pack(fill="x", padx=18, pady=(0, 6))
        ctk.CTkLabel(reihe, text="Bot-Token", font=schrift(12), width=90,
                     anchor="w", text_color=FARBE["text"]).pack(side="left")
        self.discord_token_feld = ctk.CTkEntry(
            reihe, height=32, corner_radius=RADIUS, font=schrift(11), show="•",
            placeholder_text="aus dem Discord Developer-Portal")
        self.discord_token_feld.pack(side="left", fill="x", expand=True, padx=(8, 0))
        if d_cfg.get("bot_token"):
            self.discord_token_feld.insert(0, d_cfg["bot_token"])

        reihe = ctk.CTkFrame(block, fg_color="transparent")
        reihe.pack(fill="x", padx=18, pady=(0, 8))
        ctk.CTkLabel(reihe, text="Kanal-ID", font=schrift(12), width=90,
                     anchor="w", text_color=FARBE["text"]).pack(side="left")
        self.discord_kanal_feld = ctk.CTkEntry(
            reihe, height=32, corner_radius=RADIUS, font=schrift(11),
            placeholder_text="Rechtsklick auf den Kanal → Kanal-ID kopieren")
        self.discord_kanal_feld.pack(side="left", fill="x", expand=True, padx=(8, 0))
        if d_cfg.get("kanal_id"):
            self.discord_kanal_feld.insert(0, str(d_cfg["kanal_id"]))

        reihe = ctk.CTkFrame(block, fg_color="transparent")
        reihe.pack(fill="x", padx=18, pady=(0, 8))
        knopf(reihe, "Speichern und verbinden", self._discord_speichern,
              art="primaer", breite=210, hoehe=34).pack(side="left")
        self.discord_status = ctk.CTkLabel(
            reihe, text=self._discord_statustext(), font=schrift(11),
            text_color=FARBE["text_leise"])
        self.discord_status.pack(side="left", padx=12)

        ctk.CTkLabel(block,
                     text="Alle Freunde brauchen denselben Bot-Token und dieselbe "
                          "Kanal-ID. Im Developer-Portal muss beim Bot zusätzlich "
                          "'MESSAGE CONTENT INTENT' eingeschaltet sein – sonst "
                          "kommen alle Nachrichten leer an.",
                     font=schrift(10), text_color=FARBE["text_leise"],
                     justify="left", wraplength=660).pack(anchor="w", padx=18, pady=(0, 6))
        ctk.CTkLabel(block,
                     text="⚠  Über Discord wird nur verschlüsselt gesendet – der "
                          "Kanal ist für alle im Server lesbar und Discord speichert "
                          "alles dauerhaft. Wer wann wem schreibt, sieht Discord "
                          "trotzdem mit. Im WLAN bleibt alles im Haus.",
                     font=schrift(10), text_color=FARBE["warn"],
                     justify="left", wraplength=660).pack(anchor="w", padx=18, pady=(0, 14))

        # --- Daten ----------------------------------------------------------
        block = self._einstellungsblock(seite, 5, "Daten")
        ctk.CTkLabel(block, text=f"Deine Dateien liegen in:\n{speicher.DATA_DIR}",
                     font=schrift(11), text_color=FARBE["text_leise"],
                     justify="left").pack(anchor="w", padx=18, pady=(0, 8))
        ctk.CTkButton(block, text="Ordner öffnen", height=32, width=160,
                      corner_radius=RADIUS, font=schrift(11), fg_color="transparent",
                      border_width=1, border_color=FARBE["rand"],
                      text_color=FARBE["text"], hover_color=FARBE["flaeche_tief"],
                      command=self._datenordner_oeffnen).pack(anchor="w", padx=18,
                                                              pady=(0, 16))

    def _anzeigename_speichern(self):
        name = self.name_feld.get().strip()[:40]
        self.config_data["anzeigename"] = name
        speicher.save_config(self.config_data)
        # Der laufende Discord-Transport hält den Namen selbst - ohne das
        # hier stünde in Gruppen bis zum Neustart weiter der alte.
        self.network.anzeigename = name
        if self.network.discord is not None:
            self.network.discord.anzeigename = name
        self.status(f"In Gruppen heißt du jetzt "
                    f"'{name or self.my_id}'.", "gut")

    def _chat_ueberschrift_text(self) -> str:
        return {
            # Bei "beide" sucht sich VP4 den Weg selbst - das ist der
            # Normalfall und muss niemanden beschäftigen. Nur wer den Weg
            # ausdrücklich festgelegt hat, soll ihn auch oben lesen.
            "lan": "Chat im WLAN",
            "discord": "Chat über Discord",
            "beide": "Chat",
        }.get(self.config_data.get("transport_modus", "lan"), "Chat")

    def _discord_statustext(self) -> str:
        if self.config_data.get("transport_modus", "lan") == "lan":
            return "aus"
        if self.network.discord_verbunden:
            return "verbunden"
        if not discord_transport.ist_eingerichtet():
            return "noch nicht eingerichtet"
        return "verbindet …"

    def _transport_gewaehlt(self, wahl):
        modus = {v: k for k, v in transport.MODUS_NAMEN.items()}[wahl]
        self.config_data["transport_modus"] = modus
        speicher.save_config(self.config_data)
        self.network.modus_setzen(modus, discord_transport.discord_config_laden())
        self.discord_status.configure(text=self._discord_statustext())
        # Die Chatseite ist eventuell längst gebaut - ihre Überschrift muss
        # mitwandern, sonst steht dort weiter der alte Weg.
        if getattr(self, "chat_ueberschrift", None) is not None:
            try:
                self.chat_ueberschrift.configure(text=self._chat_ueberschrift_text())
            except Exception:
                pass
        self.status(f"Transportweg: {wahl}.", "gut")

    def _discord_speichern(self):
        """Nimmt Token und Kanal-ID entgegen und baut die Verbindung neu auf."""
        token = self.discord_token_feld.get().strip()
        kanal = self.discord_kanal_feld.get().strip()

        if kanal and not kanal.isdigit():
            self.status("Die Kanal-ID muss eine reine Zahl sein.", "fehler")
            messagebox.showerror(
                "Kanal-ID stimmt nicht",
                "Eine Discord-Kanal-ID besteht nur aus Ziffern.\n\n"
                "So findest du sie:\n"
                "1. Discord → Einstellungen → Erweitert → Entwicklermodus an\n"
                "2. Rechtsklick auf den Textkanal → 'Kanal-ID kopieren'",
                parent=self)
            return

        discord_transport.discord_config_speichern(
            {"bot_token": token, "kanal_id": kanal})

        if self.config_data.get("transport_modus", "lan") == "lan":
            self.status("Gespeichert. Stell den Transportweg um, damit es "
                        "benutzt wird.", "warn")
        else:
            self.network.modus_setzen(self.config_data["transport_modus"],
                                      {"bot_token": token, "kanal_id": kanal})
            self.status("Gespeichert – verbinde mit Discord …", "gut")
        self.discord_status.configure(text=self._discord_statustext())

    def _einstellungsblock(self, eltern, zeile, titel):
        block = ctk.CTkFrame(eltern, fg_color=FARBE["flaeche_tief"], corner_radius=RADIUS)
        block.grid(row=zeile, column=0, sticky="ew", padx=26, pady=6)
        ctk.CTkLabel(block, text=titel, font=schrift(13, True),
                     text_color=FARBE["text"]).pack(anchor="w", padx=18, pady=(14, 8))
        return block

    def _design_gewaehlt(self, wahl):
        modus = {"Dunkel": "dark", "Hell": "light", "Wie das System": "system"}[wahl]
        ctk.set_appearance_mode(modus)
        self.config_data["design"] = modus
        speicher.save_config(self.config_data)
        if modus == "dark":
            self.design_schalter.select()
        elif modus == "light":
            self.design_schalter.deselect()
        self._treeview_stil()

    def _farbe_gewaehlt(self, wahl):
        umgekehrt = {name: schluessel for schluessel, name in AKZENT_NAMEN.items()}
        self.config_data["farbe"] = umgekehrt.get(wahl, "blau")
        speicher.save_config(self.config_data)

        if self.auftrag_laeuft:
            self.status(f"Akzentfarbe: {wahl}. Sichtbar, sobald der laufende "
                        f"Auftrag fertig ist.", "warn")
            return

        akzent_setzen(self.config_data["farbe"])
        self._oberflaeche_neu_bauen()
        self.status(f"Akzentfarbe: {wahl}.", "gut")

    def _chat_umschalten(self):
        an = bool(self.chat_schalter.get())
        self.config_data["chat_aktiv"] = an
        speicher.save_config(self.config_data)
        if an and not self.network.server_laeuft:
            self.network.start()
            self.status("Chat eingeschaltet.", "gut")
        elif not an:
            self.network.stop()
            self.chat_status.configure(text="Chat: aus")
            self.status("Chat ausgeschaltet.", "warn")

    def _passwort_aendern(self):
        alt = self._text_abfragen("Master-Passwort ändern",
                                  "Aktuelles Passwort:", geheim=True)
        if not alt:
            return
        neu = self._text_abfragen("Master-Passwort ändern",
                                  "Neues Passwort (mindestens 8 Zeichen):", geheim=True)
        if not neu:
            return
        if len(neu) < 8:
            messagebox.showerror("Zu kurz", "Bitte mindestens 8 Zeichen.", parent=self)
            return
        nochmal = self._text_abfragen("Master-Passwort ändern",
                                      "Neues Passwort wiederholen:", geheim=True)
        if neu != nochmal:
            messagebox.showerror("Nicht gleich",
                                 "Die beiden neuen Passwörter stimmen nicht überein.",
                                 parent=self)
            return
        try:
            self.keystore.change_password(alt, neu)
        except FalschesPasswortError:
            messagebox.showerror("Falsch", "Das aktuelle Passwort stimmt nicht.",
                                 parent=self)
            return
        except Exception as e:
            messagebox.showerror("Fehler", str(e), parent=self)
            return
        self.status("Master-Passwort geändert.", "gut")
        messagebox.showinfo("Geändert",
                            "Das Master-Passwort wurde geändert.\n\n"
                            "Merk dir das neue gut – auch dieses lässt sich nicht "
                            "wiederherstellen.", parent=self)

    def _datenordner_oeffnen(self):
        speicher.ordner_anlegen()
        try:
            if sys.platform == "win32":
                os.startfile(speicher.DATA_DIR)
            elif sys.platform == "darwin":
                subprocess.run(["open", str(speicher.DATA_DIR)])
            else:
                subprocess.run(["xdg-open", str(speicher.DATA_DIR)])
        except Exception as e:
            self.status(f"Konnte den Ordner nicht öffnen: {e}", "fehler")

    # ============================================================ Seite: Info

    def _seite_info(self, seite):
        seite.grid_columnconfigure(0, weight=1)
        seite.grid_rowconfigure(1, weight=1)

        ctk.CTkLabel(seite, text="Info & Hilfe", font=schrift(22, True),
                     text_color=FARBE["text"]).grid(row=0, column=0, sticky="w",
                                                    padx=26, pady=(22, 10))

        text = ctk.CTkTextbox(seite, corner_radius=RADIUS, font=schrift(12),
                              fg_color=FARBE["flaeche_tief"], border_width=1,
                              border_color=FARBE["rand"])
        text.grid(row=1, column=0, sticky="nsew", padx=26, pady=(0, 22))
        text.insert("1.0", self._infotext())
        text.configure(state="disabled")

    def _infotext(self):
        sicher = [n for n, i in VERFAHREN.items() if i["art"] == "sicher"]
        spiel = [n for n, i in VERFAHREN.items() if i["art"] == "spiel"]
        return f"""VERSCHLÜSSELUNGS PROGRAMM 4.0

Ein Programm zum Ver- und Entschlüsseln, zum Verwalten von Schlüsseln
und zum Chatten mit Freunden. Verschlüsselt und entschlüsselt wird
alles auf deinem Rechner, und es wird keine KI dafür gebraucht. Ins
Internet geht nur, was du selbst über den Chat verschickst – und auch
das nur verschlüsselt.


WAS IST WIRKLICH SICHER?

Sicher ({len(sicher)}):  {', '.join(sicher)}
   Das sind echte, heute gebräuchliche Verfahren. Sie gelten als
   nicht knackbar, solange dein Schlüssel geheim bleibt.

Nur zum Spielen ({len(spiel)}):  {', '.join(spiel)}
   Historische Verfahren. Sie sind interessant und machen Spaß, aber
   ein Computer knackt sie in Sekunden. Nichts Wichtiges damit schützen.


DATEIEN UND ORDNER

Auf der Seite "Dateien" verschlüsselst du ganze Dateien oder komplette
Ordner zu einer .vp4-Datei. Die Grösse spielt keine Rolle - die Daten
wandern blockweise durch und müssen nie ganz in den Arbeitsspeicher
passen. Ein laufender Vorgang lässt sich jederzeit abbrechen.

Der Dateiname steckt mit im verschlüsselten Teil. Von aussen sieht man
nur "Zeugnis.pdf.vp4" - also den Namen ruhig noch ändern, wenn schon
der etwas verraten würde.

Wird an einer .vp4-Datei etwas verändert, abgeschnitten oder
umsortiert, fällt das beim Entschlüsseln auf. Du bekommst dann einen
Fehler statt halb richtiger Daten.

Das Original bleibt liegen: VP4 löscht von sich aus nichts.


DAS MASTER-PASSWORT

Dein Schlüsselspeicher ist mit deinem Master-Passwort verschlüsselt.
Aus dem Passwort wird der Schlüssel mit Argon2id berechnet – einem
Verfahren, das absichtlich Zeit und 64 MB Arbeitsspeicher verbraucht.
Der Speicherbedarf ist der eigentliche Trick: Daran scheitern
Grafikkarten, die sonst Tausende Passwörter gleichzeitig durchprobieren
könnten.

Das Passwort selbst wird nirgends gespeichert – auch nicht in
verschlüsselter Form. Wenn du es vergisst, kommt niemand mehr an die
gespeicherten Schlüssel heran, auch dieses Programm nicht. Es gibt
keine Hintertür.

Das ist unbequem, aber der Grund, warum der Speicher überhaupt etwas
taugt: Jeder Weg zurück wäre auch ein Weg für jemand anderen.


DER CHAT

Beim ersten Start bekommt jede Installation eine eigene ID. Tausche
sie mit Freunden, dann könnt ihr chatten und Bilder oder Videos
schicken. Sitzt ihr im selben WLAN, läuft es direkt zwischen euren
Rechnern – ohne Server, ohne Internet. Sonst nimmt VP4 den Umweg über
einen Discord-Kanal; verschlüsselt wird dabei genauso, Discord
bekommt nur den Geheimtext zu sehen.

Eine Zeile im Chat zeigt mit 🌐, wenn sie diesen Umweg genommen hat.


GRUPPEN

Neben einzelnen Freunden gibt es Gruppen. "＋ Gruppe" macht eine neue
auf, "Beitreten" bringt dich in eine fremde, und "Code" zeigt den
Einladungscode der gewählten Gruppe.

Der Code ist die ganze Mitgliedschaft: Wer ihn hat, ist dabei – eine
Liste, wer dazugehört, gibt es nicht. Das macht das Beitreten einfach
und hat einen Preis: Zurückholen lässt sich ein Code nicht. Soll
jemand nicht mehr mitlesen, macht ihr eine neue Gruppe auf.

In einer Gruppe kennt ihr euch nicht gegenseitig als Freunde. Deshalb
schickt jeder seinen Namen mit – den, der unter Einstellungen bei
"Dein Name" steht. Nachprüfen kann den niemand, also steht die ID in
Klammern dahinter. Wen du selbst in deiner Freundesliste hast, siehst
du immer unter dem Namen, den du ihm gegeben hast.

Gruppen laufen immer über Discord, auch wenn ihr im selben WLAN sitzt:
Im WLAN müsste VP4 wissen, an wen es die Nachricht schicken soll – und
genau das weiß bei einer Gruppe niemand.


WIE GUT SIND DIE NACHRICHTEN GESCHÜTZT?

Das hängt davon ab, welcher Schlüssel benutzt wird – und das steht
oben im Chat und neben jedem Freund:

  🔒  Ein eigener Schlüssel, den nur ihr zwei habt (🔑-Knopf in der
      Freundesliste). Niemand sonst kann mitlesen, auch kein anderer
      VP4-Benutzer.

  👥  Der eingebaute Gruppenschlüssel. Er steckt in dieser Programm-
      datei, damit ihr sofort loslegen könnt, ohne etwas auszutauschen.
      Gegen Discord und gegen Fremde schützt er vollständig – aber
      jeder, der dasselbe VP4 hat, kann alles im Kanal mitlesen, auch
      Nachrichten zwischen zwei anderen. Es ist also eher ein
      Gruppenchat als ein privates Gespräch.

  🔓  Gar kein Schlüssel. Das geht nur im WLAN; über Discord wird
      unverschlüsselt grundsätzlich nichts verschickt.

Willst du mit einem bestimmten Freund wirklich unter vier Augen
schreiben, trag für ihn einen eigenen Schlüssel ein. Der geht dem
Gruppenschlüssel immer vor.

Ehrlich gesagt: Beim Verbinden wird nur behauptet, wer man ist –
nachgeprüft wird es nicht. Wer im selben WLAN sitzt oder im selben
Discord-Kanal ist, könnte sich als ein Freund von dir ausgeben. Für
ein Programm unter Freunden ist das in Ordnung, für Vertrauliches
nicht.


WENN DER CHAT NICHT GEHT

- Steht unten rechts, dass der Chat läuft? Dort steht auch, welcher
  Weg gerade offen ist. Ein Fehler an dieser Stelle erklärt meistens
  schon alles.
- Habt ihr beide dieselbe Programmversion? Der Zugang zu Discord
  steckt in der Programmdatei – eine alte .exe kennt ihn noch nicht.
- Soll es direkt übers WLAN gehen: Seid ihr wirklich im selben Netz?
  Ein Gäste-WLAN trennt die Geräte oft voneinander ab. Und die
  Windows-Firewall muss VP4 erlauben, sonst kommt nichts an.
- Über Discord braucht nur einer von euch beiden online zu sein, um
  zu schreiben – die Nachricht wartet dann im Kanal.


EHRLICHER HINWEIS

Das ist ein privates Programm, kein geprüftes Sicherheitsprodukt. Für
wirklich Wichtiges – Bankdaten, Passwörter für echte Konten – sollte
es nicht die einzige Absicherung sein.
"""

    # ==================================================== Hilfsdialoge

    def _text_abfragen(self, titel, frage, geheim=False):
        """Ein einfacher Eingabedialog im Stil der übrigen Oberfläche."""
        fenster = ctk.CTkToplevel(self)
        fenster.title(titel)
        fenster.geometry("420x200")
        fenster.resizable(False, False)
        fenster.transient(self)
        fenster.after(100, fenster.grab_set)

        ergebnis = {"wert": None}

        ctk.CTkLabel(fenster, text=frage, font=schrift(12), wraplength=360,
                     justify="left").pack(padx=26, pady=(26, 10))
        feld = ctk.CTkEntry(fenster, width=340, height=38, corner_radius=RADIUS,
                            font=schrift(13), show="•" if geheim else "")
        feld.pack(padx=26)
        feld.focus_set()

        def ok():
            ergebnis["wert"] = feld.get()
            fenster.destroy()

        feld.bind("<Return>", lambda e: ok())
        reihe = ctk.CTkFrame(fenster, fg_color="transparent")
        reihe.pack(pady=20)
        ctk.CTkButton(reihe, text="OK", width=120, height=34, corner_radius=RADIUS,
                      fg_color=FARBE["akzent"], text_color="#ffffff", hover_color=FARBE["akzent_hover"],
                      command=ok).pack(side="left", padx=5)
        ctk.CTkButton(reihe, text="Abbrechen", width=120, height=34,
                      corner_radius=RADIUS, fg_color="transparent", border_width=1,
                      border_color=FARBE["rand"], text_color=FARBE["text"],
                      command=fenster.destroy).pack(side="left", padx=5)

        self.wait_window(fenster)
        return ergebnis["wert"]

    def _auswahl_abfragen(self, titel, frage, moeglichkeiten):
        """Lässt einen Eintrag aus einer Liste wählen. Gibt den Index zurück."""
        fenster = ctk.CTkToplevel(self)
        fenster.title(titel)
        fenster.geometry("460x400")
        fenster.transient(self)
        fenster.after(100, fenster.grab_set)

        ergebnis = {"index": None}

        ctk.CTkLabel(fenster, text=frage, font=schrift(12)).pack(pady=(22, 10))
        liste = ctk.CTkScrollableFrame(fenster, fg_color="transparent")
        liste.pack(fill="both", expand=True, padx=22)

        def waehlen(i):
            ergebnis["index"] = i
            fenster.destroy()

        for i, eintrag in enumerate(moeglichkeiten):
            ctk.CTkButton(liste, text=eintrag, anchor="w", height=36,
                          corner_radius=8, font=schrift(12), fg_color="transparent",
                          border_width=1, border_color=FARBE["rand"],
                          text_color=FARBE["text"], hover_color=FARBE["flaeche_tief"],
                          command=lambda i=i: waehlen(i)).pack(fill="x", pady=2)

        ctk.CTkButton(fenster, text="Abbrechen", height=34, corner_radius=RADIUS,
                      fg_color="transparent", border_width=1,
                      border_color=FARBE["rand"], text_color=FARBE["text"],
                      command=fenster.destroy).pack(pady=14, padx=22, fill="x")

        self.wait_window(fenster)
        return ergebnis["index"]

    # ================================================ Ereignisse vom Netzwerk

    def _ereignisse_pruefen(self):
        """Arbeitet die Warteschlange ab und meldet sich gleich wieder an."""
        self._ereignisse_abarbeiten()
        self._spaeter(300, self._ereignisse_pruefen)

    def _ereignisse_abarbeiten(self):
        """Holt ab, was die Hintergrund-Threads gemeldet haben.

        Das läuft im Hauptthread - nur von hier aus darf die Oberfläche
        angefasst werden. Getrennt vom Wiederanmelden, damit der Selbsttest
        die Warteschlange sofort leeren kann, statt auf den Takt zu warten.
        """
        try:
            while True:
                art, daten = self.ereignisse.get_nowait()

                if art == "message":
                    fid = daten["from"]
                    name = self._absendername(fid, daten.get("name"))
                    # In einer Gruppe gehört die Zeile in den Verlauf der
                    # Gruppe, nicht in den des Absenders - sonst tauchte eine
                    # Gruppennachricht in einem Einzelchat auf.
                    wohin = daten.get("gruppe") or fid
                    schloss = "🔒" if daten["encrypted"] else "🔓"
                    weg = self._weg_zeichen(daten.get("weg"))
                    zeit = datetime.now().strftime("%H:%M")
                    self._chat_zeile(wohin,
                                     f"[{zeit}] {name} {schloss}{weg}: {daten['text']}")
                    if wohin != self.aktiver_chat:
                        self.status(self._benachrichtigung(name, daten.get("gruppe")),
                                    "gut")

                elif art == "file":
                    fid = daten["from"]
                    name = self._absendername(fid)
                    wohin = daten.get("gruppe") or fid
                    weg = self._weg_zeichen(daten.get("weg"))
                    zeit = datetime.now().strftime("%H:%M")
                    self._chat_zeile(wohin, f"[{zeit}] {name}{weg}: 📎 {daten['name']}")
                    self._chat_zeile(wohin, f"          gespeichert unter {daten['path']}")
                    self.status(f"Datei von {name} empfangen.", "gut")

                elif art == "peer_update":
                    pass        # die Liste wird ohnehin regelmäßig neu gezeichnet

                elif art == "fortschritt":
                    anteil, getan, gesamt = daten
                    self.datei_balken.set(anteil)
                    self.datei_stand.configure(
                        text=f"{int(anteil * 100)} %  "
                             f"({dateien.groesse_lesbar(getan)})")

                elif art == "auftrag_fertig":
                    was, ergebnis = daten
                    self.datei_balken.set(1)
                    self.datei_stand.configure(text="fertig")
                    self._datei_protokoll(f"✅ {was}: {ergebnis}")
                    self.status(f"{was} fertig: {Path(ergebnis).name}", "gut")
                    self._auftrag_beendet()

                elif art == "auftrag_abgebrochen":
                    self.datei_balken.set(0)
                    self.datei_stand.configure(text="abgebrochen")
                    self._datei_protokoll(f"⛔ {daten} abgebrochen.")
                    self.status(f"{daten} abgebrochen.", "warn")
                    self._auftrag_beendet()

                elif art == "auftrag_fehler":
                    was, meldung = daten
                    self.datei_balken.set(0)
                    self.datei_stand.configure(text="Fehler")
                    self._datei_protokoll(f"❌ {was}: {meldung}")
                    self.status(meldung.split(chr(10))[0], "fehler")
                    messagebox.showerror(was, meldung, parent=self)
                    self._auftrag_beendet()

                elif art == "server_bereit":
                    self.chat_status.configure(text=f"Chat: bereit (Port {daten})",
                                               text_color=FARBE["gut"])

                elif art == "discord_bereit":
                    # Ohne diese Rückmeldung stünde in den Einstellungen für
                    # immer "verbindet …", auch wenn der Bot längst drin ist.
                    wo = ("WLAN + Discord"
                          if self.config_data.get("transport_modus") == "beide"
                          else "Discord")
                    self.chat_status.configure(text=f"Chat: {wo} ({daten})",
                                               text_color=FARBE["gut"])
                    self.status(f"Mit Discord verbunden als {daten}.", "gut")
                    if getattr(self, "discord_status", None) is not None:
                        try:
                            self.discord_status.configure(
                                text=self._discord_statustext())
                        except Exception:
                            # Die Einstellungsseite kann seit dem Verbinden neu
                            # gebaut worden sein - dann ist das Feld weg, und
                            # das ist kein Grund, den Chat anzuhalten.
                            pass

                elif art == "error":
                    # Früher ging das über print() ins Leere - in einem Fenster
                    # ohne Konsole sieht das niemand, und in der .exe gibt es
                    # gar keine. Jetzt landet es sichtbar in der Statusleiste,
                    # schwere Startfehler zusätzlich als Dialog.
                    self.status(daten.split("\n")[0], "fehler")
                    self.chat_status.configure(text="Chat: Fehler",
                                               text_color=FARBE["schlecht"])
                    if "konnte nicht starten" in daten.lower():
                        messagebox.showwarning("Chat nicht gestartet", daten, parent=self)

                elif art == "info":
                    self.status(daten, "warn")

        except queue.Empty:
            pass

    # ==================================================== Sperren / Schließen

    def _sperren(self):
        """Sperrt den Schlüsselspeicher und geht zurück zum Sperrbildschirm."""
        if not messagebox.askyesno("Sperren?",
                                   "VP4 sperren? Du musst dann dein Master-Passwort "
                                   "wieder eingeben.", parent=self):
            return
        if self.auftrag_laeuft:
            self.status("Erst den laufenden Auftrag abwarten oder abbrechen.",
                        "warn")
            return
        self.keystore.lock()
        self.network.stop()
        # Nur merken und schliessen. Früher rief diese Stelle starten() auf,
        # und weil starten() wieder ein mainloop() startet, stapelte jedes
        # Sperren eine weitere Fensterschleife auf den Stapel - nach ein
        # paar Runden war das ein Turm, der nie wieder abgebaut wurde.
        self._zeitgeber_stoppen()
        self.wieder_sperren = True
        self.destroy()

    def _schliessen(self):
        self.network.stop()
        self.keystore.lock()
        self._zeitgeber_stoppen()
        self.destroy()


# =============================================================================
#  Einstieg
# =============================================================================

def starten():
    """Startet VP4: erst der Sperrbildschirm, danach das Hauptfenster."""
    speicher.ordner_anlegen()
    config = speicher.load_config()

    # Muss vor dem ersten Fenster stehen, siehe akzent_setzen().
    akzent_setzen(config.get("farbe", "blau"))
    ctk.set_appearance_mode(config.get("design", "dark"))
    ctk.set_default_color_theme("blue")

    keystore = KeyStore(speicher.KEYSTORE_FILE)

    while True:
        if not _eine_sitzung(keystore, config):
            return


def _eine_sitzung(keystore, config) -> bool:
    """Sperrbildschirm und danach das Hauptfenster. Wahr = wieder sperren."""
    ersteinrichtung = not keystore.exists()

    sperre = Sperrbildschirm(keystore, ersteinrichtung)
    sperre.mainloop()

    if not sperre.erfolgreich:
        return False

    if ersteinrichtung:
        config["master_passwort_gesetzt"] = True
        speicher.save_config(config)

    app = VP4App(keystore, config)
    app.mainloop()
    return app.wieder_sperren
