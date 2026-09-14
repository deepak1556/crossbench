# Copyright 2026 The Chromium Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from typing_extensions import override

from crossbench.probes.cb_perfetto.constants import PERFETTO_TRACE_NAME
from crossbench.probes.cb_perfetto.context.base import PerfettoProbeContext
from crossbench.probes.results import EmptyProbeResult, ProbeResult

if TYPE_CHECKING:
  from crossbench import path as pth
  from crossbench.plt.types import TupleCmdArgs
  from crossbench.probes.cb_perfetto.perfetto import PerfettoProbe
  from crossbench.runner.run import Run


class WindowsPerfettoProbeContext(PerfettoProbeContext):

  def __init__(self, probe: PerfettoProbe, run: Run) -> None:
    super().__init__(probe, run)

  @override
  def get_browser_config_path(self) -> pth.AnyPath:
    return self.run.out_dir / "perfetto_config.pb"

  @override
  def get_default_result_path(self) -> pth.AnyPath:
    return self.run.out_dir / PERFETTO_TRACE_NAME

  @override
  def setup(self) -> None:
    self._setup_push_perfetto_config()
    flags = self.browser.flags
    flags.set("--trace-perfetto-config-file",
              str(self.get_browser_config_path()))
    flags.set("--trace-startup-file", str(self.result_path))

  @override
  def _setup_push_perfetto_config(self) -> None:
    super()._setup_push_perfetto_config()
    # --trace-perfetto-config-file only supports the binary format.
    self.host_platform.write_bytes(self.get_browser_config_path(),
                                   self.probe.trace_config.SerializeToString())

  @override
  def start(self) -> None:
    self.browser.performance_mark("probe-perfetto-start")

  @override
  def stop(self) -> None:
    self.browser.performance_mark("probe-perfetto-stop")

  @override
  def _setup_validate_bin(self) -> None:
    pass

  @override
  def _start_perfetto(self) -> None:
    pass

  @override
  def _stop_perfetto(self) -> None:
    pass

  @property
  @override
  def perfetto_cmd(self) -> TupleCmdArgs:
    return ()


class ExternalWindowsPerfettoProbeContext(WindowsPerfettoProbeContext):
  """Startup tracing context for apps launched by an external story."""

  def __init__(self, probe: PerfettoProbe, run: Run) -> None:
    super().__init__(probe, run)
    self._launcher_flags: tuple[str, ...] = ()

  @property
  def launcher_flags(self) -> tuple[str, ...]:
    return self._launcher_flags

  @override
  def setup(self) -> None:
    self._setup_push_perfetto_config()
    self._launcher_flags = (
        f"--trace-perfetto-config-file={self.get_browser_config_path()}",
        f"--trace-startup-file={self.result_path}",
    )

  @override
  def start(self) -> None:
    pass

  @override
  def stop(self) -> None:
    pass

  @override
  def teardown(self) -> ProbeResult:
    if (not self.run.is_success and
        not self.host_platform.exists(self.result_path)):
      logging.error("External app did not produce a Perfetto trace.")
      return EmptyProbeResult()
    return super().teardown()
