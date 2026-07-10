import random
from typing import List, Tuple, Dict, Optional
from dataclasses import dataclass
from enum import Enum


class GateType(Enum):
    AND = "AND"
    OR = "OR"
    NOT = "NOT"
    NAND = "NAND"
    NOR = "NOR"
    XOR = "XOR"
    XNOR = "XNOR"
    BUF = "BUF"


@dataclass
class Gate:
    gate_id: str
    gate_type: GateType
    inputs: List[str]
    output: str

    # Computes the output value of this gate given a dict of all current wire values
    def evaluate(self, values: Dict[str, int]) -> int:
        if not all(inp in values for inp in self.inputs):
            raise ValueError(f"Missing input values for gate {self.gate_id}")

        input_vals = [values[inp] for inp in self.inputs]

        if self.gate_type == GateType.AND:
            return int(all(input_vals))
        elif self.gate_type == GateType.OR:
            return int(any(input_vals))
        elif self.gate_type == GateType.NOT:
            return 1 - input_vals[0]
        elif self.gate_type == GateType.NAND:
            return 1 - int(all(input_vals))
        elif self.gate_type == GateType.NOR:
            return 1 - int(any(input_vals))
        elif self.gate_type == GateType.XOR:
            return int(sum(input_vals) % 2 == 1)
        elif self.gate_type == GateType.XNOR:
            return int(sum(input_vals) % 2 == 0)
        elif self.gate_type == GateType.BUF:
            return input_vals[0]
        else:
            raise ValueError(f"Unknown gate type: {self.gate_type}")


class Circuit:
    def __init__(self):
        self.gates: List[Gate] = []
        self.primary_inputs: List[str] = []
        self.primary_outputs: List[str] = []
        self.key_inputs: List[str] = []
        self.wire_to_gate: Dict[str, Gate] = {}
        self.gate_levels: Dict[str, int] = {}
        self._sorted_gates: Optional[List[Gate]] = None  # cached topological order

    def add_gate(self, gate: Gate):
        self.gates.append(gate)
        self.wire_to_gate[gate.output] = gate
        self._sorted_gates = None

    def add_primary_input(self, name: str):
        if name not in self.primary_inputs:
            self.primary_inputs.append(name)

    def add_primary_output(self, name: str):
        if name not in self.primary_outputs:
            self.primary_outputs.append(name)

    def add_key_input(self, name: str):
        if name not in self.key_inputs:
            self.key_inputs.append(name)

    # Assigns each gate a level based on the maximum level of its inputs + 1; primary inputs and key inputs start at level 0. Used to determine evaluation order.
    def compute_levels(self):
        self.gate_levels = {}
        for pi in self.primary_inputs + self.key_inputs:
            self.gate_levels[pi] = 0

        changed = True
        while changed:
            changed = False
            for gate in self.gates:
                max_input_level = -1
                all_inputs_have_level = True

                for inp in gate.inputs:
                    if inp in self.gate_levels:
                        max_input_level = max(max_input_level, self.gate_levels[inp])
                    else:
                        all_inputs_have_level = False
                        break

                if all_inputs_have_level:
                    new_level = max_input_level + 1
                    if gate.output not in self.gate_levels or self.gate_levels[gate.output] != new_level:
                        self.gate_levels[gate.output] = new_level
                        changed = True

    # Simulates the circuit for given primary input and key values; computes and caches topological gate order on first call, then reuses it
    def evaluate(self, input_values, key_values):
        values = {**input_values, **key_values}
        if self._sorted_gates is None:
            self.compute_levels()
            self._sorted_gates = sorted(self.gates, key=lambda g: self.gate_levels.get(g.output, float('inf')))
        for gate in self._sorted_gates:
            values[gate.output] = gate.evaluate(values)
        return {out: values[out] for out in self.primary_outputs}

    # Returns an independent deep copy of the circuit so locking layers can modify it without affecting the original
    def copy(self) -> 'Circuit':
        new_circuit = Circuit()
        new_circuit.primary_inputs = self.primary_inputs.copy()
        new_circuit.primary_outputs = self.primary_outputs.copy()
        new_circuit.key_inputs = self.key_inputs.copy()

        for gate in self.gates:
            new_gate = Gate(gate.gate_id, gate.gate_type, gate.inputs.copy(), gate.output)
            new_circuit.add_gate(new_gate)

        return new_circuit


