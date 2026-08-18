"""
tools/data_write_tools.py — Scriere Directă în chronos_data
=============================================================
Tool-uri prin care Chronos ADAUGĂ date, nu doar le citește.

Principiu de performanță: ZERO apeluri LLM aici. Modelul vocal a extras deja
argumentele structurate prin function calling (sumă, titlu, kg etc.), deci nu
mai are rost un al doilea LLM care „interpretează" — scriem direct în JSON.

Toate formatele respectă EXACT schema pe care o citește dashboard-ul web
(id-uri, timestamp-uri, chei), ca datele adăugate pe voce să apară imediat
în interfață.
"""

import json
import logging
import os
from datetime import datetime, date
from difflib import SequenceMatcher
from typing import Optional

logger = logging.getLogger(__name__)

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.path.join(BASE_DIR, "chronos_data")
FINANCE_DIR = os.path.join(DATA_DIR, "finance")
GYM_DIR = os.path.join(DATA_DIR, "gym")


# ─────────────────────────────────────────────────────────────
# HELPERS
# ─────────────────────────────────────────────────────────────

def _load(path: str, default):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        return default
    except Exception as e:
        logger.warning(f"⚠️ [Write Tools] Nu pot citi {os.path.basename(path)}: {e}")
        return default


def _save(path: str, data) -> bool:
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=4)
        return True
    except Exception as e:
        logger.error(f"❌ [Write Tools] Nu pot salva {os.path.basename(path)}: {e}")
        return False


def _new_id(prefix: str) -> str:
    return f"{prefix}_{int(datetime.now().timestamp() * 1000)}"


def _similar(a: str, b: str) -> float:
    return SequenceMatcher(None, (a or "").lower(), (b or "").lower()).ratio()


def _score(query: str, name: str) -> float:
    """Cât de bine se potrivește `name` cu ce a zis Sergiu."""
    q, n = (query or "").lower().strip(), (name or "").lower().strip()
    if not q or not n:
        return 0.0
    if n in q or q in n:
        return 1.0
    # Suprapunere de cuvinte — mai robustă decât similaritatea pe caractere
    # pentru fraze ca „am curățat praful de pe imprimantă".
    qw, nw = set(q.split()), set(n.split())
    overlap = len(qw & nw) / len(nw) if nw else 0.0
    return max(_similar(q, n), overlap)


def _best_match_scored(query: str, items: list, key: str):
    """Întoarce (element, scor) — scorul permite comparații între surse diferite."""
    if not query or not items:
        return None, 0.0
    best, best_score = None, 0.0
    for it in items:
        s = _score(query, str(it.get(key, "")))
        if s > best_score:
            best, best_score = it, s
    return best, best_score


def _best_match(query: str, items: list, key: str, threshold: float = 0.5):
    """Găsește elementul cu numele cel mai apropiat (voce → text imperfect)."""
    best, score = _best_match_scored(query, items, key)
    return best if score >= threshold else None


# ─────────────────────────────────────────────────────────────
# FINANȚE
# ─────────────────────────────────────────────────────────────

def add_transaction(amount: float, kind: str = "out", note: str = "",
                    account: str = "") -> dict:
    """
    Adaugă o tranzacție. kind: 'in' (încasare) sau 'out' (cheltuială).
    account: numele contului (fuzzy). Implicit primul cont ('Cash').
    """
    try:
        amount = abs(float(amount))
    except (TypeError, ValueError):
        return {"status": "error", "message": "Sumă invalidă."}
    if amount <= 0:
        return {"status": "error", "message": "Suma trebuie să fie mai mare ca zero."}

    kind = "in" if str(kind).lower().startswith("in") else "out"

    accounts_path = os.path.join(FINANCE_DIR, "accounts.json")
    accounts = _load(accounts_path, [])
    if not accounts:
        return {"status": "error", "message": "Nu există niciun cont configurat."}

    acc = _best_match(account, accounts, "name") if account else None
    if acc is None:
        acc = accounts[0]

    tx_path = os.path.join(FINANCE_DIR, "transactions.json")
    transactions = _load(tx_path, [])
    entry = {
        "id": _new_id("tx"),
        "account_id": acc["id"],
        "amount": round(amount, 2),
        "type": kind,
        "note": (note or "").strip(),
        "date": date.today().isoformat(),
        "created_at": datetime.now().isoformat(),
    }
    transactions.append(entry)
    if not _save(tx_path, transactions):
        return {"status": "error", "message": "Nu am putut salva tranzacția."}

    semn = "+" if kind == "in" else "-"
    logger.info(f"💰 [Write Tools] Tranzacție {semn}{amount} RON [{acc['name']}] {note}")
    return {
        "status": "ok",
        "message": f"Am notat {semn}{amount:g} RON pe contul {acc['name']}"
                   + (f" ({note})" if note else "") + ".",
        "cont": acc["name"],
    }


