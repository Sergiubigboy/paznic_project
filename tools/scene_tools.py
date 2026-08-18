"""
tools/scene_tools.py — Scene Salvate + Undo
=============================================
Scenele (`chronos_data/scenes.json`) combină o stare de lumini (payload WLED
complet, per zonă) cu un prompt de muzică. Erau create din dashboard, dar nu
puteau fi activate pe voce — asta rezolvă modulul ăsta.

Bonus: fiecare schimbare de lumini trece prin `snapshot_lights()`, ca „anulează"
să poată reveni la starea de dinainte. Snapshot-ul se ia din WLED-uri (starea
REALĂ), nu din ce credem noi că am setat.
"""

import json
import logging
import os
from datetime import datetime
from difflib import SequenceMatcher

import requests

from config import WLED_IP_MAIN, WLED_IP_FLOOR
from tools.wled_tools import set_dual_zone_leds

logger = logging.getLogger(__name__)

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.path.join(BASE_DIR, "chronos_data")
SCENES_FILE = os.path.join(DATA_DIR, "scenes.json")
UNDO_FILE = os.path.join(DATA_DIR, "last_light_state.json")

# Cheile de stare pe care le păstrăm dintr-un răspuns WLED (restul e telemetrie)
_KEEP_STATE = ("on", "bri", "transition", "mainseg")
_KEEP_SEG = ("id", "col", "fx", "sx", "ix", "pal", "bri", "on", "start", "stop")


def _load_scenes() -> list:
    try:
        with open(SCENES_FILE, "r", encoding="utf-8") as f:
            return json.load(f).get("scenes", [])
    except FileNotFoundError:
        return []
    except Exception as e:
        logger.warning(f"⚠️ [Scene Tools] Nu pot citi scenele: {e}")
        return []


def list_scenes() -> dict:
    """Scenele disponibile, pentru ca modelul să știe ce poate cere."""
    scenes = _load_scenes()
    if not scenes:
        return {"status": "ok", "scene": [], "message": "Nu ai nicio scenă salvată."}
    return {
        "status": "ok",
        "scene": [
            {"nume": s.get("name"), "muzica": s.get("music_prompt") or "fără muzică"}
            for s in scenes
        ],
    }


def _fetch_zone_state(ip: str) -> dict:
    """Citește starea reală a unui WLED, păstrând doar ce e necesar la restaurare."""
    try:
        resp = requests.get(f"http://{ip}/json/state", timeout=2)
        resp.raise_for_status()
        raw = resp.json()
    except Exception as e:
        logger.debug(f"[Scene Tools] Nu pot citi starea de la {ip}: {e}")
        return {}

    state = {k: raw[k] for k in _KEEP_STATE if k in raw}
    segs = []
    for seg in raw.get("seg", []):
        if seg.get("stop", 0) == 0 and seg.get("start", 0) == 0:
            continue  # segment inactiv
        segs.append({k: seg[k] for k in _KEEP_SEG if k in seg})
    if segs:
        state["seg"] = segs
    return state


def snapshot_lights(eticheta: str = "") -> bool:
    """
    Salvează starea CURENTĂ a luminilor, ca să poată fi restaurată cu undo.
    Apelat înainte de orice schimbare de lumini.
    """
    snap = {
        "main": _fetch_zone_state(WLED_IP_MAIN),
        "floor": _fetch_zone_state(WLED_IP_FLOOR),
        "eticheta": eticheta,
        "salvat_la": datetime.now().isoformat(),
    }
    if not snap["main"] and not snap["floor"]:
        return False
    try:
        os.makedirs(DATA_DIR, exist_ok=True)
        with open(UNDO_FILE, "w", encoding="utf-8") as f:
            json.dump(snap, f, ensure_ascii=False, indent=2)
        logger.debug(f"📸 [Scene Tools] Snapshot lumini salvat ({eticheta}).")
        return True
    except Exception as e:
        logger.warning(f"⚠️ [Scene Tools] Nu pot salva snapshot-ul: {e}")
        return False


def undo_lights() -> dict:
    """Revine la starea luminilor dinaintea ultimei schimbări."""
    try:
        with open(UNDO_FILE, "r", encoding="utf-8") as f:
            snap = json.load(f)
    except FileNotFoundError:
        return {"status": "error", "message": "N-am nicio stare anterioară salvată."}
    except Exception as e:
        return {"status": "error", "message": f"Nu pot citi starea anterioară: {e}"}

    main, floor = snap.get("main"), snap.get("floor")
    if not main and not floor:
        return {"status": "error", "message": "Starea anterioară e goală."}

    set_dual_zone_leds(main or {"on": True}, floor or {"on": True})
    eticheta = snap.get("eticheta") or "starea anterioară"
    logger.info(f"↩️ [Scene Tools] Undo lumini → {eticheta}")
    return {"status": "ok", "message": f"Am revenit la {eticheta}."}


def activate_scene(name: str) -> dict:
    """
    Activează o scenă salvată: aplică luminile imediat și întoarce prompt-ul de
    muzică, pe care apelantul îl trimite mai departe către agentul DJ.
    """
    scenes = _load_scenes()
    if not scenes:
        return {"status": "error", "message": "Nu ai nicio scenă salvată."}

    query = (name or "").lower().strip()
    best, best_score = None, 0.0
    for s in scenes:
        nume = str(s.get("name", "")).lower()
        score = 1.0 if (query and (query in nume or nume in query)) else \
            SequenceMatcher(None, query, nume).ratio()
        if score > best_score:
            best, best_score = s, score

    if not best or best_score < 0.4:
        disponibile = ", ".join(s.get("name", "?") for s in scenes)
        return {"status": "error",
                "message": f"N-am găsit scena '{name}'. Am: {disponibile}."}

    # Salvăm starea curentă ÎNAINTE de a o schimba (pentru „anulează")
    snapshot_lights(f"starea de dinainte de scena '{best.get('name')}'")

    lights = best.get("lights", {})
    main = lights.get("main")
    floor = lights.get("floor")
    if main or floor:
        set_dual_zone_leds(main or {"on": True}, floor or {"on": True})

    logger.info(f"🎬 [Scene Tools] Scenă activată: {best.get('name')}")
    return {
        "status": "ok",
        "scena": best.get("name"),
        "music_prompt": best.get("music_prompt") or "",
        "message": f"Am pornit scena {best.get('name')}.",
    }
