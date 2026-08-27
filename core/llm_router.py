"""
core/llm_router.py — Chronos LLM Router
=========================================
Creierul de rutare al sistemului. Leagă evenimentele de intrare de executor.

Flow VOCE (post wake word):
    WAKE_WORD_DETECTED
      → AudioInterface.enable_live_mode(queue)
      → GeminiLiveSession.run_session(queue)
          ↳ stream mic → Gemini Native Audio Live API
          ↳ audio response → playback direct
          ↳ barge-in nativ (vorbești → se oprește)
          ↳ tool text → dispatcher
      → AudioInterface.disable_live_mode()
      → Revenim la wake word detection

Flow TERMINAL (cu streaming):
    TERMINAL_COMMAND_RECEIVED
      → ChronosAgent.prepare()            planificare / scurtcircuit determinist
      → agenții de acțiune (lumini, muzică, jurnal)
      → ChronosAgent.stream_chat_reply()  text în bucăți, pe măsură ce vine
          ↳ afișat pe ecran incremental
          ↳ trimis în conducta TTS, care vorbește de la prima clauză

Design:
    - O singură sesiune vocală la un moment dat (_voice_busy lock)
    - Procesarea de terminal nu blochează sesiunile vocale
    - Toate task-urile lansate sunt REȚINUTE (vezi _spawn) — un task fără
      referință tare poate fi colectat de GC în mijlocul execuției
    - Shutdown curat, cu anularea task-urilor
"""

from __future__ import annotations

import asyncio
import logging
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from typing import AsyncIterator, Callable, Iterator, Optional, Set

logger = logging.getLogger(__name__)

try:
    from config import DISPATCHER_TIMEOUT, TTS_SPEAK_TERMINAL_REPLIES
except ImportError:
    DISPATCHER_TIMEOUT = 35.0
    TTS_SPEAK_TERMINAL_REPLIES = False

# Intenție → nume de tool, pentru evenimentele EXECUTE_TOOL de pe bus.
_INTENT_TOOL = {
    "wled_agent": "wled_specialist",
    "music_agent": "music_specialist",
    "logger_agent": "logger_specialist",
    "general_chat": "llm_general_chat",
    "bus": "bus_schedule",
}


def _make_wake_beep():
    """Tonul de confirmare, calculat O SINGURĂ DATĂ la import.

    Înainte se regenera la fiecare wake word: două sinusoide, un linspace și
    două rampe de fade, exact pe calea unde latența se simte cel mai tare.
    E o constantă — nu are ce căuta acolo.
    """
    try:
        import numpy as np
    except ImportError:
        return None, 0
    rate, duration = 44100, 0.18
    t = np.linspace(0, duration, int(rate * duration), endpoint=False)
    wave = (np.sin(2 * np.pi * 700 * t) + np.sin(2 * np.pi * 900 * t)) * 0.22
    fade = int(rate * 0.015)
    if fade > 0:
        wave[:fade] *= np.linspace(0, 1, fade)
        wave[-fade:] *= np.linspace(1, 0, fade)
    return wave.astype(np.float32), rate


_WAKE_BEEP, _WAKE_BEEP_RATE = _make_wake_beep()


