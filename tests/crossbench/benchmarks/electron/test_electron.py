# Copyright 2026 The Chromium Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from __future__ import annotations

import argparse
import datetime as dt
import json
import logging
import sys
import tempfile
import textwrap
import unittest
from types import SimpleNamespace
from unittest import mock

from crossbench import path as pth
from crossbench import plt
from crossbench.benchmarks.electron.electron import DEFAULT_REQUIRED_PHASES, \
    DEFAULT_STORY, ElectronStoryBenchmark, ExternalElectronStory, parse_env
from crossbench.benchmarks.electron.protocol import ExternalStoryPhase, \
    ExternalStoryProtocolError, ExternalStoryResult, SCHEMA_VERSION
from crossbench.browsers.settings import Settings
from crossbench.cli.config.browser_variants import BrowserVariantsConfig
from crossbench.probes.cb_perfetto.perfetto import PerfettoProbe
from crossbench.runner.runner import Runner
from crossbench.runner.timing import Timing
from tests.crossbench.mock_browser import MockChromium


def phase(start: float, end: float) -> dict[str, float]:
  return {
      "startTimeMs": start,
      "endTimeMs": end,
      "durationMs": end - start,
  }


def valid_result(run_id: str = "0-0-default") -> dict[str, object]:
  return {
      "schemaVersion": SCHEMA_VERSION,
      "runId": run_id,
      "story": DEFAULT_STORY,
      "status": "success",
      "valid": True,
      "phases": {
          name: phase(index * 10, (index + 1) * 10)
          for index, name in enumerate(DEFAULT_REQUIRED_PHASES)
      },
      "exit": {
          "code": 0,
          "signal": None,
      },
      "shutdown": {
          "status": "clean",
          "exitCode": 0,
          "signal": None,
      },
      "metadata": {
          "app": "fake"
      },
      "artifacts": {},
  }


