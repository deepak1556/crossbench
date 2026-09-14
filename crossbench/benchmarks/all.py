# Copyright 2022 The Chromium Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from __future__ import annotations

from crossbench.benchmarks.blink_ai import BlinkAIBenchmark
from crossbench.benchmarks.devtools_frontend.devtools_frontend_benchmark import \
    DevToolsFrontendBenchmark
from crossbench.benchmarks.embedder import EmbedderBenchmark
from crossbench.benchmarks.electron import ElectronStoryBenchmark
from crossbench.benchmarks.jetstream import JetStream11Benchmark, \
    JetStream20Benchmark, JetStream21Benchmark, JetStream22Benchmark, \
    JetStream30Benchmark, JetStreamMainBenchmark
from crossbench.benchmarks.loading.browser_startup import \
    BrowserStartupBenchmark
from crossbench.benchmarks.loading.loading_benchmark import LoadingBenchmark
from crossbench.benchmarks.loadline import LoadLine1PhoneBenchmark, \
    LoadLine1PhoneDebugBenchmark, LoadLine1PhoneFastBenchmark, \
    LoadLine1TabletBenchmark, LoadLine1TabletDebugBenchmark, \
    LoadLine1TabletFastBenchmark, LoadLine2PhoneBenchmark, \
    LoadLine2PhoneDebugBenchmark, LoadLine2TabletBenchmark, \
    LoadLine2TabletDebugBenchmark, LoadLine2WebApiPhoneBenchmark, \
    LoadLine2WebApiPhoneDebugBenchmark
from crossbench.benchmarks.manual import ManualBenchmark
from crossbench.benchmarks.memory.memory_benchmark import MemoryBenchmark
from crossbench.benchmarks.motionmark import MotionMark10Benchmark, \
    MotionMark11Benchmark, MotionMark12Benchmark, MotionMark13Benchmark, \
    MotionMark14Benchmark, MotionMark131Benchmark, MotionMark132Benchmark, \
    MotionMarkMainBenchmark
from crossbench.benchmarks.powerline import PowerlineBenchmark
from crossbench.benchmarks.speedometer import Speedometer10Benchmark, \
    Speedometer20Benchmark, Speedometer21Benchmark, Speedometer30Benchmark, \
    Speedometer31Benchmark, SpeedometerMainBenchmark
from crossbench.benchmarks.web_power import WebPowerBenchmark, \
    WebPowerIdleBenchmark, WebPowerMediaPlaybackBenchmark, \
    WebPowerPageLoadBenchmark, WebPowerScrollBenchmark
from crossbench.benchmarks.webai import WebAIBenchmark
from crossbench.benchmarks.webxprt import WebXPRT5Benchmark

__all__ = [
    "BlinkAIBenchmark",
    "DevToolsFrontendBenchmark",
    "EmbedderBenchmark",
    "ElectronStoryBenchmark",
    "JetStream11Benchmark",
    "JetStream20Benchmark",
    "JetStream21Benchmark",
    "JetStream22Benchmark",
    "JetStream30Benchmark",
    "JetStreamMainBenchmark",
    "LoadingBenchmark",
    "BrowserStartupBenchmark",
    "LoadLine1PhoneBenchmark",
    "LoadLine1PhoneDebugBenchmark",
    "LoadLine1PhoneFastBenchmark",
    "LoadLine1TabletBenchmark",
    "LoadLine1TabletDebugBenchmark",
    "LoadLine1TabletFastBenchmark",
    "LoadLine2PhoneBenchmark",
    "LoadLine2PhoneDebugBenchmark",
    "LoadLine2TabletBenchmark",
    "LoadLine2TabletDebugBenchmark",
    "LoadLine2WebApiPhoneBenchmark",
    "LoadLine2WebApiPhoneDebugBenchmark",
    "ManualBenchmark",
    "MemoryBenchmark",
    "MotionMark10Benchmark",
    "MotionMark11Benchmark",
    "MotionMark12Benchmark",
    "MotionMark131Benchmark",
    "MotionMark132Benchmark",
    "MotionMark13Benchmark",
    "MotionMark14Benchmark",
    "MotionMarkMainBenchmark",
    "PowerlineBenchmark",
    "Speedometer10Benchmark",
    "Speedometer20Benchmark",
    "Speedometer21Benchmark",
    "Speedometer30Benchmark",
    "Speedometer31Benchmark",
    "SpeedometerMainBenchmark",
    "WebAIBenchmark",
    "WebPowerBenchmark",
    "WebPowerIdleBenchmark",
    "WebPowerMediaPlaybackBenchmark",
    "WebPowerPageLoadBenchmark",
    "WebPowerScrollBenchmark",
    "WebXPRT5Benchmark",
]
