# /// script
# requires-python = ">=3.12"
# dependencies = ["markdown"]
# ///
"""
Poskládá celý web jsemspotrebitel.cz do složky web/.

Jak přidat obsah (bez programování):
  obsah/<sekce>/<adresa>.md    → nová stránka v sekci (sekce: nastroje, prava, navody, clanky, podcast)
  obsah/stranky/<adresa>.md    → samostatná stránka (např. o-projektu)
Na začátku souboru je hlavička:
  ---
  nazev: Reklamace a záruka
  kratky_popis: Jednou větou, co se tu člověk dozví (zobrazí se na dlaždici).
  popis: Podtitulek stránky.
  na_uvod: ano          (zobrazit dlaždici na úvodní stránce)
  poradi: 3             (pořadí na úvodní stránce)
  rubrika: Kauza týdne  (jen u článků)
  datum: 2026-11-20     (jen u článků)
  ---
Jakmile soubor existuje, položka se v sekci i na úvodu automaticky přepne z „Připravujeme“ na odkaz.
"""
import hashlib
import html
import json
import os
import re
import sys
from datetime import date
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


# ---------------------------------------------------------------- struktura webu
SEKCE = [
    {"slug": "nastroje", "nazev": "Nástroje", "stitek": "Nástroj", "ikona": "nastroj",
     "popis": "Praktické pomůcky, které ti během chvilky řeknou, na čem jsi."},
    {"slug": "prava", "nazev": "Tvoje práva", "stitek": "Tvoje práva", "ikona": "vahy",
     "popis": "Co ti jako spotřebiteli patří. Srozumitelně a s odkazem na zákon."},
    {"slug": "navody", "nazev": "Návody a vzory", "stitek": "Návod a vzor", "ikona": "seznam",
     "popis": "Postupy krok za krokem a vzory dopisů, které si můžeš stáhnout."},
    {"slug": "clanky", "nazev": "Články", "stitek": "Článek", "ikona": "noviny",
     "popis": "Věděl jsi, že…, kauzy z praxe a novinky ze spotřebitelského světa."},
    {"slug": "podcast", "nazev": "Podcast", "stitek": "Podcast", "ikona": "mikrofon",
     "popis": "Spotřebitelské příběhy a práva do sluchátek."},
]
MENU = [(s["slug"], s["nazev"]) for s in SEKCE] + [("o-projektu", "O projektu")]
RUBRIKY = ["Věděl jsi, že…", "Kauza týdne", "Aktuality"]

# Položky, které teprve vzniknou. Jakmile pro ně přibude soubor v obsah/, přepnou se samy na odkaz.
PLANOVANE = [
    ("nastroje", "kalkulacka-lhut", "Kalkulačka lhůt", "Do kdy můžeš vrátit zboží nebo reklamovat.", 3),
    ("nastroje", "kam-si-stezovat", "Kam si stěžovat", "Který úřad řeší právě tvůj problém.", 6),
    ("nastroje", "benzinky", "Benzinky s pokutou za palivo", "Kde ČOI našla nekvalitní pohonné hmoty.", None),
    ("nastroje", "nepovolene-pujcky", "Nepovolené půjčky a investice", "Kdo nabízí finanční služby bez povolení ČNB.", None),
    ("nastroje", "nebezpecne-vyrobky", "Nebezpečné výrobky", "Výrobky stažené z trhu kvůli bezpečnosti.", None),
    ("prava", "nakup-na-internetu", "Nákup na internetu", "Co ti e-shop musí říct a co ti nesmí upřít.", 5),
    ("prava", "reklamace-a-zaruka", "Reklamace a záruka", "Kdy, jak a v jaké lhůtě reklamovat.", 2),
    ("prava", "odstoupeni-od-smlouvy", "Odstoupení od smlouvy", "Vrácení zboží do 14 dnů bez udání důvodu.", None),
    ("prava", "energie-telefon-internet", "Energie, telefon a internet", "Smlouvy, výpovědi a vyúčtování.", None),
    ("prava", "cestovani", "Cestování a letenky", "Zpoždění, zrušené lety a zájezdy.", None),
    ("prava", "pujcky-a-finance", "Půjčky a finance", "Úvěry, poplatky a předčasné splacení.", None),
    ("navody", "vzor-odstoupeni", "Vzor: odstoupení do 14 dnů", "Hotový dopis, stačí doplnit údaje.", 4),
    ("navody", "vzor-reklamace", "Vzor: reklamace", "Dopis k reklamaci vadného zboží.", 8),
    ("navody", "jak-reklamovat", "Jak reklamovat krok za krokem", "Od nahlášení vady po vyřízení.", None),
    ("navody", "podnet-na-coi", "Jak podat podnět na ČOI", "Kdy to má smysl a co do podnětu napsat.", None),
    ("navody", "spor-bez-soudu", "Spor bez soudu", "Mimosoudní řešení sporu s obchodníkem.", None),
    ("podcast", "epizody", "Epizody podcastu", "Seznam dílů a odkazy na Spotify a YouTube.", None),
]

IKONY = {
    "nastroj": '<path d="M14.5 6.5a4 4 0 0 0 5 5L12 19a2.1 2.1 0 0 1-3-3z"/><path d="M14.5 6.5L17 4l3 3-2.5 2.5"/>',
    "vahy": '<path d="M12 4v16"/><path d="M7 20h10"/><path d="M5 8h14"/><path d="M5 8l-3 6a3 3 0 0 0 6 0z"/><path d="M19 8l-3 6a3 3 0 0 0 6 0z"/>',
    "seznam": '<path d="M9 6h11"/><path d="M9 12h11"/><path d="M9 18h11"/><path d="M4 6l1 1 2-2"/><path d="M4 12l1 1 2-2"/><path d="M4 18l1 1 2-2"/>',
    "noviny": '<path d="M5 4h12v16H6a2 2 0 0 1-2-2V5a1 1 0 0 1 1-1z"/><path d="M17 8h3v10a2 2 0 0 1-2 2"/><path d="M8 8h5"/><path d="M8 12h5"/><path d="M8 16h3"/>',
    "mikrofon": '<rect x="9" y="3" width="6" height="11" rx="3"/><path d="M5 11a7 7 0 0 0 14 0"/><path d="M12 18v3"/>',
    "info": '<circle cx="12" cy="12" r="9"/><path d="M12 11v5"/><path d="M12 8h.01"/>',
    "sipka": '<path d="M5 12h14"/><path d="M13 6l6 6-6 6"/>',
    "menu": '<path d="M4 7h16"/><path d="M4 12h16"/><path d="M4 17h16"/>',
}


def svg(nazev, trida="ik"):
    return (f'<svg class="{trida}" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" '
            f'stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">{IKONY[nazev]}</svg>')