class LLMRouter:
    """Router central Chronos — voce (Gemini Live) + terminal."""

    __slots__ = (
        "bus", "EventType", "_audio", "_tts", "_live", "_dispatcher",
        "_initialized", "_tasks", "_background", "_voice_busy", "_agent_pool",
        "_speak_terminal",
    )

    def __init__(self, event_bus, audio_interface=None, tts_engine=None,
                 gemini_live=None):
        """
        Args:
            event_bus:       EventBus.
            audio_interface: AudioInterface (pentru live mode control).
            tts_engine:      TTSEngine (pentru TTS în modul terminal).
            gemini_live:     GeminiLiveSession (pentru voce live).
        """
        from core.event_bus import EventType
        self.bus = event_bus
        self.EventType = EventType

        self._audio = audio_interface
        self._tts = tts_engine
        self._live = gemini_live
        self._dispatcher = None
        self._initialized = False
        self._tasks: list = []
        self._speak_terminal = bool(TTS_SPEAK_TERMINAL_REPLIES)

        # Task-uri lansate „și gata". Fără referință tare, asyncio le poate
        # colecta în mijlocul execuției ("Task was destroyed but it is
        # pending") — comanda dispare pur și simplu, fără urmă în loguri.
        self._background: Set[asyncio.Task] = set()

        # Pool propriu pentru munca agenților (apeluri LLM sincrone, HTTP către
        # WLED/Spotify). Separat de pool-ul default, unde stau Flask și redarea
        # audio: o comandă lentă către lumini n-are voie să întârzie sunetul.
        self._agent_pool: Optional[ThreadPoolExecutor] = None

        # Previne pornirea mai multor sesiuni vocale simultan
        self._voice_busy = asyncio.Lock()

    # ─────────────────────────────────────────────────────
    # INFRASTRUCTURĂ TASK-URI
    # ─────────────────────────────────────────────────────

    def _spawn(self, coro, name: str = "") -> asyncio.Task:
        """Lansează un task și îi păstrează referința până se termină."""
        task = asyncio.create_task(coro, name=name or None)
        self._background.add(task)
        task.add_done_callback(self._on_task_done)
        return task

    def _on_task_done(self, task: asyncio.Task) -> None:
        self._background.discard(task)
        if task.cancelled():
            return
        exc = task.exception()
        if exc is not None:
            logger.error(
                f"❌ [LLMRouter] Task '{task.get_name()}' a eșuat: {exc}",
                exc_info=exc,
            )

    async def _in_pool(self, fn: Callable, *args):
        """Rulează o funcție sincronă pe pool-ul agenților."""
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(self._agent_pool, fn, *args)

    # ─────────────────────────────────────────────────────
    # INIȚIALIZARE
    # ─────────────────────────────────────────────────────

    async def initialize(self) -> bool:
        """Inițializează ChronosAgent și pornește task-urile de ascultare."""
        logger.info("🧠 [LLMRouter] Inițializare...")

        self._agent_pool = ThreadPoolExecutor(
            max_workers=4, thread_name_prefix="chronos-agent"
        )

        try:
            await asyncio.to_thread(self._init_dispatcher_sync)
            logger.info("✅ [LLMRouter] ChronosAgent inițializat.")
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
            asyncio.create_task(self._listen_terminal(), name="router_terminal"),
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
                logger.info(
                    f"\n{'='*60}\n"
                    f"🎯 [LLMRouter] Wake Word! score={data.get('score', 0.0):.3f}\n"
                    f"{'='*60}"
                )
                self._spawn(self._handle_voice_session(), "voice_session")
        except asyncio.CancelledError:
            logger.info("[LLMRouter] Task 'wake_word' anulat.")

    async def _listen_terminal(self) -> None:
        """Ascultă TERMINAL_COMMAND_RECEIVED → procesare + afișare."""
        try:
            async for data in self.bus.subscribe(self.EventType.TERMINAL_COMMAND_RECEIVED):
                text = (data.get("text") or "").strip()
                if not text:
                    continue
                logger.info(
                    f"\n{'='*60}\n"
                    f"⌨️  [LLMRouter] Terminal: '{text}'\n"
                    f"{'='*60}"
                )
                self._spawn(
                    self._process_terminal(text, str(uuid.uuid4())[:8]),
                    "terminal_cmd",
                )
        except asyncio.CancelledError:
            logger.info("[LLMRouter] Task 'terminal' anulat.")

    # ─────────────────────────────────────────────────────
    # VOICE SESSION — Gemini Live API
    # ─────────────────────────────────────────────────────

    async def _handle_voice_session(self) -> None:
        """Gestionează o sesiune vocală completă via Gemini Live API."""
        async with self._voice_busy:
            if self._live is None or not self._live.is_initialized:
                logger.error("❌ [LLMRouter] GeminiLiveSession neinițializat!")
                print("\n❌ Sesiunea vocală Gemini Live nu e disponibilă.")
                return

            if self._audio is None or not self._audio.is_enabled:
                logger.error("❌ [LLMRouter] AudioInterface indisponibil!")
                return

            # Dacă TTS-ul de terminal vorbea, tace: sesiunea vocală are prioritate.
            if self._tts is not None and self._tts.is_playing:
                self._tts.interrupt()

            # ── Beep de confirmare: Chronos a auzit wake word-ul ──
            self._spawn(self._play_wake_beep(), "wake_beep")

            # ── Pauză muzică când începe sesiunea vocală ──
            self._music_control("pause_playback")

            live_queue: asyncio.Queue = asyncio.Queue(maxsize=600)
            self._audio.enable_live_mode(live_queue)
            logger.info("[LLMRouter] Live mode activat → pornesc GeminiLiveSession.")

            try:
                await self._live.run_session(live_queue)
            except Exception as e:
                logger.error(f"❌ [LLMRouter] Eroare sesiune live: {e}", exc_info=True)
            finally:
                self._audio.disable_live_mode()
                logger.info("[LLMRouter] Live mode dezactivat → revenim la wake word.")
                self._music_control("resume_playback")

    def _music_control(self, method: str) -> None:
        """Pauză/resume muzică, fără să blocheze și fără să crape dacă
        agentul de muzică lipsește."""
        agent = getattr(self._dispatcher, "music_agent", None) if self._dispatcher else None
        fn = getattr(agent, method, None)
        if fn is None:
            return
        self._spawn(self._safe_call(fn), f"music_{method}")

    async def _safe_call(self, fn: Callable) -> None:
        try:
            await self._in_pool(fn)
        except Exception as e:
            logger.debug(f"[LLMRouter] {getattr(fn, '__name__', fn)}: {e}")

    async def _play_wake_beep(self) -> None:
        """Redă tonul de confirmare (precomputat la import)."""
        if _WAKE_BEEP is None:
            return
        try:
            import sounddevice as sd

            def _play():
                sd.play(_WAKE_BEEP, _WAKE_BEEP_RATE)
                sd.wait()

            # Un singur salt în thread: `sd.play` + `sd.wait` separate lăsau o
            # fereastră în care alt sunet putea prelua stream-ul global.
            await asyncio.to_thread(_play)
        except Exception as e:
            logger.debug(f"[LLMRouter] Beep eșuat (non-critic): {e}")

    # ─────────────────────────────────────────────────────
    # TERMINAL PROCESSING (cu streaming)
    # ─────────────────────────────────────────────────────

    async def _process_terminal(self, text: str, request_id: str) -> None:
        """Procesează o comandă de terminal.

        Când răspunsul e conversație, textul e consumat în bucăți și trimis
        simultan pe ecran și în conducta TTS — nu se mai așteaptă completarea
        întregului răspuns înainte de primul cuvânt.
        """
        if not self._initialized or not self._dispatcher:
            return

        logger.info(f"⚙️  [LLMRouter] Procesez [terminal] #{request_id}: '{text}'")
        start = time.perf_counter()

        try:
            prepared = await asyncio.wait_for(
                self._in_pool(self._dispatcher.prepare, text),
                timeout=DISPATCHER_TIMEOUT,
            )
        except asyncio.TimeoutError:
            await self._fail(f"Planificarea a depășit {DISPATCHER_TIMEOUT:.0f}s.", request_id)
            return
        except Exception as e:
            logger.error(f"❌ [LLMRouter] Planificare: {e}", exc_info=True)
            await self._fail("Ceva a picat la planificare.", request_id)
            return

        # ── Cale scurtă: răspuns determinist, fără LLM (ex: orar autobuz) ──
        if prepared.get("done"):
            result = prepared.get("result") or {}
            await self._publish_tools(result.get("intents", []), text, request_id,
                                      result.get("reasoning", ""))
            reply = result.get("reply") or ""
            self._print_response(reply, result.get("actions", []), result.get("intents", []))
            if reply:
                await self._maybe_speak_text(reply)
            await self._publish_reply(reply, result, request_id)
            return

        plan = prepared["plan"]
        agents = plan["agents"]
        reasoning = plan["reasoning"]
        data_cats = plan["data_categories"]
        streaming = "general_chat" in agents
        needs_web = plan.get("needs_web", True)

        await self._publish_tools(agents, text, request_id, reasoning)

        # ── Agenții de acțiune (lumini/muzică/jurnal) ──
        try:
            result = await asyncio.wait_for(
                self._in_pool(
                    self._dispatcher.run_agents, agents, text, reasoning,
                    data_cats, streaming, needs_web,
                ),
                timeout=DISPATCHER_TIMEOUT,
            )
        except asyncio.TimeoutError:
            await self._fail(f"Agenții au depășit {DISPATCHER_TIMEOUT:.0f}s.", request_id)
            return
        except Exception as e:
            logger.error(f"❌ [LLMRouter] Execuție agenți: {e}", exc_info=True)
            await self._fail("Ceva a picat la execuție.", request_id)
            return

        self._print_actions(result.get("actions", []), agents)

        reply = result.get("reply") or ""
        if streaming:
            reply = await self._stream_chat(text, data_cats, needs_web)
            result["reply"] = reply
        elif reply:
            print(f"\n🤖 Chronos: {reply}")
            await self._maybe_speak_text(reply)

        logger.info(f"⏱️  [LLMRouter] Total #{request_id}: {time.perf_counter() - start:.2f}s")
        await self._publish_reply(reply, result, request_id)

    async def _stream_chat(self, text: str, data_cats, needs_web: bool = True) -> str:
        """Consumă răspunsul token cu token: îl scrie pe ecran și, dacă e
        activat, îl trimite în conducta TTS în același timp."""
        collected: list = []
        print("\n🤖 Chronos: ", end="", flush=True)

        def _tee(piece: str) -> str:
            collected.append(piece)
            print(piece, end="", flush=True)
            return piece

        source = _aiter_sync(
            lambda: self._dispatcher.stream_chat_reply(text, data_cats, needs_web),
            self._agent_pool,
        )
        tapped = _amap(source, _tee)

        speak = (
            self._speak_terminal
            and self._tts is not None
            and self._tts.is_available
        )
        try:
            if speak:
                await self._tts.speak_stream(tapped)
            else:
                async for _ in tapped:
                    pass
        except Exception as e:
            logger.error(f"❌ [LLMRouter] Streaming răspuns: {e}", exc_info=True)
        print()

        reply = "".join(collected).strip()
        if not reply:
            reply = "Nu am putut genera un răspuns acum, mai încearcă."
            print(f"🤖 Chronos: {reply}")
        return reply

    async def _maybe_speak_text(self, text: str) -> None:
        if (
            self._speak_terminal
            and self._tts is not None
            and self._tts.is_available
            and text
        ):
            await self._tts.speak(text)

    def set_speak_terminal(self, enabled: bool) -> bool:
        """Comută rostirea răspunsurilor din terminal (comanda /speak)."""
        self._speak_terminal = bool(enabled)
        if not enabled and self._tts is not None:
            self._tts.interrupt()
        return self._speak_terminal

    # ─────────────────────────────────────────────────────
    # PUBLICARE PE BUS + OUTPUT
    # ─────────────────────────────────────────────────────

    async def _publish_tools(self, intents, text: str, request_id: str,
                             reasoning: str) -> None:
        for intent in intents:
            await self.bus.publish(
                self.EventType.EXECUTE_TOOL,
                {
                    "tool": _INTENT_TOOL.get(intent, f"specialist_{intent}"),
                    "args": {"command": text, "intent": intent},
                    "source": "terminal",
                    "request_id": request_id,
                    "reasoning": reasoning,
                },
            )

    async def _publish_reply(self, reply: str, result: dict, request_id: str) -> None:
        await self.bus.publish(
            self.EventType.LLM_TEXT_RESPONSE,
            {
                "text": reply or "",
                "source": "terminal",
                "request_id": request_id,
                "intents": result.get("intents", []),
                "actions": result.get("actions", []),
            },
        )

    async def _fail(self, msg: str, request_id: str) -> None:
        logger.error(f"❌ [LLMRouter] #{request_id}: {msg}")
        full = f"{msg} Încearcă din nou."
        self._print_response(full, [], [])
        await self._maybe_speak_text(full)

    @staticmethod
    def _print_actions(actions, intents) -> None:
        sep = "─" * 60
        print(f"\n{sep}")
        if intents:
            print(f"🎯 {' | '.join(f'[{i.upper()}]' for i in intents)}")
        if actions:
            print("⚡ Acțiuni:")
            for a in actions:
                ok = isinstance(a, dict) and a.get("status") in ("ok", "success")
                txt = a.get("text", str(a)) if isinstance(a, dict) else str(a)
                print(f"   {'✅' if ok else '❌'} {txt}")

    def _print_response(self, reply, actions, intents) -> None:
        self._print_actions(actions, intents)
        if reply:
            print(f"\n🤖 Chronos: {reply}")
        elif not actions:
            print("🤖 Chronos: Comandă procesată.")
        print("─" * 60)

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

        for task in [*self._tasks, *self._background]:
            if not task.done():
                task.cancel()
        pending = [t for t in [*self._tasks, *self._background] if not t.done()]
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)
        self._tasks.clear()
        self._background.clear()

        if self._agent_pool is not None:
            self._agent_pool.shutdown(wait=False)
            self._agent_pool = None
        logger.info("✅ [LLMRouter] Oprit.")

    @property
    def is_initialized(self) -> bool:
        return self._initialized

    @property
    def dispatcher(self):
        return self._dispatcher


