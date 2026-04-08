#!/usr/bin/env python3
"""
acer-profile — Acer Nitro AN515-55 Thermal Profile Manager
===========================================================

Controls thermal profiles (Silent/Balanced/Performance/Turbo) via the
standard Linux platform_profile sysfs interface, which is exposed by the
acer_wmi driver when loaded with predator_v4=1.

WMI Background (what happens under the hood):
  Writing to /sys/firmware/acpi/platform_profile triggers acer_wmi to call
  WMI method 22 (ACER_WMID_SET_GAMING_MISC_SETTING) on GUID
  7A4DDFE7-5B5D-40B4-8595-4408E0CC7F56 with field_id=0x000B and the
  corresponding profile byte:
    0x00 = quiet/silent
    0x01 = balanced
    0x04 = performance
    0x05 = turbo

Usage:
  acer-profile get                    Show current profile
  acer-profile set <name>             Set profile: silent|balanced|performance|turbo
  acer-profile choices                List available profiles from BIOS
  acer-profile status                 Full diagnostic status

Requires:
  acer_wmi loaded with predator_v4=1:
    echo 'options acer_wmi predator_v4=1' > /etc/modprobe.d/acer-wmi-gaming.conf
    modprobe -r acer_wmi && modprobe acer_wmi predator_v4=1
"""

import sys
import os
import subprocess
import argparse
from pathlib import Path

# ---- Paths ------------------------------------------------------------------

PLATFORM_PROFILE  = Path("/sys/firmware/acpi/platform_profile")
PLATFORM_CHOICES  = Path("/sys/firmware/acpi/platform_profile_choices")
EC_PROBE          = Path("/usr/bin/ec_probe")

# EC registers for fan mode (confirmed on AN515-55)
REG_CPU_FAN_MODE  = 34   # 0x22: 4=auto, 12=manual
REG_GPU_FAN_MODE  = 33   # 0x21: 16=auto, 48=manual

# ---- Profile name mapping ---------------------------------------------------
# The kernel platform_profile subsystem uses specific names. The acer_wmi
# driver with predator_v4=1 registers these kernel names. We accept friendly
# names and map them.

FRIENDLY_TO_KERNEL = {
    "silent":      ["low-power", "quiet"],
    "quiet":       ["quiet", "low-power"],
    "balanced":    ["balanced"],
    "auto":        ["balanced"],
    "performance": ["performance"],
    # AN515-55 BIOS has no turbo/performance-hold — map to highest available
    "turbo":       ["performance"],
    # expose the intermediate profile too
    "balanced-performance": ["balanced-performance"],
}

KERNEL_TO_FRIENDLY = {
    "low-power":            "silent",
    "quiet":                "quiet",
    "balanced":             "balanced",
    "balanced-performance": "balanced-performance",
    "performance":          "performance",
    # kept for other models
    "performance-hold":     "turbo",
}

# WMI byte values for reference / diagnostics
# AN515-55 confirmed BIOS profiles: low-power quiet balanced balanced-performance performance
WMI_PROFILE_BYTES = {
    "low-power":            0x00,
    "quiet":                0x00,
    "balanced":             0x01,
    "balanced-performance": 0x03,
    "performance":          0x04,
    "performance-hold":     0x05,
}

# ---- Helpers ----------------------------------------------------------------

def sysfs_read(path: Path) -> str:
    try:
        return path.read_text().strip()
    except OSError as e:
        return None

def sysfs_write(path: Path, value: str) -> bool:
    try:
        path.write_text(value + "\n")
        return True
    except PermissionError:
        print(f"ERROR: Permission denied writing to {path}")
        print("       Run with sudo or as root.")
        return False
    except OSError as e:
        print(f"ERROR: Cannot write to {path}: {e}")
        return False

def get_choices() -> list[str]:
    raw = sysfs_read(PLATFORM_CHOICES)
    if raw is None:
        return []
    return raw.split()

def get_current_kernel_profile() -> str | None:
    return sysfs_read(PLATFORM_PROFILE)

