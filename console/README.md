# Consola Chronos

Carcasa cu ecrane și butoane din jurul Pi-ului. Pico-ul (RP2040) se ocupă de tot
ce e fizic. Pi-ul face doar ce nu se poate face altfel: datele, AI-ul, vocea și
ecranul mare.

```
                ┌──────────────────────────────────────────┐
                │        ECRANUL MARE 7"  (Pi, HDMI/DSI)    │  terminalul /screen:
                │  starea zilei · log · pop-up-uri          │  transcript, detalii
                └──────────────────────────────────────────┘
   ( ROTUND )   ┌───────────┐                       ┌────────┐
   GC9A01       │ TFT 2.4"  │  ◎ encoder            │ slider │
   vocea        │  meniul   │                       │ volum  │
   [⏻ metal]    └───────────┘  [K1] [K2] [K3]       └────────┘
                 ════════ bandă LED RGBW (36) ════════
```

## Ce face fiecare piesă

| Piesă | Rol |
|---|---|
| **Ecranul mare 7"** | Terminalul `/screen` (vezi INSTALL_PI.md, pasul 8). Sus e starea zilei, dedesubt e logul, cu transcriptul conversațiilor. Când navighezi pe TFT, **aceeași listă apare aici, întreagă, cu diacritice**, ca pop-up „CONSOLĂ”. |
| **TFT 2.4"** | Meniul, ca un meniu de rack: 20×16 caractere, alb pe negru, rândul selectat e inversat. Primul punct din meniu depinde de oră: dimineața *Ziua de azi*, ziua *Remindere*, seara *Remindere → Targets* (închiderea zilei). |
| **Ecranul rotund** | Vocea. În repaus arată un inel care respiră în culoarea lui Chronos și ora. Când asculți: bare radiale după vocea ta. Când vorbește el: bare albe după vocea lui. Alarma îl face roșu. Pi-ul oprit = negru. |
| **Encoder** | Rotit: alegi / reglezi (volum, intensitate, progres ±5%). Apăsat: OK. |
| **K1** | Înapoi. Ținut apăsat: meniul principal. |
| **K2** | Acțiunea locului: bifează (în liste, **două apăsări**), piesa următoare, aprinde/stinge. |
| **K3** | Vorbește cu Chronos (= „Jarvis”). Apăsat din nou = gata. |
| **Slider** | Volumul muzicii. În *Lumini*: intensitatea. |
| **Butonul de metal** | Întrerupător inteligent: ON pornește Pi-ul, OFF îl oprește **curat** (vezi mai jos). |
| **Banda RGBW** | Starea dintr-o privire: alb cald slab = repaus · culoarea lui Chronos după vocea ta = te ascultă · alb după vocea lui = vorbește · roșu clipind = alarmă · chihlimbar = Pi pornit, Chronos nu · albastru = pornește. |
| **Orice tastă când sună alarma** | O oprește. |

## Cine ce face

```
 Pico (RP2040)                              Pi 5
 ─────────────                              ────
 ambele ecrane mici + animațiile    USB     datele: remindere, ziua, targets, scene
 meniul și navigarea             ◄──────►   Spotify, WLED (aceleași funcții ca site-ul)
 taste, encoder, slider           JSON/linie vocea: wake word, Gemini Live, boxa
 LED-uri, butonul de pornire                ecranul mare
```

Meniul rulează pe Pico, deci răspunde instant chiar dacă Pi-ul e ocupat cu AI-ul.
Pi-ul doar trimite date și execută acțiuni (`core/console_bridge.py`).
Fără Pico conectat, Chronos merge exact ca înainte.

## Alimentare

```
230V ─► [Sursă 5V 20A] ─┬─ siguranță 5A ─► Pi 5 (pigtail USB-C)
                        │                   ├─ USB      ─► Pico (doar date)
                        │                   ├─ USB      ─► PS3 Eye (microfon)
                        │                   ├─ 5V pin 2 ─► SS14 ─► Pico VSYS ─► ecranele mici
                        │                   └─ 5V pin 4 ─► MAX98357A (boxa)
                        └─ siguranță 3A ─► bandă LED (+1000µF/10V pe bornele benzii)
                                           GND-ul benzii comun cu GND-ul Pico-ului
```

