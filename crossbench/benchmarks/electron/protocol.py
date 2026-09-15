# Copyright 2026 The Chromium Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from __future__ import annotations

import dataclasses
import math
from typing import TYPE_CHECKING, Any, Final, Mapping

if TYPE_CHECKING:
  from crossbench.types import JsonDict

SCHEMA_VERSION: Final[int] = 1


class ExternalStoryProtocolError(ValueError):
  pass


@dataclasses.dataclass(frozen=True)
class ExternalStoryPhase:
  start_time_ms: float
  end_time_ms: float
  duration_ms: float

  @classmethod
  def parse(cls, name: str, value: object) -> ExternalStoryPhase:
    if not isinstance(value, dict):
      raise ExternalStoryProtocolError(f"Phase {name!r} must be an object.")
    start_time_ms = cls._number(value, "startTimeMs", name)
    end_time_ms = cls._number(value, "endTimeMs", name)
    duration_ms = cls._number(value, "durationMs", name)
    if end_time_ms < start_time_ms:
      raise ExternalStoryProtocolError(f"Phase {name!r} ends before it starts.")
    expected_duration = end_time_ms - start_time_ms
    if not math.isclose(
        duration_ms, expected_duration, rel_tol=1e-6, abs_tol=1e-3):
      raise ExternalStoryProtocolError(
          f"Phase {name!r} durationMs does not match endTimeMs-startTimeMs.")
    return cls(start_time_ms, end_time_ms, duration_ms)

  @staticmethod
  def _number(value: Mapping[str, object], key: str, name: str) -> float:
    number = value.get(key)
    if isinstance(number, bool) or not isinstance(number, (int, float)):
      raise ExternalStoryProtocolError(f"Phase {name!r} has invalid {key!r}.")
    result = float(number)
    if not math.isfinite(result) or result < 0:
      raise ExternalStoryProtocolError(f"Phase {name!r} has invalid {key!r}.")
    return result


@dataclasses.dataclass(frozen=True)
class ExternalStoryResult:
  run_id: str
  story: str
  phases: Mapping[str, ExternalStoryPhase]
  metadata: JsonDict
  artifacts: JsonDict

  @classmethod
  def parse(cls, value: object, run_id: str, story: str,
            required_phases: tuple[str, ...]) -> ExternalStoryResult:
    if not isinstance(value, dict):
      raise ExternalStoryProtocolError("Result must be a JSON object.")
    cls._validate_identity(value, run_id, story)
    cls._validate_success(value)
    raw_phases = value.get("phases")
    if not isinstance(raw_phases, dict):
      raise ExternalStoryProtocolError("Result is missing phases object.")
    phases = {}
    for name, phase in raw_phases.items():
      if not isinstance(name, str) or not name:
        raise ExternalStoryProtocolError(f"Invalid phase name: {name!r}.")
      phases[name] = ExternalStoryPhase.parse(name, phase)
    missing_phases = set(required_phases).difference(phases)
    if missing_phases:
      raise ExternalStoryProtocolError(
          f"Result is missing required phases: {sorted(missing_phases)}")
    cls._validate_phase_order(phases, required_phases)
    metadata = cls._dict(value, "metadata")
    artifacts = cls._dict(value, "artifacts")
    return cls(run_id, story, phases, metadata, artifacts)

  @staticmethod
  def _validate_phase_order(
      phases: Mapping[str, ExternalStoryPhase],
      required_phases: tuple[str, ...],
  ) -> None:
    previous_name: str | None = None
    previous_start = 0.0
    for name in required_phases:
      phase = phases[name]
      if previous_name and phase.start_time_ms < previous_start:
        raise ExternalStoryProtocolError(
            f"Required phase {name!r} starts before {previous_name!r}.")
      previous_name = name
      previous_start = phase.start_time_ms

  @staticmethod
  def _validate_identity(value: Mapping[str, Any], run_id: str,
                         story: str) -> None:
    schema_version = value.get("schemaVersion")
    if (not isinstance(schema_version, int) or
        isinstance(schema_version, bool) or schema_version != SCHEMA_VERSION):
      raise ExternalStoryProtocolError(
          f"Unsupported schemaVersion={schema_version!r}; "
          f"expected {SCHEMA_VERSION}.")
    if value.get("runId") != run_id:
      raise ExternalStoryProtocolError("Result runId does not match request.")
    if value.get("story") != story:
      raise ExternalStoryProtocolError("Result story does not match request.")

  @staticmethod
  def _validate_success(value: Mapping[str, Any]) -> None:
    status = value.get("status")
    if status not in ("success", "failure"):
      raise ExternalStoryProtocolError(f"Invalid result status: {status!r}.")
    for key in ("error", "exit", "crash"):
      item = value.get(key)
      if item is not None and not isinstance(item, dict):
        raise ExternalStoryProtocolError(f"Result {key!r} must be an object.")
    if status == "failure":
      raise ExternalStoryProtocolError(
          f"External story reported failure: {value.get('error')!r}")
    if value.get("valid") is not True:
      raise ExternalStoryProtocolError("External story result is invalid.")
    if value.get("error") is not None:
      raise ExternalStoryProtocolError(
          f"External story reported an error: {value['error']!r}")
    shutdown = value.get("shutdown")
    if not isinstance(shutdown, dict):
      raise ExternalStoryProtocolError(
          "Successful result is missing shutdown object.")
    if shutdown.get("status") != "clean":
      raise ExternalStoryProtocolError(
          f"External story did not shut down cleanly: {shutdown!r}")
    exit_code = shutdown.get("exitCode")
    if (exit_code is not None and
        (not isinstance(exit_code, int) or isinstance(exit_code, bool))):
      raise ExternalStoryProtocolError(
          f"External story reported invalid shutdown exitCode: {shutdown!r}")
    if exit_code not in (None, 0):
      raise ExternalStoryProtocolError(
          f"External story reported nonzero shutdown exitCode: {shutdown!r}")
    signal = shutdown.get("signal")
    if signal is not None and not isinstance(signal, str):
      raise ExternalStoryProtocolError(
          f"External story reported invalid shutdown signal: {shutdown!r}")
    exit_state = value.get("exit")
    if exit_state:
      exit_code = exit_state.get("code")
      if (exit_code is not None and
          (not isinstance(exit_code, int) or isinstance(exit_code, bool))):
        raise ExternalStoryProtocolError(
            f"External story reported an invalid exit: {exit_state!r}")
      if exit_code not in (None, 0):
        raise ExternalStoryProtocolError(
            f"External story reported a nonzero exit: {exit_state!r}")
    if "crash" in value and value["crash"] is not None:
      raise ExternalStoryProtocolError(
          f"External story reported a crash: {value['crash']!r}")

  @staticmethod
  def _dict(value: Mapping[str, Any], key: str) -> JsonDict:
    item = value.get(key, {})
    if not isinstance(item, dict):
      raise ExternalStoryProtocolError(f"Result {key!r} must be an object.")
    return item

  def metrics(self) -> JsonDict:
    first_start = min(phase.start_time_ms for phase in self.phases.values())
    last_end = max(phase.end_time_ms for phase in self.phases.values())
    return {
        "valid": 1,
        "durationMs": last_end - first_start,
        "phases": {
            name: {
                "durationMs": phase.duration_ms,
            } for name, phase in self.phases.items()
        },
    }
