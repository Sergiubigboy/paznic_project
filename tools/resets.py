"""
tools/resets.py — resetarea setărilor, cu backup înainte
=========================================================
Fiecare reset își salvează întâi fișierele în chronos_data/backups/<reset>-<dată>/,
apoi le readuce la starea inițială. Orice backup se poate restaura.

Doar SETĂRI și stări tehnice — nu datele tale (jurnal, bani, taskuri, poze).
Pentru alea nu există buton de reset, intenționat.

CLI:
    python -m tools.resets list
    python -m tools.resets reset theme
    python -m tools.resets backups
    python -m tools.resets restore theme-20261003-142501
"""

import json
import logging
import os
import shutil
from datetime import datetime

logger = logging.getLogger(__name__)

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.path.join(BASE_DIR, "chronos_data")
BACKUP_DIR = os.path.join(DATA_DIR, "backups")
MAX_BACKUPS = 40


def _reseed_health() -> None:
    from tools import health
    health.seed(force=True, automations=False)   # automatizările șterse rămân șterse


# cheie → (titlu, descriere, fișiere relative la chronos_data, acțiune după ștergere)
# Cele din NEEDS_RESTART țin starea în memoria procesului: fără repornire,
# Chronos ar rescrie fișierul cu starea veche la prima salvare.
RESETS = {
    "theme": ("🎨 Tema", "Culoarea interfeței revine la violetul implicit.",
              ["theme.json"], None),
    "journal_password": ("🔒 Parola jurnalului", "Uiți parola? După reset, o setezi din nou din pagina Jurnal. Intrările rămân.",
                         ["journal_lock.json"], None),
    "health_plan": ("🫀 Programul Health", "Itemele (mese, suplimente, sală), ora recap-ului și ideile de mâncare revin la protocolul inițial. Istoricul de bifări rămâne.",
                    ["health/plan.json", "health/ideas.json"], _reseed_health),
    "health_bot": ("🤖 Starea botului Health", "Uită ce mesaje Telegram a citit și rotația ideilor. Util dacă botul pare blocat.",
                   ["health/state.json"], None),
    "automations": ("🪄 Automatizările", "Șterge toate automatizările și istoricul lor de rulare.",
                    ["automations.json", "automations_state.json"], None),
    "day_bot": ("📅 Starea botului Zi", "Uită ce mesaje a citit botul programului zilei și ce a notificat azi.",
                ["day_runner_state.json"], None),
    "emotions": ("🧠 Starea lui Chronos", "Nervi, bucurie, plictis, afecțiune revin la neutru.",
                 ["emotions.json"], None),
    "music_taste": ("🎧 Gustul muzical", "DJ-ul uită ce ți-a plăcut, ce ai sărit și ce a pus recent.",
                    ["music_taste.json"], None),
    "timers": ("⏰ Timere și alarme", "Anulează toate timerele/alarmele puse din voce.",
               ["timers.json"], None),
    "trusted_devices": ("📱 Dispozitive de încredere", "Uită toate dispozitivele care intrau fără parolă din afara rețelei.",
                        ["trusted_devices.json"], None),
}


NEEDS_RESTART = {"day_bot", "emotions", "music_taste", "timers"}


def list_resets() -> list:
    out = []
    for key, (title, desc, files, _) in RESETS.items():
        present = [f for f in files if os.path.exists(os.path.join(DATA_DIR, f))]
        out.append({"key": key, "title": title, "description": desc,
                    "files": files, "present": bool(present),
                    "needs_restart": key in NEEDS_RESTART})
    return out


def _backup(key: str, files: list) -> str:
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    name = f"{key}-{stamp}"
    dest = os.path.join(BACKUP_DIR, name)
    saved = []
    for rel in files:
        src = os.path.join(DATA_DIR, rel)
        if os.path.exists(src):
            os.makedirs(os.path.dirname(os.path.join(dest, rel)), exist_ok=True)
            shutil.copy2(src, os.path.join(dest, rel))
            saved.append(rel)
    if not saved:
        return ""
    with open(os.path.join(dest, "_info.json"), "w", encoding="utf-8") as f:
        json.dump({"key": key, "files": saved, "created_at": datetime.now().isoformat(timespec="seconds")},
                  f, ensure_ascii=False, indent=2)
    _prune()
    return name


