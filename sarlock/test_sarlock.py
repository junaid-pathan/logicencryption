"""
Pytest tests for sarlock.py.

How to run:
    cd sarlock
    python -m pytest -q test_sarlock.py

sarlock.py is treated as read-only here -- these tests only call its public
classes/functions and check behavior; they never modify sarlock.py itself.

These tests encode the property any logic-locking scheme is SUPPOSED to
have: supplying the correct key must reproduce the original circuit's
output for (nearly) all inputs. Where a test fails against sarlock.py's
actual behavior, that is a real finding about the implementation, not a
mistaken test assumption -- see NOTES.md-style comments inline for the
specific mechanism.
"""

import random
from itertools import product
from pathlib import Path

import pytest

import sarlock as sl

HERE = Path(__file__).resolve().parent
TINY_DIR = HERE / "tests" / "tiny"
BENCH_DIR = HERE.parent / "benchmarks"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def gen_input_vectors(inputs, max_random=1000, seed=0):
    n = len(inputs)
    if n == 0:
        return []
    if 2 ** n <= max_random:
        return [dict(zip(inputs, bits)) for bits in product([0, 1], repeat=n)]
    rng = random.Random(seed)
    return [{name: rng.randint(0, 1) for name in inputs} for _ in range(max_random)]


def parse_tiny(name: str) -> "sl.Circuit":
    return sl.BenchmarkParser.parse_bench(str(TINY_DIR / name))


def outputs_match(orig_circuit, orig_out: dict, locked_circuit, locked_out: dict) -> bool:
    """Compare outputs by position, not by dict key -- SARLock/SARLock_SLL
    rename the protected primary output (e.g. 'out1' -> 'out1_sarlock')."""
    orig_vals = [orig_out[name] for name in orig_circuit.primary_outputs]
    locked_vals = [locked_out[name] for name in locked_circuit.primary_outputs]
    return orig_vals == locked_vals


# ---------------------------------------------------------------------------
# Test 1: Parse test
# ---------------------------------------------------------------------------

def test_parse_bench_reads_inputs_outputs_and_gates():
    circ = parse_tiny("tiny2.bench")

    assert len(circ.primary_inputs) > 0
    assert len(circ.primary_outputs) > 0
    assert len(circ.gates) > 0
    n1 = circ.wire_to_gate["n1"]
    assert n1.gate_type == sl.GateType.AND
    assert n1.inputs == ["a", "b"]


# ---------------------------------------------------------------------------
# Test 2: Key input test (all four locking classes)
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("locker_cls", [sl.RandomLogicLocking, sl.StrongLogicLocking])
@pytest.mark.parametrize("key_bits", [1, 2, 4])
def test_scattered_locking_adds_expected_key_inputs(locker_cls, key_bits):
    circ = parse_tiny("tiny2.bench")
    locker = locker_cls(circ, key_bits)
    locked = locker.lock(seed=1)

    assert len(locker.correct_key) == key_bits
    for kw in locker.correct_key:
        assert kw in locked.key_inputs


def test_sarlock_adds_expected_key_inputs():
    circ = parse_tiny("tiny2.bench")
    locker = sl.SARLock(circ, num_key_bits=4)
    locked = locker.lock(seed=1)

    assert len(locker.correct_key) == 4
    for kw in locker.correct_key:
        assert kw in locked.key_inputs


# ---------------------------------------------------------------------------
# Test 3: Correct-key full-equivalence test
#
# This is the fundamental property of ANY logic-locking scheme: the correct
# key must reproduce the original circuit's output for essentially all
# inputs (SARLock/TTLock-style schemes only carve out at most a couple of
# specific "trap" patterns as an intentional design tradeoff -- see
# ttlock/ttlock_testing_plan.md -- but the correct key must NOT corrupt
# arbitrary/most inputs).
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("locker_cls", [sl.RandomLogicLocking, sl.StrongLogicLocking])
@pytest.mark.parametrize("key_bits", [1, 2, 4])
@pytest.mark.parametrize("seed", [1, 2, 3, 4, 5])
def test_scattered_locking_correct_key_matches_original(locker_cls, key_bits, seed):
    """Gate polarity (XOR vs XNOR) must match the key bit for a locked wire
    to be transparent: XOR needs key=0, XNOR needs key=1. sarlock.py links
    these now, so the recorded correct_key should reproduce the original
    function for every input.
    """
    circ_orig = parse_tiny("tiny2.bench")
    circ_for_lock = parse_tiny("tiny2.bench")
    locker = locker_cls(circ_for_lock, key_bits)
    locked = locker.lock(seed=seed)

    for vec in gen_input_vectors(circ_orig.primary_inputs):
        orig_out = circ_orig.evaluate(vec, {})
        locked_out = locked.evaluate(vec, locker.correct_key)
        assert locked_out == orig_out, (
            f"{locker_cls.__name__}: correct key should reproduce original "
            f"output for ALL inputs; mismatch at {vec} with key "
            f"{locker.correct_key}"
        )


