"""
Pytest tests for epic.py.

How to run:
    Put this file in the same folder as epic.py, then run:
        python -m pytest -q test_epic.py
"""

import random
from itertools import product
from pathlib import Path

import pytest

import epic

HERE = Path(__file__).resolve().parent
BENCH_DIR = HERE / "benchmarks"


SIMPLE_BENCH = """
# small combinational test circuit
INPUT(a)
INPUT(b)
INPUT(c)
OUTPUT(out)

n1 = AND(a, b)
n2 = OR(n1, c)
n3 = NAND(a, c)
n4 = XOR(n2, n3)
out = NOT(n4)
""".strip()


def eval_gate(gate_type, values):
    """Evaluate one .bench gate using 0/1 integer logic."""
    gate_type = gate_type.upper()

    if gate_type == "AND":
        return int(all(values))
    if gate_type == "OR":
        return int(any(values))
    if gate_type == "NAND":
        return int(not all(values))
    if gate_type == "NOR":
        return int(not any(values))
    if gate_type == "XOR":
        result = 0
        for v in values:
            result ^= v
        return result
    if gate_type == "XNOR":
        result = 0
        for v in values:
            result ^= v
        return int(not result)
    if gate_type in ("NOT", "INV"):
        assert len(values) == 1
        return int(not values[0])
    if gate_type in ("BUF", "BUFF"):
        assert len(values) == 1
        return values[0]

    raise ValueError(f"Unsupported gate type in test simulator: {gate_type}")


def simulate(parsed, input_values):
    """
    Simulate a parsed .bench circuit.
    input_values example: {"a": 1, "b": 0, "keyinput0": 1}
    """
    env = {"0": 0, "1": 1, "1'b0": 0, "1'b1": 1}
    env.update(input_values)

    for wire in epic.topological_order(parsed["gates"], parsed["inputs"]):
        gate = parsed["gates"][wire]
        args = [env[name] for name in gate["inputs"]]
        env[wire] = eval_gate(gate["type"], args)

    return {out: env[out] for out in parsed["outputs"]}


def make_bench_file(tmp_path: Path) -> Path:
    bench_path = tmp_path / "simple.bench"
    bench_path.write_text(SIMPLE_BENCH)
    return bench_path


def test_parse_bench_reads_inputs_outputs_and_gates(tmp_path):
    bench_path = make_bench_file(tmp_path)

    parsed = epic.parse_bench(str(bench_path))

    assert parsed["inputs"] == ["a", "b", "c"]
    assert parsed["outputs"] == ["out"]
    assert parsed["gates"]["n1"] == {"type": "AND", "inputs": ["a", "b"]}
    assert parsed["gates"]["out"] == {"type": "NOT", "inputs": ["n4"]}


def test_lock_circuit_adds_key_inputs_shadow_wires_and_key_gates(tmp_path):
    bench_path = make_bench_file(tmp_path)
    parsed = epic.parse_bench(str(bench_path))

    key = 0b10
    locked, returned_key, locked_wires = epic.lock_circuit(
        parsed,
        key_bits=2,
        key=key,
        seed=123,
    )

    assert returned_key == key
    assert "keyinput0" in locked["inputs"]
    assert "keyinput1" in locked["inputs"]
    assert len(locked_wires) == 2

    # Each selected wire should be replaced by XOR/XNOR(wire_EPIC_orig, keyinputN).
    for wire, idx, gate_type, key_bit in locked_wires:
        assert f"{wire}_EPIC_orig" in locked["gates"]
        assert locked["gates"][wire] == {
            "type": gate_type,
            "inputs": [f"{wire}_EPIC_orig", f"keyinput{idx}"],
        }

        expected_gate_type = "XOR" if key_bit == 0 else "XNOR"
        assert gate_type == expected_gate_type

    # One shadow gate is added for every locked wire.
    assert len(locked["gates"]) == len(parsed["gates"]) + 2


