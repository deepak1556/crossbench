# Copyright 2024 The Chromium Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.
from __future__ import annotations

import io
import json
import pathlib
import unittest
from argparse import ArgumentTypeError
from typing import Any, Final

from crossbench import path as pth
from crossbench import plt
from crossbench.cli.config.probe_list import ProbeListConfig
from crossbench.exception import ArgumentTypeMultiException
from crossbench.probes.all import TraceProcessorProbe
from crossbench.probes.trace_processor.constants import QUERIES_DIR
from crossbench.probes.trace_processor.context.symbolizing import \
    TraceProcessorSymbolizingProbeContext
from crossbench.probes.trace_processor.query_config import \
    DeviceSpecificTraceProcessorQuery, TraceProcessorQueryConfig
from tests import test_helper
from tests.crossbench.base import BaseCrossbenchTestCase, \
    CrossbenchFakeFsTestCase


def read_query_sql(name: str) -> str:
  return (QUERIES_DIR / name).read_text("utf-8")


class TraceProcessorProbeTestCase(unittest.TestCase):

  @unittest.skipIf(not plt.PLATFORM.which("trace_processor"),
                   "trace_processor not available")
  def test_parse_example_config(self):
    config_file = (
        test_helper.config_dir() / "doc/probe/trace_processor.config.hjson")
    self.assertTrue(config_file.is_file())
    probes = ProbeListConfig.parse(config_file).probes
    self.assertEqual(len(probes), 2)
    probe = probes[0]
    self.assertIsInstance(probe, TraceProcessorProbe)
    assert isinstance(probe, TraceProcessorProbe)
    queries = probe.queries
    self.assertEqual(len(queries), 2)
    speedometer_cpu_time_sql = read_query_sql("speedometer_cpu_time.sql")
    self.assertEqual(queries[0].name, "speedometer_cpu_time")
    self.assertEqual(queries[0].sql, speedometer_cpu_time_sql)

    inline_name = "my_query"
    inline_sql = "select dur from slice where slice.name = 'my_slice'"
    self.assertEqual(queries[1].name, inline_name)
    self.assertEqual(queries[1].sql, inline_sql)

    self.assertEqual(len(probe.module_paths), 2)
    self.assertRegex(
        str(probe.module_paths[0]),
        r".*\/crossbench\/probes\/trace_processor\/modules\/ext")
    self.assertEqual(str(probe.module_paths[1]), "/my_project/modules/ext")

    metric_definitions = probe.metric_definitions
    self.assertEqual(len(metric_definitions), 2)
    self.assertTrue("file_metric_def_textproto" in metric_definitions[0])
    self.assertTrue("inline_textproto_metric" in metric_definitions[1])

    summary_metrics = probe.summary_metrics
    # Should contain everything in 'metrics' plus 'summary_metrics'
    self.assertSequenceEqual(
        list(summary_metrics),
        ["trace_stats", "file_metric_def_textproto", "inline_textproto_metric"])

  def test_query_config_duplicate_name_raises(self):
    with self.assertRaisesRegex(ArgumentTypeError,
                                "Unexpected duplicates in query names"):
      TraceProcessorProbe.parse_dict({
          "queries": [
              "loadline/benchmark_score",
              {
                  "name": "loadline_benchmark_score",
                  "sql": "select * from slice where slice.name = 'comment'",
              },
          ],
      })


