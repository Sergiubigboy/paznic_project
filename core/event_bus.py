"""
core/event_bus.py — Chronos Event Bus
======================================
Inima sistemului Chronos. Implementează un Event Bus asincron bazat pe
asyncio.Queue, care permite comunicarea decuplată între componente (audio,
LLM, terminal, web) prin publicare/abonare de evenimente.

Arhitectură:
    - Fiecare subscriber primește propria sa coadă (asyncio.Queue) → nu există
      interferențe între consumatori și nu se pierd mesaje.
    - publish() este non-blocant: pune datele în toate cozile abonaților.
    - subscribe() returnează un AsyncGenerator pentru a consuma evenimentele
      elegant cu `async for`.

Exemplu de utilizare:
    bus = EventBus()

    # Subscriber:
    async def listener():
        async for data in bus.subscribe(EventType.WAKE_WORD_DETECTED):
            print(f"Wake word detectat: {data}")

    # Publisher:
    await bus.publish(EventType.WAKE_WORD_DETECTED, {"timestamp": time.time()})
"""

import asyncio
import logging
from enum import Enum, auto
from typing import Any, Dict, List

logger = logging.getLogger(__name__)

# Sentinela de oprire. Obiect dedicat, nu None: None e un payload perfect
# valid pentru un eveniment, iar folosirea lui ca semnal de shutdown ar fi
# oprit abonații la primul publish fără date.
_STOP = object()


# =============================================================================
# DEFINIREA EVENIMENTELOR SISTEMULUI
# =============================================================================

class EventType(Enum):
    """
    Catalogul complet al evenimentelor care circulă prin sistemul Chronos.

    Convenție de numire:
        - SURSA_ACTIUNEA (ex: AUDIO_WAKE_WORD_DETECTED)
        - Evenimente cu prefixul LLM_ sunt răspunsuri de la creier
        - Evenimente cu prefixul SYSTEM_ sunt de management intern
    """

    # --- Audio / Wake Word ---
    WAKE_WORD_DETECTED = auto()
    """Emis de AudioInterface când openWakeWord detectează cuvântul de trezire.
    Payload: {"timestamp": float, "score": float, "model_name": str}
    """

    WAKE_WORD_INTERRUPT = auto()
    """Emis de AudioInterface când wake word-ul e detectat ÎN TIMPUL unei sesiuni
    vocale live aflate în „focus mode" (Chronos livrează un răspuns important,
    barge-in-ul pe voce e dezactivat). NU pornește o sesiune nouă — semnalizează
    doar că Sergiu vrea să întrerupă răspunsul curent.
    Payload: {"timestamp": float, "score": float, "model_name": str}
    """

    AUDIO_STREAM_CHUNK = auto()
    """Emis de AudioInterface cu fiecare chunk audio brut (pentru streaming LLM).
    Payload: {"chunk": bytes, "sample_rate": int, "timestamp": float}
    """

    AUDIO_LISTENING_START = auto()
    """Emis când sistemul intră în modul de ascultare activă post-wake-word.
    Payload: {"source": str}  # "wake_word" sau "manual"
    """

    AUDIO_LISTENING_STOP = auto()
    """Emis când înregistrarea audio s-a terminat (silențiu detectat).
    Payload: {"duration": float}
    """

    AUDIO_INTERRUPT = auto()
    """Emis de AudioInterface când utilizatorul vorbește în timp ce TTS redă.
    Payload: {"timestamp": float, "amplitude": int}
    """

    AUDIO_RESPONSE_START = auto()
    """Emis de TTSEngine când începe redarea răspunsului vocal.
    Payload: {"text": str}
    """

    AUDIO_RESPONSE_END = auto()
    """Emis de TTSEngine când redarea s-a terminat (normal sau întrerupt).
    Payload: {"completed": bool}
    """

    # --- Intrare Comenzi ---
    TERMINAL_COMMAND_RECEIVED = auto()
    """Emis de loop-ul de terminal când utilizatorul tastează o comandă.
    Payload: {"text": str, "timestamp": float, "source": "terminal"}
    """

    VOICE_COMMAND_RECEIVED = auto()
    """Emis de LLMRouter după transcriere STT, cu comanda vocală finală.
    Payload: {"text": str, "timestamp": float, "source": "voice"}
    """

    # --- Răspunsuri LLM ---
    LLM_TEXT_RESPONSE = auto()
    """Emis de LLMRouter cu răspunsul text al asistentului (mod terminal).
    Payload: {"text": str, "source_command": str, "intents": list, "actions": list}
    """

    LLM_AUDIO_RESPONSE = auto()
    """Emis de LLMRouter cu răspunsul audio (mod voce, Pasul 2 — TTS stream).
    Payload: {"audio_data": bytes, "text": str, "sample_rate": int}
    """

    # --- Execuție Tool-uri ---
    EXECUTE_TOOL = auto()
    """Emis de LLMRouter când LLM-ul decide să apeleze un tool.
    Payload: {
        "tool": str,          # ex: "wled_specialist", "music_specialist"
        "args": dict,         # argumentele tool-ului
        "source": str,        # "voice" sau "terminal"
        "request_id": str     # UUID pentru tracking
    }
    """

    TOOL_RESULT = auto()
    """Emis după execuția unui tool, cu rezultatul.
    Payload: {"tool": str, "result": Any, "success": bool, "request_id": str}
    """

    # --- Status Sistem ---
    SYSTEM_STATUS = auto()
    """Emis pentru logging general și actualizări de stare pentru dashboard.
    Payload: {"component": str, "status": str, "message": str, "level": str}
    """

    SYSTEM_SHUTDOWN = auto()
    """Emis când sistemul primește semnal de oprire.
    Payload: {"reason": str}
    """

    SYSTEM_READY = auto()
    """Emis de fiecare componentă când s-a inițializat cu succes.
    Payload: {"component": str}
    """



