"""
core/llm_router.py — Chronos LLM Router
=========================================
Creierul sistemului Chronos. Rutează comenzile către executor.

Flow VOCE (post wake word):
    WAKE_WORD_DETECTED
      → AudioInterface.enable_live_mode(queue)
      → GeminiLiveSession.run_session(queue)
          ↳ stream mic → Gemini Native Audio Live API
          ↳ audio response → playback direct
          ↳ barge-in nativ (vorbești → se opreste)
          ↳ tool text → dispatcher
      → AudioInterface.disable_live_mode()
      → Revenim la wake word detection

Flow TERMINAL:
    TERMINAL_COMMAND_RECEIVED
      → dispatcher.process_text_command()
      → afișare răspuns în consolă
      → TTS opțional (edge-tts)

Design:
    - Un singur wake word activ la un moment dat (_voice_busy lock)
    - Terminal processing nu blochează voice sessions
    - Shutdown curat cu anulare task-uri
"""

import asyncio
import logging
import time
import uuid
from typing import Optional

logger = logging.getLogger(__name__)

DISPATCHER_TIMEOUT = 35.0


class LLMRouter:
    """Router central Chronos — voice (Gemini Live) + terminal."""

    def __init__(self, event_bus, audio_interface=None, tts_engine=None,
                 gemini_live=None):
        """
        Args:
            event_bus:       EventBus.
            audio_interface: AudioInterface (pentru live mode control).
            tts_engine:      TTSEngine (pentru TTS în modul terminal).
            gemini_live:     GeminiLiveSession (pentru voce live).
        """
        from core.event_bus import EventBus, EventType
        self.bus       = event_bus
        self.EventType = EventType

        self._audio      = audio_interface
        self._tts        = tts_engine
        self._live       = gemini_live
        self._dispatcher = None
        self._initialized = False
        self._tasks      = []

        # Previne pornirea mai multor sesiuni vocale simultan
        self._voice_busy = asyncio.Lock()

    # ─────────────────────────────────────────────────────
    # INIȚIALIZARE
    # ─────────────────────────────────────────────────────

    async def initialize(self) -> bool:
        """Inițializează CommandDispatcher și pornește task-urile de ascultare."""
        logger.info("🧠 [LLMRouter] Inițializare...")

        try:
            await asyncio.to_thread(self._init_dispatcher_sync)
            logger.info("✅ [LLMRouter] CommandDispatcher inițializat.")
        except Exception as e:
            logger.error(f"❌ [LLMRouter] Dispatcher error: {e}", exc_info=True)
            return False

        # Injectăm dispatcher + audio în GeminiLive (după inițializare).
        # audio_interface e necesar ca sesiunea live să poată arma wake word-ul
        # drept mecanism de întrerupere în focus mode.
        if self._live:
            if self._dispatcher:
                self._live.dispatcher = self._dispatcher
            self._live.audio_interface = self._audio

        self._initialized = True
        self._tasks = [
            asyncio.create_task(self._listen_wake_word(), name="router_wake_word"),
            asyncio.create_task(self._listen_terminal(),  name="router_terminal"),
        ]

        await self.bus.publish(self.EventType.SYSTEM_READY, {"component": "LLMRouter"})
        logger.info("🚀 [LLMRouter] Gata. Ascult WAKE_WORD_DETECTED și TERMINAL_COMMAND_RECEIVED.")
        return True

    def _init_dispatcher_sync(self) -> None:
        from agents.chronos_agent import ChronosAgent

        self._dispatcher = ChronosAgent()

        try:
            import web.web_dashboard as wd
            wd.shared_dispatcher = self._dispatcher
            logger.info("🌐 [LLMRouter] ChronosAgent injectat în Web Dashboard.")
        except Exception as e:
            logger.warning(f"⚠️ [LLMRouter] Web inject: {e}")

    # ─────────────────────────────────────────────────────
    # LISTENERS
    # ─────────────────────────────────────────────────────

    async def _listen_wake_word(self) -> None:
        """Ascultă WAKE_WORD_DETECTED → pornește sesiune vocală Gemini Live."""
        try:
            async for data in self.bus.subscribe(self.EventType.WAKE_WORD_DETECTED):
                if self._voice_busy.locked():
                    logger.debug("[LLMRouter] Wake word ignorat (sesiune vocală activă).")
                    continue
                score = data.get("score", 0.0)
                logger.info(
                    f"\n{'='*60}\n"
                    f"🎯 [LLMRouter] Wake Word! score={score:.3f}\n"
                    f"{'='*60}"
                )
                asyncio.create_task(self._handle_voice_session())
        except asyncio.CancelledError:
            logger.info("[LLMRouter] Task 'wake_word' anulat.")

    async def _listen_terminal(self) -> None:
        """Ascultă TERMINAL_COMMAND_RECEIVED → dispatcher + afișare."""
        try:
            async for data in self.bus.subscribe(self.EventType.TERMINAL_COMMAND_RECEIVED):
                text = data.get("text", "").strip()
                if not text:
                    continue
                logger.info(
                    f"\n{'='*60}\n"
                    f"⌨️  [LLMRouter] Terminal: '{text}'\n"
                    f"{'='*60}"
                )
                asyncio.create_task(
                    self._process_terminal(text, str(uuid.uuid4())[:8])
                )
        except asyncio.CancelledError:
            logger.info("[LLMRouter] Task 'terminal' anulat.")

    # ─────────────────────────────────────────────────────
    # VOICE SESSION — Gemini Live API
    # ─────────────────────────────────────────────────────

    async def _handle_voice_session(self) -> None:
        """
        Gestionează o sesiune vocală completă via Gemini Live API.
        """
        async with self._voice_busy:

            if self._live is None or not self._live.is_initialized:
                logger.error(
                    "❌ [LLMRouter] GeminiLiveSession neinițializat!"
                )
                print("\n❌ Sesiunea vocală Gemini Live nu e disponibilă.")
                return

            if self._audio is None or not self._audio.is_enabled:
                logger.error("❌ [LLMRouter] AudioInterface indisponibil!")
                return

            # ── Beep de confirmare: Chronos a auzit wake word-ul ──
            asyncio.create_task(self._play_wake_beep())

            # ── Pauză muzică când începe sesiunea vocală ──
            if self._dispatcher and hasattr(self._dispatcher, "music_agent") and self._dispatcher.music_agent:
                try:
                    asyncio.create_task(
                        asyncio.to_thread(self._dispatcher.music_agent.pause_playback)
                    )
                except Exception as e:
                    logger.debug(f"[LLMRouter] Pause music err: {e}")

            # Coada live cu buffer generos
            live_queue: asyncio.Queue = asyncio.Queue(maxsize=600)

            # Activăm live mode în AudioInterface
            self._audio.enable_live_mode(live_queue)
            logger.info("[LLMRouter] Live mode activat → pornesc GeminiLiveSession.")

            try:
                await self._live.run_session(live_queue)
            except Exception as e:
                logger.error(f"❌ [LLMRouter] Eroare sesiune live: {e}", exc_info=True)
            finally:
                self._audio.disable_live_mode()
                logger.info("[LLMRouter] Live mode dezactivat → revenim la wake word.")

                # ── Reluare muzică la finalizarea sesiunii ──
                if self._dispatcher and hasattr(self._dispatcher, "music_agent") and self._dispatcher.music_agent:
                    try:
                        asyncio.create_task(
                            asyncio.to_thread(self._dispatcher.music_agent.resume_playback)
                        )
                    except Exception as e:
                        logger.debug(f"[LLMRouter] Resume music err: {e}")

    async def _play_wake_beep(self) -> None:
        """
        Redă un ton scurt de confirmare când wake word-ul e detectat.
        Semnal auditiv: 'Chronos te ascultă'.
        """
        try:
            import numpy as np
            import sounddevice as sd

            rate     = 44100
            duration = 0.18  # 180ms — scurt și plăcut

            # Ton dublu: 700Hz + 900Hz (mai plăcut decât un ton simplu)
            t    = np.linspace(0, duration, int(rate * duration), endpoint=False)
            wave = (np.sin(2 * np.pi * 700 * t) +
                    np.sin(2 * np.pi * 900 * t)) * 0.22

            # Fade in/out scurt pentru a evita clickuri
            fade = int(rate * 0.015)
            if fade > 0:
                wave[:fade]  *= np.linspace(0, 1, fade)
                wave[-fade:] *= np.linspace(1, 0, fade)

            await asyncio.to_thread(sd.play, wave.astype(np.float32), rate)
            await asyncio.to_thread(sd.wait)
        except Exception as e:
            logger.debug(f"[LLMRouter] Beep eșuat (non-critic): {e}")


    # ─────────────────────────────────────────────────────
    # TERMINAL PROCESSING
    # ─────────────────────────────────────────────────────

    async def _process_terminal(self, text: str, request_id: str) -> None:
        """Procesează o comandă de terminal: dispatcher + TTS opțional."""
        if not self._initialized or not self._dispatcher:
            return

        logger.info(f"⚙️  [LLMRouter] Procesez [terminal] #{request_id}: '{text}'")
        start = time.time()

        try:
            await asyncio.wait_for(
                asyncio.to_thread(self._dispatcher.process_text_command, text, None),
                timeout=DISPATCHER_TIMEOUT
            )
        except asyncio.TimeoutError:
            msg = "Îmi pare rău, a durat prea mult. Încearcă din nou."
            logger.error(f"❌ Timeout {DISPATCHER_TIMEOUT}s")
            self._print_response(msg, [], [])
            if self._tts and self._tts.is_available:
                await self._tts.speak(msg)
            return
        except Exception as e:
            logger.error(f"❌ Dispatcher error: {e}", exc_info=True)
            return

        elapsed = time.time() - start
        logger.info(f"⏱️  [LLMRouter] Dispatcher: {elapsed:.2f}s")

        result   = getattr(self._dispatcher, "last_result", {}) or {}
        intents  = result.get("intents", [])
        reply    = result.get("reply", "")
        actions  = result.get("actions", [])
        reasoning= result.get("reasoning", "")

        # Publică EXECUTE_TOOL
        INTENT_MAP = {
            "led": "wled_specialist", "music": "music_specialist",
            "journal": "logger_specialist", "target": "logger_specialist",
            "general": "llm_general_chat",
        }
        for intent in intents:
            await self.bus.publish(
                self.EventType.EXECUTE_TOOL,
                {
                    "tool": INTENT_MAP.get(intent, f"specialist_{intent}"),
                    "args": {"command": text, "intent": intent},
                    "source": "terminal",
                    "request_id": request_id,
                    "reasoning": reasoning,
                }
            )

        self._print_response(reply, actions, intents)

        await self.bus.publish(
            self.EventType.LLM_TEXT_RESPONSE,
            {
                "text": reply or "",
                "source": "terminal",
                "request_id": request_id,
                "intents": intents,
                "actions": actions,
            }
        )

    # ─────────────────────────────────────────────────────
    # OUTPUT
    # ─────────────────────────────────────────────────────

    def _print_response(self, reply, actions, intents) -> None:
        sep = "─" * 60
        print(f"\n{sep}")
        if intents:
            print(f"🎯 {' | '.join(f'[{i.upper()}]' for i in intents)}")
        if actions:
            print("⚡ Actiuni:")
            for a in actions:
                icon = "✅" if isinstance(a, dict) and a.get("status") == "ok" else "❌"
                text = a.get("text", str(a)) if isinstance(a, dict) else str(a)
                print(f"   {icon} {text}")
        if reply:
            print(f"\n🤖 Chronos: {reply}")
        elif not actions:
            print("🤖 Chronos: Comanda procesata.")
        print(sep)

    # ─────────────────────────────────────────────────────
    # INJECTARE TARDIVĂ + CLEANUP
    # ─────────────────────────────────────────────────────

    def inject_live(self, live_session) -> None:
        """Injectează GeminiLiveSession după inițializare."""
        self._live = live_session
        if self._dispatcher:
            self._live.dispatcher = self._dispatcher

    async def shutdown(self) -> None:
        logger.info("🛑 [LLMRouter] Oprire...")
        if self._live and self._live.is_active:
            self._live.stop_session()
        for task in self._tasks:
            if not task.done():
                task.cancel()
                try: await task
                except asyncio.CancelledError: pass
        logger.info("✅ [LLMRouter] Oprit.")

    @property
    def is_initialized(self) -> bool:
        return self._initialized

    @property
    def dispatcher(self):
        return self._dispatcher
