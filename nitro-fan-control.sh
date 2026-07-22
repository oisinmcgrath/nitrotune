#!/bin/bash
# SPDX-License-Identifier: GPL-2.0-only
#
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
# =============================================================================
# Acer Nitro AN515-55 Fan Control — V2
# Custom dual-fan controller — CPU and GPU monitored independently
# V2: smooth ramping, linear interpolation, higher baseline
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

# Poll interval in seconds
POLL_INTERVAL=2

# Hysteresis: prevent fan speed drops unless temp falls this far below threshold
HYSTERESIS=3

# Fan curve: parallel arrays for linear interpolation between points.
# Between points, fan speed is interpolated linearly.
# Below first point → first speed. Above last point → last speed.
CURVE_TEMPS=(  50  55  60  65  70  75  80  85  90)
CURVE_SPEEDS=( 25  28  32  38  45  55  70  85 100)

# Minimum fan speed — always run at least this fast
MIN_FAN_SPEED=25

# Smooth ramping: max % change per poll cycle
RAMP_UP_MAX=3    # faster ramp up for safety
RAMP_DOWN_MAX=2  # slower ramp down for smooth wind-down

# Safety: if temp reading fails, force fans to this speed
FAILSAFE_SPEED=80

# Logging: true = log every reading, false = log every 30 seconds
VERBOSE=true

# Log file
LOG_FILE="/var/log/nitro-fan-control.log"

# --- EC Probe Paths ----------------------------------------------------------

EC_PROBE=""
for candidate in /usr/bin/ec_probe /usr/local/bin/ec_probe /opt/nbfc/bin/ec_probe; do
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
LAST_LOG_TIME=0

# --- Functions ----------------------------------------------------------------

log_msg() {
    echo "$(date '+%Y-%m-%d %H:%M:%S') $1" >> "$LOG_FILE"
}

