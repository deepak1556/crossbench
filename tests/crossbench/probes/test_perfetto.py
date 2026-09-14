# Copyright 2023 The Chromium Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.
from __future__ import annotations

import argparse
import logging
import unittest
from unittest import mock

from typing_extensions import override

import crossbench.path as pth
from crossbench.browsers.settings import Settings
from crossbench.cli.config.probe import ProbeConfig
from crossbench.cli.config.probe_list import ProbeListConfig
from crossbench.plt.android_adb import Adb
from crossbench.plt.arch import MachineArch
from crossbench.probes.all import PerfettoProbe
from crossbench.probes.cb_perfetto.context.android import \
    AndroidPerfettoProbeContext
from crossbench.probes.cb_perfetto.context.chromeos import \
    ChromeOsPerfettoProbeContext
from crossbench.probes.cb_perfetto.context.desktop import \
    DesktopPerfettoProbeContext
from crossbench.probes.cb_perfetto.context.windows import \
    ExternalWindowsPerfettoProbeContext
from crossbench.probes.cb_perfetto.downloader import PerfettoToolDownloader
from crossbench.probes.cb_perfetto.perfetto import TraceConfig
from protoc import trace_config_pb2
from tests import test_helper
from tests.crossbench.base import CrossbenchConfigTestMixin, \
    CrossbenchFakeFsTestCase
from tests.crossbench.mock_browser import MockChromeStable
from tests.crossbench.mock_helper import AndroidAdbMockPlatform, \
    ChromeOsSshMockPlatform, LinuxMockPlatform, MacOsMockPlatform, MockPopen, \
    MockPopenState, WinMockPlatform
from tests.crossbench.runner.helper import MockRun


class TraceConfigTestCase(unittest.TestCase):

  def test_parse_preset(self):
    config = TraceConfig.parse("v8")
    self.assertIsInstance(config, TraceConfig)
    # v8 preset has some data sources
    self.assertTrue(len(config.trace_config.data_sources) > 0)

  def test_parse_dict_raw_proto(self):
    config = TraceConfig.parse({
        "buffers": [{
            "size_kb": 1024
        }],
        "data_sources": [{
            "config": {
                "name": "linux.process_stats"
            }
        }]
    })
    self.assertIsInstance(config, TraceConfig)
    self.assertEqual(len(config.trace_config.buffers), 1)
    self.assertEqual(config.trace_config.buffers[0].size_kb, 1024)
    self.assertEqual(len(config.trace_config.data_sources), 1)
    self.assertEqual(config.trace_config.data_sources[0].config.name,
                     "linux.process_stats")

  def test_parse_str_close_match(self):
    with self.assertLogs(level="ERROR") as cm:
      # "v8-profiling" is a valid preset, "v8-profiler" is close.
      config = TraceConfig.parse_str("v8-profiler")
      self.assertIsInstance(config, TraceConfig)
      # It should have picked "v8-profiling"
      self.assertEqual(config, TraceConfig.parse_str("v8-profiling"))
    self.assertIn("v8-profiling", cm.output[0])


