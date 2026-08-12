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


def ask_gemini_text(prompt, temperature=0.8, model=None, use_search=False):
    """
    Răspuns TEXT liber (nu JSON), opțional cu căutare web Google (grounding).

    Folosit de calea text (terminal + dashboard) ca să poată răspunde la
    întrebări despre lumea reală — vreme, evenimente, știri — la fel ca
    sesiunea vocală. Modelul decide singur dacă are nevoie să caute.
    """
    if model is None:
        model = GEMINI_MODEL_DEFAULT

    url = f"{GEMINI_BASE_URL}/{model}:generateContent?key={GEMINI_API_KEY}"
    payload = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {"temperature": temperature},
    }
    if use_search:
        payload["tools"] = [{"google_search": {}}]

    try:
        response = requests.post(url, headers={'Content-Type': 'application/json'},
                                 json=payload, timeout=45)
        if response.status_code != 200:
            logging.error(f"Detalii eroare Google API: {response.text[:300]}")
        response.raise_for_status()

        parts = response.json()['candidates'][0]['content']['parts']
        return "".join(p.get('text', '') for p in parts).strip() or None
    except Exception as e:
        logging.error(f"❌ Eroare AI Core text ({model}): {e}")
        return None
    