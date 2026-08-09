"""
core/gemini_live.py — Chronos Gemini Live Voice Session v4
============================================================
FIX v4 — 3 probleme rezolvate:

1. ECHO PREVENTION (false barge-in)
   ─ Cauza: boxele redau vocea → mic o captează → Gemini crede că vorbești
   ─ Fix:   NU trimitem audio la Gemini CÂT TIMP AI vorbeste
   ─ Întrerupere reală: detectăm vorbire tare (RMS > prag) susținut 2s

2. RE-ENTRANT RECEIVE LOOP
   ─ Cauza: session.receive() se termină după fiecare turn_complete
   ─ Fix:   while loop care re-intră în session.receive() pentru fiecare turn

3. TOOL CALLING (LED-uri, muzică)
   ─ Cauza: Gemini nu avea function declarations → doar vorbea despre tool-uri
   ─ Fix:   Declarăm funcții (control_leds) → Gemini le apelează → le executăm
"""

import asyncio
import json
import logging
import time
import threading
import urllib.request
from typing import Optional

logger = logging.getLogger(__name__)

try:
    from config import (
        LIVE_MODEL, LIVE_VOICE,
        LIVE_SAMPLE_RATE_IN, LIVE_SAMPLE_RATE_OUT,
        LIVE_INACTIVITY_TIMEOUT, LIVE_START_DELAY_MS,
        LIVE_PLAYBACK_CHUNK_BYTES, SYSTEM_PROMPT, GEMINI_API_KEY,
        INTERRUPT_AMPLITUDE_THRESHOLD, INTERRUPT_MIN_DURATION,
        WLED_IP_MAIN, WLED_IP_FLOOR,
    )
except ImportError:
    LIVE_MODEL               = "gemini-2.5-flash-native-audio-latest"
    LIVE_VOICE               = "Charon"
    LIVE_SAMPLE_RATE_IN      = 16000
    LIVE_SAMPLE_RATE_OUT     = 24000
    LIVE_INACTIVITY_TIMEOUT  = 15.0
    LIVE_START_DELAY_MS      = 300
    LIVE_PLAYBACK_CHUNK_BYTES = 2048
    SYSTEM_PROMPT            = "Ești Chronos. Răspunzi în română."
    GEMINI_API_KEY           = ""
    INTERRUPT_AMPLITUDE_THRESHOLD = 1500
    INTERRUPT_MIN_DURATION   = 2.0
    WLED_IP_MAIN             = "192.168.68.101"
    WLED_IP_FLOOR            = "192.168.68.102"

# Sentinele distincte (object() — identitate garantată, nu valoare)
_FLUSH_SENTINEL = object()   # Flush buffer, sesiune continuă
_END_SENTINEL   = object()   # Sesiune terminată definitiv


