"""INTELLIGENCE-PLANNING -- deterministic DAG analysis vectors."""

import random

import pytest

from app.intelligence.planning.dag import analyze_dag
from app.intelligence.planning.limits import PLAN_LIMITS


pytestmark = pytest.mark.contract


def steps(edges):
    """edges: {key: [deps]} -> step dicts in dict order."""
    return [{"step_key": k, "depends_on": list(v)} for k, v in edges.items()]


def codes(analysis):
    return sorted(v.code for v in analysis.violations)


def test_valid_shapes():
    assert codes(analyze_dag(steps({"a": []}))) == []
    assert analyze_dag(steps({"a": []})).depth == 1

    chain = analyze_dag(steps({"a": [], "b": ["a"], "c": ["b"], "d": ["c"]}))
    assert codes(chain) == [] and chain.depth == 4
    assert chain.order == ("a", "b", "c", "d")

    diamond = analyze_dag(steps({"a": [], "b": ["a"], "c": ["a"], "d": ["b", "c"]}))
    assert codes(diamond) == [] and diamond.depth == 3
    assert diamond.ancestors["d"] == {"a", "b", "c"}
    assert diamond.ancestors["a"] == frozenset()


def test_depth_limit_counts_nodes():
    five = {"a": [], "b": ["a"], "c": ["b"], "d": ["c"], "e": ["d"]}

    assert codes(analyze_dag(steps(five))) == ["dag_depth_exceeded"]
    five.pop("e")
    assert codes(analyze_dag(steps(five))) == []


def test_fan_out_limit():
    base = {"r": []}
    for i in range(3):
        base[f"x{i}"] = ["r"]
    assert codes(analyze_dag(steps(base))) == []

    base["x3"] = ["r"]
    result = analyze_dag(steps(base))
    assert codes(result) == ["dag_fan_out_exceeded"]
    assert result.max_fan_out == 4


def test_maximum_authorized_shape_is_accepted():
    # 8 steps, depth 4, fan-out 3
    edges = {
        "r": [],
        "a": ["r"],
        "b": ["r"],
        "c": ["r"],
        "d": ["a"],
        "e": ["b"],
        "f": ["d"],
        "g": ["e"],
    }
    result = analyze_dag(steps(edges))

    assert len(edges) == PLAN_LIMITS.max_steps
    assert codes(result) == []
    assert (result.depth, result.max_fan_out) == (4, 3)


def test_structural_violations():
    assert codes(analyze_dag(steps({"a": ["a"]}))) == ["dag_self_dependency"]
    assert codes(analyze_dag(steps({"a": [], "b": ["a", "a"]}))) == ["dag_duplicate_dependency"]
    assert codes(analyze_dag(steps({"a": ["ghost"]}))) == ["dag_unknown_dependency"]


def test_cycles_are_detected_and_expose_no_ancestors():
    two = analyze_dag(steps({"a": ["b"], "b": ["a"]}))
    three = analyze_dag(steps({"a": ["c"], "b": ["a"], "c": ["b"]}))
    tail = analyze_dag(steps({"s": [], "a": ["b", "s"], "b": ["a"]}))

    for result in (two, three, tail):
        assert "dag_cycle" in codes(result)
        assert result.ancestors == {}


def test_duplicate_step_keys_are_reported_alone():
    result = analyze_dag(
        [
            {"step_key": "a", "depends_on": []},
            {"step_key": "a", "depends_on": ["zzz"]},
        ]
    )

    assert codes(result) == ["dag_duplicate_step_key"]
    assert result.order == ()


def test_diamond_does_not_trip_fan_out_or_dedup():
    # two parents sharing a child must not count as a duplicate dependency
    result = analyze_dag(steps({"a": [], "b": ["a"], "c": ["a"], "d": ["b", "c"]}))

    assert codes(result) == [] and result.max_fan_out == 2


def test_violation_paths_point_at_the_step_index():
    result = analyze_dag(steps({"a": [], "b": ["ghost"]}))

    assert [v.path for v in result.violations] == ["$.steps[1].depends_on[0]"]


def test_analysis_is_deterministic_under_input_order():
    edges = {"a": [], "b": ["a"], "c": ["a"], "d": ["b", "c"], "e": ["d"]}
    rng = random.Random(7)
    reference = analyze_dag(steps(edges))

    for _ in range(50):
        keys = list(edges)
        rng.shuffle(keys)
        shuffled = analyze_dag(steps({k: edges[k] for k in keys}))

        assert shuffled.order == reference.order
        assert shuffled.depth == reference.depth
        assert dict(shuffled.ancestors) == dict(reference.ancestors)


def test_random_graphs_agree_with_a_reference_implementation():
    rng = random.Random(99)

    for _ in range(300):
        n = rng.randrange(1, 9)
        keys = [f"s{i}" for i in range(n)]
        edges = {
            key: sorted(rng.sample(keys[:i] + keys[i + 1 :], rng.randrange(0, min(3, n - 1) + 1)))
            if n > 1
            else []
            for i, key in enumerate(keys)
        }
        result = analyze_dag(steps(edges))
        acyclic = _is_acyclic(edges)

        assert ("dag_cycle" in codes(result)) == (not acyclic)

        if acyclic:
            assert result.depth == _depth(edges)
            for key in keys:
                assert result.ancestors[key] == _ancestors(edges, key)


def _is_acyclic(edges):
    state = {}

    def visit(node):
        if state.get(node) == 1:
            return False
        if state.get(node) == 2:
            return True
        state[node] = 1
        ok = all(visit(t) for t in edges[node])
        state[node] = 2
        return ok

    return all(visit(k) for k in edges)


def _depth(edges):
    memo = {}

    def depth(node):
        if node not in memo:
            memo[node] = 1 + max((depth(t) for t in edges[node]), default=0)
        return memo[node]

    return max(depth(k) for k in edges)


def _ancestors(edges, key):
    found, stack = set(), list(edges[key])

    while stack:
        node = stack.pop()
        if node not in found:
            found.add(node)
            stack += edges[node]

    return frozenset(found)
