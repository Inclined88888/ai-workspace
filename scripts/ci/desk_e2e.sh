#!/usr/bin/env bash
# Rooks Desk on a real X11 desktop: Xvfb + xfwm4 + a D-Bus session (for AT-SPI), then the GUI tests drive Mousepad and
# xfce4-terminal with real keyboard and mouse events. Uses the scripted model unless ROOKS_DESK_E2E_MODEL is set.
set -euo pipefail
trap 'kill $(jobs -p) 2>/dev/null || true' EXIT
cd "$(dirname "$0")/../../rooks-desk"
export DISPLAY=:99
# 1600x1000: GTK file dialogs need room (on 1280x800 the Save button is off-screen)
Xvfb :99 -screen 0 1600x1000x24 >/tmp/xvfb.log 2>&1 &
sleep 1
eval "$(dbus-launch --sh-syntax)"
export NO_AT_BRIDGE=0 GTK_MODULES=gail:atk-bridge
/usr/libexec/at-spi-bus-launcher --launch-immediately >/dev/null 2>&1 &
xfwm4 --compositor=off >/tmp/xfwm4.log 2>&1 &
sleep 2
ROOKS_DESK_E2E=1 ROOKS_DESK_E2E_SHOTS="${ROOKS_DESK_E2E_SHOTS:-/tmp/desk-shots}" python -m pytest -q tests/test_e2e_linux.py -rs
