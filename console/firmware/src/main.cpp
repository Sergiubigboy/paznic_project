// main.cpp — consola Chronos pe Pico (RP2040): stratul de hardware.
//
// core 0: USB cu Pi-ul, taste/encoder/slider, meniul pe TFT, LED-uri, butonul de pornire
// core 1: ecranul rotund (vocea) — imagine întreagă în RAM, ~30 fps
//
// Toată logica (meniu, pornire, culori) e în lib/logic și e testată pe PC;
// aici doar se citesc pini și se desenează.
#include <Adafruit_NeoPixel.h>
#include <Arduino.h>
#include <ArduinoJson.h>
#include <Arduino_GFX_Library.h>
#include <math.h>

#include "ledfx.h"
#include "menu.h"
#include "pins.h"
#include "power.h"

using namespace con;

// ═══════════════════════════════════════════════════════════════
// Starea comună între core-uri (core 0 scrie, core 1 citește).
// Pentru o animație, o valoare veche de un cadru nu contează — fără lock-uri.
// ═══════════════════════════════════════════════════════════════
struct Shared {
    volatile Mood mood = Mood::PiOff;
    volatile uint16_t color565 = 0x8BFF;
    volatile uint8_t hour = 255, minute = 0;
    volatile float level = 0;
    Wave wave;
};
static Shared shared;

static constexpr uint16_t C_BLACK = 0x0000, C_WHITE = 0xFFFF, C_DIM = 0x7BEF, C_RED = 0xF800;

static uint16_t rgb565(uint8_t r, uint8_t g, uint8_t b) {
    return ((r & 0xF8) << 8) | ((g & 0xFC) << 3) | (b >> 3);
}

// ═══════════════════════════════════════════════════════════════
// CORE 0
// ═══════════════════════════════════════════════════════════════

static Arduino_DataBus* tftBus = new Arduino_RPiPicoSPI(PIN_TFT_DC, PIN_TFT_CS, PIN_TFT_SCK, PIN_TFT_MOSI,
                                                        PIN_TFT_MISO, spi1);
static Arduino_GFX* tft = new Arduino_ILI9341(tftBus, PIN_TFT_RST, 0 /* portret */, false);
static Adafruit_NeoPixel strip(LED_COUNT, PIN_LED_DATA, NEO_GRBW + NEO_KHZ800);

static Menu menu;
static Power power;
static Screen shown, next;
static bool shownValid = false;

static uint32_t lastHb = 0;
static bool everHb = false;
static bool alarmOn = false;
static char voiceState[12] = "off";
static uint32_t lastLvl = 0;
static RGBW moodRGBW{139, 122, 255, 0};

// ── encoder: decodor în cuadratură pe întreruperi ──
static volatile uint8_t encState = 0;
static volatile int32_t encAcc = 0;
static const int8_t QDEC[16] = {0, -1, 1, 0, 1, 0, 0, -1, -1, 0, 0, 1, 0, 1, -1, 0};

static void encIsr() {
    encState = ((encState << 2) | (digitalRead(PIN_ENC_A) << 1) | digitalRead(PIN_ENC_B)) & 0x0F;
    encAcc += QDEC[encState];
}

static int takeDetents() {
    noInterrupts();
    int d = encAcc / ENC_STEPS_PER_DETENT;
    encAcc -= d * ENC_STEPS_PER_DETENT;
    interrupts();
    return ENC_REVERSE ? -d : d;
}

// ── taste cu filtru de vibrații ──
struct Btn {
    uint8_t pin;
    bool down = false, raw = false, longFired = false;
    uint32_t changedAt = 0, pressedAt = 0;
};
static Btn bSel{PIN_ENC_SW}, bK1{PIN_K1}, bK2{PIN_K2}, bK3{PIN_K3};

/** 1 = tocmai apăsat, -1 = tocmai eliberat, 0 = nimic. */
static int poll(Btn& b, uint32_t now) {
    bool r = digitalRead(b.pin) == LOW;
    if (r != b.raw) { b.raw = r; b.changedAt = now; }
    if (now - b.changedAt < 25 || r == b.down) return 0;
    b.down = r;
    if (r) { b.pressedAt = now; b.longFired = false; return 1; }
    return -1;
}

