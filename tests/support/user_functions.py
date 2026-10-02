"""Implementations of the user functions that the example and test manifests declare.

Each is written twice, once in Python (for the reference interpreter) and once in C++ (for the
generated node), and the differential tests check that the two agree. In a real project the C++
version is a file in the user's ``logic/`` folder.

A Python implementation takes the call's arguments and returns ``(value, fault)``. The C++
version is a set of functions in the namespace ``logic``, as declared by the generated ``logic_api.hpp``.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class Implementation:
    """A user's functions in both languages: ``python`` maps names to callables; ``cpp`` is the
    definition of the functions, to be placed in the namespace ``logic``."""

    python: dict
    cpp: str


def _wrap32(value: int) -> int:
    value &= 0xFFFFFFFF
    return value - (1 << 32) if value >= 1 << 31 else value


# ---------------------------------------------------------------- tests/fixtures/semantics.toml
SEMANTICS = Implementation(
    python={
        "scale": lambda x, k: (x * k + 1.0, False),
        "label": lambda n, text: (text + str(n), False),
        "double_unless_negative": lambda x: (0, True) if x < 0 else (_wrap32(x * 2), False),
        "total": lambda values: (sum_in_order(values), False),
    },
    cpp="""
double scale(double x, double k, bool & fault) { (void)fault; return x * k + 1.0; }
std::string label(std::int32_t n, const std::string & text, bool & fault) {
  (void)fault;
  return text + std::to_string(n);
}
std::int32_t double_unless_negative(std::int32_t x, bool & fault) {
  if (x < 0) { fault = true; return 0; }
  return static_cast<std::int32_t>(static_cast<std::uint32_t>(x) * 2u);
}
double total(const std::vector<double> & values, bool & fault) {
  (void)fault;
  double sum = 0.0;
  for (double v : values) sum += v;
  return sum;
}
""",
)


def sum_in_order(values):
    """Sum left to right starting from 0.0, as the C++ loop does (``sum()`` may differ)."""
    total = 0.0
    for value in values:
        total += value
    return total


# ---------------------------------------------------------------- examples/09_user_functions.toml
def _plan_speed(distance, limit, previous):
    if distance < 0:
        return 0.0, True
    wanted = distance * 0.5
    target = wanted if wanted < limit else limit
    return previous + 0.5 * (target - previous), False


def _describe(speed):
    if speed <= 0.0:
        return "stopped", False
    return ("slow" if speed < 1.0 else "fast"), False


SPEED_PLANNER = Implementation(
    python={"plan_speed": _plan_speed, "describe": _describe},
    cpp="""
double plan_speed(double distance, double limit, double previous, bool & fault) {
  if (distance < 0) { fault = true; return 0.0; }
  const double wanted = distance * 0.5;
  const double target = wanted < limit ? wanted : limit;
  return previous + 0.5 * (target - previous);
}
std::string describe(double speed, bool & fault) {
  (void)fault;
  if (speed <= 0.0) return "stopped";
  return speed < 1.0 ? "slow" : "fast";
}
""",
)

IMPLEMENTATIONS = {"semantics_node": SEMANTICS, "speed_planner_node": SPEED_PLANNER}
