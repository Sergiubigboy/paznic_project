"""
tools/day_planner.py — Planificatorul Zilei
=============================================
Îi spui dimineața ce vrei să faci, îți construiește programul pe toată ziua.

PRINCIPIUL DE BAZĂ — NU DUBLĂM STAREA:
    Un item care vine de undeva (reminder, pas de proiect) NU-și ține propria
    bifă. Ține doar o REFERINȚĂ, iar starea se citește din sursă la fiecare
    afișare. Consecința: bifezi reminderul în dashboard → apare bifat și în
    ziua ta, fără niciun cod de sincronizare, și fără să poată diverge vreodată.
    Doar challenge-urile (lucruri one-off, fără sursă) își țin starea aici —
    de aceea nu e nevoie de o pagină separată pentru ele.

Programarea e DETERMINISTĂ (fără LLM): modelul vocal a purtat deja conversația
și a trimis itemele structurate, aici doar le potrivim cu sursele existente și
le așezăm în timp. Zero apeluri Gemini în plus.

Fișiere:
    chronos_data/days/AAAA-LL-ZZ.json   — o zi
    chronos_data/gym/sleep.json         — fereastra de somn (setabilă din Gym)
"""

import ast
import json
import logging
import os
from datetime import datetime, date, timedelta
from difflib import SequenceMatcher
from typing import Optional

logger = logging.getLogger(__name__)

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.path.join(BASE_DIR, "chronos_data")
DAYS_DIR = os.path.join(DATA_DIR, "days")
SLEEP_FILE = os.path.join(DATA_DIR, "gym", "sleep.json")
REMINDERS_FILE = os.path.join(DATA_DIR, "reminders.json")
ELECTRONICS_FILE = os.path.join(DATA_DIR, "electronics_data.json")

# Cât de dens e programul, în funcție de cheful tău
INTENSITATI = {
    "relaxat": {"bloc_max": 45, "pauza": 25, "max_iteme": 4, "start_dupa_trezire": 90},
    "normal":  {"bloc_max": 60, "pauza": 15, "max_iteme": 6, "start_dupa_trezire": 60},
    "full":    {"bloc_max": 95, "pauza": 10, "max_iteme": 9, "start_dupa_trezire": 30},
}

SOMN_DEFAULT = {
    "activ": "vacanta",
    "moduri": {
        "vacanta": {"trezire": "10:00", "culcare": "02:00"},
        "scoala":  {"trezire": "07:00", "culcare": "23:00"},
    },
}


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
        logger.warning(f"⚠️ [Zi] Nu pot citi {os.path.basename(path)}: {e}")
        return default


def _save(path: str, data) -> bool:
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        return True
    except Exception as e:
        logger.error(f"❌ [Zi] Nu pot salva {os.path.basename(path)}: {e}")
        return False


def _ca_dict(raw):
    """
    Modelul vocal trimite uneori itemele ca SIRURI (dictionare serializate),
    nu ca obiecte — o slabiciune cunoscuta la array-uri de obiecte in
    declaratiile de functii. Acceptam ambele forme in loc sa crapam.
    """
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str):
        text = raw.strip()
        if not text:
            return None
        for parser in (json.loads, ast.literal_eval):
            try:
                val = parser(text)
                if isinstance(val, dict):
                    return val
            except Exception:
                continue
        # Nici asa? Atunci e doar titlul, spus simplu.
        return {"titlu": text}
    return None


def _sim(a: str, b: str) -> float:
    a, b = (a or "").lower().strip(), (b or "").lower().strip()
    if not a or not b:
        return 0.0
    if a in b or b in a:
        return 1.0
    cuv_a, cuv_b = set(a.split()), set(b.split())
    overlap = len(cuv_a & cuv_b) / len(cuv_b) if cuv_b else 0.0
    return max(SequenceMatcher(None, a, b).ratio(), overlap)


def _hm(t: datetime) -> str:
    return t.strftime("%H:%M")


# Blocurile din program poarta genul ITEMULUI (proiect/reminder/challenge),
# nu eticheta generica "task" — deci nu compara cu "task", foloseste asta.
_NELUCRU = ("masa", "ocupat", "pauza")


def _este_lucru(bloc: dict) -> bool:
    return bloc.get("gen") not in _NELUCRU


def _moment(hm: str, d: Optional[date] = None) -> datetime:
    """
    „HH:MM" → moment real, CONȘTIENT de trecerea peste miezul nopții.
    Fereastra lui Sergiu e 10:00–02:00, deci „01:30" e MÂINE, nu azi dimineață.
    Compararea orelor ca text ("22:57" < "00:12") dădea rezultate inversate.
    """
    d = d or date.today()
    trezire, _ = sleep_window(d)
    try:
        h, m = (int(x) for x in str(hm).split(":"))
    except Exception:
        return trezire
    t = datetime.combine(d, datetime.min.time()).replace(hour=h, minute=m)
    if t < trezire:
        t += timedelta(days=1)
    return t