class GeminiLiveSession:
    """
    Sesiune vocală Gemini Native Audio cu:
    - Echo prevention (mic muted în timpul playback)
    - Amplitude-gated interrupt (2s vorbire tare = oprire reală)
    - Re-entrant receive (multi-turn persistent)
    - Tool calling (LED-uri, muzică)
    """

    def __init__(self, event_bus, dispatcher=None):
        self.bus        = event_bus
        self.dispatcher = dispatcher

        self._client = None
        self._types  = None
        self._sd     = None
        self._np     = None

        self._initialized      = False
        self._session_active   = False
        self._ai_is_speaking   = False
        self._last_user_speech = 0.0

        # Interrupt detection
        self._interrupt_speech_start = None   # Când a început vorbirea tare

        # Audio playback
        self._audio_out_queue = asyncio.Queue(maxsize=400)
        self._stop_playback   = threading.Event()

        # Event types
        self._ET = None
        try:
            from core.event_bus import EventType
            self._ET = EventType
        except ImportError:
            pass

    # ─── INIȚIALIZARE ────────────────────────────────────────

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
            f"   Model: {LIVE_MODEL}\n"
            f"   Voce:  {LIVE_VOICE}\n"
            f"   Timeout inactivitate: {LIVE_INACTIVITY_TIMEOUT}s\n"
            f"   Interrupt: RMS>{INTERRUPT_AMPLITUDE_THRESHOLD} pentru {INTERRUPT_MIN_DURATION}s"
        )
        return True

    # ─── TOOL DECLARATIONS ───────────────────────────────────

    def _build_tools(self):
        """
        Construiește function declarations pentru Gemini Live.
        Gemini poate apela aceste funcții direct în sesiunea vocală.
        """
        types = self._types
        try:
            return [types.Tool(function_declarations=[
                types.FunctionDeclaration(
                    name="control_leds",
                    description=(
                        "Controlează luminile LED WLED din camera lui Sergiu. "
                        "Poți schimba culoarea (RGB), luminozitatea, sau stinge/aprinde. "
                        "Exemple: roșu=(255,0,0), albastru=(0,0,255), "
                        "alb cald=(255,180,80), violet=(128,0,255), "
                        "verde=(0,255,0), portocaliu=(255,100,0), "
                        "cyan=(0,255,255), roz=(255,50,150)."
                    ),
                    parameters={
                        "type": "object",
                        "properties": {
                            "red":        {"type": "integer", "description": "Componenta roșie 0-255"},
                            "green":      {"type": "integer", "description": "Componenta verde 0-255"},
                            "blue":       {"type": "integer", "description": "Componenta albastră 0-255"},
                            "brightness": {"type": "integer", "description": "Luminozitate generală 0-255. Default 150."},
                            "turn_off":   {"type": "boolean", "description": "True pentru a stinge luminile complet."},
                        },
                        "required": ["red", "green", "blue"],
                    },
                ),
            ])]
        except Exception as e:
            logger.warning(f"⚠️ [GeminiLive] Tool declarations eșuate: {e}")
            return []

    # ─── TOOL EXECUTION ──────────────────────────────────────

    async def _execute_tool(self, name: str, args: dict) -> dict:
        """Execută un tool call și returnează rezultatul."""
        logger.info(f"🔧 [GeminiLive] Execut tool: {name}({args})")
        try:
            if name == "control_leds":
                return await self._exec_led(args)
            else:
                return {"status": "error", "message": f"Tool necunoscut: {name}"}
        except Exception as e:
            logger.error(f"❌ [GeminiLive] Tool {name} eșuat: {e}")
            return {"status": "error", "message": str(e)}

    async def _exec_led(self, args: dict) -> dict:
        """Trimite comandă WLED via HTTP API direct (fără dispatcher)."""
        r   = int(args.get("red",   255))
        g   = int(args.get("green", 0))
        b   = int(args.get("blue",  0))
        bri = int(args.get("brightness", 150))
        off = bool(args.get("turn_off", False))

        payload = json.dumps({
            "on":  not off,
            "bri": max(0, min(255, bri)),
            "seg": [{"col": [[r, g, b]]}],
        }).encode()

        results = []
        for ip in [WLED_IP_MAIN, WLED_IP_FLOOR]:
            try:
                req = urllib.request.Request(
                    f"http://{ip}/json/state",
                    data=payload,
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                status = await asyncio.to_thread(
                    lambda req=req: urllib.request.urlopen(req, timeout=3).status
                )
                results.append(f"OK {ip}: {status}")
                logger.info(f"💡 [WLED] {ip} → RGB({r},{g},{b}) bri={bri}: {status}")
            except Exception as e:
                results.append(f"ERR {ip}: {e}")
                logger.error(f"❌ [WLED] {ip}: {e}")

        return {"status": "ok", "led_color": f"RGB({r},{g},{b})", "brightness": bri, "details": results}

    async def _handle_tool_call(self, session, tool_call) -> None:
        """
        Primim un tool call de la Gemini → executăm → trimitem răspunsul înapoi.
        Gemini va continua cu un răspuns audio bazat pe rezultat.
        """
        fcs = getattr(tool_call, "function_calls", [])
        if not fcs:
            logger.warning("[GeminiLive] tool_call fără function_calls!")
            return

        responses = []
        for fc in fcs:
            name = getattr(fc, "name", "unknown")
            args = dict(fc.args) if getattr(fc, "args", None) else {}

            result = await self._execute_tool(name, args)

            # Construim FunctionResponse — id e OBLIGATORIU (din fc.id)
            fc_id = getattr(fc, "id", None)
            responses.append(self._types.FunctionResponse(
                id=fc_id,
                name=name,
                response=result,
            ))

        try:
            await session.send_tool_response(function_responses=responses)
            logger.info(f"✅ [GeminiLive] Tool response trimis ({len(responses)} funcții).")
        except Exception as e:
            logger.error(f"❌ [GeminiLive] send_tool_response eșuat: {e}")

    # ─── SESIUNE PRINCIPALĂ ──────────────────────────────────

    async def run_session(self, mic_queue: asyncio.Queue) -> None:
        """
        Rulează o sesiune vocală multi-turn cu:
        - Echo prevention (mic muted în timpul playback)
        - Re-entrant receive (multi-turn)
        - Tool calling
        - Inactivity timeout

        Lifecycle:
            send_task CONTROLEAZĂ durata → finalizează la inactivitate
            receive_task rulează re-entrant → se re-conectează după fiecare turn
            playback_task redă audio → activ toată sesiunea
        """
        if not self._initialized:
            logger.error("❌ [GeminiLive] Neinițializat!")
            return

        self._session_active       = True
        self._ai_is_speaking       = False
        self._last_user_speech     = time.time()
        self._interrupt_speech_start = None
        self._stop_playback.clear()

        # Flush coada audio veche
        while not self._audio_out_queue.empty():
            try: self._audio_out_queue.get_nowait()
            except: break

        # Delay: consumăm frame-urile wake word din mic
        if LIVE_START_DELAY_MS > 0:
            deadline = asyncio.get_event_loop().time() + LIVE_START_DELAY_MS / 1000.0
            while asyncio.get_event_loop().time() < deadline:
                try: mic_queue.get_nowait()
                except asyncio.QueueEmpty: await asyncio.sleep(0.04)
            logger.debug(f"[GeminiLive] Delay {LIVE_START_DELAY_MS}ms OK.")

        # Build config cu tools
        tools = self._build_tools()
        config_kwargs = dict(
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
            config_kwargs["tools"] = tools
            logger.info(f"🔧 [GeminiLive] {len(tools[0].function_declarations)} tool-uri declarate.")

        config = self._types.LiveConnectConfig(**config_kwargs)

        try:
            async with self._client.aio.live.connect(
                model=LIVE_MODEL, config=config
            ) as session:
                logger.info("🔗 [GeminiLive] Sesiune WebSocket deschisă.")
                print(
                    f"\n🎙️  Chronos ascultă — vorbește natural!\n"
                    f"   Timeout: {int(LIVE_INACTIVITY_TIMEOUT)}s tăcere | "
                    f"Interrupt: {INTERRUPT_MIN_DURATION}s vorbire tare"
                )

                if self._ET:
                    await self.bus.publish(
                        self._ET.AUDIO_LISTENING_START, {"source": "gemini_live"}
                    )

                # Playback stream — activ toată sesiunea
                playback_task = asyncio.create_task(
                    self._playback_loop(), name="live_playback"
                )

                send_task = asyncio.create_task(
                    self._send_mic_loop(session, mic_queue), name="live_send"
                )
                receive_task = asyncio.create_task(
                    self._receive_loop(session), name="live_receive"
                )

                # send_task CONTROLEAZĂ durata sesiunii
                try:
                    await send_task
                except asyncio.CancelledError:
                    logger.debug("[GeminiLive] send_task anulat.")
                except Exception as e:
                    logger.error(f"❌ [GeminiLive] send_task: {type(e).__name__}: {e}")
                finally:
                    self._session_active = False

                    # Cleanup receive_task
                    if not receive_task.done():
                        receive_task.cancel()
                        try: await asyncio.wait_for(receive_task, timeout=1.0)
                        except (asyncio.CancelledError, asyncio.TimeoutError): pass

                    # Cleanup playback
                    self._stop_playback.set()
                    await self._audio_out_queue.put(_END_SENTINEL)
                    try: await asyncio.wait_for(playback_task, timeout=2.0)
                    except (asyncio.TimeoutError, asyncio.CancelledError):
                        playback_task.cancel()

        except Exception as e:
            logger.error(f"❌ [GeminiLive] Eroare WebSocket: {type(e).__name__}: {e}", exc_info=True)
        finally:
            self._session_active  = False
            self._ai_is_speaking  = False
            if self._ET:
                await self.bus.publish(
                    self._ET.AUDIO_RESPONSE_END, {"completed": True}
                )
            print("\n✅ Sesiune vocală încheiată. Spune 'Jarvis' pentru a relua.")

    # ─── SEND MIC → GEMINI ───────────────────────────────────

    async def _send_mic_loop(self, session, mic_queue: asyncio.Queue) -> None:
        """
        Trimite audio mic → Gemini cu ECHO PREVENTION.

        REGULI:
        ┌─────────────────────────────────────────────────────┐
        │ AI VORBESTE:                                        │
        │   → NU trimitem audio (previne echo barge-in)       │
        │   → DAR monitorizăm amplitudinea:                   │
        │     RMS > THRESHOLD pentru > MIN_DURATION secunde   │
        │     = Interrupt REAL → oprim playback, trimitem mic │
        │                                                     │
        │ AI NU VORBESTE:                                     │
        │   → Trimitem tot audio-ul normal                    │
        │   → Timer inactivitate activ                        │
        └─────────────────────────────────────────────────────┘
        """
        logger.debug("[GeminiLive:send] Loop pornit.")
        self._last_user_speech = time.time()
        chunks_sent = 0
        np = self._np

        while self._session_active:
            try:
                chunk = await asyncio.wait_for(mic_queue.get(), timeout=0.3)
            except asyncio.TimeoutError:
                # ── Inactivity check ──
                if self._ai_is_speaking:
                    # AI vorbeste = utilizatorul "ascultă", nu "tace"
                    self._last_user_speech = time.time()
                    continue

                elapsed   = time.time() - self._last_user_speech
                remaining = LIVE_INACTIVITY_TIMEOUT - elapsed

                if remaining <= 3.0 and remaining > 0:
                    logger.debug(f"[GeminiLive:send] Closing in {remaining:.0f}s")

                if elapsed > LIVE_INACTIVITY_TIMEOUT:
                    logger.info(
                        f"⏰ [GeminiLive] {elapsed:.0f}s tăcere → sesiune terminată."
                    )
                    self._session_active = False
                    break
                continue

            except asyncio.CancelledError:
                break

            # ── AI VORBESTE: Echo Prevention ──
            if self._ai_is_speaking:
                # Calculăm RMS (amplitudinea audio-ului din mic)
                rms = float(np.sqrt(np.mean(chunk.astype(np.float32) ** 2)))

                if rms > INTERRUPT_AMPLITUDE_THRESHOLD:
                    # Audio tare — posibil utilizatorul vorbeste
                    if self._interrupt_speech_start is None:
                        self._interrupt_speech_start = time.time()
                        logger.debug(
                            f"[GeminiLive:send] 🗣️ Posibil interrupt: RMS={rms:.0f} "
                            f"(prag={INTERRUPT_AMPLITUDE_THRESHOLD})"
                        )

                    elapsed_int = time.time() - self._interrupt_speech_start

                    if elapsed_int >= INTERRUPT_MIN_DURATION:
                        # ═══ INTERRUPT REAL ═══
                        logger.info(
                            f"🗣️ [GeminiLive] INTERRUPT REAL detectat! "
                            f"({elapsed_int:.1f}s vorbire tare, RMS={rms:.0f})"
                        )
                        # Oprim playback local
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
                        self._interrupt_speech_start = None

                        # Acum trimitem audio-ul real la Gemini
                        chunk_bytes = chunk.astype("int16").tobytes()
                        try:
                            await session.send_realtime_input(
                                audio=self._types.Blob(
                                    data=chunk_bytes,
                                    mime_type=f"audio/pcm;rate={LIVE_SAMPLE_RATE_IN}"
                                )
                            )
                            chunks_sent += 1
                            self._last_user_speech = time.time()
                        except Exception as e:
                            logger.error(f"❌ [GeminiLive:send] Post-interrupt send: {e}")
                            self._session_active = False
                            break
                else:
                    # Audio slab = echo din boxe, resetăm contorul de interrupt
                    self._interrupt_speech_start = None

                # NU trimitem audio la Gemini în timpul AI speech (echo prevention)
                continue

            # ── AI NU VORBESTE: trimitem tot ──
            self._interrupt_speech_start = None
            chunk_bytes = chunk.astype("int16").tobytes()

            try:
                await session.send_realtime_input(
                    audio=self._types.Blob(
                        data=chunk_bytes,
                        mime_type=f"audio/pcm;rate={LIVE_SAMPLE_RATE_IN}"
                    )
                )
                chunks_sent += 1
                self._last_user_speech = time.time()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"❌ [GeminiLive:send] Eroare: {type(e).__name__}: {e}")
                self._session_active = False
                break

        logger.info(f"[GeminiLive:send] Loop terminat. Chunks: {chunks_sent}")

    # ─── RECEIVE ← GEMINI ────────────────────────────────────

    async def _receive_loop(self, session) -> None:
        """
        Primeste răspunsuri de la Gemini — RE-ENTRANT.

        session.receive() se termină după fiecare turn_complete.
        Soluție: while loop care re-intră pentru turnul următor.

        Cu echo prevention activ, barge-in de la Gemini nu ar trebui
        să mai apară (nu trimitem audio în timpul AI speech).
        Dacă apare totuși, îl logăm dar NU oprim playback-ul.
        """
        logger.debug("[GeminiLive:recv] Loop pornit.")
        total_responses = 0
        turns_completed = 0

        while self._session_active:
            try:
                async for response in session.receive():
                    if not self._session_active:
                        return

                    total_responses += 1

                    # ── Server Content (audio/text response) ──
                    sc = getattr(response, "server_content", None)
                    if sc:
                        interrupted   = getattr(sc, "interrupted",   False)
                        turn_complete = getattr(sc, "turn_complete", False)
                        mt            = getattr(sc, "model_turn",    None)

                        # Barge-in de la server (ar trebui să fie rar cu echo prevention)
                        if interrupted:
                            logger.info(
                                "⛔ [GeminiLive] Barge-in server-side. "
                                "(Nu oprim playback — echo prevention activ)"
                            )
                            # NU oprim playback-ul! Cu echo prevention,
                            # barge-in-ul e probabil fals. Dacă e real,
                            # _send_mic_loop a oprit deja playback-ul local.
                            self._ai_is_speaking = False
                            self._last_user_speech = time.time()
                            continue

                        # Model turn: audio + eventual text
                        if mt:
                            self._ai_is_speaking = True
                            for part in (mt.parts or []):
                                id_ = getattr(part, "inline_data", None)
                                if id_ and getattr(id_, "data", None):
                                    await self._audio_out_queue.put(id_.data)

                                txt = getattr(part, "text", None)
                                if txt and txt.strip():
                                    logger.debug(
                                        f"[GeminiLive:recv] Text: {txt.strip()[:80]!r}"
                                    )

                        # Turn complet: Gemini a terminat UN răspuns
                        if turn_complete:
                            turns_completed += 1
                            self._ai_is_speaking = False
                            await self._audio_out_queue.put(_FLUSH_SENTINEL)
                            self._last_user_speech = time.time()
                            logger.info(
                                f"✅ [GeminiLive] Turn #{turns_completed} complet. "
                                f"Re-intru în receive pentru turnul următor."
                            )
                        continue

                    # ── Tool Call (funcții declarate) ──
                    tc = getattr(response, "tool_call", None)
                    if tc:
                        await self._handle_tool_call(session, tc)
                        continue

                    # ── Altceva (go_away, setup_complete, etc.) ──
                    logger.debug(
                        f"[GeminiLive:recv] #{total_responses} "
                        f"Tip necunoscut: {type(response).__name__}"
                    )

                # ── session.receive() s-a terminat (normal după turn_complete) ──
                # Re-intrăm pentru turnul următor
                if self._session_active:
                    logger.debug(
                        "[GeminiLive:recv] session.receive() terminat. "
                        "Re-intru pentru turnul următor..."
                    )
                    await asyncio.sleep(0.05)
                    continue
                else:
                    break

            except asyncio.CancelledError:
                logger.debug("[GeminiLive:recv] Anulat.")
                break
            except Exception as e:
                if self._session_active:
                    err_name = type(e).__name__
                    # ConnectionClosed = WebSocket mort → sesiune terminată
                    if "ConnectionClosed" in err_name or "WebSocket" in err_name:
                        logger.info(f"[GeminiLive:recv] WebSocket închis: {e}")
                        self._session_active = False
                        break
                    else:
                        logger.error(f"❌ [GeminiLive:recv] Eroare: {e}", exc_info=True)
                        await asyncio.sleep(0.2)
                        # Încercăm re-entry
                else:
                    break

        logger.info(
            f"[GeminiLive:recv] Loop terminat. "
            f"Total: {total_responses} răspunsuri, {turns_completed} turnuri."
        )
        self._ai_is_speaking = False

    # ─── PLAYBACK ────────────────────────────────────────────

    async def _playback_loop(self) -> None:
        """
        Redă audio PCM de la Gemini.
        Rămâne activ toată sesiunea (multi-turn persistent).
        """
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
                        try:
                            await asyncio.to_thread(stream.write, bytes(buffer))
                        except Exception:
                            pass
                        buffer.clear()
                    continue

                # Terminare definitivă
                if data is _END_SENTINEL:
                    if buffer and not self._stop_playback.is_set():
                        try:
                            await asyncio.to_thread(stream.write, bytes(buffer))
                        except Exception:
                            pass
                    break

                # Flush turn (AI terminat un răspuns, sesiune continuă)
                if data is _FLUSH_SENTINEL:
                    if buffer and not self._stop_playback.is_set():
                        try:
                            await asyncio.to_thread(stream.write, bytes(buffer))
                        except Exception:
                            pass
                        buffer.clear()
                    continue

                # Întrerupere activă → golim rapid
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

                # Audio normal → buffer → playback
                buffer.extend(data)
                while len(buffer) >= LIVE_PLAYBACK_CHUNK_BYTES:
                    if self._stop_playback.is_set():
                        buffer.clear()
                        break
                    chunk = bytes(buffer[:LIVE_PLAYBACK_CHUNK_BYTES])
                    buffer = bytearray(buffer[LIVE_PLAYBACK_CHUNK_BYTES:])
                    try:
                        await asyncio.to_thread(stream.write, chunk)
                    except Exception as e:
                        logger.error(f"❌ [GeminiLive:play] Write: {e}")
                        break

        except asyncio.CancelledError:
            pass
        except Exception as e:
            logger.error(f"❌ [GeminiLive:play] Eroare: {e}", exc_info=True)
        finally:
            try:
                stream.stop()
                stream.close()
            except Exception:
                pass
            self._ai_is_speaking = False
            logger.debug("[GeminiLive:play] Stream închis.")

    # ─── CONTROL ─────────────────────────────────────────────

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
