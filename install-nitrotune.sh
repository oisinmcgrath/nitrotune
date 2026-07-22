#!/bin/bash
# SPDX-License-Identifier: GPL-2.0-only
#
# install-nitrotune.sh — NitroTune installer
# Copyright (C) 2026 Oisin McGrath
#
# This program is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation; version 2 of the License only.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program. If not, see <https://www.gnu.org/licenses/>.
#
# Installs the Acer Nitro AN515-55 sensor monitor and sets up autostart.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
USER_NAME="${SUDO_USER:-$USER}"

echo "=== NitroTune Installer ==="
echo ""

if [[ ! -f /sys/firmware/acpi/platform_profile ]]; then
    echo "WARNING: /sys/firmware/acpi/platform_profile not found."
    echo "  The thermal-profile readout will show '—'. CPU/GPU/RAM/battery"
    echo "  readouts are unaffected."
fi

# ── 1. Install the script ─────────────────────────────────────────────────────
echo "Installing /usr/local/bin/nitrotune..."
install -m 755 "$SCRIPT_DIR/nitrotune.py" /usr/local/bin/nitrotune
echo "  Done."

# ── 2. Install .desktop for application menu ──────────────────────────────────
echo "Installing application menu entry..."
install -m 644 "$SCRIPT_DIR/nitrotune.desktop" /usr/share/applications/
echo "  Done."

# ── 3. Autostart for the current user ────────────────────────────────────────
AUTOSTART_DIR="$(getent passwd "$USER_NAME" | cut -d: -f6)/.config/autostart"
mkdir -p "$AUTOSTART_DIR"
install -m 644 "$SCRIPT_DIR/nitrotune.desktop" "$AUTOSTART_DIR/"
chown "${USER_NAME}:" "$AUTOSTART_DIR/nitrotune.desktop"
echo "  Autostart installed for user '${USER_NAME}'."

echo ""
echo "=== Done ==="
echo ""
echo "Launch now:  nitrotune &"
echo "Or find it in your application menu as 'NitroTune'."
echo "It will autostart on next login."