class PerfettoProbeTestCase(unittest.TestCase):

  def test_parse_empty(self):
    probe = PerfettoProbe.parse_str("")
    self.assertIsInstance(probe, PerfettoProbe)
    self.assertEqual(probe.trace_config,
                     TraceConfig.parse_str("default").trace_config)

  def test_create_empty(self):
    probe = PerfettoProbe()
    self.assertIsInstance(probe, PerfettoProbe)
    self.assertEqual(probe.trace_config,
                     TraceConfig.parse_str("default").trace_config)

  def test_parse_tags_mixed(self):
    probe = PerfettoProbe.parse_tags("tag1,+tag2,-tag3")
    self.assertSequenceEqual(probe.enabled_tags, ("tag1", "tag2"))
    self.assertSequenceEqual(probe.disabled_tags, ("tag3",))

  def test_merged_simple(self):
    probe = PerfettoProbe.parse_str("v8")
    merged = probe.trace_config
    self.assertIsInstance(merged, trace_config_pb2.TraceConfig)

  def test_merged_tags(self):
    probe = PerfettoProbe.parse_str("tag1,-tag2")
    merged = probe.trace_config
    track_event_configs = [
        ds.config.track_event_config
        for ds in merged.data_sources
        if ds.config.name == "track_event"
    ]
    self.assertEqual(len(track_event_configs), 1)
    te_config = track_event_configs[0]
    self.assertIn("tag1", te_config.enabled_tags)
    self.assertIn("tag2", te_config.disabled_tags)

  def test_merged_categories(self):
    probe = PerfettoProbe.parse_dict({
        "enabled_categories": ["cat1"],
        "disabled_categories": ["cat2"],
        "enabled_tags": ["cat4"]
    })
    merged = probe.trace_config
    self.assertIsInstance(merged, trace_config_pb2.TraceConfig)
    track_event_configs = [
        ds.config.track_event_config
        for ds in merged.data_sources
        if ds.config.name == "track_event"
    ]
    self.assertEqual(len(track_event_configs), 1)
    te_config = track_event_configs[0]
    self.assertIn("cat1", te_config.enabled_categories)
    self.assertIn("cat2", te_config.disabled_categories)
    self.assertIn("cat4", te_config.enabled_tags)

  def test_parse_dict_combined(self):
    probe = PerfettoProbe.parse_dict({
        "trace_config": "v8",
        "tags": ["tag1"],
        "categories": ["cat1"]
    })
    self.assertIsInstance(probe, PerfettoProbe)
    # v8 preset has some data sources
    self.assertTrue(len(probe.trace_config.data_sources) > 0)
    merged = probe.trace_config
    track_event_configs = [
        ds.config.track_event_config
        for ds in merged.data_sources
        if ds.config.name == "track_event"
    ]
    self.assertEqual(len(track_event_configs), 1)
    te_config = track_event_configs[0]
    self.assertIn("tag1", te_config.enabled_tags)
    self.assertIn("cat1", te_config.enabled_categories)

  def test_empty_dict_config(self):
    probe = PerfettoProbe.parse_dict({})
    self.assertIsInstance(probe, PerfettoProbe)
    self.assertEqual(probe.trace_config,
                     TraceConfig.parse_str("default").trace_config)

  def test_missing_config(self):
    with self.assertRaisesRegex(argparse.ArgumentTypeError, "unknown 1"):
      PerfettoProbe.parse_dict({"preset": "unknown 1"})
    with self.assertRaisesRegex(argparse.ArgumentTypeError, "unknown 1"):
      PerfettoProbe.parse_str("unknown 1")

  def test_parse_config(self):
    trace_config = """
        buffers: {
            size_kb: 1234
            fill_policy: DISCARD
        }
    """
    probe: PerfettoProbe = PerfettoProbe.parse_dict(
        {"trace_config": trace_config})
    self.assertEqual(probe.trace_config.buffers[0].size_kb, 1234)
    self.assertEqual(pth.AnyPath("perfetto"), probe.perfetto_bin)

  def test_parse_example_config(self):
    config_file = test_helper.config_dir() / "doc/probe/perfetto.config.hjson"
    self.assertTrue(config_file.is_file())
    probes = ProbeListConfig.parse(config_file).probes
    self.assertEqual(len(probes), 1)
    probe = probes[0]
    self.assertIsInstance(probe, PerfettoProbe)

  def test_trace_config_preset_invalid_file(self):
    trace_config_dir = test_helper.config_dir() / "probe/perfetto/trace_config"
    for config_file in trace_config_dir.glob("*.pbtxt"):
      self.fail(f"Invalid preset file extension, use .textpb: {config_file}")

  def test_trace_config_preset(self):
    trace_config_dir = test_helper.config_dir() / "probe/perfetto/trace_config"
    preset_count = 0
    for config_file in trace_config_dir.glob("*.txtpb"):
      preset_count += 1
      with self.subTest(config_file=str(config_file)):
        probe_a = PerfettoProbe.parse_dict({"trace_config": config_file.stem})
        probe_b = PerfettoProbe.parse_str(config_file.stem)
        probe_c = PerfettoProbe.parse_str(str(config_file))
        self.assertEqual(probe_a.trace_config, probe_b.trace_config)
        self.assertEqual(probe_a.trace_config, probe_c.trace_config)
        for data_source in probe_b.trace_config.data_sources:
          config = data_source.config
          self.assertNotEqual(
              config.name, "org.chromium.trace_metadata",
              "Please use the new org.chromium.trace_metadata2 data_source "
              "without the added json-serialized categories.")
          self.assertFalse(
              config.chrome_config and config.chrome_config.trace_config,
              "Please use the org.chromium.trace_metadata2 data source "
              "which does not require the json-serialized trace_config")
    self.assertGreater(preset_count, 0)

  def test_preset_file_from_probe_config(self):
    trace_config_file = TraceConfig.preset_dir() / "v8.txtpb"
    self.assertTrue(trace_config_file.is_file())
    probe = PerfettoProbe.parse_str(str(trace_config_file))
    probe_a = ProbeConfig.parse("perfetto:v8").new_instance()
    self.assertTrue(trace_config_file.is_file())
    src_str = f"perfetto:{trace_config_file}"
    probe_b = ProbeConfig.parse(src_str).new_instance()
    self.assertEqual(probe, probe_a)
    self.assertEqual(probe, probe_b)


