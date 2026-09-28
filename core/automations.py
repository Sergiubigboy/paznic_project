"""
core/automations.py — Automatizări (în stilul Shortcuts de pe iPhone)
======================================================================
O automatizare = UN declanșator + condiții opționale + o LISTĂ de acțiuni
rulate în ordine. Exemplu:

    „Trezire"  — la 06:40, luni-vineri
        1. lumini: pornesc cald, fade 10 minute
        2. sunet de alarmă
        3. muzică: „ceva energic de dimineață"
        4. volum 35%

Declanșatoare:
    time         — la o oră fixă (06:40)
    sun          — la răsărit / apus, cu decalaj în minute (-30 = cu o jumătate
                   de oră înainte)
    alarm        — când sună o alarmă pusă din voce (tools/timers.py)
    music_start  — când pornește muzica prin Chronos (DJ, play, resume)
    wake_word    — când aude „Jarvis"

Condiții (pe orice automatizare): zilele săptămânii și un interval orar
(„doar între 22:00 și 06:00" — poate trece peste miezul nopții).

Acțiuni: lights, scene, music, music_control, volume, wait, ring, ac, notify.
Lista e deschisă — un senzor sau un modul nou înseamnă un declanșator sau o
acțiune nouă aici, nu un sistem nou.

Cost pe Pi: zero polling. Motorul doarme până la următorul moment programat
(plafonat la 5 minute, ca să prindă schimbări de oră/fus), iar evenimentele
vin pe bus sau prin `emit()`. Răsăritul/apusul se calculează local (ecuația
răsăritului), fără rețea.

Fișiere:
    chronos_data/automations.json        — definițiile (le scrie dashboard-ul)
    chronos_data/automations_state.json  — ultima rulare / rezultat (motorul)

CLI (testabil înainte să fie expus AI-ului):
    python -m core.automations list
    python -m core.automations sun [--days 7]
    python -m core.automations next
    python -m core.automations run "Trezire"
    python -m core.automations fire music_start     # simulează un eveniment
"""

import asyncio
import contextvars
import json
import logging
import math
import os
import threading
import time
import uuid
from datetime import date, datetime, timedelta, timezone
from typing import Optional

logger = logging.getLogger(__name__)

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.path.join(BASE_DIR, "chronos_data")
AUTOMATIONS_FILE = os.path.join(DATA_DIR, "automations.json")
STATE_FILE = os.path.join(DATA_DIR, "automations_state.json")

TRIGGERS = ("time", "sun", "alarm", "music_start", "wake_word")
EVENT_TRIGGERS = ("alarm", "music_start", "wake_word")
ACTIONS = ("lights", "scene", "music", "music_control", "volume",
           "wait", "ring", "ac", "notify")

MAX_AUTOMATIONS = 50
MAX_ACTIONS = 20
MAX_WAIT_S = 3600
MAX_FADE_S = 6000          # WLED `tt` e în zecimi de secundă, plafon 65535
# Dacă procesul pornește la 06:41 pentru o automatizare de 06:40, o mai rulăm.
# Dacă pornește la 09:00, nu — luminile de trezire trei ore mai târziu n-ajută.
GRACE_S = 120
MAX_SLEEP_S = 300
# Un wake word spus de trei ori în zece secunde nu trebuie să ruleze de trei ori.
EVENT_COOLDOWN_S = 20

# Setat cât timp rulează acțiunile unei automatizări. `asyncio.to_thread`
# copiază contextul în thread-ul de lucru, deci și MusicAgent îl vede: muzica
# pornită DE o automatizare nu declanșează automatizări „music_start" (altfel
# „muzică → pune muzică" ar fi o buclă infinită).
_in_automation: contextvars.ContextVar[bool] = contextvars.ContextVar(
    "chronos_in_automation", default=False)

_file_lock = threading.RLock()


# =============================================================================
# RĂSĂRIT / APUS — local, fără rețea
# =============================================================================