# ─────────────────────────────────────────────────────────────
# SOMN
# ─────────────────────────────────────────────────────────────

def get_sleep() -> dict:
    cfg = _load(SLEEP_FILE, None)
    if not cfg:
        _save(SLEEP_FILE, SOMN_DEFAULT)
        cfg = SOMN_DEFAULT
    return cfg


def sleep_window(d: Optional[date] = None) -> tuple:
    """(trezire, culcare) ca datetime. Culcarea poate fi după miezul nopții."""
    d = d or date.today()
    cfg = get_sleep()
    mod = cfg.get("moduri", {}).get(cfg.get("activ"), SOMN_DEFAULT["moduri"]["vacanta"])
    try:
        th, tm = (int(x) for x in mod.get("trezire", "10:00").split(":"))
        ch, cm = (int(x) for x in mod.get("culcare", "02:00").split(":"))
    except Exception:
        th, tm, ch, cm = 10, 0, 2, 0

    trezire = datetime.combine(d, datetime.min.time()).replace(hour=th, minute=tm)
    culcare = datetime.combine(d, datetime.min.time()).replace(hour=ch, minute=cm)
    if culcare <= trezire:
        culcare += timedelta(days=1)      # se culcă după miezul nopții
    return trezire, culcare


def set_sleep(mode: str = "", trezire: str = "", culcare: str = "") -> dict:
    """Schimbă modul activ și/sau orele unui mod."""
    cfg = get_sleep()
    mode = (mode or cfg.get("activ") or "vacanta").lower().strip()
    moduri = cfg.setdefault("moduri", {})
    m = moduri.setdefault(mode, {"trezire": "10:00", "culcare": "02:00"})
    if trezire:
        m["trezire"] = trezire
    if culcare:
        m["culcare"] = culcare
    cfg["activ"] = mode
    _save(SLEEP_FILE, cfg)
    return {"status": "ok",
            "message": f"Program de somn '{mode}': trezire {m['trezire']}, culcare {m['culcare']}."}


# ─────────────────────────────────────────────────────────────
# POTRIVIREA CU SURSELE EXISTENTE
# ─────────────────────────────────────────────────────────────

def _match_reminder(titlu: str):
    data = _load(REMINDERS_FILE, {"reminders": []})
    best, scor = None, 0.0
    for r in data.get("reminders", []):
        if r.get("checked"):
            continue
        s = _sim(titlu, r.get("title", ""))
        if s > scor:
            best, scor = r, s
    return (best, scor) if scor >= 0.55 else (None, 0.0)


def _walk(steps):
    for s in steps or []:
        yield s
        yield from _walk(s.get("children", []))


def _match_project(titlu: str, hint: str = ""):
    """Găsește proiectul + eventual pasul care se potrivește cu ce vrei să faci."""
    data = _load(ELECTRONICS_FILE, {})
    proiecte = data.get("projects", [])
    if not proiecte:
        return None, None, 0.0

    cauta = f"{hint} {titlu}".strip()
    proj, scor = None, 0.0
    for p in proiecte:
        s = _sim(cauta, p.get("name", ""))
        if s > scor:
            proj, scor = p, s
    if scor < 0.4:
        return None, None, 0.0

    pas, ps = None, 0.0
    for st in _walk(proj.get("plan", [])):
        if st.get("status") == "done":
            continue
        s = _sim(titlu, st.get("title", ""))
        if s > ps:
            pas, ps = st, s
    return proj, (pas if ps >= 0.5 else None), scor


def _add_project_steps(project_name: str, steps: list) -> Optional[dict]:
    """Creează proiectul (dacă lipsește) și îi adaugă pașii ceruți."""
    data = _load(ELECTRONICS_FILE, {})
    proiecte = data.setdefault("projects", [])

    proj, scor = None, 0.0
    for p in proiecte:
        s = _sim(project_name, p.get("name", ""))
        if s > scor:
            proj, scor = p, s
    if scor < 0.5:
        proj = {
            "id": f"proj_{int(datetime.now().timestamp()*1000)}",
            "name": project_name, "description": "", "status": "activ",
            "technologies": [], "links": [], "reservations": [], "devlog": [],
            "plan": [], "created_at": datetime.now().isoformat(),
        }
        proiecte.append(proj)
        logger.info(f"🆕 [Zi] Proiect nou: {project_name}")

    noi = []
    for i, titlu in enumerate(steps or []):
        titlu = (titlu or "").strip()
        if not titlu:
            continue
        if any(_sim(titlu, s.get("title", "")) >= 0.8 for s in _walk(proj.get("plan", []))):
            continue        # pasul există deja
        pas = {
            "id": f"step_{int(datetime.now().timestamp()*1000)}_{i}",
            "title": titlu, "status": "todo", "priority": "Med",
            "children": [], "created_at": datetime.now().isoformat(),
        }
        proj.setdefault("plan", []).append(pas)
        noi.append(pas)

    proj["updated_at"] = datetime.now().isoformat()
    _save(ELECTRONICS_FILE, data)
    return {"project": proj, "noi": noi}


