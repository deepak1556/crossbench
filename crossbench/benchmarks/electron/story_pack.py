# Copyright 2026 The Chromium Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from __future__ import annotations

import argparse
import dataclasses
import re
from typing import Any, Final, Mapping

from typing_extensions import Self, override

from crossbench import path as pth
from crossbench import plt
from crossbench.config import ConfigObject
from crossbench.parse import NumberParser, ObjectParser, PathParser

MANIFEST_SCHEMA_VERSION: Final[int] = 1
PACK_VERSION_RE: Final[re.Pattern[str]] = re.compile(
    r"^[0-9]+\.[0-9]+\.[0-9]+(?:[-+][0-9A-Za-z.-]+)?$")
SUPPORTED_PLATFORMS: Final[frozenset[str]] = frozenset(
    ("windows", "linux", "macos"))
PLATFORM_ALIASES: Final[Mapping[str, str]] = {
    "win32": "windows",
    "darwin": "macos",
}


def _expect_dict(value: object, name: str) -> dict[str, Any]:
  return ObjectParser.dict(value, name)


def _expect_keys(value: Mapping[str, Any], name: str, allowed: set[str],
                 required: set[str]) -> None:
  missing = required.difference(value)
  if missing:
    raise argparse.ArgumentTypeError(
        f"{name} is missing required properties: {sorted(missing)}")
  extra = set(value).difference(allowed)
  if extra:
    raise argparse.ArgumentTypeError(
        f"{name} has unsupported properties: {sorted(extra)}")


def _parse_string_tuple(value: object,
                        name: str,
                        *,
                        required: bool = False,
                        unique: bool = True) -> tuple[str, ...]:
  if not isinstance(value, (list, tuple)):
    raise argparse.ArgumentTypeError(f"{name} must be an array of strings.")
  result = tuple(
      ObjectParser.non_empty_str(item, f"{name} item") for item in value)
  if required and not result:
    raise argparse.ArgumentTypeError(f"{name} must not be empty.")
  if unique and len(set(result)) != len(result):
    raise argparse.ArgumentTypeError(f"{name} must not contain duplicates.")
  return result


def current_platform_name() -> str:
  if plt.PLATFORM.is_win:
    return "windows"
  if plt.PLATFORM.is_macos:
    return "macos"
  if plt.PLATFORM.is_linux:
    return "linux"
  return plt.PLATFORM.name


@dataclasses.dataclass(frozen=True)
class StoryPackTraceInterval(ConfigObject):
  start_mark: str
  end_mark: str

  @classmethod
  @override
  def parse_dict(cls, config: dict[str, Any], **kwargs) -> Self:
    cls.expect_no_extra_kwargs(kwargs)
    _expect_keys(config, "traceInterval", {"startMark", "endMark"},
                 {"startMark", "endMark"})
    start_mark = ObjectParser.non_empty_str(config["startMark"], "startMark")
    end_mark = ObjectParser.non_empty_str(config["endMark"], "endMark")
    if start_mark == end_mark:
      raise argparse.ArgumentTypeError(
          "traceInterval startMark and endMark must be different.")
    return cls(start_mark, end_mark)

  @classmethod
  @override
  def parse_str(cls, value: str) -> Self:
    raise argparse.ArgumentTypeError(
        f"traceInterval must be an object, got {value!r}.")

  def as_dict(self) -> dict[str, str]:
    return {
        "startMark": self.start_mark,
        "endMark": self.end_mark,
    }