# ---------------------------------------------------------------- vzhled
STYL = r"""
:root {
  --krem: #FBF3EC; --krem2: #F3E6DA; --kobalt: #1E3AA6; --kobalt2: #142873;
  --koral: #FF6B4A; --karmin: #D62839; --karmin2: #B81F2F; --karmin-svetly: #FCE8E8;
  --nebe: #B8CBF0; --nebe2: #E8EEFB; --text: #1A2340; --tlum: #5A6180; --okraj: #E6D8CB; --bila: #FFFFFF;
  --stin: 0 6px 24px rgba(30,58,166,.08);
  --f-nadpis: "Fraunces", Georgia, "Times New Roman", serif;
  --f-text: "Work Sans", system-ui, -apple-system, "Segoe UI", sans-serif;
}
* { box-sizing: border-box; }
html { -webkit-text-size-adjust: 100%; }
body { margin: 0; background: var(--krem); color: var(--text); font-family: var(--f-text); font-size: 17px; line-height: 1.55; min-height: 100vh; display: flex; flex-direction: column; }
body > main { flex: 1 0 auto; }
a { color: var(--kobalt); }
button { font: inherit; cursor: pointer; }
:focus-visible { outline: 3px solid var(--koral); outline-offset: 2px; }
.sr { position: absolute; left: -9999px; }
.obal { max-width: 1120px; margin: 0 auto; padding-left: max(16px, env(safe-area-inset-left, 0px)); padding-right: max(16px, env(safe-area-inset-right, 0px)); }
@media (min-width: 761px) { .obal { padding-left: max(40px, env(safe-area-inset-left, 0px)); padding-right: max(40px, env(safe-area-inset-right, 0px)); } }

/* Kobaltový pás s hlavičkou */
.pas { background: var(--kobalt); color: var(--krem); padding-block: 18px 52px; position: relative; }
/* Dekorativní kruhy mají vlastní ořezávanou vrstvu, aby pás nemusel mít overflow:hidden
   (to na mobilu posouvalo hlavičku do strany a ořezávalo rozbalené menu). */
.pas .dekor { position: absolute; inset: 0; overflow: hidden; pointer-events: none; }
.pas .dekor::after { content: ""; position: absolute; right: -120px; bottom: -230px; width: 380px; height: 380px; border-radius: 50%; background: var(--koral); }
.pas .obal { position: relative; z-index: 1; }
.pas .obal:has(details[open]) { z-index: 20; }
.pas-uvod { padding-bottom: 70px; }
.pas-uvod .dekor::after { width: 460px; height: 460px; right: -170px; top: 80px; bottom: auto; }
.pas-uvod .dekor::before { content: ""; position: absolute; right: 32%; bottom: -70px; width: 170px; height: 170px; border-radius: 50%; border: 22px solid var(--karmin); }
.pas-prekryv { padding-bottom: 130px; }
.lista { display: flex; align-items: center; justify-content: space-between; gap: 16px; position: relative; z-index: 4; }
.wordmark { font-family: var(--f-nadpis); font-size: 20px; color: var(--krem); text-decoration: none; white-space: nowrap; }
.wordmark b { font-weight: 700; }
.wordmark .tecka { color: var(--koral); }
.menu { display: flex; gap: 4px; list-style: none; margin: 0; padding: 0; }
.menu a { display: block; color: var(--krem); text-decoration: none; font-weight: 500; font-size: 15.5px; padding: 8px 12px; border-radius: 999px; }
.menu a:hover { background: rgba(251,243,236,.14); }
.menu a[aria-current="page"] { background: var(--krem); color: var(--kobalt); }
.menu-mobil { display: none; }
.drobky { margin: 40px 0 0; font-size: 14.5px; color: var(--nebe); }
.drobky a { color: var(--nebe); }
.pas h1 { font-family: var(--f-nadpis); font-weight: 900; font-size: clamp(36px, 6vw, 68px); line-height: 1; letter-spacing: -.025em; margin: 40px 0 14px; max-width: 760px; text-wrap: balance; }
.drobky + h1 { margin-top: 12px; }
.pas h1 em { font-style: normal; color: var(--koral); }
.pas .sub { color: var(--nebe); font-size: clamp(17px, 2.2vw, 20px); margin: 0; max-width: 600px; }
.pas-uvod h1 { font-size: clamp(40px, 7vw, 84px); margin-top: 56px; max-width: min(760px, 62%); }
.pas-uvod .sub { max-width: min(560px, 60%); }
.logo-kruh { position: absolute; right: max(16px, env(safe-area-inset-right, 0px)); top: 54%; transform: translateY(-40%); width: clamp(210px, 23vw, 290px); aspect-ratio: 1; max-width: 100%; border-radius: 50%; background: var(--krem); display: grid; place-items: center; box-shadow: 0 0 0 14px rgba(251,243,236,.18); z-index: 2; }
.logo-kruh img { width: 76%; height: auto; }

/* Sekce a dlaždice */
main { display: block; }
.sekce { padding-block: 56px 8px; }
.sekce-nadpis { font-family: var(--f-nadpis); font-weight: 900; color: var(--kobalt); font-size: clamp(28px, 4.5vw, 42px); line-height: 1.05; letter-spacing: -.02em; margin: 0 0 8px; text-wrap: balance; }
.sekce-popis { color: var(--tlum); margin: 0 0 24px; max-width: 640px; }
.mrizka { display: grid; grid-template-columns: repeat(auto-fill, minmax(240px, 1fr)); gap: 14px; }
.polozka { display: flex; flex-direction: column; gap: 8px; background: var(--bila); border-radius: 18px; padding: 18px 18px 16px; border: 1.5px solid var(--okraj); color: var(--text); text-decoration: none; min-width: 0; transition: transform .15s, border-color .15s, box-shadow .15s; }
a.polozka:hover { border-color: var(--kobalt); box-shadow: var(--stin); transform: translateY(-2px); }
.polozka .hlava { display: flex; flex-wrap: wrap; justify-content: space-between; align-items: center; gap: 6px 8px; }
.stitek { white-space: nowrap; display: inline-flex; align-items: center; gap: 6px; font-size: 12px; font-weight: 700; letter-spacing: .05em; text-transform: uppercase; color: var(--kobalt); }
.stitek .ik { width: 16px; height: 16px; }
.stav { font-size: 12px; font-weight: 600; padding: 3px 9px; border-radius: 999px; white-space: nowrap; }
.stav.hotovo { background: var(--karmin); color: var(--bila); }
.stav.brzy { background: var(--krem2); color: var(--tlum); }
.polozka h3 { margin: 4px 0 0; font-family: var(--f-nadpis); font-size: 21px; line-height: 1.2; color: var(--kobalt); text-wrap: balance; }
.polozka p { margin: 0; font-size: 15px; color: var(--tlum); }
.polozka .dale { margin-top: auto; padding-top: 6px; display: inline-flex; align-items: center; gap: 6px; font-weight: 600; font-size: 15px; color: var(--karmin); }
.polozka .dale .ik { width: 18px; height: 18px; transition: transform .15s; }
a.polozka:hover .dale .ik { transform: translateX(3px); }
div.polozka { background: transparent; border-style: dashed; }
div.polozka h3 { color: var(--text); opacity: .75; }

.rozcestnik { display: grid; grid-template-columns: repeat(auto-fit, minmax(185px, 1fr)); gap: 14px; }
.rozcestnik a { display: flex; flex-direction: column; gap: 6px; padding: 18px; border-radius: 18px; background: var(--kobalt); color: var(--krem); text-decoration: none; min-width: 0; }
.rozcestnik a:hover { background: var(--kobalt2); }
.rozcestnik .ik { width: 28px; height: 28px; color: var(--koral); }
.rozcestnik b { font-family: var(--f-nadpis); font-size: 22px; font-weight: 700; }
.rozcestnik span { font-size: 14px; color: var(--nebe); }

.prazdno { background: var(--bila); border: 1.5px dashed var(--okraj); border-radius: 18px; padding: 28px; text-align: center; color: var(--tlum); }
.prazdno b { display: block; font-family: var(--f-nadpis); color: var(--kobalt); font-size: 22px; margin-bottom: 4px; }
.rubriky { display: flex; flex-wrap: wrap; gap: 8px; margin-bottom: 18px; }
.rubriky span { font-size: 14px; border: 1.5px solid var(--okraj); background: var(--bila); border-radius: 999px; padding: 6px 13px; }
.poznamka { background: var(--bila); border-radius: 16px; padding: 16px 18px; font-size: 15px; color: var(--tlum); border: 1px solid var(--okraj); }

/* Text článků a stránek */
.text { max-width: 720px; padding-block: 40px 8px; }
.text h2 { font-family: var(--f-nadpis); font-weight: 700; color: var(--kobalt); font-size: clamp(24px, 3.4vw, 30px); line-height: 1.15; margin: 36px 0 10px; text-wrap: balance; }
.text h2:first-child { margin-top: 0; }
.text h3 { font-size: 19px; margin: 26px 0 8px; }
.text p, .text li { font-size: 18px; line-height: 1.65; }
.text blockquote { margin: 20px 0; padding-left: 16px; border-left: 4px solid var(--koral); font-family: var(--f-nadpis); color: var(--kobalt); font-size: 19px; }
.text table { border-collapse: collapse; width: 100%; font-size: 16px; }
.text th, .text td { border-bottom: 1px solid var(--okraj); padding: 8px 10px; text-align: left; }
.tabulka { overflow-x: auto; }
.meta-clanku { display: flex; flex-wrap: wrap; gap: 8px 16px; color: var(--nebe); font-size: 15px; margin-top: 14px; }

/* Patička */
footer { background: var(--kobalt); color: var(--nebe); padding-block: 44px 48px; margin-top: 72px; font-size: 14.5px; }
footer a { color: var(--krem); }
.paticka { display: grid; grid-template-columns: auto 1fr auto; gap: 32px; align-items: start; }
.mini-kruh { width: 110px; aspect-ratio: 1; border-radius: 50%; background: var(--krem); display: grid; place-items: center; }
.mini-kruh img { width: 78%; height: auto; }
.paticka p { margin: 0 0 10px; max-width: 560px; }
.paticka .wordmark { display: inline-block; margin-bottom: 10px; }
.paticka ul { list-style: none; margin: 0; padding: 0; display: grid; gap: 6px; }
.paticka h2 { font-size: 13px; text-transform: uppercase; letter-spacing: .08em; color: var(--krem); margin: 0 0 10px; }

@media (max-width: 960px) {
  .menu-pc { display: none; }
  .menu-mobil { display: block; position: relative; }
  .menu-mobil summary { list-style: none; cursor: pointer; display: inline-flex; align-items: center; gap: 8px; color: var(--krem); font-weight: 600; padding: 8px 14px; border-radius: 999px; background: rgba(251,243,236,.14); }
  .menu-mobil summary::-webkit-details-marker { display: none; }
  .menu-mobil summary .ik { width: 20px; height: 20px; }
  .menu-mobil .menu { position: absolute; right: 0; top: calc(100% + 8px); flex-direction: column; background: var(--krem); border-radius: 16px; padding: 8px; min-width: 220px; box-shadow: 0 20px 50px rgba(20,40,115,.3); }
  .menu-mobil .menu a { color: var(--kobalt); padding: 10px 14px; }
  .menu-mobil .menu a:hover { background: var(--krem2); }
  .menu-mobil .menu a[aria-current="page"] { background: var(--kobalt); color: var(--krem); }
}
@media (max-width: 760px) {
  body { font-size: 16px; }
  .pas .dekor::after { width: 220px; height: 220px; right: -110px; bottom: -150px; top: auto; }
  .pas-uvod .dekor::after { top: 60px; bottom: auto; }
  .pas-uvod .dekor::before { display: none; }
  .paticka ul { gap: 0; }
  .paticka ul a { padding-block: 9px; }
  .pas-prekryv { padding-bottom: 110px; }
  .logo-kruh { position: relative; right: auto; top: auto; transform: none; width: 150px; margin: 24px 0 0; }
  .pas-uvod h1 { margin-top: 18px; max-width: none; }
  .pas-uvod .sub { max-width: none; }
  .pas h1 { margin-top: 28px; }
  .paticka { grid-template-columns: 1fr; gap: 18px; }
  .text p, .text li { font-size: 17px; }
}
@media (prefers-reduced-motion: reduce) { * { transition: none !important; animation: none !important; } }
"""


