"""Smoke tests offline pentru refactorizarea Chronos.

Rulează fără microfon, fără boxe și fără apeluri LLM (în afara secțiunii
marcate explicit). Verifică regresiile pe care le-am atins.
"""
import asyncio
import sys
import types

sys.path.insert(0, ".")
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

PASS, FAIL = [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(f"  {'PASS' if cond else 'FAIL'}  {name}{(' — ' + detail) if detail else ''}")


# ── 1. EVENT BUS ─────────────────────────────────────────────────────────
print("\n[1] EventBus")
from core.event_bus import EventBus, EventType


async def bus_tests():
    bus = EventBus(maxsize=3)

    sub = bus.subscribe(EventType.SYSTEM_READY)
    n = await bus.publish(EventType.SYSTEM_READY, {"component": "X"})
    check("abonare sincronă (evenimentul nu se mai pierde la pornire)", n == 1)

    s2 = bus.subscribe(EventType.SYSTEM_STATUS)
    for i in range(6):
        await bus.publish(EventType.SYSTEM_STATUS, i)
    vals = [await s2.__anext__() for _ in range(3)]
    check("coadă plină → cade cel mai VECHI", vals == [3, 4, 5], str(vals))

    s3 = bus.subscribe(EventType.TOOL_RESULT)
    await bus.publish(EventType.TOOL_RESULT, None)
    got = await s3.__anext__()
    check("None e payload valid, nu semnal de oprire", got is None)

    s4 = bus.subscribe(EventType.WAKE_WORD_DETECTED)
    t = asyncio.create_task(s4.__anext__())
    await asyncio.sleep(0)
    t.cancel()
    try:
        await t
    except asyncio.CancelledError:
        pass
    left = bus.get_stats()["subscribers"].get("WAKE_WORD_DETECTED")
    check("anularea consumatorului dezabonează (fără scurgere)", left == 0, f"rămase={left}")

    s5 = bus.subscribe(EventType.EXECUTE_TOOL)
    await bus.shutdown()
    cnt = 0
    async for _ in s5:
        cnt += 1
    check("shutdown oprește iterarea", cnt == 0)


asyncio.run(bus_tests())

# ── 2. CONFIG VIU ────────────────────────────────────────────────────────
print("\n[2] Config care chiar se aplică")
import core.audio_interface as A
import core.llm_router as R
from config import (WAKE_WORD_THRESHOLD_JARVIS, WAKE_WORD_THRESHOLD_OTHER,
                    DISPATCHER_TIMEOUT, TTS_RATE)

check("praguri wake word din personalization.py",
      (A.MIN_SCORE_FOR_JARVIS, A.MIN_SCORE_FOR_NON_JARVIS)
      == (WAKE_WORD_THRESHOLD_JARVIS, WAKE_WORD_THRESHOLD_OTHER))
check("DISPATCHER_TIMEOUT din config, nu hardcodat",
      R.DISPATCHER_TIMEOUT == DISPATCHER_TIMEOUT, f"={R.DISPATCHER_TIMEOUT}")
import core.tts_engine as T
check("TTS_RATE din config e chiar folosit", T.TTSEngine()._rate == TTS_RATE)

# ── 3. PROMPT SPLIT ──────────────────────────────────────────────────────
print("\n[3] Împărțirea system prompt-ului")
import subprocess
from personalization import (SYSTEM_PROMPT, SYSTEM_PROMPT_VOICE,
                             SYSTEM_PROMPT_TEXT, VOICE_TOOL_RULES)

old_src = subprocess.run(["git", "show", "HEAD:personalization.py"],
                         capture_output=True, text=True, encoding="utf-8").stdout
ns = {}
exec(compile(old_src, "old", "exec"), ns)
check("promptul VOCAL neschimbat față de înainte",
      " ".join(ns["SYSTEM_PROMPT"].split()) == " ".join(SYSTEM_PROMPT_VOICE.split()))
check("SYSTEM_PROMPT rămâne alias pentru cel complet", SYSTEM_PROMPT is SYSTEM_PROMPT_VOICE)
check("promptul TEXT nu mai conține reguli de tool-uri",
      "read_my_data" not in SYSTEM_PROMPT_TEXT and "read_my_data" in VOICE_TOOL_RULES)

# ── 4. ÎMPĂRȚIREA ÎN CLAUZE ──────────────────────────────────────────────
print("\n[4] Împărțitorul de clauze (TTS streaming)")
from core.tts_engine import split_clauses

out, rest = split_clauses("Bugetul e 1240.50 lei acum.", flush=True)
check("nu taie în interiorul unui număr zecimal", out == ["Bugetul e 1240.50 lei acum."], str(out))

buf, emitted = "", []
for piece in ["Sergiu, ", "am verificat. ", "Ai trei remindere ", "active acum. "]:
    buf += piece
    cl, buf = split_clauses(buf)
    emitted += cl
check("prima clauză iese înainte de finalul textului",
      emitted and emitted[0] == "Sergiu, am verificat.", str(emitted))

out, rest = split_clauses("a" * 300, flush=True)
check("text fără punctuație e totuși tăiat (plasă de siguranță)", len(out) >= 2, f"{len(out)} bucăți")

out, _ = split_clauses("Salut. Ce faci?", flush=True)
check("fragmentele foarte scurte nu sunt rupte", out == ["Salut. Ce faci?"], str(out))

# ── 5. TTS: EPOCĂ DE ÎNTRERUPERE ─────────────────────────────────────────
print("\n[5] TTS — întrerupere pe epocă")


class FakeStream:
    def __init__(self): self.written = 0
    def start(self): pass
    def write(self, b): self.written += len(b)
    def stop(self): pass
    def close(self): pass


fake_sd = types.SimpleNamespace(OutputStream=lambda **k: FakeStream())


async def tts_tests():
    import numpy as np
    eng = T.TTSEngine()
    eng._np, eng._sd = np, fake_sd
    eng._decoder = T.TTSEngine._pick_decoder()
    eng._worker = T._PlaybackWorker(fake_sd)
    eng._available = True
    check("decodor MP3 disponibil fără ffmpeg", eng._decoder == "miniaudio", str(eng._decoder))

    before = T._ENGINE_EPOCH[0]
    eng.interrupt()
    check("interrupt() avansează epoca", T._ENGINE_EPOCH[0] == before + 1)

    async def src():
        yield "Prima propozitie completa care intra in conducta. "
        await asyncio.sleep(0.4)
        yield "A doua propozitie care nu ar trebui rostita. "

    task = asyncio.create_task(eng.speak_stream(src()))
    await asyncio.sleep(0.25)
    eng.interrupt()
    done = await task
    check("întreruperea raportează 'neterminat'", done is False)
    await eng.shutdown()

asyncio.run(tts_tests())

# ── 6. AGENT: FĂRĂ STARE PARTAJATĂ ───────────────────────────────────────
print("\n[6] ChronosAgent — rezultat întors, istoric mărginit")
from agents.chronos_agent import ChronosAgent, HISTORY_MAXLEN
import inspect

src = inspect.getsource(ChronosAgent.run_agents)
check("run_agents întoarce dict-ul de rezultat", "return result" in src)
# `from __future__ import annotations` face adnotările șiruri, deci comparăm ca text.
check("process_text_command întoarce rezultatul (nu True)",
      str(ChronosAgent.process_text_command.__annotations__.get("return")) == "dict")

agent = ChronosAgent.__new__(ChronosAgent)
from collections import deque
agent.conversation_history = deque(maxlen=HISTORY_MAXLEN)
for i in range(200):
    agent._remember(f"linia {i}")
check("istoricul e mărginit (fără creștere nelimitată)",
      len(agent.conversation_history) == HISTORY_MAXLEN,
      f"len={len(agent.conversation_history)}")
check("ChronosAgent are __slots__", hasattr(ChronosAgent, "__slots__"))

# ── 7. ROUTER: PUNTEA SINCRON→ASINCRON ───────────────────────────────────
print("\n[7] LLMRouter — punte sincron→asincron")
from core.llm_router import _aiter_sync, _amap, _WAKE_BEEP


async def bridge_tests():
    def gen():
        for i in range(5):
            yield f"chunk{i} "
    got = [c async for c in _aiter_sync(gen)]
    check("generatorul sincron ajunge întreg în asyncio", len(got) == 5, "".join(got).strip())

    seen = []
    async def src():
        for c in ["a", "b", "c"]:
            yield c
    out = [x async for x in _amap(src(), lambda p: (seen.append(p), p)[1])]
    check("_amap vede fiecare bucată în trecere", seen == ["a", "b", "c"] and out == seen)

    def boom():
        yield "ok "
        raise RuntimeError("sursa a picat")
    got = [c async for c in _aiter_sync(boom)]
    check("excepția din sursă nu propagă în buclă", got == ["ok "], str(got))

asyncio.run(bridge_tests())
check("beep-ul de wake word e precomputat la import", _WAKE_BEEP is not None)

# ── 8. AI CORE ───────────────────────────────────────────────────────────
print("\n[8] ai_core — transport")
import ai_core
s1, s2 = ai_core.get_session(), ai_core.get_session()
check("sesiunea HTTP e refolosită (keep-alive)", s1 is s2)
check("cheia API merge în header, nu în URL",
      "x-goog-api-key" in s1.headers and "key=" not in ai_core._url("m"))
cfg = ai_core._gen_config(0.5, 100, thinking=False)
check("thinkingBudget=0 pe apelurile structurate",
      cfg["thinkingConfig"]["thinkingBudget"] == 0)
check("maxOutputTokens e plafonat", cfg["maxOutputTokens"] == 100)
check("stream_gemini_text există", callable(ai_core.stream_gemini_text))

# ── REZUMAT ──────────────────────────────────────────────────────────────
print(f"\n{'='*60}")
print(f"  {len(PASS)} PASS   {len(FAIL)} FAIL")
if FAIL:
    for f in FAIL:
        print(f"   ✗ {f}")
print("=" * 60)
sys.exit(1 if FAIL else 0)
