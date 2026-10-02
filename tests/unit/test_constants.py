import math

import pytest

from nodesmith.semantics.constants import Fault, evaluate


def const(i, v, ty="int64"):
    return {"id": i, "op": "const", "type_symbol": ty, "operands": [], "attrs": {"value": v}}


def param(i, name, ty="int64"):
    return {"id": i, "op": "param", "type_symbol": ty, "operands": [], "attrs": {"name": name}}


def binary(i, op, a, b, ty="int64"):
    return {
        "id": i,
        "op": "binary",
        "type_symbol": ty,
        "operands": [a, b],
        "attrs": {"operator": op},
    }


def run(nodes, defaults):
    return evaluate({"root_node_id": nodes[-1]["id"], "nodes": nodes}, defaults)


def test_comparison_of_two_parameters():
    assert (
        run(
            [param("n0", "a"), param("n1", "b"), binary("n2", "<=", "n0", "n1", "bool")],
            {"a": 3, "b": 5},
        )
        is True
    )
    assert (
        run(
            [param("n0", "a"), param("n1", "b"), binary("n2", "<=", "n0", "n1", "bool")],
            {"a": 6, "b": 5},
        )
        is False
    )


def test_integer_division_truncates_toward_zero_and_faults_on_zero():
    assert run([const("n0", -7), const("n1", 2), binary("n2", "/", "n0", "n1")], {}) == -3
    assert run([const("n0", -7), const("n1", 2), binary("n2", "%", "n0", "n1")], {}) == -1
    with pytest.raises(Fault):
        run([const("n0", 1), const("n1", 0), binary("n2", "/", "n0", "n1")], {})


def test_integers_wrap_at_their_width():
    assert run(
        [
            const("n0", 2**31 - 1, "int32"),
            const("n1", 1, "int32"),
            binary("n2", "+", "n0", "n1", "int32"),
        ],
        {},
    ) == -(2**31)


def test_float_division_by_zero_is_not_a_fault():
    assert (
        run(
            [
                const("n0", 1.0, "float64"),
                const("n1", 0.0, "float64"),
                binary("n2", "/", "n0", "n1", "float64"),
            ],
            {},
        )
        == math.inf
    )


def test_int_min_over_minus_one_is_a_fault():
    with pytest.raises(Fault):
        run([const("n0", -(2**63)), const("n1", -1), binary("n2", "/", "n0", "n1")], {})
