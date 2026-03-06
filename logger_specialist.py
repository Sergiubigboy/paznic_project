import os
import json
import time
import struct
import wave
import logging
import socket
import speech_recognition as sr
from datetime import datetime, timedelta
import glob
import chromadb
from ai_core import ask_gemini_json

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(BASE_DIR, "chronos_data")
LOGS_DIR = os.path.join(DATA_DIR, "logs")
ARCHIVE_DIR = os.path.join(DATA_DIR, "archive")
DB_DIR = os.path.join(DATA_DIR, "vector_db")

TARGETS_FILE = os.path.join(DATA_DIR, "targets.json")
COMPLETED_FILE = os.path.join(ARCHIVE_DIR, "completed_goals.json")
TEMP_WAV = os.path.join(BASE_DIR, "temp_input.wav")

JOURNAL_SILENCE = 10.0   
TARGET_SILENCE = 4.0    

class MemoryManager:
    def __init__(self):
        if not os.path.exists(DB_DIR): os.makedirs(DB_DIR, exist_ok=True)
        self.client = chromadb.PersistentClient(path=DB_DIR)
        self.collection = self.client.get_or_create_collection(name="chronos_memory")

    def add_memory(self, memory_id, text, metadata):
        try:
            self.collection.add(documents=[text], metadatas=[metadata], ids=[memory_id])
        except Exception as e: logging.error(f"Eroare ChromaDB: {e}")

    def query_memory(self, query_text, n_results=3):
        try:
            results = self.collection.query(query_texts=[query_text], n_results=n_results)
            if not results['documents'] or not results['documents'][0]: return "Nu am găsit amintiri relevante."
            
            memories = []
            for doc, meta in zip(results['documents'][0], results['metadatas'][0]):
                date = meta.get('date', 'Unknown')
                summary = meta.get('summary', '')
                memories.append(f"[{date}] {summary}")
            return "\n".join(memories)
        except: return "Baza de date de amintiri e offline."

