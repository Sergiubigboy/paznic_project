"""
main_async.py — Chronos Orchestrator
=====================================
Punctul de intrare al sistemului Chronos cu voce completă.

Componente pornite:
    1. EventBus        — comunicare inter-componente
    2. AudioInterface  — microfon + OWW wake word
    3. TTSEngine       — edge-tts, conductă cu streaming pe clauze
    4. GeminiLive      — sesiune vocală native-audio
    5. LLMRouter       — rutare + voice flow + TTS
    6. Web Dashboard   — Flask pe thread propriu, oprit curat la shutdown

Moduri de operare:
    VOCE:     "Jarvis" → sesiune Gemini Live (audio bidirecțional)
    TERMINAL: text → Enter → răspuns streamat pe ecran (+ TTS opțional)

Comenzi speciale terminal:
    /audio       — simulează wake word
    /stats       — statistici EventBus
    /voice       — listează vocile TTS disponibile
    /setvoice X  — schimbă vocea TTS
    /speak on|off— rostește (sau nu) răspunsurile din terminal
    /help        — ajutor
    /exit        — oprire curată

Oprire: Ctrl+C
"""

import asyncio
import io
import logging
import os
import signal
import sys
import threading
import time
from pathlib import Path

# Forțare UTF-8 pe Windows (cp1252 nu suportă emoji/diacritice)
os.environ.setdefault("PYTHONIOENCODING", "utf-8")
if hasattr(sys.stdout, "buffer"):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-8s | %(name)-20s | %(message)s",
    datefmt="%H:%M:%S",
    handlers=[logging.StreamHandler(sys.stdout)],
)
for noisy in ("httpx", "chromadb", "urllib3", "werkzeug", "google", "asyncio"):
    logging.getLogger(noisy).setLevel(logging.WARNING)

logger = logging.getLogger("chronos.main")

# ─────────────────────────────────────────────────────────────────────────────
# ROOT PATH
# ─────────────────────────────────────────────────────────────────────────────
ROOT_DIR = Path(__file__).parent.resolve()
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

BANNER = """
  CHRONOS — Async Event-Driven AI Assistant
  Voce Completa + Terminal
"""

# ─────────────────────────────────────────────────────────────────────────────
# IMPORTURI CHRONOS
# ─────────────────────────────────────────────────────────────────────────────
from core.event_bus import EventBus, EventType
from core.audio_interface import AudioInterface
from core.tts_engine import TTSEngine
from core.llm_router import LLMRouter
from core.gemini_live import GeminiLiveSession
from core import day_runner

WEB_HOST = "0.0.0.0"
WEB_PORT = 5000


# =============================================================================
# WEB DASHBOARD
# =============================================================================

class WebDashboard:
    """Flask pe un thread propriu, cu oprire curată.

    De ce NU `asyncio.to_thread(app.run)`, ca înainte: `app.run()` nu se
    întoarce niciodată, deci ținea ocupat permanent un worker din pool-ul
    default — exact pool-ul folosit și de redarea audio, de dispatcher și de
    analiza de emoții. Pe un Pi cu 4 nuclee pool-ul are 8 workeri, iar unul
    pierdut definitiv se simte. În plus, `to_thread` nu poate fi anulat, deci
    „oprirea" serverului era doar aparentă.

    `make_server` ne dă un obiect cu `shutdown()` adevărat.
    """

    __slots__ = ("_server", "_thread")

    def __init__(self):
        self._server = None
        self._thread = None

    def start(self) -> bool:
        try:
            from werkzeug.serving import make_server
            from web.web_dashboard import app
        except ImportError as e:
            logger.warning(f"⚠️ [Web] Nu am putut importa dashboard-ul: {e}")
            return False

        try:
            self._server = make_server(WEB_HOST, WEB_PORT, app, threaded=True)
        except OSError as e:
            logger.error(f"❌ [Web] Nu pot deschide portul {WEB_PORT}: {e}")
            return False
        except Exception as e:
            logger.error(f"❌ [Web] Eroare la pornire: {e}", exc_info=True)
            return False

        self._thread = threading.Thread(
            target=self._server.serve_forever, name="web-dashboard", daemon=True
        )
        self._thread.start()
        logger.info(f"✅ [Web] Pornit pe http://{WEB_HOST}:{WEB_PORT}")
        return True

    def stop(self) -> None:
        if self._server is None:
            return
        try:
            self._server.shutdown()
            self._server.server_close()
        except Exception as e:
            logger.debug(f"[Web] Oprire: {e}")
        if self._thread is not None:
            self._thread.join(timeout=3.0)
        self._server = None
        self._thread = None
        logger.info("🛑 [Web] Oprit.")


