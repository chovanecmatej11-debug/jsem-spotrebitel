# /// script
# requires-python = ">=3.12"
# dependencies = []
# ///
"""
Připraví data pro nástroj „Prověř firmu“.

Stáhne z otevřených dat České obchodní inspekce (ČOI) dvě datové sady:
  - Kontroly  (každá kontrola: datum, IČO kontrolované firmy, místo)
  - Sankce    (pravomocné pokuty; navázané na kontrolu přes „ID kontroly“)
a spojí je podle IČO. Názvy a sídla firem doplní z registru ARES (Ministerstvo financí).

Pravidla, která drží význam dat ČOI:
  - Jedna pokuta může být v CSV na více řádcích (víc porušených paragrafů).
    Sloučíme je podle „ID Sankce“ a částku počítáme jen jednou.
  - Pokuta 0 Kč = „sdružená“ pokuta, její částka je zahrnutá v jiné pokutě. Nezahazujeme ji,
    jen ji tak označíme.
  - Živnostníky (fyzické osoby) ČOI anonymizuje. Pokud by se fyzická osoba přesto
    objevila, podle právní formy z ARES ji vyřadíme a nezveřejníme.

Výstupy (složka web/data):
  - firmy-index.js      seznam firem pro vyhledávání podle názvu nebo IČO
  - firmy/NN.js         detail firem, rozdělený podle prvních dvou číslic IČO (rychlé načítání)
"""
import os
import csv
import io
import json
import re
import sys
import tempfile
import time
import urllib.request
from collections import Counter, defaultdict
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


ZAKLAD = "https://coi.gov.cz/userdata/files/dokumenty-ke-stazeni/open-data/"
STRANKA_KONTROLY = "https://coi.gov.cz/pro-spotrebitele/otevrena-data/kontroly/"
STRANKA_SANKCE = "https://coi.gov.cz/pro-spotrebitele/otevrena-data/ulozene-pokuty/"
ARES_URL = "https://ares.gov.cz/ekonomicke-subjekty-v-be/rest/ekonomicke-subjekty/vyhledat"
ARES_CACHE = Path(tempfile.gettempdir()) / "jsem-spotrebitel-ares-cache.json"
ARES_PLATNOST_DNU = 30

# Srozumitelné názvy předpisů. Co tu není, se zobrazí jen číslem předpisu.
PREDPISY = {
    "Zák. 634/1992": "zákon o ochraně spotřebitele",
    "Zák. 22/1997": "zákon o technických požadavcích na výrobky",
    "Zák. 89/2012": "občanský zákoník",
    "Zák. 255/2012": "kontrolní řád",
    "Zák. 64/1986": "zákon o České obchodní inspekci",
    "Zák. 477/2001": "zákon o obalech",
    "Zák. 102/2001": "zákon o obecné bezpečnosti výrobků",
    "Zák. 65/2017": "zákon o ochraně zdraví před škodlivými účinky návykových látek",
    "Zák. 90/2016": "zákon o posuzování shody stanovených výrobků",
    "Zák. 542/2020": "zákon o výrobcích s ukončenou životností",
    "Zák. 206/2015": "zákon o pyrotechnických výrobcích",
    "Zák. 253/2008": "zákon o opatřeních proti praní špinavých peněz",
    "Zák. 311/2006": "zákon o pohonných hmotách",
    "Zák. 526/1990": "zákon o cenách",
    "Zák. 505/1990": "zákon o metrologii",
    "Zák. 254/2004": "zákon o omezení plateb v hotovosti",
    "Zák. 112/2016": "zákon o evidenci tržeb",
    "Zák. 61/1997": "zákon o lihu",
    "Zák. 145/2010": "zákon o spotřebitelském úvěru (do 2016)",
    "Zák. 257/2016": "zákon o spotřebitelském úvěru",
    "Zák. 259/2014": "zákon o prekurzorech výbušnin (do 2022)",
    "Zák. 225/2022": "zákon o prekurzorech výbušnin",
    "Zák. 353/2003": "zákon o spotřebních daních",
    "Zák. 201/2012": "zákon o ochraně ovzduší",
    "Zák. 223/2016": "zákon o prodejní době v maloobchodě",
    "Zák. 424/2023": "zákon o požadavcích na přístupnost výrobků a služeb",
    "Zák. 370/2017": "zákon o platebním styku",
    "Zák. 87/2023": "zákon o dozoru nad trhem s výrobky",
    "Zák. 185/2001": "zákon o odpadech (do 2020)",
    "Zák. 379/2005": "zákon o ochraně před škodlivými účinky tabáku a alkoholu (do 2017)",
    "Zák. 159/1999": "zákon o podnikání v cestovním ruchu",
    "Zák. 156/2000": "zákon o ověřování střelných zbraní a střeliva",
    "Zák. 73/2012": "zákon o látkách poškozujících ozonovou vrstvu",
    "Zák. 247/2006": "zákon o omezení provozu zastaváren v noční době",
    "Zák. 243/2022": "zákon o omezení dopadu plastových výrobků",
    "Nař. 524/2013": "nařízení EU o online řešení spotřebitelských sporů",
    "Nař. 2016/426": "nařízení EU o spotřebičích plynných paliv",
    "Nař. 2018/302": "nařízení EU o zákazu neodůvodněného geoblokování",
    "Nař. 2015/751": "nařízení EU o mezibankovních poplatcích za karetní platby",
    "Nař. 995/2010": "nařízení EU o dřevě a dřevařských výrobcích",
    "Nař. 2021/1230": "nařízení EU o přeshraničních platbách",
    "Nař. 1007/2011": "nařízení EU o označování textilních výrobků",
    "Nař. 2016/425": "nařízení EU o osobních ochranných prostředcích",
    "Nař. 305/2011": "nařízení EU o stavebních výrobcích",
    "Nař. 2019/1020": "nařízení EU o dozoru nad trhem",
    "Nař. 2023/988": "nařízení EU o obecné bezpečnosti výrobků",
}


