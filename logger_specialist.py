import os
import json
import time
import struct
import wave
import logging
import speech_recognition as sr
from datetime import datetime
import chromadb  # <-- NOU: Baza de date vectorială
from ai_core import ask_gemini_json

# --- CONFIGURARE (CĂI ABSOLUTE) ---
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(BASE_DIR, "chronos_data")
LOGS_DIR = os.path.join(DATA_DIR, "logs")
ARCHIVE_DIR = os.path.join(DATA_DIR, "archive")
DB_DIR = os.path.join(DATA_DIR, "vector_db") # <-- NOU: Locația creierului pe termen lung

TARGETS_FILE = os.path.join(DATA_DIR, "targets.json")
COMPLETED_FILE = os.path.join(ARCHIVE_DIR, "completed_goals.json")
TEMP_WAV = os.path.join(BASE_DIR, "temp_input.wav")

# Am mărit timpii de liniște ca să nu se oprească dacă faci pauze când vorbești
JOURNAL_SILENCE = 10.0   
TARGET_SILENCE = 4.0    

# =========================================================
# GESTIONAR MEMORIE (RAG cu ChromaDB)
# =========================================================
class MemoryManager:
    def __init__(self):
        if not os.path.exists(DB_DIR):
            os.makedirs(DB_DIR, exist_ok=True)
        # Folosim clientul persistent pentru a salva embedding-urile pe hard disk
        self.client = chromadb.PersistentClient(path=DB_DIR)
        self.collection = self.client.get_or_create_collection(name="chronos_memory")

    def add_memory(self, memory_id, text, metadata):
        try:
            self.collection.add(
                documents=[text],
                metadatas=[metadata],
                ids=[memory_id]
            )
            logging.info(f"🧠 Memorie vectorială salvată cu succes: {memory_id}")
        except Exception as e:
            logging.error(f"❌ Eroare la scrierea în ChromaDB: {e}")

    def query_memory(self, query_text, n_results=3):
        try:
            results = self.collection.query(
                query_texts=[query_text],
                n_results=n_results
            )
            
            if not results['documents'] or not results['documents'][0]:
                return "Nu am găsit amintiri relevante în istoric."
            
            memories = []
            for doc, meta in zip(results['documents'][0], results['metadatas'][0]):
                date = meta.get('date', 'Dată necunoscută')
                summary = meta.get('summary', 'Fără rezumat')
                memories.append(f"[{date}] User a zis: '{doc}' -> Concluzie sistem: {summary}")
            
            return "\n".join(memories)
        except Exception as e:
            logging.error(f"❌ Eroare la citirea din ChromaDB: {e}")
            return "Baza de date cu amintiri este inaccesibilă."

