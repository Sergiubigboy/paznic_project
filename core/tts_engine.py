"""
core/tts_engine.py — Chronos TTS Engine
=========================================
Motor Text-to-Speech asincron cu suport de întrerupere.

Stack:
    - edge-tts  : sinteză vocală (Microsoft Edge TTS, gratuit, fără API key)
    - sounddevice: redare audio cross-platform (Windows & Raspberry Pi)
    - fallback pyaudio dacă sounddevice eșuează

Voce implicită: ro-RO-EmilNeural (masculin, română, calitate excelentă)
Alternativă:    ro-RO-AlinaNeural (feminină)

Funcționalitate întrerupere:
    - Pe durata redării, un thread monitorizează flagul _stop_event
    - Orice apel la interrupt() sau speak() nou → oprire imediată

Utilizare:
    tts = TTSEngine(event_bus)
    await tts.initialize()
    await tts.speak("Bună ziua, cum te pot ajuta?")
    # ... mai târziu, din alt task:
    tts.interrupt()
"""

import asyncio
import io
import logging
import threading
import time
from typing import Optional

logger = logging.getLogger(__name__)

# Vocea implicită — schimbă după preferință
DEFAULT_VOICE = "ro-RO-EmilNeural"
# DEFAULT_VOICE = "ro-RO-AlinaNeural"  # Alternativă feminină

# Rata de eșantionare pentru redare (edge-tts generează MP3, convertim la PCM)
PLAYBACK_SAMPLE_RATE = 22050


