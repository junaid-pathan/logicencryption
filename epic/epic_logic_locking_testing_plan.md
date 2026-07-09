# EPIC Logic Locking Testing Plan

## 1. Goal of the Testing

The goal is to test whether `epic.py` correctly locks digital circuits using EPIC-style logic locking.

The main things we want to verify are:

```text
1. The script can read .bench benchmark circuits.
2. The script can insert key inputs.
3. The script can insert XOR/XNOR locking gates.
4. The locked circuit works correctly when the correct key is used.
5. The locked circuit produces wrong outputs for some cases when the wrong key is used.
6. The amount of gate overhead can be measured.
7. The script can be tested on standard benchmark circuits.
```

---

## 2. Background

### What EPIC Does

EPIC locks a circuit by selecting internal wires and adding key-controlled XOR/XNOR gates.

Original circuit example:

```bench
n1 = AND(a, b)
```

Locked circuit example:

```bench
n1_EPIC_orig = AND(a, b)
n1 = XOR(n1_EPIC_orig, keyinput0)
```

or:

```bench
n1_EPIC_orig = AND(a, b)
n1 = XNOR(n1_EPIC_orig, keyinput0)
```

The correct key makes the locked circuit behave like the original circuit.

A wrong key may flip internal signals and cause incorrect outputs.

---

## 3. Benchmark Families to Use

The benchmark families mentioned are:

```text
ISCAS'85
ISCAS'89
ITC'99
```

Use them in this order.

### Phase 1: ISCAS'85

Start here.

ISCAS'85 circuits are mostly combinational circuits, so they are best suited for the current `epic.py`.

Recommended circuits:

```text
c17
c432
c499
c880
c1355
c1908
c2670
c3540
c5315
c6288
c7552
```

### Phase 2: ISCAS'89

ISCAS'89 circuits are often sequential circuits, meaning they can contain flip-flops and memory.

For now, test:

```text
1. Can epic.py parse them?
2. Can epic.py structurally lock them?
3. Does it add key inputs and XOR/XNOR gates?
```

Full functional testing may require sequential simulation.

### Phase 3: ITC'99

ITC'99 circuits are also more advanced and often sequential.

Use the same approach as ISCAS'89:

```text
1. Parse test
2. Structural locking test
3. Gate overhead test
4. Functional testing later if sequential simulation is added
```

---

## 4. Folder Structure

Set up the project like this:

```text
logic-locking-research/
│
├── epic.py
├── test_epic.py
├── run_epic_benchmarks.py
│
├── benchmarks/
│   ├── iscas85/
│   │   ├── c17.bench
│   │   ├── c432.bench
│   │   ├── c499.bench
│   │   ├── c880.bench
│   │   └── ...
│   │
│   ├── iscas89/
│   │   ├── s27.bench
│   │   ├── s298.bench
│   │   └── ...
│   │
│   └── itc99/
│       ├── b01.bench
│       ├── b02.bench
│       └── ...
│
├── locked_outputs/
│   ├── iscas85/
│   ├── iscas89/
│   └── itc99/
│
└── results/
    └── epic_results.csv
```

---

## 5. Phase 1: Unit Testing on Tiny Circuits

Before using large benchmarks, create small hand-written `.bench` circuits.

Example file: `tiny1.bench`

```bench
INPUT(a)
INPUT(b)
OUTPUT(out)

n1 = AND(a, b)
out = NOT(n1)
```

Run:

```bash
python epic.py tiny1.bench --key-bits 1 --key 0b0 --seed 1 --output tiny1_locked.bench
```

Check that the locked file contains:

```text
INPUT(keyinput0)
_EPIC_orig
XOR or XNOR
```

Purpose:

```text
This confirms that epic.py can lock a very small circuit correctly.
```

---

## 6. Phase 2: Unit Tests to Write in `test_epic.py`

The test file should include these tests.

### Test 1: Parse Test

Purpose:

```text
Check that epic.py correctly reads .bench files.
```

Checks:

```python
assert len(parsed["inputs"]) > 0
assert len(parsed["outputs"]) > 0
assert len(parsed["gates"]) > 0
```

This confirms the circuit was loaded correctly.

### Test 2: Key Input Test

Purpose:

```text
Check that the locked circuit has new key inputs.
```

Example:

```python
assert "keyinput0" in locked["inputs"]
assert "keyinput1" in locked["inputs"]
```

If key size is 4, then these should exist:

```text
keyinput0
keyinput1
keyinput2
keyinput3
```

### Test 3: Shadow Wire Test

Purpose:

```text
Check that the original wire is preserved using _EPIC_orig.
```

Expected locked structure:

```bench
n1_EPIC_orig = AND(a, b)
n1 = XOR(n1_EPIC_orig, keyinput0)
```