@dataclasses.dataclass(frozen=True)
class StoryPackStory(ConfigObject):
  name: str
  description: str
  default_timeout_ms: int
  required_phases: tuple[str, ...]
  platforms: tuple[str, ...] = ()
  capabilities: tuple[str, ...] = ()
  recommended_probes: tuple[str, ...] = ()
  trace_interval: StoryPackTraceInterval | None = None

  @classmethod
  @override
  def parse_dict(cls, config: dict[str, Any], **kwargs) -> Self:
    cls.expect_no_extra_kwargs(kwargs)
    allowed = {
        "name",
        "description",
        "defaultTimeoutMs",
        "requiredPhases",
        "platforms",
        "capabilities",
        "recommendedProbes",
        "traceInterval",
    }
    required = {
        "name", "description", "defaultTimeoutMs", "requiredPhases"
    }
    _expect_keys(config, "story descriptor", allowed, required)
    platforms = tuple(
        PLATFORM_ALIASES.get(name, name) for name in _parse_string_tuple(
            config.get("platforms", ()), "platforms"))
    unsupported = set(platforms).difference(SUPPORTED_PLATFORMS)
    if unsupported:
      raise argparse.ArgumentTypeError(
          f"Unsupported story platforms: {sorted(unsupported)}. "
          f"Supported values: {sorted(SUPPORTED_PLATFORMS)}")
    if len(set(platforms)) != len(platforms):
      raise argparse.ArgumentTypeError(
          "platforms must not contain duplicate aliases.")
    trace_interval = None
    if "traceInterval" in config:
      trace_interval = StoryPackTraceInterval.parse(config["traceInterval"])
    return cls(
        name=ObjectParser.non_empty_str(config["name"], "story name"),
        description=ObjectParser.non_empty_str(config["description"],
                                               "story description"),
        default_timeout_ms=NumberParser.positive_int(
            config["defaultTimeoutMs"], "defaultTimeoutMs"),
        required_phases=_parse_string_tuple(
            config["requiredPhases"], "requiredPhases", required=True),
        platforms=platforms,
        capabilities=_parse_string_tuple(config.get("capabilities", ()),
                                         "capabilities"),
        recommended_probes=_parse_string_tuple(
            config.get("recommendedProbes", ()), "recommendedProbes"),
        trace_interval=trace_interval)

  @classmethod
  @override
  def parse_str(cls, value: str) -> Self:
    raise argparse.ArgumentTypeError(
        f"Story descriptor must be an object, got {value!r}.")

  def supports_current_platform(self) -> bool:
    return not self.platforms or current_platform_name() in self.platforms

  def as_dict(self) -> dict[str, object]:
    result: dict[str, object] = {
        "name": self.name,
        "description": self.description,
        "defaultTimeoutMs": self.default_timeout_ms,
        "requiredPhases": list(self.required_phases),
        "platforms": list(self.platforms),
        "capabilities": list(self.capabilities),
        "recommendedProbes": list(self.recommended_probes),
        "supported": self.supports_current_platform(),
    }
    if self.trace_interval:
      result["traceInterval"] = self.trace_interval.as_dict()
    return result