# ---------------------------------------------------------------- doplňky vzhledu (z návrhu 2, Hallmark)
# Nástroje v průvodci: situace (slovy člověka), co k tomu potřebuje, a skutečný údaj z dat
PRUVODCE = [
    {"slug": "over-eshop", "situace": "Chci nakoupit na e-shopu, který neznám",
     "potrebujes": "adresu e-shopu nebo odkaz na zboží",
     "data": ("rizikove-weby.js", "rizikove-weby", "rizikových webů na seznamu ČOI")},
    {"slug": "proverit-firmu", "situace": "Chci vědět, jestli je firma poctivá",
     "potrebujes": "název firmy nebo její IČO",
     "data": ("firmy-index.js", "firmy", "firem s kontrolou ČOI")},
    {"slug": "benzinky", "situace": "Chci natankovat tam, kde nešidí",
     "potrebujes": "obec, ulici nebo název provozovatele",
     "data": ("benzinky.js", "benzinky", "pravomocných pokut za nekvalitní palivo")},
    {"slug": "nebezpecne-vyrobky", "situace": "Mám doma výrobek a nevím, jestli je bezpečný",
     "potrebujes": "název, značku nebo čárový kód z obalu",
     "data": ("vyrobky-index.js", "vyrobky", "varování EU za poslední dva roky")},
]

# Bubliny nad vyhledáváním v nástrojích (zmizí, jakmile člověk klikne do pole)
BUBLINY_NASTROJU = {
    "over-eshop": "Napiš adresu e-shopu, třeba obchod.cz, nebo sem vlož odkaz.",
    "proverit-firmu": "Stačí napsat název firmy nebo její IČO.",
    "benzinky": "Napiš obec nebo ulici, kde obvykle tankuješ.",
    "nebezpecne-vyrobky": "Opiš název, značku nebo čárový kód z obalu.",
}