class TraceProcessorProbeFakeFsTestCase(CrossbenchFakeFsTestCase):
  _QUERIES: Final = [{"name": "q", "sql": "select 1"}]

  def setUp(self) -> None:
    super().setUp()
    self.fs.create_file(QUERIES_DIR / "pprof.sql")
    self.fs.create_file(QUERIES_DIR / "jetstream_3/perf_sample_span.sql")
    for b in ("pbcopy", "xclip", "wl-copy", "xsel", "clip"):
      if plt.PLATFORM.is_win:
        self.fs.create_file(f"C:/Windows/System32/{b}.exe")
      else:
        self.fs.create_file(f"/usr/bin/{b}", st_mode=0o755)

  def test_custom_trace_processor_path(self):
    trace_processor_dir = pathlib.Path("/path/to")
    trace_processor_path = trace_processor_dir / "trace_processor_shell"
    trace_processor_dir.mkdir(parents=True)
    trace_processor_path.touch()

    config = TraceProcessorProbe.parse_dict({
        "trace_processor_bin": str(trace_processor_path),
        "queries": self._QUERIES,
    })

    self.assertEqual(str(config.trace_processor_bin), str(trace_processor_path))

  def _assert_parse_output_option(self, key: str, aliases: list[str]) -> None:
    for config_key in (key, *aliases):
      for values in ("json", "csv", ["json"], ["csv"], ["json", "csv"]):
        probe = TraceProcessorProbe.parse_dict({
            "queries": self._QUERIES,
            config_key: values,
        })
        expected = (values,) if isinstance(values, str) else tuple(values)
        self.assertEqual(getattr(probe, key), expected)

  def test_parse_output_to_stdout(self):
    self._assert_parse_output_option(key="output_to_stdout", aliases=["stdout"])

  def test_parse_output_to_clipboard(self):
    self._assert_parse_output_option(
        key="output_to_clipboard", aliases=["pbcopy", "clipboard"])

  def test_parse_invalid_stdout_option(self):
    with self.assertRaises(
        (ArgumentTypeError, ValueError, ArgumentTypeMultiException)):
      TraceProcessorProbe.parse_dict({
          "queries": self._QUERIES,
          "stdout": "invalid_choice",
      })

  def test_parse_invalid_clipboard_option(self):
    with self.assertRaises(
        (ArgumentTypeError, ValueError, ArgumentTypeMultiException)):
      TraceProcessorProbe.parse_dict({
          "queries": self._QUERIES,
          "clipboard": "invalid_choice",
      })

  def test_init_invalid_output_to_stdout_option(self):
    with self.assertRaises(ValueError):
      TraceProcessorProbe(output_to_stdout=["invalid_choice"])

  def test_init_invalid_output_to_clipboard_option(self):
    with self.assertRaises(ValueError):
      TraceProcessorProbe(output_to_clipboard=["invalid_choice"])

  def test_init_clipboard_missing_raises(self):
    mock_platform = unittest.mock.MagicMock()
    mock_platform.has_clipboard = False
    with unittest.mock.patch.object(plt, "PLATFORM", mock_platform):
      with self.assertRaisesRegex(
          RuntimeError, "Clipboard tool unavailable on current platform."):
        TraceProcessorProbe(output_to_clipboard=["json"])

  def test_has_pprof_query(self):
    run = unittest.mock.MagicMock()

    probe = TraceProcessorProbe.parse_dict({
        "queries": [
            {
                "name": "pprof",
                "sql": "select 1"
            },
            {
                "name": "other",
                "sql": "select 2"
            },
        ]
    })
    context = probe.create_context(run)
    self.assertTrue(context.has_pprof_query(context.queries))

    probe2 = TraceProcessorProbe.parse_dict(
        {"queries": [{
            "name": "other",
            "sql": "select 2"
        },]})
    context2 = probe2.create_context(run)
    self.assertFalse(context2.has_pprof_query(context2.queries))

    probe3 = TraceProcessorProbe.parse_dict(
        {"queries": [{
            "name": "my_perf_sample_query",
            "sql": "select 3"
        },]})
    context3 = probe3.create_context(run)
    self.assertTrue(context3.has_pprof_query(context3.queries))

  def mock_has_symbols(self, return_value: bool = True):
    return unittest.mock.patch.object(
        TraceProcessorSymbolizingProbeContext,
        "has_symbols",
        new_callable=unittest.mock.PropertyMock,
        return_value=return_value)

  def test_symbolizing_context_auto_adds_pprof(self):
    run = unittest.mock.MagicMock()
    probe = TraceProcessorProbe.parse_dict(
        {"queries": [{
            "name": "other",
            "sql": "select 1"
        },]})

    context = TraceProcessorSymbolizingProbeContext(probe, run)
    with self.mock_has_symbols():
      queries = context.queries
    self.assertEqual(len(queries), 2)
    self.assertEqual(queries[1].name, "pprof")

  def test_symbolizing_context_skips_pprof_with_perf_sample(self):
    run = unittest.mock.MagicMock()
    probe = TraceProcessorProbe.parse_dict({
        "queries": [{
            "name": "jetstream_3/perf_sample_span",
            "sql": "select 1"
        },]
    })

    context = TraceProcessorSymbolizingProbeContext(probe, run)
    with self.mock_has_symbols():
      queries = context.queries
    self.assertEqual(len(queries), 1)
    self.assertEqual(queries[0].name, "jetstream_3_perf_sample_span")

  def _test_should_symbolize_profile(self,
                                     run: unittest.mock.MagicMock) -> bool:
    probe = TraceProcessorProbe.parse_dict({"queries": ["pprof"]})
    context = TraceProcessorSymbolizingProbeContext(probe, run)

    # We must access the property FIRST before asserting the mock was called,
    # because the property itself makes the call.
    result = context.should_symbolize_profile
    run.has_probe_context_by_name.assert_called_once_with("profiling")
    return result

  def test_should_symbolize_profile_skips_without_profiling(self):
    run = unittest.mock.MagicMock()
    run.runner.has_probe.return_value = True
    run.has_probe_context_by_name.return_value = False
    self.assertFalse(self._test_should_symbolize_profile(run))

  def test_should_symbolize_profile_with_profiling(self):
    run = unittest.mock.MagicMock()
    run.has_probe_context_by_name.return_value = True
    self.assertTrue(self._test_should_symbolize_profile(run))


