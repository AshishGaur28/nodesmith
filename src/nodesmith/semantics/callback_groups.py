"""Assigns callback groups to pipelines (SPEC-12 section 3).

Two pipelines *conflict* if one writes a state variable the other reads or writes. Pipelines
that conflict, directly or through others, form a component and share one mutually
exclusive callback group, so state needs no locks. A pipeline that touches no state gets a
group of its own. A pipeline may name a group explicitly (``realtime`` block).
"""

from dataclasses import dataclass

from ..diagnostics import BuildError, Cascade, Report


@dataclass(frozen=True)
class StateAccess:
    """How one pipeline uses state."""

    reads: frozenset
    writes: frozenset
    uses_dt: bool  # reads time since its previous run, which is state too

    @property
    def touches_state(self) -> bool:
        """True if the pipeline reads or writes state, or reads the time since its last run."""
        return bool(self.reads or self.writes or self.uses_dt)

    def conflicts_with(self, other: "StateAccess") -> bool:
        """True if one of the two writes what the other reads or writes."""
        return bool(
            self.writes & (other.reads | other.writes) or other.writes & (self.reads | self.writes)
        )


def _components(names: list[str], accesses: dict[str, StateAccess]) -> list[list[str]]:
    """Group pipelines whose state accesses conflict, transitively (union-find)."""
    parent = {name: name for name in names}

    def root(name: str) -> str:
        while parent[name] != name:
            parent[name] = parent[parent[name]]
            name = parent[name]
        return name

    for first in names:
        for second in names:
            if first < second and accesses[first].conflicts_with(accesses[second]):
                parent[root(first)] = root(second)
    grouped: dict[str, list[str]] = {}
    for name in names:
        grouped.setdefault(root(name), []).append(name)
    return list(grouped.values())


def assign_callback_groups(
    manifest: dict, accesses: dict[str, StateAccess], report: Report
) -> tuple[dict[str, str], dict[str, tuple[str, bool]]]:
    """Return ``(group of each pipeline, {group: (type, named explicitly)})``.

    Reports ``ERR_CNC_201`` for an unsafe assignment and ``ERR_SEM_105`` for an unknown group."""
    pipelines = manifest.get("pipelines", [])
    index_of = {p["name"]: i for i, p in enumerate(pipelines)}
    declared = {
        g["name"]: g["type"] for g in manifest.get("realtime", {}).get("callback_groups", [])
    }
    explicit = {p["name"]: p["callback_group"] for p in pipelines if "callback_group" in p}

    assignment: dict[str, str] = {}
    groups: dict[str, tuple[str, bool]] = {}
    state_domains = 0
    components = sorted(_components(sorted(accesses), accesses), key=lambda c: c[0])
    for component in components:
        pointer = next((n for n in component if n in explicit), component[0])
        where = (
            "pipelines",
            index_of[pointer],
            "callback_group" if pointer in explicit else "name",
        )
        with report.guard(where):
            named = {explicit[n] for n in component if n in explicit}
            stateful = any(accesses[n].touches_state for n in component)
            if len(named) > 1:
                raise BuildError(
                    "ERR_CNC_201",
                    f"pipelines {component} share state but name several callback groups",
                )
            if named:
                group = next(iter(named))
                if group not in declared:
                    if not declared:
                        raise Cascade  # no realtime block: ERR_RT_002 already says so
                    raise BuildError("ERR_SEM_105", f"unknown callback_group {group!r}")
                if stateful and declared[group] != "MutuallyExclusive":
                    raise BuildError(
                        "ERR_CNC_201", f"stateful pipeline in Reentrant group {group!r}"
                    )
                assignment.update(dict.fromkeys(component, group))
                groups[group] = (declared[group], True)
            elif stateful:
                state_domains += 1
                group = f"state_domain_{state_domains}"
                assignment.update(dict.fromkeys(component, group))
                groups[group] = ("MutuallyExclusive", False)
            else:
                for name in component:
                    assignment[name] = f"pipeline_{name}"
                    groups[f"pipeline_{name}"] = ("MutuallyExclusive", False)
    return assignment, groups
