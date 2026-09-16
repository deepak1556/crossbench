# Copyright 2024 The Chromium Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from __future__ import annotations

import enum
import json
from typing import TYPE_CHECKING

from crossbench.cli.cli import CrossBenchCLI
from tests import test_helper

if TYPE_CHECKING:
  from tests.test_helper import TestEnv


def _batch_trace_process_config() -> str:
  return json.dumps({
      "queries": ["loadline/benchmark_score", "loadline/breakdown"],
      "batch": True,
  })


class BenchmarkType(enum.StrEnum):
  PHONE = "loadline-phone"
  TABLET = "loadline-tablet"


def _verify_metrics(out_dir, benchmark_type: BenchmarkType, only_total=False):
  result_csv = out_dir / "benchmark_score.csv"
  match benchmark_type:
    case BenchmarkType.PHONE:
      expected_titles = [
          "browser", "TOTAL_SCORE", "amazon_product", "cnn_article",
          "globo_homepage", "google_search_result", "wikipedia_article"
      ]
    case BenchmarkType.TABLET:
      expected_titles = [
          "browser", "TOTAL_SCORE", "amazon_product", "cnn_article",
          "google_doc", "google_search_result", "youtube_video"
      ]
    case _:
      raise AssertionError(f"Invalid benchmark type {benchmark_type}")

  with result_csv.open() as csv:
    lines = csv.readlines()
    assert len(lines) == 2

    titles = lines[0].strip().split(",")
    assert titles == expected_titles, (
        f"Titles mismatch: expected {expected_titles}, got {titles}")

    values = lines[1].split(",")
    assert len(values) == len(titles)
    values_to_check = values[1:2] if only_total else values[1:]
    for value in values_to_check:
      assert value, f"Encountered empty value. CSV contents: {lines}"
      assert float(value) > 0, f"Expected positive number, but got {value}"


def _verify_breakdown(out_dir):
  result_csv = out_dir / "breakdown.csv"
  with result_csv.open() as csv:
    lines = csv.readlines()
    assert len(lines) > 1

    titles = lines[0].strip().split(",")
    expected_titles = [
        "browser", "story", "os", "renderer", "compositor", "gpu",
        "surfaceflinger"
    ]
    assert titles == expected_titles, (
        f"Titles mismatch: expected {expected_titles}, got {titles}")

    has_values = False
    for line in lines[1:]:
      values = line.split(",")
      assert len(values) == len(titles)
      for value in values[2:]:
        if value and float(value) > 0:
          has_values = True
    assert has_values


def test_loadline_phone(browser_config, test_env: TestEnv) -> None:
  _test_loadline_default(browser_config, BenchmarkType.PHONE, test_env)


def test_loadline_tablet(browser_config, test_env: TestEnv) -> None:
  _test_loadline_default(browser_config, BenchmarkType.TABLET, test_env)


def _test_loadline_default(browser_config: str, benchmark_type: BenchmarkType,
                           test_env: TestEnv) -> None:
  cli = CrossBenchCLI()
  out_dir = test_env.results_dir / f"default_{benchmark_type}"
  cli.run([
      benchmark_type, f"--browser={browser_config}", "--repeat=1", "--throw",
      f"--out-dir={out_dir}", "--debug", *list(test_env.cq_flags)
  ])
  # With only 1 repetition, there's a chance that one story won't produce a
  # metric. To avoid flaky failures, we only check the total score here.
  _verify_metrics(out_dir, benchmark_type, only_total=True)
  _verify_breakdown(out_dir)


def test_loadline_batch(browser_config, test_env: TestEnv) -> None:
  cli = CrossBenchCLI()
  out_dir = test_env.results_dir
  # We run the benchmark with increased time units to account for
  # the slowness of emulators on test bots.
  cli.run([
      BenchmarkType.PHONE, f"--browser={browser_config}", "--repeat=2",
      "--throw", f"--out-dir={out_dir}", "--time-unit=3s",
      f"--probe=trace_processor:{_batch_trace_process_config()}",
      *list(test_env.cq_flags)
  ])
  _verify_metrics(out_dir, BenchmarkType.PHONE)
  _verify_breakdown(out_dir)


if __name__ == "__main__":
  test_helper.run_pytest(__file__)
