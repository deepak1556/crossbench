# Copyright 2026 The Chromium Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from __future__ import annotations

from crossbench.benchmarks.electron.electron import ElectronStoryBenchmark
from crossbench.benchmarks.electron.probe import ExternalStoryMetricsProbe

ElectronStoryBenchmark.PROBES = (ExternalStoryMetricsProbe,)

__all__ = ["ElectronStoryBenchmark"]
