# Copyright 2026 The Chromium Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from __future__ import annotations

from typing import TYPE_CHECKING, ClassVar

from typing_extensions import override

from crossbench.benchmarks.benchmark_probe import BenchmarkProbeMixin
from crossbench.benchmarks.electron.electron import ExternalElectronStory
from crossbench.probes.json import JsonResultProbe, JsonResultProbeContext
from crossbench.probes.results import EmptyProbeResult, ProbeResult

if TYPE_CHECKING:
  from crossbench.runner.actions import Actions
  from crossbench.types import Json


class ExternalStoryMetricsProbe(BenchmarkProbeMixin, JsonResultProbe):
  """Collects and merges external story phase timings."""

  NAME: ClassVar[str] = "electron.story"

  @override
  def get_context_cls(self) -> type[ExternalStoryMetricsProbeContext]:
    return ExternalStoryMetricsProbeContext


class ExternalStoryMetricsProbeContext(
    JsonResultProbeContext[ExternalStoryMetricsProbe]):

  @property
  def story(self) -> ExternalElectronStory:
    story = self.run.story
    assert isinstance(story, ExternalElectronStory)
    return story

  @override
  def to_json(self, actions: Actions) -> Json:
    del actions
    return self.story.result(self.run).metrics()

  @override
  def stop(self) -> None:
    if self.story.has_result(self.run):
      super().stop()

  @override
  def teardown(self) -> ProbeResult:
    if not self.story.has_result(self.run):
      return EmptyProbeResult()
    return super().teardown()
