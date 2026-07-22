# Changelog

## Unreleased — 2026-07-22

Public release prep.

### Added
- GPL-2.0-only `LICENSE`, with full copyright/license notice header on
  every source, unit, `.desktop`, and logrotate file.
- `acer-profile` WMI thermal-profile tooling (`acer-profile.py`,
  `try-predator-v4.sh`, `install-thermal-profile.sh`).
- `nitro-sense` read-only GTK sensor monitor, merged in as its own
  component alongside the fan-control daemon.

### Removed
- `CLAUDE-CODE-PROMPT.md` — an internal AI development prompt, not
  referenced anywhere and not part of the running program.

### Verified
- Repo audited for leaked private information (paths, hostnames,
  credentials) — none found beyond the author's name/email in
  copyright headers.
- Both GUIs (`nitro-sense.py`, `nitro-fan-monitor.py`) confirmed
  display-only — no buttons or interactive controls, read-only sensor
  readout as intended.

## v2 — 2026-04-07

Smooth, silent fan control. Complete rewrite of the fan logic.

### Added
- **Smooth ramping**: fan speed limited to +3%/cycle up, -2%/cycle down.
  Eliminates jarring audible jumps from Turbo Boost temperature spikes.
- **Linear interpolation**: fan speed interpolated between curve points
  instead of jumping between discrete steps.
- **VERBOSE config variable**: `true` logs every 2s reading, `false` logs
  every 30s. Set to `true` for testing.
- **Dual log output**: each line shows both curve `target` and ramped
  `actual` speed sent to EC, for verifying smooth operation.
- **Logrotate config**: weekly rotation, 4 compressed copies retained.

### Changed
- Minimum fan speed raised from 20% to 25%.
- Poll interval reduced from 3s to 2s for more responsive tracking.
- Fan curve refined based on v1 data (860 readings over 29 minutes):
  50°C/25%, 55/28, 60/32, 65/38, 70/45, 75/55, 80/70, 85/85, 90/100.
- ec_probe search order changed to prefer `/usr/bin/ec_probe`.

### Verified
- Stress test (stress-ng --cpu 4): smooth ramp 36%→100% over ~20s at
  +3%/cycle, temps held at 91-93°C. No audible jumps.
- Cool-down: smooth ramp 100%→~35% over ~30s at -2%/cycle.

## v1 — 2026-04-06

Initial working fan control with stepped curve.

- Stepped fan curve with 9 thresholds (50-90°C).
- 3°C hysteresis to prevent oscillation.
- Dual fan control (CPU + GPU independent).
- Failsafe 80% on temp read failure.
- Auto mode restore on service stop.
