# Copyright 2022 The Chromium Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from __future__ import annotations

import abc
import argparse
import datetime as dt
import logging
import re
from typing import TYPE_CHECKING, Any, ClassVar, Final, Generic, Iterable, \
    Mapping, Sequence, TypeAlias, TypeVar, cast

from ordered_set import OrderedSet
from typing_extensions import override

from crossbench.action_runner.config import ActionRunnerConfig
from crossbench.flags.base import Flags
from crossbench.helper import txt_helper
from crossbench.helper.collection_helper import close_matches_message
from crossbench.parse import ObjectParser
from crossbench.stories.press_benchmark import PressBenchmarkStory
from crossbench.stories.story import Story

if TYPE_CHECKING:
  from crossbench import path as pth
  from crossbench.action_runner.base import ActionRunner
  from crossbench.benchmarks.benchmark_probe import BenchmarkProbeMixin
  from crossbench.browsers.attributes import BrowserAttributes
  from crossbench.cli.parser import CBArgumentParser
  from crossbench.cli.types import Subparsers
  from crossbench.device_config import DeviceConfig
  from crossbench.plt.base import Platform
  from crossbench.runner.groups.session import BrowserSessionRunGroup
  from crossbench.runner.run import Run
  from crossbench.runner.runner import Runner
  from crossbench.types import StoryTagLookupT

VersionParts: TypeAlias = tuple[str] | tuple[int, ...]


class Benchmark(abc.ABC):
  # TODO: migrate to abstract class methods
  NAME: ClassVar[str]
  DEFAULT_STORY_CLS: ClassVar[type[Story]] = Story  # type: ignore
  PROBES: ClassVar[tuple[type[BenchmarkProbeMixin], ...]] = ()
  DEFAULT_REPETITIONS: ClassVar[int] = 1
  DEFAULT_COOL_DOWN: ClassVar[dt.timedelta] = dt.timedelta(seconds=2)
  REQUIRED_DEVICE_CONFIG: ClassVar[DeviceConfig | None] = None

  @classmethod
  def cli_help(cls) -> str:
    assert cls.__doc__, (f"Benchmark class {cls} must provide a doc string.")
    # Return the first non-empty line
    help_str: str = cls.__doc__.strip().splitlines()[0]
    if aliases := cls.aliases():
      help_str += f" [{', '.join(aliases)}]"
    return help_str

  @classmethod
  def cli_description(cls) -> str:
    assert cls.__doc__, f"Missing class doc in {cls}"
    return cls.__doc__.strip()

  @classmethod
  def cli_epilog(cls) -> str:
    return ""

  @classmethod
  def short_base_name(cls) -> str:
    return cls.base_name()

  @classmethod
  def base_name(cls) -> str:
    return cls.NAME

  @classmethod
  def version(cls) -> VersionParts:
    return ("default",)

  @classmethod
  def aliases(cls) -> tuple[str, ...]:
    return ()

  @classmethod
  def fast_mode_default_overrides(cls) -> dict[str, Any]:
    """Return benchmark-specific default overrides for --fast mode."""
    return {}

  @classmethod
  def register_subcommand(cls,
                          subparsers: Subparsers) -> argparse.ArgumentParser:
    return subparsers.add_parser(
        cls.NAME,
        aliases=cls.aliases(),
        help=cls.cli_help(),
        description=cls.cli_description(),
        epilog=cls.cli_epilog(),
        formatter_class=argparse.RawDescriptionHelpFormatter)

  @classmethod
  def add_cli_arguments(cls, parser: CBArgumentParser) -> CBArgumentParser:
    parser.add_argument(
        "--action-runner-config",
        "--action-runner",
        type=ActionRunnerConfig.parse,
        help="Set the action runner for interactive pages.",
        required=False)
    return parser

  @classmethod
  def describe(cls) -> dict[str, Any]:
    return {
        "name":
            cls.NAME,
        "aliases":
            cls.aliases() or "None",
        "description":
            "\n".join(txt_helper.wrap_lines(cls.cli_description(), 70)),
        "stories": [],
        "probes-default": {
            probe_cls.NAME:
                "\n".join(
                    list(
                        txt_helper.wrap_lines((probe_cls.__doc__ or "").strip(),
                                              70))) for probe_cls in cls.PROBES
        }
    }

  @classmethod
  def default_probe_config_path(cls) -> pth.LocalPath | None:
    return None

  @classmethod
  def default_network_config_path(cls) -> pth.LocalPath | None:
    return None

  @classmethod
  def extra_flags(cls, browser_attributes: BrowserAttributes,
                  story: Story) -> Flags:
    del browser_attributes
    del story
    return Flags()

  @classmethod
  def kwargs_from_cli(cls, args: argparse.Namespace) -> dict[str, Any]:
    return {"action_runner_config": args.action_runner_config}

  @classmethod
  def from_cli_args(cls, args: argparse.Namespace) -> Benchmark:
    kwargs = cls.kwargs_from_cli(args)
    return cls(**kwargs)

  def __init__(self,
               stories: Sequence[Story],
               action_runner_config: ActionRunnerConfig | None = None) -> None:
    assert self.NAME is not None, f"{self} has no .NAME property"
    assert self.DEFAULT_STORY_CLS != Story, (
        f"{self} has no .DEFAULT_STORY_CLS property")
    self.stories: list[Story] = self._validate_stories(stories)
    self._action_runner_config = action_runner_config or ActionRunnerConfig()

  @property
  def action_runner_config(self) -> ActionRunnerConfig:
    return self._action_runner_config

  def _validate_stories(self, stories: Sequence[Story]) -> list[Story]:
    assert stories, "No stories provided"
    for story in stories:
      assert isinstance(story, self.DEFAULT_STORY_CLS), (
          f"story={story} should be a subclass/the same "
          f"class as {self.DEFAULT_STORY_CLS}")
    return list(stories)

  def new_action_runner(self,
                        platform: Platform,
                        run: Run,
                        step_by_step_mode: bool = False) -> ActionRunner:
    return self._action_runner_config.instantiate(platform, run,
                                                  step_by_step_mode)

  def setup(self, runner: Runner) -> None:
    del runner

  def setup_session_network(self, session: BrowserSessionRunGroup) -> None:
    del session

  def prepare_cli_args(self, args: argparse.Namespace) -> None:
    del args

  @property
  def manages_browser_process(self) -> bool:
    return True

  def teardown(self, runner: Runner) -> None:
    del runner


