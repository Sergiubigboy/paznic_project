import os
import json
import time
from ai_core import ask_gemini_json

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
LOGS_DIR = os.path.join(BASE_DIR, "chronos_data", "logs")

def reprocess_all_logs():
    print("🔄 Încep reprocesarea logurilor vechi cu noul Creier Analitic...")
    
    if not os.path.exists(LOGS_DIR):
        print("❌ Nu am găsit folderul de loguri.")
        return

    for filename in os.listdir(LOGS_DIR):
        if not filename.endswith(".jsonl") or filename.startswith("reprocessed_"):
            continue
            
        file_path = os.path.join(LOGS_DIR, filename)
        new_file_path = os.path.join(LOGS_DIR, f"reprocessed_{filename}")
        
        print(f"\n📂 Procesez fișierul: {filename}")
        
        with open(file_path, 'r', encoding='utf-8') as f_in, open(new_file_path, 'w', encoding='utf-8') as f_out:
            for line in f_in:
                if not line.strip(): continue
                
                try:
                    data = json.loads(line)
                    raw_text = data.get("raw_text", "")
                    if not raw_text: continue
                    
                    print(f"   Analizez logul din: {data['timestamp']}...")

                    prompt = f"""
                    ROL: "The Judge" - Profilator Psihologic. 
                    SARCINĂ: Evaluează acest text vechi din jurnalul utilizatorului.
                    TEXT RAW: "{raw_text}"
                    
                    REGULI CRITICE:
                    1. short_summary: Extrage ESENȚA. Ce a făcut ziua asta diferită? Cum s-a simțit? Nu înșira mecanic activitățile (ex: "a fost la școală, apoi la sală"), ci surprinde starea (ex: "A fost o zi plină, dar se simte limitat și caută mai mult sens în activitățile lui").
                    2. judge_feedback: Brutal de sincer. Dacă simte un gol interior deși a muncit, subliniază asta. Fără laudă ieftină.
                    3. Toate textele STRICT în limba română.

                    SCORURI (1-10):
                    - execution: Cât de disciplinat a fost / cât a muncit fizic.
                    - fulfillment: Cât de util/împlinit sufletește se simte (scade dacă face lucruri mecanic).
                    - mental_load: Nivelul de stres / copleșire (10 = maxim).
                    - dopamine_control: Cum a rezistat tentațiilor (telefon/scroll).
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
                            "quote": {"type": "STRING"},
                            "short_summary": {"type": "STRING"},
                            "judge_feedback": {"type": "STRING"}
                        },
                        "required": ["scores", "tags", "quote", "short_summary", "judge_feedback"]
                    }
                    
                    new_analysis = ask_gemini_json(prompt, schema=schema, temperature=0.6, model="gemini-2.5-flash")
                    
                    if new_analysis:
                        data["analysis"] = new_analysis
                        f_out.write(json.dumps(data, ensure_ascii=False) + "\n")
                        print("   ✅ Actualizat cu succes.")
                    else:
                        print("   ❌ Eșec la procesare AI. Păstrez vechiul log.")
                        f_out.write(line)
                        
                    time.sleep(2) # Evităm rate-limitul la Google API
                    
                except Exception as e:
                    print(f"   ⚠️ Eroare: {e}")
                    f_out.write(line) # Păstrăm linia originală dacă dă eroare
                    
        print(f"✅ Fișier salvat ca {new_file_path}")
        print("💡 Poți șterge fișierul original și să îl redenumești pe cel 'reprocessed_' după ce verifici că e ok.")

if __name__ == "__main__":
    reprocess_all_logs()