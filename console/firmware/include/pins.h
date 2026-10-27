// pins.h — cum e legată consola la Pico (RP2040). Schema completă: console/README.md
#pragma once

// ── Ecranul rotund GC9A01 240×240 — SPI0, desenat pe core 1 ──
#define PIN_RND_SCK   18
#define PIN_RND_MOSI  19
#define PIN_RND_MISO  16   // nelegat; SPI0 îl vrea configurat oricum
#define PIN_RND_CS    17
#define PIN_RND_DC    21
#define PIN_RND_RST   20
#define PIN_RND_BL    22   // PWM

// ── TFT ILI9341 2.4" 240×320 — SPI1, meniul ──
#define PIN_TFT_SCK   10
#define PIN_TFT_MOSI  11
#define PIN_TFT_MISO  28   // nelegat; SPI1 îl vrea configurat oricum
#define PIN_TFT_CS    13
#define PIN_TFT_DC    12
#define PIN_TFT_RST   14
#define PIN_TFT_BL    15

// ── Encoder (modul cu 5 pini: CLK, DT, SW, +, GND) — + la 3V3, NU la 5V ──
#define PIN_ENC_A     2    // CLK
#define PIN_ENC_B     3    // DT
#define PIN_ENC_SW    4
#define ENC_STEPS_PER_DETENT 4   // 2 dacă la un „clic" sare câte două rânduri
#define ENC_REVERSE   0          // 1 dacă merge invers

// ── Tastele (la GND, pull-up intern) ──
#define PIN_K1        5    // înapoi (ținut apăsat = meniul principal)
#define PIN_K2        6    // acțiune (bifează, următoarea piesă, aprinde/stinge)
#define PIN_K3        7    // vorbește cu Chronos

#define PIN_SLIDER    26   // ADC0; capetele potențiometrului la 3V3 și AGND
#define SLIDER_REVERSE 0

// ── Pornire ──
#define PIN_POWER_SW  8    // butonul de metal cu reținere, la GND = ON
#define PIN_J2        9    // → 1k → baza BC547; colectorul pe pinul de semnal J2 al Pi-ului
#define PIN_VBUS      24   // pe Pico: HIGH când portul USB al Pi-ului dă tensiune
#define PIN_RING      1    // opțional: inelul LED al butonului, printr-un MOSFET (PWM)

// ── Banda RGBW (SK6812) — prin 74AHCT125 la 5V ──
#define PIN_LED_DATA  0
#define LED_COUNT     36   // 60 cm × 60 LED/m
#define LED_MAX_MA    1200 // plafon software pe toată banda
