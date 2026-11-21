# /// script
# requires-python = ">=3.12"
# dependencies = ["pymupdf"]
# ///
"""
Připraví data pro nástroj „Benzinky s pokutou za palivo“.

Zdroj: otevřená data České obchodní inspekce „Sankce za nejakostní paliva“ (JSON-LD, ČOI aktualizuje denně).
Každý záznam = jedno pravomocné rozhodnutí o pokutě a odkaz na jeho PDF.

Datová sada neobsahuje adresu čerpací stanice, ta je jen v textu rozhodnutí. Proto:
  - adresy stanic bereme z ručně ověřené tabulky obsah/data/benzinky-adresy.csv
    (klíč = spisová značka; každou adresu jsme přečetli v rozhodnutí ČOI),
  - u nového rozhodnutí, které v tabulce ještě není, skript zkusí adresu najít v PDF a vypíše ji
    do logu jako NÁVRH k doplnění; na webu se do té doby ukáže jen odkaz na rozhodnutí.
Co bylo s palivem špatně (sledovaný ukazatel jakosti), skript čte z textu rozhodnutí.
Podnikající fyzické osoby (podle ARES) na webu nejmenujeme, jméno je jen v rozhodnutí ČOI.

Výstup: web/data/benzinky.js
"""
import os
import csv
import hashlib
import json
import re
import sys
import tempfile
import time
import urllib.parse
import urllib.request
from collections import Counter
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


ZDROJ_URL = "https://coi.gov.cz/userdata/files/dokumenty-ke-stazeni/open-data/pokuty-paliva.jsonld"
ZDROJ_STRANKA = "https://coi.gov.cz/pro-spotrebitele/otevrena-data/sankce-za-nejakostni-paliva/"
ARES_URL = "https://ares.gov.cz/ekonomicke-subjekty-v-be/rest/ekonomicke-subjekty/"
CACHE = Path(tempfile.gettempdir()) / "jsem-spotrebitel-paliva"
ARES_CACHE = Path(tempfile.gettempdir()) / "jsem-spotrebitel-ares-cache.json"

PALIVA = {
    "NM": "Nafta",
    "SUPER-BA-95": "Benzín 95",
    "SUPER-PLUS-BA-98": "Benzín 98",
    "LPG": "LPG",
    "CNG": "CNG",
    "E85": "E85",
}

# Ukazatele jakosti: (klíč, srozumitelný popis, vzor v textu rozhodnutí)
ZAVADY = [
    ("vzplanuti", "Příliš nízký bod vzplanutí", r"bod\w*\s+vzplanutí"),
    ("oxidace", "Nevyhovující oxidační stabilita", r"oxidační\s+stabilit|indukční\s+perioda"),
    ("ethanol", "Nesprávný obsah ethanolu", r"ethanol|kyslíkat"),
    ("sira", "Příliš mnoho síry", r"\bsír[ay]\b|obsah\w*\s+síry"),
    ("tlak", "Nevyhovující tlak par", r"tlak\w*\s+par"),
    ("destilace", "Nevyhovující destilace", r"destilac|odpařen"),
    ("oktan", "Nízké oktanové číslo", r"oktanov"),
    ("voda", "Příliš mnoho vody", r"\bvod[ay]\b|obsah\w*\s+vody"),
    ("memk", "Nesprávný podíl bionafty (MEMK)", r"MEMK|methylester"),
    ("cfpp", "Špatná filtrovatelnost v mrazu", r"CFPP|filtrovatelnost"),
]


def klic_spisu(sz: str) -> str:
    m = re.search(r"\d+/\d+/[0-9A-Z]+", sz or "")
    return m.group(0) if m else (sz or "").strip()


def stahni(url: str, cil: Path | None = None) -> bytes:
    u = urllib.parse.quote(url, safe=":/%?=&")
    with urllib.request.urlopen(urllib.request.Request(u, headers={"User-Agent": "Mozilla/5.0"}), timeout=90) as r:
        data = r.read()
    if cil:
        cil.write_bytes(data)
    return data


def text_pdf(pdf: Path) -> str:
    import pymupdf
    try:
        t = "\n".join(p.get_text() for p in pymupdf.open(pdf))
    except Exception:
        return ""
    return re.sub(r"\s+", " ", t)