def stahni_csv(nazev: str) -> list[dict]:
    url = ZAKLAD + nazev + ".csv"
    print(f"Stahuji {url} …", flush=True)
    with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"}), timeout=180) as r:
        data = r.read()
    try:
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError:
        text = data.decode("cp1250", errors="replace")
    radky = list(csv.DictReader(io.StringIO(text, newline="")))
    print(f"  staženo {len(data) / 1e6:.1f} MB, {len(radky):_} řádků".replace("_", " "), flush=True)
    return radky


def platne_ico(ico: str) -> str | None:
    """Doplní IČO na 8 číslic a ověří kontrolní číslici (modulo 11). Neplatné vrátí None."""
    ico = re.sub(r"\D", "", ico or "")
    if not ico or len(ico) > 8:
        return None
    ico = ico.zfill(8)
    if ico == "00000000":
        return None
    s = sum(int(ico[i]) * (8 - i) for i in range(7))
    kontrola = (11 - s % 11) % 10
    return ico if kontrola == int(ico[7]) else None


def datum_iso(d: str) -> str | None:
    d = (d or "").strip()
    for f in ("%d.%m.%Y", "%Y-%m-%d", "%d. %m. %Y"):
        try:
            return datetime.strptime(d, f).strftime("%Y-%m-%d")
        except ValueError:
            pass
    return None


def je_fyzicka_osoba(pravni_forma: str | None) -> bool:
    """Právní formy 1xx a 424/425 jsou fyzické osoby (číselník ČSÚ)."""
    pf = str(pravni_forma or "")
    return pf in ("100", "101", "102", "103", "104", "105", "106", "107", "108", "424", "425")


def nacti_ares(ica: list[str]) -> dict:
    cache = {}
    if ARES_CACHE.exists():
        try:
            cache = json.loads(ARES_CACHE.read_text(encoding="utf-8"))
        except Exception:
            cache = {}
    dnes = time.time()
    chybi = [i for i in ica if i not in cache or dnes - cache[i].get("t", 0) > ARES_PLATNOST_DNU * 86400]
    print(f"ARES: {len(ica) - len(chybi):_} firem mám z dřívějška, dotazuji se na {len(chybi):_}".replace("_", " "), flush=True)
    davka = 100
    for n in range(0, len(chybi), davka):
        kus = chybi[n:n + davka]
        telo = json.dumps({"ico": kus, "pocet": davka, "start": 0}).encode("utf-8")
        for pokus in range(4):
            try:
                req = urllib.request.Request(ARES_URL, data=telo, method="POST",
                                             headers={"Content-Type": "application/json", "Accept": "application/json"})
                with urllib.request.urlopen(req, timeout=60) as r:
                    odp = json.loads(r.read().decode("utf-8"))
                break
            except Exception as chyba:
                print(f"  ARES neodpověděl ({chyba}), zkusím znovu za {5 * (pokus + 1)} s", flush=True)
                time.sleep(5 * (pokus + 1))
        else:
            print("  ARES opakovaně neodpovídá, tuto dávku přeskakuji (firmy zůstanou bez názvu)", flush=True)
            continue
        nalezene = set()
        for s in odp.get("ekonomickeSubjekty", []):
            ico = s.get("ico")
            nalezene.add(ico)
            cache[ico] = {
                "t": dnes,
                "n": s.get("obchodniJmeno"),
                "a": (s.get("sidlo") or {}).get("textovaAdresa"),
                "o": (s.get("sidlo") or {}).get("nazevObce"),
                "pf": s.get("pravniForma"),
                "z": s.get("datumZaniku"),
            }
        for ico in kus:
            if ico not in nalezene:
                cache[ico] = {"t": dnes, "n": None}
        hotovo = min(n + davka, len(chybi))
        if hotovo % 2000 == 0 or hotovo == len(chybi):
            print(f"  ARES: {hotovo:_} / {len(chybi):_}".replace("_", " "), flush=True)
            ARES_CACHE.write_text(json.dumps(cache, ensure_ascii=False), encoding="utf-8")
        time.sleep(0.25)
    ARES_CACHE.write_text(json.dumps(cache, ensure_ascii=False), encoding="utf-8")
    return cache


