"""
core/lifecycle.py — repornirea lui Chronos din dashboard
=========================================================
Butonul „Repornește Chronos" din Setări rulează pe thread-ul Flask; oprirea
trebuie făcută de bucla asyncio din main_async.py. Modulul ăsta e puntea:

    main_async  → register(loop, shutdown_event)    la pornire
    dashboard   → request_restart()                  la apăsarea butonului
    main_async  → restart_requested()                după oprirea curată → os.execv

Repornirea se face în ACELAȘI proces (os.execv): merge identic pe Pi (sub
systemd, care vede același PID) și pe PC, pornit de mână din terminal.
"""

import logging
import threading

logger = logging.getLogger(__name__)

_lock = threading.Lock()
_loop = None
_event = None
_restart = False


def register(loop, shutdown_event) -> None:
    global _loop, _event
    with _lock:
        _loop, _event = loop, shutdown_event


def can_restart() -> bool:
    return _loop is not None and _loop.is_running()


def request_restart() -> bool:
    """Sigur din orice thread. False dacă Chronos nu rulează prin main_async
    (ex. dashboard pornit singur) — atunci nu avem ce reporni."""
    global _restart
    with _lock:
        if not can_restart():
            return False
        _restart = True
        logger.info("🔄 [Lifecycle] Repornire cerută din dashboard.")
        _loop.call_soon_threadsafe(_event.set)
        return True


def restart_requested() -> bool:
    return _restart
