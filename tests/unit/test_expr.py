import pytest

from nodesmith.diagnostics import BuildError
from nodesmith.expr import common_type, parse_expression, parse_statement


def test_precedence_and_ternary():
    assert parse_expression("1 + 2 * 3") == ("binary", "+", ("const", "int32", 1), ("binary", "*", ("const", "int32", 2), ("const", "int32", 3)))
    assert parse_expression("a ? b : c ? d : e")[3][0] == "select"      # ?: is right-associative


def test_statements():
    assert parse_statement("let y = 1.0")[0] == "let"
    assert parse_statement("state.x = y")[:2] == ("assign", "x")


@pytest.mark.parametrize("bad", ["y = 1", "let y", "let y = (1", "let y = 1 2", "let y = @"])
def test_syntax_errors(bad):
    with pytest.raises(BuildError) as e:
        parse_statement(bad)
    assert e.value.diagnostic.code == "ERR_SYN_002"


def test_common_type():
    assert common_type("int32", "float32") == "float64" and common_type("int32", "int64") == "int64" and common_type("bool", "int32") is None