def js_soubor(cesta: Path, promenna: str, data) -> None:
    cesta.write_text(f"{promenna}={json.dumps(data, ensure_ascii=False, separators=(',', ':'))};\n", encoding="utf-8")


if __name__ == "__main__":
    if sys.platform == "win32":
        try:
            sys.stdout.reconfigure(encoding="utf-8")
        except Exception:
            pass

    slozka = Path(__file__).parent.resolve()
    # Když skript spouští hromadná aktualizace, píše do jejího logu (proměnná JS_LOG_SOUBOR).
    sys.stdout = TeeOutput(Path(os.environ.get("JS_LOG_SOUBOR") or slozka / "beh_skriptu.log"))
    vystup = slozka / "web" / "data"
    (vystup / "firmy").mkdir(parents=True, exist_ok=True)

    print("Spouštím přípravu dat pro „Prověř firmu“…", flush=True)
    kontroly = stahni_csv("kontroly")
    sankce = stahni_csv("sankce")

    # --- Kontroly podle IČO -------------------------------------------------------
    print("Třídím kontroly podle IČO…", flush=True)
    kontrola_ico = {}
    firmy = defaultdict(lambda: {"kontroly": set(), "data": [], "mista": set()})
    bez_ico = neplatne = 0
    for r in kontroly:
        surove = (r.get("IC subjektu") or "").strip()
        if not surove:
            bez_ico += 1
            continue
        ico = platne_ico(surove)
        if not ico:
            neplatne += 1
            continue
        kid = r["Id kontroly"].strip()
        kontrola_ico[kid] = ico
        f = firmy[ico]
        f["kontroly"].add(kid)
        d = datum_iso(r.get("Datum kontroly"))
        if d:
            f["data"].append(d)
        obec = (r.get("Obec") or "").strip()
        if obec:
            f["mista"].add(obec)
    print(f"  kontrol bez IČO (anonymizovaní živnostníci): {bez_ico:_}".replace("_", " "), flush=True)
    print(f"  kontrol s neplatným IČO (vyřazeno): {neplatne:_}".replace("_", " "), flush=True)
    print(f"  firem s platným IČO: {len(firmy):_}".replace("_", " "), flush=True)

    # --- Pokuty: sloučit řádky podle ID Sankce ------------------------------------
    print("Slučuji pokuty podle ID sankce…", flush=True)
    pokuty = {}
    nenavazane = set()
    predpisy_pocet = Counter()
    for r in sankce:
        sid = r["Id Sankce"].strip()
        kid = r["ID kontroly"].strip()
        ico = kontrola_ico.get(kid)
        if not ico:
            nenavazane.add(sid)
            continue
        p = pokuty.setdefault(sid, {"ico": ico, "kc": int(r["Vyse pokuty"] or 0),
                                    "d": datum_iso(r.get("Datum nabyti Pravni Moci")), "pr": {}})
        zakon = (r.get("Zakon") or "").strip()
        par = (r.get("§") or "").strip()
        predpisy_pocet[zakon] += 1
        p["pr"].setdefault(zakon, [])
        if par and par not in p["pr"][zakon]:
            p["pr"][zakon].append(par)
    print(f"  pokut navázaných na firmu: {len(pokuty):_}".replace("_", " "), flush=True)
    print(f"  pokut, jejichž kontrola nemá IČO (nelze přiřadit): {len(nenavazane):_}".replace("_", " "), flush=True)
    neznamy = [z for z, _ in predpisy_pocet.most_common() if z not in PREDPISY][:25]
    print(f"  předpisy bez srozumitelného názvu (zobrazí se jen číslem): {', '.join(neznamy)}", flush=True)

    # --- ARES ------------------------------------------------------------------------
    ares = nacti_ares(sorted(firmy))

    # --- Sestavení výstupu -----------------------------------------------------------
    print("Sestavuji data pro web…", flush=True)
    pokuty_firmy = defaultdict(list)
    for sid, p in pokuty.items():
        pokuty_firmy[p["ico"]].append(p)

    predpis_kody = sorted({z for p in pokuty.values() for z in p["pr"]})
    predpis_idx = {z: i for i, z in enumerate(predpis_kody)}

    index, dily = [], defaultdict(dict)
    vyrazeno_fo = bez_nazvu = 0
    soucet_kc = 0
    for ico, f in firmy.items():
        a = ares.get(ico) or {}
        if je_fyzicka_osoba(a.get("pf")):
            vyrazeno_fo += 1
            continue
        if not a.get("n"):
            bez_nazvu += 1
        pk = sorted(pokuty_firmy.get(ico, []), key=lambda p: p["d"] or "", reverse=True)
        data = sorted(f["data"])
        zaznam = {
            "n": a.get("n"),
            "a": a.get("a"),
            "z": a.get("z"),
            "k": len(f["kontroly"]),
            "od": data[0] if data else None,
            "do": data[-1] if data else None,
            "m": len(f["mista"]),
            "p": [[p["d"], p["kc"], [[predpis_idx[z], "; ".join(par)] for z, par in p["pr"].items()]] for p in pk],
        }
        kc = sum(p["kc"] for p in pk)
        soucet_kc += kc
        dily[ico[:2]][ico] = zaznam
        index.append([ico, a.get("n") or "", a.get("o") or "", len(f["kontroly"]), len(pk), kc])

    index.sort(key=lambda x: (-x[3], x[0]))
    ted = datetime.now(timezone.utc).astimezone()
    meta = {
        "aktualizace": ted.strftime("%Y-%m-%d"),
        "obdobi_od": min((f["od"] for d in dily.values() for f in d.values() if f["od"]), default=None),
        "obdobi_do": max((f["do"] for d in dily.values() for f in d.values() if f["do"]), default=None),
        "zdroje": {"kontroly": STRANKA_KONTROLY, "sankce": STRANKA_SANKCE, "ares": "https://ares.gov.cz/"},
        "predpisy": [[z, PREDPISY.get(z, "")] for z in predpis_kody],
        "pocty": {"firem": len(index), "kontrol": sum(x[3] for x in index), "pokut": sum(x[4] for x in index),
                  "kc": soucet_kc, "kontrol_bez_ico": bez_ico},
    }
    js_soubor(vystup / "firmy-index.js", "window.FIRMY_INDEX", {**meta, "f": index})
    for stary in (vystup / "firmy").glob("*.js"):
        stary.unlink()
    for dil, obsah in dily.items():
        js_soubor(vystup / "firmy" / f"{dil}.js", f'(window.FIRMY_DILY=window.FIRMY_DILY||{{}})["{dil}"]', obsah)

    print(f"  vyřazeno jako fyzické osoby: {vyrazeno_fo}", flush=True)
    print(f"  právní formy v datech: {Counter((ares.get(i) or {}).get('pf') for i in firmy).most_common(8)}", flush=True)
    print(f"  firmy, které ARES nezná (zobrazí se jen IČO): {bez_nazvu:_}".replace("_", " "), flush=True)

    # --- Kontrola výstupu -----------------------------------------------------------
    print("Kontroluji výstup…", flush=True)
    txt = (vystup / "firmy-index.js").read_text(encoding="utf-8")
    zpet = json.loads(txt[txt.index("=") + 1:].rstrip().rstrip(";"))
    assert len(zpet["f"]) == len(index)
    celkem_dily = 0
    kc_dily = 0
    for soubor in (vystup / "firmy").glob("*.js"):
        t = soubor.read_text(encoding="utf-8")
        d = json.loads(t.split('"]=', 1)[1].rstrip().rstrip(";"))
        celkem_dily += len(d)
        kc_dily += sum(p[1] for z in d.values() for p in z["p"])
    assert celkem_dily == len(index), (celkem_dily, len(index))
    assert kc_dily == soucet_kc, (kc_dily, soucet_kc)
    vel_index = (vystup / "firmy-index.js").stat().st_size / 1e6
    vel_dily = sum(s.stat().st_size for s in (vystup / "firmy").glob("*.js")) / 1e6
    print(f"  index: {len(zpet['f']):_} firem, {vel_index:.1f} MB".replace("_", " "), flush=True)
    print(f"  detaily: {len(dily)} souborů, celkem {vel_dily:.1f} MB, součty pokut sedí", flush=True)
    p = meta["pocty"]
    print(f"Hotovo. {p['firem']:_} firem, {p['kontrol']:_} kontrol, {p['pokut']:_} pokut "
          f"za {p['kc']:_} Kč (období {meta['obdobi_od']} až {meta['obdobi_do']}).".replace("_", " "), flush=True)