Check for:

```text
_EPIC_orig
```

### Test 4: XOR/XNOR Insertion Test

Purpose:

```text
Check that EPIC actually inserted locking gates.
```

Check that selected locked wires use:

```text
XOR
XNOR
```

### Test 5: Gate Overhead Test

Purpose:

```text
Check how many extra gates were added.
```

Expected:

```text
locked gate count = original gate count + key_bits
```

Example:

```python
assert len(locked["gates"]) == len(original["gates"]) + key_bits
```

Because each locked wire gets one new shadow gate.

### Test 6: Correct-Key Functional Test

Purpose:

```text
Check that the locked circuit behaves like the original circuit when the correct key is used.
```

For every input vector:

```text
original_output == locked_output_with_correct_key
```

For small circuits, test all input combinations.

Example with 3 inputs:

```text
000
001
010
011
100
101
110
111
```

Expected:

```text
All outputs should match.
```

This is the most important correctness test.

### Test 7: Wrong-Key Corruption Test

Purpose:

```text
Check that a wrong key can cause incorrect output.
```

Use:

```python
wrong_key = correct_key ^ 1
```

This flips one key bit.

Then test many input combinations.

Expected:

```text
At least one output should differ.
```

Important:

```text
A wrong key does not need to fail for every input.
It only needs to fail for at least some inputs.
```

### Test 8: Write-File Test

Purpose:

```text
Check that epic.py can write the locked circuit to a new .bench file.
```

Check that the output file exists and contains:

```text
INPUT(keyinput0)
_EPIC_orig
XOR
XNOR
```

---

## 7. Phase 3: ISCAS'85 Benchmark Testing

After tiny tests pass, run full benchmark tests on ISCAS'85.

Start with:

```text
c17
c432
c499
c880
c1355
```

Use fixed keys and seeds.

Example commands:

```bash
python epic.py benchmarks/iscas85/c17.bench \
  --key-bits 4 \
  --key 0b1010 \
  --seed 1 \
  --output locked_outputs/iscas85/c17_k4_locked.bench
```

```bash
python epic.py benchmarks/iscas85/c432.bench \
  --key-bits 8 \
  --key 0xA5 \
  --seed 1 \
  --output locked_outputs/iscas85/c432_k8_locked.bench
```

```bash
python epic.py benchmarks/iscas85/c880.bench \
  --key-bits 16 \
  --key 0xBEEF \
  --seed 1 \
  --output locked_outputs/iscas85/c880_k16_locked.bench
```

---

## 8. Key Sizes to Test

Use different key sizes:

```text
4 bits
8 bits
16 bits
32 bits
64 bits
```

Suggested testing setup:

| Circuit Size | Key Sizes |
|---|---|
| Small circuits like c17 | 1, 2, 4 |
| Medium circuits like c432/c880 | 4, 8, 16 |
| Larger circuits like c1908+ | 16, 32, 64 |

Do not force a very large key size on a tiny circuit. It may not have enough candidate wires to lock.

---

## 9. Seeds to Test

Use multiple seeds because the script randomly chooses wires.

Recommended seeds:

```text
1
2
3
4
5
```

This lets you test whether results change depending on which wires are locked.

Example:

```bash
python epic.py benchmarks/iscas85/c432.bench --key-bits 8 --key 0xA5 --seed 1 --output locked_outputs/iscas85/c432_k8_s1.bench
python epic.py benchmarks/iscas85/c432.bench --key-bits 8 --key 0xA5 --seed 2 --output locked_outputs/iscas85/c432_k8_s2.bench
python epic.py benchmarks/iscas85/c432.bench --key-bits 8 --key 0xA5 --seed 3 --output locked_outputs/iscas85/c432_k8_s3.bench
```

---

## 10. Metrics to Collect

For every benchmark, record:

```text
benchmark name
benchmark family
number of inputs
number of outputs
original gate count
key size
seed
correct key
locked gate count
gate overhead
correct-key pass/fail
wrong-key mismatch count
wrong-key corruption rate
runtime
status
```

Your result table should look like this:

| Circuit | Key Bits | Seed | Inputs | Outputs | Original Gates | Locked Gates | Overhead | Correct Key Pass? | Wrong-Key Corruption |
|---|---:|---:|---:|---:|---:|---:|---:|---|---:|
| c17 | 4 | 1 | 5 | 2 | 6 | 10 | +4 | Yes | 25% |
| c432 | 8 | 1 | 36 | 7 | 160 | 168 | +8 | Yes | 41% |
| c880 | 16 | 1 | 60 | 26 | 383 | 399 | +16 | Yes | 52% |

---

## 11. Correct-Key Equivalence Testing