def test_locked_circuit_matches_original_when_correct_key_is_used(tmp_path):
    bench_path = make_bench_file(tmp_path)
    original = epic.parse_bench(str(bench_path))

    key = 0b10
    locked, _, _ = epic.lock_circuit(original, key_bits=2, key=key, seed=123)

    for a, b, c in product([0, 1], repeat=3):
        original_inputs = {"a": a, "b": b, "c": c}
        locked_inputs = {
            "a": a,
            "b": b,
            "c": c,
            "keyinput0": (key >> 0) & 1,
            "keyinput1": (key >> 1) & 1,
        }

        assert simulate(locked, locked_inputs) == simulate(original, original_inputs)


def test_wrong_key_corrupts_at_least_one_input_case(tmp_path):
    bench_path = make_bench_file(tmp_path)
    original = epic.parse_bench(str(bench_path))

    correct_key = 0b10
    wrong_key = correct_key ^ 0b01  # flip only one key bit
    locked, _, _ = epic.lock_circuit(original, key_bits=2, key=correct_key, seed=123)

    found_difference = False
    for a, b, c in product([0, 1], repeat=3):
        original_inputs = {"a": a, "b": b, "c": c}
        locked_inputs = {
            "a": a,
            "b": b,
            "c": c,
            "keyinput0": (wrong_key >> 0) & 1,
            "keyinput1": (wrong_key >> 1) & 1,
        }

        if simulate(locked, locked_inputs) != simulate(original, original_inputs):
            found_difference = True
            break

    assert found_difference, "Wrong key should corrupt output for at least one input pattern"


def test_write_bench_creates_locked_file(tmp_path):
    bench_path = make_bench_file(tmp_path)
    original = epic.parse_bench(str(bench_path))
    locked, key, locked_wires = epic.lock_circuit(original, key_bits=2, key=0b10, seed=123)

    output_path = tmp_path / "simple_locked.bench"
    epic.write_bench(locked, str(output_path))

    text = output_path.read_text()
    assert "INPUT(keyinput0)" in text
    assert "INPUT(keyinput1)" in text
    assert "_EPIC_orig" in text
    assert "XOR" in text or "XNOR" in text


# ---------------------------------------------------------------------------
# Gate overhead test (plan section 6, Test 5)
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("key_bits", [1, 2, 4])
def test_gate_overhead_equals_key_bits(tmp_path, key_bits):
    bench_path = make_bench_file(tmp_path)
    original = epic.parse_bench(str(bench_path))

    locked, _, locked_wires = epic.lock_circuit(original, key_bits=key_bits, seed=7)

    assert len(locked_wires) == key_bits
    assert len(locked["gates"]) == len(original["gates"]) + key_bits


# ---------------------------------------------------------------------------
# Multiple key sizes / multiple seeds functional equivalence (plan sections 8-9)
# ---------------------------------------------------------------------------

TINY2_BENCH = (HERE / "tests" / "tiny" / "tiny2.bench").read_text()


def make_tiny2_file(tmp_path: Path) -> Path:
    p = tmp_path / "tiny2.bench"
    p.write_text(TINY2_BENCH)
    return p


@pytest.mark.parametrize("key_bits", [1, 2, 3])
@pytest.mark.parametrize("seed", [1, 2, 3, 4, 5])
def test_correct_key_matches_across_seeds_and_key_sizes(tmp_path, key_bits, seed):
    bench_path = make_tiny2_file(tmp_path)
    original = epic.parse_bench(str(bench_path))

    locked, key, _ = epic.lock_circuit(original, key_bits=key_bits, seed=seed)

    for a, b, c, d in product([0, 1], repeat=4):
        orig_in = {"a": a, "b": b, "c": c, "d": d}
        locked_in = dict(orig_in)
        for idx in range(key_bits):
            locked_in[f"keyinput{idx}"] = (key >> idx) & 1
        assert simulate(locked, locked_in) == simulate(original, orig_in)


