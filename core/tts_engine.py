"""
core/tts_engine.py — Chronos TTS Engine (streaming, pipelined)
================================================================
Motor Text-to-Speech pentru calea TEXT (terminal + dashboard). Calea VOCALĂ
nu trece pe aici — aia merge pe Gemini Live native-audio, care livrează direct
audio.

Ce s-a schimbat față de varianta veche și DE CE
-----------------------------------------------
1. NU MAI AȘTEPTĂM TOT TEXTUL CA SĂ ÎNCEPEM SĂ VORBIM.
   Înainte: LLM-ul genera răspunsul COMPLET → abia apoi pornea sinteza pe tot
   textul → abia apoi se auzea ceva. Pentru un răspuns de trei fraze asta
   însemna câteva secunde de tăcere.
   Acum: `speak_stream()` primește textul în bucăți, pe măsură ce modelul îl
   produce, îl taie în clauze și trimite PRIMA clauză la sinteză imediat.
   Cât timp aia se aude (1-2s), următoarea se sintetizează în paralel (~0.35s),
   deci conducta rămâne mereu înaintea redării.

2. FĂRĂ EVENT LOOP NOU PER SINTEZĂ.
   Varianta veche făcea `asyncio.new_event_loop()` într-un thread pentru
   fiecare `speak()`, ca să apeleze un API care e deja async. Acum se apelează
   direct pe bucla existentă — e I/O de rețea, exact ce știe asyncio să facă.

3. DECODARE PE CALEA CARE CHIAR FUNCȚIONEAZĂ.
   `pydub` are nevoie de ffmpeg, care nu e instalat aici; fiecare utterance
   încerca ffmpeg, eșua, și abia apoi cădea pe miniaudio. Acum miniaudio e
   primul, iar pydub rămâne doar ca variantă de rezervă.

4. ÎNTRERUPERE PE EPOCĂ, nu pe un flag global.
   Varianta veche: `speak()` nou → `interrupt()` → `_stop_event.clear()`. Dacă
   threadul vechi de redare nu apucase încă să vadă flagul, `clear()` îl
   "de-întrerupea" și continua să vorbească peste replica nouă. Acum fiecare
   redare poartă un număr de epocă; orice epocă depășită se oprește singură.

Stack: edge-tts (sinteză) + miniaudio (decodare MP3) + sounddevice (redare).
"""

from __future__ import annotations

import asyncio
import io
import logging
import queue
import re
import threading
from typing import AsyncIterator, Iterable, List, Optional, Tuple

logger = logging.getLogger(__name__)

try:
    from config import TTS_VOICE_FALLBACK, TTS_RATE
except ImportError:
    TTS_VOICE_FALLBACK = "ro-RO-EmilNeural"
    TTS_RATE = "+0%"

DEFAULT_VOICE = TTS_VOICE_FALLBACK
# edge-tts livrează MP3 mono la 24kHz. Fixăm rata ca să nu reschimbăm
# niciodată stream-ul de ieșire în mijlocul unei replici.
TTS_SAMPLE_RATE = 24000


# =============================================================================
# ÎMPĂRȚIREA ÎN CLAUZE — nucleul streamingului
# =============================================================================

# Terminatori tari (sfârșit de propoziție) și moi (pauză naturală).
_HARD_END = ".!?…\n"
_SOFT_END = ",;:—"

_ABBREV = re.compile(r"(?:\b(?:dl|dna|nr|etc|ex|art|pag|vs|dr|prof|ing)\.)$", re.I)