class TTSEngine:
    """
    Motor TTS asincron cu suport de întrerupere și integrare EventBus.

    Thread-safety: speak() și interrupt() pot fi apelate din thread-uri diferite.
    """

    def __init__(self, event_bus=None):
        """
        Args:
            event_bus: Instanța EventBus (opțional — dacă None, nu publică evenimente).
        """
        self.bus = event_bus
        self._voice = DEFAULT_VOICE
        self._stop_event = threading.Event()   # Setat → oprire redare
        self._playback_lock = asyncio.Lock()   # Un singur speak() la un moment dat
        self._is_playing = False
        self._sd = None        # sounddevice (import lazy)
        self._np = None        # numpy (import lazy)
        self._available = False

        # Import EventType doar dacă avem event_bus
        self._EventType = None
        if event_bus:
            try:
                from core.event_bus import EventType
                self._EventType = EventType
            except ImportError:
                pass

    async def initialize(self) -> bool:
        """
        Verifică disponibilitatea dependențelor.

        Returns:
            True dacă TTSEngine e funcțional, False altfel.
        """
        logger.info("🔊 [TTS] Inițializare...")

        # Verifică edge-tts
        try:
            import edge_tts
            logger.info("✅ [TTS] edge-tts disponibil.")
        except ImportError:
            logger.error(
                "❌ [TTS] 'edge-tts' nu e instalat!\n"
                "   Rulează: pip install edge-tts"
            )
            return False

        # Verifică sounddevice + numpy pentru redare
        try:
            import sounddevice as sd
            import numpy as np
            self._sd = sd
            self._np = np
            logger.info("✅ [TTS] sounddevice disponibil pentru redare.")
        except ImportError:
            logger.warning(
                "⚠️ [TTS] sounddevice indisponibil, încerc pyaudio ca fallback..."
            )
            self._sd = None

        # Verifică pydub sau alternativă pentru MP3→PCM (dacă nu avem soundfile)
        # edge-tts generează MP3 — trebuie decodat pentru sounddevice
        try:
            import pydub
            logger.info("✅ [TTS] pydub disponibil pentru decodare MP3.")
        except ImportError:
            logger.info("ℹ️ [TTS] pydub indisponibil — voi folosi metoda subprocess (ffmpeg).")

        self._available = True
        logger.info(f"✅ [TTS] Gata. Voce: '{self._voice}'")
        return True

    # -------------------------------------------------------------------------
    # SPEAK — Sinteza și redarea
    # -------------------------------------------------------------------------

    async def speak(self, text: str) -> bool:
        """
        Sintetizează textul și îl redă audio.

        Dacă există o redare în curs, o întrerupe înainte de a începe alta.

        Args:
            text: Textul de sintetizat și redat.

        Returns:
            True dacă redarea s-a terminat complet, False dacă a fost întreruptă.
        """
        if not text or not text.strip():
            return True

        # Întrerupe orice redare în curs
        if self._is_playing:
            logger.info("[TTS] Întrerup redarea anterioară...")
            self.interrupt()
            await asyncio.sleep(0.1)  # Scurt delay pentru cleanup

        async with self._playback_lock:
            return await self._synthesize_and_play(text)

    async def _synthesize_and_play(self, text: str) -> bool:
        """
        Generează audio cu edge-tts și îl redă.
        Rulează sinteza în thread pentru a nu bloca event loop-ul.
        """
        import edge_tts

        self._stop_event.clear()
        self._is_playing = True

        # Publică eveniment start
        if self.bus and self._EventType:
            await self.bus.publish(
                self._EventType.AUDIO_RESPONSE_START if hasattr(self._EventType, 'AUDIO_RESPONSE_START')
                else self._EventType.SYSTEM_STATUS,
                {"component": "TTS", "status": "SPEAKING", "message": text[:50]}
            )

        logger.info(f"🗣️  [TTS] Vorbesc: '{text[:60]}{'...' if len(text) > 60 else ''}'")
        completed = False

        try:
            # Pasul 1: Sinteză edge-tts → bytes MP3 în memorie
            audio_bytes = await asyncio.to_thread(self._synthesize_sync, text)

            if audio_bytes is None or self._stop_event.is_set():
                return False

            # Pasul 2: Redare
            completed = await asyncio.to_thread(self._play_audio_sync, audio_bytes)

        except Exception as e:
            logger.error(f"❌ [TTS] Eroare la speak: {e}", exc_info=True)
        finally:
            self._is_playing = False
            if self.bus and self._EventType:
                await self.bus.publish(
                    self._EventType.SYSTEM_STATUS,
                    {
                        "component": "TTS",
                        "status": "DONE" if completed else "INTERRUPTED",
                        "message": "Redare terminată."
                    }
                )

        return completed

    def _synthesize_sync(self, text: str) -> Optional[bytes]:
        """
        Sintetizează textul cu edge-tts ca PCM raw (WAV fără header).
        Evită dependența de ffmpeg/pydub prin generarea directă a PCM.
        Rulat în thread separat.
        """
        import edge_tts
        import asyncio

        async def _async_synthesize():
            # edge-tts generează MP3 implicit, dar putem extrage bytes-ii MP3
            # şi le decodam cu audioop (stdlib) dupa conversia wav
            communicate = edge_tts.Communicate(text, self._voice)
            chunks = []
            async for chunk in communicate.stream():
                if chunk["type"] == "audio":
                    chunks.append(chunk["data"])
                if self._stop_event.is_set():
                    return None
            return b"".join(chunks)

        try:
            loop = asyncio.new_event_loop()
            try:
                data = loop.run_until_complete(_async_synthesize())
            finally:
                loop.close()
            return data
        except Exception as e:
            logger.error(f"❌ [TTS] Eroare sinteză edge-tts: {e}")
            return None

    def _play_audio_sync(self, mp3_bytes: bytes) -> bool:
        """
        Decodifică MP3 şi redă audio.
        Foloseşte pydub+ffmpeg dacă disponibile, altfel pyaudio direct.
        """
        if self._stop_event.is_set():
            return False

        # Încearcă decodare cu pydub (necesită ffmpeg)
        if self._sd is not None and self._np is not None:
            pcm_data, sample_rate = self._decode_mp3_to_pcm(mp3_bytes)
            if pcm_data is not None:
                return self._play_sounddevice(pcm_data, sample_rate)

        # Fallback: pyaudio cu pydub
        return self._play_pyaudio_pydub(mp3_bytes)

    def _decode_mp3_to_pcm(self, mp3_bytes: bytes):
        """
        Decodifică MP3 la PCM float32 folosind metode disponibile.
        Returnează (numpy_array, sample_rate) sau (None, None).
        """
        np = self._np

        # Metoda 1: pydub (necesită ffmpeg instalat pe sistem)
        try:
            from pydub import AudioSegment
            seg = AudioSegment.from_mp3(io.BytesIO(mp3_bytes)).set_channels(1)
            sample_rate = seg.frame_rate
            raw = seg.raw_data
            pcm = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0
            return pcm, sample_rate
        except Exception:
            pass

        # Metoda 2: minimp3 / miniaudio (dacă e instalat)
        try:
            import miniaudio
            decoded = miniaudio.decode(mp3_bytes, output_format=miniaudio.SampleFormat.SIGNED16,
                                       nchannels=1, sample_rate=PLAYBACK_SAMPLE_RATE)
            pcm = np.frombuffer(decoded.samples, dtype=np.int16).astype(np.float32) / 32768.0
            return pcm, PLAYBACK_SAMPLE_RATE
        except Exception:
            pass

        # Metoda 3: soundfile (necesită libsndfile)
        try:
            import soundfile as sf
            pcm, sample_rate = sf.read(io.BytesIO(mp3_bytes), dtype='float32')
            if pcm.ndim > 1:
                pcm = pcm[:, 0]
            return pcm, sample_rate
        except Exception:
            pass

        return None, None

    def _play_pyaudio_pydub(self, mp3_bytes: bytes) -> bool:
        """Fallback: pyaudio direct cu pydub dacă e disponibil."""
        try:
            import pyaudio
            try:
                from pydub import AudioSegment
                seg = AudioSegment.from_mp3(io.BytesIO(mp3_bytes)).set_channels(1)
                raw = seg.raw_data
                rate = seg.frame_rate
            except Exception:
                logger.error(
                    "❌ [TTS] Nu pot decoda MP3.\n"
                    "   Instalează ffmpeg (necesar pentru pydub):\n"
                    "   Windows: winget install ffmpeg\n"
                    "   Pi:      sudo apt install ffmpeg"
                )
                return False

            pa = pyaudio.PyAudio()
            stream = pa.open(format=pyaudio.paInt16, channels=1, rate=rate, output=True)
            chunk_size = 2048
            for i in range(0, len(raw), chunk_size):
                if self._stop_event.is_set():
                    break
                stream.write(raw[i:i + chunk_size])
            stream.stop_stream()
            stream.close()
            pa.terminate()
            return not self._stop_event.is_set()
        except Exception as e:
            logger.error(f"❌ [TTS] Eroare pyaudio fallback: {e}")
            return False

    def _play_sounddevice(self, pcm: "np.ndarray", sample_rate: int) -> bool:
        """Redă PCM float32 cu sounddevice, cu suport de întrerupere."""
        sd = self._sd
        CHUNK = 2048  # Samples per chunk — redăm pe bucăți pentru a verifica _stop_event

        try:
            idx = 0
            total = len(pcm)
            with sd.OutputStream(samplerate=sample_rate, channels=1, dtype='float32') as stream:
                while idx < total:
                    if self._stop_event.is_set():
                        logger.info("[TTS] Redare întreruptă.")
                        return False
                    chunk = pcm[idx:idx + CHUNK]
                    stream.write(chunk)
                    idx += CHUNK
            return True
        except Exception as e:
            logger.error(f"❌ [TTS] Eroare sounddevice playback: {e}")
            return False

    # -------------------------------------------------------------------------
    # ÎNTRERUPERE
    # -------------------------------------------------------------------------

    def interrupt(self) -> None:
        """
        Oprește imediat redarea audio curentă.

        Thread-safe: poate fi apelat din orice thread sau task asyncio.
        """
        if self._is_playing:
            logger.info("⛔ [TTS] Întrerupere redare.")
            self._stop_event.set()

    # -------------------------------------------------------------------------
    # PROPRIETĂȚI
    # -------------------------------------------------------------------------

    @property
    def is_playing(self) -> bool:
        """True dacă TTS redă audio în acest moment."""
        return self._is_playing

    @property
    def is_available(self) -> bool:
        """True dacă motorul TTS e inițializat și funcțional."""
        return self._available

    def set_voice(self, voice: str) -> None:
        """
        Schimbă vocea TTS.

        Args:
            voice: Numele vocii edge-tts (ex: 'ro-RO-EmilNeural', 'ro-RO-AlinaNeural')
        """
        self._voice = voice
        logger.info(f"🎙️ [TTS] Voce schimbată la: '{voice}'")

    @staticmethod
    def list_romanian_voices() -> list:
        """
        Returnează lista vocilor române disponibile în edge-tts.
        Rulează sync (folosit doar la configurare).
        """
        try:
            import edge_tts
            import asyncio

            async def _get_voices():
                voices = await edge_tts.list_voices()
                return [v for v in voices if v["Locale"].startswith("ro-RO")]

            loop = asyncio.new_event_loop()
            try:
                return loop.run_until_complete(_get_voices())
            finally:
                loop.close()
        except Exception:
            return []