StoryT = TypeVar("StoryT", bound=Story)


class StoryFilter(Generic[StoryT], metaclass=abc.ABCMeta):
  """
  Filter stories by name or regexp.

  Syntax:
    "all"     Include all stories (defaults to story_names).
    "name"    Include story with the given name.
    "-name"   Exclude story with the given name'
    "foo.*"   Include stories whose name matches the regexp.
    "-foo.*"  Exclude stories whose name matches the regexp.
    "A...B"   Include all default stories from A to B (inclusive).
    "A..."    Include all default stories from the first A.
    "...B"    Include all default stories up to the last B.

  These patterns can be combined:
    [".*", "-foo", "-bar"] Includes all except the "foo" and "bar" story
  """

  DEFAULT_STORY_NAME: ClassVar[str] = "default"

  @classmethod
  def add_cli_arguments(
      cls, parser: argparse.ArgumentParser) -> argparse.ArgumentParser:
    story_filtering_group = parser.add_mutually_exclusive_group()
    cls._add_story_filtering_arguments(story_filtering_group)
    is_combined_group = parser.add_mutually_exclusive_group()
    cls._add_story_grouping_arguments(is_combined_group)

    return parser

  @classmethod
  def _add_story_filtering_arguments(
      cls, group: argparse._MutuallyExclusiveGroup) -> None:
    group.add_argument(
        "--stories",
        "--story",
        dest="stories",
        default=cls.DEFAULT_STORY_NAME,
        help="Comma-separated list of story names. "
        "Use 'all' for selecting all available stories. "
        "Use 'default' for the standard selection of stories. "
        "Use '#tag' to include and '-#tag' to exclude stories by tag.")
    group.add_argument(
        "--story-tags",
        "--story-tag",
        dest="story_tags",
        help="Comma-separated list of tags to include/exclude stories "
        "(e.g., 'tag1,tag2,-tag3'). Mutually exclusive with --stories.")
    group.add_argument(
        "--all",
        dest="stories",
        const="all",
        action="store_const",
        help="Run all stories (shorthand for --stories=all).")

  @classmethod
  def _add_story_grouping_arguments(
      cls, group: argparse._MutuallyExclusiveGroup) -> None:
    group.add_argument(
        "--combined",
        dest="separate",
        default=False,
        action="store_false",
        help="Run each story in the same session. (default)")
    group.add_argument(
        "--separate",
        action="store_true",
        help="Run each story in a fresh browser.")

  @classmethod
  def kwargs_from_cli(cls, args: argparse.Namespace) -> dict[str, Any]:
    if story_tags := args.story_tags:
      tags: tuple[str, ...] = ObjectParser.str_tuple(story_tags, "story_tags")
      patterns: tuple[str, ...] = ()
    else:
      patterns_and_tags = ObjectParser.str_tuple(args.stories, "stories")
      patterns, tags = cls._split_patterns_and_tags(patterns_and_tags, args)
    return {
        "patterns": patterns,
        "args": args,
        "tags": tags,
    }

  @classmethod
  def _split_patterns_and_tags(
      cls, patterns_and_tags: tuple[str, ...],
      args: argparse.Namespace) -> tuple[tuple[str, ...], tuple[str, ...]]:
    if not patterns_and_tags:
      return ("all",), ()

    if cls._is_tag_pattern(patterns_and_tags[0]):
      return cls._split_patterns_as_tags(patterns_and_tags, args)
    return cls._split_patterns_as_names(patterns_and_tags, args)

  @classmethod
  def _is_tag_pattern(cls, pattern: str) -> bool:
    return pattern.startswith(("#", "-#"))

  @classmethod
  def _split_patterns_as_tags(
      cls, patterns_and_tags: tuple[str, ...],
      args: argparse.Namespace) -> tuple[tuple[str, ...], tuple[str, ...]]:
    tags = []
    for pattern in patterns_and_tags:
      if not cls._is_tag_pattern(pattern):
        raise argparse.ArgumentTypeError(
            f"Cannot mix tags and story names in --stories: {args.stories}")
      tags.append(cls._sanitize_tag(pattern))
    return (), tuple(tags)

  @classmethod
  def _split_patterns_as_names(
      cls, patterns_and_tags: tuple[str, ...],
      args: argparse.Namespace) -> tuple[tuple[str, ...], tuple[str, ...]]:
    for pattern in patterns_and_tags:
      if cls._is_tag_pattern(pattern):
        raise argparse.ArgumentTypeError(
            f"Cannot mix tags and story names in --stories: {args.stories}")
    return patterns_and_tags, ()

  @classmethod
  def _sanitize_tag(cls, tag: str) -> str:
    # The hash is not part of the tag name.
    return tag.replace("#", "", 1)

  @classmethod
  def from_cli_args(cls, story_cls: type[StoryT],
                    args: argparse.Namespace) -> StoryFilter[StoryT]:
    kwargs = cls.kwargs_from_cli(args)
    return cls(story_cls, **kwargs)

  def __init__(
      self,
      story_cls: type[StoryT],
      patterns: Sequence[str],
      args: argparse.Namespace,
      separate: bool = False,
      tags: Iterable[str] = (),
  ) -> None:
    self._args = args
    assert args, "Missing args"
    self.story_cls: Final[type[StoryT]] = story_cls
    assert issubclass(
        story_cls, Story), (f"Subclass of {Story} expected, found {story_cls}")
    self._separate: Final[bool] = separate
    self._known_names: Final[OrderedSet[str]] = OrderedSet(
        story_cls.all_story_names())
    assert not (tags and patterns), "Cannot have both tags and names"
    self._patterns: Final[tuple[str, ...]] = tuple(patterns)
    self._tags: Final[tuple[str, ...]] = tuple(tags)
    self.stories: Final[tuple[StoryT, ...]] = self.filter()
    if not self.separate:
      assert len(self.stories) <= 1, "Invalid combined stories count"

  @property
  def args(self) -> argparse.Namespace:
    return self._args

  @property
  def separate(self) -> bool:
    return self._separate

  @property
  def patterns(self) -> tuple[str, ...]:
    return self._patterns

  @property
  def tags(self) -> tuple[str, ...]:
    return self._tags

  def filter(self) -> tuple[StoryT, ...]:
    if self.tags:
      return self.filter_by_tags(self.tags)
    return self.filter_by_name(self.patterns)

  def filter_by_name(self, patterns: Sequence[str]) -> tuple[StoryT, ...]:
    regex_filter = RegexFilter(self.story_cls.all_story_names(),
                               self.story_cls.default_story_names())
    selected_names = regex_filter.process_all(patterns)
    return self.stories_from_names(selected_names)

  def filter_by_tags(self, tags: Sequence[str]) -> tuple[StoryT, ...]:
    tags_filter = TagsFilter(self.story_cls.all_tags_lookup(),
                             self.story_cls.default_story_names())
    selected_names = tags_filter.process_all(tags)
    return self.stories_from_names(selected_names)

  def create_stories(self) -> Sequence[StoryT]:
    return self.stories

  @abc.abstractmethod
  def stories_from_names(self, names: Sequence[str]) -> tuple[StoryT, ...]:
    del names
    return ()