def _coords() -> tuple:
    try:
        from config import LOCATION_LAT, LOCATION_LON
        return float(LOCATION_LAT), float(LOCATION_LON)
    except Exception:
        return 46.5425, 24.5575        # Târgu Mureș


def sun_times(day: date, lat: Optional[float] = None,
              lon: Optional[float] = None) -> tuple:
    """(răsărit, apus) ca datetime-uri locale naive. (None, None) la noapte/zi
    polară — nu e cazul în România, dar funcția nu trebuie să crape.

    Ecuația răsăritului (vezi Wikipedia „Sunrise equation"), precizie de
    ~1 minut — mai mult decât suficient pentru lumini."""
    if lat is None or lon is None:
        lat, lon = _coords()
    rad, deg = math.radians, math.degrees

    n = day.toordinal() - date(2000, 1, 1).toordinal() + 0.0008
    j_star = n - lon / 360.0
    m = (357.5291 + 0.98560028 * j_star) % 360
    c = 1.9148 * math.sin(rad(m)) + 0.02 * math.sin(rad(2 * m)) + 0.0003 * math.sin(rad(3 * m))
    lam = (m + c + 180 + 102.9372) % 360
    j_transit = 2451545.0 + j_star + 0.0053 * math.sin(rad(m)) - 0.0069 * math.sin(rad(2 * lam))
    sin_d = math.sin(rad(lam)) * math.sin(rad(23.4397))
    cos_d = math.cos(math.asin(sin_d))
    cos_w = (math.sin(rad(-0.833)) - math.sin(rad(lat)) * sin_d) / (math.cos(rad(lat)) * cos_d)
    if not -1.0 <= cos_w <= 1.0:
        return None, None
    w = deg(math.acos(cos_w))

    epoch = datetime(2000, 1, 1, 12, tzinfo=timezone.utc)

    def _local(jd: float) -> datetime:
        utc = epoch + timedelta(days=jd - 2451545.0)
        return utc.astimezone().replace(tzinfo=None, microsecond=0)

    return _local(j_transit - w / 360.0), _local(j_transit + w / 360.0)


# =============================================================================
# PERSISTENȚĂ + VALIDARE
# =============================================================================

def _read_json(path: str, default):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        return default
    except Exception as e:
        logger.warning(f"⚠️ [Automatizări] {os.path.basename(path)} ilizibil: {e}")
        return default


def _write_json(path: str, data) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)          # atomic: motorul nu citește niciodată o jumătate


def load_all() -> list:
    with _file_lock:
        data = _read_json(AUTOMATIONS_FILE, {"automations": []})
    items = data.get("automations", []) if isinstance(data, dict) else []
    return [a for a in items if isinstance(a, dict) and a.get("id")]


def _save_all(items: list) -> None:
    with _file_lock:
        _write_json(AUTOMATIONS_FILE, {"automations": items})
    notify_changed()


def load_state() -> dict:
    with _file_lock:
        st = _read_json(STATE_FILE, {})
    return st if isinstance(st, dict) else {}


def _record_run(auto_id: str, ok: bool, message: str, reason: str) -> None:
    with _file_lock:
        st = _read_json(STATE_FILE, {})
        if not isinstance(st, dict):
            st = {}
        st[auto_id] = {
            "last_run": datetime.now().isoformat(timespec="seconds"),
            "ok": ok,
            "message": message[:300],
            "reason": reason,
        }
        _write_json(STATE_FILE, st)


def _hhmm(value) -> Optional[str]:
    try:
        h, m = str(value).strip().split(":")[:2]
        h, m = int(h), int(m)
    except (ValueError, AttributeError):
        return None
    if 0 <= h <= 23 and 0 <= m <= 59:
        return f"{h:02d}:{m:02d}"
    return None


def _clamp(value, lo, hi, default):
    try:
        return max(lo, min(hi, int(float(value))))
    except (TypeError, ValueError):
        return default


