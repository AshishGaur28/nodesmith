"""Syntax of the expression language, and the type rules of ``language``."""

import pytest

from nodesmith.diagnostics import BuildError
from nodesmith.semantics.expr import (
    Assign,
    Binary,
    Call,
    Const,
    Index,
    Let,
    Local,
    Ref,
    Select,
    Unary,
    parse_expression,
    parse_statement,
)
from nodesmith.semantics.language import common_type


def test_multiplication_binds_tighter_than_addition():
    assert parse_expression("1 + 2 * 3") == Binary(
        "+", Const("int32", 1), Binary("*", Const("int32", 2), Const("int32", 3))
    )


def test_ternary_is_right_associative():
    tree = parse_expression("a ? b : c ? d : e")
    assert tree == Select(Local("a"), Local("b"), Select(Local("c"), Local("d"), Local("e")))


def test_references_calls_unary_and_indexing():
    assert parse_expression("msg.a.b") == Ref("msg", ("a", "b"))
    assert parse_expression("f(1.0, x)") == Call("f", (Const("float64", 1.0), Local("x")))
    assert parse_expression("-x") == Unary("-", Local("x"))
    assert parse_expression("v[0]") == Index(Local("v"), Const("int32", 0))


def test_integer_literals_that_do_not_fit_int32_are_int64():
    assert parse_expression("2147483648") == Const("int64", 2147483648)


def test_statements():
    assert parse_statement("let y = 1.0") == Let("y", Const("float64", 1.0))
    assert parse_statement("state.x = y") == Assign("x", Local("y"))


@pytest.mark.parametrize("bad", ["y = 1", "let y", "let y = (1", "let y = 1 2", "let y = @"])
def test_syntax_errors(bad):
    with pytest.raises(BuildError) as error:
        parse_statement(bad)
    assert error.value.diagnostic.code == "ERR_SYN_002"


def test_common_type():
    assert common_type("int32", "float32") == "float64"
    assert common_type("int32", "int64") == "int64"
    assert common_type("bool", "int32") is None