def urci_zavadu(text: str) -> str | None:
    """Najde první zmínku o ukazateli jakosti; když ji nenajde, vezme nejdřív zmíněný ukazatel v celém textu."""
    okna = [m.end() for m in re.finditer(r"ukazatel\w*|parametr\w*|kritéri\w*", text)]
    for start in okna[:3]:
        usek = text[max(0, start - 20): start + 160]
        for klic, _, vzor in ZAVADY:
            if re.search(vzor, usek, re.I):
                return klic
    nejdriv = None
    for klic, _, vzor in ZAVADY:
        m = re.search(vzor, text, re.I)
        if m and (nejdriv is None or m.start() < nejdriv[0]):
            nejdriv = (m.start(), klic)
    return nejdriv[1] if nejdriv else None


def navrh_adresy(text: str) -> str | None:
    m = re.search(r"(?:na adrese|ČS|čerpací stanic\w*)[:,\s]+([^,;]{3,60},\s*[^,;]{2,50}(?:,\s*\d{3}\s?\d{2}[^,;]{0,40})?)", text)
    return m.group(1).strip() if m else None


def ares(ica: set[str]) -> dict:
    cache = {}
    if ARES_CACHE.exists():
        try:
            cache = json.loads(ARES_CACHE.read_text(encoding="utf-8"))
        except Exception:
            cache = {}
    for ico in sorted(ica):
        if ico in cache and cache[ico].get("n"):
            continue
        try:
            with urllib.request.urlopen(urllib.request.Request(ARES_URL + ico, headers={"Accept": "application/json"}), timeout=30) as r:
                s = json.loads(r.read().decode("utf-8"))
            cache[ico] = {"t": time.time(), "n": s.get("obchodniJmeno"), "pf": s.get("pravniForma"),
                          "a": (s.get("sidlo") or {}).get("textovaAdresa"), "o": (s.get("sidlo") or {}).get("nazevObce")}
        except Exception:
            cache.setdefault(ico, {"t": time.time(), "n": None})
        time.sleep(0.2)
    ARES_CACHE.write_text(json.dumps(cache, ensure_ascii=False), encoding="utf-8")
    return cache


