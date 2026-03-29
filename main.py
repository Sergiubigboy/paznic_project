import os
import sys
import time
import wave
import struct
import socket
import logging
import speech_recognition as sr
import pvporcupine

from config import (
    PICOVOICE_KEY, 
    UDP_PORT, 
    KEYWORD_PATH, 
    SAMPLE_RATE, 
    SILENCE_THRESHOLD, 
    SILENCE_DURATION, 
    MIN_RECORD_SECONDS, 
    MAX_RECORD_SECONDS,
    USE_LOCAL_MIC
)

from dispatcher import CommandDispatcher
from wled_specialist import WLEDStateManager
from music_specialist import MusicHandler
import threading

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
TEMP_WAV = "temp_command.wav"

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

def main():
    if not os.path.exists(KEYWORD_PATH):
        logging.critical(f"Lipsește fișierul keyword Picovoice: {KEYWORD_PATH}")
        sys.exit(1)

    wled_mechanic = WLEDStateManager()
    music_expert = MusicHandler()
    dispatcher = CommandDispatcher(music_expert, wled_mechanic)

    porcupine = pvporcupine.create(access_key=PICOVOICE_KEY, keyword_paths=[KEYWORD_PATH])
    
    sock = None
    audio_stream = None
    if USE_LOCAL_MIC:
        import pyaudio
        pa = pyaudio.PyAudio()
        audio_stream = pa.open(rate=porcupine.sample_rate, channels=1, format=pyaudio.paInt16, input=True, frames_per_buffer=porcupine.frame_length)
        logging.info("🎤 Sursa Audio: MICROFON LOCAL LAPTOP")
    else:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.bind(("0.0.0.0", UDP_PORT))
        sock.settimeout(0.05)
        logging.info("🎤 Sursa Audio: RETEA UDP (ESP32)")

    logging.info("🚀 CHRONOS CORE pornit.")
    
    # === THREAD PENTRU TERMINAL ===
    def terminal_listener():
        while True:
            try:
                cmd = input("\n[Terminal] Scrie o comanda: ")
                if cmd.strip():
                    dispatcher.process_text_command(cmd, sock)
            except Exception: pass

    threading.Thread(target=terminal_listener, daemon=True).start()

    # === AUTO-START WEB DASHBOARD ===
    def start_web_server():
        import sys as _sys
        web_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "web")
        _sys.path.insert(0, web_dir)
        from web.web_dashboard import app
        logging.info("🌐 Pornesc Dashboard-ul Chronos pe portul 5000...")
        app.run(host='0.0.0.0', port=5000, debug=False, use_reloader=False)
    threading.Thread(target=start_web_server, daemon=True).start()

    # === VERIFICĂ ZILELE LIPSĂ IMEDIAT LA PORNIRE ===
    dispatcher.jural_expert.check_and_generate_missing_summaries()

    logging.info("Aștept Wake Word ('Guardian')...")

    audio_buffer = []
    recording_buffer = []
    is_recording = False
    silence_start = None
    record_start = None
    
    last_summary_check = time.time()

    try:
        while True:
            # === VERIFICARE PERIODICĂ (O DATĂ PE ORĂ) ===
            if time.time() - last_summary_check > 3600:
                dispatcher.jural_expert.check_and_generate_missing_summaries()
                last_summary_check = time.time()

            if USE_LOCAL_MIC:
                try:
                    pcm = audio_stream.read(porcupine.frame_length, exception_on_overflow=False)
                    chunk = struct.unpack_from("h" * porcupine.frame_length, pcm)
                    data = True
                except Exception:
                    continue
            else:
                try:
                    data, _ = sock.recvfrom(2048)
                    if data:
                        chunk = struct.unpack_from("h" * (len(data) // 2), data)
                except socket.timeout:
                    continue

            if data:
                if not is_recording:
                    audio_buffer.extend(chunk)
                    
                    while len(audio_buffer) >= porcupine.frame_length:
                        frame = audio_buffer[:porcupine.frame_length]
                        audio_buffer = audio_buffer[porcupine.frame_length:]
                        
                        if porcupine.process(frame) >= 0:
                            logging.info("🎤 Wake Word Detectat! Ascult...")
                            wled_mechanic.save_state(slot="wake")            
                            wled_mechanic.start_loading_animation() 
                            music_expert.pause_playback() 

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

                        with wave.open(TEMP_WAV, 'w') as wf:
                            wf.setnchannels(1)
                            wf.setsampwidth(2)
                            wf.setframerate(SAMPLE_RATE)
                            wf.writeframes(struct.pack("h" * len(recording_buffer), *recording_buffer))

                        text = transcribe_audio(TEMP_WAV)
                        
                        if text:
                            should_restore = dispatcher.process_text_command(text, sock)
                            if should_restore:
                                logging.info("Revin la luminile anterioare...")
                                wled_mechanic.restore_state(slot="wake")
                        else:
                            wled_mechanic.restore_state(slot="wake")
                        
                        cuvinte_muzica = ["pune", "bagă", "schimbă", "stop", "oprește", "oprit", "muzic", "pauză", "pauza", "next", "următoarea", "sari", "lasă"]
                        if text and not any(kw in text.lower() for kw in cuvinte_muzica):
                            music_expert.resume_playback()
                        elif not text:
                             music_expert.resume_playback()

                        if os.path.exists(TEMP_WAV):
                            os.remove(TEMP_WAV)

    except KeyboardInterrupt:
        logging.info("Oprire sistem...")
    finally:
        porcupine.delete()
        if sock: sock.close()
        if audio_stream: audio_stream.close()

if __name__ == "__main__":
    main()