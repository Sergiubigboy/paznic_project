"""Boot test: pornește Chronos în întregime, apoi îl oprește curat.

Verifică exact ce nu putea fi verificat pe bucăți:
  - toate componentele pornesc în ordinea corectă
  - bannerul SYSTEM_READY chiar apare (bug-ul de abonare)
  - shutdown-ul se termină fără task-uri agățate sau excepții
"""
import asyncio
import sys

sys.path.insert(0, ".")
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import main_async
from core.event_bus import EventType

SHUTDOWN_AFTER = 12.0


async def fake_terminal(bus, router, tts):
    """Ține locul buclei de terminal și cere oprirea după câteva secunde."""
    await asyncio.sleep(SHUTDOWN_AFTER)
    print(f"\n>>> [test] cer oprirea după {SHUTDOWN_AFTER}s\n")
    await bus.publish(EventType.SYSTEM_SHUTDOWN, {"reason": "boot_test"})


main_async.terminal_task = fake_terminal

asyncio.run(main_async.main())

# Ce a rămas în viață după shutdown?
import threading

alive = [t.name for t in threading.enumerate() if t is not threading.main_thread()]
print(f"\n>>> [test] thread-uri rămase: {alive or 'niciunul'}")
print(">>> [test] BOOT TEST TERMINAT")