# ─────────────────────────────────────────────────────────────
# REMINDERE
# ─────────────────────────────────────────────────────────────

_REMINDERS_PATH = os.path.join(DATA_DIR, "reminders.json")


def add_reminder(title: str, priority: str = "Med", description: str = "") -> dict:
    title = (title or "").strip()
    if not title:
        return {"status": "error", "message": "Reminderul are nevoie de un titlu."}

    priority = priority if priority in ("Low", "Med", "High") else "Med"
    data = _load(_REMINDERS_PATH, {"reminders": []})
    data.setdefault("reminders", []).append({
        "id": _new_id("rem"),
        "title": title,
        "description": (description or "").strip(),
        "emoji": "📌",
        "priority": priority,
        "checked": False,
        "last_checked": None,
        "created_at": datetime.now().isoformat(),
    })
    if not _save(_REMINDERS_PATH, data):
        return {"status": "error", "message": "Nu am putut salva reminderul."}

    logger.info(f"📌 [Write Tools] Reminder nou: {title}")
    return {"status": "ok", "message": f"Am adăugat: {title}."}


_MAINTENANCE_PATH = os.path.join(DATA_DIR, "maintenance.json")


def _find_maintenance(title: str):
    """Cel mai potrivit task de mentenanță + scorul lui."""
    data = _load(_MAINTENANCE_PATH, {"items": []})
    best, best_item, best_score = None, None, 0.0
    for item in data.get("items", []):
        for task in item.get("tasks", []):
            # Potrivim pe numele task-ului, al aparatului, sau pe combinație —
            # „am curățat imprimanta" trebuie să prindă „Curatat Praful".
            score = max(
                _score(title, task.get("name", "")),
                _score(title, item.get("name", "")),
                _score(title, f"{task.get('name', '')} {item.get('name', '')}"),
            )
            if score > best_score:
                best, best_item, best_score = task, item, score
    return data, best, best_item, best_score


def complete_reminder(title: str) -> dict:
    """
    Bifează un reminder SAU un task de mentenanță — „am curățat imprimanta" e
    mentenanță, nu reminder. Comparăm scorurile din AMBELE surse și alegem
    câștigătorul, ca să nu bifăm din greșeală altceva doar pentru că a fost
    verificat primul.
    """
    rem_data = _load(_REMINDERS_PATH, {"reminders": []})
    pending = [r for r in rem_data.get("reminders", []) if not r.get("checked")]
    rem_match, rem_score = _best_match_scored(title, pending, "title")

    mnt_data, mnt_task, mnt_item, mnt_score = _find_maintenance(title)

    PRAG = 0.5
    if max(rem_score, mnt_score) < PRAG:
        disponibile = ", ".join(r.get("title", "?") for r in pending) or "niciunul"
        mnt_names = ", ".join(
            f"{t.get('name')} ({i.get('name')})"
            for i in mnt_data.get("items", []) for t in i.get("tasks", [])
        ) or "niciuna"
        return {"status": "error",
                "message": f"N-am găsit '{title}'. Remindere active: {disponibile}. "
                           f"Mentenanță: {mnt_names}."}

    # Mentenanța câștigă la egalitate — e mai specifică decât un reminder generic
    if mnt_score >= rem_score:
        mnt_task["last_done"] = date.today().isoformat()
        if not _save(_MAINTENANCE_PATH, mnt_data):
            return {"status": "error", "message": "Nu am putut salva mentenanța."}
        interval = mnt_task.get("interval_days")
        urmatoarea = f" Următoarea peste {interval} zile." if interval else ""
        logger.info(f"🔧 [Write Tools] Mentenanță: {mnt_item.get('name')} / {mnt_task.get('name')}")
        return {"status": "ok",
                "message": f"Am marcat '{mnt_task.get('name')}' la "
                           f"{mnt_item.get('name')} ca făcut azi.{urmatoarea}"}

    rem_match["checked"] = True
    rem_match["last_checked"] = datetime.now().isoformat()
    if not _save(_REMINDERS_PATH, rem_data):
        return {"status": "error", "message": "Nu am putut salva."}
    logger.info(f"✅ [Write Tools] Reminder bifat: {rem_match['title']}")
    return {"status": "ok", "message": f"Am bifat: {rem_match['title']}."}


