# Copyright 2026 The Chromium Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from __future__ import annotations

import json
from typing import TYPE_CHECKING

import tabulate as tbl
from typing_extensions import override

from crossbench.benchmarks.electron.story_pack import StoryPackManifest
from crossbench.cli.parser import CBArgumentParser
from crossbench.cli.subcommand.base import CrossbenchSubcommand

if TYPE_CHECKING:
  import argparse

  from crossbench.cli.types import Subparsers


class StoryPackSubcommand(CrossbenchSubcommand):
  """Inspect trusted local external application story packs."""

  @override
  def register_subcommand(self,
                          subparsers: Subparsers) -> argparse.ArgumentParser:
    self._parser = subparsers.add_parser(
        "story-pack", help="List or validate an external application story pack")
    self._parser.set_defaults(crossbench_subcommand=self)
    actions = self.parser.add_subparsers(
        title="Story Pack Actions",
        parser_class=CBArgumentParser,
        dest="story_pack_action",
        required=True)
    for action in ("list", "validate"):
      parser = actions.add_parser(action)
      parser.add_argument("manifest", type=StoryPackManifest.parse)
      parser.add_argument(
          "--json", action="store_true", help="Print machine-readable JSON.")
    return self.parser

  @override
  def add_cli_arguments(self, parser: CBArgumentParser) -> CBArgumentParser:
    return parser

  @override
  def run(self, args: argparse.Namespace) -> None:
    manifest: StoryPackManifest = args.manifest
    if args.json:
      print(json.dumps(manifest.as_dict(), indent=2))
      return
    if args.story_pack_action == "validate":
      print(
          f"Valid story pack {manifest.pack_id} {manifest.pack_version}: "
          f"{len(manifest.stories)} stories, protocol "
          f"{manifest.protocol_version}")
      return
    rows = [(
        story.name,
        story.default_timeout_ms,
        "yes" if story.supports_current_platform() else "no",
        story.description,
    ) for story in manifest.stories]
    print(
        tbl.tabulate(
            rows,
            headers=("Story", "Timeout (ms)", "Supported", "Description"),
            tablefmt="plain"))
