# Copyright 2024 The Chromium Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from __future__ import annotations

import abc
import logging
import subprocess
import sys
from typing import TYPE_CHECKING, Final, Iterator, Sequence

from typing_extensions import override

from crossbench import path as pth
from crossbench import plt
from crossbench.helper import path_finder_base

if TYPE_CHECKING:
  from crossbench.plt.base import Platform


# TODO: replace this file entirely with Binary-lookup approach.


class BasePathFinder(path_finder_base.BasePathFinder):
  """Augmented BasePathFinder providing the `local_binary` classmethod,
  utilizing the global `plt.PLATFORM` object for host-platform path resolution.
  """

  @classmethod
  def local_binary(cls,
                   override: pth.AnyPath | None = None) -> pth.LocalPath | None:
    if override:
      return plt.PLATFORM.parse_local_binary_path(override)
    return cls(plt.PLATFORM).local_path


class ChromiumCheckoutFinder(BasePathFinder,
                             path_finder_base.ChromiumCheckoutFinder):
  """Finds a chromium src checkout at either given locations or at
  some preset known checkout locations.
  """


class ChromiumBuildBinaryFinder(BasePathFinder):
  """Finds a custom-built binary in either a given out/BUILD dir or
  tries to find it in build dirs in common known chromium checkout locations."""

  BUILD_DIR_NAMES: Final[Sequence[str]] = ("Release", "release", "rel",
                                           "Optdebug", "optdebug", "opt")

  def __init__(self, platform: Platform, binary_name: str,
               candidates: tuple[pth.AnyPath, ...]) -> None:
    self._binary_name: Final[str] = binary_name
    self._candidates = candidates
    super().__init__(platform)

  @override
  def candidates(self) -> tuple[pth.AnyPath, ...]:
    return self._candidates

  @property
  def binary_name(self) -> str:
    return self._binary_name

  @override
  def _iterate_candidates(self) -> Iterator[pth.AnyPath]:
    # TOOD: Use candidates x search_paths like on Platform.
    for candidate_dir in self.candidates():
      yield candidate_dir / self._binary_name
      for build in self.BUILD_DIR_NAMES:
        yield candidate_dir / build / self._binary_name

    for candidate in path_finder_base.default_chromium_candidates(
        self.platform):
      candidate_out = candidate / "out"
      if not self.platform.is_dir(candidate_out):
        continue
      # TODO: support remote glob
      for build in self.BUILD_DIR_NAMES:
        yield candidate_out / build / self._binary_name

  @override
  def is_valid_path(self, candidate: pth.AnyPath) -> bool:
    assert candidate.name == self._binary_name, (
        f"Name mismatch: {candidate.name} != {self._binary_name}")
    if not self.platform.is_file(candidate):
      return False
    # .../chromium/src/out/Release/BINARY => .../chromium/src/
    # Don't use parents[] access to stop at the root.
    maybe_checkout_dir = candidate.parent.parent.parent
    return path_finder_base.is_chromium_checkout_dir(self._platform,
                                                     maybe_checkout_dir)


class V8CheckoutFinder(BasePathFinder):

  @override
  def candidates(self) -> tuple[pth.AnyPath, ...]:
    if self.platform.is_android:
      return ()
    home_dir = self._platform.home()
    return (
        # V8 Checkouts
        home_dir / "Documents/v8/v8",
        home_dir / "v8/v8",
        self._platform.path("C:/src/v8/v8"),
        # Raw V8 checkouts
        home_dir / "Documents/v8",
        home_dir / "v8",
        self._platform.path("C:/src/v8/"),
    )

  @override
  def _find_fallback_path(self) -> pth.AnyPath | None:
    if chromium_checkout := ChromiumCheckoutFinder(self._platform).path:
      return chromium_checkout / "v8"
    maybe_d8_path = self.platform.environ.get("D8_PATH")
    if not maybe_d8_path:
      return None
    for candidate_dir in self.platform.path(maybe_d8_path).parents:
      if self.is_valid_path(candidate_dir):
        return candidate_dir
    return None

  @override
  def is_valid_path(self, candidate: pth.AnyPath) -> bool:
    v8_header_file = candidate / "include/v8.h"
    return (self.platform.is_file(v8_header_file) and
            (self.platform.is_dir(candidate / ".git")))


