"""
Automated TTLock benchmark runner.

Loops through .bench files (reusing epic/benchmarks/), locks each one with
a matrix of key sizes and seeds using ttlock.py, writes the locked circuits
and .key files, verifies correct-key equivalence and measures wrong-key
corruption RATE (as a fraction of the key's sub-pattern space, since
TTLock's comparator only depends on the selected cone bits -- see
ttlock_testing_plan.md section 6, Test 6), and appends a row of metrics to
results/ttlock_results.csv.

ttlock.py itself is not modified -- this script only calls its public API.

Usage:
    python run_ttlock_benchmarks.py
    python run_ttlock_benchmarks.py --families iscas85 --key-sizes 4 8 16 --seeds 1 2 3
"""

import argparse
import csv
import random
import time
from itertools import product
from pathlib import Path

import ttlock

HERE = Path(__file__).resolve().parent
EPIC_BENCH_DIR = HERE.parent / "epic" / "benchmarks"
LOCKED_DIR = HERE / "locked_outputs"
KEYS_DIR = HERE / "keys"
RESULTS_DIR = HERE / "results"
RESULTS_CSV = RESULTS_DIR / "ttlock_results.csv"

FIELDNAMES = [
    "benchmark", "family", "num_inputs", "num_outputs",
    "original_gates", "key_size", "seed", "target_output",
    "cone_pis_used", "padded_pis", "secret_key_bin",
    "locked_gates", "overhead", "correct_key_pass",
    "wrong_key_mismatches", "wrong_key_sample_total",
    "wrong_key_corruption_rate_of_sample",
    "theoretical_corruption_rate", "runtime_sec", "status", "error",
]

DEFAULT_KEY_SIZES = {
    "iscas85": [4, 8, 16],
    "iscas89": [8, 16],
    "itc99": [8, 16],
}


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


def simulate(circ, input_values, default_for_missing=0):
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
        gtype, fans = circ.gate_def[w]
        vals = [eval_wire(f) for f in fans]
        cache[w] = eval_gate(gtype, vals)
        return cache[w]

    return {po: eval_wire(po) for po in circ.outputs}


def is_sequential(circ):
    return any(gt == "DFF" for gt, _ in circ.gate_def.values())


def gen_input_vectors(inputs, max_random=1000, seed=0):
    n = len(inputs)
    if n == 0:
        return []
    if 2 ** n <= max_random:
        return [dict(zip(inputs, bits)) for bits in product([0, 1], repeat=n)]
    rng = random.Random(seed)
    return [{name: rng.randint(0, 1) for name in inputs} for _ in range(max_random)]