class SubStoryBenchmark(Benchmark, metaclass=abc.ABCMeta):
  STORY_FILTER_CLS: ClassVar[type[StoryFilter]] = StoryFilter  # type: ignore

  @classmethod
  @override
  def cli_description(cls) -> str:
    desc = super().cli_description()
    desc += "\n\n"
    desc += ("Stories (alternatively use the 'describe benchmark "
             f"{cls.NAME}' command):\n")
    desc += ", ".join(cls.all_story_names())
    desc += "\n\n"
    desc += "Filtering (for --stories): "
    assert cls.STORY_FILTER_CLS.__doc__, (
        f"{cls.STORY_FILTER_CLS} has no doc string.")
    desc += cls.STORY_FILTER_CLS.__doc__.strip()

    return desc

  @classmethod
  @override
  def kwargs_from_cli(cls, args: argparse.Namespace) -> dict[str, Any]:
    kwargs = super().kwargs_from_cli(args)
    kwargs["stories"] = cls.stories_from_cli_args(args)
    return kwargs

  @classmethod
  def stories_from_cli_args(cls, args: argparse.Namespace) -> Sequence[Story]:
    return cls.STORY_FILTER_CLS.from_cli_args(cls.DEFAULT_STORY_CLS,
                                              args).stories

  @classmethod
  @override
  def describe(cls) -> dict[str, Any]:
    data = super().describe()
    data["stories"] = cls.describe_stories()
    data["tags"] = cls.DEFAULT_STORY_CLS.all_tags()
    return data

  @classmethod
  def describe_stories(cls) -> Mapping[str, str]:
    # TODO: use story objects instead
    return dict.fromkeys(cls.all_story_names(), "")

  @classmethod
  def all_stories(cls) -> Sequence[Story]:
    all_args = argparse.Namespace()
    return cls.STORY_FILTER_CLS(
        cls.DEFAULT_STORY_CLS, ["all"], args=all_args, separate=True).stories

  @classmethod
  def all_story_names(cls) -> tuple[str, ...]:
    return tuple(sorted(cls.DEFAULT_STORY_CLS.all_story_names()))

  @classmethod
  def all_story_tags_lookup(cls) -> StoryTagLookupT:
    return cls.DEFAULT_STORY_CLS.all_tags_lookup()

  @classmethod
  def all_story_tags(cls) -> tuple[str, ...]:
    return cls.DEFAULT_STORY_CLS.all_tags()