class JournalCore:
    def __init__(self, wled_manager):
        self.wled = wled_manager
        self._ensure_structure()
        self.memory = MemoryManager()

    def _ensure_structure(self):
        for folder in [DATA_DIR, LOGS_DIR, ARCHIVE_DIR, DB_DIR]:
            if not os.path.exists(folder): os.makedirs(folder, exist_ok=True)
        if not os.path.exists(TARGETS_FILE):
            with open(TARGETS_FILE, 'w', encoding='utf-8') as f: json.dump({"goals": []}, f)
        if not os.path.exists(COMPLETED_FILE):
            with open(COMPLETED_FILE, 'w', encoding='utf-8') as f: json.dump({"completed_history": []}, f)
        
        current_log = self._get_current_log_file()
        if not os.path.exists(current_log):
            with open(current_log, 'w', encoding='utf-8') as f: pass

    def _get_current_log_file(self):
        return os.path.join(LOGS_DIR, f"log_{datetime.now().strftime('%Y_%m')}.jsonl")

    def _get_logical_date(self, dt_obj):
        # Ziua logică: Scădem 5 ore. Așa logul de la 03:00 AM marți e numărat la "Luni".
        shifted = dt_obj - timedelta(hours=5)
        return shifted.strftime("%Y-%m-%d")

    def _record_audio(self, sock, silence_limit): 
        audio_data = []
        start_time = time.time()
        last_sound = time.time()
        
        logging.info(f"🎤 [REC] Ascult... (Max Silence: {silence_limit}s)")
        try:
            while True:
                if (time.time() - last_sound) > silence_limit or (time.time() - start_time) > 300: break
                try: data, _ = sock.recvfrom(2048)
                except (TimeoutError, socket.timeout): continue

                if data:
                    chunk = struct.unpack_from("h" * (len(data) // 2), data)
                    audio_data.extend(chunk)
                    if (sum(abs(x) for x in chunk) / len(chunk)) > 50: last_sound = time.time()
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
                return text
        except Exception: return None

    # ==================== JURNAL RAPID ====================
    def start_journal_session(self, sock):
        logging.info("📘 M-AM ACTIVAT PENTRU JURNAL")
        self.wled.save_state()
        self._set_wled_color([100, 0, 255])
        
        text = self._record_audio(sock, silence_limit=JOURNAL_SILENCE)
        self._set_wled_color([255, 200, 0])

        if text: self._process_daily_entry(text)
        self.wled.restore_state()

    def _process_daily_entry(self, raw_text):
        logging.info(f"🗣️ Ai zis: {raw_text}")
        prompt = f"""
        ROL: Asistent de dictare.
        SARCINĂ: Ai primit textul: "{raw_text}"
        Dacă utilizatorul înjură comanda, zice "am greșit", "nu salva" sau "șterge", acțiunea este DISCARD.
        Altfel, acțiunea este SAVE.
        Generează un răspuns vocal extrem de scurt (ex: "Am notat.", "Anulat.") pentru a confirma acțiunea.
        """
        schema = {
            "type": "OBJECT",
            "properties": {
                "action": {"type": "STRING", "enum": ["SAVE", "DISCARD"]},
                "voice_reply": {"type": "STRING"}
            },
            "required": ["action", "voice_reply"]
        }
        
        result = ask_gemini_json(prompt, schema=schema, temperature=0.1)
        if not result: return
        
        print(f"🤖 Chronos: {result['voice_reply']}")

        if result.get("action") == "SAVE":
            dt_now = datetime.now()
            entry = {
                "timestamp": dt_now.isoformat(),
                "type": "daily_entry",
                "logical_date": self._get_logical_date(dt_now),
                "raw_text": raw_text
            }
            with open(self._get_current_log_file(), "a", encoding="utf-8") as f:
                f.write(json.dumps(entry, ensure_ascii=False) + "\n")

    # ==================== SISTEMUL DE AUTO-VINDECARE (BATCH JUDGEMENT) ====================
    def check_and_generate_missing_summaries(self):
        logging.info("🔍 Verific dacă există zile din trecut care nu au primit 'Judecata'...")
        if not os.path.exists(LOGS_DIR): return

        days_data = {}        # { "2026-03-03": [{"time": "14:30", "text": "..."}, ...] }
        completed_days = set() # Zilele care au deja 'daily_summary'

        # 1. Parcurgem absolut toate fișierele de log
        for file_path in glob.glob(os.path.join(LOGS_DIR, "*.jsonl")):
            with open(file_path, 'r', encoding='utf-8') as f:
                for line in f:
                    if not line.strip(): continue
                    try:
                        data = json.loads(line)
                        if data.get("type") == "daily_entry":
                            l_date = data.get("logical_date")
                            if not l_date: # Fallback vechi
                                l_date = self._get_logical_date(datetime.fromisoformat(data["timestamp"]))
                            
                            dt_obj = datetime.fromisoformat(data["timestamp"])
                            time_str = dt_obj.strftime("%H:%M")
                            
                            if l_date not in days_data: days_data[l_date] = []
                            days_data[l_date].append(f"[{time_str}] {data['raw_text']}")
                            
                        elif data.get("type") == "daily_summary":
                            completed_days.add(data.get("logical_date"))
                    except: pass

        # 2. Vedem ce zi logică e acum (nu o judecăm până nu trece)
        current_logical_date = self._get_logical_date(datetime.now())

        # 3. Judecăm zilele lipsă
        for day, logs_list in days_data.items():
            if day in completed_days:
                continue # Deja judecată
            
            if day == current_logical_date:
                continue # Încă e ziua în curs, o lăsăm în pace

            # Dacă am ajuns aici, avem o zi terminată (din trecut) care nu are Summary!
            logging.info(f"⚖️ Generez Judecata pentru ziua ratată: {day}...")
            self._generate_judgment_for_day(day, logs_list)

    def _generate_judgment_for_day(self, logical_date, logs_list):
        combined_text = "\n".join(logs_list)
        past_context = self.memory.query_memory(combined_text, n_results=3)

        prompt = f"""
        ROL: "The Judge" - Profilator Psihologic Brutal de Sincer.
        SARCINĂ: Analizează toate logurile brute dictated de utilizator de-a lungul zilei de {logical_date}.
        
        AMINTIRI VECHI: {past_context}
        
        TEXTELE TALE DE IERI:
        {combined_text}

        REGULI STRICTE:
        1. short_summary: Care e ESENȚA zilei? (nu enumera robotic activitățile). Cum s-a simțit?
        2. judge_feedback: Fii TĂIOS, obiectiv, fără laude ieftine. Dacă muncește dar se simte gol/inutil, zdruncină-l un pic mental, zi-i adevărul.
        
        SISTEM DE SCORING (1-10):
        - execution: Cât a muncit / disciplina.
        - fulfillment: Cât de împlinit și util s-a simțit (1 = depresie/gol interior, 10 = sens maxim).
        - mental_load: Cât de copleșit/stresat e creierul lui (10 = epuizat psihic).
        - dopamine_control: Cum a rezistat la telefon/scroll (1 = n-a rezistat, 10 = disciplină de fier).
        """
        
        schema = {
            "type": "OBJECT",
            "properties": {
                "scores": {
                    "type": "OBJECT",
                    "properties": {
                        "execution": {"type": "INTEGER"},
                        "fulfillment": {"type": "INTEGER"},
                        "mental_load": {"type": "INTEGER"},
                        "dopamine_control": {"type": "INTEGER"}
                    },
                    "required": ["execution", "fulfillment", "mental_load", "dopamine_control"]
                },
                "tags": {"type": "ARRAY", "items": {"type": "STRING"}},
                "short_summary": {"type": "STRING"},
                "judge_feedback": {"type": "STRING"}
            },
            "required": ["scores", "tags", "short_summary", "judge_feedback"]
        }

        # Folosim modelul deștept pentru asta
        analysis = ask_gemini_json(prompt, schema=schema, temperature=0.6, model="gemini-2.5-flash")
        if not analysis: 
            logging.error(f"❌ AI-ul a eșuat la rezumatul pentru {logical_date}")
            return

        # Salvăm SINTEZA în logul lunii curente
        summary_entry = {
            "timestamp": datetime.now().isoformat(),
            "type": "daily_summary",
            "logical_date": logical_date,
            "combined_text": combined_text,
            "analysis": analysis
        }

        with open(self._get_current_log_file(), "a", encoding="utf-8") as f:
            f.write(json.dumps(summary_entry, ensure_ascii=False) + "\n")

        # Memorăm doar Esența și Feedback-ul în Vector DB
        mem_id = f"mem_{logical_date}"
        meta = {
            "date": logical_date,
            "summary": analysis['short_summary'],
            "fulfillment": analysis['scores']['fulfillment']
        }
        self.memory.add_memory(mem_id, analysis['judge_feedback'], meta)
        logging.info(f"✅ Judecata zilei de {logical_date} a fost arhivată și memorată!")

    def _set_wled_color(self, color_rgb):
        import requests
        try:
            from config import WLED_IP_MAIN
            payload = {"on": True, "bri": 180, "seg": [{"col": [color_rgb, [0,0,0], [0,0,0]]}]}
            requests.post(f"http://{WLED_IP_MAIN}/json/state", json=payload, timeout=0.5)
        except: pass