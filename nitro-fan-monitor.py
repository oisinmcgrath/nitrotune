#!/usr/bin/env python3
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
"""
Nitro Fan Control Monitor — v2
Real-time GUI with arc gauges and live scrolling graphs.
Reads from the nitro-fan-control service log — no root required.
"""

import tkinter as tk
import math
import re
import os
import subprocess
import glob
from collections import deque
from datetime import datetime

LOG_FILE = "/var/log/nitro-fan-control.log"
MAX_POINTS = 200   # ~6.5 min at 2s intervals
UPDATE_MS = 40     # poll hardware temps every 40ms
GPU_POLL_MS = 200  # nvidia-smi is ~23ms, poll every 200ms

# --- Color scheme (dark) ---
BG        = "#0d1117"
BG_CARD   = "#161b22"
BG_GRAPH  = "#0d1117"
FG        = "#e6edf3"
FG_DIM    = "#484f58"
FG_MED    = "#8b949e"
GRID      = "#21262d"
BORDER    = "#30363d"

# Accent colors
CPU_TEMP_ARC  = "#f97583"   # red-pink
GPU_TEMP_ARC  = "#56d364"   # green
CPU_FAN_ARC   = "#d29922"   # amber/orange
GPU_FAN_ARC   = "#58a6ff"   # blue
CPU_COLOR     = "#f97583"
GPU_COLOR     = "#56d364"
CPU_FAN_COLOR = "#d29922"
GPU_FAN_COLOR = "#58a6ff"
TARGET_COLOR  = "#30363d"
ARC_BG        = "#21262d"


def severity_color(val, lo, hi):
    """Gradient from green to yellow to orange to red based on val in [lo, hi]."""
    ratio = max(0.0, min(1.0, (val - lo) / max(hi - lo, 1)))
    if ratio < 0.4:
        return "#56d364"   # green
    elif ratio < 0.6:
        return "#d29922"   # amber
    elif ratio < 0.8:
        return "#f97583"   # red-pink
    else:
        return "#ff7b72"   # bright red


class ArcGauge:
    """A circular arc gauge — immediate updates, no animation."""

    def __init__(self, parent, size, arc_color, label, unit,
                 val_min=0, val_max=100, arc_width=10):
        self.size = size
        self.arc_color = arc_color
        self.label = label
        self.unit = unit
        self.val_min = val_min
        self.val_max = val_max
        self.arc_width = arc_width
        self.value = 0.0

        self.canvas = tk.Canvas(
            parent, width=size, height=size,
            bg=BG_CARD, highlightthickness=0
        )

        self.start_angle = 225
        self.total_sweep = 270

        self._draw()

    def pack(self, **kwargs):
        self.canvas.pack(**kwargs)

    def grid(self, **kwargs):
        self.canvas.grid(**kwargs)

    def set(self, value, color=None):
        if color:
            self.arc_color = color
        self.value = value
        self._draw()

    def _draw(self):
        c = self.canvas
        c.delete("all")
        s = self.size
        pad = self.arc_width + 4
        cx, cy = s / 2, s / 2

        # Background arc (full track)
        c.create_arc(
            pad, pad, s - pad, s - pad,
            start=self.start_angle, extent=-self.total_sweep,
            style=tk.ARC, outline=ARC_BG, width=self.arc_width
        )

        # Value arc
        ratio = max(0.0, min(1.0,
            (self.value - self.val_min) / max(self.val_max - self.val_min, 1)))
        sweep = ratio * self.total_sweep

        if sweep > 0.5:
            c.create_arc(
                pad, pad, s - pad, s - pad,
                start=self.start_angle, extent=-sweep,
                style=tk.ARC, outline=self.arc_color, width=self.arc_width
            )

        # Tick marks at start and end of track
        for frac in [0.0, 0.25, 0.5, 0.75, 1.0]:
            angle_deg = self.start_angle - frac * self.total_sweep
            angle_rad = math.radians(angle_deg)
            r_outer = (s / 2) - 2
            r_inner = r_outer - self.arc_width - 3
            x1 = cx + r_outer * math.cos(angle_rad)
            y1 = cy - r_outer * math.sin(angle_rad)
            x2 = cx + r_inner * math.cos(angle_rad)
            y2 = cy - r_inner * math.sin(angle_rad)
            c.create_line(x1, y1, x2, y2, fill=FG_DIM, width=1)

        # Center value
        val_text = str(int(round(self.value)))
        c.create_text(
            cx, cy - 6, text=val_text, fill=FG,
            font=("Helvetica", int(s * 0.22), "bold")
        )

        # Unit below value
        c.create_text(
            cx, cy + s * 0.15, text=self.unit, fill=FG_MED,
            font=("Helvetica", int(s * 0.08))
        )

        # Label at bottom
        c.create_text(
            cx, s - 6, text=self.label, fill=FG_DIM,
            font=("Helvetica", int(s * 0.07), "bold")
        )