# ─────────────────────────────────────────────────────────────
# STAREA UNUI ITEM — citită din sursă
# ─────────────────────────────────────────────────────────────

def _status_din_sursa(item: dict) -> Optional[bool]:
    """True/False dacă itemul are sursă, None dacă e challenge propriu."""
    ref = item.get("ref") or {}
    tip = ref.get("tip")

    if tip == "reminder":
        for r in _load(REMINDERS_FILE, {"reminders": []}).get("reminders", []):
            if r.get("id") == ref.get("id"):
                return bool(r.get("checked"))
        return None

    if tip == "proiect":
        data = _load(ELECTRONICS_FILE, {})
        for p in data.get("projects", []):
            if p.get("id") != ref.get("proiect_id"):
                continue
            for s in _walk(p.get("plan", [])):
                if s.get("id") == ref.get("pas_id"):
                    return s.get("status") == "done"
        return None
    return None


def _hidrateaza(zi: dict) -> dict:
    """Completează statusul fiecărui item din sursa lui."""
    for it in zi.get("iteme", []):
        din_sursa = _status_din_sursa(it)
        it["gata"] = din_sursa if din_sursa is not None else bool(it.get("gata"))
        it["auto"] = din_sursa is not None
    return zi


# ─────────────────────────────────────────────────────────────
# ZIUA
# ─────────────────────────────────────────────────────────────

def _day_path(d: date) -> str:
    return os.path.join(DAYS_DIR, f"{d.isoformat()}.json")


def load_day(d: Optional[date] = None) -> dict:
    d = d or date.today()
    zi = _load(_day_path(d), None)
    if not zi:
        return {"data": d.isoformat(), "intensitate": "normal", "iteme": [], "program": []}
    return _hidrateaza(zi)


def save_day(zi: dict) -> bool:
    try:
        d = date.fromisoformat(zi["data"])
    except Exception:
        d = date.today()
    return _save(_day_path(d), zi)


def restante(d: Optional[date] = None) -> list:
    """Ce a rămas nefăcut ieri (sau într-o zi anume)."""
    d = d or (date.today() - timedelta(days=1))
    zi = _load(_day_path(d), None)
    if not zi:
        return []
    zi = _hidrateaza(zi)
    return [it for it in zi.get("iteme", []) if not it.get("gata")]


# ─────────────────────────────────────────────────────────────
# CONSTRUIREA PROGRAMULUI
# ─────────────────────────────────────────────────────────────

def _mese(trezire: datetime, culcare: datetime) -> list:
    """
    Ancore de masă. Le legăm de ORE REALE de la trezire, nu de fracțiuni din
    fereastră — altfel, la un program 10:00-02:00, „40% din zi" pica prânzul
    pe la 16:30, ceea ce n-are sens pentru un om.
    """
    total = (culcare - trezire).total_seconds() / 3600
    mese = [(trezire + timedelta(minutes=40), 30, "Mic dejun")]
    if total > 6:
        mese.append((trezire + timedelta(hours=4.5), 45, "Prânz"))
    if total > 10:
        # Cina: ~10h după trezire, dar niciodată lipită de culcare
        cina = min(trezire + timedelta(hours=10), culcare - timedelta(hours=3))
        mese.append((cina, 40, "Cină"))
    return mese


