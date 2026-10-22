"""Acceptance tests for resumable adaptive Simpson integration."""

import json
import os
import subprocess
import sys

import pytest


ENTRY = os.environ.get("QUADRATURE_ENTRY", "/app/adaptive_quadrature_resume.py")


def problem():
    return {
        "problem_id": "CELL-Q8",
        "interval": [0.0, 1.0],
        "coefficients": [0.0, 0.0, 1.0],
        "tolerance": 1e-8,
        "refinements_per_run": 40,
    }


def difficult_problem(budget):
    return {
        "problem_id": "CELL-Q8",
        "interval": [0.0, 1.0],
        "coefficients": [0.0] * 10 + [1.0],
        "tolerance": 1e-12,
        "refinements_per_run": budget,
    }


def write(path, document, raw=None):
    if raw is None:
        raw = json.dumps(document, ensure_ascii=False, separators=(",", ":")) + "\n"
    path.write_bytes(raw.encode("utf-8"))
    return path


def run(tmp_path, document, *, stem="run", raw=None):
    source = write(tmp_path / "problem.json", document, raw=raw)
    output = tmp_path / f"{stem}.json"
    checkpoint = tmp_path / f"{stem}.checkpoint.json"
    result = subprocess.run(
        [sys.executable, ENTRY, str(source), str(output), str(checkpoint)],
        capture_output=True,
        text=True,
        timeout=30,
    )
    return result, source, output, checkpoint


def read(path):
    return json.loads(path.read_text(encoding="utf-8"))


def test_success_is_quiet(tmp_path):
    """题面：「运行完成时，不向标准输出或标准错误写任何字节。」"""
    result, _, output, _ = run(tmp_path, problem())
    assert result.returncode == 0 and result.stdout == "" and result.stderr == "" and output.is_file()


def test_polynomial_integral_is_accurate(tmp_path):
    """题面：「使用复合 Simpson 规则。」"""
    result, _, output, _ = run(tmp_path, problem())
    assert result.returncode == 0 and read(output)["integral"] == pytest.approx(1.0 / 3.0, abs=1e-8)


def test_complete_document_key_order(tmp_path):
    """题面：「完成态键顺序为 `problem_id`、`status`、`integral`、`active_intervals`」。"""
    result, _, output, _ = run(tmp_path, problem())
    document = read(output)
    assert result.returncode == 0 and list(document) == ["problem_id", "status", "integral", "active_intervals"]


def test_small_budget_pauses(tmp_path):
    """题面：「一次调用最多细分 `refinements_per_run` 次。」"""
    payload = difficult_problem(1)
    result, _, output, _ = run(tmp_path, payload)
    assert result.returncode == 0 and read(output)["status"] == "paused"


def test_paused_document_key_order(tmp_path):
    """题面：「暂停态键顺序为 `problem_id`、`status`、`partial_integral`、`pending_intervals`、`refinements_done`。」"""
    payload = difficult_problem(1)
    result, _, output, _ = run(tmp_path, payload)
    document = read(output)
    assert result.returncode == 0
    assert list(document) == ["problem_id", "status", "partial_integral", "pending_intervals", "refinements_done"]


def test_chunked_resume_matches_one_shot(tmp_path):
    """题面：「一次完成与分段完成最终得到的积分值、有效区间集合顺序和检查点必须逐字节一致。」"""
    full_result, _, full_output, full_checkpoint = run(tmp_path, difficult_problem(500), stem="full")
    chunked = difficult_problem(5)
    source = write(tmp_path / "problem.json", chunked)
    output = tmp_path / "chunked.json"
    checkpoint = tmp_path / "chunked.checkpoint.json"
    result = None
    for _ in range(200):
        result = subprocess.run(
            [sys.executable, ENTRY, str(source), str(output), str(checkpoint)],
            capture_output=True,
            text=True,
            timeout=30,
        )
    assert read(output)["status"] == "complete"
    assert full_result.returncode == 0 and result.returncode == 0
    assert output.read_bytes() == full_output.read_bytes()
    assert checkpoint.read_bytes() == full_checkpoint.read_bytes()


def test_completed_rerun_keeps_files(tmp_path):
    """题面：「同一输入完成后重跑，积分凭证与检查点保持原字节。」"""
    result, source, output, checkpoint = run(tmp_path, problem())
    before_output = output.read_bytes()
    before_checkpoint = checkpoint.read_bytes()
    second = subprocess.run(
        [sys.executable, ENTRY, str(source), str(output), str(checkpoint)],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0 and second.returncode == 0
    assert output.read_bytes() == before_output and checkpoint.read_bytes() == before_checkpoint


def test_checkpoint_digest_binds_input(tmp_path):
    """题面：「`input_sha256` 标识数学问题本身」。"""
    import hashlib

    result, source, _, checkpoint = run(tmp_path, problem())
    state = read(checkpoint)
    payload = problem()
    payload.pop("refinements_per_run")
    expected = hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
    ).hexdigest()
    assert result.returncode == 0 and state["input_sha256"] == expected


