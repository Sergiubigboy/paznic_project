"""
tools/health.py — Health: programul zilnic (mese, suplimente, sală) + remindere
================================================================================
Ideea: nu bifezi nimic. Primești remindere pe botul Telegram „Health" la ora
fiecărui lucru, iar seara un singur recap. Dacă nu răspunzi, ziua se închide
dimineața cu totul bifat. Notezi doar EXCEPȚIILE („n-am mers la sală", „fără
creatină") sau vacanța.

Un item are o oră pentru zilele de școală (L-V) și una pentru weekend; oricare
poate lipsi. Itemele cu aceeași oră pleacă într-un singur mesaj.

Stări în jurnal:
    done    — confirmat explicit (din pagină sau „am luat X")
    missed  — „n-am făcut"
    auto    — n-ai zis nimic până la închiderea zilei → considerat făcut
    (lipsă) — încă deschis / urmează

Fișiere (chronos_data/health/):
    plan.json   — itemele + ora recap-ului + vacanța
    ideas.json  — idei de mâncare pe grupuri, cu kcal
    log.json    — ce s-a întâmplat în fiecare zi
    state.json  — offset Telegram + rotația ideilor

CLI (testabil înainte să fie expus lui Chronos):
    python -m tools.health seed                 # programul din protocol + idei
    python -m tools.health today [--date 2026-10-05]
    python -m tools.health next
    python -m tools.health msg masa1 [--send]   # mesajul unui reminder
    python -m tools.health recap [--send]
    python -m tools.health reply "n-am mers la sala"
    python -m tools.health close                # închide zilele trecute (→ auto)
    python -m tools.health week                 # consecvența pe ultimele zile
"""

import json
import logging
import os
import re
import threading
import unicodedata
from datetime import date, datetime, timedelta
from typing import Optional

logger = logging.getLogger(__name__)

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.path.join(BASE_DIR, "chronos_data", "health")

KINDS = ("meal", "supplement", "gym", "routine")
KIND_ICON = {"meal": "🍽️", "supplement": "💊", "gym": "🏋️", "routine": "💧"}
KIND_LABEL = {"meal": "Mese", "supplement": "Suplimente", "gym": "Sală", "routine": "Rutină"}
STATUSES = ("done", "missed", "auto")
# Ziua de ieri rămâne „deschisă" până dimineața: un răspuns la recap dat după
# miezul nopții se aplică zilei pe care o recapitula.
DAY_ROLLOVER_HOUR = 5
MAX_ITEMS = 40

_lock = threading.RLock()


# =============================================================================
# PERSISTENȚĂ
# =============================================================================

def _path(name: str) -> str:
    return os.path.join(DATA_DIR, name)


def _read(name: str, default):
    with _lock:
        try:
            with open(_path(name), "r", encoding="utf-8") as f:
                return json.load(f)
        except FileNotFoundError:
            return default
        except Exception as e:
            logger.warning(f"⚠️ [Health] {name} ilizibil: {e}")
            return default


def _write(name: str, data) -> None:
    with _lock:
        os.makedirs(DATA_DIR, exist_ok=True)
        tmp = _path(name) + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        os.replace(tmp, _path(name))


def load_plan() -> dict:
    p = _read("plan.json", {})
    p.setdefault("items", [])
    p.setdefault("recap_time", "21:45")
    p.setdefault("vacation", None)
    return p


def save_plan(plan: dict) -> None:
    _write("plan.json", plan)


def load_ideas() -> dict:
    return _read("ideas.json", {})


def save_ideas(ideas: dict) -> None:
    _write("ideas.json", ideas)


def load_log() -> dict:
    return _read("log.json", {})


def save_log(log: dict) -> None:
    _write("log.json", log)


def load_state() -> dict:
    return _read("state.json", {})


def save_state(st: dict) -> None:
    _write("state.json", st)


# =============================================================================
# VALIDARE (pentru pagina web)
# =============================================================================

def _hhmm(v) -> Optional[str]:
    try:
        h, m = str(v).strip().split(":")[:2]
        h, m = int(h), int(m)
    except (ValueError, AttributeError):
        return None
    return f"{h:02d}:{m:02d}" if 0 <= h <= 23 and 0 <= m <= 59 else None


def _slug(text: str) -> str:
    s = _norm(text).replace(" ", "_")
    return re.sub(r"[^a-z0-9_]", "", s)[:24] or "item"