def build_schedule(iteme: list, intensitate: str = "normal",
                   ocupat: Optional[list] = None, d: Optional[date] = None,
                   start_from: Optional[datetime] = None) -> list:
    """
    Așază itemele în timp, între trezire și culcare, ocolind ce e deja ocupat
    și intercalând mese și pauze. Complet determinist.

    start_from: de unde să înceapă plasarea. Folosit la replanificare — când
    zici pe Telegram „nu pot acum", restul zilei se reașază de la ora curentă,
    nu de la trezire.
    """
    d = d or date.today()
    trezire, culcare = sleep_window(d)
    cfg = INTENSITATI.get(intensitate, INTENSITATI["normal"])

    # Sloturi blocate: ce ai zis că ai (ieșiri) + mesele
    blocate = []
    for oc_brut in (ocupat or []):
        oc = _ca_dict(oc_brut)
        if not oc:
            continue
        try:
            sh, sm = (int(x) for x in str(oc["start"]).split(":"))
            eh, em = (int(x) for x in str(oc["end"]).split(":"))
            s = datetime.combine(d, datetime.min.time()).replace(hour=sh, minute=sm)
            e = datetime.combine(d, datetime.min.time()).replace(hour=eh, minute=em)
            if e <= s:
                e += timedelta(days=1)
            blocate.append({"start": s, "end": e, "titlu": oc.get("title") or "Ocupat",
                            "gen": "ocupat"})
        except Exception:
            continue
    for start, durata, nume in _mese(trezire, culcare):
        blocate.append({"start": start, "end": start + timedelta(minutes=durata),
                        "titlu": nume, "gen": "masa"})
    blocate.sort(key=lambda b: b["start"])

    def _liber(inceput: datetime, minute: int) -> Optional[datetime]:
        """Primul moment >= inceput unde încap `minute` fără suprapunere."""
        cursor = inceput
        for _ in range(200):                     # limită de siguranță
            sfarsit = cursor + timedelta(minutes=minute)
            if sfarsit > culcare:
                return None
            ciocnire = next((b for b in blocate
                             if cursor < b["end"] and sfarsit > b["start"]), None)
            if not ciocnire:
                return cursor
            cursor = ciocnire["end"]
        return None

    program = []
    cursor = start_from or (trezire + timedelta(minutes=cfg["start_dupa_trezire"]))
    if cursor < trezire:
        cursor = trezire
    plasate = 0

    # PAS 1: itemele cu ora CERUTA EXPLICIT ("muta la 23:30") se ancoreaza
    # exact acolo si blocheaza slotul; restul se aseaza in jurul lor.
    for it in list(iteme):
        if not it.get("fix_start"):
            continue
        durata = max(10, min(int(it.get("minute") or 45), cfg["bloc_max"]))
        start = _moment(it["fix_start"], d)
        sfarsit = start + timedelta(minutes=durata)
        if sfarsit > culcare:
            it["nota"] = "ora ceruta e dupa ora de culcare"
            continue
        it["start"], it["sfarsit"] = _hm(start), _hm(sfarsit)
        program.append({"start": _hm(start), "sfarsit": _hm(sfarsit),
                        "titlu": it.get("titlu"), "gen": it.get("gen", "task"),
                        "item_id": it.get("id")})
        blocate.append({"start": start, "end": sfarsit,
                        "titlu": it.get("titlu"), "gen": "task"})
        plasate += 1
    blocate.sort(key=lambda b: b["start"])

    # PAS 2: restul
    for it in iteme:
        if it.get("fix_start") and it.get("start"):
            continue
        if plasate >= cfg["max_iteme"]:
            it["nota"] = "peste limita zilei"
            continue
        durata = int(it.get("minute") or 45)
        durata = max(10, min(durata, cfg["bloc_max"]))

        # Amanarile sunt lipite de item, nu de o singura rulare a planificarii.
        # Altfel, orice alta modificare ulterioara (o bifare, o renuntare) ar
        # reaseza totul de la ora curenta si ar anula amanarea.
        de_la_item = cursor
        if it.get("nu_inainte"):
            prag = _moment(it["nu_inainte"], d)
            if prag > de_la_item:
                de_la_item = prag

        start = _liber(de_la_item, durata)
        if start is None:
            it["nota"] = "n-a mai încăput azi"
            continue

        sfarsit = start + timedelta(minutes=durata)
        it["start"] = _hm(start)
        it["sfarsit"] = _hm(sfarsit)
        program.append({"start": _hm(start), "sfarsit": _hm(sfarsit),
                        "titlu": it.get("titlu"), "gen": it.get("gen", "task"),
                        "item_id": it.get("id")})
        blocate.append({"start": start, "end": sfarsit, "titlu": it.get("titlu"),
                        "gen": "task"})
        blocate.sort(key=lambda b: b["start"])
        cursor = sfarsit + timedelta(minutes=cfg["pauza"])
        plasate += 1

    for b in blocate:
        if b["gen"] in ("masa", "ocupat"):
            program.append({"start": _hm(b["start"]), "sfarsit": _hm(b["end"]),
                            "titlu": b["titlu"], "gen": b["gen"]})
    program.sort(key=lambda p: _moment(p["start"], d))
    return program


# ─────────────────────────────────────────────────────────────
# API PUBLIC
# ─────────────────────────────────────────────────────────────

