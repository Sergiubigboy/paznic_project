"""
tools/wled_tools.py — WLED Hardware Tools
===========================================
Funcții directe pentru controlul benzilor WLED prin API HTTP JSON.
"""

import requests
import logging
from concurrent.futures import ThreadPoolExecutor
from config import WLED_IP_MAIN, WLED_IP_FLOOR

logger = logging.getLogger(__name__)


def send_wled_payload(ip: str, payload: dict, timeout: int = 3) -> bool:
    """Trimite un payload JSON către un dispozitiv WLED."""
    url = f"http://{ip}/json/state"
    try:
        resp = requests.post(url, json=payload, timeout=timeout)
        if resp.status_code == 200:
            logger.info(f"💡 [WLED Tools] {ip} OK: {payload}")
            return True
        else:
            logger.error(f"❌ [WLED Tools] {ip} error {resp.status_code}: {resp.text}")
            return False
    except Exception as e:
        logger.error(f"❌ [WLED Tools] Conexiune eșuată la {ip}: {e}")
        return False


def set_all_leds(r: int, g: int, b: int, brightness: int = 150, turn_off: bool = False) -> dict:
    """Setează ambele zone WLED (Main și Floor) la o culoare/stare dată."""
    if turn_off:
        payload = {"on": False}
    else:
        payload = {
            "on": True,
            "bri": max(0, min(255, brightness)),
            "seg": [{"col": [[r, g, b]]}]
        }

    results = {}
    with ThreadPoolExecutor(max_workers=2) as executor:
        f1 = executor.submit(send_wled_payload, WLED_IP_MAIN, payload)
        f2 = executor.submit(send_wled_payload, WLED_IP_FLOOR, payload)
        results[WLED_IP_MAIN] = f1.result()
        results[WLED_IP_FLOOR] = f2.result()

    return {"status": "ok", "results": results, "rgb": (r, g, b), "brightness": brightness}


def set_dual_zone_leds(main_payload: dict, floor_payload: dict) -> dict:
    """Setează stări diferite pentru zona de sus (Main) și de jos (Floor)."""
    results = {}
    with ThreadPoolExecutor(max_workers=2) as executor:
        f1 = executor.submit(send_wled_payload, WLED_IP_MAIN, main_payload)
        f2 = executor.submit(send_wled_payload, WLED_IP_FLOOR, floor_payload)
        results[WLED_IP_MAIN] = f1.result()
        results[WLED_IP_FLOOR] = f2.result()

    return {"status": "ok", "results": results}