# =============================================================================
# TASK-URI ASYNCIO
# =============================================================================

async def audio_task(audio: AudioInterface) -> None:
    """Loop microfon + wake word detection."""
    logger.info("🎙️  [Task] Audio loop pornit.")
    await audio.run()
    logger.info("🛑 [Task] Audio loop oprit.")


async def terminal_task(bus: EventBus, router: LLMRouter, tts: TTSEngine) -> None:
    """Citire comenzi din terminal."""
    if not sys.stdin.isatty():
        logger.info("ℹ️  [Terminal] Non-interactiv (non-tty). Loop terminal dezactivat.")
        return

    logger.info(
        "⌨️  [Task] Terminal activ.\n"
        "   Tastează o comandă + Enter. Comenzi speciale: /help"
    )
    loop = asyncio.get_running_loop()

    while True:
        try:
            text = (await loop.run_in_executor(None, input, "\n⌨️  [Chronos] > ")).strip()
            if not text:
                continue

            cmd = text.lower()

            if cmd in ("/exit", "/quit"):
                await bus.publish(EventType.SYSTEM_SHUTDOWN, {"reason": "terminal_exit"})
                break

            if cmd == "/stats":
                stats = bus.get_stats()
                print("\n📊 EventBus Stats:")
                for et, cnt in stats["subscribers"].items():
                    pub = stats["publish_counts"].get(et, 0)
                    if cnt or pub:
                        print(f"   {et}: {cnt} subscribers | {pub} published")
                continue

            if cmd == "/help":
                _print_help()
                continue

            if cmd == "/audio":
                logger.info("🔧 [Terminal] Simulez WAKE_WORD_DETECTED...")
                await bus.publish(
                    EventType.WAKE_WORD_DETECTED,
                    {"timestamp": time.time(), "score": 1.0, "model_name": "manual"},
                )
                continue

            if cmd == "/voice":
                print("🔊 Listez vocile române edge-tts...")
                voices = await TTSEngine.list_romanian_voices()
                for v in voices:
                    print(f"   {v.get('ShortName')} — {v.get('Gender')}")
                if not voices:
                    print("   Nu am putut prelua lista (necesită conexiune internet).")
                continue

            if cmd.startswith("/speak"):
                arg = cmd[len("/speak"):].strip()
                if arg in ("on", "off"):
                    on = router.set_speak_terminal(arg == "on")
                    print(f"🔊 Rostirea răspunsurilor din terminal: {'PORNITĂ' if on else 'OPRITĂ'}")
                else:
                    print("   Folosire: /speak on   sau   /speak off")
                continue

            if cmd.startswith("/setvoice "):
                new_voice = text[len("/setvoice "):].strip()
                tts.set_voice(new_voice)
                print(f"🔊 Voce schimbată la: {new_voice}")
                continue

            await bus.publish(
                EventType.TERMINAL_COMMAND_RECEIVED,
                {"text": text, "timestamp": time.time(), "source": "terminal"},
            )

        except EOFError:
            logger.info("ℹ️  [Terminal] stdin închis.")
            break
        except asyncio.CancelledError:
            break
        except Exception as e:
            logger.error(f"❌ [Terminal] Eroare: {e}")
            await asyncio.sleep(0.5)


# =============================================================================
# HANDLER-E EVENT BUS
# =============================================================================

async def execute_tool_handler(sub) -> None:
    """EXECUTE_TOOL — afișează tool-ul executat în consolă."""
    try:
        async for data in sub:
            reasoning = (data.get("reasoning") or "")[:60]
            src = data.get("source", "?")
            print(
                f"\n{'─'*60}\n"
                f"🔧 TOOL #{data.get('request_id', '?')} | "
                f"{'🎙️' if src == 'voice' else '⌨️'} {src.upper()}\n"
                f"   📦 {data.get('tool', 'UNKNOWN')}\n"
                f"   📋 {data.get('args', {})}\n"
                + (f"   💭 {reasoning}...\n" if reasoning else "")
                + f"{'─'*60}"
            )
    except asyncio.CancelledError:
        pass


async def system_status_handler(sub) -> None:
    """SYSTEM_STATUS — loghează statusurile componentelor."""
    levels = {"DEBUG": logger.debug, "WARNING": logger.warning, "ERROR": logger.error}
    try:
        async for data in sub:
            fn = levels.get(str(data.get("level", "INFO")).upper(), logger.info)
            fn(f"[{data.get('component', '?')}] {data.get('status', '')}: {data.get('message', '')}")
    except asyncio.CancelledError:
        pass