def split_clauses(
    buffer: str,
    *,
    min_len: int = 18,
    soft_len: int = 60,
    max_len: int = 200,
    flush: bool = False,
) -> Tuple[List[str], str]:
    """Taie `buffer` în clauze rostibile.

    Întoarce (clauze_complete, rest_neconsumat). Apelantul păstrează restul și
    îl re-alimentează cu textul următor primit de la model.

    Reguli, în ordinea în care contează pentru cum sună:
      - Se taie la terminator tare (.!?…) dacă avem cel puțin `min_len`
        caractere — sub atât, bucata e prea scurtă și sinteza sună ciopârțit.
      - Se taie la terminator moale (,;:) doar peste `soft_len` — ține latența
        jos pe frazele lungi fără să rupă vorbirea în bucăți mici.
      - Se taie forțat la ultimul spațiu peste `max_len` — plasă de siguranță
        pentru text fără nicio punctuație.
      - NU se taie într-un număr („1240.50") și nici după abrevieri uzuale.

    `flush=True` (modelul a terminat) întoarce și restul, oricât ar fi de scurt.
    """
    out: List[str] = []
    start = 0
    i = 0
    n = len(buffer)

    while i < n:
        ch = buffer[i]
        length = i - start + 1

        if ch in _HARD_END:
            # „1240.50" / „3.5" — punctul e zecimal, nu sfârșit de frază.
            if (
                ch == "."
                and 0 < i < n - 1
                and buffer[i - 1].isdigit()
                and buffer[i + 1].isdigit()
            ):
                i += 1
                continue
            if ch == "." and _ABBREV.search(buffer[start:i + 1]):
                i += 1
                continue
            if length >= min_len or ch == "\n":
                piece = buffer[start:i + 1].strip()
                if piece:
                    out.append(piece)
                start = i + 1
        elif ch in _SOFT_END and length >= soft_len:
            piece = buffer[start:i + 1].strip()
            if piece:
                out.append(piece)
            start = i + 1
        elif length >= max_len:
            cut = buffer.rfind(" ", start, i + 1)
            if cut <= start:
                cut = i
            piece = buffer[start:cut].strip()
            if piece:
                out.append(piece)
            start = cut + 1
            i = start
            continue

        i += 1

    rest = buffer[start:]
    if flush:
        tail = rest.strip()
        if tail:
            out.append(tail)
        rest = ""
    return out, rest


# =============================================================================
# MOTORUL TTS
# =============================================================================

class _PlaybackWorker:
    """Thread dedicat de redare.

    Dedicat, nu `asyncio.to_thread`: pool-ul default e partajat cu Flask,
    dispatcher-ul și analiza de emoții. Dacă redarea ar aștepta acolo un
    worker liber, s-ar auzi ca întreruperi în vorbire.
    """

    __slots__ = ("_q", "_thread", "_sd", "_stop", "_epoch_of")

    def __init__(self, sd):
        self._sd = sd
        self._q: "queue.Queue" = queue.Queue(maxsize=64)
        self._stop = threading.Event()
        self._thread = threading.Thread(
            target=self._run, name="tts-playback", daemon=True
        )
        self._thread.start()

    def submit(self, epoch: int, pcm) -> None:
        self._q.put((epoch, pcm))

    def end_of_utterance(self, epoch: int) -> None:
        self._q.put((epoch, None))

    def _run(self) -> None:
        stream = None
        try:
            while not self._stop.is_set():
                try:
                    epoch, pcm = self._q.get(timeout=0.25)
                except queue.Empty:
                    continue

                if pcm is None:                      # sfârșit de replică
                    if stream is not None:
                        try:
                            stream.stop()
                            stream.close()
                        except Exception:
                            pass
                        stream = None
                    continue

                if epoch != _ENGINE_EPOCH[0]:        # replică depășită
                    continue

                try:
                    if stream is None:
                        stream = self._sd.OutputStream(
                            samplerate=TTS_SAMPLE_RATE, channels=1, dtype="int16"
                        )
                        stream.start()
                    # Bucăți mici, ca `interrupt()` să se simtă instantaneu.
                    step = 2048
                    for off in range(0, len(pcm), step):
                        if epoch != _ENGINE_EPOCH[0] or self._stop.is_set():
                            break
                        stream.write(pcm[off:off + step])
                except Exception as e:
                    logger.error(f"❌ [TTS] Redare: {e}")
                    try:
                        if stream is not None:
                            stream.close()
                    except Exception:
                        pass
                    stream = None
        finally:
            if stream is not None:
                try:
                    stream.stop()
                    stream.close()
                except Exception:
                    pass

    def shutdown(self) -> None:
        self._stop.set()
        try:
            self._q.put_nowait((-1, None))
        except queue.Full:
            pass
        self._thread.join(timeout=2.0)


# Epoca curentă, partajată cu threadul de redare. Listă cu un element ca să fie
# citită prin referință fără lock (int-urile CPython sunt imuabile, iar
# citirea/scrierea unui element de listă e atomică sub GIL).
_ENGINE_EPOCH = [0]