PressBenchmarkStoryT = TypeVar(
    "PressBenchmarkStoryT", bound=PressBenchmarkStory)


class RangePatternError(argparse.ArgumentTypeError):

  def __init__(self, pattern: str, message: str):
    super().__init__(f"Invalid range pattern {pattern!r}. {message}")


class RegexFilter:

  def __init__(self, all_names: Sequence[str], default_names: Sequence[str]):
    self._all_names: OrderedSet[str] = OrderedSet(all_names)
    self._default_names: OrderedSet[str] = OrderedSet(default_names)
    self._selected_names: OrderedSet[str] = OrderedSet()
    for name in self._all_names:
      self.verify_story_name(name)

  def verify_story_name(self, name: str) -> None:
    # TODO: make story_cls configurable.
    Story.verify_story_name(name)
    if name in ("default", "all"):
      raise ValueError(
          f"Cannot use reserved identifier for default story names: "
          f"{name!r}")

  def process_all(self, patterns: Sequence[str]) -> OrderedSet[str]:
    if not isinstance(patterns, (list, tuple)):
      raise ValueError("Expected Sequence of story name or patterns "
                       f"but got {type(patterns)!r}.")
    for pattern in patterns:
      self.process_pattern(pattern)
    return self._selected_names

  def process_pattern(self, pattern: str) -> None:
    if "..." in pattern:
      self._process_range_pattern(pattern)
      return
    if pattern.startswith("-"):
      self.remove(pattern[1:])
    else:
      self.add(pattern)

  def _process_range_pattern(self, pattern: str) -> None:
    parts = pattern.split("...")
    if len(parts) != 2:
      raise RangePatternError(pattern, "Expected exactly one '...' separator.")
    start_pattern, end_pattern = parts
    if not start_pattern and not end_pattern:
      raise RangePatternError(pattern,
                              "Start and end patterns cannot both be empty.")
    if start_pattern.startswith("-"):
      raise RangePatternError(
          pattern, f"Start pattern {start_pattern!r} must not be negative.")
    if end_pattern.startswith("-"):
      raise RangePatternError(
          pattern, f"End pattern {end_pattern!r} must not be negative.")

    default_names_list = list(self._default_names)
    if not default_names_list:
      return

    start_index = 0
    if start_pattern:
      start_matches = self._find_matches_in_list(start_pattern,
                                                 default_names_list)
      if not start_matches:
        raise ValueError(
            f"Start pattern {start_pattern!r} matched no default stories.")
      # Start is the first match
      first_match = start_matches[0]
      start_index = default_names_list.index(first_match)

    end_index = len(default_names_list) - 1
    if end_pattern:
      end_matches = self._find_matches_in_list(end_pattern, default_names_list)
      if not end_matches:
        raise ValueError(
            f"End pattern {end_pattern!r} matched no default stories.")
      # End is the last match
      last_match = end_matches[-1]
      end_index = default_names_list.index(last_match)

    if start_index > end_index:
      start_name = default_names_list[start_index]
      end_name = default_names_list[end_index]
      raise ValueError(
          f"Range start {start_name!r} (index {start_index}) "
          f"comes after range end {end_name!r} (index {end_index}).")

    selection = default_names_list[start_index:end_index + 1]
    self._selected_names.update(selection)

  def _find_matches_in_list(self, pattern: str, names: list[str]) -> list[str]:
    regexp = self._pattern_to_regexp(pattern)
    matches = [name for name in names if regexp.fullmatch(name)]
    if not matches:
      # Try case insensitive
      iregexp = re.compile(regexp.pattern, flags=re.IGNORECASE)
      matches = [name for name in names if iregexp.fullmatch(name)]
    return matches

  def add(self, pattern: str) -> None:
    self._check_processed_pattern(pattern)
    regexp = self._pattern_to_regexp(pattern)
    self._add_matching(regexp, pattern)

  def remove(self, pattern: str) -> None:
    self._check_processed_pattern(pattern)
    regexp = self._pattern_to_regexp(pattern)
    self._remove_matching(regexp, pattern)

  def _pattern_to_regexp(self, pattern: str) -> re.Pattern:
    if pattern == "all":
      return re.compile(".*")
    if pattern == "default":
      if self._default_names == self._all_names:
        return re.compile(".*")
      joined_names = "|".join(re.escape(name) for name in self._default_names)
      return re.compile(f"^({joined_names})$")
    if pattern in self._all_names:
      return re.compile(re.escape(pattern))
    return re.compile(pattern)

  def _check_processed_pattern(self, pattern: str) -> None:
    if not pattern:
      raise ValueError("Empty pattern is not allowed")
    if pattern == "-":
      raise ValueError(f"Empty remove pattern not allowed: {pattern!r}")
    if pattern[0] == "-":
      raise ValueError(f"Unprocessed negative pattern not allowed: {pattern!r}")

  def _add_matching(self, regexp: re.Pattern, original_pattern: str) -> None:
    substories = self._regexp_match(regexp, original_pattern)
    self._selected_names.update(substories)

  def _remove_matching(self, regexp: re.Pattern, original_pattern: str) -> None:
    substories = self._regexp_match(regexp, original_pattern)
    for substory in substories:
      try:
        self._selected_names.remove(substory)
      except KeyError as e:
        raise ValueError(
            "Removing Story failed: "
            f"name={substory!r} extracted by pattern={original_pattern!r}"
            "is not in the filtered story list") from e

  def _regexp_match(self, regexp: re.Pattern,
                    original_pattern: str) -> list[str]:
    substories = [
        substory for substory in self._all_names if regexp.fullmatch(substory)
    ]
    if not substories:
      substories = self._regexp_match_ignorecase(regexp)
    if not substories:
      return self._handle_no_match(original_pattern)
    if len(substories) == len(self._all_names) and self._selected_names:
      raise ValueError(f"{original_pattern!r} matched all and overrode all"
                       "previously filtered story names.")
    return substories

  def _regexp_match_ignorecase(self, regexp: re.Pattern) -> list[str]:
    logging.warning(
        "No matching stories, using case-insensitive fallback regexp.")
    iregexp: re.Pattern = re.compile(regexp.pattern, flags=re.IGNORECASE)
    return [
        substory for substory in self._all_names if iregexp.fullmatch(substory)
    ]

  def _handle_no_match(self, original_pattern: str) -> list[str]:
    error_message, alternative = close_matches_message(original_pattern,
                                                       self._all_names,
                                                       "Story name")
    if alternative:
      logging.error(error_message)
      return [alternative]
    raise ValueError(error_message)