# ─────────────────────────────────────────────────────────────
# SPORT
# ─────────────────────────────────────────────────────────────

def log_weight(kg: float, note: str = "") -> dict:
    try:
        kg = float(kg)
    except (TypeError, ValueError):
        return {"status": "error", "message": "Greutate invalidă."}
    if not (30 <= kg <= 300):
        return {"status": "error", "message": f"{kg} kg nu pare o valoare reală."}

    path = os.path.join(GYM_DIR, "weight_log.json")
    entries = _load(path, [])
    if not isinstance(entries, list):
        entries = []

    today = date.today().isoformat()
    existing = next((e for e in entries if e.get("date") == today), None)
    if existing:
        previous = existing.get("weight")
        existing["weight"] = kg
        existing["note"] = (note or existing.get("note", "")).strip()
        existing["logged_at"] = datetime.now().isoformat()
        msg = f"Am actualizat greutatea de azi: {kg} kg (era {previous})."
    else:
        entries.append({
            "date": today, "weight": kg,
            "note": (note or "").strip(),
            "logged_at": datetime.now().isoformat(),
        })
        msg = f"Am notat {kg} kg."

    entries.sort(key=lambda e: e.get("date", ""))
    if not _save(path, entries):
        return {"status": "error", "message": "Nu am putut salva greutatea."}

    # Comparație cu ținta, dacă există
    profile = _load(os.path.join(GYM_DIR, "profile.json"), {})
    goal = profile.get("goal_weight")
    if goal:
        diff = round(kg - float(goal), 1)
        if abs(diff) >= 0.1:
            msg += f" Față de țintă ({goal} kg): {'+' if diff > 0 else ''}{diff} kg."

    logger.info(f"⚖️ [Write Tools] Greutate: {kg} kg")
    return {"status": "ok", "message": msg}


# ─────────────────────────────────────────────────────────────
# PROIECTE (electronică / robotică)
# ─────────────────────────────────────────────────────────────

_ELECTRONICS_PATH = os.path.join(DATA_DIR, "electronics_data.json")


def _walk_steps(steps: list):
    for s in steps or []:
        yield s
        yield from _walk_steps(s.get("children", []))


def complete_project_step(step: str, project: str = "") -> dict:
    """Bifează un pas din planul unui proiect (căutare aproximativă)."""
    data = _load(_ELECTRONICS_PATH, {})
    projects = data.get("projects", [])
    if not projects:
        return {"status": "error", "message": "Niciun proiect înregistrat."}

    proj = _best_match(project, projects, "name") if project else None
    candidates = [proj] if proj else projects

    for p in candidates:
        pending = [s for s in _walk_steps(p.get("plan", [])) if s.get("status") != "done"]
        match = _best_match(step, pending, "title")
        if match:
            match["status"] = "done"
            if not _save(_ELECTRONICS_PATH, data):
                return {"status": "error", "message": "Nu am putut salva."}
            logger.info(f"✅ [Write Tools] Pas bifat: {p.get('name')} / {match['title']}")
            return {"status": "ok",
                    "message": f"Am bifat '{match['title']}' la {p.get('name')}."}

    return {"status": "error", "message": f"N-am găsit pasul '{step}'."}


def add_devlog(title: str, text: str = "", project: str = "") -> dict:
    """Adaugă o intrare în devlog-ul unui proiect."""
    title = (title or "").strip()
    if not title:
        return {"status": "error", "message": "Devlogul are nevoie de un titlu."}

    data = _load(_ELECTRONICS_PATH, {})
    projects = data.get("projects", [])
    if not projects:
        return {"status": "error", "message": "Niciun proiect înregistrat."}

    proj = _best_match(project, projects, "name") if project else None
    if proj is None:
        # Fără proiect specificat → cel mai recent actualizat
        proj = max(projects, key=lambda p: p.get("updated_at") or p.get("created_at") or "")

    proj.setdefault("devlog", []).append({
        "id": _new_id("dlog"),
        "date": date.today().isoformat(),
        "title": title,
        "text": (text or "").strip(),
        "created_at": datetime.now().isoformat(),
    })
    proj["updated_at"] = datetime.now().isoformat()
    if not _save(_ELECTRONICS_PATH, data):
        return {"status": "error", "message": "Nu am putut salva devlogul."}

    logger.info(f"📓 [Write Tools] Devlog la {proj.get('name')}: {title}")
    return {"status": "ok", "message": f"Am notat în devlogul '{proj.get('name')}': {title}."}