class PerfettoToolDownloaderTestCase(CrossbenchFakeFsTestCase):

  def test_download_linux(self):
    platform = LinuxMockPlatform()
    self._download_perfetto_tool(platform, "linux-arm64")
    platform = LinuxMockPlatform()
    platform.machine = MachineArch.ARM_32
    self._download_perfetto_tool(platform, "linux-arm")
    platform = LinuxMockPlatform()
    platform.machine = MachineArch.X64
    self._download_perfetto_tool(platform, "linux-x64")

  def test_download_macos(self):
    platform = MacOsMockPlatform()
    self._download_perfetto_tool(platform, "mac-arm64")
    platform = MacOsMockPlatform()
    platform.machine = MachineArch.X64
    self._download_perfetto_tool(platform, "mac-amd64")

  def test_download_win(self):
    platform = WinMockPlatform()
    platform.machine = MachineArch.X64
    self._download_perfetto_tool(platform, "windows-amd64")

  def test_download_win_invalid(self):
    platform = WinMockPlatform()
    platform.machine = MachineArch.ARM_64
    with self.assertRaises(ValueError):
      self._download_perfetto_tool(platform, "win-arm64")

  def _download_perfetto_tool(self, platform, key):
    platform.use_mock_name = False
    tool = "traceconv.exe" if platform.is_win else "traceconv"
    download_path = platform.cache_dir("perfetto") / f"v53.0/{tool}"
    platform.expect_download(
        "https://commondatastorage.googleapis.com/perfetto-luci-artifacts/"
        f"v53.0/{key}/{tool}", download_path)
    platform.expect_sh(
        download_path,
        "--version",
        result=("Perfetto v53.0-7a9a6a0 "
                "(7a9a6a0587348bffd1796b66a1da33cc1ea421d8)"))
    result = PerfettoToolDownloader("traceconv", platform=platform).download()
    self.assertTrue(platform.exists(result))
    # downloading the same will use the locally cached version
    result = PerfettoToolDownloader("traceconv", platform=platform).download()
    self.assertTrue(platform.exists(result))


class PerfettoProbeFunctionalTestCase(CrossbenchConfigTestMixin,
                                      CrossbenchFakeFsTestCase):

  @override
  def setUp(self) -> None:
    super().setUp()
    self.setup_perfetto_config_presets()

  def test_attach_v8_code_logger(self):
    trace_config = trace_config_pb2.TraceConfig()
    data_source = trace_config.data_sources.add()
    data_source.config.name = "dev.v8.code"
    probe = PerfettoProbe(trace_config=trace_config)

    platform = LinuxMockPlatform()
    MockChromeStable.setup_fs(self.fs, platform)
    browser = MockChromeStable(
        "mock browser", settings=Settings(platform=platform))

    probe.attach(browser)
    self.assertIn("--perfetto-code-logger", browser.js_flags)

  def test_log_results(self):
    probe = PerfettoProbe.parse_str("v8")
    run = mock.Mock()
    run.results = {probe: mock.Mock(file=self.create_file("perfetto.trace.pb"))}

    with self.assertLogs(level=logging.INFO) as cm:
      probe.log_run_result(run)
    self.assertIn("Perfetto trace results", cm.output[1])

    group = mock.Mock()
    group.runs = [run]
    with self.assertLogs(level=logging.INFO) as cm:
      probe.log_browsers_result(group)
    self.assertIn("Perfetto trace results", cm.output[1])

  def test_help_text_items(self):
    # Ensure there is at least one preset in the fake FS
    dummy_preset = TraceConfig.preset_dir() / "dummy_preset.txtpb"
    self.fs.create_file(dummy_preset)
    help_items = TraceConfig.help_text_items()
    self.assertTrue(any(k == "presets" for k, v in help_items))
    presets_str = dict(help_items)["presets"]
    self.assertIn("dummy_preset", presets_str)

  def test_create_context_desktop(self):
    probe = PerfettoProbe.parse_str("v8")
    host_platform = LinuxMockPlatform()

    run_linux = mock.Mock()
    run_linux.out_dir = pth.LocalPath("/tmp")
    run_linux.browser_platform = host_platform
    context = probe.create_context(run_linux)
    self.assertIsInstance(context, DesktopPerfettoProbeContext)

  def test_create_context_android(self):
    probe = PerfettoProbe.parse_str("v8")
    host_platform = LinuxMockPlatform()

    adb_bin = host_platform.path("/usr/bin/adb")
    self.fs.create_file(adb_bin)

    adb = mock.Mock(spec=Adb)
    adb.host_platform = host_platform
    adb.serial_id = "777"
    run_android = mock.Mock()
    run_android.out_dir = pth.LocalPath("/tmp")
    run_android.browser_platform = AndroidAdbMockPlatform(
        host_platform=host_platform, adb=adb)
    context = probe.create_context(run_android)
    self.assertIsInstance(context, AndroidPerfettoProbeContext)

  def test_create_context_chrome_os(self):
    probe = PerfettoProbe.parse_str("v8")
    host_platform = LinuxMockPlatform()

    run_chromeos = mock.Mock()
    run_chromeos.out_dir = pth.LocalPath("/tmp")
    run_chromeos.browser_platform = ChromeOsSshMockPlatform(
        host_platform=host_platform,
        host="host",
        port=22,
        ssh_port=22,
        ssh_user="user")
    context = probe.create_context(run_chromeos)
    self.assertIsInstance(context, ChromeOsPerfettoProbeContext)

  def test_create_context_external_windows_app(self):
    probe = PerfettoProbe.parse_str("v8")
    run_windows = mock.Mock()
    run_windows.out_dir = pth.LocalPath("/tmp")
    run_windows.browser_platform = WinMockPlatform()
    run_windows.benchmark.manages_browser_process = False
    context = probe.create_context(run_windows)
    self.assertIsInstance(context, ExternalWindowsPerfettoProbeContext)

  def test_get_extra_probes(self):
    probe = PerfettoProbe.parse_str("v8")
    runner = mock.Mock()
    extra_probes = list(probe.get_extra_probes(runner))
    self.assertIsInstance(extra_probes, list)


