"""
main_async.py — Chronos Orchestrator (Pasul 2 — Complet)
=========================================================
Punctul de intrare al sistemului Chronos cu voce completă.

Componente pornite:
    1. EventBus        — comunicare inter-componente
    2. AudioInterface  — microfon + OWW wake word + STT + interrupt monitor
    3. TTSEngine       — edge-tts cu suport întrerupere
    4. LLMRouter       — Dispatcher + voice flow + TTS integration
    5. Web Dashboard   — Flask în asyncio.to_thread()

Moduri de operare:
    VOCE:     "Jarvis" → înregistrare → STT → Gemini → TTS → (întrerupere)
    TERMINAL: text → Enter → Gemini → afișare consolă

Comenzi speciale terminal:
    /audio  — simulează wake word
    /stats  — statistici EventBus
    /voice  — listează vocile TTS disponibile
    /help   — ajutor
    /exit   — oprire curată

Oprire: Ctrl+C
"""

import asyncio
import io
import logging
import os
import signal
import sys
import time
from pathlib import Path

# Fortare UTF-8 pe Windows (cp1252 nu suporta emoji/caractere speciale)
os.environ.setdefault("PYTHONIOENCODING", "utf-8")
if hasattr(sys.stdout, "buffer"):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-8s | %(name)-20s | %(message)s",
    datefmt="%H:%M:%S",
    handlers=[
        logging.StreamHandler(sys.stdout),
    ],
)
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("chromadb").setLevel(logging.WARNING)
logging.getLogger("urllib3").setLevel(logging.WARNING)
logging.getLogger("werkzeug").setLevel(logging.WARNING)
logging.getLogger("google").setLevel(logging.WARNING)

# DEBUG pentru modulele Chronos care au probleme — vedem exact ce se întâmplă
logging.getLogger("core.gemini_live").setLevel(logging.DEBUG)
logging.getLogger("core.llm_router").setLevel(logging.DEBUG)

logger = logging.getLogger("chronos.main")

# ─────────────────────────────────────────────────────────────────────────────
# ROOT PATH
# ─────────────────────────────────────────────────────────────────────────────
ROOT_DIR = Path(__file__).parent.resolve()
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

# ─────────────────────────────────────────────────────────────────────────────
# BANNER
# ─────────────────────────────────────────────────────────────────────────────
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


# =============================================================================
# TASK-URI ASYNCIO
# =============================================================================

async def audio_task(audio: AudioInterface) -> None:
    """Task 1: Loop microfon + wake word detection."""
    logger.info("🎙️  [Task] Audio loop pornit.")
    await audio.run()
    logger.info("🛑 [Task] Audio loop oprit.")


async def terminal_task(bus: EventBus) -> None:
    """
    Task 2: Citire comenzi din terminal.

    Comenzi speciale: /audio, /stats, /voice, /help, /exit
    Orice altceva → TERMINAL_COMMAND_RECEIVED în EventBus
    """
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
            text = await loop.run_in_executor(
                None,
                lambda: input("\n⌨️  [Chronos] > ")
            )
            text = text.strip()

            if not text:
                continue

            # --- Comenzi speciale ---
            cmd = text.lower()

            if cmd in ("/exit", "/quit"):
                await bus.publish(EventType.SYSTEM_SHUTDOWN, {"reason": "terminal_exit"})
                break

            elif cmd == "/stats":
                stats = bus.get_stats()
                print("\n📊 EventBus Stats:")
                for et, cnt in stats["subscribers"].items():
                    pub = stats["publish_counts"].get(et, 0)
                    if cnt or pub:
                        print(f"   {et}: {cnt} subscribers | {pub} published")
                continue

            elif cmd == "/help":
                _print_help()
                continue

            elif cmd == "/audio":
                logger.info("🔧 [Terminal] Simulând WAKE_WORD_DETECTED...")
                await bus.publish(
                    EventType.WAKE_WORD_DETECTED,
                    {"timestamp": time.time(), "score": 1.0, "model_name": "manual"}
                )
                continue

            elif cmd == "/voice":
                print("🔊 Listez vocile române edge-tts...")
                voices = await asyncio.to_thread(TTSEngine.list_romanian_voices)
                for v in voices:
                    print(f"   {v.get('ShortName')} — {v.get('Gender')}")
                if not voices:
                    print("   Nu am putut prelua lista (necesită conexiune internet).")
                continue

            elif cmd.startswith("/setvoice "):
                new_voice = text[len("/setvoice "):].strip()
                await bus.publish(
                    EventType.SYSTEM_STATUS,
                    {"component": "TTS", "status": "VOICE_CHANGE", "message": new_voice}
                )
                print(f"🔊 Voce schimbată la: {new_voice}")
                continue

            # --- Comandă normală → EventBus ---
            await bus.publish(
                EventType.TERMINAL_COMMAND_RECEIVED,
                {"text": text, "timestamp": time.time(), "source": "terminal"}
            )

        except EOFError:
            logger.info("ℹ️  [Terminal] stdin închis.")
            break
        except asyncio.CancelledError:
            break
        except Exception as e:
            logger.error(f"❌ [Terminal] Eroare: {e}")
            await asyncio.sleep(0.5)