static bool switchOn = false, switchRaw = false;
static uint32_t switchAt = 0;

// ── J2: pulsuri non-blocante (200 ms apăsat; dublu = 200/300/200) ──
static uint32_t j2Until = 0, j2SecondAt = 0;

static void pulseJ2(uint32_t now, bool twice) {
    digitalWrite(PIN_J2, HIGH);
    j2Until = now + 200;
    j2SecondAt = twice ? now + 500 : 0;
}

static void serviceJ2(uint32_t now) {
    if (j2Until && now >= j2Until) { digitalWrite(PIN_J2, LOW); j2Until = 0; }
    if (j2SecondAt && now >= j2SecondAt) { j2SecondAt = 0; pulseJ2(now, false); }
}

// ── USB: câte un JSON pe linie ──
static void sendJson(JsonDocument& d) {
    if (!Serial) return;
    serializeJson(d, Serial);
    Serial.write('\n');
}

static void send(const Out& out) {
    for (int i = 0; i < out.count; i++) {
        const Cmd& c = out.cmds[i];
        JsonDocument d;
        switch (c.kind) {
            case Cmd::Get: d["t"] = "get"; d["v"] = c.v; break;
            case Cmd::Focus: d["t"] = "focus"; d["v"] = c.v; d["sel"] = c.n; break;
            case Cmd::Do: d["t"] = "do"; d["a"] = c.a; d["id"] = c.id; d["n"] = c.n; break;
            default: continue;
        }
        sendJson(d);
    }
}

static void sendDo(const char* a) {
    JsonDocument d;
    d["t"] = "do";
    d["a"] = a;
    sendJson(d);
}

static Item rxItems[MAX_ITEMS];

static void onMessage(const char* line, uint32_t now) {
    JsonDocument d;
    if (deserializeJson(d, line)) return;
    const char* t = d["t"] | "";
    Out out;

    if (!strcmp(t, "hb")) {
        lastHb = now;
        everHb = true;
        menu.heartbeat(d["h"] | 0, d["m"] | 0, now);
        shared.hour = d["h"] | 0;
        shared.minute = d["m"] | 0;
        strlcpy(voiceState, d["s"] | "off", sizeof voiceState);
        alarmOn = (d["al"] | 0) != 0;
        moodRGBW = parseHex(d["mood"] | "8b7aff");
        shared.color565 = rgb565(moodRGBW.r, moodRGBW.g, moodRGBW.b);
    } else if (!strcmp(t, "lvl")) {
        lastLvl = now;
        strlcpy(voiceState, d["s"] | "idle", sizeof voiceState);
        float a = (d["a"] | 0) / 1000.0f;
        shared.level = a;
        shared.wave.push(a);
    } else if (!strcmp(t, "list")) {
        JsonArrayConst arr = d["items"].as<JsonArrayConst>();
        int n = 0;
        for (JsonObjectConst it : arr) {
            if (n >= MAX_ITEMS) break;
            Item& x = rxItems[n++];
            strlcpy(x.id, it["id"] | "", sizeof x.id);
            strlcpy(x.text, it["x"] | "", sizeof x.text);
            x.done = it["d"] | 0;
            x.num = it["n"] | 0;
        }
        menu.setList(d["v"] | "", d["title"] | "", rxItems, n, out);
    } else if (!strcmp(t, "music")) {
        menu.setMusic((d["ok"] | 0) != 0, d["track"] | "", d["artist"] | "", (d["play"] | 0) != 0, d["vol"] | -1,
                      d["pos"] | 0, d["dur"] | 0, now);
    } else if (!strcmp(t, "lights")) {
        menu.setLights((d["ok"] | 0) != 0, (d["on"] | 0) != 0, d["bri"] | 0);
    } else if (!strcmp(t, "ok") || !strcmp(t, "err")) {
        menu.message(d["x"] | "", t[0] == 'e', now);
    }
    send(out);
}

static char rxBuf[8192];
static size_t rxLen = 0;

static void readSerial(uint32_t now) {
    while (Serial.available()) {
        char c = (char)Serial.read();
        if (c == '\n') {
            rxBuf[rxLen] = '\0';
            if (rxLen) onMessage(rxBuf, now);
            rxLen = 0;
        } else if (c != '\r') {
            if (rxLen < sizeof rxBuf - 1) rxBuf[rxLen++] = c;
            else rxLen = 0;   // linie prea lungă: o aruncăm întreagă
        }
    }
}

