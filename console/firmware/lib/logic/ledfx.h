// ledfx.h — banda RGBW și ecranul rotund: ce culoare / ce formă la fiecare stare.
#pragma once
#include <stdint.h>

namespace con {

struct RGBW { uint8_t r, g, b, w; };

enum class Mood : uint8_t {
    PiOff,        // totul stins
    Booting,      // Pi-ul pornește
    NoChronos,    // Pi pornit, Chronos nu răspunde
    Idle,
    Listening,
    Speaking,
    Alarm,
    ShuttingDown,
};

/** Culoarea benzii acum. `level` 0..1 = nivelul vocii, `mood` = culoarea lui Chronos. */
RGBW ledColor(Mood m, uint32_t now, float level, RGBW mood, int brightnessPct);

/** Scade toată banda proporțional ca să nu treacă de `maxMilliamps`
 *  (~20 mA pe canal la 255 pe LED-urile SK6812 RGBW). Întoarce factorul aplicat. */
float limitCurrent(RGBW* px, int n, int maxMilliamps);

RGBW parseHex(const char* hex);   // "8b7aff" → {139,122,255,0}

/** Istoricul nivelului vocii pentru waveform-ul de pe ecranul rotund. */
class Wave {
public:
    static constexpr int N = 64;
    void push(float a);
    float at(int i) const;           // 0 = cel mai vechi, N-1 = cel mai nou
    void decay(float k);             // între sesiuni, waveform-ul se stinge lin
private:
    float v_[N] = {};
    int head_ = 0;
};

}  // namespace con
