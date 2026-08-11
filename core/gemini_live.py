"""
core/gemini_live.py — Chronos Gemini Live Voice Session v5
============================================================
Schimbări v5:

1. TIMER INACTIVITATE CORECT
   - _last_turn_end: când AI a terminat de vorbit (reset la turn_complete)
   - Countdown porneste DUPĂ ce AI termină, nu în timp ce vorbeste
   - Dacă user vorbeste → reset timer
   - Dacă AI vorbeste → timer pauzat (nu se numără)
   Bug v4: _last_user_speech = time.time() în while AI vorbeste → niciodată nu expira

2. TOOL CALLING PRIN DISPATCHER
   - control_lights(command) → dispatcher.process_text_command → wled_specialist
   - control_music(command)  → dispatcher.process_text_command → music_specialist
   - save_journal(entry)     → dispatcher.process_text_command → logger
   - end_session()           → închide sesiunea imediat

3. ECHO PREVENTION (din v4, neschimbat)
   - Nu trimitem audio la Gemini cât timp AI vorbeste
   - Interrupt real: RMS > prag pentru 2s continuu
"""

import asyncio
import logging
import time
import threading
from typing import Optional

logger = logging.getLogger(__name__)

try:
    from config import (
        LIVE_MODEL, LIVE_VOICE,
        LIVE_SAMPLE_RATE_IN, LIVE_SAMPLE_RATE_OUT,
        LIVE_INACTIVITY_TIMEOUT, LIVE_START_DELAY_MS,
        LIVE_PLAYBACK_CHUNK_BYTES, SYSTEM_PROMPT, GEMINI_API_KEY,
        INTERRUPT_AMPLITUDE_THRESHOLD, INTERRUPT_MIN_DURATION,
    )
except ImportError:
    LIVE_MODEL                    = "gemini-2.5-flash-native-audio-latest"
    LIVE_VOICE                    = "Charon"
    LIVE_SAMPLE_RATE_IN           = 16000
    LIVE_SAMPLE_RATE_OUT          = 24000
    LIVE_INACTIVITY_TIMEOUT       = 8.0
    LIVE_START_DELAY_MS           = 300
    LIVE_PLAYBACK_CHUNK_BYTES     = 2048
    SYSTEM_PROMPT                 = "Ești Chronos. Răspunzi în română."
    GEMINI_API_KEY                = ""
    INTERRUPT_AMPLITUDE_THRESHOLD = 1500
    INTERRUPT_MIN_DURATION        = 2.0

_FLUSH_SENTINEL = object()
_END_SENTINEL   = object()