def plan_day(iteme: list, intensitate: str = "normal",
             ocupat: Optional[list] = None, cu_program: bool = False) -> dict:
    """
    Inregistreaza ce vrea Sergiu sa faca azi.

    IMPLICIT NU face orar. Doar preia intentiile, le leaga de proiectele si
    reminderele existente si creeaza pasii care lipsesc. Peste zi el spune ce
    a facut; nimic nu-l alearga dupa ceas.

    `cu_program=True` construieste si orarul pe ore — dar numai daca a cerut-o
    explicit. Se poate adauga si mai tarziu, cu fa_program().

    Apelurile repetate din aceeasi zi ADAUGA la ce exista deja: daca la pranz
    zice „vreau sa fac si X", X se adauga, nu sterge lista de dimineata.

    Fiecare item: {titlu, minute?, gen?: proiect|reminder|challenge,
                   proiect?: str, pasi?: [str]}
    """
    d = date.today()
    intensitate = (intensitate or "normal").lower()
    if intensitate in ("full throttle", "full_throttle", "hardcore"):
        intensitate = "full"

    construite, note = [], []
    for i, brut in enumerate(iteme or []):
        raw = _ca_dict(brut)
        if not raw:
            continue
        titlu = (raw.get("titlu") or raw.get("title") or "").strip()
        if not titlu:
            continue
        gen = (raw.get("gen") or "").lower()
        item = {
            "id": f"it_{int(datetime.now().timestamp()*1000)}_{i}",
            "titlu": titlu,
            "minute": int(raw.get("minute") or raw.get("minutes") or 45),
            "gen": "task", "ref": None, "gata": False,
        }

        # 1) e un reminder existent?
        if gen in ("", "reminder"):
            rem, _ = _match_reminder(titlu)
            if rem:
                item["ref"] = {"tip": "reminder", "id": rem["id"]}
                item["titlu"] = rem.get("title", titlu)
                item["gen"] = "reminder"
                construite.append(item)
                note.append(f"„{item['titlu']}” — din remindere")
                continue

        # 2) ține de un proiect?
        if gen in ("", "proiect"):
            hint = raw.get("proiect") or raw.get("project") or ""
            pasi_ceruti = raw.get("pasi") or raw.get("steps") or []
            proj, pas, scor = _match_project(titlu, hint)

            if pasi_ceruti or (hint and not proj):
                rez = _add_project_steps(hint or titlu, pasi_ceruti or [titlu])
                if rez:
                    proj = rez["project"]
                    pas = rez["noi"][0] if rez["noi"] else pas
                    if rez["noi"]:
                        note.append(f"„{proj['name']}” — am creat "
                                    f"{len(rez['noi'])} pași noi")
            if proj and not pas:
                rez = _add_project_steps(proj["name"], [titlu])
                pas = rez["noi"][0] if rez and rez["noi"] else None

            if proj and pas:
                item["ref"] = {"tip": "proiect", "proiect_id": proj["id"],
                               "pas_id": pas["id"], "proiect": proj["name"]}
                item["gen"] = "proiect"
                construite.append(item)
                note.append(f"„{titlu}” — pas în proiectul {proj['name']}")
                continue

        # 3) altfel: challenge one-off, își ține starea aici
        item["gen"] = "challenge"
        construite.append(item)

    # Adaugam la ziua existenta, nu o rescriem: peste zi mai apar lucruri.
    zi = load_day(d)
    existente = zi.get("iteme", [])
    adaugate, sarite = [], []
    for it in construite:
        dublura = next((e for e in existente
                        if _sim(it["titlu"], e.get("titlu", "")) >= 0.75), None)
        if dublura:
            sarite.append(dublura.get("titlu", it["titlu"]))
            continue
        existente.append(it)
        adaugate.append(it)

    zi.update({
        "data": d.isoformat(),
        "iteme": existente,
        "creat_la": zi.get("creat_la") or datetime.now().isoformat(timespec="seconds"),
    })
    if cu_program:
        zi["intensitate"] = intensitate
        zi["program"] = build_schedule(existente, intensitate, ocupat, d)
        zi["cu_program"] = True
    else:
        zi.setdefault("intensitate", intensitate)
        zi.setdefault("program", [])
        zi.setdefault("cu_program", False)
    save_day(zi)

    ramase = [i for i in existente if not i.get("gata")]
    logger.info(f"📝 [Zi] +{len(adaugate)} lucruri (total {len(existente)}), "
                f"program={'da' if zi.get('cu_program') else 'nu'}")

    rez = {"status": "ok", "detalii": note, "adaugate": [i["titlu"] for i in adaugate],
           "deja_erau": sarite, "cu_program": bool(zi.get("cu_program"))}

    if zi.get("cu_program"):
        trezire, culcare = sleep_window(d)
        plasate = [i for i in existente if i.get("start")]
        rez.update({
            "intensitate": intensitate,
            "fereastra": f"{_hm(trezire)}–{_hm(culcare)}",
            "program": "; ".join(f"{i['start']} {i['titlu']}" for i in plasate)
                       or "nimic plasat",
            "neincapute": [i["titlu"] for i in existente if i.get("nota")],
            "message": f"Gata, {len(plasate)} lucruri intre {_hm(trezire)} si {_hm(culcare)}.",
        })
    else:
        bucati = []
        if adaugate:
            bucati.append("Am notat: " + ", ".join(i["titlu"] for i in adaugate) + ".")
        if sarite:
            bucati.append("Aveai deja: " + ", ".join(sarite) + ".")
        bucati.append(f"In total {len(ramase)} lucruri de facut azi.")
        rez["message"] = " ".join(bucati)
        rez["info"] = ("Confirma scurt ce ai notat. NU insira ore si NU face orar — "
                       "n-a cerut asa ceva. Peste zi iti spune el ce a terminat.")
    return rez


