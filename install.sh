#!/bin/bash
# =============================================================================
# Installer for Acer Nitro AN515-55 Fan Control
# Run as root: sudo bash install.sh
# =============================================================================

set -euo pipefail

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

echo -e "${GREEN}=== Acer Nitro AN515-55 Fan Control Installer ===${NC}"
echo ""

# Check root
if [[ $EUID -ne 0 ]]; then
    echo -e "${RED}ERROR: Run as root (sudo bash install.sh)${NC}"
    exit 1
fi

# Check ec_probe exists
EC_PROBE=""
for candidate in /opt/nbfc/bin/ec_probe /usr/bin/ec_probe /usr/local/bin/ec_probe; do
    if [[ -x "$candidate" ]]; then
        EC_PROBE="$candidate"
        break
    fi
done

if [[ -z "$EC_PROBE" ]]; then
    echo -e "${RED}ERROR: ec_probe not found. Is nbfc-linux installed?${NC}"
    exit 1
fi
echo -e "${GREEN}✓ Found ec_probe at $EC_PROBE${NC}"

# Check nvidia-smi exists
if ! command -v nvidia-smi &>/dev/null; then
    echo -e "${RED}ERROR: nvidia-smi not found. NVIDIA driver installed?${NC}"
    exit 1
fi
echo -e "${GREEN}✓ Found nvidia-smi${NC}"

# Quick sanity test — read CPU temp
echo -n "Testing EC probe CPU temp read... "
CPU_TEMP=$("$EC_PROBE" read 176 2>/dev/null | awk '{print $1}') || CPU_TEMP=""
if [[ -z "$CPU_TEMP" || "$CPU_TEMP" -lt 20 || "$CPU_TEMP" -gt 110 ]]; then
    echo -e "${RED}FAILED (got: '${CPU_TEMP}')${NC}"
    echo "EC probe may not be working correctly. Aborting."
    exit 1
fi
echo -e "${GREEN}${CPU_TEMP}°C — looks good${NC}"

# Quick sanity test — read GPU temp
echo -n "Testing nvidia-smi GPU temp read... "
GPU_TEMP=$(nvidia-smi --query-gpu=temperature.gpu --format=csv,noheader 2>/dev/null) || GPU_TEMP=""
if [[ -z "$GPU_TEMP" || "$GPU_TEMP" -lt 20 || "$GPU_TEMP" -gt 110 ]]; then
    echo -e "${RED}FAILED (got: '${GPU_TEMP}')${NC}"
    echo "nvidia-smi may not be working correctly. Aborting."
    exit 1
fi
echo -e "${GREEN}${GPU_TEMP}°C — looks good${NC}"

echo ""

# Stop and disable existing NBFC service if running
if systemctl is-active --quiet nbfc_service 2>/dev/null; then
    echo -e "${YELLOW}Stopping existing nbfc_service...${NC}"
    systemctl stop nbfc_service
    systemctl disable nbfc_service
    echo -e "${GREEN}✓ Stopped and disabled nbfc_service${NC}"
elif systemctl is-active --quiet nbfc 2>/dev/null; then
    echo -e "${YELLOW}Stopping existing nbfc service...${NC}"
    systemctl stop nbfc
    systemctl disable nbfc
    echo -e "${GREEN}✓ Stopped and disabled nbfc${NC}"
else
    echo "No existing NBFC service running."
fi

# Install files
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

echo "Installing fan control script..."
cp "$SCRIPT_DIR/nitro-fan-control.sh" /usr/local/bin/nitro-fan-control.sh
chmod 755 /usr/local/bin/nitro-fan-control.sh

echo "Installing systemd service..."
cp "$SCRIPT_DIR/nitro-fan-control.service" /etc/systemd/system/nitro-fan-control.service
systemctl daemon-reload

# Create log file
touch /var/log/nitro-fan-control.log
chmod 644 /var/log/nitro-fan-control.log

echo ""
echo -e "${GREEN}=== Installation complete ===${NC}"
echo ""
echo "Commands:"
echo "  sudo systemctl start nitro-fan-control     # Start the service"
echo "  sudo systemctl enable nitro-fan-control     # Start on boot"
echo "  sudo systemctl status nitro-fan-control     # Check status"
echo "  sudo systemctl stop nitro-fan-control       # Stop (restores auto mode)"
echo "  tail -f /var/log/nitro-fan-control.log      # Watch the log"
echo ""
echo -e "${YELLOW}Recommended: Start it first and monitor for a few minutes:${NC}"
echo "  sudo systemctl start nitro-fan-control"
echo "  tail -f /var/log/nitro-fan-control.log"
echo ""
echo "If everything looks good, enable on boot:"
echo "  sudo systemctl enable nitro-fan-control"
echo ""
echo -e "${YELLOW}SAFETY: If anything goes wrong, the service restores auto fan mode on stop/crash.${NC}"
echo -e "${YELLOW}You can always manually restore auto mode:${NC}"
echo "  sudo $EC_PROBE write 34 4    # CPU fan auto"
echo "  sudo $EC_PROBE write 33 16   # GPU fan auto"