class V8ToolsFinder:
  """Helper class to find d8 binaries and the tick-processor.
  If no explicit d8 and checkout path are given, $D8_PATH and common v8 and
  chromium installation directories are checked."""

  def __init__(self,
               platform: Platform,
               d8_binary: pth.AnyPath | None = None,
               v8_checkout: pth.AnyPath | None = None) -> None:
    self.platform = platform
    self.d8_binary: pth.AnyPath | None = d8_binary
    self.v8_checkout: pth.AnyPath | None = None
    if v8_checkout:
      self.v8_checkout = v8_checkout
    else:
      self.v8_checkout = V8CheckoutFinder(self.platform).path
    self.tick_processor: pth.AnyPath | None = None
    self.v8_logviewer: pth.AnyPath | None = None
    self.d8_binary = self._find_d8()
    if self.d8_binary:
      self.tick_processor = self._find_v8_tick_processor()
      self.v8_logviewer = self._find_v8_logviewer()
    logging.debug(
        "V8ToolsFinder found d8_binary='%s' tick_processor='%s' "
        "v8_logviewer='%s'", self.d8_binary, self.tick_processor,
        self.v8_logviewer)

  def _find_d8(self) -> pth.AnyPath | None:
    if self.d8_binary and self.platform.is_file(self.d8_binary):
      return self.d8_binary
    environ = self.platform.environ
    if "D8_PATH" in environ:
      candidate = self.platform.path(environ["D8_PATH"]) / "d8"
      if self.platform.is_file(candidate):
        return candidate
      candidate = self.platform.path(environ["D8_PATH"])
      if self.platform.is_file(candidate):
        return candidate
    # Try potential build location
    for candidate_dir in V8CheckoutFinder(self.platform).candidates():
      for build_type in ("release", "optdebug", "Default", "Release"):
        candidates = list(
            self.platform.glob(candidate_dir, f"out/*{build_type}/d8"))
        if not candidates:
          continue
        d8_candidate = candidates[0]
        if self.platform.is_file(d8_candidate):
          return d8_candidate
    return None

  def _find_v8_tick_processor(self) -> pth.AnyPath | None:
    if self.platform.is_linux:
      tick_processor = "tools/linux-tick-processor"
    elif self.platform.is_macos:
      tick_processor = "tools/mac-tick-processor"
    elif self.platform.is_win:
      tick_processor = "tools/windows-tick-processor.bat"
    else:
      logging.debug(
          "Not looking for the v8 tick-processor on unsupported platform: %s",
          self.platform)
      return None
    return self._find_v8_tool(tick_processor)

  def _find_v8_logviewer(self) -> pth.AnyPath | None:
    return self._find_v8_tool("tools/v8-logviewer")

  def _find_v8_tool(self, tool_path: pth.AnyPathLike) -> pth.AnyPath | None:
    tool_path = self.platform.path(tool_path)
    if self.v8_checkout and self.platform.is_dir(self.v8_checkout):
      candidate = self.v8_checkout / tool_path
      assert self.platform.is_file(candidate), (
          f"Provided v8_checkout has no '{tool_path}' at {candidate}")
    assert self.d8_binary, "No d8 binary found"
    # Try inferring the V8 checkout from a built d8:
    # .../foo/v8/v8/out/x64.release/d8
    candidate = self.d8_binary.parents[2] / tool_path
    if self.platform.is_file(candidate):
      return candidate
    if self.v8_checkout:
      candidate = self.v8_checkout / tool_path
      if self.platform.is_file(candidate):
        return candidate
    return None


class BaseChromiumPathFinder(BasePathFinder, metaclass=abc.ABCMeta):

  @override
  def is_valid_path(self, candidate: pth.AnyPath) -> bool:
    return self._platform.is_file(candidate)

  @classmethod
  @abc.abstractmethod
  def chrome_path(cls) -> pth.AnyPath:
    """ Path within a chromium checkout. """
    raise NotImplementedError

  @override
  def candidates(self) -> tuple[pth.AnyPath, ...]:
    if maybe_chrome := ChromiumCheckoutFinder(self._platform).path:
      return (maybe_chrome / self.chrome_path(),)
    return ()


class PerfettoFinder(BaseChromiumPathFinder, metaclass=abc.ABCMeta):

  @classmethod
  @abc.abstractmethod
  def binary_name(cls) -> str:
    pass

  @classmethod
  def perfetto_tools_dir(cls) -> pth.AnyPath:
    return pth.AnyPath("third_party/perfetto/tools")

  @classmethod
  @override
  def chrome_path(cls) -> pth.AnyPath:
    return cls.perfetto_tools_dir() / cls.binary_name()


class TraceconvFinder(PerfettoFinder):

  @classmethod
  @override
  def binary_name(cls) -> str:
    return "traceconv"


class TraceboxFinder(PerfettoFinder):

  @classmethod
  @override
  def binary_name(cls) -> str:
    return "tracebox"


class TraceProcessorFinder(PerfettoFinder):

  @classmethod
  @override
  def binary_name(cls) -> str:
    return "trace_processor"


CROSSBENCH_DIR: Final = pth.LocalPath(__file__).parents[2]


class BaseCrossbenchPathFinder(BaseChromiumPathFinder):

  @override
  def candidates(self) -> tuple[pth.AnyPath, ...]:
    candidates = super().candidates()
    return (CROSSBENCH_DIR / self.crossbench_path(), *candidates)

  @classmethod
  @abc.abstractmethod
  def crossbench_path(cls) -> pth.AnyPath:
    pass


