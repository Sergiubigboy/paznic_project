import requests
import json
import logging
from config import GEMINI_API_KEY, GEMINI_BASE_URL, GEMINI_MODEL_DEFAULT

def ask_gemini_json(system_prompt, schema, temperature=0.7, model=None):
    if model is None:
        model = GEMINI_MODEL_DEFAULT

    url = f"{GEMINI_BASE_URL}/{model}:generateContent?key={GEMINI_API_KEY}"
    headers = {'Content-Type': 'application/json'}
    
    payload = {
        "contents": [{"parts": [{"text": system_prompt}]}],
        "generationConfig": {
            "temperature": temperature,
            "responseMimeType": "application/json",
            "responseSchema": schema
        }
    }

    try:
        response = requests.post(url, headers=headers, json=payload, timeout=30)
        
        # AICI E SALVAREA: Daca Google refuza (Eroare 400), printam EXACT motivul refuzului!
        if response.status_code != 200:
            logging.error(f"Detalii eroare Google API: {response.text}")
            
        response.raise_for_status()
        
        raw_text = response.json()['candidates'][0]['content']['parts'][0]['text']
        return json.loads(raw_text)
        
    except Exception as e:
        logging.error(f"❌ Eroare AI Core ({model}): {e}")
        return None
    