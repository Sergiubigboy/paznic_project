#include "power.h"

namespace con {

PowerAct Power::update(const PowerIn& in) {
    const bool flippedOn = in.switchOn && !lastSwitch_;
    lastSwitch_ = in.switchOn;
    const uint32_t inState = in.now - since_;

    switch (st_) {
        case Off:
            if (in.piPowered) { tries_ = 0; go(On, in.now); return PowerAct::None; }
            if (in.switchOn && inState >= OFF_GRACE_MS) {
                tries_ = 1;
                go(Booting, in.now);
                return PowerAct::PulseJ2;
            }
            return PowerAct::None;

        case Booting:
            if (in.piPowered) { tries_ = 0; go(On, in.now); return PowerAct::None; }
            if (!in.switchOn) { go(Off, in.now); return PowerAct::None; }
            if (inState >= BOOT_TIMEOUT_MS) {
                if (tries_ >= MAX_BOOT_TRIES) { go(Fault, in.now); return PowerAct::None; }
                tries_++;
                go(Booting, in.now);
                return PowerAct::PulseJ2;
            }
            return PowerAct::None;

        case On:
            if (!in.piPowered) { go(Off, in.now); return PowerAct::None; }
            if (in.switchOn) { offWaitFrom_ = 0; return PowerAct::None; }
            if (in.chronosAlive) {
                forced_ = false;
                go(ShuttingDown, in.now);
                return PowerAct::SendPowerOff;
            }
            // Butonul e pe OFF dar Chronos nu răspunde (pornește încă sau a căzut):
            // îi dăm o șansă, apoi oprim prin butonul de power al Pi-ului.
            if (!offWaitFrom_) offWaitFrom_ = in.now ? in.now : 1;
            if (in.now - offWaitFrom_ >= NO_CHRONOS_MS) {
                offWaitFrom_ = 0;
                forced_ = true;
                go(ShuttingDown, in.now);
                return PowerAct::DoublePulseJ2;
            }
            return PowerAct::None;

        case ShuttingDown:
            if (!in.piPowered) { go(Off, in.now); return PowerAct::None; }
            if (inState >= SHUTDOWN_TIMEOUT_MS && !forced_) {
                forced_ = true;
                go(ShuttingDown, in.now);
                return PowerAct::DoublePulseJ2;
            }
            return PowerAct::None;

        case Fault:
            // Pi-ul n-a pornit după 3 încercări. Comuți OFF → ON ca să reîncerci.
            if (in.piPowered) { tries_ = 0; go(On, in.now); return PowerAct::None; }
            if (flippedOn) { tries_ = 1; go(Booting, in.now); return PowerAct::PulseJ2; }
            return PowerAct::None;
    }
    return PowerAct::None;
}

}  // namespace con
