"""
core/audio_interface.py — Chronos Audio Interface
===================================================
Captură audio continuă + detectare wake word openWakeWord.

Moduri de operare:
    WAKE_WORD_MODE (default):
        Audio chunk → OWW model → detectează "hey_jarvis" → publică WAKE_WORD_DETECTED

    LIVE_SESSION_MODE (activat de GeminiLiveSession):
        Audio chunk → direct în live_queue → GeminiLiveSession consumă
        (Wake word detection SUSPENDAT pe durata sesiunii live)

Cross-Platform: Windows 11 (sounddevice/PortAudio) + Raspberry Pi 5 (idem)
Fallback: dacă microfon sau OWW lipsesc → Terminal-only mode (fără crash)
"""

import asyncio
import logging
import time
from pathlib import Path
from typing import Optional

import numpy as np

try:
    from config import (
        SAMPLE_RATE,
        OWW_DETECTION_THRESHOLD as DETECTION_THRESHOLD,
        OWW_CONFIRMATION_FRAMES as CONFIRMATION_FRAMES,
        OWW_DETECTION_COOLDOWN  as DETECTION_COOLDOWN,
        WAKE_WORD_THRESHOLD_JARVIS as MIN_SCORE_FOR_JARVIS,
        WAKE_WORD_THRESHOLD_OTHER  as MIN_SCORE_FOR_NON_JARVIS,
    )
except ImportError:
    SAMPLE_RATE             = 16000
    DETECTION_THRESHOLD     = 0.5
    CONFIRMATION_FRAMES     = 2
    DETECTION_COOLDOWN      = 3.0
    MIN_SCORE_FOR_JARVIS    = 0.75
    MIN_SCORE_FOR_NON_JARVIS = 0.90

logger = logging.getLogger(__name__)

_MODELS_DIR  = Path(__file__).parent / "models"
FRAME_LENGTH = 1280   # 80ms @ 16kHz — recomandat OWW
CHANNELS     = 1
DTYPE        = "int16"

# Prag minim scor pentru a accepta wake word-uri non-jarvis
# (evităm false positive-uri de la modele ca "weather", "timer")
MIN_SCORE_FOR_NON_JARVIS = 0.90
MIN_SCORE_FOR_JARVIS     = 0.75


