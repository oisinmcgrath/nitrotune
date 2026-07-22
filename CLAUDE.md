# CLAUDE.md

Guidance for AI agents working in this repo. User-facing docs live in [README.md](README.md).

## Layout

Three independent components for the Acer Nitro AN515-55:

- `nitro-fan-control.sh` + `nitro-fan-control.service` + `install-fan-control.sh` —
  custom EC-register fan curve daemon (Option B). Writes fan speed directly
  via nbfc-linux's `ec_probe`.
- `acer-profile.py` + `acer-profile.service` + `install-thermal-profile.sh` +
  `try-predator-v4.sh` — WMI `platform_profile` thermal-profile switcher
  (Option A). The BIOS manages the fan curve; this just picks the profile.
- `nitrotune.py` + `nitrotune.desktop` + `install-nitrotune.sh` —
  read-only GTK sensor monitor (project namesake, NitroTune). Never
  writes anything; single file, no package. Pure display, safe to run
  alongside either option above or neither.
- `nitro-fan-monitor.py` + `nitro-fan-monitor.desktop` — optional Tk GUI
  that tails `nitro-fan-control`'s log for live gauges/graphs.

## Architecture notes

- Only one of Option A / Option B should actively control fans at a time;
  `install-thermal-profile.sh` stops `nitro-fan-control` automatically.
- EC registers (confirmed on AN515-55): 0x22/0x21 fan mode (auto/manual),
  0x37/0x3A fan speed %, 0xB0 CPU temp. See [v1-analysis.md](v1-analysis.md).
- `platform_profile` write triggers WMI method 22 on GUID
  `7A4DDFE7-5B5D-40B4-8595-4408E0CC7F56`; see comments in `acer-profile.py`.
  On some BIOS versions (confirmed V2.06 08/19/2021) only `balanced` sticks
  — others silently revert.
- GPU temp everywhere is `nvidia-smi --query-gpu=temperature.gpu`.
- `nitrotune.py` reads `platform_profile` for display only — no write path.

## Conventions

- Stdlib + PyGObject (nitrotune) / stdlib + tkinter (fan monitor) only;
  no third-party Python deps. Shell scripts: `set -euo pipefail`.
- GPL-2.0-only. Keep the SPDX header + license notice at the top of every
  source file (scripts, unit files, `.desktop`, logrotate config).
- `ec_probe` (nbfc-linux) is invoked as a subprocess, never linked — keep
  it that way so this repo's GPL-2.0-only licensing stays independent of
  nbfc-linux's own license.