class TagsFilter:

  def __init__(self, story_tags: StoryTagLookupT,
               default_names: Sequence[str]) -> None:
    self._story_tags: StoryTagLookupT = story_tags
    self._available_tags: OrderedSet[str] = OrderedSet(
        tag for tags in self._story_tags.values() for tag in tags)
    self._default_names: OrderedSet[str] = OrderedSet(default_names)

  def process_all(self, tags: Sequence[str]) -> Sequence[str]:
    if not tags:
      return self._default_names

    if not self._available_tags:
      raise ValueError(f"No tags available, ignoring tags: {tags}")

    include_tags, exclude_tags = self._parse_tags(tags)
    return self._filter_story_names(tags, include_tags, exclude_tags)

  def _parse_tags(self, tags: Sequence[str]) -> tuple[set[str], set[str]]:
    include_tags = set()
    exclude_tags = set()
    for tag in tags:
      is_exclude = tag.startswith("-")
      if is_exclude:
        tag = tag[1:]
      if not tag:
        raise ValueError("Empty tag")
      if tag not in self._available_tags:
        error_message, alternative = close_matches_message(
            tag, self._available_tags, "story tag")
        if not alternative:
          raise ValueError(error_message)
        logging.error(error_message)
        tag = alternative

      if is_exclude:
        exclude_tags.add(tag)
      else:
        include_tags.add(tag)

    intersection = include_tags.intersection(exclude_tags)
    if intersection:
      raise ValueError(
          f"Tags cannot be both included and excluded: {intersection}")

    return include_tags, exclude_tags

  def _filter_story_names(self, tags: Sequence[str], include_tags: set[str],
                          exclude_tags: set[str]) -> Sequence[str]:
    filtered_names = []
    for story_name, tags_list in self._story_tags.items():
      story_tags_set = set(tags_list)
      if exclude_tags.intersection(story_tags_set):
        continue
      if include_tags and not include_tags.intersection(story_tags_set):
        continue
      filtered_names.append(story_name)

    if not filtered_names:
      raise ValueError(f"No stories left after filtering for tags: {tags}")
    return filtered_names