| Consumator | Tipic | Maxim |
|---|---|---|
| Pi 5 | ~1 A | ~3 A |
| Ecran 7" | ~0.4 A | ~0.8 A |
| PS3 Eye | ~0.15 A | ~0.5 A |
| Pico + 2 ecrane mici | ~0.1 A | ~0.2 A |
| Amplificator | ~0.05 A | ~0.7 A (volum maxim) |
| 36 LED-uri RGBW (ramură separată) | ~0.3 A | ~2.9 A la alb complet; **limitat în firmware la 1.2 A** |

Pinii 2 și 4 (5V) ai Pi-ului sunt legați direct la intrarea de 5V, deci **rămân
alimentați și cu Pi-ul oprit**. Pico-ul vede butonul și poate porni Pi-ul.
SS14 împiedică curentul să meargă înapoi spre USB.

Dacă ecranul de 7" e cel oficial (DSI), se alimentează tot din 5V-ul GPIO: îl
legi în paralel pe pinul 4, cu amplificatorul.

## Conexiuni — Pico

| Ce | Pin modul → Pico |
|---|---|
| **Ecran rotund GC9A01** | VCC→3V3(OUT), GND, SCL→GP18, SDA→GP19, CS→GP17, **DC→GP21**, RES→GP20, BLK→GP22 |
| **TFT ILI9341 2.4"** | VCC→VSYS (5V, are regulator pe placă; jumperul J1 rămâne deschis), GND, SCK→GP10, SDI→GP11, CS→GP13, DC→GP12, RESET→GP14, LED→GP15. SDO și pinii T_* rămân nelegați |
| **Encoder** | + → **3V3, nu 5V**, GND, CLK→GP2, DT→GP3, SW→GP4 |
| **K1 / K2 / K3** | GP5 / GP6 / GP7, celălalt capăt la GND (pull-up intern) |
| **Slider 10k** | capetele la 3V3 și AGND (pin 33), cursorul→GP26, plus 100nF de la GP26 la AGND |
| **Butonul de metal** | GP8 → buton → GND (închis = ON) |
| **J2 al Pi-ului** | GP9 → 1kΩ → baza BC547; emitorul la GND, colectorul la pinul de semnal J2 |
| **Banda LED** | GP0 → 74AHCT125 (VCC 5V, OE→GND) → 330Ω → DIN |
| *opțional* | GP1 → MOSFET AO3400 → inelul LED al butonului, dacă are |

> Față de prima schemă s-a mutat un singur fir: **DC-ul ecranului rotund e pe GP21, nu GP16**.
> GP16 e RX-ul lui SPI0, pe care biblioteca de ecran îl configurează oricum ca SPI.

La J2 verifică cu multimetrul care pin are continuitate cu GND. Celălalt e cel de semnal.
GND-ul Pico-ului, al Pi-ului și al benzii LED trebuie să fie comun.

## Conexiuni — Pi 5

| Ce | Unde |
|---|---|
| **MAX98357A** | VIN→pin 4, GND→pin 6, BCLK→pin 12, LRC→pin 35, DIN→pin 40. GAIN și SD nelegate |
| **PS3 Eye** | un port USB (4 microfoane, 16 kHz nativ; Chronos îl deschide singur pe 4 canale și îl folosește pe primul) |
| **Pico** | un port USB, doar pentru date |

## Setări pe Pi (o singură dată)

```bash
# 1. Sursa poate da 5A; fără asta Pi 5 limitează USB-ul la 600 mA.
#    POWER_OFF_ON_HALT=1: la oprire se taie și USB-ul — așa știe Pico-ul că Pi-ul e oprit.
sudo rpi-eeprom-config --edit
#    adaugă:  PSU_MAX_CURRENT=5000
#             POWER_OFF_ON_HALT=1

# 2. Boxa
echo "dtoverlay=max98357a" | sudo tee -a /boot/firmware/config.txt

# 3. Acces la portul serial al Pico-ului + pachetul Python
sudo usermod -aG dialout sergiu
source venv/bin/activate && pip install pyserial

# 4. Oprirea de la butonul de metal: Chronos rulează `sudo -n shutdown -h now`.
#    Pe Raspberry Pi OS primul utilizator are deja sudo fără parolă. Dacă nu:
echo "sergiu ALL=(root) NOPASSWD: /usr/sbin/shutdown" | sudo tee /etc/sudoers.d/chronos-shutdown

sudo reboot
```

