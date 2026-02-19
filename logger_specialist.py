import os
import json
import time
import struct
import wave
import logging
import speech_recognition as sr
from datetime import datetime
from ai_core import ask_gemini_json

# --- CONFIGURARE (CĂI ABSOLUTE) ---
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(BASE_DIR, "chronos_data")
LOGS_DIR = os.path.join(DATA_DIR, "logs")
ARCHIVE_DIR = os.path.join(DATA_DIR, "archive")

TARGETS_FILE = os.path.join(DATA_DIR, "targets.json")
COMPLETED_FILE = os.path.join(ARCHIVE_DIR, "completed_goals.json")
TEMP_WAV = os.path.join(BASE_DIR, "temp_input.wav")

JOURNAL_SILENCE = 4.0   
TARGET_SILENCE = 2.5    

class JournalCore:
    def __init__(self, wled_manager):
        self.wled = wled_manager
        self._ensure_structure()

    def _ensure_structure(self):
        folders_to_create = [DATA_DIR, LOGS_DIR, ARCHIVE_DIR]
        for folder in folders_to_create:
            if not os.path.exists(folder):
                os.makedirs(folder, exist_ok=True)
                logging.info(f"📁 Am creat folderul lipsă: {folder}")

        if not os.path.exists(TARGETS_FILE):
            with open(TARGETS_FILE, 'w', encoding='utf-8') as f: json.dump({"goals": []}, f, indent=4)
                
        if not os.path.exists(COMPLETED_FILE):
            with open(COMPLETED_FILE, 'w', encoding='utf-8') as f: json.dump({"completed_history": []}, f, indent=4)
            
        current_log = self._get_current_log_file()
        if not os.path.exists(current_log):
            with open(current_log, 'w', encoding='utf-8') as f: pass

    def _get_current_log_file(self):
        return os.path.join(LOGS_DIR, f"log_{datetime.now().strftime('%Y_%m')}.jsonl")

    def _record_audio(self, sock, silence_limit): 
        audio_data = []
        start_time = time.time()
        last_sound = time.time()
        
        logging.info(f"🎤 [REC] Ascult... (Max Silence: {silence_limit}s)")
        
        try:
            while True:
                try:
                    data, _ = sock.recvfrom(2048)
                except TimeoutError: continue

                if data:
                    chunk = struct.unpack_from("h" * (len(data) // 2), data)
                    audio_data.extend(chunk)
                    
                    amplitude = sum(abs(x) for x in chunk) / len(chunk)
                    if amplitude > 200: last_sound = time.time()
                    
                    if (time.time() - last_sound) > silence_limit: break
                    if (time.time() - start_time) > 300: break
        except KeyboardInterrupt: pass
        
        with wave.open(TEMP_WAV, 'w') as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)
            wf.setframerate(16000)
            wf.writeframes(struct.pack("h" * len(audio_data), *audio_data))
            
        r = sr.Recognizer()
        try:
            with sr.AudioFile(TEMP_WAV) as src:
                audio = r.record(src)
                text = r.recognize_google(audio, language="ro-RO")
                logging.info(f"🗣️ Transcriere: {text}")
                return text
        except Exception: return None

    # =========================================================
    def start_target_session(self, sock):
        logging.info("🎯 TARGET MODE ACTIVATED")
        self.wled.save_state()
        self._set_wled_color([0, 255, 200])

        text = self._record_audio(sock, silence_limit=TARGET_SILENCE)
        self._set_wled_color([255, 200, 0])

        if text: self._process_target_command(text)
        self.wled.restore_state()

    def _process_target_command(self, text):
        with open(TARGETS_FILE, 'r', encoding='utf-8') as f: current_data = json.load(f)

        prompt = f"""
        ACT AS: Project Manager for Chronos Core.
        CURRENT DATE: {datetime.now().strftime('%Y-%m-%d')}
        CURRENT GOALS: {json.dumps(current_data['goals'])}
        
        USER COMMAND: "{text}"

        TASK: Determine the action (CREATE, UPDATE, COMPLETE).
        """

        # SCHEMA STRICTĂ PENTRU TARGETS
        target_schema = {
            "type": "OBJECT",
            "properties": {
                "action": {"type": "STRING", "enum": ["CREATE", "UPDATE", "COMPLETE"]},
                "target_id": {"type": "INTEGER", "nullable": True},
                "target_data": {
                    "type": "OBJECT",
                    "properties": {
                        "id": {"type": "INTEGER"},
                        "title": {"type": "STRING"},
                        "deadline": {"type": "STRING"},
                        "progress": {"type": "INTEGER"},
                        "priority": {"type": "STRING", "enum": ["High", "Med", "Low"]}
                    },
                    "required": ["id", "title", "deadline", "progress", "priority"]
                },
                "response_voice": {"type": "STRING", "description": "Short confirmation message in Romanian."}
            },
            "required": ["action", "target_data", "response_voice"]
        }

        result = ask_gemini_json(prompt, schema=target_schema, temperature=0.7)
        if not result: return

        try:
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
        with open(TARGETS_FILE, 'w', encoding='utf-8') as f: json.dump(data, f, indent=4, ensure_ascii=False)

    def _archive_goal(self, goal_data):
        goal_data['completed_at'] = datetime.now().isoformat()
        with open(COMPLETED_FILE, 'r+', encoding='utf-8') as f:
            data = json.load(f)
            data['completed_history'].append(goal_data)
            f.seek(0)
            json.dump(data, f, indent=4, ensure_ascii=False)

    # =========================================================
    def start_journal_session(self, sock):
        logging.info("📘 JOURNAL MODE ACTIVATED")
        self.wled.save_state()
        self._set_wled_color([100, 0, 255])

        text = self._record_audio(sock, silence_limit=JOURNAL_SILENCE)
        self._set_wled_color([255, 200, 0])

        if text:
            history = self._get_recent_context()
            self._analyze_log(text, history)
        
        self.wled.restore_state()

    def _get_recent_context(self, lines_count=5):
        log_file = self._get_current_log_file()
        if not os.path.exists(log_file): return "No history."
        
        lines = []
        try:
            with open(log_file, 'r', encoding='utf-8') as f: lines = f.readlines()[-lines_count:]
            context = []
            for line in lines:
                data = json.loads(line)
                context.append(f"[{data.get('timestamp', '')}] {data.get('analysis', {}).get('short_summary', '')}")
            return "\n".join(context)
        except: return "Error reading logs."

    def _analyze_log(self, text, history):
        prompt = f"""
        ROLE: "The Judge" (Chronos Core AI).
        CONTEXT (Last 5 days):
        {history}

        CURRENT INPUT: "{text}"

        TASK: 
        1. Analyze emotions and productivity.
        2. Assign scores (1-10).
        3. Generate a strict but fair feedback response.
        """
        
        # SCHEMA STRICTĂ PENTRU JURNAL
        journal_schema = {
            "type": "OBJECT",
            "properties": {
                "scores": {
                    "type": "OBJECT",
                    "properties": {
                        "productivity": {"type": "INTEGER"},
                        "happiness": {"type": "INTEGER"},
                        "anger": {"type": "INTEGER"},
                        "burnout": {"type": "INTEGER"}
                    },
                    "required": ["productivity", "happiness", "anger", "burnout"]
                },
                "tags": {
                    "type": "ARRAY",
                    "items": {"type": "STRING"}
                },
                "quote": {"type": "STRING"},
                "short_summary": {"type": "STRING"},
                "judge_feedback": {"type": "STRING"}
            },
            "required": ["scores", "tags", "quote", "short_summary", "judge_feedback"]
        }
        
        analysis = ask_gemini_json(prompt, schema=journal_schema, temperature=0.7)
        if not analysis: return

        try:
            entry = {
                "timestamp": datetime.now().isoformat(),
                "type": "deep_log",
                "raw_text": text,
                "analysis": analysis
            }
            
            cale_salvare = self._get_current_log_file()
            with open(cale_salvare, "a", encoding="utf-8") as f:
                f.write(json.dumps(entry, ensure_ascii=False) + "\n")
                f.flush()
                os.fsync(f.fileno())
            
            print(f"\n⚖️ JUDECATA: {analysis['judge_feedback']}")
            print(f"📊 Scoruri: {analysis['scores']}")

        except Exception as e:
            logging.error(f"Eroare AI Log: {e}")

    def _set_wled_color(self, color_rgb):
        import requests
        try:
            from config import WLED_IP_MAIN
            payload = {"on": True, "bri": 180, "seg": [{"col": [color_rgb, [0,0,0], [0,0,0]]}]}
            requests.post(f"http://{WLED_IP_MAIN}/json/state", json=payload, timeout=0.5)
        except: pass