@pytest.mark.parametrize("key_bits", [1, 2, 3])
@pytest.mark.parametrize("seed", [1, 2, 3, 4, 5])
def test_wrong_key_corruption_rate_is_nonzero(tmp_path, key_bits, seed):
    bench_path = make_tiny2_file(tmp_path)
    original = epic.parse_bench(str(bench_path))

    correct_key, _, _ = (0, None, None)
    locked, correct_key, _ = epic.lock_circuit(original, key_bits=key_bits, seed=seed)
    wrong_key = correct_key ^ 1

    mismatches = 0
    total = 0
    for a, b, c, d in product([0, 1], repeat=4):
        orig_in = {"a": a, "b": b, "c": c, "d": d}
        locked_in = dict(orig_in)
        for idx in range(key_bits):
            locked_in[f"keyinput{idx}"] = (wrong_key >> idx) & 1
        total += 1
        if simulate(locked, locked_in) != simulate(original, orig_in):
            mismatches += 1

    assert total > 0
    assert mismatches >= 1, "expected the flipped key bit to corrupt at least one output"


# ---------------------------------------------------------------------------
# Benchmark-level tests (plan section 7): parse + structural lock only.
# These are skipped automatically if the benchmark file has not been
# downloaded into benchmarks/<family>/ yet.
# ---------------------------------------------------------------------------

def _bench_or_skip(family: str, name: str) -> Path:
    path = BENCH_DIR / family / name
    if not path.exists():
        pytest.skip(f"{path} not present; download benchmark to enable this test")
    return path


def test_c17_parses_with_expected_io_and_gate_counts():
    path = _bench_or_skip("iscas85", "c17.bench")
    parsed = epic.parse_bench(str(path))

    assert len(parsed["inputs"]) == 5
    assert len(parsed["outputs"]) == 2
    assert len(parsed["gates"]) == 6


def test_c17_locks_and_is_functionally_equivalent_with_correct_key():
    path = _bench_or_skip("iscas85", "c17.bench")
    original = epic.parse_bench(str(path))

    locked, key, locked_wires = epic.lock_circuit(original, key_bits=4, seed=1)
    assert len(locked_wires) == 4
    assert len(locked["gates"]) == len(original["gates"]) + 4

    for vec in product([0, 1], repeat=len(original["inputs"])):
        orig_in = dict(zip(original["inputs"], vec))
        locked_in = dict(orig_in)
        for idx in range(4):
            locked_in[f"keyinput{idx}"] = (key >> idx) & 1
        assert simulate(locked, locked_in) == simulate(original, orig_in)


@pytest.mark.parametrize("bench_file,key_bits", [
    ("c432.bench", 8),
    ("c499.bench", 8),
    ("c880.bench", 16),
    ("c1355.bench", 16),
])
def test_iscas85_benchmark_correct_key_random_vectors(bench_file, key_bits):
    """Random-vector correct-key equivalence test for larger ISCAS'85 circuits.

    Skips automatically until the corresponding .bench file is downloaded
    into benchmarks/iscas85/.
    """
    path = _bench_or_skip("iscas85", bench_file)
    original = epic.parse_bench(str(path))

    locked, key, _ = epic.lock_circuit(original, key_bits=key_bits, seed=1)

    rng = random.Random(1)
    for _ in range(1000):
        vec = [rng.randint(0, 1) for _ in original["inputs"]]
        orig_in = dict(zip(original["inputs"], vec))
        locked_in = dict(orig_in)
        for idx in range(key_bits):
            locked_in[f"keyinput{idx}"] = (key >> idx) & 1
        assert simulate(locked, locked_in) == simulate(original, orig_in)


@pytest.mark.parametrize("family,bench_file", [
    ("iscas89", "s27.bench"),
    ("iscas89", "s298.bench"),
    ("itc99", "b01.bench"),
    ("itc99", "b02.bench"),
])
def test_sequential_benchmark_parses_and_locks_structurally(family, bench_file):
    """Phase 2/3 of the plan: parsing + structural locking only.

    Full functional equivalence for sequential circuits needs sequential
    simulation, which is not implemented yet (see plan section 15).
    """
    path = _bench_or_skip(family, bench_file)
    original = epic.parse_bench(str(path))

    assert len(original["inputs"]) > 0
    assert len(original["outputs"]) > 0
    assert len(original["gates"]) > 0

    key_bits = 8
    locked, key, locked_wires = epic.lock_circuit(original, key_bits=key_bits, seed=1)

    assert len(locked_wires) == key_bits
    for idx in range(key_bits):
        assert f"keyinput{idx}" in locked["inputs"]
    assert len(locked["gates"]) == len(original["gates"]) + key_bits