def _hex(value) -> Optional[str]:
    v = str(value or "").strip()
    if len(v) == 7 and v.startswith("#"):
        try:
            int(v[1:], 16)
            return v.lower()
        except ValueError:
            pass
    return None


def _normalize_trigger(t: dict) -> tuple:
    t = t if isinstance(t, dict) else {}
    kind = t.get("type")
    if kind not in TRIGGERS:
        return None, "Declanșator necunoscut."
    if kind == "time":
        at = _hhmm(t.get("at"))
        if not at:
            return None, "Ora trebuie să fie HH:MM."
        return {"type": "time", "at": at}, None
    if kind == "sun":
        ev = t.get("event") if t.get("event") in ("sunrise", "sunset") else "sunset"
        return {"type": "sun", "event": ev,
                "offset_min": _clamp(t.get("offset_min", 0), -180, 180, 0)}, None
    if kind == "alarm":
        return {"type": "alarm", "label": str(t.get("label") or "").strip()[:40]}, None
    return {"type": kind}, None


def _normalize_action(a: dict) -> tuple:
    a = a if isinstance(a, dict) else {}
    kind = a.get("type")
    if kind not in ACTIONS:
        return None, f"Acțiune necunoscută: {kind}"

    if kind == "lights":
        mode = a.get("mode") if a.get("mode") in ("on", "off", "color") else "on"
        out = {"type": "lights", "mode": mode,
               "zone": a.get("zone") if a.get("zone") in ("all", "main", "floor") else "all",
               "fade_s": _clamp(a.get("fade_s", 0), 0, MAX_FADE_S, 0)}
        if mode != "off":
            out["brightness"] = _clamp(a.get("brightness", 70), 1, 100, 70)
        if mode == "color":
            out["color"] = _hex(a.get("color")) or "#ffb46b"
        return out, None
    if kind == "scene":
        name = str(a.get("name") or "").strip()
        return ({"type": "scene", "name": name[:60]}, None) if name else (None, "Alege o scenă.")
    if kind == "music":
        prompt = str(a.get("prompt") or "").strip()
        return ({"type": "music", "prompt": prompt[:200]}, None) if prompt else \
            (None, "Scrie ce muzică să pună.")
    if kind == "music_control":
        act = a.get("action") if a.get("action") in ("pause", "resume", "next") else "pause"
        return {"type": "music_control", "action": act}, None
    if kind == "volume":
        return {"type": "volume", "percent": _clamp(a.get("percent", 40), 0, 100, 40)}, None
    if kind == "wait":
        return {"type": "wait", "seconds": _clamp(a.get("seconds", 5), 1, MAX_WAIT_S, 5)}, None
    if kind == "ring":
        return {"type": "ring"}, None
    if kind == "ac":
        return {"type": "ac", "on": bool(a.get("on", True))}, None
    text = str(a.get("text") or "").strip()
    return ({"type": "notify", "text": text[:500]}, None) if text else (None, "Scrie mesajul.")


def normalize(raw: dict) -> tuple:
    """Validează o automatizare venită din UI/CLI. Întoarce (automatizare, eroare)."""
    raw = raw if isinstance(raw, dict) else {}
    name = str(raw.get("name") or "").strip()[:40]
    if not name:
        return None, "Dă-i un nume."

    trigger, err = _normalize_trigger(raw.get("trigger"))
    if err:
        return None, err

    actions = []
    for a in (raw.get("actions") or [])[:MAX_ACTIONS]:
        act, err = _normalize_action(a)
        if err:
            return None, err
        actions.append(act)
    if not actions:
        return None, "Adaugă cel puțin o acțiune."

    days = sorted({int(d) for d in (raw.get("days") or [])
                   if str(d).lstrip("-").isdigit() and 0 <= int(d) <= 6})
    between = None
    b = raw.get("between")
    if isinstance(b, (list, tuple)) and len(b) == 2:
        start, end = _hhmm(b[0]), _hhmm(b[1])
        if start and end and start != end:
            between = [start, end]

    return {
        "id": str(raw.get("id") or f"a_{uuid.uuid4().hex[:10]}"),
        "name": name,
        "icon": (str(raw.get("icon") or "⚡").strip() or "⚡")[:4],
        "enabled": bool(raw.get("enabled", True)),
        "trigger": trigger,
        "days": days if len(days) < 7 else [],       # toate zilele = fără restricție
        "between": between,
        "actions": actions,
    }, None