STAMP = r"""/* Hallmark · shape: Guided Index · genre: editorial · theme: brand (by request) · redesign: full
 * paper: oklch(96.5% 0.013 60) · accent: oklch(55% 0.2 22) · display: Fraunces · body: Work Sans · hero: kept (/) · nav: kept · footer: mast, the logo beside the links · signature: the "Co právě řešíš?" guide, a situation in your words opens the tool with its real count
 * axes: light/roman-serif/warm · posture: kept · contrast: pass · fold: pass · v2.0.0 */
/* Hallmark · pre-emit critique: P4 H3 E4 S4 R4 V4 · weakest: H (the owner keeps the current hero), the guide sits right under it and a bubble points to it */
/* Hallmark · waive 8 · the owner chose which parts of proposal 1 to keep · the rest is the current site, by request */
/* Hallmark · waive 38iii · .pas h1 em is display:block, the whole second line in coral · the brand's two-line headline (Instagram, every tool) */
/* Hallmark · waive 65 · cream, Fraunces and the crimson/coral accents are the owner's brand kit · the user asked to keep the palette, logo and fonts */
/* Hallmark · waive 24b · the current site's spacing values, kept · the owner asked to keep the current look outside the chosen parts */
"""

# Doplňky k současnému vzhledu
DOPLNKY = r"""
/* ---------- tokeny doplňků ---------- */
:root {
  --space-2xs: 4px; --space-xs: 8px; --space-sm: 12px; --space-md: 16px; --space-lg: 24px; --space-xl: 40px; --space-2xl: 64px;
  --na-pasu: color-mix(in oklab, #FBF3EC 14%, transparent);
  --halo: color-mix(in oklab, #FBF3EC 18%, transparent);
  --stin-hluboky: 0 20px 50px color-mix(in oklab, #142873 28%, transparent);
  --stin-bubliny: 0 10px 28px color-mix(in oklab, #142873 22%, transparent);
  --bila: #FFFCF8;   /* světlá plocha místo čisté bílé (téměř nepoznatelné) */
  --rychle: 120ms; --stredne: 220ms; --plynule: cubic-bezier(0.16, 1, 0.3, 1);
}
html, body { overflow-x: clip; }
button:disabled { opacity: .55; cursor: not-allowed; }

/* Nadpis v pásu: barevná část je vždy celý druhý řádek */
.pas h1 em { display: block; font-style: normal; color: var(--koral); }

/* Jemné podtržení při najetí (jako v rozcestníku) */
.podtrh { text-decoration: underline; text-decoration-color: transparent; text-decoration-thickness: 2px; text-underline-offset: 0.18em; transition: text-decoration-color var(--stredne) var(--plynule); }
a.polozka h3 { text-decoration: underline; text-decoration-color: transparent; text-decoration-thickness: 2px; text-underline-offset: 0.18em; transition: text-decoration-color var(--stredne) var(--plynule); }
a.polozka:hover h3, a.polozka:focus-visible h3 { text-decoration-color: var(--koral); }
.text a, .poznamka a { text-decoration-color: var(--koral); text-underline-offset: 0.18em; }

/* Tlačítko */
.akce { display: flex; flex-wrap: wrap; align-items: center; gap: var(--space-md) var(--space-lg); margin-top: var(--space-lg); max-width: min(760px, 62%); }
.tlacitko { display: inline-flex; align-items: center; min-height: 52px; padding: 0 var(--space-lg); border-radius: 14px; background: var(--karmin); color: var(--bila); font-weight: 700; text-decoration: none; transition: background-color var(--rychle) var(--plynule), transform var(--rychle) var(--plynule); }
.tlacitko:hover { background: var(--karmin2); }
.tlacitko:active { transform: translateY(1px); }
.tlacitko.svetle { background: var(--kobalt); }
.tlacitko.svetle:hover { background: var(--kobalt2); }

/* ---------- navigační bubliny ---------- */
.bublina { position: relative; margin: 0; max-width: 300px; padding: var(--space-sm) var(--space-md); border-radius: 18px; background: var(--koral); color: var(--text); font-weight: 600; font-size: 15px; line-height: 1.35; box-shadow: var(--stin-bubliny); animation: bublina-ven 420ms var(--plynule) 300ms both; transition: opacity var(--stredne) var(--plynule), transform var(--stredne) var(--plynule); }
.bublina::after { content: ""; position: absolute; width: 14px; height: 14px; background: var(--koral); transform: rotate(45deg); }
.bublina-vlevo::after { left: -6px; top: calc(50% - 7px); }
.bublina-dolu::after { left: 32px; bottom: -6px; }
@keyframes bublina-ven { from { opacity: 0; transform: translateY(6px) scale(.96); } to { opacity: 1; transform: none; } }
/* Bublina nad vyhledáváním nástroje: když člověk klikne do pole, splnila úkol a zmizí */
.stred > .bublina { position: absolute; right: 24px; bottom: calc(100% + 18px); z-index: 4; }
.stred:focus-within > .bublina { opacity: 0; transform: translateY(-4px); pointer-events: none; }

/* ---------- úvod: proč tenhle web ---------- */
.proc { display: grid; grid-template-columns: minmax(0, 5fr) minmax(0, 7fr); gap: 32px; align-items: start; }
.proc h2 { font-family: var(--f-nadpis); font-weight: 900; color: var(--kobalt); font-size: clamp(30px, 4.5vw, 46px); line-height: 1.05; letter-spacing: -.02em; margin: 0; text-wrap: balance; }
.proc .uvodni { font-size: 19px; line-height: 1.6; margin: 0 0 var(--space-lg); max-width: 60ch; }
.proc ul { grid-column: 1 / -1; list-style: none; margin: 0; padding: 0; display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: var(--space-lg); }
.proc li { padding-top: var(--space-sm); border-top: 2px solid var(--kobalt); }
.proc li b { display: block; font-family: var(--f-nadpis); font-size: 19px; line-height: 1.2; color: var(--kobalt); margin-bottom: var(--space-2xs); }
.proc li span { font-size: 15px; color: var(--tlum); }

/* ---------- průvodce „Co právě řešíš?“ ---------- */
.pruvodce { display: grid; grid-template-columns: minmax(0, 5fr) minmax(0, 7fr); gap: 32px; align-items: start; }
.situace-seznam { display: grid; gap: var(--space-xs); }
.situace-sloupec .bublina { margin-bottom: var(--space-md); }
.situace { display: grid; grid-template-columns: 36px minmax(0, 1fr); gap: 2px var(--space-sm); align-items: center; padding: var(--space-md); border-radius: 18px; border: 1.5px solid var(--okraj); background: var(--bila); color: var(--text); text-decoration: none; transition: background-color var(--stredne) var(--plynule), border-color var(--stredne) var(--plynule), color var(--stredne) var(--plynule); }
.situace .ik { grid-row: span 2; width: 28px; height: 28px; color: var(--kobalt); }
.situace b { font-family: var(--f-nadpis); font-size: 19px; line-height: 1.2; text-decoration: underline; text-decoration-color: transparent; text-decoration-thickness: 2px; text-underline-offset: 0.18em; transition: text-decoration-color var(--stredne) var(--plynule); }
.situace span { font-size: 14px; color: var(--tlum); }
.situace:hover { border-color: var(--kobalt); }
.situace:hover b { text-decoration-color: var(--koral); }
.situace:active { background: var(--krem2); }
.situace[aria-selected="true"] { background: var(--kobalt); border-color: var(--kobalt); color: var(--krem); }
.situace[aria-selected="true"] .ik { color: var(--koral); }
.situace[aria-selected="true"] span { color: var(--nebe); }
.panel { background: var(--bila); border-radius: 20px; padding: 28px; border: 1.5px solid var(--okraj); }
.panel + .panel { margin-top: var(--space-md); }
.js .panel + .panel { margin-top: 0; }
.js .panel[hidden] { display: none; }
.panel .fakt { display: flex; flex-wrap: wrap; align-items: baseline; gap: var(--space-2xs) var(--space-sm); padding-bottom: var(--space-md); margin-bottom: var(--space-md); border-bottom: 1px solid var(--okraj); }
.panel .cislo { font-family: var(--f-nadpis); font-weight: 900; font-size: clamp(48px, 6vw, 76px); line-height: .95; letter-spacing: -.03em; color: var(--kobalt); font-variant-numeric: tabular-nums; white-space: nowrap; }
.panel .popisek { color: var(--tlum); font-size: 15px; }
.panel h3 { font-family: var(--f-nadpis); font-size: clamp(26px, 3vw, 34px); line-height: 1.1; color: var(--kobalt); margin: 0 0 var(--space-xs); }
.panel p { margin: 0 0 var(--space-md); max-width: 58ch; }
.panel dl { display: grid; grid-template-columns: auto minmax(0, 1fr); gap: var(--space-xs) var(--space-md); margin: 0 0 var(--space-lg); font-size: 15px; }
.panel dt { color: var(--tlum); }
.panel dd { margin: 0; font-weight: 600; }

/* ---------- brzy přibude: obyčejný seznam ---------- */
.chystame { list-style: none; margin: 0; padding: 0; display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: var(--space-lg) 32px; }
.chystame li { padding-top: var(--space-sm); border-top: 2px solid var(--kobalt); }
.chystame b { display: block; font-family: var(--f-nadpis); font-size: 20px; line-height: 1.2; color: var(--text); }
.chystame span { display: block; margin-top: var(--space-2xs); font-size: 14px; color: var(--tlum); }

/* ---------- Všechno na webu (z návrhu 1) ---------- */
.pas-sekci { background: var(--krem2); margin-top: 72px; padding-block: var(--space-2xl); }
.pas-sekci h2 { font-family: var(--f-nadpis); font-weight: 900; color: var(--kobalt); font-size: clamp(28px, 4.5vw, 46px); line-height: 1.05; letter-spacing: -.02em; margin: 0 0 var(--space-xl); }
.vse-na-webu { list-style: none; margin: 0; padding: 0; display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 0 32px; }
.vse-na-webu a { display: grid; grid-template-columns: 40px minmax(0, 1fr); gap: var(--space-2xs) var(--space-md); padding-block: var(--space-lg); border-top: 1px solid var(--okraj); color: var(--text); text-decoration: none; }
.vse-na-webu .ik { grid-row: span 2; width: 32px; height: 32px; color: var(--kobalt); margin-top: var(--space-2xs); }
.vse-na-webu b { font-family: var(--f-nadpis); font-size: 25px; line-height: 1.15; color: var(--kobalt); text-decoration: underline; text-decoration-color: transparent; text-decoration-thickness: 2px; text-underline-offset: 0.18em; transition: text-decoration-color var(--stredne) var(--plynule); }
.vse-na-webu span { font-size: 14px; color: var(--tlum); }
.vse-na-webu a:hover b, .vse-na-webu a:focus-visible b { text-decoration-color: var(--koral); }
main:has(.pas-sekci) + footer { margin-top: 0; }

/* ---------- patička (z návrhu 1) ---------- */
footer { padding-block: var(--space-2xl); }
.paticka { grid-template-columns: 120px minmax(0, 1fr) minmax(0, 14rem); gap: 32px; }
.mini-kruh { width: 120px; }
.paticka .wordmark { font-size: 25px; margin-bottom: var(--space-sm); }
.paticka h2 { font-size: 14.5px; font-weight: 600; text-transform: none; letter-spacing: 0; }
.paticka ul { gap: var(--space-2xs); }
footer a { text-underline-offset: 0.18em; transition: text-decoration-color var(--stredne) var(--plynule); }
footer a:hover { text-decoration-color: var(--koral); }

.sekce-hlava { display: flex; flex-wrap: wrap; justify-content: space-between; align-items: end; gap: var(--space-xs) 32px; margin-bottom: var(--space-lg); }
.sekce-hlava .sekce-nadpis { margin: 0; }
.sekce-hlava .sekce-popis { margin: 0; }

@media (max-width: 960px) {
  .proc, .pruvodce { grid-template-columns: minmax(0, 1fr); }
  .chystame { grid-template-columns: repeat(2, minmax(0, 1fr)); }
}
@media (max-width: 760px) {
  .akce { max-width: none; }
  .bublina-vlevo::after { left: 32px; top: -6px; }
  .stred > .bublina { position: static; margin: 0 0 var(--space-sm); max-width: none; }
  .proc ul, .chystame, .vse-na-webu { grid-template-columns: minmax(0, 1fr); }
  .panel { padding: 20px; }
  .paticka { grid-template-columns: minmax(0, 1fr); gap: var(--space-md); }
  .paticka ul a { display: block; padding-block: var(--space-xs); }
}
@media (prefers-reduced-motion: reduce) { .bublina { animation: none; } }
"""