def _prune() -> None:
    """Păstrăm ultimele MAX_BACKUPS — backup-urile sunt mici, dar nu infinite."""
    items = sorted(list_backups(), key=lambda b: b["created_at"])
    for b in items[:-MAX_BACKUPS]:
        shutil.rmtree(os.path.join(BACKUP_DIR, b["name"]), ignore_errors=True)


def reset(key: str) -> dict:
    if key not in RESETS:
        return {"status": "error", "message": "Reset necunoscut."}
    title, _, files, after = RESETS[key]
    backup = _backup(key, files)
    for rel in files:
        try:
            os.remove(os.path.join(DATA_DIR, rel))
        except FileNotFoundError:
            pass
    if after:
        after()
    _notify_running(key)
    logger.info(f"♻️ [Reset] {title} (backup: {backup or 'nimic de salvat'})")
    msg = f"{title} — resetat."
    if backup:
        msg += " Am păstrat o copie, o poți restaura mai jos."
    return {"status": "ok", "message": msg, "backup": backup, "needs_restart": key in NEEDS_RESTART}


def _notify_running(key: str) -> None:
    """Componentele care țin starea în memorie află imediat de reset."""
    try:
        if key == "automations":
            from core.automations import notify_changed
            notify_changed()
    except Exception:
        pass


def list_backups() -> list:
    out = []
    if not os.path.isdir(BACKUP_DIR):
        return out
    for name in os.listdir(BACKUP_DIR):
        info_path = os.path.join(BACKUP_DIR, name, "_info.json")
        try:
            with open(info_path, "r", encoding="utf-8") as f:
                info = json.load(f)
        except Exception:
            continue
        title = RESETS.get(info.get("key"), (info.get("key"),))[0]
        out.append({"name": name, "key": info.get("key"), "title": title,
                    "files": info.get("files", []), "created_at": info.get("created_at", "")})
    return sorted(out, key=lambda b: b["created_at"], reverse=True)


def restore(name: str) -> dict:
    name = os.path.basename(name or "")
    src_dir = os.path.join(BACKUP_DIR, name)
    info_path = os.path.join(src_dir, "_info.json")
    if not name or not os.path.exists(info_path):
        return {"status": "error", "message": "Backup inexistent."}
    with open(info_path, "r", encoding="utf-8") as f:
        info = json.load(f)
    # Starea de acum se salvează și ea — un restore greșit se poate anula.
    _backup(info.get("key", "restore"), info.get("files", []))
    for rel in info.get("files", []):
        dest = os.path.join(DATA_DIR, rel)
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        shutil.copy2(os.path.join(src_dir, rel), dest)
    _notify_running(info.get("key", ""))
    title = RESETS.get(info.get("key"), (info.get("key"),))[0]
    return {"status": "ok", "message": f"{title} — restaurat din {info.get('created_at', name)}.",
            "needs_restart": info.get("key") in NEEDS_RESTART}


def main(argv=None) -> None:
    import argparse
    import sys
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    p = argparse.ArgumentParser(prog="python -m tools.resets")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("list")
    r = sub.add_parser("reset"); r.add_argument("key")
    sub.add_parser("backups")
    rs = sub.add_parser("restore"); rs.add_argument("name")
    a = p.parse_args(argv)
    if a.cmd == "list":
        for x in list_resets():
            print(f"{'●' if x['present'] else '○'} {x['key']:<17} {x['title']} — {x['description']}")
    elif a.cmd == "reset":
        print(reset(a.key)["message"])
    elif a.cmd == "backups":
        for b in list_backups():
            print(f"{b['name']:<40} {b['title']}  {', '.join(b['files'])}")
    elif a.cmd == "restore":
        print(restore(a.name)["message"])


if __name__ == "__main__":
    main()
