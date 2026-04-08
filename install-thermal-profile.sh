#!/bin/bash
# =============================================================================
# install-thermal-profile.sh
# Sets up WMI thermal profile control on Acer Nitro AN515-55
# =============================================================================
set -euo pipefail

if [[ $EUID -ne 0 ]]; then
    echo "Run as root: sudo $0"
    exit 1
fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# --- Step 1: Test if predator_v4=1 works ---

echo "=== Step 1: Testing predator_v4=1 ==="
bash "$SCRIPT_DIR/try-predator-v4.sh" || {
    echo ""
    echo "ABORT: predator_v4 test failed. See output above."
    echo "The BIOS may not support the v4 WMI gaming interface."
    exit 1
}

PLATFORM_PROFILE="/sys/firmware/acpi/platform_profile"
if [[ ! -f "$PLATFORM_PROFILE" ]]; then
    echo "ABORT: platform_profile still missing after test. Cannot continue."
    exit 1
fi

# --- Step 2: Make predator_v4=1 permanent ---

echo ""
echo "=== Step 2: Making predator_v4=1 permanent ==="
echo "options acer_wmi predator_v4=1" > /etc/modprobe.d/acer-wmi-gaming.conf
echo "Written: /etc/modprobe.d/acer-wmi-gaming.conf"

# Update initramfs so the parameter persists across reboots
update-initramfs -u 2>&1 | tail -3 || echo "(update-initramfs skipped)"

# --- Step 3: Install acer-profile script ---

echo ""
echo "=== Step 3: Installing acer-profile ==="
cp "$SCRIPT_DIR/acer-profile.py" /usr/local/bin/acer-profile
chmod +x /usr/local/bin/acer-profile
echo "Installed: /usr/local/bin/acer-profile"

# --- Step 4: Install and enable systemd service ---

echo ""
echo "=== Step 4: Installing systemd service ==="
cp "$SCRIPT_DIR/acer-profile.service" /etc/systemd/system/
systemctl daemon-reload
systemctl enable --now acer-profile
echo "Service enabled: acer-profile.service"

# --- Step 5: Stop nitro-fan-control if it's running ---

echo ""
echo "=== Step 5: Checking nitro-fan-control service ==="
if systemctl is-active --quiet nitro-fan-control 2>/dev/null; then
    echo "Stopping nitro-fan-control (WMI profile will handle fan control now)..."
    systemctl stop nitro-fan-control
    systemctl disable nitro-fan-control
    echo "Stopped and disabled nitro-fan-control."
else
    echo "nitro-fan-control is not running. Good."
fi

# --- Done ---

echo ""
echo "=== Done ==="
echo ""
echo "Current thermal profile: $(cat $PLATFORM_PROFILE 2>/dev/null)"
echo "Available profiles:      $(cat /sys/firmware/acpi/platform_profile_choices 2>/dev/null)"
echo ""
echo "Usage:"
echo "  sudo acer-profile get           — show current profile"
echo "  sudo acer-profile set balanced  — quiet, firmware manages fans"
echo "  sudo acer-profile set silent    — minimal fans (check if BIOS supports)"
echo "  sudo acer-profile set turbo     — maximum performance"
echo "  sudo acer-profile status        — full diagnostic"
echo ""
echo "The BIOS will now manage fan speeds via its own internal curves."
echo "On idle (balanced), fans should behave as they did with NitroSense Auto."
