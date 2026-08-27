"""
core/day_runner.py — Ziua pe Telegram
=======================================
Programul zilei devine o conversație pe telefon:

    1. Când începe un bloc, primești un mesaj.
    2. Îi răspunzi liber („nu pot acum", „mută peste o oră", „gata cu asta").
    3. Reașază restul zilei și îți trimite programul actualizat.
    4. Continuă de acolo, cu noul program.

Cost: interpretarea mesajelor tale e un apel LLM mic, dar DOAR când scrii ceva.
Un mesaj simplu ca „gata" nici măcar nu ajunge la model — e prins de scurtături.
Notificările și replanificarea sunt pur deterministe.

Rulează ca task asyncio pornit din main_async.py.
"""

import asyncio
import json
import logging
import os
from datetime import datetime, date

logger = logging.getLogger(__name__)

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STATE_FILE = os.path.join(BASE_DIR, "chronos_data", "day_runner_state.json")

try:
    from config import (DAY_TELEGRAM_ENABLED, DAY_NOTIFY_LEAD_MIN,
                        GEMINI_MODEL_DEFAULT)
except ImportError:
    DAY_TELEGRAM_ENABLED = True
    DAY_NOTIFY_LEAD_MIN = 0
    GEMINI_MODEL_DEFAULT = "gemini-2.5-flash"

# Scurtături: comenzi frecvente, rezolvate fără LLM
_SCURTATURI = {
    "gata": ("gata", ""), "done": ("gata", ""), "facut": ("gata", ""),
    "am facut": ("gata", ""), "bifeaza": ("gata", ""),
    "sari": ("sari", ""), "skip": ("sari", ""),
    "replan": ("replan", ""), "reprogrameaza": ("replan", ""),
    "program": ("arata", ""), "ce am": ("arata", ""), "azi": ("arata", ""),
}

_SCHEMA = {
    "type": "OBJECT",
    "properties": {
        "actiune": {"type": "STRING",
                    "enum": ["amana", "sari", "gata", "acum", "replan", "arata", "nimic"]},
        "target": {"type": "STRING", "description": "La ce se referă, dacă zice. Gol = blocul curent."},
        "minute": {"type": "INTEGER", "description": "Cu câte minute amână."},
        "ora": {"type": "STRING", "description": "HH:MM dacă cere o oră anume."},
    },
    "required": ["actiune"],
}


def _load_state() -> dict:
    try:
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def _save_state(st: dict) -> None:
    try:
        os.makedirs(os.path.dirname(STATE_FILE), exist_ok=True)
        with open(STATE_FILE, "w", encoding="utf-8") as f:
            json.dump(st, f, ensure_ascii=False, indent=2)
    except Exception as e:
        logger.debug(f"[Zi/TG] Nu pot salva starea: {e}")


def _interpreteaza(text: str) -> dict:
    """Text liber → acțiune structurată. Scurtături întâi, LLM doar dacă trebuie."""
    t = " ".join(text.lower().split())

    for cheie, (act, tgt) in _SCURTATURI.items():
        if t == cheie or t.startswith(cheie + " "):
            rest = t[len(cheie):].strip()
            return {"actiune": act, "target": rest or tgt}

    try:
        from ai_core import ask_gemini_json
    except ImportError:
        return {"actiune": "nimic"}

    prompt = f"""Sergiu răspunde pe Telegram la programul lui de azi. Tradu ce vrea
într-o acțiune.

MESAJ: "{text}"

Acțiuni:
- amana  → vrea mai târziu ("nu pot acum", "peste o oră", "mută la 6"). Pune
           `minute` SAU `ora` (HH:MM), nu ambele.
- sari   → renunță azi la treaba aia ("las-o", "nu mai fac")
- gata   → a terminat ceva ("am terminat X", "done")
- acum   → vrea să înceapă imediat ceva
- replan → vrea tot restul zilei rearanjat
- arata  → vrea doar să vadă programul
- nimic  → nu e o comandă despre program

`target` = la ce se referă, dacă spune. Gol dacă vorbește despre ce e acum."""

    rez = ask_gemini_json(prompt, schema=_SCHEMA, temperature=0.1,
                          model=GEMINI_MODEL_DEFAULT)
    return rez if isinstance(rez, dict) else {"actiune": "nimic"}