read_cpu_temp() {
    local raw
    raw=$("$EC_PROBE" read "$REG_CPU_TEMP" 2>&1) || return 1
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

write_cpu_fan_speed() {
    local speed=$1
    if (( speed != CURRENT_CPU_SPEED )); then
        "$EC_PROBE" write "$REG_CPU_FAN_SPEED" "$speed" >/dev/null 2>&1
        CURRENT_CPU_SPEED=$speed
    fi
}

write_gpu_fan_speed() {
    local speed=$1
    if (( speed != CURRENT_GPU_SPEED )); then
        "$EC_PROBE" write "$REG_GPU_FAN_SPEED" "$speed" >/dev/null 2>&1
        CURRENT_GPU_SPEED=$speed
    fi
}

# Linear interpolation fan curve.
# Returns the interpolated fan speed for a given temperature.
interpolate_curve() {
    local temp=$1
    local n=${#CURVE_TEMPS[@]}

    # Below lowest point
    if (( temp <= CURVE_TEMPS[0] )); then
        echo "${CURVE_SPEEDS[0]}"
        return
    fi

    # Above highest point
    if (( temp >= CURVE_TEMPS[n-1] )); then
        echo "${CURVE_SPEEDS[n-1]}"
        return
    fi

    # Find the two surrounding points and interpolate
    local i
    for (( i = 0; i < n - 1; i++ )); do
        if (( temp >= CURVE_TEMPS[i] && temp < CURVE_TEMPS[i+1] )); then
            local t0=${CURVE_TEMPS[i]}
            local t1=${CURVE_TEMPS[i+1]}
            local s0=${CURVE_SPEEDS[i]}
            local s1=${CURVE_SPEEDS[i+1]}
            # Integer linear interpolation: s0 + (temp - t0) * (s1 - s0) / (t1 - t0)
            local result=$(( s0 + (temp - t0) * (s1 - s0) / (t1 - t0) ))
            echo "$result"
            return
        fi
    done

    # Fallback (shouldn't reach here)
    echo "${CURVE_SPEEDS[n-1]}"
}

# Calculate target fan speed with hysteresis.
# Uses the interpolated curve, but when temp is dropping, keeps the higher
# speed until temp drops HYSTERESIS degrees below where it was.
calculate_target() {
    local temp=$1
    local prev_temp=$2
    local current_actual=$3

    local curve_speed
    curve_speed=$(interpolate_curve "$temp")

    # Apply hysteresis on temperature drops
    if (( temp < prev_temp )); then
        local hyst_speed
        hyst_speed=$(interpolate_curve "$((temp + HYSTERESIS))")
        if (( hyst_speed > curve_speed )); then
            # Only use hysteresis speed if it doesn't exceed what we're already at
            if (( current_actual >= 0 && hyst_speed <= current_actual )); then
                curve_speed=$hyst_speed
            elif (( current_actual >= 0 && curve_speed < current_actual )); then
                # We want to come down, but not as fast — use hysteresis
                curve_speed=$hyst_speed
            fi
        fi
    fi

    # Enforce minimum
    if (( curve_speed < MIN_FAN_SPEED )); then
        curve_speed=$MIN_FAN_SPEED
    fi

    echo "$curve_speed"
}

# Smooth ramping: move current speed toward target by at most RAMP_UP_MAX/RAMP_DOWN_MAX per cycle
ramp_speed() {
    local current=$1
    local target=$2

    # First cycle — jump to target (no previous speed to ramp from)
    if (( current < 0 )); then
        echo "$target"
        return
    fi

    local diff=$(( target - current ))

    if (( diff > 0 )); then
        # Ramping up
        if (( diff > RAMP_UP_MAX )); then
            echo $(( current + RAMP_UP_MAX ))
        else
            echo "$target"
        fi
    elif (( diff < 0 )); then
        # Ramping down
        local abs_diff=$(( -diff ))
        if (( abs_diff > RAMP_DOWN_MAX )); then
            echo $(( current - RAMP_DOWN_MAX ))
        else
            echo "$target"
        fi
    else
        echo "$current"
    fi
}

cleanup() {
    log_msg "Shutting down — restoring auto fan control"
    set_fan_auto_mode
    exit 0
}

# --- Main Loop ----------------------------------------------------------------

trap cleanup SIGTERM SIGINT SIGHUP EXIT

echo "Acer Nitro AN515-55 Fan Control v2 starting..."
log_msg "=== Fan control v2 started ==="
log_msg "Curve: temps=${CURVE_TEMPS[*]} speeds=${CURVE_SPEEDS[*]}"
log_msg "Hysteresis: ${HYSTERESIS}°C | Poll: ${POLL_INTERVAL}s | Min: ${MIN_FAN_SPEED}%"
log_msg "Ramp: up=${RAMP_UP_MAX}%/cycle down=${RAMP_DOWN_MAX}%/cycle | Failsafe: ${FAILSAFE_SPEED}%"
log_msg "Verbose: ${VERBOSE}"

# Switch to manual mode
set_fan_manual_mode
log_msg "Switched to manual fan control"

while true; do
    # Read temperatures
    cpu_temp=$(read_cpu_temp) || cpu_temp=""
    gpu_temp=$(read_gpu_temp) || gpu_temp=""

    # --- CPU fan ---
    if [[ -z "$cpu_temp" ]]; then
        log_msg "WARNING: Failed to read CPU temp — applying failsafe ${FAILSAFE_SPEED}%"
        write_cpu_fan_speed "$FAILSAFE_SPEED"
        cpu_temp=$PREV_CPU_TEMP
        cpu_target="fail"
        cpu_actual=$FAILSAFE_SPEED
    else
        cpu_target=$(calculate_target "$cpu_temp" "$PREV_CPU_TEMP" "$CURRENT_CPU_SPEED")
        cpu_actual=$(ramp_speed "$CURRENT_CPU_SPEED" "$cpu_target")
        write_cpu_fan_speed "$cpu_actual"
        PREV_CPU_TEMP=$cpu_temp
    fi

    # --- GPU fan ---
    if [[ -z "$gpu_temp" ]]; then
        log_msg "WARNING: Failed to read GPU temp — applying failsafe ${FAILSAFE_SPEED}%"
        write_gpu_fan_speed "$FAILSAFE_SPEED"
        gpu_temp=$PREV_GPU_TEMP
        gpu_target="fail"
        gpu_actual=$FAILSAFE_SPEED
    else
        gpu_target=$(calculate_target "$gpu_temp" "$PREV_GPU_TEMP" "$CURRENT_GPU_SPEED")
        gpu_actual=$(ramp_speed "$CURRENT_GPU_SPEED" "$gpu_target")
        write_gpu_fan_speed "$gpu_actual"
        PREV_GPU_TEMP=$gpu_temp
    fi

    # --- Logging ---
    now=$SECONDS
    if [[ "$VERBOSE" == "true" ]] || (( now - LAST_LOG_TIME >= 30 )); then
        log_msg "CPU: ${cpu_temp}°C target=${cpu_target}% actual=${cpu_actual}% | GPU: ${gpu_temp}°C target=${gpu_target}% actual=${gpu_actual}%"
        LAST_LOG_TIME=$now
    fi

    sleep "$POLL_INTERVAL"
done
