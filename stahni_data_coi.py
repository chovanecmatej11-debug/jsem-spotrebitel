# /// script
# requires-python = ">=3.12"
# dependencies = []
# ///
"""
Stáhne seznam rizikových webů z otevřených dat České obchodní inspekce (ČOI),
opraví kódování, vyčistí domény a uloží data pro web „Ověř e-shop“.

Výstupy (složka web/data):
  - rizikove-weby.js            data, která načítá stránka
  - historie-rizikovych-webu.json  kdy jsme kterou doménu poprvé/naposledy viděli
                                (ČOI datum zařazení neuvádí, proto si ho vedeme sami)
"""
import os
import json
import re
import sys
import urllib.request
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


ZDROJ_URL = "https://www.coi.gov.cz/userdata/files/dokumenty-ke-stazeni/open-data/rizikove-seznam.csv"
ZDROJ_STRANKA = "https://www.coi.gov.cz/pro-spotrebitele/otevrena-data/rizikove-weby/"

# Zjednodušené kategorie pro filtrování. Na webu se vždy zobrazuje i plné znění ČOI,
# kategorie je jen naše pomůcka, nemění význam textu.
KATEGORIE = [
    ("zdravi", "Léky a „zázračné“ zdraví", r"lékárn|léků|léčiv|zdravotní portál|lékařsk|předpis|hubnut|zázračn\w* (?:lék|přípravek)"),
    ("investice", "Falešné zprávy a investice", r"zbohatnutí|novinky\.cz|zpravodajsk|smyšlen|investic|kryptoměn|bitcoin"),
    ("seznamka", "Seznamky a spam", r"seznamk|spam"),
    ("overeni", "Kontakt nejde ověřit", r"nepodařilo ověřit|neodpovídá|nelze ověřit|neexistuj"),
    ("zahranici", "Prodejce mimo EU / cizí měna", r"mimo (?:EU|Evropsk|území)|cizí měn|sídlem mimo"),
    ("stiznosti", "Mnoho stížností", r"stížnost"),
    ("anonymni", "Anonymní provozovatel", r"není uveden nikdo|anonymní|není zřejmé, kdo|bez identifikace"),
]


def dekoduj_radek(b: bytes) -> str:
    """ČOI soubor je ve Windows-1250; pro jistotu zkusíme nejdřív UTF-8."""
    try:
        return b.decode("utf-8")
    except UnicodeDecodeError:
        return b.decode("cp1250", errors="replace")


def vycisti_domenu(d: str) -> str:
    d = d.strip().strip("'\"").strip().lower()
    d = re.sub(r"^[a-z]+://", "", d)
    d = d.split("/")[0].split("?")[0].split("#")[0]
    d = d.removeprefix("www.")
    return d.rstrip(".")


def urci_kategorii(duvod: str) -> str:
    for klic, _nazev, vzor in KATEGORIE:
        if re.search(vzor, duvod, flags=re.IGNORECASE):
            return klic
    return "jine"


