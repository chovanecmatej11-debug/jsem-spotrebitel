# /// script
# requires-python = ">=3.12"
# dependencies = ["pillow", "numpy"]
# ///
"""
Připraví logo pro web: odstraní světlé pozadí (průhlednost), ořízne okraje
a vyrobí ikonu do záložky prohlížeče. Originál zůstává beze změny.
Použití: uv run priprav_logo.py "cesta/k/logu.png"
"""
import shutil
import sys
from pathlib import Path


class TeeOutput:
    """Zapisuje výstup současně do terminálu i do živého logu v UTF-8."""
    def __init__(self, log_path: Path):
        self.terminal = sys.stdout
        self.log_file = open(log_path, "w", encoding="utf-8", buffering=1)
    def write(self, message):
        self.terminal.write(message)
        self.terminal.flush()
        self.log_file.write(message)
        self.log_file.flush()
    def flush(self):
        self.terminal.flush()
        self.log_file.flush()


def odstran_pozadi(img, np):
    """Převede barvu pozadí na průhlednost tak, aby tenké čáry zůstaly hladké."""
    a = np.asarray(img.convert("RGB")).astype(float)
    # barva pozadí = medián rohů obrázku
    rohy = np.concatenate([a[:10, :10].reshape(-1, 3), a[:10, -10:].reshape(-1, 3),
                           a[-10:, :10].reshape(-1, 3), a[-10:, -10:].reshape(-1, 3)])
    bg = np.median(rohy, axis=0)
    print(f"  Barva pozadí: RGB {tuple(int(x) for x in bg)}", flush=True)
    rozdil = np.clip((bg - a) / np.maximum(bg, 1), 0, 1)
    alfa = rozdil.max(axis=2)
    alfa = np.clip((alfa - 0.04) / 0.96, 0, 1)          # drobný šum pozadí pryč
    alfa = np.clip(alfa * 1.6, 0, 1)                    # čáry sytější
    bezp = np.maximum(alfa, 1e-3)[..., None]
    barva = np.clip((a - bg * (1 - bezp)) / bezp, 0, 255)
    out = np.dstack([barva, alfa * 255]).astype("uint8")
    return out


def main(zdroj: Path):
    from PIL import Image
    import numpy as np

    slozka = Path(__file__).parent.resolve()
    cil = slozka / "web" / "img"
    cil.mkdir(parents=True, exist_ok=True)

    print(f"Načítám logo: {zdroj.name}", flush=True)
    shutil.copy2(zdroj, cil / "logo-original.png")
    img = Image.open(zdroj)
    print(f"  Rozměr: {img.size[0]} × {img.size[1]} px", flush=True)

    rgba = Image.fromarray(odstran_pozadi(img, np), "RGBA")
    maska = rgba.split()[3].point(lambda v: 255 if v > 20 else 0)
    box = maska.getbbox()
    print(f"  Obsah loga leží v oblasti {box}", flush=True)
    okraj = 6
    box = (max(box[0] - okraj, 0), max(box[1] - okraj, 0), min(box[2] + okraj, img.size[0]), min(box[3] + okraj, img.size[1]))
    logo = rgba.crop(box)
    # 3× zvětšení pro ostré zobrazení na mobilech s jemným displejem
    logo3 = logo.resize((logo.width * 3, logo.height * 3), Image.LANCZOS)
    logo3.save(cil / "logo.png", optimize=True)
    print(f"  Uloženo logo.png ({logo3.width} × {logo3.height} px, průhledné pozadí)", flush=True)

    # (Ikona do záložky je vektorová web/img/favicon.svg – písmena J.S v logu se dotýkají vozíku,
    #  ořez z obrázku proto nevychází čistě.)

    # Kontrola
    for n in ["logo.png", "logo-original.png"]:
        p = cil / n
        assert p.exists() and p.stat().st_size > 0, f"Chybí {n}"
    zpet = Image.open(cil / "logo.png")
    assert zpet.mode == "RGBA"
    pruhl = float((np.asarray(zpet)[..., 3] == 0).mean())
    print(f"Kontrola OK: logo má průhledné pozadí ({pruhl:.0%} plochy průhledné).", flush=True)


if __name__ == "__main__":
    if sys.platform == "win32":
        try:
            sys.stdout.reconfigure(encoding="utf-8")
        except Exception:
            pass
    slozka = Path(__file__).parent.resolve()
    sys.stdout = TeeOutput(slozka / "beh_skriptu.log")
    print("Spouštím zpracování...", flush=True)
    main(Path(sys.argv[1]))
    print("Hotovo.", flush=True)
