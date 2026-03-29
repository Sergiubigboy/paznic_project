import os
import json
import time
import glob
from datetime import datetime
from ai_core import ask_gemini_json
from logger_specialist import JUDGMENT_SCHEMA

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
LOGS_DIR = os.path.join(BASE_DIR, "chronos_data", "logs")
TARGETS_FILE = os.path.join(BASE_DIR, "chronos_data", "targets.json")

def reprocess_all_logs():
    print("🔄 Încep reprocesarea logurilor cu noul sistem psihologic...")

    if not os.path.exists(LOGS_DIR):
        print("❌ Nu am găsit folderul de loguri.")
        return

    # Load targets for context
    try:
        with open(TARGETS_FILE, 'r', encoding='utf-8') as f:
            tdata = json.load(f)
        targets_list = tdata.get('goals', [])
        targets_context = "\n".join([f"- [{g.get('priority','?')}] {g['title']} (progres: {g.get('progress',0)}%)" for g in targets_list]) or "Niciun target activ."
    except:
        targets_context = "Targeturile nu au putut fi încărcate."

    for filename in sorted(glob.glob(os.path.join(LOGS_DIR, "*.jsonl"))):
        if os.path.basename(filename).startswith("reprocessed_"):
            continue

        print(f"\n📂 Procesez: {filename}")

        # Group daily_entries by logical_date, keep daily_summary positions
        entries_by_date = {}
        all_lines_info = []  # list of (line_str, parsed_data_or_None)

        with open(filename, 'r', encoding='utf-8') as f:
            raw_lines = [l for l in f.readlines() if l.strip()]

        for line in raw_lines:
            try:
                data = json.loads(line)
                ldate = data.get("logical_date", "")
                if data.get("type") == "daily_entry" and ldate:
                    if ldate not in entries_by_date:
                        entries_by_date[ldate] = []
                    dt_obj = datetime.fromisoformat(data["timestamp"])
                    entries_by_date[ldate].append(f"[{dt_obj.strftime('%H:%M')}] {data['raw_text']}")
                all_lines_info.append((line, data))
            except:
                all_lines_info.append((line, None))

        new_lines = []
        processed_dates = set()

        for line, data in all_lines_info:
            if data is None:
                new_lines.append(line)
                continue

            if data.get("type") == "daily_summary":
                ldate = data.get("logical_date", "")
                if ldate in processed_dates:
                    # Already processed, skip duplicate
                    continue

                logs_list = entries_by_date.get(ldate, [])
                if not logs_list:
                    # No raw entries found for this summary, keep original
                    new_lines.append(line)
                    continue

                combined_text = "\n".join(logs_list)
                print(f"   🔁 Re-analiza pentru {ldate}...")

                prompt = f"""
                ROL: Psiholog sincer și empatic, cunoscut ca "Chronos".
                
                TARGETURILE ACTIVE:
                {targets_context}
                
                LOGURILE ZILEI {ldate}:
                {combined_text}
                
                INSTRUCȚIUNI:
                1. short_summary: 2-3 propoziții care surprind ESENȚA zilei, nu o listă de activități.
                2. psychologist_feedback: 3-5 propoziții empatice dar sincere. Pune o întrebare bună la final.
                3. what_went_well: 1-3 lucruri concrete pozitive.
                4. pattern_alert: Un pattern negativ observat, sau "Niciun pattern negativ semnificativ azi." dacă nu există.
                5. tags: 3-6 cuvinte cheie în română (ex: epuizare, conexiune_sociala, progres, gol_interior)
                6. RĂSPUNDE EXCLUSIV ÎN ROMÂNĂ!
                """

                new_analysis = ask_gemini_json(prompt, schema=JUDGMENT_SCHEMA, temperature=0.6, model="gemini-2.5-flash")

                if new_analysis:
                    data["analysis"] = new_analysis
                    data["combined_text"] = combined_text
                    new_lines.append(json.dumps(data, ensure_ascii=False) + "\n")
                    processed_dates.add(ldate)
                    print(f"   ✅ {ldate} re-analizat cu succes.")
                else:
                    print(f"   ❌ Eșec AI pentru {ldate}. Păstrez vechiul.")
                    new_lines.append(line)
                    processed_dates.add(ldate)

                time.sleep(3)  # Rate limiting

            else:
                new_lines.append(line)

        # Write back
        with open(filename, 'w', encoding='utf-8') as f:
            f.writelines(new_lines)

        print(f"✅ Fișier actualizat: {filename}")

    print("\n🎉 Reprocesare completă!")

if __name__ == "__main__":
    reprocess_all_logs()