def get_current_friendly_profile() -> str | None:
    kp = get_current_kernel_profile()
    if kp is None:
        return None
    return KERNEL_TO_FRIENDLY.get(kp, kp)

def ec_probe_available() -> bool:
    return EC_PROBE.exists() and os.access(EC_PROBE, os.X_OK)

def ec_read(reg: int) -> int | None:
    try:
        out = subprocess.check_output([str(EC_PROBE), "read", str(reg)],
                                       stderr=subprocess.DEVNULL, timeout=3)
        # ec_probe returns "VALUE (0xHEX)" format
        return int(out.decode().strip().split()[0])
    except Exception:
        return None

def check_interface() -> bool:
    """Returns True if the platform_profile sysfs interface is available."""
    return PLATFORM_PROFILE.exists() and PLATFORM_CHOICES.exists()

# ---- Commands ---------------------------------------------------------------

def cmd_get(args):
    if not check_interface():
        print_interface_missing()
        return 1

    kp = get_current_kernel_profile()
    fp = KERNEL_TO_FRIENDLY.get(kp, kp)
    print(fp)
    return 0

def cmd_set(args):
    name = args.profile.lower()

    if not check_interface():
        print_interface_missing()
        return 1

    available = get_choices()
    if not available:
        print("ERROR: Cannot read available profile choices.")
        return 1

    # Find a matching kernel name that's actually available on this BIOS
    candidates = FRIENDLY_TO_KERNEL.get(name)
    if candidates is None:
        # Maybe they typed the kernel name directly
        if name in available:
            kernel_name = name
        else:
            print(f"ERROR: Unknown profile '{name}'.")
            print(f"       Valid options: silent, quiet, balanced, balanced-performance, performance")
            return 1
    else:
        kernel_name = None
        for c in candidates:
            if c in available:
                kernel_name = c
                break
        if kernel_name is None:
            print(f"ERROR: Profile '{name}' is not available on this BIOS.")
            print(f"       BIOS reports: {' '.join(available)}")
            return 1

    wmi_byte = WMI_PROFILE_BYTES.get(kernel_name, "?")
    print(f"Setting thermal profile: {args.profile} → kernel='{kernel_name}' (WMI byte 0x{wmi_byte:02X})")

    if not sysfs_write(PLATFORM_PROFILE, kernel_name):
        return 1

    # Verify
    new = get_current_kernel_profile()
    if new == kernel_name:
        print(f"OK. Profile is now: {KERNEL_TO_FRIENDLY.get(new, new)}")
        print("The BIOS is now managing fan speeds using its native curve for this profile.")
        if args.profile.lower() in ("balanced", "silent", "quiet", "auto"):
            print("Fans should be quieter at idle — this is the firmware-managed behavior.")
        return 0
    else:
        print(f"WARNING: Write appeared to succeed but profile reads back as '{new}'")
        return 1

def cmd_choices(args):
    if not check_interface():
        print_interface_missing()
        return 1

    choices = get_choices()
    current = get_current_kernel_profile()
    print("Available thermal profiles (from BIOS via WMI):")
    for c in choices:
        marker = " ← current" if c == current else ""
        friendly = KERNEL_TO_FRIENDLY.get(c, c)
        wmi = WMI_PROFILE_BYTES.get(c, "?")
        print(f"  {c:20s} ({friendly:12s}, WMI byte 0x{wmi:02X}){marker}")
    return 0