def test_corrupt_checkpoint_is_refused(tmp_path):
    """题面：「若续算状态损坏，返回码为 1，标准错误只含 `运行拒收: BAD_CHECKPOINT`。」"""
    result, source, output, checkpoint = run(tmp_path, problem())
    before_output = output.read_bytes()
    state = read(checkpoint)
    state["integral"] += 1.0
    checkpoint.write_text(json.dumps(state, separators=(",", ":")) + "\n", encoding="utf-8")
    before_checkpoint = checkpoint.read_bytes()
    rejected = subprocess.run(
        [sys.executable, ENTRY, str(source), str(output), str(checkpoint)],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0 and rejected.returncode == 1 and rejected.stderr == "运行拒收: BAD_CHECKPOINT\n"
    assert output.read_bytes() == before_output and checkpoint.read_bytes() == before_checkpoint


def test_top_keys_are_exact(tmp_path):
    """题面：「程序读取 UTF-8 文本，内容必须是一个 JSON 对象。顶层键如下：」。"""
    payload = problem()
    payload["method"] = "simpson"
    result, _, output, _ = run(tmp_path, payload)
    assert result.returncode == 1 and result.stderr == "运行拒收: BAD_INPUT\n" and not output.exists()


def test_duplicate_key_is_refused(tmp_path):
    """题面：「若积分记录字段不全、类型错或参数越界，程序以 1 退出，标准错误写 `运行拒收: BAD_INPUT`。」"""
    raw = (
        '{"problem_id":"A1","interval":[0,1],"coefficients":[1],'
        '"tolerance":0.001,"refinements_per_run":1,"refinements_per_run":2}'
    )
    result, _, output, _ = run(tmp_path, None, raw=raw)
    assert result.returncode == 1 and result.stderr == "运行拒收: BAD_INPUT\n" and not output.exists()


def test_boolean_budget_is_refused(tmp_path):
    """题面：「布尔值不算数字或整数。」"""
    payload = problem()
    payload["refinements_per_run"] = True
    result, _, output, _ = run(tmp_path, payload)
    assert result.returncode == 1 and result.stderr == "运行拒收: BAD_INPUT\n" and not output.exists()


def test_reversed_interval_is_refused(tmp_path):
    """题面：「积分区间由 `interval` 给出，是两个有限端点且左端必须小于右端。」"""
    payload = problem()
    payload["interval"] = [1.0, 0.0]
    result, _, output, _ = run(tmp_path, payload)
    assert result.returncode == 1 and result.stderr == "运行拒收: BAD_INPUT\n" and not output.exists()


def test_tolerance_bounds_are_enforced(tmp_path):
    """题面：「误差上限 `tolerance` 介于 1e-12 与 1e-2 之间。」"""
    payload = problem()
    payload["tolerance"] = 0.1
    result, _, output, _ = run(tmp_path, payload)
    assert result.returncode == 1 and result.stderr == "运行拒收: BAD_INPUT\n" and not output.exists()


def test_tolerance_minimum_is_accepted(tmp_path):
    """题面：「误差上限 `tolerance` 介于 1e-12 与 1e-2 之间。」"""
    payload = problem()
    payload["tolerance"] = 1e-12
    payload["refinements_per_run"] = 500
    result, _, output, _ = run(tmp_path, payload)
    assert result.returncode == 0 and read(output)["status"] == "complete"


def test_problem_identifier_bounds_are_inclusive(tmp_path):
    """题面：「`problem_id` 记录本次积分批次，可见字符数限定在 2 至 48；前后空格和控制字符均不接受。」"""
    payload = problem()
    # case_id: PROBLEM-ID-ACCEPT
    payload["problem_id"] = "A1"
    shortest, _, shortest_output, _ = run(tmp_path, payload, stem="shortest")
    payload["problem_id"] = "A" * 48
    longest, _, longest_output, _ = run(tmp_path, payload, stem="longest")
    # case_id: PROBLEM-ID-REJECT
    payload["problem_id"] = "A"
    too_short, _, missing_short_output, _ = run(tmp_path, payload, stem="too-short")
    payload["problem_id"] = "A" * 49
    too_long, _, missing_long_output, _ = run(tmp_path, payload, stem="too-long")
    assert shortest.returncode == 0 and shortest_output.is_file()
    assert longest.returncode == 0 and longest_output.is_file()
    assert too_short.returncode == 1 and too_short.stderr == "运行拒收: BAD_INPUT\n"
    assert too_long.returncode == 1 and too_long.stderr == "运行拒收: BAD_INPUT\n"
    assert not missing_short_output.exists() and not missing_long_output.exists()


def test_absent_parent_is_io_failure(tmp_path):
    """题面：「如果结果文件或检查点无法创建、替换，退出码为 1，并在标准错误单独写出 `运行拒收: IO_FAILURE`。」"""
    source = write(tmp_path / "problem.json", problem())
    output = tmp_path / "absent" / "result.json"
    checkpoint = tmp_path / "state.json"
    result = subprocess.run(
        [sys.executable, ENTRY, str(source), str(output), str(checkpoint)],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 1 and result.stderr == "运行拒收: IO_FAILURE\n" and not checkpoint.exists()
