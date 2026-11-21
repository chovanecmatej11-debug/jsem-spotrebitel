# /// script
# requires-python = ">=3.12"
# dependencies = []
# ///
"""
Hromadná aktualizace dat všech nástrojů webu „jsem spotřebitel“.

Co dělá:
  1. Podle kalendáře rozhodne, které zdroje se mají dnes stáhnout (každý zdroj má vlastní rytmus,
     podle toho, jak často ho úřad mění). Parametr --vse stáhne všechno bez ohledu na kalendář.
  2. Před každým zdrojem si odloží zálohu současných dat.
  3. Spustí příslušný stahovací skript a výsledek zkontroluje:
       - skript doběhl bez chyby,
       - všechny výstupní soubory existují a dají se přečíst,
       - počet záznamů nespadl pod 80 % minulého stavu (úřad mohl zveřejnit poškozený nebo neúplný soubor).
     Když kontrola neprojde, vrátí zálohu – na webu tedy zůstanou poslední dobrá data.
  4. Postaví web (postav_web.py) a zkontroluje odkazy.
  5. Zapíše stav do web/data/stav-aktualizace.json a vypíše souhrn: co se změnilo, co selhalo
     a co musí zkontrolovat člověk (např. adresy nových benzinek).

Spouští se ručně (uv run "aktualizuj_vse.py") nebo automaticky na GitHubu (nasazeni/aktualizace.yml).
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
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


MIN_PODIL = 0.80            # nový počet záznamů musí být aspoň 80 % předchozího
LIMIT_MINUT = 120           # nejdelší povolená doba běhu jednoho skriptu

# Zdroje v pořadí, v jakém se spouštějí. „Prověř firmu“ musí běžet před benzinkami,
# protože benzinky si z jeho dat berou názvy firem.
#   dny    = dny v týdnu, kdy se zdroj stahuje (0 = pondělí … 6 = neděle)
#   max_dni = když se zdroj nepodařilo stáhnout déle než tolik dní, stáhne se hned (dohnání zpoždění)
ZDROJE = [
    {
        "klic": "rizikove-weby", "nazev": "Ověř e-shop (ČOI Rizikové weby)",
        "skript": "stahni_data_coi.py", "dny": range(7), "max_dni": 1,
        "hlavni": "rizikove-weby.js",
        "vystupy": ["rizikove-weby.js", "historie-rizikovych-webu.json"],
    },
    {
        "klic": "firmy", "nazev": "Prověř firmu (ČOI Kontroly a Sankce + ARES)",
        "skript": "stahni_kontroly_coi.py", "dny": [0], "max_dni": 7,
        "hlavni": "firmy-index.js",
        "vystupy": ["firmy-index.js", "firmy"],
    },
    {
        "klic": "benzinky", "nazev": "Benzinky s pokutou za palivo (ČOI)",
        "skript": "stahni_benzinky_coi.py", "dny": range(7), "max_dni": 1,
        "hlavni": "benzinky.js",
        "vystupy": ["benzinky.js"],
    },
    {
        "klic": "vyrobky", "nazev": "Nebezpečné výrobky (EU Safety Gate)",
        "skript": "stahni_nebezpecne_vyrobky.py", "dny": [4, 5], "max_dni": 7,
        "hlavni": "vyrobky-index.js",
        "vystupy": ["vyrobky-index.js", "vyrobky"],
    },
]


def najdi_uv() -> str:
    """Najde program uv (na GitHubu je v PATH, na Windows bývá v ~/.local/bin)."""
    cesta = shutil.which("uv")
    if cesta:
        return cesta
    for kandidat in (Path.home() / ".local" / "bin" / "uv.exe", Path.home() / ".local" / "bin" / "uv"):
        if kandidat.exists():
            return str(kandidat)
    raise RuntimeError("Nenašel jsem program uv.")


def nacti_js_data(soubor: Path):
    """Datové soubory mají tvar `window.NECO = {…};` – vrátí to, co je za rovnítkem."""
    text = soubor.read_text(encoding="utf-8").strip()
    return json.loads(text[text.index("=") + 1:].rstrip(";").strip())


def pocet_zaznamu(soubor: Path) -> int:
    """Počet záznamů = délka nejdelšího seznamu v datech (domény, firmy, pokuty, hlášení)."""
    data = nacti_js_data(soubor)
    if isinstance(data, list):
        return len(data)
    seznamy = [len(v) for v in data.values() if isinstance(v, (list, dict))]
    return max(seznamy, default=0)


def zaloha(data_dir: Path, vystupy: list[str], cil: Path) -> None:
    cil.mkdir(parents=True, exist_ok=True)
    for nazev in vystupy:
        zdroj = data_dir / nazev
        if zdroj.is_dir():
            shutil.copytree(zdroj, cil / nazev)
        elif zdroj.exists():
            shutil.copy2(zdroj, cil / nazev)


def obnov(data_dir: Path, vystupy: list[str], zal: Path) -> None:
    for nazev in vystupy:
        cesta = data_dir / nazev
        if cesta.is_dir():
            shutil.rmtree(cesta)
        elif cesta.exists():
            cesta.unlink()
        if (zal / nazev).is_dir():
            shutil.copytree(zal / nazev, cesta)
        elif (zal / nazev).exists():
            shutil.copy2(zal / nazev, cesta)


def spust(uv: str, skript: Path, slozka: Path) -> tuple[int, list[str]]:
    """Spustí stahovací skript, jeho výstup průběžně vypisuje odsazeně do našeho logu."""
    env = dict(os.environ, PYTHONIOENCODING="utf-8", PYTHONUTF8="1",
               JS_LOG_SOUBOR=str(Path(tempfile.gettempdir()) / f"js-log-{skript.stem}.log"))
    proces = subprocess.Popen([uv, "run", "--quiet", str(skript)], cwd=slozka, env=env,
                              stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                              text=True, encoding="utf-8", errors="replace")
    radky = []
    zacatek = datetime.now()
    for radek in proces.stdout:
        radek = radek.rstrip("\n")
        radky.append(radek)
        print(f"    │ {radek}", flush=True)
        if (datetime.now() - zacatek).total_seconds() > LIMIT_MINUT * 60:
            proces.kill()
            print(f"    │ Skript běží déle než {LIMIT_MINUT} minut, ukončuji ho.", flush=True)
            break
    return proces.wait(), radky


def dnu_od(iso: str | None, ted: datetime) -> float:
    if not iso:
        return 9999
    return (ted - datetime.fromisoformat(iso)).total_seconds() / 86400


def main(vse: bool) -> int:
    slozka = Path(__file__).parent.resolve()
    data_dir = slozka / "web" / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    stav_soubor = data_dir / "stav-aktualizace.json"
    stav = json.loads(stav_soubor.read_text(encoding="utf-8")) if stav_soubor.exists() else {}
    ted = datetime.now(timezone.utc)
    uv = najdi_uv()
    zalohy = Path(tempfile.mkdtemp(prefix="js-zaloha-"))

    print(f"Hromadná aktualizace dat – {ted.astimezone():%d. %m. %Y %H:%M}", flush=True)
    print("Režim: stahuji všechny zdroje." if vse else "Režim: podle kalendáře.", flush=True)

    souhrn, problemy, k_overeni = [], [], []
    for i, z in enumerate(ZDROJE, 1):
        s = stav.setdefault(z["klic"], {})
        print(f"\n[{i}/{len(ZDROJE)}] {z['nazev']}", flush=True)

        na_rade = ted.weekday() in z["dny"]
        zpozdeno = dnu_od(s.get("naposledy_uspech"), ted) > z["max_dni"]
        chybi_data = not all((data_dir / v).exists() for v in z["vystupy"])
        if not (vse or na_rade or zpozdeno or chybi_data):
            print(f"  Dnes není na řadě (poslední úspěšná aktualizace {s.get('naposledy_uspech', '–')[:10]}), přeskakuji.", flush=True)
            souhrn.append(f"– {z['nazev']}: dnes není na řadě")
            continue

        hlavni = data_dir / z["hlavni"]
        try:
            pred = pocet_zaznamu(hlavni) if hlavni.exists() else 0
        except Exception:
            pred = 0
        zal = zalohy / z["klic"]
        zaloha(data_dir, z["vystupy"], zal)

        s["naposledy_pokus"] = ted.isoformat(timespec="minutes")
        kod, radky = spust(uv, slozka / z["skript"], slozka)

        # Kontroly výsledku
        chyba = None
        if kod != 0:
            chyba = f"skript skončil chybou (kód {kod})"
        else:
            chybi = [v for v in z["vystupy"] if not (data_dir / v).exists()]
            if chybi:
                chyba = f"chybí výstup: {', '.join(chybi)}"
            else:
                try:
                    po = pocet_zaznamu(hlavni)
                except Exception as e:
                    chyba, po = f"výstup nejde přečíst ({e})", 0
                if not chyba and po == 0:
                    chyba = "výstup je prázdný"
                elif not chyba and pred and po < pred * MIN_PODIL:
                    chyba = (f"počet záznamů spadl z {pred} na {po} (víc než o {100 - MIN_PODIL * 100:.0f} %) – "
                             "možná poškozený soubor úřadu")

        if chyba:
            obnov(data_dir, z["vystupy"], zal)
            s["vysledek"], s["zprava"] = "chyba", chyba
            print(f"  ✗ NEPOVEDLO SE: {chyba}. Na webu zůstávají předchozí data.", flush=True)
            problemy.append(f"{z['nazev']}: {chyba}")
            souhrn.append(f"✗ {z['nazev']}: {chyba} (ponechána předchozí data)")
            continue

        rozdil = po - pred
        s.update(vysledek="ok", zprava="", naposledy_uspech=ted.isoformat(timespec="minutes"),
                 pocet=po, predchozi_pocet=pred)
        znamenko = f"+{rozdil}" if rozdil > 0 else str(rozdil)
        print(f"  ✓ V pořádku: {po} záznamů ({znamenko} oproti minulé verzi).", flush=True)
        souhrn.append(f"✓ {z['nazev']}: {po} záznamů ({znamenko})")

        # Benzinky: nová rozhodnutí bez ověřené adresy musí projít člověk
        if z["klic"] == "benzinky":
            for j, r in enumerate(radky):
                if r.startswith("POZOR:") and "bez ověřené adresy" in r:
                    k_overeni.append(r)
                    k_overeni.extend(x.strip() for x in radky[j + 1:] if x.startswith("   "))

    stav_soubor.write_text(json.dumps(stav, ensure_ascii=False, indent=1), encoding="utf-8")

    print("\nStavím web…", flush=True)
    kod, _ = spust(uv, slozka / "postav_web.py", slozka)
    shutil.rmtree(zalohy, ignore_errors=True)
    if kod != 0:
        print("✗ Web se nepodařilo postavit (nefunkční odkazy nebo chyba). Nic se nezveřejní.", flush=True)
        return 1

    print("\n══════ SOUHRN ══════", flush=True)
    for r in souhrn:
        print(r, flush=True)
    if k_overeni:
        print("\nK RUČNÍMU OVĚŘENÍ:", flush=True)
        for r in k_overeni:
            print(f"  {r}", flush=True)
    print(f"\nProblémů: {len(problemy)}", flush=True)

    # Na GitHubu: souhrn do přehledu běhu a podklady pro upozornění
    if os.environ.get("GITHUB_STEP_SUMMARY"):
        with open(os.environ["GITHUB_STEP_SUMMARY"], "a", encoding="utf-8") as f:
            f.write("## Aktualizace dat\n\n" + "\n".join(f"- {r}" for r in souhrn) + "\n")
            if k_overeni:
                f.write("\n### K ručnímu ověření\n\n" + "\n".join(f"- {r}" for r in k_overeni) + "\n")
    if os.environ.get("GITHUB_OUTPUT"):
        with open(os.environ["GITHUB_OUTPUT"], "a", encoding="utf-8") as f:
            f.write(f"problemy={len(problemy)}\nk_overeni={len(k_overeni)}\n")
    zprava = slozka / "k-overeni.md"
    if k_overeni:
        zprava.write_text("Nová rozhodnutí ČOI o benzinkách, u kterých chybí ověřená adresa stanice.\n"
                          "Adresu přečti v rozhodnutí a doplň do obsah/data/benzinky-adresy.csv.\n\n"
                          + "\n".join(f"- {r}" for r in k_overeni) + "\n", encoding="utf-8")
    elif zprava.exists():
        zprava.unlink()
    return 0


if __name__ == "__main__":
    if sys.platform == "win32":
        try:
            sys.stdout.reconfigure(encoding="utf-8")
        except Exception:
            pass
    slozka = Path(__file__).parent.resolve()
    sys.stdout = TeeOutput(slozka / "beh_skriptu.log")
    print("Spouštím zpracování...", flush=True)
    vysledek = main(vse="--vse" in sys.argv)
    print("Hotovo." if vysledek == 0 else "Skončeno s chybou.", flush=True)
    sys.exit(vysledek)
