"""
core/audio_interface.py — Chronos Audio Interface
===================================================
Captură audio continuă + detectare wake word openWakeWord.

Moduri de operare:
    WAKE_WORD_MODE (default):
        chunk microfon → OWW → detectează "hey_jarvis" → publică WAKE_WORD_DETECTED

    LIVE_SESSION_MODE (activat de GeminiLiveSession):
        chunk microfon → direct în live_queue → GeminiLiveSession consumă
        (detecția de wake word e suspendată, exceptând „focus mode")

Cross-Platform: Windows 11 (sounddevice/PortAudio) + Raspberry Pi 5 (idem)
Fallback: fără microfon sau fără OWW → Terminal-only mode (fără crash)

Note de performanță (contează pe Pi):
    - Callback-ul PortAudio face O SINGURĂ copie per frame, nimic altceva.
      E un thread în timp real: orice alocare în plus se plătește în glitch-uri.
    - Inferența TFLite NU rulează pe bucla asyncio, ci pe un thread dedicat.
      Rula pe buclă înainte, blocând-o câteva ms la fiecare 80ms.
"""

import asyncio
import logging
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import List, Optional

import numpy as np

try:
    from config import (
        SAMPLE_RATE,
        OWW_DETECTION_THRESHOLD as DETECTION_THRESHOLD,
        OWW_CONFIRMATION_FRAMES as CONFIRMATION_FRAMES,
        OWW_DETECTION_COOLDOWN as DETECTION_COOLDOWN,
        WAKE_WORD_THRESHOLD_JARVIS as MIN_SCORE_FOR_JARVIS,
        WAKE_WORD_THRESHOLD_OTHER as MIN_SCORE_FOR_NON_JARVIS,
    )
except ImportError:
    SAMPLE_RATE = 16000
    DETECTION_THRESHOLD = 0.5
    CONFIRMATION_FRAMES = 2
    DETECTION_COOLDOWN = 3.0
    MIN_SCORE_FOR_JARVIS = 0.75
    MIN_SCORE_FOR_NON_JARVIS = 0.90

# ATENȚIE: aici era, înainte, o redefinire necondiționată a pragurilor
# MIN_SCORE_FOR_JARVIS / MIN_SCORE_FOR_NON_JARVIS imediat după import. Efectul
# era că valorile din personalization.py erau citite și apoi aruncate — ajustarea
# pragurilor de acolo nu avea absolut niciun efect. Nu le redefini aici.

logger = logging.getLogger(__name__)

_MODELS_DIR = Path(__file__).parent / "models"
FRAME_LENGTH = 1280   # 80ms @ 16kHz — recomandat OWW
CHANNELS = 1
DTYPE = "int16"

# Reconectare microfon: pe Pi, un mic USB scos/repus sau un xrun de driver
# omorau definitiv captura, iar Chronos rămânea surd până la restart.
_STREAM_RETRY_DELAY = 2.0
_STREAM_RETRY_MAX_DELAY = 30.0


def _enqueue_latest(queue: asyncio.Queue, item) -> None:
    """Pune `item` în coadă; dacă e plină, aruncă cel mai vechi element.

    Rulează PE BUCLA asyncio (programat cu call_soon_threadsafe), nu pe threadul
    PortAudio. Varianta veche prindea `asyncio.QueueFull` în jurul apelului
    `call_soon_threadsafe` — dar acolo nu se aruncă niciodată nimic: apelul doar
    programează callback-ul, iar excepția apărea mai târziu, pe buclă, unde
    ajungea direct la exception handler-ul default ca „Exception in callback".
    """
    try:
        queue.put_nowait(item)
    except asyncio.QueueFull:
        try:
            queue.get_nowait()          # audio vechi = audio inutil
        except asyncio.QueueEmpty:
            return
        try:
            queue.put_nowait(item)
        except asyncio.QueueFull:
            pass