def normalize_item(raw: dict, existing_ids=()) -> tuple:
    raw = raw if isinstance(raw, dict) else {}
    title = str(raw.get("title") or "").strip()[:60]
    if not title:
        return None, "Dă-i un nume."
    kind = raw.get("kind") if raw.get("kind") in KINDS else "routine"
    times = raw.get("times") or {}
    wd, we = _hhmm(times.get("weekday")), _hhmm(times.get("weekend"))
    if not wd and not we:
        return None, "Pune cel puțin o oră (L-V sau weekend)."
    iid = str(raw.get("id") or "").strip()
    if not iid:
        base, n, iid = _slug(title), 2, _slug(title)
        while iid in existing_ids:
            iid, n = f"{base}_{n}", n + 1
    kw = raw.get("keywords") or []
    if isinstance(kw, str):
        kw = kw.split(",")
    return {
        "id": iid,
        "kind": kind,
        "title": title,
        "details": str(raw.get("details") or "").strip()[:400],
        "times": {"weekday": wd, "weekend": we},
        "ideas": str(raw.get("ideas") or "").strip() or None,
        "keywords": [k.strip() for k in kw if str(k).strip()][:12],
        "enabled": bool(raw.get("enabled", True)),
    }, None


def save_item(raw: dict) -> dict:
    plan = load_plan()
    ids = {i["id"] for i in plan["items"]}
    item, err = normalize_item(raw, ids)
    if err:
        return {"status": "error", "message": err}
    for i, it in enumerate(plan["items"]):
        if it["id"] == item["id"]:
            plan["items"][i] = item
            break
    else:
        if len(plan["items"]) >= MAX_ITEMS:
            return {"status": "error", "message": f"Maxim {MAX_ITEMS} iteme."}
        plan["items"].append(item)
    save_plan(plan)
    return {"status": "ok", "item": item}


def delete_item(item_id: str) -> dict:
    plan = load_plan()
    rest = [i for i in plan["items"] if i["id"] != item_id]
    if len(rest) == len(plan["items"]):
        return {"status": "error", "message": "Nu există."}
    plan["items"] = rest
    save_plan(plan)
    return {"status": "ok"}


def set_recap_time(value: str) -> dict:
    t = _hhmm(value)
    if not t:
        return {"status": "error", "message": "Oră invalidă."}
    plan = load_plan()
    plan["recap_time"] = t
    save_plan(plan)
    return {"status": "ok", "recap_time": t}


def set_ideas(raw: dict) -> dict:
    """Înlocuiește toate ideile. raw: {grup: [{text, kcal}]}"""
    out = {}
    for group, items in (raw or {}).items():
        g = _slug(group)
        lst = []
        for it in (items or [])[:40]:
            text = str((it or {}).get("text") or "").strip()[:160]
            if not text:
                continue
            try:
                kcal = max(0, min(3000, int((it or {}).get("kcal") or 0)))
            except (TypeError, ValueError):
                kcal = 0
            lst.append({"text": text, "kcal": kcal})
        if g:
            out[g] = lst
    save_ideas(out)
    return {"status": "ok", "ideas": out}


# =============================================================================
# VACANȚĂ
# =============================================================================

def is_vacation(d: date, plan: Optional[dict] = None) -> bool:
    v = (plan or load_plan()).get("vacation")
    if not v or not v.get("from"):
        return False
    start = date.fromisoformat(v["from"])
    end = date.fromisoformat(v["to"]) if v.get("to") else None
    return start <= d and (end is None or d <= end)


def set_vacation(start: Optional[date], end: Optional[date] = None) -> dict:
    """start=None încheie vacanța. end=None = până zici că te-ai întors."""
    plan = load_plan()
    if start is None:
        plan["vacation"] = None
        save_plan(plan)
        return {"status": "ok", "message": "Bine ai revenit — reîncep reminderele."}
    plan["vacation"] = {"from": start.isoformat(), "to": end.isoformat() if end else None}
    save_plan(plan)
    cat = f"până pe {end:%d.%m}" if end else "până scrii „înapoi”"
    return {"status": "ok", "message": f"🏖️ Vacanță {cat}. Nu-ți trimit nimic și nu contează la consecvență."}


# =============================================================================
# PROGRAMUL ZILEI
# =============================================================================

def is_weekend(d: date) -> bool:
    return d.weekday() >= 5


def item_time(item: dict, d: date) -> Optional[str]:
    if not item.get("enabled", True):
        return None
    return (item.get("times") or {}).get("weekend" if is_weekend(d) else "weekday")


def day_items(d: date, plan: Optional[dict] = None) -> list:
    """[(HH:MM, item)] pentru ziua dată, în ordine. Gol în vacanță."""
    plan = plan or load_plan()
    if is_vacation(d, plan):
        return []
    out = [(item_time(it, d), it) for it in plan["items"]]
    return sorted([x for x in out if x[0]], key=lambda x: (x[0], x[1]["title"]))


def _at(d: date, hhmm: str) -> datetime:
    h, m = hhmm.split(":")
    return datetime.combine(d, datetime.min.time()).replace(hour=int(h), minute=int(m))


def logical_day(now: datetime) -> date:
    """Ziua la care se referă un răspuns: înainte de 05:00 e încă „ieri"."""
    return (now - timedelta(hours=DAY_ROLLOVER_HOUR)).date()