# =========================================================
# CORE-UL JURNALULUI ACTUALIZAT
# =========================================================
class JournalCore:
    def __init__(self, wled_manager):
        self.wled = wled_manager
        self._ensure_structure()
        self.memory = MemoryManager() # <-- Inițializăm memoria la pornire

    def _ensure_structure(self):
        folders_to_create = [DATA_DIR, LOGS_DIR, ARCHIVE_DIR, DB_DIR]
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
                # 1. VERIFICĂM TIMPUL AICI SUS! (Chiar dacă ESP-ul a oprit transmisia)
                timp_trecut = time.time() - last_sound
                if timp_trecut > silence_limit: 
                    logging.info(f"🛑 [REC] Oprit: Liniște detectată de {silence_limit}s.")
                    break
                    
                if (time.time() - start_time) > 300: 
                    logging.info("🛑 [REC] Oprit: Limita absolută de 5 minute.")
                    break

                # 2. AȘTEPTĂM DATE
                try:
                    data, _ = sock.recvfrom(2048)
                except TimeoutError: 
                    continue # Dacă nu primim nimic, bucla se reia și verifică timpul de sus!

                # 3. PROCESĂM SUNETUL
                if data:
                    chunk = struct.unpack_from("h" * (len(data) // 2), data)
                    audio_data.extend(chunk)
                    
                    amplitude = sum(abs(x) for x in chunk) / len(chunk)
                    if amplitude > 50: 
                        last_sound = time.time() # Resetăm ceasul dacă te aude vorbind
                        
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
        except Exception as e:
            logging.error(f"Eroare transcriere: {e}")
            return None

    # ==================== TARGETS ====================
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
        if not result:
            logging.error("❌ Eroare: AI-ul nu a returnat un JSON valid pentru target.")
            return

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
            logging.error(f"❌ Eroare la procesarea răspunsului pentru target: {e}. Date primite: {result}")

    def _save_targets(self, data):
        with open(TARGETS_FILE, 'w', encoding='utf-8') as f: json.dump(data, f, indent=4, ensure_ascii=False)

    def _archive_goal(self, goal_data):
        goal_data['completed_at'] = datetime.now().isoformat()
        with open(COMPLETED_FILE, 'r+', encoding='utf-8') as f:
            data = json.load(f)
            data['completed_history'].append(goal_data)
            f.seek(0)
            json.dump(data, f, indent=4, ensure_ascii=False)

    # ==================== JOURNAL & MEMORY ====================
    def start_journal_session(self, sock):
        logging.info("📘 JOURNAL MODE ACTIVATED")
        self.wled.save_state()
        self._set_wled_color([100, 0, 255])

        text = self._record_audio(sock, silence_limit=JOURNAL_SILENCE)
        self._set_wled_color([255, 200, 0])

        if text:
            past_context = self.memory.query_memory(text, n_results=5)
            self._analyze_log(text, past_context)
        
        self.wled.restore_state()

    def _analyze_log(self, text, past_context):
        prompt = f"""
        ROL: "The Judge" (Chronos Core AI). Evaluezi activitatea utilizatorului STRICT în limba ROMÂNĂ.
        
        AMINTIRI RELEVANTE DIN TRECUT (Din Vector DB):
        {past_context}

        TEXTUL CURENT AL UTILIZATORULUI: "{text}"

        REGULĂ CRITICĂ PENTRU ANULARE: 
        Dacă utilizatorul spune că a greșit, că a apăsat din greșeală, cere să nu salvezi, să ștergi sau înjură pentru că e un test ("nu salva", "am apăsat din greșeală", "șterge", "căcat"), setează câmpul "action" pe "DISCARD". Altfel, setează-l pe "SAVE".
        
        SARCINĂ: 
        1. Analizează emoțiile și productivitatea.
        2. Atribuie scoruri (1-10).
        3. Generează un feedback strict dar corect (judge_feedback).
        4. TOATE textele generate TREBUIE să fie scrise în LIMBA ROMÂNĂ.
        """
        
        journal_schema = {
            "type": "OBJECT",
            "properties": {
                "action": {"type": "STRING", "enum": ["SAVE", "DISCARD"], "description": "Salvează sau aruncă logul la gunoi"},
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
                    "items": {"type": "STRING", "description": "Cuvinte cheie în limba română"}
                },
                "quote": {"type": "STRING", "description": "Un citat reprezentativ în limba română din ce a spus utilizatorul"},
                "short_summary": {"type": "STRING", "description": "Rezumat extrem de scurt în limba română"},
                "judge_feedback": {"type": "STRING", "description": "Feedback-ul analitic și vocal în limba română"}
            },
            "required": ["action", "scores", "tags", "quote", "short_summary", "judge_feedback"]
        }
        
        analysis = ask_gemini_json(prompt, schema=journal_schema, temperature=0.7)
        
        if not analysis:
            logging.error("❌ Eroare: AI-ul nu a returnat schema validă pentru jurnal.")
            return

        # VERIFICARE ANULARE
        if analysis.get("action") == "DISCARD":
            print("\n🗑️ [JURNAL ANULAT] Ai cerut anularea. Logul NU a fost salvat.")
            logging.info("Utilizatorul a cerut anularea. Abort log.")
            return

        try:
            # 1. Salvare clasică (Fișier .jsonl)
            entry = {
                "timestamp": datetime.now().isoformat(),
                "type": "deep_log",
                "raw_text": text,
                "analysis": analysis
            }
            
            cale_salvare = self._get_current_log_file()
            with open(cale_salvare, "a", encoding="utf-8") as f:
                f.write(json.dumps(entry, ensure_ascii=False) + "\n")
            logging.info(f"✅ Log salvat în fișier: {cale_salvare}")

            # 2. Salvare în ChromaDB (Creierul pe termen lung)
            memory_id = f"mem_{int(time.time())}"
            metadata = {
                "date": datetime.now().strftime('%Y-%m-%d %H:%M'),
                "summary": analysis.get('short_summary', ''),
                "productivity": analysis['scores'].get('productivity', 0),
                "happiness": analysis['scores'].get('happiness', 0)
            }
            self.memory.add_memory(memory_id=memory_id, text=text, metadata=metadata)

            print(f"\n⚖️ JUDECATA: {analysis['judge_feedback']}")
            print(f"📊 Scoruri: {analysis['scores']}")

        except Exception as e:
            logging.error(f"❌ Eroare fatală la salvarea Jurnalului: {e}")

    def _set_wled_color(self, color_rgb):
        import requests
        try:
            from config import WLED_IP_MAIN
            payload = {"on": True, "bri": 180, "seg": [{"col": [color_rgb, [0,0,0], [0,0,0]]}]}
            requests.post(f"http://{WLED_IP_MAIN}/json/state", json=payload, timeout=0.5)
        except: pass