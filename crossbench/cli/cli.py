# Copyright 2022 The Chromium Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from __future__ import annotations

import argparse
import contextlib
import io
import logging
import os
import sys
import textwrap
import traceback
from typing import IO, TYPE_CHECKING, Any, Iterator, Sequence, TypeAlias, \
    TypeVar

import tabulate as tbl
from typing_extensions import override

import crossbench.benchmarks.all as benchmarks
import crossbench.plt as plt
from crossbench import __version__
from crossbench import path as pth
from crossbench.cli import exception_formatter
from crossbench.cli.parser import CBArgumentParser, CBNamespace
from crossbench.cli.subcommand.benchmark import BenchmarkSubcommand
from crossbench.cli.subcommand.describe import DescribeSubcommand
from crossbench.cli.subcommand.devtools_recorder_proxy.subcommand import \
    DevtoolsRecorderProxySubcommand
from crossbench.cli.subcommand.help import HelpSubcommand
from crossbench.cli.subcommand.mcp import McpSubcommand
from crossbench.cli.subcommand.perfetto import PerfettoCrossbenchSubcommand
from crossbench.cli.subcommand.pinpoint import PinpointSubcommand
from crossbench.cli.subcommand.setup_cross_platform_mode import \
    SetupCrossPlatformModeSubcommand
from crossbench.cli.subcommand.upload_results import UploadResultsSubcommand
from crossbench.cli.subcommand.version import VersionSubcommand
from crossbench.cli.ui import ui
from crossbench.helper.collection_helper import close_matches_message
from crossbench.probes.all import GENERAL_PURPOSE_PROBES
from crossbench.runner.runner import Runner

if TYPE_CHECKING:
  from crossbench.benchmarks.base import Benchmark
  from crossbench.browsers.browser import Browser
  from crossbench.cli.subcommand.base import CrossbenchSubcommand
  from crossbench.cli.types import Subparsers
  from crossbench.parse import LateArgumentError

  BenchmarkClass: TypeAlias = type[Benchmark]
  BrowserLookupTable: TypeAlias = dict[str, tuple[type[Browser], pth.LocalPath]]
  SubcommandT = TypeVar("SubcommandT", bound=CrossbenchSubcommand)


class CrossBenchArgumentError(argparse.ArgumentError):
  """Custom class that also prints the argument.help if available.
  """

  def __init__(self, argument: Any, message: str) -> None:
    self.help: str = ""
    super().__init__(argument, message)
    if self.argument_name:
      self.help = getattr(argument, "help", "")

  def __str__(self) -> str:
    formatted = super().__str__()
    if not self.help:
      return formatted
    return (f"argument error {self.argument_name}:\n\n"
            f"Help {self.argument_name}:\n{self.help}\n\n"
            f"{formatted}")


argparse.ArgumentError = CrossBenchArgumentError  # type: ignore


class EnableDebuggingAction(argparse.Action):
  """Custom action to set both --throw and -vvv."""

  def __call__(self,
               parser: argparse.ArgumentParser,
               namespace: argparse.Namespace,
               values: str | Sequence[Any] | None,
               option_string: str | None = None) -> None:
    del parser, values, option_string
    namespace.throw = True
    namespace.verbosity = 3
    namespace.driver_logging = True