def status_of(d: date, item_id: str, log: Optional[dict] = None) -> Optional[str]:
    return ((log or load_log()).get(d.isoformat()) or {}).get("items", {}).get(item_id)


def due_slots(now: datetime, plan: Optional[dict] = None) -> list:
    """Orele (cu itemele lor) de azi, în ordine: [(datetime, [items])]."""
    plan = plan or load_plan()
    slots = {}
    for hhmm, it in day_items(now.date(), plan):
        slots.setdefault(hhmm, []).append(it)
    return [(_at(now.date(), h), its) for h, its in sorted(slots.items())]


def next_event(now: datetime, plan: Optional[dict] = None) -> Optional[tuple]:
    """(când, tip, payload) — următorul reminder sau recap, în următoarele 8 zile."""
    plan = plan or load_plan()
    for off in range(0, 8):
        d = now.date() + timedelta(days=off)
        evs = [(_at(d, h), "slot", its) for h, its in
               _group_by_time(day_items(d, plan))]
        if day_items(d, plan):
            evs.append((_at(d, plan["recap_time"]), "recap", d))
        for ev in sorted(evs, key=lambda e: e[0]):
            if ev[0] > now:
                return ev
    return None


def _group_by_time(items: list) -> list:
    out = {}
    for h, it in items:
        out.setdefault(h, []).append(it)
    return sorted(out.items())


# =============================================================================
# MESAJE
# =============================================================================

def _pick_ideas(group: str, n: int = 2) -> list:
    """Ideile se rotesc: de fiecare dată următoarele n din listă."""
    ideas = load_ideas().get(group) or []
    if not ideas:
        return []
    st = load_state()
    idx = st.setdefault("ideas_idx", {}).get(group, 0) % len(ideas)
    picked = [ideas[(idx + k) % len(ideas)] for k in range(min(n, len(ideas)))]
    st["ideas_idx"][group] = (idx + len(picked)) % len(ideas)
    save_state(st)
    return picked


def build_message(items: list, hhmm: str, rotate: bool = True) -> str:
    parts = []
    for it in items:
        lines = [f"{KIND_ICON.get(it['kind'], '•')} *{it['title']}*"]
        if it.get("details"):
            lines.append(it["details"])
        if it.get("ideas"):
            ideas = _pick_ideas(it["ideas"]) if rotate else (load_ideas().get(it["ideas"]) or [])[:2]
            for idea in ideas:
                kcal = f" (~{idea['kcal']} kcal)" if idea.get("kcal") else ""
                lines.append(f"💡 {idea['text']}{kcal}")
        parts.append("\n".join(lines))
    return f"⏰ {hhmm}\n\n" + "\n\n".join(parts)


def build_recap(d: date) -> str:
    plan, log = load_plan(), load_log()
    items = day_items(d, plan)
    if not items:
        return "Azi n-a fost nimic programat."
    now = datetime.now()
    trecute, urmeaza = [], []
    for hhmm, it in items:
        st = status_of(d, it["id"], log)
        if d == now.date() and _at(d, hhmm) > now:
            urmeaza.append(f"{hhmm} {it['title']}")
            continue
        mark = "✗" if st == "missed" else "✓"
        trecute.append(f"{mark} {it['title']}")
    text = "🌙 Recap azi:\n" + "\n".join(trecute)
    if urmeaza:
        text += "\n\nMai urmează: " + ", ".join(urmeaza)
    text += ("\n\nRăspunde doar dacă ceva N-A fost (ex: „fără sală”, „n-am luat fierul”)."
             "\nDacă nu zici nimic, rămâne tot bifat. 👍")
    return text


def today_text(d: date) -> str:
    plan, log = load_plan(), load_log()
    if is_vacation(d, plan):
        return "🏖️ Ești în vacanță — nimic programat."
    items = day_items(d, plan)
    if not items:
        return "Nimic programat."
    sym = {"done": "✓", "auto": "✓", "missed": "✗"}
    return "\n".join(f"{sym.get(status_of(d, it['id'], log), '·')} {h}  {it['title']}"
                     for h, it in items)


# =============================================================================
# JURNAL
# =============================================================================

def set_status(d: date, item_id: str, status: Optional[str]) -> None:
    with _lock:
        log = load_log()
        day = log.setdefault(d.isoformat(), {})
        items = day.setdefault("items", {})
        if status in STATUSES:
            items[item_id] = status
        else:
            items.pop(item_id, None)
        save_log(log)


def mark_sent(d: date, key: str) -> bool:
    """True dacă `key` (ora sau „recap") NU fusese trimis încă azi — și îl marchează."""
    with _lock:
        log = load_log()
        day = log.setdefault(d.isoformat(), {})
        sent = day.setdefault("sent", [])
        if key in sent:
            return False
        sent.append(key)
        save_log(log)
        return True