This test checks:

```text
Original circuit output = locked circuit output with correct key
```

For small circuits:

```text
Use exhaustive testing.
```

Example:

```text
If circuit has 5 inputs, test all 2^5 = 32 input combinations.
```

For large circuits:

```text
Use random simulation.
```

Recommended:

```text
1000 random input vectors
```

For each vector:

```text
1. Simulate original circuit.
2. Simulate locked circuit using the correct key.
3. Compare outputs.
```

Expected:

```text
They should always match.
```

If they do not match, then there is likely a bug.

---

## 12. Wrong-Key Corruption Testing

This test checks whether wrong keys corrupt outputs.

Use:

```text
wrong_key = correct_key ^ 1
```

This flips one key bit.

Then simulate using the wrong key.

Record:

```text
number of mismatching output cases
total number of test cases
corruption rate
```

Formula:

```text
corruption rate = mismatches / total tests
```

Example:

```text
1000 random input vectors tested
430 mismatches
corruption rate = 43%
```

Important note:

```text
A wrong key does not need to fail for every input.
Some wrong-key outputs may still match by chance.
```

---

## 13. Benchmark Runner Script

Create a script called:

```text
run_epic_benchmarks.py
```

It should automatically:

```text
1. Loop through benchmark files.
2. Parse each benchmark.
3. Lock it with different key sizes.
4. Use different seeds.
5. Save the locked .bench file.
6. Simulate correct-key behavior.
7. Simulate wrong-key behavior.
8. Measure overhead.
9. Save results to CSV.
```

Output:

```text
results/epic_results.csv
```

---

## 14. Suggested First Experiment

Start small.

Use:

```text
Benchmark family:
ISCAS'85

Circuits:
c17, c432, c499, c880, c1355

Key sizes:
4, 8, 16

Seeds:
1, 2, 3

Input testing:
c17: exhaustive
others: 1000 random input vectors
```

This is enough for a strong first result.

---

## 15. ISCAS'89 and ITC'99 Plan

For ISCAS'89 and ITC'99, do not promise full functional testing immediately.

Start with:

```text
1. Can epic.py parse the circuit?
2. Can epic.py lock the circuit?
3. How many gates are added?
4. Are key inputs added?
5. Are XOR/XNOR gates inserted?
```

Then write this note:

```text
Full functional equivalence testing for ISCAS'89 and ITC'99 may require sequential simulation because these benchmarks may contain flip-flops and state elements.
```

This is important because sequential circuits do not behave like simple combinational circuits.

Combinational circuit:

```text
output = function(current inputs)
```

Sequential circuit:

```text
output = function(current inputs + previous state)
```

So they need more advanced simulation.

---

## 16. Final Deliverables

At the end, you should have:

```text
1. test_epic.py
   Unit tests for tiny circuits.

2. run_epic_benchmarks.py
   Automated benchmark testing script.

3. locked_outputs/
   Locked .bench files generated by epic.py.

4. results/epic_results.csv
   Table of all benchmark results.

5. short report
   Explanation of what was tested, what passed, and what needs future work.
```

---

## 17. Final Report Structure

Use this structure:

```text
1. Introduction
   Brief explanation of EPIC logic locking.

2. Testing goal
   Explain that the goal was to verify locking correctness and measure overhead/corruption.

3. Methodology
   Explain benchmarks, key sizes, seeds, and simulation method.

4. Unit tests
   Explain tiny circuit tests.

5. Benchmark results
   Include results table.

6. Observations
   Discuss correct-key behavior, wrong-key corruption, and overhead.

7. Limitations
   Explain ISCAS'89/ITC'99 sequential testing limitations.

8. Next steps
   Add SAT attack testing, delay/power overhead, and sequential simulation.
```

---

## 18. What to Say to Professor or Team

You can say:

> My plan is to first validate `epic.py` using small hand-written `.bench` circuits to check parsing, key input insertion, XOR/XNOR gate insertion, correct-key equivalence, wrong-key corruption, and output file generation. After that, I will run EPIC on ISCAS'85 combinational benchmarks such as c17, c432, c499, c880, and c1355 using multiple key sizes and seeds. I will record gate overhead, correct-key pass/fail, and wrong-key corruption rate. For ISCAS'89 and ITC'99, I will begin with parsing and structural locking tests, then handle full functional testing later because those benchmarks may require sequential simulation.

---

## 19. Summary

This plan is divided into clear phases:

```text
Phase 1: Tiny circuit unit tests
Phase 2: ISCAS'85 full benchmark testing
Phase 3: ISCAS'89 and ITC'99 structural testing
Phase 4: Sequential simulation and SAT attack testing later
```

This gives a solid starting point for research work on EPIC logic locking.