def main():
    slozka = Path(__file__).parent.resolve()
    vystup = slozka / "web" / "data"
    vystup.mkdir(parents=True, exist_ok=True)
    dnes = datetime.now().strftime("%Y-%m-%d")

    print(f"Stahuji seznam rizikových webů z ČOI…\n  {ZDROJ_URL}", flush=True)
    req = urllib.request.Request(ZDROJ_URL, headers={"User-Agent": "overeni-eshopu/1.0"})
    with urllib.request.urlopen(req, timeout=60) as r:
        surova = r.read()
    print(f"  Staženo {len(surova):,} bajtů.".replace(",", " "), flush=True)

    radky = [x for x in surova.splitlines() if x.strip()]
    print(f"  Řádků v souboru: {len(radky)}", flush=True)

    zaznamy = {}
    preskoceno = 0
    duplicity = 0
    for i, rb in enumerate(radky, 1):
        radek = dekoduj_radek(rb)
        if ";" not in radek:
            preskoceno += 1
            print(f"  ! Řádek {i} nemá oddělovač, přeskakuji: {radek[:60]}", flush=True)
            continue
        dom, duvod = radek.split(";", 1)
        dom = vycisti_domenu(dom)
        duvod = duvod.strip().strip("'").strip()
        duvod = re.sub(r"\s+", " ", duvod)
        if not dom or "." not in dom:
            preskoceno += 1
            print(f"  ! Řádek {i}: neplatná doména „{dom}“, přeskakuji", flush=True)
            continue
        if dom in zaznamy:
            duplicity += 1
            if len(duvod) > len(zaznamy[dom]["duvod"]):
                zaznamy[dom]["duvod"] = duvod
            continue
        zaznamy[dom] = {"d": dom, "duvod": duvod, "k": urci_kategorii(duvod)}

    print(f"  Platných domén: {len(zaznamy)}, duplicit: {duplicity}, přeskočeno: {preskoceno}", flush=True)
    vadne = sum(1 for z in zaznamy.values() if "�" in z["duvod"])
    print(f"  Záznamů s rozbitou diakritikou: {vadne}", flush=True)

    # Historie: kdy jsme doménu poprvé viděli (ČOI datum zařazení neuvádí)
    hist_soubor = vystup / "historie-rizikovych-webu.json"
    historie = {"zacatek_sledovani": dnes, "domeny": {}}
    if hist_soubor.exists():
        historie = json.loads(hist_soubor.read_text(encoding="utf-8"))
    nove = 0
    for dom in zaznamy:
        h = historie["domeny"].get(dom)
        if h is None:
            historie["domeny"][dom] = {"poprve": dnes, "naposledy": dnes}
            nove += 1
        else:
            h["naposledy"] = dnes
    odebrane = [d for d, h in historie["domeny"].items() if d not in zaznamy and not h.get("odebrano")]
    for d in odebrane:
        historie["domeny"][d]["odebrano"] = dnes
    hist_soubor.write_text(json.dumps(historie, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"  Historie: nově přidaných {nove}, nově odebraných {len(odebrane)} (začátek sledování {historie['zacatek_sledovani']})", flush=True)

    zacatek = historie["zacatek_sledovani"]
    seznam = []
    for z in sorted(zaznamy.values(), key=lambda x: x["d"]):
        poprve = historie["domeny"][z["d"]]["poprve"]
        seznam.append({**z, "p": poprve if poprve != zacatek else None})

    pocty = {}
    for z in seznam:
        pocty[z["k"]] = pocty.get(z["k"], 0) + 1
    print("  Rozdělení podle kategorií:", flush=True)
    nazvy = {k: n for k, n, _ in KATEGORIE} | {"jine": "Jiné důvody"}
    for k, n in sorted(pocty.items(), key=lambda x: -x[1]):
        print(f"    {nazvy[k]}: {n}", flush=True)

    data = {
        "aktualizace": datetime.now(timezone.utc).isoformat(timespec="minutes"),
        "zdroj": ZDROJ_STRANKA,
        "zacatek_sledovani": zacatek,
        "kategorie": {k: n for k, n in nazvy.items()},
        "weby": seznam,
    }
    js = "window.RIZIKOVE_WEBY = " + json.dumps(data, ensure_ascii=False, separators=(",", ":")) + ";\n"
    (vystup / "rizikove-weby.js").write_text(js, encoding="utf-8")

    # Kontrola: načíst zpět a porovnat počty
    zpet = json.loads((vystup / "rizikove-weby.js").read_text(encoding="utf-8")[len("window.RIZIKOVE_WEBY = "):-2])
    assert len(zpet["weby"]) == len(zaznamy), "Počet záznamů nesedí!"
    assert all(w["duvod"] for w in zpet["weby"]), "Některý záznam nemá důvod!"
    print(f"Kontrola OK: uloženo {len(zpet['weby'])} domén, všechny mají důvod od ČOI.", flush=True)


if __name__ == "__main__":
    if sys.platform == "win32":
        try:
            sys.stdout.reconfigure(encoding="utf-8")
        except Exception:
            pass
    slozka = Path(__file__).parent.resolve()
    # Když skript spouští hromadná aktualizace, píše do jejího logu (proměnná JS_LOG_SOUBOR).
    sys.stdout = TeeOutput(Path(os.environ.get("JS_LOG_SOUBOR") or slozka / "beh_skriptu.log"))
    print("Spouštím zpracování...", flush=True)
    main()
    print("Hotovo.", flush=True)