# =============================================================================
# SUBSCRIPTION
# =============================================================================

class Subscription:
    """
    Un abonament activ la un tip de eveniment.

    De ce e o clasă și nu un async generator (cum era înainte):
        Corpul unui async generator NU rulează până la primul `__anext__`.
        Adică `bus.subscribe(X)` nu înregistra nimic — înregistrarea se făcea
        abia când task-ul consumator apuca să ruleze. Orice eveniment publicat
        în fereastra aia se pierdea în tăcere. Exact așa dispărea SYSTEM_READY:
        componentele îl publicau în timpul inițializării, iar handler-ul se
        abona după, deci bannerul de pornire nu apărea niciodată.

        Aici coada e înregistrată SINCRON, în `subscribe()`, înainte ca
        apelantul să apuce să facă altceva.

    Se folosește identic:
        async for data in bus.subscribe(EventType.WAKE_WORD_DETECTED):
            ...
    """

    __slots__ = ("_bus", "_event_type", "_queue", "_closed")

    def __init__(self, bus: "EventBus", event_type: EventType, maxsize: int):
        self._bus = bus
        self._event_type = event_type
        self._queue: asyncio.Queue = asyncio.Queue(maxsize=maxsize)
        self._closed = False
        bus._register(event_type, self)

    # -- Protocol async-iterator ------------------------------------------
    def __aiter__(self) -> "Subscription":
        return self

    async def __anext__(self) -> Any:
        if self._closed:
            raise StopAsyncIteration
        try:
            data = await self._queue.get()
        except asyncio.CancelledError:
            # Task-ul consumator a fost anulat -> nu lăsăm coada înregistrată
            # în bus, altfel publish() ar continua să scrie într-o coadă pe
            # care n-o mai citește nimeni (scurgere de memorie lentă).
            self.close()
            raise
        if data is _STOP:
            self.close()
            raise StopAsyncIteration
        return data

    # -- Context manager (cleanup determinist) ----------------------------
    async def __aenter__(self) -> "Subscription":
        return self

    async def __aexit__(self, *_exc) -> bool:
        self.close()
        return False

    # -- Livrare ----------------------------------------------------------
    def _deliver(self, data: Any) -> bool:
        """Pune un eveniment în coadă. Când coada e plină aruncă cel mai VECHI
        element, nu pe cel nou: un status vechi de acum zece secunde valorează
        mai puțin decât cel curent, iar publisherul nu blochează niciodată."""
        if self._closed:
            return False
        try:
            self._queue.put_nowait(data)
            return True
        except asyncio.QueueFull:
            try:
                self._queue.get_nowait()          # scoatem cel mai vechi
            except asyncio.QueueEmpty:
                return False
            try:
                self._queue.put_nowait(data)
                return True
            except asyncio.QueueFull:
                return False

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        self._bus._unregister(self._event_type, self)

    def __del__(self):
        # Plasă de siguranță pentru abonamentele abandonate fără close().
        try:
            self.close()
        except Exception:
            pass

    @property
    def pending(self) -> int:
        return self._queue.qsize()