def fa_program(intensitate: str = "", ocupat: Optional[list] = None) -> dict:
    """
    Construieste orarul pe ore pentru lucrurile deja notate azi.

    Se cheama doar cand Sergiu cere explicit un program. Restul timpului ziua
    traieste ca simpla lista de intentii.
    """
    d = date.today()
    zi = load_day(d)
    iteme = zi.get("iteme", [])
    if not iteme:
        return {"status": "error",
                "message": "Nu mi-ai spus inca ce vrei sa faci azi."}

    intensitate = (intensitate or zi.get("intensitate") or "normal").lower()
    if intensitate in ("full throttle", "full_throttle", "hardcore"):
        intensitate = "full"

    ramase = [i for i in iteme if not i.get("gata")]
    zi["program"] = build_schedule(iteme, intensitate, ocupat, d)
    zi["intensitate"] = intensitate
    zi["cu_program"] = True
    save_day(zi)

    trezire, culcare = sleep_window(d)
    plasate = [i for i in iteme if i.get("start") and not i.get("gata")]
    logger.info(f"📅 [Zi] Orar construit pentru {len(plasate)} lucruri "
                f"(intensitate {intensitate}).")
    return {
        "status": "ok",
        "intensitate": intensitate,
        "fereastra": f"{_hm(trezire)}–{_hm(culcare)}",
        "program": "; ".join(f"{i['start']} {i['titlu']}" for i in plasate)
                   or "nimic plasat",
        "neincapute": [i["titlu"] for i in ramase if i.get("nota")],
        "message": f"Ti-am facut programul: {len(plasate)} lucruri intre "
                   f"{_hm(trezire)} si {_hm(culcare)}.",
    }


def today_summary() -> dict:
    """Ce ai azi + ce e deja bifat (statusuri citite din surse)."""
    zi = load_day()
    iteme = zi.get("iteme", [])
    if not iteme:
        rest = restante()
        if rest:
            return {"status": "ok", "planificat": False,
                    "restante": [r["titlu"] for r in rest],
                    "message": "N-ai niciun plan pe azi. Ieri au rămas nefăcute: "
                               + ", ".join(r["titlu"] for r in rest) + "."}
        return {"status": "ok", "planificat": False,
                "message": "N-ai niciun plan făcut pe azi."}

    gata = [i for i in iteme if i.get("gata")]
    ramase = [i for i in iteme if not i.get("gata")]
    cu_program = bool(zi.get("cu_program"))

    if not ramase:
        return {"status": "ok", "planificat": True, "cu_program": cu_program,
                "message": f"Le-ai facut pe toate {len(iteme)}. Ziua e curata."}

    # Cu orar spunem orele; fara orar e doar o lista — nu inventam ore.
    if cu_program:
        randuri = [f"{i.get('start', '--:--')} {i['titlu']}" for i in ramase]
    else:
        randuri = [i["titlu"] for i in ramase]

    msg = f"Ai {len(ramase)} lucruri ramase din {len(iteme)}: " + "; ".join(randuri) + "."
    if gata:
        msg += f" Bifate: {', '.join(i['titlu'] for i in gata)}."
    return {"status": "ok", "planificat": True, "cu_program": cu_program,
            "intensitate": zi.get("intensitate"),
            "message": msg, "program": zi.get("program", [])}


