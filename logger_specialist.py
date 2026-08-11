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
from config import GEMINI_MODEL_LOGGER

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(BASE_DIR, "chronos_data")
LOGS_DIR = os.path.join(DATA_DIR, "logs")
ARCHIVE_DIR = os.path.join(DATA_DIR, "archive")
DB_DIR = os.path.join(DATA_DIR, "vector_db")

# =====================================================
# AUTO_GENERATE_SUMMARIES = False → dezactivează generarea
# automată de judecăți/scoruri la pornire și periodic.
# Butonul "Rejudecă" din interfața web funcționează în continuare.
# =====================================================
AUTO_GENERATE_SUMMARIES = False

TARGETS_FILE = os.path.join(DATA_DIR, "targets.json")
COMPLETED_FILE = os.path.join(ARCHIVE_DIR, "completed_goals.json")
TEMP_WAV = os.path.join(BASE_DIR, "temp_input.wav")

JOURNAL_SILENCE = 10.0
TARGET_SILENCE = 4.0

# ==================== SCHEMA SCORURI NOU ====================
SCORES_SCHEMA = {
    "type": "OBJECT",
    "properties": {
        "energie":         {"type": "INTEGER", "description": "Nivelul de energie/vitalitate al zilei (1=mort, 10=exploziv)"},
        "stres":           {"type": "INTEGER", "description": "Stresul psihic resimtit (1=relaxat total, 10=copleșit/paralizat)"},
        "dopamina":        {"type": "INTEGER", "description": "Calitatea surselor de dopamina (1=scroll/vicii/pasiv, 10=activ/natural/sport/creație)"},
        "disciplina":      {"type": "INTEGER", "description": "Autocontrol si respectarea planului personal (1=caotic, 10=de fier)"},
        "social":          {"type": "INTEGER", "description": "Calitatea conexiunilor sociale ale zilei (1=izolare totala, 10=conexiuni profunde)"},
        "somn":            {"type": "INTEGER", "description": "Calitatea si cantitatea somnului din noaptea anterioara (1=epuizare, 10=odihnit complet)"},
        "claritate":       {"type": "INTEGER", "description": "Claritate mentala, focus si gandire lucida (1=ceata/confuz, 10=cristal/razor sharp)"},
        "progres_scopuri": {"type": "INTEGER", "description": "Progres concret spre obiectivele declarate (1=zero actiune, 10=zi maxima spre scop)"},
        "dispozitie":      {"type": "INTEGER", "description": "Starea emotionala generala/mood (1=depresie/gol interior, 10=fericire autentica)"},
        "corp":            {"type": "INTEGER", "description": "Cum s-a simtit si tratat corpul - sport, nutritie, ingrijire (1=neglijare totala, 10=optim)"}
    },
    "required": ["energie", "stres", "dopamina", "disciplina", "social", "somn", "claritate", "progres_scopuri", "dispozitie", "corp"]
}

JUDGMENT_SCHEMA = {
    "type": "OBJECT",
    "properties": {
        "scores": SCORES_SCHEMA,
        "tags": {
            "type": "ARRAY",
            "items": {"type": "STRING"},
            "description": "3-6 cuvinie cheie reprezentative in romana (ex: epuizare, robotica, conexiune, gol_interior)"
        },
        "short_summary": {
            "type": "STRING",
            "description": "Esenta zilei in 2-3 propozitii. Nu enumera activitati, surprinde starea si tonul zilei."
        },
        "psychologist_feedback": {
            "type": "STRING",
            "description": "Feedback empatic dar sincer, ca un psiholog bun. Observa pattern-uri, pune intrebari bune, nu judeca, ci ajuta sa inteleaga."
        },
        "what_went_well": {
            "type": "STRING",
            "description": "Ce a mers bine azi concret (1-3 lucruri scurte)"
        },
        "pattern_alert": {
            "type": "STRING",
            "description": "Un pattern negativ sau riscant observat din aceasta zi (sau confirmat din nou). Poate fi null daca nu e nimic ingrijorator."
        }
    },
    "required": ["scores", "tags", "short_summary", "psychologist_feedback", "what_went_well", "pattern_alert"]
}