def save(raw: dict) -> dict:
    auto, err = normalize(raw)
    if err:
        return {"status": "error", "message": err}
    items = load_all()
    for i, a in enumerate(items):
        if a["id"] == auto["id"]:
            auto["created_at"] = a.get("created_at")
            items[i] = auto
            break
    else:
        if len(items) >= MAX_AUTOMATIONS:
            return {"status": "error", "message": f"Maxim {MAX_AUTOMATIONS} automatizări."}
        auto["created_at"] = datetime.now().isoformat(timespec="seconds")
        items.append(auto)
    _save_all(items)
    return {"status": "ok", "id": auto["id"], "automation": auto}


def delete(auto_id: str) -> dict:
    items = load_all()
    rest = [a for a in items if a["id"] != auto_id]
    if len(rest) == len(items):
        return {"status": "error", "message": "Nu există."}
    _save_all(rest)
    return {"status": "ok"}


def set_enabled(auto_id: str, enabled: bool) -> dict:
    items = load_all()
    for a in items:
        if a["id"] == auto_id:
            a["enabled"] = bool(enabled)
            _save_all(items)
            return {"status": "ok", "enabled": a["enabled"]}
    return {"status": "error", "message": "Nu există."}


def find(query: str) -> Optional[dict]:
    """După id sau după nume (parțial, fără majuscule)."""
    q = (query or "").strip().lower()
    items = load_all()
    return (next((a for a in items if a["id"] == query), None)
            or next((a for a in items if a["name"].lower() == q), None)
            or next((a for a in items if q and q in a["name"].lower()), None))


# =============================================================================
# CONDIȚII + PROGRAMARE
# =============================================================================

def _minutes(hhmm: str) -> int:
    h, m = hhmm.split(":")
    return int(h) * 60 + int(m)


def conditions_ok(auto: dict, now: datetime) -> bool:
    days = auto.get("days") or []
    if days and now.weekday() not in days:
        return False
    between = auto.get("between")
    if between:
        start, end, cur = _minutes(between[0]), _minutes(between[1]), now.hour * 60 + now.minute
        inside = start <= cur < end if start < end else (cur >= start or cur < end)
        if not inside:
            return False
    return True


def occurrence(auto: dict, day: date) -> Optional[datetime]:
    """Momentul în care o automatizare programată ar rula în ziua dată
    (fără să țină cont de condiții). None pentru declanșatoarele pe eveniment."""
    t = auto.get("trigger") or {}
    if t.get("type") == "time":
        h, m = t["at"].split(":")
        return datetime.combine(day, datetime.min.time()).replace(hour=int(h), minute=int(m))
    if t.get("type") == "sun":
        rise, sett = sun_times(day)
        base = rise if t.get("event") == "sunrise" else sett
        return base + timedelta(minutes=t.get("offset_min", 0)) if base else None
    return None


def next_run(auto: dict, now: Optional[datetime] = None) -> Optional[datetime]:
    """Următoarea rulare programată care trece și de condiții (max 8 zile înainte)."""
    if not auto.get("enabled", True):
        return None
    now = now or datetime.now()
    for offset in range(0, 9):
        occ = occurrence(auto, now.date() + timedelta(days=offset))
        if occ and occ > now and conditions_ok(auto, occ):
            return occ
    return None


# =============================================================================
# ACȚIUNI
# =============================================================================

_music_agent = None


def _dj():
    global _music_agent
    if _music_agent is None:
        from agents.music_agent import MusicAgent
        _music_agent = MusicAgent()
    return _music_agent