def cmd_status(args):
    print("=== Acer Nitro AN515-55 Thermal Profile Status ===")
    print()

    # Interface availability
    have_interface = check_interface()
    print(f"Platform profile interface: {'AVAILABLE' if have_interface else 'NOT AVAILABLE'}")
    print(f"  {PLATFORM_PROFILE}")

    if have_interface:
        choices = get_choices()
        current_k = get_current_kernel_profile()
        current_f = KERNEL_TO_FRIENDLY.get(current_k, current_k)
        print(f"  Available profiles: {' '.join(choices)}")
        print(f"  Current profile:    {current_k} ({current_f})")
    else:
        print()
        print("  REASON: acer_wmi was not loaded with predator_v4=1")
        print()
        print_interface_missing()
        return 1

    print()

    # EC status (if ec_probe available and running as root)
    if ec_probe_available():
        cpu_mode = ec_read(REG_CPU_FAN_MODE)
        gpu_mode = ec_read(REG_GPU_FAN_MODE)
        if cpu_mode is not None:
            cpu_str = "AUTO" if cpu_mode == 4 else f"MANUAL ({cpu_mode})"
            gpu_str = "AUTO" if gpu_mode == 16 else f"MANUAL ({gpu_mode})"
            print(f"EC fan mode: CPU={cpu_str}, GPU={gpu_str}")
            if cpu_mode != 4 or gpu_mode != 16:
                print("  WARNING: EC is in manual mode — custom fan script may be running.")
                print("  When using WMI thermal profiles, the EC should be in AUTO mode.")
                print("  Stop nitro-fan-control if it's running.")
        else:
            print("EC fan mode: (requires root to read)")
    else:
        print("EC probe: not available (install nbfc-linux for EC access)")

    print()
    print("Recommendation:")
    if have_interface:
        current_k = get_current_kernel_profile()
        if current_k in ("balanced", "quiet", "low-power"):
            print("  Profile is set correctly. BIOS is managing fans.")
        else:
            print(f"  Current profile '{current_k}' may cause high fan speeds.")
            print("  Run: sudo acer-profile set balanced")

    return 0

def print_interface_missing():
    print()
    print("INTERFACE NOT AVAILABLE: /sys/firmware/acpi/platform_profile does not exist.")
    print()
    print("The acer_wmi driver needs to be loaded with predator_v4=1 to enable WMI")
    print("thermal profile control on the Acer Nitro AN515-55.")
    print()
    print("HOW TO FIX:")
    print()
    print("Step 1 — Test if your BIOS supports it:")
    print("  sudo bash try-predator-v4.sh")
    print()
    print("Step 2 — If the test succeeded, make it permanent:")
    print("  echo 'options acer_wmi predator_v4=1' | sudo tee /etc/modprobe.d/acer-wmi-gaming.conf")
    print("  sudo update-initramfs -u")
    print()
    print("Step 3 — Enable the profile-setting service:")
    print("  sudo cp acer-profile.service /etc/systemd/system/")
    print("  sudo systemctl daemon-reload")
    print("  sudo systemctl enable --now acer-profile")
    print()
    print("If the test FAILED (BIOS doesn't respond to predator_v4 WMI calls):")
    print("  Your BIOS (V2.06 08/19/2021) may not support the v4 gaming WMI interface.")
    print("  In that case, NitroSense was also using direct EC writes, not WMI profiles.")
    print("  Your best option is to put the EC in auto mode and live with the BIOS curve,")
    print("  or try a BIOS update if Acer released one with better WMI support.")

# ---- Main -------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        prog="acer-profile",
        description="Acer Nitro AN515-55 Thermal Profile Manager",
        epilog="Requires acer_wmi loaded with predator_v4=1. Run try-predator-v4.sh first.",
    )
    sub = parser.add_subparsers(dest="cmd")

    sub.add_parser("get", help="Print current thermal profile")
    sub.add_parser("choices", help="List all BIOS-supported profiles")
    sub.add_parser("status", help="Full diagnostic status")

    p_set = sub.add_parser("set", help="Set thermal profile")
    p_set.add_argument("profile", help="silent | balanced | performance | turbo")

    args = parser.parse_args()

    if args.cmd is None:
        parser.print_help()
        return 0

    dispatch = {
        "get":     cmd_get,
        "set":     cmd_set,
        "choices": cmd_choices,
        "status":  cmd_status,
    }

    return dispatch[args.cmd](args)

if __name__ == "__main__":
    sys.exit(main())