@pytest.mark.parametrize("seed", [1, 2, 3, 4, 5])
def test_sarlock_correct_key_matches_original_for_all_inputs(seed):
    """SARLock XORs a pattern-detector (fires on the hardwired protected
    pattern) with a key-comparator (fires when input == key). Since the
    protected pattern is set equal to the correct key, both fire on the
    same inputs when the correct key is supplied, canceling for every
    input -- not just one.
    """
    circ_orig = parse_tiny("tiny2.bench")
    circ_for_lock = parse_tiny("tiny2.bench")
    locker = sl.SARLock(circ_for_lock, num_key_bits=4)
    locked = locker.lock(seed=seed)

    for vec in gen_input_vectors(circ_orig.primary_inputs):
        orig_out = circ_orig.evaluate(vec, {})
        locked_out = locked.evaluate(vec, locker.correct_key)
        assert outputs_match(circ_orig, orig_out, locked, locked_out), (
            f"SARLock: correct key should reproduce original output for "
            f"ALL inputs; mismatch at {vec} with key {locker.correct_key}"
        )


@pytest.mark.parametrize("seed", [1, 2, 3])
def test_sarlock_wrong_key_corruption_matches_analytic_prediction(seed):
    """A wrong key should only mismatch on inputs whose compared bits equal
    the protected pattern or the wrong key -- not on inputs in general."""
    circ_orig = parse_tiny("tiny2.bench")
    circ_for_lock = parse_tiny("tiny2.bench")
    locker = sl.SARLock(circ_for_lock, num_key_bits=4)
    locked = locker.lock(seed=seed)

    compare_keys = sorted(locker.correct_key.keys())[:len(locker.protected_pattern)]
    wrong_key = dict(locker.correct_key)
    wrong_key[compare_keys[0]] ^= 1
    wrong_key_bits = [wrong_key[k] for k in compare_keys]

    for vec in gen_input_vectors(circ_orig.primary_inputs):
        orig_out = circ_orig.evaluate(vec, {})
        locked_out = locked.evaluate(vec, wrong_key)
        actual_mismatch = not outputs_match(circ_orig, orig_out, locked, locked_out)

        compare_vals = [vec.get(k, 0) for k in circ_orig.primary_inputs[:len(locker.protected_pattern)]]
        hits_pattern = compare_vals == locker.protected_pattern
        hits_key = compare_vals == wrong_key_bits
        predicted_mismatch = hits_pattern != hits_key

        assert actual_mismatch == predicted_mismatch


# ---------------------------------------------------------------------------
# Test 4: Wrong-key corruption test (sanity check; these two classes are
# expected to behave similarly to EPIC -- broad corruption on a wrong key)
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("locker_cls", [sl.RandomLogicLocking, sl.StrongLogicLocking])
def test_scattered_locking_wrong_key_can_corrupt_output(locker_cls):
    circ_orig = parse_tiny("tiny2.bench")
    circ_for_lock = parse_tiny("tiny2.bench")
    locker = locker_cls(circ_for_lock, num_key_bits=4)
    locked = locker.lock(seed=1)

    wrong_key = {k: v ^ 1 for k, v in locker.correct_key.items()}

    found_difference = False
    for vec in gen_input_vectors(circ_orig.primary_inputs):
        orig_out = circ_orig.evaluate(vec, {})
        locked_out = locked.evaluate(vec, wrong_key)
        if locked_out != orig_out:
            found_difference = True
            break

    assert found_difference, "flipping the whole key should corrupt at least one input"


# ---------------------------------------------------------------------------
# Test 5: Gate overhead sanity test
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("locker_cls", [sl.RandomLogicLocking, sl.StrongLogicLocking])
@pytest.mark.parametrize("key_bits", [2, 4])
def test_scattered_locking_gate_overhead_equals_key_bits(locker_cls, key_bits):
    # tiny2.bench has only 6 internal (non-output) wires, so key_bits must
    # stay <= 6 or sarlock.py correctly raises ValueError.
    circ = parse_tiny("tiny2.bench")
    locker = locker_cls(circ, key_bits)
    locked = locker.lock(seed=1)

    assert len(locked.gates) == len(circ.gates) + key_bits


