#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
#
# nitrotune — read-only sensor monitor for the Acer Nitro AN515-55
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
"""
nitrotune — Acer Nitro AN515-55 sensor monitor
Read-only replacement for the sensor page of NitroSense / DAMX on Linux.

Reads CPU/GPU temperature, fan RPM, RAM, battery, and the active WMI
thermal profile (acer_wmi predator_v4=1). Nothing here writes to sysfs —
this is a monitor, not a profile switcher.

deps: python3-gi  gir1.2-gtk-3.0  nvidia-smi (optional, for GPU temp)
"""

import gi
gi.require_version("Gtk", "3.0")
from gi.repository import Gtk, GLib, Gdk

import subprocess
from pathlib import Path
from typing import Optional

# ── Constants ─────────────────────────────────────────────────────────────────

PLATFORM_PROFILE = Path("/sys/firmware/acpi/platform_profile")
BAT_DIR          = Path("/sys/class/power_supply/BAT1")
REFRESH_MS       = 2000

# (kernel sysfs name, short display label)
PROFILE_LABELS = {
    "low-power":            "Silent",
    "quiet":                "Quiet",
    "balanced":              "Balanced",
    "balanced-performance": "Bal+",
    "performance":          "Perf",
}

# ── Sensor helpers ────────────────────────────────────────────────────────────

def _read(path: Path) -> Optional[str]:
    try:
        return path.read_text().strip()
    except OSError:
        return None

def _read_int(path: Path) -> Optional[int]:
    v = _read(path)
    try:
        return int(v) if v is not None else None
    except ValueError:
        return None

_hwmon_cache: dict[str, Optional[Path]] = {}

def find_hwmon(name: str) -> Optional[Path]:
    """Find hwmon device by name — cached after first successful lookup."""
    if name in _hwmon_cache:
        cached = _hwmon_cache[name]
        # Validate cache still valid
        if cached and (cached / "name").exists():
            return cached
    try:
        for d in sorted(Path("/sys/class/hwmon").iterdir()):
            n = d / "name"
            if n.exists() and n.read_text().strip() == name:
                _hwmon_cache[name] = d
                return d
    except OSError:
        pass
    _hwmon_cache[name] = None
    return None

def cpu_temp_c() -> Optional[float]:
    """CPU package temperature from coretemp hwmon (no root needed)."""
    h = find_hwmon("coretemp")
    if not h:
        return None
    v = _read_int(h / "temp1_input")   # "Package id 0" in millidegrees C
    return v / 1000.0 if v is not None else None

def gpu_temp_c() -> Optional[float]:
    """GPU temperature via nvidia-smi."""
    try:
        out = subprocess.check_output(
            ["nvidia-smi", "--query-gpu=temperature.gpu", "--format=csv,noheader"],
            stderr=subprocess.DEVNULL, timeout=2
        ).decode().strip()
        return float(out)
    except Exception:
        return None

def fan_rpms() -> tuple[Optional[int], Optional[int]]:
    """CPU and GPU fan RPM from acer_wmi WMI hwmon (no root needed)."""
    h = find_hwmon("acer")
    if not h:
        return None, None
    return _read_int(h / "fan1_input"), _read_int(h / "fan2_input")

def ram_usage_gb() -> tuple[float, float]:
    """Returns (used_GiB, total_GiB) from /proc/meminfo."""
    try:
        info: dict[str, int] = {}
        with open("/proc/meminfo") as f:
            for line in f:
                parts = line.split()
                if len(parts) >= 2:
                    info[parts[0].rstrip(":")] = int(parts[1])
        total = info["MemTotal"] / 1048576
        avail = info["MemAvailable"] / 1048576
        return (total - avail, total)
    except Exception:
        return (0.0, 0.0)

def battery_state() -> tuple[int, str]:
    """Returns (capacity_pct, status_string)."""
    cap    = _read_int(BAT_DIR / "capacity") or 0
    status = _read(BAT_DIR / "status") or "Unknown"
    return cap, status

def current_profile() -> Optional[str]:
    return _read(PLATFORM_PROFILE)

def current_profile_label() -> str:
    p = current_profile()
    if p is None:
        return "—"
    return PROFILE_LABELS.get(p, p)

# ── CSS ───────────────────────────────────────────────────────────────────────