@dataclasses.dataclass(frozen=True)
class StoryPackManifest(ConfigObject):
  path: pth.LocalPath
  protocol_version: int
  pack_id: str
  pack_version: str
  runner_argv: tuple[str, ...]
  stories: tuple[StoryPackStory, ...]

  VALID_EXTENSIONS = (".json", ".hjson")

  @classmethod
  @override
  def parse_path(cls, path: pth.LocalPath, **kwargs) -> Self:
    cls.expect_no_extra_kwargs(kwargs)
    config = ObjectParser.dict_hjson_file(path)
    return cls.parse_dict(config, path=path)

  @classmethod
  @override
  def parse_dict(cls, config: dict[str, Any], **kwargs) -> Self:
    path = kwargs.pop("path", None)
    cls.expect_no_extra_kwargs(kwargs)
    if path is None:
      raise argparse.ArgumentTypeError(
          "Story pack manifest parsing requires a source path.")
    manifest_path = PathParser.existing_file_path(path, "story pack manifest")
    allowed = {
        "schemaVersion", "protocolVersion", "pack", "runner", "stories"
    }
    _expect_keys(config, "story pack manifest", allowed, allowed)
    cls._validate_version(config, "schemaVersion", MANIFEST_SCHEMA_VERSION)
    cls._validate_version(config, "protocolVersion", 1)
    pack_id, pack_version = cls._parse_pack(config["pack"])
    runner_argv = cls._parse_runner(config["runner"], manifest_path.parent)
    raw_stories = config["stories"]
    if not isinstance(raw_stories, list) or not raw_stories:
      raise argparse.ArgumentTypeError(
          "Story pack manifest stories must be a non-empty array.")
    stories = tuple(StoryPackStory.parse(value) for value in raw_stories)
    names = [story.name for story in stories]
    if len(set(names)) != len(names):
      raise argparse.ArgumentTypeError(
          "Story pack manifest contains duplicate story names.")
    return cls(manifest_path, 1, pack_id, pack_version, runner_argv, stories)

  @classmethod
  @override
  def parse_str(cls, value: str) -> Self:
    raise argparse.ArgumentTypeError(
        f"Story pack manifest path does not exist: {value!r}")

  @staticmethod
  def _validate_version(config: Mapping[str, Any], key: str,
                        expected: int) -> None:
    value = config[key]
    if isinstance(value, bool) or not isinstance(value, int):
      raise argparse.ArgumentTypeError(f"{key} must be an integer.")
    if value != expected:
      raise argparse.ArgumentTypeError(
          f"Unsupported {key}={value}; expected {expected}.")

  @staticmethod
  def _parse_pack(value: object) -> tuple[str, str]:
    pack = _expect_dict(value, "pack")
    _expect_keys(pack, "pack", {"id", "version"}, {"id", "version"})
    pack_id = ObjectParser.non_empty_str(pack["id"], "pack id")
    pack_version = ObjectParser.non_empty_str(pack["version"], "pack version")
    if not PACK_VERSION_RE.fullmatch(pack_version):
      raise argparse.ArgumentTypeError(
          f"pack version must be semantic version x.y.z, got {pack_version!r}.")
    return pack_id, pack_version

  @staticmethod
  def _parse_runner(value: object,
                    pack_dir: pth.LocalPath) -> tuple[str, ...]:
    runner = _expect_dict(value, "runner")
    _expect_keys(runner, "runner", {"argv"}, {"argv"})
    argv = _parse_string_tuple(
        runner["argv"], "runner.argv", required=True, unique=False)
    if argv[0].startswith("-"):
      raise argparse.ArgumentTypeError(
          "runner.argv[0] must name an executable, not an option.")
    resolved = []
    for index, item in enumerate(argv):
      resolved.append(
          StoryPackManifest._resolve_runner_arg(item, index, pack_dir))
    return tuple(resolved)

  @staticmethod
  def _resolve_runner_arg(item: str, index: int,
                          pack_dir: pth.LocalPath) -> str:
    if "\0" in item:
      raise argparse.ArgumentTypeError("runner.argv must not contain NUL.")
    path = pth.LocalPath(item)
    if path.is_absolute():
      if index == 0:
        PathParser.file_path(path, f"runner.argv[{index}]")
      elif path.suffix or len(path.parts) > 1:
        PathParser.existing_path(path, f"runner.argv[{index}]")
      return str(path)
    candidate = pack_dir / path
    is_pack_path = candidate.exists()
    looks_like_path = (
        index > 0 and
        (path.suffix != "" or len(path.parts) > 1 or item.startswith(".")))
    if is_pack_path:
      if index == 0:
        PathParser.file_path(candidate, f"runner.argv[{index}]")
      return str(candidate.resolve())
    if looks_like_path:
      raise argparse.ArgumentTypeError(
          f"runner.argv[{index}] path does not exist in story pack: "
          f"{candidate}")
    if index == 0 and not plt.PLATFORM.which(item):
      raise argparse.ArgumentTypeError(
          f"runner executable {item!r} was not found in the story pack or PATH.")
    return item

  def story(self, name: str | None) -> StoryPackStory:
    selected_story: StoryPackStory | None
    if name is None:
      if len(self.stories) == 1:
        selected_story = self.stories[0]
      else:
        names = ", ".join(story.name for story in self.stories)
        raise argparse.ArgumentTypeError(
            "Select a story with --external-story-name. "
            f"Available stories: {names}")
    else:
      selected_story = next(
          (story for story in self.stories if story.name == name), None)
      if selected_story is None:
        names = ", ".join(story.name for story in self.stories)
        raise argparse.ArgumentTypeError(
            f"Story {name!r} is not in pack {self.pack_id!r}. "
            f"Available stories: {names}")
    assert selected_story is not None
    if not selected_story.supports_current_platform():
      raise argparse.ArgumentTypeError(
          f"Story {selected_story.name!r} does not support "
          f"{current_platform_name()!r}; supported platforms: "
          f"{selected_story.platforms}")
    return selected_story

  def as_dict(self) -> dict[str, object]:
    return {
        "schemaVersion": MANIFEST_SCHEMA_VERSION,
        "protocolVersion": self.protocol_version,
        "pack": {
            "id": self.pack_id,
            "version": self.pack_version,
        },
        "runner": {
            "argv": list(self.runner_argv),
        },
        "stories": [story.as_dict() for story in self.stories],
    }