def je_fyzicka_osoba(pf) -> bool:
    return str(pf or "") in ("100", "101", "102", "103", "104", "105", "106", "107", "108", "424", "425")


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

    print("Spouštím přípravu dat pro „Benzinky s pokutou za palivo“…", flush=True)
    print(f"Stahuji {ZDROJ_URL} …", flush=True)
    zaznamy = json.loads(stahni(ZDROJ_URL).decode("utf-8"))
    print(f"  rozhodnutí v datech ČOI: {len(zaznamy)}", flush=True)

    adresy = {}
    with open(slozka / "obsah" / "data" / "benzinky-adresy.csv", encoding="utf-8", newline="") as f:
        for r in csv.DictReader(f, delimiter=";"):
            adresy[klic_spisu(r["spisova_znacka"])] = r
    print(f"  ověřených adres stanic v tabulce: {len(adresy)}", flush=True)

    ares_data = ares({(z.get("prodejce") or {}).get("ičo", "").zfill(8) for z in zaznamy if (z.get("prodejce") or {}).get("ičo")})

    # IČO firem, které má nástroj „Prověř firmu“ (aby odkaz na provozovatele nevedl do prázdna)
    ica_proverit = set()
    idx = slozka / "web" / "data" / "firmy-index.js"
    if idx.exists():
        t = idx.read_text(encoding="utf-8")
        ica_proverit = {f[0] for f in json.loads(t[t.index("=") + 1:].rstrip().rstrip(";"))["f"]}

    vystup, chybi_adresa, nezname_zavady = [], [], []
    fo = 0
    for n, z in enumerate(zaznamy, 1):
        nazev = z["název"]["cs"]
        url = z["url"]
        pdf = CACHE / (hashlib.sha1(url.encode()).hexdigest()[:12] + ".pdf")
        if not pdf.exists():
            print(f"  [{n}/{len(zaznamy)}] stahuji rozhodnutí {nazev}", flush=True)
            try:
                stahni(url, pdf)
            except Exception as chyba:
                print(f"    PDF se nepodařilo stáhnout ({chyba})", flush=True)
        text = text_pdf(pdf) if pdf.exists() else ""
        zavada = urci_zavadu(text) if len(text) > 100 else None

        sz = klic_spisu(z.get("spisová_značka"))
        ico = (z.get("prodejce") or {}).get("ičo", "").zfill(8)
        a = ares_data.get(ico) or {}
        osoba = je_fyzicka_osoba(a.get("pf"))
        z_nazvu = re.sub(r"^\d{2,4}[-_]\d{1,2}[-_]\d{1,2}[-_ ]+|[-_ ]+I(?:-II)?[-_].*$", "", nazev).replace("-", " ").strip()
        if not a.get("n") and not re.search(r"s\.\s?r\.\s?o|a\.\s?s\b|spol\.|v\.o\.s|k\.s\.", z_nazvu, re.I):
            osoba = True  # ARES provozovatele nezná a název nevypadá jako firma -> raději nejmenovat
        fo += osoba
        if osoba:
            provozovatel = "Podnikající fyzická osoba"
        else:
            provozovatel = a.get("n") or z_nazvu

        st = adresy.get(sz)
        if st and (st.get("zavada") or "").strip():
            zavada = st["zavada"].strip()  # ručně určeno (např. naskenované rozhodnutí)
        if not st:
            chybi_adresa.append((sz, nazev, navrh_adresy(text) if text else None))
        if zavada is None:
            nezname_zavady.append(nazev)

        pm = (z.get("nabylo_právní_moci") or {}).get("datum_a_čas", "")[:10]
        vystup.append({
            "sz": sz,
            "st": st["stanice"] if st else None,
            "ad": st["adresa"] if st else None,
            "ob": st["obec"] if st else None,
            "pr": provozovatel,
            "ico": None if osoba else ico,
            "fi": (not osoba) and ico in ica_proverit,
            "pal": [PALIVA.get(p.rsplit("/", 1)[-1], p.rsplit("/", 1)[-1]) for p in z.get("druh_paliva", [])],
            "za": zavada,
            "kc": int((z.get("pokuta") or {}).get("výše") or 0),
            "pm": pm or None,
            "od": "odvolací" in " ".join(z.get("typ_rozhodnutí", [])),
            "url": url,
        })
        print(f"  [{n}/{len(zaznamy)}] {pm} · {(st or {}).get('obec', '???'):<22} · {', '.join(vystup[-1]['pal']):<10} · "
              f"{dict((k, p) for k, p, _ in ZAVADY).get(zavada, 'NEURČENO'):<32} · {vystup[-1]['kc']:>9_} Kč".replace("_", " "), flush=True)

    vystup.sort(key=lambda x: x["pm"] or "", reverse=True)
    data = {
        "aktualizace": datetime.now().strftime("%Y-%m-%d"),
        "zdroj": ZDROJ_STRANKA,
        "zavady": {k: p for k, p, _ in ZAVADY},
        "pokuty": vystup,
    }
    cil = slozka / "web" / "data" / "benzinky.js"
    cil.write_text("window.BENZINKY=" + json.dumps(data, ensure_ascii=False, separators=(",", ":")) + ";\n", encoding="utf-8")

    # --- kontrola ---
    t = cil.read_text(encoding="utf-8")
    zpet = json.loads(t[t.index("=") + 1:].rstrip().rstrip(";"))
    assert len(zpet["pokuty"]) == len(zaznamy)
    soucet = sum(p["kc"] for p in zpet["pokuty"])
    assert soucet == sum(int(z["pokuta"]["výše"]) for z in zaznamy)
    print("", flush=True)
    print(f"Fyzické osoby (bez jména na webu): {fo}", flush=True)
    print(f"Ukazatele jakosti: {Counter(p['za'] for p in vystup).most_common()}", flush=True)
    if nezname_zavady:
        print(f"U {len(nezname_zavady)} rozhodnutí se nepodařilo určit, co bylo s palivem špatně: {nezname_zavady}", flush=True)
    if chybi_adresa:
        print(f"POZOR: {len(chybi_adresa)} nových rozhodnutí bez ověřené adresy. Doplň je do obsah/data/benzinky-adresy.csv:", flush=True)
        for sz, nazev, navrh in chybi_adresa:
            print(f"   {sz} · {nazev} · návrh z PDF: {navrh or '–'}", flush=True)
    else:
        print("Všechna rozhodnutí mají ověřenou adresu stanice.", flush=True)
    print(f"Hotovo. {len(vystup)} pokut za {soucet:_} Kč, uloženo do web/data/benzinky.js".replace("_", " "), flush=True)
