"""
Pytest tests for ttlock.py.

How to run:
    cd ttlock
    python -m pytest -q test_ttlock.py

ttlock.py is treated as read-only / a black box here: these tests only
call its public classes/functions and check behavior, they never modify
ttlock.py itself.
"""

import random
from itertools import product
from pathlib import Path

import pytest

import ttlock

HERE = Path(__file__).resolve().parent
TINY_DIR = HERE / "tests" / "tiny"
BENCH_DIR = HERE.parent / "benchmarks"


# ---------------------------------------------------------------------------
# Simulator (independent of ttlock.py's own logic, used to check its output)
# ---------------------------------------------------------------------------

def eval_gate(gate_type, values):
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
        r = 0
        for v in values:
            r ^= v
        return r
    if gate_type == "XNOR":
        r = 0
        for v in values:
            r ^= v
        return int(not r)
    if gate_type in ("NOT", "INV"):
        return int(not values[0])
    if gate_type in ("BUF", "BUFF"):
        return values[0]
    if gate_type == "DFF":
        return values[0] if values else 0
    raise ValueError(f"Unsupported gate type: {gate_type}")


def simulate(circ: "ttlock.BenchCircuit", input_values: dict, default_for_missing=0):
    """Evaluate every primary output of circ given a dict of input assignments.

    Any circuit input not present in input_values (e.g. a synthetic
    ttlock_pi_N padding input) is defaulted to `default_for_missing`.
    """
    env = dict(input_values)
    for pi in circ.inputs:
        env.setdefault(pi, default_for_missing)

    cache = {}

    def eval_wire(w):
        if w in cache:
            return cache[w]
        if w in env:
            cache[w] = env[w]
            return env[w]
        if w in circ.gate_def:
            gtype, fans = circ.gate_def[w]
            vals = [eval_wire(f) for f in fans]
            cache[w] = eval_gate(gtype, vals)
            return cache[w]
        raise KeyError(f"Undefined wire: {w}")

    return {po: eval_wire(po) for po in circ.outputs}


def is_sequential(circ: "ttlock.BenchCircuit"):
    return any(gt == "DFF" for gt, _ in circ.gate_def.values())


def cone_subpattern_matches(vec: dict, cone_wires: list, pattern: list) -> bool:
    """True iff vec's values on cone_wires exactly equal pattern.

    Missing cone wires (e.g. synthetic padding PIs not in vec) default to 0,
    matching simulate()'s default_for_missing behavior.
    """
    return all(vec.get(w, 0) == p for w, p in zip(cone_wires, pattern))


def predict_mismatch(vec: dict, cone_wires: list, protected_pattern: list,
                      key_bits: list) -> bool:
    """Analytic prediction of whether `vec` mismatches under this key.

    TTLock XORs a pattern-detector (fires iff cone bits == protected_pattern)
    with a comparator (fires iff cone bits == key_bits). Output is corrupted
    exactly when these two disagree -- i.e. exactly when the cone bits equal
    ONE of {protected_pattern, key_bits} but not both. This depends only on
    the selected cone bits, not on the rest of the circuit's inputs, so for
    circuits with more inputs than the key size, many full input vectors can
    share the same cone sub-pattern and all mismatch together.
    """
    hits_pattern = cone_subpattern_matches(vec, cone_wires, protected_pattern)
    hits_key = cone_subpattern_matches(vec, cone_wires, key_bits)
    return hits_pattern != hits_key


def gen_input_vectors(inputs, max_random=1000, seed=0):
    n = len(inputs)
    if n == 0:
        return []
    if 2 ** n <= max_random:
        return [dict(zip(inputs, bits)) for bits in product([0, 1], repeat=n)]
    rng = random.Random(seed)
    return [
        {name: rng.randint(0, 1) for name in inputs}
        for _ in range(max_random)
    ]


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

TINY1_BENCH = (TINY_DIR / "tiny1.bench").read_text()
TINY2_BENCH = (TINY_DIR / "tiny2.bench").read_text()


def make_bench_file(tmp_path: Path, text: str, name: str = "circuit.bench") -> Path:
    p = tmp_path / name
    p.write_text(text)
    return p


