# /// script
# requires-python = ">=3.12"
# dependencies = []
# ///
"""
Připraví data pro nástroj „Nebezpečné výrobky“.

Zdroj: Safety Gate – systém EU pro rychlou výměnu informací o nebezpečných nepotravinářských výrobcích
(Evropská komise). Každý pátek vychází týdenní report; stahujeme české verze reportů za posledních
OBDOBI_TYDNU týdnů jako XML.

Co bereme beze změny (texty jsou oficiální český překlad Evropské komise): název výrobku, značku,
model, čárový kód, kategorii, typ a popis rizika, popis výrobku, přijatá opatření, zemi původu
a zemi, která výrobek nahlásila. Fotky neukládáme, na webu jen odkazujeme na servery Komise.

Výstupy (složka web/data):
  - vyrobky-index.js        krátký záznam ke každému hlášení (pro vyhledávání a filtry)
  - vyrobky/RRRR-MM.js      podrobnosti hlášení po měsících (načítají se, až když si je člověk otevře)
"""
import os
import json
import re
import sys
import tempfile
import time
import urllib.request
import xml.etree.ElementTree as ET
from collections import Counter, defaultdict
from datetime import datetime
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


SEZNAM_URL = "https://ec.europa.eu/safety-gate-alerts/api/download/weeklyReport/list/xml/cs"
OBDOBI_TYDNU = 104                 # dva roky
ZNOVU_STAHNOUT_POSLEDNICH = 3      # nejnovější reporty se ještě můžou doplňovat
CACHE = Path(tempfile.gettempdir()) / "jsem-spotrebitel-safety-gate"


def stahni(url: str) -> bytes:
    for pokus in range(4):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0", "Accept": "application/xml"})
            with urllib.request.urlopen(req, timeout=120) as r:
                return r.read()
        except Exception as chyba:
            print(f"    server neodpověděl ({chyba}), zkusím znovu za {5 * (pokus + 1)} s", flush=True)
            time.sleep(5 * (pokus + 1))
    raise RuntimeError(f"Nepodařilo se stáhnout {url}")


def txt(el, tag) -> str:
    x = el.find(tag)
    return re.sub(r"\s+", " ", (x.text or "")).strip() if x is not None and x.text else ""


def datum(s: str) -> str | None:
    for f in ("%d-%m-%Y", "%d/%m/%Y"):
        try:
            return datetime.strptime(s.strip(), f).strftime("%Y-%m-%d")
        except ValueError:
            pass
    return None


def opatreni(s: str) -> list[str]:
    """Z textu opatření vytáhne kategorie opatření (např. „Stažení výrobku z trhu“)."""
    kat = re.findall(r"Kategorie opatření:\s*(.*?)(?=Datum vstupu|Druh hospodářského|Kategorie opatření:|$)", s)
    return list(dict.fromkeys(k.strip(" ,;") for k in kat if k.strip()))


