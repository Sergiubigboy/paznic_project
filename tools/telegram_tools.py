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

_API = "https://api.telegram.org/bot{token}/sendMessage"
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


def send_telegram(text: str) -> dict:
    """
    Trimite un mesaj pe Telegram. Apelat DOAR când Sergiu cere explicit.

    Returnează un dict cu status, ca modelul să poată confirma sau explica
    de ce n-a mers.
    """
    text = (text or "").strip()
    if not text:
        return {"status": "error", "message": "N-ai zis ce mesaj să trimit."}

    token, chat_id = _creds()
    if not token or not chat_id:
        logger.warning("⚠️ [Telegram] Neconfigurat (lipsesc TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID).")
        return {
            "status": "error",
            "message": "Telegram nu e configurat încă — lipsesc datele botului din .env.",
        }

    try:
        resp = requests.post(
            _API.format(token=token),
            json={"chat_id": chat_id, "text": text, "disable_notification": False},
            timeout=_TIMEOUT,
        )
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