def close_days(now: datetime, back: int = 14) -> list:
    """Închide zilele trecute: ce n-a fost marcat devine `auto` (făcut).
    Doar zilele în care Chronos chiar a trimis ceva — o zi cu Pi-ul oprit nu
    devine „perfectă" din oficiu."""
    closed = []
    plan = load_plan()
    with _lock:
        log = load_log()
        last_open = logical_day(now)
        for off in range(1, back + 1):
            d = last_open - timedelta(days=off)
            day = log.get(d.isoformat())
            if not day or day.get("closed") or not day.get("sent"):
                continue
            items = day.setdefault("items", {})
            for _, it in day_items(d, plan):
                items.setdefault(it["id"], "auto")
            day["closed"] = True
            closed.append(d)
        if closed:
            save_log(log)
    return closed


# =============================================================================
# RĂSPUNSURI („n-am mers la sală", „vacanță până duminică"...)
# =============================================================================

_FARA_DIACRITICE = str.maketrans("ăâîșşțţ", "aaisstt")


def _norm(text: str) -> str:
    t = unicodedata.normalize("NFC", (text or "").lower()).translate(_FARA_DIACRITICE)
    t = re.sub(r"[^a-z0-9: ]+", " ", t.replace("-", " "))
    return " ".join(t.split())


_NEG = ("n am", "nam", "nu am", "nu", "fara", "sarit", "am sarit", "ratat", "uitat", "am uitat",
        "nu s", "n a", "nici")
_POS_ALL = ("gata", "ok", "okay", "da", "tot", "totul", "toate", "bifat", "perfect", "am facut tot",
            "le am facut", "facut")
_POS = ("am luat", "am facut", "am mancat", "am mers", "am baut", "am fost", "luat", "facut",
        "da", "gata", "ok")
_ZILE = {"luni": 0, "marti": 1, "miercuri": 2, "joi": 3, "vineri": 4, "sambata": 5, "duminica": 6}


def _keywords(item: dict) -> list:
    kws = [_norm(k) for k in item.get("keywords") or []]
    kws += [w for w in _norm(item["title"]).split() if len(w) >= 4]
    return [k for k in dict.fromkeys(kws) if k]


def _has(text: str, phrase: str) -> bool:
    return re.search(rf"(^| ){re.escape(phrase)}($| )", text) is not None


def _parse_until(t: str, today: date) -> Optional[date]:
    m = re.search(r"pana (?:pe |la |in |)(.+)$", t)
    if not m:
        return None
    rest = m.group(1).strip()
    if rest.startswith("maine"):
        return today + timedelta(days=1)
    for name, wd in _ZILE.items():
        if rest.startswith(name):
            return today + timedelta(days=(wd - today.weekday()) % 7 or 7)
    m = re.match(r"(\d{1,2})(?:[ .:/](\d{1,2}))?", rest)
    if not m:
        return None
    day = int(m.group(1))
    month = int(m.group(2)) if m.group(2) else today.month
    year = today.year
    for _ in range(2):                    # dacă a trecut: luna următoare / anul următor
        try:
            d = date(year, month, day)
        except ValueError:
            return None
        if d >= today:
            return d
        if m.group(2):
            year += 1
        else:
            month, year = (1, year + 1) if month == 12 else (month + 1, year)
    return None


def parse_reply(text: str, d: date) -> dict:
    """Text liber → {kind, done:[ids], missed:[ids], until}. Fără LLM.
    kind: all_done | items | vacation | vacation_end | show | unknown"""
    t = _norm(text)
    if not t:
        return {"kind": "unknown"}
    if t in ("/start", "start", "salut", "help", "/help"):
        return {"kind": "help"}
    if t in ("azi", "program", "ce am azi", "/azi", "lista"):
        return {"kind": "show"}
    if any(_has(t, w) for w in ("inapoi", "am revenit", "gata vacanta", "s a terminat vacanta")):
        return {"kind": "vacation_end"}
    if any(_has(t, w) for w in ("vacanta", "concediu", "plecat", "in vacanta")):
        return {"kind": "vacation", "until": _parse_until(t, d)}

    items = [it for _, it in day_items(d)] or [it for it in load_plan()["items"] if it.get("enabled", True)]
    done, missed = [], []
    # Fiecare bucată are polaritatea ei, iar una fără verb o moștenește pe a
    # celei dinainte: „fără creatină și cină" = ambele sărite, dar „am luat
    # creatina dar n-am mers la sală" = una da, una nu.
    neg = False
    for clause in re.split(r" (?:dar|si|iar|insa|apoi|nici) |,|\.|;", " " + t + " "):
        clause = clause.strip()
        if not clause:
            continue
        if any(_has(clause, n) for n in _NEG):
            neg = True
        elif any(_has(clause, p) for p in _POS):
            neg = False
        hits = [it["id"] for it in items if any(_has(clause, k) for k in _keywords(it))]
        (missed if neg else done).extend(h for h in hits if h not in missed + done)

    if done or missed:
        return {"kind": "items", "done": done, "missed": missed}
    if t in _POS_ALL or any(t.startswith(p + " ") for p in ("gata", "ok", "da", "tot")):
        return {"kind": "all_done"}
    return {"kind": "unknown"}


