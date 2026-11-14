# NitroTune

Fan/thermal-profile control **and** a read-only sensor monitor for the
**Acer Nitro AN515-55** on Linux — a native replacement for Windows-only
NitroSense/DAMX.

<p align="center">
  <img src="media/logo/nitrotune-icon.png" alt="NitroTune logo" width="160">
</p>

There are two independent ways to control fans here — pick one — plus an
optional GUI that only displays sensors and changes nothing:

## Option A — WMI thermal profiles (`acer-profile`)

Uses the kernel's standard `platform_profile` sysfs interface, exposed by
`acer_wmi` when loaded with `predator_v4=1`. In principle the BIOS manages
the fan curve internally for whichever profile you select
(silent/balanced/performance/turbo) — in practice, on the BIOS version this
was tested against (V2.06 08/19/2021), only `balanced` actually does
anything; `silent`/`performance`/`turbo` are accepted by the WMI call and the
sysfs value changes, but the firmware silently ignores them and the fan
curve doesn't change. Run `acer-profile status` to check what your BIOS
actually honours before relying on any profile besides `balanced`.

Simpler and lower-risk than Option B, but for most AN515-55 units this means
it isn't really a 4-profile switcher — it's a way to confirm/force
`balanced`, and for real fan control you'll want Option B below.

```bash
sudo bash try-predator-v4.sh          # confirm the BIOS supports it
sudo bash install-thermal-profile.sh  # make it permanent, install acer-profile.service
acer-profile status
sudo acer-profile set balanced
```

## Option B — Custom EC fan curve (`nitro-fan-control`)

