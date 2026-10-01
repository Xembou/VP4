#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
=====================================================================
 icon_erzeugen.py - erzeugt das Programm-Icon (vp4.ico)
=====================================================================
Muss nur einmal laufen bzw. dann wieder, wenn das Icon anders aussehen
soll:

    python icon_erzeugen.py

Danach liegt vp4.ico im Ordner und wird beim Bauen der .exe verwendet.

Das Icon wird hier gezeichnet statt als Bilddatei mitgeliefert, damit
es sich jederzeit ändern lässt und keine fremde Grafik im Projekt liegt.
Gezeichnet wird eine Sprechblase mit Schloss auf blauem, abgerundetem Grund - in mehreren
Größen, damit es sowohl in der Taskleiste als auch in großer Ansicht
sauber aussieht.
=====================================================================
"""

from pathlib import Path

from PIL import Image, ImageDraw

# Apples Systemblau aus iOS/macOS 26 (#0088FF) - wie der Akzent der
# Oberfläche. Oben heller, unten etwas tiefer, damit es plastisch wirkt.
BLAU_OBEN = (74, 176, 255)
BLAU_UNTEN = (0, 104, 230)
WEISS = (255, 255, 255)


def _verlauf(g, oben, unten):
    verlauf = Image.new("RGB", (g, g))
    vd = ImageDraw.Draw(verlauf)
    for y in range(g):
        a = y / max(g - 1, 1)
        vd.line([(0, y), (g, y)], fill=tuple(round(o + (u - o) * a) for o, u in zip(oben, unten)))
    return verlauf


def schloss_zeichnen(kante: int) -> Image.Image:
    """Eine Sprechblase mit Schloss - VP4 ist jetzt zuerst ein Messenger.

    Gezeichnet wird viermal so groß und danach verkleinert - dadurch
    werden die Rundungen glatt statt ausgefranst.
    """
    f = 4
    g = kante * f
    bild = Image.new("RGBA", (g, g), (0, 0, 0, 0))

    # Grund: Verlauf auf abgerundetem Quadrat (macOS-Form, ~22 % Radius)
    maske = Image.new("L", (g, g), 0)
    ImageDraw.Draw(maske).rounded_rectangle([0, 0, g - 1, g - 1], radius=int(g * 0.225), fill=255)
    bild.paste(_verlauf(g, BLAU_OBEN, BLAU_UNTEN), (0, 0), maske)
    # Glanzkante oben, wie bei Liquid Glass
    glanz = Image.new("L", (g, g), 0)
    gd = ImageDraw.Draw(glanz)
    for y in range(int(g * .55)):
        gd.line([(0, y), (g, y)], fill=round(46 * (1 - y / (g * .55)) ** 2))
    bild.paste(Image.new("RGB", (g, g), WEISS), (0, 0), Image.composite(glanz, Image.new("L", (g, g), 0), maske))
    d = ImageDraw.Draw(bild)

    # Sprechblase
    d.rounded_rectangle([int(g * .17), int(g * .2), int(g * .83), int(g * .7)], radius=int(g * .2), fill=WEISS)
    d.polygon([(int(g * .27), int(g * .62)), (int(g * .22), int(g * .84)), (int(g * .45), int(g * .68))], fill=WEISS)

    # Schloss in der Blase, in der Farbe des Grundes
    blau = BLAU_UNTEN
    bw = int(g * .045)
    d.arc([int(g * .405), int(g * .275), int(g * .595), int(g * .49)], start=180, end=360, fill=blau, width=bw)
    for x in (int(g * .405) + bw // 2, int(g * .595) - bw // 2):
        d.line([(x, int(g * .38)), (x, int(g * .44))], fill=blau, width=bw)
    d.rounded_rectangle([int(g * .36), int(g * .425), int(g * .64), int(g * .615)], radius=int(g * .04), fill=blau)
    r = int(g * .028)
    d.ellipse([g // 2 - r, int(g * .505) - r, g // 2 + r, int(g * .505) + r], fill=WEISS)
    d.rounded_rectangle([g // 2 - int(r * .55), int(g * .505), g // 2 + int(r * .55), int(g * .575)],
                        radius=int(r * .4), fill=WEISS)

    return bild.resize((kante, kante), Image.LANCZOS)


def main():
    ziel = Path(__file__).resolve().parent / "vp4.ico"
    # Windows sucht sich aus diesen Größen die passende heraus
    groessen = [16, 24, 32, 48, 64, 128, 256]
    bilder = [schloss_zeichnen(k) for k in groessen]
    bilder[-1].save(ziel, format="ICO",
                    sizes=[(k, k) for k in groessen],
                    append_images=bilder[:-1])
    print(f"Icon geschrieben: {ziel}")
    print(f"Enthaltene Größen: {', '.join(f'{k}x{k}' for k in groessen)}")

    # Zusätzlich als PNG - praktisch für die Anzeige auf der GitHub-Seite
    png = ziel.with_suffix(".png")
    schloss_zeichnen(256).save(png)
    print(f"Vorschaubild: {png}")


if __name__ == "__main__":
    main()