class TTSEngine:
    """
    Motor TTS asincron, cu conductă de sinteză și întrerupere pe epocă.

    Thread-safety: `interrupt()` poate fi apelat din orice thread sau task.
    """

    __slots__ = (
        "bus", "_voice", "_rate", "_playback_lock", "_is_playing",
        "_sd", "_np", "_available", "_EventType", "_worker", "_decoder",
    )

    def __init__(self, event_bus=None):
        self.bus = event_bus
        self._voice = DEFAULT_VOICE
        self._rate = TTS_RATE
        self._playback_lock = asyncio.Lock()
        self._is_playing = False
        self._sd = None
        self._np = None
        self._available = False
        self._worker: Optional[_PlaybackWorker] = None
        self._decoder = None          # "miniaudio" | "pydub"

        self._EventType = None
        if event_bus:
            try:
                from core.event_bus import EventType
                self._EventType = EventType
            except ImportError:
                pass

    # ─────────────────────────────────────────────────────────────────────
    # INIȚIALIZARE
    # ─────────────────────────────────────────────────────────────────────

    async def initialize(self) -> bool:
        """Verifică dependențele și pregătește conducta de redare."""
        logger.info("🔊 [TTS] Inițializare...")

        try:
            import edge_tts  # noqa: F401
        except ImportError:
            logger.error("❌ [TTS] 'edge-tts' nu e instalat! pip install edge-tts")
            return False

        try:
            import numpy as np
            import sounddevice as sd
            self._sd, self._np = sd, np
        except ImportError as e:
            logger.error(f"❌ [TTS] Redare indisponibilă ({e}) — TTS dezactivat.")
            return False

        self._decoder = self._pick_decoder()
        if self._decoder is None:
            logger.error(
                "❌ [TTS] Niciun decodor MP3 disponibil.\n"
                "   pip install miniaudio   (recomandat, fără dependențe externe)\n"
                "   sau instalează ffmpeg pentru pydub."
            )
            return False

        self._worker = _PlaybackWorker(self._sd)
        self._available = True
        logger.info(
            f"✅ [TTS] Gata. Voce: '{self._voice}' | rate: {self._rate} "
            f"| decodor: {self._decoder}"
        )
        # Prima conexiune edge-tts e cea mai lentă (DNS + TLS + WebSocket).
        # O deschidem acum, în fundal, ca prima replică reală să nu o plătească.
        asyncio.create_task(self._warmup())
        return True

    @staticmethod
    def _pick_decoder() -> Optional[str]:
        """miniaudio întâi: e pur nativ, ~15ms per replică, fără subprocese.
        pydub are nevoie de ffmpeg, care de multe ori lipsește — dacă îl punem
        primul, fiecare replică plătește un subprocess eșuat înainte de a reuși."""
        try:
            import miniaudio  # noqa: F401
            return "miniaudio"
        except ImportError:
            pass
        try:
            from pydub import AudioSegment  # noqa: F401
            from pydub.utils import which
            if which("ffmpeg") or which("avconv"):
                return "pydub"
        except ImportError:
            pass
        return None

    async def _warmup(self) -> None:
        try:
            import edge_tts
            comm = edge_tts.Communicate(".", self._voice, rate=self._rate)
            async for _ in comm.stream():
                break
            logger.debug("[TTS] Conexiune edge-tts pre-încălzită.")
        except Exception as e:
            logger.debug(f"[TTS] Warmup eșuat (non-critic): {e}")

    # ─────────────────────────────────────────────────────────────────────
    # SINTEZĂ
    # ─────────────────────────────────────────────────────────────────────

    async def _synthesize(self, text: str) -> Optional[bytes]:
        """Sintetizează o clauză → bytes MP3. edge-tts streamează deja, deci
        acumulăm doar bucata asta scurtă, nu tot răspunsul."""
        import edge_tts
        try:
            comm = edge_tts.Communicate(text, self._voice, rate=self._rate)
            parts = [
                ch["data"] async for ch in comm.stream() if ch["type"] == "audio"
            ]
            return b"".join(parts) if parts else None
        except Exception as e:
            logger.error(f"❌ [TTS] Sinteză eșuată pentru {text[:40]!r}: {e}")
            return None

    def _decode(self, mp3: bytes):
        """MP3 → numpy int16 mono @ TTS_SAMPLE_RATE. Rulat în thread."""
        np = self._np
        if self._decoder == "miniaudio":
            import miniaudio
            dec = miniaudio.decode(
                mp3,
                output_format=miniaudio.SampleFormat.SIGNED16,
                nchannels=1,
                sample_rate=TTS_SAMPLE_RATE,
            )
            return np.frombuffer(dec.samples, dtype=np.int16)
        from pydub import AudioSegment
        seg = (
            AudioSegment.from_mp3(io.BytesIO(mp3))
            .set_channels(1)
            .set_frame_rate(TTS_SAMPLE_RATE)
        )
        return np.frombuffer(seg.raw_data, dtype=np.int16)

    async def _speak_clause(self, text: str, epoch: int) -> bool:
        """Sintetizează + trimite la redare o singură clauză."""
        if epoch != _ENGINE_EPOCH[0]:
            return False
        mp3 = await self._synthesize(text)
        if not mp3 or epoch != _ENGINE_EPOCH[0]:
            return False
        try:
            pcm = await asyncio.to_thread(self._decode, mp3)
        except Exception as e:
            logger.error(f"❌ [TTS] Decodare eșuată: {e}")
            return False
        if epoch != _ENGINE_EPOCH[0]:
            return False
        self._worker.submit(epoch, pcm)
        return True

    # ─────────────────────────────────────────────────────────────────────
    # API PUBLIC
    # ─────────────────────────────────────────────────────────────────────

    async def speak(self, text: str) -> bool:
        """Rostește un text deja complet.

        Chiar și aici mergem pe clauze: prima se aude după ~0.35s, în loc să
        așteptăm sinteza întregului răspuns.
        """
        if not text or not text.strip():
            return True
        return await self.speak_stream(_as_async_iter([text]))

    async def speak_stream(self, chunks: AsyncIterator[str]) -> bool:
        """Rostește text pe măsură ce sosește.

        `chunks` produce bucăți de text (de obicei direct de la streamul LLM).
        Prima clauză completă pleacă la sinteză imediat ce e disponibilă, deci
        Chronos începe să vorbească înainte ca modelul să termine de generat.

        Returns:
            True dacă replica s-a rostit integral, False dacă a fost întreruptă.
        """
        if not self._available or self._worker is None:
            # Fără redare, consumăm streamul ca să nu lăsăm generatorul agățat.
            async for _ in chunks:
                pass
            return False

        async with self._playback_lock:
            epoch = _ENGINE_EPOCH[0] = _ENGINE_EPOCH[0] + 1
            self._is_playing = True
            spoken = [False]

            # Două etape rulate concurent, legate printr-o coadă de clauze:
            #   citire text  ──►  [clauze]  ──►  sinteză + decodare  ──► redare
            # Serializate (cum era prima variantă), sinteza unei clauze ținea pe
            # loc citirea streamului de la model — un stream HTTP lăsat necitit
            # secunde bune riscă și timeout. Așa, textul continuă să curgă cât
            # timp clauza precedentă se sintetizează.
            clause_q: asyncio.Queue = asyncio.Queue(maxsize=8)

            async def _reader() -> None:
                buffer = ""
                async for piece in chunks:
                    if epoch != _ENGINE_EPOCH[0]:
                        return
                    if not piece:
                        continue
                    buffer += piece
                    clauses, buffer = split_clauses(buffer)
                    for clause in clauses:
                        await clause_q.put(clause)
                tail, _ = split_clauses(buffer, flush=True)
                for clause in tail:
                    await clause_q.put(clause)

            async def _speaker() -> None:
                while True:
                    clause = await clause_q.get()
                    if clause is None:
                        return
                    if epoch != _ENGINE_EPOCH[0]:
                        return
                    if await self._speak_clause(clause, epoch):
                        spoken[0] = True

            await self._publish_start()
            reader = asyncio.create_task(_reader(), name="tts-reader")
            speaker = asyncio.create_task(_speaker(), name="tts-speaker")
            try:
                await reader
                await clause_q.put(None)      # gata textul → golește conducta
                await speaker
            except Exception as e:
                logger.error(f"❌ [TTS] Eroare în stream: {e}", exc_info=True)
            finally:
                for task in (reader, speaker):
                    if not task.done():
                        task.cancel()
                        try:
                            await task
                        except (asyncio.CancelledError, Exception):
                            pass
                self._worker.end_of_utterance(epoch)
                self._is_playing = False
                completed = (epoch == _ENGINE_EPOCH[0]) and spoken[0]
                await self._publish_end(completed)

            return completed

    def interrupt(self) -> None:
        """Oprește imediat redarea curentă. Thread-safe.

        Incrementarea epocii invalidează tot ce e în zbor — clauza care se
        sintetizează, cea care se decodează și cea care se redă — fără să
        atingă replica următoare.
        """
        if self._is_playing:
            logger.info("⛔ [TTS] Întrerupere redare.")
        _ENGINE_EPOCH[0] += 1

    async def shutdown(self) -> None:
        """Oprește redarea și eliberează threadul."""
        self.interrupt()
        if self._worker is not None:
            await asyncio.to_thread(self._worker.shutdown)
            self._worker = None
        self._available = False

    # ─────────────────────────────────────────────────────────────────────
    # EVENIMENTE / PROPRIETĂȚI
    # ─────────────────────────────────────────────────────────────────────

    async def _publish_start(self) -> None:
        if self.bus and self._EventType:
            await self.bus.publish(
                self._EventType.AUDIO_RESPONSE_START, {"component": "TTS"}
            )

    async def _publish_end(self, completed: bool) -> None:
        if self.bus and self._EventType:
            await self.bus.publish(
                self._EventType.AUDIO_RESPONSE_END, {"completed": completed}
            )

    @property
    def is_playing(self) -> bool:
        return self._is_playing

    @property
    def is_available(self) -> bool:
        return self._available

    def set_voice(self, voice: str) -> None:
        self._voice = voice
        logger.info(f"🎙️ [TTS] Voce schimbată la: '{voice}'")

    @staticmethod
    async def list_romanian_voices() -> list:
        """Vocile ro-RO disponibile în edge-tts."""
        try:
            import edge_tts
            voices = await edge_tts.list_voices()
            return [v for v in voices if v.get("Locale", "").startswith("ro-RO")]
        except Exception as e:
            logger.warning(f"⚠️ [TTS] Nu pot lista vocile: {e}")
            return []