Writes fan speed directly to the embedded controller via [nbfc-linux](https://github.com/nbfc-linux/nbfc-linux)'s
`ec_probe`, independent of WMI/BIOS support. Full manual control: a smooth,
linearly-interpolated fan curve with ramp limiting (no jarring jumps on
Turbo Boost temperature spikes) and independent CPU/GPU control.

```bash
sudo bash install-fan-control.sh
sudo systemctl start nitro-fan-control
tail -f /var/log/nitro-fan-control.log   # watch it for a few minutes first
sudo systemctl enable nitro-fan-control  # once you're happy
```

Restores automatic EC fan control on stop/crash — never leaves the fans
stuck at a fixed speed.

`nitro-fan-monitor.py` is an optional real-time Tk GUI (arc gauges + graphs)
that tails the fan-control log — no root required.

**Don't run both at once** — `install-thermal-profile.sh` stops
`nitro-fan-control` automatically when you switch to Option A.

## NitroTune (read-only monitor)

`nitrotune.py` is a separate, always-safe GTK system-tray GUI: live
CPU/GPU temperature, fan RPM, RAM, battery, and the active thermal profile.
It never writes anything — no buttons, no settings, just a readout. Useful
regardless of which control option above you use, or neither.

```bash
sudo bash install-nitrotune.sh
```

Installs to `/usr/local/bin/nitrotune`, adds an application-menu entry,
and autostarts on login.

### Fan RPM via the read-only EC helper

The EC reader is intended for the **Acer Nitro 5 AN515-55**. Its EC
registers and addresses are specific to this hardware; compatibility with
other models is not guaranteed.

The helper needs Linux with `ec_sys` and debugfs available, including
`/sys/kernel/debug/ec/ec0/io`. Keep the `ec_sys` parameter
`write_support=0`. If `ec_sys` is available as a module and is not
already loaded, it can be loaded with writes disabled:

```bash
sudo modprobe ec_sys write_support=0
cat /sys/module/ec_sys/parameters/write_support
```

The second command should report `N` (disabled). Check the actual value
even if `ec_sys` was already loaded: `modprobe` does not change the
parameters of an already-loaded module. If the parameter or EC interface
is unavailable, check your kernel and debugfs configuration. Neither the
helper installer nor the helper sets or checks this parameter.

To install the helper, run the following from the project directory as the
normal user who will use NitroTune, **not as root**:

```bash
bash install-nitrotune-ec-reader.sh
```

The installer needs Bash, a C compiler available as `/usr/bin/cc`,
`sudo`/`visudo`, `runuser`, `getent`, and standard GNU utilities. The
user must be authorized to use sudo for the installation. Review the source
and installer before granting that authorization.

It compiles `nitrotune-ec-read.c` as the invoking user, then uses sudo to
install the root-owned helper as `/usr/local/libexec/nitrotune-ec-read`
and a root-owned rule as `/etc/sudoers.d/nitrotune-ec-read`. The rule
allows that user to run only this command as root, without arguments or a
password, with `NOSETENV` and a two-second timeout. The installer validates
the staged rule and the full sudoers configuration; this does not prove
that the rule is included or takes precedence over existing policy.

The helper opens the EC interface read-only. It does not write EC
registers or control fans. NitroTune invokes it with `sudo -n`, so it
never prompts interactively for a password while running.

After installing NitroTune as above, launch it with:

```bash
nitrotune &
```

The CPU and GPU rows show their fan speeds in RPM. `N/A` is shown while
a reading is pending or when it is unavailable, for example if the helper,
EC interface, or sudo authorization is unavailable, or a read fails or
times out. The other sensor displays do not depend on this helper.

#### Removing the EC helper

There is no uninstall script. The following is a **manual proposal**, not
an automatically verified uninstall procedure. Stop NitroTune first. An
administrator should inspect both paths, confirm that they are the files
installed for this helper and are not needed by other users, and check
the existing sudo policy before removing anything:

```bash
sudo ls -l /etc/sudoers.d/nitrotune-ec-read /usr/local/libexec/nitrotune-ec-read
sudo cat /etc/sudoers.d/nitrotune-ec-read
```

If verified, remove the sudoers rule first, validate the remaining sudoers
configuration, and only then remove the helper:

```bash
sudo rm -- /etc/sudoers.d/nitrotune-ec-read &&
sudo /usr/sbin/visudo -c &&
sudo rm -- /usr/local/libexec/nitrotune-ec-read
```

Stop and resolve any validation error before removing the helper. This
does not remove NitroTune itself or modify other sudo rules, which may
still grant access to the helper path. Restarting NitroTune without the
helper leaves the fan RPM readings unavailable.

## EC registers (confirmed on AN515-55)

| Register | Purpose | Values |
|---|---|---|
| 0x22 (34) | CPU fan mode | 4=auto, 12=manual |
| 0x21 (33) | GPU fan mode | 16=auto, 48=manual |
| 0x37 (55) | CPU fan speed % | 0–100 (manual mode only) |
| 0x3A (58) | GPU fan speed % | 0–100 (manual mode only) |
| 0xB0 (176) | CPU temperature | °C |

GPU temperature is read via `nvidia-smi`. See [v1-analysis.md](v1-analysis.md)
for how the fan curve was derived, and [CHANGELOG.md](CHANGELOG.md) for
the v1→v2 history.

## Requirements

- Linux with a recent kernel (developed on Linux Mint 22 / kernel 6.8)
- [nbfc-linux](https://github.com/nbfc-linux/nbfc-linux) installed, for `ec_probe` (Option B, and EC diagnostics in `acer-profile status`)
- `nvidia-smi`, for GPU temperature on NVIDIA hardware
- `acer_wmi` loaded with `predator_v4=1` (Option A only)
- `python3-gi`, `gir1.2-gtk-3.0` (for `nitrotune.py` only)

## Other hardware

EC register addresses are specific to this model, but the mechanism
(`acer_wmi` + platform_profile, and EC access via nbfc-linux) applies to
many Acer Nitro/Predator laptops. Register values will likely differ —
see the [nbfc-linux issue tracker](https://github.com/nbfc-linux/nbfc-linux/issues)
for other confirmed models.

## License

GPL-2.0-only. See [LICENSE](LICENSE). This project shells out to `ec_probe`
(a separate nbfc-linux executable) at runtime rather than linking against
it, so it carries its own license independent of nbfc-linux's.
