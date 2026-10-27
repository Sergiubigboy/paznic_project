"""
core/console_bridge.py — consola fizică (Pico pe USB) ↔ Chronos
=================================================================
Pico-ul din carcasă face tot ce e fizic: cele două ecrane mici, meniul,
tastele, encoderul, sliderul, LED-urile, butonul de pornire. Pi-ul îi dă
doar date și execută acțiuni — prin ACELEAȘI funcții pe care le folosesc
site-ul și vocea, ca logica să nu existe de două ori.

Protocol: câte un obiect JSON pe linie, pe USB serial (vezi console/README.md).

    Pi → Pico   {"t":"hb","h":13,"m":5,"s":"idle","mood":"8b7aff","al":0}   1/s
                {"t":"lvl","s":"speaking","a":420}                        20/s în sesiune
                {"t":"list","v":"rem","title":"REMINDERE","items":[{"id","x","d","n"}]}
                {"t":"music",…}  {"t":"lights",…}
                {"t":"ok","x":"…"}  {"t":"err","x":"…"}
    Pico → Pi   {"t":"hello","fw":"0.1"}
                {"t":"get","v":"rem|plan|targets|scenes|music|lights"}
                {"t":"do","a":"…","id":"…","n":0}
                {"t":"focus","v":"rem","sel":2}   (v="home" → nimic selectat)

Textul spre Pico pleacă fără diacritice: fontul de pe TFT e ASCII.
Fără pyserial sau fără Pico conectat, bridge-ul doar așteaptă — Chronos
merge exact ca înainte.
"""

import asyncio
import json
import logging
import os
import subprocess
import threading
import time
import unicodedata
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime

from core import presence

logger = logging.getLogger(__name__)

PICO_VID = 0x2E8A                    # Raspberry Pi (Pico / RP2040)
HEARTBEAT_S = 1.0
LEVEL_HZ = 20
ID_MAX = 47                          # Item::id pe Pico are 48 de octeți
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.path.join(BASE_DIR, "chronos_data")

_TITLES = {"rem": "REMINDERE", "plan": "ZIUA DE AZI", "targets": "TARGETS", "scenes": "SCENE"}
_PUNCT = str.maketrans({"„": '"', "”": '"', "“": '"', "’": "'", "‘": "'", "–": "-", "—": "-",
                        "…": "...", "·": "-", "→": "->", "°": "o"})


def ascii_text(s, limit: int = 60) -> str:
    """„Plătește țânțarul” → "Plateste tantarul". ș/ț/ă se descompun în literă +
    semn (NFKD), iar semnul cade; restul de non-ASCII (emoji) dispare."""
    s = unicodedata.normalize("NFKD", str(s or "").translate(_PUNCT))
    s = "".join(c for c in s if ord(c) < 128 and not unicodedata.combining(c))
    return " ".join(s.split())[:limit]