class MainCrossBenchArgumentParser(CBArgumentParser):

  @override
  def print_help(self, file: Any = None) -> None:
    file = file or sys.stdout
    super().print_help(file=file)
    self.print_probes(file=file)
    self.print_urls(file=file)
    self.print_example(file=file)

  def print_probes(self, file: IO[str] = sys.stdout) -> None:
    lines = [
        "Probes can be added and configured for each benchmark.",
        f"Use `{sys.argv[0]} describe probe $PROBE` for the full help.",
        "",
        "Usage: --probe=v8.log --probe=video ...",
        "Usage: --probe=v8.log:{log_all:false} ...",
        "Usage: --probe-config=configs/probe/perfetto/default.config.hjson",
        "",
    ]
    table = []
    for probe_cls in GENERAL_PURPOSE_PROBES:
      table.append((probe_cls.NAME, probe_cls.summary_text()))
    lines.append(tbl.tabulate(table, tablefmt="plain"))
    contents = "\n".join(lines)
    file = file or sys.stdout
    file.write("\n")
    file.write("Available Probes for all Benchmarks:\n")
    file.write(textwrap.indent(contents, "    "))
    file.write("\n")

  def print_urls(self, file: IO[str] = sys.stdout) -> None:
    file.write("\n")
    file.write("URLS:\n")
    file.write("  Source: https://chromium.googlesource.com/crossbench\n")
    file.write("  Bugs:   "
               "https://issues.chromium.org/u/1/issues/new?component=1456712\n")

  def print_example(self, file: IO[str] = sys.stdout) -> None:
    file.write("\n")
    file.write("EXAMPLE:\n")
    file.write("  ./cb.py speedometer --browser=chrome-m131 "
               "--browser=out/release/chrome --probe=profiling\n\n")
    readme_file = pth.AnyPath(__file__).parents[2] / "README.md"
    file.write(f"  See {readme_file} for more details and instructions.\n")