def _llm_parse(text: str, d: date) -> dict:
    """Fallback pentru ce n-au prins regulile. Un apel mic, fără thinking."""
    items = [it for _, it in day_items(d)]
    if not items:
        return {"kind": "unknown"}
    try:
        import ai_core
        ask = getattr(ai_core, "ask_llm_json", None) or getattr(ai_core, "ask_gemini_json")
    except Exception:
        return {"kind": "unknown"}
    lista = "\n".join(f"- {it['id']}: {it['title']}" for it in items)
    prompt = f"""Sergiu răspunde pe Telegram la reminderele lui de sănătate (mese, suplimente, sală).
Spune ce iteme N-A făcut și ce a confirmat că A făcut. Dacă nu se referă la itemele astea, lasă listele goale.

ITEME AZI:
{lista}

MESAJ: "{text}"
"""
    schema = {"type": "OBJECT", "properties": {
        "missed": {"type": "ARRAY", "items": {"type": "STRING"}},
        "done": {"type": "ARRAY", "items": {"type": "STRING"}},
    }, "required": ["missed", "done"]}
    try:
        rez = ask(prompt, schema=schema, temperature=0.1) or {}
    except Exception as e:
        logger.warning(f"⚠️ [Health] LLM indisponibil: {e}")
        return {"kind": "unknown"}
    ids = {it["id"] for it in items}
    missed = [i for i in rez.get("missed") or [] if i in ids]
    done = [i for i in rez.get("done") or [] if i in ids and i not in missed]
    return {"kind": "items", "done": done, "missed": missed} if (done or missed) else {"kind": "unknown"}


def apply_reply(text: str, now: Optional[datetime] = None, use_llm: bool = True) -> str:
    """Aplică un răspuns și întoarce confirmarea care se trimite înapoi."""
    now = now or datetime.now()
    d = logical_day(now)
    t = _norm(text)
    if _has(t, "ieri"):
        d = d - timedelta(days=1)
    r = parse_reply(text, d)
    if r["kind"] == "unknown" and use_llm:
        r = _llm_parse(text, d)

    if r["kind"] == "help":
        return ("Salut! Sunt Health 🫀\nÎți trimit reminderele pentru mese, suplimente și sală, "
                "plus un recap seara. Nu trebuie să bifezi nimic — dacă nu zici, e făcut.\n\n"
                "Scrie doar excepțiile: „fără sală”, „n-am luat fierul”, „am sărit cina”.\n"
                "„vacanță până duminică” / „înapoi” · „azi” = ce ai azi.")
    if r["kind"] == "show":
        return f"📋 {d:%d.%m}:\n" + today_text(d)
    if r["kind"] == "vacation":
        return set_vacation(now.date(), r.get("until"))["message"]
    if r["kind"] == "vacation_end":
        return set_vacation(None)["message"]
    if r["kind"] == "all_done":
        # Doar ce a trecut deja: un „gata" la 15:00 nu bifează și cina.
        for hhmm, it in day_items(d):
            if _at(d, hhmm) <= now and status_of(d, it["id"]) != "missed":
                set_status(d, it["id"], "done")
        return "👍 Notat, tot bifat."
    if r["kind"] == "items":
        titles = {it["id"]: it["title"] for it in load_plan()["items"]}
        for i in r["missed"]:
            set_status(d, i, "missed")
        for i in r["done"]:
            set_status(d, i, "done")
        out = []
        if r["missed"]:
            out.append("✗ " + ", ".join(titles[i] for i in r["missed"]))
        if r["done"]:
            out.append("✓ " + ", ".join(titles[i] for i in r["done"]))
        zi = "ieri" if d < logical_day(now) else "azi"
        return f"Notat pentru {zi}:\n" + "\n".join(out)
    return "N-am înțeles la ce se referă 🤔 Scrie de ex. „fără sală” sau „n-am luat creatina”. („azi” = lista de azi)"


# =============================================================================
# CONSECVENȚĂ
# =============================================================================