def _lights_payload(action: dict) -> dict:
    tt = {"tt": action["fade_s"] * 10} if action.get("fade_s") else {}
    if action["mode"] == "off":
        return {"on": False, **tt}
    payload = {"on": True, "bri": round(action.get("brightness", 70) * 255 / 100), **tt}
    if action["mode"] == "color":
        c = action["color"]
        payload["seg"] = [{"col": [[int(c[1:3], 16), int(c[3:5], 16), int(c[5:7], 16)]]}]
    return payload


def _run_action_sync(action: dict) -> tuple:
    """Rulează o acțiune (blocant — apelat din thread). Întoarce (ok, mesaj)."""
    kind = action["type"]

    if kind == "lights":
        from config import WLED_IP_MAIN, WLED_IP_FLOOR
        from tools.scene_tools import snapshot_lights
        from tools.wled_tools import send_wled_payload
        snapshot_lights("starea de dinainte de automatizare")
        payload = _lights_payload(action)
        zone = action.get("zone", "all")
        ips = [ip for z, ip in (("main", WLED_IP_MAIN), ("floor", WLED_IP_FLOOR))
               if zone in ("all", z)]
        ok = [send_wled_payload(ip, payload) for ip in ips]
        return any(ok), "lumini " + ("ok" if all(ok) else f"{sum(ok)}/{len(ok)} zone")

    if kind == "scene":
        from tools.scene_tools import activate_scene
        r = activate_scene(action["name"])
        if r.get("status") != "ok":
            return False, r.get("message", "scenă eșuată")
        if r.get("music_prompt"):
            m = _dj().process_request(r["music_prompt"])
            return m.get("status") in ("ok", "success"), f"scena {r.get('scena')} + muzică"
        return True, f"scena {r.get('scena')}"

    if kind == "music":
        r = _dj().process_request(action["prompt"])
        return r.get("status") in ("ok", "success"), r.get("msg", "muzică")

    if kind == "music_control":
        r = _dj().control(action["action"])
        return r.get("status") in ("ok", "success"), r.get("msg") or r.get("message") or action["action"]

    if kind == "volume":
        # Imediat după „pune muzică" difuzorul abia se trezește și Spotify n-are
        # încă un dispozitiv activ. Câteva reîncercări scurte acoperă asta fără
        # să te oblige să pui manual un „așteaptă" înainte.
        from tools.spotify_api import set_volume
        r = {}
        for attempt in range(4):
            r = set_volume(action["percent"])
            if r.get("status") == "ok":
                return True, f"volum {action['percent']}%"
            time.sleep(2.5)
        return False, r.get("message", "volum eșuat")

    if kind == "ring":
        from tools.timers import get_store
        get_store().ring()
        return True, "a sunat"

    if kind == "ac":
        from tools.home_assistant import ac_control
        r = ac_control(action["on"])
        return r.get("status") == "ok", r.get("message", "AC")

    if kind == "notify":
        from tools.telegram_tools import send_telegram
        r = send_telegram(action["text"])
        return r.get("status") == "ok", r.get("message", "telegram")

    return False, f"acțiune necunoscută {kind}"


# =============================================================================
# MOTOR
# =============================================================================