class CrossBenchCLI:
  BENCHMARKS: tuple[BenchmarkClass, ...] = (
      benchmarks.BlinkAIBenchmark,
      benchmarks.BrowserStartupBenchmark,
      benchmarks.DevToolsFrontendBenchmark,
      benchmarks.EmbedderBenchmark,
      benchmarks.ElectronStoryBenchmark,
      # JetStream:
      benchmarks.JetStream11Benchmark,
      benchmarks.JetStream20Benchmark,
      benchmarks.JetStream21Benchmark,
      benchmarks.JetStream22Benchmark,
      benchmarks.JetStream30Benchmark,
      benchmarks.JetStreamMainBenchmark,
      # Loading:
      benchmarks.LoadingBenchmark,
      # LoadLine:
      benchmarks.LoadLine1PhoneBenchmark,
      benchmarks.LoadLine1PhoneDebugBenchmark,
      benchmarks.LoadLine1PhoneFastBenchmark,
      benchmarks.LoadLine1TabletBenchmark,
      benchmarks.LoadLine1TabletDebugBenchmark,
      benchmarks.LoadLine1TabletFastBenchmark,
      # LoadLine 2:
      benchmarks.LoadLine2PhoneBenchmark,
      benchmarks.LoadLine2PhoneDebugBenchmark,
      benchmarks.LoadLine2TabletBenchmark,
      benchmarks.LoadLine2TabletDebugBenchmark,
      benchmarks.LoadLine2WebApiPhoneBenchmark,
      benchmarks.LoadLine2WebApiPhoneDebugBenchmark,
      # Manual:
      benchmarks.ManualBenchmark,
      # Memory:
      benchmarks.MemoryBenchmark,
      # Motionmark
      benchmarks.MotionMark10Benchmark,
      benchmarks.MotionMark11Benchmark,
      benchmarks.MotionMark12Benchmark,
      benchmarks.MotionMark13Benchmark,
      benchmarks.MotionMark131Benchmark,
      benchmarks.MotionMark132Benchmark,
      benchmarks.MotionMark14Benchmark,
      benchmarks.MotionMarkMainBenchmark,
      # Powerline
      benchmarks.PowerlineBenchmark,
      # Speedometer:
      benchmarks.Speedometer10Benchmark,
      benchmarks.Speedometer20Benchmark,
      benchmarks.Speedometer21Benchmark,
      benchmarks.Speedometer30Benchmark,
      benchmarks.Speedometer31Benchmark,
      benchmarks.SpeedometerMainBenchmark,
      # Web Power
      benchmarks.WebPowerBenchmark,
      benchmarks.WebPowerIdleBenchmark,
      benchmarks.WebPowerMediaPlaybackBenchmark,
      benchmarks.WebPowerPageLoadBenchmark,
      benchmarks.WebPowerScrollBenchmark,
      # WebAI
      benchmarks.WebAIBenchmark,
      # WebXPRT
      benchmarks.WebXPRT5Benchmark,
  )

  RUNNER_CLS: type[Runner] = Runner

  def __init__(self, enable_logging: bool = True) -> None:
    self._enable_logging: bool = enable_logging
    self._console_handler: logging.StreamHandler | None = None
    self._benchmark_subcommands: dict[BenchmarkClass, BenchmarkSubcommand] = {}
    self.parser = MainCrossBenchArgumentParser(
        description=("A cross browser and cross benchmark runner "
                     "with configurable measurement probes.\n"))
    self._subparsers = self._setup_subparsers()
    self._setup_parser()
    self._last_subcommand: CrossbenchSubcommand | None = None
    self.args: CBNamespace = CBNamespace()
    self._setup_subcommands()

  def _setup_subcommands(self) -> None:
    self._setup_core_subcommands()
    self._setup_tools_subcommands()
    self._setup_benchmark_subcommands()

  def _setup_core_subcommands(self) -> None:
    self._add_subcommand(HelpSubcommand)
    self._add_subcommand(DescribeSubcommand)
    self._add_subcommand(VersionSubcommand)

  def _setup_tools_subcommands(self) -> None:
    self._add_subcommand(DevtoolsRecorderProxySubcommand)
    self._add_subcommand(McpSubcommand)
    self._add_subcommand(PerfettoCrossbenchSubcommand)
    self._add_subcommand(PinpointSubcommand)
    self._add_subcommand(SetupCrossPlatformModeSubcommand)
    self._add_subcommand(UploadResultsSubcommand)

  def _setup_benchmark_subcommands(self) -> None:
    for benchmark_cls in self.BENCHMARKS:
      subcommand = BenchmarkSubcommand(self, benchmark_cls)
      subcommand.register_subcommand(self.subparsers)
      self._benchmark_subcommands[benchmark_cls] = subcommand

  @property
  def subparsers(self) -> Subparsers:
    return self._subparsers

  @property
  def subcommands(self) -> dict[str, CrossbenchSubcommand]:
    subcommands: dict[str, CrossbenchSubcommand] = {}
    for key, parser in self.subparsers.choices.items():
      if subcommand := parser.get_default("crossbench_subcommand"):
        subcommands[key] = subcommand
    return subcommands

  @property
  def last_subcommand(self) -> CrossbenchSubcommand | None:
    return self._last_subcommand

  def _setup_parser(self) -> None:
    self.add_debugging_arguments(self.parser)
    self.add_base_arguments(self.parser)

  def _setup_subparsers(self) -> Subparsers:
    subparsers = self.parser.add_subparsers(
        title="Subcommands",
        dest="subcommand",
        required=True,
        parser_class=CBArgumentParser)
    return subparsers

  def add_base_arguments(self, parser: argparse.ArgumentParser) -> None:
    # Disable colors by default when piped to a file.
    has_color = hasattr(sys.stdout, "isatty") and sys.stdout.isatty()
    parser.add_argument(
        "--no-color",
        dest="color",
        action="store_false",
        default=has_color,
        help="Disable colored output")
    parser.add_argument(
        "--version", action="version", version=f"%(prog)s {__version__}")

    env_group = parser.add_argument_group("Environment Options")
    env_group.add_argument(
        "--bin-override",
        "--binary-override",
        dest="binary_overrides",
        action="append",
        default=[],
        help=("Override binary lookup paths. Format: name=path. "
              "Example: --bin-override wpr=/path/to/wpr"))

  def add_debugging_arguments(
      self, parser: argparse.ArgumentParser) -> argparse._ArgumentGroup:
    debug_group = parser.add_argument_group("Verbosity / Debugging Options")
    verbosity_group = debug_group.add_mutually_exclusive_group()
    verbosity_group.add_argument(
        "--quiet",
        "-q",
        dest="verbosity",
        default=0,
        action="store_const",
        const=-1,
        help="Disable most output printing.")
    verbosity_group.add_argument(
        "--verbose",
        "-v",
        dest="verbosity",
        action="count",
        default=0,
        help=("Increase output verbosity. "
              "Repeat for more verbose output (0..2)."))

    debug_group.add_argument(
        "--throw",
        action="store_true",
        default=False,
        help=("Directly throw exceptions instead. "
              "Note that this prevents merging of probe results if only "
              "a subset of the runs failed."))
    debug_group.add_argument(
        "--debug",
        action=EnableDebuggingAction,
        nargs=0,
        help="Enable debug output, equivalent to --throw -vvv")
    return debug_group

  def _add_subcommand(
      self,
      subcommand_cls: type[SubcommandT],
  ) -> SubcommandT:
    subcommand = subcommand_cls(self)
    subcommand.register_subcommand(self.subparsers)
    return subcommand

  def _lazy_init_subcommand(self, argv: Sequence[str]) -> None:
    if not argv:
      return
    subcommand_name = argv[0]
    if subcommand_name.startswith("-"):
      return
    if subcommand := self.subcommands.get(subcommand_name):
      subcommand.init_cli_parser()
      subcommand.set_fast_mode_defaults(argv)

  def log_assertion_error_statement(self, e: AssertionError) -> None:
    _, exc_exception, tb = sys.exc_info()
    if exc_exception is not e:
      return
    tb_info = traceback.extract_tb(tb)
    filename, line, _, text = tb_info[-1]
    logging.info("%s:%s: %s", filename, line, text)

  def run(self, argv: Sequence[str]) -> None:
    self._init_logging(argv)
    unprocessed_argv: list[str] = []
    try:
      argv = self._rename_subcommand(argv)
      self._lazy_init_subcommand(argv)
      # Manually check for unprocessed_argv to print nicer error messages.
      self.args, unprocessed_argv = self.parser.parse_known_args(argv)
    except argparse.ArgumentError as e:
      # args is not set at this point, as parsing might have failed before
      # handling --throw or --debug.
      if "--throw" in argv or "--debug" in argv:
        raise e
      self.error(str(e))
    if unprocessed_argv:
      self.error(f"unrecognized arguments: {unprocessed_argv}\n"
                 f"Use `{self.parser.prog} {self.args.subcommand} --help` "
                 "for more details.")
    # Properly initialize logging after having parsed all args
    self._setup_logging()

    for bin_override in self.args.binary_overrides:
      try:
        name, path_str = bin_override.split("=", 1)
        plt.PLATFORM.set_binary_lookup_override(name,
                                                plt.PLATFORM.path(path_str))
      except ValueError:
        self.error(
            f"Invalid --bin-override format: {bin_override}. Expected name=path"
        )

    try:
      self._last_subcommand = self.args.crossbench_subcommand
      self.args.crossbench_subcommand.run(self.args)
    finally:
      self._teardown_logging()

  def _rename_subcommand(self, argv: Sequence[str]) -> Sequence[str]:
    if not argv:
      return argv
    subcommand = argv[0]
    if subcommand.startswith("-"):
      return argv

    if subcommand in self.subcommands:
      return argv

    alternative: str | None = None
    choices = set(self.subcommands.keys())
    for benchmark_cls in self.BENCHMARKS:
      aliases = benchmark_cls.aliases()
      if subcommand in aliases:
        alternative = benchmark_cls.NAME
        break
      choices.update(benchmark_cls.aliases())

    message: str = ""
    if not alternative:
      message, alternative = close_matches_message(subcommand, set(choices),
                                                   "subcommand")
    if not alternative:
      raise argparse.ArgumentError(None, message)
    return [alternative, *argv[1:]]

  def handle_late_argument_error(self, e: LateArgumentError) -> None:
    self.error(f"error argument {e.flag}: {e.message}")

  def error(self, message: str) -> None:
    parser: argparse.ArgumentParser = self.parser
    # Try to use the subparser to print nicer usage help on errors.
    # ArgumentParser tends to default to the toplevel parser instead of the
    # current subcommand, which in turn prints the wrong usage text.
    subcommand_name: str = getattr(self.args, "subcommand", "")
    subcommand = self.subcommands.get(subcommand_name)
    if subcommand:
      parser = subcommand.parser
    if subcommand_name:
      message = f"{subcommand_name}: {message}"
    if isinstance(parser, CBArgumentParser):
      parser.fail(message)
    else:
      parser.error(message)

  def _init_logging(self, argv: Sequence[str] | None = None) -> None:
    sys.excepthook = exception_formatter.excepthook
    assert self._console_handler is None
    if not self._enable_logging:
      logging.getLogger().setLevel(logging.CRITICAL)
      return
    if hasattr(sys.stdout, "reconfigure"):
      sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[union-attr]
    self._console_handler = logging.StreamHandler(sys.stderr)
    self._console_handler.addFilter(logging.Filter("root"))
    self._console_handler.setLevel(logging.INFO)
    logging.getLogger().setLevel(logging.INFO)
    # Clear existing handlers in case logging has been initialized prematurely.
    logging.getLogger().handlers = []
    logging.getLogger().addHandler(self._console_handler)

    if argv is None:
      argv = sys.argv
    # Manually extract values to allow logging for failing arguments.
    if self._has_debug_logging_argv(argv):
      self._console_handler.setLevel(logging.DEBUG)
      logging.getLogger().setLevel(logging.DEBUG)
    # TODO: move to ui helpers
    ui.COLOR_LOGGING = self._detect_terminal_color(argv)
    if ui.COLOR_LOGGING:
      self._console_handler.setFormatter(ui.ColoredLogFormatter())
    logging.debug(" ".join(sys.argv))

  def _has_debug_logging_argv(self, argv: Sequence[str]) -> bool:
    return any(value in ("-v", "-vv", "-vvv", "--debug") for value in argv)

  def _detect_terminal_color(self, argv: Sequence[str]) -> bool:
    if "--no-color" in argv:
      return False
    return not os.environ.get("NO_COLOR", "")

  def _setup_logging(self) -> None:
    if not self._enable_logging:
      return
    assert self._console_handler, "Missing console handler"
    verbosity = getattr(self.args, "verbosity", 0)
    if verbosity == -1:
      self._console_handler.setLevel(logging.ERROR)
    elif verbosity == 0:
      self._console_handler.setLevel(logging.INFO)
    elif verbosity >= 1:
      self._console_handler.setLevel(logging.DEBUG)
      logging.getLogger().setLevel(logging.DEBUG)
    if not getattr(self.args, "color", True):
      ui.COLOR_LOGGING = False
    if ui.COLOR_LOGGING:
      self._console_handler.setFormatter(ui.ColoredLogFormatter())
    else:
      self._console_handler.setFormatter(None)

  def _teardown_logging(self) -> None:
    if not self._enable_logging:
      assert self._console_handler is None
      return
    assert self._console_handler, "Missing console handler"
    self._console_handler.flush()
    logging.getLogger().removeHandler(self._console_handler)
    self._console_handler = None

  @contextlib.contextmanager
  def silenced_logging(self, capture_stderr: bool = True) -> Iterator[None]:
    """Temporarily silence all logging during a context block.

    All stdout and stderr output is redirected and captured. The capture is
    only written to sys.stderr if an exception occurs. This is useful for
    background tasks to avoid cluttering the console.
    """
    log_capture_string = io.StringIO()
    self._teardown_logging()
    try:
      with contextlib.ExitStack() as stack:
        if capture_stderr:
          stack.enter_context(contextlib.redirect_stderr(log_capture_string))
        stack.enter_context(contextlib.redirect_stdout(log_capture_string))

        self._init_logging()
        self._setup_logging()
        try:
          yield
        finally:
          self._teardown_logging()
    except Exception:
      # Expose full log content on error.
      sys.stderr.write(log_capture_string.getvalue())
      raise
    finally:
      self._init_logging()
      self._setup_logging()