async def web_server_task() -> None:
    """Task 3: Flask Web Dashboard în asyncio.to_thread()."""
    logger.info("🌐 [Task] Pornesc Web Dashboard pe portul 5000...")

    def _start_flask():
        try:
            from web.web_dashboard import app
            logger.info("✅ [Web] Pornesc pe http://0.0.0.0:5000")
            app.run(host="0.0.0.0", port=5000, debug=False, use_reloader=False, threaded=True)
        except ImportError as e:
            logger.warning(f"⚠️ [Web] Nu am putut importa web_dashboard: {e}")
        except OSError as e:
            logger.error(f"❌ [Web] Portul 5000 ocupat: {e}")
        except Exception as e:
            logger.error(f"❌ [Web] Eroare Flask: {e}", exc_info=True)

    try:
        await asyncio.to_thread(_start_flask)
    except asyncio.CancelledError:
        logger.info("🛑 [Web] Task anulat.")


# =============================================================================
# HANDLER-E EVENT BUS
# =============================================================================

async def execute_tool_handler(bus: EventBus) -> None:
    """Handler EXECUTE_TOOL — afișează tool-ul executat în consolă."""
    logger.info("🔧 [Handler] execute_tool_handler pornit.")
    try:
        async for data in bus.subscribe(EventType.EXECUTE_TOOL):
            tool      = data.get("tool", "UNKNOWN")
            args      = data.get("args", {})
            source    = data.get("source", "?")
            req_id    = data.get("request_id", "?")
            reasoning = data.get("reasoning", "")[:60]

            src_icon = "🎙️" if source == "voice" else "⌨️"
            print(
                f"\n{'─'*60}\n"
                f"🔧 TOOL #{req_id} | {src_icon} {source.upper()}\n"
                f"   📦 {tool}\n"
                f"   📋 {args}\n"
                + (f"   💭 {reasoning}...\n" if reasoning else "")
                + f"{'─'*60}"
            )
    except asyncio.CancelledError:
        logger.info("🛑 [Handler] execute_tool_handler anulat.")


async def system_status_handler(bus: EventBus) -> None:
    """Handler SYSTEM_STATUS — loghează statusurile componentelor."""
    try:
        async for data in bus.subscribe(EventType.SYSTEM_STATUS):
            component = data.get("component", "?")
            status    = data.get("status", "")
            message   = data.get("message", "")
            level     = data.get("level", "INFO").upper()
            fn = {"DEBUG": logger.debug, "WARNING": logger.warning,
                  "ERROR": logger.error}.get(level, logger.info)
            fn(f"[{component}] {status}: {message}")
    except asyncio.CancelledError:
        pass


async def system_ready_handler(bus: EventBus, expected: set) -> None:
    """Handler SYSTEM_READY — anunță când toate componentele sunt gata."""
    ready = set()
    try:
        async for data in bus.subscribe(EventType.SYSTEM_READY):
            component = data.get("component", "")
            ready.add(component)
            logger.info(f"✅ [{component}] GATA.")
            if expected.issubset(ready):
                print(
                    f"\n{'═'*60}\n"
                    f"🚀 CHRONOS COMPLET INIȚIALIZAT\n"
                    f"   🎙️  Spune 'Jarvis' pentru activare vocală\n"
                    f"   ⌨️  Sau tastează direct o comandă în terminal\n"
                    f"   🌐 Dashboard: http://localhost:5000\n"
                    f"   💡 /help pentru comenzi speciale\n"
                    f"{'═'*60}\n"
                )
                return
    except asyncio.CancelledError:
        pass


# =============================================================================
# SHUTDOWN
# =============================================================================

