# Copyright 2026 The Chromium Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from __future__ import annotations

import argparse
import contextlib
import io
import json
import sys
import tempfile
import unittest

from crossbench import path as pth
from crossbench.benchmarks.electron.story_pack import StoryPackManifest, \
    current_platform_name
from crossbench.cli.cli import CrossBenchCLI


class StoryPackTestCase(unittest.TestCase):

  def setUp(self) -> None:
    super().setUp()
    self.temp_dir = tempfile.TemporaryDirectory()
    self.pack_dir = pth.LocalPath(self.temp_dir.name) / "pack with spaces"
    self.pack_dir.mkdir()
    self.runner = self.pack_dir / "runner.py"
    self.runner.write_text("# language-neutral runner fixture\n",
                           encoding="utf-8")
    self.manifest_path = self.pack_dir / "stories.json"

  def tearDown(self) -> None:
    self.temp_dir.cleanup()
    super().tearDown()

  def manifest(self, **overrides) -> dict[str, object]:
    manifest: dict[str, object] = {
        "schemaVersion": 1,
        "protocolVersion": 1,
        "pack": {
            "id": "test-pack",
            "version": "1.2.3",
        },
        "runner": {
            "argv": [sys.executable, "runner.py"],
        },
        "stories": [{
            "name": "test.story",
            "description": "Test story",
            "defaultTimeoutMs": 12000,
            "requiredPhases": ["launch", "ready", "shutdown"],
            "platforms": [current_platform_name()],
            "capabilities": ["perfetto-startup"],
            "recommendedProbes": ["electron-startup"],
            "traceInterval": {
                "startMark": "test.start",
                "endMark": "test.end",
            },
        }],
    }
    manifest.update(overrides)
    return manifest

  def parse(self, **overrides) -> StoryPackManifest:
    self.manifest_path.write_text(
        json.dumps(self.manifest(**overrides)), encoding="utf-8")
    return StoryPackManifest.parse(self.manifest_path)

  def test_parse_valid_manifest_and_resolve_pack_path(self) -> None:
    manifest = self.parse()

    self.assertEqual(manifest.pack_id, "test-pack")
    self.assertEqual(manifest.pack_version, "1.2.3")
    self.assertEqual(manifest.runner_argv[0], sys.executable)
    self.assertEqual(manifest.runner_argv[1], str(self.runner.resolve()))
    story = manifest.story("test.story")
    self.assertEqual(story.required_phases,
                     ("launch", "ready", "shutdown"))
    self.assertTrue(story.supports_current_platform())

  def test_single_story_is_implicit_selection(self) -> None:
    self.assertEqual(self.parse().story(None).name, "test.story")

  def test_multiple_stories_require_selection(self) -> None:
    manifest = self.manifest()
    stories = manifest["stories"]
    assert isinstance(stories, list)
    second = dict(stories[0])
    second["name"] = "test.other"
    stories.append(second)
    self.manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    parsed = StoryPackManifest.parse(self.manifest_path)

    with self.assertRaisesRegex(argparse.ArgumentTypeError,
                                "--external-story-name"):
      parsed.story(None)

  def test_duplicate_story_names(self) -> None:
    manifest = self.manifest()
    stories = manifest["stories"]
    assert isinstance(stories, list)
    stories.append(dict(stories[0]))
    self.manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    with self.assertRaisesRegex(argparse.ArgumentTypeError, "duplicate story"):
      StoryPackManifest.parse(self.manifest_path)

  def test_duplicate_required_phases(self) -> None:
    manifest = self.manifest()
    stories = manifest["stories"]
    assert isinstance(stories, list)
    stories[0]["requiredPhases"] = ["launch", "launch"]
    self.manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    with self.assertRaisesRegex(argparse.ArgumentTypeError, "duplicates"):
      StoryPackManifest.parse(self.manifest_path)

  def test_missing_pack_runner_path(self) -> None:
    manifest = self.manifest()
    runner = manifest["runner"]
    assert isinstance(runner, dict)
    runner["argv"] = [sys.executable, "missing.py"]
    self.manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    with self.assertRaisesRegex(argparse.ArgumentTypeError, "does not exist"):
      StoryPackManifest.parse(self.manifest_path)

  def test_unsupported_versions(self) -> None:
    for key in ("schemaVersion", "protocolVersion"):
      with self.subTest(key=key):
        self.manifest_path.write_text(
            json.dumps(self.manifest(**{key: 2})), encoding="utf-8")
        with self.assertRaisesRegex(argparse.ArgumentTypeError, key):
          StoryPackManifest.parse(self.manifest_path)

  def test_invalid_trace_interval(self) -> None:
    manifest = self.manifest()
    stories = manifest["stories"]
    assert isinstance(stories, list)
    stories[0]["traceInterval"] = {"startMark": "test.start"}
    self.manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    with self.assertRaisesRegex(argparse.ArgumentTypeError, "endMark"):
      StoryPackManifest.parse(self.manifest_path)

  def test_empty_trace_interval(self) -> None:
    manifest = self.manifest()
    stories = manifest["stories"]
    assert isinstance(stories, list)
    stories[0]["traceInterval"] = {}
    self.manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    with self.assertRaisesRegex(argparse.ArgumentTypeError,
                                "missing required properties"):
      StoryPackManifest.parse(self.manifest_path)

  def test_unsupported_platform_is_rejected_on_selection(self) -> None:
    manifest = self.manifest()
    stories = manifest["stories"]
    assert isinstance(stories, list)
    stories[0]["platforms"] = [
        "linux" if current_platform_name() == "windows" else "windows"
    ]
    self.manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    parsed = StoryPackManifest.parse(self.manifest_path)

    with self.assertRaisesRegex(argparse.ArgumentTypeError,
                                "does not support"):
      parsed.story("test.story")

  def test_cli_list_and_validate(self) -> None:
    self.parse()
    cli = CrossBenchCLI()
    stdout = io.StringIO()

    with contextlib.redirect_stdout(stdout):
      cli.run(["story-pack", "list", str(self.manifest_path)])
    self.assertIn("test.story", stdout.getvalue())
    self.assertIn("Test story", stdout.getvalue())

    stdout = io.StringIO()
    with contextlib.redirect_stdout(stdout):
      cli.run(["story-pack", "validate", str(self.manifest_path)])
    self.assertIn("Valid story pack test-pack 1.2.3", stdout.getvalue())
