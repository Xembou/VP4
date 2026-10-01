#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Baut ui/icons/sprite.svg aus den Lucide-Icons (ISC-Lizenz).

Die Oberfläche lädt nichts aus dem Internet - auch keine Icons. Deshalb
liegt ein fertiges Sprite im Repo, und nur die Icons, die wirklich
gebraucht werden. Ein neues Icon: Namen unten eintragen, dann

    npm pack lucide-static   (oder das Paket von registry.npmjs.org laden)
    python werkzeuge/icons_bauen.py <entpackter Ordner>/package/icons

Benutzt werden die Icons so:  <svg class="ic"><use href="icons/sprite.svg#send"/></svg>
"""
import re
import sys
from pathlib import Path

ICONS = """
message-circle users hash wrench settings search plus arrow-up mic paperclip
smile smile-plus image file file-text video x check check-check chevron-left
chevron-right chevron-down ellipsis reply pencil trash-2 copy shield
shield-check shield-alert lock lock-open key-round fingerprint wifi globe bell
bell-off pin moon sun monitor palette user user-plus log-out download upload
folder folder-lock signature file-check scan-line notebook-pen refresh-cw info
triangle-alert circle-check play pause square arrow-down link qr-code eye
eye-off sparkles layers zap atom binary hard-drive message-square-plus
megaphone crown circle-user forward external-link maximize-2 rotate-ccw list
circle-alert inbox send-horizontal sliders-horizontal sparkle at-sign
""".split()


def main(quelle: Path):
    ziel = Path(__file__).resolve().parent.parent / "ui" / "icons" / "sprite.svg"
    teile = ['<svg xmlns="http://www.w3.org/2000/svg" style="display:none">',
             "<!-- Lucide Icons, ISC-Lizenz, siehe LICENSE-Lucide.txt -->"]
    for name in ICONS:
        text = (quelle / f"{name}.svg").read_text(encoding="utf-8")
        innen = re.search(r"<svg[^>]*>(.*)</svg>", text, re.S).group(1)
        innen = re.sub(r"\s+", " ", innen).strip()
        teile.append(f'<symbol id="{name}" viewBox="0 0 24 24" fill="none" '
                     f'stroke="currentColor" stroke-linecap="round" '
                     f'stroke-linejoin="round">{innen}</symbol>')
    teile.append("</svg>")
    ziel.write_text("\n".join(teile) + "\n", encoding="utf-8")
    print(f"{len(ICONS)} Icons -> {ziel}")


if __name__ == "__main__":
    main(Path(sys.argv[1]))