// ── TFT: redesenăm doar rândurile care s-au schimbat ──
static constexpr int ROW_H = 20, Y_TITLE = 22, Y_ROWS = 48, Y_FOOT = 300;

static void drawText(int y, const char* text, uint16_t fg, uint16_t bg) {
    tft->fillRect(0, y, 240, ROW_H, bg);
    tft->setTextColor(fg, bg);
    tft->setCursor(0, y + 2);
    tft->print(text);
}

static void drawScreen(const Screen& s) {
    if (!shownValid || strcmp(s.header, shown.header)) drawText(0, s.header, C_BLACK, C_WHITE);
    if (!shownValid || strcmp(s.title, shown.title)) {
        drawText(Y_TITLE, s.title, C_WHITE, C_BLACK);
        tft->drawFastHLine(0, Y_TITLE + ROW_H + 2, 240, C_DIM);
    }
    for (int r = 0; r < ROWS; r++) {
        if (shownValid && s.rows[r] == shown.rows[r]) continue;
        const Line& l = s.rows[r];
        drawText(Y_ROWS + r * ROW_H, l.text, l.inv ? C_BLACK : (l.dim ? C_DIM : C_WHITE), l.inv ? C_WHITE : C_BLACK);
    }
    if (!shownValid || strcmp(s.footer, shown.footer) || s.footerErr != shown.footerErr) {
        tft->drawFastHLine(0, Y_FOOT - 4, 240, C_DIM);
        drawText(Y_FOOT, s.footer, s.footerErr ? C_RED : C_DIM, C_BLACK);
    }
    shown = s;
    shownValid = true;
}

// ── LED-uri ──
static RGBW px[LED_COUNT];

static void showLeds(Mood m, uint32_t now) {
    float lvl = shared.level;
    RGBW c = ledColor(m, now, lvl, moodRGBW, menu.ledBrightness());
    for (int i = 0; i < LED_COUNT; i++) px[i] = c;
    limitCurrent(px, LED_COUNT, LED_MAX_MA);
    for (int i = 0; i < LED_COUNT; i++) strip.setPixelColor(i, px[i].r, px[i].g, px[i].b, px[i].w);
    strip.show();
    // inelul butonului (dacă există) respiră odată cu banda
    uint8_t ring = c.r;
    for (uint8_t v : {c.g, c.b, c.w}) ring = v > ring ? v : ring;
    if (m == Mood::PiOff) ring = 0;
    analogWrite(PIN_RING, ring);
}

void setup() {
    Serial.begin(115200);

    pinMode(PIN_J2, OUTPUT);
    digitalWrite(PIN_J2, LOW);
    pinMode(PIN_POWER_SW, INPUT_PULLUP);
    pinMode(PIN_VBUS, INPUT);
    for (uint8_t p : {PIN_ENC_A, PIN_ENC_B, PIN_ENC_SW, PIN_K1, PIN_K2, PIN_K3}) pinMode(p, INPUT_PULLUP);
    attachInterrupt(digitalPinToInterrupt(PIN_ENC_A), encIsr, CHANGE);
    attachInterrupt(digitalPinToInterrupt(PIN_ENC_B), encIsr, CHANGE);
    analogReadResolution(12);
    pinMode(PIN_RING, OUTPUT);

    pinMode(PIN_TFT_BL, OUTPUT);
    digitalWrite(PIN_TFT_BL, LOW);
    tft->begin(40000000);
    tft->fillScreen(C_BLACK);
    tft->setTextSize(2);
    tft->setTextWrap(false);

    strip.begin();
    strip.show();
    menu.begin();
}