def complete(query: str) -> dict:
    """Bifează un item din ziua de azi — și sursa lui, dacă are."""
    zi = load_day()
    iteme = zi.get("iteme", [])
    if not iteme:
        return {"status": "error", "message": "N-ai niciun plan pe azi."}

    best, scor = None, 0.0
    for it in iteme:
        if it.get("gata"):
            continue
        s = _sim(query, it.get("titlu", ""))
        if s > scor:
            best, scor = it, s
    if not best or scor < 0.45:
        ramase = ", ".join(i["titlu"] for i in iteme if not i.get("gata")) or "nimic"
        return {"status": "error",
                "message": f"N-am găsit „{query}” în ziua de azi. Ai rămase: {ramase}."}

    ref = best.get("ref") or {}
    if ref.get("tip") == "reminder":
        from tools.data_write_tools import complete_reminder
        complete_reminder(best["titlu"])
    elif ref.get("tip") == "proiect":
        data = _load(ELECTRONICS_FILE, {})
        for p in data.get("projects", []):
            if p.get("id") == ref.get("proiect_id"):
                for s in _walk(p.get("plan", [])):
                    if s.get("id") == ref.get("pas_id"):
                        s["status"] = "done"
        _save(ELECTRONICS_FILE, data)
    else:
        best["gata"] = True      # challenge — starea trăiește aici
        save_day(zi)

    logger.info(f"✅ [Zi] Bifat: {best['titlu']}")
    ramase = len([i for i in load_day().get("iteme", []) if not i.get("gata")])
    return {"status": "ok",
            "message": f"Bifat „{best['titlu']}”. Îți mai rămân {ramase}."}


# ─────────────────────────────────────────────────────────────
# REPLANIFICARE — comenzile venite pe Telegram
# ─────────────────────────────────────────────────────────────

def _gaseste_item(zi: dict, query: str):
    """Itemul nefăcut care se potrivește cel mai bine. Fără query → următorul."""
    ramase = [i for i in zi.get("iteme", []) if not i.get("gata")]
    if not ramase:
        return None
    q = (query or "").strip()
    if not q:
        # „nu pot acum" fără detalii = blocul care tocmai a început / urmează
        acum = datetime.now()
        viitoare = [i for i in ramase
                    if i.get("start") and _moment(i["start"]) >= acum]
        return (viitoare or ramase)[0]

    best, scor = None, 0.0
    for it in ramase:
        s = _sim(q, it.get("titlu", ""))
        if s > scor:
            best, scor = it, s
    return best if scor >= 0.4 else None


def _reaseaza_restul(zi: dict, de_la: Optional[datetime] = None) -> dict:
    """
    Reconstruiește programul pentru ce a mai rămas, pornind de la ora curentă.
    Ce e deja bifat sau a trecut rămâne neatins în istoric.
    """
    de_la = de_la or datetime.now()
    iteme = zi.get("iteme", [])
    de_replasat = [i for i in iteme if not i.get("gata") and not i.get("sarit")]
    for it in de_replasat:
        it.pop("start", None)
        it.pop("sfarsit", None)
        it.pop("nota", None)
        # NU stergem fix_start / nu_inainte — alea sunt decizii ale lui Sergiu

    ocupat = [b for b in zi.get("program", []) if b.get("gen") == "ocupat"]
    program = build_schedule(de_replasat, zi.get("intensitate", "normal"),
                             [{"start": o["start"], "end": o["sfarsit"],
                               "title": o["titlu"]} for o in ocupat],
                             start_from=de_la)

    # Programul se RECONSTRUIEȘTE complet de fiecare dată. Dacă am păstra
    # blocurile vechi „din trecut", un item replanificat ar apărea de două ori
    # — o dată la ora veche, o dată la cea nouă.
    facute = [{"start": i["start"], "sfarsit": i.get("sfarsit", i["start"]),
               "titlu": i["titlu"], "gen": i.get("gen", "task"), "item_id": i["id"]}
              for i in iteme if i.get("gata") and i.get("start")]
    zi["program"] = sorted(facute + program, key=lambda p: _moment(p["start"]))
    save_day(zi)
    return zi


