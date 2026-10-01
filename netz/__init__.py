# -*- coding: utf-8 -*-
"""netz/ - die Transportwege von VP4 5.0.

    lan.py           direkt übers WLAN (UDP-Erkennung + TCP)
    discord_netz.py  über Discord-Textkanäle (Zeilen + Anhänge, Nachholen)
    vermittler.py    wählt pro Umschlag den Weg

Alle drei bewegen nur fertige Umschläge (kern/umschlag.py) als Bytes. Hier
wird nichts ver- oder entschlüsselt, und keine Absenderangabe aus dem Netz
wird geglaubt: Ob ein Umschlag echt ist, entscheidet allein die Krypto eine
Schicht höher. Was ein Transport über den Absender weiß (IP-Adresse,
Discord-Kanal), ist nur für die Weiterleitung und die Anzeige da.
"""
