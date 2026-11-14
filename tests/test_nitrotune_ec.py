#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
#
# nitrotune EC reader tests
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

import importlib.util
import queue
import shutil
import subprocess
import sys
import tempfile
import threading
import types
import unittest
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]


def load_nitrotune():
    gtk = types.SimpleNamespace(
        Window=type("Window", (), {}),
        Widget=type("Widget", (), {}),
        ProgressBar=type("ProgressBar", (), {}),
    )
    repository = types.ModuleType("gi.repository")
    repository.Gtk = gtk
    repository.GLib = types.SimpleNamespace(idle_add=None)
    repository.Gdk = types.SimpleNamespace()

    gi = types.ModuleType("gi")
    gi.require_version = lambda *_args: None
    gi.repository = repository

    with mock.patch.dict(
        sys.modules,
        {"gi": gi, "gi.repository": repository},
    ):
        spec = importlib.util.spec_from_file_location(
            "nitrotune_tested",
            ROOT / "nitrotune.py",
        )
        module = importlib.util.module_from_spec(spec)
        assert spec.loader is not None
        spec.loader.exec_module(module)
    return module


nitrotune = load_nitrotune()


class FakeLabel:
    def __init__(self):
        self.text = ""
        self.calls = []

    def set_text(self, text):
        self.text = text
        self.calls.append((text, threading.get_ident()))


def make_window():
    window = nitrotune.NitroTuneWindow.__new__(nitrotune.NitroTuneWindow)
    window._rpm_results = queue.SimpleQueue()
    window._rpm_generation = 0
    window._rpm_read_in_flight = False
    window._rpm_worker = None
    window._closing = False
    window._cpu_rpm = FakeLabel()
    window._gpu_rpm = FakeLabel()
    return window


class FanOutputTests(unittest.TestCase):
    def test_parser_accepts_only_exact_protocol(self):
        self.assertEqual(
            nitrotune._parse_fan_output(
                b"CPU_RPM=1704\nGPU_RPM=2400\n"
            ),
            (1704, 2400),
        )
        self.assertEqual(
            nitrotune._parse_fan_output(
                b"CPU_RPM=0\nGPU_RPM=65535\n"
            ),
            (0, 65535),
        )

    def test_parser_rejects_malformed_or_out_of_range_output(self):
        invalid_outputs = (
            b"",
            b"CPU_RPM=1\n",
            b"CPU_RPM=1\nGPU_RPM=2\nextra\n",
            b"CPU_RPM=01\nGPU_RPM=2\n",
            b"CPU_RPM=-1\nGPU_RPM=2\n",
            b"CPU_RPM=65536\nGPU_RPM=2\n",
        )
        for output in invalid_outputs:
            with self.subTest(output=output):
                self.assertEqual(
                    nitrotune._parse_fan_output(output),
                    (None, None),
                )

    def test_reader_uses_fixed_argv_and_validates_return_code(self):
        result = types.SimpleNamespace(
            returncode=0,
            stdout=b"CPU_RPM=1704\nGPU_RPM=2400\n",
        )
        with mock.patch.object(
            nitrotune.subprocess, "run", return_value=result
        ) as run:
            self.assertEqual(nitrotune.fan_rpms(), (1704, 2400))

        args, kwargs = run.call_args
        self.assertEqual(
            args[0],
            [
                "/usr/bin/sudo",
                "-n",
                "/usr/local/libexec/nitrotune-ec-read",
            ],
        )
        self.assertIs(kwargs["shell"], False)
        self.assertEqual(kwargs["timeout"], nitrotune.RPM_TIMEOUT_S)

    def test_reader_returns_unavailable_for_process_errors(self):
        failures = (
            OSError("not executable"),
            subprocess.TimeoutExpired("sudo", 4),
        )
        for failure in failures:
            with self.subTest(failure=type(failure).__name__):
                with mock.patch.object(
                    nitrotune.subprocess, "run", side_effect=failure
                ):
                    self.assertEqual(nitrotune.fan_rpms(), (None, None))

        with mock.patch.object(
            nitrotune.subprocess,
            "run",
            return_value=types.SimpleNamespace(returncode=1, stdout=b""),
        ):
            self.assertEqual(nitrotune.fan_rpms(), (None, None))