TARGET_P9 = "web_power/power_rails_p9"
TARGET_P10 = "web_power/power_rails_p10"
TARGET_P1 = "web_power/power_rails_p1"
TARGET_FALLBACK = "web_power/powerline_cpu_rails"


class TraceProcessorQueryConfigTestCase(unittest.TestCase):

  def test_invalid_name_raises(self):
    with self.assertRaisesRegex(ArgumentTypeMultiException,
                                "sql query path does not exist"):
      TraceProcessorQueryConfig.parse("not_an_actual_query")

  def test_file_query(self):
    query = TraceProcessorQueryConfig.parse("speedometer_cpu_time")
    self.assertEqual(query.name, "speedometer_cpu_time")
    self.assertEqual(query.sql, read_query_sql("speedometer_cpu_time.sql"))

  def test_electron_startup_query(self):
    query = TraceProcessorQueryConfig.parse("electron_startup")
    self.assertEqual(query.name, "electron_startup")
    self.assertEqual(query.sql, read_query_sql("electron_startup.sql"))

  def test_file_query_path(self):
    query = TraceProcessorQueryConfig.parse(
        pth.LocalPath("speedometer_cpu_time"))
    self.assertEqual(query.name, "speedometer_cpu_time")
    self.assertEqual(query.sql, read_query_sql("speedometer_cpu_time.sql"))

  def test_file_query_sql_suffix(self):
    query = TraceProcessorQueryConfig.parse("speedometer_cpu_time.sql")
    self.assertEqual(query.name, "speedometer_cpu_time")
    self.assertEqual(query.sql, read_query_sql("speedometer_cpu_time.sql"))

  def test_file_query_name_escaped(self):
    query = TraceProcessorQueryConfig.parse("loadline/benchmark_score")
    self.assertEqual(query.name, "loadline_benchmark_score")
    self.assertEqual(query.sql, read_query_sql("loadline/benchmark_score.sql"))

  def test_file_query_name_escaped_sql_suffix(self):
    query = TraceProcessorQueryConfig.parse("loadline/benchmark_score.sql")
    self.assertEqual(query.name, "loadline_benchmark_score")
    self.assertEqual(query.sql, read_query_sql("loadline/benchmark_score.sql"))

  def test_inline_query(self):
    query = TraceProcessorQueryConfig.parse({
        "name": "comment",
        "sql": "select * from slice where slice.name = 'comment'",
    })
    self.assertEqual(query.name, "comment")
    self.assertEqual(query.sql,
                     "select * from slice where slice.name = 'comment'")

  def test_inline_query_name_escaped(self):
    query = TraceProcessorQueryConfig.parse({
        "name": "//comment//",
        "sql": "select * from slice where slice.name = 'comment'",
    })
    self.assertEqual(query.name, "__comment__")
    self.assertEqual(query.sql,
                     "select * from slice where slice.name = 'comment'")

  def test_query_with_replacements(self):
    query = TraceProcessorQueryConfig.parse({
        "name": "comment",
        "sql": "'replace me'",
        "replacements": {
            "replace me": "new value"
        }
    })
    self.assertEqual(query.name, "comment")
    self.assertEqual(query.sql, "'new value'")

  def test_device_specific_query_parsing(self):
    query = TraceProcessorQueryConfig.parse({
        "name": "web_power_power_rails",
        "device_override": {
            r"Pixel 9.*": TARGET_P9,
            r"Pixel 10.*": TARGET_P10,
        },
        "sql": TARGET_FALLBACK
    })
    self.assertIsInstance(query, DeviceSpecificTraceProcessorQuery)

  def test_device_specific_query_invalid_regex_raises(self):
    with self.assertRaisesRegex(ValueError, "Invalid regular expression"):
      TraceProcessorQueryConfig.parse({
          "name": "web_power_power_rails",
          "device_override": {
              "[Pixel 9": TARGET_P9,
          },
          "sql": TARGET_FALLBACK
      })

  def test_device_specific_query_unresolved_sql_access_raises(self):
    query = TraceProcessorQueryConfig.parse({
        "name": "web_power_power_rails",
        "device_override": {
            r"Pixel 9.*": TARGET_P9,
        }
    })
    # Accessing the SQL of an unresolved device-specific query must fail fast
    # since the final query contents are bound to a device platform at runtime.
    with self.assertRaises(RuntimeError):
      _ = query.sql

  def _verify_resolution(
      self,
      device_override: dict[str, str],
      model: str,
      expected_sql_path: str,
      sql: str | None = TARGET_FALLBACK,
      replacements: dict[str, str] | None = None,
  ) -> None:
    config_dict: dict = {
        "name": "web_power_power_rails",
        "device_override": device_override,
    }
    if sql is not None:
      config_dict["sql"] = sql
    if replacements is not None:
      config_dict["replacements"] = replacements

    query = TraceProcessorQueryConfig.parse(config_dict)
    platform = unittest.mock.MagicMock()
    platform.model = model

    resolved = query.resolve_for_platform(platform)
    self.assertIsNotNone(resolved)
    expected_sql = read_query_sql(expected_sql_path)
    if replacements:
      for k, v in replacements.items():
        expected_sql = expected_sql.replace(k, v)
    self.assertEqual(resolved.sql, expected_sql)

  def test_device_specific_query_resolution_match(self):
    self._verify_resolution(
        device_override={
            r"Pixel 9.*": TARGET_P9,
            r"Pixel 10.*": TARGET_P10,
        },
        model="Pixel 10 Pro XL",
        expected_sql_path=f"{TARGET_P10}.sql")

  def test_device_specific_query_resolution_no_substring_match(self):
    device_override = {
        r"Pixel 1\b.*": TARGET_P1,
        r"Pixel 10\b.*": TARGET_P10,
    }
    self._verify_resolution(
        device_override=device_override,
        model="Pixel 10 Pro",
        expected_sql_path=f"{TARGET_P10}.sql")

    # If the device is entirely missing from the overrides, we fallback
    self._verify_resolution(
        device_override=device_override,
        model="Pixel 11",
        expected_sql_path=f"{TARGET_FALLBACK}.sql")

  def test_device_specific_query_resolution_fallback(self):
    self._verify_resolution(
        device_override={
            r"Pixel 9.*": TARGET_P9,
        },
        model="Pixel 10 Pro",
        expected_sql_path=f"{TARGET_FALLBACK}.sql")

  def test_device_specific_query_with_replacements(self):
    self._verify_resolution(
        device_override={
            r"Pixel 9.*": TARGET_P9,
        },
        model="Pixel 10 Pro",
        expected_sql_path=f"{TARGET_FALLBACK}.sql",
        replacements={"Chromium": "ReplacedChromium"})

  def test_device_specific_query_resolution_unsupported_returns_none(self):
    query = TraceProcessorQueryConfig.parse({
        "name": "web_power_power_rails",
        "device_override": {
            r"Pixel 9.*": TARGET_P9,
        }
    })

    platform = unittest.mock.MagicMock()
    platform.model = "Pixel 10 Pro"

    resolved = query.resolve_for_platform(platform)
    self.assertIsNone(resolved)

  def test_device_specific_query_resolution_multiple_conflicting_matches(self):
    query = TraceProcessorQueryConfig.parse({
        "name": "web_power_power_rails",
        "device_override": {
            r"Pixel.*": TARGET_P9,
            r"Pixel 10.*": TARGET_P10,
        }
    })
    platform = unittest.mock.MagicMock()
    platform.model = "Pixel 10 Pro"

    with self.assertRaisesRegex(
        ValueError, "Multiple conflicting mappings match device model"):
      query.resolve_for_platform(platform)

  def test_device_specific_query_resolution_multiple_identical_matches(self):
    query = TraceProcessorQueryConfig.parse({
        "name": "web_power_power_rails",
        "device_override": {
            r"Pixel.*": TARGET_P9,
            r"Pixel 9.*": TARGET_P9,
        }
    })
    platform = unittest.mock.MagicMock()
    platform.model = "Pixel 9 Pro"

    resolved = query.resolve_for_platform(platform)
    self.assertIsNotNone(resolved)