void loop() {
    static uint32_t lastInput = 0, lastLed = 0, lastTft = 0, lastSlider = 0;
    static bool wasConnected = false;
    static float sliderAvg = -1;
    const uint32_t now = millis();

    // USB: la (re)conectare ne prezentăm, ca Pi-ul să trimită starea imediat
    const bool connected = (bool)Serial;
    if (connected && !wasConnected) {
        JsonDocument d;
        d["t"] = "hello";
        d["fw"] = CONSOLE_FW;
        sendJson(d);
    }
    wasConnected = connected;
    readSerial(now);

    const bool alive = everHb && now - lastHb < 3500;
    menu.setLink(alive);
    if (now - lastLvl > 600) {      // fără nivel nou = liniște
        shared.level *= 0.85f;
        if (!alive) strlcpy(voiceState, "off", sizeof voiceState);
    }

    Out out;
    if (now - lastInput >= 5) {
        lastInput = now;
        int d = takeDetents();
        if (d) menu.turn(d, now, out);

        Key keys[4];
        int nk = 0;
        if (poll(bSel, now) == 1) keys[nk++] = Key::Select;
        if (poll(bK2, now) == 1) keys[nk++] = Key::Action;
        if (poll(bK3, now) == 1) keys[nk++] = Key::Talk;
        int k1 = poll(bK1, now);
        if (bK1.down && !bK1.longFired && now - bK1.pressedAt >= 800) {
            bK1.longFired = true;
            keys[nk++] = Key::BackLong;
        } else if (k1 == -1 && !bK1.longFired) {
            keys[nk++] = Key::Back;
        }
        for (int i = 0; i < nk; i++) {
            if (alarmOn) { sendDo("alarm_stop"); alarmOn = false; break; }   // orice tastă oprește alarma
            menu.key(keys[i], now, out);
        }

        bool r = digitalRead(PIN_POWER_SW) == LOW;
        if (r != switchRaw) { switchRaw = r; switchAt = now; }
        if (now - switchAt >= 100) switchOn = switchRaw;
    }

    if (now - lastSlider >= 40) {
        lastSlider = now;
        int raw = analogRead(PIN_SLIDER);
        sliderAvg = sliderAvg < 0 ? raw : sliderAvg * 0.8f + raw * 0.2f;
        int pct = (int)(sliderAvg * 100.0f / 4095.0f + 0.5f);
        menu.slider(SLIDER_REVERSE ? 100 - pct : pct, now, out);
    }

    menu.tick(now, out);
    send(out);

    // ── pornire / oprire ──
    const bool piPowered = digitalRead(PIN_VBUS) == HIGH || alive;
    switch (power.update({switchOn, piPowered, alive, now})) {
        case PowerAct::PulseJ2: pulseJ2(now, false); break;
        case PowerAct::DoublePulseJ2: pulseJ2(now, true); break;
        case PowerAct::SendPowerOff: sendDo("power_off"); break;
        default: break;
    }
    serviceJ2(now);

    Mood m;
    switch (power.state()) {
        case Power::Booting: m = Mood::Booting; break;
        case Power::ShuttingDown: m = Mood::ShuttingDown; break;
        case Power::On:
            if (!alive) m = Mood::NoChronos;
            else if (alarmOn) m = Mood::Alarm;
            else if (!strcmp(voiceState, "listening")) m = Mood::Listening;
            else if (!strcmp(voiceState, "speaking")) m = Mood::Speaking;
            else m = Mood::Idle;
            break;
        default: m = Mood::PiOff;
    }
    shared.mood = m;

    if (now - lastLed >= 20) {
        lastLed = now;
        showLeds(m, now);
    }

    if (now - lastTft >= 50) {
        lastTft = now;
        const bool on = m != Mood::PiOff;
        digitalWrite(PIN_TFT_BL, on ? HIGH : LOW);
        if (on) {
            menu.render(next, now);
            drawScreen(next);
        } else {
            shownValid = false;   // la pornire redesenăm tot
        }
    }
}

// ═══════════════════════════════════════════════════════════════
// CORE 1 — ecranul rotund
// ═══════════════════════════════════════════════════════════════

static Arduino_DataBus* rndBus = new Arduino_RPiPicoSPI(PIN_RND_DC, PIN_RND_CS, PIN_RND_SCK, PIN_RND_MOSI,
                                                        PIN_RND_MISO, spi0);
static Arduino_GFX* rndPanel = new Arduino_GC9A01(rndBus, PIN_RND_RST, 0, true /* IPS */);
static Arduino_Canvas* rnd = new Arduino_Canvas(240, 240, rndPanel);

static constexpr int CX = 120, CY = 120, BARS = 64;
static float cosT[BARS], sinT[BARS];