def run_one(bench_path: Path, family: str, key_size: int, seed: int,
            locked_out_dir: Path, keys_dir: Path, max_random: int):
    row = {
        "benchmark": bench_path.stem,
        "family": family,
        "key_size": key_size,
        "seed": seed,
        "status": "ok",
        "error": "",
    }
    t0 = time.time()
    try:
        original = ttlock.BenchCircuit().parse(str(bench_path))
        row["num_inputs"] = len(original.inputs)
        row["num_outputs"] = len(original.outputs)
        row["original_gates"] = len(original.gate_def)

        to_lock = ttlock.BenchCircuit().parse(str(bench_path))
        rng = random.Random(seed)
        locker = ttlock.TTLock(to_lock, key_size=key_size, rng=rng)
        locked = locker.lock()

        row["target_output"] = locker.target_po
        row["cone_pis_used"] = len(locker.cone_pi_wires)
        row["padded_pis"] = len(locked.inputs) - key_size - len(original.inputs)
        row["secret_key_bin"] = "".join(map(str, locker.key_bits))
        row["locked_gates"] = len(locked.gate_def)
        row["overhead"] = row["locked_gates"] - row["original_gates"]

        out_path = locked_out_dir / f"{bench_path.stem}_n{key_size}_s{seed}_locked.bench"
        locked.write(str(out_path))
        key_path = keys_dir / f"{bench_path.stem}_n{key_size}_s{seed}.key"
        locker.write_key(str(key_path))

        row["theoretical_corruption_rate"] = f"{2 / (2 ** key_size):.6%}"

        if is_sequential(original):
            row["correct_key_pass"] = "n/a (sequential)"
            row["wrong_key_mismatches"] = ""
            row["wrong_key_sample_total"] = ""
            row["wrong_key_corruption_rate_of_sample"] = ""
        else:
            vectors = gen_input_vectors(original.inputs, max_random=max_random, seed=seed)

            protected_vec = dict(zip(locker.cone_pi_wires, locker.protected_pattern))
            for pi in original.inputs:
                protected_vec.setdefault(pi, 0)
            vectors.append(protected_vec)

            correct_key_values = dict(zip(locker.key_input_wires, locker.key_bits))
            correct_pass = True
            for vec in vectors:
                orig_out = simulate(original, vec)
                locked_in = dict(vec)
                locked_in.update(correct_key_values)
                if simulate(locked, locked_in) != orig_out:
                    correct_pass = False
                    break
            row["correct_key_pass"] = "Yes" if correct_pass else "No"

            wrong_key_bits = locker.key_bits[:]
            wrong_key_bits[0] ^= 1
            wrong_key_values = dict(zip(locker.key_input_wires, wrong_key_bits))

            wrong_key_vec = dict(zip(locker.cone_pi_wires, wrong_key_bits))
            for pi in original.inputs:
                wrong_key_vec.setdefault(pi, 0)
            vectors.append(wrong_key_vec)

            mismatches = 0
            for vec in vectors:
                orig_out = simulate(original, vec)
                locked_in = dict(vec)
                locked_in.update(wrong_key_values)
                if simulate(locked, locked_in) != orig_out:
                    mismatches += 1
            row["wrong_key_mismatches"] = mismatches
            row["wrong_key_sample_total"] = len(vectors)
            row["wrong_key_corruption_rate_of_sample"] = f"{mismatches / len(vectors):.4%}"
    except Exception as e:
        row["status"] = "error"
        row["error"] = str(e)
        for k in FIELDNAMES:
            row.setdefault(k, "")

    row["runtime_sec"] = f"{time.time() - t0:.3f}"
    return row


def main():
    ap = argparse.ArgumentParser(description="Run TTLock across benchmark families.")
    ap.add_argument("--families", nargs="+", default=["iscas85", "iscas89", "itc99"])
    ap.add_argument("--key-sizes", nargs="+", type=int, default=None)
    ap.add_argument("--seeds", nargs="+", type=int, default=[1, 2, 3])
    ap.add_argument("--random-vectors", type=int, default=1000)
    args = ap.parse_args()

    RESULTS_DIR.mkdir(exist_ok=True)
    KEYS_DIR.mkdir(exist_ok=True)
    rows = []

    for family in args.families:
        family_dir = EPIC_BENCH_DIR / family
        locked_out_dir = LOCKED_DIR / family
        locked_out_dir.mkdir(parents=True, exist_ok=True)
        if not family_dir.exists():
            print(f"[skip] {family_dir} does not exist")
            continue

        bench_files = sorted(family_dir.glob("*.bench"))
        if not bench_files:
            print(f"[skip] no .bench files found in {family_dir}")
            continue

        key_size_options = args.key_sizes or DEFAULT_KEY_SIZES.get(family, [8, 16])

        for bench_path in bench_files:
            for key_size in key_size_options:
                for seed in args.seeds:
                    print(f"[run] {family}/{bench_path.name} key_size={key_size} seed={seed}")
                    row = run_one(bench_path, family, key_size, seed,
                                  locked_out_dir, KEYS_DIR, args.random_vectors)
                    rows.append(row)
                    if row["status"] == "error":
                        print(f"       ERROR: {row['error']}")

    write_header = not RESULTS_CSV.exists()
    with open(RESULTS_CSV, "a", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=FIELDNAMES)
        if write_header:
            writer.writeheader()
        for row in rows:
            writer.writerow({k: row.get(k, "") for k in FIELDNAMES})

    print(f"\nWrote {len(rows)} rows to {RESULTS_CSV}")


if __name__ == "__main__":
    main()
