import os
import sys
import time
import logging
import threading

from config import (
    PICOVOICE_KEY,
    UDP_PORT,
    KEYWORD_PATH,
    SAMPLE_RATE,
    SILENCE_THRESHOLD,
    SILENCE_DURATION,
    MIN_RECORD_SECONDS,
    MAX_RECORD_SECONDS,
    USE_LOCAL_MIC,
    USE_PICOVOICE,
    RASPBERRY_PI,
)

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
TEMP_WAV = "temp_command.wav"

# =====================================================
# WEB SERVER — PORNIT PRIMUL, MEREU
# =====================================================
_web_ready = threading.Event()

def start_web_server():
    try:
        web_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "web")
        sys.path.insert(0, web_dir)
        from web.web_dashboard import app
        logging.info("🌐 Pornesc Dashboard-ul Chronos pe portul 5000...")
        _web_ready.set()
        app.run(host='0.0.0.0', port=5000, debug=False, use_reloader=False)
    except Exception as e:
        logging.error(f"❌ Eroare la pornirea web server-ului: {e}")
        _web_ready.set()  # Set even on error so main thread doesn't hang

web_thread = threading.Thread(target=start_web_server, daemon=True, name="WebServer")
web_thread.start()

# Așteaptă maxim 5 secunde ca web-ul să pornească
_web_ready.wait(timeout=5)
logging.info("✅ Web server inițializat.")

# =====================================================
# IMPORTURI OPTIONALE — nur dacă USE_PICOVOICE=True
# =====================================================
porcupine_lib = None
pyaudio_lib = None

if USE_PICOVOICE:
    try:
        import pvporcupine
        porcupine_lib = pvporcupine
        logging.info("✅ Picovoice importat cu succes.")
    except ImportError as e:
        logging.error(f"❌ [PICOVOICE] Nu pot importa pvporcupine: {e}. Dezactivez wake word.")
        USE_PICOVOICE = False

    if USE_LOCAL_MIC and USE_PICOVOICE:
        try:
            import pyaudio
            pyaudio_lib = pyaudio
            logging.info("✅ PyAudio importat cu succes.")
        except ImportError as e:
            logging.error(f"❌ [PyAudio] Nu pot importa pyaudio: {e}. Dezactivez microfon local.")


def transcribe_audio(wav_filename):
    try:
        import speech_recognition as sr
        recognizer = sr.Recognizer()
        with sr.AudioFile(wav_filename) as source:
            audio_data = recognizer.record(source)
            text = recognizer.recognize_google(audio_data, language="ro-RO")
            logging.info(f"🗣️  Utilizator: {text}")
            return text
    except Exception as e:
        logging.warning(f"Nu am înțeles sau eroare transcriere: {e}")
        return None


def main():
    # --- Importăm modulele de bază (nu depind de audio) ---
    from wled_specialist import WLEDStateManager
    from music_specialist import MusicHandler
    from dispatcher import CommandDispatcher

    wled_mechanic = WLEDStateManager()
    music_expert = MusicHandler()
    dispatcher = CommandDispatcher(music_expert, wled_mechanic)

    import web.web_dashboard as web_dashboard
    web_dashboard.shared_dispatcher = dispatcher
    logging.info("✅ Core Dispatcher injectat în Web Dashboard!")

    # === VERIFICĂ ZILELE LIPSĂ (dar auto-generarea e dezactivată în logger) ===
    try:
        dispatcher.jural_expert.check_and_generate_missing_summaries()
    except Exception as e:
        logging.warning(f"⚠️ check_and_generate_missing_summaries a eșuat: {e}")

    # === THREAD TERMINAL LOCAL ===
    def terminal_listener():
        import sys
        import time
        
        # Verificăm dacă scriptul rulează într-un terminal interactiv
        if not sys.stdin.isatty():
            logging.info("Rulează în fundal. Opresc ascultarea de la tastatură.")
            return # Iese din funcție automat
            
        while True:
            try:
                cmd = input("\n[Terminal] Scrie o comanda: ")
                if cmd.strip():
                    dispatcher.process_text_command(cmd, None)
            except Exception:
                time.sleep(1) # Pauză vitală în caz de eroare pentru a nu bloca CPU-ul!

    threading.Thread(target=terminal_listener, daemon=True).start()

    threading.Thread(target=terminal_listener, daemon=True).start()

    # =========================================================
    # DACĂ PICOVOICE E DEZACTIVAT — Rulăm în mod "Dashboard only"
    # =========================================================
    if not USE_PICOVOICE:
        logging.info("ℹ️ [SISTEM] Picovoice DEZACTIVAT. Rulează în mod Dashboard-only.")
        logging.info("ℹ️ [SISTEM] Dashboard accesibil la http://0.0.0.0:5000")
        logging.info("ℹ️ [SISTEM] Apasă Ctrl+C pentru a opri.")
        try:
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            logging.info("Oprire sistem...")
        return

    # =========================================================
    # MOD COMPLET — cu Picovoice + Audio
    # =========================================================
    import wave
    import struct
    import socket

    porcupine = None
    try:
        porcupine = porcupine_lib.create(access_key=PICOVOICE_KEY, keyword_paths=[KEYWORD_PATH])
    except Exception as e:
        logging.error(f"❌ [PICOVOICE] Eroare inițializare: {e}")
        logging.info("ℹ️ Continuăm fără wake word (dashboard rulează).")
        try:
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            pass
        return

    sock = None
    audio_stream = None
    pa_instance = None

    if USE_LOCAL_MIC and pyaudio_lib:
        try:
            pa_instance = pyaudio_lib.PyAudio()
            audio_stream = pa_instance.open(
                rate=porcupine.sample_rate,
                channels=1,
                format=pyaudio_lib.paInt16,
                input=True,
                frames_per_buffer=porcupine.frame_length
            )
            logging.info("🎤 Sursa Audio: MICROFON LOCAL LAPTOP")
        except Exception as e:
            logging.error(f"❌ [PyAudio] Nu pot deschide stream: {e}. Trec pe UDP.")
            USE_LOCAL_MIC = False

    if not USE_LOCAL_MIC:
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            sock.bind(("0.0.0.0", UDP_PORT))
            sock.settimeout(0.05)
            logging.info("🎤 Sursa Audio: RETEA UDP (ESP32)")
        except Exception as e:
            logging.error(f"❌ [UDP] Nu pot bind socket: {e}")
            porcupine.delete()
            return

    logging.info("🚀 CHRONOS CORE pornit. Aștept Wake Word ('Guardian')...")

    audio_buffer = []
    recording_buffer = []
    is_recording = False
    silence_start = None
    record_start = None
    last_summary_check = time.time()

    try:
        while True:
            if time.time() - last_summary_check > 3600:
                try:
                    dispatcher.jural_expert.check_and_generate_missing_summaries()
                except Exception:
                    pass
                last_summary_check = time.time()

            data = None
            chunk = None

            if USE_LOCAL_MIC and audio_stream:
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

            if data and chunk:
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
        if porcupine:
            porcupine.delete()
        if sock:
            sock.close()
        if audio_stream:
            audio_stream.close()
        if pa_instance:
            pa_instance.terminate()


if __name__ == "__main__":
    main()