class WprGoFinder(BaseCrossbenchPathFinder):

  @override
  def candidates(self) -> tuple[pth.AnyPath, ...]:
    candidates = super().candidates()
    if override := self.platform.lookup_binary_override("wpr"):
      candidates = (self.platform.path(override).parent, *candidates)
    return candidates

  def wpr(self, browser_platform: Platform) -> pth.LocalPath:
    if override := self.platform.lookup_binary_override("wpr"):
      return self.platform.local_path(override)
    return self._build("wpr", browser_platform)

  def httparchive(self) -> pth.LocalPath:
    # httparchive always runs on the host.
    if override := self.platform.lookup_binary_override("httparchive"):
      return self.platform.local_path(override)
    return self._build("httparchive", self.platform)

  def _build(self, binary: str, target_platform: Platform) -> pth.LocalPath:
    if not self.local_path:
      raise FileNotFoundError("WebPageReplay directory not found")

    build_script = self.local_path / "scripts/build.py"
    if not self.platform.is_file(build_script):
      raise FileNotFoundError("WebPageReplay build script not found")

    os_name, arch = target_platform.type_key
    out_dir = self.platform.local_cache_dir("webpagereplay") / os_name / arch
    self.platform.sh(
        sys.executable or "python3",
        build_script,
        "--os",
        os_name,
        "--arch",
        arch,
        "--out-dir",
        out_dir,
        "--binary",
        binary,
        capture_output=True,
    )
    return out_dir / binary

  @override
  def is_valid_path(self, candidate: pth.AnyPath) -> bool:
    if self._platform.is_file(candidate / "scripts/build.py"):
      return True
    return self._platform.is_file(candidate / "deterministic.js")

  @classmethod
  @override
  def chrome_path(cls) -> pth.AnyPath:
    return pth.AnyPath("third_party/webpagereplay")

  @classmethod
  @override
  def crossbench_path(cls) -> pth.AnyPath:
    return pth.AnyPath("third_party/webpagereplay")


class BundletoolFinder(BaseChromiumPathFinder):

  @classmethod
  @override
  def chrome_path(cls) -> pth.AnyPath:
    return pth.AnyPath(
        "third_party/android_build_tools/bundletool/cipd/bundletool.jar")

  @override
  def candidates(self) -> tuple[pth.AnyPath, ...]:
    super_candidates: tuple[pth.AnyPath, ...] = super().candidates()
    if not self.platform.is_macos:
      return super_candidates

    try:
      brew_path: pth.LocalPath = self.platform.local_path(
          self.platform.sh_stdout("brew", "--prefix").strip("\n"))
    except FileNotFoundError:
      return super_candidates
    except subprocess.CalledProcessError:
      return super_candidates

    return (*super_candidates,
            self.platform.local_path(brew_path / "bin/bundletool"))


class LlvmSymbolizerFinder(BaseChromiumPathFinder):

  @classmethod
  @override
  def chrome_path(cls) -> pth.AnyPath:
    return pth.AnyPath(
        "third_party/llvm-build/Release+Asserts/bin/llvm-symbolizer")


class TsProxyFinder(BaseCrossbenchPathFinder):

  @classmethod
  @override
  def chrome_path(cls) -> pth.AnyPath:
    return pth.AnyPath("third_party/catapult/third_party/tsproxy/tsproxy.py")

  @classmethod
  @override
  def crossbench_path(cls) -> pth.AnyPath:
    return pth.AnyPath("third_party/tsproxy/tsproxy.py")


class MinidumpStackwalkFinder(BaseChromiumPathFinder):

  @classmethod
  @override
  def chrome_path(cls) -> pth.AnyPath:
    # This is a common location in some checkouts, but often it is in build dir.
    # BaseChromiumPathFinder will try build dirs if we use
    # ChromiumBuildBinaryFinder.
    return pth.AnyPath(
        "third_party/breakpad/breakpad/src/processor/minidump_stackwalk")


class MinidumpDumpFinder(BaseChromiumPathFinder):

  @classmethod
  @override
  def chrome_path(cls) -> pth.AnyPath:
    return pth.AnyPath(
        "third_party/breakpad/breakpad/src/processor/minidump_dump")


class DumpSymsFinder(BaseChromiumPathFinder):

  @classmethod
  @override
  def chrome_path(cls) -> pth.AnyPath:
    return pth.AnyPath(
        "third_party/breakpad/breakpad/src/tools/linux/dump_syms/dump_syms")


class CrashpadDatabaseUtilFinder(BaseChromiumPathFinder):

  @classmethod
  @override
  def chrome_path(cls) -> pth.AnyPath:
    return pth.AnyPath(
        "third_party/crashpad/crashpad/tools/crashpad_database_util")


class GenerateBreakpadSymbolsFinder(BaseChromiumPathFinder):

  @classmethod
  @override
  def chrome_path(cls) -> pth.AnyPath:
    return pth.AnyPath("third_party/breakpad/breakpad/src/tools/"
                       "linux/generate_breakpad_symbols.py")