# =============================================================================
# CLASA EVENT BUS
# =============================================================================

class EventBus:
    """
    Event Bus asincron pe asyncio.Queue, cu fan-out per subscriber.

    Caracteristici:
        - Multiple abonamente pe același EventType (fiecare cu coada lui)
        - publish() non-blocant; la coadă plină cade cel mai vechi element
        - Înregistrare SINCRONĂ la subscribe() (fără evenimente pierdute)
        - Dezabonare automată la anulare/închidere

    Model de fire de execuție: se folosește DOAR din bucla asyncio. Nu există
    lock: publish/subscribe nu au puncte de await între ele, deci sunt atomice
    față de restul buclei. (Un asyncio.Lock aici n-ar proteja de nimic real și
    ar adăuga overhead pe fiecare publish.)
    """

    __slots__ = ("_subscribers", "_maxsize", "_log_events", "_publish_count")

    def __init__(self, maxsize: int = 100, log_events: bool = False):
        """
        Args:
            maxsize: Dimensiunea maximă a cozii fiecărui subscriber. La coadă
                     plină se aruncă cel mai vechi eveniment.
            log_events: Loghează fiecare publish/consume (debug).
        """
        self._subscribers: Dict[EventType, List[Subscription]] = {}
        self._maxsize = maxsize
        self._log_events = log_events
        self._publish_count: Dict[EventType, int] = {}

        logger.info("EventBus inițializat.")

    # -- Registru (apelat de Subscription) --------------------------------

    def _register(self, event_type: EventType, sub: Subscription) -> None:
        subs = self._subscribers.get(event_type)
        if subs is None:
            subs = self._subscribers[event_type] = []
        subs.append(sub)
        logger.debug(f"Nou subscriber [{event_type.name}]. Total: {len(subs)}")

    def _unregister(self, event_type: EventType, sub: Subscription) -> None:
        subs = self._subscribers.get(event_type)
        if not subs:
            return
        try:
            subs.remove(sub)
            logger.debug(f"Subscriber [{event_type.name}] dezabonat.")
        except ValueError:
            pass

    # -- API public -------------------------------------------------------

    def subscribe(self, event_type: EventType) -> Subscription:
        """
        Abonează un consumator. Coada e înregistrată IMEDIAT, la apel, nu la
        prima iterație, deci nu se pierd evenimente publicate între timp.

            async for data in bus.subscribe(EventType.WAKE_WORD_DETECTED):
                await handle(data)
        """
        return Subscription(self, event_type, self._maxsize)

    async def publish(self, event_type: EventType, data: Any = None) -> int:
        """
        Publică un eveniment către toți abonații. Non-blocant.

        Returns:
            Numărul de abonați care au primit efectiv evenimentul.
        """
        subs = self._subscribers.get(event_type)
        self._publish_count[event_type] = self._publish_count.get(event_type, 0) + 1

        if not subs:
            if self._log_events:
                logger.debug(f"[{event_type.name}] publicat, fără subscribers.")
            return 0

        delivered = 0
        # Copie defensivă: _deliver poate declanșa close() -> mutarea listei.
        for sub in tuple(subs):
            if sub._deliver(data):
                delivered += 1

        if self._log_events:
            logger.debug(f"[{event_type.name}] -> {delivered}/{len(subs)} subscribers.")
        return delivered

    async def publish_status(
        self,
        component: str,
        status: str,
        message: str,
        level: str = "INFO",
    ) -> None:
        """Helper pentru evenimentele SYSTEM_STATUS."""
        await self.publish(EventType.SYSTEM_STATUS, {
            "component": component,
            "status": status,
            "message": message,
            "level": level,
        })

    async def shutdown(self) -> None:
        """Trimite sentinela de oprire tuturor abonaților activi."""
        logger.info("EventBus: semnal de shutdown către toți subscriberii...")
        all_subs = [s for subs in self._subscribers.values() for s in subs]
        for sub in all_subs:
            sub._deliver(_STOP)
        logger.info(f"EventBus shutdown. {len(all_subs)} subscribers notificați.")

    def get_stats(self) -> Dict[str, Any]:
        """Statistici pentru debugging și dashboard."""
        return {
            "subscribers": {
                et.name: len(subs) for et, subs in self._subscribers.items()
            },
            "publish_counts": {
                et.name: count for et, count in self._publish_count.items()
            },
        }
