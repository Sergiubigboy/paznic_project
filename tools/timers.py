"""
tools/timers.py — Timere și Alarme
====================================
Implementare proprie, deliberat independentă de Home Assistant: rămâne sub
controlul lui Chronos și merge chiar dacă HA e picat sau ești pe alt hardware.

Ce face:
    - timer relativ  („peste 10 minute", „într-o oră și jumătate")
    - alarmă absolută („mâine la 7", „la 14:30")
    - persistă pe disc → supraviețuiește unui restart al aplicației
    - la declanșare sună un semnal audio distinct și repetat

Un singur thread de supraveghere pentru toate, cu verificare o dată pe secundă
(cost neglijabil, precizie mai mult decât suficientă pentru voce).

Fișier: chronos_data/timers.json
"""

import json
import logging
import os
import threading
import time
from datetime import datetime, timedelta
from typing import Optional

logger = logging.getLogger(__name__)

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TIMERS_FILE = os.path.join(BASE_DIR, "chronos_data", "timers.json")

MAX_ACTIVE = 20          # plafon sănătos, previne acumularea la infinit
RING_SECONDS = 12        # cât sună dacă nu-l oprești
_CHECK_INTERVAL = 1.0


class TimerStore:
    """Timere persistente + thread de supraveghere. Thread-safe."""

    def __init__(self, path: str = TIMERS_FILE):
        self._path = path
        self._lock = threading.RLock()
        self._items = []
        self._watcher = None
        self._ringing = threading.Event()
        self._load()

    # ── Persistență ──

    def _load(self) -> None:
        try:
            with open(self._path, "r", encoding="utf-8") as f:
                self._items = json.load(f)
        except FileNotFoundError:
            self._items = []
        except Exception as e:
            logger.warning(f"⚠️ [Timere] Fișier ilizibil ({e}) — pornesc gol.")
            self._items = []

    def _save(self) -> None:
        try:
            os.makedirs(os.path.dirname(self._path), exist_ok=True)
            with open(self._path, "w", encoding="utf-8") as f:
                json.dump(self._items, f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.error(f"❌ [Timere] Nu pot salva: {e}")

    # ── Supraveghere ──

    def _ensure_watcher(self) -> None:
        if self._watcher and self._watcher.is_alive():
            return
        self._watcher = threading.Thread(target=self._loop, daemon=True,
                                         name="timer_watcher")
        self._watcher.start()

    def _loop(self) -> None:
        while True:
            time.sleep(_CHECK_INTERVAL)
            try:
                scadente = []
                with self._lock:
                    if not self._items:
                        return          # nimic de păzit — thread-ul se stinge
                    acum = datetime.now()
                    ramase = []
                    for it in self._items:
                        try:
                            due = datetime.fromisoformat(it["due"])
                        except Exception:
                            continue
                        (scadente if due <= acum else ramase).append(it)
                    if scadente:
                        self._items = ramase
                        self._save()
                for it in scadente:
                    self._fire(it)
            except Exception as e:
                logger.error(f"❌ [Timere] Eroare în supraveghere: {e}")

    def _fire(self, item: dict) -> None:
        eticheta = item.get("label") or ("alarmă" if item.get("kind") == "alarm" else "timer")
        logger.info(f"⏰ [Timere] A SUNAT: {eticheta}")
        print(f"\n⏰ ══ {eticheta.upper()} ══\n")
        threading.Thread(target=self._ring, daemon=True).start()

    def _ring(self) -> None:
        """Semnal sonor repetat, distinct de beep-ul de wake word."""
        if self._ringing.is_set():
            return                      # sună deja ceva, nu suprapunem
        self._ringing.set()
        try:
            import numpy as np
            import sounddevice as sd

            rate = 44100
            bip = 0.16
            t = np.linspace(0, bip, int(rate * bip), endpoint=False)
            # două tonuri alternate — se disting clar de orice altceva
            ton_a = np.sin(2 * np.pi * 880 * t) * 0.35
            ton_b = np.sin(2 * np.pi * 1320 * t) * 0.35
            fade = int(rate * 0.01)
            for ton in (ton_a, ton_b):
                ton[:fade] *= np.linspace(0, 1, fade)
                ton[-fade:] *= np.linspace(1, 0, fade)
            pauza = np.zeros(int(rate * 0.1), dtype=np.float32)
            grup = np.concatenate([ton_a, pauza, ton_b, pauza,
                                   ton_a, pauza, np.zeros(int(rate * 0.6))])

            capat = time.time() + RING_SECONDS
            while time.time() < capat:
                sd.play(grup.astype(np.float32), rate)
                sd.wait()
        except Exception as e:
            logger.debug(f"[Timere] Nu pot suna (non-critic): {e}")
        finally:
            self._ringing.clear()

    def stop_ringing(self) -> None:
        try:
            import sounddevice as sd
            sd.stop()
        except Exception:
            pass
        self._ringing.clear()

    # ── API public ──

    def add(self, due: datetime, label: str = "", kind: str = "timer") -> dict:
        if due <= datetime.now():
            return {"status": "error", "message": "Momentul ăla a trecut deja."}

        with self._lock:
            if len(self._items) >= MAX_ACTIVE:
                return {"status": "error",
                        "message": f"Ai deja {MAX_ACTIVE} timere active, prea multe."}
            item = {
                "id": f"t_{int(time.time() * 1000)}",
                "due": due.isoformat(timespec="seconds"),
                "label": (label or "").strip(),
                "kind": kind,
                "created_at": datetime.now().isoformat(timespec="seconds"),
            }
            self._items.append(item)
            self._items.sort(key=lambda x: x["due"])
            self._save()
        self._ensure_watcher()

        ramas = due - datetime.now()
        logger.info(f"⏱️ [Timere] Setat: {label or kind} la {due:%H:%M:%S}")
        return {"status": "ok", "id": item["id"],
                "message": f"Gata, {_descrie(item, ramas)}."}

    def list(self) -> dict:
        with self._lock:
            self._items.sort(key=lambda x: x["due"])
            items = list(self._items)
        if not items:
            return {"status": "ok", "active": 0, "message": "Nu ai niciun timer activ."}

        acum = datetime.now()
        randuri = []
        for it in items:
            try:
                ramas = datetime.fromisoformat(it["due"]) - acum
            except Exception:
                continue
            randuri.append(_descrie(it, ramas))
        return {"status": "ok", "active": len(randuri),
                "message": "Ai: " + "; ".join(randuri) + "."}

    def cancel(self, query: str = "") -> dict:
        with self._lock:
            if not self._items:
                return {"status": "ok", "message": "N-ai niciun timer de anulat."}

            q = (query or "").lower().strip()
            if not q or q in ("tot", "toate", "all"):
                n = len(self._items)
                self._items = []
                self._save()
                self.stop_ringing()
                return {"status": "ok",
                        "message": f"Am anulat {'tot' if n == 1 else f'toate {n}'}."}

            # potrivire după etichetă; fără potrivire → cel mai apropiat
            gasit = next((i for i in self._items if q in (i.get("label", "") or "").lower()),
                         None) or self._items[0]
            self._items.remove(gasit)
            self._save()
        self.stop_ringing()
        eticheta = gasit.get("label") or gasit.get("kind")
        return {"status": "ok", "message": f"Am anulat {eticheta}."}


def _descrie(item: dict, ramas: timedelta) -> str:
    eticheta = item.get("label") or ("alarma" if item.get("kind") == "alarm" else "timerul")
    s = max(0, int(ramas.total_seconds()))
    if item.get("kind") == "alarm":
        due = datetime.fromisoformat(item["due"])
        cand = "azi" if due.date() == datetime.now().date() else due.strftime("pe %d.%m")
        return f"{eticheta} {cand} la {due:%H:%M}"
    if s >= 3600:
        return f"{eticheta} peste {s // 3600}h {(s % 3600) // 60}m"
    if s >= 60:
        return f"{eticheta} peste {s // 60} min"
    return f"{eticheta} peste {s} secunde"


_store: Optional[TimerStore] = None


def get_store() -> TimerStore:
    global _store
    if _store is None:
        _store = TimerStore()
    return _store


# ── Funcții expuse ca tool ──

def set_timer(minutes: float = 0, seconds: float = 0, hours: float = 0,
              label: str = "") -> dict:
    """Timer relativ: peste X ore/minute/secunde."""
    total = (hours or 0) * 3600 + (minutes or 0) * 60 + (seconds or 0)
    if total <= 0:
        return {"status": "error", "message": "N-ai zis cât să dureze."}
    if total > 24 * 3600:
        return {"status": "error", "message": "Peste 24 de ore folosește o alarmă."}
    return get_store().add(datetime.now() + timedelta(seconds=total), label, "timer")


def set_alarm(hour: int, minute: int = 0, label: str = "") -> dict:
    """Alarmă la o oră anume. Dacă ora a trecut azi, se pune pe mâine."""
    try:
        hour, minute = int(hour), int(minute or 0)
    except (TypeError, ValueError):
        return {"status": "error", "message": "Oră invalidă."}
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        return {"status": "error", "message": "Ora trebuie să fie între 00:00 și 23:59."}

    acum = datetime.now()
    due = acum.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if due <= acum:
        due += timedelta(days=1)
    return get_store().add(due, label, "alarm")


def list_timers() -> dict:
    return get_store().list()


def cancel_timer(query: str = "") -> dict:
    return get_store().cancel(query)
