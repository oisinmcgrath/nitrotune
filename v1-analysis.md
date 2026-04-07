# Nitro Fan Control — V1 Analysis & V2 Requirements

## Hardware
- Acer Nitro 5 AN515-55
- CPU: Intel i5-10300H (Turbo Boost to 4.5GHz)
- GPU: NVIDIA GTX 1650 Mobile (driver 580, PRIME on-demand mode)
- Linux Mint 22 Cinnamon
- NVIDIA PowerMizer set to Adaptive

## EC Registers (confirmed working)
- Register 34 (0x22): CPU fan mode — write 4 = auto, 12 = manual
- Register 33 (0x21): GPU fan mode — write 16 = auto, 48 = manual
- Register 55 (0x37): CPU fan speed % (0-100, only works in manual mode)
- Register 58 (0x3A): GPU fan speed % (0-100, only works in manual mode)
- Register 176 (0xB0): CPU temperature
- GPU temp: read via `nvidia-smi --query-gpu=temperature.gpu --format=csv,noheader`

## Critical: ec_probe output parsing
ec_probe returns values like `53 (0x35)` not just `53`.
Must parse with: `grep -oP '^\d+'` or equivalent.
ec_probe location: `/usr/bin/ec_probe`

## V1 Analysis (29 minutes, 860 readings at 2-second intervals)

### Temperature distribution
- CPU: min=50, max=94, avg=63.6°C
- GPU: min=50, max=66, avg=58.4°C (very stable, barely needs attention)

### CPU fan speed distribution (v1 stepped curve)
- 20%: 9.3% of time
- 25%: 13.3%
- 30%: 36.7% ← most common
- 40%: 19.2%
- 50%: 9.4%
- 60%: 5.9%
- 75%: 3.8%
- 90%: 1.3%
- 100%: 1.0%

### Key finding: 16 temperature spikes >15°C in 2 seconds
These are real — Intel Turbo Boost fires a core to 4.5GHz for brief bursts.
Examples:
- 56°C → 74°C in 2s (+18°C)
- 64°C → 86°C in 2s (+22°C)
- 64°C → 89°C in 2s (+25°C)
- 68°C → 92°C in 2s (+24°C)

With the v1 stepped curve, these cause jarring audible jumps
(e.g. 30% → 90% instantly). This is the main problem to solve.

### CPU temp ranges
- 50-55°C: 11.3% (idle)
- 55-60°C: 19.8% (light)
- 60-65°C: 34.0% (normal use) ← most time spent here
- 65-70°C: 16.6%
- 70-75°C: 7.6%
- 75-80°C: 5.0%
- 80-85°C: 3.8%
- 85-90°C: 1.0%
- 90-100°C: 0.9%

## V2 Requirements

### 1. Smooth ramping (MOST IMPORTANT)
Instead of instantly jumping to target fan speed, limit change to
MAX 2% per poll cycle. If target is 75% and current is 30%, ramp:
30→32→34→36...→75 over many cycles. This eliminates the audible
jumps that are the main complaint.

Ramp UP slightly faster than ramp DOWN:
- Ramp up: max +3% per cycle (safety — get to cooling speed quickly)
- Ramp down: max -2% per cycle (gradual, smooth wind-down)

### 2. Higher baseline
v1 used 20% as the minimum. Data shows idle temps are 50-53°C.
Use 25% as the baseline — this keeps things slightly cooler so
spikes start from a lower temp and don't reach as high.

### 3. Refined fan curve
Based on where time is actually spent (60-65°C = 34% of time):

| Temp   | Fan % |
|--------|-------|
| <50°C  | 25%   |
| 50°C   | 25%   |
| 55°C   | 28%   |
| 60°C   | 32%   |
| 65°C   | 38%   |
| 70°C   | 45%   |
| 75°C   | 55%   |
| 80°C   | 70%   |
| 85°C   | 85%   |
| 90°C   | 100%  |

The curve should interpolate linearly between points,
not jump between discrete steps.

### 4. Poll interval
Keep at 2 seconds for responsive temperature tracking.

### 5. Hysteresis
Keep 3°C hysteresis to prevent oscillation on temp boundaries.

### 6. Logging
- Log every reading during testing (current behaviour)
- Add a config variable to switch to periodic logging (every 30s)
  for production use, to avoid filling the log
- Include actual fan speed sent to EC (after ramping), not just
  the target from the curve

### 7. Safety
- Failsafe 80% if temp read fails
- On service stop/crash: restore auto fan mode (write 4 to reg 34,
  write 16 to reg 33)
- ExecStopPost in service file must use /usr/bin/ec_probe

### 8. Service file
- Path: /etc/systemd/system/nitro-fan-control.service
- ExecStopPost uses /usr/bin/ec_probe (NOT /opt/nbfc/bin/ec_probe)

## File structure
- nitro-fan-control.sh — the main script (runs as systemd service)
- nitro-fan-control.service — systemd unit file
- install.sh — installer with sanity checks
- v1-analysis.md — this file
- logs/ — directory for test logs

## Testing workflow
1. Edit the script
2. Run: sudo cp nitro-fan-control.sh /usr/local/bin/ && sudo systemctl restart nitro-fan-control
3. Watch: tail -f /var/log/nitro-fan-control.log
4. Look for smooth transitions, no jarring jumps
5. Commit when happy

## Reference
- GitHub issue with EC register mappings: https://github.com/nbfc-linux/nbfc-linux/issues/1356
- User GrahamboJangles found the registers on the same AN515-55 model