def stats(days: int = 28, today: Optional[date] = None) -> dict:
    """Per zi și per categorie: câte din câte (done+auto+deschise / total).
    Zilele în vacanță sunt marcate separat și nu intră în procent."""
    today = today or date.today()
    plan, log = load_plan(), load_log()
    out_days, totals = [], {k: [0, 0] for k in ("meal", "supplement", "gym")}
    for off in range(days - 1, -1, -1):
        d = today - timedelta(days=off)
        entry = {"date": d.isoformat(), "vacation": is_vacation(d, plan), "cats": {}}
        day = log.get(d.isoformat())
        if not entry["vacation"] and day and day.get("sent"):
            for _, it in day_items(d, plan):
                k = it["kind"]
                if k not in totals:
                    continue
                st = (day.get("items") or {}).get(it["id"])
                if d == today and st is None:
                    continue                      # azi: contează doar ce e deja marcat
                ok = st != "missed"
                c = entry["cats"].setdefault(k, [0, 0])
                c[0] += ok
                c[1] += 1
                totals[k][0] += ok
                totals[k][1] += 1
        out_days.append(entry)
    pct = {k: (round(100 * v[0] / v[1]) if v[1] else None) for k, v in totals.items()}
    return {"days": out_days, "pct": pct}


# =============================================================================
# SEED — protocolul de acum
# =============================================================================

SEED_ITEMS = [
    {"id": "apa", "kind": "routine", "title": "Apă cu sare și lămâie",
     "details": "300–400 ml apă + un vârf de sare de mare + lămâie. Apoi apă rece pe față.",
     "times": {"weekday": "06:50", "weekend": "09:00"}, "keywords": ["apa", "sare", "lamaie"]},
    {"id": "masa1", "kind": "meal", "title": "Masa 1 — mic dejun",
     "details": "Mic dejun dens: ouă, pâine.",
     "times": {"weekday": "07:00", "weekend": "09:30"}, "ideas": "mic_dejun",
     "keywords": ["masa 1", "mic dejun", "micul dejun", "dimineata", "oua", "omleta"]},
    {"id": "supl_dimineata", "kind": "supplement", "title": "D3/MK-7 + Folic + ulei de măsline",
     "details": "NOW Mega D3 & MK-7 — 1 caps · NOW Folic Acid 800 mcg — 1 tab · 1 lingură ulei de măsline.",
     "times": {"weekday": "07:00", "weekend": "09:30"},
     "keywords": ["d3", "mk 7", "mk7", "vitamina d", "folic", "acid folic", "folat", "ulei", "vitaminele"]},
    {"id": "sandvisuri", "kind": "meal", "title": "Sandvișuri pentru școală",
     "details": "2–3 sandvișuri dense în ghiozdan.",
     "times": {"weekday": "07:10", "weekend": None}, "ideas": "scoala",
     "keywords": ["sandvis", "sandvisuri", "sandvisurile", "scoala"]},
    {"id": "nuci", "kind": "meal", "title": "Bol de nuci",
     "details": "Lângă tastatură, mănânci mecanic.",
     "times": {"weekday": "15:30", "weekend": "11:30"}, "ideas": "snack",
     "keywords": ["nuci", "nucile", "snack", "gustare", "gustarea"]},
    {"id": "masa2", "kind": "meal", "title": "Masa 2 — prânz",
     "details": "Mâncare gătită consistentă. ⚠️ Fără lactate și fără cafea (fierul).",
     "times": {"weekday": "14:30", "weekend": "14:00"}, "ideas": "pranz",
     "keywords": ["masa 2", "pranz", "pranzul", "mancare gatita"]},
    {"id": "supl_pranz", "kind": "supplement", "title": "Fier + ProSolium",
     "details": "Thorne Iron Bisglycinate — 1 caps, cu vitamina C · ProSolium — 1 caps. "
                "⚠️ Fără lactate/calciu/magneziu/cafea la masa asta.",
     "times": {"weekday": "14:30", "weekend": "14:00"},
     "keywords": ["fier", "fierul", "iron", "prosolium"]},
    {"id": "sala", "kind": "gym", "title": "Sală",
     "details": "L-V: 3–4 exerciții mari compuse, RIR 1–2 (+ opțional 15–20 min Zona 2). "
                "Weekend: 20–25 min Zona 2 pe bandă + flotări/tracțiuni.",
     "times": {"weekday": "17:00", "weekend": "12:30"},
     "keywords": ["sala", "sala de forta", "antrenament", "gym", "zona 2", "banda", "cardio", "flotari"]},
    {"id": "shake", "kind": "meal", "title": "Shake caloric",
     "details": "Bea-l în 60 de secunde.",
     "times": {"weekday": "18:30", "weekend": "16:30"}, "ideas": "shake",
     "keywords": ["shake", "shakeul", "shake ul"]},
    {"id": "creatina", "kind": "supplement", "title": "Creatină 5 g",
     "details": "În shake sau cu 300 ml apă.",
     "times": {"weekday": "18:30", "weekend": "16:30"}, "keywords": ["creatina", "creatin"]},
    {"id": "cina", "kind": "meal", "title": "Masa 3 — cina",
     "details": "Mâncare caldă. Ultima masă solidă (2.5–3 h înainte de somn).",
     "times": {"weekday": "20:00", "weekend": "20:00"}, "ideas": "cina",
     "keywords": ["cina", "masa 3", "masa de seara"]},
    {"id": "magneziu", "kind": "supplement", "title": "Magneziu 2 tablete",
     "details": "NOW Magnesium Glycinate — 2 tablete cu apă. Separat de fier. Telefonul pe birou.",
     "times": {"weekday": "22:30", "weekend": "22:30"}, "keywords": ["magneziu", "magnesium"]},
]