CSS = b"""
window.ns-window { background-color: #0f0f17; }

.ns-header  { background-color: #1c1c2c; padding: 14px 18px 10px 18px; }
.ns-title   { color: #ff5e6e; font-size: 18px; font-weight: bold; }
.ns-sub     { color: #8888a8; font-size: 11px; }

.ns-profbar { background-color: #181824; padding: 10px 14px 12px 14px;
              border-bottom: 1px solid #262638; }
.ns-prof-val { color: #ffffff; font-size: 16px; font-weight: bold; }

.ns-stats { background-color: #0f0f17; padding: 14px 18px 16px 18px; }
.ns-lbl   { color: #aaaace; font-size: 13px; font-weight: 500; }
.ns-val   { color: #ffffff; font-size: 14px; font-weight: bold; }
.ns-rpm   { color: #99a8d0; font-size: 12px; }

progressbar          { min-height: 8px; }
progressbar trough   { background-color: #1e1e2e; border-radius: 4px; min-height: 8px; }
progressbar progress { border-radius: 4px; min-height: 8px; }

progressbar.cool progress { background: #54a0ff; }
progressbar.warm progress { background: #ffa94d; }
progressbar.hot  progress { background: #ff4757; }
progressbar.ram  progress { background: #7bed9f; }
progressbar.bat  progress { background: #2ed573; }
progressbar.bat-low progress { background: #ff4757; }

.ns-footer      { background-color: #0a0a12; padding: 6px 18px; }
.ns-footer-text { color: #555578; font-size: 10px; }
"""

# ── Main window ───────────────────────────────────────────────────────────────

class NitroTuneWindow(Gtk.Window):

    def __init__(self):
        super().__init__(title="NitroTune")
        self.set_resizable(True)
        self.set_keep_above(False)
        self.set_default_size(420, 300)
        self.set_size_request(380, 260)
        self.set_border_width(0)
        self.connect("delete-event", self._on_delete)

        provider = Gtk.CssProvider()
        provider.load_from_data(CSS)
        Gtk.StyleContext.add_provider_for_screen(
            Gdk.Screen.get_default(), provider,
            Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION
        )
        self.get_style_context().add_class("ns-window")

        root = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        self.add(root)
        root.pack_start(self._build_header(),      False, False, 0)
        root.pack_start(self._build_profile_bar(), False, False, 0)
        root.pack_start(self._build_stats(),       False, False, 0)
        root.pack_start(self._build_footer(),      False, False, 0)

        self._timer_id = GLib.timeout_add(REFRESH_MS, self._tick)
        self._tick()

    # ── Header ──────────────────────────────────────────────────────────────

    def _build_header(self) -> Gtk.Widget:
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=1)
        box.get_style_context().add_class("ns-header")

        title = Gtk.Label(label="Acer Nitro AN515-55")
        title.set_xalign(0)
        title.get_style_context().add_class("ns-title")

        sub = Gtk.Label(label="NitroTune  ·  read-only sensor monitor")
        sub.set_xalign(0)
        sub.get_style_context().add_class("ns-sub")

        box.pack_start(title, False, False, 0)
        box.pack_start(sub,   False, False, 0)
        return box

    # ── Profile readout ─────────────────────────────────────────────────────

    def _build_profile_bar(self) -> Gtk.Widget:
        outer = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        outer.get_style_context().add_class("ns-profbar")

        lbl = Gtk.Label(label="Thermal profile")
        lbl.set_xalign(0)
        lbl.get_style_context().add_class("ns-lbl")

        self._profile_val = Gtk.Label(label="—")
        self._profile_val.set_xalign(0)
        self._profile_val.get_style_context().add_class("ns-prof-val")

        outer.pack_start(lbl, False, False, 0)
        outer.pack_start(self._profile_val, True, True, 0)
        return outer

    # ── Stats panel ─────────────────────────────────────────────────────────

    def _build_stats(self) -> Gtk.Widget:
        grid = Gtk.Grid()
        grid.set_column_spacing(8)
        grid.set_row_spacing(8)
        grid.get_style_context().add_class("ns-stats")

        def bar(css: str) -> Gtk.ProgressBar:
            b = Gtk.ProgressBar()
            b.set_valign(Gtk.Align.CENTER)
            b.set_hexpand(True)
            b.get_style_context().add_class(css)
            return b

        def val() -> Gtk.Label:
            l = Gtk.Label(label="—")
            l.set_xalign(0)
            l.set_width_chars(10)
            l.get_style_context().add_class("ns-val")
            return l

        def rpm() -> Gtk.Label:
            l = Gtk.Label(label="")
            l.set_xalign(0)
            l.set_width_chars(10)
            l.get_style_context().add_class("ns-rpm")
            return l

        def lbl(text: str, w: int = 4) -> Gtk.Label:
            l = Gtk.Label(label=text)
            l.set_xalign(0)
            l.set_width_chars(w)
            l.get_style_context().add_class("ns-lbl")
            return l

        self._cpu_bar = bar("cool");  self._cpu_val = val();  self._cpu_rpm = rpm()
        self._gpu_bar = bar("cool");  self._gpu_val = val();  self._gpu_rpm = rpm()
        self._ram_bar = bar("ram");   self._ram_val = val()
        self._bat_bar = bar("bat");   self._bat_val = val()

        rows = [
            (lbl("CPU"), self._cpu_bar, self._cpu_val, self._cpu_rpm),
            (lbl("GPU"), self._gpu_bar, self._gpu_val, self._gpu_rpm),
            (lbl("RAM"), self._ram_bar, self._ram_val, None),
            (lbl("BAT"), self._bat_bar, self._bat_val, None),
        ]
        for r, (l, b, v, rp) in enumerate(rows):
            grid.attach(l, 0, r, 1, 1)
            grid.attach(b, 1, r, 1, 1)
            grid.attach(v, 2, r, 1, 1)
            if rp:
                grid.attach(rp, 3, r, 1, 1)

        return grid

    def _set_temp_bar(self, bar: Gtk.ProgressBar, temp: float):
        bar.set_fraction(min(1.0, temp / 100.0))
        ctx = bar.get_style_context()
        for c in ("cool", "warm", "hot"):
            ctx.remove_class(c)
        ctx.add_class("hot" if temp >= 80 else "warm" if temp >= 60 else "cool")

    # ── Footer ───────────────────────────────────────────────────────────────

    def _build_footer(self) -> Gtk.Widget:
        box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=0)
        box.get_style_context().add_class("ns-footer")
        lbl = Gtk.Label(label="acer_wmi predator_v4=1  ·  WMI hwmon  ·  ACPI platform_profile")
        lbl.set_xalign(0)
        lbl.get_style_context().add_class("ns-footer-text")
        box.pack_start(lbl, True, True, 0)
        return box

    # ── Refresh loop ─────────────────────────────────────────────────────────

    def _tick(self) -> bool:
        # CPU
        ct = cpu_temp_c()
        f1, f2 = fan_rpms()
        if ct is not None:
            self._set_temp_bar(self._cpu_bar, ct)
            self._cpu_val.set_text(f"{ct:.0f} °C")
        if f1 is not None:
            self._cpu_rpm.set_text(f"↻ {f1:,} RPM")

        # GPU
        gt = gpu_temp_c()
        if gt is not None:
            self._set_temp_bar(self._gpu_bar, gt)
            self._gpu_val.set_text(f"{gt:.0f} °C")
        if f2 is not None:
            self._gpu_rpm.set_text(f"↻ {f2:,} RPM")

        # RAM
        used, total = ram_usage_gb()
        if total > 0:
            self._ram_bar.set_fraction(used / total)
            self._ram_val.set_text(f"{used:.1f} / {total:.0f} GB")

        # Battery
        cap, status = battery_state()
        self._bat_bar.set_fraction(cap / 100.0)
        ctx = self._bat_bar.get_style_context()
        ctx.remove_class("bat"); ctx.remove_class("bat-low")
        ctx.add_class("bat-low" if cap < 20 else "bat")
        icon = {"Charging": "⚡ ", "Discharging": "▼ ", "Full": "✓ "}.get(status, "")
        self._bat_val.set_text(f"{icon}{cap}%")

        # Thermal profile (read-only)
        self._profile_val.set_text(current_profile_label())

        return True  # keep running

    def _on_delete(self, *_):
        GLib.source_remove(self._timer_id)
        return False

    def toggle_visibility(self):
        if self.get_visible():
            self.hide()
        else:
            self.show_all()
            self.present()

