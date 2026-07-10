"""
Automated SARLock-family benchmark runner.

Loops through .bench files (reusing the shared benchmarks/ directory),
locks each one with every scheme in sarlock.py (RandomLogicLocking,
StrongLogicLocking, SARLock, SARLock_SLL) across a matrix of key sizes and
seeds, writes the locked circuits and key files, verifies correct-key
equivalence and wrong-key corruption, and appends a row of metrics to
results/sarlock_results.csv.

sarlock.py itself is not modified by this script -- it only calls its
public API.

Usage:
    python run_sarlock_benchmarks.py
    python run_sarlock_benchmarks.py --families iscas85 --schemes sarlock --key-sizes 8 16 --seeds 1 2 3
"""

import argparse
import csv
import time
from itertools import product
from pathlib import Path

import sarlock as sl

HERE = Path(__file__).resolve().parent
BENCH_DIR = HERE.parent / "benchmarks"
LOCKED_DIR = HERE / "locked_outputs"
KEYS_DIR = HERE / "keys"
RESULTS_DIR = HERE / "results"
RESULTS_CSV = RESULTS_DIR / "sarlock_results.csv"

FIELDNAMES = [
    "benchmark", "family", "scheme", "num_inputs", "num_outputs",
    "original_gates", "key_size", "seed", "correct_key_bin",
    "locked_gates", "overhead", "correct_key_pass",
    "wrong_key_mismatches", "wrong_key_sample_total",
    "wrong_key_corruption_rate_of_sample", "runtime_sec", "status", "error",
]

DEFAULT_KEY_SIZES = {
    "iscas85": [4, 8, 16],
    "iscas89": [8, 16],
    "itc99": [8, 16],
}

SCHEMES = ["random", "strong", "sarlock", "sarlock_sll"]


def is_sequential(circ: "sl.Circuit") -> bool:
    return False  # sarlock.py's Gate/GateType has no DFF support at all


def gen_input_vectors(inputs, max_random=1000, seed=0):
    n = len(inputs)
    if n == 0:
        return []
    if 2 ** n <= max_random:
        return [dict(zip(inputs, bits)) for bits in product([0, 1], repeat=n)]
    import random
    rng = random.Random(seed)
    return [{name: rng.randint(0, 1) for name in inputs} for _ in range(max_random)]


def outputs_match(orig_circuit, orig_out, locked_circuit, locked_out) -> bool:
    """Compare by position, not dict key: SARLock/SARLock_SLL rename the
    protected primary output (e.g. 'out1' -> 'out1_sarlock')."""
    orig_vals = [orig_out[name] for name in orig_circuit.primary_outputs]
    locked_vals = [locked_out[name] for name in locked_circuit.primary_outputs]
    return orig_vals == locked_vals