def lock(bench_path: Path, key_size: int, seed: int):
    """Parse + lock, returning (original_circ, locked_circ, locker)."""
    original = ttlock.BenchCircuit().parse(str(bench_path))
    # parse a second, independent copy since TTLock mutates circ in place
    to_lock = ttlock.BenchCircuit().parse(str(bench_path))
    rng = random.Random(seed)
    locker = ttlock.TTLock(to_lock, key_size=key_size, rng=rng)
    locked = locker.lock()
    return original, locked, locker


# ---------------------------------------------------------------------------
# Test 1: Parse test
# ---------------------------------------------------------------------------

def test_parse_reads_inputs_outputs_and_gates(tmp_path):
    bench_path = make_bench_file(tmp_path, TINY2_BENCH)
    circ = ttlock.BenchCircuit().parse(str(bench_path))

    assert len(circ.inputs) > 0
    assert len(circ.outputs) > 0
    assert len(circ.gate_def) > 0
    assert circ.gate_def["n1"] == ("AND", ["a", "b"])


# ---------------------------------------------------------------------------
# Test 2: Target output selection test
# ---------------------------------------------------------------------------

def test_pick_target_output_prefers_nontrivial_cone(tmp_path):
    bench_text = """
INPUT(a)
INPUT(b)
INPUT(c)
INPUT(d)
OUTPUT(trivial_out)
OUTPUT(deep_out)

trivial_out = BUF(a)

n1 = AND(a, b)
n2 = OR(c, d)
n3 = NAND(n1, n2)
n4 = XOR(a, d)
deep_out = AND(n3, n4)
""".strip()
    bench_path = make_bench_file(tmp_path, bench_text)
    circ = ttlock.BenchCircuit().parse(str(bench_path))

    rng = random.Random(1)
    chosen = ttlock.pick_target_output(circ, rng)
    assert chosen == "deep_out"


# ---------------------------------------------------------------------------
# Test 3: Key input test
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("key_size", [2, 4, 8])
def test_lock_adds_expected_key_inputs(tmp_path, key_size):
    bench_path = make_bench_file(tmp_path, TINY2_BENCH)
    _, locked, locker = lock(bench_path, key_size=key_size, seed=1)

    assert len(locker.key_input_wires) == key_size
    for kw in locker.key_input_wires:
        assert kw in locked.inputs
    assert locker.key_input_wires == [f"keyinput{i}" for i in range(key_size)]


# ---------------------------------------------------------------------------
# Test 4: Key file test
# ---------------------------------------------------------------------------

def test_write_key_matches_locker_key_bits(tmp_path):
    bench_path = make_bench_file(tmp_path, TINY2_BENCH)
    _, locked, locker = lock(bench_path, key_size=4, seed=1)

    key_path = tmp_path / "out.key"
    locker.write_key(str(key_path))

    lines = [
        l for l in key_path.read_text().splitlines()
        if l.strip() and not l.startswith("#")
    ]
    assert len(lines) == 4

    parsed_key = {}
    for line in lines:
        name, val = [x.strip() for x in line.split("=")]
        parsed_key[name] = int(val)

    for kw, kv in zip(locker.key_input_wires, locker.key_bits):
        assert parsed_key[kw] == kv


# ---------------------------------------------------------------------------
# Test 5: Correct-key full equivalence test (the important one)
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("key_size", [1, 2, 4])
@pytest.mark.parametrize("seed", [1, 2, 3, 4, 5])
def test_correct_key_matches_original_for_every_input(tmp_path, key_size, seed):
    bench_path = make_bench_file(tmp_path, TINY2_BENCH)
    original, locked, locker = lock(bench_path, key_size=key_size, seed=seed)

    correct_key_values = dict(zip(locker.key_input_wires, locker.key_bits))

    for vec in gen_input_vectors(original.inputs):
        orig_out = simulate(original, vec)
        locked_in = dict(vec)
        locked_in.update(correct_key_values)
        locked_out = simulate(locked, locked_in)
        assert locked_out == orig_out, (
            f"Correct key should reproduce original output for ALL inputs, "
            f"mismatch at {vec}"
        )