class AutomationEngine:
    """Rulează în bucla asyncio a lui main_async. Dashboard-ul (alt thread)
    vorbește cu el doar prin `notify_changed()` și `run_now()`."""

    def __init__(self):
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._changed: Optional[asyncio.Event] = None
        self._running: set = set()
        self._last_event: dict = {}          # id -> monotonic, pentru cooldown

    @property
    def active(self) -> bool:
        return self._loop is not None and self._loop.is_running()

    # ── API thread-safe (apelat din web / din alte thread-uri) ──

    def wake(self) -> None:
        if self.active and self._changed is not None:
            self._loop.call_soon_threadsafe(self._changed.set)

    def emit(self, kind: str, data: Optional[dict] = None) -> None:
        if not self.active or _in_automation.get():
            return
        self._loop.call_soon_threadsafe(
            lambda: asyncio.ensure_future(self._on_event(kind, data or {})))

    def submit(self, auto: dict, reason: str) -> bool:
        if not self.active:
            return False
        asyncio.run_coroutine_threadsafe(self.run_automation(auto, reason), self._loop)
        return True

    # ── Execuție ──

    async def run_automation(self, auto: dict, reason: str) -> tuple:
        aid = auto["id"]
        if aid in self._running:
            logger.info(f"⏭️ [Automatizări] '{auto['name']}' rulează deja — sar peste.")
            return False, "rulează deja"
        self._running.add(aid)
        token = _in_automation.set(True)
        logger.info(f"⚡ [Automatizări] '{auto['name']}' pornită ({reason}).")
        results, all_ok = [], True
        try:
            for action in auto.get("actions", []):
                if action["type"] == "wait":
                    await asyncio.sleep(action["seconds"])
                    continue
                try:
                    ok, msg = await asyncio.to_thread(_run_action_sync, action)
                except Exception as e:
                    logger.error(f"❌ [Automatizări] {action['type']}: {e}", exc_info=True)
                    ok, msg = False, f"{action['type']}: {type(e).__name__}"
                all_ok &= ok
                results.append(msg if ok else f"✗ {msg}")
        finally:
            _in_automation.reset(token)
            self._running.discard(aid)
        summary = "; ".join(results) or "gata"
        _record_run(aid, all_ok, summary, reason)
        logger.info(f"{'✅' if all_ok else '⚠️'} [Automatizări] '{auto['name']}': {summary}")
        return all_ok, summary

    async def _on_event(self, kind: str, data: dict) -> None:
        now = datetime.now()
        for auto in load_all():
            t = auto.get("trigger") or {}
            if not auto.get("enabled", True) or t.get("type") != kind:
                continue
            if not conditions_ok(auto, now):
                continue
            if kind == "alarm":
                if data.get("kind") != "alarm":
                    continue                  # timerele de bucătărie nu aprind lumina
                want = (t.get("label") or "").lower()
                if want and want not in (data.get("label") or "").lower():
                    continue
            last = self._last_event.get(auto["id"], 0.0)
            if time.monotonic() - last < EVENT_COOLDOWN_S:
                continue
            self._last_event[auto["id"]] = time.monotonic()
            asyncio.ensure_future(self.run_automation(auto, kind))

    async def _scheduler(self) -> None:
        """Doarme până la următorul moment programat. Nu se trezește degeaba."""
        while True:
            now = datetime.now()
            state = load_state()
            wake_at = now + timedelta(seconds=MAX_SLEEP_S)

            for auto in load_all():
                if not auto.get("enabled", True) or auto["trigger"]["type"] not in ("time", "sun"):
                    continue
                # Și ieri: o automatizare de 23:59 are fereastra de grație după miezul nopții.
                for day in (now.date(), now.date() - timedelta(days=1)):
                    occ = occurrence(auto, day)
                    if not (occ and occ <= now < occ + timedelta(seconds=GRACE_S)
                            and conditions_ok(auto, occ)):
                        continue
                    last = (state.get(auto["id"]) or {}).get("last_run", "")
                    if last < occ.isoformat(timespec="seconds"):
                        # Scris înainte de rulare: o automatizare lungă (cu
                        # „așteaptă") nu mai e repornită la tura următoare.
                        _record_run(auto["id"], True, "pornită…", "program")
                        asyncio.ensure_future(self.run_automation(auto, "program"))
                nxt = next_run(auto, now)
                if nxt and nxt < wake_at:
                    wake_at = nxt

            delay = max(0.5, (wake_at - datetime.now()).total_seconds() + 0.2)
            self._changed.clear()
            try:
                await asyncio.wait_for(self._changed.wait(), timeout=delay)
            except asyncio.TimeoutError:
                pass

    async def _wake_word_listener(self, bus) -> None:
        from core.event_bus import EventType
        async for data in bus.subscribe(EventType.WAKE_WORD_DETECTED):
            await self._on_event("wake_word", data or {})

    async def run(self, bus=None) -> None:
        self._loop = asyncio.get_running_loop()
        self._changed = asyncio.Event()
        n = len([a for a in load_all() if a.get("enabled", True)])
        logger.info(f"⚡ [Automatizări] Motor pornit — {n} active.")
        tasks = [asyncio.ensure_future(self._scheduler())]
        if bus is not None:
            tasks.append(asyncio.ensure_future(self._wake_word_listener(bus)))
        try:
            await asyncio.gather(*tasks)
        except asyncio.CancelledError:
            pass
        finally:
            for t in tasks:
                t.cancel()
            self._loop = None