static uint16_t dimmed(uint16_t c, float k) {
    if (k < 0) k = 0;
    if (k > 1) k = 1;
    uint8_t r = ((c >> 11) & 0x1F) * k, g = ((c >> 5) & 0x3F) * k, b = (c & 0x1F) * k;
    return (r << 11) | (g << 5) | b;
}

static float breathe01(uint32_t now, uint32_t period) {
    return 0.5f - 0.5f * cosf(6.2831853f * (now % period) / (float)period);
}

/** Bare radiale: cel mai nou nivel sus, istoricul se rotește în jur. */
static void drawWave(uint16_t color, float inner, float scale) {
    for (int i = 0; i < BARS; i++) {
        float a = shared.wave.at(i * Wave::N / BARS);
        float r2 = inner + 4 + a * scale;
        rnd->drawLine(CX + cosT[i] * inner, CY + sinT[i] * inner, CX + cosT[i] * r2, CY + sinT[i] * r2, color);
    }
}

void setup1() {
    for (int i = 0; i < BARS; i++) {
        float ang = -1.5707963f + 6.2831853f * i / BARS;
        cosT[i] = cosf(ang);
        sinT[i] = sinf(ang);
    }
    pinMode(PIN_RND_BL, OUTPUT);
    analogWrite(PIN_RND_BL, 0);
    rnd->begin(62500000);
    rnd->fillScreen(C_BLACK);
    rnd->flush();
}

void loop1() {
    static Mood lastMood = Mood::PiOff;
    static uint32_t lastFrame = 0;
    const uint32_t now = millis();
    const Mood m = shared.mood;
    const bool active = m == Mood::Listening || m == Mood::Speaking || m == Mood::Alarm;
    if (now - lastFrame < (active ? 33u : 100u)) return;   // 30 fps în sesiune, 10 în repaus
    lastFrame = now;

    if (m == Mood::PiOff) {
        if (lastMood != Mood::PiOff) {
            rnd->fillScreen(C_BLACK);
            rnd->flush();
            analogWrite(PIN_RND_BL, 0);
        }
        lastMood = m;
        return;
    }
    analogWrite(PIN_RND_BL, 200);
    lastMood = m;

    const uint16_t mood = shared.color565;
    rnd->fillScreen(C_BLACK);

    switch (m) {
        case Mood::Booting:
        case Mood::NoChronos:
        case Mood::ShuttingDown: {
            // un punct care respiră: albastru = pornește, chihlimbar = Chronos lipsește, roșu = se oprește
            uint16_t c = m == Mood::Booting ? 0x041F : (m == Mood::NoChronos ? 0xFC00 : C_RED);
            rnd->fillCircle(CX, CY, 6 + 10 * breathe01(now, 2500), dimmed(c, 0.4f + 0.6f * breathe01(now, 2500)));
            break;
        }
        case Mood::Idle: {
            float b = 0.35f + 0.65f * breathe01(now, 6000);
            rnd->drawCircle(CX, CY, 104, dimmed(mood, b));
            rnd->drawCircle(CX, CY, 105, dimmed(mood, b * 0.6f));
            if (shared.hour < 24) {
                char hm[6];
                snprintf(hm, sizeof hm, "%02d:%02d", shared.hour, shared.minute);
                rnd->setTextSize(5);
                rnd->setTextColor(C_WHITE);
                rnd->setCursor(CX - 75, CY - 20);   // 5 caractere × 30 px
                rnd->print(hm);
            }
            break;
        }
        case Mood::Listening:
            drawWave(mood, 70, 45);
            rnd->fillCircle(CX, CY, 30 + shared.level * 25, dimmed(mood, 0.5f + 0.5f * shared.level));
            break;
        case Mood::Speaking:
            drawWave(C_WHITE, 70, 45);
            rnd->fillCircle(CX, CY, 30 + shared.level * 30, C_WHITE);
            break;
        case Mood::Alarm:
            if ((now / 400) % 2) rnd->fillScreen(C_RED);
            rnd->setTextSize(6);
            rnd->setTextColor(C_WHITE);
            rnd->setCursor(CX - 18, CY - 24);
            rnd->print("!!");
            break;
        default:
            break;
    }
    rnd->flush();
}