class AudioInterface:
    """
    Interfața audio asincronă a sistemului Chronos.

    Gestionează:
        - Stream microfon continuu (sounddevice), cu reconectare
        - Detectare wake word "hey_jarvis" (openWakeWord), off-loop
        - Live mode: redirect audio → GeminiLiveSession queue
    """

    __slots__ = (
        "bus", "EventType", "_oww_model", "_model_name", "_sd", "_enabled",
        "_running", "_last_detect", "_ww_queue", "_live_mode", "_live_queue",
        "_wake_interrupt_armed", "_infer_pool", "_loop",
    )

    def __init__(self, event_bus):
        from core.event_bus import EventType
        self.bus = event_bus
        self.EventType = EventType

        self._oww_model = None
        self._model_name: str = "N/A"
        self._sd = None
        self._enabled: bool = False
        self._running: bool = False
        self._last_detect: float = 0.0

        # Coada internă pentru detecția de wake word
        self._ww_queue: asyncio.Queue = asyncio.Queue(maxsize=100)

        # Live session mode
        self._live_mode: bool = False
        self._live_queue: Optional[asyncio.Queue] = None

        # Wake-word-as-interrupt: în „focus mode" (Chronos livrează un răspuns
        # important) barge-in-ul pe voce e oprit, iar singura cale de a-l
        # întrerupe e să spui din nou wake word-ul. Când e armat, audio-ul e
        # rutat SIMULTAN către sesiunea live ȘI către detectorul OWW.
        self._wake_interrupt_armed: bool = False

        # Thread dedicat pentru inferența TFLite. Dedicat, nu pool-ul default:
        # acolo se înghesuie deja Flask, dispatcher-ul și analiza de emoții, iar
        # detecția wake word-ului nu are voie să aștepte după ele.
        self._infer_pool: Optional[ThreadPoolExecutor] = None
        self._loop: Optional[asyncio.AbstractEventLoop] = None

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

        # Încărcarea modelului citește de pe disc și construiește interpretorul
        # TFLite — sute de ms pe Pi. Pe buclă ar întârzia pornirea a tot restul.
        if not await asyncio.to_thread(self._init_oww):
            await self.bus.publish_status(
                "AudioInterface", "DISABLED",
                "openWakeWord indisponibil → Terminal-only.", "WARNING"
            )
            return False

        self._infer_pool = ThreadPoolExecutor(
            max_workers=1, thread_name_prefix="oww-infer"
        )
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

        # Alegem backend-ul in functie de ce EXISTA, nu presupunem tflite.
        # Pe Raspberry Pi cu Python 3.13, `tflite-runtime` nu are build-uri
        # (se opreste la 3.12), deci acolo mergem pe onnxruntime — la fel de
        # bun pentru wake word, doar putin mai lent la incarcare.
        try:
            import tflite_runtime          # noqa: F401
            framework = "tflite"
        except ImportError:
            framework = "onnx"
            logger.info("ℹ️ [OWW] tflite indisponibil → folosesc onnxruntime.")

        # Model custom
        custom = self._find_custom_model()
        if custom:
            try:
                self._oww_model = OWWModel(
                    wakeword_models=[str(custom)],
                    inference_framework=framework
                )
                self._model_name = custom.stem
                logger.info(f"✅ [OWW] Model custom: {custom.name}")
                return True
            except Exception as e:
                logger.warning(f"⚠️ [OWW] Custom model error: {e}")

        # Modele pre-instalate
        logger.info("🔍 [OWW] Caut modele pre-instalate...")
        try:
            self._oww_model = OWWModel(inference_framework=framework)
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

    def _make_callback(self, loop: asyncio.AbstractEventLoop):
        """Construiește callback-ul PortAudio.

        Rulează pe un thread în timp real: o singură copie, zero alocări în
        plus, zero logging. `indata` e int16 mono (vezi DTYPE/CHANNELS), deci
        `[:, 0].copy()` e exact copia necesară — varianta veche făcea
        `.astype(np.int16).copy()`, adică două copii, din care una degeaba.
        """
        call_soon = loop.call_soon_threadsafe

        def audio_callback(indata, frames, time_info, status):
            chunk = indata[:, 0].copy()
            try:
                if self._live_mode and self._live_queue is not None:
                    call_soon(_enqueue_latest, self._live_queue, chunk)
                    if self._wake_interrupt_armed:
                        call_soon(_enqueue_latest, self._ww_queue, chunk)
                else:
                    call_soon(_enqueue_latest, self._ww_queue, chunk)
            except RuntimeError:
                # Bucla s-a închis între timp (shutdown). Nu e nimic de făcut
                # și nu avem voie să aruncăm dintr-un callback PortAudio.
                pass

        return audio_callback

    async def run(self) -> None:
        """Loop principal: captură audio continuă + routing, cu reconectare."""
        if not self._enabled:
            logger.warning("⚠️ [AudioInterface] Dezactivat → Terminal-only.")
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                pass
            return

        self._running = True
        self._loop = asyncio.get_running_loop()
        callback = self._make_callback(self._loop)
        delay = _STREAM_RETRY_DELAY

        logger.info(
            f"🚀 [AudioInterface] Ascult wake word '{self._model_name}'...\n"
            "   Spune 'Jarvis' pentru a activa asistentul."
        )

        try:
            while self._running:
                try:
                    with self._sd.InputStream(
                        samplerate=SAMPLE_RATE,
                        channels=CHANNELS,
                        dtype=DTYPE,
                        blocksize=FRAME_LENGTH,
                        callback=callback,
                    ):
                        logger.info("🎙️ [AudioInterface] Stream microfon deschis.")
                        delay = _STREAM_RETRY_DELAY   # conexiune bună → reset backoff
                        await self._detection_loop()
                    # _detection_loop s-a întors normal → oprire cerută
                    break
                except asyncio.CancelledError:
                    break
                except Exception as e:
                    if not self._running:
                        break
                    logger.error(
                        f"❌ [AudioInterface] Stream pierdut ({type(e).__name__}: {e}). "
                        f"Reîncerc în {delay:.0f}s..."
                    )
                    await self.bus.publish_status(
                        "AudioInterface", "RECONNECTING",
                        f"Microfon indisponibil, reîncerc în {delay:.0f}s.", "WARNING"
                    )
                    try:
                        await asyncio.sleep(delay)
                    except asyncio.CancelledError:
                        break
                    delay = min(delay * 2, _STREAM_RETRY_MAX_DELAY)
        finally:
            self._running = False
            self._shutdown_pool()
            logger.info("🛑 [AudioInterface] Stream oprit.")

    async def _detection_loop(self) -> None:
        """Procesează frame-urile audio și detectează wake word.

        Inferența pleacă pe threadul dedicat; bucla rămâne liberă să servească
        restul sistemului (rețea, playback, event bus).
        """
        loop = asyncio.get_running_loop()
        confirmation = 0
        pending: List[np.ndarray] = []
        pending_len = 0

        while self._running:
            try:
                # În live mode nu facem detecție — exceptând „focus mode", când
                # wake word-ul e singura cale de a-l întrerupe pe Chronos.
                if self._live_mode and not self._wake_interrupt_armed:
                    await asyncio.sleep(0.1)
                    confirmation = 0
                    if pending:
                        pending.clear()
                        pending_len = 0
                    continue

                try:
                    chunk = await asyncio.wait_for(self._ww_queue.get(), timeout=0.5)
                except asyncio.TimeoutError:
                    continue

                # Cazul normal (blocksize == FRAME_LENGTH): frame-ul e gata,
                # fără concatenări și fără copii intermediare.
                if not pending and chunk.shape[0] == FRAME_LENGTH:
                    frame = chunk
                else:
                    pending.append(chunk)
                    pending_len += chunk.shape[0]
                    if pending_len < FRAME_LENGTH:
                        continue
                    joined = np.concatenate(pending) if len(pending) > 1 else pending[0]
                    frame = joined[:FRAME_LENGTH]
                    rest = joined[FRAME_LENGTH:]
                    pending = [rest] if rest.size else []
                    pending_len = rest.size

                prediction = await loop.run_in_executor(
                    self._infer_pool, self._oww_model.predict, frame
                )

                best_name, best_score = "", 0.0
                for mname, score in prediction.items():
                    if score > best_score:
                        best_score, best_name = score, mname

                # Prag diferit pentru jarvis vs alte modele (evităm false positives)
                threshold = (
                    MIN_SCORE_FOR_JARVIS if "jarvis" in best_name.lower()
                    else MIN_SCORE_FOR_NON_JARVIS
                )

                if best_score >= threshold:
                    confirmation += 1
                    logger.debug(
                        f"🔍 [OWW] {best_name}: {best_score:.3f} "
                        f"[{confirmation}/{CONFIRMATION_FRAMES}]"
                    )
                else:
                    confirmation = 0
                    continue

                if confirmation < CONFIRMATION_FRAMES:
                    continue

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
                    # Sesiune live activă → wake word-ul e ÎNTRERUPERE, nu
                    # pornire de sesiune nouă (altfel am avea două sesiuni
                    # Gemini simultan, vorbind una peste alta).
                    logger.info(
                        f"\n🖐️ [AudioInterface] WAKE WORD ca ÎNTRERUPERE! "
                        f"'{best_name}' score={best_score:.3f}"
                    )
                    await self.bus.publish(self.EventType.WAKE_WORD_INTERRUPT, payload)
                else:
                    logger.info(
                        f"\n🎯 [AudioInterface] WAKE WORD! '{best_name}' "
                        f"score={best_score:.3f}"
                    )
                    await self.bus.publish(self.EventType.WAKE_WORD_DETECTED, payload)

                self._oww_model.reset()
                pending.clear()
                pending_len = 0
                self._drain_ww_queue()

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"❌ [AudioInterface] Detection error: {e}", exc_info=True)
                await asyncio.sleep(0.1)

    # ─────────────────────────────────────────────────────
    # LIVE MODE CONTROL
    # ─────────────────────────────────────────────────────

    def _drain_ww_queue(self) -> None:
        """Golește coada de detecție (frame-uri vechi = detecții false)."""
        q = self._ww_queue
        while True:
            try:
                q.get_nowait()
            except asyncio.QueueEmpty:
                return

    def _reset_detector(self) -> None:
        self._drain_ww_queue()
        if self._oww_model is not None:
            try:
                self._oww_model.reset()
            except Exception as e:
                logger.debug(f"[AudioInterface] OWW reset: {e}")

    def enable_live_mode(self, live_queue: asyncio.Queue) -> None:
        """
        Activează live mode: audio e redirecționat la live_queue.
        Detecția de wake word e suspendată automat.
        """
        self._live_queue = live_queue
        self._live_mode = True
        logger.info("🔴 [AudioInterface] LIVE MODE activ — audio → GeminiLive")

    def arm_wake_interrupt(self) -> None:
        """
        Armează wake word-ul ca mecanism de întrerupere pe durata sesiunii live.
        Folosit în „focus mode": barge-in-ul pe voce e dezactivat, deci singura
        cale de a-l opri pe Chronos e să spui din nou wake word-ul.
        """
        if self._wake_interrupt_armed:
            return
        # Pornim de la zero: frame-urile vechi din coadă ar putea produce o
        # detecție falsă imediată.
        self._reset_detector()
        self._wake_interrupt_armed = True
        logger.info("🖐️ [AudioInterface] Wake-interrupt ARMAT (focus mode).")

    def disarm_wake_interrupt(self) -> None:
        """Dezarmează wake-interrupt — revenim la barge-in normal pe voce."""
        if not self._wake_interrupt_armed:
            return
        self._wake_interrupt_armed = False
        self._reset_detector()
        logger.info("🟢 [AudioInterface] Wake-interrupt dezarmat.")

    def disable_live_mode(self) -> None:
        """Dezactivează live mode: revenim la detecția de wake word."""
        self._live_mode = False
        self._live_queue = None
        self._wake_interrupt_armed = False
        self._reset_detector()
        logger.info("🟢 [AudioInterface] WAKE WORD MODE restaurat.")

    # ─────────────────────────────────────────────────────
    # CLEANUP
    # ─────────────────────────────────────────────────────

    def _shutdown_pool(self) -> None:
        pool, self._infer_pool = self._infer_pool, None
        if pool is not None:
            pool.shutdown(wait=False)

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