_engine: Optional[AutomationEngine] = None


def get_engine() -> AutomationEngine:
    global _engine
    if _engine is None:
        _engine = AutomationEngine()
    return _engine


def notify_changed() -> None:
    """Chemat după orice modificare a fișierului: motorul își recalculează programul."""
    get_engine().wake()


def emit(kind: str, data: Optional[dict] = None) -> None:
    """Punctul prin care modulele (alarme, muzică, senzori) anunță un eveniment.
    Sigur din orice thread; no-op dacă motorul nu rulează."""
    try:
        get_engine().emit(kind, data)
    except Exception as e:
        logger.debug(f"[Automatizări] emit({kind}): {e}")


def run_now(auto_id: str) -> dict:
    """Rulare manuală (butonul ▶ din dashboard). Dacă motorul rulează, trece prin
    el; altfel (dashboard pornit singur) rulează într-un thread propriu."""
    auto = find(auto_id)
    if not auto:
        return {"status": "error", "message": "Nu există."}
    if get_engine().submit(auto, "manual"):
        return {"status": "ok", "message": f"Am pornit „{auto['name']}”."}
    threading.Thread(target=lambda: asyncio.run(AutomationEngine().run_automation(auto, "manual")),
                     daemon=True, name="automation-manual").start()
    return {"status": "ok", "message": f"Am pornit „{auto['name']}”."}


# =============================================================================
# DESCRIERI (pentru CLI, UI și, mai târziu, pentru Chronos)
# =============================================================================

_ZILE = ("Lu", "Ma", "Mi", "Jo", "Vi", "Sâ", "Du")


def describe_trigger(auto: dict) -> str:
    t = auto.get("trigger") or {}
    kind = t.get("type")
    if kind == "time":
        s = f"la {t['at']}"
    elif kind == "sun":
        ev = "răsărit" if t.get("event") == "sunrise" else "apus"
        off = t.get("offset_min", 0)
        s = ev if not off else f"{abs(off)} min {'înainte de' if off < 0 else 'după'} {ev}"
    elif kind == "alarm":
        s = "când sună alarma" + (f" „{t['label']}”" if t.get("label") else "")
    elif kind == "music_start":
        s = "când pornește muzica"
    elif kind == "wake_word":
        s = "când aude „Jarvis”"
    else:
        s = "?"
    days = auto.get("days") or []
    if days:
        s += ", " + ("luni-vineri" if days == [0, 1, 2, 3, 4] else
                     "weekend" if days == [5, 6] else " ".join(_ZILE[d] for d in days))
    if auto.get("between"):
        s += f", între {auto['between'][0]}-{auto['between'][1]}"
    return s


def _durata(sec: int) -> str:
    m, s = divmod(int(sec), 60)
    return " ".join(p for p in (f"{m} min" if m else "", f"{s}s" if s else "") if p) or "0s"