class MemoryManager:
    def __init__(self):
        if not os.path.exists(DB_DIR): os.makedirs(DB_DIR, exist_ok=True)
        self.client = chromadb.PersistentClient(path=DB_DIR)
        self.collection = self.client.get_or_create_collection(name="chronos_memory")

    def add_memory(self, memory_id, text, metadata):
        try:
            # Delete existing if exists (for re-judgment)
            try:
                self.collection.delete(ids=[memory_id])
            except: pass
            self.collection.add(documents=[text], metadatas=[metadata], ids=[memory_id])
        except Exception as e: logging.error(f"Eroare ChromaDB: {e}")

    def get_recent(self, n=5, where_filter=None):
        """
        Returnează cele mai recente N documente (după metadata['timestamp']),
        indiferent de relevanța semantică — spre deosebire de query_memory()
        care caută pe similaritate. Folosit pentru a da lui Chronos un
        "recap" al conversațiilor recente la începutul unei sesiuni vocale noi.
        """
        try:
            results = self.collection.get(
                where=where_filter,
                limit=max(n * 4, 20),  # supra-eșantionăm ca să putem sorta după timp
                include=["documents", "metadatas"],
            )
            docs  = results.get("documents") or []
            metas = results.get("metadatas") or []
            paired = list(zip(docs, metas))
            paired.sort(key=lambda dm: (dm[1] or {}).get("timestamp", ""), reverse=True)
            return [doc for doc, _ in paired[:n] if doc]
        except Exception as e:
            logging.error(f"Eroare ChromaDB get_recent: {e}")
            return []

    def query_memory(self, queries, n_results=3, where_filter=None):
        # Asigură-te că queries este o listă (dacă primește un singur string, îl transformă în listă)
        if isinstance(queries, str):
            queries = [queries]
            
        try:
            if where_filter:
                results = self.collection.query(
                    query_texts=queries,
                    n_results=n_results,
                    where=where_filter
                )
            else:
                results = self.collection.query(
                    query_texts=queries,
                    n_results=n_results
                )
            
            # ChromaDB returnează liste de liste (câte o listă de rezultate pentru fiecare cuvânt cheie)
            # Trebuie să le unificăm și să eliminăm duplicatele ca să nu zăpăcim AI-ul.
            all_docs = []
            seen = set()
            
            if results and results.get('documents'):
                for doc_list in results['documents']:
                    for doc in doc_list:
                        if doc and doc not in seen:
                            seen.add(doc)
                            all_docs.append(doc)
            
            if not all_docs:
                return "Nu am găsit amintiri relevante pentru această perioadă sau subiect."
            
            # Returnăm textul combinat (limităm la max 10 intrări unice ca să nu depășim limita de tokeni)
            return "\n\n".join(all_docs[:10]) 
            
        except Exception as e:
            return f"Eroare la accesarea memoriei: {e}"


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

    def _get_log_file_for_date(self, date_str):
        """Get the log file path for a given YYYY-MM-DD date string."""
        year, month, _ = date_str.split('-')
        return os.path.join(LOGS_DIR, f"log_{year}_{month}.jsonl")

    def _get_logical_date(self, dt_obj):
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
        self.wled.save_state(slot="journal")
        self._set_wled_color([100, 0, 255])
        text = self._record_audio(sock, silence_limit=JOURNAL_SILENCE)
        self._set_wled_color([255, 200, 0])
        if text: self._process_daily_entry(text)
        self.wled.restore_state(slot="journal")

    def _process_daily_entry(self, raw_text, logical_date=None):
        logging.info(f"🗣️ Ai zis: {raw_text}")
        prompt = f"""
        ROL: Asistent de dictare și Analist Emoțional.
        SARCINĂ: Ai primit textul jurnalului: "{raw_text}"
        1. Dacă utilizatorul înjură comanda, zice "am greșit", "nu salva" sau "șterge", acțiunea este DISCARD. Altfel, acțiunea este SAVE.
        2. Analizează emoția din text și extrage o culoare RGB (mood_color). Ex: [255,255,0] (fericit), [0,0,255] (trist), [255,0,0] (nervos), [0,255,0] (liniștit)
        3. Generează un răspuns vocal extrem de scurt (ex: "Am notat.", "Anulat.") pentru a confirma acțiunea.
        """
        schema = {
            "type": "OBJECT",
            "properties": {
                "action": {"type": "STRING", "enum": ["SAVE", "DISCARD"]},
                "voice_reply": {"type": "STRING"},
                "mood_color": {"type": "ARRAY", "items": {"type": "INTEGER"}}
            },
            "required": ["action", "voice_reply", "mood_color"]
        }
        result = ask_gemini_json(prompt, schema=schema, temperature=0.3)
        if not result: return
        print(f"🤖 Chronos: {result.get('voice_reply', 'Am salvat.')}")
        if result.get("action") == "SAVE":
            dt_now = datetime.now()
            if not logical_date:
                logical_date = self._get_logical_date(dt_now)
            entry = {
                "timestamp": dt_now.isoformat(),
                "type": "daily_entry",
                "logical_date": logical_date,
                "raw_text": raw_text
            }
            log_file = self._get_log_file_for_date(logical_date)
            os.makedirs(LOGS_DIR, exist_ok=True)
            with open(log_file, "a", encoding="utf-8") as f:
                f.write(json.dumps(entry, ensure_ascii=False) + "\n")
            mood_color = result.get("mood_color")
            if mood_color and len(mood_color) == 3:
                logging.info(f"🎨 Sincronizare Mood: {mood_color}")
                self.wled.pulse_color(mood_color, duration=4)

    def start_target_session(self, sock):
        logging.info("🎯 M-AM ACTIVAT PENTRU TARGET")
        self.wled.save_state(slot="target")
        self._set_wled_color([255, 0, 100])
        text = self._record_audio(sock, silence_limit=TARGET_SILENCE)
        self._set_wled_color([0, 255, 0])
        if text:
            target = {
                "id": str(int(time.time())),
                "title": text,
                "progress": 0,
                "created_at": datetime.now().isoformat()
            }
            try:
                with open(TARGETS_FILE, 'r', encoding='utf-8') as f: data = json.load(f)
                data.setdefault('goals', []).append(target)
                with open(TARGETS_FILE, 'w', encoding='utf-8') as f: json.dump(data, f, ensure_ascii=False, indent=4)
                print(f"🤖 Chronos: Am notat targetul.")
            except Exception as e:
                logging.error(f"Eroare salvare target: {e}")
        self.wled.restore_state(slot="target")

    # ==================== SISTEMUL DE JUDECATĂ ====================
    def check_and_generate_missing_summaries(self):
        # ⚠️ Auto-generarea e dezactivată — foloseşte butonul "Rejudecă" din interfaţă
        if not AUTO_GENERATE_SUMMARIES:
            logging.info("⏭️ Auto-generare judecăţi DEZACTIVATĂ (AUTO_GENERATE_SUMMARIES=False). Skip.")
            return

        logging.info("🔍 Verific dacă există zile din trecut care nu au primit 'Judecata'...")
        if not os.path.exists(LOGS_DIR): return

        days_data = {}
        completed_days = set()

        for file_path in glob.glob(os.path.join(LOGS_DIR, "*.jsonl")):
            with open(file_path, 'r', encoding='utf-8') as f:
                for line in f:
                    if not line.strip(): continue
                    try:
                        data = json.loads(line)
                        if data.get("type") == "daily_entry":
                            l_date = data.get("logical_date")
                            if not l_date:
                                l_date = self._get_logical_date(datetime.fromisoformat(data["timestamp"]))
                            dt_obj = datetime.fromisoformat(data["timestamp"])
                            time_str = dt_obj.strftime("%H:%M")
                            if l_date not in days_data: days_data[l_date] = []
                            days_data[l_date].append(f"[{time_str}] {data['raw_text']}")
                        elif data.get("type") == "daily_summary":
                            completed_days.add(data.get("logical_date"))
                    except: pass

        current_logical_date = self._get_logical_date(datetime.now())

        for day, logs_list in days_data.items():
            if day in completed_days: continue
            if day == current_logical_date: continue
            logging.info(f"⚖️ Generez Judecata pentru ziua ratată: {day}...")
            self._generate_judgment_for_day(day, logs_list)

    def _generate_judgment_for_day(self, logical_date, logs_list, targets_context=None):
        combined_text = "\n".join(logs_list)
        past_context = self.memory.query_memory(combined_text, n_results=3)

        # Load current targets for context
        if targets_context is None:
            try:
                with open(TARGETS_FILE, 'r', encoding='utf-8') as f:
                    tdata = json.load(f)
                targets_list = tdata.get('goals', [])
                targets_context = "\n".join([f"- [{g.get('priority','?')}] {g['title']} (progres: {g.get('progress',0)}%)" for g in targets_list]) or "Niciun target activ."
            except:
                targets_context = "Targeturile nu au putut fi încărcate."

        prompt = f"""
        ROL: Psiholog sincer și empatic, cunoscut ca "Chronos". Analizezi jurnalul zilnic al utilizatorului.
        
        Nu ești un coach de productivitate și nu ești brutal fără motiv. Ești ca un prieten psiholog care:
        - Observă pattern-uri comportamentale și emoționale
        - Pune întrebări care ajută la reflecție
        - Laudă CE MERITĂ LĂUDAT, nu generic
        - Semnalează probleme cu empatie, nu cu judecată
        - Conectează ziua de azi cu targeturile declarate
        
        DATA ANALIZATĂ: {logical_date}
        
        TARGETURILE ACTIVE ALE UTILIZATORULUI:
        {targets_context}
        
        CONTEXT DIN ZILELE TRECUTE (memorie):
        {past_context}
        
        LOGURILE ZILEI (ce a zis utilizatorul):
        {combined_text}
        
        INSTRUCȚIUNI STRICTE:
        1. short_summary: 2-3 propoziții care surprind ESENȚA și TONUL zilei, nu o listă de activități.
        2. psychologist_feedback: 3-5 propoziții. Fii empatic dar sincer. Observă pattern-uri. Pune o întrebare bună la final.
        3. what_went_well: 1-3 lucruri concrete pozitive din această zi.
        4. pattern_alert: Un pattern negativ specific observat azi (dacă există). Dacă nu e nimic îngrijorător, scrie null sau "Niciun pattern negativ semnificativ azi."
        5. tags: 3-6 cuvinte cheie în română, relevante psihologic (ex: epuizare, conexiune_sociala, gol_interior, progres, rezistenta, anxietate)
        6. RĂSPUNDE EXCLUSIV ÎN LIMBA ROMÂNĂ!
        
        SCORURI (1-10, fii obiectiv bazat pe ce a SPUS, nu pe ce vrei tu să crezi):
        - energie: cât de energic/vital s-a simțit
        - stres: cât de stresat/presat s-a simțit (10 = paralizat de stres)
        - dopamina: calitatea surselor de dopamina (10 = activ/natural, 1 = scroll/vicii/pasiv)
        - disciplina: autocontrol și respectarea unui plan (1 = haos, 10 = fier)
        - social: calitatea interacțiunilor sociale (1 = izolare, 10 = conexiuni profunde)
        - somn: calitatea somnului anterior (estimat din context)
        - claritate: claritate mentală și focus
        - progres_scopuri: progres spre targeturile declarate
        - dispozitie: starea emoțională generală (1 = depresie, 10 = fericire autentică)
        - corp: cum s-a simțit și tratat corpul (sport, mâncare, odihnă)
        """

        # MODIFICAT: Schimbat modelul în gemini-2.5-pro pentru o analiză psihologică profundă
        analysis = ask_gemini_json(prompt, schema=JUDGMENT_SCHEMA, temperature=0.6, model=GEMINI_MODEL_LOGGER)
        if not analysis:
            logging.error(f"❌ AI-ul a eșuat la rezumatul pentru {logical_date}")
            return None

        summary_entry = {
            "timestamp": datetime.now().isoformat(),
            "type": "daily_summary",
            "logical_date": logical_date,
            "combined_text": combined_text,
            "analysis": analysis
        }

        log_file = self._get_log_file_for_date(logical_date)
        os.makedirs(LOGS_DIR, exist_ok=True)
        with open(log_file, "a", encoding="utf-8") as f:
            f.write(json.dumps(summary_entry, ensure_ascii=False) + "\n")

        mem_id = f"mem_{logical_date}"
        meta = {
            "date": logical_date,
            "summary": analysis.get('short_summary', ''),
            "dispozitie": analysis['scores'].get('dispozitie', 5)
        }
        self.memory.add_memory(mem_id, analysis.get('psychologist_feedback', ''), meta)
        logging.info(f"✅ Judecata zilei de {logical_date} a fost arhivată!")
        return summary_entry

    def rejudge_day(self, logical_date):
        """Re-generate the judgment for a specific day (deletes old summary first)."""
        logging.info(f"🔄 Re-judec ziua: {logical_date}")
        
        # Collect all entries for this day
        logs_list = []
        all_files = glob.glob(os.path.join(LOGS_DIR, "*.jsonl"))
        
        for file_path in all_files:
            lines = []
            with open(file_path, 'r', encoding='utf-8') as f:
                for line in f:
                    if not line.strip(): continue
                    try:
                        data = json.loads(line)
                        l_date = data.get("logical_date")
                        if not l_date:
                            l_date = self._get_logical_date(datetime.fromisoformat(data["timestamp"]))
                        
                        if data.get("type") == "daily_entry" and l_date == logical_date:
                            dt_obj = datetime.fromisoformat(data["timestamp"])
                            time_str = dt_obj.strftime("%H:%M")
                            logs_list.append(f"[{time_str}] {data['raw_text']}")
                        
                        # Keep all lines EXCEPT the old summary for this day
                        if data.get("type") == "daily_summary" and l_date == logical_date:
                            continue  # Skip, will be regenerated
                        lines.append(line)
                    except:
                        lines.append(line)
            
            # Rewrite file without old summary
            with open(file_path, 'w', encoding='utf-8') as f:
                f.writelines(lines)
        
        if not logs_list:
            logging.warning(f"Nu am găsit entry-uri pentru data {logical_date}")
            return None
        
        return self._generate_judgment_for_day(logical_date, logs_list)

    def _set_wled_color(self, color_rgb):
        import requests
        try:
            from config import WLED_IP_MAIN
            payload = {"on": True, "bri": 180, "seg": [{"col": [color_rgb, [0,0,0], [0,0,0]]}]}
            requests.post(f"http://{WLED_IP_MAIN}/json/state", json=payload, timeout=0.5)
        except: pass