## Butonul de metal (cu reținere)

Nu trebuie modificat. Pico-ul compară poziția lui cu starea Pi-ului:

| Butonul pe… | Pi-ul e… | Pico-ul |
|---|---|---|
| ON | oprit | apasă J2 200 ms (pornire); dacă nu pornește, reîncearcă de 3 ori |
| OFF | pornit, Chronos rulează | cere oprire curată pe USB |
| OFF | pornit, Chronos nu răspunde 30 s | dublu-apăsare pe J2 (butonul de power al Pi-ului) |
| ON | pornit | nimic |

După ce Pi-ul pare oprit, Pico-ul așteaptă 20 s înainte să-l pornească. La un
reboot USB-ul cade o clipă, iar un puls pe J2 în timpul boot-ului l-ar opri.

## Firmware-ul

```bash
cd console/firmware
pio run -t upload            # VS Code + PlatformIO; prima dată ține BOOTSEL apăsat când bagi USB-ul
```

- `src/main.cpp`: hardware-ul (pini, ecrane, LED-uri). Core 0 face meniul și USB-ul, core 1 ecranul rotund.
- `lib/logic/`: meniul, butonul de pornire, culorile. Nu depinde de Arduino și e testată pe PC:

```bash
g++ -std=c++17 -Ilib/logic test/native/logic_test.cpp lib/logic/*.cpp -o logic_test && ./logic_test
```

Dacă encoderul sare câte două rânduri la un clic sau merge invers, schimbi
`ENC_STEPS_PER_DETENT` / `ENC_REVERSE` în `include/pins.h`. La fel pentru slider (`SLIDER_REVERSE`).

## Primul test, pe breadboard

1. Doar Pico + TFT, conectat la PC: trebuie să apară „astept chronos...”.
2. Pico la Pi, Chronos pornit: în log apare `🎛️ [Consolă] Conectată`, iar pe TFT meniul.
3. Encoder + taste: navighezi în *Remindere*, iar ecranul mare arată pop-up-ul CONSOLĂ.
4. Ecranul rotund: apeși K3 și vorbești; barele urmăresc vocea.
5. Banda LED, pe sursa ei, cu GND comun.
6. La final butonul de metal și J2. Testează-l întâi cu un LED în loc de J2.

## Protocolul (USB serial, un JSON pe linie)

| Direcție | Mesaj |
|---|---|
| Pi → Pico | `{"t":"hb","h":13,"m":5,"s":"idle\|listening\|speaking","mood":"8b7aff","al":0}`, o dată pe secundă |
| Pi → Pico | `{"t":"lvl","s":"speaking","a":420}`, de 20 de ori pe secundă cât ține o sesiune vocală (a = 0…1000) |
| Pi → Pico | `{"t":"list","v":"rem","title":"REMINDERE","items":[{"id","x","d","n"}]}` · `{"t":"music",…}` · `{"t":"lights",…}` · `{"t":"ok\|err","x":"…"}` |
| Pico → Pi | `{"t":"hello","fw":"0.1"}` · `{"t":"get","v":"rem\|plan\|targets\|scenes\|music\|lights"}` |
| Pico → Pi | `{"t":"do","a":"rem_check\|plan_done\|target_set\|scene\|music\|lights\|voice\|alarm_stop\|restart\|power_off","id":"…","n":0}` |
| Pico → Pi | `{"t":"focus","v":"rem","sel":2}`: ce e selectat pe TFT, pentru ecranul mare |

Textul spre Pico pleacă fără diacritice, pentru că fontul TFT-ului e ASCII.

## Ce urmează

- **„Închide ziua”**: ritualul de seară în 5 pași (remindere → targets → jurnal dictat cu K3 ținut → scoruri cu encoderul și sliderul → ceva pentru mâine), cu streak pe ecranul rotund.
- Deblocarea jurnalului cu un PIN introdus din encoder.
- Push-to-talk adevărat pe K3 (dictare fără wake word).