def describe_action(a: dict) -> str:
    k = a["type"]
    if k == "lights":
        fade = f", fade {_durata(a['fade_s'])}" if a.get("fade_s") else ""
        zona = {"all": "", "main": " (sus)", "floor": " (jos)"}[a.get("zone", "all")]
        if a["mode"] == "off":
            return f"stinge luminile{zona}{fade}"
        culoare = f" {a['color']}" if a["mode"] == "color" else ""
        return f"aprinde luminile{zona}{culoare} {a.get('brightness', 70)}%{fade}"
    return {
        "scene": lambda: f"scena „{a['name']}”",
        "music": lambda: f"muzică: {a['prompt']}",
        "music_control": lambda: {"pause": "pauză muzică", "resume": "reia muzica",
                                  "next": "piesa următoare"}[a["action"]],
        "volume": lambda: f"volum {a['percent']}%",
        "wait": lambda: f"așteaptă {_durata(a['seconds'])}",
        "ring": lambda: "sunet de alarmă",
        "ac": lambda: f"AC {'pornit' if a['on'] else 'oprit'}",
        "notify": lambda: f"Telegram: {a['text'][:40]}",
    }[k]()


# =============================================================================
# CLI
# =============================================================================

def main(argv=None) -> None:
    import argparse
    import sys

    logging.basicConfig(level=logging.INFO, format="%(message)s")
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    p = argparse.ArgumentParser(prog="python -m core.automations",
                                description="Automatizările lui Chronos.")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("list", help="toate automatizările")
    s = sub.add_parser("sun", help="răsărit/apus în zilele următoare")
    s.add_argument("--days", type=int, default=7)
    sub.add_parser("next", help="ce urmează să ruleze, în ordine")
    r = sub.add_parser("run", help="rulează ACUM o automatizare (id sau nume)")
    r.add_argument("query")
    f = sub.add_parser("fire", help="simulează un eveniment și rulează ce se potrivește")
    f.add_argument("kind", choices=EVENT_TRIGGERS)
    f.add_argument("--label", default="")
    args = p.parse_args(argv)

    if args.cmd == "list":
        items = load_all()
        if not items:
            print("Nicio automatizare. Creează una din dashboard → Automatizări.")
        state = load_state()
        for a in items:
            st = state.get(a["id"]) or {}
            print(f"{'●' if a.get('enabled', True) else '○'} {a['icon']} {a['name']}  [{a['id']}]")
            print(f"    când: {describe_trigger(a)}")
            for i, act in enumerate(a["actions"], 1):
                print(f"    {i}. {describe_action(act)}")
            if st:
                print(f"    ultima: {st['last_run']} ({st.get('reason')}) — {st.get('message')}")
    elif args.cmd == "sun":
        lat, lon = _coords()
        print(f"Coordonate: {lat}, {lon}")
        for i in range(args.days):
            d = date.today() + timedelta(days=i)
            rise, sett = sun_times(d)
            print(f"  {d:%a %d.%m}   răsărit {rise:%H:%M}   apus {sett:%H:%M}")
    elif args.cmd == "next":
        rows = [(next_run(a), a) for a in load_all()]
        rows = sorted((x for x in rows if x[0]), key=lambda x: x[0])
        for when, a in rows:
            print(f"  {when:%a %d.%m %H:%M}   {a['icon']} {a['name']}")
        if not rows:
            print("Nimic programat (declanșatoarele pe eveniment nu apar aici).")
    elif args.cmd == "run":
        auto = find(args.query)
        if not auto:
            sys.exit(f"N-am găsit „{args.query}”.")
        ok, msg = asyncio.run(AutomationEngine().run_automation(auto, "cli"))
        print(("✅ " if ok else "⚠️ ") + msg)
    elif args.cmd == "fire":
        async def _fire():
            eng = get_engine()
            eng._loop = asyncio.get_running_loop()
            eng._changed = asyncio.Event()
            await eng._on_event(args.kind, {"kind": "alarm", "label": args.label})
            await asyncio.sleep(0.05)
            while eng._running:
                await asyncio.sleep(0.2)
        asyncio.run(_fire())


if __name__ == "__main__":
    main()
