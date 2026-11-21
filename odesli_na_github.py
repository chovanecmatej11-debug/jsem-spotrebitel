# /// script
# requires-python = ">=3.12"
# dependencies = []
# ///
"""
Odešle aktuální verzi projektu (skripty, obsah, obrázky, předlohy nasazení) do repozitáře na GitHubu.
GitHub pak web sám postaví a zveřejní a každé ráno aktualizuje data.

Proč takhle: tahle složka se synchronizuje přes Google Drive, a proto v ní nesmí být git.
Skript si proto repozitář pokaždé stáhne do dočasné složky, nakopíruje do něj soubory odsud,
uloží změnu a odešle ji. Dočasnou složku pak smaže.

Co se NIKDY neodesílá: API klíč.txt, složka jsem-spotrebitel-agent, jsem.spotřebitel.png, vygenerovaný web
(kromě obrázků ve web/img), log a soubory s nastavením Claude.

Použití:  uv run "odesli_na_github.py" "uzivatel/repozitar" ["popis změny"]
"""
import os
import shutil
import subprocess
import sys
import tempfile
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


# Co se odesílá (vše ostatní zůstává jen u vás)
SOUBORY = ["postav_web.py", "aktualizuj_vse.py", "odesli_na_github.py", "priprav_logo.py",
           "stahni_data_coi.py", "stahni_kontroly_coi.py", "stahni_benzinky_coi.py", "stahni_nebezpecne_vyrobky.py"]
# CLAUDE.md a .claude/ se neodesílají: obsahují soukromé pracovní poznámky (např. odkaz na soukromý náhled).
SLOZKY = ["obsah", "nasazeni", "web/img"]
# Přenesou se z GitHubu zpět, protože je mění automatická aktualizace
ZE_SERVERU = ["web/data/historie-rizikovych-webu.json", "web/data/stav-aktualizace.json"]
ZAKAZANE = ["API klíč.txt", "jsem-spotrebitel-agent", "jsem.spotřebitel.png", "CLAUDE.md", ".claude"]


def gh_cesta() -> str:
    cesta = shutil.which("gh")
    if cesta:
        return cesta
    for k in Path(os.environ.get("LOCALAPPDATA", "")).glob("Microsoft/WinGet/Packages/GitHub.cli*/bin/gh.exe"):
        return str(k)
    raise RuntimeError("Nenašel jsem program gh (GitHub CLI).")


def spust(prikaz: list[str], cwd: Path | None = None, kontrola: bool = True) -> subprocess.CompletedProcess:
    v = subprocess.run(prikaz, cwd=cwd, capture_output=True, text=True, encoding="utf-8", errors="replace")
    if kontrola and v.returncode != 0:
        raise RuntimeError(f"{' '.join(prikaz[:3])}… selhalo:\n{v.stderr.strip() or v.stdout.strip()}")
    return v


def main(repo: str, popis: str) -> None:
    slozka = Path(__file__).parent.resolve()
    gh = gh_cesta()
    # git použije přihlášení z gh
    spust([gh, "auth", "setup-git"])

    prac = Path(tempfile.mkdtemp(prefix="js-github-"))
    try:
        print(f"Stahuji repozitář {repo}…", flush=True)
        spust([gh, "repo", "clone", repo, str(prac / "repo")])
        repo_dir = prac / "repo"

        # Vyčistit vše kromě gitu a souborů, které spravuje server
        for polozka in repo_dir.iterdir():
            if polozka.name in (".git", ".github", "web"):
                continue
            shutil.rmtree(polozka) if polozka.is_dir() else polozka.unlink()
        if (repo_dir / "web" / "img").exists():
            shutil.rmtree(repo_dir / "web" / "img")

        print("Kopíruji soubory projektu…", flush=True)
        pocet = 0
        for s in SOUBORY:
            if (slozka / s).exists():
                (repo_dir / s).parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(slozka / s, repo_dir / s)
                pocet += 1
        for d in SLOZKY:
            if (slozka / d).exists():
                shutil.copytree(slozka / d, repo_dir / d, dirs_exist_ok=True)
                pocet += sum(1 for p in (slozka / d).rglob("*") if p.is_file())
        shutil.copy2(slozka / "nasazeni" / "gitignore.txt", repo_dir / ".gitignore")
        (repo_dir / ".github" / "workflows").mkdir(parents=True, exist_ok=True)
        shutil.copy2(slozka / "nasazeni" / "aktualizace.yml", repo_dir / ".github" / "workflows" / "aktualizace.yml")
        print(f"  zkopírováno souborů: {pocet}", flush=True)

        # Pojistka: nic zakázaného nesmí být v repozitáři
        for z in ZAKAZANE:
            if (repo_dir / z).exists():
                raise RuntimeError(f"Do repozitáře se dostal zakázaný soubor {z}, nic neodesílám.")

        spust(["git", "add", "-A"], cwd=repo_dir)
        # Při prvním odeslání nahrát i místní historii (od kdy sledujeme které weby), dál ji spravuje server
        for s in ZE_SERVERU:
            if not (repo_dir / s).exists() and (slozka / s).exists():
                (repo_dir / s).parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(slozka / s, repo_dir / s)
                spust(["git", "add", "-f", s], cwd=repo_dir)
        zmeny = spust(["git", "status", "--porcelain"], cwd=repo_dir).stdout.strip().splitlines()
        if not zmeny:
            print("Na GitHubu už je aktuální verze, není co odesílat.", flush=True)
        else:
            print(f"Změněných souborů: {len(zmeny)}", flush=True)
            for r in zmeny[:30]:
                print(f"  {r}", flush=True)
            spust(["git", "-c", "user.name=jsem-spotrebitel", "-c",
                   "user.email=jsem-spotrebitel@users.noreply.github.com", "commit", "-m", popis], cwd=repo_dir)
            spust(["git", "push"], cwd=repo_dir)
            print("Odesláno. GitHub teď web postaví a zveřejní (trvá to pár minut).", flush=True)

        # Historii a stav, které mění server, si stáhneme i sem (kvůli lokálnímu náhledu)
        for s in ZE_SERVERU:
            if (repo_dir / s).exists():
                (slozka / s).parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(repo_dir / s, slozka / s)
    finally:
        shutil.rmtree(prac, ignore_errors=True)


if __name__ == "__main__":
    if sys.platform == "win32":
        try:
            sys.stdout.reconfigure(encoding="utf-8")
        except Exception:
            pass
    slozka = Path(__file__).parent.resolve()
    sys.stdout = TeeOutput(slozka / "beh_skriptu.log")
    if len(sys.argv) < 2:
        print("Chybí název repozitáře (uzivatel/repozitar).", flush=True)
        sys.exit(1)
    print("Spouštím odeslání na GitHub...", flush=True)
    try:
        main(sys.argv[1], sys.argv[2] if len(sys.argv) > 2 else "Aktualizace webu")
    except RuntimeError as chyba:
        print(f"CHYBA: {chyba}", flush=True)
        sys.exit(1)
    print("Hotovo.", flush=True)