SKRIPT_PRUVODCE = """
<script>
(function () {
  var seznam = document.querySelector("[role=tablist]");
  if (!seznam) return;
  document.documentElement.classList.add("js");
  var karty = Array.prototype.slice.call(seznam.querySelectorAll("[role=tab]"));
  function vyber(karta, zamerit) {
    karty.forEach(function (k) {
      var ano = k === karta;
      k.setAttribute("aria-selected", ano ? "true" : "false");
      k.tabIndex = ano ? 0 : -1;
      document.getElementById(k.getAttribute("aria-controls")).hidden = !ano;
    });
    if (zamerit) karta.focus();
  }
  karty.forEach(function (k, i) {
    k.addEventListener("click", function (e) { e.preventDefault(); vyber(k, false); });
    k.addEventListener("keydown", function (e) {
      var j = { ArrowDown: i + 1, ArrowRight: i + 1, ArrowUp: i - 1, ArrowLeft: i - 1, Home: 0, End: karty.length - 1 }[e.key];
      if (j === undefined) return;
      e.preventDefault();
      vyber(karty[(j + karty.length) % karty.length], true);
    });
  });
  vyber(karty[0], false);
})();
</script>
"""


def pocet_zaznamu(soubor: Path) -> int:
    text = soubor.read_text(encoding="utf-8").strip()
    data = json.loads(text[text.index("=") + 1:].rstrip(";").strip())
    return max((len(v) for v in data.values() if isinstance(v, list)), default=0)


def cislo(n: int) -> str:
    return f"{n:,}".replace(",", " ")


def styl_webu() -> str:
    """Základní vzhled (STYL) s opravami, které nejsou vidět, a doplňky (úvod, průvodce, bubliny, patička)."""
    s = STYL.strip()
    s = s.replace("rgba(251,243,236,.14)", "var(--na-pasu)").replace("rgba(251,243,236,.18)", "var(--halo)")
    s = s.replace("0 20px 50px rgba(20,40,115,.3)", "var(--stin-hluboky)")
    s = s.replace("font-size: 12px; font-weight: 700; letter-spacing: .05em; text-transform: uppercase;", "font-size: 13px; font-weight: 600;")
    s = s.replace(".paticka { display: grid; grid-template-columns: auto 1fr auto;", ".paticka { display: grid; grid-template-columns: auto minmax(0, 1fr) auto;")
    s = s.replace(".paticka { grid-template-columns: 1fr;", ".paticka { grid-template-columns: minmax(0, 1fr);")
    return STAMP + na_skalu(s) + "\n" + DOPLNKY.strip() + "\n"


