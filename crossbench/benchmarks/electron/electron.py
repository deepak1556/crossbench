# Copyright 2026 The Chromium Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import subprocess
from typing import TYPE_CHECKING, Any, ClassVar, Final, Mapping

from typing_extensions import override

from crossbench import path as pth
from crossbench import plt
from crossbench.benchmarks.base import Benchmark
from crossbench.benchmarks.electron.protocol import ExternalStoryResult, \
    SCHEMA_VERSION
from crossbench.cli.config.browser import BrowserConfig, BrowserType
from crossbench.parse import DurationParser, ObjectParser, PathParser
from crossbench.probes.cb_perfetto.context.windows import \
    ExternalWindowsPerfettoProbeContext
from crossbench.probes.cb_perfetto.perfetto import PerfettoProbe
from crossbench.stories.story import Story

if TYPE_CHECKING:
  from crossbench.cli.parser import CBArgumentParser
  from crossbench.plt.types import TupleCmdArgs
  from crossbench.runner.run import Run
  from crossbench.types import JsonDict

DEFAULT_STORY: Final[str] = "vscode.empty-workbench.cold-start"
DEFAULT_REQUIRED_PHASES: Final[tuple[str, ...]] = (
    "processSpawn",
    "firstWindow",
    "didFinishLoad",
    "monacoWorkbench",
    "workbenchRestored",
)
PROCESS_CLEANUP_GRACE: Final[dt.timedelta] = dt.timedelta(seconds=5)


def parse_env(value: str) -> tuple[str, str]:
  name, separator, env_value = value.partition("=")
  if not separator or not name:
    raise argparse.ArgumentTypeError(
        f"Expected environment variable NAME=VALUE, got {value!r}.")
  return name, env_value


