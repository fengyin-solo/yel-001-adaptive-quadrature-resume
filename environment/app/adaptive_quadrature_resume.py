#!/usr/bin/env python3
"""Legacy one-panel Simpson integrator."""

import json
import sys
from pathlib import Path


def evaluate(coefficients, value):
    result = 0.0
    for coefficient in reversed(coefficients):
        result = result * value + coefficient
    return result


def main(argv):
    if len(argv) != 4:
        return 1
    source, output, checkpoint = map(Path, argv[1:])
    payload = json.loads(source.read_text(encoding="utf-8"))
    left, right = payload["interval"]
    middle = (left + right) / 2.0
    values = [evaluate(payload["coefficients"], point) for point in (left, middle, right)]
    integral = (right - left) * (values[0] + 4 * values[1] + values[2]) / 6.0
    result = {
        "problem_id": payload["problem_id"],
        "status": "complete",
        "integral": integral,
        "active_intervals": 1,
    }
    state = {"integral": integral, "refinements_done": 0, "segments": []}
    output.write_text(json.dumps(result, separators=(",", ":")) + "\n", encoding="utf-8")
    checkpoint.write_text(json.dumps(state, separators=(",", ":")) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