async def _as_async_iter(items: Iterable[str]) -> AsyncIterator[str]:
    for it in items:
        yield it


# =============================================================================
# CLI DE TEST — `python -m core.tts_engine "text de rostit"`
# =============================================================================
# Fără argument, rulează doar testele împărțitorului de clauze (offline, fără
# rețea și fără difuzor) — util ca să verifici logica de tăiere pe Pi, prin ssh.

if __name__ == "__main__":
    import sys

    # Consola Windows e cp1252 implicit și crapă pe diacritice/emoji.
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

    logging.basicConfig(level=logging.INFO, format="%(message)s")
    arg = " ".join(sys.argv[1:]).strip()

    if not arg:
        cases = [
            ("Salut. Ce faci?", ["Salut. Ce faci?"]),
            ("Bugetul e 1240.50 lei acum, deci mai ai destul pentru weekend.",
             ["Bugetul e 1240.50 lei acum,",
              "deci mai ai destul pentru weekend."]),
        ]
        print("── ÎMPĂRȚIRE ÎN CLAUZE ─────────────────────────────")
        for text, _ in cases:
            got, rest = split_clauses(text, flush=True)
            print(f"  in : {text}")
            for g in got:
                print(f"  out: {g!r}")
            print()

        print("── STREAMING INCREMENTAL (cum vine de la LLM) ──────")
        buf = ""
        emitted = []
        for piece in ["Sergiu, ", "am verificat. ", "Ai trei remin", "dere active ",
                      "si doua deadline-uri. ", "Vrei sa ti le citesc?"]:
            buf += piece
            clauses, buf = split_clauses(buf)
            for c in clauses:
                emitted.append(c)
                print(f"  → la sinteză imediat: {c!r}")
        tail, _ = split_clauses(buf, flush=True)
        for c in tail:
            emitted.append(c)
            print(f"  → la sinteză (final):  {c!r}")
        print(f"\n  {len(emitted)} clauze; prima a plecat la sinteză "
              f"înainte ca modelul să termine.")
        sys.exit(0)

    async def _demo():
        eng = TTSEngine()
        if not await eng.initialize():
            print("TTS indisponibil.")
            return
        import time
        t0 = time.perf_counter()
        ok = await eng.speak(arg)
        print(f"\ncompletat={ok} în {time.perf_counter() - t0:.2f}s")
        await eng.shutdown()

    asyncio.run(_demo())
