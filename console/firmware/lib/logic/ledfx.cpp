#include "ledfx.h"

#include <math.h>
#include <stdlib.h>

namespace con {

namespace {

constexpr float PI_F = 3.14159265f;

/** 0..1, sinus lent cu perioada dată — „respirația". */
float breathe(uint32_t now, uint32_t periodMs) {
    return 0.5f - 0.5f * cosf(2.0f * PI_F * (float)(now % periodMs) / (float)periodMs);
}

uint8_t sc(float v) { return (uint8_t)(v < 0 ? 0 : (v > 255 ? 255 : v + 0.5f)); }

RGBW scale(RGBW c, float k) { return {sc(c.r * k), sc(c.g * k), sc(c.b * k), sc(c.w * k)}; }

}  // namespace

RGBW ledColor(Mood m, uint32_t now, float level, RGBW mood, int brightnessPct) {
    float k = (brightnessPct < 0 ? 0 : brightnessPct > 100 ? 100 : brightnessPct) / 100.0f;
    if (level < 0) level = 0;
    if (level > 1) level = 1;
    RGBW c{0, 0, 0, 0};
    switch (m) {
        case Mood::PiOff: return c;
        case Mood::Booting: c = scale({0, 40, 255, 0}, 0.15f + 0.6f * breathe(now, 2000)); break;
        case Mood::NoChronos: c = scale({255, 120, 0, 0}, 0.1f + 0.4f * breathe(now, 3000)); break;
        case Mood::Idle: c = {0, 0, 0, 40}; break;   // alb cald, slab
        case Mood::Listening: c = scale(mood, 0.25f + 0.75f * level); break;
        case Mood::Speaking: c = scale({0, 0, 0, 255}, 0.2f + 0.8f * level); break;
        case Mood::Alarm: c = (now / 400) % 2 ? RGBW{255, 0, 0, 0} : RGBW{40, 0, 0, 0}; k = 1; break;
        case Mood::ShuttingDown: c = scale({255, 0, 0, 0}, 0.05f + 0.25f * breathe(now, 1500)); break;
    }
    return scale(c, k);
}

float limitCurrent(RGBW* px, int n, int maxMilliamps) {
    long sum = 0;   // în unități de canal: 255 ≈ 20 mA
    for (int i = 0; i < n; i++) sum += px[i].r + px[i].g + px[i].b + px[i].w;
    float ma = sum * 20.0f / 255.0f;
    if (ma <= maxMilliamps || ma <= 0) return 1.0f;
    float k = maxMilliamps / ma;
    for (int i = 0; i < n; i++) px[i] = scale(px[i], k);
    return k;
}

RGBW parseHex(const char* hex) {
    if (!hex) return {139, 122, 255, 0};
    if (*hex == '#') hex++;
    char* end = nullptr;
    unsigned long v = strtoul(hex, &end, 16);
    if (end - hex != 6) return {139, 122, 255, 0};
    return {(uint8_t)(v >> 16), (uint8_t)(v >> 8), (uint8_t)v, 0};
}

void Wave::push(float a) {
    v_[head_] = a < 0 ? 0 : (a > 1 ? 1 : a);
    head_ = (head_ + 1) % N;
}

float Wave::at(int i) const { return v_[(head_ + i) % N]; }

void Wave::decay(float k) {
    for (float& x : v_) x *= k;
}

}  // namespace con