SKALA = {"4": "2xs", "8": "xs", "12": "sm", "16": "md", "24": "lg", "40": "xl", "64": "2xl"}


def na_skalu(css: str) -> str:
    """Rozestupy, které sedí na škálu (4, 8, 12, 16, 24, 40, 64 px), převede na tokeny. Vzhled se nemění."""
    def dekl(m):
        return re.sub(r"(?<![\w.-])(\d+)px\b", lambda x: f"var(--space-{SKALA[x.group(1)]})" if x.group(1) in SKALA else x.group(0), m.group(0))
    return re.sub(r"(?<![\w-])(?:gap|row-gap|column-gap|margin(?:-[a-z]+)?|padding(?:-[a-z]+)?)\s*:[^;}]+", dekl, css)


def uprav_nastroj(slug: str, telo: str) -> str:
    """Nástroj: kód, id a rozvržení beze změny. Přibude bublina nad vyhledáváním, pomlčky v textu → čárka."""
    m = re.search(r"<style>(.*?)</style>", telo, flags=re.S)
    if m:
        styl = m.group(1)
        styl = re.sub(r"0 20px 50px rgba\(20,40,115,\.2[25]\)", "var(--stin-hluboky)", styl)
        styl = styl.replace("text-transform: uppercase; letter-spacing: .05em;", "")
        styl = styl.replace(".nove { background: var(--koral);", ".nove { background: var(--karmin);")
        styl = "\n  /* Hallmark · waive 24b · the tool's own spacing, kept from obsah/nastroje · the owner keeps the tools' current look */\n" + na_skalu(styl)
        telo = telo[:m.start(1)] + styl + telo[m.end(1):]
    if slug in BUBLINY_NASTROJU:
        telo = telo.replace('<div class="stred">', f'<div class="stred">\n    <p class="bublina bublina-dolu">{e(BUBLINY_NASTROJU[slug])}</p>', 1)
    kusy = re.split(r"(<script.*?</script>)", telo, flags=re.S)
    return "".join(k if k.startswith("<script") else k.replace(" – ", ", ").replace(">–<", ">…<") for k in kusy)



# ---------------------------------------------------------------- pomocné funkce
def nacti(soubor: Path):
    """Načte soubor s hlavičkou (--- … ---) a vrátí (hlavička, tělo)."""
    text = soubor.read_text(encoding="utf-8")
    hlav = {}
    m = re.match(r"^---\s*\n(.*?)\n---\s*\n?", text, flags=re.S)
    if m:
        for radek in m.group(1).splitlines():
            if ":" in radek:
                k, v = radek.split(":", 1)
                hlav[k.strip()] = v.strip()
        text = text[m.end():]
    return hlav, text


def ano(hodnota) -> bool:
    return str(hodnota).strip().lower() in {"ano", "yes", "true", "1"}


def otisk_souboru(cesta: Path) -> str:
    """Krátký otisk obsahu souboru (8 znaků). Když soubor chybí, vrátí „0“."""
    try:
        return hashlib.sha256(cesta.read_bytes()).hexdigest()[:8]
    except OSError:
        return "0"


def e(t) -> str:
    return html.escape(str(t), quote=True)


def menu_html(koren, aktivni):
    polozky = "".join(
        f'<li><a href="{koren}{slug}/index.html"{" aria-current=\"page\"" if slug == aktivni else ""}>{e(nazev)}</a></li>'
        for slug, nazev in MENU)
    return (f'<nav aria-label="Hlavní menu"><ul class="menu menu-pc">{polozky}</ul>'
            f'<details class="menu-mobil"><summary>{svg("menu")}Menu</summary><ul class="menu">{polozky}</ul></details></nav>')


def stranka(koren, titulek, aktivni, hero, obsah):
    """Celá stránka: hlavička v kobaltovém pásu, obsah, patička."""
    trida = "pas" + (" pas-uvod" if hero.get("uvod") else "") + (" pas-prekryv" if hero.get("prekryv") else "")
    drobky = f'<p class="drobky">{hero["drobky"]}</p>' if hero.get("drobky") else ""
    logo = (f'<div class="logo-kruh"><img src="{koren}img/logo.png" alt="Logo jsem.spotřebitel – a ty taky!"></div>'
            if hero.get("uvod") else "")
    meta = hero.get("meta", "")
    sekce_odkazy = "".join(f'<li><a href="{koren}{s}/index.html">{e(n)}</a></li>' for s, n in MENU)
    return f"""<!doctype html>
<html lang="cs">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<title>{e(titulek)}</title>
<meta name="description" content="{e(hero.get("popis_meta", hero.get("sub", "")))}">
<link rel="icon" href="{koren}img/favicon.svg" type="image/svg+xml">
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Fraunces:opsz,wght@9..144,500;9..144,700;9..144,900&family=Work+Sans:wght@400;500;600;700&display=swap" rel="stylesheet">
<link rel="stylesheet" href="{koren}assets/styl.css">
</head>
<body>
<header class="{trida}">
  <div class="dekor" aria-hidden="true"></div>
  <div class="obal">
    <div class="lista">
      <a class="wordmark" href="{koren}index.html">jsem <b>spotřebitel</b><span class="tecka">.</span>cz</a>
      {menu_html(koren, aktivni)}
    </div>
    {logo}
    {drobky}
    <h1>{hero["nadpis"]}</h1>
    {f'<p class="sub">{e(hero["sub"])}</p>' if hero.get("sub") else ""}
    {meta}
  </div>
</header>
<main>
{obsah}
</main>
<footer>
  <div class="obal paticka">
    <div class="mini-kruh"><img src="{koren}img/logo.png" alt=""></div>
    <div>
      <a class="wordmark" href="{koren}index.html">jsem <b>spotřebitel</b><span class="tecka">.</span>cz</a>
      <p>Praktické pomůcky a srozumitelné informace pro spotřebitele.</p>
      <p>Nejsme Česká obchodní inspekce ani jiný úřad. Informace na webu nejsou právní rada.</p>
      <p>Instagram, Facebook a podcast doplníme.</p>
    </div>
    <div>
      <h2>Na webu</h2>
      <ul>{sekce_odkazy}</ul>
    </div>
  </div>
</footer>
<script>
(function () {{
  var m = document.querySelector(".menu-mobil");
  if (!m) return;
  document.addEventListener("click", function (e) {{ if (m.open && !m.contains(e.target)) m.open = false; }});
  document.addEventListener("keydown", function (e) {{ if (e.key === "Escape" && m.open) {{ m.open = false; m.querySelector("summary").focus(); }} }});
}})();
</script>
</body>
</html>
"""


