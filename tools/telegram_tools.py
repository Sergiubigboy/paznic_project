"""
tools/telegram_tools.py — Notificări Telegram
===============================================
Canal prin care Chronos îți poate trimite ceva pe telefon când nu ești lângă boxe.

STARE ACTUALĂ: NU trimite nimic automat. Se activează exclusiv când îi ceri
tu explicit („trimite-mi pe Telegram lista de cumpărături"). Infrastructura e
gata pentru notificări proactive de mai târziu — vezi `notify()`, care e
scrisă special pentru asta, dar nu e apelată de nimic încă.

Configurare (în .env):
    TELEGRAM_BOT_TOKEN=...   # de la @BotFather
    TELEGRAM_CHAT_ID=...     # id-ul tău de chat

Fără cele două variabile, tool-ul raportează curat că nu e configurat —
nu crapă și nu blochează nimic.
"""

import logging
import os

import requests

logger = logging.getLogger(__name__)

# Sesiune partajată — evită un handshake TLS complet la fiecare mesaj.
_session = requests.Session()
_session.mount("https://", requests.adapters.HTTPAdapter(pool_connections=1, pool_maxsize=2))

_API = "https://api.telegram.org/bot{token}/sendMessage"
_API_UPDATES = "https://api.telegram.org/bot{token}/getUpdates"
_TIMEOUT = 10


def _creds():
    try:
        from config import TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID
        return TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID
    except ImportError:
        return (os.environ.get("TELEGRAM_BOT_TOKEN", ""),
                os.environ.get("TELEGRAM_CHAT_ID", ""))


def is_configured() -> bool:
    token, chat_id = _creds()
    return bool(token and chat_id)


def send_telegram(text: str, token: str = None, markdown: bool = False) -> dict:
    """
    Trimite un mesaj pe Telegram. Apelat DOAR când Sergiu cere explicit.

    `token` = alt bot decât cel principal (ex. botul Health); chat-ul privat
    are același id cu orice bot. `markdown` = *bold* etc.; dacă Telegram
    refuză formatarea, mesajul pleacă oricum ca text simplu.

    Returnează un dict cu status, ca modelul să poată confirma sau explica
    de ce n-a mers.
    """
    text = (text or "").strip()
    if not text:
        return {"status": "error", "message": "N-ai zis ce mesaj să trimit."}

    main_token, chat_id = _creds()
    token = token or main_token
    if not token or not chat_id:
        logger.warning("⚠️ [Telegram] Neconfigurat (lipsesc TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID).")
        return {
            "status": "error",
            "message": "Telegram nu e configurat încă — lipsesc datele botului din .env.",
        }

    try:
        payload = {"chat_id": chat_id, "text": text, "disable_notification": False}
        if markdown:
            payload["parse_mode"] = "Markdown"
        resp = _session.post(_API.format(token=token), json=payload, timeout=_TIMEOUT)
        if resp.status_code == 400 and markdown:
            payload.pop("parse_mode")          # formatare stricată → text simplu
            resp = _session.post(_API.format(token=token), json=payload, timeout=_TIMEOUT)
        if resp.status_code == 200:
            logger.info(f"📨 [Telegram] Trimis: '{text[:60]}'")
            return {"status": "ok", "message": "Ți-am trimis pe Telegram."}

        detail = resp.text[:150]
        logger.error(f"❌ [Telegram] HTTP {resp.status_code}: {detail}")
        return {"status": "error", "message": f"Telegram a refuzat (HTTP {resp.status_code})."}
    except Exception as e:
        logger.error(f"❌ [Telegram] Trimitere eșuată: {e}")
        return {"status": "error", "message": f"Nu am putut trimite: {e}"}


def notify(title: str, body: str = "") -> dict:
    """
    Notificare formatată, pregătită pentru viitoarele alerte proactive
    (deadline-uri, mentenanță scadentă, alerte de sistem).

    NU e apelată automat de nimic în momentul ăsta — există ca punctul unic
    prin care se vor trimite alertele când activăm partea proactivă.
    """
    if not is_configured():
        return {"status": "skipped", "message": "Telegram neconfigurat."}
    mesaj = f"🤖 {title}" + (f"\n\n{body}" if body else "")
    return send_telegram(mesaj)


# ─────────────────────────────────────────────────────────────
# RECEPȚIE — Telegram ca telecomandă pentru programul zilei
# ─────────────────────────────────────────────────────────────

def get_updates(offset: int = 0, timeout: int = 25, token: str = None) -> list:
    """
    Citește mesajele noi (long polling — conexiunea stă deschisă până apare
    ceva sau expiră timeout-ul, deci nu batem serverul degeaba).

    Întoarce [{update_id, text, chat_id}], doar de la chat-ul configurat.
    `token` = alt bot decât cel principal (fiecare bot are coada lui).
    """
    main_token, chat_id = _creds()
    token = token or main_token
    if not token or not chat_id:
        return []

    try:
        resp = _session.get(
            _API_UPDATES.format(token=token),
            params={"offset": offset, "timeout": timeout,
                    "allowed_updates": '["message"]'},
            timeout=timeout + 10,
        )
        if resp.status_code != 200:
            logger.debug(f"[Telegram] getUpdates HTTP {resp.status_code}")
            return []
        date = resp.json().get("result", [])
    except requests.exceptions.Timeout:
        return []          # normal la long polling
    except Exception as e:
        logger.debug(f"[Telegram] getUpdates: {e}")
        return []

    mesaje = []
    for u in date:
        msg = u.get("message") or {}
        text = (msg.get("text") or "").strip()
        cid = str((msg.get("chat") or {}).get("id", ""))
        if text and cid == str(chat_id):
            mesaje.append({"update_id": u.get("update_id"), "text": text, "chat_id": cid})
    return mesaje