class BenchmarkParser:

    # Reads an ISCAS .bench file and constructs a Circuit with all inputs,
    # outputs, and gates populated from the file
    @staticmethod
    def parse_bench(filename: str) -> Circuit:
        circuit = Circuit()

        with open(filename, 'r') as f:
            lines = f.readlines()

        for line in lines:
            line = line.strip()
            if not line or line.startswith('#'):
                continue

            if line.startswith('INPUT'):
                input_name = line.split('(')[1].split(')')[0]
                circuit.add_primary_input(input_name)
            elif line.startswith('OUTPUT'):
                output_name = line.split('(')[1].split(')')[0]
                circuit.add_primary_output(output_name)
            elif '=' in line:
                parts = line.split('=')
                output = parts[0].strip()
                gate_expr = parts[1].strip()

                if '(' in gate_expr:
                    gate_type_str = gate_expr.split('(')[0].strip()
                    inputs_str = gate_expr.split('(')[1].split(')')[0]
                    inputs = [inp.strip() for inp in inputs_str.split(',')]

                    gate_type_map = {
                        'AND': GateType.AND, 'OR': GateType.OR, 'NOT': GateType.NOT,
                        'NAND': GateType.NAND, 'NOR': GateType.NOR, 'XOR': GateType.XOR,
                        'XNOR': GateType.XNOR, 'BUF': GateType.BUF, 'BUFF': GateType.BUF,
                    }

                    if gate_type_str in gate_type_map:
                        gate = Gate(
                            gate_id=f"G_{output}",
                            gate_type=gate_type_map[gate_type_str],
                            inputs=inputs,
                            output=output
                        )
                        circuit.add_gate(gate)

        return circuit

    # Serializes a Circuit back to .bench format with all inputs (primary + key), outputs, and gates written in standard ISCAS style
    @staticmethod
    def write_bench(circuit: Circuit, filename: str):
        gate_type_str_map = {
            GateType.AND: 'AND', GateType.OR: 'OR', GateType.NOT: 'NOT',
            GateType.NAND: 'NAND', GateType.NOR: 'NOR', GateType.XOR: 'XOR',
            GateType.XNOR: 'XNOR', GateType.BUF: 'BUF', }

        with open(filename, 'w') as f:
            f.write("# Locked circuit\n")
            f.write("# Generated by Anti-SAT implementation\n\n")

            for pi in circuit.primary_inputs:
                f.write(f"INPUT({pi})\n")
            for ki in circuit.key_inputs:
                f.write(f"INPUT({ki})\n")

            f.write("\n")

            for po in circuit.primary_outputs:
                f.write(f"OUTPUT({po})\n")

            f.write("\n")

            for gate in circuit.gates:
                inputs_str = ', '.join(gate.inputs)
                gate_type_str = gate_type_str_map[gate.gate_type]
                f.write(f"{gate.output} = {gate_type_str}({inputs_str})\n")