# ─────────────────────────────────────────────────────────────
# TARGETURI
# ─────────────────────────────────────────────────────────────

_TARGETS_PATH = os.path.join(DATA_DIR, "targets.json")


def add_target(title: str, description: str = "", deadline: str = "",
               priority: str = "Med", category: str = "Personal") -> dict:
    title = (title or "").strip()
    if not title:
        return {"status": "error", "message": "Targetul are nevoie de un titlu."}

    data = _load(_TARGETS_PATH, {"goals": []})
    data.setdefault("goals", []).append({
        "id": str(int(datetime.now().timestamp() * 1000)),
        "title": title,
        "description": (description or "").strip(),
        "deadline": (deadline or "").strip(),
        "priority": priority if priority in ("Low", "Med", "High") else "Med",
        "category": category or "Personal",
        "progress": 0,
        "created_at": datetime.now().isoformat(),
    })
    if not _save(_TARGETS_PATH, data):
        return {"status": "error", "message": "Nu am putut salva targetul."}

    logger.info(f"🎯 [Write Tools] Target nou: {title}")
    return {"status": "ok", "message": f"Am adăugat targetul: {title}."}


def update_target_progress(title: str, progress: int) -> dict:
    """Setează progresul (0-100) unui target găsit după nume."""
    try:
        progress = max(0, min(100, int(progress)))
    except (TypeError, ValueError):
        return {"status": "error", "message": "Progres invalid."}

    data = _load(_TARGETS_PATH, {"goals": []})
    match = _best_match(title, data.get("goals", []), "title")
    if not match:
        return {"status": "error", "message": f"N-am găsit targetul '{title}'."}

    old = match.get("progress", 0)
    match["progress"] = progress
    if not _save(_TARGETS_PATH, data):
        return {"status": "error", "message": "Nu am putut salva."}

    logger.info(f"🎯 [Write Tools] Progres '{match['title']}': {old}% → {progress}%")
    return {"status": "ok",
            "message": f"'{match['title']}' e acum la {progress}% (era {old}%)."}


# ─────────────────────────────────────────────────────────────
# OBICEIURI
# ─────────────────────────────────────────────────────────────

_DAILY_TASKS_PATH = os.path.join(DATA_DIR, "daily_tasks.json")


def check_habit(name: str) -> dict:
    """Bifează un obicei zilnic pentru ziua de azi."""
    data = _load(_DAILY_TASKS_PATH, {"tasks": [], "checks": {}})
    tasks = data.get("tasks", [])
    match = _best_match(name, tasks, "name")
    if not match:
        disponibile = ", ".join(t.get("name", "?") for t in tasks) or "niciunul"
        return {"status": "error",
                "message": f"N-am găsit obiceiul '{name}'. Definite: {disponibile}."}

    today = date.today().isoformat()
    checks = data.setdefault("checks", {})
    today_list = checks.setdefault(today, [])
    if match["id"] in today_list:
        return {"status": "ok", "message": f"'{match['name']}' era deja bifat azi."}

    today_list.append(match["id"])
    if not _save(_DAILY_TASKS_PATH, data):
        return {"status": "error", "message": "Nu am putut salva."}

    logger.info(f"✅ [Write Tools] Obicei bifat: {match['name']}")
    return {"status": "ok", "message": f"Am bifat '{match['name']}' pe azi."}


# ─────────────────────────────────────────────────────────────
# QUICK CAPTURE — o singură ușă de intrare
# ─────────────────────────────────────────────────────────────

def quick_capture(text: str, kind: str = "nota") -> dict:
    """
    „Notează-mi X" fără să te gândești unde se duce.
    kind e ales de model (jurnal / target / reminder / nota) — deci nu
    mai avem nevoie de un LLM separat care să clasifice.
    """
    text = (text or "").strip()
    if not text:
        return {"status": "error", "message": "N-ai zis ce să notez."}

    kind = (kind or "nota").lower().strip()

    if kind == "target":
        return add_target(text)
    if kind == "reminder":
        return add_reminder(text)
    if kind in ("jurnal", "journal", "nota", "note"):
        try:
            from tools.memory_tools import save_journal_text
            res = save_journal_text(text)
            if res.get("status") == "ok":
                return {"status": "ok", "message": "Am notat în jurnal."}
            return {"status": "error", "message": res.get("message", "Eroare la salvare.")}
        except Exception as e:
            return {"status": "error", "message": str(e)}

    return {"status": "error", "message": f"Nu știu unde să pun '{kind}'."}