class ExternalElectronStory(Story):
  """Runs an app-owned Playwright Electron story process."""

  @classmethod
  @override
  def all_story_names(cls) -> tuple[str, ...]:
    return (DEFAULT_STORY,)

  def __init__(
      self,
      name: str,
      story_runtime: pth.AnyPath,
      story_cli: pth.LocalPath,
      app_executable: pth.LocalPath | None,
      app_root: pth.LocalPath | None,
      electron_executable: pth.LocalPath | None,
      timeout: dt.timedelta,
      required_phases: tuple[str, ...],
      env: Mapping[str, str],
  ) -> None:
    super().__init__(name, duration=timeout)
    self._story_runtime = story_runtime
    self._story_cli = story_cli
    self._app_executable = app_executable
    self._app_root = app_root
    if bool(app_executable) == bool(app_root):
      raise ValueError(
          "Exactly one of app_executable or app_root must be provided.")
    self._electron_executable = electron_executable
    self._timeout = timeout
    self._required_phases = required_phases
    self._env = dict(env)
    self._results: dict[int, ExternalStoryResult] = {}
    self._result_paths: dict[int, pth.LocalPath] = {}

  def result(self, run: Run) -> ExternalStoryResult:
    run_key = id(run)
    if run_key not in self._results:
      raise RuntimeError("External story has not produced a result.")
    return self._results[run_key]

  def result_path(self, run: Run) -> pth.LocalPath:
    run_key = id(run)
    if run_key not in self._result_paths:
      raise RuntimeError("External story has not produced a result path.")
    return self._result_paths[run_key]

  @property
  def launcher_executable(self) -> pth.LocalPath:
    if self._electron_executable:
      return self._electron_executable
    if self._app_executable and self._app_executable.is_file():
      return self._app_executable
    raise argparse.ArgumentTypeError(
        "Specify --electron-executable or --browser when --app-executable "
        "is not an executable file.")

  @property
  def launcher_version(self) -> str:
    version = plt.PLATFORM.app_version(self.launcher_executable).strip()
    if " " in version:
      version = version.rsplit(" ", maxsplit=1)[1]
    parts = version.removeprefix("v").split(".")
    parts.extend("0" for _ in range(4 - len(parts)))
    return ".".join(parts)

  def has_result(self, run: Run) -> bool:
    return id(run) in self._results

  @override
  def run(self, run: Run) -> None:
    run_key = id(run)
    self._results.pop(run_key, None)
    self._result_paths.pop(run_key, None)
    paths = self._prepare_run_paths(run)
    request = self._create_request(run, paths)
    self._write_json(paths["request"], request)
    command = (
        self._story_runtime,
        self._story_cli,
        "--request",
        paths["request"],
        "--result",
        paths["result"],
        "--log",
        paths["log"],
    )
    return_code = self._run_process(run, command, paths["processLog"])
    self._result_paths[run_key] = paths["result"]
    try:
      result = self._load_result(paths["result"], request["runId"])
    except Exception as e:
      if return_code:
        raise RuntimeError(
            f"External story exited with code {return_code}: {e}") from e
      raise
    if return_code:
      raise RuntimeError(f"External story exited with code {return_code}. "
                         f"Process log: {paths['processLog']}")
    self._results[run_key] = result

  def _prepare_run_paths(self, run: Run) -> dict[str, pth.LocalPath]:
    adapter_dir = run.out_dir / "external_story"
    user_data_dir = adapter_dir / "user_data"
    extensions_dir = adapter_dir / "extensions"
    artifacts_dir = adapter_dir / "artifacts"
    for path in (adapter_dir, user_data_dir, extensions_dir, artifacts_dir):
      path.mkdir(parents=True, exist_ok=True)
    return {
        "request": adapter_dir / "request.json",
        "result": adapter_dir / "result.json",
        "log": adapter_dir / "story.log",
        "processLog": adapter_dir / "process.log",
        "userDataDir": user_data_dir,
        "extensionsDir": extensions_dir,
        "artifactsDir": artifacts_dir,
    }

  def _create_request(self, run: Run,
                      paths: Mapping[str, pth.LocalPath]) -> JsonDict:
    run_id = f"{run.index}-{run.repetition_name}-{run.temperature}"
    request: JsonDict = {
        "schemaVersion": SCHEMA_VERSION,
        "story": self.name,
        "runId": run_id,
        "userDataDir": str(paths["userDataDir"]),
        "extensionsDir": str(paths["extensionsDir"]),
        "artifactsDir": str(paths["artifactsDir"]),
        "timeoutMs": int(self._timeout.total_seconds() * 1000),
        "env": self._env,
        "launchArgs": self._launcher_flags(run),
    }
    if self._app_executable:
      request["appExecutable"] = str(self._app_executable)
    if self._electron_executable:
      request["electronExecutable"] = str(self._electron_executable)
    if self._app_root:
      request["appRoot"] = str(self._app_root)
    return request

  @staticmethod
  def _launcher_flags(run: Run) -> list[str]:
    flags = list(run.browser.get_launcher_flags(run.session))
    perfetto_context = run.get_probe_context(PerfettoProbe)
    if isinstance(perfetto_context, ExternalWindowsPerfettoProbeContext):
      flags.extend(perfetto_context.launcher_flags)
    return flags

  def _run_process(self, run: Run, command: TupleCmdArgs,
                   log_path: pth.LocalPath) -> int:
    env = dict(os.environ)
    env.update(self._env)
    with log_path.open("wb") as process_log:
      process = run.browser_platform.popen(
          *command,
          stdout=process_log,
          stderr=subprocess.STDOUT,
          env=env,
          cwd=self._story_cli.parent)
      try:
        process_timeout = self._timeout + PROCESS_CLEANUP_GRACE
        return_code = process.wait(timeout=process_timeout.total_seconds())
      except subprocess.TimeoutExpired as e:
        run.browser_platform.kill(process)
        process.wait()
        raise TimeoutError(f"External story timed out after {self._timeout}. "
                           f"Process log: {log_path}") from e
    return return_code

  def _load_result(self, result_path: pth.LocalPath,
                   run_id: object) -> ExternalStoryResult:
    if not result_path.is_file():
      raise RuntimeError(
          f"External story did not write result file: {result_path}")
    try:
      with result_path.open(encoding="utf-8") as result_file:
        value = json.load(result_file)
    except json.JSONDecodeError as e:
      raise RuntimeError(
          f"External story wrote invalid JSON: {result_path}") from e
    return ExternalStoryResult.parse(value, str(run_id), self.name,
                                     self._required_phases)

  @staticmethod
  def _write_json(path: pth.LocalPath, value: JsonDict) -> None:
    with path.open("w", encoding="utf-8") as output_file:
      json.dump(value, output_file, indent=2)


