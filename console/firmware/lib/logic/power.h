// power.h — butonul de metal (cu reținere) ca întrerupător inteligent pentru Pi.
//
// Pico-ul e alimentat mereu (din pinii de 5V ai Pi-ului, care rămân sub tensiune
// și cu Pi-ul oprit). El citește poziția butonului și:
//   ON  cu Pi-ul oprit  → apasă J2 (pornire) printr-un tranzistor
//   OFF cu Pi-ul pornit → cere oprire curată pe USB; dacă Chronos nu răspunde,
//                         dublu-apăsare pe J2 (butonul de power al Pi-ului)
// „Pi pornit" = portul USB al Pi-ului dă tensiune (VBUS) — vezi console/README.md
// pentru POWER_OFF_ON_HALT=1, fără de care VBUS poate rămâne aprins după oprire.
#pragma once
#include <stdint.h>

namespace con {

struct PowerIn {
    bool switchOn;       // poziția butonului, deja filtrată de vibrații
    bool piPowered;      // VBUS de la Pi prezent
    bool chronosAlive;   // heartbeat de la Chronos în ultimele secunde
    uint32_t now;
};

enum class PowerAct : uint8_t { None, PulseJ2, DoublePulseJ2, SendPowerOff };

class Power {
public:
    enum State : uint8_t { Off, Booting, On, ShuttingDown, Fault };

    // Cât așteptăm după ce Pi-ul pare oprit înainte să-l pornim: la reboot
    // VBUS cade o clipă, iar un puls pe J2 în timpul boot-ului l-ar opri.
    static constexpr uint32_t OFF_GRACE_MS = 20000;
    static constexpr uint32_t BOOT_TIMEOUT_MS = 60000;
    static constexpr uint32_t NO_CHRONOS_MS = 30000;   // OFF + Chronos mort → J2
    static constexpr uint32_t SHUTDOWN_TIMEOUT_MS = 90000;
    static constexpr int MAX_BOOT_TRIES = 3;

    PowerAct update(const PowerIn& in);
    State state() const { return st_; }

private:
    void go(State s, uint32_t now) { st_ = s; since_ = now; }

    State st_ = Off;
    uint32_t since_ = 0;          // intrarea în starea curentă
    uint32_t offWaitFrom_ = 0;    // de când așteptăm Chronos cu butonul pe OFF
    int tries_ = 0;
    bool lastSwitch_ = false;
    bool forced_ = false;         // am dat deja dublu-puls la oprire
};

}  // namespace con