def _load(name: str, default):
    try:
        with open(os.path.join(DATA_DIR, name), "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default


def _save(name: str, data) -> None:
    os.makedirs(DATA_DIR, exist_ok=True)
    with open(os.path.join(DATA_DIR, name), "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


class ConsoleBridge:
    def __init__(self):
        self._lists = {}         # ultima listă trimisă, per view — pentru ecranul mare
        self._serial = None
        self._write_lock = threading.Lock()
        self._writer = ThreadPoolExecutor(max_workers=1, thread_name_prefix="console-tx")
        self._mood = ("8b7aff", 0.0)
        self._music_agent = None
        self.connected = False

    # ═════════════════════════════════════════════════════════════
    # DATE — liste pentru meniul de pe TFT
    # ═════════════════════════════════════════════════════════════

    def list_rem(self) -> list:
        rem = [r for r in _load("reminders.json", {}).get("reminders", []) if not r.get("checked")]
        order = {"High": 0, "Med": 1, "Low": 2}
        rem.sort(key=lambda r: order.get(r.get("priority"), 1))
        return [{"id": r["id"], "x": r.get("title", ""), "d": 0,
                 "n": 2 - order.get(r.get("priority"), 1)} for r in rem]

    def list_plan(self) -> list:
        from tools import day_planner as dp
        zi = dp.load_day()
        gata = {i.get("id"): bool(i.get("gata")) for i in zi.get("iteme", [])}
        if zi.get("cu_program") and zi.get("program"):
            return [{"id": b.get("titlu"), "x": f"{b.get('start', '')} {b.get('titlu', '')}",
                     "d": int(gata.get(b.get("item_id"), False))}
                    for b in zi["program"] if b.get("gen", "task") == "task"]
        return [{"id": i.get("titlu"), "x": i.get("titlu", ""), "d": int(bool(i.get("gata")))}
                for i in zi.get("iteme", [])]

    def list_targets(self) -> list:
        goals = _load("targets.json", {}).get("goals", [])
        return [{"id": g.get("title"), "x": g.get("title", ""), "d": int((g.get("progress") or 0) >= 100),
                 "n": int(g.get("progress") or 0)} for g in goals]

    def list_scenes(self) -> list:
        return [{"id": s.get("name"), "x": s.get("name", ""), "d": 0}
                for s in _load("scenes.json", {}).get("scenes", [])]

    def music(self) -> dict:
        from tools.spotify_api import playback_snapshot
        m = playback_snapshot()
        if m.get("status") != "ok":
            return {"t": "music", "ok": 0, "x": "spotify neconectat"}
        return {"t": "music", "ok": 1, "track": ascii_text(m.get("track"), 40),
                "artist": ascii_text(m.get("artist"), 40), "play": int(bool(m.get("playing"))),
                "vol": m.get("volume") if m.get("volume") is not None else -1,
                "pos": int((m.get("progress_ms") or 0) / 1000), "dur": int((m.get("duration_ms") or 0) / 1000)}

    def lights(self) -> dict:
        from web.web_dashboard import _fetch_wled_zones
        zones = [z for z in _fetch_wled_zones() if z]
        if not zones:
            return {"t": "lights", "ok": 0}
        on = [z for z in zones if z.get("on")]
        bri = max((z.get("bri") or 0) for z in on) if on else 0
        return {"t": "lights", "ok": 1, "on": int(bool(on)), "bri": round(bri * 100 / 255)}

    # ═════════════════════════════════════════════════════════════
    # ACȚIUNI
    # ═════════════════════════════════════════════════════════════

    def _dj(self):
        """Agentul de muzică al lui Chronos (memoria DJ-ului vede și ce sari
        de pe consolă); pornit fără Chronos, își face unul propriu."""
        try:
            from web.web_dashboard import shared_dispatcher
            return shared_dispatcher.music_agent
        except Exception:
            if self._music_agent is None:
                from agents.music_agent import MusicAgent
                self._music_agent = MusicAgent()
            return self._music_agent

    def do(self, a: str, id_: str, n) -> dict:
        """Execută o acțiune. Întoarce {"ok": bool, "x": mesaj, "refresh": view}."""
        if a == "rem_check":
            data = _load("reminders.json", {"reminders": []})
            rem = next((r for r in data.get("reminders", []) if r.get("id") == id_), None)
            if not rem:
                return {"ok": False, "x": "reminderul nu mai exista"}
            rem["checked"] = True
            rem["last_checked"] = datetime.now().isoformat()
            _save("reminders.json", data)
            return {"ok": True, "x": "bifat: " + rem.get("title", ""), "refresh": "rem"}

        if a == "plan_done":
            from tools import day_planner as dp
            r = dp.complete(id_)
            return {"ok": r.get("status") == "ok", "x": r.get("message", ""), "refresh": "plan"}

        if a == "target_set":
            from tools.data_write_tools import update_target_progress
            r = update_target_progress(id_, int(n or 0))
            return {"ok": r.get("status") == "ok", "x": r.get("message", ""), "refresh": "targets"}

        if a == "scene":
            from tools.scene_tools import activate_scene
            r = activate_scene(id_)
            if r.get("status") == "ok" and r.get("music_prompt"):
                threading.Thread(target=self._dj().process_request, args=(r["music_prompt"],),
                                 daemon=True, name="console-scene-dj").start()
            return {"ok": r.get("status") == "ok", "x": r.get("message", ""), "refresh": "lights"}

        if a == "music":
            if id_ not in ("play", "pause", "next", "previous", "set_volume", "like", "dislike"):
                return {"ok": False, "x": "actiune necunoscuta"}
            r = self._dj().control(id_, n)
            return {"ok": r.get("status", "ok") != "error", "x": r.get("msg") or r.get("message") or id_,
                    "refresh": "music"}

        if a == "lights":
            from tools.wled_tools import set_dual_zone_leds
            if id_ == "bri":
                pct = max(1, min(100, int(n or 0)))
                payload = {"on": True, "bri": round(pct * 255 / 100)}
            elif id_ in ("on", "off"):
                payload = {"on": id_ == "on"}
            else:
                return {"ok": False, "x": "actiune necunoscuta"}
            r = set_dual_zone_leds(payload, payload)
            return {"ok": r.get("status") == "ok", "x": "lumini " + ("ok" if r.get("status") == "ok" else "offline"),
                    "refresh": "lights"}

        if a == "voice":
            v = presence.voice()
            active = v.get("state") in ("listening", "speaking")
            want_stop = id_ == "stop" or (id_ == "toggle" and active)
            ok = presence.stop_voice() if want_stop else presence.wake()
            return {"ok": ok, "x": ("gata" if want_stop else "te ascult") if ok else "vocea e oprita"}

        if a == "alarm_stop":
            from tools.timers import get_store
            get_store().stop_ringing()
            return {"ok": True, "x": "alarma oprita"}

        if a == "restart":
            from core import lifecycle
            ok = lifecycle.request_restart()
            return {"ok": ok, "x": "repornesc chronos" if ok else "nu pot reporni"}

        if a == "power_off":
            # Butonul de pornire a fost mutat pe OFF. Oprire curată a Pi-ului;
            # Pico-ul rămâne alimentat și îl pornește la loc prin J2.
            logger.info("⏻ [Consolă] Oprire cerută de la butonul de pornire.")
            threading.Timer(1.5, _shutdown_pi).start()
            return {"ok": True, "x": "opresc pi-ul"}

        return {"ok": False, "x": "actiune necunoscuta: " + str(a)}

    # ═════════════════════════════════════════════════════════════
    # PROTOCOL — un mesaj de la Pico → răspunsurile pentru el
    # ═════════════════════════════════════════════════════════════

    def _list_msg(self, v: str) -> dict:
        fn = {"rem": self.list_rem, "plan": self.list_plan,
              "targets": self.list_targets, "scenes": self.list_scenes}[v]
        items = fn()
        self._lists[v] = items
        return {"t": "list", "v": v, "title": _TITLES[v],
                "items": [{"id": ascii_text(i["id"], ID_MAX), "x": ascii_text(i["x"], 40),
                           "d": i.get("d", 0), "n": i.get("n", 0)} for i in items[:40]]}

    def _view_msg(self, v: str) -> dict:
        if v in _TITLES:
            return self._list_msg(v)
        if v == "music":
            return self.music()
        if v == "lights":
            return self.lights()
        raise ValueError(v)

    def _real_id(self, v: str, id_: str) -> str:
        """Pico-ul primește id-urile fără diacritice; acțiunile au nevoie de cele reale."""
        for i in self._lists.get(v, []):
            if ascii_text(i["id"], ID_MAX) == id_:
                return i["id"]
        return id_

    def handle(self, msg: dict) -> list:
        t = msg.get("t")
        try:
            if t == "hello":
                logger.info(f"🎛️ [Consolă] Conectată (firmware {msg.get('fw', '?')}).")
                return [self.heartbeat()]

            if t == "get":
                return [self._view_msg(msg.get("v", ""))]

            if t == "focus":
                v = msg.get("v", "home")
                if v not in self._lists:
                    presence.set_console(None)
                    return []
                presence.set_console({"view": v, "title": _TITLES.get(v, v.upper()),
                                      "sel": int(msg.get("sel", 0)),
                                      "items": [{"x": i["x"], "d": i.get("d", 0), "n": i.get("n", 0)}
                                                for i in self._lists[v]]})
                return []

            if t == "do":
                a = msg.get("a", "")
                view = {"rem_check": "rem", "plan_done": "plan", "target_set": "targets",
                        "scene": "scenes"}.get(a, "")
                r = self.do(a, self._real_id(view, str(msg.get("id", ""))), msg.get("n"))
                out = [{"t": "ok" if r["ok"] else "err", "x": ascii_text(r.get("x"), 40)}]
                if r["ok"] and r.get("refresh"):
                    try:
                        out.append(self._view_msg(r["refresh"]))
                    except Exception as e:
                        logger.debug(f"[Consolă] refresh {r['refresh']}: {e}")
                return out

        except Exception as e:
            logger.warning(f"⚠️ [Consolă] {t}: {e}")
            return [{"t": "err", "x": ascii_text(str(e), 40)}]
        return [{"t": "err", "x": "mesaj necunoscut"}]

    def heartbeat(self) -> dict:
        now = datetime.now()
        v = presence.voice()
        try:
            from tools.timers import get_store
            alarm = get_store().ringing
        except Exception:
            alarm = False
        return {"t": "hb", "h": now.hour, "m": now.minute, "s": v.get("state", "off"),
                "mood": self._mood_hex(), "al": int(alarm)}

    def _mood_hex(self) -> str:
        color, ts = self._mood
        if time.time() - ts > 20:
            try:
                from web.web_dashboard import _chronos_state_payload
                color = _chronos_state_payload().get("color", "#8b7aff").lstrip("#")
            except Exception:
                pass
            self._mood = (color, time.time())
        return color

    # ═════════════════════════════════════════════════════════════
    # SERIAL
    # ═════════════════════════════════════════════════════════════

    def _find_port(self):
        forced = os.environ.get("CONSOLE_PORT")
        if forced:
            return forced
        from serial.tools import list_ports
        for p in list_ports.comports():
            if p.vid == PICO_VID:
                return p.device
        return None

    def _send_sync(self, msg: dict) -> None:
        ser = self._serial
        if ser is None:
            return
        line = (json.dumps(msg, ensure_ascii=True, separators=(",", ":")) + "\n").encode()
        with self._write_lock:
            try:
                ser.write(line)
            except Exception as e:
                logger.debug(f"[Consolă] scriere: {e}")

    async def send(self, msg: dict) -> None:
        await asyncio.get_running_loop().run_in_executor(self._writer, self._send_sync, msg)

    async def run(self) -> None:
        try:
            import serial  # noqa: F401
        except ImportError:
            logger.info("ℹ️  [Consolă] pyserial lipsă — consola fizică dezactivată.")
            return
        announced = False
        while True:
            port = self._find_port()
            if not port:
                if not announced:
                    logger.info("ℹ️  [Consolă] Niciun Pico conectat — aștept.")
                    announced = True
                await asyncio.sleep(5)
                continue
            announced = False
            try:
                await self._session(port)
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.warning(f"⚠️ [Consolă] Legătura cu {port} a căzut: {e}")
            finally:
                self.connected = False
                self._close()
            await asyncio.sleep(3)

    def _close(self) -> None:
        ser, self._serial = self._serial, None
        if ser is not None:
            try:
                ser.close()
            except Exception:
                pass

    async def _session(self, port: str) -> None:
        import serial
        loop = asyncio.get_running_loop()
        self._serial = serial.Serial(port, 115200, timeout=0.2, write_timeout=0.5)
        self.connected = True
        logger.info(f"🎛️ [Consolă] Deschis {port}.")
        inbox: asyncio.Queue = asyncio.Queue()
        dead = threading.Event()

        def _reader():
            buf = b""
            while not dead.is_set():
                try:
                    chunk = self._serial.read(256) if self._serial else b""
                except Exception:
                    break
                buf += chunk
                while b"\n" in buf:
                    line, buf = buf.split(b"\n", 1)
                    loop.call_soon_threadsafe(inbox.put_nowait, line)
                if len(buf) > 4096:
                    buf = b""          # gunoi fără sfârșit de linie — nu creștem la infinit
            loop.call_soon_threadsafe(inbox.put_nowait, None)

        threading.Thread(target=_reader, daemon=True, name="console-rx").start()
        tasks = [asyncio.create_task(self._heartbeat_loop()), asyncio.create_task(self._level_loop())]
        try:
            await self.send(self.heartbeat())
            while True:
                line = await inbox.get()
                if line is None:
                    raise ConnectionError("Pico deconectat")
                try:
                    msg = json.loads(line.decode("utf-8", "replace"))
                except ValueError:
                    continue              # linie de debug din firmware, nu JSON
                for reply in await asyncio.to_thread(self.handle, msg):
                    await self.send(reply)
        finally:
            dead.set()
            for t in tasks:
                t.cancel()
            presence.set_console(None)

    async def _heartbeat_loop(self) -> None:
        while True:
            await asyncio.sleep(HEARTBEAT_S)
            await self.send(self.heartbeat())

    async def _level_loop(self) -> None:
        """Nivelul vocii pentru waveform-ul de pe ecranul rotund — doar în sesiune."""
        while True:
            v = presence.voice()
            if v.get("state") in ("listening", "speaking"):
                await self.send({"t": "lvl", "s": v["state"], "a": int(v.get("level", 0) * 1000)})
                await asyncio.sleep(1 / LEVEL_HZ)
            else:
                await asyncio.sleep(0.25)


def _shutdown_pi() -> None:
    try:
        subprocess.run(["sudo", "-n", "shutdown", "-h", "now"], check=True, timeout=10)
    except Exception as e:
        logger.error(f"❌ [Consolă] Nu pot opri Pi-ul (sudo fără parolă?): {e}")


_bridge = None


def get_bridge() -> ConsoleBridge:
    global _bridge
    if _bridge is None:
        _bridge = ConsoleBridge()
    return _bridge
