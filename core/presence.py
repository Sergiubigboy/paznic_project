"""
core/presence.py — vocea lui Chronos, văzută (și atinsă) de pe ecranul Pi
===========================================================================
Ecranul rulează în browser și vorbește cu Flask, pe thread-ul lui; sesiunea
vocală trăiește în bucla asyncio din main_async.py. Modulul ăsta e puntea,
la fel ca core/lifecycle.py pentru repornire:

    main_async → register(loop, bus, live)   la pornire
    dashboard  → voice()                     ce face vocea acum (idle / listening / speaking)
    dashboard  → wake()                      atingerea promptului = ai zis „Jarvis"
    dashboard  → stop_voice()                închide sesiunea vocală curentă

Dashboard-ul pornit singur (fără main_async) vede `available: False` și
promptul de voce de pe ecran spune că vocea e oprită, în loc să promită ceva
ce nu poate face.
"""

import asyncio
import logging
import threading
import time

logger = logging.getLogger(__name__)

_lock = threading.Lock()
_loop = None
_bus = None
_live = None


def register(loop, bus, live=None) -> None:
    """`live` = GeminiLiveSession gata de folosit, sau None dacă vocea e oprită
    (fără microfon / fără Gemini)."""
    global _loop, _bus, _live
    with _lock:
        _loop, _bus, _live = loop, bus, live


def _running() -> bool:
    return _loop is not None and _loop.is_running()


def voice() -> dict:
    live = _live
    if live is None or not _running():
        return {"available": False, "state": "off", "user": "", "ai": "", "last": None}
    try:
        snap = live.snapshot()
    except Exception as e:
        logger.debug(f"[Presence] snapshot: {e}")
        return {"available": True, "state": "idle", "user": "", "ai": "", "last": None}
    snap["available"] = True
    return snap


def wake() -> bool:
    """Pornește o sesiune vocală exact ca wake word-ul. Sigur din orice thread."""
    with _lock:
        if _live is None or not _running():
            return False
        from core.event_bus import EventType
        payload = {"timestamp": time.time(), "score": 1.0, "model_name": "screen"}
        asyncio.run_coroutine_threadsafe(
            _bus.publish(EventType.WAKE_WORD_DETECTED, payload), _loop
        )
    logger.info("👆 [Presence] Sesiune vocală pornită de pe ecran.")
    return True


def stop_voice() -> bool:
    with _lock:
        if _live is None or not _running():
            return False
        _loop.call_soon_threadsafe(_live.stop_session)
    logger.info("✋ [Presence] Sesiune vocală oprită de pe ecran.")
    return True
