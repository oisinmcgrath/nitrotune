#!/bin/bash
# =============================================================================
# try-predator-v4.sh
# Test whether acer_wmi with predator_v4=1 exposes the platform_profile
# interface on the Acer Nitro AN515-55.
#
# Run as root. This temporarily reloads the acer_wmi module.
# If it works, it will create /sys/firmware/acpi/platform_profile
# and we can set thermal profiles natively without any kernel module build.
# =============================================================================

set -euo pipefail

PLATFORM_PROFILE="/sys/firmware/acpi/platform_profile"
PLATFORM_CHOICES="/sys/firmware/acpi/platform_profile_choices"

if [[ $EUID -ne 0 ]]; then
    echo "ERROR: Must run as root (sudo $0)"
    exit 1
fi

echo "=== Acer Nitro AN515-55 predator_v4 test ==="
echo ""
echo "Current state:"
echo "  predator_v4 param: $(cat /sys/module/acer_wmi/parameters/predator_v4 2>/dev/null || echo 'N/A')"
echo "  platform_profile:  $(test -f $PLATFORM_PROFILE && cat $PLATFORM_PROFILE || echo 'NOT PRESENT')"
echo ""

echo "--- Step 1: Unloading acer_wmi ---"
rmmod acer_wmi 2>/dev/null && echo "Unloaded OK" || echo "NOTE: rmmod returned error (possibly in use by input device — OK to continue)"

sleep 1

echo "--- Step 2: Reloading with predator_v4=1 ---"
modprobe acer_wmi predator_v4=1
echo "Module loaded."
sleep 2

echo ""
echo "=== Results ==="

if [[ -f "$PLATFORM_PROFILE" ]]; then
    echo "SUCCESS: /sys/firmware/acpi/platform_profile EXISTS"
    echo ""
    echo "Available profiles: $(cat $PLATFORM_CHOICES 2>/dev/null)"
    echo "Current profile:    $(cat $PLATFORM_PROFILE 2>/dev/null)"
    echo ""
    echo "--- Setting to 'balanced' ---"
    echo "balanced" > "$PLATFORM_PROFILE" && echo "Set to balanced OK" || echo "FAILED to set balanced"
    echo "Current profile now: $(cat $PLATFORM_PROFILE)"
    echo ""
    echo "SUCCESS: predator_v4=1 works on this hardware."
    echo ""
    echo "To make permanent, run:"
    echo "  sudo cp /dev/stdin /etc/modprobe.d/acer-wmi-gaming.conf << 'EOF'"
    echo "  options acer_wmi predator_v4=1"
    echo "  EOF"
    echo ""
    echo "Then install and enable acer-profile.service:"
    echo "  sudo cp acer-profile.py /usr/local/bin/acer-profile"
    echo "  sudo chmod +x /usr/local/bin/acer-profile"
    echo "  sudo cp acer-profile.service /etc/systemd/system/"
    echo "  sudo systemctl daemon-reload"
    echo "  sudo systemctl enable --now acer-profile"
else
    echo "FAILED: /sys/firmware/acpi/platform_profile still does not exist."
    echo ""
    echo "This means the BIOS WMI call failed even with predator_v4=1."
    echo "The AN515-55 BIOS (V2.06 08/19/2021) may not support the v4 gaming WMI interface."
    echo ""
    echo "Check dmesg for clues:"
    echo "  sudo dmesg | grep -i 'acer\\|wmi\\|gaming\\|profile' | tail -20"
    echo ""
    echo "Fallback: use acer-profile.py with --ec-auto mode"
fi