class ElectronStoryBenchmark(Benchmark):
  """Run an external app-owned Playwright Electron story."""

  NAME: ClassVar[str] = "electron"
  DEFAULT_STORY_CLS: ClassVar[type[Story]] = ExternalElectronStory
  DEFAULT_COOL_DOWN: ClassVar[dt.timedelta] = dt.timedelta()

  @property
  @override
  def manages_browser_process(self) -> bool:
    return False

  @override
  def prepare_cli_args(self, args: argparse.Namespace) -> None:
    if args.browser or args.browser_config:
      return
    story = self.stories[0]
    assert isinstance(story, ExternalElectronStory)
    args.browser = [
        BrowserConfig(
            story.launcher_executable,
            browser_type=BrowserType.CHROMIUM,
            version=story.launcher_version)
    ]

  @classmethod
  @override
  def add_cli_arguments(cls, parser: CBArgumentParser) -> CBArgumentParser:
    super().add_cli_arguments(parser)
    group = parser.add_argument_group("External Electron Story Options")
    group.add_argument(
        "--story-cli",
        required=True,
        type=PathParser.file_path,
        help="App-owned Node/TypeScript Playwright story CLI module.")
    group.add_argument(
        "--story-runtime",
        default=pth.AnyPath("node.exe" if plt.PLATFORM.is_win else "node"),
        type=lambda value: PathParser.binary_path(value, plt.PLATFORM),
        help="Runtime used to execute --story-cli. Defaults to node.")
    launch_target = group.add_mutually_exclusive_group(required=True)
    launch_target.add_argument(
        "--app-executable",
        type=PathParser.existing_path,
        help="Application executable or launch target passed to the story.")
    launch_target.add_argument(
        "--app-root",
        type=PathParser.dir_path,
        help="Application source/build root passed to the story.")
    group.add_argument(
        "--electron-executable",
        type=PathParser.file_path,
        help="Optional Electron executable override for local builds.")
    group.add_argument(
        "--external-story-name",
        default=DEFAULT_STORY,
        type=ObjectParser.non_empty_str,
        help="Versioned app-owned story identifier.")
    group.add_argument(
        "--external-story-timeout",
        default=dt.timedelta(seconds=60),
        type=DurationParser.positive_duration,
        help="Hard timeout for the external story process.")
    group.add_argument(
        "--required-phase",
        action="append",
        default=None,
        type=ObjectParser.non_empty_str,
        help="Required result phase. Repeat to override the VS Code defaults.")
    group.add_argument(
        "--story-env",
        action="append",
        default=[],
        type=parse_env,
        metavar="NAME=VALUE",
        help="Environment variable passed to the story and in its request.")
    return parser

  @classmethod
  @override
  def kwargs_from_cli(cls, args: argparse.Namespace) -> dict[str, Any]:
    kwargs = super().kwargs_from_cli(args)
    required_phases = args.required_phase
    if required_phases is None:
      required_phases = DEFAULT_REQUIRED_PHASES
    story = ExternalElectronStory(
        args.external_story_name,
        args.story_runtime,
        args.story_cli,
        args.app_executable,
        args.app_root,
        args.electron_executable,
        args.external_story_timeout,
        tuple(required_phases),
        dict(args.story_env),
    )
    kwargs["stories"] = (story,)
    return kwargs
