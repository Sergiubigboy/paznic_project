"""
test_voices.py — Ascultă toate vocile disponibile pentru Gemini Live
======================================================================
Script de test, de unică folosință — șterge-l după ce alegi vocea.

Rulează:
    python test_voices.py

Se conectează pe rând la fiecare voce, îi cere să rostească aceeași
propoziție de test, și o redă prin boxe. Apasă Enter între voci ca să
treci la următoarea (sau Ctrl+C ca să ieși oricând).
"""

import asyncio

from google import genai
from google.genai import types

from config import GEMINI_API_KEY, LIVE_MODEL, LIVE_SAMPLE_RATE_OUT

VOICES = [
    ("Charon", "masculin, profund, sofisticat"),
    ("Fenrir", "masculin, puternic, direct"),
    ("Orbit",  "masculin, calm, autoritar"),
    ("Puck",   "masculin, jucăuș, energic"),
    ("Aoede",  "feminin, cald, prietenos"),
    ("Kore",   "feminin, clar, profesional"),
    ("Zephyr", "feminin, luminos, vibrant"),
]

TEST_SENTENCE = (
    "Salut, sunt Chronos. Sistemul e activ, toate sunt sub control. "
    "Dacă vrei să pierzi vremea, treaba ta, dar zi-mi direct ce ai nevoie."
)


async def play_voice(client: genai.Client, sd, np, voice_name: str) -> None:
    config = types.LiveConnectConfig(
        response_modalities=["AUDIO"],
        speech_config=types.SpeechConfig(
            voice_config=types.VoiceConfig(
                prebuilt_voice_config=types.PrebuiltVoiceConfig(voice_name=voice_name)
            )
        ),
        system_instruction="Rostește EXACT textul primit, fără să adaugi nimic.",
    )

    audio_chunks = []
    async with client.aio.live.connect(model=LIVE_MODEL, config=config) as session:
        await session.send_client_content(
            turns=types.Content(role="user", parts=[types.Part(text=TEST_SENTENCE)]),
            turn_complete=True,
        )
        async for response in session.receive():
            sc = getattr(response, "server_content", None)
            if not sc:
                continue
            mt = getattr(sc, "model_turn", None)
            if mt:
                for part in (mt.parts or []):
                    inline = getattr(part, "inline_data", None)
                    if inline and getattr(inline, "data", None):
                        audio_chunks.append(inline.data)
            if getattr(sc, "turn_complete", False):
                break

    if not audio_chunks:
        print("   (niciun audio primit — sar peste)")
        return

    pcm = np.frombuffer(b"".join(audio_chunks), dtype=np.int16)
    sd.play(pcm, samplerate=LIVE_SAMPLE_RATE_OUT, blocking=True)


async def main() -> None:
    import sounddevice as sd
    import numpy as np

    client = genai.Client(api_key=GEMINI_API_KEY)

    print(f"\nText de test: \"{TEST_SENTENCE}\"\n")

    for name, desc in VOICES:
        print(f"🔊 {name} — {desc}")
        try:
            await play_voice(client, sd, np, name)
        except Exception as e:
            print(f"   ❌ Eroare la '{name}': {e}")
            continue

        try:
            input("   (Enter pentru următoarea voce, Ctrl+C pentru ieșire) ")
        except KeyboardInterrupt:
            print("\nGata.")
            return

    print("\nAsta a fost — toate vocile testate. Setează pe cea aleasă în personalization.py (LIVE_VOICE).")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\nOprit de utilizator.")
