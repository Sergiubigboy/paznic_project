import os
import json
import time
import struct
import wave
import logging
import requests
import speech_recognition as sr
from datetime import datetime
from collections import deque

# --- CONFIGURARE (CĂI ABSOLUTE) ---
# Aflăm calea unde se execută scriptul curent
BASE_DIR = os.path.dirname(os.path.abspath(__file__))

DATA_DIR = os.path.join(BASE_DIR, "chronos_data")
LOGS_DIR = os.path.join(DATA_DIR, "logs")
ARCHIVE_DIR = os.path.join(DATA_DIR, "archive")

TARGETS_FILE = os.path.join(DATA_DIR, "targets.json")
COMPLETED_FILE = os.path.join(ARCHIVE_DIR, "completed_goals.json")
TEMP_WAV = os.path.join(BASE_DIR, "temp_input.wav")

# Parametri Audio
JOURNAL_SILENCE = 4.0   # Timp de gândire la Jurnal
TARGET_SILENCE = 2.5    # Timp mai scurt la Target

class JournalCore:
    def __init__(self, gemini_key, wled_manager):
        self.gemini_key = gemini_key
        self.wled = wled_manager
        self._ensure_structure()

    def _ensure_structure(self):
        """Construiește structura de fișiere 'Enterprise' și verifică totul la pornire"""
        
        # 1. Creare Foldere Principale
        folders_to_create = [DATA_DIR, LOGS_DIR, ARCHIVE_DIR]
        for folder in folders_to_create:
            if not os.path.exists(folder):
                os.makedirs(folder, exist_ok=True)
                logging.info(f"📁 Am creat folderul lipsă: {folder}")

        # 2. Creare targets.json (cu structura goală corectă)
        if not os.path.exists(TARGETS_FILE):
            with open(TARGETS_FILE, 'w', encoding='utf-8') as f: 
                json.dump({"goals": []}, f, indent=4)
            logging.info(f"📄 Am creat fișierul de bază: {TARGETS_FILE}")
                
        # 3. Creare completed_goals.json (cu structura goală corectă)
        if not os.path.exists(COMPLETED_FILE):
            with open(COMPLETED_FILE, 'w', encoding='utf-8') as f: 
                json.dump({"completed_history": []}, f, indent=4)
            logging.info(f"📄 Am creat arhiva de obiective: {COMPLETED_FILE}")
            
        # 4. Creare/Verificare fișier .jsonl curent
        current_log = self._get_current_log_file()
        if not os.path.exists(current_log):
            # Doar creăm fișierul gol (jsonl nu are nevoie de structură inițială ca JSON)
            with open(current_log, 'w', encoding='utf-8') as f:
                pass
            logging.info(f"📄 Am creat jurnalul lunar: {current_log}")

    def _get_current_log_file(self):
        """Generează numele fișierului pentru luna curentă"""
        return os.path.join(LOGS_DIR, f"log_{datetime.now().strftime('%Y_%m')}.jsonl")

    # --- 1. MOTOR AUDIO (RECORDING) ---
    def _record_audio(self, sock, silence_limit): 
        audio_data = []
        start_time = time.time()
        last_sound = time.time()
        
        logging.info(f"🎤 [REC] Ascult... (Max Silence: {silence_limit}s)")
        
        try:
            while True:
                try:
                    data, _ = sock.recvfrom(2048)
                except TimeoutError:              
                    continue

                if data:
                    chunk = struct.unpack_from("h" * (len(data) // 2), data)
                    audio_data.extend(chunk)
                    
                    # Detectare VAD simplă
                    amplitude = sum(abs(x) for x in chunk) / len(chunk)
                    if amplitude > 200: 
                        last_sound = time.time()
                    
                    # Condiții oprire
                    if (time.time() - last_sound) > silence_limit:
                        logging.info("End of speech detected.")
                        break
                    if (time.time() - start_time) > 300: # Max 5 min
                        break
        except KeyboardInterrupt:
            pass
        
        # Salvare WAV Temporar
        with wave.open(TEMP_WAV, 'w') as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)
            wf.setframerate(16000)
            wf.writeframes(struct.pack("h" * len(audio_data), *audio_data))
            
        # Transcriere Google
        r = sr.Recognizer()
        try:
            with sr.AudioFile(TEMP_WAV) as src:
                audio = r.record(src)
                text = r.recognize_google(audio, language="ro-RO")
                logging.info(f"🗣️ Transcriere: {text}")
                return text
        except sr.UnknownValueError:
            logging.warning("Nu am înțeles nimic.")
            return None
        except Exception as e:
            logging.error(f"Eroare Transcriere: {e}")
            return None

    # =========================================================
    # MODUL A: TARGET MASTER (CRUD + ARHIVARE)
    # =========================================================
    def start_target_session(self, sock):
        logging.info("🎯 TARGET MODE ACTIVATED")
        
        self.wled.save_state()
        self._set_wled_color([0, 255, 200]) # Cyan

        text = self._record_audio(sock, silence_limit=TARGET_SILENCE)
        
        self._set_wled_color([255, 200, 0]) # Galben Processing

        if text:
            self._process_target_command(text)
        
        self.wled.restore_state()

    def _process_target_command(self, text):
        with open(TARGETS_FILE, 'r', encoding='utf-8') as f:
            current_data = json.load(f)

        url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.0-flash:generateContent?key={self.gemini_key}"
        
        prompt = f"""
        ACT AS: Project Manager for Chronos Core.
        CURRENT DATE: {datetime.now().strftime('%Y-%m-%d')}
        CURRENT GOALS: {json.dumps(current_data['goals'])}
        
        USER COMMAND: "{text}"

        TASK: Determine the action (CREATE, UPDATE, COMPLETE).
        
        RULES:
        - If user adds a task, create a new entry.
        - If user says "I finished X" or "X is 100% done", mark action as "COMPLETE".
        - If user updates progress ("I'm halfway done with Y"), update progress %.
        - Calculate relative dates ("next friday") to absolute YYYY-MM-DD.

        OUTPUT JSON format ONLY:
        {{
            "action": "CREATE" | "UPDATE" | "COMPLETE",
            "target_id": int (if UPDATE/COMPLETE, use existing ID, else null),
            "target_data": {{
                "id": int (generate unique random if new),
                "title": "string",
                "deadline": "YYYY-MM-DD",
                "progress": int (0-100),
                "priority": "High/Med/Low"
            }},
            "response_voice": "Short confirmation message in Romanian."
        }}
        """

        try:
            response = requests.post(url, headers={'Content-Type': 'application/json'}, json={"contents": [{"parts": [{"text": prompt}]}]})
            raw_ai = response.json()['candidates'][0]['content']['parts'][0]['text']
            result = json.loads(raw_ai.replace("```json", "").replace("```", "").strip())
            
            action = result['action']
            data = result['target_data']
            
            if action == "CREATE":
                data['id'] = int(time.time()) 
                data['created_at'] = datetime.now().strftime('%Y-%m-%d')
                current_data['goals'].append(data)
                self._save_targets(current_data)
                logging.info(f"✅ Target Creat: {data['title']}")

            elif action == "UPDATE":
                for i, goal in enumerate(current_data['goals']):
                    if goal['id'] == result.get('target_id') or goal['title'] == data['title']:
                        current_data['goals'][i].update(data)
                        
                        if int(data.get('progress', 0)) == 100:
                            self._archive_goal(current_data['goals'].pop(i))
                            result['response_voice'] += " Am mutat targetul în arhivă."
                        break
                self._save_targets(current_data)
                logging.info(f"🔄 Target Actualizat.")

            elif action == "COMPLETE":
                found = False
                for i, goal in enumerate(current_data['goals']):
                    if goal['title'].lower() in text.lower() or goal['id'] == result.get('target_id'):
                        goal['progress'] = 100
                        self._archive_goal(current_data['goals'].pop(i))
                        found = True
                        break
                
                if found:
                    self._save_targets(current_data)
                    logging.info(f"🏆 Target Completat și Arhivat!")

            print(f"🤖 Chronos: {result['response_voice']}")

        except Exception as e:
            logging.error(f"Eroare procesare target: {e}")

    def _save_targets(self, data):
        with open(TARGETS_FILE, 'w', encoding='utf-8') as f:
            json.dump(data, f, indent=4, ensure_ascii=False)

    def _archive_goal(self, goal_data):
        goal_data['completed_at'] = datetime.now().isoformat()
        with open(COMPLETED_FILE, 'r+', encoding='utf-8') as f:
            data = json.load(f)
            data['completed_history'].append(goal_data)
            f.seek(0)
            json.dump(data, f, indent=4, ensure_ascii=False)
        logging.info(f"📁 Arhivat în {COMPLETED_FILE}")


    # =========================================================
    # MODUL B: DEEP LOGGER (The Judge)
    # =========================================================
    def start_journal_session(self, sock):
        logging.info("📘 JOURNAL MODE ACTIVATED")
        self.wled.save_state()
        self._set_wled_color([100, 0, 255]) # Mov Deep

        text = self._record_audio(sock, silence_limit=JOURNAL_SILENCE)
        self._set_wled_color([255, 200, 0]) # Galben Processing

        if text:
            history = self._get_recent_context()
            self._analyze_log(text, history)
        
        self.wled.restore_state()

    def _get_recent_context(self, lines_count=5):
        log_file = self._get_current_log_file()
        if not os.path.exists(log_file): return "No history."
        
        lines = []
        try:
            with open(log_file, 'r', encoding='utf-8') as f:
                lines = f.readlines()[-lines_count:]
            
            context = []
            for line in lines:
                data = json.loads(line)
                summary = data.get('analysis', {}).get('short_summary', '')
                date = data.get('timestamp', '')
                context.append(f"[{date}] {summary}")
            return "\n".join(context)
        except: return "Error reading logs."

    def _analyze_log(self, text, history):
        url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.0-flash:generateContent?key={self.gemini_key}"
        
        prompt = f"""
        ROLE: "The Judge" (Chronos Core AI).
        CONTEXT (Last 5 days):
        {history}

        CURRENT INPUT: "{text}"

        TASK: 
        1. Analyze emotions and productivity.
        2. Assign scores (1-10).
        3. Generate a strict but fair feedback response.

        OUTPUT JSON ONLY:
        {{
            "scores": {{ "productivity": int, "happiness": int, "anger": int, "burnout": int }},
            "tags": ["tag1", "tag2", "tag3"],
            "quote": "Contextual quote - Author",
            "short_summary": "Summary of this log.",
            "judge_feedback": "Direct spoken response to user (Romanian)."
        }}
        """
        
        try:
            response = requests.post(url, headers={'Content-Type': 'application/json'}, json={"contents": [{"parts": [{"text": prompt}]}]})
            raw_ai = response.json()['candidates'][0]['content']['parts'][0]['text']
            analysis = json.loads(raw_ai.replace("```json", "").replace("```", "").strip())

            # Log Entry
            entry = {
                "timestamp": datetime.now().isoformat(),
                "type": "deep_log",
                "raw_text": text,
                "analysis": analysis
            }
            
            cale_salvare = self._get_current_log_file()
            
            # Scriem și forțăm flush direct pe disc
            with open(cale_salvare, "a", encoding="utf-8") as f:
                f.write(json.dumps(entry, ensure_ascii=False) + "\n")
                f.flush()
                os.fsync(f.fileno())
            
            print(f"\n⚖️ JUDECATA: {analysis['judge_feedback']}")
            print(f"📊 Scoruri: {analysis['scores']}")
            logging.info(f"✅ FIȘIER SALVAT FIZIC AICI: {cale_salvare}")

        except Exception as e:
            logging.error(f"Eroare AI Log: {e}")

    # Helper pentru WLED rapid
    def _set_wled_color(self, color_rgb):
        try:
            from config import WLED_IP_MAIN
            payload = {"on": True, "bri": 180, "seg": [{"col": [color_rgb, [0,0,0], [0,0,0]]}]}
            requests.post(f"http://{WLED_IP_MAIN}/json/state", json=payload, timeout=0.5)
        except: pass