class ExternalStoryProtocolTestCase(unittest.TestCase):

  def test_parse_phase(self) -> None:
    parsed = ExternalStoryPhase.parse("phase", phase(10, 15))
    self.assertEqual(parsed.duration_ms, 5)

  def test_parse_success_with_extra_phase(self) -> None:
    data = valid_result()
    phases = data["phases"]
    assert isinstance(phases, dict)
    phases["appSpecific"] = phase(50, 60)
    result = ExternalStoryResult.parse(data, "0-0-default", DEFAULT_STORY,
                                       DEFAULT_REQUIRED_PHASES)
    self.assertIn("appSpecific", result.phases)
    self.assertEqual(
        result.metrics()["phases"]["workbenchRestored"]["durationMs"], 10)
    self.assertNotIn("startTimeMs",
                     result.metrics()["phases"]["workbenchRestored"])
    self.assertEqual(result.metrics()["durationMs"], 60)

  def test_deprecated_process_spawn_alias_not_aggregated(self) -> None:
    data = valid_result()
    phases = data["phases"]
    assert isinstance(phases, dict)
    phases["processSpawn"] = phases["electronLaunch"]
    result = ExternalStoryResult.parse(data, "0-0-default", DEFAULT_STORY,
                                       DEFAULT_REQUIRED_PHASES)

    self.assertNotIn("processSpawn", result.metrics()["phases"])

  def test_required_phase_order(self) -> None:
    data = valid_result()
    phases = data["phases"]
    assert isinstance(phases, dict)
    phases["didFinishLoad"] = phase(5, 25)
    with self.assertRaisesRegex(ExternalStoryProtocolError,
                                "didFinishLoad.*firstWindow"):
      ExternalStoryResult.parse(data, "0-0-default", DEFAULT_STORY,
                                DEFAULT_REQUIRED_PHASES)

  def test_extra_phase_may_overlap(self) -> None:
    data = valid_result()
    phases = data["phases"]
    assert isinstance(phases, dict)
    phases["processSpawn"] = phase(0, 10)
    result = ExternalStoryResult.parse(data, "0-0-default", DEFAULT_STORY,
                                       DEFAULT_REQUIRED_PHASES)
    self.assertIn("processSpawn", result.phases)

  def test_schema_mismatch(self) -> None:
    data = valid_result()
    data["schemaVersion"] = 2
    with self.assertRaisesRegex(ExternalStoryProtocolError, "schemaVersion"):
      ExternalStoryResult.parse(data, "0-0-default", DEFAULT_STORY,
                                DEFAULT_REQUIRED_PHASES)

  def test_boolean_schema_version(self) -> None:
    data = valid_result()
    data["schemaVersion"] = True
    with self.assertRaisesRegex(ExternalStoryProtocolError, "schemaVersion"):
      ExternalStoryResult.parse(data, "0-0-default", DEFAULT_STORY,
                                DEFAULT_REQUIRED_PHASES)

  def test_boolean_exit_code(self) -> None:
    data = valid_result()
    data["exit"] = {"code": False}
    with self.assertRaisesRegex(ExternalStoryProtocolError, "invalid exit"):
      ExternalStoryResult.parse(data, "0-0-default", DEFAULT_STORY,
                                DEFAULT_REQUIRED_PHASES)

  def test_missing_clean_shutdown(self) -> None:
    data = valid_result()
    del data["shutdown"]
    with self.assertRaisesRegex(ExternalStoryProtocolError, "shutdown object"):
      ExternalStoryResult.parse(data, "0-0-default", DEFAULT_STORY,
                                DEFAULT_REQUIRED_PHASES)

  def test_forced_shutdown(self) -> None:
    data = valid_result()
    data["shutdown"] = {
        "status": "forced",
        "exitCode": 0,
        "signal": None,
    }
    with self.assertRaisesRegex(ExternalStoryProtocolError,
                                "shut down cleanly"):
      ExternalStoryResult.parse(data, "0-0-default", DEFAULT_STORY,
                                DEFAULT_REQUIRED_PHASES)

  def test_boolean_shutdown_exit_code(self) -> None:
    data = valid_result()
    data["shutdown"] = {
        "status": "clean",
        "exitCode": False,
        "signal": None,
    }
    with self.assertRaisesRegex(ExternalStoryProtocolError,
                                "shutdown exitCode"):
      ExternalStoryResult.parse(data, "0-0-default", DEFAULT_STORY,
                                DEFAULT_REQUIRED_PHASES)

  def test_missing_required_phase(self) -> None:
    data = valid_result()
    phases = data["phases"]
    assert isinstance(phases, dict)
    del phases["firstWindow"]
    with self.assertRaisesRegex(ExternalStoryProtocolError, "firstWindow"):
      ExternalStoryResult.parse(data, "0-0-default", DEFAULT_STORY,
                                DEFAULT_REQUIRED_PHASES)

  def test_crash_shaped_result(self) -> None:
    data = valid_result()
    data["crash"] = {}
    with self.assertRaisesRegex(ExternalStoryProtocolError, "crash"):
      ExternalStoryResult.parse(data, "0-0-default", DEFAULT_STORY,
                                DEFAULT_REQUIRED_PHASES)

  def test_failure_result(self) -> None:
    data = valid_result()
    data["status"] = "failure"
    data["valid"] = False
    data["error"] = {"message": "not ready"}
    with self.assertRaisesRegex(ExternalStoryProtocolError, "failure"):
      ExternalStoryResult.parse(data, "0-0-default", DEFAULT_STORY,
                                DEFAULT_REQUIRED_PHASES)

  def test_invalid_phase_duration(self) -> None:
    with self.assertRaisesRegex(ExternalStoryProtocolError, "durationMs"):
      ExternalStoryPhase.parse("bad", {
          "startTimeMs": 1,
          "endTimeMs": 2,
          "durationMs": 5,
      })

  def test_parse_env(self) -> None:
    self.assertEqual(
        parse_env("NAME=value=with=equals"), ("NAME", "value=with=equals"))
    with self.assertRaisesRegex(argparse.ArgumentTypeError, "NAME=VALUE"):
      parse_env("invalid")


