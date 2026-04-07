#!/bin/bash
# =============================================================================
# Acer Nitro AN515-55 Fan Control
# Custom dual-fan controller — CPU and GPU monitored independently
#
# EC Registers (confirmed working on AN515-55):
#   0x22 (34)  — CPU fan control mode (4=auto, 12=manual)
#   0x21 (33)  — GPU fan control mode (16=auto, 48=manual)
#   0x37 (55)  — CPU fan speed % (write 0-100, only works in manual mode)
#   0x3A (58)  — GPU fan speed % (write 0-100, only works in manual mode)
#   0xB0 (176) — CPU temperature
#
# GPU temp read via nvidia-smi
# Reference: https://github.com/nbfc-linux/nbfc-linux/issues/1356
# =============================================================================

set -euo pipefail

# --- Configuration -----------------------------------------------------------

# Poll interval in seconds (how often to check temps and adjust fans)
POLL_INTERVAL=3

# Hysteresis: fans won't drop speed unless temp falls this many degrees below
# the threshold that raised them. Prevents rapid fan speed oscillation.
HYSTERESIS=3

# Fan curve: TEMP_THRESHOLDS and FAN_SPEEDS are parallel arrays.
# At or above TEMP_THRESHOLDS[i], fan runs at FAN_SPEEDS[i] percent.
# Below the lowest threshold, fan is at 0%.
TEMP_THRESHOLDS=(50 55 60 65 70 75 80 85 90)
FAN_SPEEDS=(     20 25 30 40 50 60 75 90 100)

# Minimum fan speed when fans are on (some fans stall below a certain %).
# Set to 0 to allow fans to stop completely. Adjust if your fans click/stall.
MIN_FAN_SPEED=0

# Safety: if temp reading fails, force fans to this speed
FAILSAFE_SPEED=80

# Log file
LOG_FILE="/var/log/nitro-fan-control.log"

# --- EC Probe Paths ----------------------------------------------------------

EC_PROBE=""
for candidate in /opt/nbfc/bin/ec_probe /usr/bin/ec_probe /usr/local/bin/ec_probe; do
    if [[ -x "$candidate" ]]; then
        EC_PROBE="$candidate"
        break
    fi
done

if [[ -z "$EC_PROBE" ]]; then
    echo "ERROR: ec_probe not found. Install nbfc-linux first." >&2
    exit 1
fi

# --- EC Register Addresses ---------------------------------------------------

REG_CPU_FAN_MODE=34     # 0x22
REG_GPU_FAN_MODE=33     # 0x21
REG_CPU_FAN_SPEED=55    # 0x37
REG_GPU_FAN_SPEED=58    # 0x3A
REG_CPU_TEMP=176        # 0xB0

CPU_MODE_AUTO=4
CPU_MODE_MANUAL=12
GPU_MODE_AUTO=16
GPU_MODE_MANUAL=48

# --- State Tracking ----------------------------------------------------------

CURRENT_CPU_SPEED=-1
CURRENT_GPU_SPEED=-1
PREV_CPU_TEMP=0
PREV_GPU_TEMP=0

# --- Functions ----------------------------------------------------------------

log_msg() {
    echo "$(date '+%Y-%m-%d %H:%M:%S') $1" >> "$LOG_FILE"
}

read_cpu_temp() {
    local raw
    raw=$("$EC_PROBE" read "$REG_CPU_TEMP" 2>&1) || return 1
    # ec_probe returns "53 (0x35)" — extract just the leading digits
    grep -oP '^\d+' <<< "$raw"
}

read_gpu_temp() {
    local temp
    temp=$(nvidia-smi --query-gpu=temperature.gpu --format=csv,noheader 2>/dev/null) || return 1
    echo "$temp"
}

set_fan_manual_mode() {
    "$EC_PROBE" write "$REG_CPU_FAN_MODE" "$CPU_MODE_MANUAL" >/dev/null 2>&1
    "$EC_PROBE" write "$REG_GPU_FAN_MODE" "$GPU_MODE_MANUAL" >/dev/null 2>&1
}

set_fan_auto_mode() {
    "$EC_PROBE" write "$REG_CPU_FAN_MODE" "$CPU_MODE_AUTO" >/dev/null 2>&1
    "$EC_PROBE" write "$REG_GPU_FAN_MODE" "$GPU_MODE_AUTO" >/dev/null 2>&1
}

set_cpu_fan_speed() {
    local speed=$1
    if (( speed != CURRENT_CPU_SPEED )); then
        "$EC_PROBE" write "$REG_CPU_FAN_SPEED" "$speed" >/dev/null 2>&1
        CURRENT_CPU_SPEED=$speed
    fi
}

