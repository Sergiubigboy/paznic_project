"""
tools/wled_tools.py — WLED Hardware Tools
===========================================
Control direct al benzilor WLED prin API-ul lor HTTP JSON.

Două lucruri contau aici pentru cât de repede se aprind luminile:

1. CONEXIUNI REFOLOSITE. Fiecare comandă deschidea o conexiune TCP nouă către
   fiecare controller. Pe LAN handshake-ul e ieftin, dar nu gratis — și se
   plătea de două ori la fiecare comandă. O `Session` cu keep-alive ține
   socketul deschis între comenzi.

2. THREADURI REFOLOSITE. `with ThreadPoolExecutor(...)` însemna crearea și
   distrugerea a două threaduri la FIECARE comandă de lumini. Acum pool-ul e
   creat o dată, la import, și trăiește cât procesul.

Ambele zone sunt lovite în paralel: comanda ajunge la ele practic simultan,
nu una după alta.
"""

import atexit
import logging
from concurrent.futures import ThreadPoolExecutor
from typing import Tuple

import requests
from requests.adapters import HTTPAdapter

from config import WLED_IP_MAIN, WLED_IP_FLOOR

logger = logging.getLogger(__name__)

DEFAULT_TIMEOUT = 3

# Un controller WLED care tocmai s-a trezit din Wi-Fi sleep ratează primul
# pachet destul de des. O reîncercare rapidă e diferența dintre „luminile nu
# s-au aprins" și o întârziere de câteva zeci de ms pe care n-o observi.
def _build_session() -> requests.Session:
    session = requests.Session()
    try:
        from urllib3.util.retry import Retry
        kwargs = dict(total=1, connect=1, read=1, backoff_factor=0.15,
                      status_forcelist=(500, 502, 503, 504), raise_on_status=False)
        try:
            retry = Retry(allowed_methods=frozenset({"GET", "POST"}), **kwargs)
        except TypeError:                      # urllib3 < 1.26
            retry = Retry(method_whitelist=frozenset({"GET", "POST"}), **kwargs)
    except ImportError:
        retry = None
    adapter = HTTPAdapter(pool_connections=2, pool_maxsize=4, max_retries=retry)
    session.mount("http://", adapter)
    session.headers["Connection"] = "keep-alive"
    return session


_session = _build_session()
# Două zone → exact doi workeri. Creat o dată, nu per comandă.
_pool = ThreadPoolExecutor(max_workers=2, thread_name_prefix="wled")


@atexit.register
def _cleanup() -> None:
    _pool.shutdown(wait=False)
    try:
        _session.close()
    except Exception:
        pass


def send_wled_payload(ip: str, payload: dict, timeout: int = DEFAULT_TIMEOUT) -> bool:
    """Trimite un payload JSON către un dispozitiv WLED."""
    if not ip:
        logger.warning("⚠️ [WLED Tools] IP nesetat — comandă ignorată.")
        return False
    try:
        resp = _session.post(
            f"http://{ip}/json/state", json=payload, timeout=timeout
        )
    except requests.Timeout:
        logger.error(f"❌ [WLED Tools] {ip} nu răspunde (timeout {timeout}s).")
        return False
    except requests.RequestException as e:
        logger.error(f"❌ [WLED Tools] Conexiune eșuată la {ip}: {e}")
        return False

    if resp.status_code == 200:
        logger.info(f"💡 [WLED Tools] {ip} OK: {payload}")
        return True
    logger.error(f"❌ [WLED Tools] {ip} error {resp.status_code}: {resp.text[:120]}")
    return False


def _send_both(main_payload: dict, floor_payload: dict) -> dict:
    """Trimite în paralel către ambele zone și raportează per zonă."""
    f_main = _pool.submit(send_wled_payload, WLED_IP_MAIN, main_payload)
    f_floor = _pool.submit(send_wled_payload, WLED_IP_FLOOR, floor_payload)
    return {WLED_IP_MAIN: f_main.result(), WLED_IP_FLOOR: f_floor.result()}


def _status(results: dict) -> str:
    """`ok` doar dacă a răspuns cel puțin o zonă — altfel apelantul (și
    Chronos) credea că s-a executat comanda deși nu s-a aprins nimic."""
    return "ok" if any(results.values()) else "error"


def set_all_leds(r: int, g: int, b: int, brightness: int = 150,
                 turn_off: bool = False) -> dict:
    """Setează ambele zone WLED (Main și Floor) la o culoare/stare dată."""
    if turn_off:
        payload = {"on": False}
    else:
        payload = {
            "on": True,
            "bri": max(0, min(255, brightness)),
            "seg": [{"col": [[r, g, b]]}],
        }

    results = _send_both(payload, payload)
    return {
        "status": _status(results),
        "results": results,
        "rgb": (r, g, b),
        "brightness": brightness,
    }


def set_dual_zone_leds(main_payload: dict, floor_payload: dict) -> dict:
    """Setează stări diferite pentru zona de sus (Main) și de jos (Floor)."""
    results = _send_both(main_payload, floor_payload)
    return {"status": _status(results), "results": results}