async def _notifica_blocuri(st: dict) -> None:
    """Trimite un mesaj când începe un bloc — o singură dată per bloc."""
    from tools.day_planner import load_day, program_text
    from tools.telegram_tools import send_telegram

    zi = load_day()
    if not zi.get("iteme"):
        return

    azi = date.today().isoformat()
    if st.get("zi") != azi:
        st.update({"zi": azi, "notificate": []})

    acum = datetime.now()
    gata_ids = {i["id"] for i in zi.get("iteme", []) if i.get("gata")}

    from tools.day_planner import _este_lucru, _moment
    for b in zi.get("program", []):
        if not _este_lucru(b):
            continue
        cheie = f"{b['start']}|{b.get('titlu')}"
        if cheie in st.get("notificate", []):
            continue
        if b.get("item_id") in gata_ids:
            continue                      # deja bifat, n-are rost
        if _moment(b["start"]) > acum:
            continue                      # încă n-a venit vremea
        if _moment(b.get("sfarsit", "23:59")) < acum:
            st.setdefault("notificate", []).append(cheie)   # a trecut, marcăm tăcut
            continue

        rest = program_text(zi)
        await asyncio.to_thread(
            send_telegram,
            f"▶️ {b['titlu']}\n{b['start']}–{b['sfarsit']}\n\n"
            f"Mai departe azi:\n{rest}\n\n"
            f"Scrie-mi dacă nu poți acum sau vrei altfel."
        )
        st.setdefault("notificate", []).append(cheie)
        _save_state(st)
        logger.info(f"📨 [Zi/TG] Notificat: {b['titlu']} ({b['start']})")


async def _proceseaza_mesaje(st: dict) -> None:
    """Citește ce ai scris pe Telegram și adaptează programul."""
    from tools.telegram_tools import get_updates, send_telegram
    from tools.day_planner import reschedule, program_text

    mesaje = await asyncio.to_thread(get_updates, st.get("offset", 0), 25)
    if not mesaje:
        return

    for m in mesaje:
        st["offset"] = m["update_id"] + 1
        text = m["text"]
        logger.info(f"📥 [Zi/TG] Mesaj: {text[:60]}")

        cmd = await asyncio.to_thread(_interpreteaza, text)
        act = (cmd.get("actiune") or "nimic").lower()

        if act == "nimic":
            _save_state(st)
            continue

        if act == "arata":
            await asyncio.to_thread(send_telegram, "📋 Programul tău:\n" + program_text())
            _save_state(st)
            continue

        rez = await asyncio.to_thread(
            reschedule, act, cmd.get("target", ""),
            int(cmd.get("minute") or 0), cmd.get("ora", "") or "")

        raspuns = rez.get("message", "Gata.")
        if rez.get("status") == "ok":
            raspuns += "\n\n📋 Programul actualizat:\n" + (
                rez.get("program") or program_text())
        await asyncio.to_thread(send_telegram, raspuns)
        _save_state(st)


async def run(interval: float = 20.0) -> None:
    """Bucla principală. Pornită ca task din main_async.py."""
    if not DAY_TELEGRAM_ENABLED:
        logger.info("ℹ️ [Zi/TG] Dezactivat din config.")
        return

    try:
        from tools.telegram_tools import is_configured
        if not is_configured():
            logger.info("ℹ️ [Zi/TG] Telegram neconfigurat — programul zilei "
                        "merge, dar fără notificări pe telefon.")
            return
    except ImportError:
        return

    st = _load_state()
    logger.info("📅 [Zi/TG] Pornit — îți trimit fiecare bloc pe Telegram.")

    while True:
        try:
            await _notifica_blocuri(st)
            await _proceseaza_mesaje(st)     # long polling, ține până la ~25s
        except asyncio.CancelledError:
            logger.info("🛑 [Zi/TG] Oprit.")
            return
        except Exception as e:
            logger.error(f"❌ [Zi/TG] Eroare în buclă: {e}", exc_info=True)
            await asyncio.sleep(interval)
