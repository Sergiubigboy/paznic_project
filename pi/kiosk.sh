#!/usr/bin/env bash
# pi/kiosk.sh — ecranul lui Chronos pe tot monitorul Pi-ului
# =============================================================
# Pornit automat la login de ~/.config/autostart/chronos-screen.desktop
# (îl pune pi/install_screen.sh). Merge pe Raspberry Pi OS Bookworm/Trixie,
# pe Wayland (labwc / wayfire) și pe X11.
#
# Ieșire din kiosk:  pkill -f kiosk.sh && pkill chromium
# Alt URL (ex. Chronos pe alt aparat):  CHRONOS_SCREEN_URL=http://pi.local:5000/screen?kiosk

set -u

URL="${CHRONOS_SCREEN_URL:-http://localhost:5000/screen?kiosk}"
PING="${URL%%/screen*}/api/terminal/ping"
PROFILE="$HOME/.config/chronos-kiosk"

# O singură instanță — autostart-ul XDG și o pornire de mână nu se dublează.
exec 9>"${XDG_RUNTIME_DIR:-/tmp}/chronos-kiosk.lock"
flock -n 9 || { echo "Kiosk-ul rulează deja."; exit 0; }

BROWSER=""
for b in chromium chromium-browser; do
    if command -v "$b" >/dev/null 2>&1; then BROWSER="$b"; break; fi
done
if [ -z "$BROWSER" ]; then
    echo "❌ Chromium lipsește. Rulează: sudo apt install chromium" >&2
    exit 1
fi

# Desktopul e gata în câteva secunde, Chronos în ~30-60s (modele, Gemini).
# Fără așteptare, Chromium ar afișa o pagină de eroare care nu se mai reîncarcă.
# Pagina are oricum propriul ecran „Aștept Chronos…" pentru restarturi ulterioare.
for _ in $(seq 1 120); do
    curl -fsS -o /dev/null --max-time 2 "$PING" && break
    sleep 2
done

# X11: fără screensaver și fără DPMS — noaptea o gestionează pagina însăși.
# (Pe Wayland, blanking-ul se oprește din raspi-config; vezi install_screen.sh.)
if [ -n "${DISPLAY:-}" ] && [ -z "${WAYLAND_DISPLAY:-}" ] && command -v xset >/dev/null 2>&1; then
    xset s off -dpms s noblank 2>/dev/null
    command -v unclutter >/dev/null 2>&1 && unclutter -idle 0.5 -root &
fi

FLAGS=(
    --kiosk
    --noerrdialogs
    --disable-infobars
    --no-first-run
    --disable-session-crashed-bubble
    --disable-features=Translate,TranslateUI
    --overscroll-history-navigation=0
    --disable-pinch
    --check-for-update-interval=31536000
    --password-store=basic
    --ozone-platform-hint=auto
    --user-data-dir="$PROFILE"
)

# Dacă Chromium cade (pană de memorie, crash), îl repornim.
while true; do
    # După o oprire bruscă (curent picat) Chromium ar întreba „Restore pages?".
    PREFS="$PROFILE/Default/Preferences"
    if [ -f "$PREFS" ]; then
        sed -i 's/"exited_cleanly":false/"exited_cleanly":true/; s/"exit_type":"[^"]*"/"exit_type":"Normal"/' "$PREFS"
    fi
    "$BROWSER" "${FLAGS[@]}" "$URL"
    sleep 3
done