class ExternalStoryInvocationTestCase(unittest.TestCase):

  def setUp(self) -> None:
    self._temp_dir = tempfile.TemporaryDirectory()
    self.addCleanup(self._temp_dir.cleanup)
    self.root = pth.LocalPath(self._temp_dir.name)
    self.app_executable = self.root / "app.exe"
    self.app_executable.touch()

  def test_successful_invocation(self) -> None:
    cli = self._write_story_cli("""
        request = json.loads(pathlib.Path(args.request).read_text())
        result = {
            "schemaVersion": 1,
            "runId": request["runId"],
            "story": request["story"],
            "status": "success",
            "valid": True,
            "phases": {
                name: {
                    "startTimeMs": index * 10,
                    "endTimeMs": (index + 1) * 10,
                    "durationMs": 10,
                }
                for index, name in enumerate(REQUIRED_PHASES)
            },
            "shutdown": {
                "status": "clean",
                "exitCode": 0,
                "signal": None,
            },
            "metadata": {"app": "fake"},
            "artifacts": {},
        }
        pathlib.Path(args.result).write_text(json.dumps(result))
        pathlib.Path(args.log).write_text("story log")
    """)
    story = self._story(cli, use_app_root=True)
    run = self._run()
    story.run(run)

    request_path = run.out_dir / "external_story" / "request.json"
    with request_path.open(encoding="utf-8") as request_file:
      request = json.load(request_file)
    self.assertEqual(request["launchArgs"], ["--trace-startup-file=trace.pb"])
    self.assertEqual(request["env"], {"STORY_ENV": "value"})
    self.assertEqual(request["appRoot"], str(self.root))
    self.assertNotIn("appExecutable", request)
    self.assertTrue(pth.LocalPath(request["userDataDir"]).is_dir())
    self.assertEqual(story.result(run).metadata["app"], "fake")
    self.assertTrue(story.result_path(run).is_file())

  def test_app_root_uses_browser_variant_executable(self) -> None:
    cli = self._write_story_cli("""
        request = json.loads(pathlib.Path(args.request).read_text())
        result = {
            "schemaVersion": 1,
            "runId": request["runId"],
            "story": request["story"],
            "status": "success",
            "valid": True,
            "phases": {
                name: {
                    "startTimeMs": index * 10,
                    "endTimeMs": (index + 1) * 10,
                    "durationMs": 10,
                }
                for index, name in enumerate(REQUIRED_PHASES)
            },
            "shutdown": {
                "status": "clean",
                "exitCode": 0,
                "signal": None,
            },
            "metadata": {},
            "artifacts": {},
        }
        pathlib.Path(args.result).write_text(json.dumps(result))
    """)
    story = ExternalElectronStory(
        DEFAULT_STORY,
        pth.AnyPath(sys.executable),
        cli,
        None,
        self.root,
        None,
        dt.timedelta(seconds=5),
        DEFAULT_REQUIRED_PHASES,
        {},
    )
    run = self._run()
    variant_executable = self.root / "variant electron.exe"
    variant_executable.touch()
    run.browser.path = variant_executable

    story.run(run)

    request_path = run.out_dir / "external_story" / "request.json"
    request = json.loads(request_path.read_text(encoding="utf-8"))
    self.assertEqual(request["electronExecutable"], str(variant_executable))

  def test_invalid_json(self) -> None:
    cli = self._write_story_cli("""
        pathlib.Path(args.result).write_text("not-json")
    """)
    with self.assertRaisesRegex(RuntimeError, "invalid JSON"):
      self._story(cli).run(self._run())

  def test_nonzero_exit(self) -> None:
    cli = self._write_story_cli("""
        request = json.loads(pathlib.Path(args.request).read_text())
        result = {
            "schemaVersion": 1,
            "runId": request["runId"],
            "story": request["story"],
            "status": "failure",
            "valid": False,
            "phases": {},
            "metadata": {},
            "artifacts": {},
            "error": {
                "code": "invalidLaunchTarget",
                "message": "missing build",
            },
        }
        pathlib.Path(args.result).write_text(json.dumps(result))
        raise SystemExit(7)
    """)
    with self.assertRaisesRegex(RuntimeError, "code 7.*invalidLaunchTarget"):
      self._story(cli).run(self._run())

  def test_timeout(self) -> None:
    cli = self._write_story_cli("time.sleep(10)", import_time=True)
    story = self._story(cli, timeout=dt.timedelta(milliseconds=50))
    with self.assertRaisesRegex(TimeoutError, "timed out"):
      story.run(self._run())

  def test_failed_run_does_not_reuse_result(self) -> None:
    cli = self._write_story_cli("""
        request = json.loads(pathlib.Path(args.request).read_text())
        result = {
            "schemaVersion": 1,
            "runId": request["runId"],
            "story": request["story"],
            "status": "success",
            "valid": True,
            "phases": {
                name: {
                    "startTimeMs": index * 10,
                    "endTimeMs": (index + 1) * 10,
                    "durationMs": 10,
                }
                for index, name in enumerate(REQUIRED_PHASES)
            },
            "shutdown": {
                "status": "clean",
                "exitCode": 0,
                "signal": None,
            },
            "metadata": {},
            "artifacts": {},
        }
        pathlib.Path(args.result).write_text(json.dumps(result))
    """)
    story = self._story(cli)
    successful_run = self._run()
    story.run(successful_run)
    self.assertTrue(story.has_result(successful_run))

    failing_cli = self._write_story_cli("raise SystemExit(1)")
    story._story_cli = failing_cli
    failing_run = self._run()
    with self.assertRaisesRegex(RuntimeError, "exited with code 1"):
      story.run(failing_run)
    self.assertFalse(story.has_result(failing_run))
    self.assertTrue(story.has_result(successful_run))

  def test_generic_non_node_runner_argv(self) -> None:
    cli = self._write_story_cli("""
        request = json.loads(pathlib.Path(args.request).read_text())
        pathlib.Path(args.result).write_text(
            json.dumps({
                "schemaVersion": 1,
                "runId": request["runId"],
                "story": request["story"],
                "status": "success",
                "valid": True,
                "phases": {
                    name: {
                        "startTimeMs": index * 10,
                        "endTimeMs": (index + 1) * 10,
                        "durationMs": 10,
                    }
                    for index, name in enumerate(REQUIRED_PHASES)
                },
                "shutdown": {
                    "status": "clean",
                    "exitCode": 0,
                    "signal": None,
                },
                "metadata": {},
                "artifacts": {},
            }))
    """)
    story = ExternalElectronStory(
        DEFAULT_STORY,
        None,
        None,
        self.app_executable,
        None,
        None,
        dt.timedelta(seconds=5),
        DEFAULT_REQUIRED_PHASES,
        {},
        (sys.executable, str(cli)),
        cli.parent,
    )

    run = self._run()
    story.run(run)

    self.assertTrue(story.has_result(run))

  def test_runner_merges_repetitions_without_starting_browser(self) -> None:
    self.addCleanup(logging.shutdown)
    cli = self._write_story_cli("""
        request = json.loads(pathlib.Path(args.request).read_text())
        result = {
            "schemaVersion": 1,
            "runId": request["runId"],
            "story": request["story"],
            "status": "success",
            "valid": True,
            "phases": {
                name: {
                    "startTimeMs": index * 10,
                    "endTimeMs": (index + 1) * 10,
                    "durationMs": 10,
                }
                for index, name in enumerate(REQUIRED_PHASES)
            },
            "shutdown": {
                "status": "clean",
                "exitCode": 0,
                "signal": None,
            },
            "metadata": {},
            "artifacts": {},
        }
        pathlib.Path(args.result).write_text(json.dumps(result))
    """)
    story = self._story(cli)
    benchmark = ElectronStoryBenchmark((story,))
    browser_path = self.root / "chromium.exe"
    browser_path.touch()
    browser = MockChromium(
        "electron-flags",
        path=browser_path,
        settings=Settings(platform=plt.PLATFORM))
    runner = Runner(
        self.root / "results",
        browsers=(browser,),
        benchmark=benchmark,
        repetitions=2,
        platform=plt.PLATFORM,
        timing=Timing(cool_down_time=dt.timedelta()),
        create_symlinks=False,
        in_memory_result_db=True,
        throw=True)

    runner.run()

    self.assertFalse(browser.did_run)
    metrics_probe = next(
        probe for probe in runner.probes if probe.name == "electron.story")
    merged_path = runner.repetitions_groups[0].results[metrics_probe].json
    with merged_path.open(encoding="utf-8") as merged_file:
      merged = json.load(merged_file)
    self.assertEqual(merged["phases/workbenchRestored/durationMs"]["average"],
                     10)
    self.assertNotIn("phases/workbenchRestored/startTimeMs", merged)
    self.assertEqual(merged["durationMs"]["average"], 60)

  @unittest.skipUnless(plt.PLATFORM.is_win, "Windows startup tracing test")
  def test_runner_passes_perfetto_startup_flags(self) -> None:
    self.addCleanup(logging.shutdown)
    cli = self._write_story_cli("""
        request = json.loads(pathlib.Path(args.request).read_text())
        trace_flag = next(
            arg for arg in request["launchArgs"]
            if arg.startswith("--trace-startup-file="))
        pathlib.Path(trace_flag.split("=", 1)[1]).write_bytes(b"trace")
        result = {
            "schemaVersion": 1,
            "runId": request["runId"],
            "story": request["story"],
            "status": "success",
            "valid": True,
            "phases": {
                name: {
                    "startTimeMs": index * 10,
                    "endTimeMs": (index + 1) * 10,
                    "durationMs": 10,
                }
                for index, name in enumerate(REQUIRED_PHASES)
            },
            "shutdown": {
                "status": "clean",
                "exitCode": 0,
                "signal": None,
            },
            "metadata": {},
            "artifacts": {},
        }
        pathlib.Path(args.result).write_text(json.dumps(result))
    """)
    story = self._story(cli)
    browser_path = self.root / "chromium.exe"
    browser_path.touch()
    browser = MockChromium(
        "electron-tracing",
        path=browser_path,
        settings=Settings(platform=plt.PLATFORM))
    perfetto_probe = PerfettoProbe.parse_str("electron-startup")
    runner = Runner(
        self.root / "perfetto-results",
        browsers=(browser,),
        benchmark=ElectronStoryBenchmark((story,)),
        probes=(perfetto_probe,),
        repetitions=2,
        platform=plt.PLATFORM,
        timing=Timing(cool_down_time=dt.timedelta()),
        create_symlinks=False,
        in_memory_result_db=True,
        throw=True)

    runner.run()

    launch_args_by_run = []
    trace_paths = []
    for run in runner.runs:
      request_path = run.out_dir / "external_story" / "request.json"
      with request_path.open(encoding="utf-8") as request_file:
        launch_args = json.load(request_file)["launchArgs"]
      launch_args_by_run.append(launch_args)
      trace_flag = next(
          arg for arg in launch_args if arg.startswith("--trace-startup-file="))
      trace_paths.append(trace_flag.split("=", 1)[1])
      self.assertTrue(run.results[perfetto_probe].perfetto.is_file())
    self.assertEqual(len(set(trace_paths)), 2)
    launch_args = launch_args_by_run[0]
    self.assertTrue(
        any(
            arg.startswith("--trace-perfetto-config-file=")
            for arg in launch_args))
    self.assertTrue(
        any(arg.startswith("--trace-startup-file=") for arg in launch_args))
    self.assertTrue(any("--perfetto-code-logger" in arg for arg in launch_args))

  def test_launcher_flags_preserve_merged_values_and_order(self) -> None:
    run = self._run()
    run.browser.get_launcher_flags.return_value = (
        "--js-flags=--no-opt,--trace-ic",
        "--enable-features=FeatureA,FeatureB",
        "--disable-features=FeatureC",
        "--path-with-space=C:\\Program Files\\Electron",
    )
    self.assertEqual(
        ExternalElectronStory._launcher_flags(run),
        list(run.browser.get_launcher_flags.return_value))

  def test_prepare_cli_args_infers_browser_from_empty_config(self) -> None:
    story = self._story(self._write_story_cli(""))
    benchmark = ElectronStoryBenchmark((story,))
    args = SimpleNamespace(browser=None, browser_config=BrowserVariantsConfig())

    with mock.patch.object(
        ExternalElectronStory,
        "launcher_version",
        new_callable=mock.PropertyMock,
        return_value="Chromium 100.2.3.4"):
      benchmark.prepare_cli_args(args)

    self.assertEqual(len(args.browser), 1)
    self.assertIsNone(args.browser_config)

  def test_launcher_version_accepts_packaged_app_version(self) -> None:
    story = self._story(self._write_story_cli(""))

    with mock.patch.object(
        plt.PLATFORM, "app_version", return_value="1.137.0"):
      self.assertEqual(story.launcher_version, "Chromium 137.0.0.0")
    with mock.patch.object(
        plt.PLATFORM, "app_version", return_value="1.9.0"):
      self.assertEqual(story.launcher_version, "Chromium 100.0.0.0")

  def test_prepare_cli_args_preserves_browser_config_path(self) -> None:
    story = self._story(self._write_story_cli(""))
    benchmark = ElectronStoryBenchmark((story,))
    browser_config = self.root / "browsers.hjson"
    args = SimpleNamespace(browser=None, browser_config=browser_config)

    benchmark.prepare_cli_args(args)

    self.assertIs(args.browser_config, browser_config)
    self.assertIsNone(args.browser)

  def test_prepare_cli_args_preserves_parsed_browser_config(self) -> None:
    story = self._story(self._write_story_cli(""))
    benchmark = ElectronStoryBenchmark((story,))
    browser_config = BrowserVariantsConfig()
    browser_config._variants.append(mock.Mock())
    args = SimpleNamespace(browser=None, browser_config=browser_config)

    benchmark.prepare_cli_args(args)

    self.assertIs(args.browser_config, browser_config)
    self.assertIsNone(args.browser)

  def test_interrupt_kills_process_tree(self) -> None:
    story = self._story(self._write_story_cli(""))
    process = mock.Mock()
    process.wait.side_effect = [KeyboardInterrupt, 0]
    platform = mock.Mock()
    platform.popen.return_value = process
    run = self._run()
    run.browser_platform = platform

    with self.assertRaises(KeyboardInterrupt):
      story._run_process(run, ("runtime", "story"), self.root / "process.log")

    platform.kill.assert_called_once_with(process)
    self.assertEqual(process.wait.call_count, 2)

  def _story(
      self,
      cli: pth.LocalPath,
      timeout: dt.timedelta = dt.timedelta(seconds=5),
      use_app_root: bool = False,
  ) -> ExternalElectronStory:
    return ExternalElectronStory(
        DEFAULT_STORY,
        pth.AnyPath(sys.executable),
        cli,
        None if use_app_root else self.app_executable,
        self.root if use_app_root else None,
        self.app_executable if use_app_root else None,
        timeout,
        DEFAULT_REQUIRED_PHASES,
        {"STORY_ENV": "value"},
    )

  def _run(self) -> SimpleNamespace:
    out_dir = self.root / "run"
    out_dir.mkdir(exist_ok=True)
    browser = mock.Mock()
    browser.path = self.app_executable
    browser.get_launcher_flags.return_value = ("--trace-startup-file=trace.pb",)
    return SimpleNamespace(
        out_dir=out_dir,
        index=0,
        repetition_name="0",
        temperature="default",
        browser=browser,
        session=mock.Mock(),
        browser_platform=plt.PLATFORM,
        get_probe_context=lambda probe_cls: None,
    )

  def _write_story_cli(self,
                       body: str,
                       import_time: bool = False) -> pth.LocalPath:
    time_import = "import time" if import_time else ""
    script = textwrap.dedent(f"""
        import argparse
        import json
        import pathlib
        {time_import}

        REQUIRED_PHASES = {DEFAULT_REQUIRED_PHASES!r}
        parser = argparse.ArgumentParser()
        parser.add_argument("--request", required=True)
        parser.add_argument("--result", required=True)
        parser.add_argument("--log", required=True)
        args = parser.parse_args()
    """) + textwrap.dedent(body)
    path = self.root / f"story_{len(tuple(self.root.glob('story_*.py')))}.py"
    path.write_text(script, encoding="utf-8")
    return path


if __name__ == "__main__":
  unittest.main()