def build_locker(scheme: str, circ: "sl.Circuit", key_size: int):
    if scheme == "random":
        return sl.RandomLogicLocking(circ, key_size)
    if scheme == "strong":
        return sl.StrongLogicLocking(circ, key_size)
    if scheme == "sarlock":
        return sl.SARLock(circ, num_key_bits=key_size)
    if scheme == "sarlock_sll":
        half = max(1, key_size // 2)
        return sl.SARLock_SLL(circ, num_key_bits_sll=half, num_key_bits_sarlock=key_size - half)
    raise ValueError(f"Unknown scheme: {scheme}")


def write_key_file(correct_key: dict, path: Path):
    with open(path, "w") as fh:
        for name in sorted(correct_key.keys()):
            fh.write(f"{name} = {correct_key[name]}\n")


def run_one(bench_path: Path, family: str, scheme: str, key_size: int, seed: int,
            locked_out_dir: Path, keys_dir: Path, max_random: int):
    row = {
        "benchmark": bench_path.stem,
        "family": family,
        "scheme": scheme,
        "key_size": key_size,
        "seed": seed,
        "status": "ok",
        "error": "",
    }
    t0 = time.time()
    try:
        circ_orig = sl.BenchmarkParser.parse_bench(str(bench_path))
        row["num_inputs"] = len(circ_orig.primary_inputs)
        row["num_outputs"] = len(circ_orig.primary_outputs)
        row["original_gates"] = len(circ_orig.gates)

        circ_for_lock = sl.BenchmarkParser.parse_bench(str(bench_path))
        locker = build_locker(scheme, circ_for_lock, key_size)
        locked = locker.lock(seed=seed)

        correct_key = locker.correct_key
        row["correct_key_bin"] = "".join(str(correct_key[k]) for k in sorted(correct_key))
        row["locked_gates"] = len(locked.gates)
        row["overhead"] = row["locked_gates"] - row["original_gates"]

        out_path = locked_out_dir / f"{bench_path.stem}_{scheme}_n{key_size}_s{seed}_locked.bench"
        sl.BenchmarkParser.write_bench(locked, str(out_path))
        key_path = keys_dir / f"{bench_path.stem}_{scheme}_n{key_size}_s{seed}.key"
        write_key_file(correct_key, key_path)

        vectors = gen_input_vectors(circ_orig.primary_inputs, max_random=max_random, seed=seed)

        correct_pass = True
        for vec in vectors:
            orig_out = circ_orig.evaluate(vec, {})
            locked_out = locked.evaluate(vec, correct_key)
            if not outputs_match(circ_orig, orig_out, locked, locked_out):
                correct_pass = False
                break
        row["correct_key_pass"] = "Yes" if correct_pass else "No"

        wrong_key = dict(correct_key)
        first_key_name = sorted(wrong_key.keys())[0]
        wrong_key[first_key_name] ^= 1

        mismatches = 0
        for vec in vectors:
            orig_out = circ_orig.evaluate(vec, {})
            locked_out = locked.evaluate(vec, wrong_key)
            if not outputs_match(circ_orig, orig_out, locked, locked_out):
                mismatches += 1
        row["wrong_key_mismatches"] = mismatches
        row["wrong_key_sample_total"] = len(vectors)
        row["wrong_key_corruption_rate_of_sample"] = (
            f"{mismatches / len(vectors):.4%}" if vectors else "n/a"
        )
    except Exception as e:
        row["status"] = "error"
        row["error"] = str(e)
        for k in FIELDNAMES:
            row.setdefault(k, "")

    row["runtime_sec"] = f"{time.time() - t0:.3f}"
    return row


def main():
    ap = argparse.ArgumentParser(description="Run SARLock-family schemes across benchmark families.")
    ap.add_argument("--families", nargs="+", default=["iscas85", "iscas89", "itc99"])
    ap.add_argument("--schemes", nargs="+", choices=SCHEMES, default=SCHEMES)
    ap.add_argument("--key-sizes", nargs="+", type=int, default=None)
    ap.add_argument("--seeds", nargs="+", type=int, default=[1, 2, 3])
    ap.add_argument("--random-vectors", type=int, default=1000)
    args = ap.parse_args()

    RESULTS_DIR.mkdir(exist_ok=True)
    KEYS_DIR.mkdir(exist_ok=True)
    rows = []

    for family in args.families:
        family_dir = BENCH_DIR / family
        locked_out_dir = LOCKED_DIR / family
        locked_out_dir.mkdir(parents=True, exist_ok=True)
        if not family_dir.exists():
            print(f"[skip] {family_dir} does not exist")
            continue

        bench_files = sorted(family_dir.glob("*.bench"))
        if not bench_files:
            print(f"[skip] no .bench files found in {family_dir}")
            continue

        # sarlock.py has no DFF/sequential support at all, so ISCAS'89 and
        # ITC'99 circuits will error out on any gate type it doesn't
        # recognize -- those errors are captured per-row via status=error
        # rather than skipped, so they show up in the CSV for visibility.
        key_size_options = args.key_sizes or DEFAULT_KEY_SIZES.get(family, [8, 16])

        for bench_path in bench_files:
            for scheme in args.schemes:
                for key_size in key_size_options:
                    for seed in args.seeds:
                        print(f"[run] {family}/{bench_path.name} scheme={scheme} "
                              f"key_size={key_size} seed={seed}")
                        row = run_one(bench_path, family, scheme, key_size, seed,
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