SEED_IDEAS = {
    "mic_dejun": [
        {"text": "3–4 ouă ochiuri + 2 felii pâine cu unt", "kcal": 650},
        {"text": "Omletă cu cașcaval și șuncă + pâine", "kcal": 700},
        {"text": "Ovăz cu lapte integral, banană și unt de arahide", "kcal": 750},
        {"text": "Sandviș cald cu ou, bacon și avocado", "kcal": 650},
        {"text": "Clătite proteice cu miere și banană", "kcal": 600},
    ],
    "scoala": [
        {"text": "Sandvișuri: unt pe ambele felii, cașcaval, carne", "kcal": 900},
        {"text": "Wrap cu pui și sos + o banană", "kcal": 650},
        {"text": "Covrigi/pateu + iaurt de băut", "kcal": 600},
        {"text": "Baton proteic + o mână de nuci", "kcal": 450},
    ],
    "pranz": [
        {"text": "Pui + orez + legume, cu ulei de măsline (+ ardei pentru vitamina C)", "kcal": 800},
        {"text": "Vită + cartofi la cuptor + salată cu lămâie", "kcal": 850},
        {"text": "Paste cu carne tocată și sos de roșii (fără parmezan)", "kcal": 900},
        {"text": "Somon + orez + salată de ardei", "kcal": 750},
        {"text": "Ciorbă + friptură cu piure (fără lapte)", "kcal": 800},
    ],
    "snack": [
        {"text": "Bol de nuci: caju, migdale, merișoare", "kcal": 500},
        {"text": "2 felii pâine cu unt de arahide + banană", "kcal": 450},
        {"text": "Fructe uscate + ciocolată neagră", "kcal": 400},
        {"text": "Granola cu nuci și miere", "kcal": 450},
    ],
    "shake": [
        {"text": "Lapte integral + banană + unt de arahide + proteină + creatină 5 g", "kcal": 700},
        {"text": "Lapte + ovăz măcinat + miere + proteină", "kcal": 650},
        {"text": "Lapte + cacao + banană + unt de arahide", "kcal": 600},
    ],
    "cina": [
        {"text": "Carne la grătar + cartofi + salată", "kcal": 750},
        {"text": "Pește + orez + legume", "kcal": 700},
        {"text": "Tocăniță cu mămăligă", "kcal": 750},
        {"text": "Chili con carne cu orez", "kcal": 800},
    ],
}


SEED_AUTOMATIONS = [
    {"name": "Răsărit (școală)", "icon": "🌅",
     "trigger": {"type": "time", "at": "06:30", "times": ["06:30"]}, "days": [0, 1, 2, 3, 4],
     "actions": [
         # 06:30–06:40: de la 1% la portocaliu cald
         {"type": "lights", "mode": "color", "color": "#ff8a3d", "brightness": 35, "fade_s": 600, "zone": "all"},
         {"type": "wait", "seconds": 600},
         # 06:40: muzică groove, volum care crește
         {"type": "music", "prompt": "o piesă ritmată, groove — funk sau electronic melodic, bună de trezit"},
         {"type": "volume", "percent": 20},
         # 06:40–06:45: spre alb neutru, 100%
         {"type": "lights", "mode": "color", "color": "#fff1dc", "brightness": 100, "fade_s": 300, "zone": "all"},
         {"type": "wait", "seconds": 150},
         {"type": "volume", "percent": 35},
     ]},
    {"name": "Seara chihlimbar", "icon": "🌙",
     "trigger": {"type": "time", "at": "21:30", "times": ["21:30"]},
     "actions": [
         {"type": "lights", "mode": "color", "color": "#ff7a2e", "brightness": 20, "fade_s": 300, "zone": "all"},
     ]},
]


def seed_automations() -> list:
    """Creează automatizările de lumină din protocol (după nume, fără dubluri).
    Dacă ai deja o automatizare dimineața (ex. „Trezire"), răsăritul nou e
    creat OPRIT, ca să nu ruleze două treziri una peste alta."""
    from core import automations as A
    existing = A.load_all()
    names = {a["name"] for a in existing}
    morning = [a["name"] for a in existing if a.get("enabled", True)
               and a["trigger"]["type"] == "time"
               and any("05:30" <= t <= "08:00" for t in a["trigger"].get("times") or [a["trigger"]["at"]])]
    out = []
    for raw in SEED_AUTOMATIONS:
        if raw["name"] in names:
            continue
        enabled = not (raw["name"].startswith("Răsărit") and morning)
        r = A.save({**raw, "enabled": enabled})
        note = "" if enabled else f" (oprită — ai deja: {', '.join(morning)})"
        out.append(f"{raw['name']}{note}" if r["status"] == "ok" else f"{raw['name']}: {r['message']}")
    return out