class TraceProcessorResultTestCase(BaseCrossbenchTestCase):

  def test_merge_browsers(self):
    probe: TraceProcessorProbe = TraceProcessorProbe.parse_dict({})

    browser = unittest.mock.MagicMock()
    browser.label = "browser"
    browser.unique_name = "browser"

    story = unittest.mock.MagicMock()
    story.name = "story"

    result1 = unittest.mock.MagicMock()
    csv1 = self.create_file("run1/query.csv", contents="foo,bar\n1,2\n")
    json1 = self.create_file(
        "run1/metric.json", contents=json.dumps({"foo": {
            "bar": 7
        }}))
    result1.csv_list = [csv1]
    result1.json_list = [json1]

    run1 = unittest.mock.MagicMock()
    run1.repetition = 0
    run1.results = {probe: result1}
    run1.browser = browser
    run1.story = story
    run1.temperature = "default"

    result2 = unittest.mock.MagicMock()
    csv2 = self.create_file("run2/query.csv", contents="foo,bar\n3,4\n")
    json2 = self.create_file(
        "run2/metric.json", contents=json.dumps({"foo": {
            "bar": 9
        }}))
    result2.csv_list = [csv2]
    result2.json_list = [json2]

    run2 = unittest.mock.MagicMock()
    run2.repetition = 1
    run2.results = {probe: result2}
    run2.browser = browser
    run2.story = story
    run2.temperature = "default"

    rep_group = unittest.mock.MagicMock()
    rep_group.story = story
    rep_group.runs = [run1, run2]

    story_group = unittest.mock.MagicMock()
    story_group.browser = browser
    story_group.repetitions_groups = [rep_group]

    browsers_run_group = unittest.mock.MagicMock()
    browsers_run_group.get_local_probe_result_path = unittest.mock.MagicMock(
        return_value=pth.LocalPath("result/"))
    browsers_run_group.story_groups = [story_group]
    browsers_run_group.runs = [run1, run2]

    merged_result = probe.merge_browsers(browsers_run_group)
    self.assertEqual(len(merged_result.csv_list), 1)
    self.assertEqual(len(merged_result.json_list), 1)

    expected_csv = ("foo,bar,cb_browser,cb_story,cb_temperature,cb_run\n"
                    "1,2,browser,story,default,0\n"
                    "3,4,browser,story,default,1\n")
    with merged_result.csv.open("r") as f:
      self.assertEqual(f.read(), expected_csv)

    with merged_result.json.open("r") as f:
      metrics = json.load(f)
    self.assertTrue("foo/bar" in metrics)
    self.assertTrue("values" in metrics["foo/bar"])
    self.assertSequenceEqual([7, 9], metrics["foo/bar"]["values"])

  def _assert_independent_results(self, config1: dict[str, Any],
                                  config2: dict[str, Any]) -> None:
    probe1 = TraceProcessorProbe.parse_dict(config1)
    probe2 = TraceProcessorProbe.parse_dict(config2)
    res1 = unittest.mock.MagicMock()
    res2 = unittest.mock.MagicMock()

    run = unittest.mock.MagicMock()
    run.results = {probe1: res1, probe2: res2}

    self.assertIs(run.results[probe1], res1)
    self.assertIs(run.results[probe2], res2)

  def test_multiple_probes_preserve_independent_results(self):
    self._assert_independent_results(
        {"metrics": ["metric_cpu"]},
        {"metrics": ["metric_memory"]},
    )
    self._assert_independent_results(
        {"queries": [{
            "name": "query_cpu",
            "sql": "SELECT 1"
        }]},
        {"queries": [{
            "name": "query_memory",
            "sql": "SELECT 2"
        }]},
    )
    self._assert_independent_results(
        {"metrics": ["metric_cpu"]},
        {"queries": [{
            "name": "query_cpu",
            "sql": "SELECT 1"
        }]},
    )

  def _capture_stdout(self, group: unittest.mock.MagicMock, *probes:
                      TraceProcessorProbe) -> str:
    with unittest.mock.patch(
        "sys.stdout", new_callable=io.StringIO) as mock_out:
      for p in probes:
        p.log_browsers_result(group)
      return mock_out.getvalue()

  def _capture_clipboard(self, group: unittest.mock.MagicMock, *probes:
                         TraceProcessorProbe) -> str:
    with unittest.mock.patch.object(plt.PLATFORM, "set_clipboard") as mock_set:
      for p in probes:
        p.log_browsers_result(group)
      if mock_set.called:
        text, = mock_set.call_args.args
        return text
    return ""

  def _test_log_browsers_result(self, key: str, output_capturer):
    probe = TraceProcessorProbe.parse_dict({
        key: ["json"],
    })
    merged_result = unittest.mock.MagicMock(is_empty=False)
    json_file = self.create_file("out/query.json", contents='{"test": 123}')
    merged_result.json_list = [json_file]

    browsers_run_group = unittest.mock.MagicMock(runs=[])
    browsers_run_group.results = {probe: merged_result}

    out = output_capturer(browsers_run_group, probe)
    self.assertIn('{"test": 123}', out)

  def test_log_browsers_result_stdout(self):
    self._test_log_browsers_result("stdout", self._capture_stdout)

  def test_log_browsers_result_clipboard(self):
    self._test_log_browsers_result("clipboard", self._capture_clipboard)

  def _test_log_browsers_result_multiple_runs(self, key: str, output_capturer):
    probe = TraceProcessorProbe.parse_dict({
        key: ["json"],
    })
    merged_result = unittest.mock.MagicMock(is_empty=False)
    f1 = self.create_file("out/q1.json", contents='{"run": 1}')
    f2 = self.create_file("out/q2.json", contents='{"run": 2}')
    merged_result.json_list = [f1, f2]
    group = unittest.mock.MagicMock(runs=[], results={probe: merged_result})

    out = output_capturer(group, probe)
    self.assertIn('{"run": 1}', out)
    self.assertIn('{"run": 2}', out)

  def test_log_browsers_result_multiple_runs_stdout(self):
    self._test_log_browsers_result_multiple_runs("stdout", self._capture_stdout)

  def test_log_browsers_result_multiple_runs_clipboard(self):
    self._test_log_browsers_result_multiple_runs("clipboard",
                                                 self._capture_clipboard)

  def test_log_browsers_result_multiple_probes(self):
    probe_silent = TraceProcessorProbe.parse_dict({"metrics": ["silent"]})
    probe_print = TraceProcessorProbe.parse_dict({
        "metrics": ["printed"],
        "output_to_stdout": ["json"],
    })
    probe_copy = TraceProcessorProbe.parse_dict({
        "metrics": ["clip"],
        "output_to_clipboard": ["csv"],
    })

    res_silent = unittest.mock.MagicMock(is_empty=False)
    res_print = unittest.mock.MagicMock(is_empty=False)
    res_copy = unittest.mock.MagicMock(is_empty=False)

    f_silent = self.create_file("out_silent/q.json", contents='{"silent": 1}')
    f_print = self.create_file("out_print/q.json", contents='{"printed": 2}')
    f_copy = self.create_file("out_copy/q.csv", contents="clip,data\n1,2")

    res_silent.json_list = [f_silent]
    res_print.json_list = [f_print]
    res_copy.csv_list = [f_copy]

    group = unittest.mock.MagicMock(runs=[])
    group.results = {
        probe_silent: res_silent,
        probe_print: res_print,
        probe_copy: res_copy,
    }

    mock_platform = unittest.mock.MagicMock()
    with unittest.mock.patch.object(probe_copy, "_platform", mock_platform):
      out = self._capture_stdout(group, probe_copy, probe_silent, probe_print)
    self.assertNotIn("silent", out)
    self.assertIn('{"printed": 2}', out)
    self.assertNotIn("clip", out)
    mock_platform.set_clipboard.assert_called_once_with("clip,data\n1,2")

  def _test_log_browsers_result_multiple_outputs(self, key: str,
                                                 output_capturer):
    probe = TraceProcessorProbe.parse_dict({
        key: ["json", "csv"],
    })
    merged_result = unittest.mock.MagicMock(is_empty=False)
    json_file = self.create_file("out/q.json", contents='{"metric": 1}')
    csv_file = self.create_file("out/q.csv", contents="col1,col2\n1,2")
    merged_result.json_list = [json_file]
    merged_result.csv_list = [csv_file]
    group = unittest.mock.MagicMock(runs=[], results={probe: merged_result})

    out = output_capturer(group, probe)
    self.assertIn('{"metric": 1}', out)
    self.assertIn("col1,col2", out)

  def test_log_browsers_result_multiple_outputs_stdout(self):
    self._test_log_browsers_result_multiple_outputs("stdout",
                                                    self._capture_stdout)

  def test_log_browsers_result_multiple_outputs_clipboard(self):
    self._test_log_browsers_result_multiple_outputs("clipboard",
                                                    self._capture_clipboard)

  def test_log_browsers_result_stdout_and_clipboard_same_file(self):
    probe = TraceProcessorProbe.parse_dict({
        "stdout": ["json"],
        "clipboard": ["json"],
    })
    merged_result = unittest.mock.MagicMock(is_empty=False)
    json_file = self.create_file("out/q.json", contents='{"metric": 1}')
    merged_result.json_list = [json_file]
    group = unittest.mock.MagicMock(runs=[], results={probe: merged_result})

    mock_platform = unittest.mock.MagicMock()
    with unittest.mock.patch.object(probe, "_platform", mock_platform):
      out = self._capture_stdout(group, probe)

    self.assertEqual(out, '{"metric": 1}\n')
    mock_platform.set_clipboard.assert_called_once_with('{"metric": 1}')


if __name__ == "__main__":
  test_helper.run_pytest(__file__)