set_gpu_fan_speed() {
    local speed=$1
    if (( speed != CURRENT_GPU_SPEED )); then
        "$EC_PROBE" write "$REG_GPU_FAN_SPEED" "$speed" >/dev/null 2>&1
        CURRENT_GPU_SPEED=$speed
    fi
}

# Calculate target fan speed for a given temperature, applying hysteresis
# against the previous temperature to avoid oscillation.
calculate_fan_speed() {
    local temp=$1
    local prev_temp=$2
    local target_speed=0
    local i

    # Walk the curve to find the target speed for current temp
    for i in "${!TEMP_THRESHOLDS[@]}"; do
        if (( temp >= TEMP_THRESHOLDS[i] )); then
            target_speed=${FAN_SPEEDS[i]}
        fi
    done

    # Apply hysteresis: if temp is dropping, only reduce fan speed if we've
    # fallen HYSTERESIS degrees below the threshold that set the current speed.
    if (( temp < prev_temp )); then
        local prev_speed=0
        for i in "${!TEMP_THRESHOLDS[@]}"; do
            if (( prev_temp >= TEMP_THRESHOLDS[i] )); then
                prev_speed=${FAN_SPEEDS[i]}
            fi
        done
        # If previous speed was higher, check hysteresis
        if (( prev_speed > target_speed )); then
            local hyst_speed=0
            for i in "${!TEMP_THRESHOLDS[@]}"; do
                if (( temp >= (TEMP_THRESHOLDS[i] - HYSTERESIS) )); then
                    hyst_speed=${FAN_SPEEDS[i]}
                fi
            done
            if (( hyst_speed > target_speed )); then
                target_speed=$hyst_speed
            fi
        fi
    fi

    # Enforce minimum speed (if fans are supposed to be on)
    if (( target_speed > 0 && target_speed < MIN_FAN_SPEED )); then
        target_speed=$MIN_FAN_SPEED
    fi

    echo "$target_speed"
}

cleanup() {
    log_msg "Shutting down — restoring auto fan control"
    set_fan_auto_mode
    exit 0
}

# --- Main Loop ----------------------------------------------------------------

trap cleanup SIGTERM SIGINT SIGHUP EXIT

echo "Acer Nitro AN515-55 Fan Control starting..."
log_msg "=== Fan control started ==="
log_msg "Curve: thresholds=${TEMP_THRESHOLDS[*]} speeds=${FAN_SPEEDS[*]}"
log_msg "Hysteresis: ${HYSTERESIS}°C | Poll: ${POLL_INTERVAL}s | Failsafe: ${FAILSAFE_SPEED}%"

# Switch to manual mode
set_fan_manual_mode
log_msg "Switched to manual fan control"

while true; do
    # Read temperatures
    cpu_temp=$(read_cpu_temp) || cpu_temp=""
    gpu_temp=$(read_gpu_temp) || gpu_temp=""

    # Handle failed readings with failsafe
    if [[ -z "$cpu_temp" ]]; then
        log_msg "WARNING: Failed to read CPU temp — applying failsafe ${FAILSAFE_SPEED}%"
        set_cpu_fan_speed "$FAILSAFE_SPEED"
        cpu_temp=$PREV_CPU_TEMP
    else
        cpu_fan=$(calculate_fan_speed "$cpu_temp" "$PREV_CPU_TEMP")
        set_cpu_fan_speed "$cpu_fan"
        PREV_CPU_TEMP=$cpu_temp
    fi

    if [[ -z "$gpu_temp" ]]; then
        log_msg "WARNING: Failed to read GPU temp — applying failsafe ${FAILSAFE_SPEED}%"
        set_gpu_fan_speed "$FAILSAFE_SPEED"
        gpu_temp=$PREV_GPU_TEMP
    else
        gpu_fan=$(calculate_fan_speed "$gpu_temp" "$PREV_GPU_TEMP")
        set_gpu_fan_speed "$gpu_fan"
        PREV_GPU_TEMP=$gpu_temp
    fi

    # Log periodically (every ~30 seconds to avoid log spam)
    if (( SECONDS % 30 < POLL_INTERVAL )); then
        log_msg "CPU: ${cpu_temp}°C → ${CURRENT_CPU_SPEED}% | GPU: ${gpu_temp}°C → ${CURRENT_GPU_SPEED}%"
    fi

    sleep "$POLL_INTERVAL"
done