async def system_ready_handler(sub, expected: set) -> None:
    """SYSTEM_READY — anunță când toate componentele sunt gata.

    Funcționează abia acum: înainte, abonarea se făcea la prima iterație a
    task-ului, adică DUPĂ ce componentele publicaseră deja SYSTEM_READY în
    timpul inițializării. Bannerul nu apărea niciodată. Acum abonamentul e
    creat înainte de pornirea componentelor (vezi main()).
    """
    ready = set()
    try:
        async for data in sub:
            component = data.get("component", "")
            ready.add(component)
            logger.info(f"✅ [{component}] GATA.")
            if expected.issubset(ready):
                print(
                    f"\n{'═'*60}\n"
                    f"🚀 CHRONOS COMPLET INIȚIALIZAT\n"
                    f"   🎙️  Spune 'Jarvis' pentru activare vocală\n"
                    f"   ⌨️  Sau tastează direct o comandă în terminal\n"
                    f"   🌐 Dashboard: http://localhost:{WEB_PORT}\n"
                    f"   💡 /help pentru comenzi speciale\n"
                    f"{'═'*60}\n"
                )
                return
    except asyncio.CancelledError:
        pass


async def shutdown_watcher(sub, shutdown_event: asyncio.Event) -> None:
    try:
        async for _ in sub:
            shutdown_event.set()
            return
    except asyncio.CancelledError:
        pass


# =============================================================================
# SHUTDOWN
# =============================================================================

async def shutdown_all(bus, audio, router, tts, live, web, tasks) -> None:
    """Oprire curată a tuturor componentelor, în ordinea dependențelor."""
    logger.info("\n🛑 [Shutdown] Inițiez oprirea curată...")

    await bus.publish(EventType.SYSTEM_SHUTDOWN, {"reason": "shutdown"})

    # 1. Tăiem sunetul înainte de orice — altfel Chronos continuă să vorbească
    #    peste procesul care se oprește.
    if tts is not None:
        try:
            await tts.shutdown()
        except Exception as e:
            logger.debug(f"[Shutdown] TTS: {e}")

    # 2. Oprim producătorii de evenimente.
    audio.stop()
    await router.shutdown()
    if live is not None:
        try:
            await live.shutdown()
        except Exception as e:
            logger.debug(f"[Shutdown] GeminiLive: {e}")
    web.stop()

    # 3. Deblocăm abonații rămași, apoi anulăm task-urile.
    await bus.shutdown()
    for task in tasks:
        if not task.done():
            task.cancel()
    pending = [t for t in tasks if not t.done()]
    if pending:
        try:
            await asyncio.wait_for(
                asyncio.gather(*pending, return_exceptions=True), timeout=5.0
            )
        except asyncio.TimeoutError:
            logger.warning("⚠️ [Shutdown] Unele task-uri n-au răspuns la timp.")

    # 4. Conexiunile HTTP păstrate deschise pentru keep-alive.
    try:
        from ai_core import close_session
        close_session()
    except Exception:
        pass

    logger.info("✅ [Shutdown] Chronos oprit. La revedere!")


# =============================================================================
# HELPERS
# =============================================================================

def _print_help() -> None:
    print(
        "\n📖 CHRONOS — COMENZI TERMINALE\n"
        + "─" * 40 + "\n"
        + "Comenzi normale:\n"
        + "   aprinde lumina în roșu\n"
        + "   pune muzică jazz\n"
        + "   ce știi despre mine?\n"
        + "\nComenzi speciale:\n"
        + "   /audio         — simulează wake word 'Jarvis'\n"
        + "   /stats         — statistici EventBus\n"
        + "   /voice         — listează vocile TTS române\n"
        + "   /setvoice NAME — schimbă vocea TTS\n"
        + "   /speak on|off  — rostește răspunsurile din terminal\n"
        + "   /help          — acest meniu\n"
        + "   /exit          — oprire Chronos\n"
        + "─" * 40
    )


# =============================================================================
# MAIN
# =============================================================================