class GeminiLiveSession:
    """
    Sesiune vocală Gemini Native Audio.
    Tool-urile sunt rutate prin dispatcher-ul existent (wled_specialist, etc.)
    """

    def __init__(self, event_bus, dispatcher=None):
        self.bus        = event_bus
        self.dispatcher = dispatcher

        self._client = None
        self._types  = None
        self._sd     = None
        self._np     = None

        self._initialized        = False
        self._session_active     = False
        self._ai_is_speaking     = False

        # Timer inactivitate: moment când AI a terminat ultimul turn
        # (countdown porneste de ATUNCI, nu din timpul AI speech)
        self._last_turn_end      = 0.0

        # Interrupt detection (echo prevention)
        self._interrupt_start    = None

        # Playback
        self._audio_out_queue    = asyncio.Queue(maxsize=400)
        self._stop_playback      = threading.Event()
        self._close_after_turn   = False

        self._ET = None
        try:
            from core.event_bus import EventType
            self._ET = EventType
        except ImportError:
            pass

    # ─────────────────────────────────────────────────────────
    # INIȚIALIZARE
    # ─────────────────────────────────────────────────────────

    async def initialize(self) -> bool:
        logger.info("🔗 [GeminiLive] Inițializare...")
        try:
            from google import genai
            from google.genai import types
            self._client = genai.Client(api_key=GEMINI_API_KEY)
            self._types  = types
        except ImportError:
            logger.error("❌ [GeminiLive] google-genai lipsă!")
            return False
        try:
            import sounddevice as sd
            import numpy as np
            self._sd = sd
            self._np = np
        except ImportError:
            logger.error("❌ [GeminiLive] sounddevice lipsă!")
            return False

        self._initialized = True
        logger.info(
            f"✅ [GeminiLive] Gata.\n"
            f"   Model: {LIVE_MODEL} | Voce: {LIVE_VOICE}\n"
            f"   Timeout: {LIVE_INACTIVITY_TIMEOUT}s | "
            f"Interrupt: RMS>{INTERRUPT_AMPLITUDE_THRESHOLD} × {INTERRUPT_MIN_DURATION}s"
        )
        return True

    # ─────────────────────────────────────────────────────────
    # TOOL DECLARATIONS (pentru Gemini Live API)
    # ─────────────────────────────────────────────────────────

    def _build_tools(self) -> list:
        """
        Declară funcțiile pe care Gemini le poate apela.
        Execuția e rutată prin dispatcher (wled_specialist, music_specialist, etc.)
        """
        types = self._types
        try:
            return [types.Tool(function_declarations=[

                types.FunctionDeclaration(
                    name="control_lights",
                    description=(
                        "Controlează luminile LED WLED din camera lui Sergiu. "
                        "Trimite comanda LITERALE în română exact cum a spus-o Sergiu. "
                        "Exemple: 'pune luminile roșii', 'stinge luminile', 'mod curcubeu'."
                    ),
                    parameters={
                        "type": "object",
                        "properties": {
                            "command": {
                                "type": "string",
                                "description": "Comanda originală pentru lumini."
                            },
                        },
                        "required": ["command"],
                    },
                ),

                types.FunctionDeclaration(
                    name="control_music",
                    description=(
                        "Controlează muzica pe Spotify / Google Home speaker. "
                        "Trimite comanda LITERALE a lui Sergiu (ex: 'vreau muzică rock', "
                        "'pune ceva latină', 'mărește volumul', 'pauză'). "
                        "NU alege tu piesa! Agentul specializat DJ va alege piesa potrivită."
                    ),
                    parameters={
                        "type": "object",
                        "properties": {
                            "command": {
                                "type": "string",
                                "description": "Comanda originală pentru muzică."
                            },
                        },
                        "required": ["command"],
                    },
                ),

                types.FunctionDeclaration(
                    name="execute_command",
                    description=(
                        "Execută o comandă combinată sau de atmosferă (ex: 'atmosferă de munte', "
                        "'schimbă muzica și luminile'). Se rutează în paralel către toți agenții."
                    ),
                    parameters={
                        "type": "object",
                        "properties": {
                            "command": {
                                "type": "string",
                                "description": "Comanda originală a utilizatorului."
                            },
                        },
                        "required": ["command"],
                    },
                ),

                types.FunctionDeclaration(
                    name="save_journal",
                    description=(
                        "Salvează o notă sau gând în jurnalul personal al lui Sergiu."
                    ),
                    parameters={
                        "type": "object",
                        "properties": {
                            "entry": {
                                "type": "string",
                                "description": "Textul de salvat în jurnal."
                            },
                        },
                        "required": ["entry"],
                    },
                ),

                types.FunctionDeclaration(
                    name="end_session",
                    description=(
                        "Termină sesiunea vocală. Apelează IMEDIAT când utilizatorul "
                        "spune: 'pa', 'la revedere', 'stop', 'taci', 'gata', "
                        "'terminat', 'ieși', 'opreste-te', 'bye' sau orice rămas-bun."
                    ),
                    parameters={
                        "type": "object",
                        "properties": {
                            "reason": {
                                "type": "string",
                                "description": "Motivul închiderii (opțional)."
                            },
                        },
                        "required": [],
                    },
                ),

            ])]
        except Exception as e:
            logger.warning(f"⚠️ [GeminiLive] Tool declarations eșuate: {e}")
            return []

    # ─────────────────────────────────────────────────────────
    # TOOL EXECUTION — prin dispatcher
    # ─────────────────────────────────────────────────────────

    async def _handle_tool_call(self, session, tool_call) -> bool:
        """
        Primim un tool_call de la Gemini → executăm → trimitem FunctionResponse.

        Returns:
            True  → sesiunea continuă
            False → sesiunea trebuie închisă (end_session apelat)
        """
        fcs = getattr(tool_call, "function_calls", [])
        if not fcs:
            return True

        responses = []
        should_close = False

        for fc in fcs:
            name  = getattr(fc, "name", "unknown")
            args  = dict(fc.args) if getattr(fc, "args", None) else {}
            fc_id = getattr(fc, "id", None)

            logger.info(f"🔧 [GeminiLive] Tool call: {name}({args})")

            # ── end_session: închide sesiunea ──
            if name == "end_session":
                logger.info("👋 [GeminiLive] end_session apelat → sesiune terminată.")
                should_close = True
                result = {"status": "ok", "message": "Sesiune terminată."}

            # ── Dispatcher tools ──
            elif name in ("control_lights", "control_music", "save_journal", "execute_command"):
                command = args.get("command") or args.get("entry") or ""
                result  = await self._dispatch(name, command)
                # Auto-close după ce Gemini confirmă scurt audio acțiunea
                self._close_after_turn = True

            else:
                result = {"status": "error", "message": f"Tool necunoscut: {name}"}

            responses.append(self._types.FunctionResponse(
                id=fc_id, name=name, response=result
            ))

        # Trimitem răspunsul la Gemini (va genera un audio de confirmare)
        try:
            await session.send_tool_response(function_responses=responses)
            logger.info(f"✅ [GeminiLive] Tool response trimis ({len(responses)} funcții).")
        except Exception as e:
            logger.error(f"❌ [GeminiLive] send_tool_response: {e}")

        return not should_close

    async def _dispatch(self, tool_name: str, command: str) -> dict:
        """
        Rutează comanda prin dispatcher-ul existent în fundal (asincron).
        Dispatcher → intent detection → specialist (wled, music, logger, etc.)
        """
        if not self.dispatcher:
            logger.warning("[GeminiLive] Dispatcher indisponibil.")
            return {"status": "error", "executed": False, "message": "Dispatcher indisponibil."}

        logger.info(f"🔄 [GeminiLive] Dispatch RAW background: {tool_name} → '{command}'")

        try:
            # Lansăm comanda în background fără să blocăm răspunsul vocal!
            # process_text_command este funcție sincronă, de aceea folosim asyncio.to_thread!
            asyncio.create_task(
                asyncio.to_thread(self.dispatcher.process_text_command, command, "voice_live")
            )

            return {
                "status": "success",
                "executed": True,
                "info": "Comanda a fost transmisă cu succes la agenții specializați. Confirmă-i INSTANT și scurt lui Sergiu pe tonul tău de boss că s-a rezolvat (ex: 'Gata tati, am dat comanda!')."
            }
        except Exception as e:
            logger.error(f"❌ [GeminiLive] Dispatch error: {e}", exc_info=True)
            return {"status": "error", "executed": False, "error_details": str(e)}

    # ─────────────────────────────────────────────────────────
    # SESIUNE PRINCIPALĂ
    # ─────────────────────────────────────────────────────────

    async def run_session(self, mic_queue: asyncio.Queue) -> None:
        if not self._initialized:
            logger.error("❌ [GeminiLive] Neinițializat!")
            return

        self._session_active   = True
        self._ai_is_speaking   = False
        self._close_after_turn = False
        self._last_turn_end    = time.time()   # Countdown porneste imediat
        self._interrupt_start  = None
        self._stop_playback.clear()

        # Flush audio vechi
        while not self._audio_out_queue.empty():
            try: self._audio_out_queue.get_nowait()
            except: break

        # Delay wake word
        if LIVE_START_DELAY_MS > 0:
            deadline = asyncio.get_event_loop().time() + LIVE_START_DELAY_MS / 1000.0
            while asyncio.get_event_loop().time() < deadline:
                try: mic_queue.get_nowait()
                except asyncio.QueueEmpty: await asyncio.sleep(0.04)

        # Config cu tools
        tools = self._build_tools()
        cfg_kw = dict(
            response_modalities=["AUDIO"],
            speech_config=self._types.SpeechConfig(
                voice_config=self._types.VoiceConfig(
                    prebuilt_voice_config=self._types.PrebuiltVoiceConfig(
                        voice_name=LIVE_VOICE
                    )
                )
            ),
            system_instruction=SYSTEM_PROMPT,
        )
        if tools:
            cfg_kw["tools"] = tools

        config = self._types.LiveConnectConfig(**cfg_kw)

        try:
            async with self._client.aio.live.connect(
                model=LIVE_MODEL, config=config
            ) as session:
                logger.info("🔗 [GeminiLive] Sesiune WebSocket deschisă.")
                print(
                    f"\n🎙️  Chronos ascultă!\n"
                    f"   Timeout {LIVE_INACTIVITY_TIMEOUT}s | "
                    f"Spune 'pa' pentru a închide"
                )

                if self._ET:
                    await self.bus.publish(
                        self._ET.AUDIO_LISTENING_START, {"source": "gemini_live"}
                    )

                playback_task = asyncio.create_task(
                    self._playback_loop(), name="live_playback"
                )
                send_task = asyncio.create_task(
                    self._send_mic_loop(session, mic_queue), name="live_send"
                )
                receive_task = asyncio.create_task(
                    self._receive_loop(session), name="live_receive"
                )

                # send_task controlează durata sesiunii
                try:
                    await send_task
                except asyncio.CancelledError:
                    pass
                except Exception as e:
                    logger.error(f"❌ [GeminiLive] send_task: {type(e).__name__}: {e}")
                finally:
                    self._session_active = False
                    if not receive_task.done():
                        receive_task.cancel()
                        try: await asyncio.wait_for(receive_task, timeout=1.0)
                        except (asyncio.CancelledError, asyncio.TimeoutError): pass

                    self._stop_playback.set()
                    await self._audio_out_queue.put(_END_SENTINEL)
                    try: await asyncio.wait_for(playback_task, timeout=2.0)
                    except (asyncio.TimeoutError, asyncio.CancelledError):
                        playback_task.cancel()

        except Exception as e:
            logger.error(f"❌ [GeminiLive] WebSocket: {type(e).__name__}: {e}", exc_info=True)
        finally:
            self._session_active = False
            self._ai_is_speaking = False
            if self._ET:
                await self.bus.publish(
                    self._ET.AUDIO_RESPONSE_END, {"completed": True}
                )
            print("\n✅ Sesiune vocală încheiată. Spune 'Jarvis' pentru a relua.")

    # ─────────────────────────────────────────────────────────
    # SEND MIC → GEMINI (cu echo prevention + inactivity fix)
    # ─────────────────────────────────────────────────────────

    async def _send_mic_loop(self, session, mic_queue: asyncio.Queue) -> None:
        """
        Timer inactivitate CORECT:
        ┌──────────────────────────────────────────────────────────┐
        │ AI VORBESTE  → timer PAUZAT (nu se numără, nu se resetează) │
        │ AI terminat  → _last_turn_end = now (countdown porneste)   │
        │ User vorbeste → _last_turn_end = now (reset countdown)     │
        │ Tăcere > TIMEOUT (după AI response) → sesiune terminată    │
        └──────────────────────────────────────────────────────────┘
        """
        logger.debug("[GeminiLive:send] Loop pornit.")
        chunks_sent   = 0
        warned_close  = False
        np            = self._np

        while self._session_active:
            try:
                chunk = await asyncio.wait_for(mic_queue.get(), timeout=0.3)
            except asyncio.TimeoutError:
                # ── AI vorbeste: timer pauzat ──
                if self._ai_is_speaking:
                    continue   # Nu schimbăm _last_turn_end, nu numărăm

                # ── AI nu vorbeste: verificăm inactivitate ──
                elapsed   = time.time() - self._last_turn_end
                remaining = LIVE_INACTIVITY_TIMEOUT - elapsed

                if remaining <= 3.0 and not warned_close and remaining > 0:
                    warned_close = True
                    print(f"\n⏰ Sesiunea se închide în ~{int(remaining)}s...")

                if elapsed > LIVE_INACTIVITY_TIMEOUT:
                    logger.info(
                        f"⏰ [GeminiLive] {elapsed:.0f}s inactivitate "
                        f"(prag={LIVE_INACTIVITY_TIMEOUT}s) → sesiune terminată."
                    )
                    self._session_active = False
                    break
                continue

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"❌ [GeminiLive:send] mic_queue: {e}")
                break

            # ══ Avem un chunk audio din microfon ══

            # ── ECHO PREVENTION: AI vorbeste → nu trimitem ──
            if self._ai_is_speaking:
                rms = float(np.sqrt(np.mean(chunk.astype(np.float32) ** 2)))

                if rms > INTERRUPT_AMPLITUDE_THRESHOLD:
                    if self._interrupt_start is None:
                        self._interrupt_start = time.time()
                        logger.debug(
                            f"[GeminiLive:send] 🗣️ Posibil interrupt RMS={rms:.0f}"
                        )

                    if time.time() - self._interrupt_start >= INTERRUPT_MIN_DURATION:
                        # Interrupt REAL
                        logger.info(
                            f"🗣️ [GeminiLive] INTERRUPT: "
                            f"{time.time()-self._interrupt_start:.1f}s RMS={rms:.0f}"
                        )
                        self._stop_playback.set()
                        while not self._audio_out_queue.empty():
                            try:
                                item = self._audio_out_queue.get_nowait()
                                if item is _END_SENTINEL:
                                    await self._audio_out_queue.put(_END_SENTINEL)
                                    break
                            except asyncio.QueueEmpty:
                                break
                        self._ai_is_speaking = False
                        await asyncio.sleep(0.05)
                        self._stop_playback.clear()
                        self._interrupt_start = None
                        # Trimitem chunk-ul ACUM că am întrerupt
                        try:
                            await session.send_realtime_input(
                                audio=self._types.Blob(
                                    data=chunk.astype("int16").tobytes(),
                                    mime_type=f"audio/pcm;rate={LIVE_SAMPLE_RATE_IN}"
                                )
                            )
                            chunks_sent += 1
                            self._last_turn_end = time.time()
                            warned_close = False
                        except Exception as e:
                            logger.error(f"❌ [GeminiLive:send] post-interrupt: {e}")
                            self._session_active = False
                            break
                else:
                    self._interrupt_start = None

                continue  # Skip trimitere normală cât timp AI vorbeste

            # ── NORMAL: AI nu vorbeste, trimitem audio ──
            self._interrupt_start = None

            try:
                await session.send_realtime_input(
                    audio=self._types.Blob(
                        data=chunk.astype("int16").tobytes(),
                        mime_type=f"audio/pcm;rate={LIVE_SAMPLE_RATE_IN}"
                    )
                )
                chunks_sent += 1
                self._last_turn_end = time.time()   # User a vorbit → reset countdown
                warned_close = False
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"❌ [GeminiLive:send] trimitere: {type(e).__name__}: {e}")
                self._session_active = False
                break

        logger.info(f"[GeminiLive:send] Terminat. Chunks: {chunks_sent}")

    # ─────────────────────────────────────────────────────────
    # RECEIVE ← GEMINI (re-entrant multi-turn)
    # ─────────────────────────────────────────────────────────

    async def _receive_loop(self, session) -> None:
        """
        Re-entrant receive: session.receive() se termină după fiecare turn_complete.
        while loop re-intră pentru turnul următor.
        """
        logger.debug("[GeminiLive:recv] Loop pornit.")
        total  = 0
        turns  = 0

        while self._session_active:
            try:
                async for response in session.receive():
                    if not self._session_active:
                        return

                    total += 1

                    sc = getattr(response, "server_content", None)
                    tc = getattr(response, "tool_call", None)

                    # ── Tool Call ──
                    if tc:
                        continue_session = await self._handle_tool_call(session, tc)
                        if not continue_session:
                            logger.info("[GeminiLive:recv] end_session → opresc sesiunea.")
                            self._session_active = False
                            return
                        continue

                    if not sc:
                        continue

                    interrupted   = getattr(sc, "interrupted",   False)
                    turn_complete = getattr(sc, "turn_complete", False)
                    mt            = getattr(sc, "model_turn",    None)

                    # Barge-in server-side (rar cu echo prevention)
                    if interrupted:
                        logger.info("⛔ [GeminiLive] Barge-in (interrupted). Anulez auto-close pentru că utilizatorul dorește altceva.")
                        self._ai_is_speaking   = False
                        self._close_after_turn = False  # Întreruperea anulează auto-close!
                        self._last_turn_end    = time.time()
                        continue

                    # Audio / text de la model
                    if mt:
                        self._ai_is_speaking = True
                        for part in (mt.parts or []):
                            id_ = getattr(part, "inline_data", None)
                            if id_ and getattr(id_, "data", None):
                                await self._audio_out_queue.put(id_.data)
                            txt = getattr(part, "text", None)
                            if txt and txt.strip():
                                logger.debug(f"[GeminiLive:recv] Text: {txt.strip()[:80]!r}")

                    # Turn complet
                    if turn_complete:
                        turns += 1
                        self._ai_is_speaking = False
                        self._last_turn_end  = time.time()
                        await self._audio_out_queue.put(_FLUSH_SENTINEL)
                        logger.info(
                            f"✅ [GeminiLive] Turn #{turns} complet. "
                            f"Countdown {LIVE_INACTIVITY_TIMEOUT}s pornit."
                        )

                        # Auto-close automat după ce comanda de acțiune a fost confirmată
                        if self._close_after_turn:
                            logger.info("👋 [GeminiLive] Auto-close activat după executarea acțiunii → Închid sesiunea.")
                            self._close_after_turn = False
                            self._session_active = False
                            return

                # session.receive() s-a terminat → re-intrăm pentru turnul următor
                if self._session_active:
                    await asyncio.sleep(0.05)

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.info(f"ℹ️ [GeminiLive:recv] Conexiune încheiată ({type(e).__name__}): {e}")
                self._session_active = False
                break

        logger.info(f"[GeminiLive:recv] Terminat. {total} răspunsuri, {turns} turnuri.")
        self._ai_is_speaking = False

    # ─────────────────────────────────────────────────────────
    # PLAYBACK
    # ─────────────────────────────────────────────────────────

    async def _playback_loop(self) -> None:
        buffer = bytearray()
        try:
            stream = self._sd.RawOutputStream(
                samplerate=LIVE_SAMPLE_RATE_OUT,
                channels=1,
                dtype="int16",
                blocksize=1024,
            )
            stream.start()
            logger.debug("[GeminiLive:play] Stream pornit.")

            if self._ET:
                await self.bus.publish(self._ET.AUDIO_RESPONSE_START, {})

            while True:
                try:
                    data = await asyncio.wait_for(
                        self._audio_out_queue.get(), timeout=0.2
                    )
                except asyncio.TimeoutError:
                    if buffer and not self._stop_playback.is_set():
                        try: await asyncio.to_thread(stream.write, bytes(buffer))
                        except Exception: pass
                        buffer.clear()
                    continue

                if data is _END_SENTINEL:
                    if buffer and not self._stop_playback.is_set():
                        try: await asyncio.to_thread(stream.write, bytes(buffer))
                        except Exception: pass
                    break

                if data is _FLUSH_SENTINEL:
                    if buffer and not self._stop_playback.is_set():
                        try: await asyncio.to_thread(stream.write, bytes(buffer))
                        except Exception: pass
                        buffer.clear()
                    continue

                if self._stop_playback.is_set():
                    buffer.clear()
                    while not self._audio_out_queue.empty():
                        try:
                            item = self._audio_out_queue.get_nowait()
                            if item is _END_SENTINEL:
                                await self._audio_out_queue.put(_END_SENTINEL)
                                break
                            if item is _FLUSH_SENTINEL:
                                break
                        except asyncio.QueueEmpty:
                            break
                    continue

                buffer.extend(data)
                while len(buffer) >= LIVE_PLAYBACK_CHUNK_BYTES:
                    if self._stop_playback.is_set():
                        buffer.clear()
                        break
                    chunk = bytes(buffer[:LIVE_PLAYBACK_CHUNK_BYTES])
                    buffer = bytearray(buffer[LIVE_PLAYBACK_CHUNK_BYTES:])
                    try: await asyncio.to_thread(stream.write, chunk)
                    except Exception as e:
                        logger.error(f"❌ [GeminiLive:play] Write: {e}")
                        break

        except asyncio.CancelledError:
            pass
        except Exception as e:
            logger.error(f"❌ [GeminiLive:play] {e}", exc_info=True)
        finally:
            try:
                stream.stop()
                stream.close()
            except Exception:
                pass
            self._ai_is_speaking = False
            logger.debug("[GeminiLive:play] Stream închis.")

    # ─────────────────────────────────────────────────────────
    # CONTROL PUBLIC
    # ─────────────────────────────────────────────────────────

    def interrupt(self) -> None:
        self._stop_playback.set()
        self._ai_is_speaking = False

    def stop_session(self) -> None:
        self._session_active = False
        self._stop_playback.set()

    @property
    def is_active(self) -> bool:
        return self._session_active

    @property
    def is_initialized(self) -> bool:
        return self._initialized
