import os
import sys
import time
import json
import wave
import struct
import socket
import logging
import requests
import speech_recognition as sr
import pvporcupine

# --- IMPORTURI MODULE SPECIALISTE ---
from wled_specialist import WLEDDispatcher, WLEDStateManager
from music_specialist import MusicHandler # <--- NOUL SPECIALIST MUZICAL
from logger_specialist import JournalCore

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

# --- DISPATCHER INTELIGENT (ROUTER) ---
def classify_intent_with_gemini(transcription):
    logging.info(f"🧠 Dispatcher: Analizez intenția pentru: '{transcription}'")
    
    url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.0-flash:generateContent?key={GEMINI_API_KEY}"
    headers = {'Content-Type': 'application/json'}

    prompt_text = f"""
    You are the Intelligent Room Assistant Dispatcher.
    Route the user's voice command.
    
    USER COMMAND: "{transcription}"

    OUTPUT FORMAT: JSON {{ "led": bool, "music": bool, "general": bool, "journal": bool, "target": bool, "reasoning": "string" }}
    Logic:
    - "led": lights, colors, brightness, "atmosphere", "vibe" related to sight.
    - "music": songs, radio, artists, volume, "baga", "pune", "play", "stop", "next".
    - "general": weather, chat, jokes, math.
    - "journal": User talks about feelings, day summary, rant, ideas ("vreau să mă descarc", "jurnal", "azi a fost greu").
    - "target": User wants to add a specific task ("adaugă target", "trebuie să fac X", "amintește-mi să").
    
    """

    payload = {
        "contents": [{"parts": [{"text": prompt_text}]}],
        "generationConfig": {"responseMimeType": "application/json"}
    }

    try:
        response = requests.post(url, headers=headers, json=payload)
        response.raise_for_status()
        raw_text = response.json()['candidates'][0]['content']['parts'][0]['text']
        parsed = json.loads(raw_text)
        
        # Handle case where Gemini returns a list instead of dict
        if isinstance(parsed, list) and len(parsed) > 0:
            parsed = parsed[0]
        
        # Ensure it's a dict
        if not isinstance(parsed, dict):
            logging.error(f"Gemini returned unexpected format: {type(parsed)}")
            return None
        
        return parsed
    except Exception as e:
        logging.error(f"Eroare Dispatcher Gemini: {e}")
        return None

# --- AUDIO & TRANSCRIPTION ---
def transcribe_audio(wav_filename):
    recognizer = sr.Recognizer()
    try:
        with sr.AudioFile(wav_filename) as source:
            audio_data = recognizer.record(source)
            # Folosim limba română pentru transcriere, Gemini va traduce intern în engleză pentru Spotify
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

    logging.info("🚀 Sistem INTEGRAT (WLED + SPOTIFY) pornit. Aștept Wake Word...")

    # --- INIȚIALIZARE EXPERȚI ---
    wled_expert = WLEDDispatcher()
    wled_mechanic = WLEDStateManager()
    music_expert = MusicHandler() # <--- AICI SE CONECTEAZĂ SPOTIFY
    jural_expert = JournalCore(GEMINI_API_KEY, wled_mechanic)
    
    
    audio_buffer = []
    recording_buffer = []
    is_recording = False
    silence_start = None
    record_start = None

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
                    if len(audio_buffer) > porcupine.frame_length:
                        frame = audio_buffer[:porcupine.frame_length]
                        audio_buffer = audio_buffer[porcupine.frame_length:]
                        
                        if porcupine.process(frame) >= 0:
                            logging.info("🎤 Wake Word Detectat! Ascult...")
                            
                            # --- ANIMATIE LOADING (Feedback Vizual) ---
                            wled_mechanic.save_state()            
                            wled_mechanic.start_loading_animation() 
                            # ----------------------------------

                            is_recording = True
                            recording_buffer = []
                            record_start = time.time()
                            silence_start = time.time()
                else:
                    # Logică Înregistrare
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
                            intent = classify_intent_with_gemini(text)
                            
                            if intent and isinstance(intent, dict):
                                logging.info(f"📋 Intenție: {intent.get('reasoning', 'unknown')}")
                                
                                # A. CAZUL LED-URI (Dacă vrei lumini, nu le restabilim pe cele vechi)
                                if intent.get("led"):
                                    wled_expert.execute(text)
                                    should_restore_lights = False 
                                
                                
                                
                                if intent.get("journal"):
                                    logging.info("📔 Jurnal Personal ")
                                    jural_expert.start_journal_session(sock)

                                if intent.get("target"):
                                    logging.info("🎯 Target Manager")
                                    jural_expert.start_target_session(sock)

                                if intent.get("music"):
                                    logging.info("🎵 Procesare Muzică...")
                                    # Acum totul e gestionat în music_specialist.py
                                    music_expert.process_command(text)
                           
                                if intent.get("general"):
                                    logging.info("💬 General Chat (Coming Soon)")
                            else:
                                logging.error("Failed to parse intent or invalid intent format")
                        
                        # --- RESTAURARE STARE LUMINI ---
                        if should_restore_lights:
                            logging.info("Revin la luminile anterioare...")
                            wled_mechanic.restore_state()
                        # --------------------------------

                        if os.path.exists(temp_wav):
                            os.remove(temp_wav)

    except KeyboardInterrupt:
        logging.info("Oprire sistem...")
    finally:
        porcupine.delete()
        sock.close()

if __name__ == "__main__":
    main()