import os
import json
import glob
from collections import defaultdict
from datetime import datetime
from functools import wraps
from flask import Flask, request, Response, render_template, jsonify

# --- CONFIGURARE SECURITATE ---
USERNAME = "admin"
PASSWORD = "123" # SCHIMBĂ ASTA CU O PAROLĂ PUTERNICĂ

# --- CONFIGURARE CĂI ---
# Aflăm unde e folderul "web"
CURRENT_DIR = os.path.dirname(os.path.abspath(__file__))

# Urcăm UN NIVEL mai sus pentru a ajunge în folderul principal (paznic_project)
BASE_DIR = os.path.abspath(os.path.join(CURRENT_DIR, ".."))

# Acum găsim logurile corect
LOGS_DIR = os.path.join(BASE_DIR, "chronos_data", "logs")
app = Flask(__name__)

# --- SISTEM DE AUTENTIFICARE ---
def check_auth(username, password):
    return username == USERNAME and password == PASSWORD

def authenticate():
    return Response(
        'Acces interzis. Te rog să te autentifici cu user și parolă.\n', 401,
        {'WWW-Authenticate': 'Basic realm="Chronos Core Login"'})

def requires_auth(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        auth = request.authorization
        if not auth or not check_auth(auth.username, auth.password):
            return authenticate()
        return f(*args, **kwargs)
    return decorated

# --- CITIREA ȘI PROCESAREA LOGURILOR ---
def get_all_logs():
    logs = []
    if not os.path.exists(LOGS_DIR):
        return logs

    for file_path in glob.glob(os.path.join(LOGS_DIR, "*.jsonl")):
        with open(file_path, 'r', encoding='utf-8') as f:
            for line in f:
                if line.strip():
                    try:
                        logs.append(json.loads(line))
                    except:
                        pass
    
    grouped_logs = defaultdict(list)
    for log in logs:
        try:
            date_obj = datetime.fromisoformat(log['timestamp'])
            day_string = date_obj.strftime("%Y-%m-%d")
            log['display_time'] = date_obj.strftime("%H:%M")
            grouped_logs[day_string].append(log)
        except Exception as e:
            continue

    sorted_days = sorted(grouped_logs.keys(), reverse=True)
    
    result = []
    for day in sorted_days:
        day_logs = sorted(grouped_logs[day], key=lambda x: x['timestamp'], reverse=True)
        result.append({
            "date": day,
            "logs": day_logs
        })
        
    return result

# --- RUTELE ---
@app.route('/')
@requires_auth
def index():
    # Randează fișierul HTML din folderul 'templates'
    return render_template('index.html')

@app.route('/api/logs')
@requires_auth
def api_logs():
    data = get_all_logs()
    return jsonify(data)

if __name__ == '__main__':
    print("🌐 Pornesc Dashboard-ul Chronos...")
    print("👉 Accesează: http://127.0.0.1:5000")
    app.run(host='0.0.0.0', port=5000, debug=True)