class PerfettoProbeContextTestCase(CrossbenchConfigTestMixin,
                                   CrossbenchFakeFsTestCase):

  @override
  def setUp(self) -> None:
    super().setUp()
    self.setup_perfetto_config_presets()

  def test_desktop_context_lifecycle(self):
    probe = PerfettoProbe.parse_str("v8")
    platform = LinuxMockPlatform(fake_fs=self.fs)
    platform.install_mock_binary("tracebox", "/usr/bin/tracebox")
    platform.install_mock_binary("perfetto", "/usr/bin/perfetto")

    tracebox_proc = MockPopen()
    platform.popens.append(tracebox_proc)

    MockChromeStable.setup_fs(self.fs, platform)
    browser = MockChromeStable(
        "mock browser", settings=Settings(platform=platform))
    browser.performance_mark = mock.Mock()
    browser_session = mock.Mock()
    browser_session.browser = browser
    browser_session.root_dir = pth.LocalPath("/tmp/results")
    self.fs.create_dir(browser_session.root_dir)
    run = MockRun(
        runner=mock.Mock(), browser_session=browser_session, story=mock.Mock())
    # MockRun calculates out_dir from browser_session.root_dir
    self.fs.create_dir(run.out_dir)
    run.get_default_probe_result_path = mock.Mock(return_value=run.out_dir /
                                                  "perfetto")

    context = DesktopPerfettoProbeContext(probe, run)

    # setup
    platform.expect_sh("tracebox", "traced", "traced_probes", result="")
    self.assertEqual(tracebox_proc.state, MockPopenState.UNUSED)
    context.setup()
    self.assertTrue(self.fs.exists(run.out_dir / "perfetto_config.textproto"))
    self.assertEqual(tracebox_proc.state, MockPopenState.RUNNING)

    # start
    platform.expect_sh(
        "tracebox",
        "perfetto",
        "--background",
        "--config",
        str(run.out_dir / "perfetto_config.textproto"),
        "--txt",
        "--out",
        str(run.out_dir / "perfetto.trace.pb"),
        result="123")
    context.start()
    run.browser.performance_mark.assert_called_with("probe-perfetto-start")
    self.assertEqual(tracebox_proc.state, MockPopenState.RUNNING)

    context.stop()
    self.assertEqual(tracebox_proc.state, MockPopenState.RUNNING)
    run.browser.performance_mark.assert_called_with("probe-perfetto-stop")

    # teardown
    trace_file = run.browser_platform.local_path(context.result_path)
    self.fs.create_file(trace_file)
    platform.expect_sh("gzip", str(trace_file))
    self.fs.create_file(trace_file.with_suffix(".pb.gz"))

    context.teardown()
    self.assertEqual(tracebox_proc.state, MockPopenState.TERMINATED)


if __name__ == "__main__":
  test_helper.run_pytest(__file__)