def dlazdice(koren, p, sekce_podle_slugu, se_stitkem=True):
    s = sekce_podle_slugu[p["sekce"]]
    stitek = f'<span class="stitek">{svg(s["ikona"])}{e(p.get("stitek") or s["stitek"])}</span>' if se_stitkem else "<span></span>"
    if p["hotovo"]:
        return (f'<a class="polozka" href="{koren}{p["sekce"]}/{p["slug"]}/index.html">'
                f'<div class="hlava">{stitek}</div>'
                f'<h3>{e(p["nazev"])}</h3><p>{e(p["kratky_popis"])}</p>'
                f'<span class="dale">Otevřít {svg("sipka")}</span></a>')
    return (f'<div class="polozka" aria-disabled="true"><div class="hlava">{stitek}'
            f'<span class="stav brzy">Připravujeme</span></div>'
            f'<h3>{e(p["nazev"])}</h3><p>{e(p["kratky_popis"])}</p></div>')


def md_na_html(text):
    import markdown
    vystup = markdown.markdown(text, extensions=["extra", "sane_lists"], output_format="html")
    vystup = vystup.replace('<a href="http', '<a target="_blank" rel="noopener" href="http')
    return vystup.replace("<table>", '<div class="tabulka"><table>').replace("</table>", "</table></div>")