# ---------------------------------------------------------------------------
# Test 6: Wrong-key corruption test
#
# This is the property that actually distinguishes TTLock from EPIC: a wrong
# key must corrupt output ONLY on inputs whose selected cone bits equal the
# protected pattern or the (wrong) key -- verified here against the exact
# analytic prediction, not just an absolute mismatch-count bound (an
# absolute "<=2" bound only holds when the cone covers every circuit input;
# for circuits with more inputs than the key size, many full vectors can
# share the same triggering cone sub-pattern, so the count scales with
# circuit size while the *rate* over the cone's sub-pattern space stays
# exactly 2 / 2^key_size).
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("key_size", [2, 4])
@pytest.mark.parametrize("seed", [1, 2, 3])
def test_wrong_key_mismatch_matches_analytic_prediction(tmp_path, key_size, seed):
    bench_path = make_bench_file(tmp_path, TINY2_BENCH)
    original, locked, locker = lock(bench_path, key_size=key_size, seed=seed)

    correct_key_bits = locker.key_bits
    wrong_key_bits = correct_key_bits[:]
    wrong_key_bits[0] ^= 1  # flip exactly one key bit
    wrong_key_values = dict(zip(locker.key_input_wires, wrong_key_bits))

    saw_a_mismatch = False
    for vec in gen_input_vectors(original.inputs):
        orig_out = simulate(original, vec)
        locked_in = dict(vec)
        locked_in.update(wrong_key_values)
        locked_out = simulate(locked, locked_in)
        actual_mismatch = locked_out != orig_out
        predicted_mismatch = predict_mismatch(
            vec, locker.cone_pi_wires, locker.protected_pattern, wrong_key_bits
        )
        assert actual_mismatch == predicted_mismatch, (
            f"mismatch prediction disagreed with actual simulation at {vec}"
        )
        saw_a_mismatch = saw_a_mismatch or actual_mismatch

    # Since tiny2's 4 inputs are small enough that the cone plus padding
    # covers (or nearly covers) the whole input space here, we should
    # actually observe the trap firing at least once in the exhaustive sweep.
    assert saw_a_mismatch, "expected the wrong key to corrupt at least one input pattern"


# ---------------------------------------------------------------------------
# Test 7: The protected pattern P* is actually corrupted with a wrong key
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("key_size", [2, 4])
def test_protected_pattern_is_corrupted_with_wrong_key(tmp_path, key_size):
    bench_path = make_bench_file(tmp_path, TINY2_BENCH)
    original, locked, locker = lock(bench_path, key_size=key_size, seed=1)

    protected_vec = dict(zip(locker.cone_pi_wires, locker.protected_pattern))
    # fill any remaining original inputs not part of the protected cone with 0
    for pi in original.inputs:
        protected_vec.setdefault(pi, 0)

    wrong_key_bits = [b ^ 1 for b in locker.key_bits]
    wrong_key_values = dict(zip(locker.key_input_wires, wrong_key_bits))

    locked_in = dict(protected_vec)
    locked_in.update(wrong_key_values)

    orig_out = simulate(original, protected_vec)
    locked_out = simulate(locked, locked_in)

    assert locked_out != orig_out, (
        "Feeding the exact protected pattern with a wrong key should "
        "corrupt the output (the trap should fire)"
    )


# ---------------------------------------------------------------------------
# Test 8: Gate overhead sanity test
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("key_size", [2, 4, 8])
def test_locked_circuit_has_more_gates_than_original(tmp_path, key_size):
    bench_path = make_bench_file(tmp_path, TINY2_BENCH)
    original, locked, locker = lock(bench_path, key_size=key_size, seed=1)

    overhead = len(locked.gate_def) - len(original.gate_def)
    assert overhead > 0
    # sanity bound: overhead should scale with key size, not be constant
    assert overhead >= key_size


# ---------------------------------------------------------------------------
# Test 9: Validate / write-file test
# ---------------------------------------------------------------------------

def test_validate_bench_passes_before_and_after_locking(tmp_path):
    bench_path = make_bench_file(tmp_path, TINY2_BENCH)
    original, locked, locker = lock(bench_path, key_size=4, seed=1)

    assert ttlock.validate_bench(original) is True
    assert ttlock.validate_bench(locked) is True


def test_write_creates_locked_file_with_expected_markers(tmp_path):
    bench_path = make_bench_file(tmp_path, TINY2_BENCH)
    _, locked, locker = lock(bench_path, key_size=4, seed=1)

    out_path = tmp_path / "locked_out.bench"
    locked.write(str(out_path))

    text = out_path.read_text()
    assert "INPUT(keyinput0)" in text
    assert "ttlock_final_xor" in text
    assert "ttlock_mlc_xor" in text