def reschedule(action: str, target: str = "", minute: int = 0,
               ora: str = "") -> dict:
    """
    Ajustează programul zilei. Apelat din Telegram sau vocal.

    action:
        amana   — mută itemul mai târziu (`minute`) sau la o oră fixă (`ora`)
        sari    — scoate itemul din ziua de azi
        gata    — bifează (deleagă la complete())
        acum    — mută itemul să înceapă imediat
        replan  — reașază tot restul zilei de la ora curentă
    """
    action = (action or "").lower().strip()
    zi = load_day()
    if not zi.get("iteme"):
        return {"status": "error", "message": "N-ai niciun plan pe azi."}

    if action == "gata":
        return complete(target)

    # „sari" merge oricum: scoate ceva din ziua de azi, orar sau nu.
    if action == "sari" and not zi.get("cu_program"):
        it = _gaseste_item(zi, target)
        if not it:
            ramase = ", ".join(i["titlu"] for i in zi["iteme"]
                               if not i.get("gata")) or "nimic"
            return {"status": "error",
                    "message": f"N-am gasit „{target}”. Ai ramase: {ramase}."}
        it["sarit"] = True
        it["gata"] = True
        save_day(zi)
        return {"status": "ok",
                "message": f"Am scos „{it['titlu']}” din ziua de azi.",
                "program": lista_text(zi)}

    # Restul inseamna mutat in timp — n-are sens fara orar.
    if not zi.get("cu_program"):
        return {"status": "error", "fara_orar": True,
                "message": "Nu ti-am facut orar pe azi, deci n-am ce muta. "
                           "Spune-mi doar ce ai terminat, sau cere-mi un program."}

    if action == "replan":
        zi = _reaseaza_restul(zi)
        return {"status": "ok", "message": "Am reașezat restul zilei.",
                "program": program_text(zi)}

    it = _gaseste_item(zi, target)
    if not it:
        ramase = ", ".join(i["titlu"] for i in zi["iteme"] if not i.get("gata")) or "nimic"
        return {"status": "error",
                "message": f"N-am găsit „{target}”. Ai rămase: {ramase}."}

    if action == "sari":
        it["sarit"] = True
        it.pop("start", None)
        it.pop("sfarsit", None)
        zi = _reaseaza_restul(zi)
        return {"status": "ok", "message": f"Am scos „{it['titlu']}” din ziua de azi.",
                "program": program_text(zi)}

    if action == "acum":
        zi = _reaseaza_restul(zi, datetime.now())
        return {"status": "ok", "message": f"Am mutat „{it['titlu']}” pe acum.",
                "program": program_text(zi)}

    if action == "amana":
        if ora:
            try:
                h, m = (int(x) for x in ora.split(":"))
                de_la = datetime.now().replace(hour=h, minute=m, second=0, microsecond=0)
                if de_la < datetime.now():
                    de_la += timedelta(days=1)
            except Exception:
                return {"status": "error", "message": f"Ora „{ora}” n-o înțeleg."}
        else:
            de_la = datetime.now() + timedelta(minutes=int(minute or 60))

        # Ora ceruta explicit ancoreaza itemul acolo; „peste N minute" e doar
        # un prag de la care poate incepe. Ambele se retin PE ITEM, ca sa
        # supravietuiasca replanificarilor ulterioare.
        if ora:
            it["fix_start"] = _hm(de_la)
            it.pop("nu_inainte", None)
        else:
            it["nu_inainte"] = _hm(de_la)
            it.pop("fix_start", None)
        # ...si trece la coada listei, ca sa nu blocheze restul
        iteme = zi["iteme"]
        iteme.remove(it)
        iteme.append(it)
        zi = _reaseaza_restul(zi)
        cand = it.get("start", "mai târziu")
        return {"status": "ok",
                "message": f"Am amânat „{it['titlu']}” — acum e la {cand}.",
                "program": program_text(zi)}

    return {"status": "error", "message": f"Nu știu acțiunea „{action}”."}


def lista_text(zi: Optional[dict] = None) -> str:
    """Ce a ramas de facut, fara ore — modul implicit al zilei."""
    zi = zi or load_day()
    ramase = [i for i in zi.get("iteme", []) if not i.get("gata")]
    if not ramase:
        return "Nimic ramas azi."
    icon = {"proiect": "🔧", "reminder": "📌", "challenge": "🎯"}
    return "\n".join(f"{icon.get(i.get('gen'), '▪️')} {i['titlu']}" for i in ramase)


def program_text(zi: Optional[dict] = None, doar_ramase: bool = True) -> str:
    """
    Programul ca text scurt, pentru Telegram.

    Fara orar construit, cade pe simpla lista de intentii — altfel ar returna
    „Nimic ramas azi" desi are lucruri de facut, doar ca fara ore.
    """
    zi = zi or load_day()
    if not zi.get("cu_program"):
        return lista_text(zi)
    acum = datetime.now()
    gata = {i["id"] for i in zi.get("iteme", []) if i.get("gata")}
    randuri = []
    for b in zi.get("program", []):
        if b.get("item_id") and b["item_id"] in gata:
            continue                      # deja facut, nu-l mai plimbam
        if doar_ramase and _moment(b.get("sfarsit", "23:59")) < acum:
            continue
        icon = {"masa": "🍽", "ocupat": "📌", "proiect": "🔧",
                "reminder": "📌", "challenge": "🎯"}.get(b.get("gen"), "▪️")
        randuri.append(f"{b['start']}–{b['sfarsit']}  {icon} {b['titlu']}")
    return "\n".join(randuri) or "Nimic rămas azi."


def next_blocks(zi: Optional[dict] = None) -> list:
    """Blocurile de tip task care încă n-au început (pentru notificări)."""
    zi = zi or load_day()
    gata = {i["id"] for i in zi.get("iteme", []) if i.get("gata")}
    return [b for b in zi.get("program", [])
            if _este_lucru(b) and b.get("item_id") not in gata]