# ---------------------------------------------------------------- stavba
def main():
    slozka = Path(__file__).parent.resolve()
    obsah_dir = slozka / "obsah"
    web = slozka / "web"
    (web / "assets").mkdir(parents=True, exist_ok=True)
    (web / "assets" / "styl.css").write_text(styl_webu(), encoding="utf-8")
    sekce_podle_slugu = {s["slug"]: s for s in SEKCE}
    vytvoreno = []

    # 1) Posbírat položky: plánované + skutečné soubory v obsah/
    polozky = {}
    for sekce, slug, nazev, kratky, poradi in PLANOVANE:
        polozky[(sekce, slug)] = {"sekce": sekce, "slug": slug, "nazev": nazev, "kratky_popis": kratky,
                                  "na_uvod": poradi is not None, "poradi": poradi or 99, "hotovo": False}
    for s in SEKCE:
        d = obsah_dir / s["slug"]
        if not d.exists():
            continue
        for soubor in sorted(list(d.glob("*.md")) + list(d.glob("*.html"))):
            hlav, telo = nacti(soubor)
            klic = (s["slug"], soubor.stem)
            zaklad = polozky.get(klic, {"na_uvod": False, "poradi": 99})
            p = {**zaklad, "sekce": s["slug"], "slug": soubor.stem, "hotovo": True,
                 "nazev": hlav.get("nazev", zaklad.get("nazev", soubor.stem)),
                 "kratky_popis": hlav.get("kratky_popis", zaklad.get("kratky_popis", hlav.get("popis", ""))),
                 "hlav": hlav, "telo": telo, "typ": soubor.suffix}
            if "na_uvod" in hlav:
                p["na_uvod"] = ano(hlav["na_uvod"])
            if "poradi" in hlav:
                p["poradi"] = int(hlav["poradi"])
            polozky[klic] = p
            print(f"  Obsah: {s['slug']}/{soubor.name} → „{p['nazev']}“", flush=True)

    # 2) Stránky jednotlivých položek
    for p in polozky.values():
        if not p["hotovo"]:
            continue
        s = sekce_podle_slugu[p["sekce"]]
        koren = "../../"
        hlav = p["hlav"]
        if p["typ"] == ".html":
            obsah = uprav_nastroj(p["slug"], p["telo"]).replace("{{KOREN}}", koren)
            # Datové soubory dostanou „otisk“ obsahu (?v=…), aby prohlížeč po aktualizaci dat nenačetl starou verzi
            # ze své mezipaměti. Otisk se mění jen tehdy, když se data opravdu změní.
            obsah = re.sub(r'src="([^"?]*?data/([\w-]+\.js))"',
                           lambda m: f'src="{m.group(1)}?v={otisk_souboru(web / "data" / m.group(2))}"', obsah)
        else:
            obsah = f'<div class="obal"><article class="text">{md_na_html(p["telo"])}</article></div>'
        meta = ""
        if p["sekce"] == "clanky":
            dt = date.fromisoformat(hlav["datum"]) if hlav.get("datum") else None
            kusy = [x for x in (hlav.get("rubrika"), f"{dt.day}. {dt.month}. {dt.year}" if dt else None) if x]
            meta = '<div class="meta-clanku">' + "".join(f"<span>{e(k)}</span>" for k in kusy) + "</div>" if kusy else ""
        hero = {"nadpis": hlav.get("nadpis", e(p["nazev"])), "sub": hlav.get("popis", p["kratky_popis"]),
                "drobky": f'<a href="{koren}{s["slug"]}/index.html">{e(s["nazev"])}</a>',
                "prekryv": ano(hlav.get("hero_prekryv", "ne")), "meta": meta}
        cil = web / p["sekce"] / p["slug"] / "index.html"
        cil.parent.mkdir(parents=True, exist_ok=True)
        cil.write_text(stranka(koren, f'{p["nazev"]} | jsem spotřebitel', s["slug"], hero, obsah), encoding="utf-8")
        vytvoreno.append(cil)

    # 3) Stránky sekcí
    for s in SEKCE:
        koren = "../"
        vSekci = sorted((p for p in polozky.values() if p["sekce"] == s["slug"]),
                        key=lambda p: (not p["hotovo"], -int((p.get("hlav", {}).get("datum") or "0").replace("-", "")), p["nazev"]))
        if s["slug"] == "clanky":
            hotove = [p for p in vSekci if p["hotovo"]]
            rubriky = '<div class="rubriky">' + "".join(f"<span>{e(r)}</span>" for r in RUBRIKY) + "</div>"
            if hotove:
                telo = rubriky + '<div class="mrizka">' + "".join(dlazdice(koren, p, sekce_podle_slugu, False) for p in hotove) + "</div>"
            else:
                telo = rubriky + '<div class="prazdno"><b>První články připravujeme</b>Rubriky budou stejné jako na Instagramu.</div>'
        else:
            telo = '<div class="mrizka">' + "".join(dlazdice(koren, p, sekce_podle_slugu, False) for p in vSekci) + "</div>"
        obsah = f'<section class="sekce"><div class="obal">{telo}</div></section>'
        cil = web / s["slug"] / "index.html"
        cil.parent.mkdir(parents=True, exist_ok=True)
        cil.write_text(stranka(koren, f'{s["nazev"]} | jsem spotřebitel', s["slug"], {"nadpis": e(s["nazev"]), "sub": s["popis"]}, obsah), encoding="utf-8")
        vytvoreno.append(cil)

    # 4) Samostatné stránky (O projektu…)
    for soubor in sorted((obsah_dir / "stranky").glob("*.md")):
        hlav, telo = nacti(soubor)
        koren = "../"
        obsah = f'<div class="obal"><article class="text">{md_na_html(telo)}</article></div>'
        cil = web / soubor.stem / "index.html"
        cil.parent.mkdir(parents=True, exist_ok=True)
        cil.write_text(stranka(koren, f'{hlav.get("nazev", soubor.stem)} | jsem spotřebitel', soubor.stem,
                               {"nadpis": e(hlav.get("nazev", soubor.stem)), "sub": hlav.get("popis", "")}, obsah), encoding="utf-8")
        vytvoreno.append(cil)

    # 5) Úvodní stránka = rozcestník: úvod, průvodce „Co právě řešíš?“, brzy přibude, všechno na webu
    stav = {}
    if (web / "data" / "stav-aktualizace.json").exists():
        stav = json.loads((web / "data" / "stav-aktualizace.json").read_text(encoding="utf-8"))
    koren = ""
    situace, panely = [], []
    for i, n in enumerate(PRUVODCE):
        p = polozky.get(("nastroje", n["slug"]))
        if not p or not p["hotovo"]:
            continue
        soubor, klic, popisek = n["data"]
        pocet = pocet_zaznamu(web / "data" / soubor)
        iso = (stav.get(klic) or {}).get("naposledy_uspech", "")[:10]
        kdy = f"{int(iso[8:10])}. {int(iso[5:7])}. {iso[:4]}" if iso else "–"
        zdroj = "Evropská komise (Safety Gate)" if n["slug"] == "nebezpecne-vyrobky" else "Česká obchodní inspekce"
        print(f"  {n['slug']}: {pocet} {popisek} (data k {kdy})", flush=True)
        situace.append(
            f'<a class="situace" role="tab" id="tab-{n["slug"]}" href="#panel-{n["slug"]}" aria-controls="panel-{n["slug"]}" aria-selected="{"true" if i == 0 else "false"}">'
            f'{svg("nastroj")}<b>{e(n["situace"])}</b><span>{e(p["nazev"])}</span></a>')
        panely.append(
            f'<div class="panel" role="tabpanel" id="panel-{n["slug"]}" aria-labelledby="tab-{n["slug"]}">'
            f'<div class="fakt"><span class="cislo">{cislo(pocet)}</span><span class="popisek">{e(popisek)}</span></div>'
            f'<h3>{e(p["nazev"])}</h3><p>{e(p["hlav"].get("popis", p["kratky_popis"]))}</p>'
            f'<dl><dt>Budeš potřebovat</dt><dd>{e(n["potrebujes"])}</dd>'
            f'<dt>Odkud jsou data</dt><dd>{e(zdroj)}, stav k {e(kdy)}</dd>'
            f'<dt>Cena</dt><dd>Zdarma, bez registrace</dd></dl>'
            f'<a class="tlacitko" href="nastroje/{n["slug"]}/index.html">Otevřít {e(p["nazev"])}</a></div>')

    brzy = sorted((p for p in polozky.values() if p["na_uvod"] and not p["hotovo"]), key=lambda p: (p["poradi"], p["nazev"]))
    chystame = '<ul class="chystame">' + "".join(
        f'<li><b>{e(p["nazev"])}</b><span>{e(sekce_podle_slugu[p["sekce"]]["nazev"])}: {e(p["kratky_popis"])}</span></li>' for p in brzy) + "</ul>"
    vse = "".join(
        f'<li><a href="{s["slug"]}/index.html">{svg(s["ikona"])}<b>{e(s["nazev"])}</b><span>{e(s["popis"])}</span></a></li>' for s in SEKCE)

    obsah = f"""
<section class="sekce"><div class="obal proc">
  <h2>Spotřebitel jsi každý den.</h2>
  <p class="uvodni">Kupuješ jídlo, platíš za telefon, objednáváš z e-shopu nebo tankuješ. Pokaždé máš práva, o kterých se moc nemluví. Tady je najdeš srozumitelně, spolu s nástroji, které ti pomůžou se rozhodnout.</p>
    <ul>
      <li><b>Data přímo od úřadů</b><span>Nástroje berou otevřená data České obchodní inspekce a Evropské komise. U každého vidíš, odkud a z kdy jsou.</span></li>
      <li><b>Zdarma a bez registrace</b><span>Nic neplatíš a nikde se nepřihlašuješ.</span></li>
      <li><b>Co hledáš, zůstane u tebe</b><span>Hledání probíhá přímo v tvém prohlížeči, nikam ho neodesíláme.</span></li>
    </ul>
</div></section>
<section class="sekce" id="pruvodce"><div class="obal">
  <div class="sekce-hlava"><h2 class="sekce-nadpis">Co právě řešíš?</h2><p class="sekce-popis">Vyber situaci a ukážeme ti, který nástroj ti pomůže.</p></div>
  <div class="pruvodce">
    <div class="situace-sloupec">
      <p class="bublina bublina-dolu">Klikni na situaci, která sedí na tebe.</p>
      <div class="situace-seznam" role="tablist" aria-label="Situace" aria-orientation="vertical">{"".join(situace)}</div>
    </div>
    <div>{"".join(panely)}</div>
  </div>
</div></section>
<section class="sekce"><div class="obal">
  <div class="sekce-hlava"><h2 class="sekce-nadpis">Brzy přibude</h2><p class="sekce-popis">Na těchhle pomůckách právě pracujeme.</p></div>
  {chystame}
</div></section>
<section class="pas-sekci"><div class="obal">
  <h2>Všechno na webu</h2>
  <ul class="vse-na-webu">{vse}</ul>
</div></section>
{SKRIPT_PRUVODCE}"""
    akce = ('<div class="akce"><a class="tlacitko" href="#pruvodce">Co právě řešíš?</a>'
            '<p class="bublina bublina-vlevo">Nevíš, kde začít? Tady ti ukážeme cestu.</p></div>')
    hero = {"uvod": True, "nadpis": "Jsem spotřebitel. <em>A ty taky.</em>",
            "sub": "Praktické pomůcky, tvoje práva a návody srozumitelně a na jednom místě.", "meta": akce}
    (web / "index.html").write_text(stranka(koren, "jsem spotřebitel", "", hero, obsah), encoding="utf-8")
    vytvoreno.append(web / "index.html")

    # 6) Stránka 404
    obsah = '<section class="sekce"><div class="obal"><div class="prazdno"><b>Tahle stránka tu není</b><a href="index.html">Zpátky na úvod</a></div></div></section>'
    (web / "404.html").write_text(stranka("", "Stránka nenalezena | jsem spotřebitel", "", {"nadpis": "Tady nic není."}, obsah), encoding="utf-8")
    vytvoreno.append(web / "404.html")

    print(f"Vytvořeno stránek: {len(vytvoreno)}", flush=True)
    for c in vytvoreno:
        print(f"  {c.relative_to(web).as_posix()}", flush=True)

    # 7) Kontrola odkazů: každý místní odkaz musí vést na existující soubor
    chyby = 0
    pocet = 0
    for c in vytvoreno:
        text = c.read_text(encoding="utf-8")
        for odkaz in re.findall(r'(?:href|src)="([^"#]+)"', text):
            if re.match(r"^(https?:|mailto:|data:)", odkaz):
                continue
            odkaz = odkaz.split("?", 1)[0]
            pocet += 1
            if not (c.parent / odkaz).resolve().exists():
                chyby += 1
                print(f"  ! Nefunkční odkaz v {c.relative_to(web).as_posix()}: {odkaz}", flush=True)
    print(f"Kontrola odkazů: {pocet} místních odkazů, nefunkčních {chyby}.", flush=True)
    if chyby:
        sys.exit(1)


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