# =============================================================================
# PUNTE SINCRON → ASINCRON
# =============================================================================

async def _aiter_sync(
    gen_factory: Callable[[], Iterator[str]],
    pool: Optional[ThreadPoolExecutor] = None,
) -> AsyncIterator[str]:
    """Transformă un generator SINCRON într-unul asincron.

    `stream_gemini_text` e sincron (requests cu stream=True): iterat direct pe
    buclă ar bloca tot sistemul între bucăți. Aici rulează pe un thread și
    împinge bucățile în bucla asyncio pe măsură ce sosesc.
    """
    loop = asyncio.get_running_loop()
    queue: asyncio.Queue = asyncio.Queue()   # nemărginită: bucăți mici, număr finit
    done = object()

    def _pump() -> None:
        try:
            for item in gen_factory():
                loop.call_soon_threadsafe(queue.put_nowait, item)
        except Exception as e:          # noqa: BLE001 - propagat în consumator
            loop.call_soon_threadsafe(queue.put_nowait, e)
        finally:
            loop.call_soon_threadsafe(queue.put_nowait, done)

    future = loop.run_in_executor(pool, _pump)
    try:
        while True:
            item = await queue.get()
            if item is done:
                break
            if isinstance(item, Exception):
                logger.error(f"❌ [LLMRouter] Stream sursă: {item}")
                break
            yield item
    finally:
        # Threadul se termină singur; îl așteptăm ca să nu rămână orfan.
        try:
            await future
        except Exception:
            pass


async def _amap(source: AsyncIterator[str], fn: Callable[[str], str]) -> AsyncIterator[str]:
    """Aplică `fn` fiecărei bucăți în trecere (folosit ca să afișăm textul pe
    ecran în același timp în care pleacă spre sinteză)."""
    async for item in source:
        yield fn(item)