def test_sarlock_gate_overhead_is_positive_and_scales_with_key_bits():
    circ = parse_tiny("tiny2.bench")
    small = sl.SARLock(circ, num_key_bits=2).lock(seed=1)
    circ2 = parse_tiny("tiny2.bench")
    big = sl.SARLock(circ2, num_key_bits=8).lock(seed=1)

    small_overhead = len(small.gates) - len(circ.gates)
    big_overhead = len(big.gates) - len(circ2.gates)
    assert small_overhead > 0
    assert big_overhead > small_overhead


# ---------------------------------------------------------------------------
# Test 6: SARLock_SLL composed-layer test
# ---------------------------------------------------------------------------

def test_sarlock_sll_combines_key_bits_from_both_layers():
    circ = parse_tiny("tiny2.bench")
    locker = sl.SARLock_SLL(circ, num_key_bits_sll=2, num_key_bits_sarlock=2)
    locked = locker.lock(seed=1)

    assert len(locker.correct_key) == 4
    assert set(locker.correct_key.keys()) == {
        "keyinput0", "keyinput1", "keyinput2", "keyinput3"
    }
    for kw in locker.correct_key:
        assert kw in locked.key_inputs


# ---------------------------------------------------------------------------
# Test 7: write_bench round-trip test
# ---------------------------------------------------------------------------

def test_write_bench_round_trips_correctly(tmp_path):
    # Note: .bench format has no separate "key input" declaration -- both
    # write_bench() here and epic.py/ttlock.py's writers emit key inputs as
    # plain INPUT(...) lines, so a reparsed file cannot distinguish primary
    # inputs from key inputs. This is a format limitation shared by all
    # three tools, not a sarlock-specific bug, so we only check the total
    # input count and gate count round-trip correctly.
    circ = parse_tiny("tiny2.bench")
    locker = sl.SARLock(circ, num_key_bits=4)
    locked = locker.lock(seed=1)

    out_path = tmp_path / "locked.bench"
    sl.BenchmarkParser.write_bench(locked, str(out_path))

    reparsed = sl.BenchmarkParser.parse_bench(str(out_path))
    assert len(reparsed.primary_inputs) == len(locked.primary_inputs) + len(locked.key_inputs)
    assert set(locked.primary_inputs) <= set(reparsed.primary_inputs)
    assert set(locked.key_inputs) <= set(reparsed.primary_inputs)
    assert set(reparsed.primary_outputs) == set(locked.primary_outputs)
    assert len(reparsed.gates) == len(locked.gates)


# ---------------------------------------------------------------------------
# Benchmark-level tests, reusing the shared benchmarks/ directory. These
# skip automatically if the benchmark file is not present.
# ---------------------------------------------------------------------------

def _bench_or_skip(family: str, name: str) -> Path:
    path = BENCH_DIR / family / name
    if not path.exists():
        pytest.skip(f"{path} not present; download benchmark to enable this test")
    return path


@pytest.mark.parametrize("bench_file,key_bits", [
    ("c17.bench", 4),
    ("c432.bench", 8),
])
def test_iscas85_scattered_locking_parses_and_locks(bench_file, key_bits):
    """Structural + correct-key check for RandomLogicLocking on real
    benchmarks. Uses random sampling since these circuits are too large
    to test exhaustively."""
    path = _bench_or_skip("iscas85", bench_file)
    circ_orig = sl.BenchmarkParser.parse_bench(str(path))
    circ_for_lock = sl.BenchmarkParser.parse_bench(str(path))

    locker = sl.RandomLogicLocking(circ_for_lock, key_bits)
    locked = locker.lock(seed=1)

    assert len(locked.gates) == len(circ_orig.gates) + key_bits

    vectors = gen_input_vectors(circ_orig.primary_inputs, max_random=200, seed=1)
    for vec in vectors:
        orig_out = circ_orig.evaluate(vec, {})
        locked_out = locked.evaluate(vec, locker.correct_key)
        assert locked_out == orig_out


@pytest.mark.parametrize("bench_file", ["c17.bench", "c432.bench"])
def test_iscas85_sarlock_parses_and_locks(bench_file):
    path = _bench_or_skip("iscas85", bench_file)
    circ_orig = sl.BenchmarkParser.parse_bench(str(path))
    circ_for_lock = sl.BenchmarkParser.parse_bench(str(path))

    locker = sl.SARLock(circ_for_lock, num_key_bits=8)
    locked = locker.lock(seed=1)

    assert len(locker.correct_key) == 8
    for kw in locker.correct_key:
        assert kw in locked.key_inputs

    vectors = gen_input_vectors(circ_orig.primary_inputs, max_random=200, seed=1)
    for vec in vectors:
        orig_out = circ_orig.evaluate(vec, {})
        locked_out = locked.evaluate(vec, locker.correct_key)
        assert outputs_match(circ_orig, orig_out, locked, locked_out)