async def main() -> None:
    print(BANNER)

    bus = EventBus(maxsize=200, log_events=False)
    logger.info("✅ EventBus inițializat.")

    # ── Abonamentele se creează ÎNAINTE de pornirea componentelor ──
    # Altfel, evenimentele emise în timpul inițializării (SYSTEM_READY,
    # SYSTEM_STATUS) se publică într-un bus fără ascultători și se pierd.
    ready_sub = bus.subscribe(EventType.SYSTEM_READY)
    status_sub = bus.subscribe(EventType.SYSTEM_STATUS)
    tool_sub = bus.subscribe(EventType.EXECUTE_TOOL)
    shutdown_sub = bus.subscribe(EventType.SYSTEM_SHUTDOWN)

    # ── 1. TTSEngine ─────────────────────────────────────────────────────
    tts = TTSEngine(event_bus=bus)
    tts_ok = await tts.initialize()
    if not tts_ok:
        logger.warning("⚠️  TTS indisponibil — răspunsurile vor fi doar text.")

    # ── 2. AudioInterface ────────────────────────────────────────────────
    audio = AudioInterface(bus)
    audio_ok = await audio.initialize()
    if not audio_ok:
        logger.warning(
            "⚠️  AudioInterface dezactivat → modul TERMINAL-ONLY.\n"
            "   Tastează '/audio' pentru a simula un wake word."
        )

    # ── 3. GeminiLiveSession ─────────────────────────────────────────────
    live = GeminiLiveSession(event_bus=bus)
    live_ok = await live.initialize()
    if not live_ok:
        logger.warning(
            "⚠️  GeminiLive indisponibil → vocea va fi dezactivată.\n"
            "   Verifică internet + google-genai API key."
        )

    # ── 4. LLMRouter ─────────────────────────────────────────────────────
    router = LLMRouter(
        event_bus=bus,
        audio_interface=audio if audio_ok else None,
        tts_engine=tts if tts_ok else None,
        gemini_live=live if live_ok else None,
    )
    if not await router.initialize():
        logger.error("❌ LLMRouter nu a pornit! Verifică agents/ și dependințele.")
        await bus.shutdown()
        return

    # ── 5. Web Dashboard ─────────────────────────────────────────────────
    web = WebDashboard()
    web.start()

    # ── 6. Task-uri ──────────────────────────────────────────────────────
    expected_components = {"LLMRouter"}
    if audio_ok:
        expected_components.add("AudioInterface")

    shutdown_event = asyncio.Event()

    all_tasks = [
        asyncio.create_task(audio_task(audio), name="audio_main"),
        asyncio.create_task(terminal_task(bus, router, tts), name="terminal_main"),
        asyncio.create_task(execute_tool_handler(tool_sub), name="tool_handler"),
        asyncio.create_task(system_status_handler(status_sub), name="status_handler"),
        asyncio.create_task(
            system_ready_handler(ready_sub, expected_components), name="ready_handler"
        ),
        asyncio.create_task(
            shutdown_watcher(shutdown_sub, shutdown_event), name="shutdown_watcher"
        ),
        # Programul zilei pe Telegram: anunta fiecare bloc cand incepe si
        # asculta raspunsurile tale ca sa reaseze restul zilei.
        asyncio.create_task(day_runner.run(), name="day_telegram"),
    ]

    # Un task critic care moare cu excepție oprește sistemul — dar prin
    # callback, nu prin scanarea listei de 2 ori pe secundă la nesfârșit.
    # Pe Pi, „premium la vedere, aproape zero CPU" înseamnă inclusiv asta:
    # procesul în repaus nu are de ce să se trezească.
    critical = {"audio_main"}

    def _on_done(task: asyncio.Task) -> None:
        if task.cancelled() or task.get_name() not in critical:
            return
        exc = task.exception()
        if exc is not None:
            logger.error(f"Task critic '{task.get_name()}' a eșuat: {exc}", exc_info=exc)
            shutdown_event.set()

    for task in all_tasks:
        task.add_done_callback(_on_done)

    # ── 7. Semnale ───────────────────────────────────────────────────────
    def _on_signal(*_args):
        if not shutdown_event.is_set():
            logger.info("\n⚡ Semnal de oprire primit (Ctrl+C).")
            shutdown_event.set()

    loop = asyncio.get_running_loop()
    if sys.platform == "win32":
        # Windows nu suportă loop.add_signal_handler; signal.signal rulează pe
        # thread-ul principal, deci trecem prin loop ca să rămână thread-safe.
        signal.signal(signal.SIGINT, lambda *_: loop.call_soon_threadsafe(_on_signal))
    else:
        for sig in (signal.SIGINT, signal.SIGTERM):
            loop.add_signal_handler(sig, _on_signal)

    logger.info(f"[{len(all_tasks)} task-uri pornite]. Chronos activ.")

    try:
        await shutdown_event.wait()
    except (KeyboardInterrupt, asyncio.CancelledError):
        logger.info("\nÎntrerupere.")

    await shutdown_all(bus, audio, router, tts, live if live_ok else None, web, all_tasks)


# =============================================================================
# ENTRY POINT
# =============================================================================

if __name__ == "__main__":
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())

    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n✅ Chronos oprit de utilizator.")
    except Exception as e:
        logger.critical(f"💥 Eroare critică: {e}", exc_info=True)
        sys.exit(1)