class NitroFanMonitor:
    def __init__(self, root):
        self.root = root
        self.root.title("Nitro Fan Monitor")
        self.root.configure(bg=BG)
        self.root.minsize(860, 700)

        # Data storage
        self.cpu_temps = deque(maxlen=MAX_POINTS)
        self.gpu_temps = deque(maxlen=MAX_POINTS)
        self.cpu_targets = deque(maxlen=MAX_POINTS)
        self.cpu_actuals = deque(maxlen=MAX_POINTS)
        self.gpu_targets = deque(maxlen=MAX_POINTS)
        self.gpu_actuals = deque(maxlen=MAX_POINTS)
        self.timestamps = deque(maxlen=MAX_POINTS)

        # Current values
        self.cur = {
            "cpu_temp": 0, "gpu_temp": 0,
            "cpu_target": 0, "cpu_actual": 0,
            "gpu_target": 0, "gpu_actual": 0,
        }

        # Log tracking — persistent file handle for zero-latency reads
        self.log_pos = 0
        self._log_fh = None
        self._log_size = 0
        self._seed_from_log()

        # Direct hardware temp sources
        self._cpu_temp_file = self._find_cpu_temp_sysfs()
        self._gpu_temp_cmd = ["nvidia-smi", "--query-gpu=temperature.gpu",
                              "--format=csv,noheader"]
        self._live_gpu_temp = 0

        self._build_ui()
        self._update()
        self._poll_gpu_temp()

    def _seed_from_log(self):
        """Read recent log data to populate graphs on startup."""
        try:
            size = os.path.getsize(LOG_FILE)
            read_from = max(0, size - 40000)
            with open(LOG_FILE, "r", errors="replace") as f:
                f.seek(read_from)
                if read_from > 0:
                    f.readline()
                for line in f:
                    self._parse_line(line)
                self.log_pos = f.tell()
        except (OSError, IOError):
            self.log_pos = 0

    @staticmethod
    def _find_cpu_temp_sysfs():
        """Find the sysfs file for CPU package temperature."""
        # Prefer x86_pkg_temp, fall back to coretemp hwmon, then thermal_zone0
        for tz in sorted(glob.glob("/sys/class/thermal/thermal_zone*/type")):
            try:
                with open(tz) as f:
                    if f.read().strip() == "x86_pkg_temp":
                        return tz.replace("/type", "/temp")
            except OSError:
                continue
        # Fallback: coretemp hwmon
        for hw in sorted(glob.glob("/sys/class/hwmon/hwmon*/name")):
            try:
                with open(hw) as f:
                    if f.read().strip() == "coretemp":
                        candidate = hw.replace("/name", "/temp1_input")
                        if os.path.exists(candidate):
                            return candidate
            except OSError:
                continue
        # Last resort
        return "/sys/class/thermal/thermal_zone0/temp"

    def _read_cpu_temp(self):
        """Read CPU temp from sysfs — ~0.01ms, effectively free."""
        try:
            with open(self._cpu_temp_file) as f:
                return int(f.read().strip()) // 1000
        except (OSError, ValueError):
            return self.cur["cpu_temp"]

    def _poll_gpu_temp(self):
        """Read GPU temp from nvidia-smi every GPU_POLL_MS."""
        try:
            out = subprocess.check_output(
                self._gpu_temp_cmd, timeout=1, stderr=subprocess.DEVNULL
            )
            self._live_gpu_temp = int(out.strip())
        except (subprocess.SubprocessError, ValueError, OSError):
            pass  # keep last known value
        self.root.after(GPU_POLL_MS, self._poll_gpu_temp)

    def _build_ui(self):
        # --- Title bar ---
        title_bar = tk.Frame(self.root, bg=BG)
        title_bar.pack(fill=tk.X, padx=16, pady=(12, 0))
        tk.Label(
            title_bar, text="NITRO FAN CONTROL", fg=FG_MED, bg=BG,
            font=("Helvetica", 10, "bold")
        ).pack(side=tk.LEFT)

        # Live indicator with pulsing dot
        live_frame = tk.Frame(title_bar, bg=BG)
        live_frame.pack(side=tk.RIGHT)
        self.live_dot = tk.Canvas(
            live_frame, width=10, height=10, bg=BG, highlightthickness=0
        )
        self.live_dot.pack(side=tk.LEFT, padx=(0, 4))
        self.live_dot.create_oval(2, 2, 9, 9, fill="#56d364", outline="", tags="dot")
        self._dot_on = True
        self._pulse_dot()
        self.status_lbl = tk.Label(
            live_frame, text="", fg=FG_DIM, bg=BG,
            font=("Helvetica", 9)
        )
        self.status_lbl.pack(side=tk.LEFT)

        # === CPU section ===
        cpu_frame = tk.Frame(self.root, bg=BG_CARD, bd=0)
        cpu_frame.pack(fill=tk.X, padx=12, pady=(10, 4))

        cpu_header = tk.Frame(cpu_frame, bg=BG_CARD)
        cpu_header.pack(fill=tk.X, padx=12, pady=(8, 0))
        tk.Label(
            cpu_header, text="CPU", fg=CPU_TEMP_ARC, bg=BG_CARD,
            font=("Helvetica", 11, "bold")
        ).pack(side=tk.LEFT)
        self.cpu_subtitle = tk.Label(
            cpu_header, text="Intel i5-10300H", fg=FG_DIM, bg=BG_CARD,
            font=("Helvetica", 9)
        )
        self.cpu_subtitle.pack(side=tk.LEFT, padx=(8, 0))

        gauge_size = 130
        cpu_gauges = tk.Frame(cpu_frame, bg=BG_CARD)
        cpu_gauges.pack(pady=(4, 8))

        self.cpu_temp_gauge = ArcGauge(
            cpu_gauges, gauge_size, CPU_TEMP_ARC, "TEMP", "°C",
            val_min=30, val_max=100, arc_width=8
        )
        self.cpu_temp_gauge.grid(row=0, column=0, padx=16)

        self.cpu_fan_gauge = ArcGauge(
            cpu_gauges, gauge_size, CPU_FAN_ARC, "FAN SPEED", "%",
            val_min=0, val_max=100, arc_width=8
        )
        self.cpu_fan_gauge.grid(row=0, column=1, padx=16)

        self.cpu_target_gauge = ArcGauge(
            cpu_gauges, gauge_size, FG_DIM, "TARGET", "%",
            val_min=0, val_max=100, arc_width=6
        )
        self.cpu_target_gauge.grid(row=0, column=2, padx=16)

        # === GPU section ===
        gpu_frame = tk.Frame(self.root, bg=BG_CARD, bd=0)
        gpu_frame.pack(fill=tk.X, padx=12, pady=(4, 4))

        gpu_header = tk.Frame(gpu_frame, bg=BG_CARD)
        gpu_header.pack(fill=tk.X, padx=12, pady=(8, 0))
        tk.Label(
            gpu_header, text="GPU", fg=GPU_TEMP_ARC, bg=BG_CARD,
            font=("Helvetica", 11, "bold")
        ).pack(side=tk.LEFT)
        self.gpu_subtitle = tk.Label(
            gpu_header, text="NVIDIA GTX 1650", fg=FG_DIM, bg=BG_CARD,
            font=("Helvetica", 9)
        )
        self.gpu_subtitle.pack(side=tk.LEFT, padx=(8, 0))

        gpu_gauges = tk.Frame(gpu_frame, bg=BG_CARD)
        gpu_gauges.pack(pady=(4, 8))

        self.gpu_temp_gauge = ArcGauge(
            gpu_gauges, gauge_size, GPU_TEMP_ARC, "TEMP", "°C",
            val_min=30, val_max=100, arc_width=8
        )
        self.gpu_temp_gauge.grid(row=0, column=0, padx=16)

        self.gpu_fan_gauge = ArcGauge(
            gpu_gauges, gauge_size, GPU_FAN_ARC, "FAN SPEED", "%",
            val_min=0, val_max=100, arc_width=8
        )
        self.gpu_fan_gauge.grid(row=0, column=1, padx=16)

        self.gpu_target_gauge = ArcGauge(
            gpu_gauges, gauge_size, FG_DIM, "TARGET", "%",
            val_min=0, val_max=100, arc_width=6
        )
        self.gpu_target_gauge.grid(row=0, column=2, padx=16)

        # === Graphs section ===
        graphs = tk.Frame(self.root, bg=BG)
        graphs.pack(fill=tk.BOTH, expand=True, padx=12, pady=(4, 4))

        # Temperature graph
        temp_hdr = tk.Frame(graphs, bg=BG)
        temp_hdr.pack(fill=tk.X)
        tk.Label(
            temp_hdr, text="TEMPERATURE", fg=FG_DIM, bg=BG,
            font=("Helvetica", 9, "bold")
        ).pack(side=tk.LEFT)
        # Legend dots
        for color, name in [(CPU_COLOR, "CPU"), (GPU_COLOR, "GPU")]:
            tk.Canvas(
                temp_hdr, bg=color, width=8, height=8, highlightthickness=0
            ).pack(side=tk.RIGHT, padx=(0, 2))
            tk.Label(
                temp_hdr, text=name, fg=FG_DIM, bg=BG,
                font=("Helvetica", 8)
            ).pack(side=tk.RIGHT, padx=(8, 0))

        self.temp_canvas = tk.Canvas(
            graphs, bg=BG_GRAPH, highlightthickness=0, height=120
        )
        self.temp_canvas.pack(fill=tk.BOTH, expand=True, pady=(2, 6))

        # Fan graph
        fan_hdr = tk.Frame(graphs, bg=BG)
        fan_hdr.pack(fill=tk.X)
        tk.Label(
            fan_hdr, text="FAN SPEED", fg=FG_DIM, bg=BG,
            font=("Helvetica", 9, "bold")
        ).pack(side=tk.LEFT)
        for color, name in [(CPU_FAN_COLOR, "CPU"), (GPU_FAN_COLOR, "GPU"),
                            (TARGET_COLOR, "target")]:
            tk.Canvas(
                fan_hdr, bg=color, width=8, height=8, highlightthickness=0
            ).pack(side=tk.RIGHT, padx=(0, 2))
            tk.Label(
                fan_hdr, text=name, fg=FG_DIM, bg=BG,
                font=("Helvetica", 8)
            ).pack(side=tk.RIGHT, padx=(8, 0))

        self.fan_canvas = tk.Canvas(
            graphs, bg=BG_GRAPH, highlightthickness=0, height=120
        )
        self.fan_canvas.pack(fill=tk.BOTH, expand=True, pady=(2, 0))

        # Status bar
        self.status_bar = tk.Label(
            self.root, text="", fg=FG_DIM, bg=BG,
            font=("Helvetica", 8), anchor=tk.W
        )
        self.status_bar.pack(fill=tk.X, padx=14, pady=(4, 8))

    def _parse_line(self, line):
        m = re.search(
            r"CPU:\s*(\d+)°C\s+target=(\d+)%\s+actual=(\d+)%\s*\|\s*"
            r"GPU:\s*(\d+)°C\s+target=(\d+)%\s+actual=(\d+)%",
            line
        )
        if not m:
            return False

        self.cur["cpu_temp"] = int(m.group(1))
        self.cur["cpu_target"] = int(m.group(2))
        self.cur["cpu_actual"] = int(m.group(3))
        self.cur["gpu_temp"] = int(m.group(4))
        self.cur["gpu_target"] = int(m.group(5))
        self.cur["gpu_actual"] = int(m.group(6))

        self.cpu_temps.append(self.cur["cpu_temp"])
        self.gpu_temps.append(self.cur["gpu_temp"])
        self.cpu_targets.append(self.cur["cpu_target"])
        self.cpu_actuals.append(self.cur["cpu_actual"])
        self.gpu_targets.append(self.cur["gpu_target"])
        self.gpu_actuals.append(self.cur["gpu_actual"])

        tm = re.match(r"(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})", line)
        self.timestamps.append(tm.group(1) if tm else "")
        return True

    def _read_new_lines(self):
        count = 0
        try:
            size = os.path.getsize(LOG_FILE)
            if size == self._log_size:
                return 0  # nothing new — skip I/O entirely
            if size < self._log_size:
                # log was rotated
                self.log_pos = 0
                if self._log_fh:
                    self._log_fh.close()
                    self._log_fh = None
            self._log_size = size
            if self._log_fh is None:
                self._log_fh = open(LOG_FILE, "r", errors="replace")
                self._log_fh.seek(self.log_pos)
            for line in self._log_fh:
                if self._parse_line(line):
                    count += 1
            self.log_pos = self._log_fh.tell()
        except (OSError, IOError):
            if self._log_fh:
                self._log_fh.close()
                self._log_fh = None
        return count

    def _pulse_dot(self):
        """Pulse the live indicator dot."""
        color = "#56d364" if self._dot_on else "#1a4028"
        self.live_dot.itemconfig("dot", fill=color)
        self._dot_on = not self._dot_on
        self.root.after(800, self._pulse_dot)

    def _update(self):
        new = self._read_new_lines()

        # Always update temp gauges from direct hardware reads
        cpu_t = self._read_cpu_temp()
        gpu_t = self._live_gpu_temp or self.cur["gpu_temp"]
        self.cpu_temp_gauge.set(cpu_t, severity_color(cpu_t, 40, 95))
        self.gpu_temp_gauge.set(gpu_t, severity_color(gpu_t, 40, 90))

        if new > 0:
            c = self.cur

            # Fan gauges from log (only source for fan data)
            self.cpu_fan_gauge.set(c["cpu_actual"], severity_color(c["cpu_actual"], 20, 90))
            self.cpu_target_gauge.set(c["cpu_target"], FG_DIM)

            self.gpu_fan_gauge.set(c["gpu_actual"], severity_color(c["gpu_actual"], 20, 90))
            self.gpu_target_gauge.set(c["gpu_target"], FG_DIM)

            # Redraw graphs immediately (log-rate data keeps temp+fan in sync)
            self._draw_temp_graph()
            self._draw_fan_graph()

            self.status_bar.config(
                text=f"{len(self.cpu_temps)} readings  |  +{new} new"
            )

        # Always update clock
        now = datetime.now().strftime("%H:%M:%S")
        self.status_lbl.config(text=f"LIVE  {now}")

        self.root.after(UPDATE_MS, self._update)

    def _draw_graph(self, canvas, datasets, y_min, y_max, y_step):
        canvas.delete("all")
        w = canvas.winfo_width()
        h = canvas.winfo_height()
        if w < 60 or h < 40:
            return

        ml, mr, mt, mb = 36, 8, 6, 4
        gw = w - ml - mr
        gh = h - mt - mb
        if gw < 10 or gh < 10:
            return

        # Grid lines and labels
        for val in range(y_min, y_max + 1, y_step):
            y = mt + gh - (val - y_min) / (y_max - y_min) * gh
            canvas.create_line(ml, y, w - mr, y, fill=GRID, width=1)
            canvas.create_text(
                ml - 4, y, text=str(val), fill=FG_DIM,
                font=("Helvetica", 7), anchor=tk.E
            )

        n = max((len(d) for d, _, _ in datasets), default=0)
        if n < 2:
            canvas.create_text(
                w // 2, h // 2, text="Waiting for data…",
                fill=FG_DIM, font=("Helvetica", 10)
            )
            return

        # Draw filled area + line for each dataset
        for data, color, width in datasets:
            if len(data) < 2:
                continue
            points = []
            for i, val in enumerate(data):
                x = ml + (i / (n - 1)) * gw
                y = mt + gh - (val - y_min) / max(y_max - y_min, 1) * gh
                y = max(mt, min(mt + gh, y))
                points.append((x, y))

            # Subtle filled area under curve
            if width >= 2:
                fill_points = [(points[0][0], mt + gh)]
                fill_points.extend(points)
                fill_points.append((points[-1][0], mt + gh))
                flat = []
                for p in fill_points:
                    flat.extend(p)
                # Create semi-transparent fill by using stipple
                canvas.create_polygon(
                    *flat, fill=color, outline="",
                    stipple="gray12"
                )

            # Line
            flat_line = []
            for p in points:
                flat_line.extend(p)
            if len(flat_line) >= 4:
                canvas.create_line(
                    *flat_line, fill=color, width=width, smooth=True
                )

        # Border
        canvas.create_rectangle(ml, mt, w - mr, mt + gh, outline=BORDER, width=1)

    def _draw_temp_graph(self):
        datasets = [
            (self.cpu_temps, CPU_COLOR, 2),
            (self.gpu_temps, GPU_COLOR, 2),
        ]
        self._draw_graph(self.temp_canvas, datasets, 30, 100, 10)

    def _draw_fan_graph(self):
        datasets = [
            (self.cpu_targets, TARGET_COLOR, 1),
            (self.gpu_targets, TARGET_COLOR, 1),
            (self.cpu_actuals, CPU_FAN_COLOR, 2),
            (self.gpu_actuals, GPU_FAN_COLOR, 2),
        ]
        self._draw_graph(self.fan_canvas, datasets, 0, 100, 20)


def main():
    root = tk.Tk()
    try:
        root.iconname("nitro-fan-monitor")
    except tk.TclError:
        pass

    app = NitroFanMonitor(root)

    def on_resize(event):
        app._draw_temp_graph()
        app._draw_fan_graph()

    root.bind("<Configure>", on_resize)
    root.mainloop()


if __name__ == "__main__":
    main()
