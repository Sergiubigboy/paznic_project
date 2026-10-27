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
CLAUDE_CODE_OAUTH_TOKEN=...   # vezi 4b
```

---

## 4b. Textul pe abonamentul Claude

Tot ce e text (planificare, DJ, lumini, jurnal, emoții, conversația din
terminal) merge prin Claude Code CLI, pe abonamentul Claude — fără cost per
token. Vocea Live rămâne pe Gemini, cu `GEMINI_API_KEY`.

```bash
curl -fsSL https://claude.ai/install.sh | bash     # pune `claude` în ~/.local/bin
claude setup-token                                  # login în browser, afișează un token
```

`setup-token` îți dă un link: deschide-l pe telefon/PC, loghează-te, și
copiază tokenul primit în `.env` ca `CLAUDE_CODE_OAUTH_TOKEN=...`. Serviciul
systemd nu are browser, așa că tokenul ăsta e singurul login care merge acolo.

Verifică: `python tests/diagnostic.py` → secțiunea **5b** trebuie să arate
„Autentificat". Test rapid, fără restul lui Chronos:

```bash
python -m core.claude_cli "Spune salut în 3 cuvinte"
```

Vrei înapoi pe Gemini? `LLM_PROVIDER=gemini` în `.env` (sau în
`personalization.py`, secțiunea 7). Dacă Claude pică (CLI lipsă, limita
abonamentului atinsă), apelul se reîncearcă singur pe Gemini cât timp
`LLM_FALLBACK_TO_GEMINI = True`.

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

Repo-ul conține `chronos.service` gata făcut. Instalează-l:

```bash
sudo cp chronos.service /etc/systemd/system/chronos.service
sudo systemctl daemon-reload
sudo systemctl enable --now chronos.service
```

**Atenție dacă ai deja un serviciu mai vechi:** el pornea `main.py`, fișier
care nu mai există — a fost înlocuit de `main_async.py` la refactorizare.
Un serviciu rămas pe calea veche eșuează la fiecare pornire, tăcut. Comanda
de copiere de mai sus îl corectează.

După orice `git pull`:

```bash
sudo systemctl restart chronos.service
sudo systemctl status chronos.service
```

Log-uri în timp real:

```bash
journalctl -u chronos.service -f
```

---

## 8. Ecranul (touchscreen pe Pi)

Chronos are o interfață făcută special pentru ecranul Pi-ului:
**`http://localhost:5000/screen`** — un terminal simplu, alb pe negru:
starea curentă sus, un log care curge dedesubt, iar tot restul vine ca pop-up.

Instalare, o singură dată (ca utilizatorul tău, **fără** `sudo` în față):

```bash
cd ~/projects/paznic_project
bash pi/install_screen.sh
sudo reboot
```

După reboot: Pi-ul intră singur în desktop, așteaptă să pornească Chronos și
deschide ecranul pe tot monitorul (Chromium în kiosk). Dacă Chronos repornește
(din Setări sau după un `git pull` + restart), pagina se reîncarcă singură.

Ce găsești pe ecran:

| Zonă | Ce face |
|---|---|
| **Starea** (sus) | `ACUM` / `APOI` din programul zilei, timere, ce cântă, lumini, remindere. Atinge un rând → pop-up-ul lui. |
| **Logul** | Ce s-a întâmplat: ce i-ai zis (`tu`) și ce a răspuns (`ch`), piese noi (`♪`), ce ai apăsat (`ok`), erori (`err`). |
| **`> atinge pentru voce`** | = ai zis „Jarvis". Pop-up-ul VOCE arată live ce spui și ce răspunde; `gata` închide sesiunea. |
| **`muz`** | Play/pauză, `<<` `>>`, ♥ (îl ține minte DJ-ul), volum `-` / `+`. |
| **`lum`** | Aprinde / stinge, intensitate `-` / `+`, scenele salvate în dashboard. |
| **`zi`** | Programul de azi și reminderele. **Două atingeri** bifează — o atingere din greșeală nu bifează nimic. |
| **`sys`** | Luminozitate, stingere automată noaptea, reîncărcare, repornire Chronos. |
| **`off`** | Stinge ecranul acum: rămâne un ceas abia vizibil. Orice atingere îl aprinde. |

Când sună o alarmă sau un timer, apare pop-up-ul roșu ALARMĂ cu `oprește`.

**Noaptea**, între ora de culcare și cea de trezire din programul de somn,
după 2 minute fără atingere ecranul trece singur pe ceasul estompat și se
aprinde dimineața. Pe touchscreen-urile oficiale Raspberry Pi scade și lumina
de fundal; pe un monitor HDMI pagina doar se întunecă.

Ecran pe verticală? Rotește-l din *Preferences → Screen Configuration*;
interfața se aranjează singură și pe vertical.

Ieșire din kiosk (cu tastatură sau prin SSH):

```bash
pkill -f kiosk.sh; pkill chromium
```

Dezactivare definitivă: `rm ~/.config/autostart/chronos-screen.desktop`.

---

## 9. Consola fizică (Pico, butoane, ecranele mici)

Carcasa cu TFT, ecran rotund, encoder, taste, slider și LED-uri e condusă de un
Pico legat pe USB la Pi. Schema, pinii, ce trebuie cumpărat și cum se încarcă
firmware-ul sunt în **[console/README.md](console/README.md)**.

Pe Pi îți trebuie doar `pip install pyserial` și `sudo usermod -aG dialout sergiu`
(acces la portul serial). Fără Pico conectat, Chronos merge exact ca înainte.

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

**Serviciul pornește și moare imediat**
Verifică ce fișier lansează: `grep ExecStart /etc/systemd/system/chronos.service`.
Dacă spune `main.py`, e calea veche — fișierul a fost înlocuit de
`main_async.py`. Copiază serviciul din repo (pasul 7).

**Diagnosticul raportează zeci de pachete lipsă**
Îți găsește singur venv-ul acum. Dacă tot le raportează, chiar lipsesc:
vezi pasul 3.

**Serviciul pornește dar n-aude nimic**
Serviciul rulează ca alt utilizator, care poate n-are acces la placa audio.
Verifică `User=sergiu` în `/etc/systemd/system/chronos.service` și că userul e
în grupul `audio`: `sudo usermod -aG audio sergiu`.

**Ecranul rămâne pe pop-up-ul OFFLINE**
Pagina e bine, Chronos nu răspunde pe portul 5000. Verifică serviciul:
`systemctl status chronos.service`. Pagina se reconectează singură.

**Pe ecran, promptul spune „voce oprită"**
Chronos rulează, dar fără microfon sau fără Gemini Live — vezi pașii 4 și 5.
Restul ecranului merge normal.

**Rândul MUZICĂ scrie „spotify neconectat"**
Pe Pi lipsește tokenul Spotify: `python -m tools.spotify_api auth`, apoi
repornește serviciul.

**Luminozitatea nu apare în `sys`**
Ecranul n-are lumină de fundal reglabilă (monitor HDMI) sau regula udev nu s-a
aplicat încă — a fost nevoie de un reboot după `install_screen.sh`. Verifică:
`ls -l /sys/class/backlight/*/brightness` (grupul trebuie să fie `video`).
