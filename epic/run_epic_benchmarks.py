"""
Automated EPIC benchmark runner.

Loops through .bench files under benchmarks/<family>/, locks each one with
a matrix of key sizes and seeds, writes the locked circuits to
locked_outputs/<family>/, simulates correct-key and wrong-key behavior,
and appends a row of metrics to results/epic_results.csv.

Usage:
    python run_epic_benchmarks.py
    python run_epic_benchmarks.py --families iscas85 --key-bits 4 8 16 --seeds 1 2 3
    python run_epic_benchmarks.py --random-vectors 1000
"""

import argparse
import csv
import itertools
import random
import time
from pathlib import Path

import epic

HERE = Path(__file__).resolve().parent
BENCH_DIR = HERE / "benchmarks"
LOCKED_DIR = HERE / "locked_outputs"
RESULTS_DIR = HERE / "results"
RESULTS_CSV = RESULTS_DIR / "epic_results.csv"

FIELDNAMES = [
    "benchmark", "family", "num_inputs", "num_outputs",
    "original_gates", "key_bits", "seed", "correct_key",
    "locked_gates", "overhead", "correct_key_pass",
    "wrong_key_mismatches", "wrong_key_total", "wrong_key_corruption_rate",
    "runtime_sec", "status", "error",
]

DEFAULT_KEY_BITS = {
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
        # structural-only support: treat as a buffer of its first input
        return values[0] if values else 0
    raise ValueError(f"Unsupported gate type: {gate_type}")


def simulate(parsed, input_values):
    env = {"0": 0, "1": 1, "1'b0": 0, "1'b1": 1}
    env.update(input_values)
    for wire in epic.topological_order(parsed["gates"], parsed["inputs"]):
        gate = parsed["gates"][wire]
        args = [env[name] for name in gate["inputs"]]
        env[wire] = eval_gate(gate["type"], args)
    return {out: env[out] for out in parsed["outputs"] if out in env}


def is_sequential(parsed):
    return any(g["type"].upper() == "DFF" for g in parsed["gates"].values())


def gen_input_vectors(inputs, max_random=1000, seed=0):
    n = len(inputs)
    if n == 0:
        return []
    if 2 ** n <= max_random:
        return list(itertools.product([0, 1], repeat=n))
    rng = random.Random(seed)
    return [tuple(rng.randint(0, 1) for _ in range(n)) for _ in range(max_random)]


def run_one(bench_path: Path, family: str, key_bits: int, seed: int,
            locked_out_dir: Path, max_random: int):
    row = {
        "benchmark": bench_path.stem,
        "family": family,
        "key_bits": key_bits,
        "seed": seed,
        "status": "ok",
        "error": "",
    }
    t0 = time.time()
    try:
        parsed = epic.parse_bench(str(bench_path))
        row["num_inputs"] = len(parsed["inputs"])
        row["num_outputs"] = len(parsed["outputs"])
        row["original_gates"] = len(parsed["gates"])

        locked, key, locked_wires = epic.lock_circuit(
            parsed, key_bits, key=None, seed=seed
        )
        row["correct_key"] = hex(key)
        row["locked_gates"] = len(locked["gates"])
        row["overhead"] = row["locked_gates"] - row["original_gates"]

        out_path = locked_out_dir / f"{bench_path.stem}_k{key_bits}_s{seed}_locked.bench"
        epic.write_bench(locked, str(out_path))

        if is_sequential(parsed):
            # Structural-only checks for sequential circuits (Phase 2/3 of the plan).
            row["correct_key_pass"] = "n/a (sequential)"
            row["wrong_key_mismatches"] = ""
            row["wrong_key_total"] = ""
            row["wrong_key_corruption_rate"] = ""
        else:
            vectors = gen_input_vectors(parsed["inputs"], max_random=max_random, seed=seed)

            correct_pass = True
            for vec in vectors:
                orig_in = dict(zip(parsed["inputs"], vec))
                locked_in = dict(orig_in)
                for idx in range(key_bits):
                    locked_in[f"keyinput{idx}"] = (key >> idx) & 1
                if simulate(locked, locked_in) != simulate(parsed, orig_in):
                    correct_pass = False
                    break
            row["correct_key_pass"] = "Yes" if correct_pass else "No"

            wrong_key = key ^ 1
            mismatches = 0
            for vec in vectors:
                orig_in = dict(zip(parsed["inputs"], vec))
                locked_in = dict(orig_in)
                for idx in range(key_bits):
                    locked_in[f"keyinput{idx}"] = (wrong_key >> idx) & 1
                if simulate(locked, locked_in) != simulate(parsed, orig_in):
                    mismatches += 1
            row["wrong_key_mismatches"] = mismatches
            row["wrong_key_total"] = len(vectors)
            row["wrong_key_corruption_rate"] = (
                f"{mismatches / len(vectors):.2%}" if vectors else "n/a"
            )
    except Exception as e:
        row["status"] = "error"
        row["error"] = str(e)
        for k in ("num_inputs", "num_outputs", "original_gates", "correct_key",
                   "locked_gates", "overhead", "correct_key_pass",
                   "wrong_key_mismatches", "wrong_key_total",
                   "wrong_key_corruption_rate"):
            row.setdefault(k, "")

    row["runtime_sec"] = f"{time.time() - t0:.3f}"
    return row


def main():
    ap = argparse.ArgumentParser(description="Run EPIC across benchmark families.")
    ap.add_argument("--families", nargs="+", default=["iscas85", "iscas89", "itc99"])
    ap.add_argument("--key-bits", nargs="+", type=int, default=None,
                     help="Override key sizes for every family")
    ap.add_argument("--seeds", nargs="+", type=int, default=[1, 2, 3])
    ap.add_argument("--random-vectors", type=int, default=1000,
                     help="Max random input vectors for circuits too large to test exhaustively")
    args = ap.parse_args()

    RESULTS_DIR.mkdir(exist_ok=True)
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

        key_bit_options = args.key_bits or DEFAULT_KEY_BITS.get(family, [8, 16])

        for bench_path in bench_files:
            for key_bits in key_bit_options:
                for seed in args.seeds:
                    print(f"[run] {family}/{bench_path.name} key_bits={key_bits} seed={seed}")
                    row = run_one(bench_path, family, key_bits, seed,
                                  locked_out_dir, args.random_vectors)
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