class AudioInterface:
    """
    Interfața audio asincronă a sistemului Chronos.

    Gestionează:
        - Stream microfon continuu (sounddevice)
        - Detectare wake word "hey_jarvis" (openWakeWord)
        - Live mode: redirect audio → GeminiLiveSession queue
    """

    def __init__(self, event_bus):
        from core.event_bus import EventBus, EventType
        self.bus      = event_bus
        self.EventType = EventType

        self._oww_model   = None
        self._model_name  : str  = "N/A"
        self._sd          = None
        self._enabled     : bool = False
        self._running     : bool = False
        self._last_detect : float = 0.0

        # Coada internă pentru WW detection
        self._ww_queue : asyncio.Queue = asyncio.Queue(maxsize=100)

        # Live session mode
        self._live_mode  : bool          = False
        self._live_queue : Optional[asyncio.Queue] = None

        # Wake-word-as-interrupt: în „focus mode" (Chronos livrează un răspuns
        # important) barge-in-ul pe voce e oprit, iar singura cale de a-l
        # întrerupe e să spui din nou wake word-ul. Când e armat, audio-ul e
        # rutat SIMULTAN către sesiunea live ȘI către detectorul OWW.
        self._wake_interrupt_armed : bool = False

    # ─────────────────────────────────────────────────────
    # INIȚIALIZARE
    # ─────────────────────────────────────────────────────

    async def initialize(self) -> bool:
        """Inițializează microfonul + OWW. Returnează True dacă OK."""
        logger.info("🎤 [AudioInterface] Inițializare...")

        if not self._init_sounddevice():
            await self.bus.publish_status(
                "AudioInterface", "DISABLED",
                "sounddevice indisponibil → Terminal-only.", "WARNING"
            )
            return False

        if not self._init_oww():
            await self.bus.publish_status(
                "AudioInterface", "DISABLED",
                "openWakeWord indisponibil → Terminal-only.", "WARNING"
            )
            return False

        self._enabled = True
        logger.info(f"✅ [AudioInterface] Gata. Model: '{self._model_name}'")
        await self.bus.publish(self.EventType.SYSTEM_READY, {"component": "AudioInterface"})
        return True

    def _init_sounddevice(self) -> bool:
        try:
            import sounddevice as sd
            self._sd = sd
            dev = sd.query_devices(sd.default.device[0], "input")
            logger.info(f"🎙️ [AudioInterface] Microfon: '{dev['name']}' @ {SAMPLE_RATE}Hz")
            return True
        except ImportError:
            logger.error("❌ sounddevice lipsă! pip install sounddevice")
            return False
        except Exception as e:
            logger.error(f"❌ sounddevice error: {e}")
            return False

    def _init_oww(self) -> bool:
        try:
            from openwakeword.model import Model as OWWModel
        except ImportError:
            logger.error("❌ openwakeword lipsă! pip install openwakeword")
            return False

        # Model custom
        custom = self._find_custom_model()
        if custom:
            try:
                self._oww_model = OWWModel(
                    wakeword_models=[str(custom)],
                    inference_framework="tflite"
                )
                self._model_name = custom.stem
                logger.info(f"✅ [OWW] Model custom: {custom.name}")
                return True
            except Exception as e:
                logger.warning(f"⚠️ [OWW] Custom model error: {e}")

        # Modele pre-instalate
        logger.info("🔍 [OWW] Caut modele pre-instalate...")
        try:
            self._oww_model = OWWModel(inference_framework="tflite")
            names = list(self._oww_model.models.keys()) if self._oww_model.models else []
            if not names:
                logger.error("❌ [OWW] Niciun model disponibil!")
                return False
            jarvis = [n for n in names if "jarvis" in n.lower()]
            self._model_name = jarvis[0] if jarvis else names[0]
            logger.info(
                f"✅ [OWW] Model activ: '{self._model_name}'\n"
                f"   Disponibile: {names}"
            )
            return True
        except Exception as e:
            logger.error(f"❌ [OWW] Error: {e}")
            return False

    def _find_custom_model(self) -> Optional[Path]:
        if not _MODELS_DIR.exists():
            _MODELS_DIR.mkdir(parents=True, exist_ok=True)
            return None
        files = list(_MODELS_DIR.glob("*.tflite")) + list(_MODELS_DIR.glob("*.onnx"))
        if not files:
            return None
        jarvis = [f for f in files if "jarvis" in f.name.lower()]
        return jarvis[0] if jarvis else files[0]

    # ─────────────────────────────────────────────────────
    # LOOP PRINCIPAL
    # ─────────────────────────────────────────────────────

    async def run(self) -> None:
        """Loop principal: captura audio continua + routing."""
        if not self._enabled:
            logger.warning("⚠️ [AudioInterface] Dezactivat → Terminal-only.")
            # Asteaptam la infinit fara sa facem nimic (nu crash)
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                pass
            return

        self._running = True
        loop = asyncio.get_running_loop()

        def audio_callback(indata, frames, time_info, status):
            chunk = indata[:, 0].astype(np.int16).copy()
            if self._live_mode and self._live_queue is not None:
                # LIVE MODE: trimitem direct la sesiunea Gemini
                try:
                    loop.call_soon_threadsafe(self._live_queue.put_nowait, chunk)
                except asyncio.QueueFull:
                    pass
                # ...și în paralel la detector, dacă wake-interrupt e armat
                if self._wake_interrupt_armed:
                    try:
                        loop.call_soon_threadsafe(self._ww_queue.put_nowait, chunk)
                    except asyncio.QueueFull:
                        pass
            else:
                # WAKE WORD MODE: trimitem la detector
                try:
                    loop.call_soon_threadsafe(self._ww_queue.put_nowait, chunk)
                except asyncio.QueueFull:
                    pass

        logger.info(
            f"🚀 [AudioInterface] Ascult wake word '{self._model_name}'...\n"
            "   Spune 'Jarvis' pentru a activa asistentul."
        )

        try:
            with self._sd.InputStream(
                samplerate=SAMPLE_RATE,
                channels=CHANNELS,
                dtype=DTYPE,
                blocksize=FRAME_LENGTH,
                callback=audio_callback,
            ):
                logger.info("🎙️ [AudioInterface] Stream microfon deschis.")
                await self._detection_loop()
        except Exception as e:
            logger.error(f"❌ [AudioInterface] Stream error: {e}", exc_info=True)
        finally:
            self._running = False
            logger.info("🛑 [AudioInterface] Stream oprit.")

    async def _detection_loop(self) -> None:
        """Procesează frame-urile audio și detectează wake word."""
        confirmation = 0
        audio_buffer = bytearray()

        while self._running:
            try:
                # În live mode nu facem wake word detection — EXCEPTÂND cazul
                # în care wake-interrupt e armat (focus mode), când wake word-ul
                # e singura cale de a-l întrerupe pe Chronos.
                if self._live_mode and not self._wake_interrupt_armed:
                    await asyncio.sleep(0.1)
                    confirmation = 0
                    audio_buffer.clear()
                    continue

                try:
                    chunk_np = await asyncio.wait_for(self._ww_queue.get(), timeout=0.5)
                except asyncio.TimeoutError:
                    continue

                audio_buffer.extend(chunk_np.tobytes())

                if len(audio_buffer) >= FRAME_LENGTH * 2:
                    audio_np = np.frombuffer(
                        bytes(audio_buffer[:FRAME_LENGTH * 2]), dtype=np.int16
                    )
                    audio_buffer = bytearray(audio_buffer[FRAME_LENGTH * 2:])

                    prediction = self._oww_model.predict(audio_np)

                    best_score = 0.0
                    best_name  = ""
                    for mname, score in prediction.items():
                        if score > best_score:
                            best_score = score
                            best_name  = mname

                    # Prag diferit pentru jarvis vs alte modele (evitam false positives)
                    is_jarvis = "jarvis" in best_name.lower()
                    threshold = MIN_SCORE_FOR_JARVIS if is_jarvis else MIN_SCORE_FOR_NON_JARVIS

                    if best_score >= threshold:
                        confirmation += 1
                        logger.debug(f"🔍 [OWW] {best_name}: {best_score:.3f} [{confirmation}/{CONFIRMATION_FRAMES}]")
                    else:
                        confirmation = 0

                    if confirmation >= CONFIRMATION_FRAMES:
                        confirmation = 0
                        now = time.time()
                        if now - self._last_detect < DETECTION_COOLDOWN:
                            continue
                        self._last_detect = now

                        payload = {
                            "timestamp": now,
                            "score": float(best_score),
                            "model_name": best_name,
                        }

                        if self._live_mode:
                            # Sesiune live activă → wake word-ul e ÎNTRERUPERE,
                            # nu pornire de sesiune nouă (altfel am avea două
                            # sesiuni Gemini simultan, vorbind una peste alta).
                            logger.info(
                                f"\n🖐️ [AudioInterface] WAKE WORD ca ÎNTRERUPERE! "
                                f"'{best_name}' score={best_score:.3f}"
                            )
                            await self.bus.publish(
                                self.EventType.WAKE_WORD_INTERRUPT, payload
                            )
                        else:
                            logger.info(
                                f"\n🎯 [AudioInterface] WAKE WORD! '{best_name}' "
                                f"score={best_score:.3f}"
                            )
                            await self.bus.publish(
                                self.EventType.WAKE_WORD_DETECTED, payload
                            )
                        self._oww_model.reset()

                        # Golim coada
                        while not self._ww_queue.empty():
                            try: self._ww_queue.get_nowait()
                            except asyncio.QueueEmpty: break

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"❌ [AudioInterface] Detection error: {e}", exc_info=True)
                await asyncio.sleep(0.1)

    # ─────────────────────────────────────────────────────
    # LIVE MODE CONTROL
    # ─────────────────────────────────────────────────────

    def enable_live_mode(self, live_queue: asyncio.Queue) -> None:
        """
        Activează live mode: audio e redirecționat la live_queue.
        Wake word detection e suspendat automat.

        Args:
            live_queue: Coada în care GeminiLiveSession primește audio.
        """
        self._live_queue = live_queue
        self._live_mode  = True
        logger.info("🔴 [AudioInterface] LIVE MODE activ — audio → GeminiLive")

    def arm_wake_interrupt(self) -> None:
        """
        Armează wake word-ul ca mecanism de întrerupere pe durata sesiunii live.
        Folosit în „focus mode": barge-in-ul pe voce e dezactivat, deci singura
        cale de a-l opri pe Chronos e să spui din nou wake word-ul.
        """
        if self._wake_interrupt_armed:
            return
        # Pornim de la zero: frame-urile vechi din coadă sunt irelevante și ar
        # putea produce o detecție falsă imediată.
        while not self._ww_queue.empty():
            try: self._ww_queue.get_nowait()
            except asyncio.QueueEmpty: break
        if self._oww_model:
            self._oww_model.reset()
        self._wake_interrupt_armed = True
        logger.info("🖐️ [AudioInterface] Wake-interrupt ARMAT (focus mode).")

    def disarm_wake_interrupt(self) -> None:
        """Dezarmează wake-interrupt — revenim la barge-in normal pe voce."""
        if not self._wake_interrupt_armed:
            return
        self._wake_interrupt_armed = False
        while not self._ww_queue.empty():
            try: self._ww_queue.get_nowait()
            except asyncio.QueueEmpty: break
        if self._oww_model:
            self._oww_model.reset()
        logger.info("🟢 [AudioInterface] Wake-interrupt dezarmat.")

    def disable_live_mode(self) -> None:
        """
        Dezactivează live mode: revenim la wake word detection.
        """
        self._live_mode  = False
        self._live_queue = None
        self._wake_interrupt_armed = False
        # Golim coada WW (frame-uri acumulate în live mode sunt irelevante)
        while not self._ww_queue.empty():
            try: self._ww_queue.get_nowait()
            except asyncio.QueueEmpty: break
        if self._oww_model:
            self._oww_model.reset()
        logger.info("🟢 [AudioInterface] WAKE WORD MODE restaurat.")

    # ─────────────────────────────────────────────────────
    # CLEANUP
    # ─────────────────────────────────────────────────────

    def stop(self) -> None:
        self._running = False

    @property
    def is_enabled(self) -> bool:
        return self._enabled

    @property
    def model_name(self) -> str:
        return self._model_name

    @property
    def in_live_mode(self) -> bool:
        return self._live_mode
