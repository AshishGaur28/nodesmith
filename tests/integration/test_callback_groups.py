"""Callback-group assignment (SPEC-12 §3): worked by hand for the examples, and checked as properties on random inputs."""

import random
from pathlib import Path

import pytest

from nodesmith.api import compile_manifest
from nodesmith.diagnostics import Report
from nodesmith.semantics.callback_groups import StateAccess, assign_callback_groups

EX = Path(__file__).resolve().parents[2] / "examples"

# Derived by hand from each manifest: pipelines that read or write the same state variable share one mutually exclusive
# "state domain"; a pipeline that touches no state gets its own group; an explicit callback_group is used as named.
EXPECTED = {
    "01_simple_pubsub": {"echo": "pipeline_echo"},  # stateless
    "02_filter_pipeline": {"filter_imu": "state_domain_1"},  # reads and writes prev_z
    "03_state_machine": {"tick": "state_domain_1", "reset": "state_domain_1"},  # both touch `count`
    "04_all_extensions": {
        "relay_frame": "pipeline_relay_frame",
        "control_loop": "control_loop_group",
    },  # the second names its group
    "05_realtime_controller": {"control_loop": "pipeline_control_loop"},  # stateless
    "06_secured_filter": {"filter_imu": "state_domain_1"},
    "07_gain_service": {
        "apply_gain": "state_domain_1",
        "handle_set": "state_domain_1",  # all four touch `gain`
        "handle_reset": "state_domain_1",
        "handle_get": "state_domain_1",
    },
    "08_link_watchdog": {
        "boot": "state_domain_1",
        "on_heartbeat": "state_domain_1",
        "check_link": "state_domain_1",
    },  # all touch `last_seen`
}


@pytest.mark.parametrize("name", sorted(EXPECTED))
def test_groups_match_the_hand_worked_answer(name):
    ir = compile_manifest(EX / f"{name}.toml").ir
    assert {d["id"]: d["callback_group"] for d in ir["execution_dags"]} == EXPECTED[name]
    assert all(g["type"] == "MutuallyExclusive" for g in ir["concurrency"]["callback_groups"])


def test_effective_executor_of_the_examples():
    assert compile_manifest(EX / "04_all_extensions.toml").ir["concurrency"] == {
        "executor": "MultiThreadedExecutor",
        "threads": 2,
        "callback_groups": [
            {"name": "control_loop_group", "type": "MutuallyExclusive", "explicit": True},
            {"name": "pipeline_relay_frame", "type": "MutuallyExclusive", "explicit": False},
        ],
    }
    assert (
        compile_manifest(EX / "02_filter_pipeline.toml").ir["concurrency"]["executor"]
        == "SingleThreadedExecutor"
    )


# ---------------------------------------------------------------- properties
def random_case(rng):
    n = rng.randint(1, 7)
    names = [f"p{i}" for i in range(n)]
    variables = [f"v{i}" for i in range(rng.randint(0, 4))]
    access = {}
    for name in names:
        reads = {v for v in variables if rng.random() < 0.3}
        writes = {v for v in variables if rng.random() < 0.25}
        access[name] = (reads, writes, rng.random() < 0.1)
    return names, access


def components(names, access):
    """Independent reference: connected components of the conflict relation (one side writes what the other reads or writes)."""
    conflict = lambda a, b: bool(
        access[a][1] & (access[b][0] | access[b][1]) or access[b][1] & (access[a][0] | access[a][1])
    )
    seen, out = set(), []
    for a in names:
        if a in seen:
            continue
        comp, todo = set(), [a]
        while todo:
            x = todo.pop()
            if x in comp:
                continue
            comp.add(x)
            todo += [y for y in names if y != x and conflict(x, y)]
        seen |= comp
        out.append(comp)
    return out


def run(names, access):
    m = {"pipelines": [{"name": n} for n in names]}
    report = Report()
    accesses = {
        n: StateAccess(frozenset(access[n][0]), frozenset(access[n][1]), access[n][2])
        for n in names
    }
    assign, groups = assign_callback_groups(m, accesses, report)
    assert not report.errors
    return assign, groups


@pytest.mark.parametrize("seed", range(200))
def test_assignment_properties(seed):
    rng = random.Random(seed)
    names, access = random_case(rng)
    assign, groups = run(names, access)
    stateful = lambda n: bool(access[n][0] or access[n][1] or access[n][2])
    # groups are exactly the conflict components, and stateless pipelines are alone in a group of their own
    by_group = {}
    for n, g in assign.items():
        by_group.setdefault(g, set()).add(n)
    comps = components(names, access)
    assert sorted(map(sorted, by_group.values())) == sorted(map(sorted, comps))
    for n in names:
        if not stateful(n):
            assert assign[n] == f"pipeline_{n}" and by_group[assign[n]] == {n}
    # anything that conflicts shares a group, so state needs no lock
    for a in names:
        for b in names:
            if access[a][1] & (access[b][0] | access[b][1]):
                assert assign[a] == assign[b]
    # every group is mutually exclusive, and state domains are numbered 1..k with no gaps
    assert all(t == "MutuallyExclusive" for t, _ in groups.values())
    domains = sorted(int(g.rsplit("_", 1)[1]) for g in groups if g.startswith("state_domain_"))
    assert domains == list(range(1, len(domains) + 1))


@pytest.mark.parametrize("seed", range(50))
def test_assignment_does_not_depend_on_declaration_order(seed):
    rng = random.Random(1000 + seed)
    names, access = random_case(rng)
    shuffled = names[:]
    rng.shuffle(shuffled)
    assert run(names, access)[0] == run(shuffled, access)[0]
