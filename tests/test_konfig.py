# -*- coding: utf-8 -*-
"""Prüfungen rund um den eingebauten Discord-Zugang und die Beilagen."""

import re
from pathlib import Path

WURZEL = Path(__file__).resolve().parent.parent


def pruefen(R, hilfen):
    import importlib
    import discord_konfig
    importlib.reload(discord_konfig)
    R.pruefe("Im Quelltext steht kein echter Bot-Token",
             not discord_konfig.BOT_TOKEN.strip() and not str(discord_konfig.KANAL_IDS).strip(),
             "discord_konfig.py darf nur leere Platzhalter enthalten - die echten Werte setzt der Workflow ein.")
    R.pruefe("Den alten eingebauten Gruppenschlüssel gibt es nicht mehr",
             not hasattr(discord_konfig, "GRUPPEN_SCHLUESSEL")
             and "GRUPPEN_SCHLUESSEL" not in (WURZEL / ".github" / "workflows" / "release.yml").read_text(encoding="utf-8"))
    # Nirgends im Repo ein Discord-Token (drei Teile mit Punkten, typische Länge)
    muster = re.compile(r"[MNO][A-Za-z\d_-]{23,27}\.[A-Za-z\d_-]{6}\.[A-Za-z\d_-]{27,40}")
    fundstellen = [str(p.relative_to(WURZEL)) for p in WURZEL.rglob("*")
                   if p.is_file() and p.suffix in (".py", ".js", ".json", ".md", ".yml", ".html", ".txt")
                   and ".venv" not in p.parts and "screenshots" not in p.parts and "vp4_daten" not in p.parts
                   and muster.search(p.read_text(encoding="utf-8", errors="ignore"))]
    R.pruefe("Kein Discord-Token irgendwo im Quelltext", not fundstellen, ", ".join(fundstellen))
    anforderungen = (WURZEL / "requirements.txt").read_text(encoding="utf-8")
    R.pruefe("requirements.txt nennt alle Pakete",
             all(p in anforderungen for p in ("pywebview", "cryptography", "argon2-cffi", "PyNaCl", "pyrage", "discord.py")))
    R.pruefe("customtkinter ist raus", "customtkinter" not in anforderungen)
    from netz import lan
    R.pruefe("Chat-Ports liegen unter dem dynamischen Windows-Bereich (49152)",
             lan.CHAT_PORT < 49152 and lan.BROADCAST_PORT < 49152)
