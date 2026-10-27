#!/usr/bin/env bash
# pi/install_screen.sh — o singură dată pe Pi: ecranul pornește singur cu Chronos
# ===============================================================================
# Rulează-l ca utilizatorul tău normal (NU cu sudo în față); cere singur sudo
# unde e nevoie:
#
#     bash pi/install_screen.sh
#
# Ce face:
#   1. instalează Chromium, fontul de emoji și utilitarele kiosk-ului
#   2. pornește Pi-ul direct în desktop, logat automat (fără tastatură)
#   3. oprește stingerea ecranului de către sistem
#   4. pune kiosk-ul în autostart (~/.config/autostart/chronos-screen.desktop)
#   5. lasă Chronos să regleze lumina de fundal a ecranului (dacă are)
#
# Totul e reversibil: șterge fișierul din ~/.config/autostart și gata.

set -euo pipefail

if [ "$(id -u)" -eq 0 ]; then
    echo "❌ Rulează-l fără sudo: bash pi/install_screen.sh" >&2
    exit 1
fi

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
KIOSK="$DIR/pi/kiosk.sh"
ME="$(id -un)"

echo "── 1/5  Pachete"
sudo apt-get update -qq
if ! command -v chromium >/dev/null 2>&1 && ! command -v chromium-browser >/dev/null 2>&1; then
    sudo apt-get install -y chromium || sudo apt-get install -y chromium-browser
fi
sudo apt-get install -y curl fonts-noto-color-emoji unclutter || true

echo "── 2/5  Pornire direct în desktop, cu autologin"
if command -v raspi-config >/dev/null 2>&1; then
    sudo raspi-config nonint do_boot_behaviour B4 || echo "   ⚠️  Setează manual: raspi-config → System → Boot / Auto Login → Desktop Autologin"
else
    echo "   ⚠️  raspi-config lipsește — activează manual autologin-ul în desktop."
fi

echo "── 3/5  Fără stingerea ecranului de către sistem"
if command -v raspi-config >/dev/null 2>&1; then
    # 1 = blanking DEZACTIVAT. Noaptea o face pagina (ceas estompat + lumină de fundal la minim).
    sudo raspi-config nonint do_blanking 1 || true
fi

echo "── 4/5  Autostart"
chmod +x "$KIOSK"
mkdir -p "$HOME/.config/autostart"
cat > "$HOME/.config/autostart/chronos-screen.desktop" <<EOF
[Desktop Entry]
Type=Application
Name=Chronos Screen
Comment=Ecranul lui Chronos în kiosk
Exec=$KIOSK
X-GNOME-Autostart-enabled=true
EOF
echo "   ✅ $HOME/.config/autostart/chronos-screen.desktop"

echo "── 5/5  Lumina de fundal (touchscreen-urile oficiale Raspberry Pi)"
# Fișierul brightness e scris doar de root. Regula udev îl dă grupului video,
# din care face parte utilizatorul — serviciul chronos (User=$ME) îl poate regla.
sudo tee /etc/udev/rules.d/99-chronos-backlight.rules >/dev/null <<'EOF'
SUBSYSTEM=="backlight", RUN+="/bin/chgrp video /sys/class/backlight/%k/brightness", RUN+="/bin/chmod g+w /sys/class/backlight/%k/brightness"
EOF
sudo usermod -aG video "$ME"
sudo udevadm control --reload-rules
sudo udevadm trigger --subsystem-match=backlight || true
if ls /sys/class/backlight/* >/dev/null 2>&1; then
    echo "   ✅ Ecran cu lumină de fundal reglabilă: $(ls /sys/class/backlight | tr '\n' ' ')"
else
    echo "   ℹ️  Ecranul n-are lumină de fundal reglabilă (ex. monitor HDMI) — noaptea"
    echo "      pagina doar se întunecă, fără să stingă lumina monitorului."
fi

echo
echo "✅ Gata. Repornește Pi-ul:  sudo reboot"
echo "   După boot: desktop → Chronos pornește → ecranul se deschide singur."
echo "   Test fără reboot (din desktopul Pi-ului):  $KIOSK"