class AntiSAT:
    """Baseline Anti-SAT block (Xie & Srivastava, CHES 2016, Fig. 3(a)/4(a)).

    Two blocks share the same n input wires X, each XOR'd with its own
    n-bit key (Kl1, Kl2) before feeding an AND-tree (g) and a NAND-tree
    (g-bar). Y = g(X^Kl1) AND g-bar(X^Kl2) is XOR'd into a primary output.

    Correct-key condition: Kl1_i == Kl2_i for every bit i (paper section
    4.1) -- then both trees see identical inputs, so g == NOT(g-bar)
    always, Y is a constant 0, and the XOR into the output is a no-op for
    every input. Any wrong key makes Y = 1 for at least the one input
    pattern that makes g(X^Kl1) fire (g is an AND-tree, so p=1: it fires
    on exactly one pattern), corrupting the output there.
    """

    def __init__(self, circuit: Circuit, block_input_size: int):
        self.original_circuit = circuit
        self.n = block_input_size
        self.locked_circuit: Optional[Circuit] = None
        self.correct_key: Optional[Dict[str, int]] = None
        self.block_inputs: List[str] = []
        self.target_output: Optional[str] = None

    # Reduces a list of wires with a balanced AND tree; returns the output wire
    def _and_tree(self, wires: List[str], gate_prefix: str) -> Tuple[str, List[Gate]]:
        gates = []
        if len(wires) == 1:
            return wires[0], gates
        level = wires[:]
        stage = 0
        while len(level) > 1:
            next_level = []
            for i in range(0, len(level) - 1, 2):
                and_wire = f"{gate_prefix}_and_s{stage}_{i // 2}"
                gates.append(Gate(
                    gate_id=f"{gate_prefix}_AND_s{stage}_{i // 2}",
                    gate_type=GateType.AND,
                    inputs=[level[i], level[i + 1]],
                    output=and_wire
                ))
                next_level.append(and_wire)
            if len(level) % 2 == 1:
                next_level.append(level[-1])
            level = next_level
            stage += 1
        return level[0], gates

    def lock(self, seed: Optional[int] = None, target_output_idx: int = 0) -> Circuit:
        if seed is not None:
            random.seed(seed)

        self.locked_circuit = self.original_circuit.copy()
        n = self.n

        if n > len(self.locked_circuit.primary_inputs):
            raise ValueError(
                f"block_input_size ({n}) exceeds available primary inputs "
                f"({len(self.locked_circuit.primary_inputs)})"
            )
        self.block_inputs = random.sample(self.locked_circuit.primary_inputs, n)

        kl1_names = [f"keyinput{i}" for i in range(n)]
        kl2_names = [f"keyinput{n + i}" for i in range(n)]
        for kw in kl1_names + kl2_names:
            self.locked_circuit.add_key_input(kw)

        # Kl2 == Kl1 is one of the 2^n valid correct-key combinations (paper
        # section 4.1): any bit-for-bit-equal pair works, this just picks one.
        kl1_bits = [random.randint(0, 1) for _ in range(n)]
        kl2_bits = kl1_bits[:]
        self.correct_key = {}
        self.correct_key.update(dict(zip(kl1_names, kl1_bits)))
        self.correct_key.update(dict(zip(kl2_names, kl2_bits)))

        all_gates: List[Gate] = []

        # z1_i = x_i XOR Kl1_i, feeds g (AND-tree)
        z1_wires = []
        for i, pi in enumerate(self.block_inputs):
            z_wire = f"antisat_z1_{i}"
            all_gates.append(Gate(f"ANTISAT_Z1_{i}", GateType.XOR, [pi, kl1_names[i]], z_wire))
            z1_wires.append(z_wire)

        # z2_i = x_i XOR Kl2_i, feeds g-bar (NAND-tree)
        z2_wires = []
        for i, pi in enumerate(self.block_inputs):
            z_wire = f"antisat_z2_{i}"
            all_gates.append(Gate(f"ANTISAT_Z2_{i}", GateType.XOR, [pi, kl2_names[i]], z_wire))
            z2_wires.append(z_wire)

        g_out, g_gates = self._and_tree(z1_wires, "antisat_g")
        all_gates.extend(g_gates)

        gbar_and_out, gbar_gates = self._and_tree(z2_wires, "antisat_gbar")
        all_gates.extend(gbar_gates)
        gbar_out = "antisat_gbar_out"
        all_gates.append(Gate("ANTISAT_GBAR_NOT", GateType.NOT, [gbar_and_out], gbar_out))

        y_wire = "antisat_y"
        all_gates.append(Gate("ANTISAT_Y_AND", GateType.AND, [g_out, gbar_out], y_wire))

        for gate in all_gates:
            self.locked_circuit.add_gate(gate)

        # Secure integration (paper section 4.3): XOR Y into a primary
        # output. A future refinement could pick the wire with the top-30%
        # observability instead of a primary output directly, per the
        # paper's "secure integration" experiment -- a primary output is
        # the simplest choice that guarantees full observability.
        self.target_output = self.locked_circuit.primary_outputs[target_output_idx]
        new_output = f"{self.target_output}_antisat"
        self.locked_circuit.add_gate(Gate(
            gate_id="ANTISAT_OUTPUT_XOR",
            gate_type=GateType.XOR,
            inputs=[self.target_output, y_wire],
            output=new_output
        ))
        self.locked_circuit.primary_outputs[target_output_idx] = new_output

        return self.locked_circuit

    def write_key(self, path: str):
        with open(path, 'w') as f:
            f.write(f"# Anti-SAT Secret Key File\n")
            f.write(f"# block_input_size (n): {self.n}\n")
            f.write(f"# target_output: {self.target_output}\n\n")
            for name in sorted(self.correct_key.keys()):
                f.write(f"{name} = {self.correct_key[name]}\n")

    def print_summary(self):
        n = self.n
        print("\n" + "=" * 60)
        print("  Anti-SAT Locking Summary")
        print("=" * 60)
        print(f"  Block input size (n)  : {n}")
        print(f"  Total key bits (2n)   : {2 * n}")
        print(f"  Block inputs (X)      : {', '.join(self.block_inputs)}")
        print(f"  Target output         : {self.target_output}")
        print(f"  SAT attack iterations : lower-bounded by 2^{n} (Theorem 1, p=1)")
        print("=" * 60 + "\n")


def main():
    import argparse

    ap = argparse.ArgumentParser(
        description="Apply a baseline Anti-SAT block to a .bench circuit "
                    "(Xie & Srivastava, CHES 2016)."
    )
    ap.add_argument('input', help='Input .bench file')
    ap.add_argument('output', help='Output locked .bench file')
    ap.add_argument('--block-size', '-n', type=int, default=8,
                    help='Anti-SAT block input size n (total key bits = 2n). Default: 8')
    ap.add_argument('--seed', '-s', type=int, default=None,
                    help='Random seed for reproducibility (default: random)')
    ap.add_argument('--target-output-idx', type=int, default=0,
                    help='Index into primary_outputs to XOR the Anti-SAT block into')
    ap.add_argument('--key-file', '-k', type=str, default=None,
                    help='Path to write the secret key (default: <output>.key)')
    args = ap.parse_args()

    print(f"[Anti-SAT] Parsing: {args.input}")
    circuit = BenchmarkParser.parse_bench(args.input)
    print(f"  {len(circuit.primary_inputs)} inputs, {len(circuit.gates)} gates, "
          f"{len(circuit.primary_outputs)} outputs")

    locker = AntiSAT(circuit, block_input_size=args.block_size)
    locked = locker.lock(seed=args.seed, target_output_idx=args.target_output_idx)
    locker.print_summary()

    BenchmarkParser.write_bench(locked, args.output)
    print(f"[Anti-SAT] Locked circuit written to: {args.output}")

    key_path = args.key_file or (args.output.replace('.bench', '') + '.key')
    locker.write_key(key_path)
    print(f"[Anti-SAT] Secret key written to: {key_path}")


if __name__ == '__main__':
    main()