# ── System tray ───────────────────────────────────────────────────────────────
# Use Gtk.StatusIcon (works natively on Cinnamon's XApp Status Applet).
# AppIndicator3 is X11-only and requires extra applets on Cinnamon, so we
# avoid it for portability.

def build_tray(window: "NitroTuneWindow"):
    try:
        icon = Gtk.StatusIcon()
    except Exception:
        return None

    # Try a few well-known icon names; the first one available will show
    for name in ("computer", "applications-system", "preferences-system",
                 "system-run", "utilities-system-monitor"):
        icon.set_from_icon_name(name)
        if icon.get_pixbuf() or icon.get_gicon():
            break

    icon.set_tooltip_text("NitroTune — click to show/hide")
    icon.set_visible(True)

    menu = Gtk.Menu()

    item_toggle = Gtk.MenuItem(label="Show / Hide")
    item_toggle.connect("activate", lambda _: window.toggle_visibility())
    menu.append(item_toggle)
    menu.append(Gtk.SeparatorMenuItem())

    item_quit = Gtk.MenuItem(label="Quit")
    item_quit.connect("activate", lambda _: Gtk.main_quit())
    menu.append(item_quit)

    menu.show_all()

    # Left click → toggle window
    icon.connect("activate", lambda _: window.toggle_visibility())
    # Right click → menu
    icon.connect("popup-menu",
                 lambda i, btn, t: menu.popup(None, None,
                                              Gtk.StatusIcon.position_menu,
                                              i, btn, t))

    # Keep references alive
    window._tray_icon = icon
    window._tray_menu = menu
    return icon

# ── Entry point ───────────────────────────────────────────────────────────────

def main():
    window = NitroTuneWindow()
    window.show_all()
    build_tray(window)
    try:
        Gtk.main()
    except KeyboardInterrupt:
        pass

if __name__ == "__main__":
    main()
