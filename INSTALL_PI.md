# Chronos pe Raspberry Pi

## 0. Datele tale sunt în siguranță

`chronos_data/` e în `.gitignore`, deci **`git pull` nu îl atinge niciodată**.
Jurnalul, finanțele și memoria de pe Pi rămân exact cum sunt, chiar dacă pe
Windows ai altă versiune. Cele două instalări au date separate, intenționat.

La fel și `.env` — secretele nu ajung niciodată în git. Pe Pi ai nevoie de
propriul `.env` (vezi pasul 4).

---

## 1. Librării de sistem (înainte de pip)

`sounddevice` e doar un wrapper peste PortAudio, iar acesta nu se instalează
prin pip. Fără el, pachetul se instalează dar crapă la rulare.

```bash
sudo apt update
sudo apt install -y python3-dev portaudio19-dev libportaudio2 \
                    libsndfile1 ffmpeg git
```

- `portaudio19-dev` + `libportaudio2` — microfon și redare
- `libsndfile1` — citire/scriere audio
- `ffmpeg` — rezervă pentru decodarea audio

---

## 2. Mediul virtual

```bash
cd ~/projects/paznic_project
python3 -m venv venv
source venv/bin/activate
pip install --upgrade pip
```

---

## 3. Pachetele Python

**Doi pași, nu unul.** Al doilea e obligatoriu:

```bash
pip install -r requirements.txt
pip install --no-deps openwakeword
```

### De ce openwakeword separat

`openwakeword` declară `tflite-runtime` ca dependință **obligatorie pe Linux**,
iar acesta nu are build-uri pentru Python 3.13 pe ARM64 — se oprește la 3.12.
Instalarea normală eșuează cu:

```
ERROR: No matching distribution found for tflite-runtime<3,>=2.8.0
```

Cu `--no-deps` îl instalăm fără ea. Rulează pe **onnxruntime**, care e la fel de
bun pentru wake word (doar puțin mai lent la încărcarea modelului). Codul
detectează singur ce backend există și îl alege pe cel potrivit — nu trebuie să
configurezi nimic.

Dependențele reale ale lui `openwakeword` (onnxruntime, scipy, scikit-learn,
tqdm) sunt deja în `requirements.txt`, deci nu lipsește nimic.

### Modelele de wake word

Dacă e prima instalare, descarcă modelele (inclusiv variantele `.onnx`):

```bash
python -c "import openwakeword.utils as u; u.download_models()"
```

### Dacă chromadb dă erori de compilare

```bash
pip install --only-binary=:all: chromadb
```

---

## 4. Fișierul `.env`

Nu vine din git. Creează-l pe Pi cu aceleași chei ca pe Windows:

```bash
nano .env
```

```bash
GEMINI_API_KEY=...
SPOTIFY_CLIENT_ID=...
SPOTIFY_CLIENT_SECRET=...
SPOTIFY_REDIRECT_URI=http://127.0.0.1:8888/callback
SPOTIFY_DEVICE_NAME=...
HA_URL=...
HA_TOKEN=...
WLED_IP_MAIN=...
WLED_IP_FLOOR=...
TELEGRAM_BOT_TOKEN=...
TELEGRAM_CHAT_ID=...
```

---

## 5. Verifică microfonul

Chronos caută singur un microfon: dacă nu există unul implicit (pe Pi e des
cazul — `Error querying device -1`), îl ia pe primul cu intrare disponibilă.

Ce vede sistemul de operare:

```bash
arecord -l
```

Ce vede Python:

```bash
python -c "import sounddevice as sd; print(sd.query_devices())"
```

Dacă ai mai multe microfoane și vrei unul anume, pune-l în `.env` — index sau
o bucată din nume:

```bash
AUDIO_INPUT_DEVICE=USB
```

Asta e mai sigur decât indexul pe Pi, unde ordinea plăcilor se poate schimba
la reboot.

Test rapid de înregistrare:

```bash
arecord -d 3 -f cd test.wav && aplay test.wav
```

---

## 6. Pornire manuală (prima dată)

```bash
python main_async.py
```

Ar trebui să vezi `✅ [AudioInterface] Gata` și `🔗 [GeminiLive] Gata`.

**Spotify cere o autorizare unică prin browser.** Pe Pi fără ecran, cel mai
simplu e să copiezi tokenul deja autorizat de pe Windows:

```
chronos_data/.spotify_token_cache
```

---

## 7. Serviciul systemd

Ai deja `chronos.service`. După orice `git pull`:

```bash
sudo systemctl restart chronos.service
sudo systemctl status chronos.service
```

Log-uri în timp real:

```bash
journalctl -u chronos.service -f
```

---

## Probleme frecvente

**`cannot import name 'SYSTEM_PROMPT' from 'config'`**
`config.py` era în `.gitignore` și Pi-ul avea o versiune veche. E rezolvat —
acum vine prin git. Dacă tot apare: `git pull` din nou.

**`sounddevice lipsă` deși l-ai instalat**
Lipsește PortAudio din sistem. Vezi pasul 1.

**`No matching distribution found for tflite-runtime`**
Nu instala `openwakeword` prin `requirements.txt`. Vezi pasul 3 — trebuie
`pip install --no-deps openwakeword`.

**`Tried to import the tflite runtime, but it was not found`**
Normal pe Pi — codul trece pe onnxruntime. Nu e eroare.

**`Niciun model disponibil!` la pornire**
Lipsesc modelele de wake word. Rulează comanda `download_models()` din pasul 3.

**`Error querying device -1`**
Nu există microfon implicit. Rezolvat — codul caută acum singur primul
dispozitiv de intrare. Dacă tot apare, microfonul chiar nu e văzut de sistem:
verifică `arecord -l` și că userul e în grupul `audio`.

**Serviciul pornește dar n-aude nimic**
Serviciul rulează ca alt utilizator, care poate n-are acces la placa audio.
Verifică `User=sergiu` în `/etc/systemd/system/chronos.service` și că userul e
în grupul `audio`: `sudo usermod -aG audio sergiu`.