# ---------------------------------------------------------------------------
# Benchmark-level tests (ISCAS'85, reusing epic/benchmarks/)
# These skip automatically if the benchmark file is not present.
# ---------------------------------------------------------------------------

def _bench_or_skip(family: str, name: str) -> Path:
    path = BENCH_DIR / family / name
    if not path.exists():
        pytest.skip(f"{path} not present; download benchmark to enable this test")
    return path


@pytest.mark.parametrize("bench_file,key_size", [
    ("c17.bench", 4),
    ("c432.bench", 8),
    ("c499.bench", 8),
    ("c880.bench", 8),
    ("c1355.bench", 8),
])
@pytest.mark.parametrize("seed", [1, 2, 3])
def test_iscas85_benchmark_correct_key_and_bounded_wrong_key(bench_file, key_size, seed):
    path = _bench_or_skip("iscas85", bench_file)

    original = ttlock.BenchCircuit().parse(str(path))
    to_lock = ttlock.BenchCircuit().parse(str(path))
    rng = random.Random(seed)
    locker = ttlock.TTLock(to_lock, key_size=key_size, rng=rng)
    locked = locker.lock()

    vectors = gen_input_vectors(original.inputs, max_random=1000, seed=seed)

    # explicitly include the protected pattern and a wrong-key-equal pattern
    # as targeted vectors, since random sampling alone is very unlikely to
    # ever hit these exact points for larger circuits.
    protected_vec = dict(zip(locker.cone_pi_wires, locker.protected_pattern))
    for pi in original.inputs:
        protected_vec.setdefault(pi, 0)
    vectors.append(protected_vec)

    correct_key_values = dict(zip(locker.key_input_wires, locker.key_bits))
    for vec in vectors:
        orig_out = simulate(original, vec)
        locked_in = dict(vec)
        locked_in.update(correct_key_values)
        assert simulate(locked, locked_in) == orig_out

    wrong_key_bits = locker.key_bits[:]
    wrong_key_bits[0] ^= 1
    wrong_key_values = dict(zip(locker.key_input_wires, wrong_key_bits))

    # explicitly add a vector matching the wrong key's cone sub-pattern, since
    # for circuits with more inputs than key_size, random sampling is very
    # unlikely to ever hit that exact sub-pattern.
    wrong_key_vec = dict(zip(locker.cone_pi_wires, wrong_key_bits))
    for pi in original.inputs:
        wrong_key_vec.setdefault(pi, 0)
    vectors.append(wrong_key_vec)

    mismatches = 0
    for vec in vectors:
        orig_out = simulate(original, vec)
        locked_in = dict(vec)
        locked_in.update(wrong_key_values)
        actual_mismatch = simulate(locked, locked_in) != orig_out
        predicted_mismatch = predict_mismatch(
            vec, locker.cone_pi_wires, locker.protected_pattern, wrong_key_bits
        )
        assert actual_mismatch == predicted_mismatch
        mismatches += int(actual_mismatch)

    # We deliberately included the two triggering sub-patterns as explicit
    # vectors, so we expect to observe exactly those (>=1, since P* == K*
    # would make it 1) and no corruption anywhere else in the sample.
    assert mismatches >= 1


@pytest.mark.parametrize("family,bench_file", [
    ("iscas89", "s27.bench"),
    ("iscas89", "s298.bench"),
    ("itc99", "b01.bench"),
    ("itc99", "b02.bench"),
])
def test_sequential_benchmark_parses_and_locks_structurally(family, bench_file):
    """Structural-only test for sequential circuits: TTLock's fanin-cone
    logic doesn't model DFF state, so full functional-equivalence claims
    aren't made here -- only that it parses and locks without error and
    the expected key inputs get added."""
    path = _bench_or_skip(family, bench_file)

    circ = ttlock.BenchCircuit().parse(str(path))
    assert len(circ.inputs) > 0
    assert len(circ.outputs) > 0
    assert len(circ.gate_def) > 0

    key_size = 8
    rng = random.Random(1)
    locker = ttlock.TTLock(circ, key_size=key_size, rng=rng)
    locked = locker.lock()

    assert len(locker.key_input_wires) == key_size
    for kw in locker.key_input_wires:
        assert kw in locked.inputs
    assert ttlock.validate_bench(locked) is True
