import os
import sys
import time
import wave
import struct
import socket
import logging
import speech_recognition as sr
import pvporcupine

# --- IMPORTURI MODULE SPECIALISTE ---
from wled_specialist import WLEDDispatcher, WLEDStateManager
from music_specialist import MusicHandler
from logger_specialist import JournalCore
from ai_core import ask_gemini_json

from config import (
    PICOVOICE_KEY, 
    GEMINI_API_KEY, 
    UDP_PORT, 
    KEYWORD_PATH, 
    SAMPLE_RATE, 
    SILENCE_THRESHOLD, 
    SILENCE_DURATION, 
    MIN_RECORD_SECONDS, 
    MAX_RECORD_SECONDS
)

# --- LOGGING ---
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

def classify_intent_with_gemini(transcription, conversation_history):
    logging.info(f"🧠 Dispatcher: Analizez intenția...")
    
    prompt_text = f"""
    You are the Intelligent Room Assistant Dispatcher.
    Route the user's voice command.
    
    RECENT CONVERSATION HISTORY (Last Hour):
    {conversation_history}
    
    CURRENT USER COMMAND: "{transcription}"

    Logic for classification:
    - "led": lights, colors, brightness, "atmosphere", "vibe" related to sight.
    - "music": songs, radio, artists, volume, "baga", "pune", "play", "stop", "next" (even if user just says "schimba" or "next", look at history).
    - "general": weather, chat, jokes, math.
    - "journal": User talks about feelings, day summary, rant, ideas ("vreau să mă descarc", "jurnal", "azi a fost greu").
    - "target": User wants to add a specific task ("adaugă target", "trebuie să fac X", "amintește-mi să").
    """

    intent_schema = {
        "type": "OBJECT",
        "properties": {
            "led": {"type": "BOOLEAN"},
            "music": {"type": "BOOLEAN"},
            "general": {"type": "BOOLEAN"},
            "journal": {"type": "BOOLEAN"},
            "target": {"type": "BOOLEAN"},
            "reasoning": {"type": "STRING", "description": "Short explanation of the decision"}
        },
        "required": ["led", "music", "general", "journal", "target", "reasoning"]
    }

    parsed = ask_gemini_json(prompt_text, schema=intent_schema, temperature=0.1)
    
    if not parsed:
        logging.error(f"Gemini a returnat None.")
        return None
    
    return parsed

# --- AUDIO & TRANSCRIPTION ---
def transcribe_audio(wav_filename):
    recognizer = sr.Recognizer()
    try:
        with sr.AudioFile(wav_filename) as source:
            audio_data = recognizer.record(source)
            text = recognizer.recognize_google(audio_data, language="ro-RO")
            logging.info(f"🗣️  Utilizator: {text}")
            return text
    except sr.UnknownValueError:
        logging.warning("Nu am înțeles ce ai spus.")
        return None
    except Exception as e:
        logging.error(f"Eroare transcriere: {e}")
        return None

# --- MAIN LOOP ---
def main():
    if not os.path.exists(KEYWORD_PATH):
        logging.critical(f"Lipsește fișierul keyword Picovoice: {KEYWORD_PATH}")
        sys.exit(1)

    porcupine = pvporcupine.create(access_key=PICOVOICE_KEY, keyword_paths=[KEYWORD_PATH])
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.bind(("0.0.0.0", UDP_PORT))
    sock.settimeout(0.05)

    logging.info("🚀 Sistem INTEGRAT pornit. Aștept Wake Word...")

    wled_expert = WLEDDispatcher()
    wled_mechanic = WLEDStateManager()
    music_expert = MusicHandler()
    jural_expert = JournalCore(wled_mechanic) 
    
    audio_buffer = []
    recording_buffer = []
    is_recording = False
    silence_start = None
    record_start = None

    conversation_history = [] 

    try:
        while True:
            try:
                data, addr = sock.recvfrom(2048)
            except socket.timeout:
                continue

            if data:
                chunk = struct.unpack_from("h" * (len(data) // 2), data)

                if not is_recording:
                    audio_buffer.extend(chunk)
                    
                    # Fixul magic care nu lasă microfonul să aibă lag după câteva ore
                    while len(audio_buffer) >= porcupine.frame_length:
                        frame = audio_buffer[:porcupine.frame_length]
                        audio_buffer = audio_buffer[porcupine.frame_length:]
                        
                        if porcupine.process(frame) >= 0:
                            logging.info("🎤 Wake Word Detectat! Ascult...")
                            wled_mechanic.save_state()            
                            wled_mechanic.start_loading_animation() 

                            is_recording = True
                            recording_buffer = []
                            record_start = time.time()
                            silence_start = time.time()
                            break
                else:
                    recording_buffer.extend(chunk)
                    amplitude = sum(abs(x) for x in chunk) / len(chunk)
                    if amplitude > SILENCE_THRESHOLD:
                        silence_start = time.time()
                    
                    duration = time.time() - record_start
                    silence_duration = time.time() - silence_start

                    if duration > MIN_RECORD_SECONDS and (silence_duration > SILENCE_DURATION or duration > MAX_RECORD_SECONDS):
                        logging.info("Procesare comandă...")
                        is_recording = False
                        audio_buffer = [] 

                        temp_wav = "temp_command.wav"
                        with wave.open(temp_wav, 'w') as wf:
                            wf.setnchannels(1)
                            wf.setsampwidth(2)
                            wf.setframerate(SAMPLE_RATE)
                            wf.writeframes(struct.pack("h" * len(recording_buffer), *recording_buffer))

                        text = transcribe_audio(temp_wav)
                        should_restore_lights = True 

                        if text:
                            current_time = time.time()
                            
                            conversation_history = [msg for msg in conversation_history if current_time - msg[0] <= 3600]
                            history_str = "\n".join([msg[1] for msg in conversation_history]) if conversation_history else "No previous context."
                            
                            intent = classify_intent_with_gemini(text, history_str)
                            
                            conversation_history.append((current_time, f"User: {text}"))
                            
                            if intent and isinstance(intent, dict):
                                logging.info(f"📋 Intenție: {intent.get('reasoning', 'unknown')}")
                                
                                if intent.get("led"):
                                    wled_expert.execute(text, history_str)
                                    should_restore_lights = False 
                                
                                if intent.get("journal"):
                                    logging.info("📔 Jurnal Personal ")
                                    jural_expert.start_journal_session(sock)

                                if intent.get("target"):
                                    logging.info("🎯 Target Manager")
                                    jural_expert.start_target_session(sock)

                                if intent.get("music"):
                                    logging.info("🎵 Procesare Muzică...")
                                    music_expert.process_command(text, history_str)
                           
                                if intent.get("general"):
                                    logging.info("💬 General Chat (Coming Soon)")
                            else:
                                logging.error("Failed to parse intent")
                        
                        if should_restore_lights:
                            logging.info("Revin la luminile anterioare...")
                            wled_mechanic.restore_state()

                        if os.path.exists(temp_wav):
                            os.remove(temp_wav)

    except KeyboardInterrupt:
        logging.info("Oprire sistem...")
    finally:
        porcupine.delete()
        sock.close()

if __name__ == "__main__":
    main()