def seed(force: bool = False) -> dict:
    """Pune protocolul din rezumat. Nu suprascrie iteme sau idei existente
    (după id / grup), decât cu force=True."""
    plan = load_plan()
    have = {i["id"] for i in plan["items"]}
    added = 0
    for raw in SEED_ITEMS:
        if raw["id"] in have and not force:
            continue
        item, _ = normalize_item(raw)
        plan["items"] = [i for i in plan["items"] if i["id"] != item["id"]] + [item]
        added += 1
    save_plan(plan)

    ideas = load_ideas()
    new_groups = 0
    for g, lst in SEED_IDEAS.items():
        if g not in ideas or force:
            ideas[g] = lst
            new_groups += 1
    save_ideas(ideas)
    try:
        autos = seed_automations()
    except Exception as e:
        autos = [f"automatizările n-au putut fi create: {e}"]
    return {"status": "ok", "items_added": added, "idea_groups_added": new_groups,
            "automations_added": autos}


# =============================================================================
# TELEGRAM (botul Health)
# =============================================================================

def bot_token() -> str:
    try:
        import config  # noqa: F401 — încarcă .env în os.environ
    except Exception:
        pass
    return os.environ.get("HEALTH_BOT_TOKEN", "").strip()


def send(text: str) -> dict:
    from tools.telegram_tools import send_telegram
    token = bot_token()
    if not token:
        return {"status": "error", "message": "Lipsește HEALTH_BOT_TOKEN în .env."}
    return send_telegram(text, token=token, markdown=True)


# =============================================================================
# CLI
# =============================================================================

def main(argv=None) -> None:
    import argparse
    import sys

    logging.basicConfig(level=logging.INFO, format="%(message)s")
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    p = argparse.ArgumentParser(prog="python -m tools.health")
    sub = p.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("seed")
    s.add_argument("--force", action="store_true")
    t = sub.add_parser("today")
    t.add_argument("--date")
    sub.add_parser("next")
    m = sub.add_parser("msg")
    m.add_argument("item_id")
    m.add_argument("--send", action="store_true")
    r = sub.add_parser("recap")
    r.add_argument("--send", action="store_true")
    rp = sub.add_parser("reply")
    rp.add_argument("text")
    rp.add_argument("--no-llm", action="store_true")
    sub.add_parser("close")
    w = sub.add_parser("week")
    w.add_argument("--days", type=int, default=14)
    a = p.parse_args(argv)

    now = datetime.now()
    if a.cmd == "seed":
        print(seed(a.force))
    elif a.cmd == "today":
        d = date.fromisoformat(a.date) if a.date else now.date()
        print(f"{d:%A %d.%m} ({'weekend' if is_weekend(d) else 'L-V'})\n" + today_text(d))
    elif a.cmd == "next":
        ev = next_event(now)
        if not ev:
            print("Nimic programat.")
        elif ev[1] == "recap":
            print(f"{ev[0]:%a %d.%m %H:%M}  recap")
        else:
            print(f"{ev[0]:%a %d.%m %H:%M}  " + ", ".join(i["title"] for i in ev[2]))
    elif a.cmd == "msg":
        plan = load_plan()
        it = next((i for i in plan["items"] if i["id"] == a.item_id), None)
        if not it:
            sys.exit(f"Nu există itemul {a.item_id}. Ai: " + ", ".join(i["id"] for i in plan["items"]))
        hhmm = item_time(it, now.date()) or "--:--"
        same = [i for _, i in day_items(now.date(), plan) if item_time(i, now.date()) == hhmm] or [it]
        text = build_message(same, hhmm)
        print(text)
        if a.send:
            print(send(text))
    elif a.cmd == "recap":
        text = build_recap(logical_day(now))
        print(text)
        if a.send:
            print(send(text))
    elif a.cmd == "reply":
        print(apply_reply(a.text, now, use_llm=not a.no_llm))
    elif a.cmd == "close":
        print("Închise:", [d.isoformat() for d in close_days(now)])
    elif a.cmd == "week":
        st = stats(a.days)
        print("Consecvență:", {KIND_LABEL[k]: (f"{v}%" if v is not None else "—") for k, v in st["pct"].items()})
        for day in st["days"]:
            if day["vacation"]:
                print(f"  {day['date']}  🏖️")
            elif day["cats"]:
                print(f"  {day['date']}  " + "  ".join(f"{KIND_LABEL[k]} {c[0]}/{c[1]}" for k, c in day["cats"].items()))


if __name__ == "__main__":
    main()
