"""Generates the fixed voice prompts the Asterisk dialplan plays.

    python -m curado.prompts /var/lib/curado/sounds
"""

import sys
from pathlib import Path

from .notify import synthesize

PROMPTS = {
    "confirmada": "Alarma confirmada. No se repetirán los avisos hasta que vuelva a ocurrir. Gracias.",
    "menu": "Para confirmar una alarma pulse 1. Para repetir pulse 2.",
    "pin": "Marque su PIN de cuatro cifras.",
    "pin-error": "PIN incorrecto.",
    "estado": "No hay datos de la cámara de curado.",
}


def main(directory: str) -> None:
    out = Path(directory)
    for name, text in PROMPTS.items():
        print(synthesize(text, out / name))


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "/var/lib/curado/sounds")
