# Copyright 2024 The Chromium Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from __future__ import annotations

import enum
import json
import logging
from typing import TYPE_CHECKING

import pytest

from crossbench.browsers.chrome.version import ChromeVersion
from crossbench.cli.cli import CrossBenchCLI
from crossbench.path import check_hash
from crossbench.plt import PLATFORM
from crossbench.plt.android_adb import Adb, AndroidAdbPlatform
from tests import test_helper

if TYPE_CHECKING:
  from tests.test_helper import TestEnv

CHROME_APK_URL = "gs://chrome-telemetry/apks/MonochromeCanary.apk"
CHROME_APK_HASH = "5de59881c02783d2174e1e891d82c9dbbce09c67"

MIN_VERSION = ChromeVersion.any((130,))


@pytest.fixture(autouse=True)
def adb_test_env(device_id, adb_path, test_env: TestEnv) -> None:
  tmp_dir = test_env.output_dir / "tmp_dir"
  assert not tmp_dir.exists()

  adb = Adb(PLATFORM, device_id, adb_path)
  adb_platform = AndroidAdbPlatform(PLATFORM, adb=adb)

  installed_version_str = adb_platform.app_version("com.android.chrome")
  installed_version = ChromeVersion.parse(installed_version_str)
  if installed_version >= MIN_VERSION:
    logging.info("Using pre-installed chrome version %s", installed_version)
  else:
    # Download and install chrome-canary M130 (x64 arch) from the cloud.
    # The benchmark requires trace events that were introduced in M126.
    # TODO(crbug/377290309): Remove this workaround when chrome preinstalled
    # in the emulator image is >=M126.
    local_apk = tmp_dir / "chrome.apk"
    PLATFORM.download_gcs_file(CHROME_APK_URL, local_apk)
    assert check_hash(local_apk, CHROME_APK_HASH)

    assert adb_path, "Missing adb"
    assert device_id, "Missing device id"
    adb = Adb(PLATFORM, device_id, adb_path)
    adb.install_apk(local_apk)


# TODO(crbug/377290309): Remove the custom browser config when the test passes
# with the preinstalled browser.
def _browser_config(device_id, adb_path) -> str:
  return json.dumps({
      "browser": "chrome-canary",
      "driver": {
          "type": "adb",
          "device_id": device_id,
          "adb_bin": adb_path
      }
  })


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


def test_loadline_phone(device_id, adb_path, test_env: TestEnv) -> None:
  _test_loadline_default(device_id, adb_path, BenchmarkType.PHONE, test_env)


def test_loadline_tablet(device_id, adb_path, test_env: TestEnv) -> None:
  _test_loadline_default(device_id, adb_path, BenchmarkType.TABLET, test_env)


def _test_loadline_default(device_id, adb_path, benchmark_type: BenchmarkType,
                           test_env: TestEnv) -> None:
  cli = CrossBenchCLI()
  browser_config = _browser_config(device_id, adb_path)
  out_dir = test_env.results_dir / f"default_{benchmark_type}"
  cli.run([
      benchmark_type, f"--browser={browser_config}", "--repeat=1", "--throw",
      f"--out-dir={out_dir}", "--debug", *list(test_env.cq_flags)
  ])
  # With only 1 repetition, there's a chance that one story won't produce a
  # metric. To avoid flaky failures, we only check the total score here.
  _verify_metrics(out_dir, benchmark_type, only_total=True)
  _verify_breakdown(out_dir)


def test_loadline_batch(device_id, adb_path, test_env: TestEnv) -> None:
  cli = CrossBenchCLI()
  browser_config = _browser_config(device_id, adb_path)
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