class WorkerTests(unittest.TestCase):
    def prepare_tick(self, window):
        window._bat_bar = mock.MagicMock()
        window._bat_val = mock.MagicMock()
        window._profile_val = mock.MagicMock()
        patches = (
            mock.patch.object(nitrotune, "cpu_temp_c", return_value=None),
            mock.patch.object(nitrotune, "gpu_temp_c", return_value=None),
            mock.patch.object(nitrotune, "ram_usage_gb", return_value=(0, 0)),
            mock.patch.object(nitrotune, "battery_state", return_value=(50, "")),
            mock.patch.object(nitrotune, "current_profile_label", return_value="—"),
        )
        for patch in patches:
            patch.start()
            self.addCleanup(patch.stop)

    def test_ticks_do_not_start_another_worker_while_reading(self):
        window = make_window()
        self.prepare_tick(window)
        started = threading.Event()
        release = threading.Event()
        callbacks = []
        calls = []

        def reader():
            calls.append(threading.get_ident())
            started.set()
            release.wait()
            return 1704, 2400

        with (
            mock.patch.object(nitrotune, "fan_rpms", side_effect=reader),
            mock.patch.object(
                nitrotune.GLib, "idle_add",
                side_effect=lambda callback, *args: callbacks.append((callback, args)),
            ),
        ):
            window._tick()
            worker = window._rpm_worker
            try:
                self.assertTrue(started.wait(timeout=2))
                for _ in range(5):
                    window._tick()
                    self.assertIs(window._rpm_worker, worker)
                    self.assertTrue(worker.is_alive())
                    self.assertTrue(window._rpm_read_in_flight)
                self.assertEqual(len(calls), 1)
            finally:
                release.set()
                worker.join(timeout=2)
            self.assertFalse(worker.is_alive())
            self.assertEqual(len(callbacks), 1)

    def test_old_callback_preserves_new_generation_for_its_callback(self):
        window = make_window()
        window._rpm_generation = 2
        window._rpm_read_in_flight = True
        callbacks = []

        with (
            mock.patch.object(nitrotune, "fan_rpms", return_value=(1704, 2400)),
            mock.patch.object(
                nitrotune.GLib, "idle_add",
                side_effect=lambda callback, *args: callbacks.append((callback, args)),
            ),
        ):
            window._fan_read_worker(2)

        window._consume_fan_result(1)
        self.assertTrue(window._rpm_read_in_flight)
        self.assertEqual(window._cpu_rpm.calls, [])
        self.assertEqual(window._gpu_rpm.calls, [])
        callback, args = callbacks.pop()
        callback(*args)
        self.assertFalse(window._rpm_read_in_flight)
        self.assertEqual(window._cpu_rpm.text, "↻ 1,704 RPM")
        self.assertEqual(window._gpu_rpm.text, "↻ 2,400 RPM")
        self.assertTrue(window._rpm_results.empty())

    def test_tick_recovers_result_when_idle_add_fails(self):
        window = make_window()
        self.prepare_tick(window)

        with (
            mock.patch.object(nitrotune, "fan_rpms", return_value=(1704, 2400)),
            mock.patch.object(
                nitrotune.GLib, "idle_add", side_effect=RuntimeError("failed")
            ),
            self.assertLogs(nitrotune.LOGGER, level="ERROR"),
        ):
            window._start_fan_read()
            worker = window._rpm_worker
            worker.join(timeout=2)
            self.assertFalse(worker.is_alive())
            self.assertTrue(window._rpm_read_in_flight)
            window._tick()

        self.assertFalse(window._rpm_read_in_flight)
        self.assertEqual(window._cpu_rpm.text, "↻ 1,704 RPM")
        self.assertEqual(window._gpu_rpm.text, "↻ 2,400 RPM")
        self.assertTrue(window._rpm_results.empty())

    def test_close_during_read_prevents_late_widget_updates(self):
        window = make_window()
        window._timer_id = 42
        started = threading.Event()
        release = threading.Event()
        callbacks = []

        def reader():
            started.set()
            release.wait()
            return 1704, 2400

        with (
            mock.patch.object(nitrotune, "fan_rpms", side_effect=reader),
            mock.patch.object(
                nitrotune.GLib, "idle_add",
                side_effect=lambda callback, *args: callbacks.append((callback, args)),
            ),
            mock.patch.object(nitrotune.GLib, "source_remove", create=True) as remove,
        ):
            window._start_fan_read()
            worker = window._rpm_worker
            try:
                self.assertTrue(started.wait(timeout=2))
                self.assertFalse(window._on_delete())
                self.assertTrue(window._closing)
                cpu_calls = list(window._cpu_rpm.calls)
                gpu_calls = list(window._gpu_rpm.calls)
            finally:
                release.set()
                worker.join(timeout=2)
            self.assertFalse(worker.is_alive())
            remove.assert_called_once_with(42)
            self.assertEqual(len(callbacks), 1)
            callback, args = callbacks.pop()
            callback(*args)

        self.assertEqual(window._cpu_rpm.calls, cpu_calls)
        self.assertEqual(window._gpu_rpm.calls, gpu_calls)
        self.assertFalse(window._rpm_read_in_flight)

    def test_thread_start_failure_leaves_values_unavailable(self):
        window = make_window()
        window._cpu_rpm.set_text("old CPU")
        window._gpu_rpm.set_text("old GPU")

        with mock.patch.object(
            nitrotune.threading.Thread, "start", side_effect=RuntimeError("failed")
        ):
            window._start_fan_read()

        self.assertFalse(window._rpm_read_in_flight)
        self.assertIsNone(window._rpm_worker)
        self.assertEqual(window._cpu_rpm.text, "N/A")
        self.assertEqual(window._gpu_rpm.text, "N/A")

    def test_worker_updates_widgets_only_via_main_thread_callback(self):
        window = make_window()
        main_thread = threading.get_ident()
        started = threading.Event()
        release = threading.Event()
        callbacks = []
        calls = []

        def fake_reader():
            calls.append(threading.get_ident())
            started.set()
            release.wait(timeout=2)
            return 1704, 2400

        with (
            mock.patch.object(nitrotune, "fan_rpms", side_effect=fake_reader),
            mock.patch.object(
                nitrotune.GLib,
                "idle_add",
                side_effect=lambda callback, *args: callbacks.append(
                    (callback, args)
                ),
            ),
        ):
            window._start_fan_read()
            self.assertTrue(started.wait(timeout=1))
            window._start_fan_read()
            self.assertEqual(len(calls), 1)

            release.set()
            window._rpm_worker.join(timeout=2)
            self.assertFalse(window._rpm_worker.is_alive())
            self.assertEqual(len(callbacks), 1)

            callback, args = callbacks.pop()
            callback(*args)

        self.assertNotEqual(calls[0], main_thread)
        self.assertTrue(
            all(thread_id == main_thread
                for _, thread_id in
                window._cpu_rpm.calls + window._gpu_rpm.calls)
        )
        self.assertEqual(window._cpu_rpm.text, "↻ 1,704 RPM")
        self.assertEqual(window._gpu_rpm.text, "↻ 2,400 RPM")
        self.assertFalse(window._rpm_read_in_flight)

    def test_queue_recovers_result_when_idle_callback_is_not_delivered(self):
        window = make_window()
        callbacks = []
        window._rpm_read_in_flight = True
        window._rpm_generation = 1

        with mock.patch.object(nitrotune, "fan_rpms", return_value=(1704, 2400)), \
                mock.patch.object(
                    nitrotune.GLib,
                    "idle_add",
                    side_effect=lambda callback, *args: callbacks.append(
                        (callback, args)
                    ),
                ):
            window._fan_read_worker(1)

        self.assertEqual(len(callbacks), 1)
        self.assertTrue(window._rpm_read_in_flight)
        window._consume_fan_result()
        self.assertFalse(window._rpm_read_in_flight)
        self.assertEqual(window._cpu_rpm.text, "↻ 1,704 RPM")

    def test_stale_callback_cannot_consume_newer_generation(self):
        window = make_window()
        window._rpm_generation = 2
        window._rpm_read_in_flight = True
        window._rpm_results.put((2, (1704, 2400)))

        window._consume_fan_result(expected_generation=1)
        self.assertTrue(window._rpm_read_in_flight)
        self.assertEqual(window._cpu_rpm.text, "")

        window._consume_fan_result(expected_generation=2)
        self.assertFalse(window._rpm_read_in_flight)
        self.assertEqual(window._cpu_rpm.text, "↻ 1,704 RPM")

    def test_worker_exception_produces_unavailable_values(self):
        window = make_window()
        window._rpm_generation = 1
        window._rpm_read_in_flight = True

        with (
            self.assertLogs(nitrotune.LOGGER, level="ERROR"),
            mock.patch.object(
                nitrotune,
                "fan_rpms",
                side_effect=RuntimeError("unexpected failure"),
            ),
            mock.patch.object(nitrotune.GLib, "idle_add"),
        ):
            window._fan_read_worker(1)

        window._consume_fan_result()
        self.assertEqual(window._cpu_rpm.text, "N/A")
        self.assertEqual(window._gpu_rpm.text, "N/A")

    def test_callback_after_close_does_not_update_widgets(self):
        window = make_window()
        window._rpm_generation = 1
        window._rpm_read_in_flight = True
        window._closing = True
        window._rpm_results.put((1, (1704, 2400)))

        window._consume_fan_result(expected_generation=1)

        self.assertEqual(window._cpu_rpm.calls, [])
        self.assertEqual(window._gpu_rpm.calls, [])
        self.assertFalse(window._rpm_read_in_flight)


class CReaderTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        compiler = shutil.which("cc")
        if compiler is None:
            raise unittest.SkipTest("C compiler is not available")

        cls.temp_dir = tempfile.TemporaryDirectory()
        cls.binary = Path(cls.temp_dir.name) / "nitrotune-ec-read"
        subprocess.run(
            [
                compiler,
                "-std=c11",
                "-Wall",
                "-Wextra",
                "-Werror",
                str(ROOT / "nitrotune-ec-read.c"),
                "-o",
                str(cls.binary),
            ],
            check=True,
            capture_output=True,
        )

    @classmethod
    def tearDownClass(cls):
        if hasattr(cls, "temp_dir"):
            cls.temp_dir.cleanup()

    def test_helper_rejects_arguments_without_output(self):
        result = subprocess.run(
            [str(self.binary), "0x13"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(result.stdout, b"")


if __name__ == "__main__":
    unittest.main()