if __name__ == "__main__":
    if sys.platform == "win32":
        try:
            sys.stdout.reconfigure(encoding="utf-8")
        except Exception:
            pass

    slozka = Path(__file__).parent.resolve()
    # Když skript spouští hromadná aktualizace, píše do jejího logu (proměnná JS_LOG_SOUBOR).
    sys.stdout = TeeOutput(Path(os.environ.get("JS_LOG_SOUBOR") or slozka / "beh_skriptu.log"))
    CACHE.mkdir(parents=True, exist_ok=True)
    vystup = slozka / "web" / "data"
    (vystup / "vyrobky").mkdir(parents=True, exist_ok=True)

    print("Spouštím přípravu dat pro „Nebezpečné výrobky“ (EU Safety Gate)…", flush=True)
    seznam = ET.fromstring(stahni(SEZNAM_URL))
    reporty = []
    for w in seznam.findall("weeklyReport"):
        reporty.append((txt(w, "reference"), datum(txt(w, "publicationDate")), txt(w, "URL")))
    reporty.sort(key=lambda r: r[1] or "", reverse=True)
    reporty = reporty[:OBDOBI_TYDNU]
    print(f"  týdenních reportů ke zpracování: {len(reporty)} ({reporty[-1][1]} až {reporty[0][1]})", flush=True)

    zaznamy = {}
    for n, (ref, dat, url) in enumerate(reporty, 1):
        soubor = CACHE / f"{ref}.xml"
        if not soubor.exists() or n <= ZNOVU_STAHNOUT_POSLEDNICH:
            print(f"  [{n}/{len(reporty)}] stahuji {ref} ({dat})", flush=True)
            soubor.write_bytes(stahni(url))
            time.sleep(0.5)
        try:
            koren = ET.parse(soubor).getroot()
        except ET.ParseError:
            print(f"  [{n}/{len(reporty)}] {ref}: poškozený soubor, stahuji znovu", flush=True)
            soubor.write_bytes(stahni(url))
            koren = ET.parse(soubor).getroot()
        pocet = 0
        for a in koren.iter("notifications"):
            odkaz = txt(a, "reference")
            m = re.search(r"/(\d+)$", odkaz)
            if not m:
                continue
            aid = m.group(1)
            fotky = [re.search(r"/(\d+)$", p.text).group(1) for p in a.iter("picture") if p.text and re.search(r"/(\d+)$", p.text)]
            zaznamy[aid] = {
                "id": aid,
                "d": datum(txt(a, "publicationDate")) or dat,
                "cislo": txt(a, "caseNumber"),
                "kat": txt(a, "category"),
                "vyr": txt(a, "product"),
                "zn": txt(a, "brand"),
                "nazev": txt(a, "name"),
                "model": txt(a, "type_numberOfModel"),
                "sarze": txt(a, "batchNumber"),
                "ean": txt(a, "barcode"),
                "riziko": [r.strip() for r in txt(a, "riskType").split(",") if r.strip()],
                "popis_rizika": txt(a, "danger"),
                "opatreni": opatreni(txt(a, "measures")),
                "opatreni_text": txt(a, "measures"),
                "popis": txt(a, "description"),
                "zeme": txt(a, "notifyingCountry"),
                "puvod": txt(a, "countryOfOrigin"),
                "uroven": txt(a, "level"),
                "typ": txt(a, "type"),
                "fotky": fotky[:4],
                "eshop": txt(a, "onlineTrader").strip(" /"),
                "stazeni": txt(a, "URLrecall"),
            }
            pocet += 1
        if n <= ZNOVU_STAHNOUT_POSLEDNICH or n % 10 == 0 or n == len(reporty):
            print(f"  [{n}/{len(reporty)}] {ref}: {pocet} hlášení, celkem {len(zaznamy)}", flush=True)

    # --- číselníky, ať je index menší ---
    KAT = [k for k, _ in Counter(z["kat"] for z in zaznamy.values()).most_common()]
    RIZ = [k for k, _ in Counter(r for z in zaznamy.values() for r in z["riziko"]).most_common()]
    ZEME = [k for k, _ in Counter(z["zeme"] for z in zaznamy.values()).most_common()]
    UROVEN = [k for k, _ in Counter(z["uroven"] for z in zaznamy.values()).most_common()]
    kat_i, riz_i, zeme_i, ur_i = ({v: i for i, v in enumerate(s)} for s in (KAT, RIZ, ZEME, UROVEN))

    serazene = sorted(zaznamy.values(), key=lambda z: (z["d"] or "", z["id"]), reverse=True)
    index = [[z["id"], z["d"], z["vyr"], z["zn"], z["nazev"], z["model"], z["ean"], kat_i[z["kat"]],
              [riz_i[r] for r in z["riziko"]], zeme_i[z["zeme"]], ur_i[z["uroven"]], 1 if z["puvod"].startswith("Čínsk") else 0,
              z["fotky"][0] if z["fotky"] else None]
             for z in serazene]
    meta = {
        "aktualizace": datetime.now().strftime("%Y-%m-%d"),
        "od": serazene[-1]["d"], "do": serazene[0]["d"],
        "kategorie": KAT, "rizika": RIZ, "zeme": ZEME, "urovne": UROVEN,
        "zdroj": "https://ec.europa.eu/safety-gate-alerts/screen/search",
        "f": index,
    }
    (vystup / "vyrobky-index.js").write_text(
        "window.VYROBKY=" + json.dumps(meta, ensure_ascii=False, separators=(",", ":")) + ";\n", encoding="utf-8")

    for stary in (vystup / "vyrobky").glob("*.js"):
        stary.unlink()
    po_mesicich = defaultdict(dict)
    for z in serazene:
        po_mesicich[(z["d"] or "0000-00")[:7]][z["id"]] = {k: z[k] for k in (
            "cislo", "popis_rizika", "opatreni", "opatreni_text", "popis", "puvod", "typ", "fotky", "eshop", "stazeni", "sarze")}
    for mesic, obsah in po_mesicich.items():
        (vystup / "vyrobky" / f"{mesic}.js").write_text(
            f'(window.VYROBKY_DETAIL=window.VYROBKY_DETAIL||{{}})["{mesic}"]='
            + json.dumps(obsah, ensure_ascii=False, separators=(",", ":")) + ";\n", encoding="utf-8")

    # --- kontrola ---
    print("Kontroluji výstup…", flush=True)
    t = (vystup / "vyrobky-index.js").read_text(encoding="utf-8")
    zpet = json.loads(t[t.index("=") + 1:].rstrip().rstrip(";"))
    assert len(zpet["f"]) == len(zaznamy)
    v_detailech = 0
    for s in (vystup / "vyrobky").glob("*.js"):
        x = s.read_text(encoding="utf-8")
        v_detailech += len(json.loads(x.split('"]=', 1)[1].rstrip().rstrip(";")))
    assert v_detailech == len(zaznamy), (v_detailech, len(zaznamy))
    prazdne = sum(1 for z in zaznamy.values() if not z["vyr"])
    cz = sum(1 for z in zaznamy.values() if z["zeme"].startswith("Česk"))
    print(f"  index {(vystup / 'vyrobky-index.js').stat().st_size / 1e6:.1f} MB, detaily ve {len(po_mesicich)} souborech "
          f"({sum(s.stat().st_size for s in (vystup / 'vyrobky').glob('*.js')) / 1e6:.1f} MB)", flush=True)
    print(f"  hlášení bez názvu výrobku: {prazdne}", flush=True)
    print(f"  nahlášeno Českou republikou: {cz}", flush=True)
    print(f"  kategorie (nejčastější): {Counter(z['kat'] for z in zaznamy.values()).most_common(8)}", flush=True)
    print(f"  rizika (nejčastější): {Counter(r for z in zaznamy.values() for r in z['riziko']).most_common(8)}", flush=True)
    print(f"  opatření (nejčastější): {Counter(o for z in zaznamy.values() for o in z['opatreni']).most_common(6)}", flush=True)
    print(f"  úrovně rizika: {Counter(z['uroven'] for z in zaznamy.values()).most_common()}", flush=True)
    print(f"  země nahlášení: {ZEME[:12]}", flush=True)
    print(f"Hotovo. {len(zaznamy)} hlášení o nebezpečných výrobcích ({meta['od']} až {meta['do']}).", flush=True)