class PressBenchmarkStoryFilter(StoryFilter[PressBenchmarkStoryT],
                                Generic[PressBenchmarkStoryT]):
  __doc__ = StoryFilter.__doc__

  @classmethod
  @override
  def kwargs_from_cli(cls, args: argparse.Namespace) -> dict[str, Any]:
    kwargs = super().kwargs_from_cli(args)
    kwargs["separate"] = args.separate
    kwargs["url"] = args.custom_benchmark_url
    return kwargs

  def __init__(
      self,
      story_cls: type[PressBenchmarkStoryT],
      patterns: Sequence[str],
      args: argparse.Namespace,
      separate: bool = False,
      url: str | None = None,
      tags: Iterable[str] = (),
  ) -> None:
    self.url: str | None = url
    super().__init__(story_cls, patterns, args, separate, tags)
    assert issubclass(self.story_cls, PressBenchmarkStory)

  def stories_from_names(
      self, names: Sequence[str]) -> tuple[PressBenchmarkStoryT, ...]:
    return self.story_cls.from_names(
        names, separate=self.separate, url=self.url)


class PressBenchmark(SubStoryBenchmark):
  STORY_FILTER_CLS: ClassVar[
      type[PressBenchmarkStoryFilter]] = PressBenchmarkStoryFilter
  DEFAULT_STORY_CLS: ClassVar[
      type[PressBenchmarkStory]] = PressBenchmarkStory  # type: ignore

  @classmethod
  @abc.abstractmethod
  def short_base_name(cls) -> str:
    raise NotImplementedError

  @classmethod
  @abc.abstractmethod
  def base_name(cls) -> str:
    raise NotImplementedError

  @classmethod
  @abc.abstractmethod
  def version(cls) -> VersionParts:
    raise NotImplementedError

  @classmethod
  @override
  def aliases(cls) -> tuple[str, ...]:
    raw_version: VersionParts = cls.version()
    is_branch_version = (
        len(raw_version) == 1 and isinstance(raw_version[0], str))
    if not is_branch_version:
      assert (all((isinstance(part, int)) for part in raw_version)), (
          "All version parts should be integers.")
    version = [str(v) for v in raw_version]
    assert version, "Expected non-empty version tuple."
    version_names = []
    dot_version = ".".join(version)
    for name in (cls.short_base_name(), cls.base_name()):
      assert name, "Expected non-empty base name."
      if not is_branch_version:
        version_names.append(f"{name}{dot_version}")
      version_name = f"{name}_{dot_version}"
      if version_name != cls.NAME:
        version_names.append(version_name)
    return tuple(version_names)

  @classmethod
  @override
  def add_cli_arguments(cls, parser: CBArgumentParser) -> CBArgumentParser:
    super().add_cli_arguments(parser)
    # TODO: Move story-related args to dedicated PressBenchmarkStoryFilter class
    cls._add_story_url_arguments(parser)
    cls.STORY_FILTER_CLS.add_cli_arguments(parser)
    return parser

  @classmethod
  def _add_story_url_arguments(cls, parser: CBArgumentParser) -> None:
    benchmark_url_group = parser.add_argument_group(
        "Story URL Options").add_mutually_exclusive_group()
    live_url: str = cls.DEFAULT_STORY_CLS.URL
    local_url: str = cls.DEFAULT_STORY_CLS.URL_LOCAL
    official_url: str = cls.DEFAULT_STORY_CLS.URL_OFFICIAL
    benchmark_url_group.add_argument(
        "--live",
        "--live-url",
        "--browser-ben",
        "--browserben",
        dest="custom_benchmark_url",
        const=None,
        action="store_const",
        help=(f"Use chrome live benchmark url ({live_url}) "
              "on https://browserben.ch."))
    benchmark_url_group.add_argument(
        "--official",
        "--official-url",
        dest="custom_benchmark_url",
        const=official_url,
        action="store_const",
        help=(f"Use officially hosted live/online benchmark url "
              f"({official_url})."))
    benchmark_url_group.add_argument(
        "--local",
        "--local-url",
        "--url",
        "--custom-benchmark-url",
        type=ObjectParser.httpx_url_str,
        nargs="?",
        dest="custom_benchmark_url",
        const=local_url,
        help=(f"Use custom or locally (default={local_url}) "
              "hosted benchmark url."))

    if custom_fork_url := getattr(cls.DEFAULT_STORY_CLS, "URL_CHROME_FORK",
                                  None):
      benchmark_url_group.add_argument(
          "--custom",
          "--chrome-custom-fork",
          "--chrome-fork",
          action="store_const",
          dest="custom_benchmark_url",
          const=custom_fork_url,
          help=(f"Use custom chrome fork hosted on {custom_fork_url}. "
                "This include additional options and performance.mark calls "
                "for easier investigation."))

  @classmethod
  @override
  def kwargs_from_cli(cls, args: argparse.Namespace) -> dict[str, Any]:
    kwargs = super().kwargs_from_cli(args)
    kwargs["custom_url"] = args.custom_benchmark_url
    return kwargs

  @classmethod
  @override
  def describe(cls) -> dict[str, Any]:
    data = super().describe()
    assert issubclass(cls.DEFAULT_STORY_CLS, PressBenchmarkStory)
    data["url"] = cls.DEFAULT_STORY_CLS.URL
    data["url-official"] = cls.DEFAULT_STORY_CLS.URL_OFFICIAL
    data["url-local"] = cls.DEFAULT_STORY_CLS.URL_LOCAL
    data["version"] = ".".join(map(str, cls.version()))
    return data

  def __init__(self,
               stories: Sequence[Story],
               action_runner_config: ActionRunnerConfig | None = None,
               custom_url: str | None = None) -> None:
    super().__init__(stories, action_runner_config)
    self.custom_url = custom_url
    if custom_url:
      for story in stories:
        press_story = cast(PressBenchmarkStory, story)
        assert press_story.url == custom_url, (
            f"Expected custom url on {press_story} to be {custom_url} "
            f"but got {press_story.url}")

  @override
  def setup(self, runner: Runner) -> None:
    super().setup(runner)
    self.validate_url(runner)

  def validate_url(self, runner: Runner) -> None:
    if self.custom_url:
      if runner.has_any_live_network():
        self._validate_custom_url(runner, self.custom_url)
      return
    first_story = cast(PressBenchmarkStory, self.stories[0])
    url = first_story.url
    if not runner.has_all_live_network() and not url:
      # For non-live networks we create a matching URL
      return
    if not url:
      raise ValueError("Invalid empty url")
    if all(runner.env.validate_url(url, p) for p in runner.platforms):
      return
    msg = [
        f"Could not reach live benchmark URL: {url!r}."
        f"Please make sure you're connected to the internet."
    ]
    local_url = first_story.URL_LOCAL
    if local_url:
      msg.append(
          f"Alternatively use --local for the default local URL: {local_url}")
    raise ValueError("\n".join(msg))

  def _validate_custom_url(self, runner: Runner, url: str) -> None:
    if not all(runner.env.validate_url(url, p) for p in runner.platforms):
      raise ValueError(
          f"Could not reach custom benchmark URL: {self.custom_url!r}. "
          f"Please make sure your local web server is running.")