async def shutdown_all(bus, audio, router, tts, tasks) -> None:
    """Oprire curată a tuturor componentelor."""
    logger.info("\n🛑 [Shutdown] Inițiez oprirea curată...")

    await bus.publish(EventType.SYSTEM_SHUTDOWN, {"reason": "shutdown"})
    await asyncio.sleep(0.1)

    # Oprire TTS dacă vorbește
    if tts and tts.is_playing:
        tts.interrupt()

    audio.stop()
    await router.shutdown()
    await bus.shutdown()

    for task in tasks:
        if not task.done():
            task.cancel()
            try:
                await asyncio.wait_for(task, timeout=2.0)
            except (asyncio.CancelledError, asyncio.TimeoutError):
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
        + "   /help          — acest meniu\n"
        + "   /exit          — oprire Chronos\n"
        + "─" * 40
    )


# =============================================================================
# MAIN
# =============================================================================

async def main() -> None:
    """Funcția principală asincronă."""
    print(BANNER)

    # ── 1. EventBus ─────────────────────────────────────────────────────────
    bus = EventBus(maxsize=200, log_events=False)
    logger.info("✅ EventBus inițializat.")

    # ── 2. TTSEngine ─────────────────────────────────────────────────────────
    tts = TTSEngine(event_bus=bus)
    tts_ok = await tts.initialize()
    if not tts_ok:
        logger.warning("⚠️  TTS indisponibil — răspunsurile vor fi doar text.")

    # ── 3. AudioInterface ────────────────────────────────────────────────────
    audio = AudioInterface(bus)
    audio_ok = await audio.initialize()
    if not audio_ok:
        logger.warning(
            "⚠️  AudioInterface dezactivat → modul TERMINAL-ONLY.\n"
            "   Tastează '/audio' pentru a simula un wake word."
        )

    # -- 4a. GeminiLiveSession --------------------------------------------------
    live = GeminiLiveSession(event_bus=bus)
    live_ok = await live.initialize()
    if not live_ok:
        logger.warning(
            "⚠️  GeminiLive indisponibil → vocea va fi dezactivata.\n"
            "   Verifica internet + google-genai API key."
        )

    # ── 4b. LLMRouter -----------------------------------------------------------
    router = LLMRouter(
        event_bus=bus,
        audio_interface=audio if audio_ok else None,
        tts_engine=tts if tts_ok else None,
        gemini_live=live if live_ok else None,
    )
    router_ok = await router.initialize()
    if not router_ok:
        logger.error("❌ LLMRouter nu a pornit! Verifică dispatcher.py și dependințele.")
        return

    # ── 5. Task-uri ──────────────────────────────────────────────────────────
    expected_components = {"LLMRouter"}
    if audio_ok:
        expected_components.add("AudioInterface")

    all_tasks = [
        asyncio.create_task(audio_task(audio),              name="audio_main"),
        asyncio.create_task(terminal_task(bus),             name="terminal_main"),
        asyncio.create_task(web_server_task(),              name="web_server"),
        asyncio.create_task(execute_tool_handler(bus),      name="tool_handler"),
        asyncio.create_task(system_status_handler(bus),     name="status_handler"),
        asyncio.create_task(
            system_ready_handler(bus, expected_components), name="ready_handler"
        ),
    ]

    # ── 6. Shutdown handling ─────────────────────────────────────────────────
    shutdown_event = asyncio.Event()

    def _on_signal():
        if not shutdown_event.is_set():
            logger.info("\n⚡ Semnal oprire primit (Ctrl+C).")
            shutdown_event.set()

    loop = asyncio.get_running_loop()
    if sys.platform != "win32":
        for sig in (signal.SIGINT, signal.SIGTERM):
            loop.add_signal_handler(sig, _on_signal)

    # Watcher pentru SYSTEM_SHUTDOWN din EventBus (ex: /exit din terminal)
    async def _watch_shutdown():
        async for _ in bus.subscribe(EventType.SYSTEM_SHUTDOWN):
            shutdown_event.set()
            break

    all_tasks.append(
        asyncio.create_task(_watch_shutdown(), name="shutdown_watcher")
    )

    logger.info(f"[{len(all_tasks)} task-uri pornite]. Chronos activ.")

    critical = {"audio_main", "web_server"}
    try:
        while not shutdown_event.is_set():
            await asyncio.sleep(0.5)
            for task in all_tasks:
                if task.done() and not task.cancelled():
                    tname = task.get_name()
                    try:
                        exc = task.exception()
                    except Exception:
                        exc = None
                    if exc and tname in critical:
                        logger.error(f"Task critic '{tname}' a esuat: {exc}", exc_info=exc)
                        shutdown_event.set()
    except KeyboardInterrupt:
        logger.info("\nKeyboardInterrupt.")

    # ── 7. Shutdown curat ───────────────────────────────────────────────────
    await shutdown_all(bus, audio, router, tts, all_tasks)


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

