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
    MAX_RECORD_SECONDS
)

from dispatcher import CommandDispatcher
from wled_specialist import WLEDStateManager
from music_specialist import MusicHandler

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

    # Inițializăm specialiștii o singură dată
    wled_mechanic = WLEDStateManager()
    music_expert = MusicHandler()
    dispatcher = CommandDispatcher(music_expert, wled_mechanic)

    porcupine = pvporcupine.create(access_key=PICOVOICE_KEY, keyword_paths=[KEYWORD_PATH])
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.bind(("0.0.0.0", UDP_PORT))
    sock.settimeout(0.05)

    logging.info("🚀 CHRONOS CORE pornit. Aștept Wake Word ('Guardian')...")

    audio_buffer = []
    recording_buffer = []
    is_recording = False
    silence_start = None
    record_start = None

    try:
        while True:
            try:
                data, _ = sock.recvfrom(2048)
            except socket.timeout:
                continue

            if data:
                chunk = struct.unpack_from("h" * (len(data) // 2), data)

                if not is_recording:
                    audio_buffer.extend(chunk)
                    
                    while len(audio_buffer) >= porcupine.frame_length:
                        frame = audio_buffer[:porcupine.frame_length]
                        audio_buffer = audio_buffer[porcupine.frame_length:]
                        
                        if porcupine.process(frame) >= 0:
                            logging.info("🎤 Wake Word Detectat! Ascult...")
                            
                            # ACȚIUNILE IMEDIATE LA WAKE WORD
                            wled_mechanic.save_state()            
                            wled_mechanic.start_loading_animation() 
                            music_expert.pause_playback() # OPREȘTE MUZICA

                            is_recording = True
                            recording_buffer = []
                            record_start = time.time()
                            silence_start = time.time()
                            break
                else:
                    recording_buffer.extend(chunk)
                    amplitude = sum(abs(x) for x in chunk) / len(chunk)
                    
                    # Verificăm dacă vorbește
                    if amplitude > SILENCE_THRESHOLD:
                        silence_start = time.time()
                    
                    duration = time.time() - record_start
                    silence_duration = time.time() - silence_start

                    # A terminat de vorbit
                    if duration > MIN_RECORD_SECONDS and (silence_duration > SILENCE_DURATION or duration > MAX_RECORD_SECONDS):
                        logging.info("Procesare comandă...")
                        is_recording = False
                        audio_buffer = [] 

                        with wave.open(TEMP_WAV, 'w') as wf:
                            wf.setnchannels(1)
                            wf.setsampwidth(2)
                            wf.setframerate(SAMPLE_RATE)
                            wf.writeframes(struct.pack("h" * len(recording_buffer), *recording_buffer))

                        # 1. Transcriere
                        text = transcribe_audio(TEMP_WAV)
                        
                        # 2. Trimitem la creier pentru decizie
                        if text:
                            should_restore = dispatcher.process_text_command(text, sock)
                            if should_restore:
                                logging.info("Revin la luminile anterioare...")
                                wled_mechanic.restore_state()
                        else:
                            # Dacă n-a înțeles nimic, revine la normal
                            wled_mechanic.restore_state()
                        
                        # 3. Reluăm muzica DOAR dacă nu a dat o comandă de control muzical
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
        sock.close()

if __name__ == "__main__":
    main()