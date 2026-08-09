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
from typing import AsyncGenerator, Any, Dict, List

logger = logging.getLogger(__name__)


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
# CLASA EVENT BUS
# =============================================================================

class EventBus:
    """
    Event Bus asincron, thread-safe, bazat pe asyncio.Queue.

    Caracteristici:
        - Multiple subscriptions pe același EventType (fan-out)
        - Publish non-blocant (nu așteaptă consumatorii)
        - Fiecare subscriber are propria sa coadă izolată
        - Suport pentru unsubscribe explicit (cleanup)
        - Logging opțional per event type
    """

    def __init__(self, maxsize: int = 100, log_events: bool = False):
        """
        Args:
            maxsize: Dimensiunea maximă a fiecărei cozi de subscriber.
                     Dacă coada e plină și publisherul nu poate pune mesajul,
                     cel mai vechi mesaj este abandonat (nu blochează).
            log_events: Dacă True, loghează fiecare publish/consume (util pentru debug).
        """
        self._subscribers: Dict[EventType, List[asyncio.Queue]] = {}
        self._maxsize = maxsize
        self._log_events = log_events
        self._lock = asyncio.Lock()
        self._publish_count: Dict[EventType, int] = {}

        logger.info("🚌 EventBus inițializat.")

    async def subscribe(self, event_type: EventType) -> AsyncGenerator[Any, None]:
        """
        Abonează un consumator la un tip de eveniment.

        Returnează un AsyncGenerator care yield-uiește fiecare eveniment primit.
        Folosit cu `async for`:

            async for data in bus.subscribe(EventType.WAKE_WORD_DETECTED):
                await handle_wake_word(data)

        Args:
            event_type: Tipul de eveniment la care se abonează.

        Yields:
            data: Payload-ul evenimentului (dict sau orice alt tip).
        """
        queue: asyncio.Queue = asyncio.Queue(maxsize=self._maxsize)

        async with self._lock:
            if event_type not in self._subscribers:
                self._subscribers[event_type] = []
            self._subscribers[event_type].append(queue)
            logger.debug(
                f"🔔 Nou subscriber pentru [{event_type.name}]. "
                f"Total: {len(self._subscribers[event_type])}"
            )

        try:
            while True:
                data = await queue.get()

                # Sentinelul None => semnal de oprire pentru acest subscriber
                if data is None:
                    logger.debug(f"🛑 Subscriber [{event_type.name}] a primit semnal de oprire.")
                    break

                if self._log_events:
                    logger.debug(f"📨 [{event_type.name}] consumat: {str(data)[:100]}")

                yield data
                queue.task_done()

        finally:
            # Cleanup: elimină coada din lista de subscribers
            async with self._lock:
                try:
                    self._subscribers[event_type].remove(queue)
                    logger.debug(f"🔕 Subscriber [{event_type.name}] dezabonat.")
                except (ValueError, KeyError):
                    pass  # Deja eliminat

    async def publish(self, event_type: EventType, data: Any = None) -> int:
        """
        Publică un eveniment către toți subscriberii activi.

        Non-blocant: dacă coada unui subscriber e plină, abandonează mesajul
        pentru acel subscriber (nu blochează publisherul).

        Args:
            event_type: Tipul evenimentului de publicat.
            data: Payload-ul evenimentului (de obicei un dict).

        Returns:
            Numărul de subscribers care au primit efectiv evenimentul.
        """
        delivered = 0

        async with self._lock:
            queues = list(self._subscribers.get(event_type, []))

        if not queues:
            if self._log_events:
                logger.debug(f"📢 [{event_type.name}] publicat, dar nu există subscribers.")
            return 0

        for queue in queues:
            try:
                queue.put_nowait(data)
                delivered += 1
            except asyncio.QueueFull:
                logger.warning(
                    f"⚠️ [{event_type.name}] Coada unui subscriber e plină! "
                    f"Mesaj abandonat pentru acest subscriber."
                )

        # Statistici
        self._publish_count[event_type] = self._publish_count.get(event_type, 0) + 1

        if self._log_events:
            logger.debug(
                f"📢 [{event_type.name}] publicat → {delivered}/{len(queues)} subscribers."
            )

        return delivered

    async def publish_status(
        self,
        component: str,
        status: str,
        message: str,
        level: str = "INFO"
    ) -> None:
        """
        Metodă helper pentru publicarea rapidă de evenimente SYSTEM_STATUS.

        Args:
            component: Numele componentei care publică (ex: "AudioInterface").
            status: Starea curentă (ex: "READY", "ERROR", "LISTENING").
            message: Mesajul descriptiv.
            level: Nivelul de log ("INFO", "WARNING", "ERROR").
        """
        await self.publish(EventType.SYSTEM_STATUS, {
            "component": component,
            "status": status,
            "message": message,
            "level": level,
        })

    async def shutdown(self) -> None:
        """
        Trimite semnal de oprire (None) către toți subscriberii activi.
        Apelat la shutdown-ul sistemului pentru cleanup curat.
        """
        logger.info("🛑 EventBus: trimit semnal de shutdown la toți subscriberii...")
        async with self._lock:
            all_queues = [
                queue
                for queues in self._subscribers.values()
                for queue in queues
            ]

        for queue in all_queues:
            try:
                queue.put_nowait(None)  # Sentinelul de oprire
            except asyncio.QueueFull:
                pass  # Ignore — sistemul se oprește oricum

        logger.info(f"✅ EventBus shutdown complet. {len(all_queues)} subscribers notificați.")

    def get_stats(self) -> Dict[str, Any]:
        """
        Returnează statistici despre Event Bus (util pentru debugging și dashboard).

        Returns:
            Dict cu numărul de subscribers per EventType și numărul de publish-uri.
        """
        return {
            "subscribers": {
                et.name: len(queues)
                for et, queues in self._subscribers.items()
            },
            "publish_counts": {
                et.name: count
                for et, count in self._publish_count.items()
            }
        }
