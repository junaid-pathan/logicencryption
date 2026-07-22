from typing import List, Dict, Optional
from dataclasses import dataclass
from enum import Enum

CONSTANT_WIRES = {"0", "1", "1'b0", "1'b1"}


class GateType(Enum):
    AND = "AND"
    OR = "OR"
    NOT = "NOT"
    NAND = "NAND"
    NOR = "NOR"
    XOR = "XOR"
    XNOR = "XNOR"
    BUF = "BUF"
    DFF = "DFF"


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
            raise ValueError(
                f"Gate type {self.gate_type} has no combinational evaluate() "
                f"(DFF gates are stateful and must be unrolled first, see Unroller)"
            )


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
    def evaluate(self, input_values, key_values=None):
        values = {**input_values, **(key_values or {})}
        if self._sorted_gates is None:
            self.compute_levels()
            self._sorted_gates = sorted(self.gates, key=lambda g: self.gate_levels.get(g.output, float('inf')))
        for gate in self._sorted_gates:
            values[gate.output] = gate.evaluate(values)
        return {out: values[out] for out in self.primary_outputs}


class BenchmarkParser:

    # Reads a .bench file and constructs a Circuit with all inputs, outputs,
    # and gates populated from the file. Unlike the combinational-only
    # parsers in epic.py/ttlock.py/sarlock.py, this one keeps DFF gates
    # (needed as input to Unroller below).
    @staticmethod
    def parse_bench(filename: str) -> Circuit:
        circuit = Circuit()

        with open(filename, 'r') as f:
            lines = f.readlines()

        gate_type_map = {
            'AND': GateType.AND, 'OR': GateType.OR, 'NOT': GateType.NOT,
            'NAND': GateType.NAND, 'NOR': GateType.NOR, 'XOR': GateType.XOR,
            'XNOR': GateType.XNOR, 'BUF': GateType.BUF, 'BUFF': GateType.BUF,
            'DFF': GateType.DFF,
        }

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
                    gate_type_str = gate_expr.split('(')[0].strip().upper()
                    inputs_str = gate_expr.split('(')[1].split(')')[0]
                    inputs = [inp.strip() for inp in inputs_str.split(',')]

                    if gate_type_str in gate_type_map:
                        gate = Gate(
                            gate_id=f"G_{output}",
                            gate_type=gate_type_map[gate_type_str],
                            inputs=inputs,
                            output=output
                        )
                        circuit.add_gate(gate)

        return circuit

    # Serializes a Circuit back to .bench format
    @staticmethod
    def write_bench(circuit: Circuit, filename: str):
        gate_type_str_map = {
            GateType.AND: 'AND', GateType.OR: 'OR', GateType.NOT: 'NOT',
            GateType.NAND: 'NAND', GateType.NOR: 'NOR', GateType.XOR: 'XOR',
            GateType.XNOR: 'XNOR', GateType.BUF: 'BUF', GateType.DFF: 'DFF',
        }

        with open(filename, 'w') as f:
            f.write("# Unrolled circuit\n")
            f.write("# Generated by unroll.py\n\n")

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
