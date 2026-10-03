"""
core/health_runner.py — botul Health pe Telegram
=================================================
Trimite reminderele din tools/health.py la ora lor, recap-ul seara și ascultă
ce-i răspunzi. Toată logica (ce, când, cum se interpretează un răspuns) stă în
tools/health.py; aici e doar bucla.

Aceeași formă ca day_runner: long polling pe Telegram (conexiunea stă deschisă
~25s așteptând un mesaj, deci nu „bate" serverul), iar între două polling-uri
verifică dacă a venit ora vreunui reminder. Botul e SEPARAT (HEALTH_BOT_TOKEN),
deci are coada lui de mesaje și nu se ceartă cu botul programului zilei.

Rulează ca task asyncio pornit din main_async.py.
"""

import asyncio
import logging
from datetime import datetime, timedelta

from tools import health as H

logger = logging.getLogger(__name__)

# Dacă Chronos pornește la 07:20, reminderul de 07:00 încă pleacă; cel de 06:50
# nu mai are rost după o oră. Recap-ul are fereastră mai mare.
SLOT_GRACE = timedelta(minutes=30)
RECAP_GRACE = timedelta(minutes=90)


async def _send(text: str) -> None:
    r = await asyncio.to_thread(H.send, text)
    if r.get("status") != "ok":
        logger.warning(f"⚠️ [Health] Nu am putut trimite: {r.get('message')}")


async def _check_due(now: datetime) -> None:
    plan = H.load_plan()
    today = now.date()
    for when, items in H.due_slots(now, plan):
        hhmm = f"{when:%H:%M}"
        if when <= now < when + SLOT_GRACE and H.mark_sent(today, hhmm):
            logger.info(f"🫀 [Health] Reminder {hhmm}: " + ", ".join(i["title"] for i in items))
            await _send(H.build_message(items, hhmm))

    if H.day_items(today, plan):
        recap_at = datetime.combine(today, datetime.min.time()).replace(
            hour=int(plan["recap_time"][:2]), minute=int(plan["recap_time"][3:5]))
        if recap_at <= now < recap_at + RECAP_GRACE and H.mark_sent(today, "recap"):
            logger.info("🌙 [Health] Recap trimis.")
            await _send(H.build_recap(today))


async def _handle_messages(st: dict, token: str) -> None:
    from tools.telegram_tools import get_updates

    t0 = asyncio.get_running_loop().time()
    msgs = await asyncio.to_thread(get_updates, st.get("offset", 0), 25, token)
    if not msgs and asyncio.get_running_loop().time() - t0 < 2:
        # Long polling-ul n-a ținut deloc = eroare (rețea, token greșit).
        # Fără pauza asta bucla s-ar învârti în gol pe CPU-ul Pi-ului.
        await asyncio.sleep(15)
    for m in msgs:
        st["offset"] = m["update_id"] + 1
        H.save_state({**H.load_state(), "offset": st["offset"]})
        logger.info(f"📥 [Health] Mesaj: {m['text'][:60]}")
        try:
            raspuns = await asyncio.to_thread(H.apply_reply, m["text"], datetime.now())
        except Exception as e:
            logger.error(f"❌ [Health] Răspuns: {e}", exc_info=True)
            raspuns = "Ceva n-a mers când am notat. Încearcă din nou sau bifează din pagină."
        await _send(raspuns)


async def run(error_pause: float = 20.0) -> None:
    token = H.bot_token()
    if not token:
        logger.info("ℹ️ [Health] HEALTH_BOT_TOKEN lipsă în .env — reminderele Health sunt oprite.")
        return
    if not H.load_plan()["items"]:
        logger.info("ℹ️ [Health] Niciun item în program — rulează `python -m tools.health seed`.")

    st = {"offset": H.load_state().get("offset", 0)}
    last_close = None
    logger.info("🫀 [Health] Pornit — remindere pe botul Health.")

    while True:
        try:
            now = datetime.now()
            if last_close != H.logical_day(now):
                closed = await asyncio.to_thread(H.close_days, now)
                if closed:
                    logger.info(f"🫀 [Health] Zile închise (tăcere = făcut): {[d.isoformat() for d in closed]}")
                last_close = H.logical_day(now)
            await _check_due(now)
            await _handle_messages(st, token)      # long polling, ține până la ~25s
        except asyncio.CancelledError:
            logger.info("🛑 [Health] Oprit.")
            return
        except Exception as e:
            logger.error(f"❌ [Health] Eroare în buclă: {e}", exc_info=True)
            await asyncio.sleep(error_pause)
