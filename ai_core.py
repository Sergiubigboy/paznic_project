import requests
import json
import logging
from config import GEMINI_API_KEY

def ask_gemini_json(system_prompt, schema, temperature=0.7, model="gemini-2.0-flash"):
    """
    Acum suportăm modele multiple. 
    WLED și Dispatcher pot merge pe 2.0 Flash (super ieftin).
    Muzica va cere explicit "gemini-2.5-flash" pentru gusturi mai bune.
    """
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={GEMINI_API_KEY}"
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
        response = requests.post(url, headers=headers, json=payload, timeout=15)
        response.raise_for_status()
        
        raw_text = response.json()['candidates'][0]['content']['parts'][0]['text']
        return json.loads(raw_text)
        
    except Exception as e:
        logging.error(f"❌ Eroare AI Core ({model}): {e}")
        return None