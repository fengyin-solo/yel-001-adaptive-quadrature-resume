#!/usr/bin/env python3
"""Resumable adaptive Simpson integration with deterministic priorities."""

from __future__ import annotations

import hashlib
import json
import math
import os
import sys
import tempfile
from pathlib import Path


INPUT_KEYS = {"problem_id", "interval", "coefficients", "tolerance", "refinements_per_run"}


class Refusal(Exception):
    def __init__(self, kind):
        self.kind = kind


def fail(kind):
    sys.stderr.write(f"运行拒收: {kind}\n")
    raise SystemExit(1)


def finite(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def pairs_hook(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise Refusal("BAD_INPUT")
        result[key] = value
    return result


def polynomial(coefficients, value):
    result = 0.0
    for coefficient in reversed(coefficients):
        result = result * value + coefficient
    return result


def simpson(coefficients, left, right):
    middle = (left + right) / 2.0
    values = [polynomial(coefficients, point) for point in (left, middle, right)]
    return (right - left) * (values[0] + 4.0 * values[1] + values[2]) / 6.0


def make_segment(coefficients, left, right):
    middle = (left + right) / 2.0
    quarter = (left + middle) / 2.0
    three_quarter = (middle + right) / 2.0
    values = [polynomial(coefficients, point) for point in (left, quarter, middle, three_quarter, right)]
    whole = (right - left) * (values[0] + 4.0 * values[2] + values[4]) / 6.0
    left_value = (middle - left) * (values[0] + 4.0 * values[1] + values[2]) / 6.0
    right_value = (right - middle) * (values[2] + 4.0 * values[3] + values[4]) / 6.0
    error = abs(left_value + right_value - whole) / 15.0
    return {
        "left": left,
        "right": right,
        "middle": middle,
        "whole": whole,
        "error": error,
    }


def validate(payload):
    if not isinstance(payload, dict) or set(payload) != INPUT_KEYS:
        raise Refusal("BAD_INPUT")
    problem_id = payload["problem_id"]
    if (
        not isinstance(problem_id, str)
        or not 2 <= len(problem_id) <= 48
        or problem_id != problem_id.strip()
        or any(ord(char) < 32 or ord(char) == 127 for char in problem_id)
    ):
        raise Refusal("BAD_INPUT")
    interval = payload["interval"]
    if not isinstance(interval, list) or len(interval) != 2 or any(not finite(value) for value in interval):
        raise Refusal("BAD_INPUT")
    if not interval[0] < interval[1]:
        raise Refusal("BAD_INPUT")
    coefficients = payload["coefficients"]
    if not isinstance(coefficients, list) or not 1 <= len(coefficients) <= 12:
        raise Refusal("BAD_INPUT")
    if any(not finite(value) for value in coefficients):
        raise Refusal("BAD_INPUT")
    tolerance = payload["tolerance"]
    if not finite(tolerance) or not 1e-12 <= tolerance <= 1e-2:
        raise Refusal("BAD_INPUT")
    budget = payload["refinements_per_run"]
    if isinstance(budget, bool) or not isinstance(budget, int) or not 1 <= budget <= 10000:
        raise Refusal("BAD_INPUT")
    return problem_id, [float(interval[0]), float(interval[1])], [float(value) for value in coefficients], float(tolerance), budget


def validate_checkpoint(state, digest, coefficients, interval):
    if not isinstance(state, dict) or set(state) != {"input_sha256", "segments", "integral", "refinements_done"}:
        raise Refusal("BAD_CHECKPOINT")
    if state["input_sha256"] != digest:
        raise Refusal("BAD_CHECKPOINT")
    if not finite(state["integral"]) or isinstance(state["refinements_done"], bool) or not isinstance(state["refinements_done"], int) or state["refinements_done"] < 0:
        raise Refusal("BAD_CHECKPOINT")
    segments = state["segments"]
    if not isinstance(segments, list) or not segments:
        raise Refusal("BAD_CHECKPOINT")
    clean = []
    for item in segments:
        if not isinstance(item, dict) or set(item) != {"left", "right", "middle", "whole", "error"}:
            raise Refusal("BAD_CHECKPOINT")
        if any(not finite(item[key]) for key in item):
            raise Refusal("BAD_CHECKPOINT")
        if not (interval[0] <= item["left"] < item["middle"] < item["right"] <= interval[1]):
            raise Refusal("BAD_CHECKPOINT")
        expected = make_segment(coefficients, item["left"], item["right"])
        if any(abs(item[key] - expected[key]) > 1e-12 * max(1.0, abs(expected[key])) for key in expected):
            raise Refusal("BAD_CHECKPOINT")
        clean.append(expected)
    if abs(state["integral"] - sum(item["whole"] for item in clean)) > 1e-9 * max(1.0, abs(state["integral"])):
        raise Refusal("BAD_CHECKPOINT")
    return clean, float(state["integral"]), state["refinements_done"]


def publish(output, checkpoint, output_bytes, checkpoint_bytes):
    for target in (output, checkpoint):
        if not target.parent.is_dir() or target.is_dir() or not os.access(target.parent, os.W_OK):
            raise Refusal("IO_FAILURE")
    if output == checkpoint:
        raise Refusal("IO_FAILURE")
    old = {}
    stages = []
    try:
        for target, content in ((output, output_bytes), (checkpoint, checkpoint_bytes)):
            old[target] = target.read_bytes() if target.exists() else None
            descriptor, name = tempfile.mkstemp(prefix=".quad-", dir=target.parent)
            stage = Path(name)
            stages.append(stage)
            with os.fdopen(descriptor, "wb") as handle:
                handle.write(content)
                handle.flush()
                os.fsync(handle.fileno())
        os.replace(stages[0], output)
        os.replace(stages[1], checkpoint)
        stages.clear()
    except OSError:
        for target, previous in old.items():
            if previous is None:
                target.unlink(missing_ok=True)
            else:
                target.write_bytes(previous)
        raise Refusal("IO_FAILURE")
    finally:
        for stage in stages:
            stage.unlink(missing_ok=True)


def main(argv):
    if len(argv) != 4:
        fail("BAD_INPUT")
    source, output, checkpoint = map(Path, argv[1:])
    try:
        raw = source.read_bytes()
        payload = json.loads(raw.decode("utf-8"), object_pairs_hook=pairs_hook)
        problem_id, interval, coefficients, tolerance, budget = validate(payload)
        digest_payload = {
            key: payload[key]
            for key in ("problem_id", "interval", "coefficients", "tolerance")
        }
        digest = hashlib.sha256(
            json.dumps(
                digest_payload,
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=False,
            ).encode("utf-8")
        ).hexdigest()
        if checkpoint.exists() and checkpoint.stat().st_size:
            state = json.loads(checkpoint.read_text(encoding="utf-8"), object_pairs_hook=pairs_hook)
            segments, integral, refinements = validate_checkpoint(state, digest, coefficients, interval)
        else:
            segment = make_segment(coefficients, interval[0], interval[1])
            segments, integral, refinements = [segment], segment["whole"], 0
        done_in_call = 0
        while done_in_call < budget and sum(item["error"] for item in segments) > tolerance:
            index = max(range(len(segments)), key=lambda position: (segments[position]["error"], -segments[position]["left"], -segments[position]["right"]))
            segment = segments.pop(index)
            integral -= segment["whole"]
            left = make_segment(coefficients, segment["left"], segment["middle"])
            right = make_segment(coefficients, segment["middle"], segment["right"])
            segments.extend((left, right))
            integral += left["whole"] + right["whole"]
            refinements += 1
            done_in_call += 1
        segments.sort(key=lambda item: item["left"])
        total_error = sum(item["error"] for item in segments)
        if total_error <= tolerance:
            document = {
                "problem_id": problem_id,
                "status": "complete",
                "integral": integral,
                "active_intervals": len(segments),
            }
        else:
            document = {
                "problem_id": problem_id,
                "status": "paused",
                "partial_integral": integral,
                "pending_intervals": len(segments),
                "refinements_done": refinements,
            }
        state = {
            "input_sha256": digest,
            "segments": segments,
            "integral": integral,
            "refinements_done": refinements,
        }
        publish(
            output,
            checkpoint,
            (json.dumps(document, ensure_ascii=False, separators=(",", ":")) + "\n").encode(),
            (json.dumps(state, ensure_ascii=False, separators=(",", ":")) + "\n").encode(),
        )
        return 0
    except Refusal as exc:
        fail(exc.kind)
    except (OSError, UnicodeError, ValueError):
        fail("BAD_INPUT")


if __name__ == "__main__":
    sys.exit(main(sys.argv))
